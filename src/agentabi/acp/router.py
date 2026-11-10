"""Router backend: one ACP gateway process, one backend per agent.

The client chooses an agent for each ``session/new`` call with
``_meta.agent``.  The router creates one backend instance per agent (or
reuses a live one), keeps an ACP session -> (agent, backend session) map,
and stops a backend when its last session closes.

Capability policy: ``initialize`` advertises the intersection of what every
enabled and reachable backend honours, further limited by what the gateway
itself implements.  The agent is chosen after ``initialize``, so per-agent
capabilities cannot be advertised honestly there.

Permission policy: deny by default.  The router never turns a missing
permission channel into an allow (see :class:`ACPBackend`).
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from agentabi._vendor.acp import (
    AgentCapabilities,
    ConfigOption,
    ConfigOptionValue,
    InitializeParams,
    InitializeResult,
    JSONRPCError,
    JSONRPCException,
    LoadSessionParams,
    McpCapabilities,
    NewSessionParams,
    NewSessionResult,
    PromptCapabilities,
    PromptParams,
    PromptResult,
    SessionCapabilities,
    SetConfigOptionParams,
    SetConfigOptionResult,
)
from agentabi._vendor.jsonrpc import INTERNAL_ERROR, INVALID_PARAMS, METHOD_NOT_FOUND

from ..types.ir.permissions import PermissionDecision
from .audit import AuditLog, AuditRecord
from .backends.base import Backend
from .permissions import PermissionBroker

logger = logging.getLogger("agentabi.acp.router")

_AGENT_OPTION_ID = "agent"

_SESSION_FIELDS = (
    "list",
    "resume",
    "close",
    "delete",
    "additional_directories",
)


def _intersect_prompt(
    caps: list[PromptCapabilities | None],
) -> PromptCapabilities | None:
    """AND a list of prompt capability sets, field by field.

    Returns ``None`` unless every backend advertises the group: a backend
    that omits it supports none of its members.
    """
    if not caps or any(c is None for c in caps):
        return None
    return PromptCapabilities(
        image=all(bool(c and c.image) for c in caps),
        audio=all(bool(c and c.audio) for c in caps),
        embedded_context=all(bool(c and c.embedded_context) for c in caps),
    )


def _intersect_mcp(caps: list[McpCapabilities | None]) -> McpCapabilities | None:
    """AND a list of MCP capability sets, field by field.

    Returns ``None`` unless every backend advertises the group.
    """
    if not caps or any(c is None for c in caps):
        return None
    return McpCapabilities(
        stdio=all(bool(c and c.stdio) for c in caps),
        http=all(bool(c and c.http) for c in caps),
        sse=all(bool(c and c.sse) for c in caps),
    )


def _intersect_session(
    caps: list[SessionCapabilities | None],
) -> SessionCapabilities | None:
    """Keep only session capabilities present in every cap set.

    Returns ``None`` unless every backend advertises the group.
    """
    if not caps or any(c is None for c in caps):
        return None

    def present(attr: str) -> bool:
        return all(bool(c and getattr(c, attr) is not None) for c in caps)

    if not any(present(attr) for attr in _SESSION_FIELDS):
        return None
    first = next(c for c in caps if c is not None)
    return SessionCapabilities(
        list=first.list if present("list") else None,
        resume=first.resume if present("resume") else None,
        close=first.close if present("close") else None,
        delete=first.delete if present("delete") else None,
        additional_directories=(
            first.additional_directories if present("additional_directories") else None
        ),
    )


def intersect_capabilities(
    caps: list[AgentCapabilities | None],
) -> AgentCapabilities:
    """Return the capabilities every backend in *caps* honours.

    ``session/load`` is always false: the router cannot map a replay request
    to a backend session it never created.  ``session/list``,
    ``session/resume`` and ``session/delete`` are cleared because the
    gateway does not implement them.  Authentication is not proxied.

    Args:
        caps: One capability set per reachable backend.

    Returns:
        The intersection, with gateway limits applied.
    """
    prompt = _intersect_prompt([c.prompt_capabilities if c else None for c in caps])
    mcp = _intersect_mcp([c.mcp_capabilities if c else None for c in caps])
    session = _intersect_session([c.session_capabilities if c else None for c in caps])
    if session is not None:
        session.list = None
        session.resume = None
        session.delete = None
    return AgentCapabilities(
        load_session=False,
        prompt_capabilities=prompt,
        mcp_capabilities=mcp,
        session_capabilities=session,
        auth=None,
    )


@dataclass
class _Route:
    """Mapping from one ACP session to its backend session."""

    agent: str
    backend_session_id: str


class RouterBackend:
    """Backend that multiplexes several agents in one ACP gateway process.

    Args:
        factories: Ordered mapping of agent name to backend factory.  The
            first entry is the default agent when ``_meta.agent`` is absent.
        audit: Optional audit log; a private log is created when omitted.
    """

    name = "router"

    #: Router mode cannot translate a child session id to the gateway session
    #: id in this PoC, so it never claims a permission round trip.
    permission_capable = False

    def __init__(
        self,
        factories: Mapping[str, Callable[[], Backend]],
        *,
        audit: AuditLog | None = None,
    ) -> None:
        if not factories:
            raise ValueError("router mode needs at least one agent")
        self._factories: dict[str, Callable[[], Backend]] = dict(factories)
        self._audit = audit or AuditLog()
        self._permission_broker: PermissionBroker | None = None
        self._backends: dict[str, Backend] = {}
        self._initialized: set[str] = set()
        self._init_locks: dict[str, asyncio.Lock] = {}
        self._init_errors: dict[str, str] = {}
        self._init_params: InitializeParams | None = None
        self._protocol_version = 1
        self._capabilities: dict[str, AgentCapabilities | None] = {}
        self._sessions: dict[str, _Route] = {}
        self._session_counts: dict[str, int] = {}

    @property
    def enabled_agents(self) -> list[str]:
        """Agent names in configuration order."""
        return list(self._factories)

    @property
    def audit_records(self) -> list[AuditRecord]:
        """Audit records recorded so far."""
        return list(self._audit.records)

    @property
    def agent_capabilities(self) -> dict[str, AgentCapabilities | None]:
        """Last probed capabilities per agent (``None`` when unavailable)."""
        return dict(self._capabilities)

    @property
    def session_agents(self) -> dict[str, str]:
        """ACP session id -> selected agent."""
        return {session: route.agent for session, route in self._sessions.items()}

    async def start(self) -> None:
        """Nothing to start; ``initialize`` probes the enabled backends."""

    async def stop(self) -> None:
        """Close all sessions and stop every backend."""
        for session_id in list(self._sessions):
            try:
                await self.close_session(session_id)
            except Exception:  # noqa: BLE001 - best effort during shutdown
                logger.exception("failed to close session %s", session_id)
        for name in list(self._backends):
            await self._discard_backend(name)
        self._initialized.clear()
        self._session_counts.clear()

    def set_permission_broker(self, broker: PermissionBroker) -> None:
        """Store the broker without forwarding it to inner backends.

        Inner backends use their own session ids, which the gateway client
        does not know.  Router mode therefore fails closed until the id
        translation is implemented.
        """
        self._permission_broker = broker

    async def answer_permission(
        self,
        session_id: str,
        request_id: str,
        decision: PermissionDecision,
    ) -> None:
        """No-op: router mode does not forward permission answers yet."""

    async def initialize(self, params: InitializeParams) -> InitializeResult:
        """Probe every enabled backend and advertise the intersection.

        A backend that cannot start is logged and left out of the
        intersection.  Selecting it later retries and returns a clear error
        if it still fails.

        Args:
            params: Client initialization parameters.

        Returns:
            The negotiated version and intersected capabilities.

        Raises:
            JSONRPCException: If no enabled backend could be initialized.
        """
        with self._audit.span("initialize", agent="router"):
            return await self._probe(params)

    async def _probe(self, params: InitializeParams) -> InitializeResult:
        self._init_params = params
        names = list(self._factories)
        results = await asyncio.gather(
            *(self._probe_one(name, params) for name in names),
            return_exceptions=True,
        )
        reachable: dict[str, InitializeResult] = {}
        for name, result in zip(names, results, strict=True):
            if isinstance(result, BaseException):
                self._init_errors[name] = str(result)
                self._capabilities[name] = None
                logger.warning("agent %s is unavailable: %s", name, result)
                await self._discard_backend(name)
            else:
                reachable[name] = result
        if not reachable:
            details = "; ".join(
                f"{name}: {err}" for name, err in self._init_errors.items()
            )
            raise JSONRPCException(
                JSONRPCError(
                    code=INTERNAL_ERROR,
                    message=f"no enabled agent could be initialized ({details})",
                )
            )

        common = min(result.protocol_version for result in reachable.values())
        for name, result in list(reachable.items()):
            if result.protocol_version <= common:
                continue
            try:
                await self._converge_version(name, common)
            except Exception as exc:  # noqa: BLE001 - agent becomes unavailable
                logger.warning("agent %s failed to renegotiate: %s", name, exc)
                self._init_errors[name] = str(exc)
                self._initialized.discard(name)
                self._capabilities[name] = None
                await self._discard_backend(name)
                reachable.pop(name)

        if not reachable:  # pragma: no cover - convergence failure path
            raise JSONRPCException(
                JSONRPCError(
                    code=INTERNAL_ERROR,
                    message="no enabled agent could be initialized",
                )
            )
        self._protocol_version = common
        capabilities = intersect_capabilities(
            [self._capabilities.get(name) for name in reachable]
        )
        return InitializeResult(
            protocol_version=common,
            agent_capabilities=capabilities,
        )

    async def _probe_one(self, name: str, params: InitializeParams) -> InitializeResult:
        backend = self._backend_for(name)
        await backend.start()
        result = await backend.initialize(params)
        self._initialized.add(name)
        self._capabilities[name] = result.agent_capabilities
        if not getattr(backend, "mediates_permissions", False):
            logger.warning(
                "agent %s cannot mediate ACP permissions; its provider's own "
                "default policy applies",
                name,
            )
        return result

    async def _converge_version(self, name: str, version: int) -> None:
        """Make backend *name* emit updates at the gateway's *version*.

        IR backends accept ``set_protocol_version``.  Native ACP backends
        forward downstream updates unchanged, so they are restarted and
        re-initialized at *version*.
        """
        backend = self._backend_for(name)
        setter = getattr(backend, "set_protocol_version", None)
        if callable(setter):
            setter(version)
            return
        await self._discard_backend(name)
        assert self._init_params is not None
        recreated = self._backend_for(name)
        await recreated.start()
        result = await recreated.initialize(
            replace(self._init_params, protocol_version=version)
        )
        if result.protocol_version < version:
            await self._discard_backend(name)
            raise RuntimeError(
                f"negotiated protocol v{result.protocol_version} below v{version}"
            )
        self._initialized.add(name)
        self._capabilities[name] = result.agent_capabilities
        setter = getattr(recreated, "set_protocol_version", None)
        if callable(setter):
            setter(version)

    def set_protocol_version(self, version: int) -> None:
        """Propagate the gateway's negotiated version to live backends."""
        self._protocol_version = version
        for backend in self._backends.values():
            setter = getattr(backend, "set_protocol_version", None)
            if callable(setter):
                setter(version)

    def _backend_for(self, name: str) -> Backend:
        backend = self._backends.get(name)
        if backend is None:
            backend = self._factories[name]()
            self._backends[name] = backend
        return backend

    def _lock_for(self, name: str) -> asyncio.Lock:
        """Per-agent lock serializing backend creation and recycle."""
        return self._init_locks.setdefault(name, asyncio.Lock())

    async def _ensure_initialized(self, name: str) -> Backend:
        """Return an initialized backend, creating one when it was recycled.

        Concurrent ``session/new`` calls for the same agent share a lock so
        a backend is created and initialized exactly once.
        """
        lock = self._lock_for(name)
        async with lock:
            backend = self._backend_for(name)
            if name in self._initialized or self._init_params is None:
                return backend
            try:
                await backend.start()
                result = await backend.initialize(
                    replace(
                        self._init_params,
                        protocol_version=self._protocol_version,
                    )
                )
            except Exception as exc:
                self._init_errors[name] = str(exc)
                await self._discard_backend(name)
                raise
            if result.protocol_version < self._protocol_version:
                self._init_errors[name] = (
                    f"negotiated protocol v{result.protocol_version} below "
                    f"the gateway's v{self._protocol_version}"
                )
                await self._discard_backend(name)
                raise JSONRPCException(
                    JSONRPCError(
                        code=INTERNAL_ERROR,
                        message=(
                            f"agent '{name}' {self._init_errors[name]}; "
                            "the session was not created"
                        ),
                    )
                )
            self._initialized.add(name)
            self._init_errors.pop(name, None)
            self._capabilities[name] = result.agent_capabilities
            setter = getattr(backend, "set_protocol_version", None)
            if callable(setter):
                setter(self._protocol_version)
            return backend

    async def _discard_backend(self, name: str) -> None:
        backend = self._backends.pop(name, None)
        self._initialized.discard(name)
        if backend is not None:
            try:
                await backend.stop()
            except Exception:  # noqa: BLE001 - cleanup must not mask the cause
                logger.exception("failed to stop agent %s", name)

    def _select_agent(self, meta: dict[str, Any] | None) -> tuple[str, str]:
        """Resolve the agent for a session.

        Args:
            meta: ACP ``_meta`` object from ``session/new``.

        Returns:
            The agent name and a selection detail for the audit log.

        Raises:
            JSONRPCException: If the requested agent is not enabled.
        """
        requested = meta.get("agent") if isinstance(meta, dict) else None
        if requested is None:
            return next(iter(self._factories)), "default"
        if not isinstance(requested, str) or requested not in self._factories:
            enabled = ", ".join(self._factories)
            raise JSONRPCException(
                JSONRPCError(
                    code=INVALID_PARAMS,
                    message=(f"unknown agent {requested!r}; enabled agents: {enabled}"),
                )
            )
        return requested, "_meta.agent"

    async def new_session(self, params: NewSessionParams) -> NewSessionResult:
        """Create a session on the default agent."""
        return await self.new_session_with_meta(params, {})

    async def new_session_with_meta(
        self,
        params: NewSessionParams,
        meta: dict[str, Any] | None,
    ) -> NewSessionResult:
        """Create a session on the agent named by ``_meta.agent``.

        Args:
            params: ACP ``session/new`` parameters.
            meta: ACP ``_meta`` object carrying the requested agent.

        Returns:
            The result, with a gateway session id and the selected agent as
            a config option.

        Raises:
            JSONRPCException: If the agent is unknown or fails to start.
        """
        started = time.perf_counter()
        try:
            name, detail = self._select_agent(meta)
        except JSONRPCException as exc:
            requested = meta.get("agent") if isinstance(meta, dict) else None
            self._audit.record(
                AuditRecord(
                    method="session/new",
                    agent=requested if isinstance(requested, str) else None,
                    session=None,
                    duration_ms=(time.perf_counter() - started) * 1000.0,
                    outcome="error",
                    detail=f"{exc.error.code}: {exc.error.message}",
                )
            )
            raise
        try:
            backend = await self._ensure_initialized(name)
            result = await backend.new_session(params)
        except JSONRPCException as exc:
            self._record_new_session(name, started, "error", detail, exc.error)
            raise
        except Exception as exc:  # noqa: BLE001 - surfaced to the ACP client
            self._record_new_session(name, started, "error", str(exc), None)
            raise JSONRPCException(
                JSONRPCError(
                    code=INTERNAL_ERROR,
                    message=f"agent '{name}' failed to create a session: {exc}",
                )
            ) from exc

        session_id = f"agentabi-{name}-{uuid.uuid4().hex}"
        self._sessions[session_id] = _Route(name, result.session_id)
        self._session_counts[name] = self._session_counts.get(name, 0) + 1
        result.session_id = session_id
        result.config_options = self._config_options(result.config_options, name)
        self._record_new_session(name, started, "ok", detail, None, session_id)
        return result

    def _record_new_session(
        self,
        agent: str,
        started: float,
        outcome: str,
        detail: str | None,
        error: JSONRPCError | None,
        session_id: str | None = None,
    ) -> None:
        if error is not None:
            detail = f"{error.code}: {error.message}"
        self._audit.record(
            AuditRecord(
                method="session/new",
                agent=agent,
                session=session_id,
                duration_ms=(time.perf_counter() - started) * 1000.0,
                outcome=outcome,
                detail=detail,
            )
        )

    def _config_options(
        self,
        existing: list[ConfigOption] | None,
        agent: str,
    ) -> list[ConfigOption]:
        options = list(existing or [])
        if any(option.id == _AGENT_OPTION_ID for option in options):
            return options
        options.append(
            ConfigOption(
                id=_AGENT_OPTION_ID,
                name="Agent",
                type="select",
                current_value=agent,
                options=[ConfigOptionValue(value=agent, name=agent)],
                description=(
                    "Chosen per session with _meta.agent in session/new; "
                    "fixed for the lifetime of the session"
                ),
            )
        )
        return options

    async def load_session(
        self, params: LoadSessionParams
    ) -> AsyncIterator[dict[str, Any]]:
        """Reject ``session/load``: the router has no replayable sessions."""
        raise JSONRPCException(
            JSONRPCError(
                code=METHOD_NOT_FOUND,
                message="session/load is not supported in router mode",
            )
        )
        yield {}  # pragma: no cover

    def prompt(self, params: PromptParams) -> AsyncIterator[dict[str, Any]]:
        """Forward a prompt to the session's backend."""
        route = self._require_route(params.session_id)
        backend = self._backends.get(route.agent)
        if backend is None:  # pragma: no cover - route always owns a backend
            raise JSONRPCException(
                JSONRPCError(
                    code=INTERNAL_ERROR,
                    message=f"backend '{route.agent}' is not running",
                )
            )
        inner = PromptParams(
            session_id=route.backend_session_id,
            prompt=params.prompt,
        )
        return self._prompt_events(route.agent, params.session_id, backend, inner)

    async def _prompt_events(
        self,
        agent: str,
        session_id: str,
        backend: Backend,
        params: PromptParams,
    ) -> AsyncIterator[dict[str, Any]]:
        with self._audit.span("session/prompt", agent=agent, session=session_id):
            async for update in backend.prompt(params):
                yield update

    def prompt_result(self, session_id: str) -> PromptResult | None:
        """Return the last prompt result from the session's backend."""
        route = self._sessions.get(session_id)
        if route is None:
            return None
        backend = self._backends.get(route.agent)
        if backend is None:
            return None
        return backend.prompt_result(route.backend_session_id)

    def prompt_error(self, session_id: str) -> str | None:
        """Return the last prompt error from the session's backend."""
        route = self._sessions.get(session_id)
        if route is None:
            return None
        backend = self._backends.get(route.agent)
        if backend is None:
            return None
        return backend.prompt_error(route.backend_session_id)

    async def cancel(self, session_id: str) -> None:
        """Cancel the session's current turn on its backend."""
        route = self._sessions.get(session_id)
        backend = self._backends.get(route.agent) if route else None
        if route is None or backend is None:
            return
        with self._audit.span("session/cancel", agent=route.agent, session=session_id):
            await backend.cancel(route.backend_session_id)

    async def close_session(self, session_id: str) -> None:
        """Close a session and recycle its backend after the last one."""
        route = self._sessions.pop(session_id, None)
        if route is None:
            return
        with self._audit.span("session/close", agent=route.agent, session=session_id):
            lock = self._lock_for(route.agent)
            async with lock:
                backend = self._backends.get(route.agent)
                try:
                    if backend is not None:
                        await backend.close_session(route.backend_session_id)
                finally:
                    remaining = self._session_counts.get(route.agent, 1) - 1
                    if remaining <= 0:
                        self._session_counts.pop(route.agent, None)
                        await self._discard_backend(route.agent)
                    else:
                        self._session_counts[route.agent] = remaining

    async def set_config_option(
        self, params: SetConfigOptionParams
    ) -> SetConfigOptionResult:
        """Report config options; reject changing the agent.

        ACP config options are returned by ``session/new`` and set
        afterwards, so they cannot choose an agent at creation time.  The
        ``agent`` option is therefore informational and immutable.

        Args:
            params: ACP ``session/set_config_option`` parameters.

        Returns:
            The session's current options.

        Raises:
            JSONRPCException: For an unknown session or an agent change.
        """
        route = self._require_route(params.session_id)
        with self._audit.span(
            "session/set_config_option",
            agent=route.agent,
            session=params.session_id,
        ):
            if params.config_id == _AGENT_OPTION_ID and params.value != route.agent:
                raise JSONRPCException(
                    JSONRPCError(
                        code=INVALID_PARAMS,
                        message=(
                            "agent is fixed for the lifetime of a session; "
                            "create a new session with _meta.agent to choose "
                            "another agent"
                        ),
                    )
                )
            return SetConfigOptionResult(
                config_options=self._config_options(None, route.agent)
            )

    def _require_route(self, session_id: str) -> _Route:
        route = self._sessions.get(session_id)
        if route is None:
            raise JSONRPCException(
                JSONRPCError(
                    code=INVALID_PARAMS,
                    message=f"unknown session: {session_id}",
                )
            )
        return route


__all__ = ["RouterBackend", "intersect_capabilities"]
