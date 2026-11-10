"""Mock OpenCode V2 HTTP/SSE server for provider tests."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, cast
from urllib.parse import urlsplit


class MockOpenCodeV2:
    """A tiny `opencode serve` stand-in speaking the V2 HTTP/SSE API.

    The SSE handler blocks until ``/prompt`` is called, then replays
    ``script`` and either closes the stream or holds it open (so a
    provider timeout can be exercised).
    """

    def __init__(
        self,
        script: Iterable[dict[str, Any]] = (),
        *,
        session_id: str = "ses_mock",
        support_v2: bool = True,
        hold_open: bool = False,
    ) -> None:
        self.script = list(script)
        self.session_id = session_id
        self.hold_open = hold_open
        self.support_v2 = support_v2
        self.known_sessions: set[str] | None = None
        self.prompts: list[dict[str, Any]] = []
        self.models: list[dict[str, Any]] = []
        self.interrupts = 0
        self.requests: list[tuple[str, str]] = []
        self.prompt_sent = threading.Event()
        self.stop = threading.Event()
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        setattr(self._server, "mock", self)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def __enter__(self) -> MockOpenCodeV2:
        self._thread.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop.set()
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    @property
    def mock(self) -> MockOpenCodeV2:
        return cast(MockOpenCodeV2, getattr(self.server, "mock"))

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        self.mock.requests.append(("GET", path))
        if path == "/api/session/active":
            if not self.mock.support_v2:
                self._json(404, {"error": "not found"})
                return
            self._json(200, {"data": {}})
        elif path == "/api/event":
            self._stream_events()
        elif path == "/global/health":
            self._json(200, {"healthy": True, "version": "1.19.0"})
        elif path == "/config":
            self._json_chunked(200, {"model": "mock/default-model"})
        elif path.startswith("/api/session/"):
            session_id = path.split("/")[3] if len(path.split("/")) > 3 else ""
            if self.mock.known_sessions is not None:
                if session_id not in self.mock.known_sessions:
                    self._json(404, {"error": "SessionNotFoundError"})
                    return
            self._json(200, {"data": {"id": session_id}})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        body = self._read_json()
        self.mock.requests.append(("POST", path))
        if path == "/session":
            self._json(200, {"id": self.mock.session_id, "title": "mock"})
        elif path.endswith("/model"):
            self.mock.models.append(body)
            self._empty(204)
        elif path.endswith("/prompt"):
            self.mock.prompts.append(body)
            self.mock.prompt_sent.set()
            self._json(
                200,
                {
                    "data": {
                        "admittedSeq": 1,
                        "id": "msg_user_mock",
                        "sessionID": self.mock.session_id,
                        "delivery": "steer",
                        "timeCreated": 0,
                    }
                },
            )
        elif path.endswith("/interrupt"):
            self.mock.interrupts += 1
            self._empty(204)
        elif "/permission/" in path and path.endswith("/reply"):
            self._empty(204)
        elif "/question/" in path and path.endswith("/reject"):
            self._empty(204)
        else:
            self._json(404, {"error": "not found"})

    # -- helpers ----------------------------------------------------------

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _json_chunked(self, status: int, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        self._chunk(raw[:5])
        self._chunk(raw[5:])
        self._chunk(b"")

    def _empty(self, status: int) -> None:
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _chunk(self, data: bytes) -> None:
        try:
            self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
            self.wfile.flush()
        except OSError:
            # The provider closed the stream early (timeout / cancellation).
            self.mock.stop.set()

    def _stream_events(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        self._sse({"id": "evt_connected", "type": "server.connected", "data": {}})
        # For tests that only probe the stream, do not wait for a prompt.
        self.mock.prompt_sent.wait(timeout=5)
        for payload in self.mock.script:
            if self.mock.stop.is_set():
                return
            self._sse(payload)
        if self.mock.hold_open:
            self.mock.stop.wait(timeout=5)
        self._chunk(b"")

    def _sse(self, payload: dict[str, Any]) -> None:
        self._chunk(f"data: {json.dumps(payload)}\n\n".encode())


__all__ = ["MockOpenCodeV2"]
