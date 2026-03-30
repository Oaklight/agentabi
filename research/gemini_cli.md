# Gemini CLI

> **Vendor**: Google
> **Command**: `gemini`
> **Language**: TypeScript (Node.js)
> **Package**: `@google/gemini-cli` (npm)
> **Repository**: https://github.com/google-gemini/gemini-cli
> **Docs**: https://geminicli.com/docs/ and https://google-gemini.github.io/gemini-cli/
> **License**: Apache 2.0

## Invocation

- **Interactive mode**: `gemini` (launches interactive chat)
- **Headless/non-interactive mode**: `gemini "prompt"` or `gemini -p "prompt"` (positional arg triggers non-interactive)
- **Piping**: `echo "data" | gemini -p "analyze this"`
- **Key CLI flags**:
  - `-p "prompt"` — non-interactive prompt (alternative: positional argument)
  - `--output-format text|json|stream-json` — output format
  - `-m <model>` — specify model (e.g., `gemini-2.5-flash`)
  - `--include-directories <dirs>` — include additional directories
  - `--sandbox` — enable sandbox mode
  - `--debug` — verbose/debug output
  - `--allowed-tools <tools>` — tools allowed to run without confirmation
  - `--allowed-mcp-server-names <names>` — allowed MCP server names
  - `-e` / `--extensions <list>` — extensions to use
  - `-l` / `--list-extensions` — list available extensions
  - `--proxy <url>` — proxy for Gemini client
  - `--experimental-acp` — start agent in ACP mode

## Output Format

- **Default**: Plain text output to stdout
- **Structured JSON**: `--output-format json` — single JSON object:
  ```json
  {
    "response": "The architecture of this codebase...",
    "stats": {
      "input_tokens": 5000,
      "output_tokens": 300,
      "latency_ms": 2500
    }
  }
  ```
- **Streaming JSONL**: `--output-format stream-json` — newline-delimited JSON events

### JSON output schema

| Field | Type | Description |
|---|---|---|
| `response` | string | The model's final answer |
| `stats` | object | Token usage and API latency metrics |
| `error` | object (optional) | Error details if request failed |

## Input Format

- **Positional argument**: `gemini "prompt text"` (triggers non-interactive)
- **Flag**: `gemini -p "prompt text"`
- **Stdin piping**: `cat file.txt | gemini -p "summarize this"`
- **File references**: `gemini -p "Summarize @./summary.txt"`
- **MCP slash commands**: `gemini "/some-mcp-prompt"` (non-interactive MCP command)
- **Multi-turn**: Not supported in non-interactive mode; use checkpointing for conversation persistence

## Permission Model

- **Allowed tools**: `--allowed-tools <tools>` — tools that can run without confirmation
- **Allowed MCP servers**: `--allowed-mcp-server-names <names>` — restrict which MCP servers can be used
- **Interactive approval**: In interactive mode, tools request user confirmation before execution
- **Sandbox mode**: `--sandbox` — enables sandboxed execution
- **Trusted folders**: Configure execution policies by folder
- **No granular permission modes**: Unlike Claude Code, Gemini CLI does not have `--permission-mode` with named levels
- **Configuration**: Permission settings in `~/.gemini/settings.json`

## Session Management

- **Checkpointing**: Save and resume complex sessions
  - `/checkpoint save` — save current conversation state
  - `/checkpoint list` — list saved checkpoints
  - `/checkpoint restore <name>` — restore a checkpoint
- **No `--resume` flag**: Sessions are managed via checkpointing, not session IDs on the CLI
- **Conversation context**: Maintained within a single interactive session
- **Token caching**: Optimizes token usage across turns
- **Storage**: Local filesystem (managed by Gemini CLI)
- **History model**: Linear conversation within a session

## Tool System

### Built-in tools

| Tool | Description |
|------|-------------|
| `read_file` | Read file contents |
| `write_file` | Write/create files |
| `shell` | Execute shell commands |
| `web_fetch` | Fetch URL content |
| `google_search` | Google Search grounding for real-time information |
| `list_directory` | List directory contents |
| `search_files` | Search for files |
| `edit_file` | Edit file contents |

### MCP support

- **Yes**, via `~/.gemini/settings.json` configuration
- Supports `stdio` transport for local MCP servers
- Supports `streamable HTTP` transport for remote servers
- CLI management commands:
  - `gemini mcp add <name> -- <command>` — add stdio server
  - `gemini mcp add <name> --url <url>` — add HTTP server
  - `gemini mcp list` — list configured servers
  - `gemini mcp remove <name>` — remove a server
- Configuration format in `settings.json`:
```json
{
  "mcpServers": {
    "my-tool": {
      "command": "python",
      "args": ["server.py"],
      "env": {"KEY": "value"}
    }
  }
}
```
- MCP servers return structured `CallToolResult` with `ContentBlock` arrays (text + binary)
- MCP prompts can be run non-interactively: `gemini "/some-mcp-prompt"`

### Custom tool extension

- MCP servers are the primary extension mechanism
- Custom extensions can be built and shared
- Extensions system: `-e` / `--extensions` flag to select extensions

## Configuration

- **Project config file**: `GEMINI.md` at repo root (context/instructions file, similar to `CLAUDE.md`)
- **User/global config**: `~/.gemini/settings.json`
- **Key settings**:
  - `mcpServers` — MCP server definitions
  - `proxy` — proxy configuration
  - Model preferences
  - Tool permissions
- **Authentication options**:
  1. **Google OAuth** (Login with Google): Free tier — 60 req/min, 1000 req/day
  2. **Gemini API Key**: `GEMINI_API_KEY` environment variable
  3. **Vertex AI**: `GOOGLE_API_KEY` + `GOOGLE_GENAI_USE_VERTEXAI=true`
  4. **Google Cloud Project**: `GOOGLE_CLOUD_PROJECT` for Code Assist license
- **Environment variables**:
  - `GEMINI_API_KEY` — Gemini API key
  - `GOOGLE_API_KEY` — Google API key (Vertex AI)
  - `GOOGLE_CLOUD_PROJECT` — Google Cloud project ID
  - `GOOGLE_GENAI_USE_VERTEXAI` — enable Vertex AI

## Streaming Events Schema

When using `--output-format stream-json`, events are emitted as NDJSON (newline-delimited JSON).

### Event types

| Event type | Description | Semantic Category |
|---|---|---|
| `init` | Session metadata (session ID, model) | `session_lifecycle` |
| `message` | User and assistant message chunks | `message_streaming` |
| `tool_use` | Tool call requests with arguments | `tool_call` |
| `tool_result` | Output from executed tools | `tool_call` |
| `error` | Non-fatal warnings and system errors | `error` |
| `result` | Final outcome with aggregated statistics | `session_lifecycle` |

### Event examples

#### init
```json
{"type": "init", "session_id": "abc-123", "model": "gemini-2.5-flash"}
```

#### message
```json
{"type": "message", "role": "assistant", "content": "I'll analyze the codebase..."}
```

#### tool_use
```json
{"type": "tool_use", "tool": "read_file", "arguments": {"path": "src/main.ts"}}
```

#### tool_result
```json
{"type": "tool_result", "tool": "read_file", "content": "import express from 'express'..."}
```

#### error
```json
{"type": "error", "message": "Rate limit exceeded", "code": "RATE_LIMIT"}
```

#### result
```json
{"type": "result", "response": "The codebase uses Express...", "stats": {"input_tokens": 5000, "output_tokens": 300}}
```

### Exit codes

| Code | Meaning |
|---|---|
| `0` | Success |
| `1` | General error or API failure |
| `42` | Input error (invalid prompt or arguments) |
| `53` | Turn limit exceeded |

## Programmatic API / SDK

- **CLI-based**: Primary programmatic interface is `gemini -p "prompt" --output-format json|stream-json`
- **No standalone SDK**: Gemini CLI does not publish a separate SDK package for programmatic control
- **Google AI SDK**: For direct Gemini API access (separate from CLI):
  - Python: `google-genai` (pip)
  - TypeScript/JavaScript: `@google/genai` (npm)
  - Go, Java, Swift, Dart, and more
- **GitHub Action**: `gemini-cli` GitHub Action for CI/CD workflows
  - PR reviews, issue triage, on-demand assistance
  - `@gemini-cli` mention in issues/PRs for help
- **IDE integration**: VS Code companion extension
- **ACP mode**: `--experimental-acp` for Agent Communication Protocol (experimental)

### Models

- **Default**: Gemini 3 models (latest)
- **Available**: Gemini 2.5, 2.5 Flash, 2.0 Flash
- **Context window**: Up to 1M tokens
- **Free tier**: 60 requests/min, 1,000 requests/day with personal Google account
