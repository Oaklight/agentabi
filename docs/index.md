---
title: Home
hide:
  - navigation
---

# agentabi

**A unified Python interface for driving coding agent CLIs.**

agentabi wraps multiple coding agent CLIs — [Claude Code](https://github.com/anthropics/claude-code), [Codex](https://github.com/openai/codex), [Gemini CLI](https://github.com/google-gemini/gemini-cli), and [OpenCode](https://github.com/opencode-ai/opencode) — behind a single async Python API with streaming support.

## Why agentabi?

Each coding agent CLI has its own invocation method, output format, and event model. agentabi provides:

- **Unified API** — One `Session` class to rule them all. Switch agents with a single parameter.
- **Intermediate Representation** — All agent events are normalized into a common IR event stream, making cross-agent tooling possible.
- **Streaming** — Real-time event streaming from any supported agent.
- **Auto-detection** — Automatically discovers which agent CLIs are installed.
- **Provider fallback** — Native (subprocess) and SDK providers with automatic fallback chains.

## Quick Example

```python
import asyncio
from agentabi import Session, detect_agents

async def main():
    # Discover available agents
    agents = detect_agents()
    print(f"Available: {agents}")

    # Run a task
    session = Session(agent="claude_code")
    result = await session.run(prompt="What is 2+2?", max_turns=2)
    print(result["result_text"])

    # Stream events
    async for event in session.stream(prompt="Explain asyncio"):
        if event["type"] == "message_delta":
            print(event["text"], end="", flush=True)

asyncio.run(main())
```

## Supported Agents

| Agent | Provider Types | Transport |
|-------|---------------|-----------|
| Claude Code | Native (subprocess) + SDK | subprocess / SDK |
| Codex | Native (subprocess) + SDK | subprocess / SDK |
| Gemini CLI | Native (subprocess) + SDK | subprocess / SDK |
| OpenCode | Native (subprocess) | subprocess |

## Getting Started

- [Installation](usage/installation.md) — Install agentabi and optional dependencies
- [Quick Start](usage/quickstart.md) — Run your first task in 5 minutes
- [Streaming](usage/streaming.md) — Handle real-time event streams
- [Architecture](architecture.md) — Understand the provider model and IR design
