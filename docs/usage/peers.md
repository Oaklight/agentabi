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

This page covers Claude Code. The `PeerAdapter` protocol leaves room for
other backends, but only the Claude one exists today.

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

`PeerBroker` options:

- `spawn(name, cwd, prompt=None, **options)` — `options` accepts
  `model`, `permission_mode`, `agents`, `system_prompt`, `settings`,
  `env`, `add_dirs`, `command`, `session_id`, `resume`, `extra_args`.
  `settings` is the same mapping as `Session(settings=...)`.
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
peerctl recover reviewer --permission-mode acceptEdits
```

The new process starts with `--resume <session_id>`, so the previous
conversation is available. The in-flight turn is not replayed.

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

## Limits

- A steer is confirmed only by the write to the child process stdin.
  Claude sends no acknowledgement; the turn result is authoritative.
- The daemon is the parent of its peers. A daemon crash takes them down;
  use `recover` to resume from the saved session.
- `wait` holds one request open for the turn duration. A timeout raises
  `PeerTimeout` and leaves the turn running.
- Permission requests are denied automatically (`--permission-prompts none`),
  and each denial is recorded in the inbox as `permission_denied`.
