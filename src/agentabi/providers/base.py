"""
agentabi - Provider Protocol

Single interface for all agent backends. Providers are the only
abstraction layer between Session and agent CLIs/SDKs.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from typing_extensions import Protocol, runtime_checkable

from ..types.ir.capabilities import AgentCapabilities
from ..types.ir.events import IREvent, UsageInfo
from ..types.ir.session import SessionResult, SessionStatus
from ..types.ir.task import TaskConfig


@runtime_checkable
class Provider(Protocol):
    """Single interface for all agent backends.

    Each provider wraps one agent CLI or SDK and translates
    between TaskConfig/IREvent and the agent's native format.

    Providers form fallback chains per agent (e.g., NativeProvider
    is tried before SDKProvider). The first available provider wins.
    """

    @staticmethod
    def is_available() -> bool:
        """Check if this provider can be used (CLI/SDK installed).

        Returns:
            True if the provider's dependencies are satisfied.
        """
        ...

    def capabilities(self) -> AgentCapabilities:
        """Declare supported features.

        Returns:
            AgentCapabilities describing what this provider supports.
        """
        ...

    def stream(self, task: TaskConfig) -> AsyncIterator[IREvent]:
        """Run task and yield IR events as they arrive.

        Args:
            task: Unified task configuration.

        Yields:
            IR events produced by the agent.
        """
        ...

    async def run(self, task: TaskConfig) -> SessionResult:
        """Run task and return aggregated result.

        Default implementation consumes stream() and aggregates events.

        Args:
            task: Unified task configuration.

        Returns:
            Aggregated session result.
        """
        ...


class _RunState:
    """Mutable accumulator for default_run event aggregation."""

    __slots__ = (
        "session_id",
        "delta_parts",
        "result_text",
        "status",
        "model",
        "cost_usd",
        "errors",
        "usage",
    )

    def __init__(self) -> None:
        self.session_id = ""
        self.delta_parts: list[str] = []
        self.result_text = ""
        self.status: SessionStatus = "success"
        self.model = ""
        self.cost_usd = 0.0
        self.errors: list[str] = []
        self.usage: UsageInfo = {}

    def handle(self, event: IREvent) -> None:
        """Dispatch a single IR event into the accumulator."""
        etype = event.get("type")
        if etype == "session_start":
            self.session_id = event.get("session_id", "")
            self.model = event.get("model", "")
        elif etype == "message_delta":
            text = event.get("text", "")
            if text:
                self.delta_parts.append(text)
        elif etype == "message_end":
            self._flush_message(event)
        elif etype == "usage":
            self.usage = event.get("usage", {})
            cost = event.get("cost_usd")
            if cost is not None:
                self.cost_usd = cost
        elif etype == "error":
            self.errors.append(event.get("error", ""))
            if event.get("is_fatal"):
                self.status = "error"

    def _flush_message(self, event: IREvent) -> None:
        text = event.get("text")
        if text:
            self.result_text = text
        elif self.delta_parts:
            self.result_text = "".join(self.delta_parts)
        self.delta_parts = []

    def build(self) -> SessionResult:
        result: SessionResult = {
            "session_id": self.session_id,
            "status": self.status,
        }
        if self.model:
            result["model"] = self.model
        if self.result_text:
            result["result_text"] = self.result_text
        if self.usage:
            result["usage"] = self.usage
        if self.cost_usd:
            result["cost_usd"] = self.cost_usd
        if self.errors:
            result["errors"] = self.errors
        return result


async def default_run(provider: Any, task: TaskConfig) -> SessionResult:
    """Default run() implementation that aggregates stream() events.

    Providers can use this as their run() body to avoid duplication.

    Args:
        provider: The provider instance (any object with stream()).
        task: Unified task configuration.

    Returns:
        Aggregated SessionResult built from stream events.
    """
    state = _RunState()
    async for event in provider.stream(task):
        state.handle(event)
    return state.build()


__all__ = [
    "Provider",
    "default_run",
]
