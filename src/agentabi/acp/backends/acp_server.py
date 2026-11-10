"""Backend for an external ACP server process.

Any CLI that implements ACP on stdio can be served by the gateway without
an agentabi provider. The gateway speaks ACP to the process and forwards
its ``session/update`` payloads to the client.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from agentabi._vendor.acp import (
    ACPClient,
    InitializeParams,
    InitializeResult,
    JSONRPCError,
    JSONRPCException,
    LoadSessionParams,
    NewSessionParams,
    NewSessionResult,
    PromptParams,
    PromptResult,
)
from agentabi._vendor.jsonrpc import METHOD_NOT_FOUND

from .base import split_prompt_blocks


class ACPBackend:
    """Backend that delegates to an external ACP server process."""

    #: The gateway answers downstream permission requests (with a deny), so
    #: this backend does not silently inherit the downstream's policy.
    mediates_permissions = True

    def __init__(self, command: list[str], *, name: str = "acp") -> None:
        """Create a backend for *command*.

        Args:
            command: argv of the ACP server process.
            name: Backend name exposed by the registry.
        """
        self.name = name
        self._command = list(command)
        self._client = ACPClient(self._command)
        self._client.set_request_handler(self._handle_client_request)
        self._started = False
        self._prompt_results: dict[str, PromptResult] = {}

    async def _handle_client_request(
        self, method: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Answer downstream agent requests without granting anything.

        The gateway has no permission UI and does not proxy client
        filesystem or terminal methods, so a downstream
        ``session/request_permission`` is denied explicitly.  Denying
        (rather than erroring) keeps the outcome unambiguous for agents
        that treat an error as a transport failure.
        """
        if method == "session/request_permission":
            return {"outcome": {"outcome": "cancelled"}}
        raise JSONRPCException(
            JSONRPCError(
                code=METHOD_NOT_FOUND,
                message=f"the agentabi gateway does not proxy {method}",
            )
        )

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
        # The gateway does not replay sessions yet, so do not advertise it.
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
        raise NotImplementedError("session replay is not implemented")
        yield {}  # pragma: no cover

    async def prompt(self, params: PromptParams) -> AsyncIterator[dict[str, Any]]:
        await self.start()
        self._prompt_results.pop(params.session_id, None)
        text, extra = split_prompt_blocks(params.prompt)
        async for raw in self._client.prompt(params.session_id, text, extra):
            yield raw.get("update", raw)
        result = self._client.prompt_result(params.session_id)
        if result is not None:
            self._prompt_results[params.session_id] = result

    def prompt_result(self, session_id: str) -> PromptResult | None:
        return self._prompt_results.get(session_id)

    def prompt_error(self, session_id: str) -> str | None:
        return None

    async def cancel(self, session_id: str) -> None:
        await self._client.cancel(session_id)

    async def close_session(self, session_id: str) -> None:
        self._prompt_results.pop(session_id, None)
        await self._client.close_session(session_id)


__all__ = ["ACPBackend"]
