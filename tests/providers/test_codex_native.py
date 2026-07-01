"""Tests for CodexNativeProvider event parsing and command building."""

from typing import Any, cast

from agentabi.providers.codex_native import CodexNativeProvider
from agentabi.types.ir.task import TaskConfig


class TestBuildCommand:
    @staticmethod
    def _task(d: dict[str, Any]) -> TaskConfig:
        return cast(TaskConfig, d)

    def test_basic_command(self):
        task = self._task({"prompt": "Hello", "agent": "codex"})
        cmd = CodexNativeProvider._build_command(task)
        assert cmd == ["codex", "exec", "--json", "--full-auto", "Hello"]

    def test_with_model(self):
        task = self._task({"prompt": "Hi", "agent": "codex", "model": "o3"})
        cmd = CodexNativeProvider._build_command(task)
        assert "-m" in cmd
        assert "o3" in cmd

    def test_with_working_dir(self):
        task = self._task(
            {"prompt": "Do it", "agent": "codex", "working_dir": "/tmp/test"}
        )
        cmd = CodexNativeProvider._build_command(task)
        assert "-C" in cmd
        assert "/tmp/test" in cmd

    def test_with_full_auto_permissions(self):
        task = self._task(
            {
                "prompt": "Fix it",
                "agent": "codex",
                "permissions": {"level": "full_auto"},
            }
        )
        cmd = CodexNativeProvider._build_command(task)
        assert "--dangerously-bypass-approvals-and-sandbox" in cmd

    def test_prompt_is_last(self):
        task = self._task({"prompt": "What is 2+2?", "agent": "codex"})
        cmd = CodexNativeProvider._build_command(task)
        assert cmd[-1] == "What is 2+2?"


class TestParseEvent:
    def setup_method(self):
        self.provider = CodexNativeProvider()

    def test_thread_started(self):
        raw = {"type": "thread.started", "thread_id": "abc-123"}
        events = self.provider._parse_event(raw)
        assert len(events) == 1
        assert events[0]["type"] == "session_start"
        assert events[0]["session_id"] == "abc-123"
        assert events[0]["agent"] == "codex"

    def test_turn_started(self):
        raw = {"type": "turn.started"}
        events = self.provider._parse_event(raw)
        assert len(events) == 1
        assert events[0]["type"] == "message_start"
        assert events[0]["role"] == "assistant"

    def test_agent_message_completed(self):
        raw = {
            "type": "item.completed",
            "item": {
                "id": "item_0",
                "type": "agent_message",
                "text": "The answer is 4",
            },
        }
        events = self.provider._parse_event(raw)
        assert len(events) == 1
        assert events[0]["type"] == "message_delta"
        assert events[0]["text"] == "The answer is 4"

    def test_agent_message_started_ignored(self):
        raw = {
            "type": "item.started",
            "item": {"id": "item_0", "type": "agent_message", "text": ""},
        }
        events = self.provider._parse_event(raw)
        assert events == []

    def test_agent_message_empty_text(self):
        raw = {
            "type": "item.completed",
            "item": {"id": "item_0", "type": "agent_message", "text": ""},
        }
        events = self.provider._parse_event(raw)
        assert events == []

    def test_command_started(self):
        raw = {
            "type": "item.started",
            "item": {
                "id": "item_1",
                "type": "command_execution",
                "command": "/usr/bin/bash -lc 'ls -la'",
                "aggregated_output": "",
                "exit_code": None,
                "status": "in_progress",
            },
        }
        events = self.provider._parse_event(raw)
        assert len(events) == 1
        assert events[0]["type"] == "tool_use"
        assert events[0]["tool_use_id"] == "item_1"
        assert events[0]["tool_name"] == "command_execution"
        assert events[0]["tool_input"] == {"command": "/usr/bin/bash -lc 'ls -la'"}

    def test_command_completed_success(self):
        raw = {
            "type": "item.completed",
            "item": {
                "id": "item_1",
                "type": "command_execution",
                "command": "ls",
                "aggregated_output": "file1.py\nfile2.py\n",
                "exit_code": 0,
                "status": "completed",
            },
        }
        events = self.provider._parse_event(raw)
        assert len(events) == 1
        assert events[0]["type"] == "tool_result"
        assert events[0]["tool_use_id"] == "item_1"
        assert events[0]["content"] == "file1.py\nfile2.py\n"
        assert "is_error" not in events[0]

    def test_command_completed_failed(self):
        raw = {
            "type": "item.completed",
            "item": {
                "id": "item_2",
                "type": "command_execution",
                "command": "bad_cmd",
                "aggregated_output": "command not found",
                "exit_code": 127,
                "status": "failed",
            },
        }
        events = self.provider._parse_event(raw)
        assert len(events) == 1
        assert events[0]["type"] == "tool_result"
        assert events[0]["is_error"] is True

    def test_file_change_completed(self):
        raw = {
            "type": "item.completed",
            "item": {
                "id": "item_3",
                "type": "file_change",
                "changes": [
                    {"path": "src/main.py", "kind": "update"},
                    {"path": "src/new.py", "kind": "add"},
                    {"path": "src/old.py", "kind": "delete"},
                ],
            },
        }
        events = self.provider._parse_event(raw)
        assert len(events) == 3
        assert events[0]["type"] == "file_diff"
        assert events[0]["file_path"] == "src/main.py"
        assert events[0]["action"] == "modify"
        assert events[1]["action"] == "create"
        assert events[2]["action"] == "delete"

    def test_file_change_started_ignored(self):
        raw = {
            "type": "item.started",
            "item": {"id": "item_3", "type": "file_change", "changes": []},
        }
        events = self.provider._parse_event(raw)
        assert events == []

    def test_mcp_tool_started(self):
        raw = {
            "type": "item.started",
            "item": {
                "id": "item_4",
                "type": "mcp_tool_call",
                "server": "my_server",
                "tool": "search",
                "arguments": {"query": "test"},
                "status": "in_progress",
            },
        }
        events = self.provider._parse_event(raw)
        assert len(events) == 1
        assert events[0]["type"] == "tool_use"
        assert events[0]["tool_name"] == "mcp:my_server/search"
        assert events[0]["tool_input"] == {"query": "test"}

    def test_mcp_tool_completed(self):
        raw = {
            "type": "item.completed",
            "item": {
                "id": "item_4",
                "type": "mcp_tool_call",
                "server": "my_server",
                "tool": "search",
                "result": {"content": ["result data"]},
                "status": "completed",
            },
        }
        events = self.provider._parse_event(raw)
        assert len(events) == 1
        assert events[0]["type"] == "tool_result"
        assert events[0]["tool_use_id"] == "item_4"

    def test_mcp_tool_failed(self):
        raw = {
            "type": "item.completed",
            "item": {
                "id": "item_5",
                "type": "mcp_tool_call",
                "server": "s",
                "tool": "t",
                "error": {"message": "not found"},
                "status": "failed",
            },
        }
        events = self.provider._parse_event(raw)
        assert len(events) == 1
        assert events[0]["is_error"] is True
        assert events[0]["content"] == "not found"

    def test_error_item(self):
        raw = {
            "type": "item.completed",
            "item": {
                "id": "item_err",
                "type": "error",
                "message": "Something went wrong",
            },
        }
        events = self.provider._parse_event(raw)
        assert len(events) == 1
        assert events[0]["type"] == "error"
        assert events[0]["error"] == "Something went wrong"

    def test_turn_completed(self):
        raw = {
            "type": "turn.completed",
            "usage": {
                "input_tokens": 10849,
                "cached_input_tokens": 0,
                "output_tokens": 5,
            },
        }
        events = self.provider._parse_event(raw)
        assert len(events) == 3  # usage + message_end + session_end
        assert events[0]["type"] == "usage"
        assert events[0]["usage"]["input_tokens"] == 10849
        assert events[0]["usage"]["output_tokens"] == 5
        assert events[0]["usage"]["total_tokens"] == 10854
        assert events[1]["type"] == "message_end"
        assert events[1]["stop_reason"] == "end_turn"
        assert events[2]["type"] == "session_end"

    def test_turn_completed_with_cache(self):
        raw = {
            "type": "turn.completed",
            "usage": {
                "input_tokens": 100,
                "cached_input_tokens": 50,
                "output_tokens": 10,
            },
        }
        events = self.provider._parse_event(raw)
        usage = events[0]["usage"]
        assert usage["cache_read_tokens"] == 50

    def test_unknown_event_type(self):
        raw = {"type": "unknown.thing", "data": "whatever"}
        events = self.provider._parse_event(raw)
        assert events == []

    def test_message_end_carries_text(self):
        """Verify that message_end carries accumulated text from agent_message."""
        # Simulate: agent_message completed → turn.completed
        self.provider._parse_event(
            {
                "type": "item.completed",
                "item": {
                    "id": "item_0",
                    "type": "agent_message",
                    "text": "The answer is 18",
                },
            }
        )
        events = self.provider._parse_event(
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 100,
                    "cached_input_tokens": 0,
                    "output_tokens": 5,
                },
            }
        )
        end = [e for e in events if e["type"] == "message_end"][0]
        assert end["text"] == "The answer is 18"

    def test_message_end_accumulates_multiple_messages(self):
        """Verify text from multiple agent_message items is concatenated."""
        self.provider._parse_event(
            {
                "type": "item.completed",
                "item": {"id": "item_0", "type": "agent_message", "text": "Hello "},
            }
        )
        self.provider._parse_event(
            {
                "type": "item.completed",
                "item": {"id": "item_1", "type": "agent_message", "text": "world"},
            }
        )
        events = self.provider._parse_event(
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 10,
                    "cached_input_tokens": 0,
                    "output_tokens": 5,
                },
            }
        )
        end = [e for e in events if e["type"] == "message_end"][0]
        assert end["text"] == "Hello world"

    def test_message_end_no_text_when_no_agent_message(self):
        """Verify message_end has no text key when no agent_message was received."""
        events = self.provider._parse_event(
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 10,
                    "cached_input_tokens": 0,
                    "output_tokens": 5,
                },
            }
        )
        end = [e for e in events if e["type"] == "message_end"][0]
        assert "text" not in end

    def test_pending_text_cleared_after_flush(self):
        """Verify pending text is cleared after turn.completed."""
        self.provider._parse_event(
            {
                "type": "item.completed",
                "item": {"id": "item_0", "type": "agent_message", "text": "First"},
            }
        )
        self.provider._parse_event(
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 10,
                    "cached_input_tokens": 0,
                    "output_tokens": 5,
                },
            }
        )
        # Second turn with no text
        events = self.provider._parse_event(
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 10,
                    "cached_input_tokens": 0,
                    "output_tokens": 5,
                },
            }
        )
        end = [e for e in events if e["type"] == "message_end"][0]
        assert "text" not in end


class TestResultTextAggregation:
    """End-to-end test: full event flow through _RunState produces result_text."""

    def test_codex_flow_produces_result_text(self):
        from agentabi.providers.base import _RunState

        provider = CodexNativeProvider()
        state = _RunState()

        # Simulate full Codex session: thread.started → turn.started →
        # agent_message → turn.completed
        raw_events = [
            {"type": "thread.started", "thread_id": "thread-abc"},
            {"type": "turn.started"},
            {
                "type": "item.completed",
                "item": {"id": "item_0", "type": "agent_message", "text": "18"},
            },
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 100,
                    "cached_input_tokens": 0,
                    "output_tokens": 5,
                },
            },
        ]
        for raw in raw_events:
            for event in provider._parse_event(raw):
                state.handle(event)

        result = state.build()
        assert result["status"] == "success"
        assert result["result_text"] == "18"

    def test_codex_multi_turn_produces_result_text(self):
        """Multi-turn: text → tool → text → done. Final text should win."""
        from agentabi.providers.base import _RunState

        provider = CodexNativeProvider()
        state = _RunState()

        raw_events = [
            {"type": "thread.started", "thread_id": "thread-abc"},
            # Turn 1: agent says something, then uses a tool
            {"type": "turn.started"},
            {
                "type": "item.completed",
                "item": {
                    "id": "item_0",
                    "type": "agent_message",
                    "text": "Let me check.",
                },
            },
            {
                "type": "item.started",
                "item": {
                    "id": "item_1",
                    "type": "command_execution",
                    "command": "echo 18",
                },
            },
            {
                "type": "item.completed",
                "item": {
                    "id": "item_1",
                    "type": "command_execution",
                    "command": "echo 18",
                    "aggregated_output": "18\n",
                    "exit_code": 0,
                    "status": "completed",
                },
            },
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 100,
                    "cached_input_tokens": 0,
                    "output_tokens": 50,
                },
            },
            # Turn 2: agent gives final answer
            {"type": "turn.started"},
            {
                "type": "item.completed",
                "item": {
                    "id": "item_2",
                    "type": "agent_message",
                    "text": "The answer is 18.",
                },
            },
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 200,
                    "cached_input_tokens": 0,
                    "output_tokens": 10,
                },
            },
        ]
        for raw in raw_events:
            for event in provider._parse_event(raw):
                state.handle(event)

        result = state.build()
        assert result["status"] == "success"
        assert result["result_text"] == "The answer is 18."


class TestCapabilities:
    def test_capabilities(self):
        caps = CodexNativeProvider().capabilities()
        assert caps["agent_type"] == "codex"
        assert caps["supports_streaming"] is True
        assert caps["supports_mcp"] is True
        assert caps["transport"] == "subprocess"
