"""mitmproxy addon: record HTTP(S) and WebSocket messages as NDJSON.

Usage:
  CAPTURE_OUT=captures/amp/mitm.ndjson \
  CAPTURE_HOSTS=ampcode.com \
  mitmdump -p 9091 -s bin/capture_mitm.py

Environment:
  CAPTURE_OUT    output NDJSON path
  CAPTURE_HOSTS  comma-separated host substrings; empty means all hosts
  CAPTURE_BODY   max body characters to record (default 20000)
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from mitmproxy import http

OUT = os.environ.get("CAPTURE_OUT", "captures/mitm.ndjson")
HOSTS = [h.strip() for h in os.environ.get("CAPTURE_HOSTS", "").split(",") if h.strip()]
MAX_BODY = int(os.environ.get("CAPTURE_BODY", "20000"))

_SECRET_HEADERS = {
    "authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
    "api-key",
    "proxy-authorization",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _allowed(host: str) -> bool:
    return not HOSTS or any(item in host for item in HOSTS)


def _write(record: dict) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(OUT)), exist_ok=True)
    with open(OUT, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _headers(headers) -> dict[str, str]:
    return {
        key: ("<redacted>" if key.lower() in _SECRET_HEADERS else value)
        for key, value in headers.items()
    }


def _body(message) -> str | None:
    try:
        text = message.get_text(strict=False)
    except Exception:
        return None
    if text is None:
        return None
    return text[:MAX_BODY]


def request(flow: http.HTTPFlow) -> None:
    if not _allowed(flow.request.pretty_host):
        return
    _write(
        {
            "ts": _now(),
            "dir": "http_request",
            "method": flow.request.method,
            "url": flow.request.pretty_url,
            "headers": _headers(flow.request.headers),
            "body": _body(flow.request),
        }
    )


def response(flow: http.HTTPFlow) -> None:
    if not _allowed(flow.request.pretty_host):
        return
    _write(
        {
            "ts": _now(),
            "dir": "http_response",
            "status": flow.response.status_code,
            "url": flow.request.pretty_url,
            "headers": _headers(flow.response.headers),
            "body": _body(flow.response),
        }
    )


def websocket_message(flow: http.HTTPFlow) -> None:
    if not _allowed(flow.request.pretty_host):
        return
    if not flow.websocket or not flow.websocket.messages:
        return
    message = flow.websocket.messages[-1]
    _write(
        {
            "ts": _now(),
            "dir": "ws_client" if message.from_client else "ws_server",
            "url": flow.request.pretty_url,
            "body": message.content.decode("utf-8", "replace")[:MAX_BODY],
        }
    )
