# 03: Zed Agent Client Protocol (ACP) Ecosystem Analysis

## Purpose

This document analyzes Zed's Agent Client Protocol (ACP) — the emerging standard for editor-agent communication — and its implications for agentabi's positioning.

## Naming Disambiguation

There are **two different protocols** both abbreviated "ACP":

| | Agent **Communication** Protocol | Agent **Client** Protocol |
|---|---|---|
| Origin | IBM / BeeAI | **Zed editor** |
| Purpose | Agent ↔ Agent (HTTP REST) | **Editor ↔ Agent (JSON-RPC stdio)** |
| Status | Archived (2025.8), merged into A2A | **Active** (v0.11.4, 2026.3.28) |
| Repo | github.com/i-am-bee/acp (976★) | github.com/agentclientprotocol/agent-client-protocol (**2.6k★**) |
| Website | agentcommunicationprotocol.dev | **agentclientprotocol.com** |

**This document focuses on Zed's Agent Client Protocol.**

## Protocol Overview

ACP standardizes communication between code editors and coding agents, analogous to LSP (Language Server Protocol) for language servers.

- **Transport**: JSON-RPC 2.0 over stdio (local), HTTP/WebSocket (remote, WIP)
- **License**: Apache-2.0
- **Spec language**: Rust (Cargo workspace)
- **Official SDKs**: TypeScript, Python, Rust, Kotlin, Java, Go (coming)

Key JSON-RPC methods:

| Method | Direction | Purpose |
|--------|-----------|---------|
| `session/new` | Client → Agent | Create or resume session |
| `session/load` | Client → Agent | Load existing session (Codex) |
| `session/prompt` | Client → Agent | Send user message |
| `session/update` | Agent → Client | Streaming: text chunks, tool calls, usage, plan updates |
| `session/request_permission` | Agent → Client | Approval flow (allow/deny/always_allow) |
| `session/set_config_option` | Client → Agent | Switch model or settings |
| `session/set_mode` | Client → Agent | Plan/yolo/bypassPermissions |
| `fs/read_text_file` | Agent → Client | File operations |
| `fs/write_text_file` | Agent → Client | File operations |

## Agent Support Matrix

### Native ACP Support (no bridge needed)

These agents implement ACP directly in their CLI:

| Agent | Command | Provider |
|-------|---------|----------|
| Gemini CLI | Built-in | Google |
| Goose | `goose acp` | Block (Square) |
| OpenCode | `opencode acp` | opencode-ai / Anomaly |
| Augment Code | `auggie --acp` | Augment |
| Kimi CLI | `kimi acp` | Moonshot AI |
| Qwen Code | Built-in | Alibaba |
| GitHub Copilot | `copilot --acp --stdio` | GitHub/Microsoft |
| Mistral Vibe | `vibe-acp` | Mistral |
| Qoder CLI | Built-in | Qoder |
| Kiro CLI | `kiro-cli acp` | AWS |
| Minion Code | Built-in | Minion |
| Stakpak | Built-in | Stakpak |
| VT Code | Built-in | VT |
| Blackbox AI | Built-in | Blackbox |
| Code Assistant | Built-in | — |
| fast-agent | Built-in | — |
| Docker cagent | Built-in | Docker |

### Bridge Adapters (for agents without native ACP)

| Bridge | Stars | Language | Wraps | Org |
|--------|-------|----------|-------|-----|
| **claude-agent-acp** | 1,400 | TypeScript | Claude Agent SDK → ACP | agentclientprotocol |
| **codex-acp** | 549 | Rust | Codex CLI → ACP | zed-industries |
| cursor-agent-acp-npm | small | TypeScript | Cursor CLI → ACP | community (blowmage) |
| Cline ACP adapter | — | — | Cline → ACP | community |

### Bridge Architecture

```
claude-agent-acp:
  ACP Client ──JSON-RPC──> claude-agent-acp ──SDK──> claude-agent-sdk ──JSONL──> claude CLI

codex-acp:
  ACP Client ──JSON-RPC──> codex-acp ──subprocess──> codex CLI
```

The bridges translate between ACP JSON-RPC and each agent's native protocol (JSONL for Claude, custom for Codex). They handle:
- Context @-mentions and images
- Tool calls with permission requests
- Edit review (accept/reject)
- TODO lists
- Interactive and background terminals
- Custom slash commands
- Client MCP servers
- Session resume

## Editor Adoption

### Currently Supported

| Editor | Integration |
|--------|-------------|
| **Zed** | Native (ACP originator) |
| **AionUI** | Native (20.5k★ desktop GUI) |
| **Emacs** | Via agent-shell plugin |
| **Neovim** | Via CodeCompanion / avante.nvim |
| **Obsidian** | ACP plugin for side panel |
| **marimo** | Built-in (Python notebooks) |
| **DeepChat** | Built-in |
| **aizen** | Built-in (multi-branch manager) |
| **Tidewave** | Built-in (full-stack web dev) |
| **Browser (AI SDK)** | Via @mcpc/acp-ai-provider |

### Coming Soon

| Editor | Status |
|--------|--------|
| **JetBrains IDEs** | Official collaboration announced (Oct 2025) |
| **Sidequery** | In progress |

## Python SDK

**Package**: `agent-client-protocol` on PyPI
**Repo**: github.com/agentclientprotocol/python-sdk (205★)
**Version**: 0.9.0 (2026.3.26)
**License**: Apache-2.0

Features:
- `acp.schema`: Generated Pydantic models from canonical ACP spec
- Async base classes for agents and clients
- stdio JSON-RPC transport plumbing
- Helper builders (mirrors Go/TS SDK APIs)
- Contrib utilities: session accumulators, tool call trackers, permission brokers
- Examples: echo agent, streaming, permissions, Gemini bridge, duet demos

Usage — building an ACP agent in Python:

```python
# Minimal ACP agent
from acp.server import Server

server = Server()

@server.agent()
async def my_agent(input, context):
    """My coding agent"""
    yield {"thought": "Processing..."}
    yield result

server.run()  # Starts JSON-RPC stdio listener
```

Usage — building an ACP client in Python:

```python
from acp.client import Client

async with Client(command=["goose", "acp"]) as client:
    session = await client.session_new()
    response = await client.prompt(session.id, "Fix the bug in auth.py")
    async for event in response:
        print(event)
```

## Comparison: ACP vs agentabi

| Dimension | Zed ACP | agentabi |
|-----------|---------|----------|
| **Abstraction** | Wire protocol (JSON-RPC methods) | IR normalization (TaskConfig → IREvent → SessionResult) |
| **Agent cooperation** | Required (agent must implement ACP) | Not required (single-side adaptation) |
| **Ecosystem** | 20+ agents, 10+ editors, JetBrains coming | None (research project) |
| **Python SDK** | Yes (205★, Apache-2.0) | Yes (this project) |
| **Multi-agent routing** | No (1:1 client-agent) | Yes (core design goal) |
| **Benchmarking** | No | Yes (same task across agents) |
| **Cost/usage tracking** | Via `usage_update` events (per-agent) | Unified `UsageInfo` in SessionResult |
| **IR/normalization** | No (protocol is the format) | Yes (convert diverse formats to common IR) |
| **Academic contribution** | None | IR/ABI pattern for SE tooling |
| **Event taxonomy** | `session/update` subtypes (agent_message_chunk, tool_call, etc.) | Typed union (MessageDeltaEvent, ToolUseEvent, etc.) |
| **Middleware** | No built-in concept | Designed for middleware pipeline |

## What ACP Does NOT Solve

Despite its rapid adoption, ACP has gaps that agentabi could fill:

1. **No multi-agent orchestration**: ACP is 1:1 (one client, one agent per session). No concept of routing a task to the best available agent.

2. **No cross-agent result normalization**: Each agent's `session/update` events have different semantics. ACP defines the envelope but not the content taxonomy.

3. **No benchmarking framework**: Running the same task across multiple agents and comparing results is not part of ACP's scope.

4. **No cost-aware routing**: ACP doesn't track cumulative costs or enable choosing agents based on cost/quality tradeoffs.

5. **No fallback/retry logic**: If an agent fails or hits rate limits, the client must handle failover itself.

6. **Python SDK is minimal**: 205 stars, early stage. No high-level abstractions for common patterns.

## Strategic Implications for agentabi

### Option A: Become ACP's high-level Python orchestration layer

Position agentabi as the "requests to ACP's urllib" — a high-level, ergonomic Python library that:
- Uses ACP as the primary transport for ACP-compatible agents
- Falls back to subprocess + JSONL for non-ACP agents
- Adds multi-agent routing, cost tracking, benchmarking, middleware
- Provides the IR normalization that ACP lacks

### Option B: Pivot to ACP-native research framework

Reframe agentabi as an evaluation/benchmarking harness that speaks ACP natively:
- Run standardized tasks across agents via ACP
- Collect and normalize results for comparison
- Focus on the academic contribution (IR pattern, evaluation methodology)

### Option C: Contribute to ACP ecosystem directly

Join the ACP community and:
- Enhance the Python SDK with agentabi's IR concepts
- Propose multi-agent extensions to the ACP spec
- Build agentabi as an ACP middleware layer

### Recommendation

**Option A** preserves the most value from existing agentabi work while aligning with the ecosystem direction. The IR normalization, multi-agent routing, and middleware concepts are genuinely missing from ACP and represent both practical and academic contributions.
