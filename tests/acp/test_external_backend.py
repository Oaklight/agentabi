"""Tests for the external ACP server backend."""

from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path

import pytest

from agentabi._vendor.acp import (
    ACPClient,
    InitializeParams,
    NewSessionParams,
    PromptParams,
    StopReason,
    TextContent,
)
from agentabi.acp.backends import ACPBackend, create_backend

_FAKE_SERVER = Path(__file__).with_name("fake_acp_server.py")


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


@pytest.mark.asyncio
async def test_concurrent_sessions_are_isolated():
    backend = ACPBackend([sys.executable, str(_FAKE_SERVER)], name="fake")
    await backend.initialize(InitializeParams())
    first = await backend.new_session(NewSessionParams(cwd="/tmp"))
    second = await backend.new_session(NewSessionParams(cwd="/tmp"))

    async def run(session_id: str, marker: str):
        chunks = []
        async for update in backend.prompt(
            PromptParams(
                session_id=session_id,
                prompt=[TextContent(text=marker)],
            )
        ):
            content = update.get("content", {})
            if update.get("sessionUpdate") == "agent_message_chunk":
                chunks.append(content.get("text", ""))
        return "".join(chunks)

    first_text, second_text = await asyncio.gather(
        run(first.session_id, "APPLE"),
        run(second.session_id, "BANANA"),
    )
    assert first_text == "APPLEAPPLEAPPLE"
    assert second_text == "BANANABANANABANANA"
    first_result = backend.prompt_result(first.session_id)
    second_result = backend.prompt_result(second.session_id)
    assert first_result is not None and first_result.stop_reason == StopReason.END_TURN
    assert (
        second_result is not None and second_result.stop_reason == StopReason.END_TURN
    )
    await backend.stop()
