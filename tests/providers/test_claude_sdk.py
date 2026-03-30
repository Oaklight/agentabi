"""Tests for ClaudeSDKProvider event conversion."""

from dataclasses import dataclass, field
from typing import Any, List, Optional
from unittest.mock import patch


# Mock SDK types for testing without installing claude-agent-sdk
@dataclass
class MockTextBlock:
    text: str


@dataclass
class MockThinkingBlock:
    thinking: str
    signature: str


@dataclass
class MockToolUseBlock:
    id: str
    name: str
    input: dict


@dataclass
class MockToolResultBlock:
    tool_use_id: str
    content: Any = None
    is_error: bool = False


@dataclass
class MockAssistantMessage:
    content: List[Any] = field(default_factory=list)
    model: str = "claude-sonnet-4-20250514"
    message_id: Optional[str] = None
    stop_reason: Optional[str] = None
    session_id: Optional[str] = None
    usage: Optional[dict] = None
    parent_tool_use_id: Optional[str] = None
    error: Optional[str] = None
    uuid: Optional[str] = None


@dataclass
class MockSystemMessage:
    subtype: str = "init"
    data: dict = field(default_factory=dict)


@dataclass
class MockUserMessage:
    content: Any = ""
    uuid: Optional[str] = None
    parent_tool_use_id: Optional[str] = None
    tool_use_result: Optional[dict] = None


@dataclass
class MockResultMessage:
    subtype: str = "result"
    duration_ms: int = 1000
    duration_api_ms: int = 800
    is_error: bool = False
    num_turns: int = 1
    session_id: str = "sess-123"
    stop_reason: Optional[str] = "end_turn"
    total_cost_usd: Optional[float] = 0.01
    usage: Optional[dict] = None
    result: Optional[str] = None
    structured_output: Any = None
    model_usage: Optional[dict] = None
    permission_denials: Optional[list] = None
    errors: Optional[list] = None
    uuid: Optional[str] = None


@dataclass
class MockStreamEvent:
    uuid: str = "uuid-1"
    session_id: str = "sess-123"
    event: dict = field(default_factory=dict)
    parent_tool_use_id: Optional[str] = None


class TestClaudeSDKConvert:
    """Test _convert() dispatches to correct handler."""

    def _convert(self, msg):
        """Helper to call _convert with mocked SDK types."""
        with patch.dict(
            "sys.modules",
            {
                "claude_agent_sdk": type(
                    "module",
                    (),
                    {
                        "AssistantMessage": MockAssistantMessage,
                        "SystemMessage": MockSystemMessage,
                        "UserMessage": MockUserMessage,
                        "ResultMessage": MockResultMessage,
                        "StreamEvent": MockStreamEvent,
                        "TextBlock": MockTextBlock,
                        "ThinkingBlock": MockThinkingBlock,
                        "ToolUseBlock": MockToolUseBlock,
                        "ToolResultBlock": MockToolResultBlock,
                    },
                )(),
            },
        ):
            from agentabi.providers.claude_sdk import ClaudeSDKProvider

            return ClaudeSDKProvider._convert(msg)

    def test_convert_system_message(self):
        msg = MockSystemMessage(
            subtype="init",
            data={
                "session_id": "sess-abc",
                "model": "claude-sonnet-4-20250514",
                "cwd": "/home/user/project",
                "tools": ["Read", "Write", "Bash"],
            },
        )
        events = self._convert(msg)
        assert len(events) == 1
        ev = events[0]
        assert ev["type"] == "session_start"
        assert ev["session_id"] == "sess-abc"
        assert ev["model"] == "claude-sonnet-4-20250514"
        assert ev["working_dir"] == "/home/user/project"
        assert ev["tools"] == ["Read", "Write", "Bash"]

    def test_convert_system_message_minimal(self):
        msg = MockSystemMessage(subtype="init", data={})
        events = self._convert(msg)
        assert len(events) == 1
        assert events[0]["type"] == "session_start"
        assert events[0]["session_id"] == ""

    def test_convert_assistant_text_only(self):
        msg = MockAssistantMessage(
            content=[MockTextBlock(text="Hello, world!")],
            message_id="msg-1",
            stop_reason="end_turn",
        )
        events = self._convert(msg)
        assert len(events) == 2  # start + end
        assert events[0]["type"] == "message_start"
        assert events[0]["role"] == "assistant"
        assert events[0]["message_id"] == "msg-1"
        assert events[1]["type"] == "message_end"
        assert events[1]["text"] == "Hello, world!"
        assert events[1]["stop_reason"] == "end_turn"

    def test_convert_assistant_with_tool_use(self):
        msg = MockAssistantMessage(
            content=[
                MockTextBlock(text="Let me read that file."),
                MockToolUseBlock(
                    id="tu-1",
                    name="Read",
                    input={"file_path": "/tmp/test.py"},
                ),
            ],
            message_id="msg-2",
        )
        events = self._convert(msg)
        assert len(events) == 3  # start + tool_use + end
        assert events[0]["type"] == "message_start"
        assert events[1]["type"] == "tool_use"
        assert events[1]["tool_use_id"] == "tu-1"
        assert events[1]["tool_name"] == "Read"
        assert events[1]["tool_input"] == {"file_path": "/tmp/test.py"}
        assert events[2]["type"] == "message_end"
        assert events[2]["text"] == "Let me read that file."

    def test_convert_assistant_multiple_tool_uses(self):
        msg = MockAssistantMessage(
            content=[
                MockTextBlock(text="I'll check both files."),
                MockToolUseBlock(id="tu-1", name="Read", input={"file_path": "a.py"}),
                MockToolUseBlock(id="tu-2", name="Read", input={"file_path": "b.py"}),
            ],
        )
        events = self._convert(msg)
        assert len(events) == 4  # start + 2 tool_use + end
        tool_uses = [e for e in events if e["type"] == "tool_use"]
        assert len(tool_uses) == 2

    def test_convert_assistant_no_message_id(self):
        msg = MockAssistantMessage(content=[MockTextBlock(text="Hi")])
        events = self._convert(msg)
        assert "message_id" not in events[0]
        assert "message_id" not in events[1]

    def test_convert_user_tool_result(self):
        msg = MockUserMessage(
            content=[
                MockToolResultBlock(
                    tool_use_id="tu-1",
                    content="file contents here",
                    is_error=False,
                ),
            ]
        )
        events = self._convert(msg)
        assert len(events) == 1
        assert events[0]["type"] == "tool_result"
        assert events[0]["tool_use_id"] == "tu-1"
        assert events[0]["content"] == "file contents here"
        assert "is_error" not in events[0]

    def test_convert_user_tool_result_error(self):
        msg = MockUserMessage(
            content=[
                MockToolResultBlock(
                    tool_use_id="tu-2",
                    content="Permission denied",
                    is_error=True,
                ),
            ]
        )
        events = self._convert(msg)
        assert len(events) == 1
        assert events[0]["is_error"] is True

    def test_convert_user_string_content_ignored(self):
        msg = MockUserMessage(content="just a string prompt")
        events = self._convert(msg)
        assert len(events) == 0

    def test_convert_stream_event_text_delta(self):
        msg = MockStreamEvent(
            event={
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "Hello"},
            }
        )
        events = self._convert(msg)
        assert len(events) == 1
        assert events[0]["type"] == "message_delta"
        assert events[0]["text"] == "Hello"

    def test_convert_stream_event_non_text_delta(self):
        msg = MockStreamEvent(
            event={
                "type": "content_block_delta",
                "delta": {"type": "input_json_delta", "partial_json": '{"a":'},
            }
        )
        events = self._convert(msg)
        assert len(events) == 0

    def test_convert_stream_event_other_type(self):
        msg = MockStreamEvent(event={"type": "content_block_start"})
        events = self._convert(msg)
        assert len(events) == 0

    def test_convert_result_message(self):
        msg = MockResultMessage(
            session_id="sess-123",
            total_cost_usd=0.05,
            usage={
                "input_tokens": 1000,
                "output_tokens": 500,
                "cache_read_input_tokens": 200,
            },
        )
        events = self._convert(msg)
        assert len(events) == 2  # usage + session_end

        usage_ev = events[0]
        assert usage_ev["type"] == "usage"
        assert usage_ev["usage"]["input_tokens"] == 1000
        assert usage_ev["usage"]["output_tokens"] == 500
        assert usage_ev["usage"]["cache_read_tokens"] == 200
        assert usage_ev["usage"]["total_tokens"] == 1500
        assert usage_ev["cost_usd"] == 0.05

        end_ev = events[1]
        assert end_ev["type"] == "session_end"
        assert end_ev["session_id"] == "sess-123"

    def test_convert_result_message_error(self):
        msg = MockResultMessage(
            is_error=True,
            errors=["Rate limit exceeded", "Retry later"],
            session_id="sess-err",
        )
        events = self._convert(msg)
        assert len(events) == 3  # usage + error + session_end
        error_ev = events[1]
        assert error_ev["type"] == "error"
        assert "Rate limit exceeded" in error_ev["error"]
        assert "Retry later" in error_ev["error"]
        assert error_ev["is_fatal"] is True

    def test_convert_result_message_no_usage(self):
        msg = MockResultMessage(usage=None, total_cost_usd=None)
        events = self._convert(msg)
        usage_ev = events[0]
        assert usage_ev["type"] == "usage"
        assert usage_ev["usage"] == {}
        assert "cost_usd" not in usage_ev

    def test_convert_unknown_message_type(self):
        """Unknown message types should return empty list."""

        @dataclass
        class UnknownMessage:
            data: str = "unknown"

        events = self._convert(UnknownMessage())
        assert events == []


class TestClaudeSDKCapabilities:
    def test_capabilities(self):
        with patch.dict("sys.modules", {"claude_agent_sdk": None}):
            from agentabi.providers.claude_sdk import ClaudeSDKProvider

            caps = ClaudeSDKProvider().capabilities()
            assert caps["agent_type"] == "claude_code"
            assert caps["supports_streaming"] is True
            assert caps["supports_mcp"] is True
