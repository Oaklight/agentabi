# Antigravity CLI (agy)

> **Vendor**: Google
> **Command**: `agy`
> **Language**: Go (standalone binary)
> **Version tested**: 1.1.20
> **Predecessor**: Gemini CLI (discontinued June 2026)

## Invocation

### Interactive mode

```bash
agy                            # starts interactive TUI
agy "initial prompt"           # interactive with initial prompt
agy -i "prompt"                # run prompt then continue interactive
```

### Non-interactive (print) mode

```bash
agy -p "prompt"                # print mode, text output
agy --output-format json -p "prompt"         # single JSON result
agy --output-format stream-json -p "prompt"  # streaming JSONL events
```

**Note:** `-p`/`--print` takes the prompt as its value. `--output-format` must come before `-p`.

## CLI Flags

| Flag | Purpose |
|------|---------|
| `-p, --print` | Non-interactive mode (prompt as value) |
| `--output-format` | `text`, `json`, `stream-json` |
| `--model` | Model selection |
| `--effort` | Reasoning effort: `low`, `medium`, `high` |
| `--mode` | Execution mode: `accept-edits`, `plan` |
| `--dangerously-skip-permissions` | Bypass all permission checks |
| `--conversation <id>` | Resume by conversation ID |
| `--continue` | Resume most recent |
| `--add-dir <dirs...>` | Additional directories (repeatable) |
| `--json-schema <schema>` | Structured output (JSON schema string or file) |
| `--sandbox` | Sandbox mode |
| `--agent <agent>` | Select agent |
| `--disable-slash-commands` | Disable skills in print mode |
| `--print-timeout <Ns>` | Timeout for print mode (e.g. `300s`) |
| `--input-format` | `text` or `stream-json` (bidirectional streaming) |
| `--new-project` | Create new project for session |
| `--project` | Project ID or name |

## Output Format (stream-json)

```jsonl
{"event":"init","conversation_id":"...","init":{"cwd":"...","tools":[...],"permission_mode":"..."}}
{"event":"step_update","step_update":{"conversation_id":"...","step_index":0,"state":"DONE","step_type":"user_input"}}
{"event":"step_update","step_update":{"conversation_id":"...","step_index":1,"state":"DONE","step_type":"agent_response","text_delta":"...\n","duration_seconds":2.3,"usage":{"input_tokens":14158,"output_tokens":356,"thinking_tokens":354,"cache_read_tokens":0,"total_tokens":14514}}}
{"event":"step_update","step_update":{"conversation_id":"...","step_index":2,"state":"DONE","step_type":"checkpoint"}}
{"event":"result","result":{"conversation_id":"...","status":"SUCCESS","response":"...\n","duration_seconds":2.6,"num_turns":1,"usage":{...}}}
```

### Event types

| Event | step_type | Description |
|-------|-----------|-------------|
| `init` | — | Session metadata (cwd, tools, permission_mode) |
| `step_update` | `user_input` | User input step (skipped in IR) |
| `step_update` | `agent_response` | Model response with text_delta and usage |
| `step_update` | `tool_use` | Tool invocation with tool_name, tool_input, optional tool_result |
| `step_update` | `checkpoint` | Checkpoint marker (skipped in IR) |
| `step_update` | `error_message` | Error during execution |
| `result` | — | Final result: status, response, usage totals |

### Result status values

`SUCCESS`, `ERROR`, `FAILURE`, `TIMEOUT`

## Permission Model

| Mode | Flag |
|------|------|
| Bypass all | `--dangerously-skip-permissions` |
| Accept edits | `--mode accept-edits` |
| Plan only | `--mode plan` |
| Default | (omit flag) |

No `--allowed-tools` equivalent — agy's deprecated `--allowed-tools` was removed in favor of a Policy Engine.

## Session Management

- `--conversation <id>` — resume existing by ID
- `--continue` / `-c` — resume most recent
- No `--session-id` (create-if-missing) equivalent
- No ephemeral/no-session mode

## Configuration

- Auth: `GEMINI_API_KEY` + `GOOGLE_GEMINI_BASE_URL` environment variables
- Settings: `~/.gemini/antigravity-cli/settings.json` with `modelProvider` and `model.name`
- MCP: `agy mcp add/remove/list/enable/disable`
- Plugins: `agy plugin install/uninstall/list/enable/disable`
- Skills: `agy skills` subcommand (similar to Claude Code slash commands)

### Known quirks

- agy hardcodes a planner model (`gemini-3.1-pro-preview`) that cannot be overridden — the gateway must have this model configured
- Uses Google GenAI API format (`/v1beta/models/...`), not OpenAI-compatible

## MCP Support

Yes, via `agy mcp` subcommand and extension/plugin system.

## Tools

Built-in tools (from `init` event): `bash`/`run_command`, `view_file`, `write_to_file`, `replace_file_content`, `multi_replace_file_content`, `grep_search`, `find_by_name`, `list_dir`, `search_web`, `read_url_content`, `open_browser_url`, `execute_browser_javascript`, `call_mcp_tool`, and many more browser/subagent/task management tools.

## Differences from Gemini CLI

| Feature | Gemini CLI | agy |
|---------|-----------|-----|
| Language | TypeScript (Node.js) | Go (binary) |
| Event format | `init/message/tool_use/tool_result/result` | `init/step_update/result` |
| Permission flag | `--approval-mode` (4 levels) | `--dangerously-skip-permissions` only |
| Session ID | `--session-id <uuid>` | (not available) |
| System prompt | (none) | (none) |
| Tool filtering | `--allowed-tools` (deprecated) | (removed) |
| Auth | `GEMINI_API_KEY` env | `GEMINI_API_KEY` env (same) |
| Planner model | (none) | hardcoded `gemini-3.1-pro-preview` |
