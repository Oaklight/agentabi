# ACP Gateway

agentabi can expose any supported agent as an [Agent Client Protocol](https://agentclientprotocol.com/) (ACP) endpoint. The client speaks ACP over stdin/stdout; agentabi speaks each CLI's native interface internally. No external ACP adapter or Node runtime is needed.

## Run one agent

```bash
agentabi acp --agent opencode
```

| Agent | How agentabi drives it |
| --- | --- |
| `opencode` | The CLI's own ACP server, passed through |
| `agy` | The agentabi IR stream (`agy -p --output-format stream-json`) |
| `claude` | The agentabi IR stream (`claude --print --output-format stream-json`) |
| `codex` | The agentabi IR stream (`codex exec --json`) |
| `pi` | The agentabi IR stream (`pi --mode rpc`) |

`agy`, `claude`, `codex`, and `pi` accept `--prefer`:

- `--prefer native` (default) uses the subprocess provider.
- `--prefer sdk` puts the SDK provider first when it is installed.
- `--prefer pty` uses the interactive Claude Code TUI transport. It is slower and emits whole messages, but it counts against the interactive subscription allowance instead of the `sdk-cli` one.

## Use an external ACP server

Any CLI that ships an ACP server works without an agentabi provider:

```bash
agentabi acp --agent acp --acp-command "npx -y @agentclientprotocol/claude-agent-acp"
```

The gateway speaks ACP to that process and forwards its `session/update` payloads unchanged.

## Serve several agents

`--router` with repeated `--agent` starts one gateway process for several agents:

```bash
agentabi acp --router --agent claude --agent opencode
```

The client chooses the agent per session with `_meta.agent` in `session/new`:

```json
{"jsonrpc": "2.0", "id": 1, "method": "session/new",
 "params": {"cwd": "/work", "mcpServers": [],
            "_meta": {"agent": "opencode"}}}
```

- The first enabled agent is the default when `_meta.agent` is absent.
- An unknown name fails with `-32602` and lists the enabled agents.
- The selected agent is reported in the session config as an `agent` option. It is informational: ACP config options arrive with `session/new` and are set afterwards, so they cannot choose an agent at creation time. `session/set_config_option` on it returns an error; create a new session to switch.
- One backend instance per agent is shared by its sessions and stopped when its last session closes. A new session starts it again.

## Capability policy

`initialize` advertises the intersection of what every enabled and reachable backend honours, further limited by what the gateway implements. The agent is chosen after `initialize`, so per-agent capabilities cannot be advertised honestly there.

- `session/load`, `session/list`, `session/resume`, and `session/delete` are never advertised.
- Authentication is not proxied.
- A backend that cannot start is logged and left out of the intersection; selecting it later returns a clear error.
- The negotiated protocol version is the lowest version any reachable backend reports.

## Permission policy

Deny by default. Every session has a `permission_mode` config option:

| Value | Behaviour |
| --- | --- |
| `ask` | Forward each request to the client and wait for the answer. Default where the backend can ask. |
| `deny` | Reject every request without contacting the client. |
| `allow_all` | Allow every request. Reachable only by an explicit choice. |

- Claude asks over the SDK control protocol. Answering `allow_once` runs the tool; `reject_once` leaves it unrun and the turn continues.
- `allow_always` and `reject_always` are cached per session by the gateway. Claude's own `updatedPermissions` is not used: it writes `.claude/settings.local.json` and was measured not to suppress the next prompt.
- Backends that cannot round-trip (`codex`, `agy`, `opencode`, `pi`) offer only `deny` and `allow_all`; their provider's own policy applies otherwise.
- `--permission-timeout N` denies a request the client does not answer within N seconds. The default `0` waits.
- `session/cancel` while a request is pending resolves it as cancelled and ends the turn.

A client answers `session/request_permission` with the id of the option it was offered:

```json
{"outcome": {"outcome": "selected", "optionId": "allow_once"}}
{"outcome": {"outcome": "cancelled"}}
```

## Audit

Every routed method is recorded with method, agent, session, duration, and outcome on the `agentabi.acp.audit` logger. The log keeps the last 200 records in memory.

## Client example

The gateway speaks standard ACP, so any ACP client library works. The minimal exchange:

```json
{"jsonrpc": "2.0", "id": 1, "method": "initialize",
 "params": {"protocolVersion": 1, "clientCapabilities": {}}}
{"jsonrpc": "2.0", "id": 2, "method": "session/new",
 "params": {"cwd": "/work", "mcpServers": []}}
{"jsonrpc": "2.0", "id": 3, "method": "session/prompt",
 "params": {"sessionId": "...", "prompt": [{"type": "text", "text": "What is 2+2?"}]}}
```

Updates arrive as `session/update` notifications, and `session/prompt` returns when the turn ends.

## Limitations

- The gateway tags every update with `sessionId`, but the vendored `ACPClient.prompt()` uses one update queue. Concurrent prompts through one client object need per-session routing on the client side.
- Subagents, terminals, and structured diffs are not passed through; adapter fidelity is lossy for those features.
- `agentabi acp --agent acp` needs the external server's `--acp-command`; other agents ignore that flag.
