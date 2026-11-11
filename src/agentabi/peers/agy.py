"""agy peer: one long-lived bidirectional ``agy`` stream-json process.

The CLI accepts ``user`` and ``interrupt`` events on stdin and writes
``init``, ``step_update``, and ``result`` events on stdout.  A second
``user`` event is a queued follow-up turn, not a mid-turn steer, and
``interrupt`` is ignored while the agent is between steps (measured with
agy 1.5.5), so both are reported honestly instead of emulated.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from .._streams import STREAM_LIMIT
from ..settings.registry import adapter_for
from ..types.ir.task import TaskConfig
from .base import PeerInfo, PeerNote, PeerStatus, PeerTurn, SendResult
from .errors import PeerBusy, PeerClosed, PeerError, PeerExited, PeerTimeout

_DEFAULT_COMMAND = ["agy"]
_STREAM_ARGS = [
    "--output-format",
    "stream-json",
    "--input-format",
    "stream-json",
    "--dangerously-skip-permissions",
]
_CANCELLED_STATUSES = {"ERROR"}


@dataclass
class _Turn:
    """Bookkeeping for one in-flight turn."""

    turn_id: str
    started_at: float
    future: asyncio.Future[PeerTurn] | None = None
    text: list[str] = field(default_factory=list)
    interrupting: bool = False


class AgyPeer:
    """An agy stream-json process that accepts one turn at a time."""

    engine = "process"
    backend = "agy"

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
            model: Model name passed as ``--model``.
            permission_mode: Accepted for a uniform interface; the peer
                always runs with permissions skipped.
            system_prompt: Not supported by this transport.
            settings: Canonical agentabi settings.
            env: Extra child environment variables.
            command: Override the CLI argv prefix (used by tests).
            session_id: Conversation id to resume.
            resume: Continue ``session_id`` (``--conversation``).
            extra_args: Extra CLI arguments.
            agents: Not supported by this backend.
        """
        if system_prompt:
            raise PeerError("system prompts are not supported by the agy peer")
        if agents:
            raise PeerError("agent definitions are not supported by the agy peer")
        self.name = name
        self._cwd = cwd
        self._model = model
        self._permission_mode = permission_mode
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
        self._stderr: list[str] = []
        self._status: PeerStatus = "starting"
        self._started_at = time.time()
        self._closed = False
        self._turn: _Turn | None = None
        self._last_turn: PeerTurn | None = None
        self._history: list[PeerTurn] = []
        self._inbox: list[PeerNote] = []
        self._write_lock = asyncio.Lock()

    # -- lifecycle --------------------------------------------------------

    async def start(self) -> PeerInfo:
        """Spawn the process; the first ``init`` event carries the id."""
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
            raise PeerError(f"cannot start agy: {exc}") from exc
        self._reader = asyncio.create_task(self._read_loop())
        self._stderr_task = asyncio.create_task(self._drain_stderr())
        self._status = "idle"
        return self.info()

    def _build_command(self) -> tuple[list[str], dict[str, str]]:
        """Build the stream-json argv and child environment."""
        cmd = list(self._command) + _STREAM_ARGS
        if self._model:
            cmd += ["--model", self._model]
        if self._resume:
            if not self._session_id:
                raise PeerError(f"peer {self.name} has no conversation to resume")
            cmd += ["--conversation", self._session_id]
        cmd += self._extra_args
        task: TaskConfig = {"prompt": "", "working_dir": self._cwd}
        if self._model:
            task["model"] = self._model
        if self._settings:
            task["settings"] = self._settings  # type: ignore[typeddict-item]
        if self._env:
            task["env"] = dict(self._env)
        resolved = adapter_for("agy").resolve(task)
        cmd += list(resolved.argv)
        cmd += list(resolved.extra_args)
        env = {**os.environ, **self._env, **dict(resolved.env)}
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
        """Stop the process."""
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
        self._finish_turn(None, cancelled=True)

    # -- messaging --------------------------------------------------------

    async def send(self, text: str) -> SendResult:
        """Start a turn; a busy peer refuses the message.

        agy queues a second ``user`` event as a new turn, so a message
        sent mid-turn would silently become a follow-up.  The peer keeps
        one turn at a time and asks the caller to wait instead.
        """
        if not text:
            raise PeerError("message text is empty")
        async with self._write_lock:
            self._ensure_running()
            if self._turn is not None:
                raise PeerBusy(
                    f"peer {self.name} is running {self._turn.turn_id}; "
                    "agy queues messages, so wait or interrupt first"
                )
            turn = _Turn(
                turn_id=f"{self.name}:{len(self._history) + 1}",
                started_at=time.time(),
            )
            turn.future = asyncio.get_running_loop().create_future()
            self._turn = turn
            self._status = "running"
            try:
                await self._write(
                    {
                        "event": "user",
                        "message": {
                            "role": "user",
                            "content": [{"type": "text", "text": text}],
                        },
                    }
                )
            except PeerError:
                self._turn = None
                self._status = "idle"
                raise
            return SendResult(accepted=True, mode="turn", turn_id=turn.turn_id)

    async def steer(self, text: str, *, interrupt: bool = False) -> SendResult:
        """Not available: agy has no mid-turn steering event."""
        if self._turn is None:
            raise PeerBusy(f"peer {self.name} has no running turn")
        raise PeerBusy("agy has no steer event; interrupt the turn and send a new one")

    async def interrupt(self) -> None:
        """Ask agy to cancel the running turn.

        Measured with 1.5.5: the event is accepted during a tool step but
        ignored between steps, so the turn may still finish normally.
        """
        turn = self._turn
        if turn is None:
            return
        turn.interrupting = True
        await self._write({"event": "interrupt"})

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

    async def _write(self, event: dict[str, Any]) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise PeerError(f"peer {self.name} is not running")
        try:
            proc.stdin.write((json.dumps(event, ensure_ascii=False) + "\n").encode())
            await proc.stdin.drain()
        except (ConnectionError, OSError) as exc:
            raise PeerError(f"cannot write to agy: {exc}") from exc

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
                if isinstance(message, dict):
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
        event = message.get("event")
        if event == "init":
            conversation = message.get("conversation_id") or (
                message.get("init") or {}
            ).get("conversation_id")
            if conversation:
                self._session_id = str(conversation)
            return
        if event == "step_update":
            step = message.get("step_update") or {}
            if not isinstance(step, dict):
                return
            kind = step.get("step_type")
            turn = self._turn
            if kind == "agent_response" and turn is not None:
                delta = step.get("text_delta")
                if delta:
                    turn.text.append(str(delta))
            elif kind == "error_message":
                text = str(step.get("text_delta") or step.get("error") or "")
                self._inbox.append(
                    PeerNote(kind="error", text=text or "agy error", at=time.time())
                )
            return
        if event == "result":
            result = message.get("result")
            self._finish_turn(result if isinstance(result, dict) else {})

    def _on_exit(self) -> None:
        code = self._proc.returncode if self._proc is not None else None
        self._status = "exited"
        self._inbox.append(
            PeerNote(kind="exit", text=f"agy exited (code {code})", at=time.time())
        )
        self._finish_turn(None, cancelled=True)

    def _finish_turn(
        self, result: dict[str, Any] | None, *, cancelled: bool = False
    ) -> None:
        turn = self._turn
        if turn is None or turn.future is None:
            return
        self._turn = None
        if self._closed or self._status == "exited":
            self._status = "exited"
        else:
            self._status = "idle"
        result = result or {}
        status_text = str(result.get("status") or "")
        text = str(result.get("response") or "".join(turn.text))
        usage = _usage(result.get("usage"))
        if cancelled or (turn.interrupting and status_text in _CANCELLED_STATUSES):
            status: Any = "cancelled"
            stop_reason = "interrupted" if turn.interrupting else "cancelled"
            errors: tuple[str, ...] = ()
        elif status_text == "SUCCESS":
            status = "success"
            stop_reason = "SUCCESS"
            errors = ()
        else:
            status = "error"
            stop_reason = status_text or "ERROR"
            error = result.get("error")
            errors = (str(error),) if error else ()
        outcome = PeerTurn(
            turn_id=turn.turn_id,
            status=status,
            text=text,
            usage=usage,
            num_turns=int(result.get("num_turns") or 1),
            stop_reason=stop_reason,
            started_at=turn.started_at,
            ended_at=time.time(),
            errors=errors,
        )
        self._history.append(outcome)
        self._last_turn = outcome
        self._inbox.append(PeerNote(kind="turn", turn=outcome, at=time.time()))
        if not turn.future.done():
            turn.future.set_result(outcome)


def _usage(raw: Any) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    mapping = {
        "input_tokens": raw.get("input_tokens"),
        "output_tokens": raw.get("output_tokens"),
        "cache_read_tokens": raw.get("cache_read_tokens"),
        "total_tokens": raw.get("total_tokens"),
    }
    return {key: value for key, value in mapping.items() if isinstance(value, int)}


__all__ = ["AgyPeer"]
