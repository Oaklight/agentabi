"""
agentabi - IR Event Types

Defines all streaming event types emitted by agent adapters.
Analogous to llmir's IRStreamEvent.

Event categories:
    - Session lifecycle: session_start, session_end
    - Message streaming: message_start, message_delta, message_end
    - Content blocks: content_block_start, content_block_end
    - Reasoning: reasoning_delta
    - Tool calls: tool_use, tool_result
    - Permissions: permission_request, permission_response
    - Usage/errors: usage, error
    - File changes: file_diff
"""

from typing import Any, Literal, Union

from typing_extensions import NotRequired, Required, TypedDict

# ============================================================================
# Session lifecycle events
# ============================================================================


class SessionStartEvent(TypedDict):
    """Emitted when an agent session begins.

    Carries session-level metadata such as the session ID, model, and
    available tools.
    """

    type: Required[Literal["session_start"]]
    session_id: Required[str]
    agent: NotRequired[str]  # agent type identifier
    model: NotRequired[str]
    tools: NotRequired[list[str]]
    working_dir: NotRequired[str]


class SessionEndEvent(TypedDict):
    """Emitted when an agent session ends."""

    type: Required[Literal["session_end"]]
    session_id: NotRequired[str]


# ============================================================================
# Message streaming events
# ============================================================================


class MessageStartEvent(TypedDict):
    """Emitted when the agent begins a new message."""

    type: Required[Literal["message_start"]]
    role: NotRequired[str]  # "assistant" typically
    message_id: NotRequired[str]


class MessageDeltaEvent(TypedDict):
    """Emitted when a new text fragment arrives from the agent.

    This is the primary event for streaming text output.
    """

    type: Required[Literal["message_delta"]]
    text: Required[str]
    message_id: NotRequired[str]


class MessageEndEvent(TypedDict):
    """Emitted when the agent finishes a message.

    Includes the complete message text if available.
    """

    type: Required[Literal["message_end"]]
    text: NotRequired[str]  # full accumulated text
    stop_reason: NotRequired[str]
    message_id: NotRequired[str]


# ============================================================================
# Content block events (aligned with llm-rosetta ContentBlockStartEvent/EndEvent)
# ============================================================================


class ContentBlockStartEvent(TypedDict):
    """Emitted when a content block begins within a message.

    Tracks boundaries between different content types (text, thinking, tool_use).
    """

    type: Required[Literal["content_block_start"]]
    block_index: Required[int]
    block_type: Required[str]  # "text", "thinking", "tool_use"


class ContentBlockEndEvent(TypedDict):
    """Emitted when a content block ends."""

    type: Required[Literal["content_block_end"]]
    block_index: Required[int]


# ============================================================================
# Reasoning events (aligned with llm-rosetta ReasoningDeltaEvent)
# ============================================================================


class ReasoningDeltaEvent(TypedDict):
    """Emitted when a reasoning/thinking text fragment arrives.

    Captures the model's internal reasoning process (e.g., Claude's
    extended thinking, Pi's thinking deltas).
    """

    type: Required[Literal["reasoning_delta"]]
    reasoning: Required[str]
    block_index: NotRequired[int]


# ============================================================================
# Tool call events
# ============================================================================


class ToolUseEvent(TypedDict):
    """Emitted when the agent invokes a tool.

    Contains the tool name and input parameters.
    """

    type: Required[Literal["tool_use"]]
    tool_use_id: Required[str]
    tool_name: Required[str]
    tool_input: Required[dict[str, Any]]


class ToolResultEvent(TypedDict):
    """Emitted when a tool returns its result.

    Contains the tool result content and whether it was an error.
    """

    type: Required[Literal["tool_result"]]
    tool_use_id: Required[str]
    content: Required[str]
    is_error: NotRequired[bool]
    duration_ms: NotRequired[int]


# ============================================================================
# Permission events
# ============================================================================


class PermissionRequestEvent(TypedDict):
    """Emitted when the agent requests permission for an action.

    The consumer can respond by sending a PermissionResponse.
    """

    type: Required[Literal["permission_request"]]
    tool_name: Required[str]
    tool_use_id: Required[str]
    tool_input: NotRequired[dict[str, Any]]
    description: NotRequired[str]


class PermissionResponseEvent(TypedDict):
    """Emitted when a permission decision is made."""

    type: Required[Literal["permission_response"]]
    tool_use_id: Required[str]
    granted: Required[bool]


# ============================================================================
# Usage and error events
# ============================================================================


class UsageInfo(TypedDict):
    """Token usage statistics."""

    input_tokens: NotRequired[int]
    output_tokens: NotRequired[int]
    cache_read_tokens: NotRequired[int]
    cache_creation_tokens: NotRequired[int]
    total_tokens: NotRequired[int]
    reasoning_tokens: NotRequired[int]


class UsageEvent(TypedDict):
    """Emitted with token usage statistics."""

    type: Required[Literal["usage"]]
    usage: Required[UsageInfo]
    cost_usd: NotRequired[float]
    model: NotRequired[str]


class ErrorEvent(TypedDict):
    """Emitted when an error occurs during execution."""

    type: Required[Literal["error"]]
    error: Required[str]
    is_fatal: NotRequired[bool]


# ============================================================================
# File change events
# ============================================================================


class FileDiffEvent(TypedDict):
    """Emitted when a file is created, modified, or deleted.

    Provides a unified diff representation of file changes.
    """

    type: Required[Literal["file_diff"]]
    file_path: Required[str]
    action: Required[Literal["create", "modify", "delete"]]
    diff: NotRequired[str]  # unified diff format
    content: NotRequired[str]  # new content (for create)


# ============================================================================
# Union type
# ============================================================================

IREvent = Union[
    SessionStartEvent,
    SessionEndEvent,
    MessageStartEvent,
    MessageDeltaEvent,
    MessageEndEvent,
    ContentBlockStartEvent,
    ContentBlockEndEvent,
    ReasoningDeltaEvent,
    ToolUseEvent,
    ToolResultEvent,
    PermissionRequestEvent,
    PermissionResponseEvent,
    UsageEvent,
    ErrorEvent,
    FileDiffEvent,
]

# ============================================================================
# Exports
# ============================================================================

__all__ = [
    "SessionStartEvent",
    "SessionEndEvent",
    "MessageStartEvent",
    "MessageDeltaEvent",
    "MessageEndEvent",
    "ContentBlockStartEvent",
    "ContentBlockEndEvent",
    "ReasoningDeltaEvent",
    "ToolUseEvent",
    "ToolResultEvent",
    "PermissionRequestEvent",
    "PermissionResponseEvent",
    "UsageInfo",
    "UsageEvent",
    "ErrorEvent",
    "FileDiffEvent",
    "IREvent",
]
