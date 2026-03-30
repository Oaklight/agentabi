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
from typing import Any, AsyncIterator, Dict, List

from ..types.ir.capabilities import AgentCapabilities
from ..types.ir.events import (
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


class OpenCodeNativeProvider:
    """Native subprocess provider for OpenCode CLI.

    Runs `opencode run --format json` as a subprocess and parses
    JSON events into IR events.
    """

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
            "supports_system_prompt": True,
            "supports_tool_filtering": False,
            "supports_permissions": False,
            "supports_multi_turn": False,
            "transport": "subprocess",
        }

    async def stream(self, task: TaskConfig) -> AsyncIterator[IREvent]:
        """Run task via opencode CLI and yield IR events."""
        cmd = self._build_command(task)
        merged_env = {**os.environ, **(task.get("env") or {})}

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=merged_env,
            cwd=task.get("working_dir"),
        )

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
        finally:
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
    def _build_command(task: TaskConfig) -> List[str]:
        """Convert TaskConfig to opencode CLI arguments."""
        cmd = ["opencode", "run", "--format", "json"]

        if "model" in task:
            cmd.extend(["--model", task["model"]])

        if "system_prompt" in task:
            cmd.extend(["--prompt", task["system_prompt"]])

        if "working_dir" in task:
            cmd.extend(["--dir", task["working_dir"]])

        if task.get("resume") and "session_id" in task:
            cmd.extend(["--session", task["session_id"]])

        cmd.append("--")
        cmd.append(task["prompt"])
        return cmd

    @staticmethod
    def _parse_event(raw: Dict[str, Any]) -> List[IREvent]:
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
            return OpenCodeNativeProvider._handle_step_start(part, session_id)
        elif event_type == "text":
            return OpenCodeNativeProvider._handle_text(part)
        elif event_type == "tool_use":
            return OpenCodeNativeProvider._handle_tool_use(part)
        elif event_type == "step_finish":
            return OpenCodeNativeProvider._handle_step_finish(part, session_id)
        return []

    @staticmethod
    def _handle_step_start(part: Dict[str, Any], session_id: str) -> List[IREvent]:
        results: List[IREvent] = []
        start: SessionStartEvent = {
            "type": "session_start",
            "session_id": session_id,
            "agent": "opencode",
        }
        results.append(start)

        msg_start: MessageStartEvent = {
            "type": "message_start",
            "role": "assistant",
        }
        message_id = part.get("messageID", "")
        if message_id:
            msg_start["message_id"] = message_id
        results.append(msg_start)
        return results

    @staticmethod
    def _handle_text(part: Dict[str, Any]) -> List[IREvent]:
        text = part.get("text", "")
        if text:
            delta: MessageDeltaEvent = {"type": "message_delta", "text": text}
            return [delta]
        return []

    @staticmethod
    def _handle_tool_use(part: Dict[str, Any]) -> List[IREvent]:
        results: List[IREvent] = []
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

    @staticmethod
    def _handle_step_finish(part: Dict[str, Any], session_id: str) -> List[IREvent]:
        results: List[IREvent] = []

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
            "stop_reason": reason,
        }
        message_id = part.get("messageID", "")
        if message_id:
            end["message_id"] = message_id
        results.append(end)

        if reason == "stop":
            session_end: SessionEndEvent = {"type": "session_end"}
            if session_id:
                session_end["session_id"] = session_id
            results.append(session_end)

        return results


__all__ = ["OpenCodeNativeProvider"]
