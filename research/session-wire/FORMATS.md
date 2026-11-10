# Agent Session Wire Formats — Observed

Captured 2026-11-07 on Linux 6.18.59. Raw captures live in `captures/`
(gitignored). This document keeps only redacted, minimal shapes.

## 1. Transport families

| Family | CLIs observed | Envelope |
| --- | --- | --- |
| NDJSON event stream | Claude Code, agy, Pi | one JSON object per line, `event`/`type` field |
| JSON-RPC 2.0 | Codex app-server, ACP (Goose/OpenCode/agy), OpenClaw | `jsonrpc`, `id`, `method`, `params`; replies use the same `id` |
| HTTP + SSE | OpenCode, Crush | SSE `data: {...}` with `type` + `properties` |
| Control-channel receipts | Qwen Code | message frame plus a separate `control/delivery_status` frame |
| Text only | Aider | no session wire; `.aider.chat.history.md` |

## 2. Common message taxonomy

| Semantic | Claude Code | Codex | agy | OpenCode | ACP | Qwen | OpenClaw |
| --- | --- | --- | --- | --- | --- | --- | --- |
| session start | `system/init` | `thread/started` | `event:init` | `session.created` | `session/new` result | registry record | `sessions.create` |
| user message | `stream_event` + `message_start` | `item/started` (userMessage) | `step_update` user_input / `event:user` input | `message.updated` role=user | `session/prompt` | `type:user` frame | `sessions.send` |
| assistant delta | `stream_event/content_block_delta` | `item/agentMessage/delta` | `step_update` text_delta | `session.next.text.delta` | `session/update` agent_message_chunk | — | `sessions.changed` + chat events |
| tool call | `tool_use` | `item/started` + `item/completed` | `step_update` tool (blocked) | `message.part.updated` / `session.next.tool.*` | `session/update` tool_call | — | `chat` events |
| approval request | UI-local, no peer API | `item/commandExecution/requestApproval` | `ask_permission` tool (not captured) | `permission.asked` / ACP `session/request_permission` | `session/request_permission` | hold + `/peers` | operator approval |
| approval response | user at terminal | `{"decision": ...}` with same id | — | `optionId` with same id | `{"outcome":{"outcome":"selected","optionId":...}}` | `/peers` release | operator client |
| peer send | `SendMessage` | `codex queue` | `send_message` tool (blocked) | none native | none native | `type:user` frame | `sessions.send` |
| delivery ack | `SendMessage` result | queue result | — | — | — | `control/delivery_status` | `{runId,status:"started"}` |
| idle / done | `result` | `turn/completed` | `result` | `session.idle` | `session/prompt` result | — | `sessions.changed` |

## 3. Claude Code

### 3.1 stream-json

```json
{"type":"system","subtype":"init","session_id":"822782ca-...","tools":["Task","Bash","SendMessage","ListAgents"]}
{"type":"stream_event","event":"content_block_delta", "delta":{"type":"text_delta","text":"OK"}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"OK"}]}}
{"type":"result","subtype":"success","is_error":false,"num_turns":1,"result":"OK","usage":{...}}
```

### 3.2 Cross-session envelope (receiver side)

```json
{
  "type": "user",
  "isMeta": true,
  "message": {"role":"user","content":"Another Claude session sent a message:\n<cross-session-message from=\"uds:/run/user/1000/cc-socks/3247740.sock\" from-name=\"agentabi-c1\" from-mode=\"bypass\">...body...</cross-session-message>"},
  "origin": {
    "kind": "peer",
    "from": "uds:/run/user/1000/cc-socks/3247740.sock",
    "verifiedPeerPid": 3247740,
    "verifiedPeerProcStart": "17719993",
    "msg_id": "69798bc2-c1c8-46c7-b05a-ba863e90db08",
    "name": "agentabi-c1",
    "fromMode": "bypass",
    "body": "..."
  },
  "turnOrigin": "peer",
  "userType": "external"
}
```

### 3.3 Peer socket wire (captured)

Each session listens on `$XDG_RUNTIME_DIR/cc-socks/<pid>.sock`. A sender
opens one connection per message and writes NDJSON:

```json
{"type":"auth","token":"<redacted>"}
{"msgV":1,"msg_id":"<uuid>","type":"user","message":{"role":"user","content":"<cross-session-message from=\"uds:/run/user/1000/cc-socks/3247740.sock\" from-name=\"agentabi-coordinator\" from-mode=\"bypass\">\n<body>\n</cross-session-message>"},"priority":"next","from":"uds:/run/user/1000/cc-socks/3247740.sock"}
```

Observed on Claude Code 2.1.299:

| Property | Observed |
| --- | --- |
| Framing | one JSON object per line, LF-terminated |
| Direction | client → server only; the server never writes a reply |
| Ack | none on the wire; the `SendMessage` result is local |
| Connection | one connection per send, plus an empty probe connection (`connect`+`close`) before it |
| Auth | `{"type":"auth","token":...}` is optional; missing or wrong tokens are accepted |
| Identity | `from`, `from-name`, `from-mode` are self-asserted strings |
| Peer pid | receiver records `verifiedPeerPid` from `SO_PEERCRED`; it is the connecting helper pid, not the session pid |
| Duplicate handling | none; two frames with the same `msg_id` are both delivered |
| Frame limit | 1,048,576 bytes including LF; larger frames are silently dropped and the connection stays open |
| Oversize response | no error frame, no close |

Inbound policy key `crossSessionInbound`:

| Value | Behavior |
| --- | --- |
| `accept` | deliver regardless of the sender's mode class |
| `hold` (default) | if the envelope's `from-mode` class differs from the receiver's, store it as `Held peer message — from <addr> [verified pid <pid>] (peer claims name: <name>); preview: «...»` and do not deliver; the held counter increments |
| `refuse` | silent drop; no transcript entry |

Policy caveats:
- The mode class comes from the envelope's `from-mode`. Omitting it, or
  setting it to the receiver's class (`default`), bypasses `hold`; a
  spoofed `from-mode="bypass"` is held. The check is content-asserted,
  not authenticated.
- Held messages surface in the receiver's own transcript/UI. There is no
  machine-wide approval inbox.
- The model sees `Another Claude session sent a message:` plus a fixed
  safety preamble; the raw envelope remains in the content.

Same-machine traffic does not go through Anthropic servers.

## 4. Codex app-server

```json
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"clientInfo":{"name":"probe","version":"0.0.0"}}}
{"id":1,"result":{"userAgent":"probe/0.162.1 ...","codexHome":"/home/pding/.codex","platformFamily":"unix","platformOs":"linux"}}
{"method":"thread/started","params":{"thread":{"id":"01a1b73d-...","sessionId":"01a1b73d-...","cwd":"..."}}}
{"method":"turn/started","params":{"threadId":"...","turn":{"id":"...","status":"inProgress"}}}
{"method":"item/started","params":{"item":{"type":"userMessage","id":"..."},"threadId":"...","turnId":"..."}}
{"method":"item/agentMessage/delta","params":{"threadId":"...","turnId":"...","itemId":"msg_...","delta":"OK"}}
```

Approval is a server-to-client request:

```json
{"method":"item/commandExecution/requestApproval","id":0,"params":{"kind":"command","threadId":"...","turnId":"...","itemId":"call_...","approvalId":null,"reason":"...","command":"/usr/bin/bash -lc 'touch ...'","availableDecisions":["accept",{"acceptWithExecpolicyAmendment":{...}},"cancel"]}}
```

```json
{"jsonrpc":"2.0","id":0,"result":{"decision":"decline"}}
{"method":"serverRequest/resolved","params":{"threadId":"...","requestId":0}}
```

Notes:
- `id` is the server request id, not a JSON-RPC increment; the reply must echo it.
- `availableDecisions` may omit `decline`, but `decline` is accepted.
- `approvalId` is non-null only for multi-callback bridges (zsh/stdin).
- `codex exec --json` emits only `thread.started`, `item.completed`,
  `turn.started`, `turn.completed`; no deltas.

## 5. agy

```json
{"event":"init","conversation_id":"...","init":{"cwd":"...","tools":["ask_permission","send_message","invoke_subagent","manage_inbox",...],"permission_mode":"request-review"}}
{"event":"step_update","step_update":{"conversation_id":"...","step_index":0,"state":"DONE","step_type":"user_input"}}
{"event":"step_update","step_update":{"conversation_id":"...","step_index":1,"state":"ACTIVE","step_type":"agent_response","text_delta":"OK"}}
{"event":"result","result":{"conversation_id":"...","status":"SUCCESS","response":"OK\n","num_turns":1,"usage":{...}}}
```

NDJSON input (`--input-format stream-json --output-format stream-json -p=`):

```json
{"event":"user","message":{"role":"user","content":[{"type":"text","text":"Reply with exactly: OK"}]}}
```

ACP (`agy acp`): `initialize` → `session/new` → `session/prompt`;
`session/update` kinds observed: `available_commands_update`,
`user_message_chunk`, `session_info_update`, `agent_message_chunk`.

Blocked: with rosetta's `genai` wire, every prompt fails before the model
call because the built-in `send_message` tool schema is rejected:

```text
Invalid schema for function 'send_message': "1" is not of type "integer"
```

This is not an MCP issue. Disabling `toolregistry-server-local` removes
the slash-name error, but `send_message` still fails. agy's peer tool
exists (`init.tools` lists it), but its frame format could not be
captured through rosetta.

## 6. OpenCode

V1 (`POST /session/{id}/message`):

```json
{"type":"message.updated","properties":{"sessionID":"ses_...","info":{"id":"msg_...","role":"user","agent":"build","model":{"providerID":"rosetta","modelID":"deepseek-v4.1-flash"}}}}
{"type":"message.part.updated","properties":{"part":{"type":"text","text":"OK","messageID":"msg_...","id":"prt_..."}}}
{"type":"session.idle","properties":{"sessionID":"ses_..."}}
```

V2 (`POST /api/session/{id}/prompt`):

```json
{"type":"session.next.text.delta","properties":{"sessionID":"ses_...","assistantMessageID":"msg_...","textID":"text-0","delta":"OK"}}
```

ACP permission:

```json
{"jsonrpc":"2.0","id":0,"method":"session/request_permission","params":{"sessionId":"ses_...","toolCall":{"toolCallId":"call_...","title":"read","kind":"read","status":"pending","locations":[],"rawInput":{}},"options":[{"optionId":"once","kind":"allow_once","name":"Allow once"},{"optionId":"always","kind":"allow_always","name":"Always allow"},{"optionId":"reject","kind":"reject_once","name":"Reject"}]}}
{"jsonrpc":"2.0","id":0,"result":{"outcome":{"outcome":"selected","optionId":"reject"}}}
```

Notes:
- V1 and V2 are different event universes; V2 uses `session.next.*`.
- V2 needs `POST /api/session/{id}/model` before prompting.
- HTTP `permission.asked` exists in the OpenAPI schema but was not
  captured; ACP `session/request_permission` was.

## 7. Goose ACP

```json
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":1,"clientCapabilities":{"fs":{"readTextFile":true,"writeTextFile":true}},"clientInfo":{"name":"probe","version":"0.0.0"}}}
{"jsonrpc":"2.0","id":2,"method":"session/new","params":{"cwd":"/tmp/goose-acp-probe-work","mcpServers":[]}}
{"jsonrpc":"2.0","id":25,"method":"session/set_mode","params":{"sessionId":"20261107_2","modeId":"approve"}}
{"jsonrpc":"2.0","id":4,"method":"session/prompt","params":{"sessionId":"20261107_2","prompt":[{"type":"text","text":"Run echo probe using the shell tool."}]}}
```

Permission request:

```json
{"jsonrpc":"2.0","id":"8f193702-3702-4b64-9836-2f3c419b7c3c","method":"session/request_permission","params":{"sessionId":"20261107_2","toolCall":{"toolCallId":"call_probe_1","kind":"other","status":"pending","title":"shell · echo probe","rawInput":{"command":"echo probe"}},"options":[{"optionId":"allow_always","name":"allow_always","kind":"allow_always"},{"optionId":"allow_once","name":"allow_once","kind":"allow_once"},{"optionId":"reject_once","name":"reject_once","kind":"reject_once"},{"optionId":"reject_always","name":"reject_always","kind":"reject_always"}]}}
{"jsonrpc":"2.0","id":"8f193702-3702-4b64-9836-2f3c419b7c3c","result":{"outcome":{"outcome":"selected","optionId":"allow_always"}}}
```

Notes:
- `session/new` reports modes: `auto`, `approve`, `smart_approve`, `chat`.
- `auto` never asks; `approve` asks every tool call.
- Permission request `id` is a UUID string, not the numeric JSON-RPC id.

## 8. Qwen Code peer protocol

```json
{"msgV":1,"type":"auth","token":"<redacted>"}
{"msgV":1,"msgId":"<uuid>","type":"user","from":"/run/user/1000/qwen-socks/<pid>.sock","replyToken":"<redacted>","fromName":"wireprobe","fromMode":"bypass","toSessionId":"13ccf960-...","priority":"next","message":{"role":"user","content":"probe bravo"}}
```

Receipt (separate reverse connection):

```json
{"msgV":1,"msgId":"<fresh uuid>","type":"control","action":"delivery_status","status":"held","origMsgId":"<sent uuid>","from":"...","reason":"Your message is held for the recipient user to review before it reaches their Qwen Code session."}
```

Status: `held` / `delivered` / `denied` / `refused` / `expired` /
`misaddressed` / `dropped`; `dropped` carries `dropReason` =
`duplicate` / `rate-limited` / `queue-full`.

Observed behavior:
- `fromMode=prompting` → `held`; `fromMode=bypass` → `delivered`.
- Duplicate detection is by body, not `msgId`, within 30 seconds.

Registry `$QWEN_HOME/sessions/<pid>.json`:

```text
schemaVersion=1, pid, procStart=<boot_id>:<start_ticks>, pidNs,
sessionId, cwd, name=<cwd basename>-<sha256(sessionId)[0:2]>,
startedAt, qwenVersion, kind∈{tui,headless,serve,external},
ipcPath=$XDG_RUNTIME_DIR/qwen-socks/<pid>.sock, ipcToken=64 hex,
ref=sha256(sessionId)[0:6]
```

## 9. OpenClaw

```json
{"type":"req","id":"probe-list-...","method":"sessions.list","params":{"limit":10,"includeGlobal":true,"includeUnknown":true}}
{"type":"req","id":"probe-send-...","method":"sessions.send","params":{"key":"agent:dev:probe","agentId":"dev","message":"wire probe: reply with exactly OK","timeoutMs":3000,"idempotencyKey":"probe-..."}}
{"type":"res","id":"probe-send-...","ok":true,"payload":{"runId":"probe-...","status":"started"}}
{"type":"event","event":"sessions.changed","payload":{"sessionKey":"agent:dev:probe","agentId":"dev","reason":"send","sessionId":"20c10333-...","phase":"model"}}
```

- Address is `sessionKey` (`agent:<agentId>:<name>`), not `label`.
- `sessions.send` ack is admission only: `{runId,status:"started"}`.
- `sessions.describe` returns status, `hasActiveRun`, `activeRunIds`,
  `lastRunId`, `placement.state`.
- `idempotencyKey` is the client retry identity.

## 10. Pi

```json
{"type":"session","version":3,"id":"01a1b73f-...","timestamp":"...","cwd":"..."}
{"type":"message_start","message":{"role":"user","content":[{"type":"text","text":"Reply with exactly OK"}]}}
{"type":"message_end","message":{"role":"assistant","content":[{"type":"text","text":"OK"}],"responseId":"c3181ac4-...","stopReason":"stop"}}
{"type":"turn_end","message":{"role":"assistant","content":[{"type":"text","text":"OK"}],"responseId":"c3181ac4-..."}}
```

Core has no peer/agent-discovery/send command. Third-party
`pi-collaborating-agents` adds a file-based registry and `agent_message`
tool, but it was not installed.

## 11. GitHub Copilot CLI

Event chain:

```text
user.message → assistant.message_delta → assistant.tool_call_delta
→ tool.execution_start → tool.shell_output → tool.execution_complete
```

Non-interactive permission denial:

```json
{"type":"tool.execution_complete","data":{"toolCallId":"call_...","interactionId":"...","turnId":"0","success":false,"error":{"code":"denied","message":"Permission denied because no interactive user response was available. Retry in an interactive session so the user can approve it, ..."}}}
```

There is no separate permission-request event in non-interactive mode.
`sessionId` is on `result`; causality uses envelope `id`/`parentId` plus
`turnId`, `interactionId`, `toolCallId`.

## 12. Aider

No session wire and no peer protocol. One process is one chat.
Scriptable entries: `--message`, `--message-file`, `--watch-files`.
The only structured artifact is `.aider.chat.history.md`:

```text
> Aider v0.86.2
> Model: gpt-4o-mini with whole edit format
#### Reply OK
OK
> Tokens: 1 sent, 1 received. Cost: ...
```

## 13. Amp

Amp has a `custom-url` router that can point at an OpenAI-compatible
gateway:

```bash
printf %s "$ROSETTA_API_KEY" | amp config model-providers add-router custom-url \
  --name rosetta \
  --base-url https://rosetta.service.oaklight.top/v1 \
  --api-format chat-completions \
  --api-key-file - \
  --model-mapping '<exact-amp-model-id>->deepseek-v4.1-flash' \
  --personal --active
```

Observed blockers:
- `amp config model-providers list` in an isolated HOME:
  `Error: Invalid or missing API key. Run 'amp login' to authenticate.`
- `--model-mapping '*/*->deepseek-v4.1-flash'` is rejected:
  `Provider model ID mappings (->) need an exact model ID, not a wildcard pattern`.
- No saved Amp account. Without login, provider configuration is blocked;
  this is an Amp account requirement, not a rosetta gateway issue.
- The exact model ID must come from Amp after login (or its docs). The
  `->` override form only accepts an exact ID.

## 14. What this means for agentabi / mansio

1. **Two approval shapes.** ACP (`session/request_permission`) and Codex
   (`item/commandExecution/requestApproval`) both have a request id and
   an option set. Claude Code and Copilot do not expose a peer-answerable
   approval request: the former is UI-local, the latter denies in
   non-interactive mode.
2. **Two peer-message shapes.** Claude Code and Qwen use a session
   socket with a from/reply token; OpenClaw uses `sessions.send` with a
   `sessionKey` and a `runId` admission ack. A normalized `PeerSend`
   needs `to`, `body`, `reply_to`, `idempotency_key`, and a delivery
   receipt type.
3. **Receipts are not optional.** Qwen has explicit
   `delivery_status`; OpenClaw has `runId` + `sessions.changed`; Claude
   Code's `SendMessage` result is a local admission notice with no wire
   ack, so `accepted` must not be reported as `delivered`. A bus should
   model `accepted / delivered / held / refused / expired / dropped`.
4. **Capability negotiation matters.** agy's `send_message` exists but
   its schema is rejected by rosetta's genai wire. A provider capability
   flag is not enough; the tool schema must be validated per wire
   protocol.
5. **Do not assume one transport.** Unix socket, stdio JSON-RPC, SSE,
   and WebSocket all appear. mitmproxy only covers the HTTP(S)/WS
   subset.
6. **Claude's peer socket is unauthenticated and spoofable.** A missing
   or wrong auth token is accepted; `from-name` and `from-mode` are
   self-asserted, and `from-mode` alone decides `hold`. Only
   `SO_PEERCRED` (pid) is kernel-verified, and it identifies the
   connecting helper, not the session. A bus must authenticate senders
   and treat claimed names/modes as hints, not authorization.
