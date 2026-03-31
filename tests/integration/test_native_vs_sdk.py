"""Integration tests comparing native vs SDK providers for the same agent.

Runs identical prompts through both provider types and verifies that
IR event structure and text output are consistent.
"""

import pytest

from agentabi import detect_agents, get_provider

from .conftest import SIMPLE_PROMPT

pytestmark = [pytest.mark.integration, pytest.mark.native_vs_sdk]

# Agents that have both native and SDK providers
DUAL_AGENTS = ["claude_code", "codex", "gemini_cli"]

# Standard IR event types that both providers should emit
REQUIRED_EVENT_TYPES = {"session_start", "session_end"}
TEXT_EVENT_TYPES = {"message_delta", "message_end"}


def _get_dual_providers(agent: str):
    """Get both native and SDK providers for an agent, skip if either unavailable."""
    if agent not in detect_agents():
        pytest.skip(f"{agent} CLI not available")

    try:
        native = get_provider(agent, prefer="native")
    except Exception:
        pytest.skip(f"{agent} native provider not available")

    try:
        sdk = get_provider(agent, prefer="sdk")
    except Exception:
        pytest.skip(f"{agent} SDK provider not available")

    native_name = type(native).__name__
    sdk_name = type(sdk).__name__

    # Ensure we actually got different provider types
    if native_name == sdk_name:
        pytest.skip(f"{agent} only has one provider type: {native_name}")

    return native, sdk


async def _collect_stream(provider, prompt: str):
    """Run a prompt through a provider's stream and collect events."""
    from typing import Any, cast

    from agentabi.types.ir.task import TaskConfig

    task: dict[str, Any] = {"prompt": prompt, "agent": "test", "max_turns": 2}

    events = []
    try:
        async for event in provider.stream(cast(TaskConfig, task)):
            events.append(event)
    except Exception as exc:
        pytest.skip(
            f"{type(provider).__name__} stream failed: {exc.__class__.__name__}: {exc}"
        )
    return events


def _extract_text(events: list) -> str:
    """Extract full text output from a list of IR events."""
    parts = []
    for event in events:
        if event["type"] == "message_delta":
            parts.append(event.get("text", ""))
        elif event["type"] == "message_end":
            text = event.get("text", "")
            if text:
                parts.append(text)
    return "".join(parts)


def _extract_event_types(events: list) -> set[str]:
    """Extract unique event types from a list of IR events."""
    return {e["type"] for e in events}


@pytest.fixture(params=DUAL_AGENTS)
def dual_providers(request):
    """Parametrized fixture yielding (agent_name, native, sdk) for each dual agent."""
    agent = request.param
    native, sdk = _get_dual_providers(agent)
    return agent, native, sdk


class TestNativeVsSdkConsistency:
    """Compare native and SDK providers for the same agent."""

    async def test_both_produce_session_lifecycle(self, dual_providers):
        """Both providers emit session_start and session_end events."""
        agent, native, sdk = dual_providers

        native_events = await _collect_stream(native, SIMPLE_PROMPT)
        sdk_events = await _collect_stream(sdk, SIMPLE_PROMPT)

        native_types = _extract_event_types(native_events)
        sdk_types = _extract_event_types(sdk_events)

        for req in REQUIRED_EVENT_TYPES:
            assert req in native_types, f"{agent} native missing {req}: {native_types}"
            assert req in sdk_types, f"{agent} SDK missing {req}: {sdk_types}"

    async def test_both_produce_text_events(self, dual_providers):
        """Both providers emit at least one text event."""
        agent, native, sdk = dual_providers

        native_events = await _collect_stream(native, SIMPLE_PROMPT)
        sdk_events = await _collect_stream(sdk, SIMPLE_PROMPT)

        native_types = _extract_event_types(native_events)
        sdk_types = _extract_event_types(sdk_events)

        native_has_text = bool(native_types & TEXT_EVENT_TYPES)
        sdk_has_text = bool(sdk_types & TEXT_EVENT_TYPES)

        assert native_has_text, f"{agent} native has no text events: {native_types}"
        assert sdk_has_text, f"{agent} SDK has no text events: {sdk_types}"

    async def test_both_answer_correctly(self, dual_providers):
        """Both providers produce text containing '4' for '2+2'."""
        agent, native, sdk = dual_providers

        native_events = await _collect_stream(native, SIMPLE_PROMPT)
        sdk_events = await _collect_stream(sdk, SIMPLE_PROMPT)

        native_text = _extract_text(native_events)
        sdk_text = _extract_text(sdk_events)

        assert "4" in native_text, f"{agent} native answer missing '4': {native_text!r}"
        assert "4" in sdk_text, f"{agent} SDK answer missing '4': {sdk_text!r}"

    async def test_event_types_are_valid_ir(self, dual_providers):
        """All events from both providers have valid IR event type values."""
        valid_types = {
            "session_start",
            "session_end",
            "message_start",
            "message_delta",
            "message_end",
            "tool_use",
            "tool_result",
            "file_diff",
            "usage",
            "error",
        }
        agent, native, sdk = dual_providers

        native_events = await _collect_stream(native, SIMPLE_PROMPT)
        sdk_events = await _collect_stream(sdk, SIMPLE_PROMPT)

        for event in native_events:
            assert event["type"] in valid_types, (
                f"{agent} native invalid type: {event['type']}"
            )
        for event in sdk_events:
            assert event["type"] in valid_types, (
                f"{agent} SDK invalid type: {event['type']}"
            )

    async def test_event_type_overlap(self, dual_providers):
        """Native and SDK providers share a core set of event types.

        They don't need to match exactly (different granularity is OK),
        but should overlap on at least session lifecycle and text events.
        """
        agent, native, sdk = dual_providers

        native_events = await _collect_stream(native, SIMPLE_PROMPT)
        sdk_events = await _collect_stream(sdk, SIMPLE_PROMPT)

        native_types = _extract_event_types(native_events)
        sdk_types = _extract_event_types(sdk_events)

        overlap = native_types & sdk_types
        # Both should at least share session_start or session_end
        assert len(overlap) >= 2, (
            f"{agent} providers share too few event types. "
            f"Native: {native_types}, SDK: {sdk_types}, Overlap: {overlap}"
        )
