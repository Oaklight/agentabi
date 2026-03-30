"""Tests for ClaudeNativeProvider."""

from agentabi.providers.claude_native import ClaudeNativeProvider


class TestBuildCommand:
    """Test TaskConfig → CLI args conversion."""

    def test_minimal_task(self):
        cmd = ClaudeNativeProvider._build_command({"prompt": "hello"})
        assert cmd[0] == "claude"
        assert "--print" in cmd
        assert "--output-format" in cmd
        assert "stream-json" in cmd
        assert cmd[-1] == "hello"
        assert cmd[-2] == "--"

    def test_with_model(self):
        cmd = ClaudeNativeProvider._build_command({"prompt": "hi", "model": "opus"})
        idx = cmd.index("--model")
        assert cmd[idx + 1] == "opus"

    def test_with_system_prompt(self):
        cmd = ClaudeNativeProvider._build_command(
            {"prompt": "hi", "system_prompt": "You are helpful"}
        )
        idx = cmd.index("--system-prompt")
        assert cmd[idx + 1] == "You are helpful"

    def test_with_max_turns(self):
        cmd = ClaudeNativeProvider._build_command({"prompt": "hi", "max_turns": 5})
        idx = cmd.index("--max-turns")
        assert cmd[idx + 1] == "5"

    def test_with_session_resume(self):
        cmd = ClaudeNativeProvider._build_command(
            {"prompt": "hi", "session_id": "abc123", "resume": True}
        )
        idx = cmd.index("--resume")
        assert cmd[idx + 1] == "abc123"

    def test_full_auto_permissions(self):
        cmd = ClaudeNativeProvider._build_command(
            {"prompt": "hi", "permissions": {"level": "full_auto"}}
        )
        assert "--dangerously-skip-permissions" in cmd

    def test_plan_mode_permissions(self):
        cmd = ClaudeNativeProvider._build_command(
            {"prompt": "hi", "permissions": {"level": "plan"}}
        )
        idx = cmd.index("--permission-mode")
        assert cmd[idx + 1] == "plan"

    def test_with_mcp_config(self):
        cmd = ClaudeNativeProvider._build_command(
            {"prompt": "hi", "mcp_config": "/path/to/mcp.json"}
        )
        idx = cmd.index("--mcp-config")
        assert cmd[idx + 1] == "/path/to/mcp.json"

    def test_with_allowed_tools(self):
        cmd = ClaudeNativeProvider._build_command(
            {"prompt": "hi", "allowed_tools": ["Read", "Write"]}
        )
        idx = cmd.index("--allowed-tools")
        assert cmd[idx + 1] == "Read,Write"


class TestEventParsing:
    """Test Claude Code JSONL → IR event conversion."""

    def test_system_event(self):
        raw = {
            "type": "system",
            "session_id": "sess-123",
            "model": "claude-sonnet-4-20250514",
            "tools": ["Read", "Write"],
            "cwd": "/tmp",
        }
        events = ClaudeNativeProvider._parse_event(raw)
        assert len(events) == 1
        assert events[0]["type"] == "session_start"
        assert events[0]["session_id"] == "sess-123"
        assert events[0]["model"] == "claude-sonnet-4-20250514"
        assert events[0]["agent"] == "claude_code"

    def test_assistant_text_event(self):
        raw = {
            "type": "assistant",
            "message": {
                "id": "msg-1",
                "content": [{"type": "text", "text": "Hello world"}],
                "stop_reason": "end_turn",
            },
        }
        events = ClaudeNativeProvider._parse_event(raw)
        types = [e["type"] for e in events]
        assert "message_start" in types
        assert "message_end" in types
        end = [e for e in events if e["type"] == "message_end"][0]
        assert end["text"] == "Hello world"

    def test_assistant_tool_use_event(self):
        raw = {
            "type": "assistant",
            "message": {
                "id": "msg-2",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tu-1",
                        "name": "Read",
                        "input": {"file_path": "/tmp/test.py"},
                    }
                ],
                "stop_reason": "tool_use",
            },
        }
        events = ClaudeNativeProvider._parse_event(raw)
        tool_events = [e for e in events if e["type"] == "tool_use"]
        assert len(tool_events) == 1
        assert tool_events[0]["tool_name"] == "Read"
        assert tool_events[0]["tool_input"]["file_path"] == "/tmp/test.py"

    def test_stream_event_text_delta(self):
        raw = {
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "Hello"},
            },
        }
        events = ClaudeNativeProvider._parse_event(raw)
        assert len(events) == 1
        assert events[0]["type"] == "message_delta"
        assert events[0]["text"] == "Hello"

    def test_user_tool_result(self):
        raw = {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tu-1",
                        "content": "file contents here",
                    }
                ],
            },
        }
        events = ClaudeNativeProvider._parse_event(raw)
        assert len(events) == 1
        assert events[0]["type"] == "tool_result"
        assert events[0]["tool_use_id"] == "tu-1"

    def test_result_event(self):
        raw = {
            "type": "result",
            "session_id": "sess-123",
            "usage": {"input_tokens": 100, "output_tokens": 50},
            "total_cost_usd": 0.005,
        }
        events = ClaudeNativeProvider._parse_event(raw)
        types = [e["type"] for e in events]
        assert "usage" in types
        assert "session_end" in types
        usage = [e for e in events if e["type"] == "usage"][0]
        assert usage["usage"]["input_tokens"] == 100
        assert usage["cost_usd"] == 0.005

    def test_result_with_error(self):
        raw = {
            "type": "result",
            "session_id": "sess-123",
            "is_error": True,
            "errors": ["rate limit exceeded"],
            "usage": {},
        }
        events = ClaudeNativeProvider._parse_event(raw)
        error_events = [e for e in events if e["type"] == "error"]
        assert len(error_events) == 1
        assert "rate limit" in error_events[0]["error"]

    def test_unknown_event_returns_empty(self):
        events = ClaudeNativeProvider._parse_event({"type": "unknown_type"})
        assert events == []


class TestCapabilities:
    def test_capabilities(self):
        provider = ClaudeNativeProvider()
        caps = provider.capabilities()
        assert caps["name"] == "Claude Code"
        assert caps["agent_type"] == "claude_code"
        assert caps["supports_streaming"] is True
