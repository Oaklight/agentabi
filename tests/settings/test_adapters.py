"""Adapter behaviour for canonical settings."""

from __future__ import annotations

import json
from typing import cast

import pytest

from agentabi.settings import (
    InvalidSettingsError,
    UnsupportedSettingsError,
    adapter_for,
)
from agentabi.settings.adapters.codex import toml_literal
from agentabi.types.ir.task import TaskConfig

SECRET = "sk-live-very-secret"
BASE = "https://rosetta.test/v1"


def _task(**settings) -> TaskConfig:
    return cast(TaskConfig, {"prompt": "hi", "settings": settings})


class TestClaude:
    def test_canonical_payload_keeps_key_out_of_argv(self) -> None:
        resolved = adapter_for("claude_native").resolve(
            _task(base_url=BASE, api_key=SECRET)
        )
        assert resolved.argv[0] == "--settings"
        payload = json.loads(resolved.argv[1])
        assert payload["apiKeyHelper"] == "printenv AGENTABI_CLAUDE_API_KEY"
        assert payload["env"]["ANTHROPIC_BASE_URL"] == BASE
        assert SECRET not in resolved.argv[1]
        assert resolved.env["AGENTABI_CLAUDE_API_KEY"] == SECRET

    def test_native_merges_into_settings_json(self) -> None:
        resolved = adapter_for("claude_native").resolve(
            _task(base_url=BASE, api_key=SECRET, native={"model": "opus"})
        )
        payload = json.loads(resolved.argv[1])
        assert payload["model"] == "opus"

    def test_api_key_env_is_resolved(self, monkeypatch) -> None:
        monkeypatch.setenv("AGENTABI_TEST_KEY", SECRET)
        resolved = adapter_for("claude_native").resolve(
            _task(base_url=BASE, api_key_env="AGENTABI_TEST_KEY")
        )
        assert resolved.env["AGENTABI_CLAUDE_API_KEY"] == SECRET

    def test_missing_api_key_env_names_the_variable_only(self) -> None:
        with pytest.raises(InvalidSettingsError) as exc:
            adapter_for("claude_native").resolve(
                _task(base_url=BASE, api_key_env="AGENTABI_UNSET_KEY")
            )
        assert "AGENTABI_UNSET_KEY" in str(exc.value)
        assert SECRET not in str(exc.value)

    def test_settings_beat_task_env(self) -> None:
        task: TaskConfig = {
            "prompt": "hi",
            "env": {"ANTHROPIC_BASE_URL": "https://old.test"},
            "settings": {"base_url": BASE, "api_key": SECRET},
        }
        resolved = adapter_for("claude_native").resolve(task)
        assert resolved.env["ANTHROPIC_BASE_URL"] == BASE

    def test_legacy_env_keeps_the_old_payload(self) -> None:
        task: TaskConfig = {
            "prompt": "hi",
            "env": {"ANTHROPIC_BASE_URL": BASE, "ANTHROPIC_API_KEY": SECRET},
        }
        resolved = adapter_for("claude_native").resolve(task)
        payload = json.loads(resolved.argv[1])
        assert payload["apiKeyHelper"].startswith("echo ")
        assert resolved.env == {}

    def test_args_are_returned_for_the_prompt_boundary(self) -> None:
        resolved = adapter_for("claude_native").resolve(
            _task(base_url=BASE, api_key=SECRET, args=["--flag"])
        )
        assert resolved.extra_args == ("--flag",)

    def test_redacted_view_hides_the_payload(self) -> None:
        resolved = adapter_for("claude_native").resolve(
            _task(base_url=BASE, api_key=SECRET)
        )
        assert SECRET not in json.dumps(resolved.redacted())


class TestCodex:
    def test_provider_block_from_settings(self) -> None:
        resolved = adapter_for("codex_native").resolve(
            _task(
                base_url=BASE,
                api_key=SECRET,
                model_provider="rosetta",
            )
        )
        argv = list(resolved.argv)
        assert "model_provider=rosetta" in argv
        assert f"model_providers.rosetta.base_url={BASE}" in argv
        assert 'model_providers.rosetta.env_key="AGENTABI_CODEX_API_KEY"' in argv
        assert SECRET not in json.dumps(argv)
        assert resolved.env["AGENTABI_CODEX_API_KEY"] == SECRET
        assert resolved.env["OPENAI_BASE_URL"] == BASE
        assert resolved.env["CODEX_PROVIDER"] == "rosetta"

    def test_headers_become_an_inline_table(self) -> None:
        resolved = adapter_for("codex_native").resolve(
            _task(base_url=BASE, api_key=SECRET, headers={"X-Tenant": "a"})
        )
        assert (
            'model_providers.agentabi.http_headers={"X-Tenant" = "a"}' in resolved.argv
        )

    def test_native_keys_become_config_overrides(self) -> None:
        resolved = adapter_for("codex_native").resolve(
            _task(base_url=BASE, native={"sandbox_mode": "read-only"})
        )
        assert 'sandbox_mode="read-only"' in resolved.argv

    def test_resume_uses_the_same_overrides(self) -> None:
        resolved = adapter_for("codex_native").resolve(
            _task(base_url=BASE, api_key=SECRET), resume=True
        )
        assert any(
            part.startswith("model_providers.agentabi.base_url=")
            for part in resolved.argv
        )

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("x", '"x"'),
            (True, "true"),
            (False, "false"),
            (3, "3"),
            (["a", 1], '["a", 1]'),
            ({"k": "v"}, '{"k" = "v"}'),
        ],
    )
    def test_toml_literal(self, value, expected: str) -> None:
        assert toml_literal(value) == expected


class TestAgy:
    def test_endpoint_is_bridged_and_stripped(self) -> None:
        resolved = adapter_for("agy").resolve(_task(base_url=BASE, api_key=SECRET))
        assert resolved.env["GOOGLE_GEMINI_BASE_URL"] == "https://rosetta.test"
        assert resolved.env["GEMINI_API_KEY"] == SECRET
        assert resolved.env["OPENAI_BASE_URL"] == BASE

    def test_native_names_the_model_and_drops_the_flag(self) -> None:
        task: TaskConfig = {
            "prompt": "hi",
            "model": "qwen3.8-27b",
            "settings": {"base_url": BASE, "native": {"model": "qwen3.8-27b"}},
        }
        resolved = adapter_for("agy").resolve(task)
        assert resolved.argv[0] == "--settings"
        assert "model" in json.loads(resolved.argv[1])
        assert resolved.task_or(task).get("model") is None

    def test_native_merges_with_the_extension_block(self) -> None:
        task: TaskConfig = {
            "prompt": "hi",
            "agent_extensions": {"agy": {"settings": {"model": "a"}}},
            "settings": {"native": {"temperature": 0.2}},
        }
        resolved = adapter_for("agy").resolve(task)
        merged = json.loads(resolved.argv[1])
        assert merged == {"model": "a", "temperature": 0.2}

    def test_legacy_settings_file_path_is_untouched(self) -> None:
        task: TaskConfig = {
            "prompt": "hi",
            "agent_extensions": {"agy": {"settings": "/tmp/agy.json"}},
        }
        resolved = adapter_for("agy").resolve(task)
        assert resolved.argv == ("--settings", "/tmp/agy.json")


class TestGemini:
    def test_endpoint_bridge_and_args(self) -> None:
        resolved = adapter_for("gemini_native").resolve(
            _task(base_url=BASE, api_key=SECRET, args=["--x"])
        )
        assert resolved.env["GOOGLE_GEMINI_BASE_URL"] == "https://rosetta.test"
        assert resolved.env["GEMINI_API_KEY"] == SECRET
        assert resolved.extra_args == ("--x",)

    def test_native_is_unsupported(self) -> None:
        with pytest.raises(UnsupportedSettingsError) as exc:
            adapter_for("gemini_native").resolve(_task(native={"a": 1}))
        assert exc.value.field == "native"
        assert exc.value.provider == "gemini_native"

    def test_ignore_drops_and_records(self) -> None:
        resolved = adapter_for("gemini_native").resolve(
            _task(native={"a": 1}, on_unsupported="ignore")
        )
        assert resolved.ignored == ("native",)

    def test_sdk_claims_nothing(self) -> None:
        with pytest.raises(UnsupportedSettingsError):
            adapter_for("gemini_sdk").resolve(_task(base_url=BASE))


class TestOpenCode:
    def test_config_content_and_model_rewrite(self) -> None:
        resolved = adapter_for("opencode_native").resolve(
            {
                "prompt": "hi",
                "model": "qwen3.8-27b",
                "settings": {
                    "base_url": BASE,
                    "api_key": SECRET,
                    "model_provider": "rosetta",
                    "headers": {"X-Tenant": "a"},
                },
            }
        )
        rewritten = resolved.task_or({"model": "qwen3.8-27b"})
        assert rewritten["model"] == "rosetta/qwen3.8-27b"
        config = json.loads(resolved.env["OPENCODE_CONFIG_CONTENT"])
        provider = config["provider"]["rosetta"]
        assert provider["npm"] == "@ai-sdk/openai-compatible"
        assert provider["options"]["baseURL"] == BASE
        assert provider["options"]["apiKey"] == SECRET
        assert provider["options"]["headers"] == {"X-Tenant": "a"}
        assert provider["models"] == {"qwen3.8-27b": {"name": "qwen3.8-27b"}}

    def test_existing_config_is_merged(self) -> None:
        task: TaskConfig = {
            "prompt": "hi",
            "env": {"OPENCODE_CONFIG_CONTENT": json.dumps({"theme": "dark"})},
            "settings": {"base_url": BASE},
        }
        resolved = adapter_for("opencode_native").resolve(task)
        config = json.loads(resolved.env["OPENCODE_CONFIG_CONTENT"])
        assert config["theme"] == "dark"
        assert "agentabi" in config["provider"]

    def test_invalid_existing_config_is_reported(self) -> None:
        task: TaskConfig = {
            "prompt": "hi",
            "env": {"OPENCODE_CONFIG_CONTENT": "{not json"},
            "settings": {"base_url": BASE},
        }
        with pytest.raises(InvalidSettingsError) as exc:
            adapter_for("opencode_native").resolve(task)
        assert "OPENCODE_CONFIG_CONTENT" in str(exc.value)

    def test_legacy_model_prefix_still_applies(self) -> None:
        task: TaskConfig = {
            "prompt": "hi",
            "model": "qwen",
            "env": {"OPENAI_BASE_URL": BASE},
        }
        resolved = adapter_for("opencode_native").resolve(task)
        assert resolved.task_or(task)["model"] == "openai/qwen"
        assert resolved.env == {}
