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
- **Multi-turn follow-up**: Use `--continue` or `--resume <session-id>` with a new `-p "follow-up"`
- **Separator**: Use `--` between options and prompt to avoid flag parsing issues

## Permission Model

- **Permission modes** (via `--permission-mode`):
  - `default` — prompt for sensitive operations (file writes, shell commands)
  - `acceptEdits` — auto-approve file edits, prompt for shell commands
  - `plan` — planning mode, no execution of tools
  - `bypassPermissions` — skip all permission checks (also `--dangerously-skip-permissions`)
- **Tool filtering**:
  - `--allowedTools "Read,Edit,Bash"` — whitelist specific tools
  - `--disallowed-tools "Bash,Write"` — blacklist specific tools
  - Supports prefix matching with `*`: `Bash(git diff *)` allows any command starting with `git diff`
- **Configuration**: Permissions can be set in `~/.claude/settings.json` or project-level `.claude/settings.json`
- **Permission denials**: Reported in the `result` message's `permission_denials` array

## Session Management

- **Session creation**: Automatic on first `-p` invocation; `session_id` returned in system init message
- **Resume by ID**: `claude -p "continue" --resume "550e8400-..."`
- **Resume most recent**: `claude -p "continue" --continue`
- **Storage format**: JSON files
- **Storage location**: `~/.claude/sessions/`
- **History model**: Linear (conversation transcript)

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

### MCP support

- **Yes**, via `--mcp-config <path>` flag
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
  "claude_code_version": "2.1.3"
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
