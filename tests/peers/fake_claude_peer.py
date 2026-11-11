#!/usr/bin/env python3
"""Scripted Claude Code stand-in for peer tests.

Reads stream-json user messages from stdin and writes stream-json rows to
stdout.  Special markers in the message:

- ``SLEEP=<seconds>``: delay before answering; a message that arrives
  during the delay steers the result.
- ``SUBAGENT``: emit an ``Agent`` tool call and its result first.
- ``FAIL``: answer with an error result.
"""

from __future__ import annotations

import json
import os
import select
import sys
import time
import uuid


def write(row: dict) -> None:
    sys.stdout.write(json.dumps(row) + "\n")
    sys.stdout.flush()


_buffer = b""


def read_message(timeout: float) -> dict | None:
    """Read one line from stdin without buffering ahead.

    ``sys.stdin.readline()`` can buffer several lines, which hides a
    steer message from ``select``; read raw bytes instead.
    """
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


def text_of(message: dict) -> str:
    content = (message.get("message") or {}).get("content") or []
    parts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "".join(parts)


def main() -> int:
    session_id = str(uuid.uuid4())
    for index, arg in enumerate(sys.argv):
        if arg == "--session-id" and index + 1 < len(sys.argv):
            session_id = sys.argv[index + 1]
    write(
        {
            "type": "system",
            "subtype": "init",
            "session_id": session_id,
            "model": "fake",
            "tools": ["Bash", "Agent"],
        }
    )

    try:
        return _serve(session_id)
    except EOFError:
        return 0


def _serve(session_id: str) -> int:  # noqa: C901 - scripted test double
    while True:
        message = read_message(1.0)
        if message is None:
            continue
        msg_type = message.get("type")
        if msg_type == "control_request":
            if (message.get("request") or {}).get("subtype") == "interrupt":
                write(
                    {
                        "type": "result",
                        "subtype": "error_during_execution",
                        "is_error": True,
                        "num_turns": 1,
                        "session_id": session_id,
                        "errors": ["interrupted"],
                        "result": None,
                    }
                )
            continue
        if msg_type != "user":
            continue

        text = text_of(message)
        steered: str | None = None
        delay = 0.0
        for token in text.split():
            if token.startswith("SLEEP="):
                delay = float(token.split("=", 1)[1])

        deadline = time.monotonic() + delay
        interrupted = False
        while time.monotonic() < deadline:
            extra = read_message(0.05)
            if extra is None:
                continue
            if extra.get("type") == "control_request":
                if (extra.get("request") or {}).get("subtype") == "interrupt":
                    interrupted = True
                    break
            elif extra.get("type") == "user":
                steered = text_of(extra)
                deadline = time.monotonic()

        if interrupted:
            write(
                {
                    "type": "result",
                    "subtype": "error_during_execution",
                    "is_error": True,
                    "num_turns": 1,
                    "session_id": session_id,
                    "errors": ["interrupted"],
                    "result": None,
                }
            )
            continue

        answer = steered if steered is not None else text

        if "SUBAGENT" in text:
            tool_id = "call_subagent_1"
            write(
                {
                    "type": "assistant",
                    "message": {
                        "id": "msg_sub_1",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": tool_id,
                                "name": "Agent",
                                "input": {
                                    "subagent_type": "probe",
                                    "prompt": "say hi",
                                    "run_in_background": False,
                                },
                            }
                        ],
                        "stop_reason": "tool_use",
                    },
                }
            )
            write(
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": tool_id,
                                "content": "[Subagent hand-back] SUBAGENT-OK",
                            }
                        ],
                    },
                }
            )

        write(
            {
                "type": "assistant",
                "message": {
                    "id": "msg_1",
                    "role": "assistant",
                    "content": [{"type": "text", "text": answer}],
                    "stop_reason": "end_turn",
                },
            }
        )

        if "FAIL" in text:
            write(
                {
                    "type": "result",
                    "subtype": "error_during_execution",
                    "is_error": True,
                    "num_turns": 1,
                    "session_id": session_id,
                    "errors": ["scripted failure"],
                    "result": None,
                }
            )
            continue

        write(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "num_turns": 2 if steered is not None else 1,
                "session_id": session_id,
                "result": answer,
                "usage": {"input_tokens": 3, "output_tokens": 2},
            }
        )


if __name__ == "__main__":
    raise SystemExit(main())
