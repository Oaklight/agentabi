# Cursor CLI (cursor-agent)

> **Vendor**: Anysphere (Cursor)
> **Command**: `cursor-agent`
> **Language**: TypeScript (Node.js)
> **Install**: `curl https://cursor.com/install -fsS | bash`
> **Docs**: https://docs.cursor.com/docs/cli/overview
> **Note**: Originally known as "Pi" internally. The community project "anon-kode" was an unofficial fork of Claude Code that supported any OpenAI-compatible model; it was taken down via DMCA by Anthropic. Cursor CLI (`cursor-agent`) is Cursor's official terminal agent, distinct from anon-kode.

## Invocation

- **Interactive mode**: `cursor-agent` or `cursor-agent "prompt"` (launches TUI)
- **Headless/print mode**: `cursor-agent -p "prompt"` or `cursor-agent -p -m <model> "prompt"`
- **Key CLI flags**:
  - `-p` / `--print` — non-interactive mode (outputs result to stdout)
  - `--output-format text|json|stream-json` — output format
  - `--force` — allow file writes in print mode (otherwise changes are only proposed)
  - `-m` / `--model <name>` — select model (any model in Cursor subscription)
  - `--resume <id>` — resume a specific session
- **Session management**:
  - `cursor-agent ls` — list sessions
  - `cursor-agent resume` — resume most recent or by ID
- **Other commands**:
  - `cursor-agent update` — manual upgrade
  - `cursor-agent mcp ...` — manage MCP servers
  - `cursor-agent chat "prompt"` — direct chat mode

## Output Format

- **Default**: Human-readable text with progress lines
- **JSON**: `--output-format json` — single result JSON object
- **Streaming JSONL**: `--output-format stream-json` — NDJSON events (system init, deltas, tool calls, result)
- **Plain text**: `--output-format text` — progress lines

### Output format (JSON) example

```json
{
  "type": "result",
  "subtype": "success",
  "result": "The code review found 3 issues...",
  "session_id": "abc-123-def",
  "usage": {
    "input_tokens": 5000,
    "output_tokens": 200
  }
}
```

## Input Format

- **CLI argument**: `cursor-agent -p "prompt text"`
- **Interactive input**: Type in the TUI editor, send with Ctrl+S
- **Context selection**: Use `@` to include specific files or folders
- **Follow-up**: Use `I` key in interactive mode to add instructions
- **Compress**: `/compress` command to shrink context

## Permission Model

- **Interactive mode**: CLI asks for approval (Y/N) before shell commands
- **Print mode**: File writes gated behind `--force` flag (without it, changes are proposed but not applied)
- **Permission rules**: Configure in `.cursor/cli.json` or `~/.cursor/cli-config.json`
  - Allow/deny tokens: `Shell(git)`, `Read(src/**/*.ts)`, `Write(package.json)`
  - Similar syntax to Claude Code's `--allowedTools`
- **Trust model**: MCP tools require the workspace to be "trusted" via an interactive session first before headless use
- **Levels** (inferred from Claude Code heritage):
  - Default: approve each action
  - Restricted: deny rules limit available tools
  - Full autonomy: for sandbox/CI environments

## Session Management

- **Session creation**: Automatic on invocation
- **List sessions**: `cursor-agent ls`
- **Resume session**: `cursor-agent resume` (most recent) or `cursor-agent resume --resume <id>`
- **Storage**: Local filesystem
- **History model**: Linear conversation transcript
- **Context management**: `/compress` to summarize and reduce token usage

## Tool System

### Built-in tools

| Tool | Description |
|------|-------------|
| File read | Read file contents |
| File write | Create/modify files (requires `--force` in print mode) |
| File edit | Find-and-replace editing |
| Shell execution | Run shell commands (with approval) |
| Codebase search | Search across project files |
| Web search | Search the web |

### MCP support

- **Yes**, via `mcp.json` configuration file
- Auto-discovers MCP servers from project `mcp.json`
- Can list servers/tools via `cursor-agent mcp ...`
- **Limitation**: In headless mode, MCP tools require prior interactive trust of the workspace
- Configuration format (same as Claude Code):
```json
{
  "mcpServers": {
    "playwright": {
      "command": "npx",
      "args": ["@playwright/mcp@latest"]
    }
  }
}
```

### Custom tool extension

- MCP servers provide the extension mechanism
- Cursor also supports Plugins and Skills systems from the IDE

## Configuration

- **Project config files**:
  - `.cursor/rules/` — repo-scoped rules
  - `AGENTS.md` — agent instructions (read by CLI)
  - `CLAUDE.md` — also read by CLI (Claude Code compatibility)
- **User/global config**: `~/.cursor/cli-config.json`
- **Project config**: `.cursor/cli.json`
- **Environment variables**:
  - `CURSOR_API_KEY` — API key for headless/CI authentication
- **Rules system**: Same `.cursor/rules` as Cursor IDE

## Streaming Events Schema

When using `--output-format stream-json`, events are emitted as NDJSON. Based on the Claude Code heritage, the event schema is very similar:

### Event types

| Event type | Description | Semantic Category |
|---|---|---|
| System init | Session metadata, tools, model | `session_lifecycle` |
| Assistant message | Text + tool_use content blocks | `message_streaming` |
| User/tool result | Tool execution results | `tool_call` |
| Result | Final outcome with usage | `session_lifecycle` |
| Stream event | Token-level deltas | `message_streaming` |

### Example (result event from headless mode)

```json
{
  "type": "result",
  "subtype": "success",
  "result": "Generated a txt file with top largest density materials",
  "session_id": "...",
  "is_error": false,
  "duration_ms": 4500
}
```

**Note**: The exact streaming event schema for cursor-agent is not fully documented publicly. Based on community reports, it closely mirrors Claude Code's `stream-json` format (system, assistant, user, result, stream_event types) since cursor-agent shares architectural heritage with Claude Code's CLI design.

## Programmatic API / SDK

- **CLI-based**: Primary programmatic interface is `cursor-agent -p --output-format json`
- **Cloud Agent API**: Cursor offers a Cloud Agent API with REST endpoints for:
  - Creating tasks
  - Monitoring task status
  - Retrieving results
  - Documented at: https://docs.cursor.com/docs/cloud-agent/api/endpoints
- **ACP (Agent Communication Protocol)**: Cursor CLI supports ACP for inter-agent communication
  - `cursor-agent-acp` adapter available
  - Documented at: https://docs.cursor.com/docs/cli/acp
- **IDE integration**: Cursor CLI integrates with Cursor IDE, VS Code, JetBrains
- **No standalone SDK package**: Unlike Claude Code, there is no published npm/pip package for programmatic cursor-agent control
