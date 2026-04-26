"""Tests for OpenCodeNativeProvider event parsing and command building."""

from typing import Any, cast

from agentabi.providers.opencode_native import OpenCodeNativeProvider
from agentabi.types.ir.task import TaskConfig


class TestBuildCommand:
    @staticmethod
    def _task(d: dict[str, Any]) -> TaskConfig:
        return cast(TaskConfig, d)

    def test_basic_command(self):
        task = self._task({"prompt": "Hello", "agent": "opencode"})
        cmd = OpenCodeNativeProvider._build_command(task)
        assert cmd == ["opencode", "run", "--format", "json", "--", "Hello"]

    def test_with_model(self):
        task = self._task(
            {
                "prompt": "Hi",
                "agent": "opencode",
                "model": "anthropic/claude-sonnet-4-20250514",
            }
        )
        cmd = OpenCodeNativeProvider._build_command(task)
        assert "--model" in cmd
        assert "anthropic/claude-sonnet-4-20250514" in cmd

    def test_system_prompt_ignored(self):
        task = self._task(
            {"prompt": "Hi", "agent": "opencode", "system_prompt": "Be concise"}
        )
        cmd = OpenCodeNativeProvider._build_command(task)
        assert "--prompt" not in cmd
        assert "Be concise" not in cmd

    def test_full_auto_permissions(self):
        task = self._task(
            {"prompt": "Hi", "agent": "opencode", "permissions": {"level": "full_auto"}}
        )
        cmd = OpenCodeNativeProvider._build_command(task)
        assert "--dangerously-skip-permissions" in cmd

    def test_no_permissions_flag_by_default(self):
        task = self._task({"prompt": "Hi", "agent": "opencode"})
        cmd = OpenCodeNativeProvider._build_command(task)
        assert "--dangerously-skip-permissions" not in cmd

    def test_with_working_dir(self):
        task = self._task(
            {"prompt": "Hi", "agent": "opencode", "working_dir": "/tmp/test"}
        )
        cmd = OpenCodeNativeProvider._build_command(task)
        assert "--dir" in cmd
        assert "/tmp/test" in cmd

    def test_with_resume_session(self):
        task = self._task(
            {
                "prompt": "Continue",
                "agent": "opencode",
                "resume": True,
                "session_id": "ses_abc123",
            }
        )
        cmd = OpenCodeNativeProvider._build_command(task)
        assert "--session" in cmd
        assert "ses_abc123" in cmd

    def test_prompt_after_separator(self):
        task = self._task({"prompt": "--dangerous-flag", "agent": "opencode"})
        cmd = OpenCodeNativeProvider._build_command(task)
        separator_idx = cmd.index("--")
        assert cmd[separator_idx + 1] == "--dangerous-flag"


class TestParseEvent:
    def test_step_start(self):
        raw = {
            "type": "step_start",
            "timestamp": 1000,
            "sessionID": "ses_abc",
            "part": {
                "id": "prt_1",
                "messageID": "msg_1",
                "sessionID": "ses_abc",
                "type": "step-start",
            },
        }
        events = OpenCodeNativeProvider._parse_event(raw)
        assert len(events) == 2
        assert events[0]["type"] == "session_start"
        assert events[0]["session_id"] == "ses_abc"
        assert events[0]["agent"] == "opencode"
        assert events[1]["type"] == "message_start"
        assert events[1]["role"] == "assistant"
        assert events[1]["message_id"] == "msg_1"

    def test_text_event(self):
        raw = {
            "type": "text",
            "sessionID": "ses_abc",
            "part": {
                "type": "text",
                "text": "The answer is 4",
            },
        }
        events = OpenCodeNativeProvider._parse_event(raw)
        assert len(events) == 1
        assert events[0]["type"] == "message_delta"
        assert events[0]["text"] == "The answer is 4"

    def test_text_event_empty(self):
        raw = {
            "type": "text",
            "sessionID": "ses_abc",
            "part": {"type": "text", "text": ""},
        }
        events = OpenCodeNativeProvider._parse_event(raw)
        assert len(events) == 0

    def test_tool_use_completed(self):
        raw = {
            "type": "tool_use",
            "sessionID": "ses_abc",
            "part": {
                "type": "tool",
                "tool": "think-think",
                "callID": "call_123",
                "state": {
                    "status": "completed",
                    "input": {"thinking_mode": "reasoning"},
                    "output": "Done thinking",
                    "time": {"start": 1000, "end": 1500},
                },
            },
        }
        events = OpenCodeNativeProvider._parse_event(raw)
        assert len(events) == 2
        assert events[0]["type"] == "tool_use"
        assert events[0]["tool_use_id"] == "call_123"
        assert events[0]["tool_name"] == "think-think"
        assert events[0]["tool_input"] == {"thinking_mode": "reasoning"}
        assert events[1]["type"] == "tool_result"
        assert events[1]["tool_use_id"] == "call_123"
        assert events[1]["content"] == "Done thinking"
        assert events[1]["duration_ms"] == 500

    def test_tool_use_error(self):
        raw = {
            "type": "tool_use",
            "sessionID": "ses_abc",
            "part": {
                "tool": "bash",
                "callID": "call_err",
                "state": {
                    "status": "error",
                    "input": {"command": "rm -rf /"},
                    "output": "Permission denied",
                },
            },
        }
        events = OpenCodeNativeProvider._parse_event(raw)
        assert len(events) == 2
        assert events[0]["type"] == "tool_use"
        assert events[1]["type"] == "tool_result"
        assert events[1]["is_error"] is True
        assert events[1]["content"] == "Permission denied"

    def test_step_finish_with_usage(self):
        raw = {
            "type": "step_finish",
            "sessionID": "ses_abc",
            "part": {
                "reason": "tool-calls",
                "messageID": "msg_2",
                "tokens": {
                    "total": 150,
                    "input": 100,
                    "output": 50,
                    "reasoning": 0,
                    "cache": {"write": 10, "read": 20},
                },
                "cost": 0.005,
            },
        }
        events = OpenCodeNativeProvider._parse_event(raw)
        assert len(events) == 2  # usage + message_end
        assert events[0]["type"] == "usage"
        assert events[0]["usage"]["input_tokens"] == 100
        assert events[0]["usage"]["output_tokens"] == 50
        assert events[0]["usage"]["total_tokens"] == 150
        assert events[0]["usage"]["cache_read_tokens"] == 20
        assert events[0]["usage"]["cache_creation_tokens"] == 10
        assert events[0]["cost_usd"] == 0.005
        assert events[1]["type"] == "message_end"
        assert events[1]["stop_reason"] == "tool-calls"

    def test_step_finish_stop_emits_session_end(self):
        raw = {
            "type": "step_finish",
            "sessionID": "ses_abc",
            "part": {
                "reason": "stop",
                "tokens": {"total": 10, "input": 0, "output": 10},
                "cost": 0,
            },
        }
        events = OpenCodeNativeProvider._parse_event(raw)
        assert len(events) == 3  # usage + message_end + session_end
        assert events[2]["type"] == "session_end"
        assert events[2]["session_id"] == "ses_abc"

    def test_step_finish_tool_calls_no_session_end(self):
        raw = {
            "type": "step_finish",
            "sessionID": "ses_abc",
            "part": {
                "reason": "tool-calls",
                "tokens": {},
            },
        }
        events = OpenCodeNativeProvider._parse_event(raw)
        types = [e["type"] for e in events]
        assert "session_end" not in types

    def test_unknown_event_type(self):
        raw = {"type": "unknown_event", "data": "whatever"}
        events = OpenCodeNativeProvider._parse_event(raw)
        assert events == []


class TestCapabilities:
    def test_capabilities(self):
        caps = OpenCodeNativeProvider().capabilities()
        assert caps["agent_type"] == "opencode"
        assert caps["supports_streaming"] is True
        assert caps["supports_mcp"] is True
        assert caps["transport"] == "subprocess"
