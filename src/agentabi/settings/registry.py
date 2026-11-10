"""Settings adapter registry.

One adapter per transport, keyed by the provider name.  The registry holds
no provider imports, so importing it from a provider cannot create a cycle.
"""

from __future__ import annotations

from .adapters import (
    AgySettings,
    ClaudeNativeSettings,
    ClaudeSdkSettings,
    ClaudeTuiSettings,
    CodexNativeSettings,
    CodexSdkSettings,
    GeminiNativeSettings,
    GeminiSdkSettings,
    OpenCodeV1Settings,
    OpenCodeV2Settings,
    PiSettings,
)
from .base import SettingsAdapter
from .specs import SETTINGS_SPECS, SettingsSpec, spec_for

_ADAPTERS: dict[str, SettingsAdapter] = {
    adapter.name: adapter
    for adapter in (
        ClaudeNativeSettings(),
        ClaudeTuiSettings(),
        ClaudeSdkSettings(),
        CodexNativeSettings(),
        CodexSdkSettings(),
        AgySettings(),
        GeminiNativeSettings(),
        GeminiSdkSettings(),
        OpenCodeV1Settings(),
        OpenCodeV2Settings(),
        PiSettings(),
    )
}


def adapter_for(provider: str) -> SettingsAdapter:
    """Return the adapter for a provider name.

    Raises:
        KeyError: If the provider has no adapter.
    """
    return _ADAPTERS[provider]


def spec_of(provider: str) -> SettingsSpec:
    """Return the settings spec for a provider name."""
    return spec_for(provider)


def supported_settings(provider: str) -> list[str]:
    """Return the sorted settings fields a provider honours."""
    return sorted(adapter_for(provider).spec.supported)


__all__ = [
    "SETTINGS_SPECS",
    "adapter_for",
    "spec_of",
    "supported_settings",
]
