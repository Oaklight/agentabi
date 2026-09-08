# IR Events

All agent output is normalized into a common set of Intermediate Representation (IR) events. These are Python `TypedDict` dictionaries with a `type` discriminator field.

## Event Types

### SessionStartEvent

Emitted when a session begins.

```python
{
    "type": "session_start",
    "session_id": "sess-abc123",
    "agent": "claude_code",
    "model": "claude-sonnet-4-20250514",  # optional
    "working_dir": "/path/to/project",     # optional
}
```

### MessageStartEvent

Marks the beginning of an assistant message turn.

```python
{
    "type": "message_start",
    "role": "assistant",
    "message_id": "msg_001",  # optional
}
```

### MessageDeltaEvent

A streamed text chunk from the assistant.

```python
{
    "type": "message_delta",
    "text": "The answer is ",
}
```

### MessageEndEvent

Marks the end of an assistant message turn.

```python
{
    "type": "message_end",
    "text": "Full response text",    # optional (some agents include full text here)
    "stop_reason": "end_turn",       # optional
    "message_id": "msg_001",         # optional
}
```

#
## Content Block Events

Track boundaries between different content types within a message (text, thinking, tool_use). Aligned with llm-rosetta's `ContentBlockStartEvent`/`ContentBlockEndEvent`.

```python
class ContentBlockStartEvent(TypedDict):
    type: Required[Literal["content_block_start"]]
    block_index: Required[int]
    block_type: Required[str]  # "text", "thinking", "tool_use"

class ContentBlockEndEvent(TypedDict):
    type: Required[Literal["content_block_end"]]
    block_index: Required[int]
```

Emitted by:

- **Claude**: from `content_block_start`/`content_block_stop` stream events
- **Pi**: from `thinking_start`/`thinking_end`/`text_start`/`text_end` message update subtypes

## Reasoning Events

Capture the model's internal reasoning/thinking process. Aligned with llm-rosetta's `ReasoningDeltaEvent`.

```python
class ReasoningDeltaEvent(TypedDict):
    type: Required[Literal["reasoning_delta"]]
    reasoning: Required[str]
    block_index: NotRequired[int]
```

Emitted by:

- **Claude**: from `thinking_delta` in streaming, and `thinking` content blocks in non-streaming
- **Pi**: from `thinking_delta` message update subtypes

Previously, thinking data was silently dropped by both providers. Now it flows through to consumers via this event type.

## ToolUseEvent

An agent invokes a tool.

```python
{
    "type": "tool_use",
    "tool_use_id": "call_xyz",
    "tool_name": "read_file",
    "tool_input": {"path": "main.py"},
}
```

### ToolResultEvent

Result from a tool execution.

```python
{
    "type": "tool_result",
    "tool_use_id": "call_xyz",
    "content": "file contents here...",
    "is_error": False,          # optional, default False
    "duration_ms": 150,         # optional
}
```

### UsageEvent

Token usage statistics for the session or turn.

```python
{
    "type": "usage",
    "usage": {
        "input_tokens": 1500,
        "output_tokens": 200,
        "total_tokens": 1700,
        "cache_read_tokens": 500,       # optional
        "cache_creation_tokens": 100,   # optional
    },
    "cost_usd": 0.0042,  # optional
}
```

### ErrorEvent

An error occurred during execution.

```python
{
    "type": "error",
    "error": "Rate limit exceeded",
    "is_fatal": True,   # optional
    "code": "rate_limit", # optional
}
```

### FileDiffEvent

A file was created, modified, or deleted.

```python
{
    "type": "file_diff",
    "file_path": "src/main.py",
    "action": "modify",  # "create" | "modify" | "delete"
    "diff": "...",        # optional
}
```

### SessionEndEvent

Session has completed.

```python
{
    "type": "session_end",
    "session_id": "sess-abc123",  # optional
}
```

### PermissionRequestEvent / PermissionResponseEvent

Permission flow events (used by agents with approval modes).

```python
# Request
{
    "type": "permission_request",
    "permission": {
        "tool": "write_file",
        "description": "Write to main.py",
    },
}

# Response
{
    "type": "permission_response",
    "granted": True,
}
```

## Union Type

All events are part of the `IREvent` union type:

```python
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
```

## Type Guards

agentabi provides type guard functions for narrowing event types:

```python
from agentabi.types.ir.type_guards import is_message_delta, is_tool_use

async for event in session.stream(prompt="..."):
    if is_message_delta(event):
        print(event["text"])  # type-safe access
    elif is_tool_use(event):
        print(event["tool_name"])
```
