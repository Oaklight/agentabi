"""Validation tests for AgentSettings."""

from __future__ import annotations

import pytest

from agentabi.settings import InvalidSettingsError, merge_settings, validate_settings

SECRET = "sk-live-very-secret"


class TestValidate:
    def test_accepts_a_complete_mapping(self) -> None:
        validate_settings(
            {
                "base_url": "https://example.test/v1",
                "api_key_env": "MY_KEY",
                "model_provider": "rosetta",
                "headers": {"X-Tenant": "a"},
                "env": {"A": "1"},
                "args": ["--flag"],
                "native": {"model": "m"},
                "on_unsupported": "ignore",
            }
        )

    def test_unknown_key_names_itself(self) -> None:
        with pytest.raises(InvalidSettingsError) as exc:
            validate_settings({"base_uri": "https://x.test"})
        assert "base_uri" in str(exc.value)

    def test_non_mapping(self) -> None:
        with pytest.raises(InvalidSettingsError):
            validate_settings("https://x.test")  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        "url",
        [
            "example.test/v1",  # no scheme
            "ftp://example.test",  # wrong scheme
            "https://",  # no host
            "https://user:pass@example.test",  # userinfo
            "",
        ],
    )
    def test_bad_base_url(self, url: str) -> None:
        with pytest.raises(InvalidSettingsError) as exc:
            validate_settings({"base_url": url})
        assert "settings.base_url" in str(exc.value)

    def test_api_key_and_env_conflict(self) -> None:
        with pytest.raises(InvalidSettingsError):
            validate_settings({"api_key": "a", "api_key_env": "B"})

    def test_api_key_env_must_be_a_name(self) -> None:
        with pytest.raises(InvalidSettingsError):
            validate_settings({"api_key_env": "not a name"})

    def test_header_with_newline(self) -> None:
        with pytest.raises(InvalidSettingsError):
            validate_settings({"headers": {"X-A": "a\r\nX-B: b"}})

    def test_args_must_be_strings(self) -> None:
        with pytest.raises(InvalidSettingsError):
            validate_settings({"args": ["--ok", 1]})

    def test_native_must_be_small(self) -> None:
        with pytest.raises(InvalidSettingsError):
            validate_settings({"native": {"blob": "x" * (101 * 1024)}})

    def test_on_unsupported_values(self) -> None:
        with pytest.raises(InvalidSettingsError):
            validate_settings({"on_unsupported": "skip"})

    def test_error_never_repeats_a_secret(self) -> None:
        with pytest.raises(InvalidSettingsError) as exc:
            validate_settings(
                {"api_key": SECRET, "api_key_env": "HOME", "base_url": "nope"}
            )
        assert SECRET not in str(exc.value)
        assert SECRET not in repr(exc.value)


class TestMerge:
    def test_scalar_override_wins(self) -> None:
        merged = merge_settings(
            {"base_url": "https://a.test", "model_provider": "p1"},
            {"base_url": "https://b.test"},
        )
        assert merged["base_url"] == "https://b.test"
        assert merged["model_provider"] == "p1"

    def test_nested_mappings_merge_by_key(self) -> None:
        merged = merge_settings(
            {"headers": {"A": "1"}, "env": {"X": "1"}, "native": {"m": 1}},
            {"headers": {"B": "2"}, "env": {"Y": "2"}, "native": {"n": 2}},
        )
        assert merged["headers"] == {"A": "1", "B": "2"}
        assert merged["env"] == {"X": "1", "Y": "2"}
        assert merged["native"] == {"m": 1, "n": 2}

    def test_inputs_are_not_modified(self) -> None:
        base = {"env": {"A": "1"}}
        override = {"env": {"B": "2"}}
        merge_settings(base, override)
        assert base == {"env": {"A": "1"}}
        assert override == {"env": {"B": "2"}}

    def test_empty_inputs(self) -> None:
        assert merge_settings(None, None) == {}
