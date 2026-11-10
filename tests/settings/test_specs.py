"""Contract tests for the unified settings spec table.

The table is the single source of truth for per-CLI differences.  These
tests keep it in sync with the provider registry and with the adapters.
"""

from __future__ import annotations

import re

import pytest

from agentabi.providers.registry import list_agents
from agentabi.settings import SETTINGS_SPECS, SettingsAdapter, adapter_for
from agentabi.settings.registry import supported_settings

FIELDS = {
    "base_url",
    "api_key",
    "api_key_env",
    "model_provider",
    "headers",
    "env",
    "args",
    "native",
}

TRANSPORTS = {"subprocess", "http", "pty", "sdk"}
MECHANISMS = {
    "settings_json",
    "config_overrides",
    "env_map",
    "config_content",
    "models_json",
    "v1_delegation",
    "sdk_options",
    "none",
}


def _normalize(name: str) -> str:
    """Compare provider and spec names ignoring case and separators."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _provider_keys() -> dict[str, set[str]]:
    """Return each provider class's acceptable adapter keys.

    A trailing ``native`` is optional so ``AgyNativeProvider`` matches the
    ``agy`` spec key as well as its own normalized name.
    """
    from agentabi.providers.registry import _build_provider_chain

    keys: dict[str, set[str]] = {}
    for chain in _build_provider_chain().values():
        for provider_cls in chain:
            raw = provider_cls.__name__.removesuffix("Provider")
            normalized = _normalize(raw)
            candidates = {normalized}
            if normalized.endswith("native"):
                candidates.add(normalized[: -len("native")])
            keys[provider_cls.__name__] = candidates
    return keys


class TestSpecTable:
    def test_every_provider_has_a_spec(self) -> None:
        spec_keys = {_normalize(name) for name in SETTINGS_SPECS}
        for provider_name, candidates in _provider_keys().items():
            assert candidates & spec_keys, provider_name

    def test_every_spec_maps_to_a_registered_provider(self) -> None:
        spec_keys = {_normalize(name) for name in SETTINGS_SPECS}
        provider_keys = set().union(*_provider_keys().values())
        assert spec_keys <= provider_keys

    def test_spec_name_matches_its_key(self) -> None:
        for key, spec in SETTINGS_SPECS.items():
            assert spec.name == key

    def test_spec_agent_is_a_registered_agent(self) -> None:
        agents = set(list_agents())
        for spec in SETTINGS_SPECS.values():
            assert spec.agent in agents, spec.name

    def test_supported_fields_are_known(self) -> None:
        for spec in SETTINGS_SPECS.values():
            assert spec.supported <= FIELDS, spec.name
            assert spec.transport in TRANSPORTS, spec.name
            assert spec.mechanism in MECHANISMS, spec.name

    def test_sdk_transports_claim_nothing(self) -> None:
        for spec in SETTINGS_SPECS.values():
            if spec.transport == "sdk":
                assert spec.supported == frozenset(), spec.name

    def test_active_transports_claim_at_least_endpoint(self) -> None:
        wired = {
            "settings_json",
            "config_overrides",
            "env_map",
            "config_content",
        }
        for spec in SETTINGS_SPECS.values():
            if spec.transport in {"subprocess", "pty", "http"} and (
                spec.mechanism in wired
            ):
                assert {"base_url", "api_key", "api_key_env"} <= spec.supported


class TestAdapterRegistry:
    @pytest.mark.parametrize("name", sorted(SETTINGS_SPECS))
    def test_adapter_matches_its_spec(self, name: str) -> None:
        adapter = adapter_for(name)
        assert isinstance(adapter, SettingsAdapter)
        assert adapter.name == name
        assert adapter.spec is SETTINGS_SPECS[name]

    @pytest.mark.parametrize("name", sorted(SETTINGS_SPECS))
    def test_empty_task_resolves_to_nothing(self, name: str) -> None:
        resolved = adapter_for(name).resolve({"prompt": ""})
        assert resolved.argv == ()
        assert dict(resolved.env) == {}
        assert resolved.task is None

    @pytest.mark.parametrize("name", sorted(SETTINGS_SPECS))
    def test_supported_settings_are_sorted(self, name: str) -> None:
        assert supported_settings(name) == sorted(supported_settings(name))

    def test_unknown_provider_raises_key_error(self) -> None:
        with pytest.raises(KeyError):
            adapter_for("no_such_provider")
