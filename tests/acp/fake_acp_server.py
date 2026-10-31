"""Minimal ACP server used to pin cross-session isolation in tests.

Reads newline-delimited JSON-RPC on stdin and writes responses on stdout.
Prompts are handled concurrently; each emits three text chunks with the
prompt text as the marker, interleaved with the other session's chunks.
"""

from __future__ import annotations

import asyncio
import json
import sys

_counter = 0


async def _handle(msg: dict) -> None:
    global _counter
    method = msg.get("method")
    raw_id = msg.get("id")
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
    session_id = params.get("sessionId", "")
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
