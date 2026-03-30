# Examples

## Quick Start

The [`examples/quickstart.py`](https://github.com/oaklight/agentabi/blob/master/examples/quickstart.py) script demonstrates the core workflow:

1. **Discover** available agents with `detect_agents()`
2. **Inspect** capabilities with `get_agent_capabilities()`
3. **Run** a task with `Session.run()`
4. **Display** the result (status, text, tokens, cost)

```bash
# Use auto-detected agent
python examples/quickstart.py

# Specify agent
python examples/quickstart.py --agent codex

# Custom prompt
python examples/quickstart.py --agent opencode --prompt "List files"
```

## Streaming

The [`examples/streaming.py`](https://github.com/oaklight/agentabi/blob/master/examples/streaming.py) script demonstrates real-time event streaming:

1. **Connect** to an agent via `Session`
2. **Stream** events with `session.stream()`
3. **Handle** each event type (text deltas, tool calls, usage, errors)

```bash
python examples/streaming.py
python examples/streaming.py --agent codex --prompt "Explain asyncio"
```

### Event Types Demonstrated

| Event | Handling |
|-------|---------|
| `session_start` | Print session ID and model |
| `message_delta` | Print text chunks in real time |
| `message_end` | Print newline |
| `tool_use` | Print tool name and input |
| `tool_result` | Print output preview (OK/ERR) |
| `usage` | Print token counts and cost |
| `error` | Print error message |
| `session_end` | Print end marker |
