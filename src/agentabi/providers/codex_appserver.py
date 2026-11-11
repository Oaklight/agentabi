"""Stdio JSON-RPC client for ``codex app-server``.

Shared by the ACP backend and the peer runtime.  One client owns one
``codex app-server --listen stdio://`` process: requests carry an id and
await a response, server requests arrive on a handler, and notifications
are dispatched without an id.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from collections import deque
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any

from .._vendor.jsonrpc import INTERNAL_ERROR, JSONRPCError

logger = logging.getLogger("agentabi.providers.codex_appserver")


class AppServerError(Exception):
    """The app-server rejected a request or stopped unexpectedly."""


class AppServerClient:
    """Newline-delimited JSON-RPC client for one app-server process."""

    def __init__(
        self,
        command: list[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self._command = list(command)
        self._cwd = cwd
        self._env = env
        self._proc: asyncio.subprocess.Process | None = None
        self._ids = iter(range(1, 1_000_000))
        self._pending: dict[Any, asyncio.Future[dict[str, Any]]] = {}
        self._request_handler: (
            Callable[[Any, str, dict[str, Any]], Awaitable[dict[str, Any] | None]]
            | None
        ) = None
        self._notification_handler: Callable[[str, dict[str, Any]], None] | None = None
        self._close_handler: Callable[[str], None] | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._stderr_tail: deque[str] = deque(maxlen=20)
        self._closing = False

    @property
    def returncode(self) -> int | None:
        """Exit code, or None while the process runs or never started."""
        return self._proc.returncode if self._proc is not None else None

    @property
    def pid(self) -> int | None:
        """Process id, or None before the process starts."""
        return self._proc.pid if self._proc is not None else None

    def set_handlers(
        self,
        *,
        request: Callable[[Any, str, dict[str, Any]], Awaitable[dict[str, Any] | None]],
        notification: Callable[[str, dict[str, Any]], None],
        closed: Callable[[str], None],
    ) -> None:
        self._request_handler = request
        self._notification_handler = notification
        self._close_handler = closed

    async def start(self) -> None:
        if self._proc is not None:
            return
        binary = self._command[0]
        if shutil.which(binary) is None:
            raise AppServerError(f"executable not found: {binary}")
        env = None
        if self._env is not None:
            env = {**os.environ, **self._env}
        self._proc = await asyncio.create_subprocess_exec(
            *self._command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self._cwd,
            env=env,
        )
        self._reader_task = asyncio.create_task(self._read_loop())
        self._stderr_task = asyncio.create_task(self._drain_stderr())

    async def stop(self) -> None:
        proc = self._proc
        if proc is None:
            return
        self._closing = True
        if proc.stdin is not None:
            with suppress(Exception):
                proc.stdin.close()
        if proc.returncode is None:
            with suppress(ProcessLookupError):
                proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                with suppress(ProcessLookupError):
                    proc.kill()
                await proc.wait()
        for task in (self._reader_task, self._stderr_task):
            if task is not None and not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
        self._fail_pending("app-server stopped")
        self._proc = None

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float = 60.0,
    ) -> dict[str, Any]:
        if self._proc is None or self._closing:
            raise AppServerError("app-server is not running")
        request_id = next(self._ids)
        future: asyncio.Future[dict[str, Any]] = (
            asyncio.get_running_loop().create_future()
        )
        self._pending[request_id] = future
        await self._write(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params or {},
            }
        )
        try:
            if timeout > 0:
                return await asyncio.wait_for(future, timeout)
            return await future
        finally:
            self._pending.pop(request_id, None)

    async def respond(self, server_id: Any, result: dict[str, Any]) -> None:
        """Answer a server request (for example an approval decision)."""
        await self._write({"jsonrpc": "2.0", "id": server_id, "result": result})

    async def _write(self, message: dict[str, Any]) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise AppServerError("app-server is not running")
        raw = json.dumps(message, separators=(",", ":"), ensure_ascii=False)
        try:
            proc.stdin.write((raw + "\n").encode("utf-8"))
            await proc.stdin.drain()
        except (ConnectionError, OSError) as exc:
            raise AppServerError(f"app-server is not running: {exc}") from exc

    async def _read_loop(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stdout is not None
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            try:
                message = json.loads(text)
            except json.JSONDecodeError:
                logger.warning("ignoring malformed app-server line: %s", text[:120])
                continue
            if not isinstance(message, dict):
                continue
            if "method" in message:
                if "id" in message:
                    asyncio.create_task(self._handle_request(message))
                else:
                    self._dispatch_notification(message)
            elif "id" in message:
                self._resolve_response(message)
        reason = self._exit_reason(proc)
        self._fail_pending(reason)
        if not self._closing:
            handler = self._close_handler
            if handler is not None:
                handler(reason)

    def _dispatch_notification(self, message: dict[str, Any]) -> None:
        handler = self._notification_handler
        if handler is None:
            return
        params = message.get("params")
        try:
            handler(
                str(message.get("method")), params if isinstance(params, dict) else {}
            )
        except Exception:  # noqa: BLE001 - a bad notification must not stop the stream
            logger.exception("app-server notification handler failed")

    def _resolve_response(self, message: dict[str, Any]) -> None:
        future = self._pending.pop(message.get("id"), None)
        if future is None or future.done():
            return
        error = message.get("error")
        if isinstance(error, dict):
            future.set_exception(
                AppServerError(str(error.get("message") or "app-server error"))
            )
            return
        result = message.get("result")
        future.set_result(result if isinstance(result, dict) else {})

    async def _handle_request(self, message: dict[str, Any]) -> None:
        handler = self._request_handler
        method = str(message.get("method"))
        params = message.get("params")
        try:
            if handler is None:
                raise AppServerError(f"unsupported request: {method}")
            result = await handler(
                message.get("id"), method, params if isinstance(params, dict) else {}
            )
        except Exception as exc:  # noqa: BLE001 - report to the server, fail closed
            error = JSONRPCError(code=INTERNAL_ERROR, message=str(exc))
            with suppress(AppServerError):
                await self._write(
                    {
                        "jsonrpc": "2.0",
                        "id": message.get("id"),
                        "error": error.to_dict(),
                    }
                )
            return
        if result is None:
            # The handler already answered (for example a cancel).
            return
        with suppress(AppServerError):
            await self.respond(message.get("id"), result)

    async def _drain_stderr(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stderr is not None
        while True:
            line = await proc.stderr.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip()
            if text:
                self._stderr_tail.append(text)

    def _exit_reason(self, proc: asyncio.subprocess.Process) -> str:
        code = proc.returncode
        tail = "\n".join(self._stderr_tail)
        reason = (
            f"app-server exited (code {code})"
            if code is not None
            else ("app-server closed its output")
        )
        return f"{reason}: {tail}" if tail else reason

    def _fail_pending(self, reason: str) -> None:
        pending = list(self._pending.items())
        self._pending.clear()
        for _, future in pending:
            if not future.done():
                future.set_exception(AppServerError(reason))


__all__ = ["AppServerClient", "AppServerError"]
