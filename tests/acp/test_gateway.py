"""Unit tests for the ACP gateway and backend registry."""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

from agentabi._vendor.acp import (
    ACPClient,
    AgentCapabilities,
    AuthenticateParams,
    AuthMethod,
    CancelParams,
    ClientCapabilities,
    CloseSessionParams,
    InitializeParams,
    InitializeResult,
    JSONRPCException,
    LoadSessionParams,
    LogoutParams,
    NewSessionParams,
    NewSessionResult,
    PromptParams,
    PromptResult,
    SessionClientCapabilities,
    SetConfigOptionParams,
    StopReason,
    TextContent,
)
from agentabi._vendor.jsonrpc import METHOD_NOT_FOUND
from agentabi.acp.backends import OpenCodeBackend, create_backend
from agentabi.acp.gateway import GatewayAgent
from agentabi.acp.permissions import PermissionBroker
from agentabi.types.ir.events import PermissionRequestEvent
from agentabi.types.ir.permissions import PermissionDecision


class FakeBackend:
    """Deterministic backend used by gateway unit tests."""

    name = "fake"
    permission_capable = True

    def __init__(self) -> None:
        self.started = False
        self.cancelled: list[str] = []
        self.closed: list[str] = []
        self.auth_methods: list[AuthMethod] | None = None
        self.broker: PermissionBroker | None = None
        self._last_result: PromptResult | None = PromptResult(
            stop_reason=StopReason.END_TURN
        )
        self._last_error: str | None = None

    def set_permission_broker(self, broker: PermissionBroker) -> None:
        self.broker = broker

    async def answer_permission(
        self, session_id: str, request_id: str, decision: PermissionDecision
    ) -> None:
        """No-op: this backend answers inline."""

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.started = False

    async def initialize(self, params: InitializeParams) -> InitializeResult:
        return InitializeResult(
            protocol_version=params.protocol_version,
            agent_capabilities=AgentCapabilities(load_session=True),
            auth_methods=self.auth_methods,
        )

    async def new_session(self, params: NewSessionParams) -> NewSessionResult:
        return NewSessionResult(session_id="s1")

    async def load_session(self, params: LoadSessionParams):
        yield {
            "sessionUpdate": "agent_message_chunk",
            "content": {"type": "text", "text": "replayed"},
        }

    async def prompt(self, params: PromptParams):
        yield {
            "sessionUpdate": "agent_message_chunk",
            "content": {"type": "text", "text": "hello"},
        }

    def prompt_result(self, session_id: str) -> PromptResult | None:
        return self._last_result

    def prompt_error(self, session_id: str) -> str | None:
        return self._last_error

    async def cancel(self, session_id: str) -> None:
        self.cancelled.append(session_id)

    async def close_session(self, session_id: str) -> None:
        self.closed.append(session_id)


class FakeTransport:
    """Capture outgoing ACP notifications."""

    def __init__(self) -> None:
        self.notifications: list[tuple[str, dict[str, Any]]] = []

    async def send_notification(
        self, method: str, params: dict[str, Any] | None = None
    ) -> None:
        self.notifications.append((method, params or {}))


@pytest.mark.asyncio
async def test_gateway_initializes_and_overrides_agent_info():
    backend = FakeBackend()
    agent = GatewayAgent(backend)
    result = await agent.on_initialize(InitializeParams())
    assert backend.started is False  # GatewayAgent does not start the backend
    assert result.protocol_version == 2
    assert result.agent_info is not None
    assert result.agent_info.name == "agentabi"
    assert result.agent_capabilities is not None
    assert result.agent_capabilities.load_session is True


@pytest.mark.asyncio
async def test_gateway_new_session_delegates():
    agent = GatewayAgent(FakeBackend())
    result = await agent.on_new_session(NewSessionParams(cwd="/tmp"))
    assert result.session_id == "s1"


@pytest.mark.asyncio
async def test_gateway_negotiates_its_own_protocol_version():
    agent = GatewayAgent(FakeBackend())
    result = await agent.on_initialize(InitializeParams(protocol_version=2))
    assert result.protocol_version == 2


@pytest.mark.asyncio
async def test_gateway_downgrades_to_the_backend_version():
    class _V1Backend(FakeBackend):
        async def initialize(self, params: InitializeParams) -> InitializeResult:
            return InitializeResult(
                protocol_version=1,
                agent_capabilities=AgentCapabilities(load_session=True),
            )

    agent = GatewayAgent(_V1Backend())
    result = await agent.on_initialize(InitializeParams(protocol_version=2))
    assert result.protocol_version == 1


@pytest.mark.asyncio
async def test_gateway_tells_ir_backend_which_version_to_emit():
    class _VersionBackend(FakeBackend):
        def __init__(self) -> None:
            super().__init__()
            self.version: int | None = None

        def set_protocol_version(self, version: int) -> None:
            self.version = version

    backend = _VersionBackend()
    agent = GatewayAgent(backend)
    await agent.on_initialize(InitializeParams(protocol_version=2))
    assert backend.version == 2


@pytest.mark.asyncio
async def test_gateway_defaults_missing_prompt_result_to_unknown():
    backend = FakeBackend()
    backend._last_result = None  # type: ignore[assignment]
    agent = GatewayAgent(backend)
    agent._transport = FakeTransport()  # type: ignore[assignment]

    result = await agent.on_prompt(
        PromptParams(session_id="s1", prompt=[TextContent(text="hi")])
    )
    assert result.stop_reason == StopReason.UNKNOWN


@pytest.mark.asyncio
async def test_gateway_does_not_advertise_unhandled_auth_methods():
    backend = FakeBackend()
    backend.auth_methods = [AuthMethod(id="login", name="Login")]
    agent = GatewayAgent(backend)
    result = await agent.on_initialize(InitializeParams())
    assert result.auth_methods is None


@pytest.mark.asyncio
async def test_gateway_rejects_authenticate_and_logout():
    agent = GatewayAgent(FakeBackend())
    with pytest.raises(JSONRPCException) as auth_exc:
        await agent.on_authenticate(AuthenticateParams(method_id="login"))
    assert isinstance(auth_exc.value, JSONRPCException)
    assert auth_exc.value.error.code == METHOD_NOT_FOUND
    with pytest.raises(JSONRPCException) as logout_exc:
        await agent.on_logout(LogoutParams())
    assert isinstance(logout_exc.value, JSONRPCException)
    assert logout_exc.value.error.code == METHOD_NOT_FOUND


@pytest.mark.asyncio
async def test_gateway_drops_updates_the_client_did_not_advertise():
    backend = FakeBackend()
    agent = GatewayAgent(backend)
    transport = FakeTransport()
    agent._transport = transport  # type: ignore[assignment]
    agent._client_capabilities = ClientCapabilities()

    await agent._send_raw_update("s1", {"sessionUpdate": "notice", "title": "x"})
    await agent._send_raw_update(
        "s1", {"sessionUpdate": "agent_message_chunk", "content": {"text": "hi"}}
    )
    assert len(transport.notifications) == 1
    assert transport.notifications[0][1]["update"]["sessionUpdate"] == (
        "agent_message_chunk"
    )


@pytest.mark.asyncio
async def test_gateway_forwards_gated_updates_when_advertised():
    backend = FakeBackend()
    agent = GatewayAgent(backend)
    transport = FakeTransport()
    agent._transport = transport  # type: ignore[assignment]
    agent._client_capabilities = ClientCapabilities(
        session=SessionClientCapabilities(notices={})
    )

    await agent._send_raw_update("s1", {"sessionUpdate": "notice", "title": "x"})
    assert len(transport.notifications) == 1
    assert transport.notifications[0][1]["update"]["sessionUpdate"] == "notice"


@pytest.mark.asyncio
async def test_gateway_prompt_forwards_raw_updates():
    agent = GatewayAgent(FakeBackend())
    transport = FakeTransport()
    agent._transport = transport  # type: ignore[assignment]

    result = await agent.on_prompt(
        PromptParams(session_id="s1", prompt=[TextContent(text="hi")])
    )

    assert result.stop_reason == StopReason.END_TURN
    assert len(transport.notifications) == 1
    method, params = transport.notifications[0]
    assert method == "session/update"
    assert params["sessionId"] == "s1"
    assert params["update"]["sessionUpdate"] == "agent_message_chunk"


@pytest.mark.asyncio
async def test_gateway_cancel_and_close_delegate():
    backend = FakeBackend()
    agent = GatewayAgent(backend)
    await agent.on_cancel(CancelParams(session_id="s1"))
    await agent.on_close_session(CloseSessionParams(session_id="s1"))
    assert backend.cancelled == ["s1"]
    assert backend.closed == ["s1"]


def test_backend_registry():
    backend = create_backend("opencode")
    assert isinstance(backend, OpenCodeBackend)
    with pytest.raises(ValueError, match="Unsupported ACP backend"):
        create_backend("unknown")


@pytest.mark.skipif(
    shutil.which("opencode") is None,
    reason="opencode CLI is not installed",
)
@pytest.mark.asyncio
async def test_cli_opencode_handshake(tmp_path: Path):
    root = Path(__file__).resolve().parents[2]
    env = {
        **os.environ,
        "PYTHONPATH": str(root / "src"),
    }
    client = ACPClient(
        [sys.executable, "-m", "agentabi.cli", "acp", "--agent", "opencode"],
        env=env,
    )
    await client.start()
    try:
        init = await client.initialize()
        assert init.agent_info is not None
        assert init.agent_info.name == "agentabi"
        session = await client.new_session(str(tmp_path), mcp_servers=[])
        assert session.session_id
    finally:
        await client.stop()


def test_cli_passes_prefer_to_backend(monkeypatch):
    from agentabi import cli

    seen: dict[str, object] = {}

    def fake_create(name, **kwargs):
        seen.update(name=name, **kwargs)
        return object()

    async def fake_serve(backend, **kwargs):
        seen["served"] = True
        seen.update(kwargs)

    monkeypatch.setattr(cli, "create_backend", fake_create)
    monkeypatch.setattr(cli, "serve", fake_serve)
    assert cli.main(["acp", "--agent", "claude", "--prefer", "pty"]) == 0
    assert seen == {
        "name": "claude",
        "acp_command": None,
        "prefer": "pty",
        "served": True,
        "permission_timeout": 0.0,
        "permission_policy": "ask",
    }


def test_cli_rejects_prefer_for_opencode():
    from agentabi import cli

    with pytest.raises(SystemExit):
        cli.main(["acp", "--agent", "opencode", "--prefer", "pty"])


class _NeverAnswer:
    """Requester that keeps a permission request pending."""

    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def request_permission(self, session_id, tool_call, options):
        self.started.set()
        await asyncio.Event().wait()


def _permission_request() -> PermissionRequestEvent:
    return {
        "type": "permission_request",
        "request_id": "toolu_1",
        "tool_name": "Bash",
        "tool_use_id": "toolu_1",
        "tool_input": {"command": "touch marker1"},
        "options": [
            {"option_id": "allow_once", "name": "Allow once", "kind": "allow_once"},
            {"option_id": "reject_once", "name": "Reject", "kind": "reject_once"},
        ],
    }


@pytest.mark.asyncio
async def test_new_session_advertises_permission_mode():
    backend = FakeBackend()
    agent = GatewayAgent(backend)
    broker = PermissionBroker(_NeverAnswer())
    agent.set_permission_broker(broker)

    result = await agent.on_new_session(NewSessionParams(cwd="/tmp"))

    assert result.config_options is not None
    option = next(
        item for item in result.config_options if item.id == "permission_mode"
    )
    assert option.current_value == "ask"
    assert [value.value for value in option.options] == ["ask", "deny", "allow_all"]


@pytest.mark.asyncio
async def test_non_capable_backend_does_not_offer_ask():
    class _StaticBackend(FakeBackend):
        permission_capable = False

    agent = GatewayAgent(_StaticBackend())
    broker = PermissionBroker(_NeverAnswer())
    agent.set_permission_broker(broker)

    result = await agent.on_new_session(NewSessionParams(cwd="/tmp"))

    assert result.config_options is not None
    option = next(
        item for item in result.config_options if item.id == "permission_mode"
    )
    assert option.current_value == "deny"
    assert [value.value for value in option.options] == ["deny", "allow_all"]


@pytest.mark.asyncio
async def test_set_config_option_changes_permission_policy():
    backend = FakeBackend()
    agent = GatewayAgent(backend)
    broker = PermissionBroker(_NeverAnswer())
    agent.set_permission_broker(broker)
    await agent.on_new_session(NewSessionParams(cwd="/tmp"))

    result = await agent.on_set_config_option(
        SetConfigOptionParams(
            session_id="s1", config_id="permission_mode", value="deny"
        )
    )

    assert broker.policy_for("s1") == "deny"
    assert result.config_options
    option = next(
        item for item in result.config_options if item.id == "permission_mode"
    )
    assert option.current_value == "deny"


@pytest.mark.asyncio
async def test_set_config_option_rejects_unknown_policy():
    agent = GatewayAgent(FakeBackend())
    broker = PermissionBroker(_NeverAnswer())
    agent.set_permission_broker(broker)

    with pytest.raises(JSONRPCException):
        await agent.on_set_config_option(
            SetConfigOptionParams(
                session_id="s1", config_id="permission_mode", value="maybe"
            )
        )


@pytest.mark.asyncio
async def test_cancel_resolves_a_pending_permission():
    backend = FakeBackend()
    agent = GatewayAgent(backend)
    requester = _NeverAnswer()
    broker = PermissionBroker(requester)
    agent.set_permission_broker(broker)

    task = asyncio.create_task(broker.ask("s1", _permission_request()))
    await asyncio.wait_for(requester.started.wait(), timeout=1)

    await agent.on_cancel(CancelParams(session_id="s1"))
    decision = await asyncio.wait_for(task, timeout=1)

    assert decision == {"option_id": None, "outcome": "cancelled"}
    assert backend.cancelled == ["s1"]


@pytest.mark.asyncio
async def test_close_session_forgets_permission_state():
    backend = FakeBackend()
    agent = GatewayAgent(backend)
    broker = PermissionBroker(_NeverAnswer())
    agent.set_permission_broker(broker)
    broker.set_policy("s1", "deny")

    await agent.on_close_session(CloseSessionParams(session_id="s1"))

    assert broker.policy_for("s1") == "ask"
    assert backend.closed == ["s1"]
