# OpenCode V2 HTTP/SSE API — probed on 2026-11-10

Probe of `opencode serve` 1.19.0 on Linux, plus the reference material in
`research/session-wire/` (FORMATS.md §6, `captures/opencode/`,
`schemas/opencode/openapi.json`). Version and paths are what this CLI
answered; treat them as observed, not guaranteed.

## Startup

```bash
opencode serve --port 4123 --hostname 127.0.0.1
# stderr/stdout: "opencode server listening on http://127.0.0.1:4123"
```

- `--port 0` does **not** pick an ephemeral port; it fell back to 4096.
  Reserve a free port with a socket bind and pass it explicitly.
- The process warns `OPENCODE_SERVER_PASSWORD is not set; server is
  unsecured` when unauthenticated. `agentabi` treats that as the default.
- Sessions live in shared OpenCode storage: a second server process on
  another port answers `GET /api/session/{id}` for a session created by the
  first.

## Endpoints used by the provider

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/global/health` | `{"healthy":true,"version":"1.19.0"}` |
| GET | `/api/session/active` | `{"data":{}}`; `{"data":{"ses_x":{"type":"running"}}}` while a turn runs |
| POST | `/session?directory=<dir>` | Create a session (no `POST /api/session` exists). Body `{"title","model"}` → `{"id":"ses_..."}` |
| GET | `/api/session/{id}` | `{"data":{...}}`, includes `model`, `tokens`, `cost` |
| POST | `/api/session/{id}/model` | `{"model":{"providerID","id"}}` → `204`. A bad model id is accepted here; it fails later |
| POST | `/api/session/{id}/prompt` | `{"prompt":{"text"},"delivery":"steer"}` → `{"data":{"id":"msg_...","admittedSeq":N,...}}` |
| GET | `/api/event` | Server-wide `V2Event` SSE, **includes** `text.delta`, no replay |
| GET | `/api/session/{id}/event?after=<seq>` | Durable SSE, **replays from seq 1** and omits `text.delta` |
| POST | `/api/session/{id}/interrupt` | `204`; idle is a no-op |
| POST | `/api/session/{id}/permission/{requestID}/reply` | `{"reply":"once"\|"always"\|"reject"}` |
| POST | `/api/session/{id}/question/{requestID}/reject` | Reject an interactive question |
| GET | `/config` | Includes the default `model` as `provider/model` |
| POST | `/api/session/{id}/wait` | Returns `503 ServiceUnavailableError` ("not available yet") in 1.19.0 |

`GET /session/status` (V1) returns `{}` during a V2 turn, so it cannot be
used for completion.

## SSE envelope

`/api/event` frames carry `data: {...}` with no `event:` name. The object
shape matches the OpenAPI `V2Event`:

```json
{"id":"evt_...","type":"session.next.text.delta","location":{"directory":"..."},
 "durable":{"aggregateID":"ses_...","seq":12,"version":1},
 "data":{"timestamp":1794303589994,"sessionID":"ses_...","assistantMessageID":"msg_...","textID":"text-0","delta":"OK"}}
```

`session.next.text.delta` has no `durable` block (it is not persisted).
`: heartbeat` comment frames appear every 10 s.

## Observed event sequence (one tool turn)

```
server.connected
session.next.prompt.admitted   data.messageID = user message id
session.next.prompted
session.next.context.updated   (large skill preamble, only sometimes)
session.next.step.started      data.assistantMessageID, data.model{providerID,id,variant}
session.next.tool.input.started | .delta | .ended   data.callID, name, text
session.next.tool.called       data.callID, tool, input
session.next.tool.success      data.callID, content[{type:"text",text}], structured
session.next.step.ended        data.finish="tool-calls", cost, tokens{input,output,reasoning,cache{read,write}}
session.next.step.started      (next step)
session.next.text.started | .delta | .ended
session.next.step.ended        data.finish="stop", cost, tokens{...}
```

`session.idle` was **not** emitted on the V2 path in any probe (plain reply,
tool turn, or after an interrupt), even though the schema still lists it.
Turn completion must be detected from `session.next.step.ended` with a
finish other than `tool-calls` (`stop`, `length`, ...).

`session.next.step.failed` carries `data.error {type:"unknown", message}`.
`session.error` carries one of the V1 error unions (`{name, data{message}}`,
e.g. `ProviderAuthError`).

## Cancel behaviour

`POST /api/session/{id}/interrupt` mid-turn returned `204`; no further
events arrived for that prompt, and `GET /api/session/active` dropped back
to `{"data":{}}` within a second. The provider therefore interrupts on
timeout and on task cancellation.

## Model selection

`POST /api/session/{id}/model` accepted an unknown model id (`204`) and the
turn then stalled without events. Model validation is not reliable at this
endpoint; a stalled turn is caught by the provider timeout.

## V1 fallback

The V1 subprocess (`opencode run --format json`) is unchanged and stays in
the provider chain. Selection:

1. `agent_extensions.opencode.transport` = `v1` / `v2` / `auto`
2. `AGENTABI_OPENCODE_TRANSPORT` = `v1` / `v2` / `auto`
3. `auto`: V2 if a base URL is configured or `opencode --version` is at
   least 1.19.0; otherwise V1.

In `auto`, a server that fails to start or lacks `/api/session/active`
falls back to V1. A **configured** base URL is authoritative: if it is not
usable the task fails with an error instead of silently running V1.

## What the provider maps

| V2 event | IR |
| --- | --- |
| `session.next.step.started` | `message_start` (session_start is emitted once before prompting) |
| `session.next.text.delta` | `message_delta` |
| `session.next.text.ended` | `message_delta`, only when no deltas arrived for that `textID` |
| `session.next.reasoning.delta` | `reasoning_delta` |
| `session.next.tool.called` | `tool_use` |
| `session.next.tool.success` / `.failed` | `tool_result` (`is_error` on failure, `duration_ms` from event timestamps) |
| `session.next.step.ended` | `usage` + `message_end`; `session_end` when finish is not `tool-calls` |
| `session.next.step.failed` / `session.error` | `error` (fatal) + `session_end` |
| `session.next.retried` | `error` (non-fatal) |
| `permission.v2.asked` | `permission_request` + `permission_response`; auto-reply `always` for `full_auto`, `once` for `accept_edits`, otherwise reject + fatal error |
| `question.v2.asked` | fatal `error` (interactive questions are unsupported) |
| `session.idle` | `session_end` (defensive; not emitted in 1.19.0) |

Usage maps `tokens.input/output/reasoning` and `tokens.cache.read/write`;
`total_tokens` is `input + output`.
