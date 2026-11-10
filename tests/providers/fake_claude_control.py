"""Scripted Claude control-protocol child used by provider tests.

It ignores argv, reads the stream-json user message from stdin, emits one
``can_use_tool`` request, and then follows ``FAKE_CLAUDE_MODE``:

``allow`` / ``deny``
    Wait for the control response, log it, emit a successful ``result``.
``noanswer``
    Wait for an ``interrupt`` control request, log it, emit a cancelled
    ``result``.
``cancel``
    Emit ``control_cancel_request`` for the pending request, then a
    successful ``result``.

Received messages are appended as JSON lines to ``FAKE_CLAUDE_LOG``.
"""

from __future__ import annotations

import json
import os
import sys
import time

MODE = os.environ.get("FAKE_CLAUDE_MODE", "allow")
LOG = os.environ.get("FAKE_CLAUDE_LOG", "")


def write(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def log(payload: dict) -> None:
    if LOG:
        with open(LOG, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload) + "\n")


def read_message() -> dict | None:
    line = sys.stdin.readline()
    if not line:
        return None
    try:
        message = json.loads(line)
    except json.JSONDecodeError:
        return None
    return message if isinstance(message, dict) else None


def result(*, error: bool = False) -> dict:
    return {
        "type": "result",
        "subtype": "error_during_execution" if error else "success",
        "is_error": error,
        "errors": ["interrupted"] if error else [],
        "usage": {},
        "session_id": "fake-session",
    }


def main() -> None:
    while True:
        message = read_message()
        if message is None:
            return
        if message.get("type") == "user":
            break

    write(
        {
            "type": "control_request",
            "request_id": "ctrl-1",
            "request": {
                "subtype": "can_use_tool",
                "tool_name": "Bash",
                "display_name": "Bash",
                "input": {"command": "touch marker1"},
                "description": "Create marker1",
                "permission_suggestions": [
                    {
                        "type": "addRules",
                        "rules": [
                            {"toolName": "Bash", "ruleContent": "touch marker1 *"}
                        ],
                        "behavior": "allow",
                        "destination": "localSettings",
                    }
                ],
                "tool_use_id": "toolu_1",
            },
        }
    )

    if MODE == "cancel":
        time.sleep(0.05)
        write({"type": "control_cancel_request", "request_id": "ctrl-1"})
        write(result())
        return

    while True:
        message = read_message()
        if message is None:
            return
        log(message)
        if MODE == "noanswer":
            request = message.get("request")
            if (
                message.get("type") == "control_request"
                and isinstance(request, dict)
                and request.get("subtype") == "interrupt"
            ):
                write({"type": "control_cancel_request", "request_id": "ctrl-1"})
                write(result(error=True))
                return
            continue
        response = message.get("response")
        if (
            message.get("type") == "control_response"
            and isinstance(response, dict)
            and response.get("request_id") == "ctrl-1"
        ):
            write(result())
            return


if __name__ == "__main__":
    main()
