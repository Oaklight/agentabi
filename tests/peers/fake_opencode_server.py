"""In-process stand-in for the OpenCode V2 HTTP API used by peer tests.

The peer polls ``/api/session/active`` and the session messages, so the
fake completes a turn by appending one assistant message after a delay.
The text of a prompt selects behaviour:

- ``SLEEP=<seconds>``: hold the turn open for that long.
- ``FAIL``: complete the turn with an error.
- ``again``: answer with the previous answer.
- any other text: echo it.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

SESSION_ID = "ses_fake"


class _State:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.messages: list[dict[str, Any]] = []
        self.pending: dict[str, Any] | None = None
        self.counter = 0
        self.last_answer = ""

    def next_id(self, prefix: str) -> str:
        self.counter += 1
        return f"{prefix}_{self.counter}"

    def advance(self) -> None:
        pending = self.pending
        if pending is None or time.monotonic() < pending["deadline"]:
            return
        self.pending = None
        now = int(time.time() * 1000)
        message: dict[str, Any] = {
            "id": self.next_id("msg_a"),
            "parts": [{"type": "text", "text": pending["answer"]}],
            "time": {"created": pending["created"], "completed": now},
            "tokens": {
                "input": 11,
                "output": len(pending["answer"].split()),
                "cache": {"read": 2},
            },
        }
        if pending.get("error"):
            message["parts"] = []
            message["error"] = {"message": pending["error"]}
        else:
            self.last_answer = pending["answer"]
        self.messages.append(message)

    def prompt(self, text: str) -> None:
        self.advance()
        now = int(time.time() * 1000)
        self.messages.append(
            {
                "id": self.next_id("msg_u"),
                "parts": [{"type": "text", "text": text}],
                "time": {"created": now},
            }
        )
        delay = 0.0
        for token in text.split():
            if token.startswith("SLEEP="):
                delay = float(token.split("=", 1)[1])
        if self.pending is not None:
            answer = text
            delay = 0.0
        elif text.strip() == "again":
            answer = self.last_answer
        else:
            answer = text
        self.pending = {
            "answer": answer,
            "created": now,
            "deadline": time.monotonic() + delay,
            "error": "fake turn failed" if "FAIL" in text else "",
        }

    def interrupt(self) -> None:
        self.pending = None


class _Handler(BaseHTTPRequestHandler):
    state: _State

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            data = json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    def _send(self, status: int, payload: Any = None) -> None:
        body = b"" if payload is None else json.dumps(payload).encode()
        self.send_response(status)
        if payload is not None:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        path = urlparse(self.path).path
        if path == "/api/event":
            self._serve_events()
            return
        state = self.state
        with state.lock:
            if path == "/global/health":
                self._send(200, {"healthy": True, "version": "1.19.0"})
                return
            if path == "/api/session/active":
                state.advance()
                data = {SESSION_ID: {"type": "running"}} if state.pending else {}
                self._send(200, {"data": data})
                return
            if path == f"/api/session/{SESSION_ID}":
                self._send(200, {"id": SESSION_ID, "model": "fake/model"})
                return
            if path == f"/api/session/{SESSION_ID}/message":
                state.advance()
                self._send(200, {"data": state.messages})
                return
            if path in (
                f"/api/session/{SESSION_ID}/permission",
                f"/api/session/{SESSION_ID}/question",
            ):
                self._send(200, {"data": []})
                return
        self._send(404, {"error": f"unknown path {path}"})

    def _serve_events(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        try:
            self.wfile.write(b'data: {"type": "server.connected"}\n\n')
            self.wfile.flush()
            while True:
                time.sleep(1)
                self.wfile.write(b": heartbeat\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        path = urlparse(self.path).path
        state = self.state
        with state.lock:
            if path == "/session":
                self._send(200, {"id": SESSION_ID})
                return
            if path == f"/api/session/{SESSION_ID}/model":
                self._send(204)
                return
            if path == f"/api/session/{SESSION_ID}/prompt":
                body = self._body()
                text = str((body.get("prompt") or {}).get("text") or "")
                state.prompt(text)
                self._send(
                    200,
                    {
                        "data": {
                            "id": state.next_id("msg_p"),
                            "admittedSeq": state.counter,
                        }
                    },
                )
                return
            if path == f"/api/session/{SESSION_ID}/interrupt":
                state.interrupt()
                self._send(204)
                return
            if path.endswith("/reply"):
                self._send(204)
                return
            if path.endswith("/reject"):
                self._send(204)
                return
        self._send(404, {"error": f"unknown path {path}"})


class FakeOpenCodeServer:
    """Run the fake API on a background thread."""

    def __init__(self) -> None:
        handler = type("_Handler", (_Handler,), {"state": _State()})
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def __enter__(self) -> FakeOpenCodeServer:
        self._thread.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


__all__ = ["FakeOpenCodeServer", "SESSION_ID"]
