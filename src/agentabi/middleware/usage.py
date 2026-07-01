"""
agentabi - Usage Meter Middleware

Tracks cumulative token usage and cost across multiple task executions.
"""

from __future__ import annotations

import threading
from collections.abc import AsyncIterator
from typing import Callable

from typing_extensions import TypedDict

from ..types.ir.events import IREvent
from ..types.ir.task import TaskConfig

StreamHandler = Callable[[TaskConfig], AsyncIterator[IREvent]]


class UsageSummary(TypedDict, total=False):
    """Cumulative usage statistics across multiple task executions."""

    call_count: int
    total_input_tokens: int
    total_output_tokens: int
    total_cache_read_tokens: int
    total_cache_creation_tokens: int
    total_tokens: int
    total_cost_usd: float


class UsageMeterMiddleware:
    """Middleware that accumulates token usage and cost across calls.

    Watches for ``usage`` events in the stream and accumulates token
    counts and cost. The accumulated totals are available via the
    ``summary`` property at any time.

    Thread-safe: uses a lock for counter updates so the summary can
    be read from any thread.

    Example::

        meter = UsageMeterMiddleware()
        session = Session(agent="claude_code", middleware=[meter])

        await session.run(prompt="Task 1")
        await session.run(prompt="Task 2")

        print(meter.summary)
        # {'call_count': 2, 'total_input_tokens': 1500, ...}

        meter.reset()
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._call_count = 0
        self._input_tokens = 0
        self._output_tokens = 0
        self._cache_read_tokens = 0
        self._cache_creation_tokens = 0
        self._total_tokens = 0
        self._cost_usd = 0.0

    @property
    def summary(self) -> UsageSummary:
        """Return cumulative usage statistics.

        Returns:
            A UsageSummary dict with all accumulated counters.
        """
        with self._lock:
            result: UsageSummary = {
                "call_count": self._call_count,
                "total_input_tokens": self._input_tokens,
                "total_output_tokens": self._output_tokens,
                "total_tokens": self._total_tokens,
                "total_cost_usd": self._cost_usd,
            }
            if self._cache_read_tokens:
                result["total_cache_read_tokens"] = self._cache_read_tokens
            if self._cache_creation_tokens:
                result["total_cache_creation_tokens"] = self._cache_creation_tokens
            return result

    def reset(self) -> None:
        """Reset all accumulated counters to zero."""
        with self._lock:
            self._call_count = 0
            self._input_tokens = 0
            self._output_tokens = 0
            self._cache_read_tokens = 0
            self._cache_creation_tokens = 0
            self._total_tokens = 0
            self._cost_usd = 0.0

    def __call__(self, handler: StreamHandler) -> StreamHandler:
        """Wrap a StreamHandler with usage metering."""

        async def wrapper(task: TaskConfig) -> AsyncIterator[IREvent]:
            with self._lock:
                self._call_count += 1

            async for event in handler(task):
                if event.get("type") == "usage":
                    usage = event.get("usage", {})
                    cost = event.get("cost_usd")
                    with self._lock:
                        self._input_tokens += usage.get("input_tokens", 0)
                        self._output_tokens += usage.get("output_tokens", 0)
                        self._cache_read_tokens += usage.get("cache_read_tokens", 0)
                        self._cache_creation_tokens += usage.get(
                            "cache_creation_tokens", 0
                        )
                        self._total_tokens += usage.get("total_tokens", 0)
                        if cost is not None:
                            self._cost_usd += cost
                yield event

        return wrapper

    def __repr__(self) -> str:
        return (
            f"UsageMeterMiddleware(calls={self._call_count}, "
            f"tokens={self._total_tokens}, cost=${self._cost_usd:.4f})"
        )


__all__ = [
    "UsageMeterMiddleware",
    "UsageSummary",
]
