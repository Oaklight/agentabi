"""Tests for GeminiSDKProvider event conversion."""

from dataclasses import dataclass, field
from typing import Any, List, Optional
from unittest.mock import patch


# Mock SDK types
@dataclass
class MockTextBlock:
    text: str
    type: str = "text"


@dataclass
class MockCodeBlock:
    code: str
    language: str = "python"
    type: str = "code"


@dataclass
class MockToolUseBlock:
    id: str
    name: str
    input: dict
    type: str = "tool_use"


@dataclass
class MockToolResultBlock:
    tool_use_id: str
    content: Any = None
    is_error: bool = False
    type: str = "tool_result"


@dataclass
class MockUserMessage:
    content: str = "user prompt"
    type: str = "user"


@dataclass
class MockAssistantMessage:
    content: List[Any] = field(default_factory=list)
    type: str = "assistant"


@dataclass
class MockSystemMessage:
    subtype: str = "init"
    data: dict = field(default_factory=dict)
    type: str = "system"


@dataclass
class MockResultMessage:
    subtype: str = "result"
    duration_ms: int = 1000
    is_error: bool = False
    session_id: str = "gemini-sess-1"
    num_turns: int = 1
    total_cost_usd: Optional[float] = None
    usage: Optional[dict] = None
    result: Optional[str] = None
    type: str = "result"


def _patch_gemini_modules():
    return patch.dict(
        "sys.modules",
        {
            "gemini_cli_sdk": type(
                "module",
                (),
                {
                    "GeminiOptions": None,
                    "query": None,
                    "AssistantMessage": MockAssistantMessage,
                    "SystemMessage": MockSystemMessage,
                    "UserMessage": MockUserMessage,
                    "ResultMessage": MockResultMessage,
                    "TextBlock": MockTextBlock,
                    "CodeBlock": MockCodeBlock,
                    "ToolUseBlock": MockToolUseBlock,
                    "ToolResultBlock": MockToolResultBlock,
                },
            )(),
        },
    )


class TestGeminiSDKConvert:
    def _convert(self, msg):
        with _patch_gemini_modules():
            from agentabi.providers.gemini_sdk import GeminiSDKProvider

            return GeminiSDKProvider._convert(msg)

    def test_convert_system_message(self):
        msg = MockSystemMessage(
            subtype="init",
            data={"session_id": "g-sess", "model": "gemini-2.5-pro", "cwd": "/tmp"},
        )
        events = self._convert(msg)
        assert len(events) == 1
        assert events[0]["type"] == "session_start"
        assert events[0]["session_id"] == "g-sess"
        assert events[0]["agent"] == "gemini_cli"
        assert events[0]["model"] == "gemini-2.5-pro"
        assert events[0]["working_dir"] == "/tmp"

    def test_convert_system_message_minimal(self):
        msg = MockSystemMessage(data={})
        events = self._convert(msg)
        assert events[0]["session_id"] == ""

    def test_convert_assistant_text_only(self):
        msg = MockAssistantMessage(content=[MockTextBlock(text="Hello from Gemini")])
        events = self._convert(msg)
        assert len(events) == 2  # start + end
        assert events[0]["type"] == "message_start"
        assert events[0]["role"] == "assistant"
        assert events[1]["type"] == "message_end"
        assert events[1]["text"] == "Hello from Gemini"

    def test_convert_assistant_code_block(self):
        msg = MockAssistantMessage(
            content=[
                MockTextBlock(text="Here is the code:"),
                MockCodeBlock(code="print('hi')", language="python"),
            ]
        )
        events = self._convert(msg)
        end_event = events[-1]
        assert "```python" in end_event["text"]
        assert "print('hi')" in end_event["text"]

    def test_convert_assistant_tool_use(self):
        msg = MockAssistantMessage(
            content=[
                MockTextBlock(text="Reading file..."),
                MockToolUseBlock(
                    id="tu-g1", name="read_file", input={"path": "/tmp/test.py"}
                ),
            ]
        )
        events = self._convert(msg)
        assert len(events) == 3  # start + tool_use + end
        assert events[1]["type"] == "tool_use"
        assert events[1]["tool_use_id"] == "tu-g1"
        assert events[1]["tool_name"] == "read_file"

    def test_convert_user_message_ignored(self):
        msg = MockUserMessage(content="user input")
        events = self._convert(msg)
        assert len(events) == 0

    def test_convert_result_message(self):
        msg = MockResultMessage(
            session_id="g-sess-2",
            total_cost_usd=0.02,
            usage={"input_tokens": 300, "output_tokens": 150},
        )
        events = self._convert(msg)
        assert len(events) == 2  # usage + session_end
        assert events[0]["type"] == "usage"
        assert events[0]["usage"]["input_tokens"] == 300
        assert events[0]["usage"]["output_tokens"] == 150
        assert events[0]["usage"]["total_tokens"] == 450
        assert events[0]["cost_usd"] == 0.02
        assert events[1]["type"] == "session_end"
        assert events[1]["session_id"] == "g-sess-2"

    def test_convert_result_error(self):
        msg = MockResultMessage(
            is_error=True,
            result="API key invalid",
            session_id="g-err",
        )
        events = self._convert(msg)
        assert len(events) == 3  # usage + error + session_end
        assert events[1]["type"] == "error"
        assert events[1]["error"] == "API key invalid"
        assert events[1]["is_fatal"] is True

    def test_convert_result_no_usage(self):
        msg = MockResultMessage(usage=None, total_cost_usd=None)
        events = self._convert(msg)
        assert events[0]["usage"] == {}
        assert "cost_usd" not in events[0]

    def test_convert_unknown_type(self):
        @dataclass
        class UnknownMsg:
            type: str = "unknown"

        events = self._convert(UnknownMsg())
        assert events == []


class TestGeminiSDKCapabilities:
    def test_capabilities(self):
        with _patch_gemini_modules():
            from agentabi.providers.gemini_sdk import GeminiSDKProvider

            caps = GeminiSDKProvider().capabilities()
            assert caps["agent_type"] == "gemini_cli"
            assert caps["supports_streaming"] is True
            assert caps["supports_session_resume"] is False
