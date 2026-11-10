#!/usr/bin/env python3
"""Transparent tap for a Claude Code per-session messaging socket.

Claude Code listens on ``$XDG_RUNTIME_DIR/cc-socks/<pid>.sock`` (a Unix
stream socket) and peers connect to it to deliver messages.  This script
renames the socket, listens on the original path, forwards every
connection to the real listener, and logs both directions as NDJSON.

Restore: SIGINT/SIGTERM.  The proxy unlinks its listener and renames the
original socket back to ``<path>``.

Usage:
    python3 capture_cc_socket.py /run/user/1000/cc-socks/1234.sock \
        --out captures/claude/socket.frames.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import sys
import threading
import time

_lock = threading.Lock()
_log_fh = None


def _log(direction: str, data: bytes | str) -> None:
    rec = {"ts": time.time(), "dir": direction}
    if isinstance(data, bytes):
        rec["bytes"] = len(data)
        rec["data"] = data.decode("utf-8", "backslashreplace")
    else:
        rec["event"] = data
    with _lock:
        assert _log_fh is not None
        _log_fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        _log_fh.flush()


def _pump(src: socket.socket, dst: socket.socket, direction: str) -> None:
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            _log(direction, data)
            dst.sendall(data)
    except OSError:
        pass
    finally:
        try:
            dst.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def _handle(conn: socket.socket, target: str) -> None:
    upstream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        upstream.connect(target)
    except OSError as exc:
        _log("proxy", f"upstream connect failed: {exc}")
        conn.close()
        return
    _log("proxy", "connection opened")
    a = threading.Thread(
        target=_pump, args=(conn, upstream, "client->server"), daemon=True
    )
    b = threading.Thread(
        target=_pump, args=(upstream, conn, "server->client"), daemon=True
    )
    a.start()
    b.start()
    a.join()
    b.join()
    conn.close()
    upstream.close()
    _log("proxy", "connection closed")


def main() -> int:
    global _log_fh
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", help="messaging socket path")
    parser.add_argument("--out", required=True, help="NDJSON log path")
    args = parser.parse_args()

    path = os.path.abspath(args.path)
    original = path + ".orig"
    if os.path.exists(original):
        print(f"refusing: {original} already exists", file=sys.stderr)
        return 1
    if not os.path.exists(path):
        print(f"refusing: {path} does not exist", file=sys.stderr)
        return 1

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    _log_fh = open(args.out, "a", encoding="utf-8")

    os.rename(path, original)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(path)
    os.chmod(path, 0o600)
    server.listen(64)
    _log("proxy", f"tapping {path} -> {original}")

    stopping = threading.Event()

    def _stop(signum, frame):  # noqa: ARG001
        stopping.set()
        try:
            server.close()
        except OSError:
            pass

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    try:
        while not stopping.is_set():
            try:
                conn, _ = server.accept()
            except OSError:
                break
            threading.Thread(target=_handle, args=(conn, original), daemon=True).start()
    finally:
        try:
            server.close()
        except OSError:
            pass
        try:
            os.unlink(path)
        except OSError:
            pass
        try:
            os.rename(original, path)
        except OSError as exc:
            print(f"restore failed: {exc}", file=sys.stderr)
        _log("proxy", "stopped; socket restored")
        _log_fh.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
