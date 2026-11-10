#!/usr/bin/env python3
"""Tap a line-oriented stdio protocol (NDJSON / JSON-RPC).

Spawns CMD, forwards --send lines to its stdin, and records every stdout,
stderr, and sent line as one JSON object per line in --out.

Example:
  capture_stdio.py --name codex --out captures/codex/app-server.jsonl \
    --send '{"jsonrpc":"2.0","id":1,"method":"initialize"}' \
    --send '{"jsonrpc":"2.0","id":2,"method":"thread/list","params":{}}' \
    -- codex app-server --listen stdio://
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wire_common import write_record  # noqa: E402


def _reader(stream, name: str, out: queue.Queue) -> None:
    try:
        for line in iter(stream.readline, ""):
            out.put((name, line.rstrip("\n")))
    finally:
        out.put((name, None))


def _pending_ids(sends: list[str]) -> set:
    pending = set()
    for line in sends:
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "id" in obj:
            pending.add(obj["id"])
    return pending


def _load_sends(args: argparse.Namespace) -> list[str]:
    sends = list(args.send)
    if args.send_file:
        with open(args.send_file, encoding="utf-8") as handle:
            sends.extend(line.rstrip("\n") for line in handle if line.strip())
    return sends


def _build_env(overrides: list[str]) -> dict[str, str]:
    env = dict(os.environ)
    for item in overrides:
        key, _, value = item.partition("=")
        env[key] = value
    return env


def _send_all(proc, sends: list[str], fh, redact: bool, delay: float) -> None:
    for line in sends:
        write_record(fh, "sent", line, redact)
        try:
            proc.stdin.write(line + "\n")
            proc.stdin.flush()
        except BrokenPipeError:
            return
        if delay:
            time.sleep(delay)


def _read_until_done(
    proc,
    queue_: queue.Queue,
    pending: set,
    deadline: float,
    fh,
    redact: bool,
) -> None:
    while time.time() < deadline:
        try:
            name, line = queue_.get(timeout=min(0.5, max(0.0, deadline - time.time())))
        except queue.Empty:
            if proc.poll() is not None:
                return
            continue
        if line is None:
            continue
        write_record(fh, name, line, redact)
        if name != "stdout":
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "id" in obj and obj["id"] in pending:
            pending.discard(obj["id"])
            if not pending:
                time.sleep(0.2)
                return


def _stop(proc) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--send", action="append", default=[])
    parser.add_argument("--send-file")
    parser.add_argument("--send-delay", type=float, default=0.0)
    parser.add_argument("--cwd")
    parser.add_argument("--env", action="append", default=[])
    parser.add_argument("--allow-secrets", action="store_true")
    parser.add_argument("cmd", nargs=argparse.REMAINDER)
    args = parser.parse_args()

    cmd = args.cmd[1:] if args.cmd and args.cmd[0] == "--" else args.cmd
    if not cmd:
        parser.error("missing command after --")

    sends = _load_sends(args)
    out_path = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        cwd=args.cwd,
        env=_build_env(args.env),
    )
    queue_: queue.Queue = queue.Queue()
    threading.Thread(
        target=_reader, args=(proc.stdout, "stdout", queue_), daemon=True
    ).start()
    threading.Thread(
        target=_reader, args=(proc.stderr, "stderr", queue_), daemon=True
    ).start()

    redact = not args.allow_secrets
    pending = _pending_ids(sends)
    deadline = time.time() + args.timeout

    with open(out_path, "w", encoding="utf-8") as fh:
        _send_all(proc, sends, fh, redact, args.send_delay)
        _read_until_done(proc, queue_, pending, deadline, fh, redact)

    _stop(proc)
    print(f"captured -> {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
