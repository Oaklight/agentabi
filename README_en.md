# agentabi

[![PyPI version](https://img.shields.io/pypi/v/agentabi?color=green)](https://pypi.org/project/agentabi/)
[![TestPyPI](https://img.shields.io/pypi/v/agentabi?pypiBaseUrl=https://test.pypi.org&label=TestPyPI&color=orange)](https://test.pypi.org/project/agentabi/)
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
| [OpenCode](https://github.com/opencode-ai/opencode) | Community | Implemented (V2 HTTP/SSE + V1 fallback) |
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

### One endpoint setting for every agent

Point any supported CLI at a custom model endpoint through one mapping.
Each transport translates it into its own mechanism, so caller code does
not change between agents:

```python
session = Session(
    agent="codex",
    settings={
        "base_url": "https://llm.example.com/v1",
        "api_key_env": "LLM_API_KEY",
        "model_provider": "gateway",
    },
)
```

`settings` also takes `headers`, `env`, `args`, and a `native` block for
CLI-specific options. Keys travel in the child environment, never in argv.
An unsupported field raises `UnsupportedSettingsError` unless
`on_unsupported="ignore"`. See the
[settings guide](https://agentabi-en.readthedocs.io/en/latest/usage/settings/).

### Claude subscription quota (PTY transport)

By default Claude Code runs as `claude -p`. Anthropic classifies that
entrypoint as `sdk-cli` and counts it at roughly twice the interactive
`cli` mode's allowance. To drive the real interactive TUI instead:

```python
session = Session(agent="claude_code", prefer="pty")
```

The PTY provider is opt-in. It uses a POSIX PTY on Linux and macOS and the
Windows pseudoconsole (ConPTY) on Windows; both come from the vendored
`ptyx` module, so there is no extra dependency. It waits for the TUI
prompt, pastes the message, and tails the session transcript, so it emits
whole messages rather than token deltas. If Claude Code asks to trust the
folder or is not signed in, it returns a clear error instead of changing
your configuration.

The ACP gateway takes the same choice:

```bash
agentabi acp --agent claude --prefer pty
```

## OpenCode V2 transport

OpenCode 1.19.0 and later expose a second API: `opencode serve` with HTTP
commands and a server-sent event stream. agentabi uses it by default and
keeps the `opencode run --format json` V1 subprocess as a fallback.

```python
session = Session(agent="opencode")
result = await session.run(prompt="Fix the failing test")
```

- **Selection.** V2 is used when the `opencode` CLI is at least 1.19.0 or a
  server URL is configured. Otherwise the V1 provider runs. Force either
  transport with `AGENTABI_OPENCODE_TRANSPORT=v1|v2`, or per task with
  `agent_extensions={"opencode": {"transport": "v2"}}`.
- **Server.** Without a URL, the provider starts `opencode serve` on a free
  localhost port and reuses that process for later calls. Set
  `OPENCODE_BASE_URL` (or `agent_extensions={"opencode": {"base_url": ...}}`)
  to use an existing `opencode serve`, including a remote one with
  `http://user:pass@host:port`. A configured URL is authoritative: if that
  server does not answer the V2 API the task fails instead of silently
  running V1.
- **Sessions.** `session_id` with `resume=True` reuses the server session, so
  multi-turn conversations keep their context.
- **Permissions.** With `permissions={"level": "full_auto"}` requests are
  approved automatically; `accept_edits` approves each request once. Any
  other level fails the turn with a clear error, because the streaming API
  cannot prompt. Interactive questions are rejected and fail the turn.
- **Limits.** There is no system-prompt hook. `usage` and `cost_usd` are
  reported per step; a turn with tool calls emits one pair per step. The
  turn ends at `session.next.step.ended` because V2 servers do not emit
  `session.idle`.

`agentabi acp --agent opencode` is unaffected; it keeps using the native ACP
pass-through.

## Long-lived peers

`Session.run` runs one task. A peer keeps a Claude Code, Codex, or
OpenCode process alive between turns, so a coordinator can send
follow-up work, steer a running turn, or interrupt it and keep the
conversation:

```python
from agentabi.peers import PeerBroker

async with PeerBroker() as broker:
    peer = await broker.spawn("reviewer", cwd="/repo", prompt="Review the diff")
    # backend="codex" or backend="opencode" for the other CLIs
    await peer.send("Also check the tests")   # new turn, or steer if busy
    turn = await peer.wait(timeout=300)
    print(turn.status, turn.text)
```

`peerctl` drives the same runtime across processes: `spawn`, `send`,
`steer`, `interrupt`, `wait`, `inbox`, plus `attach` for a session
another process started and `recover` after the daemon stops. See the
[peers guide](https://agentabi-en.readthedocs.io/en/latest/usage/peers/).

## ACP gateway

Expose agentabi as a single ACP endpoint. The gateway speaks ACP to the client and delegates to each agent's native interface:

```bash
pip install agentabi
agentabi acp --agent opencode
```

The ACP transport is vendored from [zerodep](https://github.com/Oaklight/zerodep) (`acp` + `jsonrpc`), so no external ACP SDK is required. Current backends: `opencode` (native ACP); `codex-app-server` (JSON-RPC to `codex app-server`); and `agy`, `claude`, `codex`, `pi` (via the agentabi IR stream). More backends are tracked in the [ACP gateway epic](https://github.com/Oaklight/agentabi/issues/43).

Any CLI that ships an ACP server can be used without an agentabi provider:

```bash
agentabi acp --agent acp --acp-command "npx -y @agentclientprotocol/claude-agent-acp"
```

The gateway speaks ACP to that process and forwards its `session/update` payloads unchanged.

### Permissions

`session/new` returns a `permission_mode` config option (`ask`, `deny`,
`allow_all`). `session/set_config_option` changes it for the session.

The Claude backend (`agentabi acp --agent claude`) and ACP pass-through
backends (`opencode`, `--agent acp`) can ask: a request reaches the client as
`session/request_permission` and the answer is written back. Backends without a
permission channel (`codex`, `agy`) still get `deny` and `allow_all`, which the
gateway enforces with the provider's own flags (codex runs read-only with no
approvals; agy drops its skip-permissions flag). `pi` has no such flags, so it
gets no option. The gateway never turns a missing channel into an allow.

Use `--permission-timeout SECONDS` to bound the wait (default `0` waits
forever) and `--permission-policy ask|deny|allow_all` to choose the initial
policy.

### Router mode

One gateway process can serve several agents. Enable more than one with
`--router` and repeated `--agent`:

```bash
agentabi acp --router --agent claude --agent opencode
```

The client picks an agent per session with `_meta.agent` in `session/new`:

```json
{"jsonrpc": "2.0", "id": 1, "method": "session/new",
 "params": {"cwd": "/work", "_meta": {"agent": "opencode"}}}
```

The first enabled agent is the default when `_meta.agent` is absent. An
unknown name fails with `-32602` (invalid params) and lists the enabled
agents. The selected agent is reported in the session config as an `agent`
option. That option is informational: ACP config options are returned by
`session/new` and set afterwards, so they cannot choose an agent at creation
time. Changing it with `session/set_config_option` returns an error; create a
new session to switch agents.

**Capability policy.** `initialize` advertises the intersection of what every
enabled and reachable backend honours, further limited by what the gateway
itself implements: `session/load`, `session/list`, `session/resume`, and
`session/delete` are never advertised, and authentication is not proxied.
A backend that cannot start is logged and left out of the intersection;
selecting it later returns a clear error. Per-agent capabilities are not
advertised because the agent is chosen after `initialize`. The negotiated
protocol version is the lowest version any reachable backend reports; IR
backends are told to emit that version, and native ACP backends are
restarted so their downstream negotiates it too.

**Permission policy.** Deny by default. Router mode fails closed: an inner
backend uses its own session id, which the client does not know, so the router
never claims a permission round trip and answers a downstream
`session/request_permission` with an explicit `cancelled`. Run one gateway per
agent (`agentabi acp --agent claude`) when the client must answer permission
requests.

**Audit.** Every routed method is recorded with method, agent, session,
duration, and outcome on the `agentabi.acp.audit` logger.

**Lifecycle.** One backend instance per agent is shared by its sessions and
stopped when its last session closes. A new session starts it again.

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
- **Endpoint and auth**: `settings` (`base_url`, `api_key_env`, `model_provider`, `headers`, `env`, `args`, `native`)

### Unified IR Events

All agents emit the same streaming events:

- `session_start` / `session_end` — session lifecycle
- `message_start` / `message_delta` / `message_end` — text streaming
- `content_block_start` / `content_block_end` — content block boundaries
- `reasoning_delta` — thinking/reasoning text (Claude, Pi)
- `tool_use` / `tool_result` — tool calls
- `subagent_start` / `subagent_end` — a Claude session's own subagent dispatch (observation)
- `usage` — token counts (including `reasoning_tokens`)
- `error` — error reporting

## Documentation

- [English docs](https://agentabi-en.readthedocs.io/)
- [中文文档](https://agentabi-zh.readthedocs.io/)

## License

MIT
