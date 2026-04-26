"""
agentabi - Provider Registry

Maps agent names to ordered provider chains.
resolve_provider() tries each in order, returning the first available.
"""

from __future__ import annotations

from .base import Provider


class AgentNotAvailable(Exception):
    """Raised when no provider is available for the requested agent."""

    def __init__(self, agent: str) -> None:
        self.agent = agent
        available = list_agents()
        if available:
            hint = f" Available agents: {available}"
        else:
            hint = " No agents are currently available."
        super().__init__(f"No provider available for agent '{agent}'.{hint}")


def _build_provider_chain() -> dict[str, list[type[Provider]]]:
    """Build the provider chain lazily to avoid circular imports."""
    from .claude_native import ClaudeNativeProvider

    # SDK providers are imported lazily inside is_available(),
    # so we can always list them here.
    from .claude_sdk import ClaudeSDKProvider
    from .codex_native import CodexNativeProvider
    from .codex_sdk import CodexSDKProvider
    from .gemini_native import GeminiNativeProvider
    from .gemini_sdk import GeminiSDKProvider
    from .opencode_native import OpenCodeNativeProvider

    return {
        "claude_code": [ClaudeNativeProvider, ClaudeSDKProvider],
        "codex": [CodexNativeProvider, CodexSDKProvider],
        "gemini_cli": [GeminiNativeProvider, GeminiSDKProvider],
        "opencode": [OpenCodeNativeProvider],
    }


_chain_cache: dict[str, list[type[Provider]]] | None = None


def _get_chain() -> dict[str, list[type[Provider]]]:
    global _chain_cache
    if _chain_cache is None:
        _chain_cache = _build_provider_chain()
    return _chain_cache


def resolve_provider(
    agent: str,
    *,
    prefer: str | None = None,
) -> Provider:
    """Try providers in order, return first available.

    Args:
        agent: Agent identifier (e.g., "claude_code", "codex").
        prefer: Preferred provider type. "native" tries native first (default),
            "sdk" tries SDK first. If the preferred type is unavailable,
            falls back to the other.

    Returns:
        An instantiated Provider.

    Raises:
        AgentNotAvailable: If no provider is available for the agent.
    """
    chain = _get_chain()
    if agent not in chain:
        raise AgentNotAvailable(agent)

    providers = list(chain[agent])

    if prefer == "sdk":
        # Move SDK providers to front
        sdk = [p for p in providers if "SDK" in p.__name__]
        native = [p for p in providers if "SDK" not in p.__name__]
        providers = sdk + native
    # Default (prefer=None or prefer="native") keeps native-first order

    for provider_cls in providers:
        if provider_cls.is_available():
            return provider_cls()

    raise AgentNotAvailable(agent)


def get_provider(agent: str, *, prefer: str | None = None) -> Provider:
    """Alias for resolve_provider()."""
    return resolve_provider(agent, prefer=prefer)


def list_agents() -> list[str]:
    """List all registered agent names (regardless of availability).

    Returns:
        List of agent identifiers.
    """
    return list(_get_chain().keys())


def list_available_agents() -> list[str]:
    """List agents that have at least one available provider.

    Returns:
        List of agent identifiers with available providers.
    """
    available = []
    chain = _get_chain()
    for agent, providers in chain.items():
        if any(p.is_available() for p in providers):
            available.append(agent)
    return available


__all__ = [
    "AgentNotAvailable",
    "resolve_provider",
    "get_provider",
    "list_agents",
    "list_available_agents",
]
