"""
agentabi - Provider Protocol

Single interface for all agent backends. Providers are the only
abstraction layer between Session and agent CLIs/SDKs.
"""

from __future__ import annotations

from typing import Any, AsyncIterator, List

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


async def default_run(provider: Any, task: TaskConfig) -> SessionResult:
    """Default run() implementation that aggregates stream() events.

    Providers can use this as their run() body to avoid duplication.

    Args:
        provider: The provider instance (any object with stream()).
        task: Unified task configuration.

    Returns:
        Aggregated SessionResult built from stream events.
    """
    session_id = ""
    result_text = ""
    status: SessionStatus = "success"
    model = ""
    cost_usd = 0.0
    errors: List[str] = []
    usage: UsageInfo = {}

    async for event in provider.stream(task):
        etype = event.get("type")
        if etype == "session_start":
            session_id = event.get("session_id", "")
            model = event.get("model", "")
        elif etype == "message_end":
            text = event.get("text")
            if text:
                result_text = text
        elif etype == "usage":
            usage = event.get("usage", {})
            cost = event.get("cost_usd")
            if cost is not None:
                cost_usd = cost
        elif etype == "error":
            errors.append(event.get("error", ""))
            if event.get("is_fatal"):
                status = "error"

    result: SessionResult = {
        "session_id": session_id,
        "status": status,
    }
    if model:
        result["model"] = model
    if result_text:
        result["result_text"] = result_text
    if usage:
        result["usage"] = usage
    if cost_usd:
        result["cost_usd"] = cost_usd
    if errors:
        result["errors"] = errors

    return result


__all__ = [
    "Provider",
    "default_run",
]
