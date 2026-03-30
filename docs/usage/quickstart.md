# Quick Start

This guide walks through the basic agentabi workflow: detect agents, run a task, and inspect the result.

## 1. Detect Available Agents

```python
from agentabi import detect_agents, get_agent_capabilities

agents = detect_agents()
print(f"Available agents: {agents}")
# e.g. ['claude_code', 'codex', 'gemini_cli', 'opencode']

# Inspect capabilities
for agent in agents:
    caps = get_agent_capabilities(agent)
    print(f"  {caps['name']}: streaming={caps['supports_streaming']}")
```

## 2. Run a Task

```python
import asyncio
from agentabi import Session

async def main():
    session = Session(agent="claude_code")
    result = await session.run(
        prompt="What is 2+2? Reply with just the number.",
        max_turns=2,
    )
    print(f"Status: {result.get('status')}")
    print(f"Answer: {result.get('result_text')}")
    print(f"Tokens: {result.get('usage')}")

asyncio.run(main())
```

The `Session.run()` method executes the task to completion and returns a `SessionResult` dictionary containing:

- `session_id` — Unique session identifier
- `status` — `"success"` or `"error"`
- `result_text` — The agent's text output
- `usage` — Token usage statistics
- `cost_usd` — Estimated cost (if available)

## 3. Auto-detect Agent

If you don't specify an agent, agentabi picks the first available one:

```python
session = Session()  # auto-detect
print(f"Using: {session.agent}")
```

## 4. Synchronous Convenience

For simple scripts that don't need async:

```python
from agentabi import run_sync

result = run_sync(
    prompt="Explain Python generators in one sentence.",
    agent="opencode",
    max_turns=2,
)
print(result["result_text"])
```

## 5. Full Example

See [`examples/quickstart.py`](https://github.com/oaklight/agentabi/blob/master/examples/quickstart.py) for a complete working example with argument parsing and formatted output.

## Next Steps

- [Streaming](streaming.md) — Handle real-time event streams
- [Agent Discovery](discovery.md) — Advanced discovery and capability checks
- [API Reference](../api/session.md) — Full Session API documentation
