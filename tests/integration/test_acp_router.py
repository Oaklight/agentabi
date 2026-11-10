"""Integration test for ACP router mode with real agent CLIs.

One gateway process serves several agents; the client selects one per
session with ``_meta.agent``. Skipped unless at least two agent CLIs are
installed.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
from pathlib import Path

import pytest

from agentabi._vendor.acp import ACPClient, JSONRPCException
from agentabi._vendor.jsonrpc import INVALID_PARAMS

pytestmark = [pytest.mark.integration, pytest.mark.cross_cli]

_ROOT = Path(__file__).resolve().parents[2]
_AGENTS = ("claude", "codex", "opencode")
_PROMPT = "What is 2+2? Reply with just the number."
_TIMEOUT = 180


@pytest.fixture
async def router_client():
    """ACP client connected to a router gateway over real stdio."""
    agents = [name for name in _AGENTS if shutil.which(name)]
    if len(agents) < 2:
        pytest.skip("router mode needs at least two agent CLIs")

    env = {**os.environ, "PYTHONPATH": str(_ROOT / "src")}
    command = [sys.executable, "-m", "agentabi.cli", "acp", "--router"]
    for name in agents:
        command += ["--agent", name]

    client = ACPClient(command, env=env)
    await client.start()
    try:
        await client.initialize()
        yield client, agents
    finally:
        await client.stop()


async def test_router_answers_from_each_selected_agent(router_client, tmp_path: Path):
    """Each ``_meta.agent`` choice reaches its backend and answers the prompt."""
    client, agents = router_client

    for agent in agents:
        raw = await client._call(
            "session/new",
            {
                "cwd": str(tmp_path),
                "mcpServers": [],
                "_meta": {"agent": agent},
            },
        )
        session_id = raw["sessionId"]
        assert session_id.startswith(f"agentabi-{agent}-")

        text: list[str] = []

        async def collect() -> None:
            async for update in client.prompt(session_id, _PROMPT):
                payload = update.get("update") or {}
                if payload.get("sessionUpdate") != "agent_message_chunk":
                    continue
                content = payload.get("content") or {}
                if content.get("type") == "text":
                    text.append(content.get("text", ""))

        await asyncio.wait_for(collect(), timeout=_TIMEOUT)
        assert "4" in "".join(text), f"{agent}: unexpected answer {text!r}"


async def test_router_rejects_an_unknown_agent(router_client, tmp_path: Path):
    """An agent name the router does not serve is invalid params."""
    client, _ = router_client

    with pytest.raises(JSONRPCException) as exc:
        await client._call(
            "session/new",
            {
                "cwd": str(tmp_path),
                "mcpServers": [],
                "_meta": {"agent": "nope"},
            },
        )
    assert isinstance(exc.value, JSONRPCException)
    assert exc.value.error.code == INVALID_PARAMS
    assert "enabled agents:" in exc.value.error.message
