#!/usr/bin/env python3
"""peerctl — peer lifecycle and messaging.

Two engines:

- ``process`` (default): the peer daemon owns a long-lived
  ``claude --print --input-format stream-json`` process.  Messages are
  written to its stdin, so ``send``/``steer``/``wait``/``inbox`` work.
- ``bg``: the previous ``claude --bg`` path.  Sessions are owned by
  Claude's daemon and cannot receive messages; only lifecycle commands
  apply.

Every command reconciles against its authority: the peer daemon for
``process`` peers, ``claude agents --json`` for ``bg`` sessions.  The
local manifest is a cache plus labels, never the authority.

Commands:
    spawn NAME --cwd DIR [--prompt TEXT] [--engine process|bg]
        [--backend claude|codex|opencode]
    ls [--workspace WS] [--cwd DIR] [--json] [--all]
    send NAME TEXT [--json]
    steer NAME TEXT [--interrupt] [--json]
    interrupt NAME
    wait NAME [--timeout S] [--json]
    inbox [NAME] [--clear] [--json]
    status NAME [--json]
    attach NAME TEXT [--from-name N] [--from-mode bypass|default]
    recover NAME [--permission-mode MODE] [--settings JSON] [--env K=V] [--json]
    stop NAME|ID [--rm]
    daemon start|stop|status
    export PATH
    import PATH [--adopt]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, NoReturn

try:
    from agentabi.peers import PeerClient
    from agentabi.peers.errors import PeerError, PeerNotFound
    from agentabi.peers.recover import recover_peer
    from agentabi.peers.socket_wire import send_cross_session, session_socket
except ImportError as exc:  # pragma: no cover - environment problem
    print(
        "peerctl: agentabi is not importable; install the package or set "
        f"PYTHONPATH to its src directory ({exc})",
        file=sys.stderr,
    )
    raise SystemExit(2) from exc

STATE_DIR = Path(os.environ.get("PEERCTL_STATE", Path.home() / ".local/state/peerctl"))
MANIFEST = STATE_DIR / "pool.json"
DEFAULT_MAX = 8
CLAUDE = "claude-bg"


def die(msg: str) -> NoReturn:
    print(f"peerctl: {msg}", file=sys.stderr)
    raise SystemExit(1)


def run(
    cmd: list[str], cwd: str | None = None, timeout: int = 90
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout
        )
    except FileNotFoundError:
        die(f"command not found: {cmd[0]}")
    except subprocess.TimeoutExpired:
        die(f"timed out: {' '.join(cmd)}")


def live_sessions() -> list[dict[str, Any]]:
    proc = run(["claude", "agents", "--json"], timeout=30)
    if proc.returncode != 0:
        die(f"claude agents failed: {proc.stderr.strip() or proc.stdout.strip()}")
    try:
        return json.loads(proc.stdout or "[]")
    except json.JSONDecodeError as exc:
        die(f"claude agents returned invalid JSON: {exc}")


def load_manifest() -> dict[str, Any]:
    if MANIFEST.exists():
        try:
            data = json.loads(MANIFEST.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("entries", {})
                return data
        except (OSError, json.JSONDecodeError):
            pass
    return {"version": 1, "entries": {}}


def save_manifest(manifest: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = MANIFEST.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    tmp.replace(MANIFEST)


def sync(
    manifest: dict[str, Any], live: list[dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    """Refresh existing manifest entries from the live registry.

    Sessions that peerctl did not create are not adopted automatically;
    use ``import --adopt`` for a deliberate handover.
    """
    if live is None:
        live = live_sessions()
    entries: dict[str, dict[str, Any]] = manifest.setdefault("entries", {})
    by_sid = {item.get("sessionId"): item for item in live if item.get("sessionId")}

    for sid, entry in entries.items():
        item = by_sid.get(sid)
        entry["live"] = item is not None
        if item:
            entry.update(
                {
                    "id": item.get("id"),
                    "name": item.get("name"),
                    "cwd": item.get("cwd"),
                    "status": item.get("status"),
                    "state": item.get("state"),
                    "pid": item.get("pid"),
                    "startedAt": item.get("startedAt"),
                    "kind": item.get("kind"),
                }
            )

    manifest["updated_at"] = int(time.time())
    save_manifest(manifest)
    return live


def resolve(live: list[dict[str, Any]], needle: str) -> dict[str, Any] | None:
    for item in live:
        if needle in (item.get("id"), item.get("sessionId"), item.get("name")):
            return item
    for item in live:
        sid = str(item.get("sessionId") or "")
        if sid.startswith(needle):
            return item
    return None


def parse_short_id(text: str) -> str | None:
    for pattern in (
        r"claude attach ([0-9a-z]{6,})",
        r"backgrounded\s*[·:]\s*([0-9a-z]{6,})",
    ):
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return None


def cmd_spawn(args: argparse.Namespace) -> int:
    cwd = str(Path(args.cwd).expanduser().resolve())
    if not Path(cwd).is_dir():
        die(f"cwd is not a directory: {cwd}")
    if args.engine == "bg":
        if args.backend != "claude":
            die("--engine bg only supports the claude backend")
        if not args.prompt:
            die("--engine bg requires --prompt")
        return _spawn_bg(args, cwd)
    return _spawn_process(args, cwd)


def _parse_env(values: list[str] | None) -> dict[str, str]:
    """Parse repeated ``KEY=VALUE`` options."""
    env: dict[str, str] = {}
    for item in values or []:
        key, sep, value = item.partition("=")
        if not sep or not key:
            die(f"--env expects KEY=VALUE, got {item!r}")
        env[key] = value
    return env


def _load_json_arg(value: str | None, label: str) -> Any:
    """Parse an inline JSON value or read it from a file."""
    if value is None:
        return None
    path = Path(value).expanduser()
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            die(f"{label} file is not valid JSON: {exc}")
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        die(f"{label} must be JSON or a JSON file: {exc}")


def _spawn_process(args: argparse.Namespace, cwd: str) -> int:
    params: dict[str, Any] = {
        "name": args.name,
        "cwd": cwd,
        "backend": args.backend,
    }
    if args.prompt:
        params["prompt"] = args.prompt
    if args.model:
        params["model"] = args.model
    if args.permission_mode:
        params["permission_mode"] = args.permission_mode
    agents = _load_json_arg(args.agent_json, "--agent-json")
    if agents is not None:
        if args.backend != "claude":
            die("--agent-json is only supported by the claude backend")
        params["agents"] = agents
    settings = _load_json_arg(args.settings, "--settings")
    if settings is not None:
        params["settings"] = settings
    if args.system_prompt:
        params["system_prompt"] = args.system_prompt
    for directory in args.add_dir or []:
        params.setdefault("add_dirs", []).append(directory)
    env = _parse_env(args.env)
    if env:
        params["env"] = env
    try:
        info = PeerClient().request("spawn", params, timeout=60)
    except PeerError as exc:
        die(str(exc))
    print(json.dumps(info, ensure_ascii=False))
    return 0


def _spawn_bg(args: argparse.Namespace, cwd: str) -> int:
    manifest = load_manifest()
    live = sync(manifest)
    cwd = str(Path(args.cwd).expanduser().resolve())

    if not Path(cwd).is_dir():
        die(f"cwd is not a directory: {cwd}")
    for item in live:
        if args.name in (item.get("name"), item.get("id")):
            die(f"name or id already in use: {args.name} ({item.get('id')})")

    workspace = args.workspace or Path(cwd).name or cwd
    cap = args.max or DEFAULT_MAX
    in_pool = [
        entry
        for entry in manifest["entries"].values()
        if entry.get("live") and entry.get("workspace") == workspace
    ]
    if len(in_pool) >= cap:
        die(
            "workspace pool is full: "
            f"{len(in_pool)}/{cap} managed sessions in {workspace}; "
            "stop one or raise --max"
        )

    cmd = ["claude", "--bg", "-n", args.name]
    if args.permission_mode:
        cmd += ["--permission-mode", args.permission_mode]
    if args.disallow:
        # Use the ``=`` form: ``--disallowedTools`` is variadic and would
        # otherwise swallow the positional prompt as another tool name.
        cmd.append(f"--disallowedTools={','.join(args.disallow)}")
    if args.model:
        cmd += ["--model", args.model]
    cmd.append(args.prompt)

    proc = run(cmd, cwd=cwd)
    if proc.returncode != 0:
        die(f"spawn failed: {(proc.stderr or proc.stdout).strip()}")

    short = parse_short_id(proc.stdout + proc.stderr)
    if not short:
        die(
            "spawn succeeded but no session id was found in output: "
            f"{proc.stdout.strip()}"
        )

    match = None
    deadline = time.time() + 15
    while time.time() < deadline:
        live = sync(manifest)
        match = next((item for item in live if item.get("id") == short), None)
        if match:
            break
        time.sleep(0.5)
    if match is None:
        die(f"session {short} was started but did not appear in claude agents --json")

    sid = match["sessionId"]
    entry = manifest["entries"].setdefault(sid, {})
    entry.update(
        {
            "adapter": CLAUDE,
            "sessionId": sid,
            "id": match.get("id"),
            "name": match.get("name"),
            "cwd": match.get("cwd"),
            "status": match.get("status"),
            "state": match.get("state"),
            "pid": match.get("pid"),
            "startedAt": match.get("startedAt"),
            "kind": match.get("kind"),
            "live": True,
            "created_by": "peerctl",
            "workspace": workspace,
        }
    )
    save_manifest(manifest)
    print(
        json.dumps(
            {
                "id": short,
                "name": args.name,
                "cwd": cwd,
                "workspace": entry["workspace"],
            },
            ensure_ascii=False,
        )
    )
    return 0


def _process_rows() -> list[dict[str, Any]]:
    """Read live peers from the daemon without starting it."""
    client = PeerClient(autostart=False)
    try:
        return client.request("list", timeout=5)
    except (PeerError, OSError, RuntimeError):
        return []


def cmd_ls(args: argparse.Namespace) -> int:
    manifest = load_manifest()
    live = sync(manifest)
    sources = (
        live
        if args.all
        else [entry for entry in manifest["entries"].values() if entry.get("live")]
    )
    rows: list[dict[str, Any]] = []
    for item in _process_rows():
        label = Path(str(item.get("cwd") or "")).name
        if args.workspace and args.workspace not in (label, str(item.get("cwd"))):
            continue
        if args.cwd and not str(item.get("cwd") or "").startswith(
            str(Path(args.cwd).expanduser().resolve())
        ):
            continue
        rows.append(
            {
                "workspace": label,
                "name": item.get("name"),
                "id": "-",
                "status": item.get("status"),
                "state": item.get("turn_id") or "-",
                "kind": "process",
                "engine": "process",
                "backend": item.get("backend") or "claude",
                "cwd": item.get("cwd"),
                "sessionId": item.get("session_id"),
            }
        )
    for item in sources:
        sid = item.get("sessionId")
        label = item.get("workspace") or Path(str(item.get("cwd") or "")).name
        if args.workspace and args.workspace not in (label, str(item.get("cwd"))):
            continue
        if args.cwd and not str(item.get("cwd") or "").startswith(
            str(Path(args.cwd).expanduser().resolve())
        ):
            continue
        rows.append(
            {
                "workspace": label,
                "name": item.get("name"),
                "id": item.get("id") or "-",
                "status": item.get("status"),
                "state": item.get("state"),
                "kind": item.get("kind"),
                "engine": "bg",
                "cwd": item.get("cwd"),
                "sessionId": sid,
            }
        )
    rows.sort(key=lambda row: (row["workspace"] or "", row["name"] or ""))
    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return 0
    if not rows:
        print("no live peer sessions")
        return 0
    header = (
        f"{'WORKSPACE':<18} {'NAME':<24} {'ENGINE':<8} {'BACKEND':<8} "
        f"{'ID':<10} {'STATUS':<9} {'STATE':<9} CWD"
    )
    print(header)
    for row in rows:
        print(
            f"{row['workspace'] or '-':<18} "
            f"{row['name'] or '-':<24} "
            f"{row['engine'] or '-':<8} "
            f"{row['backend'] or '-':<8} "
            f"{row['id'] or '-':<10} "
            f"{row['status'] or '-':<9} "
            f"{row['state'] or '-':<9} "
            f"{row['cwd'] or '-'}"
        )
    return 0


def _rpc(op: str, params: dict[str, Any], *, timeout: float = 60) -> Any:
    try:
        return PeerClient().request(op, params, timeout=timeout)
    except PeerError as exc:
        die(str(exc))


def cmd_send(args: argparse.Namespace) -> int:
    result = _rpc("send", {"name": args.name, "text": args.text})
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"{result['mode']} {result['turn_id']}")
    return 0


def cmd_steer(args: argparse.Namespace) -> int:
    result = _rpc(
        "steer",
        {"name": args.name, "text": args.text, "interrupt": args.interrupt},
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"{result['mode']} {result['turn_id']}")
    return 0


def cmd_interrupt(args: argparse.Namespace) -> int:
    result = _rpc("interrupt", {"name": args.name})
    print(json.dumps(result, ensure_ascii=False))
    return 0


def cmd_wait(args: argparse.Namespace) -> int:
    timeout = args.timeout
    turn = _rpc(
        "wait",
        {"name": args.name, "timeout": timeout},
        timeout=timeout + 15,
    )
    if args.json:
        print(json.dumps(turn, ensure_ascii=False))
    else:
        print(turn.get("text") or "")
    return 0


def cmd_inbox(args: argparse.Namespace) -> int:
    params: dict[str, Any] = {"clear": args.clear}
    if args.name:
        params["name"] = args.name
    notes = _rpc("inbox", params)
    if args.json:
        print(json.dumps(notes, ensure_ascii=False))
        return 0
    for note in notes:
        kind = note.get("kind")
        if kind == "turn":
            turn = note.get("turn") or {}
            print(
                f"turn {turn.get('turn_id')} {turn.get('status')}: {turn.get('text')}"
            )
        else:
            print(f"{kind}: {note.get('text')}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    status = _rpc("status", {"name": args.name})
    if args.json:
        print(json.dumps(status, ensure_ascii=False))
    else:
        print(
            f"{status.get('name')} [{status.get('engine')}/"
            f"{status.get('backend') or 'claude'}] "
            f"{status.get('status')} turn={status.get('turn_id') or '-'} "
            f"session={status.get('session_id') or '-'}"
        )
    return 0


def cmd_attach(args: argparse.Namespace) -> int:
    """Send one message to a session through Claude's peer socket."""
    for peer in _process_rows():
        if peer.get("name") == args.target:
            die(
                f"{args.target} is a process peer; use `peerctl send` "
                "(the socket would bypass its turn tracking)"
            )
    item = resolve(live_sessions(), args.target)
    if item is None:
        die(f"no live session matches {args.target!r}")
    pid = item.get("pid")
    if not pid:
        die(f"session {args.target!r} has no pid")
    receipt = send_cross_session(
        session_socket(int(pid)),
        args.text,
        from_name=args.from_name,
        from_mode=args.from_mode,
    )
    if args.json:
        print(json.dumps(receipt, ensure_ascii=False))
    else:
        print(
            f"sent {receipt['msg_id']} to {item.get('name') or args.target} "
            f"via {receipt['socket']} (no ack)"
        )
    return 0


def cmd_recover(args: argparse.Namespace) -> int:
    info = recover_peer(
        PeerClient(),
        args.name,
        permission_mode=args.permission_mode,
        settings=_load_json_arg(args.settings, "--settings"),
        env=_parse_env(args.env) or None,
    )
    if args.json:
        print(json.dumps(info, ensure_ascii=False))
    else:
        print(
            f"recovered {info.get('name')} session={info.get('session_id')} "
            f"pid={info.get('pid')}"
        )
    return 0


def cmd_daemon(args: argparse.Namespace) -> int:
    client = PeerClient()
    if args.action == "start":
        ping = client.ensure_daemon()
        print(json.dumps(ping, ensure_ascii=False))
        return 0
    if args.action == "stop":
        client.stop_daemon()
        print("stopped")
        return 0
    ping = client.ping()
    if ping is None:
        print("not running")
        return 1
    print(json.dumps(ping, ensure_ascii=False))
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    # A process peer lives in the daemon; stop it there when present.
    try:
        result = PeerClient(autostart=False).request(
            "stop", {"name": args.target}, timeout=10
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except PeerNotFound:
        pass
    except (PeerError, OSError, RuntimeError):
        pass
    manifest = load_manifest()
    live = sync(manifest)
    item = resolve(live, args.target)
    if item is None:
        die(f"no live session matches: {args.target}")
    if not item.get("id"):
        die(
            "not a background session (no short id): "
            f"{item.get('name')} ({item.get('sessionId')})"
        )
    proc = run(["claude", "stop", str(item.get("id"))])
    if proc.returncode != 0:
        die(f"stop failed: {(proc.stderr or proc.stdout).strip()}")
    if args.rm:
        run(["claude", "rm", str(item.get("id"))])
    entry = manifest["entries"].get(item.get("sessionId"), {})
    entry["live"] = False
    entry["stopped_at"] = int(time.time())
    save_manifest(manifest)
    print(f"stopped {item.get('id')} ({item.get('name')})")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    manifest = load_manifest()
    live = sync(manifest)
    entries = {
        sid: entry for sid, entry in manifest["entries"].items() if entry.get("live")
    }
    snapshot = {
        "version": 1,
        "exported_at": int(time.time()),
        "tool": "peerctl",
        "entries": entries,
        "live": live,
        "process_peers": _process_rows(),
    }
    path = Path(args.path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"exported {len(live)} live sessions to {path}")
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    path = Path(args.path).expanduser()
    try:
        old = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        die(f"cannot read snapshot: {exc}")

    manifest = load_manifest()
    live = sync(manifest)
    by_sid = {item.get("sessionId"): item for item in live}
    by_name_cwd = {(item.get("cwd"), item.get("name")): item for item in live}

    adopted: list[str] = []
    renamed: list[str] = []
    orphaned: list[str] = []
    for sid, entry in (old.get("entries") or {}).items():
        if sid in by_sid:
            adopted.append(sid)
        else:
            match = by_name_cwd.get((entry.get("cwd"), entry.get("name")))
            if match:
                renamed.append(f"{sid} -> {match.get('sessionId')}")
                if args.adopt:
                    manifest["entries"].setdefault(match["sessionId"], {}).update(
                        {
                            k: v
                            for k, v in entry.items()
                            if k not in ("sessionId", "live")
                        }
                    )
            else:
                orphaned.append(f"{sid} ({entry.get('workspace')}/{entry.get('name')})")

    if args.adopt:
        save_manifest(manifest)
    print(
        json.dumps(
            {
                "adopted": len(adopted),
                "matched_by_name_and_cwd": len(renamed),
                "orphaned": len(orphaned),
                "orphaned_entries": orphaned,
                "adopted_session_ids": adopted,
                "renamed": renamed,
                "adopted_into_manifest": bool(args.adopt),
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="peerctl", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    spawn = sub.add_parser("spawn", help="start a peer")
    spawn.add_argument("name")
    spawn.add_argument("--cwd", required=True)
    spawn.add_argument("--prompt", help="initial task (required for --engine bg)")
    spawn.add_argument(
        "--backend",
        choices=("claude", "codex", "opencode"),
        default="claude",
        help="peer backend for --engine process (default: claude)",
    )
    spawn.add_argument(
        "--engine",
        choices=("process", "bg"),
        default="process",
        help="process: daemon-owned, messageable (default); bg: claude --bg",
    )
    spawn.add_argument(
        "--workspace", help="label for listing; defaults to the cwd basename"
    )
    spawn.add_argument("--model")
    spawn.add_argument("--permission-mode", default="bypassPermissions")
    spawn.add_argument(
        "--agent-json",
        help="subagent definitions as JSON or a path to a JSON file",
    )
    spawn.add_argument(
        "--settings",
        help="agentabi settings as JSON or a path to a JSON file",
    )
    spawn.add_argument("--system-prompt")
    spawn.add_argument("--add-dir", action="append", help="repeatable")
    spawn.add_argument(
        "--env",
        action="append",
        metavar="KEY=VALUE",
        help="child environment entry (repeatable); never saved to state",
    )
    spawn.add_argument(
        "--disallow",
        action="append",
        default=["AskUserQuestion"],
        help="bg only: tool to disallow (repeatable; default: AskUserQuestion)",
    )
    spawn.add_argument(
        "--max", type=int, default=DEFAULT_MAX, help="bg live session cap"
    )
    spawn.set_defaults(func=cmd_spawn)

    ls = sub.add_parser("ls", help="list live sessions grouped by workspace")
    ls.add_argument("--workspace")
    ls.add_argument("--cwd")
    ls.add_argument("--json", action="store_true")
    ls.add_argument(
        "--all", action="store_true", help="include sessions not created by peerctl"
    )
    ls.set_defaults(func=cmd_ls)

    send = sub.add_parser("send", help="send a message: new turn, or steer")
    send.add_argument("name")
    send.add_argument("text")
    send.add_argument("--json", action="store_true")
    send.set_defaults(func=cmd_send)

    steer = sub.add_parser("steer", help="send into the running turn")
    steer.add_argument("name")
    steer.add_argument("text")
    steer.add_argument(
        "--interrupt", action="store_true", help="interrupt the turn first"
    )
    steer.add_argument("--json", action="store_true")
    steer.set_defaults(func=cmd_steer)

    interrupt = sub.add_parser("interrupt", help="cancel the running turn")
    interrupt.add_argument("name")
    interrupt.set_defaults(func=cmd_interrupt)

    wait = sub.add_parser("wait", help="wait for the running turn")
    wait.add_argument("name")
    wait.add_argument("--timeout", type=float, default=300.0)
    wait.add_argument("--json", action="store_true")
    wait.set_defaults(func=cmd_wait)

    inbox = sub.add_parser("inbox", help="read turn results, exits, denials")
    inbox.add_argument("name", nargs="?")
    inbox.add_argument("--clear", action="store_true")
    inbox.add_argument("--json", action="store_true")
    inbox.set_defaults(func=cmd_inbox)

    status = sub.add_parser("status", help="show one peer")
    status.add_argument("name")
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=cmd_status)

    attach = sub.add_parser("attach", help="message a bg or interactive session")
    attach.add_argument("target", help="session name, id, or session id")
    attach.add_argument("text")
    attach.add_argument("--from-name", default="agentabi-peerctl")
    attach.add_argument("--from-mode", choices=("bypass", "default"), default="bypass")
    attach.add_argument("--json", action="store_true")
    attach.set_defaults(func=cmd_attach)

    recover = sub.add_parser("recover", help="resume a saved process peer")
    recover.add_argument("name")
    recover.add_argument("--permission-mode")
    recover.add_argument(
        "--settings",
        help="agentabi settings for the new process (not saved in state)",
    )
    recover.add_argument(
        "--env",
        action="append",
        metavar="KEY=VALUE",
        help="child environment entry (repeatable); not saved in state",
    )
    recover.add_argument("--json", action="store_true")
    recover.set_defaults(func=cmd_recover)

    daemon = sub.add_parser("daemon", help="manage the peer daemon")
    daemon.add_argument("action", choices=("start", "stop", "status"))
    daemon.set_defaults(func=cmd_daemon)

    stop = sub.add_parser("stop", help="stop a session by name, short id, or sessionId")
    stop.add_argument("target")
    stop.add_argument(
        "--rm", action="store_true", help="also delete the session record"
    )
    stop.set_defaults(func=cmd_stop)

    export = sub.add_parser("export", help="write a handover snapshot")
    export.add_argument("path")
    export.set_defaults(func=cmd_export)

    imp = sub.add_parser(
        "import", help="reconcile a handover snapshot without stopping anything"
    )
    imp.add_argument("path")
    imp.add_argument(
        "--adopt",
        action="store_true",
        help="merge matched old entries into the manifest",
    )
    imp.set_defaults(func=cmd_import)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
