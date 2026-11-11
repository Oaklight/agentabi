"""OpenCode peer: one ``opencode serve`` process and one V2 session.

A turn starts with ``POST /api/session/{id}/prompt`` using
``delivery: "steer"``; the server acknowledges the admitted message with
its id and sequence.  A second prompt during the turn steers it.  The
global event stream closes after a steer in 1.19.0, so completion is
detected by polling the session state and its messages instead.

Endpoint settings cannot reach the V2 catalog, so this backend refuses
``settings`` fields that would change the endpoint.  Authentication can
be supplied through ``OPENCODE_AUTH_CONTENT`` in ``env``; the user's own
CLI configuration is never modified.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from ..providers._http_sse import HTTPError, SSEStream, request_json
from ..providers.opencode_v2 import _decode, _free_port
from .base import PeerInfo, PeerNote, PeerStatus, PeerTurn, SendResult
from .errors import PeerClosed, PeerError, PeerExited, PeerTimeout

_DEFAULT_COMMAND = ["opencode"]
_SERVER_ARGS = ["serve"]
_ENDPOINT_FIELDS = (
    "base_url",
    "api_key",
    "api_key_env",
    "model_provider",
    "headers",
    "native",
)
_APPROVING_MODES = {"bypassPermissions", "full_auto", "acceptEdits"}
_POLL_INTERVAL = 0.5
_HEALTH_TIMEOUT = 30.0


@dataclass
class _Turn:
    """Bookkeeping for one in-flight turn."""

    turn_id: str
    started_at: float
    since_ms: int
    future: asyncio.Future[PeerTurn] | None = None
    message_id: str = ""
    interrupted: bool = False
    interrupted_at: float = 0.0
    answered: set[str] = field(default_factory=set)


class OpenCodePeer:
    """An OpenCode V2 session that accepts one message at a time."""

    engine = "process"
    backend = "opencode"

    def __init__(
        self,
        *,
        name: str,
        cwd: str,
        model: str | None = None,
        permission_mode: str = "bypassPermissions",
        settings: dict[str, Any] | None = None,
        env: dict[str, str] | None = None,
        command: list[str] | None = None,
        base_url: str | None = None,
        session_id: str | None = None,
        resume: bool = False,
        extra_args: list[str] | None = None,
        system_prompt: str | None = None,
        add_dirs: list[str] | None = None,
        agents: dict[str, Any] | str | None = None,
    ) -> None:
        """Describe a peer; call :meth:`start` to open the session.

        Args:
            name: Caller-facing name.
            cwd: Working directory for the server and the session.
            model: ``provider/model`` label the V2 session should use.
            permission_mode: Approving modes answer a permission request
                with ``once``; other modes reject it and record a note.
            settings: Canonical agentabi settings.  Endpoint fields are
                refused because the V2 catalog cannot see them.
            env: Extra server environment.  ``OPENCODE_AUTH_CONTENT``
                supplies credentials without touching the CLI config.
            command: Override the CLI argv prefix (used by tests).
            base_url: Attach to an already running server.
            session_id: Session id to resume (recover).
            resume: Continue ``session_id`` instead of creating it.
            extra_args: Extra CLI arguments before ``serve``.
            system_prompt: Not supported by this backend.
            add_dirs: Not supported by this backend.
            agents: Not supported by this backend.
        """
        if system_prompt:
            raise PeerError("system prompts are not supported by the OpenCode peer")
        if add_dirs:
            raise PeerError("add_dirs is not supported by the OpenCode peer")
        if agents:
            raise PeerError("agent definitions are not supported by the OpenCode peer")
        for field_name in _ENDPOINT_FIELDS:
            if settings and settings.get(field_name):
                raise PeerError(
                    f"settings.{field_name} cannot reach the OpenCode V2 catalog; "
                    "configure the provider in opencode or pass "
                    "OPENCODE_AUTH_CONTENT in env"
                )
        if model and "/" not in model:
            raise PeerError(f"OpenCode model must be provider/model, got {model!r}")
        self.name = name
        self._cwd = cwd
        self._model = model
        self._permission_mode = permission_mode
        self._settings = settings
        self._env = dict(env or {})
        self._command = list(command or _DEFAULT_COMMAND)
        self._base_url = base_url.rstrip("/") if base_url else None
        self._session_id = session_id
        self._resume = resume
        self._extra_args = list(extra_args or [])

        self._peer_id = f"{name}-{uuid.uuid4().hex[:8]}"
        self._proc: asyncio.subprocess.Process | None = None
        self._owns_server = base_url is None
        self._status: PeerStatus = "starting"
        self._started_at = time.time()
        self._closed = False
        self._turn: _Turn | None = None
        self._last_turn: PeerTurn | None = None
        self._history: list[PeerTurn] = []
        self._inbox: list[PeerNote] = []
        self._poller: asyncio.Task[None] | None = None
        self._stream_task: asyncio.Task[None] | None = None
        self._stream_ready = asyncio.Event()
        self._stream_error: dict[str, Any] | None = None
        self._lock = asyncio.Lock()

    # -- lifecycle --------------------------------------------------------

    async def start(self) -> PeerInfo:
        """Start the server when needed, then open or resume the session."""
        if self._session_id and self._status == "idle":
            return self.info()
        if not os.path.isdir(self._cwd):
            raise PeerError(f"cwd is not a directory: {self._cwd}")
        if self._base_url is None:
            await self._start_server()
        try:
            await self._wait_healthy()
            await self._open_session()
            self._stream_task = asyncio.create_task(self._keep_stream())
            await self._wait_stream_ready()
        except Exception:
            await self._stop_stream()
            await self._stop_server()
            raise
        self._status = "idle"
        return self.info()

    async def _start_server(self) -> None:
        port = _free_port()
        self._base_url = f"http://127.0.0.1:{port}"
        argv = list(self._command) + self._extra_args + _SERVER_ARGS
        argv += ["--port", str(port)]
        env = {**os.environ, **self._env}
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                env=env,
                cwd=self._cwd,
            )
        except OSError as exc:
            raise PeerError(f"cannot start opencode: {exc}") from exc

    async def _wait_healthy(self) -> None:
        base = self._base_url
        assert base is not None
        deadline = time.monotonic() + _HEALTH_TIMEOUT
        while time.monotonic() < deadline:
            if self._proc is not None and self._proc.returncode is not None:
                raise PeerError("opencode exited before it became healthy")
            try:
                status, _ = await request_json(
                    "GET", f"{base}/global/health", timeout=1
                )
                if status == 200:
                    return
            except Exception:  # noqa: BLE001 - not up yet
                pass
            await asyncio.sleep(0.1)
        raise PeerError(f"opencode server did not become healthy at {base}")

    async def _open_session(self) -> None:
        base = self._base_url
        assert base is not None
        if self._resume:
            if not self._session_id:
                raise PeerError(f"peer {self.name} has no session id to resume")
            status, _ = await request_json(
                "GET", f"{base}/api/session/{self._session_id}", timeout=10
            )
            if status != 200:
                raise PeerError(
                    f"OpenCode session not found: {self._session_id} (HTTP {status})"
                )
        else:
            body: dict[str, Any] = {}
            if self._model:
                provider, model = self._model.split("/", 1)
                body["model"] = {"providerID": provider, "id": model}
            status, payload = await request_json(
                "POST",
                f"{base}/session?directory={self._cwd}",
                body=body,
                timeout=20,
            )
            session_id = _session_id(payload)
            if status != 200 or not session_id:
                raise PeerError(
                    f"cannot create an OpenCode session (HTTP {status}): {payload!r}"
                )
            self._session_id = session_id
        if self._model:
            provider, model = self._model.split("/", 1)
            status, payload = await request_json(
                "POST",
                f"{base}/api/session/{self._session_id}/model",
                body={"model": {"providerID": provider, "id": model}},
                timeout=10,
            )
            if status not in (200, 204):
                raise PeerError(
                    f"cannot set the OpenCode model (HTTP {status}): {payload!r}"
                )

    def info(self) -> PeerInfo:
        """Return the current peer description."""
        return PeerInfo(
            name=self.name,
            peer_id=self._peer_id,
            cwd=self._cwd,
            status=self._status,
            engine=self.engine,
            backend=self.backend,
            session_id=self._session_id,
            model=self._model,
            pid=self._proc.pid if self._proc is not None else None,
            turn_id=self._turn.turn_id if self._turn is not None else None,
            started_at=self._started_at,
        )

    async def close(self) -> None:
        """Stop polling and stop the server when this peer started it."""
        if self._closed:
            return
        self._closed = True
        self._status = "exited"
        poller, self._poller = self._poller, None
        if poller is not None:
            poller.cancel()
            try:
                await poller
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._finish_turn(None, cancelled=True)
        await self._stop_stream()
        await self._stop_server()

    async def _keep_stream(self) -> None:
        """Keep an ``/api/event`` subscriber alive.

        OpenCode 1.19.0 does not start a turn for a prompt sent while the
        server has no event subscriber, and it closes the stream after a
        steer.  Reconnect in a loop so the next prompt has a subscriber.
        """
        base = self._base_url
        while base is not None and not self._closed:
            stream = SSEStream(f"{base}/api/event")
            try:
                await stream.open()
                async for message in stream:
                    payload = _decode(message)
                    if (
                        payload is not None
                        and payload.get("type") == "server.connected"
                    ):
                        self._stream_ready.set()
            except (StopAsyncIteration, HTTPError, OSError):
                pass
            finally:
                stream.close()
                self._stream_ready.clear()
            if not self._closed:
                await asyncio.sleep(0.5)

    def _on_stream_event(self, payload: dict[str, Any]) -> None:
        """Record a failure the polling view cannot see."""
        kind = payload.get("type")
        data = payload.get("data") or {}
        if not isinstance(data, dict):
            return
        session = data.get("sessionID")
        if session and session != self._session_id:
            return
        if kind == "session.next.step.failed":
            self._stream_error = data.get("error") or {"message": "turn failed"}
        elif kind == "session.error":
            self._stream_error = data

    async def _wait_stream_ready(self) -> None:
        try:
            await asyncio.wait_for(self._stream_ready.wait(), timeout=15)
        except asyncio.TimeoutError:
            raise PeerError("OpenCode event stream did not connect") from None

    async def _stop_stream(self) -> None:
        task, self._stream_task = self._stream_task, None
        self._stream_ready.clear()
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    async def _stop_server(self) -> None:
        if not self._owns_server:
            return
        proc, self._proc = self._proc, None
        if proc is None:
            return
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()

    # -- messaging --------------------------------------------------------

    async def send(self, text: str) -> SendResult:
        """Start a turn when idle, or steer the running turn."""
        if not text:
            raise PeerError("message text is empty")
        async with self._lock:
            self._ensure_running()
            turn = self._turn
            mode = "steer"
            if turn is None:
                now = time.time()
                turn = _Turn(
                    turn_id=f"{self.name}:{len(self._history) + 1}",
                    started_at=now,
                    since_ms=int(now * 1000),
                )
                turn.future = asyncio.get_running_loop().create_future()
                self._turn = turn
                self._status = "running"
                mode = "turn"
            await self._wait_stream_ready()
            self._stream_error = None
            admitted = await self._post_prompt(text)
            if not turn.message_id:
                turn.message_id = admitted
            if mode == "turn":
                self._poller = asyncio.create_task(self._poll_turn(turn))
            return SendResult(accepted=True, mode=mode, turn_id=turn.turn_id)  # type: ignore[arg-type]

    async def _post_prompt(self, text: str) -> str:
        base = self._base_url
        assert base is not None
        try:
            status, payload = await request_json(
                "POST",
                f"{base}/api/session/{self._session_id}/prompt",
                body={"prompt": {"text": text}, "delivery": "steer"},
                timeout=30,
            )
        except Exception as exc:  # noqa: BLE001 - transport failure
            raise PeerError(f"cannot send the prompt: {exc}") from exc
        if status not in (200, 201, 202):
            raise PeerError(f"cannot send the prompt (HTTP {status}): {payload!r}")
        data = payload.get("data") if isinstance(payload, dict) else None
        return str(data.get("id") or "") if isinstance(data, dict) else ""

    async def steer(self, text: str, *, interrupt: bool = False) -> SendResult:
        """Send a message into the running turn."""
        if self._turn is None:
            raise PeerError(f"peer {self.name} has no running turn")
        if interrupt:
            await self.interrupt()
        return await self.send(text)

    async def interrupt(self) -> None:
        """Cancel the running turn; the session stays usable."""
        turn = self._turn
        base = self._base_url
        if turn is None or base is None:
            return
        turn.interrupted = True
        turn.interrupted_at = time.monotonic()
        await request_json(
            "POST", f"{base}/api/session/{self._session_id}/interrupt", body={}
        )

    async def wait(self, timeout: float | None = None) -> PeerTurn:
        """Wait for the running turn, or return the last completed one."""
        turn = self._turn
        if turn is None:
            if self._last_turn is not None:
                return self._last_turn
            raise PeerError(f"peer {self.name} has no turn to wait for")
        if turn.future is None:  # pragma: no cover - always set on send
            raise PeerError(f"peer {self.name} has no turn future")
        try:
            return await asyncio.wait_for(asyncio.shield(turn.future), timeout)
        except asyncio.TimeoutError:
            raise PeerTimeout(
                f"peer {self.name} turn {turn.turn_id} did not finish in {timeout}s"
            ) from None

    def inbox(self, *, clear: bool = False) -> list[PeerNote]:
        """Return notes collected since the last read."""
        notes = list(self._inbox)
        if clear:
            self._inbox.clear()
        return notes

    # -- internals --------------------------------------------------------

    def _ensure_running(self) -> None:
        if self._closed:
            raise PeerClosed(f"peer {self.name} is closed")
        if self._status == "exited":
            code = self._proc.returncode if self._proc is not None else None
            raise PeerExited(self.name, code)
        if self._base_url is None or self._session_id is None:
            raise PeerError(f"peer {self.name} is not running")

    async def _poll_turn(self, turn: _Turn) -> None:
        base = self._base_url
        assert base is not None
        idle = 0
        stable = 0
        last_sig: tuple[Any, ...] | None = None
        while self._turn is turn and not self._closed:
            await asyncio.sleep(_POLL_INTERVAL)
            if self._proc is not None and self._proc.returncode is not None:
                self._status = "exited"
                self._inbox.append(
                    PeerNote(
                        kind="exit",
                        text=f"opencode exited (code {self._proc.returncode})",
                        at=time.time(),
                    )
                )
                self._finish_turn(None, cancelled=True)
                return
            try:
                await self._answer_permissions(base)
                active = await self._active(base)
                messages = await self._messages(base)
            except Exception:  # noqa: BLE001 - a failed poll is retried
                continue
            assistants = [
                message
                for message in messages
                if (message.get("time") or {}).get("created", 0) >= turn.since_ms
                and (message.get("time") or {}).get("completed")
            ]
            signature = tuple(message.get("id") for message in assistants)
            idle = 0 if self._session_id in active else idle + 1
            stable = stable + 1 if signature == last_sig else 0
            last_sig = signature
            interrupted_ready = (
                turn.interrupted and time.monotonic() - turn.interrupted_at >= 1.0
            )
            if assistants and idle >= 2 and stable >= 2:
                self._finish_from_messages(turn, assistants)
                return
            if self._stream_error is not None and idle >= 2 and stable >= 2:
                self._finish_turn(None, cancelled=False, error=self._stream_error)
                return
            if interrupted_ready and idle >= 2 and stable >= 2:
                self._finish_turn(None, cancelled=True)
                return

    async def _active(self, base: str) -> dict[str, Any]:
        status, payload = await request_json(
            "GET", f"{base}/api/session/active", timeout=10
        )
        if status != 200 or not isinstance(payload, dict):
            return {}
        data = payload.get("data")
        return data if isinstance(data, dict) else {}

    async def _messages(self, base: str) -> list[dict[str, Any]]:
        status, payload = await request_json(
            "GET", f"{base}/api/session/{self._session_id}/message", timeout=10
        )
        if status != 200 or not isinstance(payload, dict):
            return []
        data = payload.get("data")
        return (
            [item for item in data if isinstance(item, dict)]
            if isinstance(data, list)
            else []
        )

    async def _answer_permissions(self, base: str) -> None:
        """Answer pending permission asks without an event stream."""
        session = self._session_id
        try:
            status, payload = await request_json(
                "GET", f"{base}/api/session/{session}/permission", timeout=10
            )
        except Exception:  # noqa: BLE001 - endpoint may be absent
            return
        if status != 200 or not isinstance(payload, dict):
            return
        data = payload.get("data")
        requests = data if isinstance(data, list) else []
        for request in requests:
            if not isinstance(request, dict):
                continue
            request_id = str(request.get("id") or "")
            if not request_id:
                continue
            turn = self._turn
            if turn is not None and request_id in turn.answered:
                continue
            if turn is not None:
                turn.answered.add(request_id)
            approve = self._permission_mode in _APPROVING_MODES
            reply = "once" if approve else "reject"
            await request_json(
                "POST",
                f"{base}/api/session/{session}/permission/{request_id}/reply",
                body={"reply": reply},
                timeout=10,
            )
            if not approve:
                self._inbox.append(
                    PeerNote(
                        kind="permission_denied",
                        text=f"denied permission {request_id}",
                        at=time.time(),
                    )
                )

    def _finish_from_messages(
        self, turn: _Turn, messages: list[dict[str, Any]]
    ) -> None:
        text = "".join(_message_text(message) for message in messages)
        last = messages[-1]
        error = last.get("error")
        if error:
            self._finish_turn(None, cancelled=False, text=text, error=error)
        else:
            self._finish_turn(
                None, cancelled=False, text=text, tokens=last.get("tokens")
            )

    def _finish_turn(
        self,
        completed: dict[str, Any] | None,
        *,
        cancelled: bool,
        text: str = "",
        tokens: Any = None,
        error: Any = None,
    ) -> None:
        turn = self._turn
        if turn is None or turn.future is None:
            return
        self._turn = None
        self._status = "exited" if self._closed else "idle"
        usage = _usage(tokens)
        self._stream_error = None
        if cancelled:
            status: Any = "cancelled"
            stop_reason = "interrupted" if turn.interrupted else "cancelled"
            errors: tuple[str, ...] = ()
        elif error:
            status = "error"
            stop_reason = "error"
            errors = (
                (str(error.get("message") or error),)
                if isinstance(error, dict)
                else (str(error),)
            )
        else:
            status = "success"
            stop_reason = "completed"
            errors = ()
        result = PeerTurn(
            turn_id=turn.turn_id,
            status=status,
            text=text,
            usage=usage,
            num_turns=1,
            stop_reason=stop_reason,
            started_at=turn.started_at,
            ended_at=time.time(),
            errors=errors,
        )
        self._history.append(result)
        self._last_turn = result
        self._inbox.append(PeerNote(kind="turn", turn=result, at=time.time()))
        if not turn.future.done():
            turn.future.set_result(result)


def _session_id(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    candidate = payload.get("id")
    if isinstance(candidate, str) and candidate:
        return candidate
    data = payload.get("data")
    if isinstance(data, dict):
        candidate = data.get("id")
        if isinstance(candidate, str):
            return candidate
    return ""


def _message_text(message: dict[str, Any]) -> str:
    parts = message.get("parts")
    if parts is None:
        parts = message.get("content")
    if not isinstance(parts, list):
        return ""
    if not parts:
        text = message.get("text")
        return str(text) if isinstance(text, str) else ""
    return "".join(
        str(part.get("text") or "")
        for part in parts
        if isinstance(part, dict) and part.get("type") == "text"
    )


def _usage(tokens: Any) -> dict[str, int]:
    if not isinstance(tokens, dict):
        return {}
    cache = tokens.get("cache")
    cached = cache.get("read") if isinstance(cache, dict) else 0
    values = {
        "input_tokens": tokens.get("input"),
        "output_tokens": tokens.get("output"),
        "cache_read_tokens": cached,
    }
    usage = {key: value for key, value in values.items() if isinstance(value, int)}
    if isinstance(tokens.get("input"), int) and isinstance(tokens.get("output"), int):
        usage["total_tokens"] = int(tokens["input"]) + int(tokens["output"])
    return usage


__all__ = ["OpenCodePeer"]
