# peerctl (prototype)

Minimal peer control for Claude Code background sessions. It opens,
lists, and stops sessions. It does not send messages; the main session
handles that itself.

```bash
python3 tools/peerctl.py spawn NAME --cwd DIR --prompt TEXT \
    [--workspace LABEL] [--model M] [--permission-mode MODE] [--max N]
python3 tools/peerctl.py ls [--workspace LABEL] [--cwd DIR] [--json] [--all]
python3 tools/peerctl.py stop NAME|ID [--rm]
python3 tools/peerctl.py export PATH
python3 tools/peerctl.py import PATH [--adopt]
```

Defaults: `--permission-mode bypassPermissions`; `--disallow
AskUserQuestion`; pool cap 8 managed sessions.

## State model

- `claude agents --json` is the authority. The manifest at
  `$PEERCTL_STATE` (default `~/.local/state/peerctl/pool.json`) is a
  cache for workspace labels and handover.
- Sessions are keyed by `sessionId`, which is stable across rename and
  resume. The short id (`74b420fb`) and the name are mutable.
- `ls` shows only sessions created by peerctl; `ls --all` shows every
  live Claude session on the machine.
- `stop` uses the short id; names are resolved first against the live
  registry.

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
