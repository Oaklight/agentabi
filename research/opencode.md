# OpenCode

> **Vendor**: opencode-ai (original author), now continued as "Crush" by Charm team
> **Command**: `opencode`
> **Language**: Go
> **Repository**: https://github.com/opencode-ai/opencode (archived Sep 2025)
> **Docs**: https://opencode.ai/docs/
> **Status**: Archived. The project has continued under the name **Crush**, developed by the original author and the Charm team.

## Invocation

- **Interactive mode**: `opencode` (launches Bubble Tea TUI)
- **Headless/non-interactive mode**: `opencode -p "prompt"` (print mode)
- **Key CLI flags**:
  - `-p` / `--prompt` — run a single prompt in non-interactive mode
  - `-d` / `--debug` — enable debug logging
  - `-c` / `--cwd <path>` — set working directory
  - `-f` / `--output-format <format>` — output format: `text` (default) or `json`
  - `-q` / `--quiet` — hide spinner in non-interactive mode
  - `-h` / `--help` — display help

## Output Format

- **Default**: Plain text output to stdout
- **Structured JSON**: `opencode -p "prompt" -f json` — output wrapped in a JSON object
- **No streaming JSONL**: OpenCode does not support streaming JSON events; it outputs the final result only
- **Spinner**: A spinner animation is displayed while processing (suppress with `-q`)

### JSON output example

```json
{
  "result": "The use of context in Go provides a way to carry deadlines, cancellation signals, and request-scoped values across API boundaries..."
}
```

## Input Format

- **CLI argument**: `opencode -p "prompt text"`
- **Interactive TUI**: Type in the integrated Vim-like editor, send with Ctrl+S or Enter
- **Follow-up / multi-turn**: In interactive mode via session-based conversation; no multi-turn in non-interactive mode
- **External editor**: Ctrl+E opens preferred external editor for composing messages

## Permission Model

- **Non-interactive mode**: All permissions are auto-approved for the session
- **Interactive mode**: Permission dialog with keyboard shortcuts:
  - `a` — allow
  - `A` — allow for session
  - `d` — deny
  - Arrow keys / Tab to navigate options
- **No granular tool filtering**: Unlike Claude Code, there is no `--allowedTools` equivalent
- **Configuration**: No per-tool permission config in the configuration file

## Session Management

- **Session creation**: Automatic on each invocation
- **List sessions**: Via TUI (Ctrl+A to switch sessions)
- **Create new session**: Ctrl+N in TUI
- **Resume**: Sessions are persisted; switch between them in the TUI
- **Storage format**: SQLite database
- **Storage location**: `.opencode/` directory (configurable via `data.directory` in config)
- **History model**: Linear conversation per session
- **Auto-compact**: Automatically summarizes conversation when approaching context window limit (configurable, default: enabled at 95% usage)

## Tool System

### Built-in tools

| Tool | Description | Key Parameters |
|------|-------------|----------------|
| `bash` | Execute shell commands | `command`, `timeout` |
| `view` | View file contents | `file_path`, `offset`, `limit` |
| `write` | Write to files | `file_path`, `content` |
| `edit` | Edit files | Various (find-and-replace) |
| `patch` | Apply patches to files | `file_path`, `diff` |
| `glob` | Find files by pattern | `pattern`, `path` |
| `grep` | Search file contents | `pattern`, `path`, `include`, `literal_text` |
| `ls` | List directory contents | `path`, `ignore` |
| `fetch` | Fetch data from URLs | `url`, `format`, `timeout` |
| `sourcegraph` | Search code across public repos | `query`, `count`, `context_window` |
| `diagnostics` | Get LSP diagnostics | `file_path` |
| `agent` | Run sub-tasks with AI agent | `prompt` |

### MCP support

- **Yes**, configured in `.opencode.json` under `mcpServers`
- Supports `stdio` and `sse` transport types
- Configuration format:
```json
{
  "mcpServers": {
    "example": {
      "type": "stdio",
      "command": "path/to/mcp-server",
      "env": [],
      "args": []
    },
    "web-example": {
      "type": "sse",
      "url": "https://example.com/mcp",
      "headers": {"Authorization": "Bearer token"}
    }
  }
}
```
- MCP tools follow the same permission model as built-in tools

### Custom tool extension

- MCP servers are the primary extension mechanism
- Custom commands via Markdown files in config directories (see Configuration section)

## Configuration

- **Project config file**: No `AGENTS.md` equivalent (uses `.opencode.json` in project root)
- **Config file locations** (in priority order):
  1. `./.opencode.json` (project-local)
  2. `$XDG_CONFIG_HOME/opencode/.opencode.json`
  3. `$HOME/.opencode.json`
- **JSON Schema**: Available at `opencode-schema.json` in the repository
- **Key config sections**:
  - `providers` — API keys and provider settings (openai, anthropic, copilot, groq, openrouter)
  - `agents` — model and token config per agent type (coder, task, title)
  - `shell` — shell path and args
  - `mcpServers` — MCP server definitions
  - `lsp` — Language Server Protocol configuration
  - `data.directory` — storage directory (default: `.opencode`)
  - `autoCompact` — auto-compact feature toggle (default: true)
  - `debug` — debug mode
- **Environment variables**:
  - `ANTHROPIC_API_KEY` — for Claude models
  - `OPENAI_API_KEY` — for OpenAI models
  - `GEMINI_API_KEY` — for Gemini models
  - `GITHUB_TOKEN` — for GitHub Copilot
  - `GROQ_API_KEY` — for Groq models
  - `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION` — for AWS Bedrock
  - `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY` — for Azure OpenAI
  - `LOCAL_ENDPOINT` — for self-hosted models
  - `SHELL` — default shell

### Custom commands

- User commands: `$XDG_CONFIG_HOME/opencode/commands/` (prefixed with `user:`)
- Project commands: `<PROJECT>/.opencode/commands/` (prefixed with `project:`)
- Markdown files become commands; file name = command ID
- Support named arguments: `$NAME` placeholders (uppercase letters, numbers, underscores)
- Organize with subdirectories: `commands/git/commit.md` → `user:git:commit`
- Built-in commands:
  - Initialize Project — creates/updates OpenCode.md memory file
  - Compact Session — manually trigger summarization

## Streaming Events Schema

OpenCode does **not** support a streaming events protocol in its CLI output. In non-interactive mode, it outputs the final result only (as text or JSON).

Internally, the TUI uses Bubble Tea's message-passing architecture, but this is not exposed as a programmatic streaming interface.

### Hypothetical event mapping (for IR design)

| Concept | OpenCode Equivalent | Notes |
|---|---|---|
| Session lifecycle | Session creation in SQLite | Not exposed as events |
| Message streaming | TUI rendering | Internal only |
| Tool call | Permission dialog in TUI | Interactive only |
| File change | Write/Edit tool execution | No event emitted |
| Usage | Token counting (auto-compact) | Internal metric |
| Error | TUI error display | Not structured |

## Programmatic API / SDK

- **No official SDK**: OpenCode is CLI-only with no published SDK package
- **Non-interactive mode**: `opencode -p "prompt" -f json` is the programmatic interface
- **HTTP API**: Not implemented in the archived version (was on the roadmap)
- **Database access**: Sessions stored in SQLite; could be queried directly
- **Successor**: The project continues as "Crush" by the Charm team — check that project for updated programmatic interfaces

### Supported AI models

OpenCode supports a wide range of models:

- **OpenAI**: GPT-4.1, GPT-4.5, GPT-4o, O1, O3, O4 Mini
- **Anthropic**: Claude 4 Sonnet/Opus, Claude 3.5/3.7 Sonnet, Claude 3 Haiku/Opus
- **Google Gemini**: 2.5, 2.5 Flash, 2.0 Flash
- **GitHub Copilot**: Various models via Copilot token
- **AWS Bedrock**: Claude models
- **Groq**: Llama 4, QWEN, Deepseek R1
- **Azure OpenAI**: GPT-4.1, GPT-4.5, GPT-4o, O1, O3, O4 Mini
- **Google Cloud VertexAI**: Gemini models
- **Self-hosted**: Any OpenAI-compatible endpoint via `LOCAL_ENDPOINT`
