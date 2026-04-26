# agentabi

[![PyPI version](https://img.shields.io/pypi/v/agentabi?color=green)](https://pypi.org/project/agentabi/)
[![GitHub release](https://img.shields.io/github/v/release/Oaklight/agentabi?color=green)](https://github.com/Oaklight/agentabi/releases/latest)
[![CI](https://github.com/Oaklight/agentabi/actions/workflows/ci.yml/badge.svg)](https://github.com/Oaklight/agentabi/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/Oaklight/agentabi)

Unified interface layer for agentic coding CLIs.

One interface. Any coding agent.

## What is agentabi?

`agentabi` provides a stable, unified interface (an "ABI") for interacting with different agentic coding CLIs. Write your integration once, swap agents with a config change.

### Supported Agents

| Agent | Provider | Status |
|-------|----------|--------|
| [Claude Code](https://github.com/anthropics/claude-code) | Anthropic | Implemented |
| [Codex](https://github.com/openai/codex) | OpenAI | Implemented |
| [Gemini CLI](https://github.com/google-gemini/gemini-cli) | Google | Implemented |
| [OpenCode](https://opencode.ai) | Open Source | Implemented |

## Installation

```bash
pip install agentabi
```

With optional SDK integrations:

```bash
pip install agentabi[claude]   # Claude Code SDK support
pip install agentabi[codex]    # Codex SDK support
pip install agentabi[gemini]   # Gemini CLI SDK support
pip install agentabi[all]      # All optional SDKs
```

> **Note:** Each agent's CLI must be installed separately (e.g., `claude`, `codex`, `gemini`, `opencode`).

## Quick Start

### Run a task

```python
import asyncio
from agentabi import Session

async def main():
    session = Session(agent="claude_code")
    result = await session.run(prompt="Fix the bug in auth.py")
    print(result["status"])       # "success"
    print(result["result_text"])  # agent's response

asyncio.run(main())
```

### Stream events

```python
async for event in session.stream(prompt="Explain this code"):
    if event["type"] == "message_delta":
        print(event["text"], end="")
```

### Sync convenience

```python
from agentabi import run_sync

result = run_sync(prompt="List Python files", agent="codex")
```

### Discover available agents

```python
from agentabi import detect_agents, get_agent_capabilities

agents = detect_agents()          # ["claude_code", "codex", ...]
caps = get_agent_capabilities("claude_code")
print(caps["supports_streaming"]) # True
```

## Use Cases

- **Fleet Management** — Unified entry point for managing multiple coding agents
- **Agent-to-Agent Calls** — Translation layer for inter-agent invocation
- **Benchmarking** — Run the same task across agents, compare results
- **Fallback & Routing** — Automatic failover and cost-aware routing
- **Middleware Pipeline** — Inject logging, metering, security scanning, audit trails
- **CI/CD Integration** — Vendor-agnostic agent integration for pipelines

## Ecosystem

`agentabi` is part of a layered stack:

```
agentabi  →  Agent CLI unified interface  →  like an OS ABI
llmir     →  LLM API format conversion    →  like a compiler IR
```

- [llmir](https://github.com/Oaklight/llmir) — LLM Intermediate Representation for converting between LLM provider API formats (OpenAI, Anthropic, Google)

## License

[MIT](LICENSE)
