"""Per-provider settings specification table.

This table is the single place that records how each transport receives
settings.  Everything else (adapters, capabilities, docs) reads from it, so
a CLI difference appears exactly once.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SettingsField = Literal[
    "base_url",
    "api_key",
    "api_key_env",
    "model_provider",
    "headers",
    "env",
    "args",
    "native",
]

SettingsMechanism = Literal[
    "settings_json",  # claude, agy: --settings JSON
    "config_overrides",  # codex: -c key=value
    "env_map",  # gemini: provider environment variables
    "config_content",  # opencode V1: OPENCODE_CONFIG_CONTENT
    "models_json",  # pi: temporary agent directory with models.json
    "v1_delegation",  # opencode V2: injected endpoints only work through V1
    "sdk_options",  # SDK providers: option objects
    "none",  # nothing can be injected
]

_ENDPOINT = frozenset({"base_url", "api_key", "api_key_env"})
_ESCAPE = frozenset({"env", "args", "native"})


@dataclass(frozen=True)
class SettingsSpec:
    """What one transport can do with settings.

    Args:
        name: Registry key, also the provider class name in kebab form
            (``claude_native`` is ``ClaudeNativeProvider``).
        agent: Canonical agent name (``claude_code``).
        transport: ``subprocess``, ``http``, ``pty``, or ``sdk``.
        supported: Settings fields this transport honours.
        mechanism: How values reach the CLI.
        notes: One sentence for the generated support table.
    """

    name: str
    agent: str
    transport: str
    supported: frozenset[str]
    mechanism: str
    notes: str = ""


SETTINGS_SPECS: dict[str, SettingsSpec] = {
    "claude_native": SettingsSpec(
        name="claude_native",
        agent="claude_code",
        transport="subprocess",
        supported=_ENDPOINT | _ESCAPE,
        mechanism="settings_json",
        notes="--settings with apiKeyHelper reading a key from the child env",
    ),
    "claude_tui": SettingsSpec(
        name="claude_tui",
        agent="claude_code",
        transport="pty",
        supported=_ENDPOINT | _ESCAPE,
        mechanism="settings_json",
        notes="same as the native transport, applied to the interactive process",
    ),
    "claude_sdk": SettingsSpec(
        name="claude_sdk",
        agent="claude_code",
        transport="sdk",
        supported=frozenset(),
        mechanism="sdk_options",
        notes="the Python SDK is not installed here, so nothing is claimed",
    ),
    "codex_native": SettingsSpec(
        name="codex_native",
        agent="codex",
        transport="subprocess",
        supported=_ENDPOINT | _ESCAPE | {"model_provider"},
        mechanism="config_overrides",
        notes="-c model_providers.<id>.* overrides, key referenced by env_key",
    ),
    "codex_sdk": SettingsSpec(
        name="codex_sdk",
        agent="codex",
        transport="sdk",
        supported=frozenset(),
        mechanism="sdk_options",
        notes="the Python SDK is not installed here, so nothing is claimed",
    ),
    "agy": SettingsSpec(
        name="agy",
        agent="agy",
        transport="subprocess",
        supported=_ENDPOINT | _ESCAPE,
        mechanism="settings_json",
        notes="GOOGLE_GEMINI_BASE_URL and GEMINI_API_KEY, plus --settings JSON",
    ),
    "gemini_native": SettingsSpec(
        name="gemini_native",
        agent="gemini_cli",
        transport="subprocess",
        supported=_ENDPOINT | frozenset({"env", "args"}),
        mechanism="env_map",
        notes="deprecated CLI; GOOGLE_GEMINI_BASE_URL and GEMINI_API_KEY only",
    ),
    "gemini_sdk": SettingsSpec(
        name="gemini_sdk",
        agent="gemini_cli",
        transport="sdk",
        supported=frozenset(),
        mechanism="sdk_options",
        notes="deprecated; the Python SDK is not installed here",
    ),
    "opencode_native": SettingsSpec(
        name="opencode_native",
        agent="opencode",
        transport="subprocess",
        supported=_ENDPOINT | _ESCAPE | {"model_provider", "headers"},
        mechanism="config_content",
        notes="inline provider block through OPENCODE_CONFIG_CONTENT",
    ),
    "opencode_v2": SettingsSpec(
        name="opencode_v2",
        agent="opencode",
        transport="http",
        supported=_ENDPOINT | _ESCAPE | {"model_provider", "headers"},
        mechanism="v1_delegation",
        notes="the V2 catalog ignores injected providers, so such tasks run on V1",
    ),
    "pi_native": SettingsSpec(
        name="pi_native",
        agent="pi",
        transport="subprocess",
        supported=_ENDPOINT | _ESCAPE | {"model_provider", "headers"},
        mechanism="models_json",
        notes="a temporary agent directory holds models.json for the run",
    ),
}


def spec_for(provider: str) -> SettingsSpec:
    """Return the spec for a provider name.

    Raises:
        KeyError: If the provider has no spec.
    """
    return SETTINGS_SPECS[provider]


__all__ = [
    "SETTINGS_SPECS",
    "SettingsField",
    "SettingsMechanism",
    "SettingsSpec",
    "spec_for",
]
