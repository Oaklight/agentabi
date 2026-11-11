#!/usr/bin/env python3
"""Scripted bidirectional ``agy`` stream-json stand-in for peer tests.

Reads ``{"event": "user"|"interrupt"}`` lines.  The prompt text selects
behaviour:

- ``SLEEP=<seconds>``: keep the turn open; the answer becomes ``SLOW-DONE``
  unless an ``interrupt`` arrives first.
- ``EXIT``: stop the process after the turn is admitted.
- ``again``: answer with the previous answer.

An ``interrupt`` during ``SLEEP`` ends the turn with status ``ERROR`` and
``error: interrupted``, the way agy 1.5.5 reports it.
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


def step(index: int, step_type: str, **fields: object) -> None:
    write(
        {
            "event": "step_update",
            "step_update": {
                "conversation_id": CONVERSATION,
                "step_index": index,
                "state": "DONE",
                "step_type": step_type,
                **fields,
            },
        }
    )


def result(status: str, response: str, *, error: str = "") -> None:
    payload = {
        "conversation_id": CONVERSATION,
        "status": status,
        "response": response,
        "num_turns": 1,
        "usage": {
            "input_tokens": 21,
            "output_tokens": len(response.split()),
            "cache_read_tokens": 3,
            "total_tokens": 24,
        },
    }
    if error:
        payload["error"] = error
    write({"event": "result", "result": payload})


_buffer = b""


def read_event(timeout: float) -> dict | None:
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


CONVERSATION = "agy-fake"


def prompt_text(event: dict) -> str:
    message = event.get("message") or {}
    content = message.get("content") or []
    parts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text") or ""))
    return "".join(parts)


def main() -> int:
    last_answer = ""
    sent_init = False
    index = 0
    try:
        while True:
            event = read_event(1.0)
            if event is None:
                continue
            if not sent_init:
                write(
                    {
                        "event": "init",
                        "conversation_id": CONVERSATION,
                        "init": {"model": "fake", "cwd": os.getcwd()},
                    }
                )
                sent_init = True
            if event.get("event") != "user":
                continue
            text = prompt_text(event)
            index += 1
            step(index, "user_input")
            if "EXIT" in text:
                return 0
            delay = 0.0
            for token in text.split():
                if token.startswith("SLEEP="):
                    delay = float(token.split("=", 1)[1])
            deadline = time.monotonic() + delay
            interrupted = False
            while time.monotonic() < deadline:
                extra = read_event(0.05)
                if extra is None:
                    continue
                if extra.get("event") == "interrupt":
                    interrupted = True
                    break
            if interrupted:
                result("ERROR", "", error="interrupted")
                continue
            answer = last_answer if text.strip() == "again" else text
            index += 1
            write(
                {
                    "event": "step_update",
                    "step_update": {
                        "conversation_id": CONVERSATION,
                        "step_index": index,
                        "state": "DONE",
                        "step_type": "agent_response",
                        "text_delta": answer,
                    },
                }
            )
            result("SUCCESS", answer)
            last_answer = answer
    except EOFError:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
