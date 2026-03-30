# 流式输出

agentabi 支持从任意 agent 实时流式获取事件。`Session.stream()` 方法按事件产生的顺序逐个 yield IR 事件。

## 基本流式使用

```python
import asyncio
from agentabi import Session

async def main():
    session = Session(agent="claude_code")

    async for event in session.stream(prompt="Explain asyncio briefly"):
        etype = event["type"]

        if etype == "session_start":
            print(f"会话: {event.get('session_id')}")

        elif etype == "message_delta":
            print(event["text"], end="", flush=True)

        elif etype == "message_end":
            print()  # 消息结束换行

        elif etype == "tool_use":
            print(f"\n[工具] {event['tool_name']}({event['tool_input']})")

        elif etype == "tool_result":
            status = "错误" if event.get("is_error") else "成功"
            print(f"  [{status}] {event['content'][:100]}")

        elif etype == "usage":
            u = event["usage"]
            print(f"\nToken: {u.get('input_tokens', 0)} 输入 / {u.get('output_tokens', 0)} 输出")

        elif etype == "error":
            print(f"\n错误: {event['error']}")

        elif etype == "session_end":
            print("--- 会话结束 ---")

asyncio.run(main())
```

## 事件类型

所有 agent 产生统一的 IR 事件：

| 事件类型 | 描述 | 关键字段 |
|---------|------|---------|
| `session_start` | 会话初始化 | `session_id`, `agent`, `model` |
| `message_start` | 助手回复开始 | `role` |
| `message_delta` | 流式文本片段 | `text` |
| `message_end` | 助手回复结束 | `text`（可选完整文本）, `stop_reason` |
| `tool_use` | 工具调用 | `tool_use_id`, `tool_name`, `tool_input` |
| `tool_result` | 工具输出 | `tool_use_id`, `content`, `is_error` |
| `usage` | Token 使用统计 | `usage`（字典）, `cost_usd` |
| `error` | 发生错误 | `error`, `is_fatal` |
| `file_diff` | 文件修改 | `file_path`, `action` |
| `session_end` | 会话完成 | `session_id` |

## 事件流程

典型的事件序列：

```
session_start
  message_start (role=assistant)
    message_delta (文本片段)
    message_delta (文本片段)
    ...
  message_end
  usage
session_end
```

包含工具调用时：

```
session_start
  message_start
    tool_use (name=read_file, input={path: "main.py"})
    tool_result (content="文件内容...")
    message_delta (文本)
  message_end
  usage
session_end
```

## 收集完整文本

从流中累积完整的回复文本：

```python
text_parts = []
async for event in session.stream(prompt="..."):
    if event["type"] == "message_delta":
        text_parts.append(event["text"])
    elif event["type"] == "message_end":
        end_text = event.get("text", "")
        if end_text:
            text_parts.append(end_text)

full_text = "".join(text_parts)
```

## 完整示例

参见 [`examples/streaming.py`](https://github.com/oaklight/agentabi/blob/master/examples/streaming.py)。
