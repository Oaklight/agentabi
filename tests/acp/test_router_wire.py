"""Wire-level tests for router mode: real stdio, real JSON-RPC framing."""

from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path

import pytest

from agentabi._vendor.acp import (
    ACPClient,
    JSONRPCException,
)
from agentabi._vendor.jsonrpc import INVALID_PARAMS

_ROOT = Path(__file__).resolve().parents[2]
_FAKE_SERVER = Path(__file__).with_name("fake_acp_server.py")


@pytest.fixture
async def router_client():
    """ACP client connected to a one-agent router gateway."""
    env = {**os.environ, "PYTHONPATH": str(_ROOT / "src")}
    command = [
        sys.executable,
        "-m",
        "agentabi.cli",
        "acp",
        "--router",
        "--agent",
        "acp",
        "--acp-command",
        shlex.join([sys.executable, str(_FAKE_SERVER)]),
    ]
    client = ACPClient(command, env=env)
    await client.start()
    try:
        yield client
    finally:
        await client.stop()


@pytest.mark.asyncio
async def test_session_new_selects_an_agent_from_meta(router_client, tmp_path: Path):
    client: ACPClient = router_client
    init = await client.initialize()
    assert init.agent_info is not None
    assert init.agent_info.name == "agentabi"

    raw = await client._call(
        "session/new",
        {
            "cwd": str(tmp_path),
            "mcpServers": [],
            "_meta": {"agent": "acp"},
        },
    )
    assert raw["sessionId"].startswith("agentabi-acp-")
    assert raw["configOptions"][0]["id"] == "agent"
    assert raw["configOptions"][0]["currentValue"] == "acp"


@pytest.mark.asyncio
async def test_unknown_agent_over_the_wire_is_invalid_params(
    router_client, tmp_path: Path
):
    client: ACPClient = router_client
    await client.initialize()

    with pytest.raises(JSONRPCException) as exc:
        await client._call(
            "session/new",
            {"cwd": str(tmp_path), "_meta": {"agent": "nope"}},
        )
    assert isinstance(exc.value, JSONRPCException)
    assert exc.value.error.code == INVALID_PARAMS
    assert "enabled agents: acp" in exc.value.error.message


@pytest.mark.asyncio
async def test_prompt_uses_the_mapped_backend_session(router_client, tmp_path: Path):
    client: ACPClient = router_client
    await client.initialize()

    raw = await client._call(
        "session/new",
        {"cwd": str(tmp_path), "_meta": {"agent": "acp"}},
    )
    session_id = raw["sessionId"]
    chunks = [update async for update in client.prompt(session_id, "hello")]
    assert chunks
    assert client.prompt_result(session_id) is not None


def test_router_backend_requires_at_least_one_agent():
    from agentabi.acp.router import RouterBackend

    with pytest.raises(ValueError, match="at least one agent"):
        RouterBackend({})


@pytest.mark.asyncio
async def test_two_agents_share_one_gateway_and_negotiate_the_lowest_version(
    tmp_path: Path,
):
    """An IR backend (v2) and an external ACP backend (v1) in one process."""
    env = {**os.environ, "PYTHONPATH": str(_ROOT / "src")}
    command = [
        sys.executable,
        "-m",
        "agentabi.cli",
        "acp",
        "--router",
        "--agent",
        "claude",
        "--agent",
        "acp",
        "--acp-command",
        shlex.join([sys.executable, str(_FAKE_SERVER)]),
    ]
    client = ACPClient(command, env=env)
    await client.start()
    try:
        init = await client.initialize()
        assert init.protocol_version == 1
        assert init.agent_info is not None
        assert init.agent_info.name == "agentabi"

        raw = await client._call(
            "session/new",
            {"cwd": str(tmp_path), "_meta": {"agent": "acp"}},
        )
        assert raw["sessionId"].startswith("agentabi-acp-")
        chunks = [update async for update in client.prompt(raw["sessionId"], "hello")]
        assert chunks
    finally:
        await client.stop()
