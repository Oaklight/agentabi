"""Pi materialization and OpenCode V2 routing."""

from __future__ import annotations

import json
from contextlib import ExitStack
from pathlib import Path
from typing import cast

import pytest

from agentabi.settings import InvalidSettingsError, adapter_for
from agentabi.settings.adapters.opencode import has_endpoint_settings
from agentabi.settings.adapters.pi import AGENT_DIR_ENV
from agentabi.types.ir.task import TaskConfig

BASE = "https://rosetta.test/v1"


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """Point Pi's default agent directory at a temporary home."""
    home = tmp_path / "home"
    agent = home / ".pi" / "agent"
    agent.mkdir(parents=True)
    (agent / "settings.json").write_text('{"theme": "x"}')
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv(AGENT_DIR_ENV, raising=False)
    return home


class TestPiAdapter:
    def test_model_is_qualified(self) -> None:
        task: TaskConfig = {
            "prompt": "hi",
            "model": "qwen",
            "settings": {"base_url": BASE, "api_key": "k"},
        }
        resolved = adapter_for("pi_native").resolve(task)
        assert resolved.task_or(task)["model"] == "agentabi/qwen"

    def test_missing_key_is_rejected(self) -> None:
        task: TaskConfig = {"prompt": "hi", "settings": {"base_url": BASE}}
        with pytest.raises(InvalidSettingsError) as exc:
            adapter_for("pi_native").resolve(task)
        assert "settings.api_key" in str(exc.value)

    def test_other_fields_need_a_base_url(self) -> None:
        task: TaskConfig = {"prompt": "hi", "settings": {"headers": {"X": "1"}}}
        with pytest.raises(InvalidSettingsError) as exc:
            adapter_for("pi_native").resolve(task)
        assert "settings.base_url" in str(exc.value)

    def test_materialize_writes_private_agent_dir(self, isolated_home) -> None:
        task: TaskConfig = {
            "prompt": "hi",
            "model": "qwen",
            "settings": {
                "base_url": BASE,
                "api_key": "secret",
                "model_provider": "rosetta",
                "headers": {"X-Tenant": "a"},
            },
        }
        adapter = adapter_for("pi_native")
        resolved = adapter.resolve(task)
        with ExitStack() as stack:
            env = adapter.materialize(resolved, stack)
            directory = Path(env[AGENT_DIR_ENV])
            assert directory.stat().st_mode & 0o777 == 0o700
            models = json.loads((directory / "models.json").read_text())
            assert models["providers"]["rosetta"]["baseUrl"] == BASE
            assert models["providers"]["rosetta"]["apiKey"] == "secret"
            assert models["providers"]["rosetta"]["headers"] == {"X-Tenant": "a"}
            assert models["providers"]["rosetta"]["models"] == [{"id": "qwen"}]
            copied = (directory / "settings.json").read_text()
            assert json.loads(copied) == {"theme": "x"}
            assert (directory / "settings.json").stat().st_mode & 0o777 == 0o600
        assert not directory.exists()

    def test_materialize_is_a_noop_without_settings(self) -> None:
        adapter = adapter_for("pi_native")
        task: TaskConfig = {"prompt": "hi"}
        resolved = adapter.resolve(task)
        with ExitStack() as stack:
            assert adapter.materialize(resolved, stack) == {}


class TestOpenCodeV2Routing:
    def test_endpoint_fields_detected(self) -> None:
        assert has_endpoint_settings({"prompt": "hi", "settings": {"base_url": BASE}})
        assert not has_endpoint_settings(
            {"prompt": "hi", "settings": {"args": ["--x"]}}
        )
        assert not has_endpoint_settings({"prompt": "hi"})

    async def test_explicit_v2_rejects_endpoint_settings(self, monkeypatch) -> None:
        from agentabi.providers.opencode_v2 import OpenCodeV2Provider

        monkeypatch.setenv("AGENTABI_OPENCODE_TRANSPORT", "v2")
        provider = OpenCodeV2Provider()
        events = [
            event
            async for event in provider.stream(
                cast(TaskConfig, {"prompt": "hi", "settings": {"base_url": BASE}})
            )
        ]
        assert events and events[0]["type"] == "error"
        assert events[0]["is_fatal"] is True
        assert "V2" in events[0]["error"]

    async def test_auto_delegates_to_v1(self, monkeypatch) -> None:
        from agentabi.providers.opencode_native import OpenCodeNativeProvider
        from agentabi.providers.opencode_v2 import OpenCodeV2Provider

        async def fake_stream(self, task):
            yield {"type": "session_start", "session_id": "s", "agent": "opencode"}
            yield {"type": "session_end"}

        monkeypatch.setenv("AGENTABI_OPENCODE_TRANSPORT", "auto")
        monkeypatch.setattr(OpenCodeNativeProvider, "stream", fake_stream)
        provider = OpenCodeV2Provider()
        events = [
            event
            async for event in provider.stream(
                cast(TaskConfig, {"prompt": "hi", "settings": {"base_url": BASE}})
            )
        ]
        assert [event["type"] for event in events] == ["session_start", "session_end"]

    async def test_invalid_settings_still_raise(self, monkeypatch) -> None:
        from agentabi.providers.opencode_v2 import OpenCodeV2Provider

        monkeypatch.setenv("AGENTABI_OPENCODE_TRANSPORT", "v2")
        provider = OpenCodeV2Provider()
        with pytest.raises(InvalidSettingsError):
            async for _ in provider.stream(
                {"prompt": "hi", "settings": {"base_url": "not-a-url"}}
            ):
                pass
