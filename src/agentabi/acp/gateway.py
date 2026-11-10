"""ACP gateway server."""

from __future__ import annotations

from contextvars import ContextVar
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
    SetConfigOptionParams,
    SetConfigOptionResult,
    StopReason,
    negotiate_protocol_version,
)
from agentabi._vendor.jsonrpc import INTERNAL_ERROR, METHOD_NOT_FOUND

from .backends.base import Backend, SessionSelectingBackend

#: ``_meta`` of the ``session/new`` request being handled.  The vendored
#: parser drops unknown fields, so the gateway stashes the raw object here.
#: Each dispatched message runs in its own task, so concurrent ``session/new``
#: calls do not see each other's metadata.
_SESSION_META: ContextVar[dict[str, Any]] = ContextVar(
    "agentabi_session_meta", default={}
)


class GatewayAgent(ACPAgent):
    """ACP agent that delegates each session to a backend."""

    def __init__(self, backend: Backend) -> None:
        super().__init__()
        self._backend = backend

    async def on_initialize(self, params: InitializeParams) -> InitializeResult:
        requested = negotiate_protocol_version(params.protocol_version)
        # Ask the backend for the same version the client asked for.  The
        # backend may negotiate lower (for example an external v1-only
        # agent), and the gateway must not advertise more than the backend
        # can produce.
        backend_params = InitializeParams(
            protocol_version=requested,
            client_capabilities=params.client_capabilities,
            client_info=params.client_info,
        )
        result = await self._backend.initialize(backend_params)
        negotiated = min(requested, result.protocol_version)
        set_version = getattr(self._backend, "set_protocol_version", None)
        if callable(set_version):
            set_version(negotiated)
        result.protocol_version = negotiated
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
        if isinstance(self._backend, SessionSelectingBackend):
            return await self._backend.new_session_with_meta(
                params, _SESSION_META.get()
            )
        return await self._backend.new_session(params)

    async def _handle_new_session(self, params: dict[str, Any]) -> Any:
        """Capture ``_meta`` before the vendored parser drops it.

        ``ACPAgent._handle_new_session`` parses the wire params into
        ``NewSessionParams``, which has no ``_meta`` field.  Stash the raw
        object in a context variable for :meth:`on_new_session`.  Each
        message is dispatched in its own task, so the value is isolated.
        """
        raw_meta = params.get("_meta")
        token = _SESSION_META.set(raw_meta if isinstance(raw_meta, dict) else {})
        try:
            return await super()._handle_new_session(params)
        finally:
            _SESSION_META.reset(token)

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

    async def on_set_config_option(
        self, params: SetConfigOptionParams
    ) -> SetConfigOptionResult:
        """Delegate config option changes to backends that handle them."""
        handler = getattr(self._backend, "set_config_option", None)
        if callable(handler):
            return await handler(params)
        return SetConfigOptionResult()

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
