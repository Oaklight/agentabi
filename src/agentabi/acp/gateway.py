"""ACP gateway server."""

from __future__ import annotations

from typing import Any

from agentabi import __version__
from agentabi._vendor.acp import (
    ACPAgent,
    AuthenticateParams,
    CancelParams,
    CloseSessionParams,
    ImplementationInfo,
    InitializeParams,
    InitializeResult,
    JSONRPCError,
    JSONRPCException,
    LoadSessionParams,
    LogoutParams,
    NewSessionParams,
    NewSessionResult,
    PromptParams,
    PromptResult,
    StopReason,
    negotiate_protocol_version,
)
from agentabi._vendor.jsonrpc import INTERNAL_ERROR, METHOD_NOT_FOUND

from .backends.base import Backend


class GatewayAgent(ACPAgent):
    """ACP agent that delegates each session to a backend."""

    def __init__(self, backend: Backend) -> None:
        super().__init__()
        self._backend = backend

    async def on_initialize(self, params: InitializeParams) -> InitializeResult:
        result = await self._backend.initialize(params)
        # The gateway speaks its own vendored ACP version, not the backend's.
        result.protocol_version = negotiate_protocol_version(params.protocol_version)
        result.agent_info = ImplementationInfo(
            name="agentabi",
            version=__version__,
            title="agentabi ACP gateway",
        )
        # Auth is not proxied to backends yet, so do not advertise methods the
        # gateway cannot honour (see on_authenticate / on_logout).
        result.auth_methods = None
        return result

    async def on_new_session(self, params: NewSessionParams) -> NewSessionResult:
        return await self._backend.new_session(params)

    async def on_load_session(self, params: LoadSessionParams) -> None:
        async for update in self._backend.load_session(params):
            await self._send_raw_update(params.session_id, update)

    async def on_prompt(self, params: PromptParams) -> PromptResult:
        async for update in self._backend.prompt(params):
            await self._send_raw_update(params.session_id, update)
        error = self._backend.prompt_error(params.session_id)
        if error:
            raise JSONRPCException(JSONRPCError(code=INTERNAL_ERROR, message=error))
        return self._backend.prompt_result(params.session_id) or PromptResult(
            stop_reason=StopReason.UNKNOWN
        )

    async def on_cancel(self, params: CancelParams) -> None:
        await self._backend.cancel(params.session_id)

    async def on_close_session(self, params: CloseSessionParams) -> None:
        await self._backend.close_session(params.session_id)

    async def on_authenticate(self, params: AuthenticateParams) -> None:
        """Reject ``authenticate``: the gateway does not proxy backend auth."""
        raise JSONRPCException(
            JSONRPCError(
                code=METHOD_NOT_FOUND,
                message="authenticate is not proxied by the agentabi gateway",
            )
        )

    async def on_logout(self, params: LogoutParams) -> None:
        """Reject ``logout``: the gateway does not proxy backend auth."""
        raise JSONRPCException(
            JSONRPCError(
                code=METHOD_NOT_FOUND,
                message="logout is not proxied by the agentabi gateway",
            )
        )

    async def _send_raw_update(self, session_id: str, update: dict[str, Any]) -> None:
        """Forward a raw ACP ``session/update`` payload unchanged.

        Capability-gated updates are dropped when the client did not
        advertise the matching capability in ``initialize``.
        """
        if not self.supports_update(update.get("sessionUpdate", "")):
            return
        assert self._transport is not None
        await self._transport.send_notification(
            "session/update",
            {"sessionId": session_id, "update": update},
        )


async def serve(backend: Backend) -> None:
    """Run the ACP gateway on stdin/stdout.

    Args:
        backend: Agent-specific backend to delegate to.
    """
    await backend.start()
    try:
        await GatewayAgent(backend).run()
    finally:
        await backend.stop()
