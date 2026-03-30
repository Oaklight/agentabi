# 02: High-Star Projects Survey — Session Managers, Agent Harnesses, and Desktop UIs

## Purpose

This document surveys high-star projects in the AI coding agent UI/session manager ecosystem, focusing on their architecture, agent integration mechanisms, and positioning. It complements [01_session_manager_cli_interaction.md](01_session_manager_cli_interaction.md) which covered the first wave of tmux-based managers.

## Landscape Overview

The ecosystem has stratified into distinct categories:

```
                        Stars
  OMO (oh-my-openagent) ████████████████████████████████████████████████  44.9k
  AionUI                ██████████████████████                           20.5k
  AG-UI (protocol)      █████████████                                   12.7k
  OpenTUI (framework)   ██████████                                       9.7k
  CloudCLI              █████████                                        9.1k
  Claude Squad          ███████                                          6.7k
  Ralph TUI             ██                                               2.2k
  Agent Deck            ██                                               1.8k
  Agent of Empires      █                                                1.4k
  CCManager             █                                                  972
  CASS                  █                                                  636
```

## Agent Integration Mechanisms — Three Approaches

A critical finding from this survey is that projects use **three distinct approaches** to communicate with CLI agents:

### Approach 1: Terminal Screen Scraping (most common)

**Projects**: Claude Squad, Agent Deck, CCManager, Ralph TUI, Agent of Empires (fallback)

- Spawn agent CLI in tmux session or PTY
- Capture terminal text via `tmux capture-pane` or headless xterm
- Detect status via hardcoded string matching (prompt text, spinner characters)
- Send input by writing bytes to PTY (Enter = `0x0D`)
- **Fragile**: breaks when agent changes UI text

See [01_session_manager_cli_interaction.md](01_session_manager_cli_interaction.md) for detailed analysis.

### Approach 2: Hook-based File Status Detection (hybrid)

**Projects**: Agent of Empires (primary for Claude/Cursor/Gemini)

Agent of Empires installs hooks into agent settings files (e.g., Claude Code's `settings.json`). These hooks are shell commands that write status strings to temp files:

```sh
# Installed as PreToolUse hook → writes "running"
sh -c '[ -n "$AOE_INSTANCE_ID" ] || exit 0; \
  mkdir -p /tmp/aoe-hooks/$AOE_INSTANCE_ID && \
  printf running > /tmp/aoe-hooks/$AOE_INSTANCE_ID/status'
```

Hook events mapped to states:
- `PreToolUse`, `UserPromptSubmit` → running
- `Stop` → idle
- `Notification` (permission_prompt) → waiting

**More reliable than screen scraping** — leverages agent's native hook system. Falls back to tmux pane parsing for agents without hook support (Codex, OpenCode, Vibe, etc.).

### Approach 3: ACP Protocol — JSON-RPC 2.0 over stdio (structured)

**Projects**: AionUI

AionUI uses **Zed editor's ACP (Agent Communication Protocol)** — a JSON-RPC 2.0 protocol over stdin/stdout. This is fundamentally different from screen scraping:

```
AionUI --stdin-->  {"jsonrpc":"2.0","method":"session/prompt","params":{...}}  --> CLI Agent
AionUI <--stdout-- {"jsonrpc":"2.0","method":"session/update","params":{...}}  <-- CLI Agent
```

Key ACP methods:
| Method | Direction | Purpose |
|--------|-----------|---------|
| `session/new` | Request | Create/resume session |
| `session/prompt` | Request | Send user message |
| `session/update` | Notification | Streaming chunks, tool calls, usage |
| `session/request_permission` | Notification | Approval flow |
| `session/set_mode` | Request | Plan/yolo mode |
| `fs/read_text_file` | Request | File operations |

Agent launch via ACP bridges (from Zed Industries):
| Agent | Command |
|-------|---------|
| Claude Code | `npx @zed-industries/claude-agent-acp` |
| Codex | `npx @zed-industries/codex-acp` |
| Goose | `goose acp` |
| Gemini CLI | Native ACP flag |
| Augment Code | `auggie --acp` |
| OpenCode | `opencode acp` |

**Most reliable approach** — structured JSON data, typed events, no text parsing heuristics.

## Individual Project Reports

### AionUI (20.5k stars, Electron/React/TypeScript, Apache-2.0)

**GitHub**: https://github.com/iOfficeAI/AionUi

**What it is**: Desktop GUI (Electron) that serves as both a standalone AI agent AND a universal frontend for external CLI agents. Ships with a built-in agent engine (`@office-ai/aioncli-core`) that works without any CLI tools installed.

**Architecture**:
- Electron + React 19 + Arco Design + UnoCSS
- SQLite (WAL mode, schema v18) for persistence
- Express 5 for WebUI server mode (remote browser access)
- ACP (JSON-RPC 2.0 over stdio) for CLI agent communication

**Built-in agent engine**: Full agent loop using direct API calls to Gemini, OpenAI, Anthropic, Bedrock, Ollama, and 20+ Chinese AI platforms. Includes file tools, web search, image generation, MCP support.

**Session management**: SQLite-backed conversations with resume support. Session IDs persisted in conversation metadata. Codex uses `session/load`, Claude uses `session/new` with resume option.

**Multi-agent**: Parallel but independent — each conversation bound to one agent type. No cross-agent coordination.

**Key differentiator**: ACP protocol integration (from Zed) provides structured, reliable communication. No screen scraping whatsoever.

---

### OMO / oh-my-openagent (44.9k stars, TypeScript/Bun, SUL-1.0)

**GitHub**: https://github.com/code-yeongyu/oh-my-openagent

**What it is**: Plugin for OpenCode that transforms it from a single-model terminal agent into a multi-model, multi-agent orchestration system. Previously named "oh-my-opencode".

**Architecture**:
- Plugin for `opencode-ai/opencode` (Go-based coding agent, 11.6k stars)
- Uses `@opencode-ai/plugin` and `@opencode-ai/sdk`
- Hooks into OpenCode's plugin system (chat.params, chat.message, tool execution, etc.)

**Multi-model orchestration**: Routes tasks to different LLM providers automatically:
- Anthropic (Claude Opus/Sonnet/Haiku)
- OpenAI (GPT-5.x series)
- Google (Gemini 3.x)
- xAI (Grok), Moonshot (Kimi), Zhipu (GLM), MiniMax

**Three-layer architecture**:
1. **Planning**: Prometheus + Metis + Momus (plan generation, verification, critique)
2. **Conducting**: Atlas (task orchestration)
3. **Executing**: Sisyphus-Junior + specialists (parallel workers)

**Session management**: Multi-layered — `boulder.json` tracks active plans, `.sisyphus/notepads/` stores accumulated learnings (decisions, issues, verification results) that persist across sessions.

**Key features**:
- Hash-anchored edits (LINE#ID): 6.7% → 68.3% edit success improvement for weaker models
- Parallel background agents (5+ simultaneously)
- Intent Gate: classifies user intent before acting
- LSP + AST tools (ast-grep)
- Claude Code compatibility layer

**License warning**: SUL-1.0 (Sustainable Use License) — source-available but NOT open source. Commercial use restricted to internal business purposes only.

---

### Ralph TUI (2.2k stars, TypeScript/Bun, MIT)

**GitHub**: https://github.com/subsy/ralph-tui

**What it is**: Autonomous loop orchestrator based on the "Ralph Wiggum technique" — a productized version of `while :; do cat PROMPT.md | claude-code; done` with task tracking, error recovery, and remote management.

**Architecture**:
- TypeScript + Bun runtime
- OpenTUI (React-based terminal UI by SST)
- Handlebars for prompt templates
- TOML configuration
- WebSocket for remote instance management

**Agent integration**: PTY-based spawning, same fundamental approach as others. Detects task completion when agent outputs `COMPLETE`.

**Core orchestration loop**:
1. SELECT TASK — highest priority from dependency graph
2. BUILD PROMPT — Handlebars template with cross-iteration context
3. EXECUTE AGENT — spawn via PTY
4. DETECT COMPLETION — parse output for completion signal
5. NEXT TASK — mark done, loop

**Key features**:
- Agent fallback chains (Claude → OpenAI on rate limit)
- Cross-iteration context accumulation
- `learn` command: scans project, generates context file for agents
- Remote instance management via WebSocket (multi-machine monitoring)
- Headless mode for CI/CD
- Per-agent YOLO mode

**Unique positioning**: Not a session manager — it's an **autonomous task executor**. You give it a PRD (product requirements document), and it autonomously works through all tasks.

---

### Agent of Empires (1.4k stars, Rust/ratatui, MIT)

**GitHub**: https://github.com/njbrake/agent-of-empires

**What it is**: Most feature-rich terminal session manager. Written in Rust, wraps tmux with Docker/Apple Container sandboxing, hook-based status detection, 9 agent support.

**Architecture**:
- Rust (edition 2021) + ratatui + crossterm
- tmux for session backbone
- git2 (libgit2) for git operations
- Docker and Apple Containers via `ContainerRuntimeInterface` trait

**Supported agents** (9): Claude Code, OpenCode, Mistral Vibe, Codex CLI, Gemini CLI, Cursor CLI, Copilot CLI, Pi.dev, Factory Droid

**Status detection — dual mechanism**:
1. **Hook-based** (preferred, for Claude/Cursor/Gemini): installs hooks in agent settings that write status to `/tmp/aoe-hooks/$ID/status`
2. **Tmux pane parsing** (fallback): ANSI-stripped content matching with per-agent detection functions

**Docker sandboxing**:
- Container created with `docker run -d --name aoe-sandbox-{id} -w /workspace <image> sleep infinity`
- Project mounted at `/workspace`, agent configs from shared sandbox dirs
- Pre-built image with all 9 agents installed
- CPU/memory limits, credential syncing (including macOS Keychain extraction)
- Apple Containers support for macOS-native alternative

**Key features**:
- Per-repo config (`.aoe/config.toml`) with lifecycle hooks (`on_create`, `on_launch`)
- Multi-repo workspaces (worktrees across multiple repos)
- Profiles for separate workspaces
- Built-in diff view
- Custom instruction injection per agent
- AoE 2 themed sound effects and session naming (civilization names)
- 5 color themes

**Explicit influence**: README acknowledges "Inspired by agent-deck"

---

## Cross-Project Comparison

| | AionUI | OMO | Ralph TUI | AoE | Claude Squad | CCManager |
|---|---|---|---|---|---|---|
| **Stars** | 20.5k | 44.9k | 2.2k | 1.4k | 6.7k | 972 |
| **Language** | TS/Electron | TS/Bun | TS/Bun | Rust | Go | TS/Node |
| **License** | Apache-2.0 | SUL-1.0 | MIT | MIT | AGPL-3.0 | MIT |
| **Category** | Desktop GUI | Agent plugin | Loop orchestrator | Session manager | Session manager | Session manager |
| **Agent comm** | ACP (JSON-RPC) | Plugin API | PTY | Hooks + tmux | tmux scraping | PTY + headless xterm |
| **Status detection** | ACP events | Plugin hooks | Output parsing | Hook files + pane parse | Hash + string match | Regex + debounce |
| **Agents supported** | 15+ | 6+ providers | 6 | 9 | 3 (Claude/Aider/Gemini) | 7+ |
| **Docker sandbox** | No | No | bwrap/sandbox-exec | Yes (full) | No | No |
| **Session resume** | Yes (ACP) | Yes (boulder.json) | Yes (JSON files) | Yes (tmux) | Yes (tmux) | Yes (data copy) |
| **Multi-model** | Yes (built-in engine) | Yes (core feature) | No (fallback only) | No | No | No |
| **Built-in agent** | Yes | No (plugin) | No | No | No | No |

## Key Insights

### 1. ACP (from Zed) is the emerging structured protocol

AionUI's use of Zed's ACP bridges is the most architecturally sound approach. It provides:
- Typed events instead of text parsing
- Reliable status detection
- Structured tool call / permission flows
- Session resume via protocol methods

Multiple CLI agents now support ACP natively (`goose acp`, `opencode acp`, `auggie --acp`). For those that don't, Zed provides bridge packages. **This is likely where the ecosystem converges.**

### 2. Three tiers of agent integration reliability

```
Tier 3 (Most reliable):  ACP / JSON-RPC structured protocol     → AionUI
Tier 2 (Moderate):        Hook-based file status detection        → Agent of Empires
Tier 1 (Fragile):         Terminal screen scraping + string match → Everyone else
```

### 3. The market is segmenting by use case

- **Desktop GUI** (AionUI, CloudCLI): for users who want a visual interface
- **Session managers** (Claude Squad, AoE, Agent Deck, CCManager): for terminal-native devs running multiple agents
- **Autonomous orchestrators** (Ralph TUI, OMO): for hands-off task execution
- **Protocols/frameworks** (AG-UI, ACP, OpenTUI): infrastructure layers

### 4. agentabi's competitive landscape has shifted

The original agentabi vision (unified CLI wrapper) now faces:
- **ACP**: Structured protocol already adopted by AionUI and Zed, with growing CLI agent support
- **OMO**: 44.9k-star plugin that achieves multi-model orchestration within OpenCode's plugin system
- **Session managers**: Already ship their own (fragile) integration layers

agentabi's remaining differentiated value:
1. **ACP alternative for Python ecosystem** — ACP bridges are JS/TS; agentabi could be the Python equivalent
2. **JSONL streaming normalization** — for agents that support `--output-format stream-json` but not ACP
3. **Academic contribution** — the IR/ABI abstraction pattern is novel regardless of ecosystem adoption
4. **Bridge layer** — wrapping CLI agents as A2A-compliant servers

### 5. License landscape

| License | Projects |
|---------|----------|
| MIT | AoE, CCManager, OMO (SUL-1.0 ≠ MIT), Ralph TUI, Agent Deck |
| Apache-2.0 | AionUI, A2A |
| AGPL-3.0 | Claude Squad, CloudCLI, Steer |
| SUL-1.0 | OMO (source-available, commercially restricted) |
