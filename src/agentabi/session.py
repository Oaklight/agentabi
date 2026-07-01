"""
agentabi - Session

Consumer-facing API for interacting with agent CLIs.
Async-first design with sync convenience wrapper.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from typing import Any, cast

from .middleware import Middleware, chain_middleware
from .providers.base import Provider
from .providers.registry import resolve_provider
from .types.ir.events import IREvent
from .types.ir.session import SessionResult
from .types.ir.task import TaskConfig


class Session:
    """High-level session interface for interacting with agent CLIs.

    Supports an optional middleware pipeline that wraps the provider's
    ``stream()`` method. Middleware can inspect/modify TaskConfig,
    filter/transform IR events, and run side effects.

    Async-first API. Use run_sync() for synchronous scripts.

    Examples:
        # Async usage
        session = Session(agent="claude_code")
        result = await session.run(prompt="Fix the bug in auth.py")

        # Streaming
        async for event in session.stream(prompt="Explain this code"):
            if event["type"] == "message_delta":
                print(event["text"], end="")

        # With middleware
        from agentabi.middleware import LoggingMiddleware, TimeoutMiddleware

        session = Session(
            agent="claude_code",
            middleware=[LoggingMiddleware(), TimeoutMiddleware(120)],
        )

        # Add middleware after construction
        session.add_middleware(UsageMeterMiddleware())

        # Auto-detect agent
        session = Session()
    """

    def __init__(
        self,
        *,
        agent: str | None = None,
        model: str | None = None,
        prefer: str | None = None,
        middleware: Sequence[Middleware] | None = None,
    ) -> None:
        """Initialize a Session.

        Args:
            agent: Agent type to use (e.g., "claude_code", "codex").
                If None, auto-detects the first available agent.
            model: Default model to use. Can be overridden per-task.
            prefer: Preferred provider type. "native" (default) tries
                native subprocess providers first; "sdk" tries SDK
                providers first. Falls back if preferred is unavailable.
            middleware: Optional sequence of middleware to apply to the
                stream pipeline. The first middleware in the sequence
                is the outermost wrapper.

        Raises:
            AgentNotAvailable: If no provider is available.
        """
        if agent is None:
            from .auto_detect import get_default_agent

            agent = get_default_agent()

        self._agent = agent
        self._model = model
        self._provider: Provider = resolve_provider(agent, prefer=prefer)
        self._middleware: list[Middleware] = list(middleware) if middleware else []

    @property
    def agent(self) -> str:
        """The agent type being used."""
        return self._agent

    @property
    def model(self) -> str | None:
        """The default model, if set."""
        return self._model

    @property
    def provider(self) -> Provider:
        """The underlying provider instance."""
        return self._provider

    @property
    def middleware(self) -> list[Middleware]:
        """The current middleware stack (mutable copy)."""
        return list(self._middleware)

    def add_middleware(self, mw: Middleware) -> None:
        """Append a middleware to the pipeline.

        The new middleware becomes the outermost wrapper (runs first
        on entry, last on exit).

        Args:
            mw: Middleware to add.
        """
        self._middleware.append(mw)

    async def stream(
        self,
        prompt: str,
        *,
        working_dir: str | None = None,
        max_turns: int | None = None,
        system_prompt: str | None = None,
        **kwargs,
    ) -> AsyncIterator[IREvent]:
        """Stream IR events from a task execution.

        If middleware is configured, the provider's stream is wrapped
        by the middleware pipeline before events are yielded.

        Args:
            prompt: The task instruction to send to the agent.
            working_dir: Optional working directory.
            max_turns: Optional max turns limit.
            system_prompt: Optional system prompt.
            **kwargs: Additional TaskConfig fields.

        Yields:
            IR events as they are produced by the agent.
        """
        task = self._build_task(
            prompt,
            working_dir=working_dir,
            max_turns=max_turns,
            system_prompt=system_prompt,
            **kwargs,
        )
        handler = self._get_stream_handler()
        async for event in handler(task):
            yield event

    async def run(
        self,
        prompt: str,
        *,
        working_dir: str | None = None,
        max_turns: int | None = None,
        system_prompt: str | None = None,
        **kwargs,
    ) -> SessionResult:
        """Run a task to completion.

        When middleware is configured, ``run()`` consumes the
        middleware-wrapped stream (not the raw provider stream),
        so all middleware effects apply.

        Args:
            prompt: The task instruction to send to the agent.
            working_dir: Optional working directory.
            max_turns: Optional max turns limit.
            system_prompt: Optional system prompt.
            **kwargs: Additional TaskConfig fields.

        Returns:
            Aggregated SessionResult.
        """
        if not self._middleware:
            # Fast path: no middleware, delegate directly to provider
            task = self._build_task(
                prompt,
                working_dir=working_dir,
                max_turns=max_turns,
                system_prompt=system_prompt,
                **kwargs,
            )
            return await self._provider.run(task)

        # With middleware: consume the wrapped stream via default_run
        # so middleware effects (logging, timeout, etc.) apply to run()
        task = self._build_task(
            prompt,
            working_dir=working_dir,
            max_turns=max_turns,
            system_prompt=system_prompt,
            **kwargs,
        )
        handler = self._get_stream_handler()
        return await _run_from_handler(handler, task)

    def _get_stream_handler(self):
        """Build the stream handler with middleware applied.

        Returns:
            StreamHandler with all middleware chained around
            provider.stream.
        """
        if not self._middleware:
            return self._provider.stream
        return chain_middleware(self._provider.stream, self._middleware)

    def _build_task(
        self,
        prompt: str,
        *,
        working_dir: str | None = None,
        max_turns: int | None = None,
        system_prompt: str | None = None,
        **kwargs,
    ) -> TaskConfig:
        """Build a TaskConfig from arguments."""
        task: dict[str, Any] = {"prompt": prompt}
        task["agent"] = self._agent

        if self._model:
            task["model"] = self._model
        if working_dir:
            task["working_dir"] = working_dir
        if max_turns is not None:
            task["max_turns"] = max_turns
        if system_prompt:
            task["system_prompt"] = system_prompt

        for key, value in kwargs.items():
            if value is not None:
                task[key] = value

        return cast(TaskConfig, task)


async def _run_from_handler(handler, task: TaskConfig) -> SessionResult:
    """Consume a StreamHandler and aggregate into SessionResult.

    Similar to default_run but works with any StreamHandler callable,
    not just a Provider instance.

    Args:
        handler: A StreamHandler callable.
        task: Unified task configuration.

    Returns:
        Aggregated SessionResult built from stream events.
    """
    from .providers.base import _RunState

    state = _RunState()
    async for event in handler(task):
        state.handle(event)
    return state.build()


def run_sync(
    prompt: str,
    *,
    agent: str | None = None,
    model: str | None = None,
    **kwargs,
) -> SessionResult:
    """Synchronous convenience for running a task.

    Creates a Session, runs the prompt, and returns the result.
    Suitable for simple scripts that don't need async.

    Args:
        prompt: The task instruction.
        agent: Agent type. Auto-detected if None.
        model: Model to use.
        **kwargs: Additional TaskConfig fields passed to Session.run().

    Returns:
        SessionResult.
    """
    session = Session(agent=agent, model=model)
    return asyncio.run(session.run(prompt, **kwargs))


__all__ = [
    "Session",
    "run_sync",
]
