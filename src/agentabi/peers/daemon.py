"""Peer daemon: one long-lived process that owns the peers.

``peerctl`` and other processes talk to it over a Unix domain socket
using newline-delimited JSON.  The daemon is the parent of every peer
process, so stopping it stops them; ``peers.json`` keeps enough identity
to recover later.
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any

from .broker import DEFAULT_MAX_PEERS, PeerBroker
from .errors import (
    PeerBusy,
    PeerClosed,
    PeerError,
    PeerExited,
    PeerNotFound,
    PeerTimeout,
)

STATE_ENV = "AGENTABI_PEER_STATE"
STATE_FILE = "peers.json"
SOCK_NAME = "daemon.sock"
LOCK_NAME = "daemon.lock"
LOG_NAME = "daemon.log"

#: Keys accepted by ``spawn`` and forwarded to ClaudePeer.
_PEER_OPTIONS = frozenset(
    {
        "model",
        "permission_mode",
        "agents",
        "system_prompt",
        "settings",
        "env",
        "add_dirs",
        "command",
        "session_id",
        "resume",
        "extra_args",
    }
)

#: Spawn options that survive a daemon restart for ``recover``.  Endpoint
#: settings and ``env`` may hold keys, so they are never written to disk.
_REPLAY_OPTIONS = frozenset(
    {"model", "permission_mode", "agents", "add_dirs", "command"}
)


def default_state_dir() -> Path:
    """Return the peer state directory."""
    raw = os.environ.get(STATE_ENV)
    base = (
        Path(raw).expanduser() if raw else Path.home() / ".local/state/agentabi/peers"
    )
    return base


def _error_code(exc: Exception) -> str:
    if isinstance(exc, PeerNotFound):
        return "not_found"
    if isinstance(exc, PeerBusy):
        return "busy"
    if isinstance(exc, PeerExited):
        return "exited"
    if isinstance(exc, PeerClosed):
        return "closed"
    if isinstance(exc, PeerTimeout):
        return "timeout"
    if isinstance(exc, PeerError):
        return "peer_error"
    if isinstance(exc, (ValueError, KeyError)):
        return "invalid"
    return "internal"


class PeerDaemon:
    """Serve peer operations over a Unix socket."""

    def __init__(
        self,
        state_dir: Path | None = None,
        *,
        max_peers: int = DEFAULT_MAX_PEERS,
    ) -> None:
        self.state_dir = Path(state_dir) if state_dir else default_state_dir()
        self.sock_path = self.state_dir / SOCK_NAME
        self.state_path = self.state_dir / STATE_FILE
        self.lock_path = self.state_dir / LOCK_NAME
        self.broker = PeerBroker(max_peers=max_peers)
        self._server: asyncio.AbstractServer | None = None
        self._stop = asyncio.Event()
        self._lock_file: Any = None
        self._started_at = time.time()
        self._replay: dict[str, dict[str, Any]] = {}

    async def start(self) -> None:
        """Bind the socket and begin serving."""
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._lock_file = open(self.lock_path, "w", encoding="utf-8")
        try:
            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("another peer daemon holds the lock") from exc
        if self.sock_path.exists():
            self.sock_path.unlink()
        self._server = await asyncio.start_unix_server(
            self._handle_client, path=str(self.sock_path)
        )
        os.chmod(self.sock_path, 0o600)
        self._load_state()

    async def stop(self) -> None:
        """Stop accepting requests, close every peer, remove the socket."""
        self._stop.set()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        await self.broker.close()
        if self.sock_path.exists():
            try:
                self.sock_path.unlink()
            except OSError:
                pass
        if self._lock_file is not None:
            try:
                fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
                self._lock_file.close()
            except OSError:
                pass
            self._lock_file = None

    async def serve_forever(self) -> None:
        """Run until ``shutdown`` is requested or a signal arrives."""
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, self._stop.set)
            except NotImplementedError:  # pragma: no cover - Windows
                pass
        await self._stop.wait()

    @property
    def stopping(self) -> asyncio.Event:
        """Event that is set when the daemon should exit."""
        return self._stop

    # -- protocol ---------------------------------------------------------

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                try:
                    request = json.loads(line)
                except json.JSONDecodeError:
                    await self._write(
                        writer,
                        {
                            "id": None,
                            "ok": False,
                            "error": {"code": "invalid", "message": "invalid JSON"},
                        },
                    )
                    continue
                response = await self.dispatch(request)
                await self._write(writer, response)
                if response.get("_shutdown"):
                    self._stop.set()
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    async def _write(
        self, writer: asyncio.StreamWriter, payload: dict[str, Any]
    ) -> None:
        body = {key: value for key, value in payload.items() if key != "_shutdown"}
        writer.write((json.dumps(body, ensure_ascii=False) + "\n").encode())
        await writer.drain()

    async def dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
        """Execute one request and build its response envelope."""
        request_id = request.get("id")
        op = request.get("op")
        params = request.get("params") or {}
        if not isinstance(params, dict):
            return {
                "id": request_id,
                "ok": False,
                "error": {"code": "invalid", "message": "params must be an object"},
            }
        try:
            result = await self._run_op(str(op), params)
        except Exception as exc:  # noqa: BLE001 - reported to the client
            return {
                "id": request_id,
                "ok": False,
                "error": {"code": _error_code(exc), "message": str(exc)},
            }
        response: dict[str, Any] = {"id": request_id, "ok": True, "result": result}
        if op == "shutdown":
            response["_shutdown"] = True
        return response

    async def _run_op(self, op: str, params: dict[str, Any]) -> Any:
        if op == "ping":
            return {
                "pid": os.getpid(),
                "started_at": self._started_at,
                "peers": len(self.broker.list_peers()),
            }
        if op == "spawn":
            return await self._spawn(params)
        if op == "list":
            return [info.as_dict() for info in self.broker.list_peers()]
        if op == "status":
            return self._status(params)
        if op == "send":
            peer = self.broker.get(self._name(params))
            return (await peer.send(str(params.get("text", "")))).as_dict()
        if op == "steer":
            peer = self.broker.get(self._name(params))
            result = await peer.steer(
                str(params.get("text", "")),
                interrupt=bool(params.get("interrupt")),
            )
            return result.as_dict()
        if op == "interrupt":
            peer = self.broker.get(self._name(params))
            await peer.interrupt()
            return {"interrupted": True}
        if op == "wait":
            peer = self.broker.get(self._name(params))
            timeout = params.get("timeout")
            turn = await peer.wait(float(timeout) if timeout is not None else None)
            return turn.as_dict()
        if op == "inbox":
            name = params.get("name")
            notes = self.broker.notes(
                str(name) if name else None, clear=bool(params.get("clear"))
            )
            return [note.as_dict() for note in notes]
        if op == "stop":
            name = self._name(params)
            await self.broker.close(name)
            self._replay.pop(name, None)
            self._save_state()
            return {"stopped": name}
        if op == "shutdown":
            return {"stopping": True}
        raise ValueError(f"unknown op: {op}")

    async def _spawn(self, params: dict[str, Any]) -> dict[str, Any]:
        name = self._name(params)
        cwd = params.get("cwd")
        if not isinstance(cwd, str) or not cwd:
            raise ValueError("cwd is required")
        options = {
            key: params[key] for key in _PEER_OPTIONS if params.get(key) is not None
        }
        command = options.get("command")
        if command is not None and not (
            isinstance(command, list) and all(isinstance(part, str) for part in command)
        ):
            raise ValueError("command must be a list of strings")
        prompt = params.get("prompt")
        peer = await self.broker.spawn(
            name,
            cwd=cwd,
            prompt=str(prompt) if prompt else None,
            **options,
        )
        self._replay[name] = {
            key: options[key] for key in _REPLAY_OPTIONS if key in options
        }
        self._save_state()
        return peer.info().as_dict()

    def _status(self, params: dict[str, Any]) -> dict[str, Any]:
        peer = self.broker.get(self._name(params))
        info = peer.info().as_dict()
        info["inbox"] = [note.as_dict() for note in peer.inbox()]
        return info

    @staticmethod
    def _name(params: dict[str, Any]) -> str:
        name = params.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("name is required")
        return name

    # -- persistence ------------------------------------------------------

    def _save_state(self) -> None:
        entries = []
        for info in self.broker.list_peers():
            entry = info.as_dict()
            replay = self._replay.get(entry["name"])
            if replay:
                entry["recreate"] = replay
            entries.append(entry)
        payload = {"version": 1, "updated_at": time.time(), "peers": entries}
        tmp = self.state_path.with_suffix(".tmp")
        try:
            tmp.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            tmp.replace(self.state_path)
            os.chmod(self.state_path, 0o600)
        except OSError:
            pass

    def _load_state(self) -> None:
        """Read the state file left by a previous daemon (recover input)."""
        if not self.state_path.exists():
            return
        try:
            json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass


def main(argv: list[str] | None = None) -> int:
    """Run the daemon in the foreground."""
    parser = argparse.ArgumentParser(prog="agentabi-peer-daemon")
    parser.add_argument("--state-dir", type=Path, default=None)
    parser.add_argument("--max-peers", type=int, default=DEFAULT_MAX_PEERS)
    args = parser.parse_args(argv)

    async def run() -> None:
        daemon = PeerDaemon(args.state_dir, max_peers=args.max_peers)
        try:
            await daemon.start()
        except RuntimeError as exc:
            print(f"peer daemon: {exc}", file=sys.stderr)
            return
        try:
            await daemon.serve_forever()
        finally:
            await daemon.stop()

    asyncio.run(run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["PeerDaemon", "default_state_dir", "main"]
