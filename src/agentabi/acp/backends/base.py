"""Backend protocol for the ACP gateway.

A backend translates agentabi's ACP-facing gateway into one concrete agent
transport (native ACP, a CLI JSON stream, an SDK, or a long-lived RPC).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol, runtime_checkable

from agentabi._vendor.acp import (
    InitializeParams,
    InitializeResult,
    LoadSessionParams,
    NewSessionParams,
    NewSessionResult,
    PromptParams,
    PromptResult,
)


@runtime_checkable
class Backend(Protocol):
    """One agent-specific transport used by the ACP gateway."""

    name: str

    async def start(self) -> None:
        """Start the backend process or connection."""
        ...

    async def stop(self) -> None:
        """Stop the backend and release resources."""
        ...

    async def initialize(self, params: InitializeParams) -> InitializeResult:
        """Perform the backend handshake and return its capabilities."""
        ...

    async def new_session(self, params: NewSessionParams) -> NewSessionResult:
        """Create a backend session."""
        ...

    def load_session(self, params: LoadSessionParams) -> AsyncIterator[dict[str, Any]]:
        """Replay an existing session as ACP ``session/update`` payloads."""
        ...

    def prompt(self, params: PromptParams) -> AsyncIterator[dict[str, Any]]:
        """Run a prompt turn and yield ACP ``session/update`` payloads."""
        ...

    @property
    def last_prompt_result(self) -> PromptResult | None:
        """Result of the most recent prompt turn, if any."""
        ...

    async def cancel(self, session_id: str) -> None:
        """Cancel the current turn for a session."""
        ...

    async def close_session(self, session_id: str) -> None:
        """Close an active backend session."""
        ...
