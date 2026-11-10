# agy stream-json wire format (CLI 1.5.3, 2026-11-07)

Transport: stdout, one JSON object per line (NDJSON). stderr carries plain-text
errors and an `AGY_ERROR: {...}` line. `--output-format stream-json` is required.

## Output events

| event | payload | meaning |
| --- | --- | --- |
| `init` | `conversation_id`, `init` | Session opened |
| `step_update` | `step_update` | One step changed state |
| `result` | `result` | Turn finished |

### `init.init`

| field | type | sample / notes |
| --- | --- | --- |
| `cwd` | string | absolute working directory |
| `tools` | string[] | built-in tool names visible to the agent |
| `permission_mode` | string | `request-review` |
| `agent` | string | present only with `--agent <name>` |

Observed `tools` includes `send_message`, `manage_inbox`, `invoke_subagent`,
`manage_subagents`, `define_subagent`, `ask_permission`, `list_dir`,
`run_command`, etc.

### `step_update.step_update` — all steps

| field | type | notes |
| --- | --- | --- |
| `conversation_id` | string | same UUID as `init` |
| `step_index` | int | 0-based, shared across step types |
| `state` | string | `ACTIVE`, `DONE`, `ERROR` |
| `step_type` | string | `user_input`, `agent_response`, `tool`, `error_message` |
| `text_delta` | string | streamed text, `agent_response` only |
| `duration_seconds` | number | on `DONE` / `ERROR` |
| `usage` | object | `input_tokens`, `output_tokens`, `thinking_tokens`, `cache_read_tokens`, `total_tokens` |

### `step_update.step_update` — tool steps

`step_type:"tool"` adds:

| field | type | notes |
| --- | --- | --- |
| `tool_name` | string | e.g. `run_command` |
| `tool_info.name` | string | same as `tool_name` |
| `tool_info.parameters` | object | e.g. `{"CommandLine":"ls /tmp"}` |
| `tool_info.output` | string | present when the tool completes |
| `tool_info.error` | object | `{"type":"TOOL_ERROR","message":"..."}` |
| `state` | string | `ACTIVE`, then `DONE` or `ERROR` |

Observed sequence for a shell call:

```json
{"event":"step_update","step_update":{"step_index":2,"state":"ACTIVE","step_type":"tool","tool_name":"run_command","tool_info":{"name":"run_command","parameters":{"CommandLine":"ls /tmp"}}}}
{"event":"step_update","step_update":{"step_index":2,"state":"DONE","step_type":"tool","tool_name":"run_command","tool_info":{"name":"run_command","parameters":{"CommandLine":"ls /tmp"},"output":"Command exited with code 0.\n..."}}}
```

## Permission behavior (headless `-p`)

Headless mode cannot show an approval prompt. With `permission_mode:"request-review"`,
a command is auto-denied. The denial appears as a tool step error, not a
separate event:

```json
{"step_type":"tool","state":"ERROR","tool_name":"run_command",
 "tool_info":{"error":{"type":"TOOL_ERROR","message":"permission check failed for command \"ls /tmp\": user denied permission to run command:\nls /tmp\n..."}}}
```

The result carries `"denied_actions":[{"action":"command","display_name":"RunCommand"}]`.
stderr adds:
`jetski: no output produced — a tool required the "command" permission that headless mode cannot prompt for, so it was auto-denied.`

`--dangerously-skip-permissions` sets `permission_mode:"always-proceed"` and the
tool runs; no permission event is emitted.

## ACP permission request

`agy acp` does emit a real permission frame. Method:
`session/request_permission`.

```json
{"jsonrpc":"2.0","method":"session/request_permission","params":{
  "sessionId":"...",
  "toolCall":{"sessionUpdate":"tool_call","toolCallId":"call_...","name":"run_command",
              "args":{"CommandLine":"ls /tmp","WaitMsBeforeAsync":5000,"toolSummary":"List /tmp directory"},
              "status":"pending","kind":"execute","title":"ls /tmp","rawInput":{...}},
  "options":[{"optionId":"allow","name":"Allow","kind":"allow_once"},
             {"optionId":"deny","name":"Deny","kind":"reject_once"}]}}
```

Client reply:

```json
{"jsonrpc":"2.0","id":"<request id>","result":{"outcome":{"outcome":"selected","optionId":"allow"}}}
```

Then `session/update` frames with `tool_call` / `tool_call_update`:
`status` goes `in_progress` -> `pending` -> `in_progress` -> `completed`;
`rawOutput` is `{"output":"...","exit_code":0}`; `content` duplicates the output.

## Capture workaround (not product behavior)

The provider rejects several built-in tool schemas with
`Invalid schema for function '<name>': "1" is not of type "integer"`.
Observed names: `send_message`, `define_subagent`, `write_file`, `edit_file`,
`ask_question`. The request also fails when an MCP server exposes names with `/`.

To capture, a local pass-through proxy was placed between `agy` and rosetta:
`AGY_LLM_GATEWAY_URL=http://127.0.0.1:<port>`, dropping only those tool
declarations from `tools[].functionDeclarations`. A temp `HOME` avoided the
user's MCP config. No user config was changed. Upstream endpoint:
`https://rosetta.service.oaklight.top/v1beta/models/deepseek-v4.1-flash:streamGenerateContent?alt=sse`.

This means the tool list seen by the model was reduced. The wire shapes above
are agy's real output shapes; only the request was filtered.

### `result.result`

| field | type | notes |
| --- | --- | --- |
| `conversation_id` | string | session UUID |
| `status` | string | `SUCCESS`, `ERROR` |
| `response` | string | final text |
| `error` | string | present on `ERROR` |
| `duration_seconds` | number | |
| `num_turns` | int | |
| `usage` | object | same shape as above |
| `finished_reason` | string | `error` observed |

## Input event (`--input-format stream-json`)

One NDJSON line per turn. `--input-format stream-json` requires
`--output-format stream-json`. Do not use `-p <prompt>` with it; use `-p=`.

```json
{"event":"user","message":{"role":"user","content":[{"type":"text","text":"Reply with exactly: OK"}]}}
```

The `event` field is mandatory. Unknown values are ignored with
`warning: ignoring unsupported stream input message event "<value>"`.
`event:"user"` is the supported value. `message` is an object, not a string.

## ACP transport (`agy acp`)

JSON-RPC 2.0 over stdio. Captured methods:

- `initialize` -> `agentInfo`, `agentCapabilities`, `authMethods`
- `session/new` with `{"cwd": "...", "mcpServers": []}` ->
  `sessionId`, `models`, `modes`, `configOptions`
- `session/prompt` -> `stopReason`
- notifications: `session/update` with
  `sessionUpdate` in `available_commands_update`,
  `user_message_chunk`, `session_info_update`, `agent_message_chunk`

`session/request_permission` is captured in
`captures/agy/acp-permission-rosetta.redacted.jsonl`. Earlier attempts without
the proxy failed before reaching it; see “Capture workaround”.

## Previously blocked, now captured

Before the workaround, every prompt failed with `Invalid 'tools[16].function.name'`
(MCP names containing `/`) and later with `Invalid schema for function '<name>'`
(`"1"` not integer). Both are provider-side validation errors. The workaround
below removed only the offending declarations; no user config was changed.

## Redacted samples

`captures/agy/stream.redacted.jsonl` (pre-existing successful turn):

```json
{"event":"step_update","step_update":{"conversation_id":"661e...","step_index":1,"state":"ACTIVE","step_type":"agent_response","text_delta":"OK"}}
{"event":"result","result":{"conversation_id":"661e...","status":"SUCCESS","response":"OK\n","num_turns":1}}
```

`captures/agy/tool-permission-sample.redacted.jsonl` (trimmed: denied headless
tool, ACP `session/request_permission`, approved tool call + result):

```json
{"method":"session/request_permission","params":{"toolCall":{"name":"run_command","args":{"CommandLine":"ls /tmp"},"status":"pending","kind":"execute"},"options":[{"optionId":"allow","kind":"allow_once"},{"optionId":"deny","kind":"reject_once"}]}}
{"step_type":"tool","state":"ERROR","tool_name":"run_command","tool_info":{"error":{"type":"TOOL_ERROR","message":"permission check failed ... user denied permission ..."}}}
{"step_type":"tool","state":"DONE","tool_name":"run_command","tool_info":{"parameters":{"CommandLine":"ls /tmp"},"output":"Command exited with code 0. ..."}}
```

`captures/agy/input-format.redacted.jsonl`:

```json
{"event":"init","conversation_id":"6329...","init":{"cwd":"/tmp","tools":["ask_permission","list_dir","run_command","send_message"],"permission_mode":"request-review"}}
{"event":"step_update","step_update":{"conversation_id":"6329...","step_index":0,"state":"DONE","step_type":"user_input"}}
{"event":"step_update","step_update":{"conversation_id":"6329...","step_index":1,"state":"DONE","step_type":"error_message"}}
```
