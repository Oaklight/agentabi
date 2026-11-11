"""Pi peer: one long-lived ``pi --mode rpc`` process.

The RPC mode reads one JSON command per line on stdin and writes events
and responses as NDJSON on stdout.  ``prompt`` starts an agent run,
``steer`` injects a message into the running run, and ``abort`` ends it;
the run is over when ``agent_settled`` arrives.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from collections import deque
from contextlib import ExitStack
from dataclasses import dataclass, field
from typing import Any

from .._streams import STREAM_LIMIT
from ..settings.registry import adapter_for
from ..types.ir.task import TaskConfig
from .base import PeerInfo, PeerNote, PeerStatus, PeerTurn, SendResult
from .errors import PeerBusy, PeerClosed, PeerError, PeerExited, PeerTimeout

_DEFAULT_COMMAND = ["pi"]


@dataclass
class _Turn:
    """Bookkeeping for one in-flight agent run."""

    turn_id: str
    started_at: float
    future: asyncio.Future[PeerTurn] | None = None
    text: list[str] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    interrupting: bool = False


class PiPeer:
    """A pi RPC process that accepts prompts and steering messages."""

    engine = "process"
    backend = "pi"

    def __init__(
        self,
        *,
        name: str,
        cwd: str,
        model: str | None = None,
        permission_mode: str = "bypassPermissions",
        system_prompt: str | None = None,
        settings: dict[str, Any] | None = None,
        env: dict[str, str] | None = None,
        command: list[str] | None = None,
        session_id: str | None = None,
        resume: bool = False,
        extra_args: list[str] | None = None,
        agents: dict[str, Any] | str | None = None,
    ) -> None:
        """Describe a peer; call :meth:`start` to spawn it.

        Args:
            name: Caller-facing name.
            cwd: Working directory for the process.
            model: ``provider/model`` or a pi model pattern.
            permission_mode: Accepted for a uniform interface; pi has no
                approval flag in this transport.
            system_prompt: Passed as ``--system-prompt``.
            settings: Canonical agentabi settings.  Endpoint settings are
                materialized into a private agent directory.
            env: Extra child environment variables.
            command: Override the CLI argv prefix (used by tests).
            session_id: Session id to reuse.
            resume: Continue ``session_id`` (pi creates it when missing).
            extra_args: Extra CLI arguments.
            agents: Not supported by this backend.
        """
        if agents:
            raise PeerError("agent definitions are not supported by the pi peer")
        self.name = name
        self._cwd = cwd
        self._model = model
        self._permission_mode = permission_mode
        self._system_prompt = system_prompt
        self._settings = settings
        self._env = dict(env or {})
        self._command = list(command or _DEFAULT_COMMAND)
        self._session_id = session_id
        self._resume = resume
        self._extra_args = list(extra_args or [])

        self._peer_id = f"{name}-{uuid.uuid4().hex[:8]}"
        self._proc: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._stderr: deque[str] = deque(maxlen=100)
        self._stack: ExitStack | None = None
        self._ids = iter(range(1, 1_000_000))
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._write_lock = asyncio.Lock()

        self._status: PeerStatus = "starting"
        self._started_at = time.time()
        self._closed = False
        self._turn: _Turn | None = None
        self._last_turn: PeerTurn | None = None
        self._history: list[PeerTurn] = []
        self._inbox: list[PeerNote] = []

    # -- lifecycle --------------------------------------------------------

    async def start(self) -> PeerInfo:
        """Spawn the RPC process and read its session id."""
        if self._proc is not None:
            return self.info()
        if not os.path.isdir(self._cwd):
            raise PeerError(f"cwd is not a directory: {self._cwd}")
        argv, env = self._build_command()
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=self._cwd,
                limit=STREAM_LIMIT,
            )
        except OSError as exc:
            raise PeerError(f"cannot start pi: {exc}") from exc
        self._reader = asyncio.create_task(self._read_loop())
        self._stderr_task = asyncio.create_task(self._drain_stderr())
        try:
            state = await self._rpc("get_state", timeout=30)
        except Exception:
            await self.close()
            raise
        data = state.get("data") if isinstance(state.get("data"), dict) else {}
        session = data.get("sessionId") if isinstance(data, dict) else None
        if session:
            self._session_id = str(session)
        self._status = "idle"
        return self.info()

    def _build_command(self) -> tuple[list[str], dict[str, str]]:
        """Build the RPC argv and child environment."""
        cmd = list(self._command) + ["-p", "--mode", "rpc"]
        if self._model and not self._settings:
            cmd += ["--model", self._model]
        if self._system_prompt:
            cmd += ["--system-prompt", self._system_prompt]
        if self._session_id:
            cmd += ["--session-id", self._session_id]
        cmd += self._extra_args
        env = {**os.environ, **self._env}
        if self._settings:
            task: TaskConfig = {"prompt": "", "working_dir": self._cwd}
            if self._model:
                task["model"] = self._model
            if self._env:
                task["env"] = dict(self._env)
            task["settings"] = self._settings  # type: ignore[typeddict-item]
            adapter = adapter_for("pi_native")
            resolved = adapter.resolve(task)
            rewritten = resolved.task_or(task)
            model = rewritten.get("model")
            if model:
                self._model = str(model)
                cmd += ["--model", str(model)]
            self._stack = ExitStack()
            env.update(adapter.materialize(resolved, self._stack))
        return cmd, env

    def info(self) -> PeerInfo:
        """Return the current peer description."""
        return PeerInfo(
            name=self.name,
            peer_id=self._peer_id,
            cwd=self._cwd,
            status=self._status,
            engine=self.engine,
            backend=self.backend,
            session_id=self._session_id,
            model=self._model,
            pid=self._proc.pid if self._proc is not None else None,
            turn_id=self._turn.turn_id if self._turn is not None else None,
            started_at=self._started_at,
        )

    async def close(self) -> None:
        """Stop the process and release a materialized settings directory."""
        if self._closed:
            return
        self._closed = True
        self._status = "exited"
        proc = self._proc
        if proc is not None:
            if proc.stdin is not None:
                try:
                    proc.stdin.close()
                except Exception:  # noqa: BLE001 - best effort
                    pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
        for task in (self._reader, self._stderr_task):
            if task is not None:
                task.cancel()
        self._fail_pending("peer closed")
        self._finish_turn(None, cancelled=True)
        if self._stack is not None:
            self._stack.close()
            self._stack = None

    # -- messaging --------------------------------------------------------

    async def send(self, text: str) -> SendResult:
        """Start a run when idle, or steer the running run."""
        if not text:
            raise PeerError("message text is empty")
        async with self._write_lock:
            self._ensure_running()
            if self._turn is None:
                turn = _Turn(
                    turn_id=f"{self.name}:{len(self._history) + 1}",
                    started_at=time.time(),
                )
                turn.future = asyncio.get_running_loop().create_future()
                self._turn = turn
                self._status = "running"
                try:
                    await self._rpc("prompt", {"message": text}, timeout=60)
                except PeerError:
                    self._turn = None
                    self._status = "idle"
                    raise
                return SendResult(accepted=True, mode="turn", turn_id=turn.turn_id)
            turn = self._turn
            await self._rpc("steer", {"message": text}, timeout=30)
            return SendResult(accepted=True, mode="steer", turn_id=turn.turn_id)

    async def steer(self, text: str, *, interrupt: bool = False) -> SendResult:
        """Send a message into the running run."""
        if self._turn is None:
            raise PeerBusy(f"peer {self.name} has no running turn")
        if interrupt:
            await self.interrupt()
        return await self.send(text)

    async def interrupt(self) -> None:
        """Abort the running run; the process stays alive."""
        turn = self._turn
        if turn is None:
            return
        turn.interrupting = True
        await self._rpc("abort", timeout=30)

    async def wait(self, timeout: float | None = None) -> PeerTurn:
        """Wait for the running turn, or return the last completed one."""
        turn = self._turn
        if turn is None:
            if self._last_turn is not None:
                return self._last_turn
            raise PeerError(f"peer {self.name} has no turn to wait for")
        if turn.future is None:  # pragma: no cover - always set on send
            raise PeerError(f"peer {self.name} has no turn future")
        try:
            return await asyncio.wait_for(asyncio.shield(turn.future), timeout)
        except asyncio.TimeoutError:
            raise PeerTimeout(
                f"peer {self.name} turn {turn.turn_id} did not finish in {timeout}s"
            ) from None

    def inbox(self, *, clear: bool = False) -> list[PeerNote]:
        """Return notes collected since the last read."""
        notes = list(self._inbox)
        if clear:
            self._inbox.clear()
        return notes

    # -- internals --------------------------------------------------------

    def _ensure_running(self) -> None:
        if self._closed:
            raise PeerClosed(f"peer {self.name} is closed")
        if self._status == "exited":
            code = self._proc.returncode if self._proc is not None else None
            raise PeerExited(self.name, code, "".join(self._stderr))
        if self._proc is None or self._proc.stdin is None:
            raise PeerError(f"peer {self.name} is not running")

    async def _rpc(
        self, kind: str, params: dict[str, Any] | None = None, *, timeout: float = 30
    ) -> dict[str, Any]:
        """Write one RPC command and wait for its response."""
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise PeerError(f"peer {self.name} is not running")
        command_id = next(self._ids)
        future: asyncio.Future[dict[str, Any]] = (
            asyncio.get_running_loop().create_future()
        )
        self._pending[command_id] = future
        payload = {"id": str(command_id), "type": kind, **(params or {})}
        raw = json.dumps(payload, ensure_ascii=False)
        try:
            proc.stdin.write((raw + "\n").encode())
            await proc.stdin.drain()
        except (ConnectionError, OSError) as exc:
            self._pending.pop(command_id, None)
            raise PeerError(f"cannot write to pi: {exc}") from exc
        try:
            response = await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError:
            raise PeerError(f"pi did not answer {kind} in {timeout}s") from None
        finally:
            self._pending.pop(command_id, None)
        if not response.get("success"):
            raise PeerError(str(response.get("error") or f"{kind} failed"))
        return response

    async def _drain_stderr(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        try:
            async for line in proc.stderr:
                text = line.decode(errors="replace").rstrip()
                if text:
                    self._stderr.append(text + "\n")
        except asyncio.CancelledError:
            raise

    async def _read_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:  # pragma: no cover - guarded
            return
        try:
            async for line in proc.stdout:
                text = line.decode(errors="replace").strip()
                if not text:
                    continue
                try:
                    message = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if not isinstance(message, dict):
                    continue
                self._handle_message(message)
            await proc.wait()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - report as a note
            self._inbox.append(
                PeerNote(kind="error", text=f"reader failed: {exc}", at=time.time())
            )
        finally:
            if not self._closed:
                self._on_exit()

    def _handle_message(self, message: dict[str, Any]) -> None:
        kind = message.get("type")
        if kind == "response":
            future = self._pending.get(int(message.get("id") or 0))
            if future is not None and not future.done():
                future.set_result(message)
            return
        turn = self._turn
        if kind == "message_update":
            event = message.get("assistantMessageEvent") or {}
            if isinstance(event, dict) and event.get("type") == "text_delta":
                if turn is not None:
                    turn.text.append(str(event.get("delta") or ""))
            return
        if kind == "turn_end":
            assistant = message.get("message")
            if isinstance(assistant, dict) and turn is not None:
                final = _content_text(assistant)
                if final:
                    turn.text = [final]
                usage = assistant.get("usage")
                if isinstance(usage, dict):
                    turn.usage = _usage(usage)
            return
        if kind == "agent_settled":
            self._finish_turn(None, cancelled=bool(message.get("aborted")))
            return
        if kind == "extension_error":
            self._inbox.append(
                PeerNote(
                    kind="error", text=str(message.get("error") or ""), at=time.time()
                )
            )

    def _on_exit(self) -> None:
        code = self._proc.returncode if self._proc is not None else None
        self._status = "exited"
        self._inbox.append(
            PeerNote(
                kind="exit",
                text=f"pi exited (code {code})",
                at=time.time(),
            )
        )
        self._fail_pending("pi exited")
        self._finish_turn(None, cancelled=True)

    def _fail_pending(self, reason: str) -> None:
        pending = list(self._pending.values())
        self._pending.clear()
        for future in pending:
            if not future.done():
                future.set_exception(PeerError(reason))

    def _finish_turn(
        self, completed: dict[str, Any] | None, *, cancelled: bool
    ) -> None:
        turn = self._turn
        if turn is None or turn.future is None:
            return
        self._turn = None
        if self._closed or self._status == "exited":
            self._status = "exited"
        else:
            self._status = "idle"
        if cancelled:
            status: Any = "cancelled"
            stop_reason = "interrupted" if turn.interrupting else "cancelled"
            errors: tuple[str, ...] = ()
        else:
            status = "success"
            stop_reason = "completed"
            errors = ()
        result = PeerTurn(
            turn_id=turn.turn_id,
            status=status,
            text="".join(turn.text),
            usage=dict(turn.usage),
            num_turns=1,
            stop_reason=stop_reason,
            started_at=turn.started_at,
            ended_at=time.time(),
            errors=errors,
        )
        self._history.append(result)
        self._last_turn = result
        self._inbox.append(PeerNote(kind="turn", turn=result, at=time.time()))
        if not turn.future.done():
            turn.future.set_result(result)


def _content_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "".join(
        str(block.get("text") or "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    )


def _usage(raw: dict[str, Any]) -> dict[str, int]:
    mapping = {
        "input_tokens": raw.get("input"),
        "output_tokens": raw.get("output"),
        "cache_read_tokens": raw.get("cacheRead"),
        "total_tokens": raw.get("totalTokens"),
    }
    return {key: value for key, value in mapping.items() if isinstance(value, int)}


__all__ = ["PiPeer"]
