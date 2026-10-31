"""Unit tests for the ACP gateway and backend registry."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

from agentabi._vendor.acp import (
    ACPClient,
    AgentCapabilities,
    CancelParams,
    CloseSessionParams,
    InitializeParams,
    InitializeResult,
    LoadSessionParams,
    NewSessionParams,
    NewSessionResult,
    PromptParams,
    PromptResult,
    StopReason,
    TextContent,
)
from agentabi.acp.backends import OpenCodeBackend, create_backend
from agentabi.acp.gateway import GatewayAgent


class FakeBackend:
    """Deterministic backend used by gateway unit tests."""

    name = "fake"

    def __init__(self) -> None:
        self.started = False
        self.cancelled: list[str] = []
        self.closed: list[str] = []
        self._last_result = PromptResult(stop_reason=StopReason.END_TURN)

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.started = False

    async def initialize(self, params: InitializeParams) -> InitializeResult:
        return InitializeResult(
            protocol_version=params.protocol_version,
            agent_capabilities=AgentCapabilities(load_session=True),
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

    @property
    def last_prompt_result(self) -> PromptResult:
        return self._last_result

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
    assert result.protocol_version == 1
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
    assert result.protocol_version == 1


@pytest.mark.asyncio
async def test_gateway_defaults_missing_prompt_result_to_end_turn():
    backend = FakeBackend()
    backend._last_result = None  # type: ignore[assignment]
    agent = GatewayAgent(backend)
    agent._transport = FakeTransport()  # type: ignore[assignment]

    result = await agent.on_prompt(
        PromptParams(session_id="s1", prompt=[TextContent(text="hi")])
    )
    assert result.stop_reason == StopReason.END_TURN


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
