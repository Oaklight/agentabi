"""
agentabi - Claude Code Native Event Types

TypedDict definitions for Claude Code's streaming JSONL output.
Based on `claude -p --output-format stream-json --verbose`.

Top-level message types:
    system  — session init metadata
    assistant — model response (text + tool_use blocks)
    user — tool results
    stream_event — token-level streaming deltas
    result — final session result
"""

from typing import Any, Dict, List, Literal, Optional, Union

from typing_extensions import NotRequired, Required, TypedDict

# ============================================================================
# Content block types (shared by assistant and user messages)
# ============================================================================


class ClaudeTextBlock(TypedDict):
    type: Required[Literal["text"]]
    text: Required[str]


class ClaudeToolUseBlock(TypedDict):
    type: Required[Literal["tool_use"]]
    id: Required[str]
    name: Required[str]
    input: Required[Dict[str, Any]]


class ClaudeToolResultBlock(TypedDict):
    type: Required[Literal["tool_result"]]
    tool_use_id: Required[str]
    content: Required[str]
    is_error: NotRequired[bool]


ClaudeContentBlock = Union[ClaudeTextBlock, ClaudeToolUseBlock, ClaudeToolResultBlock]


# ============================================================================
# Usage info
# ============================================================================


class ClaudeUsage(TypedDict):
    input_tokens: NotRequired[int]
    output_tokens: NotRequired[int]
    cache_creation_input_tokens: NotRequired[int]
    cache_read_input_tokens: NotRequired[int]
    service_tier: NotRequired[str]


# ============================================================================
# Top-level message types
# ============================================================================


class ClaudeSystemMessage(TypedDict):
    """System init message emitted at session start."""

    type: Required[Literal["system"]]
    subtype: Required[Literal["init"]]
    session_id: Required[str]
    model: NotRequired[str]
    cwd: NotRequired[str]
    tools: NotRequired[List[str]]
    mcp_servers: NotRequired[List[Dict[str, Any]]]
    permissionMode: NotRequired[str]
    claude_code_version: NotRequired[str]


class ClaudeAssistantMessage(TypedDict):
    """Assistant response message with content blocks."""

    type: Required[Literal["assistant"]]
    message: Required[Dict[str, Any]]  # contains content, model, usage, etc.
    session_id: NotRequired[str]
    parent_tool_use_id: NotRequired[Optional[str]]


class ClaudeUserMessage(TypedDict):
    """User message (typically tool results)."""

    type: Required[Literal["user"]]
    message: Required[Dict[str, Any]]  # contains content (tool_result blocks)
    session_id: NotRequired[str]
    tool_use_result: NotRequired[Dict[str, Any]]


class ClaudeStreamEvent(TypedDict):
    """Streaming event wrapping Anthropic API SSE events."""

    type: Required[Literal["stream_event"]]
    event: Required[Dict[str, Any]]  # Anthropic SSE event (content_block_delta, etc.)
    session_id: NotRequired[str]


class ClaudeResultMessage(TypedDict):
    """Final result message at session end."""

    type: Required[Literal["result"]]
    subtype: Required[
        str
    ]  # "success", "error_max_turns", "error_during_execution", etc.
    is_error: Required[bool]
    result: NotRequired[str]
    session_id: NotRequired[str]
    duration_ms: NotRequired[int]
    duration_api_ms: NotRequired[int]
    num_turns: NotRequired[int]
    total_cost_usd: NotRequired[float]
    usage: NotRequired[ClaudeUsage]
    errors: NotRequired[List[str]]
    permission_denials: NotRequired[List[Dict[str, Any]]]


# ============================================================================
# Union type
# ============================================================================

ClaudeNativeEvent = Union[
    ClaudeSystemMessage,
    ClaudeAssistantMessage,
    ClaudeUserMessage,
    ClaudeStreamEvent,
    ClaudeResultMessage,
]

__all__ = [
    "ClaudeTextBlock",
    "ClaudeToolUseBlock",
    "ClaudeToolResultBlock",
    "ClaudeContentBlock",
    "ClaudeUsage",
    "ClaudeSystemMessage",
    "ClaudeAssistantMessage",
    "ClaudeUserMessage",
    "ClaudeStreamEvent",
    "ClaudeResultMessage",
    "ClaudeNativeEvent",
]
