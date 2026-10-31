"""IR backend: run any agentabi provider and map IR events to ACP updates.

This is the bridge for CLIs without a native ACP server (agy, claude,
codex, pi). The provider keeps owning CLI parsing; the backend only
translates the unified IR stream into ACP ``session/update`` payloads.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

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

from .base import split_prompt_blocks

_STOP_REASONS: dict[str, StopReason] = {
    "end_turn": StopReason.END_TURN,
    "stop": StopReason.END_TURN,
    "max_tokens": StopReason.MAX_TOKENS,
    "length": StopReason.MAX_TOKENS,
    "max_turn_requests": StopReason.MAX_TURN_REQUESTS,
    "refusal": StopReason.REFUSAL,
    "cancelled": StopReason.CANCELLED,
    "canceled": StopReason.CANCELLED,
    # Provider-specific spellings.
    "tool_use": StopReason.END_TURN,  # claude: turn yielded to a tool
    "toolUse": StopReason.END_TURN,  # pi
    "stop_sequence": StopReason.END_TURN,  # claude
    "aborted": StopReason.CANCELLED,  # pi: user abort
}

_TOOL_KINDS: dict[str, str] = {
    "read": "read",
    "view_file": "read",
    "read_resource": "read",
    "grep_search": "search",
    "list_dir": "search",
    "find_by_name": "search",
    "search_web": "fetch",
    "read_url_content": "fetch",
    "write_to_file": "edit",
    "replace_file_content": "edit",
    "multi_replace_file_content": "edit",
    "sed_file_content": "edit",
    "run_command": "execute",
    "command_status": "execute",
    "execute_browser_javascript": "execute",
    "delete_file": "delete",
    "move_file": "move",
}


@dataclass
class _SessionState:
    """Backend state for one ACP session."""

    cwd: str
    agent_session_id: str | None = None
    additional_dirs: list[str] = field(default_factory=list)
    session: Any = None


def _text_update(kind: str, text: str) -> dict[str, Any]:
    return {
        "sessionUpdate": kind,
        "content": {"type": "text", "text": text},
    }


def _to_acp_update(event: dict[str, Any]) -> dict[str, Any] | None:
    """Translate one IR event into an ACP ``session/update`` payload."""
    etype = event.get("type")

    if etype == "message_delta":
        text = event.get("text", "")
        return _text_update("agent_message_chunk", text) if text else None

    if etype == "reasoning_delta":
        text = event.get("reasoning", "")
        return _text_update("agent_thought_chunk", text) if text else None

    if etype == "tool_use":
        name = event.get("tool_name", "")
        return {
            "sessionUpdate": "tool_call",
            "toolCallId": event.get("tool_use_id", ""),
            "title": name,
            "kind": _TOOL_KINDS.get(name, "other"),
            "status": "in_progress",
            "rawInput": event.get("tool_input", {}),
        }

    if etype == "tool_result":
        content = str(event.get("content", ""))
        return {
            "sessionUpdate": "tool_call_update",
            "toolCallId": event.get("tool_use_id", ""),
            "status": "failed" if event.get("is_error") else "completed",
            "content": [
                {
                    "type": "content",
                    "content": {"type": "text", "text": content},
                }
            ],
        }

    return None


class IRBackend:
    """Backend that drives an agentabi provider through its IR stream."""

    def __init__(
        self,
        agent: str,
        *,
        session_factory: Callable[[], Any] | None = None,
    ) -> None:
        """Create a backend for *agent*.

        Args:
            agent: agentabi agent name (``agy``, ``claude_code``, ...).
            session_factory: Optional factory returning an object with an
                async ``stream(prompt, **kwargs)`` method. Defaults to
                ``agentabi.Session(agent=agent)``.
        """
        self.name = agent
        self._agent = agent
        self._session_factory = session_factory
        self._states: dict[str, _SessionState] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._queues: dict[str, asyncio.Queue[Any]] = {}
        self._cancelled: set[str] = set()
        self._results: dict[str, PromptResult] = {}
        self._errors: dict[str, str] = {}

    async def start(self) -> None:
        """Nothing to start; the provider is launched per prompt."""

    async def stop(self) -> None:
        """Cancel any in-flight prompt."""
        for session_id in list(self._tasks):
            await self.cancel(session_id)

    def _get_session(self, state: _SessionState) -> Any:
        if state.session is None:
            if self._session_factory is not None:
                state.session = self._session_factory()
            else:
                from agentabi import Session

                state.session = Session(agent=self._agent)
        return state.session

    async def initialize(self, params: InitializeParams) -> InitializeResult:
        return InitializeResult(
            protocol_version=params.protocol_version,
            agent_capabilities=AgentCapabilities(load_session=False),
        )

    async def new_session(self, params: NewSessionParams) -> NewSessionResult:
        session_id = f"agentabi-{uuid.uuid4().hex}"
        self._states[session_id] = _SessionState(
            cwd=params.cwd or ".",
            additional_dirs=list(params.additional_directories or []),
        )
        return NewSessionResult(session_id=session_id)

    async def load_session(
        self, params: LoadSessionParams
    ) -> AsyncIterator[dict[str, Any]]:
        raise NotImplementedError(f"{self._agent} session replay is not implemented")
        yield {}  # pragma: no cover

    async def prompt(self, params: PromptParams) -> AsyncIterator[dict[str, Any]]:
        state = self._states.get(params.session_id)
        if state is None:
            raise ValueError(f"unknown session: {params.session_id}")

        self._results.pop(params.session_id, None)
        self._errors.pop(params.session_id, None)
        self._cancelled.discard(params.session_id)
        text, _ = split_prompt_blocks(params.prompt)

        queue: asyncio.Queue[Any] = asyncio.Queue()

        async def pump() -> None:
            try:
                async for event in self._get_session(state).stream(
                    text,
                    working_dir=state.cwd,
                    **self._task_kwargs(state),
                ):
                    await queue.put(event)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - surfaced to the ACP client
                await queue.put(exc)
            finally:
                queue.put_nowait(None)

        task = asyncio.create_task(pump())
        self._tasks[params.session_id] = task
        self._queues[params.session_id] = queue
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                if isinstance(item, BaseException):
                    self._errors[params.session_id] = str(item)
                    break
                self._observe(item, state, params.session_id)
                update = _to_acp_update(item)
                if update is not None:
                    yield update
        finally:
            if not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            self._tasks.pop(params.session_id, None)
            self._queues.pop(params.session_id, None)

        cancelled = params.session_id in self._cancelled
        self._cancelled.discard(params.session_id)
        if cancelled:
            self._results[params.session_id] = PromptResult(
                stop_reason=StopReason.CANCELLED
            )

    @staticmethod
    def _task_kwargs(state: _SessionState) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        if state.agent_session_id:
            kwargs["session_id"] = state.agent_session_id
            kwargs["resume"] = True
        if state.additional_dirs:
            kwargs["additional_dirs"] = state.additional_dirs
        return kwargs

    def _observe(
        self, event: dict[str, Any], state: _SessionState, session_id: str
    ) -> None:
        etype = event.get("type")
        if etype == "session_start" and event.get("session_id"):
            state.agent_session_id = event["session_id"]
        elif etype == "message_end":
            reason = event.get("stop_reason", "")
            if not reason:
                # Providers that stream no stop reason completed normally.
                self._results[session_id] = PromptResult(
                    stop_reason=StopReason.END_TURN
                )
                return
            mapped = _STOP_REASONS.get(reason)
            if mapped is None:
                self._results[session_id] = PromptResult(
                    stop_reason=StopReason.UNKNOWN,
                    raw_stop_reason=reason,
                )
            else:
                self._results[session_id] = PromptResult(stop_reason=mapped)
        elif etype == "error" and event.get("is_fatal"):
            self._errors[session_id] = event.get("error", "agent error")

    def prompt_result(self, session_id: str) -> PromptResult | None:
        return self._results.get(session_id)

    def prompt_error(self, session_id: str) -> str | None:
        return self._errors.get(session_id)

    async def cancel(self, session_id: str) -> None:
        self._cancelled.add(session_id)
        task = self._tasks.get(session_id)
        if task is not None and not task.done():
            task.cancel()
        # Wake the prompt loop even if the pump task never started.
        queue = self._queues.get(session_id)
        if queue is not None:
            queue.put_nowait(None)

    async def close_session(self, session_id: str) -> None:
        await self.cancel(session_id)
        self._cancelled.discard(session_id)
        self._states.pop(session_id, None)
        self._results.pop(session_id, None)
        self._errors.pop(session_id, None)


__all__ = ["IRBackend"]
