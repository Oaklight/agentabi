"""Unified settings injection for agent CLIs.

Callers set ``TaskConfig.settings``; each transport translates it into its
own native mechanism through an adapter.  Per-CLI differences live in
:mod:`agentabi.settings.specs`, not in the providers.
"""

from __future__ import annotations

from .base import Adapter, AppliedSettings, ResolvedSettings, SettingsAdapter
from .errors import InvalidSettingsError, SettingsError, UnsupportedSettingsError
from .registry import adapter_for, supported_settings
from .resolve import merge_settings, validate_settings
from .specs import SETTINGS_SPECS, SettingsField, SettingsSpec, spec_for

__all__ = [
    "SETTINGS_SPECS",
    "Adapter",
    "AppliedSettings",
    "InvalidSettingsError",
    "ResolvedSettings",
    "SettingsAdapter",
    "SettingsError",
    "SettingsField",
    "SettingsSpec",
    "UnsupportedSettingsError",
    "adapter_for",
    "merge_settings",
    "spec_for",
    "supported_settings",
    "validate_settings",
]
