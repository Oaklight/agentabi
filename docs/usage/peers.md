# Peers

A `Session.run` call is one task: start a CLI, stream a turn, exit. A
**peer** is a CLI process that stays alive between turns, so a
coordinator can send a follow-up task, steer a running one, or interrupt
it and keep going.

Two things are involved:

- `agentabi.peers` is the library. `PeerBroker` spawns peers; `ClaudePeer`
  owns one `claude --print --input-format stream-json` process.
- `peerctl` is the CLI front end. It talks to a background daemon that
  owns the peers, so a peer outlives the command that started it.

Two backends are implemented:

| backend | process | steer | interrupt |
| --- | --- | --- | --- |
| `claude` (default) | `claude --print --input-format stream-json` | a second user message on stdin | control request |
| `codex` | `codex app-server --listen stdio://` | `turn/steer` | `turn/interrupt` |
| `opencode` | `opencode serve` (V2 HTTP/SSE) | a second prompt with `delivery: "steer"` | `/api/session/{id}/interrupt` |
| `pi` | `pi --mode rpc` (NDJSON) | `{"type":"steer"}` | `{"type":"abort"}` |
| `agy` | `agy --input-format stream-json` | not available | `{"event":"interrupt"}` (best effort) |

The peer API and `peerctl` are the same for both. Differences are listed
under [Backends](#backends).

## Library

```python
import asyncio
from agentabi.peers import PeerBroker

async def main():
    async with PeerBroker() as broker:
        peer = await broker.spawn(
            "reviewer",
            cwd="/repo",
            prompt="Review the diff of the current branch",
        )
        turn = await peer.wait(timeout=300)
        print(turn.status, turn.text)

        await peer.send("Now check the tests for the same change")
        turn = await peer.wait(timeout=300)
        print(turn.text)

asyncio.run(main())
```

`send` while the peer is idle starts a new turn. `send` while a turn is
running steers it: Claude reads the message as soon as the current tool
call finishes, and the turn continues with the new instruction. The
`SendResult.mode` field says which happened (`"turn"` or `"steer"`).

```python
result = await peer.send("Stop and explain the plan first")
assert result.mode in ("turn", "steer")

await peer.steer("Only list files, do not edit", interrupt=False)
await peer.interrupt()          # ends the turn; the process survives
notes = peer.inbox()            # turn results, exits, denied permissions
await peer.close()
```

`wait()` returns a `PeerTurn`:

| field | meaning |
| --- | --- |
| `turn_id` | `"<name>:<n>"`, incremented per turn |
| `status` | `success`, `error`, or `cancelled` |
| `text` | assistant text for the turn |
| `usage` | `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_creation_tokens`, `total_tokens` |
| `num_turns` | Claude's own turn counter; a steer raises it |
| `stop_reason` | CLI stop reason, for example `success` or `error_during_execution` |
| `errors` | diagnostic strings from the CLI result row |

`PeerBroker` options (the default backend is `claude`; pass
`backend="codex"` for Codex):


- `spawn(name, cwd, prompt=None, backend="claude", **options)` —
  `options` accepts `model`, `permission_mode`, `agents`, `system_prompt`,
  `settings`, `env`, `add_dirs`, `command`, `session_id`, `resume`,
  `extra_args`; the Codex backend additionally takes `sandbox` and
  `approval_policy`. `settings` is the same mapping as
  `Session(settings=...)`.
- `max_peers` (default 8) caps the broker.
- `get(name)`, `list_peers()`, `notes()`, `close(name)` and
  `close()` are the rest of the surface.

## CLI: peerctl

`peerctl` keeps peers in a daemon so they survive the command that
started them. The first command starts the daemon; it writes
`peers.json` and a log under `$AGENTABI_PEER_STATE` (default
`~/.local/state/agentabi/peers`).

```bash
peerctl spawn reviewer --cwd /repo --prompt "Review the current branch"
peerctl spawn cx --backend codex --cwd /repo --prompt "Review the tests"
peerctl spawn oc --backend opencode --cwd /repo \
    --model rosetta/qwen3.8-27b --env OPENCODE_AUTH_CONTENT="$AUTH"
peerctl wait reviewer --timeout 300
peerctl send reviewer "Check the tests too"
peerctl steer reviewer "Do not edit files" --interrupt
peerctl interrupt reviewer
peerctl inbox reviewer --clear
peerctl status reviewer --json
peerctl ls
peerctl stop reviewer
peerctl daemon start|status|stop
```

`peerctl ls` lists both engines and adds an `ENGINE` column:

| engine | owner | messageable |
| --- | --- | --- |
| `process` (default) | the peer daemon | `send`, `steer`, `interrupt`, `wait`, `inbox` |
| `bg` | Claude's own daemon (`claude --bg`) | `attach` only |

The `BACKEND` column names the CLI behind a process peer (`claude` or
`codex`).

Every command accepts `--json` where the output is structured; use it
when another agent drives `peerctl`.

## Reaching a session another process started

`peerctl attach` writes one message to a session's peer socket under
`$XDG_RUNTIME_DIR/cc-socks/<pid>.sock`. The target may be a `bg` session
or an interactive Claude Code session, as long as it shows up in
`claude agents --json`:

```bash
peerctl attach reviewer "Run the linter and report only failures"
peerctl attach reviewer "..." --from-name coordinator --from-mode bypass
```

The wire is one-way: the receiver never replies and the sender gets no
acknowledgement, so the command reports the message id and byte count,
not delivery. The frame format was measured on Claude Code 2.1.299, not
published. A receiver whose permission mode differs from the claimed
`from-mode` may hold the message instead of delivering it.

`attach` refuses a `process` peer: writing to its socket would bypass
the broker's turn tracking. Use `peerctl send` for those.

## Recovering after the daemon stops

Stopping the daemon stops its peers, because it is their parent.
`peers.json` keeps the Claude session id and the replayable spawn options
(model, permission mode, add-dirs, agents, command). Settings and env are
never written, because they can hold keys.

```bash
peerctl recover reviewer
peerctl recover cx --settings "$SETTINGS_JSON"
```

For Claude the new process starts with `--resume <session_id>`; for
Codex it calls `thread/resume`. The previous conversation is available
in both cases. The in-flight turn is not replayed, and endpoint settings
are not saved: pass `--settings` again when the new process needs them.

## Subagent events

When a Claude session dispatches its own subagent through the `Task` or
`Agent` tool, the native provider emits a complementary pair:

```python
{"type": "subagent_start", "subagent_id": "call_abc", "agent": "reviewer",
 "prompt": "check the tests", "background": False}
{"type": "subagent_end", "subagent_id": "call_abc", "status": "completed",
 "result": "..."}
```

`subagent_id` is the originating `tool_use` id, so it also matches the
`tool_use` and `tool_result` events for the same call. `status` is
`completed`, `failed`, or `cancelled`. These events are observation
only: agentabi reports what the CLI decided to dispatch and does not
schedule subagents itself. Check `AgentCapabilities.supports_subagent_events`
before waiting for them.

## Backends

### Claude

The CLI reads stream-json user messages from stdin. A message sent while
a turn runs is read by the model when the current tool call finishes.
Denied permission requests are recorded in the inbox.

### Codex

One peer is one `codex app-server` process with one thread. `send` uses
`turn/start`; a message during the turn uses `turn/steer` with the
active turn id, which the server acknowledges. `interrupt` sends
`turn/interrupt`; the thread remains usable. The server reports token
usage through `thread/tokenUsage/updated`.

`permission_mode` maps to an app-server sandbox and approval policy:
`bypassPermissions` is `danger-full-access` with approvals `never`,
`full_auto` and `acceptEdits` are `workspace-write` with `never`, and
`default` or `plan` keep approvals `on-request`. A peer has no approval
channel, so an `on-request` approval is declined and recorded. `agents`
is not supported by this backend.

```python
peer = await broker.spawn(
    "cx",
    cwd="/repo",
    backend="codex",
    prompt="Review the tests",
    settings={"base_url": "...", "api_key_env": "OPENAI_API_KEY"},
)
```

### OpenCode

One peer is one `opencode serve` process with one V2 session. `send`
posts the prompt with `delivery: "steer"`; a second prompt during the
turn steers it. `interrupt` posts `/interrupt`; the session remains
usable. Completion and text are read from the session messages, because
opencode 1.19.0 closes its event stream after a steer. A subscriber is
kept open anyway: 1.19.0 does not start a turn for a prompt sent while
no subscriber exists.

Endpoint `settings` are refused, because the V2 model catalog does not
see providers injected by `OPENCODE_CONFIG` or
`OPENCODE_CONFIG_CONTENT` (measured). Instead, configure the provider in
opencode itself, or pass credentials to the peer's server:

```python
peer = await broker.spawn(
    "oc",
    cwd="/repo",
    backend="opencode",
    model="rosetta/qwen3.8-27b",
    env={"OPENCODE_AUTH_CONTENT": '{"rosetta":{"type":"api","key":"..."}}'},
)
```

`peerctl spawn ... --env OPENCODE_AUTH_CONTENT=...` does the same. The
model must be `provider/model`. The peer answers a permission ask with
`once` in an approving mode and with `reject` otherwise, recording a
`permission_denied` note.

### Pi

One peer is one `pi -p --mode rpc` process. Commands are one JSON object
per line on stdin; the process answers with `response` objects and emits
`agent_start`, `turn_start`, `message_update`, `turn_end`, `agent_end`,
and `agent_settled` events. `send` uses `prompt`, a message during the
run uses `steer` (the model sees it after the running tool call), and
`interrupt` uses `abort`; the run ends at `agent_settled`, whose
`aborted` flag marks a cancelled turn. The session id comes from
`get_state`, and `recover` reuses it with `--session-id`.

Endpoint settings go through the same adapter as the pi transport: a
private agent directory with `models.json`, removed when the peer
closes. pi has no approval flag in this transport, so
`permission_mode` is accepted but not applied.

```python
peer = await broker.spawn(
    "pi",
    cwd="/repo",
    backend="pi",
    model="rosetta/qwen3.8-27b",
    prompt="Review the tests",
)
```

### agy

One peer is one `agy --output-format stream-json --input-format
stream-json` process. `send` writes one `user` event and the turn ends
at the `result` event; text, usage, and status come from it. The
conversation id arrives with the first `init` event, and `recover`
resumes it with `--conversation`.

The input side accepts only `user` and `interrupt`. A second `user` is a
queued follow-up turn, not a steer, so a message sent while the peer is
running is refused (`PeerBusy`) instead of silently queuing. `steer`
raises for the same reason. Measured on 1.5.5, `interrupt` is honoured
during a tool step but ignored while the agent is between steps, so a
turn may still finish normally; the result is authoritative.

```python
peer = await broker.spawn(
    "agy",
    cwd="/repo",
    backend="agy",
    model="qwen3.8-27b",
    prompt="Review the tests",
    # command=["/path/to/agy.real"] when a launcher pins another profile
)
```

`peerctl spawn/recover --command` sets that argv prefix; `--env` supplies
extra environment.

## Limits

- A steer is confirmed only by the write to the child process stdin in
  the Claude backend. Codex acknowledges `turn/steer` with a turn id, and
  opencode with an admitted message id.
- The daemon is the parent of its peers. A daemon crash takes them down;
  use `recover` to resume from the saved session.
- `wait` holds one request open for the turn duration. A timeout raises
  `PeerTimeout` and leaves the turn running.
- Permission requests are denied automatically (`--permission-prompts none`),
  and each denial is recorded in the inbox as `permission_denied`.
