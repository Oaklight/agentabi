"""Scripted ``codex app-server`` child for backend unit tests.

Speaks newline-delimited JSON-RPC.  It implements ``initialize``,
``thread/start``, ``turn/start``, and ``turn/interrupt``, and emits one
``item/*/requestApproval`` server request per turn.

Environment:
    FAKE_APPSERVER_LOG: append received approval answers as JSON lines.
    FAKE_APPSERVER_METHOD: ``command`` (default) or ``file``.
    FAKE_APPSERVER_DECISIONS: JSON list used as ``availableDecisions`` for
        command approvals.  Omit for file-change requests.
    FAKE_APPSERVER_TEXT: text delta emitted before the approval.
    FAKE_APPSERVER_NO_APPROVAL: ``1`` to skip the approval request.
    FAKE_APPSERVER_TURN_STATUS: force the final turn status.
    FAKE_APPSERVER_TURN_ITEMS: ``1`` to include completed items in
        ``turn/completed``.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

_thread_id = "thread-1"
_turn_counter = 0
_request_counter = 0
_pending: dict[int, asyncio.Future[dict[str, Any]]] = {}


def _log(payload: dict[str, Any]) -> None:
    path = os.environ.get("FAKE_APPSERVER_LOG")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload) + "\n")


async def _write(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


async def _result(req_id: Any, result: dict[str, Any]) -> None:
    await _write({"jsonrpc": "2.0", "id": req_id, "result": result})


async def _error(req_id: Any, message: str) -> None:
    await _write(
        {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": message},
        }
    )


async def _notify(method: str, params: dict[str, Any]) -> None:
    await _write({"jsonrpc": "2.0", "method": method, "params": params})


async def _approve(params: dict[str, Any], method: str) -> str:
    """Send the approval request and wait for the client's answer."""
    global _request_counter
    _request_counter += 1
    server_id = _request_counter
    future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
    _pending[server_id] = future
    await _write(
        {"jsonrpc": "2.0", "id": server_id, "method": method, "params": params}
    )
    response = await future
    result = response.get("result")
    decision = result.get("decision") if isinstance(result, dict) else None
    _log({"requestId": server_id, "result": result})
    return str(decision)


async def _run_turn(turn_id: str) -> None:
    item_id = "item-1"
    method = os.environ.get("FAKE_APPSERVER_METHOD", "command")
    item_type = "fileChange" if method == "file" else "commandExecution"
    approval_method = (
        "item/fileChange/requestApproval"
        if method == "file"
        else "item/commandExecution/requestApproval"
    )

    await _notify(
        "turn/started",
        {
            "threadId": _thread_id,
            "turn": {"id": turn_id, "status": "inProgress", "items": []},
        },
    )
    item: dict[str, Any] = {
        "type": item_type,
        "id": item_id,
        "status": "inProgress",
    }
    if item_type == "commandExecution":
        item.update({"command": "touch marker", "cwd": os.getcwd()})
    else:
        item.update({"changes": []})
    await _notify(
        "item/started",
        {
            "item": item,
            "threadId": _thread_id,
            "turnId": turn_id,
            "startedAtMs": 1,
        },
    )

    text = os.environ.get("FAKE_APPSERVER_TEXT", "working")
    if text:
        await _notify(
            "item/agentMessage/delta",
            {
                "delta": text,
                "itemId": "msg-1",
                "threadId": _thread_id,
                "turnId": turn_id,
            },
        )

    status = "completed"
    turn_status = os.environ.get("FAKE_APPSERVER_TURN_STATUS", "completed")
    if os.environ.get("FAKE_APPSERVER_NO_APPROVAL") != "1":
        params: dict[str, Any] = {
            "threadId": _thread_id,
            "turnId": turn_id,
            "itemId": item_id,
            "startedAtMs": 1,
            "reason": "The sandbox is read-only. Allow?",
        }
        if item_type == "commandExecution":
            params.update(
                {
                    "kind": "command",
                    "command": "touch marker",
                    "cwd": os.getcwd(),
                }
            )
            decisions = os.environ.get("FAKE_APPSERVER_DECISIONS")
            if decisions:
                params["availableDecisions"] = json.loads(decisions)
        else:
            params["grantRoot"] = os.getcwd()
        decision = await _approve(params, approval_method)
        if decision in ("cancel", "decline"):
            status = "declined"
            if decision == "cancel":
                turn_status = "interrupted"

    completed = {**item, "status": status, "aggregatedOutput": "done"}
    await _notify(
        "item/completed",
        {
            "item": completed,
            "threadId": _thread_id,
            "turnId": turn_id,
            "completedAtMs": 2,
        },
    )
    items = [completed] if os.environ.get("FAKE_APPSERVER_TURN_ITEMS") == "1" else []
    await _notify(
        "turn/completed",
        {
            "threadId": _thread_id,
            "turn": {
                "id": turn_id,
                "status": turn_status,
                "items": items,
                "error": (
                    {"message": "forced failure"} if turn_status == "failed" else None
                ),
            },
        },
    )


async def _handle_request(req_id: Any, method: str, params: dict[str, Any]) -> None:
    global _turn_counter
    if method == "initialize":
        await _result(req_id, {"userAgent": "fake/0", "platformOs": "linux"})
    elif method == "thread/start":
        await _result(
            req_id,
            {"thread": {"id": _thread_id, "cwd": params.get("cwd", "")}},
        )
    elif method == "turn/start":
        _turn_counter += 1
        turn_id = f"turn-{_turn_counter}"
        await _result(req_id, {"turn": {"id": turn_id, "status": "inProgress"}})
        asyncio.create_task(_run_turn(turn_id))
    elif method == "turn/interrupt":
        await _result(req_id, {})
        await _notify(
            "turn/completed",
            {
                "threadId": _thread_id,
                "turn": {
                    "id": f"turn-{_turn_counter}",
                    "status": "interrupted",
                    "items": [],
                },
            },
        )
    else:
        await _error(req_id, f"unsupported: {method}")


async def main() -> None:
    loop = asyncio.get_running_loop()
    while True:
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if not line:
            break
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "id" in message and "method" not in message:
            future = _pending.pop(message["id"], None)
            if future is not None and not future.done():
                future.set_result(message)
            continue
        if "method" not in message:
            continue
        if "id" in message:
            await _handle_request(
                message["id"], message["method"], message.get("params", {})
            )


if __name__ == "__main__":
    asyncio.run(main())
