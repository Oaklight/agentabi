"""
agentabi - Pi Coding Agent Native Provider

Native subprocess provider for Pi CLI.
Runs `pi --print --mode json <prompt>` and parses JSONL output.
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
    ContentBlockEndEvent,
    ContentBlockStartEvent,
    IREvent,
    MessageDeltaEvent,
    MessageEndEvent,
    MessageStartEvent,
    ReasoningDeltaEvent,
    SessionEndEvent,
    SessionStartEvent,
    ToolResultEvent,
    ToolUseEvent,
    UsageEvent,
    UsageInfo,
)
from ..types.ir.session import SessionResult
from ..types.ir.task import TaskConfig
from .base import collect_subprocess_errors

_THINKING_LEVEL_MAP: dict[str, str] = {
    "off": "off",
    "low": "low",
    "medium": "medium",
    "high": "high",
    "max": "xhigh",
}


class PiNativeProvider:
    """Native subprocess provider for Pi coding agent CLI.

    Runs `pi --print --mode json <prompt>` as a subprocess
    and parses JSONL events into IR events.

    Pi CLI JSONL event types:
    - session         — session metadata (id, cwd)
    - agent_start     — agent begins processing (skipped, no IR mapping)
    - turn_start      — new LLM turn begins
    - message_start   — message begins (user messages skipped)
    - message_update  — streaming delta (text_delta mapped;
                         thinking_start/delta/end skipped, no IR type)
    - message_end     — message completed with full content + usage
    - tool_execution_start — tool call begins
    - tool_execution_end   — tool call completed with result
    - turn_end        — turn ends with usage stats
    - agent_end       — agent finishes, contains full message history
    """

    def __init__(self) -> None:
        self._pending_text: list[str] = []
        self._block_index: int = 0

    @staticmethod
    def is_available() -> bool:
        """Check if `pi` CLI is available."""
        return shutil.which("pi") is not None

    def capabilities(self) -> AgentCapabilities:
        """Declare Pi capabilities."""
        return {
            "name": "Pi",
            "agent_type": "pi",
            "supports_streaming": True,
            "supports_mcp": True,
            "supports_thinking": True,
            "supports_ephemeral": True,
            "supports_session_resume": True,
            "supports_system_prompt": True,
            "supports_tool_filtering": True,
            "supports_permissions": False,
            "supports_multi_turn": True,
            "transport": "subprocess",
        }

    async def stream(self, task: TaskConfig) -> AsyncIterator[IREvent]:
        """Run task via pi CLI and yield IR events."""
        cmd = self._build_command(task)
        merged_env = {**os.environ, **(task.get("env") or {})}

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=merged_env,
            cwd=task.get("working_dir"),
        )

        timed_out = False
        timeout = task.get("timeout")
        kill_task: asyncio.Task[None] | None = None

        if timeout is not None and timeout > 0:

            async def _kill_after(secs: float) -> None:
                await asyncio.sleep(secs)
                nonlocal timed_out
                timed_out = True
                proc.kill()

            kill_task = asyncio.create_task(_kill_after(timeout))

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
            for err_event in await collect_subprocess_errors(
                proc, timed_out=timed_out, timeout_seconds=timeout
            ):
                yield err_event
        finally:
            if kill_task is not None:
                kill_task.cancel()
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
        """Convert TaskConfig to pi CLI arguments."""
        cmd = ["pi", "--print", "--mode", "json"]

        if "model" in task:
            cmd.extend(["--model", task["model"]])

        if "system_prompt" in task:
            cmd.extend(["--system-prompt", task["system_prompt"]])
        if "append_system_prompt" in task:
            cmd.extend(["--append-system-prompt", task["append_system_prompt"]])

        ext = task.get("agent_extensions") or {}
        PiNativeProvider._add_session_flags(cmd, task, ext)
        PiNativeProvider._add_extension_flags(cmd, task, ext)
        PiNativeProvider._add_tool_flags(cmd, task)

        cmd.append(task["prompt"])
        return cmd

    @staticmethod
    def _add_session_flags(
        cmd: list[str], task: TaskConfig, ext: dict[str, Any]
    ) -> None:
        """Append session flags to command."""
        if task.get("ephemeral") or ext.get("no_session"):
            cmd.append("--no-session")
        elif "session_id" in task:
            if task.get("resume"):
                cmd.extend(["--session", task["session_id"]])
            else:
                cmd.extend(["--session-id", task["session_id"]])
        elif task.get("resume"):
            cmd.append("--continue")

    @staticmethod
    def _add_extension_flags(
        cmd: list[str], task: TaskConfig, ext: dict[str, Any]
    ) -> None:
        """Append Pi-specific flags from agent_extensions."""
        thinking = _THINKING_LEVEL_MAP.get(task.get("thinking_level", ""))
        if not thinking:
            thinking = ext.get("thinking")
        if thinking:
            cmd.extend(["--thinking", thinking])

        if ext.get("approve") and ext.get("no_approve"):
            raise ValueError("approve and no_approve are mutually exclusive")
        if ext.get("approve"):
            cmd.append("--approve")
        elif ext.get("no_approve"):
            cmd.append("--no-approve")

        if ext.get("no_extensions"):
            cmd.append("--no-extensions")
        else:
            for e in ext.get("extensions") or []:
                cmd.extend(["--extension", e])

        if ext.get("no_skills"):
            cmd.append("--no-skills")
        else:
            for s in ext.get("skills") or []:
                cmd.extend(["--skill", s])

        if ext.get("no_context_files"):
            cmd.append("--no-context-files")

        if "mcp_config" in task:
            cmd.extend(["--extension", task["mcp_config"]])

    @staticmethod
    def _add_tool_flags(cmd: list[str], task: TaskConfig) -> None:
        """Append tool allow/deny flags."""
        if "allowed_tools" in task:
            cmd.extend(["--tools", ",".join(task["allowed_tools"])])
        elif task.get("permissions") and "allowed_tools" in task["permissions"]:
            cmd.extend(["--tools", ",".join(task["permissions"]["allowed_tools"])])

        if "disallowed_tools" in task:
            cmd.extend(["--exclude-tools", ",".join(task["disallowed_tools"])])
        elif task.get("permissions") and "disallowed_tools" in task["permissions"]:
            cmd.extend(
                [
                    "--exclude-tools",
                    ",".join(task["permissions"]["disallowed_tools"]),
                ]
            )

    # ========== Private: event parsing ==========

    def _parse_event(self, raw: dict[str, Any]) -> list[IREvent]:
        """Convert a Pi CLI JSONL event to IR events."""
        event_type = raw.get("type")

        if event_type == "session":
            return self._handle_session(raw)
        elif event_type == "turn_start":
            return self._handle_turn_start()
        elif event_type == "message_update":
            return self._handle_message_update(raw)
        elif event_type == "message_end":
            return self._handle_message_end(raw)
        elif event_type == "tool_execution_start":
            return self._handle_tool_start(raw)
        elif event_type == "tool_execution_end":
            return self._handle_tool_end(raw)
        elif event_type == "turn_end":
            return self._handle_turn_end(raw)
        elif event_type == "agent_end":
            return self._handle_agent_end(raw)
        return []

    @staticmethod
    def _handle_session(raw: dict[str, Any]) -> list[IREvent]:
        """Handle session event — emits SessionStartEvent."""
        start: SessionStartEvent = {
            "type": "session_start",
            "session_id": raw.get("id", ""),
            "agent": "pi",
        }
        cwd = raw.get("cwd")
        if cwd:
            start["working_dir"] = cwd
        return [start]

    def _handle_turn_start(self) -> list[IREvent]:
        """Handle turn_start — emits MessageStartEvent.

        Also defensively clears pending text to prevent bleed from
        a previous turn if message_end was missed.
        """
        self._pending_text = []
        self._block_index = 0
        msg_start: MessageStartEvent = {
            "type": "message_start",
            "role": "assistant",
        }
        return [msg_start]

    def _handle_message_update(self, raw: dict[str, Any]) -> list[IREvent]:
        """Handle message_update — emits delta and content block events."""
        assistant_event = raw.get("assistantMessageEvent", {})
        event_subtype = assistant_event.get("type", "")

        if event_subtype == "text_delta":
            delta_text = assistant_event.get("delta", "")
            if delta_text:
                self._pending_text.append(delta_text)
                delta: MessageDeltaEvent = {
                    "type": "message_delta",
                    "text": delta_text,
                }
                return [delta]
        elif event_subtype == "thinking_start":
            block_start: ContentBlockStartEvent = {
                "type": "content_block_start",
                "block_index": self._block_index,
                "block_type": "thinking",
            }
            self._block_index += 1
            return [block_start]
        elif event_subtype == "thinking_delta":
            delta_text = assistant_event.get("delta", "")
            if delta_text:
                reasoning: ReasoningDeltaEvent = {
                    "type": "reasoning_delta",
                    "reasoning": delta_text,
                    "block_index": max(0, self._block_index - 1),
                }
                return [reasoning]
        elif event_subtype == "thinking_end":
            block_end: ContentBlockEndEvent = {
                "type": "content_block_end",
                "block_index": max(0, self._block_index - 1),
            }
            return [block_end]
        elif event_subtype == "text_start":
            block_start_text: ContentBlockStartEvent = {
                "type": "content_block_start",
                "block_index": self._block_index,
                "block_type": "text",
            }
            self._block_index += 1
            return [block_start_text]
        elif event_subtype == "text_end":
            block_end_text: ContentBlockEndEvent = {
                "type": "content_block_end",
                "block_index": max(0, self._block_index - 1),
            }
            return [block_end_text]
        return []

    def _handle_message_end(self, raw: dict[str, Any]) -> list[IREvent]:
        """Handle message_end — emits MessageEndEvent with accumulated text."""
        message = raw.get("message", {})
        role = message.get("role", "")

        # Skip user message_end events
        if role != "assistant":
            return []

        # Extract full text from content blocks
        content = message.get("content", [])
        full_text = ""
        for block in content:
            if block.get("type") == "text":
                full_text += block.get("text", "")

        end: MessageEndEvent = {
            "type": "message_end",
            "stop_reason": message.get("stopReason", ""),
        }

        # Prefer full text from content blocks, fallback to pending
        if full_text:
            end["text"] = full_text
            self._pending_text = []
        elif self._pending_text:
            end["text"] = "".join(self._pending_text)
            self._pending_text = []

        return [end]

    @staticmethod
    def _handle_tool_start(raw: dict[str, Any]) -> list[IREvent]:
        """Handle tool_execution_start — emits ToolUseEvent."""
        tool_use: ToolUseEvent = {
            "type": "tool_use",
            "tool_use_id": raw.get("toolCallId", ""),
            "tool_name": raw.get("toolName", ""),
            "tool_input": raw.get("args", {}),
        }
        return [tool_use]

    @staticmethod
    def _handle_tool_end(raw: dict[str, Any]) -> list[IREvent]:
        """Handle tool_execution_end — emits ToolResultEvent."""
        result = raw.get("result", {})
        content_blocks = result.get("content", [])
        content_text = ""
        for block in content_blocks:
            if isinstance(block, dict) and block.get("type") == "text":
                content_text += block.get("text", "")

        tool_result: ToolResultEvent = {
            "type": "tool_result",
            "tool_use_id": raw.get("toolCallId", ""),
            "content": content_text,
        }
        if raw.get("isError"):
            tool_result["is_error"] = True
        return [tool_result]

    def _handle_turn_end(self, raw: dict[str, Any]) -> list[IREvent]:
        """Handle turn_end — emits UsageEvent with token counts and cost."""
        results: list[IREvent] = []
        message = raw.get("message", {})

        # Extract usage
        raw_usage = message.get("usage", {})
        usage: UsageInfo = {}
        if raw_usage.get("input"):
            usage["input_tokens"] = raw_usage["input"]
        if raw_usage.get("output"):
            usage["output_tokens"] = raw_usage["output"]
        total = raw_usage.get("totalTokens", 0)
        if total:
            usage["total_tokens"] = total
        if raw_usage.get("cacheRead"):
            usage["cache_read_tokens"] = raw_usage["cacheRead"]
        if raw_usage.get("cacheWrite"):
            usage["cache_creation_tokens"] = raw_usage["cacheWrite"]

        usage_event: UsageEvent = {"type": "usage", "usage": usage}

        # Extract cost
        cost = raw_usage.get("cost", {})
        total_cost = cost.get("total")
        if total_cost:
            usage_event["cost_usd"] = total_cost

        # Extract model
        model = message.get("model")
        if model:
            usage_event["model"] = model

        results.append(usage_event)
        return results

    @staticmethod
    def _handle_agent_end(raw: dict[str, Any]) -> list[IREvent]:
        """Handle agent_end — emits SessionEndEvent."""
        end: SessionEndEvent = {"type": "session_end"}
        return [end]


__all__ = ["PiNativeProvider"]
