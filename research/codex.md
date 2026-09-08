# Codex CLI

> **Vendor**: OpenAI
> **Version**: 0.153.4
> **Command**: `codex` (interactive), `codex exec` (non-interactive)
> **Language**: Rust + TypeScript
> **Package**: `@openai/codex` (npm)
> **Docs**: https://developers.openai.com/codex/cli/

## Invocation

- **Interactive mode**: `codex` or `codex \"prompt\"` (launches TUI)
- **Headless/non-interactive mode**: `codex exec \"prompt\"` (alias: `codex e \"prompt\"`)
- **Quiet mode (legacy)**: `codex -q \"prompt\"` (deprecated in favor of `codex exec`)
- **Key CLI flags** (global):
  - `--model, -m <model>` — override configured model (e.g., `gpt-5-codex`)
  - `--sandbox, -s <mode>` — `read-only` | `workspace-write` | `danger-full-access`
  - `--ask-for-approval, -a <policy>` — `untrusted` | `on-failure` | `on-request` | `never`
  - `--full-auto` — shortcut for `--ask-for-approval on-request --sandbox workspace-write`
  - `--dangerously-bypass-approvals-and-sandbox` / `--yolo` — bypass everything
  - `--dangerously-bypass-hook-trust` — skip hook trust checks
  - `--image, -i <FILE>...` — attach images to initial prompt
  - `--profile, -p <name>` — configuration profile from `config.toml`
  - `--profile-v2` — enable config profile layering (v2 semantics)
  - `--oss` / `--local-provider` — use open-source / local Ollama provider
  - `--search` — enable live web search tool
  - `--add-dir <DIR>` — grant additional directory write access
  - `--no-alt-screen` — inline TUI (no alternate screen buffer)
  - `--remote <URL>` — connect to a remote app server
  - `--remote-auth-token-env <VAR>` — env var holding auth token for remote server
  - `--strict-config` — error on unrecognized config fields
- **Key CLI flags** (`codex exec` specific):
  - `--json` / `--experimental-json` — emit JSONL events instead of formatted text
  - `--output-last-message, -o <FILE>` — write final message to file
  - `--output-schema <FILE>` — JSON Schema for structured final response
  - `--ephemeral` — don't persist session to disk
  - `--color <mode>` — `always` | `never` | `auto`
  - `--skip-git-repo-check` — allow running outside a git repo
  - `--ignore-user-config` — skip `config.toml` (use defaults only)
  - `--ignore-rules` — skip `.rules` / `AGENTS.md` files
  - `--full-auto` — low-friction automation preset
  - `PROMPT` — accepts string or `-` for stdin

## Subcommands

| Subcommand | Description |
|---|---|
| `exec` / `e` | Non-interactive headless execution |
| `resume` | Resume an existing session interactively or non-interactively |
| `fork` | Fork an existing session into a new thread |
| `agents` | Manage agent configurations |
| `apply` | Apply a patch or rollout file |
| `queue` | Manage queued tasks |
| `archive` | Archive a session |
| `unarchive` | Unarchive a session |
| `delete` | Delete a session |
| `cloud` | Submit tasks to Codex Cloud |
| `exec-server` | Start a headless exec server (JSONL protocol) |
| `app-server` | Start the app server (JSONL-over-stdio or WebSocket) |
| `mcp` | Manage MCP server integrations |
| `mcp-server` | Run Codex as an MCP server |
| `sandbox` | Sandbox management and inspection |
| `features` | List / enable / disable feature flags |
| `login` | Authenticate (ChatGPT OAuth, device auth, or API key) |
| `migrate-rollouts` | Migrate legacy rollout files to new format |
| `debug` | Debug utilities and diagnostics |
| `execpolicy` | Inspect and test exec policy / rule files |

## Output Format

- **Default**: Formatted text to stderr (progress), final message to stdout
- **Structured JSON**: `codex exec --json \"prompt\"` — newline-delimited JSON events (JSONL) to stdout
- **Final message file**: `codex exec -o result.txt \"prompt\"` — writes final message to a file
- **Structured output**: `codex exec --output-schema ./schema.json \"prompt\"` — validates final response against JSON Schema

### JSONL event types

When using `--json`, stdout becomes a JSONL stream with these event types:

| Event type | Description |
|---|---|
| `thread.started` | Session/thread initialization |
| `turn.started` | Beginning of a new turn |
| `turn.completed` | Turn finished with usage stats |
| `turn.failed` | Turn failed |
| `item.started` | An item (message, command, file change) began |
| `item.completed` | An item finished |
| `error` | Error event |

### Item types (within `item.started` / `item.completed`)

| Item type | Description |
|---|---|
| `agent_message` | Text response from the agent |
| `command_execution` | Shell command execution |
| `file_change` | File modification |
| `mcp_tool_call` | MCP tool invocation |
| `web_search` | Web search action |
| `plan_update` | Plan update |
| `reasoning` | Reasoning step |

### JSONL example

```json
{"type": "thread.started", "thread_id": "0199a213-81c0-7800-8aa1-bbab2a035a53"}
{"type": "turn.started"}
{"type": "item.started", "item": {"id": "item_1", "type": "command_execution", "command": "bash -lc ls", "status": "in_progress"}}
{"type": "item.completed", "item": {"id": "item_3", "type": "agent_message", "text": "Repo contains docs, sdk, and examples directories."}}
{"type": "turn.completed", "usage": {"input_tokens": 24763, "cached_input_tokens": 24448, "output_tokens": 122}}
```

## Input Format

- **CLI argument**: `codex exec "prompt text"`
- **Stdin**: `codex exec -` (reads prompt from stdin)
- **Image attachment**: `codex exec -i image.png "describe this"`
- **Follow-up / multi-turn**: Use `codex exec resume --last "follow-up"` or `codex exec resume <SESSION_ID> "follow-up"`

## Permission Model

- **Approval policy** (via `--ask-for-approval` / `-a`), 4 levels:
  - `untrusted` — ask before every action (most restrictive; legacy name: `suggest`)
  - `on-failure` — ask only when a command exits with a non-zero status
  - `on-request` — ask when the agent explicitly requests approval
  - `never` — never ask (least restrictive; legacy name: `full-auto` approval side)
- **Sandbox mode** (via `--sandbox` / `-s`), 3 levels:
  - `read-only` — no writes allowed (default for `codex exec`)
  - `workspace-write` — writes allowed in workspace + /tmp
  - `danger-full-access` — full filesystem access
- **Combined shortcut**: `--full-auto` = `--ask-for-approval on-request --sandbox workspace-write`
- **Bypass everything**: `--yolo` / `--dangerously-bypass-approvals-and-sandbox`
- **Bypass hook trust**: `--dangerously-bypass-hook-trust` — skip hook trust verification only
- **Exec policy**: `codex execpolicy check` to test command allowability against rule files
- **Configuration**: Per-tool approval overrides in `~/.codex/config.toml`

## Session Management

- **Session creation**: Automatic when starting interactive or exec mode
- **Resume interactive**: `codex resume [SESSION_ID]` or `codex resume --last`
- **Resume non-interactive**: `codex exec resume [SESSION_ID]` or `codex exec resume --last`
- **Fork session**: `codex fork [SESSION_ID]` — create new thread from existing session
- **Archive / unarchive**: `codex archive <SESSION_ID>` / `codex unarchive <SESSION_ID>`
- **Delete**: `codex delete <SESSION_ID>` — permanently remove a session
- **Storage format**: Session rollout files on disk (migrate with `codex migrate-rollouts`)
- **Storage location**: Local filesystem (managed by Codex)
- **History model**: Linear with fork capability
- **Ephemeral mode**: `--ephemeral` to skip persisting session files

## Tool System

### Built-in tools

| Tool | Description |
|------|-------------|
| Shell execution | Run shell commands in sandboxed environment |
| File read/write | Read and modify files |
| Web search | Search the web (cached by default, `--search` for live) |
| MCP tools | External tools via MCP protocol |

### MCP support

- **Yes**, via `codex mcp` subcommands
- Supports `stdio` and `streamable HTTP` transports
- Commands:
  - `codex mcp add <name> -- <command...>` — register stdio MCP server
  - `codex mcp add <name> --url <url>` — register HTTP MCP server
  - `codex mcp list` — list configured servers
  - `codex mcp remove <name>` — remove a server
  - `codex mcp login <name>` — OAuth login for HTTP servers
- Can also run as MCP server: `codex mcp-server` (stdio transport)
- Configuration stored in `~/.codex/config.toml`

### Custom tool extension

- MCP servers are the primary extension mechanism
- Supports `--env KEY=VALUE` for stdio server environment
- Supports `--bearer-token-env-var` for HTTP server auth

## Configuration

- **Project config file**: `AGENTS.md` at repo root (skipped with `--ignore-rules`)
- **User/global config**: `~/.codex/config.toml` (skipped with `--ignore-user-config`)
- **Profiles**: `--profile <name>` to select config profile; `--profile-v2` for layered profile semantics
- **Inline overrides**: `-c key=value` for per-invocation config
- **Strict mode**: `--strict-config` causes an error on any unrecognized config field
- **Feature flags**: `codex features list|enable|disable`
- **Environment variables**:
  - `OPENAI_API_KEY` / `CODEX_API_KEY` — API key
- **Authentication**:
  - `codex login` — ChatGPT OAuth or device auth
  - `codex login --with-api-key` — API key from stdin
  - `codex login status` — check auth

## Streaming Events Schema

When using `codex exec --json`, events are emitted as JSONL.

### Event types with examples

#### thread.started
```json
{"type": "thread.started", "thread_id": "0199a213-81c0-7800-8aa1-bbab2a035a53"}
```
**Semantic category**: `session_lifecycle`

#### turn.started
```json
{"type": "turn.started"}
```
**Semantic category**: `session_lifecycle`

#### turn.completed
```json
{"type": "turn.completed", "usage": {"input_tokens": 24763, "cached_input_tokens": 24448, "output_tokens": 122}}
```
**Semantic category**: `usage`

#### turn.failed
```json
{"type": "turn.failed", "error": "Maximum turns exceeded"}
```
**Semantic category**: `error`

#### item.started (command_execution)
```json
{"type": "item.started", "item": {"id": "item_1", "type": "command_execution", "command": "bash -lc ls", "status": "in_progress"}}
```
**Semantic category**: `tool_call`

#### item.completed (agent_message)
```json
{"type": "item.completed", "item": {"id": "item_3", "type": "agent_message", "text": "The repository structure looks good."}}
```
**Semantic category**: `message_streaming`

#### item.completed (file_change)
```json
{"type": "item.completed", "item": {"id": "item_5", "type": "file_change", "path": "src/main.rs"}}
```
**Semantic category**: `file_change`

#### error
```json
{"type": "error", "message": "Failed to execute command"}
```
**Semantic category**: `error`

### Semantic category mapping

| Event | Category |
|---|---|
| `thread.started` | `session_lifecycle` |
| `turn.started` | `session_lifecycle` |
| `turn.completed` | `usage` |
| `turn.failed` | `error` |
| `item.started` (command_execution) | `tool_call` |
| `item.started` (agent_message) | `message_streaming` |
| `item.completed` (agent_message) | `message_streaming` |
| `item.completed` (file_change) | `file_change` |
| `item.completed` (mcp_tool_call) | `tool_call` |
| `item.completed` (web_search) | `tool_call` |
| `error` | `error` |

## Programmatic API / SDK

- **CLI-first**: Primary programmatic interface is `codex exec --json`
- **App server** (experimental): `codex app-server` — JSONL-over-stdio or WebSocket transport
  - `codex app-server --listen stdio://` — stdio transport
  - `codex app-server --listen ws://IP:PORT` — WebSocket (experimental)
- **Exec server**: `codex exec-server` — headless exec server with JSONL protocol
- **Remote server**: `codex --remote <URL> --remote-auth-token-env <VAR>` — connect to a hosted app server
- **Desktop app**: `codex app` — macOS desktop app
- **Cloud tasks**: `codex cloud exec "prompt"` — submit to Codex Cloud
- **As MCP server**: `codex mcp-server` — run Codex as an MCP server for other tools
