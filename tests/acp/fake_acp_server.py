"""Minimal ACP server used to pin cross-session isolation in tests.

Reads newline-delimited JSON-RPC on stdin and writes responses on stdout.
Prompts are handled concurrently; each emits three text chunks with the
prompt text as the marker, interleaved with the other session's chunks.

Set ``FAKE_ACP_PERMISSION=1`` to make each prompt send one
``session/request_permission`` request before its updates.  The received
outcome is appended to ``FAKE_ACP_LOG`` as a JSON line.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

_counter = 0
_request_counter = 9000
_pending: dict[int, asyncio.Future[dict[str, Any]]] = {}


async def _handle(msg: dict) -> None:
    global _counter
    raw_id = msg.get("id")
    if raw_id is not None and ("result" in msg or "error" in msg):
        future = _pending.pop(int(raw_id), None)
        if future is not None and not future.done():
            future.set_result(msg)
        return
    method = msg.get("method")
    if raw_id is None:
        return
    req_id = int(raw_id)
    params = msg.get("params", {})

    if method == "initialize":
        await _send_result(
            req_id,
            {
                "protocolVersion": 1,
                "agentCapabilities": {},
                "agentInfo": {"name": "fake", "version": "1"},
            },
        )
        return

    if method == "session/new":
        _counter += 1
        await _send_result(req_id, {"sessionId": f"fake-{_counter}"})
        return

    if method == "session/prompt":
        asyncio.create_task(_run_prompt(req_id, params))
        return

    await _send_error(req_id, -32601, f"unsupported: {method}")


async def _run_prompt(req_id: int, params: dict) -> None:
    global _request_counter
    session_id = params.get("sessionId", "")
    if os.environ.get("FAKE_ACP_PERMISSION"):
        _request_counter += 1
        request_id = _request_counter
        future: asyncio.Future[dict[str, Any]] = (
            asyncio.get_running_loop().create_future()
        )
        _pending[request_id] = future
        await _write(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "session/request_permission",
                "params": {
                    "sessionId": session_id,
                    "toolCall": {
                        "toolCallId": "call-1",
                        "title": "touch marker",
                        "rawInput": {"command": "touch marker"},
                    },
                    "options": [
                        {
                            "optionId": "once",
                            "name": "Allow once",
                            "kind": "allow_once",
                        },
                        {"optionId": "reject", "name": "Reject", "kind": "reject_once"},
                    ],
                },
            }
        )
        response = await future
        _log(response)

    blocks = params.get("prompt", [])
    text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
    for _ in range(3):
        await asyncio.sleep(0.02)
        await _notify(
            "session/update",
            {
                "sessionId": session_id,
                "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": text},
                },
            },
        )
    await _send_result(req_id, {"stopReason": "end_turn"})


def _log(payload: dict) -> None:
    path = os.environ.get("FAKE_ACP_LOG")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload) + "\n")


async def _send_result(req_id: int, result: dict) -> None:
    await _write({"jsonrpc": "2.0", "id": req_id, "result": result})


async def _send_error(req_id: int, code: int, message: str) -> None:
    await _write(
        {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}
    )


async def _notify(method: str, params: dict) -> None:
    await _write({"jsonrpc": "2.0", "method": method, "params": params})


async def _write(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


async def main() -> None:
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    await loop.connect_read_pipe(
        lambda: asyncio.StreamReaderProtocol(reader), sys.stdin
    )
    while True:
        line = await reader.readline()
        if not line:
            break
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        await _handle(msg)


if __name__ == "__main__":
    asyncio.run(main())
