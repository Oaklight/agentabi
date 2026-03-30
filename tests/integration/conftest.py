"""Shared fixtures and constants for integration tests."""

import pytest

from agentabi import detect_agents, get_provider

# ── Constants ──────────────────────────────────────────────────
SIMPLE_PROMPT = "What is 2+2? Reply with just the number."
TIMEOUT = 120  # seconds


# ── Helpers ────────────────────────────────────────────────────
def agent_available(agent: str) -> bool:
    """Check if an agent CLI is available on this machine."""
    return agent in detect_agents()


# ── Fixtures ───────────────────────────────────────────────────
@pytest.fixture
def claude_provider():
    """Get a Claude Code provider, skip if unavailable."""
    if not agent_available("claude_code"):
        pytest.skip("claude_code CLI not available")
    return get_provider("claude_code")


@pytest.fixture
def codex_provider():
    """Get a Codex provider, skip if unavailable."""
    if not agent_available("codex"):
        pytest.skip("codex CLI not available")
    return get_provider("codex")


@pytest.fixture
def gemini_provider():
    """Get a Gemini CLI provider, skip if unavailable."""
    if not agent_available("gemini_cli"):
        pytest.skip("gemini_cli CLI not available")
    return get_provider("gemini_cli")


@pytest.fixture
def opencode_provider():
    """Get an OpenCode provider, skip if unavailable."""
    if not agent_available("opencode"):
        pytest.skip("opencode CLI not available")
    return get_provider("opencode")


@pytest.fixture
def all_available_providers():
    """Get all available providers as a list of (agent_name, provider) tuples."""
    agents = detect_agents()
    if not agents:
        pytest.skip("No agent CLIs available")
    return [(name, get_provider(name)) for name in agents]
