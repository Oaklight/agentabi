"""Tests for AntigravityNativeProvider command building and streaming."""

from typing import Any, cast

from agentabi.providers.antigravity_native import AntigravityNativeProvider
from agentabi.types.ir.task import TaskConfig


class TestBuildCommand:
    @staticmethod
    def _task(d: dict[str, Any]) -> TaskConfig:
        return cast(TaskConfig, d)

    def test_basic_command(self):
        task = self._task({"prompt": "Hello", "agent": "antigravity"})
        cmd = AntigravityNativeProvider._build_command(task)
        assert cmd[0] == "agy"
        assert "--dangerously-skip-permissions" in cmd
        assert "--print" in cmd
        assert "Hello" in cmd

    def test_print_is_last_with_prompt(self):
        task = self._task({"prompt": "Say hi", "agent": "antigravity"})
        cmd = AntigravityNativeProvider._build_command(task)
        idx = cmd.index("--print")
        assert cmd[idx + 1] == "Say hi"

    def test_with_timeout(self):
        task = self._task({"prompt": "Hi", "agent": "antigravity", "timeout": 120.0})
        cmd = AntigravityNativeProvider._build_command(task)
        assert "--print-timeout" in cmd
        idx = cmd.index("--print-timeout")
        assert cmd[idx + 1] == "120s"

    def test_with_resume_conversation(self):
        task = self._task(
            {
                "prompt": "Continue",
                "agent": "antigravity",
                "resume": True,
                "session_id": "conv-abc",
            }
        )
        cmd = AntigravityNativeProvider._build_command(task)
        assert "--conversation" in cmd
        idx = cmd.index("--conversation")
        assert cmd[idx + 1] == "conv-abc"

    def test_resume_latest(self):
        task = self._task(
            {"prompt": "Continue", "agent": "antigravity", "resume": True}
        )
        cmd = AntigravityNativeProvider._build_command(task)
        assert "--continue" in cmd
        assert "--conversation" not in cmd

    def test_full_auto_permissions(self):
        task = self._task(
            {
                "prompt": "Hi",
                "agent": "antigravity",
                "permissions": {"level": "full_auto"},
            }
        )
        cmd = AntigravityNativeProvider._build_command(task)
        assert "--dangerously-skip-permissions" in cmd

    def test_accept_edits_permissions(self):
        task = self._task(
            {
                "prompt": "Hi",
                "agent": "antigravity",
                "permissions": {"level": "accept_edits"},
            }
        )
        cmd = AntigravityNativeProvider._build_command(task)
        assert "--mode" in cmd
        idx = cmd.index("--mode")
        assert cmd[idx + 1] == "accept-edits"
        assert "--dangerously-skip-permissions" not in cmd

    def test_plan_mode_permissions(self):
        task = self._task(
            {
                "prompt": "Hi",
                "agent": "antigravity",
                "permissions": {"level": "plan"},
            }
        )
        cmd = AntigravityNativeProvider._build_command(task)
        assert "--mode" in cmd
        idx = cmd.index("--mode")
        assert cmd[idx + 1] == "plan"
        assert "--dangerously-skip-permissions" not in cmd

    def test_default_permissions(self):
        task = self._task(
            {
                "prompt": "Hi",
                "agent": "antigravity",
                "permissions": {"level": "default"},
            }
        )
        cmd = AntigravityNativeProvider._build_command(task)
        assert "--dangerously-skip-permissions" in cmd


class TestBuildEnv:
    @staticmethod
    def _task(d: dict[str, Any]) -> TaskConfig:
        return cast(TaskConfig, d)

    def test_openai_base_url_mapping(self):
        task = self._task(
            {
                "prompt": "Hi",
                "env": {"OPENAI_BASE_URL": "https://proxy.example.com/v1"},
            }
        )
        env = AntigravityNativeProvider._build_env(task)
        assert env["GOOGLE_GEMINI_BASE_URL"] == "https://proxy.example.com"

    def test_openai_api_key_mapping(self):
        task = self._task(
            {
                "prompt": "Hi",
                "env": {"OPENAI_API_KEY": "sk-test123"},
            }
        )
        env = AntigravityNativeProvider._build_env(task)
        assert env["GEMINI_API_KEY"] == "sk-test123"

    def test_gemini_env_takes_precedence(self):
        task = self._task(
            {
                "prompt": "Hi",
                "env": {
                    "GOOGLE_GEMINI_BASE_URL": "https://custom.example.com",
                    "OPENAI_BASE_URL": "https://should-be-ignored.com/v1",
                },
            }
        )
        env = AntigravityNativeProvider._build_env(task)
        assert env["GOOGLE_GEMINI_BASE_URL"] == "https://custom.example.com"


class TestCapabilities:
    def test_capabilities(self):
        caps = AntigravityNativeProvider().capabilities()
        assert caps["agent_type"] == "antigravity"
        assert caps["name"] == "Antigravity CLI"
        assert caps["supports_streaming"] is True
        assert caps["supports_mcp"] is True
        assert caps["transport"] == "subprocess"

    def test_system_prompt_not_supported(self):
        caps = AntigravityNativeProvider().capabilities()
        assert caps["supports_system_prompt"] is False
