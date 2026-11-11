"""Codex peer: one long-lived ``codex app-server`` process and one thread.

A turn starts with ``turn/start``; a message sent while the turn runs
goes through ``turn/steer``, which the app-server acknowledges with the
turn id.  ``turn/interrupt`` ends the turn and leaves the thread usable
for the next one.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from ..providers.codex_appserver import AppServerClient, AppServerError
from ..settings.registry import adapter_for
from ..types.ir.task import TaskConfig
from .base import PeerInfo, PeerNote, PeerStatus, PeerTurn, SendResult
from .errors import PeerBusy, PeerClosed, PeerError, PeerExited, PeerTimeout

_DEFAULT_COMMAND = ["codex"]
_APP_SERVER_ARGS = ["app-server", "--listen", "stdio://"]

#: Peer permission modes mapped to app-server sandbox and approval policy.
_PERMISSION_MODES: dict[str, tuple[str, str]] = {
    "bypassPermissions": ("danger-full-access", "never"),
    "full_auto": ("workspace-write", "never"),
    "acceptEdits": ("workspace-write", "never"),
    "default": ("workspace-write", "on-request"),
    "plan": ("read-only", "on-request"),
}


def _last_usage(raw: Any) -> dict[str, int]:
    """Map a codex ``TokenUsageBreakdown`` to the shared usage keys."""
    if not isinstance(raw, dict):
        return {}
    mapping = {
        "input_tokens": raw.get("inputTokens"),
        "output_tokens": raw.get("outputTokens"),
        "cache_read_tokens": raw.get("cachedInputTokens"),
        "total_tokens": raw.get("totalTokens"),
    }
    return {key: value for key, value in mapping.items() if isinstance(value, int)}


@dataclass
class _Turn:
    """Bookkeeping for one in-flight turn."""

    turn_id: str
    started_at: float
    future: asyncio.Future[PeerTurn] | None = None
    deltas: list[str] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)


class CodexPeer:
    """A Codex app-server thread that accepts one message at a time."""

    engine = "process"
    backend = "codex"

    def __init__(
        self,
        *,
        name: str,
        cwd: str,
        model: str | None = None,
        permission_mode: str = "bypassPermissions",
        sandbox: str | None = None,
        approval_policy: str | None = None,
        system_prompt: str | None = None,
        settings: dict[str, Any] | None = None,
        env: dict[str, str] | None = None,
        add_dirs: list[str] | None = None,
        command: list[str] | None = None,
        session_id: str | None = None,
        resume: bool = False,
        extra_args: list[str] | None = None,
        agents: dict[str, Any] | str | None = None,
    ) -> None:
        """Describe a peer; call :meth:`start` to open the thread.

        Args:
            name: Caller-facing name.
            cwd: Working directory for the app-server process.
            model: Optional model override for the thread.
            permission_mode: Peer permission mode; mapped to the sandbox
                and approval policy unless those are given explicitly.
            sandbox: Explicit app-server sandbox mode.
            approval_policy: Explicit app-server approval policy.  The
                peer has no approval channel, so a request is declined.
            system_prompt: Passed as ``developerInstructions``.
            settings: Canonical agentabi settings.
            env: Extra child environment variables.
            add_dirs: Extra workspace roots.
            command: Override the CLI argv prefix (used by tests).
            session_id: Thread id to resume (recover).
            resume: Continue ``session_id`` instead of creating it.
            extra_args: Extra CLI arguments before ``app-server``.
            agents: Not supported by this backend; must be empty.
        """
        if agents:
            raise PeerError("agent definitions are not supported by the Codex peer")
        mode = _PERMISSION_MODES.get(permission_mode)
        if mode is None:
            raise PeerError(
                f"unknown permission_mode {permission_mode!r}; "
                f"expected one of {sorted(_PERMISSION_MODES)}"
            )
        self.name = name
        self._cwd = cwd
        self._model = model
        self._sandbox = sandbox or mode[0]
        self._approval_policy = approval_policy or mode[1]
        self._system_prompt = system_prompt
        self._settings = settings
        self._env = dict(env or {})
        self._add_dirs = list(add_dirs or [])
        self._command = list(command or _DEFAULT_COMMAND)
        self._session_id = session_id
        self._resume = resume
        self._extra_args = list(extra_args or [])

        self._peer_id = f"{name}-{uuid.uuid4().hex[:8]}"
        self._client: AppServerClient | None = None
        self._status: PeerStatus = "starting"
        self._started_at = time.time()
        self._closed = False
        self._thread_id: str | None = None
        self._turn: _Turn | None = None
        self._last_turn: PeerTurn | None = None
        self._history: list[PeerTurn] = []
        self._inbox: list[PeerNote] = []
        self._last_error: str = ""
        self._write_lock = asyncio.Lock()

    # -- lifecycle --------------------------------------------------------

    async def start(self) -> PeerInfo:
        """Start the app-server, open the thread, and return the info."""
        if self._client is not None:
            return self.info()
        if not os.path.isdir(self._cwd):
            raise PeerError(f"cwd is not a directory: {self._cwd}")
        argv, env = self._build_command()
        client = AppServerClient(argv, cwd=self._cwd, env=env)
        client.set_handlers(
            request=self._handle_server_request,
            notification=self._on_notification,
            closed=self._on_closed,
        )
        try:
            await client.start()
            await client.request(
                "initialize",
                {
                    "clientInfo": {"name": "agentabi-peers", "version": "1"},
                    "capabilities": {"experimentalApi": True},
                },
                timeout=60,
            )
        except Exception:
            await client.stop()
            raise
        self._client = client
        try:
            await self._open_thread()
        except Exception:
            await client.stop()
            self._client = None
            raise
        self._status = "idle"
        return self.info()

    async def _open_thread(self) -> None:
        client = self._client
        assert client is not None
        roots = [self._cwd]
        for directory in self._add_dirs:
            absolute = os.path.abspath(directory)
            if absolute not in roots:
                roots.append(absolute)
        params: dict[str, Any] = {
            "cwd": self._cwd,
            "approvalPolicy": self._approval_policy,
            "sandbox": self._sandbox,
            "runtimeWorkspaceRoots": roots,
        }
        if self._model:
            params["model"] = self._model
        if self._system_prompt:
            params["developerInstructions"] = self._system_prompt
        if self._resume:
            if not self._session_id:
                raise PeerError(f"peer {self.name} has no session id to resume")
            params["threadId"] = self._session_id
            result = await client.request("thread/resume", params, timeout=120)
        else:
            result = await client.request("thread/start", params, timeout=120)
        thread = result.get("thread")
        thread_id = str(thread.get("id") or "") if isinstance(thread, dict) else ""
        if not thread_id:
            raise PeerError("codex thread did not return an id")
        if self._resume and self._session_id and thread_id != self._session_id:
            raise PeerError(
                f"resume returned thread {thread_id}, expected {self._session_id}"
            )
        self._thread_id = thread_id
        self._session_id = thread_id

    def _build_command(self) -> tuple[list[str], dict[str, str]]:
        """Build the app-server argv and child environment."""
        task: TaskConfig = {"prompt": "", "working_dir": self._cwd}
        if self._model:
            task["model"] = self._model
        if self._settings:
            task["settings"] = self._settings  # type: ignore[typeddict-item]
        if self._env:
            task["env"] = dict(self._env)
        resolved = adapter_for("codex_native").resolve(task)
        cmd = list(self._command)
        cmd += list(resolved.argv)
        cmd += list(resolved.extra_args)
        cmd += self._extra_args
        cmd += _APP_SERVER_ARGS
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
            pid=self._client.pid if self._client is not None else None,
            turn_id=self._turn.turn_id if self._turn is not None else None,
            started_at=self._started_at,
        )

    async def close(self) -> None:
        """Stop the app-server process."""
        if self._closed:
            return
        self._closed = True
        self._status = "exited"
        client = self._client
        if client is not None:
            await client.stop()
        self._finish_turn(None, cancelled=True)

    # -- messaging --------------------------------------------------------

    async def send(self, text: str) -> SendResult:
        """Start a turn when idle, or steer the running turn."""
        if not text:
            raise PeerError("message text is empty")
        async with self._write_lock:
            self._ensure_running()
            client = self._client
            assert client is not None
            if self._turn is None:
                turn = _Turn(turn_id="", started_at=time.time())
                turn.future = asyncio.get_running_loop().create_future()
                self._turn = turn
                self._status = "running"
                try:
                    result = await client.request(
                        "turn/start",
                        {
                            "threadId": self._thread_id,
                            "input": [{"type": "text", "text": text}],
                        },
                        timeout=120,
                    )
                except AppServerError as exc:
                    self._turn = None
                    self._status = "idle"
                    raise PeerError(str(exc)) from exc
                started = result.get("turn")
                turn_id = (
                    str(started.get("id") or "") if isinstance(started, dict) else ""
                )
                turn.turn_id = turn_id or f"{self.name}:{len(self._history) + 1}"
                return SendResult(accepted=True, mode="turn", turn_id=turn.turn_id)
            turn = self._turn
            if not turn.turn_id:
                raise PeerBusy(f"peer {self.name} has no turn id yet")
            try:
                await client.request(
                    "turn/steer",
                    {
                        "threadId": self._thread_id,
                        "expectedTurnId": turn.turn_id,
                        "input": [{"type": "text", "text": text}],
                    },
                    timeout=60,
                )
            except AppServerError as exc:
                raise PeerError(str(exc)) from exc
            return SendResult(accepted=True, mode="steer", turn_id=turn.turn_id)

    async def steer(self, text: str, *, interrupt: bool = False) -> SendResult:
        """Send a message into the running turn."""
        if self._turn is None:
            raise PeerBusy(f"peer {self.name} has no running turn")
        if interrupt:
            await self.interrupt()
        return await self.send(text)

    async def interrupt(self) -> None:
        """Cancel the running turn; the thread stays usable."""
        turn = self._turn
        client = self._client
        if turn is None or client is None or not turn.turn_id:
            return
        await client.request(
            "turn/interrupt",
            {"threadId": self._thread_id, "turnId": turn.turn_id},
            timeout=30,
        )

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
            code = self._client.returncode if self._client is not None else None
            raise PeerExited(self.name, code, self._last_error)
        if self._client is None or self._thread_id is None:
            raise PeerError(f"peer {self.name} is not running")

    async def _handle_server_request(
        self, server_id: Any, method: str, params: dict[str, Any]
    ) -> dict[str, Any] | None:
        if method in (
            "item/commandExecution/requestApproval",
            "item/fileChange/requestApproval",
        ):
            # No approval channel: decline, and record it.
            self._inbox.append(
                PeerNote(
                    kind="permission_denied",
                    text=f"denied {method}",
                    at=time.time(),
                )
            )
            return {"decision": "decline"}
        raise AppServerError(f"unsupported server request: {method}")

    def _on_notification(self, method: str, params: dict[str, Any]) -> None:
        thread = str(params.get("threadId") or "")
        if thread and self._thread_id and thread != self._thread_id:
            return
        if method == "thread/started":
            return
        turn = self._turn
        if method == "item/agentMessage/delta":
            if turn is not None:
                turn.deltas.append(str(params.get("delta") or ""))
            return
        if method == "thread/tokenUsage/updated":
            if turn is not None:
                usage = params.get("tokenUsage")
                last = usage.get("last") if isinstance(usage, dict) else None
                turn.usage = _last_usage(last)
            return
        if method == "error":
            error = params.get("error")
            if isinstance(error, dict) and error.get("message"):
                self._last_error = str(error["message"])
            return
        if method == "turn/completed":
            self._on_turn_completed(params)
            return
        if method == "turn/started":
            if turn is not None and not turn.turn_id:
                started = params.get("turn")
                if isinstance(started, dict):
                    turn.turn_id = str(started.get("id") or "")

    def _on_turn_completed(self, params: dict[str, Any]) -> None:
        turn = self._turn
        if turn is None:
            return
        completed = params.get("turn")
        completed = completed if isinstance(completed, dict) else {}
        turn_id = str(completed.get("id") or "")
        if turn.turn_id and turn_id and turn_id != turn.turn_id:
            return
        if turn_id and not turn.turn_id:
            turn.turn_id = turn_id
        self._finish_turn(completed, cancelled=False)

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
        if turn_id := (completed or {}).get("id"):
            turn.turn_id = str(turn_id)
        status = str((completed or {}).get("status") or "")
        if cancelled:
            outcome: Any = "cancelled"
            stop_reason = "cancelled"
            errors: tuple[str, ...] = ()
        elif status == "interrupted":
            outcome = "cancelled"
            stop_reason = "interrupted"
            errors = ()
        elif status == "failed":
            outcome = "error"
            stop_reason = "failed"
            error = (completed or {}).get("error")
            message = str(error.get("message") or "") if isinstance(error, dict) else ""
            errors = (message or self._last_error or "codex turn failed",)
        else:
            outcome = "success"
            stop_reason = status or "completed"
            errors = ()
        text = _items_text((completed or {}).get("items")) or "".join(turn.deltas)
        result = PeerTurn(
            turn_id=turn.turn_id or f"{self.name}:{len(self._history) + 1}",
            status=outcome,
            text=text,
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

    def _on_closed(self, reason: str) -> None:
        if self._closed:
            return
        self._status = "exited"
        self._inbox.append(PeerNote(kind="exit", text=reason, at=time.time()))
        self._finish_turn(None, cancelled=True)


def _items_text(items: Any) -> str:
    """Join the assistant text blocks of a completed turn's items."""
    if not isinstance(items, list):
        return ""
    parts: list[str] = []
    for item in items:
        if not isinstance(item, dict) or item.get("type") != "agentMessage":
            continue
        content = item.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(str(block.get("text") or ""))
        elif isinstance(content, str):
            parts.append(content)
    return "".join(parts)


__all__ = ["CodexPeer"]
