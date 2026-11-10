"""
agentabi - Minimal HTTP/SSE client (stdlib only)

Small asyncio HTTP/1.1 client used by the OpenCode V2 provider. It covers
exactly what a local `opencode serve` needs: unary JSON requests and a
server-sent-events stream. No third-party dependency is involved.

Limits (deliberate): HTTP/1.1 only, one request per connection
(``Connection: close``), no redirects, no compression.
"""

from __future__ import annotations

import asyncio
import base64
import json
import ssl
from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

_DEFAULT_CONNECT_TIMEOUT = 10.0
_MAX_HEADER_BYTES = 64 * 1024
_READ_CHUNK = 64 * 1024


class HTTPError(Exception):
    """Raised for a transport failure or a non-2xx response."""

    def __init__(self, message: str, *, status: int | None = None, body: str = ""):
        super().__init__(message)
        self.status = status
        self.body = body


@dataclass
class SSEMessage:
    """One dispatched server-sent event."""

    event: str = "message"
    data: str = ""
    id: str = ""


@dataclass
class SSEParser:
    """Incremental SSE decoder (RFC 9110 field syntax, WHATWG semantics).

    Feed decoded text with :meth:`feed`; it returns every event completed
    by that chunk. Comment lines (``:``) and unknown fields are ignored.
    """

    _buffer: str = ""
    _data: list[str] = field(default_factory=list)
    _event: str = ""
    _id: str = ""

    def feed(self, text: str) -> list[SSEMessage]:
        """Decode a text chunk and return the events it completes."""
        self._buffer += text
        messages: list[SSEMessage] = []
        while True:
            newline = self._buffer.find("\n")
            if newline < 0:
                break
            line = self._buffer[:newline]
            self._buffer = self._buffer[newline + 1 :]
            if line.endswith("\r"):
                line = line[:-1]
            message = self._feed_line(line)
            if message is not None:
                messages.append(message)
        return messages

    def _feed_line(self, line: str) -> SSEMessage | None:
        if line == "":
            return self._dispatch()
        if line.startswith(":"):
            return None
        name, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if name == "data":
            self._data.append(value)
        elif name == "event":
            self._event = value
        elif name == "id":
            self._id = value
        return None

    def _dispatch(self) -> SSEMessage | None:
        if not self._data:
            self._event = ""
            return None
        message = SSEMessage(
            event=self._event or "message",
            data="\n".join(self._data),
            id=self._id,
        )
        self._data = []
        self._event = ""
        return message


@dataclass
class _Target:
    host: str
    port: int
    path: str
    ssl_context: ssl.SSLContext | None
    authorization: str | None


def _parse_url(url: str) -> _Target:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise HTTPError(f"unsupported URL scheme: {parts.scheme or '(none)'}")
    if not parts.hostname:
        raise HTTPError(f"missing host in URL: {url}")
    secure = parts.scheme == "https"
    authorization = None
    if parts.username is not None:
        raw = f"{parts.username}:{parts.password or ''}"
        token = base64.b64encode(raw.encode()).decode()
        authorization = f"Basic {token}"
    path = parts.path or "/"
    if parts.query:
        path = f"{path}?{parts.query}"
    return _Target(
        host=parts.hostname,
        port=parts.port or (443 if secure else 80),
        path=path,
        ssl_context=ssl.create_default_context() if secure else None,
        authorization=authorization,
    )


async def _connect(
    target: _Target, *, timeout: float
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    try:
        return await asyncio.wait_for(
            asyncio.open_connection(
                target.host,
                target.port,
                ssl=target.ssl_context,
                server_hostname=target.host if target.ssl_context else None,
            ),
            timeout,
        )
    except (OSError, asyncio.TimeoutError) as exc:
        raise HTTPError(
            f"cannot connect to {target.host}:{target.port}: {exc}"
        ) from exc


async def _send_request(
    writer: asyncio.StreamWriter,
    target: _Target,
    *,
    method: str,
    body: bytes | None,
    headers: dict[str, str],
) -> None:
    lines = [
        f"{method} {target.path} HTTP/1.1",
        f"Host: {target.host}:{target.port}",
        "Connection: close",
        "User-Agent: agentabi",
    ]
    if target.authorization:
        lines.append(f"Authorization: {target.authorization}")
    if body is not None:
        lines.append(f"Content-Length: {len(body)}")
    lines.extend(f"{key}: {value}" for key, value in headers.items())
    raw = "\r\n".join(lines).encode("latin-1") + b"\r\n\r\n" + (body or b"")
    writer.write(raw)
    await writer.drain()


async def _read_head(
    reader: asyncio.StreamReader, *, timeout: float
) -> tuple[int, dict[str, str]]:
    try:
        status_line = await asyncio.wait_for(reader.readline(), timeout)
    except asyncio.TimeoutError as exc:
        raise HTTPError("timed out waiting for the HTTP status line") from exc
    if not status_line:
        raise HTTPError("connection closed before the HTTP status line")
    parts = status_line.decode("latin-1").split(" ", 2)
    if len(parts) < 2 or not parts[1].isdigit():
        raise HTTPError(f"malformed HTTP status line: {status_line!r}")
    status = int(parts[1])

    headers: dict[str, str] = {}
    consumed = 0
    while True:
        line = await asyncio.wait_for(reader.readline(), timeout)
        consumed += len(line)
        if consumed > _MAX_HEADER_BYTES:
            raise HTTPError("HTTP response headers are too large")
        if line in (b"\r\n", b"\n", b""):
            break
        key, _, value = line.decode("latin-1").partition(":")
        headers[key.strip().lower()] = value.strip()
    return status, headers


async def _iter_body(
    reader: asyncio.StreamReader, headers: dict[str, str]
) -> AsyncIterator[bytes]:
    """Yield the response body, decoding chunked transfer encoding."""
    if "chunked" in headers.get("transfer-encoding", "").lower():
        async for chunk in _iter_chunked(reader):
            yield chunk
        return
    if "content-length" in headers:
        remaining = int(headers["content-length"] or 0)
        while remaining > 0:
            chunk = await reader.read(min(_READ_CHUNK, remaining))
            if not chunk:
                return
            remaining -= len(chunk)
            yield chunk
        return
    while True:
        chunk = await reader.read(_READ_CHUNK)
        if not chunk:
            return
        yield chunk


async def _iter_chunked(reader: asyncio.StreamReader) -> AsyncIterator[bytes]:
    while True:
        size_line = await reader.readline()
        if not size_line:
            return
        raw_size = size_line.split(b";")[0].strip()
        if not raw_size:
            continue
        try:
            size = int(raw_size, 16)
        except ValueError as exc:
            raise HTTPError(f"malformed chunk size: {raw_size!r}") from exc
        if size == 0:
            while True:
                trailer = await reader.readline()
                if trailer in (b"\r\n", b"\n", b""):
                    return
        remaining = size
        while remaining > 0:
            chunk = await reader.read(remaining)
            if not chunk:
                return
            remaining -= len(chunk)
            yield chunk
        await reader.readline()


async def _open(
    method: str,
    url: str,
    *,
    body: dict[str, Any] | None,
    headers: dict[str, str],
    timeout: float,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter, int, dict[str, str]]:
    target = _parse_url(url)
    reader, writer = await _connect(target, timeout=timeout)
    payload = None
    request_headers = {"Accept": "application/json", **headers}
    if body is not None:
        payload = json.dumps(body).encode()
        request_headers["Content-Type"] = "application/json"
    try:
        await asyncio.wait_for(
            _send_request(
                writer,
                target,
                method=method,
                body=payload,
                headers=request_headers,
            ),
            timeout,
        )
        status, response_headers = await _read_head(reader, timeout=timeout)
    except BaseException:
        writer.close()
        raise
    return reader, writer, status, response_headers


async def request(
    method: str,
    url: str,
    *,
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
) -> tuple[int, str]:
    """Send one request and return ``(status, body_text)``."""
    reader, writer, status, response_headers = await _open(
        method,
        url,
        body=body,
        headers=headers or {},
        timeout=timeout,
    )
    chunks: list[bytes] = []
    try:
        async for chunk in _iter_body(reader, response_headers):
            chunks.append(chunk)
    except asyncio.TimeoutError as exc:
        raise HTTPError(f"timed out reading {url}") from exc
    finally:
        writer.close()
    return status, b"".join(chunks).decode("utf-8", "replace")


async def request_json(
    method: str,
    url: str,
    *,
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
) -> tuple[int, Any]:
    """Send one request and decode a JSON body.

    Returns ``(status, decoded)``; ``decoded`` is ``None`` for an empty body
    or a body that is not JSON. Raises :class:`HTTPError` only on transport
    failures, so callers can inspect the status themselves.
    """
    status, text = await request(
        method, url, body=body, headers=headers, timeout=timeout
    )
    if not text:
        return status, None
    try:
        return status, json.loads(text)
    except json.JSONDecodeError:
        return status, None


class SSEStream:
    """An opened server-sent-events stream.

    ``open()`` performs the connection and header exchange; ``__anext__``
    yields decoded messages. ``close()`` is synchronous so callers can use
    it from a ``finally`` block without awaiting during cancellation.
    """

    def __init__(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        connect_timeout: float = 10.0,
    ) -> None:
        self.url = url
        self._headers = headers or {}
        self._connect_timeout = connect_timeout
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._body: AsyncIterator[bytes] | None = None
        self._parser = SSEParser()
        self._pending: list[SSEMessage] = []
        self._done = False

    async def open(self) -> None:
        """Connect and read the response headers."""
        if self._reader is not None or self._done:
            return
        request_headers = {
            "Accept": "text/event-stream",
            "Cache-Control": "no-cache",
            **self._headers,
        }
        reader, writer, status, response_headers = await _open(
            "GET",
            self.url,
            body=None,
            headers=request_headers,
            timeout=self._connect_timeout,
        )
        if not 200 <= status < 300:
            body = b""
            try:
                async for chunk in _iter_body(reader, response_headers):
                    body += chunk
            finally:
                writer.close()
            raise HTTPError(
                f"SSE request failed with HTTP {status}",
                status=status,
                body=body.decode("utf-8", "replace"),
            )
        self._reader = reader
        self._writer = writer
        self._body = _iter_body(reader, response_headers)

    def __aiter__(self) -> SSEStream:
        return self

    async def __anext__(self) -> SSEMessage:
        while True:
            if self._pending:
                return self._pending.pop(0)
            if self._done:
                raise StopAsyncIteration
            await self.open()
            assert self._body is not None
            try:
                chunk = await self._body.__anext__()
            except StopAsyncIteration:
                self._done = True
                continue
            self._pending.extend(self._parser.feed(chunk.decode("utf-8", "replace")))

    def close(self) -> None:
        """Close the connection. Safe to call more than once."""
        self._done = True
        writer, self._writer = self._writer, None
        self._reader = None
        self._body = None
        if writer is not None:
            writer.close()


async def iter_sse(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    connect_timeout: float = 10.0,
) -> AsyncGenerator[SSEMessage, None]:
    """Open an SSE stream and yield decoded messages until it closes."""
    stream = SSEStream(url, headers=headers, connect_timeout=connect_timeout)
    try:
        await stream.open()
        async for message in stream:
            yield message
    finally:
        stream.close()


__all__ = [
    "HTTPError",
    "SSEMessage",
    "SSEParser",
    "SSEStream",
    "iter_sse",
    "request",
    "request_json",
]
