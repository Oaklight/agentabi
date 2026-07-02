"""Tests for PiNativeProvider event parsing and command building."""

from typing import Any, cast

from agentabi.providers.pi_native import PiNativeProvider
from agentabi.types.ir.task import TaskConfig


class TestBuildCommand:
    @staticmethod
    def _task(d: dict[str, Any]) -> TaskConfig:
        return cast(TaskConfig, d)

    def test_basic_command(self):
        task = self._task({"prompt": "Hello", "agent": "pi"})
        cmd = PiNativeProvider._build_command(task)
        assert cmd == ["pi", "--print", "--mode", "json", "Hello"]

    def test_with_model(self):
        task = self._task(
            {"prompt": "Hi", "agent": "pi", "model": "anthropic/claude-sonnet-4"}
        )
        cmd = PiNativeProvider._build_command(task)
        assert "--model" in cmd
        assert "anthropic/claude-sonnet-4" in cmd

    def test_with_system_prompt(self):
        task = self._task(
            {"prompt": "Hi", "agent": "pi", "system_prompt": "Be concise"}
        )
        cmd = PiNativeProvider._build_command(task)
        assert "--system-prompt" in cmd
        assert "Be concise" in cmd

    def test_with_append_system_prompt(self):
        task = self._task(
            {
                "prompt": "Hi",
                "agent": "pi",
                "append_system_prompt": "Extra context",
            }
        )
        cmd = PiNativeProvider._build_command(task)
        assert "--append-system-prompt" in cmd
        assert "Extra context" in cmd

    def test_with_session_resume(self):
        task = self._task(
            {
                "prompt": "Continue",
                "agent": "pi",
                "resume": True,
                "session_id": "abc-123",
            }
        )
        cmd = PiNativeProvider._build_command(task)
        assert "--session" in cmd
        assert "abc-123" in cmd

    def test_no_session_without_resume(self):
        task = self._task({"prompt": "Hi", "agent": "pi", "session_id": "abc-123"})
        cmd = PiNativeProvider._build_command(task)
        assert "--session" not in cmd

    def test_with_allowed_tools(self):
        task = self._task(
            {
                "prompt": "Hi",
                "agent": "pi",
                "allowed_tools": ["bash", "read"],
            }
        )
        cmd = PiNativeProvider._build_command(task)
        assert "--tools" in cmd
        assert "bash,read" in cmd

    def test_with_disallowed_tools(self):
        task = self._task(
            {
                "prompt": "Hi",
                "agent": "pi",
                "disallowed_tools": ["write", "edit"],
            }
        )
        cmd = PiNativeProvider._build_command(task)
        assert "--exclude-tools" in cmd
        assert "write,edit" in cmd

    def test_prompt_is_last(self):
        task = self._task({"prompt": "What is 2+2?", "agent": "pi"})
        cmd = PiNativeProvider._build_command(task)
        assert cmd[-1] == "What is 2+2?"


class TestParseEvent:
    def setup_method(self):
        self.provider = PiNativeProvider()

    def test_session_event(self):
        raw = {
            "type": "session",
            "version": 3,
            "id": "019f-abc-123",
            "timestamp": "2026-07-01T00:00:00Z",
            "cwd": "/home/user/project",
        }
        events = self.provider._parse_event(raw)
        assert len(events) == 1
        assert events[0]["type"] == "session_start"
        assert events[0]["session_id"] == "019f-abc-123"
        assert events[0]["agent"] == "pi"
        assert events[0]["working_dir"] == "/home/user/project"

    def test_turn_start(self):
        raw = {"type": "turn_start"}
        events = self.provider._parse_event(raw)
        assert len(events) == 1
        assert events[0]["type"] == "message_start"
        assert events[0]["role"] == "assistant"

    def test_turn_start_clears_pending_text(self):
        """turn_start defensively clears pending text from previous turn."""
        self.provider._pending_text = ["leftover"]
        self.provider._parse_event({"type": "turn_start"})
        assert self.provider._pending_text == []

    def test_text_delta(self):
        raw = {
            "type": "message_update",
            "assistantMessageEvent": {
                "type": "text_delta",
                "contentIndex": 1,
                "delta": "Hello",
            },
        }
        events = self.provider._parse_event(raw)
        assert len(events) == 1
        assert events[0]["type"] == "message_delta"
        assert events[0]["text"] == "Hello"

    def test_text_delta_empty_skipped(self):
        raw = {
            "type": "message_update",
            "assistantMessageEvent": {
                "type": "text_delta",
                "contentIndex": 1,
                "delta": "",
            },
        }
        events = self.provider._parse_event(raw)
        assert events == []

    def test_thinking_delta_skipped(self):
        raw = {
            "type": "message_update",
            "assistantMessageEvent": {
                "type": "thinking_delta",
                "contentIndex": 0,
                "delta": "Let me think...",
            },
        }
        events = self.provider._parse_event(raw)
        assert events == []

    def test_thinking_start_skipped(self):
        raw = {
            "type": "message_update",
            "assistantMessageEvent": {
                "type": "thinking_start",
                "contentIndex": 0,
            },
        }
        events = self.provider._parse_event(raw)
        assert events == []

    def test_message_end_assistant(self):
        raw = {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "The answer is 4"},
                ],
                "stopReason": "stop",
            },
        }
        events = self.provider._parse_event(raw)
        assert len(events) == 1
        assert events[0]["type"] == "message_end"
        assert events[0]["text"] == "The answer is 4"
        assert events[0]["stop_reason"] == "stop"

    def test_message_end_user_skipped(self):
        raw = {
            "type": "message_end",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "What is 2+2?"}],
            },
        }
        events = self.provider._parse_event(raw)
        assert events == []

    def test_message_end_with_thinking_and_text(self):
        """Content blocks with thinking + text — only text extracted."""
        raw = {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "4"},
                    {"type": "text", "text": "4"},
                ],
                "stopReason": "stop",
            },
        }
        events = self.provider._parse_event(raw)
        assert len(events) == 1
        assert events[0]["text"] == "4"

    def test_message_end_clears_pending_text(self):
        """message_end should clear pending text accumulator."""
        self.provider._parse_event(
            {
                "type": "message_update",
                "assistantMessageEvent": {
                    "type": "text_delta",
                    "delta": "partial",
                },
            }
        )
        self.provider._parse_event(
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "full text"}],
                    "stopReason": "stop",
                },
            }
        )
        assert self.provider._pending_text == []

    def test_message_end_uses_pending_text_as_fallback(self):
        """If content blocks have no text, use accumulated deltas."""
        self.provider._parse_event(
            {
                "type": "message_update",
                "assistantMessageEvent": {
                    "type": "text_delta",
                    "delta": "from deltas",
                },
            }
        )
        events = self.provider._parse_event(
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "content": [],
                    "stopReason": "stop",
                },
            }
        )
        assert events[0]["text"] == "from deltas"

    def test_tool_execution_start(self):
        raw = {
            "type": "tool_execution_start",
            "toolCallId": "call_123",
            "toolName": "bash",
            "args": {"command": "ls -la"},
        }
        events = self.provider._parse_event(raw)
        assert len(events) == 1
        assert events[0]["type"] == "tool_use"
        assert events[0]["tool_use_id"] == "call_123"
        assert events[0]["tool_name"] == "bash"
        assert events[0]["tool_input"] == {"command": "ls -la"}

    def test_tool_execution_end_success(self):
        raw = {
            "type": "tool_execution_end",
            "toolCallId": "call_123",
            "toolName": "bash",
            "result": {
                "content": [{"type": "text", "text": "file1.py\nfile2.py\n"}],
            },
            "isError": False,
        }
        events = self.provider._parse_event(raw)
        assert len(events) == 1
        assert events[0]["type"] == "tool_result"
        assert events[0]["tool_use_id"] == "call_123"
        assert events[0]["content"] == "file1.py\nfile2.py\n"
        assert "is_error" not in events[0]

    def test_tool_execution_end_error(self):
        raw = {
            "type": "tool_execution_end",
            "toolCallId": "call_456",
            "toolName": "bash",
            "result": {
                "content": [{"type": "text", "text": "command not found"}],
            },
            "isError": True,
        }
        events = self.provider._parse_event(raw)
        assert len(events) == 1
        assert events[0]["type"] == "tool_result"
        assert events[0]["is_error"] is True

    def test_turn_end_with_usage(self):
        raw = {
            "type": "turn_end",
            "message": {
                "role": "assistant",
                "model": "argo:claude-opus-4.6",
                "usage": {
                    "input": 100,
                    "output": 50,
                    "cacheRead": 20,
                    "cacheWrite": 10,
                    "totalTokens": 150,
                    "cost": {
                        "input": 0.001,
                        "output": 0.002,
                        "total": 0.003,
                    },
                },
                "stopReason": "stop",
            },
        }
        events = self.provider._parse_event(raw)
        assert len(events) == 1
        assert events[0]["type"] == "usage"
        assert events[0]["usage"]["input_tokens"] == 100
        assert events[0]["usage"]["output_tokens"] == 50
        assert events[0]["usage"]["total_tokens"] == 150
        assert events[0]["usage"]["cache_read_tokens"] == 20
        assert events[0]["usage"]["cache_creation_tokens"] == 10
        assert events[0]["cost_usd"] == 0.003
        assert events[0]["model"] == "argo:claude-opus-4.6"

    def test_turn_end_minimal_usage(self):
        raw = {
            "type": "turn_end",
            "message": {
                "role": "assistant",
                "usage": {
                    "input": 0,
                    "output": 0,
                    "totalTokens": 0,
                    "cost": {"total": 0},
                },
            },
        }
        events = self.provider._parse_event(raw)
        assert len(events) == 1
        assert events[0]["type"] == "usage"

    def test_agent_end(self):
        raw = {
            "type": "agent_end",
            "messages": [],
            "willRetry": False,
        }
        events = self.provider._parse_event(raw)
        assert len(events) == 1
        assert events[0]["type"] == "session_end"

    def test_unknown_event_type(self):
        raw = {"type": "unknown_event", "data": "whatever"}
        events = self.provider._parse_event(raw)
        assert events == []

    def test_message_end_carries_accumulated_text(self):
        """Full flow: text_delta → message_end → text is on message_end."""
        self.provider._parse_event(
            {
                "type": "message_update",
                "assistantMessageEvent": {
                    "type": "text_delta",
                    "delta": "Hello ",
                },
            }
        )
        self.provider._parse_event(
            {
                "type": "message_update",
                "assistantMessageEvent": {
                    "type": "text_delta",
                    "delta": "world",
                },
            }
        )
        events = self.provider._parse_event(
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Hello world"}],
                    "stopReason": "stop",
                },
            }
        )
        end = events[0]
        assert end["text"] == "Hello world"
        assert self.provider._pending_text == []


class TestResultTextAggregation:
    """End-to-end: full event flow through _RunState produces result_text."""

    def test_pi_simple_flow(self):
        from agentabi.providers.base import _RunState

        provider = PiNativeProvider()
        state = _RunState()

        raw_events = [
            {
                "type": "session",
                "version": 3,
                "id": "ses-abc",
                "cwd": "/tmp",
            },
            {"type": "turn_start"},
            {
                "type": "message_update",
                "assistantMessageEvent": {
                    "type": "text_delta",
                    "delta": "4",
                },
            },
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "4"}],
                    "stopReason": "stop",
                },
            },
            {
                "type": "turn_end",
                "message": {
                    "role": "assistant",
                    "usage": {
                        "input": 10,
                        "output": 5,
                        "totalTokens": 15,
                        "cost": {"total": 0},
                    },
                },
            },
            {"type": "agent_end", "messages": [], "willRetry": False},
        ]
        for raw in raw_events:
            for event in provider._parse_event(raw):
                state.handle(event)

        result = state.build()
        assert result["status"] == "success"
        assert result["result_text"] == "4"
        assert result["session_id"] == "ses-abc"

    def test_pi_multi_turn_with_tool(self):
        """Multi-turn: text → tool → text → done."""
        from agentabi.providers.base import _RunState

        provider = PiNativeProvider()
        state = _RunState()

        raw_events = [
            {
                "type": "session",
                "version": 3,
                "id": "ses-xyz",
                "cwd": "/tmp",
            },
            # Turn 1: tool use
            {"type": "turn_start"},
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "toolCall",
                            "id": "call_1",
                            "name": "bash",
                            "arguments": {"command": "echo 18"},
                        },
                    ],
                    "stopReason": "toolUse",
                },
            },
            {
                "type": "tool_execution_start",
                "toolCallId": "call_1",
                "toolName": "bash",
                "args": {"command": "echo 18"},
            },
            {
                "type": "tool_execution_end",
                "toolCallId": "call_1",
                "toolName": "bash",
                "result": {
                    "content": [{"type": "text", "text": "18\n"}],
                },
                "isError": False,
            },
            {
                "type": "turn_end",
                "message": {
                    "role": "assistant",
                    "usage": {
                        "input": 50,
                        "output": 20,
                        "totalTokens": 70,
                        "cost": {"total": 0},
                    },
                    "stopReason": "toolUse",
                },
            },
            # Turn 2: final answer
            {"type": "turn_start"},
            {
                "type": "message_update",
                "assistantMessageEvent": {
                    "type": "text_delta",
                    "delta": "The answer is 18.",
                },
            },
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "The answer is 18."},
                    ],
                    "stopReason": "stop",
                },
            },
            {
                "type": "turn_end",
                "message": {
                    "role": "assistant",
                    "usage": {
                        "input": 80,
                        "output": 10,
                        "totalTokens": 90,
                        "cost": {"total": 0},
                    },
                    "stopReason": "stop",
                },
            },
            {"type": "agent_end", "messages": [], "willRetry": False},
        ]
        for raw in raw_events:
            for event in provider._parse_event(raw):
                state.handle(event)

        result = state.build()
        assert result["status"] == "success"
        assert result["result_text"] == "The answer is 18."


class TestCapabilities:
    def test_capabilities(self):
        caps = PiNativeProvider().capabilities()
        assert caps["agent_type"] == "pi"
        assert caps["supports_streaming"] is True
        assert caps["supports_system_prompt"] is True
        assert caps["supports_tool_filtering"] is True
        assert caps["supports_session_resume"] is True
        assert caps["supports_permissions"] is False
        assert caps["supports_mcp"] is False
        assert caps["supports_multi_turn"] is True
        assert caps["transport"] == "subprocess"
