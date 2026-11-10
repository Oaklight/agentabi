"""
agentabi - OpenCode V2 Provider

Drives an `opencode serve` HTTP/SSE server (OpenCode V2 API) instead of the
`opencode run --format json` subprocess (V1). V2 and V1 do not share event
names: V2 streams `session.next.*` events and has no `session.idle` event, so
a turn ends at `session.next.step.ended` with a non-`tool-calls` finish.

Transport selection (first match wins):

1. ``agent_extensions.opencode.transport`` (``"auto"``, ``"v1"``, ``"v2"``)
2. ``AGENTABI_OPENCODE_TRANSPORT``
3. ``"auto"``: V2 when a base URL is configured, or the CLI is at least
   1.19.0; otherwise V1.

A base URL comes from ``agent_extensions.opencode.base_url``,
``OPENCODE_BASE_URL``, or ``OPENCODE_SERVER_URL``. Without one, the provider
starts ``opencode serve`` on a free localhost port and reuses it.

Everything here is stdlib-only (``asyncio`` + the bundled ``_http_sse``).
"""

from __future__ import annotations

import asyncio
import atexit
import json
import mimetypes
import os
import re
import shutil
import socket
import subprocess
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ..settings.registry import adapter_for
from ..types.ir.capabilities import AgentCapabilities
from ..types.ir.events import (
    ErrorEvent,
    IREvent,
    MessageDeltaEvent,
    MessageEndEvent,
    MessageStartEvent,
    PermissionRequestEvent,
    PermissionResponseEvent,
    ReasoningDeltaEvent,
    SessionEndEvent,
    SessionStartEvent,
    ToolResultEvent,
    ToolUseEvent,
    UsageEvent,
    UsageInfo,
)
from ..types.ir.session import SessionResult
from ..types.ir.stop_reasons import stop_reason
from ..types.ir.task import TaskConfig
from ._http_sse import HTTPError, SSEMessage, SSEStream, request, request_json

# First OpenCode release verified to expose the V2 HTTP/SSE API.
_MIN_V2_VERSION = (1, 19, 0)
_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")
_LISTEN_RE = re.compile(r"listening on (https?://\S+)")
_SERVER_START_TIMEOUT = 30.0
_SUBSCRIBE_TIMEOUT = 10.0
_HTTP_TIMEOUT = 30.0
# How long to wait for the V2 model catalog to say whether it has a model.
_CATALOG_WAIT = 4.0
_CATALOG_POLL = 0.25

_TERMINAL_FINISHES = frozenset({"stop", "length", "content-filter", "error"})
_NON_TERMINAL_FINISHES = frozenset({"tool-calls"})


class OpenCodeV2Error(RuntimeError):
    """Raised when the V2 transport cannot run."""


class _V2Unavailable(OpenCodeV2Error):
    """The server does not expose the V2 API; V1 fallback is allowed."""


class _ModelNotInCatalog(OpenCodeV2Error):
    """The V2 catalog lacks the task's model; V1 fallback is allowed."""


# ============================================================================
# Configuration helpers
# ============================================================================


def _extension_config(task: TaskConfig | None) -> dict[str, Any]:
    """Read the ``opencode`` extension block, accepting flat keys too."""
    if not task:
        return {}
    extensions = task.get("agent_extensions") or {}
    nested = extensions.get("opencode")
    config = dict(nested) if isinstance(nested, dict) else {}
    for key in ("transport", "base_url", "binary"):
        flat = extensions.get(f"opencode_{key}")
        if flat is not None and key not in config:
            config[key] = flat
    return config


def _configured_base_url(task: TaskConfig | None = None) -> str | None:
    """Return an explicitly configured server URL, if any."""
    config = _extension_config(task)
    for value in (
        config.get("base_url"),
        os.environ.get("OPENCODE_BASE_URL"),
        os.environ.get("OPENCODE_SERVER_URL"),
    ):
        if value:
            return str(value).rstrip("/")
    return None


def _transport_mode(task: TaskConfig | None = None) -> str:
    """Return ``"auto"``, ``"v1"``, or ``"v2"``."""
    config = _extension_config(task)
    raw = config.get("transport") or os.environ.get("AGENTABI_OPENCODE_TRANSPORT")
    mode = str(raw or "auto").strip().lower()
    return mode if mode in ("auto", "v1", "v2") else "auto"


def _opencode_binary(task: TaskConfig | None = None) -> str:
    """Return the CLI path or name to invoke."""
    config = _extension_config(task)
    return str(config.get("binary") or os.environ.get("OPENCODE_BINARY") or "opencode")


def _parse_version(text: str) -> tuple[int, int, int] | None:
    match = _VERSION_RE.search(text)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


_version_cache: dict[str, tuple[int, int, int] | None] = {}


def _cli_version(binary: str) -> tuple[int, int, int] | None:
    """Return the CLI version, cached per binary path."""
    if binary in _version_cache:
        return _version_cache[binary]
    version: tuple[int, int, int] | None = None
    try:
        completed = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        version = _parse_version(completed.stdout or completed.stderr)
    except (OSError, subprocess.SubprocessError):
        version = None
    _version_cache[binary] = version
    return version


def _free_port() -> int:
    """Reserve a free localhost port for the server we are about to spawn."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


# ============================================================================
# Turn state machine: V2 events -> IR events
# ============================================================================


def _tool_content(data: dict[str, Any]) -> str:
    """Flatten a tool result's content array into text."""
    parts = data.get("content")
    if isinstance(parts, list):
        texts = [
            str(part.get("text", ""))
            for part in parts
            if isinstance(part, dict) and part.get("type") == "text"
        ]
        if texts:
            return "".join(texts)
    structured = data.get("structured")
    if structured:
        return json.dumps(structured)
    result = data.get("result")
    if isinstance(result, str):
        return result
    return ""


def _error_text(error: Any) -> str:
    """Render an OpenCode error object (several shapes) as one line."""
    if isinstance(error, str):
        return error
    if not isinstance(error, dict):
        return ""
    inner = error.get("data")
    if isinstance(inner, dict) and inner.get("message"):
        name = error.get("name") or error.get("type")
        message = str(inner["message"])
        return f"{name}: {message}" if name else message
    for key in ("message", "name", "type"):
        value = error.get(key)
        if value:
            return str(value)
    return ""


def _usage_from_tokens(data: dict[str, Any]) -> UsageInfo:
    """Map a V2 ``tokens`` object to IR usage."""
    tokens = data.get("tokens") or {}
    usage: UsageInfo = {}
    for key, target in (
        ("input", "input_tokens"),
        ("output", "output_tokens"),
        ("reasoning", "reasoning_tokens"),
    ):
        value = tokens.get(key)
        if value:
            usage[target] = int(value)
    cache = tokens.get("cache") or {}
    if cache.get("read"):
        usage["cache_read_tokens"] = int(cache["read"])
    if cache.get("write"):
        usage["cache_creation_tokens"] = int(cache["write"])
    total = int(tokens.get("input") or 0) + int(tokens.get("output") or 0)
    if total:
        usage["total_tokens"] = total
    return usage


@dataclass
class _TurnState:
    """Translate one prompt's V2 events into IR events.

    Actions that need HTTP (permission replies, interrupts) are queued and
    executed by the provider after each event, so this class stays sync.
    """

    session_id: str
    max_turns: int | None = None
    permission_level: str | None = None
    prompt_message_id: str = ""
    armed: bool = False
    finished: bool = False
    model: str = ""
    steps: int = 0
    actions: list[tuple[str, str, str]] = field(default_factory=list)
    _text: list[str] = field(default_factory=list)
    _text_with_delta: set[str] = field(default_factory=set)
    _tool_started: dict[str, int] = field(default_factory=dict)

    def handle(self, payload: dict[str, Any]) -> list[IREvent]:
        """Map one V2 event envelope to zero or more IR events."""
        etype = str(payload.get("type") or "")
        data = payload.get("data")
        if not isinstance(data, dict):
            return []
        sid = data.get("sessionID")
        if sid and sid != self.session_id:
            return []
        if not self._arm(etype, data):
            return []
        handler = self._handlers().get(etype)
        if handler is None:
            return []
        return handler(data)

    def _arm(self, etype: str, data: dict[str, Any]) -> bool:
        """Ignore events that belong to an earlier turn of this session."""
        if self.armed:
            return True
        if etype in ("session.next.prompt.admitted", "session.next.prompted"):
            if not self.prompt_message_id or data.get("messageID") == (
                self.prompt_message_id
            ):
                self.armed = True
            return False
        if etype in ("session.next.step.failed", "session.error"):
            # A turn can fail before its first step event, for example when
            # the session model is not in the server's catalog. The event
            # names this session and only one prompt is in flight, so it
            # starts the turn; otherwise the failure is dropped and the
            # caller waits for the timeout.
            if self.prompt_message_id:
                self.armed = True
            return self.armed
        if etype == "session.next.step.started" and self.prompt_message_id:
            self.armed = True
        return self.armed

    def _handlers(self) -> dict[str, Any]:
        return {
            "session.next.step.started": self._on_step_started,
            "session.next.step.ended": self._on_step_ended,
            "session.next.step.failed": self._on_step_failed,
            "session.next.text.started": self._on_text_started,
            "session.next.text.delta": self._on_text_delta,
            "session.next.text.ended": self._on_text_ended,
            "session.next.reasoning.delta": self._on_reasoning_delta,
            "session.next.tool.called": self._on_tool_called,
            "session.next.tool.success": self._on_tool_success,
            "session.next.tool.failed": self._on_tool_failed,
            "session.next.retried": self._on_retried,
            "permission.v2.asked": self._on_permission_asked,
            "question.v2.asked": self._on_question_asked,
            "session.error": self._on_session_error,
            "session.idle": self._on_session_idle,
        }

    # -- message lifecycle ------------------------------------------------

    def _on_step_started(self, data: dict[str, Any]) -> list[IREvent]:
        self.steps += 1
        if self.max_turns is not None and self.steps > self.max_turns:
            self.actions.append(("interrupt", "", ""))
            self.finished = True
            events: list[IREvent] = [
                ErrorEvent(
                    type="error",
                    error=f"Reached max_turns ({self.max_turns})",
                    is_fatal=True,
                )
            ]
            events.append(
                SessionEndEvent(type="session_end", session_id=self.session_id)
            )
            return events

        self._model_label(data)
        self._text = []
        self._text_with_delta = set()
        message: MessageStartEvent = {"type": "message_start", "role": "assistant"}
        message_id = data.get("assistantMessageID")
        if message_id:
            message["message_id"] = str(message_id)
        return [message]

    def _model_label(self, data: dict[str, Any]) -> str:
        model = data.get("model")
        if isinstance(model, dict):
            provider = model.get("providerID")
            name = model.get("id")
            if provider and name:
                self.model = f"{provider}/{name}"
                return self.model
            if name:
                self.model = str(name)
                return self.model
        return self.model

    def _on_step_ended(self, data: dict[str, Any]) -> list[IREvent]:
        events: list[IREvent] = []
        usage = _usage_from_tokens(data)
        if usage:
            usage_event: UsageEvent = {"type": "usage", "usage": usage}
            cost = data.get("cost")
            if isinstance(cost, (int, float)) and cost:
                usage_event["cost_usd"] = float(cost)
            if self.model:
                usage_event["model"] = self.model
            events.append(usage_event)

        finish = str(data.get("finish") or "")
        end: MessageEndEvent = {
            "type": "message_end",
            "stop_reason": stop_reason(finish, default="end_turn"),
        }
        if self._text:
            end["text"] = "".join(self._text)
        message_id = data.get("assistantMessageID")
        if message_id:
            end["message_id"] = str(message_id)
        events.append(end)

        if finish in _NON_TERMINAL_FINISHES:
            return events
        if finish and finish not in _TERMINAL_FINISHES:
            events.append(
                ErrorEvent(
                    type="error",
                    error=f"OpenCode step finished with '{finish}'",
                    is_fatal=True,
                )
            )
        self.finished = True
        events.append(SessionEndEvent(type="session_end", session_id=self.session_id))
        return events

    def _on_step_failed(self, data: dict[str, Any]) -> list[IREvent]:
        self.finished = True
        message = _error_text(data.get("error")) or "OpenCode step failed"
        return [
            ErrorEvent(type="error", error=message, is_fatal=True),
            SessionEndEvent(type="session_end", session_id=self.session_id),
        ]

    # -- text and reasoning ----------------------------------------------

    def _on_text_started(self, data: dict[str, Any]) -> list[IREvent]:
        return []

    def _on_text_delta(self, data: dict[str, Any]) -> list[IREvent]:
        text = str(data.get("delta") or "")
        if not text:
            return []
        self._text.append(text)
        text_id = str(data.get("textID") or "")
        if text_id:
            self._text_with_delta.add(text_id)
        delta: MessageDeltaEvent = {"type": "message_delta", "text": text}
        message_id = data.get("assistantMessageID")
        if message_id:
            delta["message_id"] = str(message_id)
        return [delta]

    def _on_text_ended(self, data: dict[str, Any]) -> list[IREvent]:
        """Backfill text when the server sent no deltas for this text part."""
        text_id = str(data.get("textID") or "")
        text = str(data.get("text") or "")
        if not text or text_id in self._text_with_delta:
            return []
        self._text_with_delta.add(text_id)
        self._text.append(text)
        return [MessageDeltaEvent(type="message_delta", text=text)]

    def _on_reasoning_delta(self, data: dict[str, Any]) -> list[IREvent]:
        reasoning = str(data.get("delta") or "")
        if not reasoning:
            return []
        return [ReasoningDeltaEvent(type="reasoning_delta", reasoning=reasoning)]

    # -- tools ------------------------------------------------------------

    def _on_tool_called(self, data: dict[str, Any]) -> list[IREvent]:
        call_id = str(data.get("callID") or "")
        if not call_id:
            return []
        timestamp = data.get("timestamp")
        if isinstance(timestamp, (int, float)):
            self._tool_started[call_id] = int(timestamp)
        tool_input = data.get("input")
        event: ToolUseEvent = {
            "type": "tool_use",
            "tool_use_id": call_id,
            "tool_name": str(data.get("tool") or ""),
            "tool_input": tool_input if isinstance(tool_input, dict) else {},
        }
        return [event]

    def _tool_result(self, data: dict[str, Any], *, is_error: bool) -> list[IREvent]:
        call_id = str(data.get("callID") or "")
        if not call_id:
            return []
        if is_error:
            content = _error_text(data.get("error")) or _tool_content(data)
        else:
            content = _tool_content(data)
        event: ToolResultEvent = {
            "type": "tool_result",
            "tool_use_id": call_id,
            "content": content,
        }
        if is_error:
            event["is_error"] = True
        started = self._tool_started.pop(call_id, None)
        timestamp = data.get("timestamp")
        if started is not None and isinstance(timestamp, (int, float)):
            event["duration_ms"] = max(0, int(timestamp) - started)
        return [event]

    def _on_tool_success(self, data: dict[str, Any]) -> list[IREvent]:
        return self._tool_result(data, is_error=False)

    def _on_tool_failed(self, data: dict[str, Any]) -> list[IREvent]:
        return self._tool_result(data, is_error=True)

    # -- retries, permissions, questions, errors --------------------------

    def _on_retried(self, data: dict[str, Any]) -> list[IREvent]:
        attempt = data.get("attempt")
        message = _error_text(data.get("error")) or "retrying"
        label = f"OpenCode retry #{attempt}: {message}" if attempt else message
        return [ErrorEvent(type="error", error=label)]

    def _on_permission_asked(self, data: dict[str, Any]) -> list[IREvent]:
        request_id = str(data.get("id") or "")
        action = str(data.get("action") or "tool")
        resources = data.get("resources")
        description = action
        if isinstance(resources, list) and resources:
            description = f"{action}: {', '.join(str(item) for item in resources)}"
        events: list[IREvent] = [
            PermissionRequestEvent(
                type="permission_request",
                request_id=request_id,
                tool_name=action,
                tool_use_id=request_id,
                tool_input={"resources": resources} if resources else {},
                description=description,
            )
        ]
        reply = self._permission_reply()
        unsupported = reply is None
        if unsupported:
            reply = "reject"
            self.finished = True
        self.actions.append(("permission", request_id, reply))
        events.append(
            PermissionResponseEvent(
                type="permission_response",
                request_id=request_id,
                tool_use_id=request_id,
                option_id=reply,
                outcome="selected",
                granted=reply != "reject",
            )
        )
        if unsupported:
            events.append(
                ErrorEvent(
                    type="error",
                    error=(
                        "OpenCode V2 asked for permission "
                        f"({description}) and agentabi cannot prompt; "
                        "set permissions.level to 'full_auto' or 'accept_edits'"
                    ),
                    is_fatal=True,
                )
            )
            events.append(
                SessionEndEvent(type="session_end", session_id=self.session_id)
            )
        return events

    def _permission_reply(self) -> str | None:
        if self.permission_level == "full_auto":
            return "always"
        if self.permission_level == "accept_edits":
            return "once"
        return None

    def _on_question_asked(self, data: dict[str, Any]) -> list[IREvent]:
        request_id = str(data.get("id") or "")
        self.actions.append(("question", request_id, ""))
        self.finished = True
        return [
            ErrorEvent(
                type="error",
                error=(
                    "OpenCode V2 asked a question; agentabi does not support "
                    "interactive questions on this transport"
                ),
                is_fatal=True,
            ),
            SessionEndEvent(type="session_end", session_id=self.session_id),
        ]

    def _on_session_error(self, data: dict[str, Any]) -> list[IREvent]:
        self.finished = True
        message = _error_text(data.get("error")) or "OpenCode session error"
        return [
            ErrorEvent(type="error", error=message, is_fatal=True),
            SessionEndEvent(type="session_end", session_id=self.session_id),
        ]

    def _on_session_idle(self, data: dict[str, Any]) -> list[IREvent]:
        if self.finished:
            return []
        self.finished = True
        return [SessionEndEvent(type="session_end", session_id=self.session_id)]


# ============================================================================
# Provider
# ============================================================================


@dataclass
class _Server:
    """A locally spawned `opencode serve` process."""

    process: asyncio.subprocess.Process
    base_url: str


class OpenCodeV2Provider:
    """Provider for the OpenCode V2 HTTP/SSE API."""

    def __init__(self) -> None:
        self._server: _Server | None = None
        self._base_url: str | None = None
        self._lock = asyncio.Lock()

    # -- availability -----------------------------------------------------

    @staticmethod
    def is_available() -> bool:
        """True when V2 can be used or must be tried.

        Explicit ``v1`` disables this provider so the chain falls through to
        the subprocess provider. Explicit ``v2`` only needs the CLI (or a
        configured server). ``auto`` needs a verified CLI version or a server
        URL; otherwise V1 is used.
        """
        mode = _transport_mode()
        if mode == "v1":
            return False
        if _configured_base_url():
            return True
        binary = _opencode_binary()
        if shutil.which(binary) is None:
            return False
        if mode == "v2":
            return True
        version = _cli_version(binary)
        return version is not None and version >= _MIN_V2_VERSION

    def capabilities(self) -> AgentCapabilities:
        return {
            "name": "OpenCode (V2 HTTP/SSE)",
            "agent_type": "opencode",
            "supports_streaming": True,
            "supports_mcp": True,
            "supports_session_resume": True,
            "supports_system_prompt": False,
            "supports_tool_filtering": False,
            "supports_permissions": True,
            "supports_multi_turn": True,
            "supports_thinking": True,
            # No V2 event carries a diff, and the provider emits no
            # file_diff event.
            "supports_file_diffs": False,
            # Measured against opencode 1.19.0's V2 schema: PromptInput
            # carries text, files, and agents only (no response schema),
            # and session create has no persistence-off flag. A session
            # takes one directory, so there is no way to add another.
            "supports_structured_output": False,
            "supports_ephemeral": False,
            "supports_additional_dirs": False,
            "supports_file_attachments": True,
            "transport": "http",
            "supported_settings": sorted(adapter_for("opencode_v2").supported),
            "settings_mechanism": adapter_for("opencode_v2").mechanism,
        }

    # -- lifecycle --------------------------------------------------------

    async def aclose(self) -> None:
        """Stop the server this provider spawned (no-op for external ones)."""
        server, self._server = self._server, None
        if server is None:
            return
        process = server.process
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()

    # -- streaming --------------------------------------------------------

    async def stream(self, task: TaskConfig) -> AsyncIterator[IREvent]:
        """Run one prompt against the V2 API, falling back to V1 in auto mode."""
        mode = _transport_mode(task)
        if mode == "v1":
            from .opencode_native import OpenCodeNativeProvider

            async for event in OpenCodeNativeProvider().stream(task):
                yield event
            return

        yielded = False
        try:
            async for event in self._stream_v2(task):
                yielded = True
                yield event
            return
        except _V2Unavailable:
            if mode != "auto" or yielded:
                raise
            await self.aclose()
        except _ModelNotInCatalog as exc:
            # The V2 server cannot serve this model, but the CLI can: V1
            # takes the model id from --model. Fall back rather than fail.
            if mode != "auto" or yielded:
                yield ErrorEvent(type="error", error=str(exc), is_fatal=True)
                return
            await self.aclose()
        except (OpenCodeV2Error, HTTPError) as exc:
            yield ErrorEvent(type="error", error=str(exc), is_fatal=True)
            return

        from .opencode_native import OpenCodeNativeProvider

        async for event in OpenCodeNativeProvider().stream(task):
            yield event

    async def run(self, task: TaskConfig) -> SessionResult:
        from .base import default_run

        return await default_run(self, task)

    async def _stream_v2(self, task: TaskConfig) -> AsyncIterator[IREvent]:
        base = await self._ensure_base_url(task)
        session_id = await self._ensure_session(base, task)
        model = await self._configure_model(base, session_id, task)
        state = _TurnState(
            session_id=session_id,
            max_turns=task.get("max_turns"),
            permission_level=(task.get("permissions") or {}).get("level"),
            model=model,
        )
        start: SessionStartEvent = {
            "type": "session_start",
            "session_id": session_id,
            "agent": "opencode",
        }
        if model:
            start["model"] = model
        yield start

        stream = SSEStream(f"{base}/api/event")
        try:
            await stream.open()
            await self._await_connected(stream)
            state.prompt_message_id = await self._send_prompt(base, session_id, task)
            turn = self._consume_turn(base, session_id, stream, state, task)
            async for event in turn:
                yield event
        finally:
            stream.close()

    async def _consume_turn(
        self,
        base: str,
        session_id: str,
        stream: SSEStream,
        state: _TurnState,
        task: TaskConfig,
    ) -> AsyncIterator[IREvent]:
        deadline = None
        timeout = task.get("timeout")
        if timeout:
            deadline = time.monotonic() + float(timeout)
        try:
            while not state.finished:
                wait = None
                if deadline is not None:
                    wait = max(0.0, deadline - time.monotonic())
                try:
                    message = await asyncio.wait_for(stream.__anext__(), wait)
                except StopAsyncIteration:
                    if not state.finished:
                        yield ErrorEvent(
                            type="error",
                            error="OpenCode event stream closed before the turn ended",
                            is_fatal=True,
                        )
                    break
                except asyncio.TimeoutError:
                    await self._interrupt(base, session_id)
                    yield ErrorEvent(
                        type="error",
                        error=f"Timed out after {timeout}s",
                        is_fatal=True,
                    )
                    break
                except (OSError, HTTPError) as exc:
                    yield ErrorEvent(
                        type="error",
                        error=f"OpenCode event stream failed: {exc}",
                        is_fatal=True,
                    )
                    break
                payload = _decode(message)
                if payload is None:
                    continue
                for event in state.handle(payload):
                    yield event
                await self._run_actions(base, session_id, state)
        except asyncio.CancelledError:
            await self._interrupt(base, session_id)
            raise

    # -- server and session setup -----------------------------------------

    async def _ensure_base_url(self, task: TaskConfig) -> str:
        """Return a server URL, spawning `opencode serve` when needed.

        A configured URL is authoritative: if it does not answer the V2 API
        the task fails instead of falling back to V1. A server this provider
        spawns itself is probed too, and in ``auto`` mode a probe or startup
        failure falls back to V1.
        """
        configured = _configured_base_url(task)
        if configured:
            self._base_url = configured
            try:
                await self._probe_v2(configured)
            except _V2Unavailable as exc:
                raise OpenCodeV2Error(
                    f"configured OpenCode server at {configured} is not usable: {exc}"
                ) from exc
            return configured

        auto = _transport_mode(task) == "auto"
        async with self._lock:
            if self._server and self._server.process.returncode is None:
                base = self._server.base_url
            else:
                await self.aclose()
                try:
                    self._server = await self._start_server(task)
                except OpenCodeV2Error as exc:
                    if auto:
                        raise _V2Unavailable(str(exc)) from exc
                    raise
                base = self._server.base_url
        try:
            await self._probe_v2(base)
        except _V2Unavailable:
            if not auto:
                raise
            await self.aclose()
            raise
        return base

    async def _start_server(self, task: TaskConfig) -> _Server:
        binary = _opencode_binary(task)
        if shutil.which(binary) is None:
            raise OpenCodeV2Error(f"OpenCode CLI not found: {binary}")
        port = _free_port()
        env = {**os.environ, **(task.get("env") or {})}
        try:
            process = await asyncio.create_subprocess_exec(
                binary,
                "serve",
                "--port",
                str(port),
                "--hostname",
                "127.0.0.1",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
                cwd=task.get("working_dir"),
            )
        except OSError as exc:
            raise OpenCodeV2Error(f"cannot start 'opencode serve': {exc}") from exc
        assert process.stdout is not None
        base_url = ""
        deadline = time.monotonic() + _SERVER_START_TIMEOUT
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise OpenCodeV2Error(
                        "timed out waiting for 'opencode serve' to listen"
                    )
                line = await asyncio.wait_for(process.stdout.readline(), remaining)
                if not line:
                    raise OpenCodeV2Error(
                        "'opencode serve' exited before it started listening"
                    )
                match = _LISTEN_RE.search(line.decode("utf-8", "replace"))
                if match:
                    base_url = match.group(1).rstrip("/")
                    break
            await self._wait_healthy(base_url, deadline)
        except BaseException:
            if process.returncode is None:
                process.kill()
                await process.wait()
            raise
        atexit.register(_terminate_process, process)
        return _Server(process=process, base_url=base_url)

    async def _wait_healthy(self, base_url: str, deadline: float) -> None:
        last_error = "no response"
        while time.monotonic() < deadline:
            try:
                status, _ = await request_json(
                    "GET", f"{base_url}/global/health", timeout=2.0
                )
                if status == 200:
                    return
                last_error = f"HTTP {status}"
            except HTTPError as exc:
                last_error = str(exc)
            await asyncio.sleep(0.2)
        raise OpenCodeV2Error(f"OpenCode server is not healthy: {last_error}")

    @staticmethod
    async def _probe_v2(base_url: str) -> None:
        """Raise _V2Unavailable when the server has no V2 API."""
        try:
            status, _ = await request_json(
                "GET", f"{base_url}/api/session/active", timeout=_HTTP_TIMEOUT
            )
        except HTTPError as exc:
            raise _V2Unavailable(f"cannot reach OpenCode server: {exc}") from exc
        if status != 200:
            raise _V2Unavailable(
                f"OpenCode server does not expose the V2 API (HTTP {status})"
            )

    async def _ensure_session(self, base: str, task: TaskConfig) -> str:
        """Reuse a session for resume, otherwise create a new one."""
        requested = task.get("session_id") if task.get("resume") else None
        if requested:
            status, _ = await request_json(
                "GET", f"{base}/api/session/{requested}", timeout=_HTTP_TIMEOUT
            )
            if status == 200:
                return str(requested)
            raise OpenCodeV2Error(
                f"OpenCode session not found: {requested} (HTTP {status})"
            )

        url = f"{base}/session"
        working_dir = task.get("working_dir")
        if working_dir:
            url = f"{url}?directory={_quote(str(Path(working_dir).expanduser()))}"
        body: dict[str, Any] = {}
        model = _split_model(task.get("model"))
        if model:
            body["model"] = model
        status, payload = await request_json(
            "POST", url, body=body, timeout=_HTTP_TIMEOUT
        )
        session_id = _session_id_from(payload)
        if status != 200 or not session_id:
            raise OpenCodeV2Error(
                f"cannot create an OpenCode session (HTTP {status}): {payload!r}"
            )
        return session_id

    async def _configure_model(
        self, base: str, session_id: str, task: TaskConfig
    ) -> str:
        """Set the session model and return its ``provider/model`` label."""
        model = _split_model(task.get("model"))
        if model is None:
            model = await self._default_model(base)
        if model is None:
            return ""
        await self._require_catalog_model(base, model)
        status, payload = await request_json(
            "POST",
            f"{base}/api/session/{session_id}/model",
            body={"model": model},
            timeout=_HTTP_TIMEOUT,
        )
        if status not in (200, 204):
            raise OpenCodeV2Error(
                f"cannot set the OpenCode model (HTTP {status}): {payload!r}"
            )
        return f"{model['providerID']}/{model['id']}"

    @staticmethod
    async def _require_catalog_model(base: str, model: dict[str, str]) -> None:
        """Fail fast when the server does not list the requested model.

        The V2 API resolves a session model against its own catalog
        (``GET /api/model``). A model missing from it fails inside the
        server without a ``session.next.step.failed`` event on the event
        stream, so the caller would wait for the turn timeout. Measured
        with opencode 1.19.0: a model added to the config through
        ``OPENCODE_CONFIG`` is usable through ``opencode run`` (V1) but
        absent from the V2 catalog.

        The check is skipped when the endpoint does not answer, so an
        older server keeps working.

        Raises:
            OpenCodeV2Error: If the catalog answers and lacks the model.
        """
        wanted = (model["providerID"], model["id"])
        # The catalog is populated asynchronously right after the server
        # starts (measured with opencode 1.19.0: empty, then 9030
        # placeholder entries, then the real 223 about 1.5 s in). Poll
        # briefly so a fresh server can still answer, and only judge once
        # the wanted provider is listed.
        deadline = time.monotonic() + _CATALOG_WAIT
        while True:
            try:
                status, payload = await request_json(
                    "GET", f"{base}/api/model", timeout=_HTTP_TIMEOUT
                )
            except HTTPError:
                return
            if status != 200:
                # No catalog endpoint here; there is nothing to judge.
                return
            entries = payload.get("data") if isinstance(payload, dict) else payload
            if isinstance(entries, list) and entries:
                available = {
                    (str(item.get("providerID")), str(item.get("id")))
                    for item in entries
                    if isinstance(item, dict)
                }
                if wanted in available:
                    return
                if any(provider == wanted[0] for provider, _ in available):
                    listed = ", ".join(
                        sorted(f"{p}/{m}" for p, m in available if p == wanted[0])
                    )
                    raise _ModelNotInCatalog(
                        f"OpenCode server does not offer {wanted[0]}/{wanted[1]} "
                        f"(the server lists: {listed})"
                    )
            if time.monotonic() >= deadline:
                return
            await asyncio.sleep(_CATALOG_POLL)

    @staticmethod
    async def _default_model(base: str) -> dict[str, Any] | None:
        """Read the configured default model, used when the task has none."""
        try:
            status, payload = await request_json(
                "GET", f"{base}/config", timeout=_HTTP_TIMEOUT
            )
        except HTTPError:
            return None
        if status != 200 or not isinstance(payload, dict):
            return None
        return _split_model(payload.get("model"))

    async def _send_prompt(self, base: str, session_id: str, task: TaskConfig) -> str:
        """POST the prompt and return the admitted user message id."""
        prompt: dict[str, Any] = {"text": str(task.get("prompt") or "")}
        files = _prompt_files(task.get("files"))
        if files:
            prompt["files"] = files
        status, payload = await request_json(
            "POST",
            f"{base}/api/session/{session_id}/prompt",
            body={"prompt": prompt, "delivery": "steer"},
            timeout=_HTTP_TIMEOUT,
        )
        if status not in (200, 201, 202):
            raise OpenCodeV2Error(
                f"cannot send the prompt (HTTP {status}): {payload!r}"
            )
        data = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(data, dict) and data.get("id"):
            return str(data["id"])
        return ""

    # -- stream helpers ---------------------------------------------------

    @staticmethod
    async def _await_connected(stream: SSEStream) -> None:
        """Wait until the SSE subscription is live before prompting."""
        deadline = time.monotonic() + _SUBSCRIBE_TIMEOUT
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            try:
                message = await asyncio.wait_for(stream.__anext__(), remaining)
            except (asyncio.TimeoutError, StopAsyncIteration):
                return
            payload = _decode(message)
            if payload is not None and payload.get("type") == "server.connected":
                return

    async def _run_actions(self, base: str, session_id: str, state: _TurnState) -> None:
        """Execute queued HTTP actions (permission replies, questions)."""
        actions, state.actions = state.actions, []
        for kind, request_id, value in actions:
            try:
                if kind == "permission":
                    await request(
                        "POST",
                        f"{base}/api/session/{session_id}/permission/"
                        f"{request_id}/reply",
                        body={"reply": value},
                        timeout=_HTTP_TIMEOUT,
                    )
                elif kind == "question":
                    await request(
                        "POST",
                        f"{base}/api/session/{session_id}/question/{request_id}/reject",
                        timeout=_HTTP_TIMEOUT,
                    )
                elif kind == "interrupt":
                    await self._interrupt(base, session_id)
            except HTTPError:
                continue

    @staticmethod
    async def _interrupt(base: str, session_id: str) -> None:
        """Best-effort cancel of the running turn."""
        try:
            await request(
                "POST",
                f"{base}/api/session/{session_id}/interrupt",
                timeout=5.0,
            )
        except HTTPError:
            return


# ============================================================================
# Small helpers
# ============================================================================


def _terminate_process(process: asyncio.subprocess.Process) -> None:
    """Best-effort stop of a server we spawned, at interpreter exit."""
    if process.returncode is None:
        try:
            process.terminate()
        except ProcessLookupError:
            return


def _decode(message: SSEMessage) -> dict[str, Any] | None:
    """Decode an SSE message payload as a JSON object."""
    try:
        payload = json.loads(message.data)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _split_model(value: Any) -> dict[str, Any] | None:
    """Convert ``provider/model`` into a V2 ModelRef."""
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if "/" not in raw:
        return None
    provider, name = raw.split("/", 1)
    if not provider or not name:
        return None
    return {"providerID": provider, "id": name}


def _session_id_from(payload: Any) -> str:
    """Read a session id from a create-session response."""
    if not isinstance(payload, dict):
        return ""
    if payload.get("id"):
        return str(payload["id"])
    data = payload.get("data")
    if isinstance(data, dict) and data.get("id"):
        return str(data["id"])
    return ""


def _prompt_files(files: Any) -> list[dict[str, str]]:
    """Convert TaskConfig.files paths into V2 prompt file attachments."""
    if not files:
        return []
    attachments: list[dict[str, str]] = []
    for item in files:
        path = Path(str(item)).expanduser()
        if not path.is_absolute():
            path = path.absolute()
        mime, _ = mimetypes.guess_type(path.name)
        attachments.append(
            {
                "uri": path.as_uri(),
                "mime": mime or "application/octet-stream",
                "name": path.name,
            }
        )
    return attachments


def _quote(value: str) -> str:
    """Percent-encode a query-string value."""
    return quote(value, safe="")


__all__ = ["OpenCodeV2Error", "OpenCodeV2Provider"]
