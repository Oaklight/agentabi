"""
agentabi - Timeout Middleware

Enforces a wall-clock timeout on streaming task execution.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Callable

from ..types.ir.events import IREvent
from ..types.ir.task import TaskConfig

StreamHandler = Callable[[TaskConfig], AsyncIterator[IREvent]]


class TimeoutMiddleware:
    """Middleware that enforces a wall-clock timeout on task execution.

    Wraps each ``__anext__`` call on the event stream with
    ``asyncio.wait_for``, so the timeout fires even if the stream
    stalls mid-iteration (not just between events).

    Raises ``asyncio.TimeoutError`` if the total elapsed time exceeds
    the configured timeout.

    Args:
        timeout: Maximum wall-clock seconds for the entire stream.
            Must be positive.

    Example::

        session = Session(
            agent="claude_code",
            middleware=[TimeoutMiddleware(120)],
        )

        try:
            result = await session.run(prompt="Long task")
        except asyncio.TimeoutError:
            print("Task timed out after 120s")

    Note:
        The timeout is per-stream invocation, not cumulative across
        multiple calls. Each ``stream()`` or ``run()`` gets a fresh
        timeout budget.
    """

    def __init__(self, timeout: float) -> None:
        if timeout <= 0:
            raise ValueError(f"timeout must be positive, got {timeout}")
        self._timeout = timeout

    def __call__(self, handler: StreamHandler) -> StreamHandler:
        """Wrap a StreamHandler with timeout enforcement."""
        timeout = self._timeout

        async def wrapper(task: TaskConfig) -> AsyncIterator[IREvent]:
            start = time.monotonic()
            aiter = handler(task).__aiter__()

            while True:
                elapsed = time.monotonic() - start
                remaining = timeout - elapsed
                if remaining <= 0:
                    raise asyncio.TimeoutError(
                        f"Task exceeded timeout of {timeout}s (elapsed: {elapsed:.1f}s)"
                    )
                try:
                    event = await asyncio.wait_for(aiter.__anext__(), timeout=remaining)
                except StopAsyncIteration:
                    break
                yield event

        return wrapper

    def __repr__(self) -> str:
        return f"TimeoutMiddleware(timeout={self._timeout})"


__all__ = ["TimeoutMiddleware"]
