"""Claude Code peer: one long-lived ``claude --print`` stream-json process.

The process stays alive between turns, so a coordinator can write more
user messages: a message sent while a turn is running steers that turn,
and a message sent while idle starts the next turn.  The CLI has no
server-side acknowledgement, so a send is accepted once it is written to
the child's stdin.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from ..providers.base import _RunState
from ..providers.claude_native import ClaudeNativeProvider
from ..settings.registry import adapter_for
from ..types.ir.events import IREvent
from ..types.ir.task import TaskConfig
from .base import PeerInfo, PeerNote, PeerStatus, PeerTurn, SendResult
from .errors import PeerBusy, PeerClosed, PeerError, PeerExited, PeerTimeout

_DEFAULT_COMMAND = ["claude"]


def _usage_dict(raw: Any) -> dict[str, int]:
    """Copy a usage mapping into ``dict[str, int]``."""
    if not isinstance(raw, dict):
        return {}
    return {
        str(key): int(value) for key, value in raw.items() if isinstance(value, int)
    }


_INTERRUPT_SUBTYPES = {"error_during_execution"}


@dataclass
class _Turn:
    """Bookkeeping for one in-flight turn."""

    turn_id: str
    started_at: float
    state: _RunState = field(default_factory=_RunState)
    future: asyncio.Future[PeerTurn] | None = None


class ClaudePeer:
    """A Claude Code process that accepts one message at a time."""

    engine = "process"

    def __init__(
        self,
        *,
        name: str,
        cwd: str,
        model: str | None = None,
        permission_mode: str = "bypassPermissions",
        agents: dict[str, Any] | str | None = None,
        system_prompt: str | None = None,
        settings: dict[str, Any] | None = None,
        env: dict[str, str] | None = None,
        add_dirs: list[str] | None = None,
        command: list[str] | None = None,
        session_id: str | None = None,
        resume: bool = False,
        extra_args: list[str] | None = None,
    ) -> None:
        """Describe a peer; call :meth:`start` to spawn it.

        Args:
            name: Caller-facing name.
            cwd: Working directory for the child process.
            model: Optional model passed as ``--model``.
            permission_mode: Claude permission mode.  The peer never asks
                the coordinator, so anything that would prompt is denied.
            agents: Subagent definitions: a mapping or a JSON/file string
                for ``--agents``.
            system_prompt: Optional system prompt.
            settings: Canonical agentabi settings.
            env: Extra child environment variables.
            add_dirs: Extra directories passed as ``--add-dir``.
            command: Override the CLI argv prefix (used by tests).
            session_id: Reuse a session id (recover).
            resume: Continue ``session_id`` instead of creating it.
            extra_args: Extra CLI arguments before the session flags.
        """
        self.name = name
        self._cwd = cwd
        self._model = model
        self._permission_mode = permission_mode
        self._agents = agents
        self._system_prompt = system_prompt
        self._settings = settings
        self._env = dict(env or {})
        self._add_dirs = list(add_dirs or [])
        self._command = list(command or _DEFAULT_COMMAND)
        self._session_id = session_id
        self._resume = resume
        self._extra_args = list(extra_args or [])

        self._peer_id = f"{name}-{uuid.uuid4().hex[:8]}"
        self._parser = ClaudeNativeProvider()
        self._proc: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._stderr: deque[str] = deque(maxlen=100)
        self._write_lock = asyncio.Lock()

        self._status: PeerStatus = "starting"
        self._started_at = time.time()
        self._closed = False
        self._interrupting = False
        self._turn_counter = 0
        self._turn: _Turn | None = None
        self._last_turn: PeerTurn | None = None
        self._history: list[PeerTurn] = []
        self._inbox: list[PeerNote] = []

    # -- lifecycle --------------------------------------------------------

    async def start(self) -> PeerInfo:
        """Spawn the process and begin reading its event stream."""
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
            )
        except OSError as exc:
            raise PeerError(f"cannot start peer {self.name}: {exc}") from exc
        self._status = "idle"
        self._reader = asyncio.create_task(self._read_loop())
        self._stderr_task = asyncio.create_task(self._drain_stderr())
        return self.info()

    def info(self) -> PeerInfo:
        """Return the current peer description."""
        return PeerInfo(
            name=self.name,
            peer_id=self._peer_id,
            cwd=self._cwd,
            status=self._status,
            engine=self.engine,
            session_id=self._session_id,
            model=self._model,
            pid=self._proc.pid if self._proc is not None else None,
            turn_id=self._turn.turn_id if self._turn is not None else None,
            started_at=self._started_at,
        )

    def _build_command(self) -> tuple[list[str], dict[str, str]]:
        """Build the CLI argv and the child environment."""
        cmd = list(self._command)
        cmd += [
            "--print",
            "--output-format",
            "stream-json",
            "--verbose",
            "--input-format",
            "stream-json",
            "--permission-prompts",
            "none",
            "--permission-mode",
            self._permission_mode,
        ]
        if self._model:
            cmd += ["--model", self._model]
        if self._system_prompt:
            cmd += ["--system-prompt", self._system_prompt]
        for directory in self._add_dirs:
            cmd += ["--add-dir", directory]
        if self._agents is not None:
            encoded = (
                self._agents
                if isinstance(self._agents, str)
                else json.dumps(self._agents)
            )
            cmd += ["--agents", encoded]
        cmd += self._extra_args

        if self._session_id and self._resume:
            cmd += ["--resume", self._session_id]
        else:
            if self._session_id is None:
                self._session_id = str(uuid.uuid4())
            cmd += ["--session-id", self._session_id]

        task: TaskConfig = {
            "prompt": "",
            "working_dir": self._cwd,
        }
        if self._model:
            task["model"] = self._model
        if self._settings:
            task["settings"] = self._settings  # type: ignore[typeddict-item]
        if self._env:
            task["env"] = dict(self._env)
        resolved = adapter_for("claude_native").resolve(task)
        cmd += list(resolved.argv)
        cmd += list(resolved.extra_args)
        env = {**os.environ, **self._env, **dict(resolved.env)}
        return cmd, env

    async def close(self) -> None:
        """Stop the peer: close stdin, wait briefly, then terminate."""
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
        if self._reader is not None:
            self._reader.cancel()
            try:
                await self._reader
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        if self._stderr_task is not None:
            self._stderr_task.cancel()
        self._finish_turn(None, cancelled=True)

    # -- messaging --------------------------------------------------------

    async def send(self, text: str) -> SendResult:
        """Start a turn when idle, or steer the running turn."""
        if not text:
            raise PeerError("message text is empty")
        async with self._write_lock:
            self._ensure_running()
            proc = self._proc
            assert proc is not None
            if self._turn is None:
                self._turn_counter += 1
                turn = _Turn(
                    turn_id=f"{self.name}:{self._turn_counter}",
                    started_at=time.time(),
                )
                turn.future = asyncio.get_running_loop().create_future()
                self._turn = turn
                self._status = "running"
                mode: str = "turn"
            else:
                turn = self._turn
                mode = "steer"
            await ClaudeNativeProvider._send_user_message(proc, text)
            return SendResult(accepted=True, mode=mode, turn_id=turn.turn_id)  # type: ignore[arg-type]

    async def steer(self, text: str, *, interrupt: bool = False) -> SendResult:
        """Send a message into the running turn."""
        if self._turn is None:
            raise PeerBusy(f"peer {self.name} has no running turn")
        if interrupt:
            await self.interrupt()
        return await self.send(text)

    async def interrupt(self) -> None:
        """Cancel the running turn; the process stays alive."""
        proc = self._proc
        if proc is None or self._turn is None:
            return
        self._interrupting = True
        await ClaudeNativeProvider._send_interrupt(proc)

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
            raise PeerExited(self.name, self._exit_code(), self._stderr_text())
        if self._proc is None or self._proc.stdin is None:
            raise PeerError(f"peer {self.name} is not running")

    def _exit_code(self) -> int | None:
        return self._proc.returncode if self._proc is not None else None

    def _stderr_text(self) -> str:
        return "".join(self._stderr)

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
                raw = ClaudeNativeProvider._decode_line(line)
                if raw is None:
                    continue
                kind, control_id, control_event = (
                    ClaudeNativeProvider._classify_control(raw)
                )
                if kind is not None:
                    await self._handle_control(kind, control_id, control_event)
                    continue
                for event in self._parser.parse_event(raw):
                    self._handle_event(event)
                if raw.get("type") == "result":
                    self._finish_turn(raw)
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

    async def _handle_control(
        self,
        kind: str,
        control_id: str,
        event: Any,
    ) -> None:
        if kind == "permission" and event is not None:
            # A peer has no approval channel; deny and record it.
            assert self._proc is not None
            await ClaudeNativeProvider._write_control_response(
                self._proc,
                control_id,
                event,
                {"option_id": "reject_once", "outcome": "selected"},
            )
            tool = event.get("tool_name", "tool")
            self._inbox.append(
                PeerNote(
                    kind="permission_denied",
                    text=f"denied {tool}",
                    at=time.time(),
                )
            )
            return
        if kind == "other":
            self._inbox.append(
                PeerNote(
                    kind="error",
                    text=f"unhandled control request {control_id}",
                    at=time.time(),
                )
            )

    def _handle_event(self, event: IREvent) -> None:
        turn = self._turn
        if turn is not None:
            turn.state.handle(event)
        if event.get("type") == "session_start":
            session_id = event.get("session_id")
            if session_id:
                self._session_id = str(session_id)

    def _finish_turn(
        self,
        raw: dict[str, Any] | None,
        *,
        cancelled: bool = False,
    ) -> None:
        turn = self._turn
        if turn is None:
            return
        result = turn.state.build()
        subtype = str((raw or {}).get("subtype") or "")
        is_error = bool((raw or {}).get("is_error")) or result.get("status") == "error"
        if cancelled:
            status = "cancelled"
        elif self._interrupting or subtype in _INTERRUPT_SUBTYPES and is_error:
            status = "cancelled"
        elif is_error:
            status = "error"
        else:
            status = "success"
        peer_turn = PeerTurn(
            turn_id=turn.turn_id,
            status=status,  # type: ignore[arg-type]
            text=str(result.get("result_text") or ""),
            usage=_usage_dict(result.get("usage")),
            num_turns=int((raw or {}).get("num_turns") or 0),
            stop_reason=subtype,
            started_at=turn.started_at,
            ended_at=time.time(),
            errors=tuple(result.get("errors") or ()),
        )
        self._last_turn = peer_turn
        self._history.append(peer_turn)
        self._turn = None
        self._interrupting = False
        self._status = "exited" if self._closed else "idle"
        self._inbox.append(PeerNote(kind="turn", turn=peer_turn, at=time.time()))
        if turn.future is not None and not turn.future.done():
            turn.future.set_result(peer_turn)

    def _on_exit(self) -> None:
        self._status = "exited"
        code = self._exit_code()
        self._inbox.append(
            PeerNote(
                kind="exit",
                text=f"exit code {code}" if code is not None else "exited",
                at=time.time(),
            )
        )
        self._finish_turn(None, cancelled=True)


__all__ = ["ClaudePeer"]
