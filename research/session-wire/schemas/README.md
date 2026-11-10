# Schemas

Generated protocol schemas used to interpret captures.

## Codex app-server

Source: `codex app-server generate-json-schema --experimental`.

```bash
mkdir -p schemas/codex
env -u AGENT_PROFILE \
  CODEX_HOME=/tmp/empty-codex \
  codex app-server generate-json-schema --experimental -o schemas/codex
```

`AGENT_PROFILE` must be unset: the local `codex` shell function injects
`--profile`, and `app-server` rejects it.

Key files:

- `codex_app_server_protocol.v2.schemas.json` — all v2 definitions
- `ServerRequest.json`, `ServerNotification.json` — JSON-RPC envelopes
- `CommandExecutionRequestApprovalParams.json` — exec approval
- `PermissionsRequestApprovalParams.json` — permission profile approval
- `ToolRequestUserInputParams.json` — user-input request

## OpenCode HTTP API

Source: `opencode serve` then `GET /doc`.

```bash
opencode serve --port 4099 --hostname 127.0.0.1
curl -s http://127.0.0.1:4099/doc -o schemas/opencode/openapi.json
```

Key schemas:

- `EventSessionCreated`
- `EventMessageUpdated`
- `EventSessionNextTextDelta`
- `EventPermissionAsked`
- `EventQuestionAsked`
- `EventSessionIdle`

## Claude Code

No public JSON Schema. Captures are the source of truth. Cross-session
messaging is a newline-delimited JSON protocol over a per-session Unix
socket; see `captures/claude/`.
