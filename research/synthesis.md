# Synthesis: Cross-Agent Comparison

This document compares the CLI interfaces of the six coding agents researched in Phase 0, identifies common patterns suitable for the AgentABI intermediate representation (IR), and highlights agent-specific features that require an extension mechanism.

## 1. Event Type Mapping Table

This table maps each agent's streaming event types to a common semantic vocabulary.

| Semantic Category | Claude Code | Codex CLI | Cursor CLI | OpenCode | OpenClaw | Gemini CLI |
|---|---|---|---|---|---|---|
| **Session init** | `system` (subtype: `init`) | `thread.started` | `system` (init) | N/A | WS session event | `init` |
| **Turn start** | (implicit in assistant msg) | `turn.started` | (implicit) | N/A | N/A | (implicit) |
| **Turn end** | (implicit in result) | `turn.completed` | (implicit) | N/A | N/A | (implicit) |
| **Text message** | `assistant` (text content block) | `item.completed` (agent_message) | `assistant` (text block) | Final text output | WS message block | `message` |
| **Tool call** | `assistant` (tool_use block) | `item.started` (command_execution, mcp_tool_call) | `assistant` (tool_use block) | N/A | WS tool streaming | `tool_use` |
| **Tool result** | `user` (tool_result block) | `item.completed` (command_execution) | `user` (tool_result block) | N/A | WS tool result | `tool_result` |
| **File change** | `user` (Edit/Write tool_result) | `item.completed` (file_change) | `user` (tool_result) | N/A | WS tool result | `tool_result` |
| **Token delta** | `stream_event` (content_block_delta) | N/A (no token streaming) | `stream_event` (delta) | N/A | WS block streaming | N/A |
| **Usage/cost** | `result` (usage, total_cost_usd) | `turn.completed` (usage) | `result` (usage) | N/A | N/A | `result` (stats) |
| **Error** | `result` (error subtypes) | `turn.failed`, `error` | `result` (error) | N/A | WS error | `error` |
| **Final result** | `result` (subtype: success) | (last turn.completed) | `result` (success) | JSON output | Channel response | `result` |
| **Permission denial** | `result.permission_denials[]` | N/A | N/A | N/A | N/A | N/A |

## 2. Permission Model Comparison

| Feature | Claude Code | Codex CLI | Cursor CLI | OpenCode | OpenClaw | Gemini CLI |
|---|---|---|---|---|---|---|
| **Named modes** | `default`, `acceptEdits`, `plan`, `bypassPermissions` | `untrusted`, `on-request`, `never` | Default + per-rule | Auto-approve in `-p` | Main=full, sandbox for non-main | Interactive approval |
| **Tool whitelist** | `--allowedTools` | N/A (approval mode covers all) | Allow tokens in config | N/A | Sandbox allowlist | `--allowed-tools` |
| **Tool blacklist** | `--disallowed-tools` | N/A | Deny tokens in config | N/A | Sandbox denylist | N/A |
| **Sandbox** | N/A (tool-level control) | `--sandbox read-only\|workspace-write\|danger-full-access` | `--force` for writes | N/A | Docker per-session | `--sandbox` |
| **Bypass flag** | `--dangerously-skip-permissions` | `--yolo` | `--force` | All auto-approved in `-p` | Main session = full | N/A |
| **Config location** | `~/.claude/settings.json` | `~/.codex/config.toml` | `.cursor/cli.json` | N/A | `openclaw.json` | `~/.gemini/settings.json` |
| **Prefix matching** | Yes (`Bash(git diff *)`) | N/A | Yes (`Shell(git)`) | N/A | N/A | N/A |

## 3. Transport Mechanism Comparison

| Feature | Claude Code | Codex CLI | Cursor CLI | OpenCode | OpenClaw | Gemini CLI |
|---|---|---|---|---|---|---|
| **Primary transport** | Process spawn (stdio) | Process spawn (stdio) | Process spawn (stdio) | Process spawn (stdio) | WebSocket | Process spawn (stdio) |
| **Streaming format** | NDJSON | JSONL | NDJSON | N/A (no streaming) | WS JSON messages | NDJSON |
| **Output flag** | `--output-format stream-json` | `--json` | `--output-format stream-json` | `-f json` (final only) | N/A | `--output-format stream-json` |
| **Token streaming** | Yes (`--include-partial-messages`) | No | Yes (likely) | No | Yes (WS block streaming) | No (event-level only) |
| **SDK transport** | Subprocess stdio | Subprocess stdio / app-server WS | Subprocess stdio | Subprocess stdio | WebSocket RPC | Subprocess stdio |
| **Can act as MCP server** | No (but MCP client) | Yes (`codex mcp-server`) | No | No | No | No |

## 4. Session Model Comparison

| Feature | Claude Code | Codex CLI | Cursor CLI | OpenCode | OpenClaw | Gemini CLI |
|---|---|---|---|---|---|---|
| **Session ID** | UUID | UUID | UUID | Internal ID | Per-channel | N/A (checkpoints) |
| **Resume by ID** | `--resume <id>` | `codex resume <id>` | `--resume <id>` | TUI only | N/A (persistent) | `/checkpoint restore` |
| **Resume latest** | `--continue` | `codex resume --last` | `cursor-agent resume` | TUI switch | N/A | N/A |
| **Fork** | N/A | `codex fork` | N/A | N/A | `sessions_spawn` | N/A |
| **Storage** | JSON files (`~/.claude/sessions/`) | Rollout files | Local filesystem | SQLite | Gateway state | Local filesystem |
| **Auto-compact** | N/A | N/A | `/compress` | Yes (at 95% context) | `/compact` | Token caching |
| **Multi-turn in headless** | Yes (`--continue`/`--resume`) | Yes (`codex exec resume`) | Yes (`--resume`) | No | Yes (persistent sessions) | No |

## 5. Configuration Comparison

| Feature | Claude Code | Codex CLI | Cursor CLI | OpenCode | OpenClaw | Gemini CLI |
|---|---|---|---|---|---|---|
| **Context file** | `CLAUDE.md` | `AGENTS.md` | `AGENTS.md`, `CLAUDE.md`, `.cursor/rules/` | N/A | `AGENTS.md`, `SOUL.md`, `TOOLS.md` | `GEMINI.md` |
| **Global config** | `~/.claude/settings.json` | `~/.codex/config.toml` | `~/.cursor/cli-config.json` | `~/.opencode.json` | `~/.openclaw/openclaw.json` | `~/.gemini/settings.json` |
| **Project config** | `.claude/settings.json` | N/A | `.cursor/cli.json` | `.opencode.json` | Workspace config | N/A |
| **Config format** | JSON | TOML | JSON | JSON | JSON | JSON |
| **MCP config** | `--mcp-config` flag / settings.json | `~/.codex/config.toml` | `mcp.json` | `.opencode.json` | Skills / settings | `~/.gemini/settings.json` |
| **Custom commands** | Skills system | N/A | Skills + Plugins | Custom commands (MD) | Skills (ClawHub) | Custom extensions |
| **Profiles** | N/A | `--profile` | N/A | N/A | N/A | N/A |

## 6. Common Denominator (IR Candidates)

These features are present in the majority of agents and are strong candidates for the AgentABI intermediate representation:

### 6.1 Core IR Events

| IR Event | Present In | Notes |
|---|---|---|
| `session.start` | All 6 | Session initialization with metadata |
| `session.end` | All 6 | Final result with usage/cost |
| `message.text` | All 6 | Agent text response |
| `tool.call` | Claude, Codex, Cursor, Gemini | Tool invocation with name + input |
| `tool.result` | Claude, Codex, Cursor, Gemini | Tool execution output |
| `error` | All 6 | Error reporting |
| `usage` | Claude, Codex, Cursor, Gemini | Token usage + cost metrics |

### 6.2 Core IR Metadata

| Field | Present In | Notes |
|---|---|---|
| `session_id` | All 6 | Unique session identifier |
| `model` | All 6 | Model name/identifier |
| `tools` | All 6 | Available tool list |
| `cwd` | Claude, Codex, Cursor, OpenCode | Working directory |
| `duration_ms` | Claude, Codex, Cursor | Wall clock time |
| `token_usage` | All 6 | Input/output token counts |
| `cost_usd` | Claude, Codex | Dollar cost |

### 6.3 Core CLI Interface Pattern

| Pattern | Details |
|---|---|
| **Non-interactive flag** | `-p` (Claude, Cursor, OpenCode, Gemini), `exec` subcommand (Codex), `agent --message` (OpenClaw) |
| **JSON output** | `--output-format json` (Claude, Cursor, Gemini), `--json` (Codex), `-f json` (OpenCode) |
| **Streaming output** | `--output-format stream-json` (Claude, Cursor, Gemini), `--json` with JSONL (Codex) |
| **Model selection** | `-m` / `--model` (all) |
| **MCP configuration** | Config file or CLI flag (all 6) |
| **Session resume** | `--resume`/`--continue` (Claude, Codex, Cursor) |

### 6.4 Common Built-in Tools

| Tool Category | Claude Code | Codex | Cursor | OpenCode | OpenClaw | Gemini |
|---|---|---|---|---|---|---|
| File read | `Read` | Yes | Yes | `view` | `read` | `read_file` |
| File write | `Write` | Yes | Yes | `write` | `write` | `write_file` |
| File edit | `Edit` | Yes | Yes | `edit`/`patch` | `edit` | `edit_file` |
| Shell exec | `Bash` | Yes | Yes | `bash` | `bash` | `shell` |
| File search | `Glob` | Yes | Yes | `glob` | N/A | `search_files` |
| Content search | `Grep` | Yes | Yes | `grep` | N/A | N/A |
| Web fetch | `WebFetch` | N/A | N/A | `fetch` | N/A | `web_fetch` |
| Web search | `WebSearch` | Yes | Yes | `sourcegraph` | N/A | `google_search` |
| Sub-agent | `Task` | N/A | N/A | `agent` | `sessions_spawn` | N/A |

## 7. Agent-Specific Features (Extension Mechanism Candidates)

These features are unique to specific agents and should be handled by the AgentABI extension mechanism:

### Claude Code specific
- **Permission denials array** in result message
- **`stream_event`** with Anthropic API delta format (content_block_delta, message_start, etc.)
- **Plan mode** (`--permission-mode plan`, `EnterPlanMode`/`ExitPlanMode` tools)
- **`--json-schema`** for structured output validation
- **Skill system** with `/command` invocation
- **`apiKeySource`** field in system init
- **`--append-system-prompt`** vs `--system-prompt`

### Codex CLI specific
- **Thread/turn/item event hierarchy** (more granular than other agents)
- **`item.type` taxonomy**: `agent_message`, `command_execution`, `file_change`, `mcp_tool_call`, `web_search`, `plan_update`, `reasoning`
- **Exec policy system** (`codex execpolicy`) for command allowability rules
- **Session forking** (`codex fork`)
- **Cloud tasks** (`codex cloud exec`)
- **`--output-schema`** for structured final response
- **Sandbox policy** as separate concern from approval mode
- **`--ephemeral`** mode
- **Can run as MCP server** (`codex mcp-server`)

### Cursor CLI specific
- **`--force`** flag for write gating in print mode
- **Trust model** requiring interactive session before headless MCP use
- **ACP (Agent Communication Protocol)** support
- **Cloud Agent API** with REST endpoints
- **`.cursor/rules/`** directory for rule files
- **`/compress`** command for context reduction

### OpenCode specific
- **SQLite storage** for sessions
- **LSP integration** for code diagnostics
- **Auto-compact** at configurable context window threshold
- **Custom commands** via Markdown files with named argument placeholders
- **Vim-like editor** in TUI
- **Sourcegraph integration** for public code search

### OpenClaw specific
- **Multi-channel routing** (WhatsApp, Telegram, Slack, Discord, etc.)
- **WebSocket gateway** as primary transport (not CLI stdio)
- **Voice Wake + Talk Mode**
- **Canvas/A2UI** visual workspace
- **Device nodes** (camera, screen recording, location, notifications)
- **Inter-session communication** (`sessions_send`, `sessions_history`)
- **DM pairing** security model
- **Cron scheduling**
- **Skills registry** (ClawHub)

### Gemini CLI specific
- **Google Search grounding** as a built-in tool
- **Checkpointing** instead of session IDs
- **Token caching** optimization
- **Google OAuth** free tier (60 req/min, 1000 req/day)
- **Exit code taxonomy** (0, 1, 42, 53)
- **`--experimental-acp`** flag
- **Extensions system** (`-e`, `--extensions`)
- **`@file` references** in prompts

## 8. Design Implications for AgentABI

### 8.1 Event IR should support

1. **Envelope pattern**: Every event has `type`, `timestamp`, `session_id`
2. **Semantic categories**: `session_lifecycle`, `message_streaming`, `tool_call`, `permission`, `usage`, `error`, `file_change`
3. **Content blocks**: Text, tool_use, tool_result as typed content blocks (following the Anthropic API pattern, which Claude Code, Cursor, and partially Gemini already use)
4. **Metadata**: Model, tools, cost, duration, token counts

### 8.2 Adapter pattern needed for

1. **Event normalization**: Each agent's events map to the common IR
2. **Transport abstraction**: stdio/NDJSON for most agents, WebSocket for OpenClaw
3. **Permission model normalization**: Map agent-specific modes to a common taxonomy
4. **Session ID handling**: UUID-based for most, checkpoint-based for Gemini, channel-based for OpenClaw
5. **Tool name normalization**: Map `Read`/`view`/`read_file` to a common tool taxonomy

### 8.3 Extension points needed for

1. **Agent-specific metadata**: Extra fields in init (apiKeySource, permissionMode, etc.)
2. **Agent-specific event types**: stream_event deltas, plan updates, file changes
3. **Agent-specific tools**: Sourcegraph, Canvas, device nodes, etc.
4. **Agent-specific configuration**: Each agent's config format and location
5. **Agent-specific authentication**: API keys, OAuth, ChatGPT auth, Google OAuth

### 8.4 Recommended IR event types

```
session.init          — session started (maps to: system/init, thread.started, init)
session.end           — session completed (maps to: result/success, turn.completed, result)
session.error         — session error (maps to: result/error_*, turn.failed, error)

message.start         — assistant message begins
message.delta         — token-level text delta
message.end           — assistant message complete

tool.call             — tool invocation (maps to: tool_use, item.started, tool_use)
tool.result           — tool result (maps to: tool_result, item.completed, tool_result)
tool.permission       — permission request/grant/deny

usage.update          — token/cost update (maps to: usage in result/turn.completed)

file.change           — file was modified (maps to: file_change item, Write/Edit result)
```
