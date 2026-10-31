"""Tests for the external ACP server backend."""

from __future__ import annotations

import shutil

import pytest

from agentabi._vendor.acp import ACPClient
from agentabi.acp.backends import ACPBackend, create_backend


def test_create_acp_backend_from_command():
    backend = create_backend("acp", acp_command="claude-agent-acp --verbose")
    assert isinstance(backend, ACPBackend)
    assert backend.name == "acp"


def test_acp_backend_requires_a_command():
    with pytest.raises(ValueError, match="requires a command"):
        create_backend("acp")


def test_unknown_backend_lists_acp():
    with pytest.raises(ValueError, match="acp"):
        create_backend("nope")


@pytest.mark.skipif(
    shutil.which("claude-agent-acp") is None,
    reason="claude-agent-acp is not installed",
)
@pytest.mark.asyncio
async def test_external_claude_adapter_handshake():
    client = ACPClient(["claude-agent-acp"])
    await client.start()
    try:
        init = await client.initialize()
        assert init.protocol_version == 1
        assert init.agent_info is not None
    finally:
        await client.stop()
