# OpenCode

> **Vendor**: opencode-ai / opencode.ai
> **Command**: `opencode`
> **Language**: Go
> **Version**: 1.18.29
> **Repository**: https://github.com/opencode-ai/opencode
> **Docs**: https://opencode.ai/docs/
> **Status**: Actively maintained at opencode.ai. Not archived; not moved to Crush.

## Invocation

- **Interactive mode**: `opencode` (launches Bubble Tea TUI)
- **Non-interactive/headless mode**: `opencode run -p "prompt"` (or `opencode run --prompt "..."`)
- **Key top-level flags**:
  - `-p` / `--prompt` — run a single prompt in non-interactive mode (also available on `run` subcommand)
  - `-d` / `--debug` — enable debug logging
  - `-c` / `--cwd <path>` — set working directory
  - `--pure` — run without loading external plugins
  - `--agent <agent>` — select which agent to use
  - `--auto` — auto-approve all permissions (dangerous; skips all permission dialogs)
  - `--mini` — launch minimal interactive interface (reduced UI)
  - `--no-replay` — disable session history replay on startup
  - `--replay-limit <n>` — limit number of messages replayed from history
  - `--fork` — fork the session when continuing from a previous one
  - `--mdns` — enable mDNS service discovery
  - `--mdns-domain <domain>` — mDNS domain to advertise/discover

## `opencode run` subcommand

The `run` subcommand executes a single prompt non-interactively (headless mode):

```
opencode run [flags] [prompt]
```

### `run` flags

| Flag | Description |
|------|-------------|
| `--format` | Output format: `default` or `json` |
| `--variant <effort>` | Model variant / effort level: `minimal`, `high`, or `max` |
| `-f, --file <files>` | File attachments (can be specified multiple times) |
| `--agent <agent>` | Agent to use for this run |
| `--title <title>` | Session title |
| `--command <cmd>` | Command to run (alternative to inline prompt) |
| `--share` | Share the session (generates a shareable link) |
| `--attach <url>` | Attach to a running OpenCode server at the given URL |
| `--thinking` | Show thinking/reasoning blocks in output |
| `-i, --interactive` | Launch in interactive split-footer mode |
| `--auto` | Auto-approve all permissions for this run |
| `--fork` | Fork session when continuing from a previous session |
| `-p, --password` | Password for authenticating to a remote server |
| `-u, --username` | Username for authenticating to a remote server |

## Output Format

- **Default**: Plain text output to stdout
- **Structured JSON**: `opencode run --format json` — output wrapped in a JSON object
- **No streaming JSONL**: OpenCode does not support streaming JSON events; it outputs the final result only
- **Spinner**: A spinner animation is displayed while processing

### JSON output example

```json
{
  "result": "The use of context in Go provides a way to carry deadlines, cancellation signals, and request-scoped values across API boundaries..."
}
```

## Input Format

- **CLI argument**: `opencode run "prompt text"` or `opencode run --prompt "..."`
- **File attachments**: `opencode run -f file1.txt -f file2.py "prompt"`
- **Interactive TUI**: Type in the integrated Vim-like editor, send with Ctrl+S or Enter
- **Follow-up / multi-turn**: In interactive mode via session-based conversation; no multi-turn in non-interactive mode
- **External editor**: Ctrl+E opens preferred external editor for composing messages

## Permission Model

- **Non-interactive mode**: All permissions are auto-approved for the session by default
- **`--auto` flag**: Explicitly skips all permission dialogs (also available at top level)
- **Interactive mode**: Permission dialog with keyboard shortcuts:
  - `a` — allow
  - `A` — allow for session
  - `d` — deny
  - Arrow keys / Tab to navigate options
- **No granular tool filtering**: Unlike Claude Code, there is no `--allowedTools` equivalent
- **Configuration**: No per-tool permission config in the configuration file

## Subcommands

| Subcommand | Description |
|------------|-------------|
| `run` | Execute a prompt non-interactively (headless mode) |
| `serve` | Start the OpenCode HTTP API server |
| `web` | Open the OpenCode web interface |
| `attach` | Attach to a running OpenCode server |
| `acp` | Agent Communication Protocol — manage ACP connections |
| `mcp` | Manage MCP (Model Context Protocol) servers |
| `agent` | Manage and configure agents |
| `models` | List available models |
| `providers` | Manage AI providers and credentials (alias: `auth`) |
| `session` | Manage sessions (list, export, delete, etc.) |
| `plugin` | Manage plugins |
| `github` | GitHub integration helpers |
| `pr` | Pull request workflow helpers |
| `export` | Export session data |
| `import` | Import session data |
| `stats` | Display usage statistics |
| `db` | Database inspection and maintenance |
| `upgrade` | Upgrade OpenCode to the latest version |
| `uninstall` | Uninstall OpenCode |
| `debug` | Debug utilities and diagnostics |

## Session Management

- **Session creation**: Automatic on each invocation
- **List sessions**: Via `opencode session` subcommand or TUI (Ctrl+A to switch sessions)
- **Create new session**: Ctrl+N in TUI
- **Resume**: Sessions are persisted; switch between them in the TUI
- **Storage format**: SQLite database
- **Storage location**: `.opencode/` directory (configurable via `data.directory` in config)
- **History model**: Linear conversation per session
- **Replay control**: `--no-replay` disables replaying history on startup; `--replay-limit <n>` caps it
- **Fork**: `--fork` creates a new session branched from the current one
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
- Managed at runtime via `opencode mcp` subcommand
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

### Plugin system

- `--pure` flag disables all external plugins for a clean run
- Plugins managed via `opencode plugin` subcommand

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

## Server / API Mode

- **`opencode serve`**: Start the OpenCode HTTP API server for programmatic access
- **`opencode web`**: Open the OpenCode web interface (connects to local server)
- **`opencode attach`** / `run --attach <url>`: Connect a session to a running server
- **Remote auth**: `run -u <username> -p <password>` for authenticating to a remote server
- **mDNS**: `--mdns` / `--mdns-domain` enables local network service discovery

## Streaming Events Schema

OpenCode does **not** support a streaming events protocol in its CLI output. In non-interactive mode (`opencode run`), it outputs the final result only (as text or JSON).

Internally, the TUI uses Bubble Tea's message-passing architecture, but this is not exposed as a programmatic streaming interface. The HTTP server mode (`opencode serve`) may expose streaming endpoints — check the API docs at opencode.ai.

### Hypothetical event mapping (for IR design)

| Concept | OpenCode Equivalent | Notes |
|---|---|---|
| Session lifecycle | Session creation in SQLite | Not exposed as CLI events |
| Message streaming | TUI rendering / server SSE | Internal or server-side |
| Tool call | Permission dialog in TUI | Interactive only |
| File change | Write/Edit tool execution | No event emitted |
| Usage | Token counting (auto-compact) | Internal metric |
| Error | TUI error display | Not structured in CLI |

## Programmatic API / SDK

- **No official SDK**: OpenCode is CLI-only with no published SDK package
- **Non-interactive mode**: `opencode run --format json` is the primary programmatic interface
- **HTTP API**: Available via `opencode serve` (check opencode.ai docs for current API surface)
- **Database access**: Sessions stored in SQLite; could be queried directly via `opencode db`
- **Remote usage**: `opencode run --attach <url>` or `opencode attach` for connecting to a running server

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

Run `opencode models` to list all currently available models.
