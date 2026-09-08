# Pi Coding Agent

> **Vendor**: Earendil Works (community)
> **Command**: `pi`
> **Package**: `@earendil-works/pi-coding-agent`
> **Language**: TypeScript (Node.js)
> **Version tested**: 0.85.1

## Invocation

### Interactive mode

```bash
pi                               # starts interactive TUI
pi "initial prompt"              # interactive with initial prompt
pi @prompt.md @image.png "msg"   # attach files to initial message
```

### Non-interactive (print) mode

```bash
pi -p "prompt"                   # print mode, text output
pi -p --mode json "prompt"       # JSON streaming output
pi -p --mode rpc "prompt"        # RPC mode
```

## CLI Flags

### Core flags

| Flag | Purpose |
|------|---------|
| `--print, -p` | Non-interactive mode |
| `--mode <mode>` | Output mode: `text` (default), `json`, `rpc` |
| `--model <pattern>` | Model pattern/ID (supports `provider/id` and `:<thinking>` suffix) |
| `--provider <name>` | Provider name |
| `--api-key <key>` | API key override |
| `--system-prompt <text>` | System prompt |
| `--append-system-prompt <text>` | Append to system prompt (repeatable) |
| `--verbose` | Force verbose startup |

### Reasoning control

| Flag | Purpose |
|------|---------|
| `--thinking <level>` | Thinking level: `off`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max` |
| `--models <patterns>` | Comma-separated model patterns for Ctrl+P cycling (supports globs, `:<thinking>` suffix) |

### Session management

| Flag | Purpose |
|------|---------|
| `--session <path\|id>` | Use specific session file or partial UUID (must exist) |
| `--session-id <id>` | Use exact project session ID (create-if-missing) |
| `--continue, -c` | Continue previous session |
| `--resume, -r` | Select a session to resume |
| `--fork <path\|id>` | Fork session into new session |
| `--no-session` | Ephemeral mode (don't save) |
| `--name, -n <name>` | Session display name |
| `--session-dir <dir>` | Directory for session storage |
| `--export <file>` | Export session to HTML |

### Tool control

| Flag | Purpose |
|------|---------|
| `--tools, -t <tools>` | Comma-separated allowlist of tool names |
| `--exclude-tools, -xt <tools>` | Comma-separated denylist of tool names |
| `--no-tools, -nt` | Disable all tools |
| `--no-builtin-tools, -nbt` | Disable built-in tools only |

### Extension/MCP control

| Flag | Purpose |
|------|---------|
| `--extension, -e <path>` | Load extension file (repeatable) |
| `--no-extensions, -ne` | Disable extension discovery |
| `--skill <path>` | Load skill file/directory (repeatable) |
| `--no-skills, -ns` | Disable skills discovery |
| `--approve, -a` | Trust project-local files |
| `--no-approve, -na` | Ignore project-local files |
| `--no-context-files, -nc` | Disable AGENTS.md/CLAUDE.md discovery |

### Theme/template control

| Flag | Purpose |
|------|---------|
| `--prompt-template <path>` | Load prompt template (repeatable) |
| `--no-prompt-templates, -np` | Disable template discovery |
| `--theme <path>` | Load theme file (repeatable) |
| `--use-theme <name>` | Set initial theme |
| `--no-themes` | Disable theme discovery |
| `--tui-mode <mode>` | TUI mode: `regular` or `fullscreen` |

## Output Format (JSON mode)

```jsonl
{"type":"session","version":3,"id":"...","timestamp":"...","cwd":"..."}
{"type":"agent_start"}
{"type":"turn_start"}
{"type":"message_start","message":{"role":"user","content":[...]}}
{"type":"message_end","message":{"role":"user","content":[...]}}
{"type":"message_start","message":{"role":"assistant","content":[],"api":"anthropic-messages","provider":"...","model":"..."}}
{"type":"message_update","assistantMessageEvent":{"type":"thinking_start","contentIndex":0}}
{"type":"message_update","assistantMessageEvent":{"type":"thinking_delta","contentIndex":0,"delta":"..."}}
{"type":"message_update","assistantMessageEvent":{"type":"thinking_end","contentIndex":0,"content":"..."}}
{"type":"message_update","assistantMessageEvent":{"type":"text_start","contentIndex":1}}
{"type":"message_update","assistantMessageEvent":{"type":"text_delta","contentIndex":1,"delta":"..."}}
{"type":"message_update","assistantMessageEvent":{"type":"text_end","contentIndex":1,"content":"..."}}
{"type":"message_end","message":{"role":"assistant","content":[{"type":"thinking",...},{"type":"text",...}],"stopReason":"stop"}}
{"type":"tool_execution_start","toolCallId":"...","toolName":"bash","args":{...}}
{"type":"tool_execution_end","toolCallId":"...","result":{"content":[{"type":"text","text":"..."}]},"isError":false}
{"type":"turn_end","message":{"role":"assistant","model":"...","usage":{...},"stopReason":"stop"}}
{"type":"agent_end","messages":[...],"willRetry":false}
```

### Message update subtypes

| Subtype | Description |
|---------|-------------|
| `thinking_start` | Thinking block begins |
| `thinking_delta` | Thinking text fragment |
| `thinking_end` | Thinking block ends (includes full content) |
| `text_start` | Text block begins |
| `text_delta` | Text fragment |
| `text_end` | Text block ends (includes full content) |

## Permission Model

Pi does not have a CLI-level permission mode flag. Project trust is controlled via:

- `--approve, -a` — Trust project-local files (AGENTS.md, skills, etc.) for this run
- `--no-approve, -na` — Ignore project-local files

## MCP Support

Yes, via the extension system:

- `--extension, -e <path>` — Load MCP extension file
- Extensions are discovered automatically unless `--no-extensions` is set
- `pi install <source>` / `pi remove <source>` — Install/remove extension sources
- `pi list` — List installed extensions
- `pi config` — TUI for enabling/disabling package resources

## Built-in Tools

`read`, `bash`, `edit`, `write`, `grep` (off by default), `find` (off by default), `ls` (off by default)

## Configuration

- Settings: `~/.pi/agent/settings.json` (provider, model, theme)
- Models: `~/.pi/agent/models.json` (custom provider endpoints and model definitions)
- Context files: AGENTS.md, CLAUDE.md (auto-discovered unless `--no-context-files`)
- Auth: Provider-specific env vars (ANTHROPIC_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY, etc.)

### Supported providers

40+ providers including: Anthropic, OpenAI, Azure, Google, DeepSeek, Groq, AWS Bedrock, Cloudflare, and many more (via `models.json` configuration).

## Subcommands

| Command | Purpose |
|---------|---------|
| `pi install <source>` | Install extension source |
| `pi remove <source>` | Remove extension source |
| `pi update [source\|self\|pi]` | Update Pi, extensions, or model catalogs |
| `pi list` | List installed extensions |
| `pi config` | TUI for package resource management |
| `pi auth <command>` | Print credentials or check provider readiness |
