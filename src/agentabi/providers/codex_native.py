"""
agentabi - Codex CLI Native Provider

Native subprocess provider for Codex CLI.
Runs `codex exec --json --full-auto <prompt>` and parses JSONL output.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from collections.abc import AsyncIterator
from typing import Any, Literal, cast

from ..types.ir.capabilities import AgentCapabilities
from ..types.ir.events import (
    ErrorEvent,
    FileDiffEvent,
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


class CodexNativeProvider:
    """Native subprocess provider for Codex CLI.

    Runs `codex exec --json --full-auto <prompt>` as a subprocess
    and parses JSONL events into IR events.

    Codex CLI JSONL event types:
    - thread.started  — session metadata (thread_id)
    - turn.started    — new assistant turn begins
    - item.started    — tool/message item begins (command_execution, mcp, etc.)
    - item.completed  — tool/message item completed (with output)
    - turn.completed  — turn ends with usage stats
    """

    @staticmethod
    def is_available() -> bool:
        """Check if `codex` CLI is available."""
        return shutil.which("codex") is not None

    def capabilities(self) -> AgentCapabilities:
        return {
            "name": "Codex",
            "agent_type": "codex",
            "supports_streaming": True,
            "supports_mcp": True,
            "supports_session_resume": True,
            "supports_system_prompt": True,
            "supports_tool_filtering": False,
            "supports_permissions": True,
            "supports_multi_turn": True,
            "transport": "subprocess",
        }

    async def stream(self, task: TaskConfig) -> AsyncIterator[IREvent]:
        """Run task via codex CLI and yield IR events."""
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
    def _build_command(task: TaskConfig) -> list[str]:
        """Convert TaskConfig to codex CLI arguments."""
        cmd = ["codex", "exec", "--json", "--full-auto"]

        if "model" in task:
            cmd.extend(["-m", task["model"]])

        if task.get("working_dir"):
            cmd.extend(["-C", task["working_dir"]])

        permissions = task.get("permissions")
        if permissions:
            level = permissions.get("level")
            if level == "full_auto":
                cmd.append("--dangerously-bypass-approvals-and-sandbox")

        cmd.append(task["prompt"])
        return cmd

    @staticmethod
    def _parse_event(raw: dict[str, Any]) -> list[IREvent]:
        """Convert a Codex CLI JSONL event to IR events."""
        event_type = raw.get("type")

        if event_type == "thread.started":
            return CodexNativeProvider._handle_thread_started(raw)
        elif event_type == "turn.started":
            return CodexNativeProvider._handle_turn_started()
        elif event_type == "item.started":
            return CodexNativeProvider._handle_item(raw, started=True)
        elif event_type == "item.completed":
            return CodexNativeProvider._handle_item(raw, started=False)
        elif event_type == "turn.completed":
            return CodexNativeProvider._handle_turn_completed(raw)
        return []

    @staticmethod
    def _handle_thread_started(raw: dict[str, Any]) -> list[IREvent]:
        start: SessionStartEvent = {
            "type": "session_start",
            "session_id": raw.get("thread_id", ""),
            "agent": "codex",
        }
        return [start]

    @staticmethod
    def _handle_turn_started() -> list[IREvent]:
        msg_start: MessageStartEvent = {
            "type": "message_start",
            "role": "assistant",
        }
        return [msg_start]

    @staticmethod
    def _handle_turn_completed(raw: dict[str, Any]) -> list[IREvent]:
        results: list[IREvent] = []

        usage_raw = raw.get("usage", {})
        usage: UsageInfo = {}
        input_tokens = usage_raw.get("input_tokens", 0)
        output_tokens = usage_raw.get("output_tokens", 0)
        cached = usage_raw.get("cached_input_tokens", 0)

        if input_tokens:
            usage["input_tokens"] = input_tokens
        if output_tokens:
            usage["output_tokens"] = output_tokens
        if cached:
            usage["cache_read_tokens"] = cached
        total = input_tokens + output_tokens
        if total:
            usage["total_tokens"] = total

        usage_event: UsageEvent = {"type": "usage", "usage": usage}
        results.append(usage_event)

        end: MessageEndEvent = {"type": "message_end", "stop_reason": "end_turn"}
        results.append(end)

        session_end: SessionEndEvent = {"type": "session_end"}
        results.append(session_end)

        return results

    @staticmethod
    def _handle_item(raw: dict[str, Any], *, started: bool) -> list[IREvent]:
        """Convert an item.started or item.completed event to IR events."""
        item = raw.get("item", {})
        item_type = item.get("type", "")

        if item_type == "agent_message":
            return CodexNativeProvider._handle_agent_message(item, started=started)
        elif item_type == "command_execution":
            return CodexNativeProvider._handle_command(item, started=started)
        elif item_type == "file_change":
            return CodexNativeProvider._handle_file_change(item, started=started)
        elif item_type == "mcp_tool_call":
            return CodexNativeProvider._handle_mcp_tool(item, started=started)
        elif item_type == "error":
            if not started:
                err: ErrorEvent = {
                    "type": "error",
                    "error": item.get("message", "Unknown error"),
                }
                return [err]
        return []

    @staticmethod
    def _handle_agent_message(item: dict[str, Any], *, started: bool) -> list[IREvent]:
        if started:
            return []
        text = item.get("text", "")
        if not text:
            return []
        delta: MessageDeltaEvent = {
            "type": "message_delta",
            "text": text,
        }
        return [delta]

    @staticmethod
    def _handle_command(item: dict[str, Any], *, started: bool) -> list[IREvent]:
        item_id = item.get("id", "")
        if started:
            tool_use: ToolUseEvent = {
                "type": "tool_use",
                "tool_use_id": item_id,
                "tool_name": "command_execution",
                "tool_input": {"command": item.get("command", "")},
            }
            return [tool_use]
        else:
            tool_result: ToolResultEvent = {
                "type": "tool_result",
                "tool_use_id": item_id,
                "content": item.get("aggregated_output", ""),
            }
            if item.get("status") == "failed":
                tool_result["is_error"] = True
            return [tool_result]

    @staticmethod
    def _handle_file_change(item: dict[str, Any], *, started: bool) -> list[IREvent]:
        if started:
            return []
        results: list[IREvent] = []
        for change in item.get("changes", []):
            kind = change.get("kind", "update")
            action_map: dict[str, str] = {
                "add": "create",
                "delete": "delete",
                "update": "modify",
            }
            action_str = action_map.get(kind, "modify")
            action = cast(Literal["create", "modify", "delete"], action_str)
            diff_event: FileDiffEvent = {
                "type": "file_diff",
                "file_path": change.get("path", ""),
                "action": action,
            }
            results.append(diff_event)
        return results

    @staticmethod
    def _handle_mcp_tool(item: dict[str, Any], *, started: bool) -> list[IREvent]:
        item_id = item.get("id", "")
        if started:
            server = item.get("server", "")
            tool = item.get("tool", "")
            mcp_use: ToolUseEvent = {
                "type": "tool_use",
                "tool_use_id": item_id,
                "tool_name": f"mcp:{server}/{tool}",
                "tool_input": item.get("arguments", {}),
            }
            return [mcp_use]
        else:
            result_content = ""
            result_obj = item.get("result")
            if result_obj:
                content_list = result_obj.get("content", [])
                result_content = str(content_list)
            mcp_result: ToolResultEvent = {
                "type": "tool_result",
                "tool_use_id": item_id,
                "content": result_content,
            }
            error_obj = item.get("error")
            if error_obj or item.get("status") == "failed":
                mcp_result["is_error"] = True
                if isinstance(error_obj, dict):
                    mcp_result["content"] = error_obj.get("message", result_content)
            return [mcp_result]


__all__ = ["CodexNativeProvider"]
