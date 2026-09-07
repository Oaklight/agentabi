"""
agentabi - Providers

Flat provider model: each provider implements the same Protocol
for driving a specific agent CLI or SDK.
"""

from .agy_native import AgyNativeProvider
from .base import Provider
from .registry import (
    AgentNotAvailable,
    get_provider,
    list_agents,
    resolve_provider,
)

__all__ = [
    "AgyNativeProvider",
    "Provider",
    "AgentNotAvailable",
    "resolve_provider",
    "get_provider",
    "list_agents",
]
