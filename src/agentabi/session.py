"""
agentabi - Session

Consumer-facing API for interacting with agent CLIs.
Async-first design with sync convenience wrapper.
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Optional, cast

from .providers.base import Provider
from .providers.registry import resolve_provider
from .types.ir.events import IREvent
from .types.ir.session import SessionResult
from .types.ir.task import TaskConfig


class Session:
    """High-level session interface for interacting with agent CLIs.

    Async-first API. Use run_sync() for synchronous scripts.

    Examples:
        # Async usage
        session = Session(agent="claude_code")
        result = await session.run(prompt="Fix the bug in auth.py")

        # Streaming
        async for event in session.stream(prompt="Explain this code"):
            if event["type"] == "message_delta":
                print(event["text"], end="")

        # Auto-detect agent
        session = Session()
    """

    def __init__(
        self,
        *,
        agent: Optional[str] = None,
        model: Optional[str] = None,
    ) -> None:
        """Initialize a Session.

        Args:
            agent: Agent type to use (e.g., "claude_code", "codex").
                If None, auto-detects the first available agent.
            model: Default model to use. Can be overridden per-task.

        Raises:
            AgentNotAvailable: If no provider is available.
        """
        if agent is None:
            from .auto_detect import get_default_agent

            agent = get_default_agent()

        self._agent = agent
        self._model = model
        self._provider: Provider = resolve_provider(agent)

    @property
    def agent(self) -> str:
        """The agent type being used."""
        return self._agent

    @property
    def model(self) -> Optional[str]:
        """The default model, if set."""
        return self._model

    @property
    def provider(self) -> Provider:
        """The underlying provider instance."""
        return self._provider

    async def stream(
        self,
        prompt: str,
        *,
        working_dir: Optional[str] = None,
        max_turns: Optional[int] = None,
        system_prompt: Optional[str] = None,
        **kwargs,
    ) -> AsyncIterator[IREvent]:
        """Stream IR events from a task execution.

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
        async for event in self._provider.stream(task):
            yield event

    async def run(
        self,
        prompt: str,
        *,
        working_dir: Optional[str] = None,
        max_turns: Optional[int] = None,
        system_prompt: Optional[str] = None,
        **kwargs,
    ) -> SessionResult:
        """Run a task to completion.

        Args:
            prompt: The task instruction to send to the agent.
            working_dir: Optional working directory.
            max_turns: Optional max turns limit.
            system_prompt: Optional system prompt.
            **kwargs: Additional TaskConfig fields.

        Returns:
            Aggregated SessionResult.
        """
        task = self._build_task(
            prompt,
            working_dir=working_dir,
            max_turns=max_turns,
            system_prompt=system_prompt,
            **kwargs,
        )
        return await self._provider.run(task)

    def _build_task(
        self,
        prompt: str,
        *,
        working_dir: Optional[str] = None,
        max_turns: Optional[int] = None,
        system_prompt: Optional[str] = None,
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


def run_sync(
    prompt: str,
    *,
    agent: Optional[str] = None,
    model: Optional[str] = None,
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
