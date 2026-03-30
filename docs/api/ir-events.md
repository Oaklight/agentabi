# IR 事件

所有 agent 的输出被归一化为一组通用的中间表示（IR）事件。这些是带有 `type` 判别字段的 Python `TypedDict` 字典。

## 事件类型

### SessionStartEvent

会话开始时发出。

```python
{
    "type": "session_start",
    "session_id": "sess-abc123",
    "agent": "claude_code",
    "model": "claude-sonnet-4-20250514",  # 可选
    "working_dir": "/path/to/project",     # 可选
}
```

### MessageStartEvent

标记助手消息回合的开始。

```python
{
    "type": "message_start",
    "role": "assistant",
    "message_id": "msg_001",  # 可选
}
```

### MessageDeltaEvent

来自助手的流式文本片段。

```python
{
    "type": "message_delta",
    "text": "答案是 ",
}
```

### MessageEndEvent

标记助手消息回合的结束。

```python
{
    "type": "message_end",
    "text": "完整回复文本",       # 可选（部分 agent 在此包含完整文本）
    "stop_reason": "end_turn",  # 可选
    "message_id": "msg_001",    # 可选
}
```

### ToolUseEvent

Agent 调用工具。

```python
{
    "type": "tool_use",
    "tool_use_id": "call_xyz",
    "tool_name": "read_file",
    "tool_input": {"path": "main.py"},
}
```

### ToolResultEvent

工具执行的结果。

```python
{
    "type": "tool_result",
    "tool_use_id": "call_xyz",
    "content": "文件内容...",
    "is_error": False,          # 可选，默认 False
    "duration_ms": 150,         # 可选
}
```

### UsageEvent

会话或回合的 token 使用统计。

```python
{
    "type": "usage",
    "usage": {
        "input_tokens": 1500,
        "output_tokens": 200,
        "total_tokens": 1700,
        "cache_read_tokens": 500,       # 可选
        "cache_creation_tokens": 100,   # 可选
    },
    "cost_usd": 0.0042,  # 可选
}
```

### ErrorEvent

执行过程中发生错误。

```python
{
    "type": "error",
    "error": "Rate limit exceeded",
    "is_fatal": True,     # 可选
    "code": "rate_limit", # 可选
}
```

### FileDiffEvent

文件被创建、修改或删除。

```python
{
    "type": "file_diff",
    "file_path": "src/main.py",
    "action": "modify",  # "create" | "modify" | "delete"
    "diff": "...",        # 可选
}
```

### SessionEndEvent

会话已完成。

```python
{
    "type": "session_end",
    "session_id": "sess-abc123",  # 可选
}
```

### PermissionRequestEvent / PermissionResponseEvent

权限流事件（用于有审批模式的 agent）。

```python
# 请求
{
    "type": "permission_request",
    "permission": {
        "tool": "write_file",
        "description": "写入 main.py",
    },
}

# 响应
{
    "type": "permission_response",
    "granted": True,
}
```

## 联合类型

所有事件属于 `IREvent` 联合类型：

```python
IREvent = Union[
    SessionStartEvent,
    SessionEndEvent,
    MessageStartEvent,
    MessageDeltaEvent,
    MessageEndEvent,
    ToolUseEvent,
    ToolResultEvent,
    PermissionRequestEvent,
    PermissionResponseEvent,
    UsageEvent,
    ErrorEvent,
    FileDiffEvent,
]
```

## 类型守卫

agentabi 提供类型守卫函数用于事件类型收窄：

```python
from agentabi.types.ir.type_guards import is_message_delta, is_tool_use

async for event in session.stream(prompt="..."):
    if is_message_delta(event):
        print(event["text"])  # 类型安全访问
    elif is_tool_use(event):
        print(event["tool_name"])
```
