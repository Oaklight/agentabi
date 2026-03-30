"""Integration tests for OpenCode CLI."""

import pytest

from agentabi import Session

from .conftest import SIMPLE_PROMPT

pytestmark = [pytest.mark.integration, pytest.mark.opencode]


@pytest.fixture
def session():
    """Create an OpenCode session, skip if unavailable."""
    from agentabi import detect_agents

    if "opencode" not in detect_agents():
        pytest.skip("opencode CLI not available")
    return Session(agent="opencode")


class TestOpenCodeIntegration:
    async def test_run(self, session):
        """run() returns SessionResult with status and result_text."""
        result = await session.run(prompt=SIMPLE_PROMPT, max_turns=2)
        assert result.get("status") in ("success", "completed", None)
        text = result.get("result_text", "")
        assert "4" in text

    async def test_stream_events(self, session):
        """stream() yields standard IR event types."""
        event_types: list[str] = []
        async for event in session.stream(prompt=SIMPLE_PROMPT, max_turns=2):
            event_types.append(event["type"])

        assert "session_start" in event_types
        has_text = "message_delta" in event_types or "message_end" in event_types
        assert has_text, f"No text events found in: {event_types}"
        assert "session_end" in event_types

    async def test_stream_text_content(self, session):
        """stream() text content includes the expected answer."""
        text_parts: list[str] = []
        async for event in session.stream(prompt=SIMPLE_PROMPT, max_turns=2):
            if event["type"] == "message_delta":
                text_parts.append(event["text"])
            elif event["type"] == "message_end":
                end_text = event.get("text", "")
                if end_text:
                    text_parts.append(end_text)

        full_text = "".join(text_parts)
        assert "4" in full_text
