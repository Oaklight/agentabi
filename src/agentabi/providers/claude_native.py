"""
agentabi - Claude Native Provider

Our own subprocess + JSONL implementation for Claude Code CLI.
Migrated from the old ClaudeCodeAdapter.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from collections.abc import AsyncIterator
from typing import Any, cast

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

_PERMISSION_LEVEL_MAP: dict[str, str] = {
    "full_auto": "bypassPermissions",
    "accept_edits": "acceptEdits",
    "plan": "plan",
    "auto": "auto",
    "dont_ask": "dontAsk",
    "default": "default",
}


class ClaudeNativeProvider:
    """Native subprocess + JSONL provider for Claude Code CLI.

    Runs `claude --print --output-format stream-json` as a subprocess
    and parses the JSONL output into IR events.
    """

    @staticmethod
    def is_available() -> bool:
        """Check if `claude` CLI is available."""
        return shutil.which("claude") is not None

    def capabilities(self) -> AgentCapabilities:
        """Declare Claude Code capabilities."""
        return {
            "name": "Claude Code",
            "agent_type": "claude_code",
            "supports_streaming": True,
            "supports_mcp": True,
            "supports_session_resume": True,
            "supports_system_prompt": True,
            "supports_tool_filtering": True,
            "supports_file_diffs": False,
            "supports_permissions": True,
            "supports_multi_turn": True,
            "transport": "subprocess",
        }

    async def stream(self, task: TaskConfig) -> AsyncIterator[IREvent]:
        """Run task and yield IR events."""
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
            async for line in proc.stdout:
                decoded = line.decode().rstrip("\n").rstrip("\r")
                if not decoded:
                    continue
                try:
                    raw = json.loads(decoded)
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
        """Run task and return aggregated result."""
        from .base import default_run

        return await default_run(self, task)

    # ========== Private: command building ==========

    @staticmethod
    def _build_command(task: TaskConfig) -> list[str]:
        """Convert TaskConfig to `claude` CLI arguments."""
        cmd = ["claude", "--print", "--output-format", "stream-json", "--verbose"]

        if "model" in task:
            cmd.extend(["--model", task["model"]])

        if "system_prompt" in task:
            cmd.extend(["--system-prompt", task["system_prompt"]])
        if "append_system_prompt" in task:
            cmd.extend(["--append-system-prompt", task["append_system_prompt"]])

        if "max_turns" in task:
            cmd.extend(["--max-turns", str(task["max_turns"])])

        if "session_id" in task and task.get("resume"):
            cmd.extend(["--resume", task["session_id"]])

        permissions = task.get("permissions")
        if permissions:
            level = permissions.get("level")
            cli_mode = _PERMISSION_LEVEL_MAP.get(level or "")
            if cli_mode:
                cmd.extend(["--permission-mode", cli_mode])

        if "allowed_tools" in task:
            cmd.extend(["--allowed-tools", ",".join(task["allowed_tools"])])
        elif permissions and "allowed_tools" in permissions:
            cmd.extend(["--allowed-tools", ",".join(permissions["allowed_tools"])])

        if "disallowed_tools" in task:
            cmd.extend(["--disallowed-tools", ",".join(task["disallowed_tools"])])
        elif permissions and "disallowed_tools" in permissions:
            cmd.extend(
                ["--disallowed-tools", ",".join(permissions["disallowed_tools"])]
            )

        if "mcp_config" in task:
            cmd.extend(["--mcp-config", task["mcp_config"]])

        cmd.append("--include-partial-messages")

        extensions = task.get("agent_extensions", {})
        if "max_budget_usd" in extensions:
            cmd.extend(["--max-budget-usd", str(extensions["max_budget_usd"])])
        if extensions.get("continue_session"):
            cmd.append("--continue")

        cmd.append("--")
        cmd.append(task["prompt"])

        return cmd

    # ========== Private: event parsing ==========

    @staticmethod
    def _parse_event(event: dict[str, Any]) -> list[IREvent]:
        """Convert a Claude Code JSONL event to IR event(s)."""
        event_type = event.get("type")

        if event_type == "system":
            return ClaudeNativeProvider._handle_system(event)
        elif event_type == "assistant":
            return ClaudeNativeProvider._handle_assistant(event)
        elif event_type == "user":
            return ClaudeNativeProvider._handle_user(event)
        elif event_type == "stream_event":
            return ClaudeNativeProvider._handle_stream_event(event)
        elif event_type == "result":
            return ClaudeNativeProvider._handle_result(event)
        return []

    @staticmethod
    def _handle_system(event: dict[str, Any]) -> list[IREvent]:
        ir: SessionStartEvent = {
            "type": "session_start",
            "session_id": event.get("session_id", ""),
            "agent": "claude_code",
        }
        if "model" in event:
            ir["model"] = event["model"]
        if "tools" in event:
            ir["tools"] = event["tools"]
        if "cwd" in event:
            ir["working_dir"] = event["cwd"]
        return [ir]

    @staticmethod
    def _handle_assistant(event: dict[str, Any]) -> list[IREvent]:
        results: list[IREvent] = []
        message = event.get("message", {})
        content = message.get("content", [])
        message_id = message.get("id", "")

        start: MessageStartEvent = {"type": "message_start", "role": "assistant"}
        if message_id:
            start["message_id"] = message_id
        results.append(start)

        full_text = ""
        for block in content:
            block_type = block.get("type")
            if block_type == "text":
                full_text += block.get("text", "")
            elif block_type == "tool_use":
                tool_event: ToolUseEvent = {
                    "type": "tool_use",
                    "tool_use_id": block.get("id", ""),
                    "tool_name": block.get("name", ""),
                    "tool_input": block.get("input", {}),
                }
                results.append(tool_event)

        end: MessageEndEvent = {
            "type": "message_end",
            "stop_reason": message.get("stop_reason", ""),
        }
        if message_id:
            end["message_id"] = message_id
        if full_text:
            end["text"] = full_text
        results.append(end)

        return results

    @staticmethod
    def _handle_user(event: dict[str, Any]) -> list[IREvent]:
        results: list[IREvent] = []
        message = event.get("message", {})
        content = message.get("content", [])

        for block in content:
            if block.get("type") == "tool_result":
                tool_result: ToolResultEvent = {
                    "type": "tool_result",
                    "tool_use_id": block.get("tool_use_id", ""),
                    "content": str(block.get("content", "")),
                }
                if block.get("is_error"):
                    tool_result["is_error"] = True
                results.append(tool_result)

        tool_use_result = event.get("tool_use_result", {})
        if tool_use_result and results:
            duration = tool_use_result.get("durationMs")
            if duration is not None and results[-1].get("type") == "tool_result":
                last = cast(ToolResultEvent, results[-1])
                last["duration_ms"] = duration

        return results

    @staticmethod
    def _handle_stream_event(event: dict[str, Any]) -> list[IREvent]:
        inner = event.get("event", {})
        if inner.get("type") == "content_block_delta":
            delta = inner.get("delta", {})
            if delta.get("type") == "text_delta":
                text = delta.get("text", "")
                if text:
                    ir: MessageDeltaEvent = {"type": "message_delta", "text": text}
                    return [ir]
        return []

    @staticmethod
    def _handle_result(event: dict[str, Any]) -> list[IREvent]:
        results: list[IREvent] = []

        raw_usage = event.get("usage", {})
        usage: UsageInfo = {}
        if "input_tokens" in raw_usage:
            usage["input_tokens"] = raw_usage["input_tokens"]
        if "output_tokens" in raw_usage:
            usage["output_tokens"] = raw_usage["output_tokens"]
        if "cache_read_input_tokens" in raw_usage:
            usage["cache_read_tokens"] = raw_usage["cache_read_input_tokens"]
        if "cache_creation_input_tokens" in raw_usage:
            usage["cache_creation_tokens"] = raw_usage["cache_creation_input_tokens"]

        total = raw_usage.get("input_tokens", 0) + raw_usage.get("output_tokens", 0)
        if total:
            usage["total_tokens"] = total

        usage_event: UsageEvent = {"type": "usage", "usage": usage}
        if "total_cost_usd" in event:
            usage_event["cost_usd"] = event["total_cost_usd"]
        results.append(usage_event)

        if event.get("is_error"):
            error_msg = "; ".join(event.get("errors", ["Unknown error"]))
            error_event: ErrorEvent = {
                "type": "error",
                "error": error_msg,
                "is_fatal": True,
            }
            results.append(error_event)

        end: SessionEndEvent = {"type": "session_end"}
        if "session_id" in event:
            end["session_id"] = event["session_id"]
        results.append(end)

        return results


__all__ = ["ClaudeNativeProvider"]
