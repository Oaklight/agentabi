"""Codex app-server backend: permission round-trips over stdio JSON-RPC.

``codex exec --json`` has no approval channel, so the IR backend cannot ask
the ACP client for permission.  ``codex app-server`` does: it sends
``item/commandExecution/requestApproval`` and
``item/fileChange/requestApproval`` as JSON-RPC requests, and the client
answers with ``{"decision": ...}``.

This backend owns one ``codex app-server --listen stdio://`` process.  Each
ACP session maps to one app-server thread.  Approval requests are translated
to IR ``permission_request`` events, resolved by the gateway's
``PermissionBroker``, and written back as a native decision.

Deny by default: a missing broker, an unknown thread, or an unlisted
decision never becomes an allow.  ``availableDecisions`` is authoritative;
only decisions the server lists are offered to the client.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import uuid
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

from agentabi import __version__
from agentabi._vendor.acp import (
    AgentCapabilities,
    InitializeParams,
    InitializeResult,
    LoadSessionParams,
    NewSessionParams,
    NewSessionResult,
    PromptParams,
    PromptResult,
    StopReason,
)
from agentabi._vendor.jsonrpc import INTERNAL_ERROR, JSONRPCError

from ...types.ir.events import PermissionRequestEvent
from ...types.ir.permissions import (
    PermissionDecision,
    PermissionOption,
    PermissionOptionKind,
)
from ..permissions import PermissionBroker, new_request_id
from .base import split_prompt_blocks

logger = logging.getLogger("agentabi.acp.codex_app_server")

#: Default argv.  The binary is spawned directly, so shell wrappers such as
#: ``AGENT_YOLO`` do not inject flags.
DEFAULT_COMMAND = ["codex", "app-server", "--listen", "stdio://"]

#: Methods that request a user decision.
APPROVAL_METHODS: dict[str, str] = {
    "item/commandExecution/requestApproval": "command_execution",
    "item/fileChange/requestApproval": "file_change",
}

#: Canonical option for each native decision the server may list.
_DECISION_OPTIONS: dict[str, tuple[str, str, PermissionOptionKind]] = {
    "accept": ("allow_once", "Allow once", "allow_once"),
    "acceptForSession": ("allow_always", "Allow for session", "allow_always"),
    "decline": ("reject_once", "Reject", "reject_once"),
    "cancel": ("x-codex-cancel", "Cancel turn", "reject_once"),
}

#: Decisions used when the server does not send ``availableDecisions``.
#: ``cancel`` is not offered: it is reserved for ACP ``session/cancel``.
_DEFAULT_DECISIONS: list[Any] = ["accept", "acceptForSession", "decline"]

_EXEC_POLICY_OPTION = "x-codex-accept-with-execpolicy-amendment"


class AppServerError(Exception):
    """The app-server rejected a request or stopped unexpectedly."""


def _decision_key(entry: Any) -> str | None:
    """Return the native decision name for a string or single-key object."""
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict) and len(entry) == 1:
        key = next(iter(entry))
        return key if isinstance(key, str) else None
    return None


@dataclass(frozen=True)
class _OptionTable:
    """Client options and their native decision mapping for one request."""

    options: list[PermissionOption]
    native: dict[str, Any]
    available: frozenset[str]


def _option_table(params: dict[str, Any]) -> _OptionTable:
    """Build client options from the request's ``availableDecisions``.

    When the server sends a list, it is authoritative: only listed decisions
    are offered.  When it does not (file-change requests and older servers),
    the documented decision set is offered.
    """
    raw = params.get("availableDecisions")
    entries: list[Any]
    if isinstance(raw, list):
        entries = raw
    else:
        entries = _DEFAULT_DECISIONS

    options: list[PermissionOption] = []
    native: dict[str, Any] = {}
    available: set[str] = set()

    def add(option_id: str, name: str, kind: PermissionOptionKind, value: Any) -> None:
        if option_id in native:
            return
        options.append({"option_id": option_id, "name": name, "kind": kind})
        native[option_id] = value

    for entry in entries:
        key = _decision_key(entry)
        if key is None:
            continue
        available.add(key)
        if key == "acceptWithExecpolicyAmendment":
            add(
                _EXEC_POLICY_OPTION,
                "Allow and apply policy",
                "allow_once",
                entry,
            )
            continue
        mapped = _DECISION_OPTIONS.get(key)
        if mapped is None:
            continue
        option_id, name, kind = mapped
        add(option_id, name, kind, key)
        if key == "decline":
            # reject_always is a broker-side rule over the native decline.
            add("reject_always", "Reject always", "reject_always", "decline")

    return _OptionTable(options=options, native=native, available=frozenset(available))


def _native_decision(decision: PermissionDecision, table: _OptionTable) -> Any:
    """Map a broker decision to a native decision the server listed.

    An unlisted decision is never sent as an allow.  Denies fall back to
    ``decline`` when listed and ``cancel`` otherwise.
    """
    if decision.get("outcome") == "cancelled":
        return "cancel"
    option_id = decision.get("option_id") or "reject_once"
    if option_id in table.native:
        return table.native[option_id]
    if option_id in ("allow_once", "allow_always") or option_id.startswith(
        "x-codex-accept"
    ):
        for key in ("accept", "acceptForSession"):
            if key in table.available:
                return key
        for candidate, value in table.native.items():
            if candidate in ("allow_once", "allow_always") or candidate.startswith(
                "x-codex-accept"
            ):
                return value
        return "cancel"
    if "decline" in table.available:
        return "decline"
    return "cancel"


def _permission_event(
    tool_name: str, params: dict[str, Any], table: _OptionTable
) -> PermissionRequestEvent:
    """Translate an approval request to an IR ``permission_request``."""
    item_id = str(params.get("itemId") or "")
    request_id = item_id or new_request_id()
    event: PermissionRequestEvent = {
        "type": "permission_request",
        "request_id": request_id,
        "tool_name": tool_name,
        "tool_use_id": request_id,
        "options": table.options,
        "raw": params,
    }
    tool_input: dict[str, Any] = {}
    if tool_name == "command_execution":
        if params.get("command") is not None:
            tool_input["command"] = params.get("command")
        if params.get("cwd") is not None:
            tool_input["cwd"] = params.get("cwd")
    else:
        if params.get("grantRoot") is not None:
            tool_input["grantRoot"] = params.get("grantRoot")
    if tool_input:
        event["tool_input"] = tool_input
    reason = params.get("reason")
    if reason:
        event["description"] = str(reason)
    return event


@dataclass
class _SessionState:
    """Backend state for one ACP session."""

    session_id: str
    thread_id: str
    cwd: str
    queue: asyncio.Queue[list[dict[str, Any]] | None] | None = None
    turn_id: str | None = None
    started_items: set[str] = field(default_factory=set)
    delta_items: set[str] = field(default_factory=set)
    result: PromptResult | None = None
    error: str | None = None
    last_error: str | None = None
    cancel_requested: bool = False
    cancelled_approval: bool = False


@dataclass
class _PendingApproval:
    """One approval request awaiting the broker."""

    server_id: Any
    session_id: str
    item_id: str
    table: _OptionTable
    task: asyncio.Task[None] | None = None


class _AppServerClient:
    """Newline-delimited JSON-RPC client for one app-server process."""

    def __init__(self, command: list[str], *, cwd: str | None = None) -> None:
        self._command = list(command)
        self._cwd = cwd
        self._proc: asyncio.subprocess.Process | None = None
        self._ids = iter(range(1, 1_000_000))
        self._pending: dict[Any, asyncio.Future[dict[str, Any]]] = {}
        self._request_handler: (
            Callable[[Any, str, dict[str, Any]], Awaitable[dict[str, Any] | None]]
            | None
        ) = None
        self._notification_handler: Callable[[str, dict[str, Any]], None] | None = None
        self._close_handler: Callable[[str], None] | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._stderr_tail: deque[str] = deque(maxlen=20)
        self._closing = False

    def set_handlers(
        self,
        *,
        request: Callable[[Any, str, dict[str, Any]], Awaitable[dict[str, Any] | None]],
        notification: Callable[[str, dict[str, Any]], None],
        closed: Callable[[str], None],
    ) -> None:
        self._request_handler = request
        self._notification_handler = notification
        self._close_handler = closed

    async def start(self) -> None:
        if self._proc is not None:
            return
        binary = self._command[0]
        if shutil.which(binary) is None:
            raise AppServerError(f"executable not found: {binary}")
        self._proc = await asyncio.create_subprocess_exec(
            *self._command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self._cwd,
        )
        self._reader_task = asyncio.create_task(self._read_loop())
        self._stderr_task = asyncio.create_task(self._drain_stderr())

    async def stop(self) -> None:
        proc = self._proc
        if proc is None:
            return
        self._closing = True
        if proc.stdin is not None:
            with suppress(Exception):
                proc.stdin.close()
        if proc.returncode is None:
            with suppress(ProcessLookupError):
                proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                with suppress(ProcessLookupError):
                    proc.kill()
                await proc.wait()
        for task in (self._reader_task, self._stderr_task):
            if task is not None and not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
        self._fail_pending("app-server stopped")
        self._proc = None

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float = 60.0,
    ) -> dict[str, Any]:
        if self._proc is None or self._closing:
            raise AppServerError("app-server is not running")
        request_id = next(self._ids)
        future: asyncio.Future[dict[str, Any]] = (
            asyncio.get_running_loop().create_future()
        )
        self._pending[request_id] = future
        await self._write(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params or {},
            }
        )
        try:
            if timeout > 0:
                return await asyncio.wait_for(future, timeout)
            return await future
        finally:
            self._pending.pop(request_id, None)

    async def respond(self, server_id: Any, result: dict[str, Any]) -> None:
        """Answer a server request (for example an approval decision)."""
        await self._write({"jsonrpc": "2.0", "id": server_id, "result": result})

    async def _write(self, message: dict[str, Any]) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise AppServerError("app-server is not running")
        raw = json.dumps(message, separators=(",", ":"), ensure_ascii=False)
        proc.stdin.write((raw + "\n").encode("utf-8"))
        await proc.stdin.drain()

    async def _read_loop(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stdout is not None
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            try:
                message = json.loads(text)
            except json.JSONDecodeError:
                logger.warning("ignoring malformed app-server line: %s", text[:120])
                continue
            if not isinstance(message, dict):
                continue
            if "method" in message:
                if "id" in message:
                    asyncio.create_task(self._handle_request(message))
                else:
                    self._dispatch_notification(message)
            elif "id" in message:
                self._resolve_response(message)
        reason = self._exit_reason(proc)
        self._fail_pending(reason)
        if not self._closing:
            handler = self._close_handler
            if handler is not None:
                handler(reason)

    def _dispatch_notification(self, message: dict[str, Any]) -> None:
        handler = self._notification_handler
        if handler is None:
            return
        params = message.get("params")
        try:
            handler(
                str(message.get("method")), params if isinstance(params, dict) else {}
            )
        except Exception:  # noqa: BLE001 - a bad notification must not stop the stream
            logger.exception("app-server notification handler failed")

    def _resolve_response(self, message: dict[str, Any]) -> None:
        future = self._pending.pop(message.get("id"), None)
        if future is None or future.done():
            return
        error = message.get("error")
        if isinstance(error, dict):
            future.set_exception(
                AppServerError(str(error.get("message") or "app-server error"))
            )
            return
        result = message.get("result")
        future.set_result(result if isinstance(result, dict) else {})

    async def _handle_request(self, message: dict[str, Any]) -> None:
        handler = self._request_handler
        method = str(message.get("method"))
        params = message.get("params")
        try:
            if handler is None:
                raise AppServerError(f"unsupported request: {method}")
            result = await handler(
                message.get("id"), method, params if isinstance(params, dict) else {}
            )
        except Exception as exc:  # noqa: BLE001 - report to the server, fail closed
            error = JSONRPCError(code=INTERNAL_ERROR, message=str(exc))
            with suppress(AppServerError):
                await self._write(
                    {
                        "jsonrpc": "2.0",
                        "id": message.get("id"),
                        "error": error.to_dict(),
                    }
                )
            return
        if result is None:
            # The handler already answered (for example a cancel).
            return
        with suppress(AppServerError):
            await self.respond(message.get("id"), result)

    async def _drain_stderr(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stderr is not None
        while True:
            line = await proc.stderr.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip()
            if text:
                self._stderr_tail.append(text)

    def _exit_reason(self, proc: asyncio.subprocess.Process) -> str:
        code = proc.returncode
        tail = "\n".join(self._stderr_tail)
        reason = (
            f"app-server exited (code {code})"
            if code is not None
            else ("app-server closed its output")
        )
        return f"{reason}: {tail}" if tail else reason

    def _fail_pending(self, reason: str) -> None:
        pending = list(self._pending.items())
        self._pending.clear()
        for _, future in pending:
            if not future.done():
                future.set_exception(AppServerError(reason))


def _tool_status(item: dict[str, Any]) -> str:
    status = item.get("status")
    if status in ("failed", "declined"):
        return "failed"
    return "completed"


def _tool_update(
    item: dict[str, Any], *, started: bool, version: int, title: str, kind: str
) -> dict[str, Any] | None:
    item_id = str(item.get("id") or "")
    if not item_id:
        return None
    if started:
        update: dict[str, Any] = {
            "sessionUpdate": "tool_call" if version < 2 else "tool_call_update",
            "toolCallId": item_id,
            "title": title,
            "kind": kind,
            "status": "in_progress",
        }
        raw = item.get("rawInput")
        if isinstance(raw, dict):
            update["rawInput"] = raw
        return update
    update = {
        "sessionUpdate": "tool_call_update",
        "toolCallId": item_id,
        "status": _tool_status(item),
    }
    output = item.get("aggregatedOutput")
    if output:
        update["content"] = [
            {"type": "content", "content": {"type": "text", "text": str(output)}}
        ]
    return update


def _item_updates(
    item: dict[str, Any], *, started: bool, version: int, state: _SessionState
) -> list[dict[str, Any]]:
    """Translate one ``item/started`` or ``item/completed`` item."""
    item_type = item.get("type")
    item_id = str(item.get("id") or "")

    if item_type == "commandExecution":
        if started:
            state.started_items.add(item_id)
        command = str(item.get("command") or "")
        raw = {
            key: item[key] for key in ("command", "cwd") if item.get(key) is not None
        }
        enriched = {**item, "rawInput": raw}
        update = _tool_update(
            enriched, started=started, version=version, title=command, kind="execute"
        )
        return [update] if update else []

    if item_type == "fileChange":
        if started:
            state.started_items.add(item_id)
        enriched = {**item, "rawInput": {"changes": item.get("changes") or []}}
        update = _tool_update(
            enriched, started=started, version=version, title="file_change", kind="edit"
        )
        return [update] if update else []

    if item_type == "mcpToolCall":
        server = str(item.get("server") or "")
        tool = str(item.get("tool") or "")
        name = f"mcp:{server}/{tool}" if server else f"mcp:{tool}"
        enriched = {**item, "rawInput": item.get("arguments") or {}}
        update = _tool_update(
            enriched, started=started, version=version, title=name, kind="other"
        )
        return [update] if update else []

    if item_type == "agentMessage" and not started:
        text = str(item.get("text") or "")
        if not text or item_id in state.delta_items:
            return []
        chunk: dict[str, Any] = {
            "sessionUpdate": "agent_message_chunk",
            "content": {"type": "text", "text": text},
        }
        if version >= 2 and item_id:
            chunk["messageId"] = item_id
        return [chunk]

    return []


class CodexAppServerBackend:
    """Backend that drives ``codex app-server`` over stdio JSON-RPC."""

    #: The gateway answers downstream approval requests through the broker.
    mediates_permissions = True

    #: The app-server round-trips ``item/*/requestApproval``.
    permission_capable = True

    def __init__(
        self,
        command: list[str] | None = None,
        *,
        approval_policy: str = "on-request",
        sandbox: str = "read-only",
        ephemeral: bool = True,
        request_timeout: float = 60.0,
    ) -> None:
        """Create a backend.

        Args:
            command: argv of the app-server. Defaults to
                ``codex app-server --listen stdio://``.
            approval_policy: ``approvalPolicy`` for ``thread/start``.
            sandbox: ``sandbox`` mode for ``thread/start``.  ``read-only``
                makes commands that need writes request approval.
            ephemeral: Do not persist threads to the Codex home directory.
            request_timeout: Seconds to wait for a control response.
        """
        self.name = "codex-app-server"
        self._command = list(command) if command else list(DEFAULT_COMMAND)
        self._approval_policy = approval_policy
        self._sandbox = sandbox
        self._ephemeral = ephemeral
        self._request_timeout = request_timeout
        self._client = _AppServerClient(self._command)
        self._client.set_handlers(
            request=self._handle_server_request,
            notification=self._on_notification,
            closed=self._on_closed,
        )
        self._start_lock = asyncio.Lock()
        self._started = False
        self._broker: PermissionBroker | None = None
        self._protocol_version = 1
        self._states: dict[str, _SessionState] = {}
        self._by_thread: dict[str, _SessionState] = {}
        self._pending: dict[str, _PendingApproval] = {}

    # -- lifecycle --------------------------------------------------------

    def set_protocol_version(self, version: int) -> None:
        """Select the ACP update shapes this backend emits."""
        self._protocol_version = version

    def set_permission_broker(self, broker: PermissionBroker) -> None:
        """Attach the gateway's permission broker."""
        self._broker = broker

    async def answer_permission(
        self,
        session_id: str,
        request_id: str,
        decision: PermissionDecision,
    ) -> None:
        """No-op: approvals are answered inside their server-request task."""

    async def start(self) -> None:
        """Start the app-server process and perform the handshake."""
        async with self._start_lock:
            if self._started:
                return
            try:
                await self._client.start()
                await self._client.request(
                    "initialize",
                    {
                        "clientInfo": {
                            "name": "agentabi",
                            "title": "agentabi ACP gateway",
                            "version": __version__,
                        },
                        # ``availableDecisions`` is an experimental field.
                        "capabilities": {"experimentalApi": True},
                    },
                    timeout=self._request_timeout,
                )
            except Exception:
                await self._client.stop()
                raise
            self._started = True

    async def stop(self) -> None:
        """Stop the app-server process and release pending requests."""
        for session_id in list(self._pending_sessions()):
            broker = self._broker
            if broker is not None:
                broker.cancel(session_id)
        self._pending.clear()
        await self._client.stop()
        self._started = False

    # -- ACP surface ------------------------------------------------------

    async def initialize(self, params: InitializeParams) -> InitializeResult:
        await self.start()
        return InitializeResult(
            protocol_version=params.protocol_version,
            agent_capabilities=AgentCapabilities(load_session=False),
        )

    async def new_session(self, params: NewSessionParams) -> NewSessionResult:
        await self.start()
        cwd = params.cwd or os.getcwd()
        result = await self._client.request(
            "thread/start",
            {
                "cwd": cwd,
                "approvalPolicy": self._approval_policy,
                "sandbox": self._sandbox,
                "ephemeral": self._ephemeral,
            },
            timeout=self._request_timeout,
        )
        thread = result.get("thread")
        thread_id = str(thread.get("id") or "") if isinstance(thread, dict) else ""
        if not thread_id:
            raise AppServerError("thread/start returned no thread id")
        session_id = f"agentabi-{uuid.uuid4().hex}"
        state = _SessionState(session_id=session_id, thread_id=thread_id, cwd=cwd)
        self._states[session_id] = state
        self._by_thread[thread_id] = state
        return NewSessionResult(session_id=session_id)

    async def load_session(
        self, params: LoadSessionParams
    ) -> AsyncIterator[dict[str, Any]]:
        raise NotImplementedError("codex app-server session replay is not implemented")
        yield {}  # pragma: no cover

    async def prompt(self, params: PromptParams) -> AsyncIterator[dict[str, Any]]:
        state = self._states.get(params.session_id)
        if state is None:
            raise ValueError(f"unknown session: {params.session_id}")
        if state.queue is not None:
            raise AppServerError(f"session {params.session_id} already has a turn")
        state.queue = asyncio.Queue()
        state.turn_id = None
        state.result = None
        state.error = None
        state.last_error = None
        state.cancel_requested = False
        state.cancelled_approval = False
        state.started_items.clear()
        state.delta_items.clear()
        text, _ = split_prompt_blocks(params.prompt)
        try:
            response = await self._client.request(
                "turn/start",
                {
                    "threadId": state.thread_id,
                    "input": [{"type": "text", "text": text, "text_elements": []}],
                    "sandboxPolicy": {"type": "readOnly"},
                },
                timeout=self._request_timeout,
            )
            turn = response.get("turn")
            if isinstance(turn, dict):
                state.turn_id = str(turn.get("id") or "") or None
            if state.cancel_requested and not state.cancelled_approval:
                await self._interrupt(state)
            while True:
                updates = await state.queue.get()
                if updates is None:
                    break
                for update in updates:
                    yield update
        finally:
            state.queue = None

    def prompt_result(self, session_id: str) -> PromptResult | None:
        state = self._states.get(session_id)
        return state.result if state is not None else None

    def prompt_error(self, session_id: str) -> str | None:
        state = self._states.get(session_id)
        return state.error if state is not None else None

    async def cancel(self, session_id: str) -> None:
        """Cancel a turn.

        A pending approval is answered with ``decision: cancel``; a bare
        ``turn/interrupt`` is not enough (it left the turn hanging for 40 s
        in the measured probe).  Without a pending approval, interrupt the
        turn normally.
        """
        state = self._states.get(session_id)
        if state is None:
            return
        state.cancel_requested = True
        # Resolve the broker's pending ask even when the gateway did not
        # (the gateway calls this before ``backend.cancel``, so it is
        # normally idempotent).
        broker = self._broker
        if broker is not None:
            broker.cancel(session_id)
        cancelled = False
        for item_id, pending in list(self._pending.items()):
            if pending.session_id != session_id:
                continue
            self._pending.pop(item_id, None)
            cancelled = True
            await self._client.respond(pending.server_id, {"decision": "cancel"})
            if pending.task is not None and not pending.task.done():
                pending.task.cancel()
        if cancelled:
            state.cancelled_approval = True
            return
        if state.turn_id:
            await self._interrupt(state)

    async def _interrupt(self, state: _SessionState) -> None:
        """Send ``turn/interrupt`` when no approval is pending."""
        if not state.turn_id:
            return
        with suppress(AppServerError):
            await self._client.request(
                "turn/interrupt",
                {"threadId": state.thread_id, "turnId": state.turn_id},
                timeout=self._request_timeout,
            )

    async def close_session(self, session_id: str) -> None:
        await self.cancel(session_id)
        state = self._states.pop(session_id, None)
        if state is not None:
            self._by_thread.pop(state.thread_id, None)

    # -- server requests --------------------------------------------------

    async def _handle_server_request(
        self, server_id: Any, method: str, params: dict[str, Any]
    ) -> dict[str, Any] | None:
        tool_name = APPROVAL_METHODS.get(method)
        if tool_name is None:
            # Fail closed: never answer an unknown request with an allow.
            raise AppServerError(f"the agentabi gateway does not proxy {method}")
        return await self._approval(server_id, tool_name, params)

    async def _approval(
        self, server_id: Any, tool_name: str, params: dict[str, Any]
    ) -> dict[str, Any] | None:
        table = _option_table(params)
        thread_id = str(params.get("threadId") or "")
        state = self._by_thread.get(thread_id)
        item_id = str(params.get("itemId") or "")

        if not table.options or state is None or self._broker is None:
            # No client channel: deny.  ``decline`` is preferred, ``cancel``
            # is the only deny some servers list.
            return {
                "decision": _native_decision(
                    {"option_id": "reject_once", "outcome": "selected"}, table
                )
            }

        request = _permission_event(tool_name, params, table)
        pending = _PendingApproval(
            server_id=server_id,
            session_id=state.session_id,
            item_id=item_id or request["request_id"],
            table=table,
        )
        self._pending[pending.item_id] = pending
        try:
            decision: PermissionDecision = await self._broker.ask(
                state.session_id, request
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - fail closed
            decision = {"option_id": "reject_once", "outcome": "selected"}
        if self._pending.pop(pending.item_id, None) is None:
            # ``cancel`` already answered this request.
            return None
        return {"decision": _native_decision(decision, table)}

    # -- notifications ----------------------------------------------------

    def _on_notification(self, method: str, params: dict[str, Any]) -> None:
        if method == "serverRequest/resolved":
            self._resolved(params.get("requestId"))
            return
        state = self._by_thread.get(str(params.get("threadId") or ""))
        if state is None or state.queue is None:
            return
        if method == "turn/completed":
            self._turn_completed(state, params)
            return
        if method == "error":
            error = params.get("error")
            if isinstance(error, dict) and error.get("message"):
                state.last_error = str(error["message"])
            return
        updates = self._translate(method, params, state)
        if updates:
            state.queue.put_nowait(updates)

    def _turn_completed(self, state: _SessionState, params: dict[str, Any]) -> None:
        queue = state.queue
        if queue is None:
            return
        turn = params.get("turn")
        turn = turn if isinstance(turn, dict) else {}
        turn_id = str(turn.get("id") or "")
        if state.turn_id and turn_id and turn_id != state.turn_id:
            return
        status = turn.get("status")
        updates: list[dict[str, Any]] = []
        items = turn.get("items")
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    updates.extend(
                        _item_updates(
                            item,
                            started=False,
                            version=self._protocol_version,
                            state=state,
                        )
                    )
        if updates:
            queue.put_nowait(updates)
        if status == "interrupted":
            state.result = PromptResult(stop_reason=StopReason.CANCELLED)
        elif status == "failed":
            error = turn.get("error")
            message = ""
            if isinstance(error, dict):
                message = str(error.get("message") or "")
            state.error = message or state.last_error or "Codex turn failed"
        else:
            state.result = PromptResult(stop_reason=StopReason.END_TURN)
        queue.put_nowait(None)

    def _translate(
        self, method: str, params: dict[str, Any], state: _SessionState
    ) -> list[dict[str, Any]]:
        version = self._protocol_version
        item_id = str(params.get("itemId") or "")

        if method == "item/agentMessage/delta":
            delta = str(params.get("delta") or "")
            if not delta:
                return []
            state.delta_items.add(item_id)
            update: dict[str, Any] = {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": delta},
            }
            if version >= 2 and item_id:
                update["messageId"] = item_id
            return [update]

        if method in (
            "item/reasoning/summaryTextDelta",
            "item/reasoning/textDelta",
        ):
            delta = str(params.get("delta") or "")
            if not delta:
                return []
            update = {
                "sessionUpdate": "agent_thought_chunk",
                "content": {"type": "text", "text": delta},
            }
            if version >= 2 and item_id:
                update["messageId"] = item_id
            return [update]

        if method == "item/started":
            item = params.get("item")
            if isinstance(item, dict):
                return _item_updates(item, started=True, version=version, state=state)
            return []

        if method == "item/completed":
            item = params.get("item")
            if isinstance(item, dict):
                return _item_updates(item, started=False, version=version, state=state)
            return []

        return []

    def _resolved(self, server_id: Any) -> None:
        """Drop a pending approval the server has already resolved."""
        for item_id, pending in list(self._pending.items()):
            if pending.server_id == server_id:
                self._pending.pop(item_id, None)
                return

    # -- process exit -----------------------------------------------------

    def _on_closed(self, reason: str) -> None:
        for session_id in list(self._pending_sessions()):
            broker = self._broker
            if broker is not None:
                broker.cancel(session_id)
        self._pending.clear()
        for state in self._states.values():
            if state.queue is None:
                continue
            if state.result is None and state.error is None:
                state.error = reason
            state.queue.put_nowait(None)

    def _pending_sessions(self) -> set[str]:
        return {pending.session_id for pending in self._pending.values()}


__all__ = ["APPROVAL_METHODS", "CodexAppServerBackend", "DEFAULT_COMMAND"]
