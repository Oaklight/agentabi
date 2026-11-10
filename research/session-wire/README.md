# Session Wire Capture

Capture the actual session/message wire formats of agentic CLIs.

This directory is research tooling. It is intentionally dependency-free
(Python 3.10+ stdlib only) so every peer can run it without installing
anything.

## Transport matrix

| CLI | Session message transport | Capture method |
| --- | --- | --- |
| Claude Code (stream) | stdio NDJSON (`--output-format stream-json`) | `capture_stdio.py` |
| Claude Code (peer) | Unix socket, newline-delimited JSON | `capture_socket.py` |
| Codex app-server | JSON-RPC 2.0 over stdio / Unix socket / WebSocket | `capture_stdio.py` |
| agy | stdio NDJSON (`--output-format stream-json`) | `capture_stdio.py` |
| OpenCode | HTTP OpenAPI + SSE | `capture_sse.py` + `capture_mitm.py` |
| ACP (Goose, OpenCode, agentabi) | JSON-RPC 2.0 over stdio | `capture_stdio.py` |
| OpenClaw | WebSocket JSON-RPC 2.0 | `capture_mitm.py` or a WS client |
| Amp / Cursor / Copilot | HTTPS API | `capture_mitm.py` |

`mitmproxy` cannot see Unix sockets or stdio. Use the matching capture
method above; do not assume a proxy will capture everything.

## Scripts

### `bin/capture_stdio.py`

Spawn a CLI, send zero or more lines to stdin, record every sent line,
stdout line, and stderr line as NDJSON.

```bash
python3 bin/capture_stdio.py \
  --name codex --out captures/codex/app-server.jsonl --timeout 15 \
  --send '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"clientInfo":{"name":"probe","version":"0.0.0"}}}' \
  --send '{"jsonrpc":"2.0","id":2,"method":"thread/list","params":{}}' \
  -- codex app-server --listen stdio://
```

### `bin/capture_socket.py`

Connect to a Unix domain socket, optionally send an auth line, send
messages, and record both directions.

```bash
python3 bin/capture_socket.py \
  --name claude-peer --path "$CLAUDE_CODE_MESSAGING_SOCKET" \
  --auth-token-env CLAUDE_CODE_MESSAGING_TOKEN \
  --send '{"type":"message","text":"hello"}' \
  --out captures/claude/peer-socket.jsonl
```

The exact message frame is what we are trying to discover. Keep probes
harmless and send them to a throwaway session, not to the coordinator.

### `bin/capture_sse.py`

Subscribe to an SSE stream and optionally trigger an action with a POST.

```bash
python3 bin/capture_sse.py \
  --url http://127.0.0.1:4099/event \
  --post-url http://127.0.0.1:4099/session \
  --post-body '{}' --post-delay 1 \
  --out captures/opencode/events.ndjson --timeout 8
```

### `bin/capture_mitm.py`

A mitmproxy addon that records HTTP requests/responses and WebSocket
messages, with header and token redaction.

```bash
CAPTURE_OUT=captures/amp/mitm.ndjson \
CAPTURE_HOSTS=ampcode.com \
mitmdump -p 9091 -s bin/capture_mitm.py
```

Point only the target CLI at `http://127.0.0.1:9091` via its proxy
environment. Do not point the whole machine at it.

### `bin/summarize.py`

Count message types in a capture and print one sample per type.

```bash
python3 bin/summarize.py captures/codex/app-server.jsonl
```

### `bin/redact.py`

Redact common secret shapes from a capture before sharing it.

```bash
python3 bin/redact.py captures/codex/app-server.jsonl \
  -o captures/codex/app-server.redacted.jsonl
```

## Rules for peers

1. Read-only against the target CLI. Do not change its config.
2. Never commit raw captures. Only commit redacted, small samples.
3. Redact `Authorization`, `Cookie`, `token`, `api_key`, `secret`,
   `password`, and any `sk-`, `ghp_`, `xoxb-` values.
4. Use `pipx` or `conda` for PyPI installs. Do not use `uv`.
5. Write only under `captures/<cli>/` and `schemas/<cli>/`.
6. If a CLI is not installed, record it as `blocked` with the install
   command you would use; do not install system-wide without asking.
7. Record the CLI version, OS, and capture date in `captures/<cli>/META.json`.

## Layout

```
bin/            capture and redaction tools
captures/       raw and redacted captures (gitignored)
schemas/        OpenAPI / JSON Schema snapshots (committed, redacted)
targets.yaml    per-CLI capture plan and status
```
