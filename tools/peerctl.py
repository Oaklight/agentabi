#!/usr/bin/env python3
"""peerctl — minimal background peer control for Claude Code.

Scope: open, list, stop. No message sending. The main session handles
messages itself. Every command reconciles against Claude's own registry
(``claude agents --json``); the local manifest is a cache plus labels,
never the authority.

Commands:
    spawn NAME --cwd DIR --prompt TEXT [--model M] [--permission-mode MODE]
    ls [--workspace WS] [--cwd DIR] [--json]
    stop NAME|ID [--rm]
    export PATH
    import PATH [--adopt]

Handover design: entries are keyed by the adapter's stable identity
(Claude: sessionId). ``export`` before switching tools; ``import`` after
reconciles by sessionId first, then by (cwd, name). Import never stops
anything; unmatched entries are reported as orphans.
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
    manifest = load_manifest()
    live = sync(manifest)
    cwd = str(Path(args.cwd).expanduser().resolve())

    if not Path(cwd).is_dir():
        die(f"cwd is not a directory: {cwd}")
    for item in live:
        if args.name in (item.get("name"), item.get("id")):
            die(f"name or id already in use: {args.name} ({item.get('id')})")

    cap = args.max or DEFAULT_MAX
    in_pool = [entry for entry in manifest["entries"].values() if entry.get("live")]
    if len(in_pool) >= cap:
        die(
            "pool is full: "
            f"{len(in_pool)}/{cap} managed sessions; stop one or raise --max"
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
            "workspace": args.workspace or Path(cwd).name or cwd,
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


def cmd_ls(args: argparse.Namespace) -> int:
    manifest = load_manifest()
    live = sync(manifest)
    sources = (
        live
        if args.all
        else [entry for entry in manifest["entries"].values() if entry.get("live")]
    )
    rows = []
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
    header = f"{'WORKSPACE':<18} {'NAME':<24} {'ID':<10} {'STATUS':<9} {'STATE':<9} CWD"
    print(header)
    for row in rows:
        print(
            f"{row['workspace'] or '-':<18} "
            f"{row['name'] or '-':<24} "
            f"{row['id'] or '-':<10} "
            f"{row['status'] or '-':<9} "
            f"{row['state'] or '-':<9} "
            f"{row['cwd'] or '-'}"
        )
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
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

    spawn = sub.add_parser("spawn", help="start a background Claude session")
    spawn.add_argument("name")
    spawn.add_argument("--cwd", required=True)
    spawn.add_argument("--prompt", required=True)
    spawn.add_argument(
        "--workspace", help="label for listing; defaults to the cwd basename"
    )
    spawn.add_argument("--model")
    spawn.add_argument("--permission-mode", default="bypassPermissions")
    spawn.add_argument(
        "--disallow",
        action="append",
        default=["AskUserQuestion"],
        help="tool to disallow (repeatable; default: AskUserQuestion)",
    )
    spawn.add_argument("--max", type=int, default=DEFAULT_MAX, help="live session cap")
    spawn.set_defaults(func=cmd_spawn)

    ls = sub.add_parser("ls", help="list live sessions grouped by workspace")
    ls.add_argument("--workspace")
    ls.add_argument("--cwd")
    ls.add_argument("--json", action="store_true")
    ls.add_argument(
        "--all", action="store_true", help="include sessions not created by peerctl"
    )
    ls.set_defaults(func=cmd_ls)

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
