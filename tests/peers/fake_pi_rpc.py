#!/usr/bin/env python3
"""Scripted ``pi --mode rpc`` stand-in for peer tests.

Reads one JSON command per line.  ``prompt`` starts a run, ``steer``
during a ``SLEEP=<seconds>`` run answers with the steering text, and
``abort`` settles the run as aborted.

Markers in the prompt text:

- ``SLEEP=<seconds>``: hold the run open.
- ``EXIT``: stop the process after the prompt is admitted.
- ``FAIL``: answer the command with ``success: false``.
- ``again``: answer with the previous answer.
"""

from __future__ import annotations

import json
import os
import select
import sys
import time


def write(message: dict) -> None:
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def respond(command: dict, data: dict | None = None, *, success: bool = True) -> None:
    message = {
        "id": str(command.get("id")),
        "type": "response",
        "command": command.get("type"),
        "success": success,
    }
    if data is not None:
        message["data"] = data
    if not success:
        message["error"] = data.get("error") if isinstance(data, dict) else "failed"
    write(message)


def event(kind: str, **payload: object) -> None:
    write({"type": kind, **payload})


_buffer = b""


def read_command(timeout: float) -> dict | None:
    """Read one command without buffering ahead."""
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
        message = json.loads(line)
    except json.JSONDecodeError:
        return None
    return message if isinstance(message, dict) else None


def assistant(text: str) -> dict:
    return {
        "role": "assistant",
        "content": [{"type": "text", "text": text}],
        "usage": {
            "input": 11,
            "output": len(text.split()),
            "cacheRead": 2,
            "totalTokens": 13,
        },
    }


def finish(text: str, *, aborted: bool = False) -> None:
    event("turn_end", message=assistant(text))
    event("agent_end", willRetry=False)
    event("agent_settled", aborted=aborted)


def run_turn(command: dict, text: str, answer: str, delay: float) -> None:
    deadline = time.monotonic() + delay
    while time.monotonic() < deadline:
        extra = read_command(0.05)
        if extra is None:
            continue
        if extra.get("type") == "steer":
            respond(extra, {"disposition": "queued"})
            steered = str(extra.get("message") or "")
            event("turn_start", turnIndex=1)
            event(
                "message_update",
                assistantMessageEvent={"type": "text_delta", "delta": steered},
            )
            finish(steered)
            return
        if extra.get("type") == "abort":
            finish("", aborted=True)
            respond(extra)
            return
        respond(extra, {"error": "unsupported command"}, success=False)
    event("turn_start", turnIndex=0)
    event(
        "message_update",
        assistantMessageEvent={"type": "text_delta", "delta": answer},
    )
    finish(answer)


def main() -> int:
    last_answer = ""
    try:
        while True:
            command = read_command(1.0)
            if command is None:
                continue
            kind = command.get("type")
            if kind == "get_state":
                respond(command, {"sessionId": "pi-fake", "isStreaming": False})
            elif kind == "prompt":
                text = str(command.get("message") or "")
                if "FAIL" in text:
                    respond(command, {"error": "fake prompt failed"}, success=False)
                    continue
                respond(command, {"disposition": "started"})
                if "EXIT" in text:
                    return 0
                event("agent_start")
                delay = 0.0
                for token in text.split():
                    if token.startswith("SLEEP="):
                        delay = float(token.split("=", 1)[1])
                answer = last_answer if text.strip() == "again" else text
                run_turn(command, text, answer, delay)
                last_answer = answer
            elif kind == "steer":
                respond(command, {"error": "no running turn"}, success=False)
            elif kind == "abort":
                respond(command)
            else:
                respond(command, {"error": f"unknown command {kind}"}, success=False)
    except EOFError:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
