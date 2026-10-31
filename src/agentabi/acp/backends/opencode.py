"""OpenCode backend: pass ACP through to ``opencode acp``."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from agentabi._vendor.acp import (
    ACPClient,
    InitializeParams,
    InitializeResult,
    LoadSessionParams,
    NewSessionParams,
    NewSessionResult,
    PromptParams,
    PromptResult,
    TextContent,
)


def _split_prompt_blocks(
    blocks: list[Any],
) -> tuple[str, list[Any]]:
    """Split prompt blocks into text and non-text content.

    Args:
        blocks: ACP content blocks from ``session/prompt``.

    Returns:
        A tuple of joined text and the remaining content blocks.
    """
    text_parts: list[str] = []
    extra: list[Any] = []
    for block in blocks:
        if isinstance(block, TextContent):
            text_parts.append(block.text)
        else:
            extra.append(block)
    return "\n".join(text_parts), extra


class OpenCodeBackend:
    """Backend that delegates to OpenCode's native ACP server."""

    name = "opencode"

    def __init__(self) -> None:
        self._client = ACPClient(["opencode", "acp"])
        self._started = False
        self._last_prompt_result: PromptResult | None = None

    async def start(self) -> None:
        if not self._started:
            await self._client.start()
            self._started = True

    async def stop(self) -> None:
        if self._started:
            await self._client.stop()
            self._started = False

    async def initialize(self, params: InitializeParams) -> InitializeResult:
        await self.start()
        result = await self._client.initialize(params)
        # Session replay is not implemented in the gateway yet.
        caps = result.agent_capabilities
        if caps is not None:
            caps.load_session = False
            if caps.session_capabilities is not None:
                caps.session_capabilities.resume = None
                caps.session_capabilities.delete = None
        return result

    async def new_session(self, params: NewSessionParams) -> NewSessionResult:
        await self.start()
        return await self._client.new_session(
            params.cwd,
            params.mcp_servers,
            params.additional_directories,
        )

    async def load_session(
        self, params: LoadSessionParams
    ) -> AsyncIterator[dict[str, Any]]:
        raise NotImplementedError("OpenCode session replay is not implemented")
        yield {}  # pragma: no cover

    async def prompt(self, params: PromptParams) -> AsyncIterator[dict[str, Any]]:
        await self.start()
        self._last_prompt_result = None
        text, extra = _split_prompt_blocks(params.prompt)
        async for raw in self._client.prompt(params.session_id, text, extra):
            update = raw.get("update", raw)
            yield update
        self._last_prompt_result = self._client.last_prompt_result

    @property
    def last_prompt_result(self) -> PromptResult | None:
        return self._last_prompt_result

    async def cancel(self, session_id: str) -> None:
        await self._client.cancel(session_id)

    async def close_session(self, session_id: str) -> None:
        await self._client.close_session(session_id)


__all__ = ["OpenCodeBackend"]
