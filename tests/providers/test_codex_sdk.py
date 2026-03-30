"""Tests for CodexSDKProvider event conversion."""

from dataclasses import dataclass, field
from typing import Any, List, Literal, Optional
from unittest.mock import patch


# Mock SDK types for testing
@dataclass
class MockUsage:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class MockThreadError:
    message: str = "error"


@dataclass
class MockThreadStartedEvent:
    type: str = "thread.started"
    thread_id: str = "thread-abc"


@dataclass
class MockTurnStartedEvent:
    type: str = "turn.started"


@dataclass
class MockTurnCompletedEvent:
    type: str = "turn.completed"
    usage: MockUsage = field(default_factory=MockUsage)


@dataclass
class MockTurnFailedEvent:
    type: str = "turn.failed"
    error: MockThreadError = field(default_factory=MockThreadError)


@dataclass
class MockAgentMessageItem:
    id: str = "item-1"
    type: Literal["agent_message"] = "agent_message"
    text: str = "Hello from Codex"


@dataclass
class MockCommandExecutionItem:
    id: str = "item-2"
    type: Literal["command_execution"] = "command_execution"
    command: str = "ls -la"
    aggregated_output: str = "total 0\ndrwxr-xr-x 2 user user 40 Jan 1 00:00 ."
    exit_code: Optional[int] = 0
    status: str = "completed"


@dataclass
class MockFileUpdateChange:
    path: str = "src/main.py"
    kind: str = "update"


@dataclass
class MockFileChangeItem:
    id: str = "item-3"
    type: Literal["file_change"] = "file_change"
    changes: List[MockFileUpdateChange] = field(default_factory=list)
    status: str = "completed"


@dataclass
class MockMcpToolCallItemResult:
    content: list = field(default_factory=list)
    structured_content: Any = None


@dataclass
class MockMcpToolCallItemError:
    message: str = "tool failed"


@dataclass
class MockMcpToolCallItem:
    id: str = "item-4"
    type: Literal["mcp_tool_call"] = "mcp_tool_call"
    server: str = "my-server"
    tool: str = "my-tool"
    status: str = "completed"
    arguments: Any = None
    result: Optional[MockMcpToolCallItemResult] = None
    error: Optional[MockMcpToolCallItemError] = None


@dataclass
class MockErrorItem:
    id: str = "item-err"
    type: Literal["error"] = "error"
    message: str = "Something went wrong"


@dataclass
class MockItemStartedEvent:
    type: str = "item.started"
    item: Any = None


@dataclass
class MockItemCompletedEvent:
    type: str = "item.completed"
    item: Any = None


@dataclass
class MockItemUpdatedEvent:
    type: str = "item.updated"
    item: Any = None


@dataclass
class MockThreadErrorEvent:
    type: str = "error"
    message: str = "Thread error occurred"


def _patch_codex_modules():
    """Create a mock codex_sdk module for testing."""
    return patch.dict(
        "sys.modules",
        {
            "codex_sdk": type(
                "module",
                (),
                {
                    "Codex": None,
                    "CodexOptions": None,
                    "ThreadOptions": None,
                    "ThreadStartedEvent": MockThreadStartedEvent,
                    "TurnStartedEvent": MockTurnStartedEvent,
                    "TurnCompletedEvent": MockTurnCompletedEvent,
                    "TurnFailedEvent": MockTurnFailedEvent,
                    "ItemStartedEvent": MockItemStartedEvent,
                    "ItemCompletedEvent": MockItemCompletedEvent,
                    "ItemUpdatedEvent": MockItemUpdatedEvent,
                    "ThreadErrorEvent": MockThreadErrorEvent,
                    "AgentMessageItem": MockAgentMessageItem,
                    "CommandExecutionItem": MockCommandExecutionItem,
                    "FileChangeItem": MockFileChangeItem,
                    "McpToolCallItem": MockMcpToolCallItem,
                    "ErrorItem": MockErrorItem,
                },
            )(),
        },
    )


class TestCodexSDKConvert:
    """Test _convert() dispatches to correct handler."""

    def _convert(self, event):
        with _patch_codex_modules():
            from agentabi.providers.codex_sdk import CodexSDKProvider

            return CodexSDKProvider._convert(event)

    def test_thread_started(self):
        event = MockThreadStartedEvent(thread_id="thread-xyz")
        events = self._convert(event)
        assert len(events) == 1
        assert events[0]["type"] == "session_start"
        assert events[0]["session_id"] == "thread-xyz"
        assert events[0]["agent"] == "codex"

    def test_turn_started(self):
        event = MockTurnStartedEvent()
        events = self._convert(event)
        assert len(events) == 1
        assert events[0]["type"] == "message_start"
        assert events[0]["role"] == "assistant"

    def test_turn_completed(self):
        event = MockTurnCompletedEvent(
            usage=MockUsage(input_tokens=500, output_tokens=200, cached_input_tokens=50)
        )
        events = self._convert(event)
        assert len(events) == 2  # usage + message_end
        assert events[0]["type"] == "usage"
        assert events[0]["usage"]["input_tokens"] == 500
        assert events[0]["usage"]["output_tokens"] == 200
        assert events[0]["usage"]["cache_read_tokens"] == 50
        assert events[0]["usage"]["total_tokens"] == 700
        assert events[1]["type"] == "message_end"

    def test_turn_failed(self):
        event = MockTurnFailedEvent(error=MockThreadError(message="Rate limited"))
        events = self._convert(event)
        assert len(events) == 1
        assert events[0]["type"] == "error"
        assert events[0]["error"] == "Rate limited"
        assert events[0]["is_fatal"] is True

    def test_thread_error(self):
        event = MockThreadErrorEvent(message="Connection lost")
        events = self._convert(event)
        assert len(events) == 1
        assert events[0]["type"] == "error"
        assert events[0]["error"] == "Connection lost"

    def test_agent_message_completed(self):
        item = MockAgentMessageItem(text="Here is my response")
        event = MockItemCompletedEvent(item=item)
        events = self._convert(event)
        assert len(events) == 1
        assert events[0]["type"] == "message_delta"
        assert events[0]["text"] == "Here is my response"

    def test_agent_message_started_no_output(self):
        item = MockAgentMessageItem(text="partial")
        event = MockItemStartedEvent(item=item)
        events = self._convert(event)
        assert len(events) == 0

    def test_command_execution_started(self):
        item = MockCommandExecutionItem(id="cmd-1", command="git status")
        event = MockItemStartedEvent(item=item)
        events = self._convert(event)
        assert len(events) == 1
        assert events[0]["type"] == "tool_use"
        assert events[0]["tool_use_id"] == "cmd-1"
        assert events[0]["tool_name"] == "command_execution"
        assert events[0]["tool_input"] == {"command": "git status"}

    def test_command_execution_completed(self):
        item = MockCommandExecutionItem(
            id="cmd-1",
            command="ls",
            aggregated_output="file1.py\nfile2.py",
            status="completed",
        )
        event = MockItemCompletedEvent(item=item)
        events = self._convert(event)
        assert len(events) == 1
        assert events[0]["type"] == "tool_result"
        assert events[0]["tool_use_id"] == "cmd-1"
        assert events[0]["content"] == "file1.py\nfile2.py"
        assert "is_error" not in events[0]

    def test_command_execution_failed(self):
        item = MockCommandExecutionItem(
            id="cmd-2",
            command="bad_cmd",
            aggregated_output="not found",
            status="failed",
        )
        event = MockItemCompletedEvent(item=item)
        events = self._convert(event)
        assert events[0]["is_error"] is True

    def test_file_change_completed(self):
        item = MockFileChangeItem(
            changes=[
                MockFileUpdateChange(path="src/main.py", kind="update"),
                MockFileUpdateChange(path="src/new.py", kind="add"),
                MockFileUpdateChange(path="src/old.py", kind="delete"),
            ]
        )
        event = MockItemCompletedEvent(item=item)
        events = self._convert(event)
        assert len(events) == 3
        assert all(e["type"] == "file_diff" for e in events)
        assert events[0]["file_path"] == "src/main.py"
        assert events[0]["action"] == "modify"
        assert events[1]["file_path"] == "src/new.py"
        assert events[1]["action"] == "create"
        assert events[2]["file_path"] == "src/old.py"
        assert events[2]["action"] == "delete"

    def test_file_change_started_no_output(self):
        item = MockFileChangeItem(changes=[MockFileUpdateChange()])
        event = MockItemStartedEvent(item=item)
        events = self._convert(event)
        assert len(events) == 0

    def test_mcp_tool_call_started(self):
        item = MockMcpToolCallItem(
            id="mcp-1",
            server="my-server",
            tool="search",
            arguments={"query": "test"},
        )
        event = MockItemStartedEvent(item=item)
        events = self._convert(event)
        assert len(events) == 1
        assert events[0]["type"] == "tool_use"
        assert events[0]["tool_name"] == "mcp:my-server/search"
        assert events[0]["tool_input"] == {"query": "test"}

    def test_mcp_tool_call_completed(self):
        item = MockMcpToolCallItem(
            id="mcp-1",
            server="srv",
            tool="fetch",
            status="completed",
            result=MockMcpToolCallItemResult(content=["result data"]),
        )
        event = MockItemCompletedEvent(item=item)
        events = self._convert(event)
        assert len(events) == 1
        assert events[0]["type"] == "tool_result"
        assert events[0]["tool_use_id"] == "mcp-1"
        assert "is_error" not in events[0]

    def test_mcp_tool_call_failed(self):
        item = MockMcpToolCallItem(
            id="mcp-2",
            server="srv",
            tool="fetch",
            status="failed",
            error=MockMcpToolCallItemError(message="timeout"),
        )
        event = MockItemCompletedEvent(item=item)
        events = self._convert(event)
        assert events[0]["is_error"] is True
        assert events[0]["content"] == "timeout"

    def test_error_item(self):
        item = MockErrorItem(message="Something broke")
        event = MockItemCompletedEvent(item=item)
        events = self._convert(event)
        assert len(events) == 1
        assert events[0]["type"] == "error"
        assert events[0]["error"] == "Something broke"

    def test_unknown_event_type(self):
        @dataclass
        class UnknownEvent:
            type: str = "unknown"

        events = self._convert(UnknownEvent())
        assert events == []


class TestCodexSDKCapabilities:
    def test_capabilities(self):
        with _patch_codex_modules():
            from agentabi.providers.codex_sdk import CodexSDKProvider

            caps = CodexSDKProvider().capabilities()
            assert caps["agent_type"] == "codex"
            assert caps["supports_streaming"] is True
            assert caps["supports_mcp"] is True
