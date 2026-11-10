#!/usr/bin/env python3
"""Capture a newline-delimited JSON protocol over a Unix domain socket.

Example:
  capture_socket.py --name claude-peer --path "$CLAUDE_CODE_MESSAGING_SOCKET" \
    --auth-token-env CLAUDE_CODE_MESSAGING_TOKEN \
    --send '{"type":"message","text":"hello"}' \
    --out captures/claude/peer-socket.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wire_common import write_record  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    parser.add_argument("--path", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--send", action="append", default=[])
    parser.add_argument("--send-file")
    parser.add_argument("--auth-json")
    parser.add_argument("--auth-token-env")
    parser.add_argument("--allow-secrets", action="store_true")
    args = parser.parse_args()

    sends = list(args.send)
    if args.send_file:
        with open(args.send_file, encoding="utf-8") as handle:
            sends.extend(line.rstrip("\n") for line in handle if line.strip())

    auth_line = args.auth_json
    if auth_line is None and args.auth_token_env:
        auth_line = json.dumps(
            {"type": "auth", "token": os.environ[args.auth_token_env]}
        )

    out_path = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    redact = not args.allow_secrets

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(args.timeout)
    sock.connect(args.path)

    with open(out_path, "w", encoding="utf-8") as fh:
        if auth_line:
            write_record(fh, "sent", auth_line, redact)
            sock.sendall((auth_line + "\n").encode())

        for line in sends:
            write_record(fh, "sent", line, redact)
            sock.sendall((line + "\n").encode())

        buffer = b""
        deadline = time.time() + args.timeout
        while time.time() < deadline:
            sock.settimeout(max(0.1, deadline - time.time()))
            try:
                chunk = sock.recv(65536)
            except TimeoutError:
                break
            if not chunk:
                break
            buffer += chunk
            while b"\n" in buffer:
                raw, buffer = buffer.split(b"\n", 1)
                write_record(fh, "recv", raw.decode("utf-8", "replace"), redact)

        if buffer:
            write_record(fh, "recv", buffer.decode("utf-8", "replace"), redact)

    sock.close()
    print(f"captured -> {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
