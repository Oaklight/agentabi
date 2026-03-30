# OpenClaw

> **Vendor**: OpenClaw (Peter Steinberger / steipete, community)
> **Command**: `openclaw`
> **Language**: TypeScript (Node.js)
> **Repository**: https://github.com/openclaw/openclaw
> **Docs**: https://docs.openclaw.ai
> **Note**: OpenClaw is a personal AI assistant platform, not a coding-only agent. It routes messages across channels (WhatsApp, Telegram, Slack, Discord, etc.) through a Gateway. It includes a "Pi" agent runtime for coding tasks. The earlier project "Mentat" by AbanteAI is a separate, now-archived Python CLI coding assistant; the current AbanteAI "Mentat" is an AI-powered GitHub bot, distinct from OpenClaw.

## Invocation

- **Interactive mode**: `openclaw` (launches onboarding wizard if not configured)
- **Gateway mode**: `openclaw gateway --port 18789 --verbose` (starts the WS control plane)
- **Agent mode**: `openclaw agent --message "prompt" --thinking high` (direct agent invocation)
- **Send message**: `openclaw message send --to +1234567890 --message "Hello"`
- **Key CLI commands**:
  - `openclaw onboard --install-daemon` — guided setup wizard + daemon install
  - `openclaw gateway` — start the WebSocket gateway
  - `openclaw agent --message "prompt"` — talk to the agent directly
  - `openclaw channels login` — authenticate messaging channels
  - `openclaw doctor` — diagnose configuration issues
  - `openclaw update --channel stable|beta|dev` — update to a channel
  - `openclaw pairing approve <channel> <code>` — approve DM pairing
  - `openclaw nodes ...` — manage device nodes
  - `openclaw devices ...` — manage device pairing

## Output Format

- **Default**: Interactive TUI / gateway log output
- **Gateway WebSocket**: JSON messages over WebSocket (`ws://127.0.0.1:18789`)
- **No CLI JSON output mode**: Unlike Claude Code, OpenClaw does not have `--output-format json` for CLI
- **Channel output**: Responses delivered to messaging channels (WhatsApp, Telegram, etc.)

### Gateway WebSocket protocol

The Gateway uses a WebSocket-based protocol for real-time communication:
- Sessions, presence, config, cron, webhooks
- Tool streaming and block streaming from the Pi agent runtime
- RPC mode for agent interactions

## Input Format

- **CLI argument**: `openclaw agent --message "prompt"`
- **Messaging channels**: WhatsApp, Telegram, Slack, Discord, Google Chat, Signal, iMessage, IRC, Microsoft Teams, Matrix, LINE, and more
- **WebChat**: Built-in web UI served by the Gateway
- **Voice**: Voice Wake + Talk Mode on macOS/iOS/Android
- **Chat commands** (in messaging channels):
  - `/status` — session status
  - `/new` or `/reset` — reset session
  - `/compact` — compact session context
  - `/think <level>` — off|minimal|low|medium|high|xhigh
  - `/verbose on|off`
  - `/usage off|tokens|full` — per-response usage footer
  - `/restart` — restart gateway
  - `/activation mention|always` — group activation toggle

## Permission Model

- **Main session**: Tools run on the host with full access (single-user model)
- **Sandbox mode**: `agents.defaults.sandbox.mode: "non-main"` — runs non-main sessions (groups/channels) in Docker sandboxes
- **Sandbox defaults**:
  - Allowlist: `bash`, `process`, `read`, `write`, `edit`, `sessions_list`, `sessions_history`, `sessions_send`, `sessions_spawn`
  - Denylist: `browser`, `canvas`, `nodes`, `cron`, `discord`, `gateway`
- **Elevated bash**: `/elevated on|off` to toggle per-session elevated access
- **DM security**:
  - `dmPolicy="pairing"` — unknown senders must use pairing code
  - `dmPolicy="open"` — accept all DMs (opt-in)
  - `allowFrom` — allowlist for each channel
- **Tool permissions**: Per-tool approval in interactive mode, auto-approved for the main session

## Session Management

- **Session model**: Main session for direct chats, isolated group sessions
- **Multi-agent routing**: Route channels/accounts to isolated agents with separate workspaces
- **Session per channel**: Each messaging channel/group gets its own session
- **Inter-session communication**: `sessions_list`, `sessions_history`, `sessions_send`, `sessions_spawn` tools
- **Compaction**: `/compact` command or auto-compact
- **Persistence**: Gateway manages session state
- **Queue modes**: Configurable per session
- **History model**: Linear per session, with cross-session communication

## Tool System

### Built-in tools

| Tool | Description |
|------|-------------|
| `bash` | Execute shell commands |
| `process` | Process management |
| `read` | Read file contents |
| `write` | Write files |
| `edit` | Edit files |
| `browser` | Browser control (managed Chrome/Chromium with CDP) |
| `canvas` | A2UI visual workspace (push, reset, eval, snapshot) |
| `nodes` | Device node actions (camera, screen record, location, notifications) |
| `cron` | Scheduled tasks |
| `sessions_list` | List active sessions |
| `sessions_history` | Fetch session transcripts |
| `sessions_send` | Message another session |
| `sessions_spawn` | Spawn a new session |
| `discord` / `gateway` | Platform-specific actions |

### MCP support

- **Yes**, via Skills system and MCP integration
- ClawHub skill registry for discovering and installing skills
- Skills defined as Markdown files in `~/.openclaw/workspace/skills/<skill>/SKILL.md`
- Supports integration with external coding agents (Codex, Claude Code) as skills

### Custom tool extension

- **Skills system**: Bundled, managed, and workspace skills
  - Skills installed from ClawHub registry
  - Custom skills created as `SKILL.md` files
- **Workspace files**: `AGENTS.md`, `SOUL.md`, `TOOLS.md` for prompt injection
- **MCP servers**: Standard MCP integration

## Configuration

- **Project config file**: `AGENTS.md`, `SOUL.md`, `TOOLS.md` in workspace
- **User/global config**: `~/.openclaw/openclaw.json`
- **Key config sections**:
  - `agent.model` — model selection (e.g., `anthropic/claude-opus-4-6`)
  - `channels.*` — per-channel configuration (whatsapp, telegram, slack, discord, etc.)
  - `agents.defaults.sandbox` — sandbox configuration
  - `gateway.*` — gateway settings (bind, tailscale, auth)
  - `browser.*` — browser control settings
- **Environment variables**:
  - `TELEGRAM_BOT_TOKEN` — Telegram bot token
  - `DISCORD_BOT_TOKEN` — Discord bot token
  - `SLACK_BOT_TOKEN` / `SLACK_APP_TOKEN` — Slack tokens
  - Standard LLM provider keys (OpenAI, Anthropic, etc.)
- **Multiple providers**: Supports model failover and OAuth-based provider rotation

## Streaming Events Schema

OpenClaw uses a WebSocket-based event protocol through the Gateway, not CLI-based JSONL streaming.

### Gateway WebSocket events

| Event Category | Description | Semantic Category |
|---|---|---|
| Session events | Session creation, activation, queue | `session_lifecycle` |
| Agent message blocks | Streamed text blocks from Pi agent | `message_streaming` |
| Tool streaming | Tool invocations and results | `tool_call` |
| Presence | Typing indicators, online status | `session_lifecycle` |
| Media | Images, audio, video processing | `message_streaming` |
| System events | `openclaw system event --text "..." --mode now` | `session_lifecycle` |

### Gateway WS methods

| Method | Description |
|---|---|
| `sessions.patch` | Update session settings (model, thinkingLevel, etc.) |
| `node.list` / `node.describe` | Discover device nodes and capabilities |
| `node.invoke` | Execute actions on device nodes |

### Pi agent runtime

The Pi agent uses RPC mode with tool streaming and block streaming internally. Events are proxied through the Gateway WebSocket to connected clients.

**Note**: OpenClaw's event model is fundamentally different from CLI-based agents. It is designed as a multi-channel messaging platform with a persistent gateway, not a single-invocation CLI tool. The "events" are WebSocket messages, not JSONL lines.

## Programmatic API / SDK

- **No standalone SDK**: OpenClaw is operated via CLI commands and the Gateway WebSocket
- **Gateway WebSocket API**: `ws://127.0.0.1:18789` — full RPC interface for sessions, tools, and events
- **CLI commands**: `openclaw agent`, `openclaw message send`, etc.
- **Control UI**: Built-in web dashboard served by the Gateway
- **Docker integration**: `Dockerfile.sandbox` for sandboxed execution
- **Tailscale integration**: Remote access via Tailscale Serve/Funnel
- **Plugin SDK**: TypeScript types available via `tsconfig.plugin-sdk.dts.json`
