"""
agentabi - OpenCode Native Provider

Native subprocess + JSONL provider for OpenCode CLI.
No SDK exists for OpenCode, so this is the only provider.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from collections.abc import AsyncIterator
from typing import Any

from ..types.ir.capabilities import AgentCapabilities
from ..types.ir.events import (
    ErrorEvent,
    IREvent,
    MessageDeltaEvent,
    MessageEndEvent,
    MessageStartEvent,
    SessionEndEvent,
    SessionStartEvent,
    ToolResultEvent,
    ToolUseEvent,
    UsageEvent,
    UsageInfo,
)
from ..types.ir.session import SessionResult
from ..types.ir.stop_reasons import stop_reason
from ..types.ir.task import TaskConfig

_THINKING_LEVEL_MAP: dict[str, str] = {
    "low": "minimal",
    "high": "high",
    "max": "max",
}


class OpenCodeNativeProvider:
    """Native subprocess provider for OpenCode CLI.

    Runs `opencode run --format json` as a subprocess and parses
    JSON events into IR events.
    """

    def __init__(self) -> None:
        self._pending_text: list[str] = []
        self._session_id = ""
        self._session_started = False
        self._session_ended = False

    @staticmethod
    def is_available() -> bool:
        """Check if `opencode` CLI is available."""
        return shutil.which("opencode") is not None

    def capabilities(self) -> AgentCapabilities:
        return {
            "name": "OpenCode",
            "agent_type": "opencode",
            "supports_streaming": True,
            "supports_mcp": True,
            "supports_session_resume": True,
            "supports_system_prompt": False,
            "supports_tool_filtering": False,
            "supports_permissions": True,
            "supports_multi_turn": True,
            "supports_thinking": True,
            "supports_structured_output": False,
            "supports_ephemeral": False,
            "supports_additional_dirs": False,
            "supports_file_attachments": True,
            "transport": "subprocess",
        }

    async def stream(self, task: TaskConfig) -> AsyncIterator[IREvent]:
        """Run task via opencode CLI and yield IR events."""
        from .base import collect_subprocess_errors

        self._session_id = ""
        self._session_started = False
        self._session_ended = False

        cmd = self._build_command(task)
        merged_env = {**os.environ, **(task.get("env") or {})}

        timeout = task.get("timeout")
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=merged_env,
            cwd=task.get("working_dir"),
        )

        timed_out = False
        timeout_task: asyncio.Task[None] | None = None
        if timeout:

            async def _kill_after_timeout() -> None:
                nonlocal timed_out
                await asyncio.sleep(timeout)
                if proc.returncode is None:
                    timed_out = True
                    proc.kill()

            timeout_task = asyncio.create_task(_kill_after_timeout())

        try:
            assert proc.stdout is not None
            async for line_bytes in proc.stdout:
                line = line_bytes.decode().rstrip("\n").rstrip("\r")
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    continue
                for event in self._parse_event(raw):
                    yield event

            await proc.wait()
            for err in await collect_subprocess_errors(
                proc, timed_out=timed_out, timeout_seconds=timeout
            ):
                yield err
            for event in self._end_session():
                yield event
        finally:
            if timeout_task and not timeout_task.done():
                timeout_task.cancel()
            if proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()

    async def run(self, task: TaskConfig) -> SessionResult:
        from .base import default_run

        return await default_run(self, task)

    @staticmethod
    def _build_command(task: TaskConfig) -> list[str]:
        """Convert TaskConfig to opencode CLI arguments."""
        cmd = ["opencode", "run", "--format", "json"]

        # OpenCode uses provider/model format.  When env overrides the
        # base URL (via OPENAI_BASE_URL), prefix the model with "openai/"
        # so OpenCode routes through the overridden OpenAI-compatible
        # provider instead of a config-file provider.
        task_env = task.get("env") or {}
        model = task.get("model", "")
        if model and task_env.get("OPENAI_BASE_URL") and "/" not in model:
            model = f"openai/{model}"
        if model:
            cmd.extend(["--model", model])

        # Note: OpenCode CLI does not support system prompts via CLI flags.
        # system_prompt in TaskConfig is ignored for this provider.

        if "working_dir" in task:
            cmd.extend(["--dir", task["working_dir"]])

        if task.get("resume") and "session_id" in task:
            cmd.extend(["--session", task["session_id"]])
        elif task.get("resume"):
            cmd.append("--continue")

        permissions = task.get("permissions")
        if permissions:
            level = permissions.get("level")
            if level == "full_auto":
                cmd.append("--auto")

        variant = _THINKING_LEVEL_MAP.get(task.get("thinking_level", ""))
        if variant:
            cmd.extend(["--variant", variant])

        for f in task.get("files") or []:
            cmd.extend(["--file", f])

        cmd.append("--")
        cmd.append(task["prompt"])
        return cmd

    def _parse_event(self, raw: dict[str, Any]) -> list[IREvent]:
        """Convert an OpenCode JSON event to IR events.

        OpenCode JSON format (--format json):
        - type: "step_start" — a new LLM turn begins
        - type: "text" — text output from the agent
        - type: "tool_use" — tool call with result
        - type: "step_finish" — turn ends, includes token usage
        """
        event_type = raw.get("type")
        part = raw.get("part", {})
        session_id = raw.get("sessionID", "")

        if event_type == "step_start":
            return self._start_session(session_id) + self._handle_step_start(
                part, session_id
            )
        elif event_type == "text":
            return self._handle_text(part)
        elif event_type == "tool_use":
            return self._handle_tool_use(part)
        elif event_type == "step_finish":
            return self._handle_step_finish(part, session_id)
        elif event_type == "error":
            return self._handle_error(raw)
        return []

    @staticmethod
    def _handle_error(raw: dict[str, Any]) -> list[IREvent]:
        """Convert a top-level OpenCode error event."""
        error = raw.get("error") or {}
        message = ""
        if isinstance(error, dict):
            data = error.get("data")
            if isinstance(data, dict) and data.get("message"):
                message = data["message"]
            else:
                message = error.get("message") or error.get("name") or ""
        elif error:
            message = str(error)
        err: ErrorEvent = {
            "type": "error",
            "error": message or "OpenCode error",
            "is_fatal": True,
        }
        return [err]

    @staticmethod
    def _handle_step_start(part: dict[str, Any], session_id: str) -> list[IREvent]:
        results: list[IREvent] = []

        msg_start: MessageStartEvent = {
            "type": "message_start",
            "role": "assistant",
        }
        message_id = part.get("messageID", "")
        if message_id:
            msg_start["message_id"] = message_id
        results.append(msg_start)
        return results

    def _start_session(self, session_id: str) -> list[IREvent]:
        """Emit ``session_start`` once per run.

        opencode sends one ``step_start`` per model step, so a tool loop
        produces several. The session begins at the first one.

        Returns:
            One ``session_start`` the first time, then nothing.
        """
        if self._session_started:
            return []
        self._session_started = True
        self._session_id = session_id
        start: SessionStartEvent = {
            "type": "session_start",
            "session_id": session_id,
            "agent": "opencode",
        }
        return [start]

    def _end_session(self) -> list[IREvent]:
        """Emit the closing ``session_end`` exactly once.

        The CLI signals a clean ending with ``reason: "stop"``, but it can
        also exit after a failed step or an interrupted stream. The caller
        emits the missing event at end of stream so a consumer waiting for
        ``session_end`` is not left hanging.

        Returns:
            One ``session_end`` when none was emitted, else nothing.
        """
        if not self._session_started or self._session_ended:
            return []
        self._session_ended = True
        end: SessionEndEvent = {"type": "session_end"}
        if self._session_id:
            end["session_id"] = self._session_id
        return [end]

    def _handle_text(self, part: dict[str, Any]) -> list[IREvent]:
        text = part.get("text", "")
        if text:
            self._pending_text.append(text)
            delta: MessageDeltaEvent = {"type": "message_delta", "text": text}
            return [delta]
        return []

    @staticmethod
    def _handle_tool_use(part: dict[str, Any]) -> list[IREvent]:
        results: list[IREvent] = []
        state = part.get("state", {})
        tool_name = part.get("tool", "")
        call_id = part.get("callID", "")

        tool_event: ToolUseEvent = {
            "type": "tool_use",
            "tool_use_id": call_id,
            "tool_name": tool_name,
            "tool_input": state.get("input", {}),
        }
        results.append(tool_event)

        status = state.get("status", "")
        if status == "completed":
            output = state.get("output", "")
            tool_result: ToolResultEvent = {
                "type": "tool_result",
                "tool_use_id": call_id,
                "content": str(output) if output else "",
            }
            time_info = state.get("time", {})
            start_ms = time_info.get("start")
            end_ms = time_info.get("end")
            if start_ms is not None and end_ms is not None:
                tool_result["duration_ms"] = end_ms - start_ms
            results.append(tool_result)
        elif status == "error":
            output = state.get("output", "Error")
            tool_result_err: ToolResultEvent = {
                "type": "tool_result",
                "tool_use_id": call_id,
                "content": str(output),
                "is_error": True,
            }
            results.append(tool_result_err)

        return results

    def _handle_step_finish(
        self, part: dict[str, Any], session_id: str
    ) -> list[IREvent]:
        results: list[IREvent] = []

        tokens = part.get("tokens", {})
        usage: UsageInfo = {}
        if tokens.get("input"):
            usage["input_tokens"] = tokens["input"]
        if tokens.get("output"):
            usage["output_tokens"] = tokens["output"]
        total = tokens.get("total", 0)
        if total:
            usage["total_tokens"] = total
        cache = tokens.get("cache", {})
        if cache.get("read"):
            usage["cache_read_tokens"] = cache["read"]
        if cache.get("write"):
            usage["cache_creation_tokens"] = cache["write"]

        usage_event: UsageEvent = {"type": "usage", "usage": usage}
        cost = part.get("cost")
        if cost:
            usage_event["cost_usd"] = cost
        results.append(usage_event)

        reason = part.get("reason", "")
        end: MessageEndEvent = {
            "type": "message_end",
            "stop_reason": stop_reason(reason, default="end_turn"),
        }
        if self._pending_text:
            end["text"] = "".join(self._pending_text)
            self._pending_text = []
        message_id = part.get("messageID", "")
        if message_id:
            end["message_id"] = message_id
        results.append(end)

        if reason == "stop":
            self._session_ended = True
            session_end: SessionEndEvent = {"type": "session_end"}
            if session_id:
                session_end["session_id"] = session_id
            results.append(session_end)

        return results


__all__ = ["OpenCodeNativeProvider"]
