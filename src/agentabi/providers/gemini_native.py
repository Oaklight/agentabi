"""
agentabi - Gemini CLI Native Provider

Native subprocess + stream-json provider for Gemini CLI.
Runs `gemini -o stream-json --approval-mode <mode> -p <prompt>` and parses JSONL output.
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
from ..types.ir.task import TaskConfig


class GeminiNativeProvider:
    """Native subprocess provider for Gemini CLI.

    Runs `gemini -o stream-json -y -p <prompt>` as a subprocess
    and parses stream-json events into IR events.

    Gemini CLI stream-json format:
    - type: "init"        — session metadata (session_id, model)
    - type: "message"     — user/assistant message (delta streaming)
    - type: "tool_use"    — tool invocation
    - type: "tool_result" — tool output
    - type: "result"      — final stats / usage
    """

    @staticmethod
    def is_available() -> bool:
        """Check if `gemini` CLI is available."""
        return shutil.which("gemini") is not None

    def capabilities(self) -> AgentCapabilities:
        return {
            "name": "Gemini CLI",
            "agent_type": "gemini_cli",
            "supports_streaming": True,
            "supports_mcp": True,
            "supports_session_resume": True,
            "supports_system_prompt": False,
            "supports_tool_filtering": False,
            "supports_permissions": True,
            "supports_multi_turn": True,
            "transport": "subprocess",
        }

    async def stream(self, task: TaskConfig) -> AsyncIterator[IREvent]:
        """Run task via gemini CLI and yield IR events."""
        from .base import collect_subprocess_errors

        cmd = self._build_command(task)
        merged_env = self._build_env(task)

        timeout = task.get("timeout")
        proc = await asyncio.create_subprocess_exec(
            *cmd,
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

    @staticmethod
    def _build_env(task: TaskConfig) -> dict[str, str]:
        """Build merged environment with Gemini-specific variable mapping.

        Maps generic OPENAI_BASE_URL / OPENAI_API_KEY to Gemini-specific
        equivalents when the Gemini-native env vars are not already set.
        """
        task_env = task.get("env") or {}
        merged_env = {**os.environ, **task_env}

        has_gemini_url = task_env.get("GOOGLE_GEMINI_BASE_URL")
        if not has_gemini_url and task_env.get("OPENAI_BASE_URL"):
            # Strip /v1 suffix — Gemini CLI appends paths automatically
            base = task_env["OPENAI_BASE_URL"].rstrip("/")
            if base.endswith("/v1"):
                base = base[:-3]
            merged_env["GOOGLE_GEMINI_BASE_URL"] = base
        if not task_env.get("GEMINI_API_KEY") and task_env.get("OPENAI_API_KEY"):
            merged_env["GEMINI_API_KEY"] = task_env["OPENAI_API_KEY"]

        return merged_env

    async def run(self, task: TaskConfig) -> SessionResult:
        from .base import default_run

        return await default_run(self, task)

    @staticmethod
    def _build_command(task: TaskConfig) -> list[str]:
        """Convert TaskConfig to gemini CLI arguments."""
        cmd = ["gemini", "-o", "stream-json"]

        permissions = task.get("permissions")
        if permissions:
            level = permissions.get("level")
            if level == "full_auto":
                cmd.extend(["--approval-mode", "yolo"])
            elif level == "accept_edits":
                cmd.extend(["--approval-mode", "auto_edit"])
            elif level == "plan":
                cmd.extend(["--approval-mode", "plan"])
            elif level == "default":
                cmd.extend(["--approval-mode", "default"])
            else:
                cmd.extend(["--approval-mode", "yolo"])
        else:
            cmd.extend(["--approval-mode", "yolo"])

        if "model" in task:
            cmd.extend(["-m", task["model"]])

        if task.get("resume"):
            resume_val = task.get("session_id", "latest")
            cmd.extend(["-r", str(resume_val)])

        cmd.extend(["-p", task["prompt"]])
        return cmd

    @staticmethod
    def _parse_event(raw: dict[str, Any]) -> list[IREvent]:
        """Convert a Gemini CLI stream-json event to IR events."""
        event_type = raw.get("type")

        if event_type == "init":
            return GeminiNativeProvider._handle_init(raw)
        elif event_type == "message":
            return GeminiNativeProvider._handle_message(raw)
        elif event_type == "tool_use":
            return GeminiNativeProvider._handle_tool_use(raw)
        elif event_type == "tool_result":
            return GeminiNativeProvider._handle_tool_result(raw)
        elif event_type == "result":
            return GeminiNativeProvider._handle_result(raw)
        return []

    @staticmethod
    def _handle_init(raw: dict[str, Any]) -> list[IREvent]:
        start: SessionStartEvent = {
            "type": "session_start",
            "session_id": raw.get("session_id", ""),
            "agent": "gemini_cli",
        }
        model = raw.get("model")
        if model:
            start["model"] = model
        return [start]

    @staticmethod
    def _handle_message(raw: dict[str, Any]) -> list[IREvent]:
        role = raw.get("role", "")
        if role == "user":
            return []

        content = raw.get("content", "")
        is_delta = raw.get("delta", False)

        if is_delta:
            if content:
                delta: MessageDeltaEvent = {
                    "type": "message_delta",
                    "text": content,
                }
                return [delta]
            return []

        # Non-delta assistant message: full text
        results: list[IREvent] = []
        msg_start: MessageStartEvent = {
            "type": "message_start",
            "role": "assistant",
        }
        results.append(msg_start)

        end: MessageEndEvent = {"type": "message_end"}
        if content:
            end["text"] = content
        results.append(end)
        return results

    @staticmethod
    def _handle_tool_use(raw: dict[str, Any]) -> list[IREvent]:
        tool_event: ToolUseEvent = {
            "type": "tool_use",
            "tool_use_id": raw.get("tool_id", ""),
            "tool_name": raw.get("tool_name", ""),
            "tool_input": raw.get("parameters", {}),
        }
        return [tool_event]

    @staticmethod
    def _handle_tool_result(raw: dict[str, Any]) -> list[IREvent]:
        result: ToolResultEvent = {
            "type": "tool_result",
            "tool_use_id": raw.get("tool_id", ""),
            "content": raw.get("output", ""),
        }
        status = raw.get("status", "")
        if status == "error":
            result["is_error"] = True
            error_info = raw.get("error", {})
            if error_info and isinstance(error_info, dict):
                msg = error_info.get("message", "")
                if msg:
                    result["content"] = msg
        return [result]

    @staticmethod
    def _handle_result(raw: dict[str, Any]) -> list[IREvent]:
        results: list[IREvent] = []
        stats = raw.get("stats", {})

        usage: UsageInfo = {}
        input_tokens = stats.get("input_tokens", 0)
        output_tokens = stats.get("output_tokens", 0)
        total_tokens = stats.get("total_tokens", 0)
        cached = stats.get("cached", 0)

        if input_tokens:
            usage["input_tokens"] = input_tokens
        if output_tokens:
            usage["output_tokens"] = output_tokens
        if total_tokens:
            usage["total_tokens"] = total_tokens
        if cached:
            usage["cache_read_tokens"] = cached

        usage_event: UsageEvent = {"type": "usage", "usage": usage}
        results.append(usage_event)

        # message_end for the final turn
        end: MessageEndEvent = {
            "type": "message_end",
            "stop_reason": "end_turn",
        }
        results.append(end)

        status = raw.get("status", "")
        if status == "error":
            error_msg = raw.get("error", "Unknown error")
            err: ErrorEvent = {
                "type": "error",
                "error": str(error_msg),
                "is_fatal": True,
            }
            results.append(err)

        session_end: SessionEndEvent = {"type": "session_end"}
        results.append(session_end)

        return results


__all__ = ["GeminiNativeProvider"]
