# Streaming

agentabi supports real-time event streaming from any agent. The `Session.stream()` method yields IR events as they are produced.

## Basic Streaming

```python
import asyncio
from agentabi import Session

async def main():
    session = Session(agent="claude_code")

    async for event in session.stream(prompt="Explain asyncio briefly"):
        etype = event["type"]

        if etype == "session_start":
            print(f"Session: {event.get('session_id')}")

        elif etype == "message_delta":
            print(event["text"], end="", flush=True)

        elif etype == "message_end":
            print()  # newline after message

        elif etype == "tool_use":
            print(f"\n[Tool] {event['tool_name']}({event['tool_input']})")

        elif etype == "tool_result":
            status = "ERR" if event.get("is_error") else "OK"
            print(f"  [{status}] {event['content'][:100]}")

        elif etype == "usage":
            u = event["usage"]
            print(f"\nTokens: {u.get('input_tokens', 0)} in / {u.get('output_tokens', 0)} out")

        elif etype == "error":
            print(f"\nERROR: {event['error']}")

        elif etype == "session_end":
            print("--- session end ---")

asyncio.run(main())
```

## Event Types

All agents produce a common set of IR events:

| Event Type | Description | Key Fields |
|-----------|-------------|------------|
| `session_start` | Session initialized | `session_id`, `agent`, `model` |
| `message_start` | Assistant turn begins | `role` |
| `message_delta` | Streamed text chunk | `text` |
| `message_end` | Assistant turn ends | `text` (optional full text), `stop_reason` |
| `tool_use` | Tool invocation | `tool_use_id`, `tool_name`, `tool_input` |
| `tool_result` | Tool output | `tool_use_id`, `content`, `is_error` |
| `usage` | Token usage stats | `usage` (dict), `cost_usd` |
| `error` | Error occurred | `error`, `is_fatal` |
| `file_diff` | File modification | `file_path`, `action` |
| `session_end` | Session completed | `session_id` |

## Event Flow

A typical event sequence looks like:

```
session_start
  message_start (role=assistant)
    message_delta (text chunk)
    message_delta (text chunk)
    ...
  message_end
  usage
session_end
```

With tool use:

```
session_start
  message_start
    tool_use (name=read_file, input={path: "main.py"})
    tool_result (content="file contents...")
    message_delta (text)
  message_end
  usage
session_end
```

## Collecting Full Text

To accumulate the complete response text from a stream:

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

## Full Example

See [`examples/streaming.py`](https://github.com/oaklight/agentabi/blob/master/examples/streaming.py) for a complete streaming example.
