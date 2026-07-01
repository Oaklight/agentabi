"""
agentabi - Middleware Pipeline

Composable middleware for intercepting Session.stream() calls.
Middleware can inspect/modify TaskConfig, filter/transform IREvents,
and run side effects (logging, metering, timeouts).

Architecture:
    Middleware follows the onion model (like ASGI). Each middleware
    wraps a ``StreamHandler`` and returns a new ``StreamHandler``.
    The first middleware in the list is the outermost wrapper.

Quick start::

    from agentabi.middleware import LoggingMiddleware, TimeoutMiddleware

    session = Session(
        agent="claude_code",
        middleware=[LoggingMiddleware(), TimeoutMiddleware(120)],
    )

Custom middleware::

    from agentabi.middleware import Middleware, StreamHandler

    class MyMiddleware:
        def __call__(self, handler: StreamHandler) -> StreamHandler:
            async def wrapper(task):
                task = {**task, "prompt": task["prompt"].strip()}
                async for event in handler(task):
                    yield event
            return wrapper
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Callable

from ..types.ir.events import IREvent
from ..types.ir.task import TaskConfig
from .logging import LoggingMiddleware
from .timeout import TimeoutMiddleware
from .usage import UsageMeterMiddleware

# ============================================================================
# Core type aliases
# ============================================================================

StreamHandler = Callable[[TaskConfig], AsyncIterator[IREvent]]
"""A callable that takes a TaskConfig and returns an async iterator of IREvents.

This is the fundamental unit of the middleware pipeline. The provider's
``stream()`` method is the innermost StreamHandler; each middleware wraps
it with additional behavior.
"""

Middleware = Callable[[StreamHandler], StreamHandler]
"""A callable that wraps a StreamHandler with additional behavior.

Middleware receives the next handler in the chain and returns a new
handler. The wrapper can:

- Modify TaskConfig before passing to the next handler (copy-on-mutate
  recommended: ``task = {**task, "key": new_value}``)
- Filter or transform yielded IREvents
- Run side effects (logging, timing, metering)
- Short-circuit the pipeline (e.g., timeout)
"""


# ============================================================================
# Pipeline chaining
# ============================================================================


def chain_middleware(
    handler: StreamHandler,
    middleware: Sequence[Middleware],
) -> StreamHandler:
    """Chain middleware around a base handler using the onion model.

    The first middleware in the sequence becomes the outermost wrapper.
    When the pipeline executes, control flows inward through each
    middleware, reaches the base handler, then flows back outward.

    Args:
        handler: The base StreamHandler (typically provider.stream).
        middleware: Sequence of middleware to apply. Empty sequence
            returns the handler unchanged.

    Returns:
        A new StreamHandler with all middleware applied.

    Example::

        pipeline = chain_middleware(
            provider.stream,
            [LoggingMiddleware(), TimeoutMiddleware(30)],
        )
        async for event in pipeline(task):
            ...
    """
    for mw in reversed(middleware):
        handler = mw(handler)
    return handler


__all__ = [
    # Core types
    "StreamHandler",
    "Middleware",
    # Pipeline
    "chain_middleware",
    # Built-in middleware
    "LoggingMiddleware",
    "TimeoutMiddleware",
    "UsageMeterMiddleware",
]
