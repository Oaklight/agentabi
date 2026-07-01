"""Tests for the middleware pipeline."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any, cast
from unittest.mock import patch

import pytest

from agentabi.middleware import (
    LoggingMiddleware,
    Middleware,
    StreamHandler,
    TimeoutMiddleware,
    UsageMeterMiddleware,
    chain_middleware,
)
from agentabi.types.ir.events import IREvent
from agentabi.types.ir.task import TaskConfig

# ============================================================================
# Test helpers
# ============================================================================


def _make_task(prompt: str = "test prompt", **kwargs: Any) -> TaskConfig:
    """Create a minimal TaskConfig for testing."""
    task: dict[str, Any] = {"prompt": prompt, "agent": "claude_code"}
    task.update(kwargs)
    return cast(TaskConfig, task)


async def _fake_stream(task: TaskConfig) -> AsyncIterator[IREvent]:
    """A minimal fake stream handler that emits a predictable event sequence."""
    yield cast(IREvent, {"type": "session_start", "session_id": "test-123"})
    yield cast(IREvent, {"type": "message_delta", "text": "Hello "})
    yield cast(IREvent, {"type": "message_delta", "text": "world"})
    yield cast(IREvent, {"type": "message_end", "text": "Hello world"})
    yield cast(
        IREvent,
        {
            "type": "usage",
            "usage": {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
            "cost_usd": 0.005,
        },
    )
    yield cast(IREvent, {"type": "session_end", "session_id": "test-123"})


async def _slow_stream(task: TaskConfig) -> AsyncIterator[IREvent]:
    """A stream handler that emits events slowly (for timeout tests)."""
    yield cast(IREvent, {"type": "session_start", "session_id": "slow-1"})
    await asyncio.sleep(0.1)
    yield cast(IREvent, {"type": "message_delta", "text": "first"})
    await asyncio.sleep(0.5)  # long pause
    yield cast(IREvent, {"type": "message_delta", "text": "second"})
    yield cast(IREvent, {"type": "session_end", "session_id": "slow-1"})


async def _collect_events(handler: StreamHandler, task: TaskConfig) -> list[IREvent]:
    """Consume a stream handler and collect all events."""
    events = []
    async for event in handler(task):
        events.append(event)
    return events


# ============================================================================
# Core pipeline tests
# ============================================================================


class TestChainMiddleware:
    """Test the chain_middleware function."""

    async def test_empty_middleware_returns_handler_unchanged(self):
        task = _make_task()
        events = await _collect_events(chain_middleware(_fake_stream, []), task)
        assert len(events) == 6
        assert events[0]["type"] == "session_start"

    async def test_single_middleware_wraps_handler(self):
        call_log: list[str] = []

        class TrackingMiddleware:
            def __call__(self, handler: StreamHandler) -> StreamHandler:
                async def wrapper(task: TaskConfig) -> AsyncIterator[IREvent]:
                    call_log.append("before")
                    async for event in handler(task):
                        yield event
                    call_log.append("after")

                return wrapper

        pipeline = chain_middleware(_fake_stream, [TrackingMiddleware()])
        events = await _collect_events(pipeline, _make_task())

        assert len(events) == 6
        assert call_log == ["before", "after"]

    async def test_middleware_ordering_is_onion(self):
        """First middleware in list is outermost (runs first/last)."""
        order: list[str] = []

        def make_mw(name: str) -> Middleware:
            def middleware(handler: StreamHandler) -> StreamHandler:
                async def wrapper(task: TaskConfig) -> AsyncIterator[IREvent]:
                    order.append(f"{name}:enter")
                    async for event in handler(task):
                        yield event
                    order.append(f"{name}:exit")

                return wrapper

            return middleware

        pipeline = chain_middleware(_fake_stream, [make_mw("A"), make_mw("B")])
        await _collect_events(pipeline, _make_task())

        assert order == ["A:enter", "B:enter", "B:exit", "A:exit"]

    async def test_middleware_can_modify_task(self):
        """Middleware can modify TaskConfig before passing to next handler."""
        seen_prompts: list[str] = []

        def capture_mw(handler: StreamHandler) -> StreamHandler:
            async def wrapper(task: TaskConfig) -> AsyncIterator[IREvent]:
                seen_prompts.append(task["prompt"])
                async for event in handler(task):
                    yield event

            return wrapper

        def modify_mw(handler: StreamHandler) -> StreamHandler:
            async def wrapper(task: TaskConfig) -> AsyncIterator[IREvent]:
                task = {**task, "prompt": task["prompt"].upper()}
                async for event in handler(task):
                    yield event

            return wrapper

        pipeline = chain_middleware(_fake_stream, [modify_mw, capture_mw])
        await _collect_events(pipeline, _make_task("hello"))

        assert seen_prompts == ["HELLO"]

    async def test_middleware_can_filter_events(self):
        """Middleware can filter out specific events."""

        def filter_mw(handler: StreamHandler) -> StreamHandler:
            async def wrapper(task: TaskConfig) -> AsyncIterator[IREvent]:
                async for event in handler(task):
                    if event.get("type") != "message_delta":
                        yield event

            return wrapper

        pipeline = chain_middleware(_fake_stream, [filter_mw])
        events = await _collect_events(pipeline, _make_task())

        types = [e["type"] for e in events]
        assert "message_delta" not in types
        assert "session_start" in types
        assert "session_end" in types

    async def test_middleware_can_transform_events(self):
        """Middleware can transform events before yielding."""

        def upper_mw(handler: StreamHandler) -> StreamHandler:
            async def wrapper(task: TaskConfig) -> AsyncIterator[IREvent]:
                async for event in handler(task):
                    if event.get("type") == "message_delta":
                        event = cast(IREvent, {**event, "text": event["text"].upper()})
                    yield event

            return wrapper

        pipeline = chain_middleware(_fake_stream, [upper_mw])
        events = await _collect_events(pipeline, _make_task())

        deltas = [e for e in events if e.get("type") == "message_delta"]
        assert deltas[0]["text"] == "HELLO "
        assert deltas[1]["text"] == "WORLD"


# ============================================================================
# LoggingMiddleware tests
# ============================================================================


class TestLoggingMiddleware:
    """Test LoggingMiddleware."""

    async def test_logs_task_start_and_end(self, caplog):
        with caplog.at_level(logging.INFO, logger="agentabi.middleware"):
            mw = LoggingMiddleware()
            pipeline = chain_middleware(_fake_stream, [mw])
            await _collect_events(pipeline, _make_task("my prompt"))

        assert any("Task started" in r.message for r in caplog.records)
        assert any("Task completed" in r.message for r in caplog.records)
        assert any("my prompt" in r.message for r in caplog.records)

    async def test_logs_events_at_debug(self, caplog):
        with caplog.at_level(logging.DEBUG, logger="agentabi.middleware"):
            mw = LoggingMiddleware()
            pipeline = chain_middleware(_fake_stream, [mw])
            await _collect_events(pipeline, _make_task())

        event_logs = [r for r in caplog.records if "Event #" in r.message]
        assert len(event_logs) == 6

    async def test_log_events_disabled(self, caplog):
        with caplog.at_level(logging.DEBUG, logger="agentabi.middleware"):
            mw = LoggingMiddleware(log_events=False)
            pipeline = chain_middleware(_fake_stream, [mw])
            await _collect_events(pipeline, _make_task())

        event_logs = [r for r in caplog.records if "Event #" in r.message]
        assert len(event_logs) == 0

    async def test_log_event_types_filter(self, caplog):
        with caplog.at_level(logging.DEBUG, logger="agentabi.middleware"):
            mw = LoggingMiddleware(log_event_types={"usage", "error"})
            pipeline = chain_middleware(_fake_stream, [mw])
            await _collect_events(pipeline, _make_task())

        event_logs = [r for r in caplog.records if "Event #" in r.message]
        assert len(event_logs) == 1  # only the usage event
        assert "usage" in event_logs[0].message

    async def test_logs_error_on_exception(self, caplog):
        async def failing_stream(task: TaskConfig) -> AsyncIterator[IREvent]:
            yield cast(IREvent, {"type": "session_start", "session_id": "fail"})
            raise RuntimeError("boom")

        with caplog.at_level(logging.ERROR, logger="agentabi.middleware"):
            mw = LoggingMiddleware()
            pipeline = chain_middleware(failing_stream, [mw])

            with pytest.raises(RuntimeError, match="boom"):
                await _collect_events(pipeline, _make_task())

        assert any("Task failed" in r.message for r in caplog.records)

    async def test_custom_logger(self, caplog):
        custom = logging.getLogger("my.custom.logger")
        with caplog.at_level(logging.INFO, logger="my.custom.logger"):
            mw = LoggingMiddleware(logger=custom)
            pipeline = chain_middleware(_fake_stream, [mw])
            await _collect_events(pipeline, _make_task())

        assert any(
            r.name == "my.custom.logger" and "Task started" in r.message
            for r in caplog.records
        )

    def test_repr(self):
        mw = LoggingMiddleware()
        r = repr(mw)
        assert "LoggingMiddleware" in r
        assert "agentabi.middleware" in r


# ============================================================================
# UsageMeterMiddleware tests
# ============================================================================


class TestUsageMeterMiddleware:
    """Test UsageMeterMiddleware."""

    async def test_accumulates_usage(self):
        meter = UsageMeterMiddleware()
        pipeline = chain_middleware(_fake_stream, [meter])

        await _collect_events(pipeline, _make_task())

        summary = meter.summary
        assert summary["call_count"] == 1
        assert summary["total_input_tokens"] == 100
        assert summary["total_output_tokens"] == 50
        assert summary["total_tokens"] == 150
        assert summary["total_cost_usd"] == pytest.approx(0.005)

    async def test_accumulates_across_calls(self):
        meter = UsageMeterMiddleware()
        pipeline = chain_middleware(_fake_stream, [meter])

        await _collect_events(pipeline, _make_task())
        await _collect_events(pipeline, _make_task())

        summary = meter.summary
        assert summary["call_count"] == 2
        assert summary["total_input_tokens"] == 200
        assert summary["total_output_tokens"] == 100
        assert summary["total_tokens"] == 300
        assert summary["total_cost_usd"] == pytest.approx(0.010)

    async def test_reset(self):
        meter = UsageMeterMiddleware()
        pipeline = chain_middleware(_fake_stream, [meter])

        await _collect_events(pipeline, _make_task())
        meter.reset()

        summary = meter.summary
        assert summary["call_count"] == 0
        assert summary["total_input_tokens"] == 0
        assert summary["total_cost_usd"] == pytest.approx(0.0)

    async def test_events_pass_through(self):
        """Usage meter should not modify or drop any events."""
        meter = UsageMeterMiddleware()
        pipeline = chain_middleware(_fake_stream, [meter])

        events = await _collect_events(pipeline, _make_task())
        assert len(events) == 6

    async def test_no_usage_event(self):
        """Handle streams with no usage events gracefully."""

        async def no_usage_stream(task: TaskConfig) -> AsyncIterator[IREvent]:
            yield cast(IREvent, {"type": "session_start", "session_id": "x"})
            yield cast(IREvent, {"type": "message_delta", "text": "hi"})
            yield cast(IREvent, {"type": "session_end", "session_id": "x"})

        meter = UsageMeterMiddleware()
        pipeline = chain_middleware(no_usage_stream, [meter])
        await _collect_events(pipeline, _make_task())

        summary = meter.summary
        assert summary["call_count"] == 1
        assert summary["total_input_tokens"] == 0
        assert summary["total_cost_usd"] == pytest.approx(0.0)

    async def test_cache_tokens(self):
        """Tracks cache_read and cache_creation tokens."""

        async def cache_stream(task: TaskConfig) -> AsyncIterator[IREvent]:
            yield cast(
                IREvent,
                {
                    "type": "usage",
                    "usage": {
                        "input_tokens": 200,
                        "output_tokens": 80,
                        "cache_read_tokens": 150,
                        "cache_creation_tokens": 50,
                        "total_tokens": 280,
                    },
                    "cost_usd": 0.01,
                },
            )

        meter = UsageMeterMiddleware()
        pipeline = chain_middleware(cache_stream, [meter])
        await _collect_events(pipeline, _make_task())

        summary = meter.summary
        assert summary["total_cache_read_tokens"] == 150
        assert summary["total_cache_creation_tokens"] == 50

    def test_repr(self):
        meter = UsageMeterMiddleware()
        r = repr(meter)
        assert "UsageMeterMiddleware" in r
        assert "calls=0" in r


# ============================================================================
# TimeoutMiddleware tests
# ============================================================================


class TestTimeoutMiddleware:
    """Test TimeoutMiddleware."""

    async def test_normal_stream_completes(self):
        mw = TimeoutMiddleware(10.0)
        pipeline = chain_middleware(_fake_stream, [mw])
        events = await _collect_events(pipeline, _make_task())
        assert len(events) == 6

    async def test_timeout_fires_on_slow_stream(self):
        mw = TimeoutMiddleware(0.15)
        pipeline = chain_middleware(_slow_stream, [mw])

        with pytest.raises(asyncio.TimeoutError):
            await _collect_events(pipeline, _make_task())

    async def test_timeout_allows_fast_enough_stream(self):
        mw = TimeoutMiddleware(5.0)
        pipeline = chain_middleware(_slow_stream, [mw])
        events = await _collect_events(pipeline, _make_task())
        assert len(events) == 4

    def test_invalid_timeout_raises(self):
        with pytest.raises(ValueError, match="positive"):
            TimeoutMiddleware(0)

        with pytest.raises(ValueError, match="positive"):
            TimeoutMiddleware(-1)

    def test_repr(self):
        mw = TimeoutMiddleware(30.5)
        assert repr(mw) == "TimeoutMiddleware(timeout=30.5)"


# ============================================================================
# Session integration tests
# ============================================================================


class TestSessionMiddleware:
    """Test middleware integration with Session."""

    @patch("shutil.which", return_value="/usr/bin/claude")
    def test_session_accepts_middleware(self, mock_which):
        from agentabi import Session

        mw = LoggingMiddleware()
        session = Session(agent="claude_code", middleware=[mw])
        assert len(session.middleware) == 1

    @patch("shutil.which", return_value="/usr/bin/claude")
    def test_session_no_middleware_by_default(self, mock_which):
        from agentabi import Session

        session = Session(agent="claude_code")
        assert session.middleware == []

    @patch("shutil.which", return_value="/usr/bin/claude")
    def test_add_middleware(self, mock_which):
        from agentabi import Session

        session = Session(agent="claude_code")
        mw = LoggingMiddleware()
        session.add_middleware(mw)
        assert len(session.middleware) == 1

    @patch("shutil.which", return_value="/usr/bin/claude")
    def test_middleware_property_returns_copy(self, mock_which):
        from agentabi import Session

        session = Session(agent="claude_code", middleware=[LoggingMiddleware()])
        mw_list = session.middleware
        mw_list.append(TimeoutMiddleware(10))
        # Original should be unchanged
        assert len(session.middleware) == 1


# ============================================================================
# Composition tests
# ============================================================================


class TestMiddlewareComposition:
    """Test multiple middleware working together."""

    async def test_logging_plus_usage(self, caplog):
        meter = UsageMeterMiddleware()
        logging_mw = LoggingMiddleware()

        pipeline = chain_middleware(_fake_stream, [logging_mw, meter])

        with caplog.at_level(logging.INFO, logger="agentabi.middleware"):
            events = await _collect_events(pipeline, _make_task())

        assert len(events) == 6
        assert meter.summary["total_input_tokens"] == 100
        assert any("Task completed" in r.message for r in caplog.records)

    async def test_timeout_plus_usage(self):
        meter = UsageMeterMiddleware()
        timeout = TimeoutMiddleware(5.0)

        pipeline = chain_middleware(_fake_stream, [timeout, meter])
        events = await _collect_events(pipeline, _make_task())

        assert len(events) == 6
        assert meter.summary["call_count"] == 1

    async def test_all_three_middleware(self, caplog):
        meter = UsageMeterMiddleware()
        logging_mw = LoggingMiddleware()
        timeout = TimeoutMiddleware(5.0)

        pipeline = chain_middleware(_fake_stream, [logging_mw, timeout, meter])

        with caplog.at_level(logging.INFO, logger="agentabi.middleware"):
            events = await _collect_events(pipeline, _make_task())

        assert len(events) == 6
        assert meter.summary["total_input_tokens"] == 100
        assert any("Task completed" in r.message for r in caplog.records)

    async def test_timeout_with_slow_stream_and_meter(self):
        """Timeout fires and meter still captures partial usage."""
        meter = UsageMeterMiddleware()
        timeout = TimeoutMiddleware(0.15)

        pipeline = chain_middleware(_slow_stream, [timeout, meter])

        with pytest.raises(asyncio.TimeoutError):
            await _collect_events(pipeline, _make_task())

        # Meter should have counted the call even though it timed out
        assert meter.summary["call_count"] == 1
