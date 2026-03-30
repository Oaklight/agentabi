"""Tests for GeminiNativeProvider event parsing and command building."""

from typing import Any, cast

from agentabi.providers.gemini_native import GeminiNativeProvider
from agentabi.types.ir.task import TaskConfig


class TestBuildCommand:
    @staticmethod
    def _task(d: dict[str, Any]) -> TaskConfig:
        return cast(TaskConfig, d)

    def test_basic_command(self):
        task = self._task({"prompt": "Hello", "agent": "gemini_cli"})
        cmd = GeminiNativeProvider._build_command(task)
        assert cmd == [
            "gemini",
            "-o",
            "stream-json",
            "-y",
            "-p",
            "Hello",
        ]

    def test_with_model(self):
        task = self._task(
            {"prompt": "Hi", "agent": "gemini_cli", "model": "argo:gpt-4o"}
        )
        cmd = GeminiNativeProvider._build_command(task)
        assert "-m" in cmd
        assert "argo:gpt-4o" in cmd

    def test_with_resume_session(self):
        task = self._task(
            {
                "prompt": "Continue",
                "agent": "gemini_cli",
                "resume": True,
                "session_id": "abc-123",
            }
        )
        cmd = GeminiNativeProvider._build_command(task)
        assert "-r" in cmd
        assert "abc-123" in cmd

    def test_resume_latest(self):
        task = self._task({"prompt": "Continue", "agent": "gemini_cli", "resume": True})
        cmd = GeminiNativeProvider._build_command(task)
        assert "-r" in cmd
        assert "latest" in cmd

    def test_prompt_in_command(self):
        task = self._task({"prompt": "What is 2+2?", "agent": "gemini_cli"})
        cmd = GeminiNativeProvider._build_command(task)
        assert "-p" in cmd
        p_idx = cmd.index("-p")
        assert cmd[p_idx + 1] == "What is 2+2?"


class TestParseEvent:
    def test_init_event(self):
        raw = {
            "type": "init",
            "timestamp": "2026-03-30T00:00:00Z",
            "session_id": "sess-abc",
            "model": "argo:gpt-4o",
        }
        events = GeminiNativeProvider._parse_event(raw)
        assert len(events) == 1
        assert events[0]["type"] == "session_start"
        assert events[0]["session_id"] == "sess-abc"
        assert events[0]["agent"] == "gemini_cli"
        assert events[0]["model"] == "argo:gpt-4o"

    def test_init_without_model(self):
        raw = {"type": "init", "session_id": "sess-abc"}
        events = GeminiNativeProvider._parse_event(raw)
        assert len(events) == 1
        assert "model" not in events[0]

    def test_user_message_ignored(self):
        raw = {
            "type": "message",
            "role": "user",
            "content": "What is 2+2?",
        }
        events = GeminiNativeProvider._parse_event(raw)
        assert events == []

    def test_assistant_delta_message(self):
        raw = {
            "type": "message",
            "role": "assistant",
            "content": "The answer",
            "delta": True,
        }
        events = GeminiNativeProvider._parse_event(raw)
        assert len(events) == 1
        assert events[0]["type"] == "message_delta"
        assert events[0]["text"] == "The answer"

    def test_assistant_delta_empty(self):
        raw = {
            "type": "message",
            "role": "assistant",
            "content": "",
            "delta": True,
        }
        events = GeminiNativeProvider._parse_event(raw)
        assert events == []

    def test_assistant_full_message(self):
        raw = {
            "type": "message",
            "role": "assistant",
            "content": "Full response text",
        }
        events = GeminiNativeProvider._parse_event(raw)
        assert len(events) == 2
        assert events[0]["type"] == "message_start"
        assert events[0]["role"] == "assistant"
        assert events[1]["type"] == "message_end"
        assert events[1]["text"] == "Full response text"

    def test_tool_use_event(self):
        raw = {
            "type": "tool_use",
            "tool_name": "list_directory",
            "tool_id": "call_123",
            "parameters": {"dir_path": "."},
        }
        events = GeminiNativeProvider._parse_event(raw)
        assert len(events) == 1
        assert events[0]["type"] == "tool_use"
        assert events[0]["tool_use_id"] == "call_123"
        assert events[0]["tool_name"] == "list_directory"
        assert events[0]["tool_input"] == {"dir_path": "."}

    def test_tool_result_success(self):
        raw = {
            "type": "tool_result",
            "tool_id": "call_123",
            "status": "success",
            "output": "Listed 14 items",
        }
        events = GeminiNativeProvider._parse_event(raw)
        assert len(events) == 1
        assert events[0]["type"] == "tool_result"
        assert events[0]["tool_use_id"] == "call_123"
        assert events[0]["content"] == "Listed 14 items"
        assert "is_error" not in events[0]

    def test_tool_result_error(self):
        raw = {
            "type": "tool_result",
            "tool_id": "call_err",
            "status": "error",
            "output": "raw error output",
            "error": {
                "type": "invalid_tool_params",
                "message": "missing dir_path",
            },
        }
        events = GeminiNativeProvider._parse_event(raw)
        assert len(events) == 1
        assert events[0]["type"] == "tool_result"
        assert events[0]["is_error"] is True
        assert events[0]["content"] == "missing dir_path"

    def test_result_event(self):
        raw = {
            "type": "result",
            "status": "success",
            "stats": {
                "total_tokens": 6384,
                "input_tokens": 6373,
                "output_tokens": 11,
                "cached": 0,
                "duration_ms": 1581,
                "tool_calls": 0,
            },
        }
        events = GeminiNativeProvider._parse_event(raw)
        assert len(events) == 3  # usage + message_end + session_end
        assert events[0]["type"] == "usage"
        assert events[0]["usage"]["input_tokens"] == 6373
        assert events[0]["usage"]["output_tokens"] == 11
        assert events[0]["usage"]["total_tokens"] == 6384
        assert events[1]["type"] == "message_end"
        assert events[1]["stop_reason"] == "end_turn"
        assert events[2]["type"] == "session_end"

    def test_result_error_status(self):
        raw = {
            "type": "result",
            "status": "error",
            "error": "API connection failed",
            "stats": {},
        }
        events = GeminiNativeProvider._parse_event(raw)
        types = [e["type"] for e in events]
        assert "error" in types
        err = [e for e in events if e["type"] == "error"][0]
        assert err["error"] == "API connection failed"
        assert err["is_fatal"] is True

    def test_unknown_event_type(self):
        raw = {"type": "unknown_thing", "data": "whatever"}
        events = GeminiNativeProvider._parse_event(raw)
        assert events == []


class TestCapabilities:
    def test_capabilities(self):
        caps = GeminiNativeProvider().capabilities()
        assert caps["agent_type"] == "gemini_cli"
        assert caps["supports_streaming"] is True
        assert caps["supports_mcp"] is True
        assert caps["transport"] == "subprocess"
