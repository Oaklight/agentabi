"""Cross-CLI consistency tests.

Verifies that all available agent CLIs produce consistent IR output
through the agentabi unified interface.
"""

from typing import get_args

import pytest

from agentabi import Session, detect_agents
from agentabi.types.ir.events import IREvent

from .conftest import SIMPLE_PROMPT

pytestmark = [pytest.mark.integration, pytest.mark.cross_cli]


def _valid_event_types() -> set[str]:
    """Derive the event-type set from the ``IREvent`` union."""
    values: set[str] = set()
    for event_cls in get_args(IREvent):
        for literal in get_args(event_cls.__annotations__["type"]):
            values.update(get_args(literal))
    return values


# All valid IR event type values
VALID_EVENT_TYPES = _valid_event_types()


def _get_available_sessions() -> list[tuple[str, Session]]:
    """Return (agent_name, Session) pairs for all available agents."""
    agents = detect_agents()
    return [(name, Session(agent=name)) for name in agents]


class TestCrossCliConsistency:
    async def test_all_agents_return_session_result(self):
        """All available agents return a SessionResult from run()."""
        sessions = _get_available_sessions()
        if not sessions:
            pytest.skip("No agent CLIs available")

        for name, session in sessions:
            result = await session.run(prompt=SIMPLE_PROMPT, max_turns=2)
            assert isinstance(result, dict), f"{name}: result is not a dict"
            assert "session_id" in result or "status" in result, (
                f"{name}: result missing session_id and status"
            )

    async def test_all_agents_stream_standard_events(self):
        """All available agents produce at least session_start in stream()."""
        sessions = _get_available_sessions()
        if not sessions:
            pytest.skip("No agent CLIs available")

        for name, session in sessions:
            event_types: set[str] = set()
            async for event in session.stream(prompt=SIMPLE_PROMPT, max_turns=2):
                event_types.add(event["type"])

            assert "session_start" in event_types, (
                f"{name}: missing session_start, got: {event_types}"
            )
            has_text = "message_delta" in event_types or "message_end" in event_types
            assert has_text, f"{name}: no text events, got: {event_types}"

    async def test_all_agents_answer_correctly(self):
        """All available agents answer '2+2' with '4'."""
        sessions = _get_available_sessions()
        if not sessions:
            pytest.skip("No agent CLIs available")

        for name, session in sessions:
            result = await session.run(prompt=SIMPLE_PROMPT, max_turns=2)
            text = result.get("result_text", "")
            assert "4" in text, f"{name}: expected '4' in result, got: {text!r}"

    async def test_event_type_values_are_valid(self):
        """All events have a 'type' in the defined IR event types."""
        sessions = _get_available_sessions()
        if not sessions:
            pytest.skip("No agent CLIs available")

        for name, session in sessions:
            async for event in session.stream(prompt=SIMPLE_PROMPT, max_turns=2):
                assert event["type"] in VALID_EVENT_TYPES, (
                    f"{name}: unknown event type {event['type']!r}"
                )
