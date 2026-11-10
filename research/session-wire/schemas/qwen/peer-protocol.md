# Qwen Code peer wire protocol (observed)

Schema version 1, frame version (`msgV`) 1. Observed on Qwen Code 0.26.9,
Linux, 2026-11-07, by driving two idle TUI sessions in a temp `QWEN_HOME`
and one hand-registered external peer (`kind: "external"`). No model turn
was involved; sessions stayed on the first-run auth screen.

Raw captures: `captures/qwen/01-send-prompting.jsonl`,
`02-receipts.jsonl`, `03-statuses.jsonl` (redacted copies alongside).

## Registry record

`$QWEN_HOME/sessions/<pid>.json`, mode 0600, directory 0700.

| Field | Type | Observed value / rule |
| --- | --- | --- |
| `schemaVersion` | int | `1` |
| `pid` | int | must equal the file name prefix |
| `procStart` | string | `<boot_id>:<start_ticks>` (Linux); `null` elsewhere |
| `pidNs` | int | inode of `/proc/self/ns/pid`; readers only list their own namespace |
| `sessionId` | uuid | changes on `/clear` and `/resume` under the same pid |
| `cwd` | string | working directory at registration |
| `name` | string | `<cwd-basename>-<sha256(sessionId)[0:2]>`; not unique |
| `startedAt` | epoch ms | newest-first listing order |
| `qwenVersion` | string | `"0.26.9"` |
| `kind` | string | `tui` \| `headless` \| `serve` \| `external` |
| `ipcPath` | string | `/run/user/<uid>/qwen-socks/<pid>.sock`; absent = not messageable |
| `ipcToken` | hex64 | presented on the auth line; absent = no auth required |

`ref` (display handle) = `sha256(sessionId)[0:6]`. Address grammar:
`name`, `name [ref]`, `[ref]`, or bare `ref`; ambiguous names are an error.

`qwen sessions ps --json` emitted every field above **except `ipcToken`**.
`list_agents` is the in-session view of the same registry.

## Auth line (first line on a new connection)

```json
{"msgV":1,"type":"auth","token":"<64 hex>"}
```

Accepted tokens: the target's registry `ipcToken`; the target's own
`QWEN_CODE_MESSAGING_TOKEN`; or a controller token `qpc_<64 hex>` minted by
`qwen sessions controllers`. No token = silent drop.

## User frame

```json
{"msgV":1,"msgId":"<uuid>","type":"user","from":"<sender ipcPath>",
 "replyToken":"<sender ipcToken>","fromName":"wireprobe","fromMode":"prompting",
 "toSessionId":"<sessionId>","priority":"next",
 "message":{"role":"user","content":"probe alpha"}}
```

Validation observed: `toSessionId` mismatch → `misaddressed`; same body within
the duplicate window → `dropped` / `dropReason: "duplicate"`; unknown fields
ignored. `from` absent = no receipts.

## Receipt frame (sent to `from`, one connection per outcome)

```json
{"msgV":1,"msgId":"<fresh uuid>","type":"control","action":"delivery_status",
 "status":"held","origMsgId":"<original msgId>",
 "from":"<receiver ipcPath>","reason":"..."}
```

| `status` | Meaning | Extra fields |
| --- | --- | --- |
| `held` | parked for the recipient user to review | `reason` |
| `delivered` | queued for the model (not proof of reading) | `reason` |
| `denied` | a person rejected it | `reason` |
| `refused` | session policy turns peers away | `reason` |
| `expired` | hold timed out / session exited | `reason` |
| `misaddressed` | `toSessionId` does not match the target | `reason` |
| `dropped` | inbox rejected before policy | `reason`, `dropReason` (`duplicate` \| `rate-limited` \| `queue-full`), optional `droppedMsgIds` |

The receiver authenticates back to the sender's inbox with the sender's
`replyToken` before writing the receipt: observed as a second connection
whose first line is `{"msgV":1,"type":"auth","token":"<replyToken>"}`.

## Observable review-class behaviour

With the receiver sitting on the first-run auth screen:

| Sender `fromMode` | Receipt |
| --- | --- |
| `prompting` | `held` |
| `bypass` | `delivered` |

Same body from the same peer inside the window was `dropped`/`duplicate`
even with a fresh `msgId`, so deduplication is body-based, not id-based.

Not captured: `/peers` release/deny UI, hold expiry, rate-limit counters,
controller-token delivery. Sessions had no model credentials, so no
`send_message` tool call was produced; the send side here is a hand-built
frame, which matched the documented contract byte-for-byte.
