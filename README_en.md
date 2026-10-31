# agentabi

[![PyPI version](https://img.shields.io/pypi/v/agentabi?color=green)](https://pypi.org/project/agentabi/)
[![GitHub release](https://img.shields.io/github/v/release/Oaklight/agentabi?color=green)](https://github.com/Oaklight/agentabi/releases/latest)
[![CI](https://github.com/Oaklight/agentabi/actions/workflows/ci.yml/badge.svg)](https://github.com/Oaklight/agentabi/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)

Unified interface layer for agentic coding CLIs.

One interface. Any coding agent.

## What is agentabi?

`agentabi` provides a stable, unified interface (an "ABI") for interacting with different agentic coding CLIs. Write your integration once, swap agents with a config change.

### Supported Agents

| Agent | Provider | Status |
|-------|----------|--------|
| [Claude Code](https://github.com/anthropics/claude-code) | Anthropic | Implemented |
| [Codex](https://github.com/openai/codex) | OpenAI | Implemented |
| [Antigravity (agy)](https://github.com/google/anthropic-agy) | Google | Implemented |
| [OpenCode](https://github.com/opencode-ai/opencode) | Community | Implemented |
| [Pi](https://github.com/anthropics/pi) | Community | Implemented |
| ~~Gemini CLI~~ | Google | Deprecated (use agy) |

## Installation

```bash
pip install agentabi
```

> **Note:** You also need at least one agent CLI installed: `claude`, `codex`, `agy`, `opencode`, or `pi`.

### Optional SDK dependencies

```bash
pip install agentabi[claude]    # Claude Agent SDK
pip install agentabi[codex]     # Codex SDK
```

## Quick Start

```python
import asyncio
from agentabi import Session

async def main():
    session = Session(agent="claude_code")
    result = await session.run(prompt="What is 2+2?")
    print(result["result_text"])

asyncio.run(main())
```

### Claude subscription quota (PTY transport)

By default Claude Code runs as `claude -p`. Anthropic classifies that
entrypoint as `sdk-cli` and counts it at roughly twice the interactive
`cli` mode's allowance. To drive the real interactive TUI instead:

```python
session = Session(agent="claude_code", prefer="pty")
```

The PTY provider is opt-in and POSIX-only. It waits for the TUI prompt,
pastes the message, and tails the session transcript, so it emits whole
messages rather than token deltas. If Claude Code asks to trust the
folder or is not signed in, it returns a clear error instead of changing
your configuration.

The ACP gateway takes the same choice:

```bash
agentabi acp --agent claude --prefer pty
```

## ACP gateway

Expose agentabi as a single ACP endpoint. The gateway speaks ACP to the client and delegates to each agent's native interface:

```bash
pip install agentabi
agentabi acp --agent opencode
```

The ACP transport is vendored from [zerodep](https://github.com/Oaklight/zerodep) (`acp` + `jsonrpc`), so no external ACP SDK is required. Current backends: `opencode` (native ACP) and `agy`, `claude`, `codex`, `pi` (via the agentabi IR stream). More backends are tracked in the [ACP gateway epic](https://github.com/Oaklight/agentabi/issues/43).

Any CLI that ships an ACP server can be used without an agentabi provider:

```bash
agentabi acp --agent acp --acp-command "npx -y @agentclientprotocol/claude-agent-acp"
```

The gateway speaks ACP to that process and forwards its `session/update` payloads unchanged.

## Key Features

### Unified TaskConfig

All agents accept the same `TaskConfig` with fields for:

- **Agent selection**: `agent`, `model`
- **Reasoning control**: `thinking_level` (`"off"`, `"low"`, `"medium"`, `"high"`, `"max"`)
- **Structured output**: `output_schema` (JSON Schema)
- **Session management**: `session_id`, `resume`, `ephemeral`
- **Workspace**: `working_dir`, `additional_dirs`, `files`
- **Permissions**: `permissions`, `allowed_tools`, `disallowed_tools`
- **System prompts**: `system_prompt`, `append_system_prompt`

### Unified IR Events

All agents emit the same streaming events:

- `session_start` / `session_end` — session lifecycle
- `message_start` / `message_delta` / `message_end` — text streaming
- `content_block_start` / `content_block_end` — content block boundaries
- `reasoning_delta` — thinking/reasoning text (Claude, Pi)
- `tool_use` / `tool_result` — tool calls
- `usage` — token counts (including `reasoning_tokens`)
- `error` — error reporting

## Documentation

- [English docs](https://agentabi-en.readthedocs.io/)
- [中文文档](https://agentabi-zh.readthedocs.io/)

## License

MIT
