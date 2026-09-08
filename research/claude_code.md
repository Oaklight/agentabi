# Claude Code CLI

> **Vendor**: Anthropic
> **Command**: `claude`
> **Language**: TypeScript (Node.js)
> **Package**: `@anthropic-ai/claude-code` (npm)
> **Docs**: https://code.claude.com/docs/en/headless

## Invocation

- **Interactive mode**: `claude` (launches TUI)
- **Headless/print mode**: `claude -p "prompt"` (non-interactive, outputs to stdout)
- **Key CLI flags**:
  - `-p` / `--print` — non-interactive mode
  - `--output-format text|json|stream-json` — output format
  - `--verbose` — required for `stream-json` format
  - `--include-partial-messages` — enable token-level streaming
  - `--model <model>` — model alias (`opus`, `sonnet`, `haiku`) or full name
  - `--max-turns <n>` — maximum conversation turns
  - `--max-budget-usd <n>` — maximum spend in USD
  - `--system-prompt <text>` — replace default system prompt
  - `--append-system-prompt <text>` — append to default system prompt
  - `--json-schema <schema>` — constrain JSON output to a schema (with `--output-format json`)
  - `--allowedTools <tools>` — comma-separated tool whitelist
  - `--disallowed-tools <tools>` — comma-separated tool blacklist
  - `--mcp-config <path>` — path to MCP server configuration JSON
  - `--permission-mode <mode>` — permission mode
  - `--dangerously-skip-permissions` — bypass all permission checks
  - `--resume <session-id>` — resume a specific session
  - `--continue` — continue most recent session
  - `--session-id <id>` — specify session identifier

## CLI Flags Reference

Full table of supported flags as of v2.1.263:

| Flag | Description |
|------|-------------|
| `-p` / `--print` | Non-interactive (headless) mode |
| `--output-format <fmt>` | `text`, `json`, or `stream-json` |
| `--verbose` | Enable verbose output; required for `stream-json` |
| `--include-partial-messages` | Token-level streaming events |
| `--input-format <fmt>` | `text` (default) or `stream-json` for structured input |
| `--model <model>` | Model alias or full model ID |
| `--fallback-model <model>` | Fallback model when primary is overloaded |
| `--max-turns <n>` | Maximum conversation turns |
| `--max-budget-usd <n>` | Maximum spend cap in USD |
| `--effort <level>` | Reasoning effort: `low`, `medium`, `high`, `xhigh`, `max` |
| `--system-prompt <text>` | Replace default system prompt |
| `--append-system-prompt <text>` | Append to default system prompt |
| `--json-schema <schema>` | Structured output schema (use with `--output-format json`) |
| `--allowedTools <tools>` | Comma-separated tool whitelist |
| `--disallowed-tools <tools>` | Comma-separated tool blacklist |
| `--tools <tools...>` | Built-in tool set control |
| `--mcp-config <path>` | Path to MCP server configuration JSON |
| `--strict-mcp-config` | Only use MCP servers from `--mcp-config`, ignoring all others |
| `--permission-mode <mode>` | Permission mode (see Permission Model) |
| `--dangerously-skip-permissions` | Bypass all permission checks |
| `--allow-dangerously-skip-permissions` | Enable bypass-permissions as an available option |
| `--session-id <uuid>` | Specify session ID (create if missing) |
| `--resume <session-id>` | Resume a specific existing session |
| `--continue` | Resume the most recent session |
| `--fork-session` | Start a new session ID when resuming (branch from existing) |
| `--no-session-persistence` | Ephemeral mode; session not saved to disk |
| `--add-dir <dirs...>` | Additional directories to expose to the agent |
| `--file <specs...>` | File attachments passed into the session |
| `--agent <agent>` | Custom agent definition (single) |
| `--agents <json>` | Custom agent definitions (JSON array) |
| `--bare` | Minimal mode; strips system prompt scaffolding |
| `--worktree [name]` | Git worktree isolation; creates/enters a named worktree |
| `--betas <betas...>` | Beta API headers to enable |
| `--disable-slash-commands` | Disable skill/slash-command invocation |
| `--brief` | Operate as a SendUserMessage tool target (brief mode) |
| `--cloud` | Use cloud sessions |
| `--bg` / `--background` | Run as a background session |
| `--autocompact` | Auto-compact context when approaching limits |
| `--include-hook-events` | Emit hook lifecycle events in the stream |
| `--replay-user-messages` | Re-emit user messages in the output stream |
| `--from-pr` | Resume a session linked to a pull request |

## Output Format

- **Default**: Plain text (`--output-format text`)
- **Structured JSON**: `--output-format json` — single JSON object on completion with fields:
  - `result` — text result
  - `session_id` — session UUID
  - `structured_output` — parsed JSON if `--json-schema` was provided
  - Usage metadata (tokens, cost)
- **Streaming JSONL**: `--output-format stream-json` — newline-delimited JSON (NDJSON), one event per line

### JSON output example

```json
{
  "result": "The auth module handles user authentication...",
  "session_id": "550e8400-e29b-41d4-a716-446655440001",
  "total_cost_usd": 0.0234,
  "usage": {
    "input_tokens": 200,
    "output_tokens": 150
  }
}
```

## Input Format

- **CLI argument**: `claude -p "prompt text"`
- **Stdin piping**: `echo "prompt" | claude -p`
- **Structured stream input**: `--input-format stream-json` for pre-formatted NDJSON prompts
- **Multi-turn follow-up**: Use `--continue` or `--resume <session-id>` with a new `-p "follow-up"`
- **Separator**: Use `--` between options and prompt to avoid flag parsing issues

## Permission Model

- **Permission modes** (via `--permission-mode`):
  - `default` — prompt for sensitive operations (file writes, shell commands)
  - `acceptEdits` — auto-approve file edits, prompt for shell commands
  - `auto` — automatically accept most operations without prompting
  - `dontAsk` — suppress all permission prompts (but still respects tool restrictions)
  - `plan` — planning mode, no execution of tools
  - `bypassPermissions` — skip all permission checks (also `--dangerously-skip-permissions`)
- **Tool filtering**:
  - `--allowedTools "Read,Edit,Bash"` — whitelist specific tools
  - `--disallowed-tools "Bash,Write"` — blacklist specific tools
  - `--tools <tools...>` — built-in tool set control at a higher level
  - Supports prefix matching with `*`: `Bash(git diff *)` allows any command starting with `git diff`
- **Bypass flags**:
  - `--dangerously-skip-permissions` — unconditionally bypass all permissions
  - `--allow-dangerously-skip-permissions` — make bypass available as a runtime option without forcing it
- **Configuration**: Permissions can be set in `~/.claude/settings.json` or project-level `.claude/settings.json`
- **Permission denials**: Reported in the `result` message's `permission_denials` array

## Session Management

Sessions are stored as JSON files under `~/.claude/sessions/` and follow a linear conversation transcript model.

- **Session creation**: `--session-id <uuid>` creates a session with the given ID if it doesn't exist, or attaches to the existing one
- **Resume by ID**: `--resume <session-id>` — resume a specific existing session
- **Resume most recent**: `--continue` — resume the most recent session
- **Fork a session**: `--fork-session` — when used with `--resume` or `--continue`, starts a new session ID branching from the resumed session (preserves the original)
- **Ephemeral mode**: `--no-session-persistence` — session is not written to disk; useful for one-shot queries
- **PR-linked sessions**: `--from-pr` — resume the session associated with a pull request
- **Storage format**: JSON files
- **Storage location**: `~/.claude/sessions/`
- **History model**: Linear (conversation transcript)

## Subcommands

In addition to the default `claude <prompt>` invocation, the CLI exposes named subcommands:

| Subcommand | Description |
|------------|-------------|
| `claude agents` | Manage custom agent definitions |
| `claude auto-mode` | Configure or run in fully autonomous mode |
| `claude plugin` / `claude plugins` | Manage installed plugins/skills |
| `claude project` | Project-level operations (init, status, config) |
| `claude setup-token` | Configure and store authentication tokens |
| `claude ultrareview` | Run a deep code review pass |

## Tool System

### Built-in tools

| Tool | Description |
|------|-------------|
| `Read` | Read file contents (with offset/limit) |
| `Write` | Create or overwrite a file |
| `Edit` | Find-and-replace in a file |
| `Bash` | Execute shell commands |
| `Glob` | Pattern-match files |
| `Grep` | Search file contents with regex |
| `WebFetch` | Fetch URL content |
| `WebSearch` | Web search |
| `Task` | Spawn a subagent |
| `TaskOutput` | Read subagent output |
| `TodoWrite` | Manage task list |
| `NotebookEdit` | Edit Jupyter notebooks |
| `Skill` | Invoke a skill |
| `EnterPlanMode` / `ExitPlanMode` | Toggle planning mode |
| `KillShell` | Terminate a running shell |
| `AskUserQuestion` | Prompt the user |
| `SendUserMessage` | Send a message to the user (brief mode) |
| `EnterWorktree` / `ExitWorktree` | Git worktree isolation management |

### MCP support

- **Yes**, via `--mcp-config <path>` flag
- `--strict-mcp-config` restricts the session to only the MCP servers in the provided config file, ignoring any globally configured servers
- Supports `stdio`, `sse`, and `http` transport types
- MCP tools namespaced as `mcp__<server>__<tool>`
- Configuration file format:
```json
{
  "mcpServers": {
    "my-tools": {
      "command": "python",
      "args": ["mcp_server.py"],
      "env": {"API_KEY": "secret"}
    },
    "remote-tools": {
      "type": "sse",
      "url": "https://mcp.example.com/sse"
    }
  }
}
```

### Custom tool extension

- MCP servers provide the extension mechanism
- Also configurable in `~/.claude/settings.json` under `mcpServers`
- Custom agents (via `--agent` / `--agents`) can define their own tool subsets

## Configuration

- **Project config file**: `CLAUDE.md` at the repo root (context/instructions file)
- **User/global config**: `~/.claude/settings.json`
- **Project-level settings**: `.claude/settings.json`
- **Environment variables**:
  - `ANTHROPIC_API_KEY` — API key for authentication
  - `CLAUDE_CODE_USE_BEDROCK=1` — use AWS Bedrock
  - `CLAUDE_CODE_USE_VERTEX=1` — use Google Vertex AI

## Streaming Events Schema

When using `--output-format stream-json`, events are emitted as NDJSON. The top-level `type` field determines the message kind.

### Message types

| `type` | `subtype` | Semantic Category | Description |
|--------|-----------|-------------------|-------------|
| `system` | `init` | `session_lifecycle` | Session initialization with metadata |
| `assistant` | — | `message_streaming` | Complete assistant message (text + tool_use blocks) |
| `user` | — | `tool_call` | Tool results returned to the model |
| `result` | `success` | `session_lifecycle` | Query completed successfully |
| `result` | `error_max_turns` | `error` | Exceeded maximum turns |
| `result` | `error_during_execution` | `error` | Runtime error |
| `result` | `error_max_budget_usd` | `error` | Exceeded budget limit |
| `stream_event` | — | `message_streaming` | Token-level delta (requires `--include-partial-messages`) |
| `hook_event` | — | `lifecycle` | Hook lifecycle event (requires `--include-hook-events`) |

### Event examples

#### System init
```json
{
  "type": "system",
  "subtype": "init",
  "session_id": "5620625c-b4c7-4185-9b2b-8de430dd2184",
  "cwd": "/path/to/project",
  "model": "claude-sonnet-4-5-20250929",
  "tools": ["Task", "Bash", "Glob", "Grep", "Read", "Edit", "Write", "WebFetch", "WebSearch"],
  "mcp_servers": [{"name": "ruby-tools", "status": "connected"}],
  "permissionMode": "default",
  "apiKeySource": "ANTHROPIC_API_KEY",
  "claude_code_version": "2.1.263"
}
```

#### Assistant message with tool use
```json
{
  "type": "assistant",
  "session_id": "5620625c-...",
  "message": {
    "model": "claude-sonnet-4-5-20250929",
    "role": "assistant",
    "content": [
      {"type": "text", "text": "I'll find the Ruby files."},
      {"type": "tool_use", "id": "toolu_01XYZ", "name": "Glob", "input": {"pattern": "**/*.rb"}}
    ],
    "usage": {"input_tokens": 150, "output_tokens": 42}
  }
}
```

#### Tool result (user message)
```json
{
  "type": "user",
  "session_id": "5620625c-...",
  "message": {
    "role": "user",
    "content": [
      {"type": "tool_result", "tool_use_id": "toolu_01XYZ", "content": "file1.rb\nfile2.rb"}
    ]
  },
  "tool_use_result": {"filenames": ["file1.rb", "file2.rb"], "durationMs": 45}
}
```

#### Result (success)
```json
{
  "type": "result",
  "subtype": "success",
  "is_error": false,
  "duration_ms": 7040,
  "num_turns": 2,
  "result": "I found 3 Ruby files...",
  "total_cost_usd": 0.0187,
  "session_id": "5620625c-...",
  "usage": {"input_tokens": 7, "output_tokens": 114},
  "modelUsage": {"claude-sonnet-4-5-20250929": {"inputTokens": 9, "outputTokens": 143, "costUSD": 0.0158}},
  "permission_denials": []
}
```

#### Stream event (token delta)
```json
{
  "type": "stream_event",
  "session_id": "4a7c99c6-...",
  "event": {
    "type": "content_block_delta",
    "index": 0,
    "delta": {"type": "text_delta", "text": "Hello"}
  }
}
```

### Stream event sub-types (within `event.type`)

| `event.type` | Description |
|---|---|
| `message_start` | Beginning of assistant message |
| `content_block_start` | Beginning of content block |
| `content_block_delta` | Incremental content (text_delta or input_json_delta) |
| `content_block_stop` | End of content block |
| `message_delta` | Message metadata update (stop_reason) |
| `message_stop` | End of assistant message |

## Programmatic API / SDK

- **Official SDK**: Claude Agent SDK
  - **TypeScript**: `@anthropic-ai/claude-code` (npm)
  - **Python**: `claude-code-sdk` (PyPI)
- **Key interfaces (TypeScript)**:
  - `query(prompt, options)` — run a query, returns async iterable of messages
  - Options: `model`, `maxTurns`, `maxBudgetUsd`, `systemPrompt`, `allowedTools`, `mcpConfig`, `permissionMode`, `sessionId`, `continue`
- **Key interfaces (Python)**:
  - `claude_code_sdk.query(prompt, options)` — async generator yielding messages
  - Message types: `SystemMessage`, `AssistantMessage`, `UserMessage`, `ResultMessage`, `StreamEvent`
- **Transport**: Process spawn over stdio (SDK spawns `claude` CLI as subprocess)
