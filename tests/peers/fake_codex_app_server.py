#!/usr/bin/env python3
"""Scripted ``codex app-server`` stand-in for peer tests.

Speaks newline-delimited JSON-RPC on stdio.  The text of ``turn/start``
selects behaviour:

- ``SLEEP=<seconds>``: stay in the turn until a ``turn/steer`` or
  ``turn/interrupt`` arrives, or the delay expires.
- ``FAIL``: complete the turn with status ``failed``.
- ``ARGV``: answer with the process argv as the assistant text.
- ``EXIT``: stop the process right after the turn is admitted.

Every other turn echoes the request text back as one assistant message.
"""

from __future__ import annotations

import json
import os
import select
import sys
import time
import uuid


def write(message: dict) -> None:
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def notify(method: str, params: dict) -> None:
    write({"jsonrpc": "2.0", "method": method, "params": params})


def result(request: dict, payload: dict) -> None:
    write({"jsonrpc": "2.0", "id": request.get("id"), "result": payload})


def error(request: dict, message: str) -> None:
    write(
        {
            "jsonrpc": "2.0",
            "id": request.get("id"),
            "error": {"code": -32601, "message": message},
        }
    )


_buffer = b""


def read_message(timeout: float) -> dict | None:
    """Read one line without buffering ahead, so a steer is visible."""
    global _buffer
    fd = sys.stdin.fileno()
    while b"\n" not in _buffer:
        ready, _, _ = select.select([fd], [], [], timeout)
        if not ready:
            return None
        chunk = os.read(fd, 65536)
        if not chunk:
            raise EOFError
        _buffer += chunk
    line, _buffer = _buffer.split(b"\n", 1)
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


def message_text(params: dict) -> str:
    parts = []
    for item in params.get("input") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text") or ""))
    return "".join(parts)


def complete(thread_id: str, turn_id: str, text: str, *, failed: bool = False) -> None:
    turn: dict = {
        "id": turn_id,
        "threadId": thread_id,
        "status": "failed" if failed else "completed",
        "items": []
        if failed
        else [
            {
                "id": f"item-{uuid.uuid4().hex[:8]}",
                "type": "agentMessage",
                "content": [{"type": "text", "text": text}],
            }
        ],
    }
    if failed:
        turn["error"] = {"message": "fake turn failed"}
    notify("turn/completed", {"threadId": thread_id, "turn": turn})


def run_turn(thread_id: str, turn_id: str, text: str) -> None:
    delay = 0.0
    for token in text.split():
        if token.startswith("SLEEP="):
            delay = float(token.split("=", 1)[1])
    deadline = time.monotonic() + delay
    while time.monotonic() < deadline:
        message = read_message(0.05)
        if message is None:
            continue
        method = message.get("method")
        if method == "turn/steer":
            steered = message_text(message.get("params") or {})
            result(message, {"turnId": turn_id})
            notify(
                "thread/tokenUsage/updated",
                {
                    "threadId": thread_id,
                    "tokenUsage": {
                        "last": {
                            "inputTokens": 10,
                            "outputTokens": 5,
                            "cachedInputTokens": 1,
                            "totalTokens": 15,
                        }
                    },
                },
            )
            notify(
                "item/agentMessage/delta",
                {"threadId": thread_id, "delta": steered},
            )
            complete(thread_id, turn_id, steered)
            return
        if method == "turn/interrupt":
            result(message, {})
            notify(
                "turn/completed",
                {
                    "threadId": thread_id,
                    "turn": {"id": turn_id, "status": "interrupted", "items": []},
                },
            )
            return
        error(message, f"unexpected request during turn: {method}")

    if "FAIL" in text:
        complete(thread_id, turn_id, "", failed=True)
        return
    answer = " ".join(sys.argv) if "ARGV" in text else text
    notify(
        "thread/tokenUsage/updated",
        {
            "threadId": thread_id,
            "tokenUsage": {
                "last": {
                    "inputTokens": 7,
                    "outputTokens": 3,
                    "cachedInputTokens": 0,
                    "totalTokens": 10,
                }
            },
        },
    )
    notify("item/agentMessage/delta", {"threadId": thread_id, "delta": answer})
    complete(thread_id, turn_id, answer)


def main() -> int:
    thread_id = str(uuid.uuid4())
    try:
        while True:
            message = read_message(1.0)
            if message is None:
                continue
            method = message.get("method")
            params = message.get("params") or {}
            if method == "initialize":
                result(message, {"userAgent": "fake-app-server"})
            elif method in ("thread/start", "thread/resume"):
                if method == "thread/resume":
                    thread_id = str(params.get("threadId") or thread_id)
                result(message, {"thread": {"id": thread_id, "cwd": params.get("cwd")}})
                notify("thread/started", {"thread": {"id": thread_id}})
            elif method == "turn/start":
                turn_id = str(uuid.uuid4())
                result(message, {"turn": {"id": turn_id, "status": "inProgress"}})
                if "EXIT" in message_text(params):
                    return 0
                notify(
                    "turn/started",
                    {
                        "threadId": thread_id,
                        "turn": {"id": turn_id, "status": "inProgress"},
                    },
                )
                run_turn(thread_id, turn_id, message_text(params))
            elif method == "turn/steer":
                error(message, "no active turn")
            elif method == "turn/interrupt":
                result(message, {})
            else:
                error(message, f"unknown method: {method}")
    except EOFError:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
