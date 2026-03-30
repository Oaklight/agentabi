"""Tests for Provider protocol and registry."""

from unittest.mock import patch

import pytest

from agentabi.providers.base import Provider
from agentabi.providers.registry import (
    AgentNotAvailable,
    list_agents,
    list_available_agents,
    resolve_provider,
)


class TestProviderProtocol:
    """Test that Provider protocol works as a structural type check."""

    def test_claude_native_is_provider(self):
        from agentabi.providers.claude_native import ClaudeNativeProvider

        assert isinstance(ClaudeNativeProvider(), Provider)

    def test_opencode_native_is_provider(self):
        from agentabi.providers.opencode_native import OpenCodeNativeProvider

        assert isinstance(OpenCodeNativeProvider(), Provider)


class TestRegistry:
    """Test provider registry and resolution."""

    def test_list_agents_returns_all(self):
        agents = list_agents()
        assert "claude_code" in agents
        assert "codex" in agents
        assert "gemini_cli" in agents
        assert "opencode" in agents

    def test_resolve_unknown_agent_raises(self):
        with pytest.raises(AgentNotAvailable, match="no_such_agent"):
            resolve_provider("no_such_agent")

    @patch("shutil.which", return_value="/usr/bin/claude")
    def test_resolve_claude_native(self, mock_which):
        from agentabi.providers.claude_native import ClaudeNativeProvider

        provider = resolve_provider("claude_code")
        assert isinstance(provider, ClaudeNativeProvider)

    @patch("agentabi.providers.claude_sdk.ClaudeSDKProvider.is_available", return_value=False)
    @patch("shutil.which", return_value=None)
    def test_resolve_claude_no_cli_no_sdk_raises(self, mock_which, mock_sdk):
        """When neither CLI nor SDK is available, should raise."""
        with pytest.raises(AgentNotAvailable):
            resolve_provider("claude_code")

    def test_list_available_agents(self):
        available = list_available_agents()
        assert isinstance(available, list)


class TestAgentNotAvailable:
    """Test AgentNotAvailable exception."""

    def test_message_contains_agent_name(self):
        exc = AgentNotAvailable("test_agent")
        assert "test_agent" in str(exc)

    def test_agent_attribute(self):
        exc = AgentNotAvailable("test_agent")
        assert exc.agent == "test_agent"
