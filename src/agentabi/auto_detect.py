"""
agentabi - Auto Detection

Utility functions for auto-detecting available agent CLIs
and getting agent capabilities.
"""

from __future__ import annotations

from typing import List

from .providers.registry import (
    AgentNotAvailable,
    get_provider,
    list_available_agents,
)
from .types.ir.capabilities import AgentCapabilities


def detect_agents() -> List[str]:
    """Detect which agents have at least one available provider.

    Returns:
        List of available agent type identifiers.

    Examples:
        >>> agents = detect_agents()
        >>> "claude_code" in agents  # True if `claude` is in PATH
    """
    return list_available_agents()


def get_agent_capabilities(agent: str) -> AgentCapabilities:
    """Get the capabilities of a specific agent.

    Args:
        agent: Agent type identifier.

    Returns:
        AgentCapabilities for the agent's active provider.

    Raises:
        AgentNotAvailable: If no provider is available.
    """
    provider = get_provider(agent)
    return provider.capabilities()


def get_default_agent() -> str:
    """Get the first available agent.

    Returns:
        The first detected agent type identifier.

    Raises:
        AgentNotAvailable: If no agents are available.
    """
    available = detect_agents()
    if not available:
        raise AgentNotAvailable("(auto-detect)")
    return available[0]


__all__ = [
    "detect_agents",
    "get_agent_capabilities",
    "get_default_agent",
]
