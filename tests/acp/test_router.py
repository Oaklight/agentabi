"""Tests for the ACP router: selection, isolation, and capability policy."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest

from agentabi._vendor.acp import (
    AgentCapabilities,
    InitializeParams,
    InitializeResult,
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
    StopReason,
    TextContent,
)
from agentabi._vendor.acp.acp import SessionListCapability
from agentabi._vendor.jsonrpc import INTERNAL_ERROR, INVALID_PARAMS, METHOD_NOT_FOUND
from agentabi.acp.backends import create_backend
from agentabi.acp.backends.acp_server import ACPBackend
from agentabi.acp.gateway import GatewayAgent
from agentabi.acp.permissions import PermissionBroker
from agentabi.acp.router import RouterBackend, intersect_capabilities
from agentabi.types.ir.permissions import PermissionDecision


class FakeBackend:
    """Deterministic backend used by router tests."""

    def __init__(
        self,
        name: str,
        *,
        version: int = 2,
        capabilities: AgentCapabilities | None = None,
        prompt_delay: float = 0.0,
        fail_initialize: bool = False,
    ) -> None:
        self.name = name
        self.version = version
        self.capabilities = capabilities or AgentCapabilities(load_session=False)
        self.prompt_delay = prompt_delay
        self.fail_initialize = fail_initialize
        self.started = False
        self.stop_calls = 0
        self.sessions: list[str] = []
        self.closed: list[str] = []
        self.cancelled: list[str] = []
        self.init_versions: list[int] = []
        self.protocol_version: int | None = None
        self._results: dict[str, PromptResult] = {}

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stop_calls += 1
        self.started = False

    async def initialize(self, params: InitializeParams) -> InitializeResult:
        if self.fail_initialize:
            raise RuntimeError(f"{self.name} is not installed")
        self.init_versions.append(params.protocol_version)
        return InitializeResult(
            protocol_version=min(params.protocol_version, self.version),
            agent_capabilities=self.capabilities,
        )

    async def new_session(self, params: NewSessionParams) -> NewSessionResult:
        await asyncio.sleep(0)
        session_id = f"{self.name}-s{len(self.sessions) + 1}"
        self.sessions.append(session_id)
        return NewSessionResult(session_id=session_id)

    async def load_session(
        self, params: LoadSessionParams
    ) -> AsyncIterator[dict[str, Any]]:
        raise NotImplementedError
        yield {}  # pragma: no cover

    async def prompt(self, params: PromptParams) -> AsyncIterator[dict[str, Any]]:
        self._results.pop(params.session_id, None)
        for index in range(3):
            await asyncio.sleep(self.prompt_delay)
            yield {
                "sessionUpdate": "agent_message_chunk",
                "content": {
                    "type": "text",
                    "text": f"{self.name}:{params.session_id}:{index}",
                },
            }
        self._results[params.session_id] = PromptResult(stop_reason=StopReason.END_TURN)

    def prompt_result(self, session_id: str) -> PromptResult | None:
        return self._results.get(session_id)

    def prompt_error(self, session_id: str) -> str | None:
        return None

    async def cancel(self, session_id: str) -> None:
        self.cancelled.append(session_id)

    async def close_session(self, session_id: str) -> None:
        self.closed.append(session_id)

    def set_permission_broker(self, broker: PermissionBroker) -> None:
        self.broker = broker

    async def answer_permission(
        self, session_id: str, request_id: str, decision: PermissionDecision
    ) -> None:
        """No-op: this backend answers inline."""


class VersionedBackend(FakeBackend):
    """IR-like backend that accepts a protocol version setter."""

    def set_protocol_version(self, version: int) -> None:
        self.protocol_version = version


class SlowInitBackend(FakeBackend):
    """Backend whose handshake yields, exposing concurrent init races."""

    async def initialize(self, params: InitializeParams) -> InitializeResult:
        await asyncio.sleep(0.01)
        return await super().initialize(params)


def make_router(
    *names: str,
    created: list[FakeBackend] | None = None,
    config: dict[str, dict[str, Any]] | None = None,
    **defaults: Any,
) -> RouterBackend:
    """Build a router whose factories create fresh fake backends.

    Args:
        names: Enabled agent names, in order.
        created: Optional list collecting every backend instance created.
        config: Per-agent keyword arguments for the fake backend.  The
            special key ``backend_class`` selects a different class.
        defaults: Keyword arguments shared by every agent.
    """
    instances = created if created is not None else []
    overrides = config or {}

    def factory(name: str) -> Callable[[], FakeBackend]:
        def make() -> FakeBackend:
            options = {**defaults, **overrides.get(name, {})}
            backend_class = options.pop("backend_class", FakeBackend)
            backend = backend_class(name, **options)
            instances.append(backend)
            return backend

        return make

    return RouterBackend({name: factory(name) for name in names})


async def _collect(router: RouterBackend, session_id: str) -> list[dict[str, Any]]:
    updates = []
    async for update in router.prompt(
        PromptParams(session_id=session_id, prompt=[TextContent(text="hi")])
    ):
        updates.append(update)
    return updates


def _text(update: dict[str, Any]) -> str:
    return update["content"]["text"]


@pytest.mark.asyncio
async def test_selects_agent_from_meta():
    router = make_router("a", "b")
    result = await router.new_session_with_meta(
        NewSessionParams(cwd="/tmp"), {"agent": "b"}
    )
    assert router.session_agents[result.session_id] == "b"
    assert result.session_id.startswith("agentabi-b-")


@pytest.mark.asyncio
async def test_defaults_to_the_first_enabled_agent():
    router = make_router("a", "b")
    result = await router.new_session_with_meta(NewSessionParams(cwd="/tmp"), {})
    assert router.session_agents[result.session_id] == "a"


@pytest.mark.asyncio
async def test_unknown_agent_is_rejected_with_enabled_agents():
    router = make_router("a", "b")
    with pytest.raises(JSONRPCException) as exc:
        await router.new_session_with_meta(
            NewSessionParams(cwd="/tmp"), {"agent": "nope"}
        )
    assert isinstance(exc.value, JSONRPCException)
    assert exc.value.error.code == INVALID_PARAMS
    assert "enabled agents: a, b" in exc.value.error.message


@pytest.mark.asyncio
async def test_two_agents_have_isolated_sessions_and_results():
    router = make_router("a", "b")
    first = await router.new_session_with_meta(
        NewSessionParams(cwd="/tmp"), {"agent": "a"}
    )
    second = await router.new_session_with_meta(
        NewSessionParams(cwd="/tmp"), {"agent": "b"}
    )

    first_updates = await _collect(router, first.session_id)
    second_updates = await _collect(router, second.session_id)

    assert {_text(u).split(":")[0] for u in first_updates} == {"a"}
    assert {_text(u).split(":")[0] for u in second_updates} == {"b"}
    assert router.prompt_result(first.session_id) == PromptResult(
        stop_reason=StopReason.END_TURN
    )
    assert router.prompt_result(second.session_id) == PromptResult(
        stop_reason=StopReason.END_TURN
    )


@pytest.mark.asyncio
async def test_concurrent_prompts_on_different_agents_do_not_cross_talk():
    router = make_router("a", "b", prompt_delay=0.01)
    first = await router.new_session_with_meta(
        NewSessionParams(cwd="/tmp"), {"agent": "a"}
    )
    second = await router.new_session_with_meta(
        NewSessionParams(cwd="/tmp"), {"agent": "b"}
    )

    first_updates, second_updates = await asyncio.gather(
        _collect(router, first.session_id),
        _collect(router, second.session_id),
    )

    assert [_text(u) for u in first_updates] == [f"a:a-s1:{i}" for i in range(3)]
    assert [_text(u) for u in second_updates] == [f"b:b-s1:{i}" for i in range(3)]
    assert first.session_id != second.session_id


@pytest.mark.asyncio
async def test_unknown_session_prompt_is_rejected():
    router = make_router("a")
    with pytest.raises(JSONRPCException) as exc:
        router.prompt(PromptParams(session_id="missing", prompt=[]))
    assert isinstance(exc.value, JSONRPCException)
    assert exc.value.error.code == INVALID_PARAMS


@pytest.mark.asyncio
async def test_backend_is_reused_then_recycled_after_the_last_session():
    created: list[FakeBackend] = []
    router = make_router("a", created=created)
    await router.initialize(InitializeParams(protocol_version=2))

    first = await router.new_session_with_meta(NewSessionParams(cwd="/tmp"), {})
    second = await router.new_session_with_meta(NewSessionParams(cwd="/tmp"), {})
    assert len(created) == 1

    await router.close_session(first.session_id)
    assert created[0].stop_calls == 0

    await router.close_session(second.session_id)
    assert created[0].stop_calls == 1

    await router.new_session_with_meta(NewSessionParams(cwd="/tmp"), {})
    assert len(created) == 2
    assert created[1].init_versions == [2]


@pytest.mark.asyncio
async def test_capabilities_are_the_intersection_and_gateway_limits():
    rich = AgentCapabilities(
        load_session=True,
        prompt_capabilities=PromptCapabilities(image=True),
        mcp_capabilities=McpCapabilities(stdio=True),
        session_capabilities=SessionCapabilities(
            close={}, list=SessionListCapability()
        ),
    )
    poor = AgentCapabilities(
        load_session=True,
        prompt_capabilities=PromptCapabilities(audio=True),
        session_capabilities=SessionCapabilities(close={}),
    )
    router = make_router(
        "rich",
        "poor",
        config={"rich": {"capabilities": rich}, "poor": {"capabilities": poor}},
    )

    result = await router.initialize(InitializeParams(protocol_version=2))
    caps = result.agent_capabilities
    assert caps is not None
    assert caps.load_session is False  # the router cannot replay sessions
    assert caps.auth is None
    assert caps.prompt_capabilities is not None
    assert caps.prompt_capabilities.image is False
    assert caps.prompt_capabilities.audio is False
    assert caps.mcp_capabilities is None
    assert caps.session_capabilities is not None
    assert caps.session_capabilities.close == {}
    assert caps.session_capabilities.list is None
    assert router.agent_capabilities["rich"] is rich


def test_intersect_capabilities_keeps_only_shared_markers():
    caps = intersect_capabilities(
        [
            AgentCapabilities(
                session_capabilities=SessionCapabilities(
                    close={}, resume={}, additional_directories={}
                )
            ),
            AgentCapabilities(
                session_capabilities=SessionCapabilities(close={}, resume={}, delete={})
            ),
        ]
    )
    assert caps.load_session is False
    assert caps.session_capabilities is not None
    assert caps.session_capabilities.close == {}
    # The gateway does not implement resume or delete, so both are dropped.
    assert caps.session_capabilities.resume is None
    assert caps.session_capabilities.delete is None
    assert caps.session_capabilities.additional_directories is None


def test_intersect_capabilities_drops_a_group_a_backend_does_not_advertise():
    caps = intersect_capabilities(
        [
            AgentCapabilities(session_capabilities=SessionCapabilities(close={})),
            AgentCapabilities(),
        ]
    )
    assert caps.session_capabilities is None


@pytest.mark.asyncio
async def test_unavailable_agent_is_left_out_and_reported_on_selection():
    router = make_router(
        "good",
        "bad",
        config={"bad": {"fail_initialize": True}},
    )
    result = await router.initialize(InitializeParams(protocol_version=2))
    assert result.agent_capabilities is not None
    assert router.agent_capabilities["bad"] is None

    with pytest.raises(JSONRPCException) as exc:
        await router.new_session_with_meta(
            NewSessionParams(cwd="/tmp"), {"agent": "bad"}
        )
    assert isinstance(exc.value, JSONRPCException)
    assert exc.value.error.code == INTERNAL_ERROR
    assert "bad" in exc.value.error.message


@pytest.mark.asyncio
async def test_initialize_fails_when_no_agent_is_available():
    router = make_router("bad", config={"bad": {"fail_initialize": True}})
    with pytest.raises(JSONRPCException) as exc:
        await router.initialize(InitializeParams(protocol_version=2))
    assert isinstance(exc.value, JSONRPCException)
    assert exc.value.error.code == INTERNAL_ERROR
    assert "no enabled agent could be initialized" in exc.value.error.message


@pytest.mark.asyncio
async def test_native_backend_is_restarted_at_the_common_version():
    created: list[FakeBackend] = []
    router = make_router(
        "v2",
        "v1",
        created=created,
        config={"v1": {"version": 1}},
    )
    result = await router.initialize(InitializeParams(protocol_version=2))
    assert result.protocol_version == 1
    v2_instances = [b for b in created if b.name == "v2"]
    assert len(v2_instances) == 2
    assert v2_instances[0].stop_calls == 1
    assert v2_instances[1].init_versions == [1]


@pytest.mark.asyncio
async def test_ir_backend_is_told_the_common_version_without_a_restart():
    created: list[FakeBackend] = []
    router = make_router(
        "ir",
        "v1",
        created=created,
        config={"ir": {"backend_class": VersionedBackend}, "v1": {"version": 1}},
    )
    result = await router.initialize(InitializeParams(protocol_version=2))
    assert result.protocol_version == 1
    ir_instances = [b for b in created if b.name == "ir"]
    assert len(ir_instances) == 1
    assert ir_instances[0].protocol_version == 1


@pytest.mark.asyncio
async def test_agent_config_option_is_reported_and_immutable():
    router = make_router("a", "b")
    created = await router.new_session_with_meta(
        NewSessionParams(cwd="/tmp"), {"agent": "a"}
    )
    assert created.config_options is not None
    option = created.config_options[0]
    assert option.id == "agent"
    assert option.current_value == "a"

    with pytest.raises(JSONRPCException) as exc:
        await router.set_config_option(
            SetConfigOptionParams(
                session_id=created.session_id, config_id="agent", value="b"
            )
        )
    assert isinstance(exc.value, JSONRPCException)
    assert exc.value.error.code == INVALID_PARAMS
    assert "fixed for the lifetime of a session" in exc.value.error.message


@pytest.mark.asyncio
async def test_audit_records_method_agent_session_duration_and_outcome():
    router = make_router("a")
    created = await router.new_session_with_meta(NewSessionParams(cwd="/tmp"), {})
    await _collect(router, created.session_id)
    with pytest.raises(JSONRPCException):
        await router.set_config_option(
            SetConfigOptionParams(
                session_id=created.session_id, config_id="agent", value="b"
            )
        )

    methods = [record.method for record in router.audit_records]
    assert "session/new" in methods
    assert "session/prompt" in methods
    prompt = next(r for r in router.audit_records if r.method == "session/prompt")
    assert prompt.agent == "a"
    assert prompt.session == created.session_id
    assert prompt.outcome == "ok"
    assert prompt.duration_ms >= 0
    failed = [r for r in router.audit_records if r.outcome == "error"]
    assert failed and failed[0].method == "session/set_config_option"


@pytest.mark.asyncio
async def test_concurrent_new_sessions_initialize_the_backend_once():
    created: list[FakeBackend] = []
    router = make_router(
        "a",
        created=created,
        config={"a": {"backend_class": SlowInitBackend}},
    )
    await router.initialize(InitializeParams(protocol_version=2))
    warmup = await router.new_session_with_meta(NewSessionParams(cwd="/tmp"), {})
    await router.close_session(warmup.session_id)
    assert created[0].stop_calls == 1

    sessions = await asyncio.gather(
        *(
            router.new_session_with_meta(NewSessionParams(cwd="/tmp"), {})
            for _ in range(3)
        )
    )
    assert len({session.session_id for session in sessions}) == 3
    assert len(created) == 2
    assert created[1].init_versions == [2]


@pytest.mark.asyncio
async def test_gateway_routes_meta_from_the_wire_to_the_router():
    router = make_router("a", "b")
    agent = GatewayAgent(router)

    result = await agent._handle_new_session({"cwd": "/tmp", "_meta": {"agent": "b"}})
    assert router.session_agents[result.session_id] == "b"


@pytest.mark.asyncio
async def test_gateway_keeps_concurrent_session_meta_isolated():
    router = make_router("a", "b")
    agent = GatewayAgent(router)

    first, second = await asyncio.gather(
        agent._handle_new_session({"cwd": "/tmp", "_meta": {"agent": "a"}}),
        agent._handle_new_session({"cwd": "/tmp", "_meta": {"agent": "b"}}),
    )
    assert router.session_agents[first.session_id] == "a"
    assert router.session_agents[second.session_id] == "b"


@pytest.mark.asyncio
async def test_gateway_set_config_option_delegates_to_the_router():
    router = make_router("a")
    agent = GatewayAgent(router)
    created = await router.new_session_with_meta(NewSessionParams(cwd="/tmp"), {})

    result = await agent.on_set_config_option(
        SetConfigOptionParams(
            session_id=created.session_id, config_id="model", value="x"
        )
    )
    assert [option.id for option in result.config_options] == ["agent"]


@pytest.mark.asyncio
async def test_acp_backend_denies_downstream_permission_requests():
    backend = create_backend("acp", acp_command="true")
    assert isinstance(backend, ACPBackend)

    outcome = await backend._handle_client_request("session/request_permission", {})
    assert outcome == {"outcome": {"outcome": "cancelled"}}
    with pytest.raises(JSONRPCException) as exc:
        await backend._handle_client_request("fs/read_text_file", {})
    assert isinstance(exc.value, JSONRPCException)
    assert exc.value.error.code == METHOD_NOT_FOUND


def test_cli_router_builds_one_factory_per_agent(monkeypatch):
    from agentabi import cli

    created: list[tuple[str, dict[str, Any]]] = []
    served: dict[str, Any] = {}

    def fake_create(name: str, **kwargs: Any) -> FakeBackend:
        created.append((name, kwargs))
        return FakeBackend(name)

    async def fake_serve(backend: RouterBackend, **kwargs) -> None:
        served["backend"] = backend

    monkeypatch.setattr(cli, "create_backend", fake_create)
    monkeypatch.setattr(cli, "serve", fake_serve)

    assert (
        cli.main(
            [
                "acp",
                "--router",
                "--agent",
                "claude",
                "--agent",
                "opencode",
                "--prefer",
                "pty",
            ]
        )
        == 0
    )
    router = served["backend"]
    assert router.enabled_agents == ["claude", "opencode"]

    asyncio.run(router.initialize(InitializeParams(protocol_version=2)))
    assert created == [
        ("claude", {"acp_command": None, "prefer": "pty"}),
        ("opencode", {"acp_command": None, "prefer": "pty"}),
    ]


def test_cli_router_deduplicates_agents(monkeypatch):
    from agentabi import cli

    served: dict[str, Any] = {}

    async def fake_serve(backend: RouterBackend, **kwargs) -> None:
        served["backend"] = backend

    monkeypatch.setattr(cli, "create_backend", lambda name, **kw: FakeBackend(name))
    monkeypatch.setattr(cli, "serve", fake_serve)

    assert cli.main(["acp", "--router", "--agent", "claude", "--agent", "claude"]) == 0
    assert served["backend"].enabled_agents == ["claude"]


@pytest.mark.parametrize(
    "argv",
    [
        ["acp", "--agent", "claude", "--agent", "opencode"],
        ["acp", "--router", "--agent", "opencode", "--prefer", "pty"],
        ["acp", "--router", "--agent", "acp"],
        ["acp", "--router"],
        ["acp"],
    ],
)
def test_cli_rejects_invalid_router_combinations(argv):
    from agentabi import cli

    with pytest.raises(SystemExit):
        cli.main(argv)
