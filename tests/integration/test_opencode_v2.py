"""Integration tests for the OpenCode V2 HTTP/SSE provider.

These tests start a real `opencode serve` process through the provider and
need a working model configuration. They are skipped when the CLI is absent
or too old to expose the V2 API.
"""

from __future__ import annotations

import shutil

import pytest

from agentabi import Session
from agentabi.providers.opencode_v2 import OpenCodeV2Provider

from .conftest import SIMPLE_PROMPT

pytestmark = [pytest.mark.integration, pytest.mark.opencode]


def _v2_available() -> bool:
    return shutil.which("opencode") is not None and OpenCodeV2Provider.is_available()


@pytest.fixture
async def provider():
    """Create a V2 provider with its own server, skip if unusable."""
    if shutil.which("opencode") is None:
        pytest.skip("opencode CLI not available")
    if not OpenCodeV2Provider.is_available():
        pytest.skip("opencode CLI does not expose the V2 API")
    instance = OpenCodeV2Provider()
    try:
        yield instance
    finally:
        await instance.aclose()


class TestOpenCodeV2Integration:
    async def test_run(self, provider):
        """run() returns a successful SessionResult with streamed text."""
        result = await provider.run(
            {"prompt": SIMPLE_PROMPT, "agent": "opencode", "timeout": 180}
        )
        assert result["status"] == "success", result.get("errors")
        assert result["session_id"].startswith("ses_")
        assert "4" in result.get("result_text", "")
        assert result.get("usage", {}).get("output_tokens", 0) > 0

    async def test_stream_events(self, provider):
        """stream() yields the IR lifecycle events around the text."""
        event_types: list[str] = []
        text: list[str] = []
        task = {"prompt": SIMPLE_PROMPT, "agent": "opencode", "timeout": 180}
        async for event in provider.stream(task):
            event_types.append(event["type"])
            if event["type"] == "message_delta":
                text.append(event["text"])
            elif event["type"] == "message_end" and event.get("text"):
                text.append(event["text"])

        assert event_types[0] == "session_start"
        assert "message_delta" in event_types or "message_end" in event_types
        assert event_types[-1] == "session_end"
        assert "4" in "".join(text)

    async def test_tool_round_trip(self, provider):
        """A bash tool call maps to tool_use plus tool_result."""
        prompt = (
            "Use the bash tool to run `echo v2-tool-ok`, "
            "then reply with only that output."
        )
        task = {
            "prompt": prompt,
            "agent": "opencode",
            "permissions": {"level": "full_auto"},
            "timeout": 240,
        }
        events = [event async for event in provider.stream(task)]
        tool_uses = [e for e in events if e["type"] == "tool_use"]
        tool_results = [e for e in events if e["type"] == "tool_result"]
        assert tool_uses, [e["type"] for e in events]
        assert tool_results
        assert tool_uses[0]["tool_use_id"] == tool_results[0]["tool_use_id"]
        assert "v2-tool-ok" in tool_results[0]["content"]

    async def test_session_resume(self, provider):
        """resume=True reuses the given session id."""
        first = await provider.run(
            {"prompt": "Reply with exactly: alpha", "agent": "opencode", "timeout": 180}
        )
        assert first["status"] == "success", first.get("errors")
        second = await provider.run(
            {
                "prompt": "Which word did I ask you to reply with? One word only.",
                "agent": "opencode",
                "session_id": first["session_id"],
                "resume": True,
                "timeout": 180,
            }
        )
        assert second["status"] == "success", second.get("errors")
        assert second["session_id"] == first["session_id"]
        assert "alpha" in second.get("result_text", "").lower()

    async def test_session_api_resolves_v2(self):
        """Session(agent='opencode') selects the V2 provider when available."""
        if not _v2_available():
            pytest.skip("opencode V2 API not available")
        session = Session(agent="opencode")
        assert isinstance(session.provider, OpenCodeV2Provider)
        result = await session.run(SIMPLE_PROMPT, timeout=180)
        assert result["status"] == "success", result.get("errors")
        assert "4" in result.get("result_text", "")
        await session.provider.aclose()  # type: ignore[attr-defined]
