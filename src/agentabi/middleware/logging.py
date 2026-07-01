"""
agentabi - Logging Middleware

Logs task execution and IR events at configurable levels.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from typing import Callable

from ..types.ir.events import IREvent
from ..types.ir.task import TaskConfig

StreamHandler = Callable[[TaskConfig], AsyncIterator[IREvent]]

_DEFAULT_LOGGER = logging.getLogger("agentabi.middleware")


class LoggingMiddleware:
    """Middleware that logs task execution and IR events.

    Logs task start/end at INFO level and individual events at DEBUG
    level. Uses the ``agentabi.middleware`` logger by default.

    Args:
        logger: Logger instance to use. Defaults to ``agentabi.middleware``.
        level: Log level for event-level messages. Defaults to DEBUG.
        log_events: Whether to log individual IR events. Defaults to True.
        log_event_types: If set, only log events whose ``type`` is in
            this set. Defaults to None (log all events).

    Example::

        import logging
        logging.basicConfig(level=logging.DEBUG)

        session = Session(
            agent="claude_code",
            middleware=[LoggingMiddleware()],
        )
    """

    def __init__(
        self,
        *,
        logger: logging.Logger | None = None,
        level: int = logging.DEBUG,
        log_events: bool = True,
        log_event_types: set[str] | None = None,
    ) -> None:
        self._logger = logger or _DEFAULT_LOGGER
        self._level = level
        self._log_events = log_events
        self._log_event_types = log_event_types

    def __call__(self, handler: StreamHandler) -> StreamHandler:
        """Wrap a StreamHandler with logging."""
        logger = self._logger
        level = self._level
        log_events = self._log_events
        log_event_types = self._log_event_types

        async def wrapper(task: TaskConfig) -> AsyncIterator[IREvent]:
            prompt_preview = task["prompt"][:80]
            agent = task.get("agent", "unknown")
            logger.info("Task started: agent=%s prompt=%r", agent, prompt_preview)

            start = time.monotonic()
            event_count = 0
            failed = False

            try:
                async for event in handler(task):
                    event_count += 1
                    if log_events:
                        etype = event.get("type", "unknown")
                        if log_event_types is None or etype in log_event_types:
                            logger.log(level, "Event #%d: type=%s", event_count, etype)
                    yield event
            except Exception:
                failed = True
                raise
            finally:
                elapsed = time.monotonic() - start
                if failed:
                    logger.error(
                        "Task failed: agent=%s elapsed=%.2fs events=%d",
                        agent,
                        elapsed,
                        event_count,
                    )
                else:
                    logger.info(
                        "Task completed: agent=%s elapsed=%.2fs events=%d",
                        agent,
                        elapsed,
                        event_count,
                    )

        return wrapper

    def __repr__(self) -> str:
        return (
            f"LoggingMiddleware(logger={self._logger.name!r}, "
            f"level={self._level}, log_events={self._log_events})"
        )


__all__ = ["LoggingMiddleware"]
