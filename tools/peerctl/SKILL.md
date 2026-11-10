---
name: peerctl
description: Spawn, list, export/import, and stop Claude Code background peer sessions; handover-safe pool management.
---

# peerctl

Minimal lifecycle control for Claude Code background peers. It opens,
lists, and stops sessions. It does not send messages.

Use this skill when the task is to create a one-off background worker or
reviewer, inspect the managed pool by workspace or name, stop a finished
worker, or carry the pool across a peerctl/tool version change.

## Commands

```bash
python3 tools/peerctl.py spawn NAME --cwd DIR --prompt TEXT \
    [--workspace LABEL] [--model M] [--permission-mode MODE] \
    [--disallow TOOL] [--max N]
python3 tools/peerctl.py ls [--workspace LABEL] [--cwd DIR] [--json] [--all]
python3 tools/peerctl.py stop NAME|ID [--rm]
python3 tools/peerctl.py export PATH
python3 tools/peerctl.py import PATH [--adopt]
```

Defaults:

- `--permission-mode bypassPermissions`
- `--disallow AskUserQuestion`
- pool cap 8 managed live sessions (`--max` overrides)
- state at `$PEERCTL_STATE`, default `~/.local/state/peerctl/pool.json`

`ls` shows only sessions created by peerctl. `ls --all` shows every live
Claude session on the machine.

## State model

- `claude agents --json` is authoritative.
- The manifest is a cache for workspace labels and handover.
- `sessionId` is the stable identity; the short id and the name are
  mutable.
- `stop` resolves a name or id against the live registry, then calls
  `claude stop <short-id>`.

## Handover across tool versions

1. Before switching: `python3 tools/peerctl.py export /tmp/pool-<time>.json`
2. After switching: `python3 tools/peerctl.py import /tmp/pool-<time>.json --adopt`
3. Matching order: `sessionId` first, then `(cwd, name)`, else orphan.
4. Import never stops anything. Orphans are reported for a human
   decision; they are not killed or silently dropped.

Why this is safe for Claude Code: `claude --bg` sessions are owned by
Claude's daemon, not by peerctl. peerctl can restart or be replaced
without touching a running session.

For adapters that own the process (PTY), do not hot-swap. Drain
in-flight turns, export, restart; or run sessions under a neutral
supervisor (tmux, systemd) so the manager is not the parent.

## What peerctl cannot do

- It does not send messages. The main session uses cross-session
  `SendMessage` by name; peerctl only manages lifecycle.
- `stop` refuses interactive sessions: they have no short id
  (`claude agents --json` reports `id: "-"` for foreground sessions).
  Retiring a foreground session means stop assigning work and close the
  window.
- It is Claude-only. Other CLIs need an adapter that provides a stable
  identity, `list`, `spawn`, and `stop`.

## Safety

- Never hard-kill the manager while PTY sessions are live.
- Keep the pool cap in place; each Claude session costs roughly
  250–300 MiB plus MCP children.
- Do not let peers spawn peers without a depth or budget limit.
- `--disallowedTools` must use the `=` form; the flag is variadic and
  otherwise swallows the positional prompt.
