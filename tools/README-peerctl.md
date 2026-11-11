# peerctl (prototype)

Skill entry point: `tools/peerctl/SKILL.md`. The project skill at
`.claude/skills/peerctl/SKILL.md` is a symlink to that file; keep the
source of truth in `tools/`.

Peer lifecycle and messaging for Claude Code. Two engines:

- `process` (default): the peer daemon owns a long-lived CLI process,
  `claude --print --input-format stream-json`, `codex app-server`, or `opencode serve`. Messages go to its
  stdin, so `send`, `steer`, `interrupt`, `wait`, and `inbox` work.
- `bg`: a `claude --bg` session. Lifecycle only; `attach` writes one
  message to its peer socket.

Backends for a process peer: `claude` (default), `codex` (`codex
app-server`), and `opencode` (`opencode serve`). See
`docs_en/docs/usage/peers.md` for their differences.

```bash
python3 tools/peerctl.py spawn NAME --cwd DIR [--prompt TEXT] \
    [--engine process|bg] [--backend claude|codex|opencode] [--model M] \
    [--permission-mode MODE] [--agent-json JSON] [--settings JSON] \
    [--system-prompt TEXT] [--add-dir DIR] [--env K=V] \
    [--workspace LABEL] [--max N]
python3 tools/peerctl.py send NAME TEXT [--json]
python3 tools/peerctl.py steer NAME TEXT [--interrupt] [--json]
python3 tools/peerctl.py interrupt NAME
python3 tools/peerctl.py wait NAME [--timeout S] [--json]
python3 tools/peerctl.py inbox [NAME] [--clear] [--json]
python3 tools/peerctl.py status NAME [--json]
python3 tools/peerctl.py attach NAME TEXT [--from-name N] [--from-mode MODE]
python3 tools/peerctl.py recover NAME [--permission-mode MODE] [--settings JSON] [--env K=V]
python3 tools/peerctl.py ls [--workspace LABEL] [--cwd DIR] [--json] [--all]
python3 tools/peerctl.py stop NAME|ID [--rm]
python3 tools/peerctl.py daemon start|stop|status
python3 tools/peerctl.py export PATH
python3 tools/peerctl.py import PATH [--adopt]
```

Defaults: `--engine process`; `--backend claude`;
`--permission-mode bypassPermissions`;
`--disallow AskUserQuestion` (bg only); per-workspace pool cap 8 managed
sessions; peer state at `$AGENTABI_PEER_STATE` (default
`~/.local/state/agentabi/peers`).

## Messaging

- Idle `send` starts a turn; busy `send` steers the running one.
- `wait` returns status, text, usage, turn count, and stop reason.
- `interrupt` ends the turn and leaves the process alive.
- `attach` targets a bg or interactive session found in
  `claude agents --json`; the socket frame is unacknowledged, so the
  command reports the message id, not delivery. Process peers are
  refused.
- `recover` resumes a saved process peer with `--resume <session_id>`
  after the daemon stopped. Settings and env are never written to
  `peers.json`.

See `docs_en/docs/usage/peers.md` for the full guide.

## State model

- The peer daemon is the authority for process peers;
  `claude agents --json` is the authority for bg sessions. The manifest
  at `$PEERCTL_STATE` (default `~/.local/state/peerctl/pool.json`) is a
  cache for workspace labels and handover.
- Sessions are keyed by `sessionId`, which is stable across rename and
  resume. The short id (`74b420fb`) and the name are mutable.
- `ls` shows process peers plus sessions created by peerctl; `ls --all`
  shows every live Claude session on the machine. The `ENGINE` column
  says which commands apply.
- `stop` routes by engine: the daemon for process peers, `claude stop
  <short-id>` for bg sessions.

## Handover between prototype versions

Goal: switching the manager must not lose sessions.

1. Before switching, export:

   ```bash
   python3 tools/peerctl.py export /tmp/pool-$(date +%s).json
   ```

2. After switching, import:

   ```bash
   python3 tools/peerctl.py import /tmp/pool-....json --adopt
   ```

3. Matching order:
   1. `sessionId` (exact, preferred);
   2. `(cwd, name)` (rename or id drift);
   3. otherwise the entry is reported as `orphaned`.

4. Import never stops anything. Orphans are reported for a human
   decision; they are not killed, and they are not silently dropped.

### Why this is safe for Claude Code

`claude --bg` sessions are owned by Claude's daemon, not by peerctl.
peerctl can exit, restart, or be replaced without touching a running
session. A new version reconstructs the pool from `claude agents --json`
plus the exported manifest.

### Adapters that own the process (PTY)

For a PTY-driven adapter, the pool manager is the parent process. Killing
the manager kills the sessions. Handover then requires one of:

- **drain**: stop accepting new work, let in-flight turns finish, export,
  restart under the new version;
- **supervisor handoff**: run each PTY session under a neutral supervisor
  (tmux, systemd user unit, a small daemon) so the manager is not the
  parent;
- **reconnect**: if the CLI exposes a durable session id (Claude's
  `sessionId`, OpenCode's server session, Codex thread id), restart the
  CLI with that id and replay from the transcript.

Never hard-kill the manager while PTY sessions are live.

### Adapter contract for non-Claude CLIs

An adapter must provide:

- `list() -> [{identity, name, cwd, status}]`
- `spawn(name, cwd, prompt) -> identity`
- `stop(identity)`
- a stable `identity` that survives manager restarts

If the CLI has no durable identity, the adapter must declare
`handover: "drain"` and the pool must stop before switching versions.

## Known limits

- Claude-only prototype; one adapter.
- No reap/idle TTL; cap counts only manifest-managed live sessions.
- `--disallowedTools` is passed with `=` because the flag is variadic;
  without it the positional prompt is swallowed as another tool name.
- Background sessions have `AskUserQuestion` but no answer channel;
  peerctl disallows it by default so peers escalate by message.
