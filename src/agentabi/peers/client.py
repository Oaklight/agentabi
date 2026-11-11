"""Synchronous client for the peer daemon.

Each request opens one short-lived Unix socket connection: the daemon
reads one JSON object per line and writes one response.  ``wait`` holds
the connection open for the turn duration.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .daemon import LOG_NAME, SOCK_NAME, default_state_dir


class DaemonUnavailable(RuntimeError):
    """The daemon is not running and could not be started."""


class PeerClient:
    """Talk to the peer daemon, starting it on demand."""

    def __init__(
        self,
        state_dir: Path | str | None = None,
        *,
        autostart: bool = True,
        start_timeout: float = 10.0,
    ) -> None:
        self.state_dir = Path(state_dir) if state_dir else default_state_dir()
        self.sock_path = self.state_dir / SOCK_NAME
        self.log_path = self.state_dir / LOG_NAME
        self.autostart = autostart
        self.start_timeout = start_timeout

    # -- daemon lifecycle -------------------------------------------------

    def ping(self, *, timeout: float = 1.0) -> dict[str, Any] | None:
        """Return the daemon's ping result, or None when it is not up."""
        try:
            return self.request("ping", timeout=timeout, ensure=False)
        except (OSError, DaemonUnavailable):
            return None

    def ensure_daemon(self) -> dict[str, Any]:
        """Start the daemon when needed and return its ping result."""
        result = self.ping()
        if result is not None:
            return result
        if not self.autostart:
            raise DaemonUnavailable(f"peer daemon is not running at {self.sock_path}")
        # A daemon that is still shutting down holds the lock, so a fresh
        # process can lose the lock race.  Retry the spawn rather than wait
        # for a process that has already exited.
        deadline = time.monotonic() + self.start_timeout
        next_spawn = 0.0
        while time.monotonic() < deadline:
            if time.monotonic() >= next_spawn:
                self._spawn_daemon()
                next_spawn = time.monotonic() + 1.0
            result = self.ping(timeout=0.5)
            if result is not None:
                return result
            time.sleep(0.05)
        raise DaemonUnavailable(f"peer daemon did not start; see {self.log_path}")

    def _spawn_daemon(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        cmd = [
            sys.executable,
            "-m",
            "agentabi.peers.daemon",
            "--state-dir",
            str(self.state_dir),
        ]
        env = dict(os.environ)
        # Make the package importable from a source checkout.
        package_root = str(Path(__file__).resolve().parents[2])
        existing = env.get("PYTHONPATH")
        env["PYTHONPATH"] = (
            package_root if not existing else package_root + os.pathsep + existing
        )
        with open(self.log_path, "ab") as log:
            subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                env=env,
                cwd=str(self.state_dir),
                start_new_session=True,
                close_fds=True,
            )

    def stop_daemon(self) -> None:
        """Ask the daemon to shut down."""
        try:
            self.request("shutdown", timeout=5)
        except (OSError, DaemonUnavailable):
            return
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if self.ping(timeout=0.2) is None:
                return
            time.sleep(0.05)

    # -- requests ---------------------------------------------------------

    def request(
        self,
        op: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float = 30.0,
        ensure: bool = True,
    ) -> Any:
        """Send one request and return its result.

        Raises:
            DaemonUnavailable: The daemon is not reachable.
            Peer...: The daemon reported a peer error, by code.
        """
        if ensure:
            self.ensure_daemon()
        payload = {"id": f"{os.getpid()}-{time.monotonic_ns()}", "op": op}
        if params is not None:
            payload["params"] = params
        response = self._roundtrip(payload, timeout=timeout)
        if not response.get("ok"):
            error = response.get("error") or {}
            raise _client_error(
                str(error.get("code") or "internal"),
                str(error.get("message") or "peer daemon error"),
            )
        return response.get("result")

    def _roundtrip(self, payload: dict[str, Any], *, timeout: float) -> dict[str, Any]:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect(str(self.sock_path))
            sock.sendall((json.dumps(payload) + "\n").encode())
            with sock.makefile("rb") as stream:
                line = stream.readline()
        except (FileNotFoundError, ConnectionRefusedError) as exc:
            raise DaemonUnavailable(str(exc)) from exc
        finally:
            sock.close()
        if not line:
            raise DaemonUnavailable("peer daemon closed the connection")
        return json.loads(line)


def _client_error(code: str, message: str) -> Exception:
    from . import errors

    mapping = {
        "not_found": errors.PeerNotFound,
        "busy": errors.PeerBusy,
        "closed": errors.PeerClosed,
        "timeout": errors.PeerTimeout,
    }
    cls = mapping.get(code, errors.PeerError)
    return cls(message)


__all__ = ["DaemonUnavailable", "PeerClient"]
