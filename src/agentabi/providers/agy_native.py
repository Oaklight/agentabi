"""
agentabi - Antigravity CLI (agy) Native Provider

Native subprocess provider for the agy CLI (Google's agentic coding CLI).
Runs `agy --output-format stream-json [options] -p <prompt>` and parses JSONL output.
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
from .base import collect_subprocess_errors


class AgyNativeProvider:
    """Native subprocess provider for Antigravity CLI (agy).

    Runs `agy --output-format stream-json [options] -p <prompt>` as a
    subprocess and parses stream-json events into IR events.

    agy stream-json event types:
    - event: "init"        — session metadata (conversation_id, cwd, tools)
    - event: "step_update" — step progress with step_type variants:
        - "user_input"      — user input step (skipped)
        - "agent_response"  — assistant response with text_delta and usage
        - "tool_use"        — tool invocation
        - "checkpoint"      — checkpoint marker (skipped)
        - "error_message"   — error during step (skipped, errors in result)
    - event: "result"      — final result with status, response, usage
    """

    @staticmethod
    def is_available() -> bool:
        """Check if `agy` CLI is available."""
        return shutil.which("agy") is not None

    def capabilities(self) -> AgentCapabilities:
        """Declare Antigravity capabilities."""
        return {
            "name": "Antigravity",
            "agent_type": "agy",
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
        """Run task via agy CLI and yield IR events."""
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

    async def run(self, task: TaskConfig) -> SessionResult:
        """Run task and return aggregated result."""
        from .base import default_run

        return await default_run(self, task)

    # ========== Private: environment ==========

    @staticmethod
    def _build_env(task: TaskConfig) -> dict[str, str]:
        """Build merged environment with agy-specific variable mapping.

        Maps generic OPENAI_BASE_URL / OPENAI_API_KEY to agy-specific
        equivalents (GOOGLE_GEMINI_BASE_URL / GEMINI_API_KEY) when the
        agy-native env vars are not already set.
        """
        task_env = task.get("env") or {}
        merged_env = {**os.environ, **task_env}

        if not task_env.get("GOOGLE_GEMINI_BASE_URL") and task_env.get(
            "OPENAI_BASE_URL"
        ):
            base = task_env["OPENAI_BASE_URL"].rstrip("/")
            if base.endswith("/v1"):
                base = base[:-3]
            merged_env["GOOGLE_GEMINI_BASE_URL"] = base

        if not task_env.get("GEMINI_API_KEY") and task_env.get("OPENAI_API_KEY"):
            merged_env["GEMINI_API_KEY"] = task_env["OPENAI_API_KEY"]

        return merged_env

    # ========== Private: command building ==========

    @staticmethod
    def _build_command(task: TaskConfig) -> list[str]:
        """Convert TaskConfig to agy CLI arguments."""
        cmd = ["agy", "--output-format", "stream-json"]

        if "model" in task:
            cmd.extend(["--model", task["model"]])

        # Permission mapping
        permissions = task.get("permissions")
        if permissions:
            level = permissions.get("level")
            if level == "full_auto":
                cmd.append("--dangerously-skip-permissions")
            elif level == "accept_edits":
                cmd.extend(["--mode", "accept-edits"])
            elif level == "plan":
                cmd.extend(["--mode", "plan"])
            # "default" → omit

        # Session management
        if "session_id" in task and task.get("resume"):
            cmd.extend(["--conversation", task["session_id"]])
        elif task.get("resume"):
            cmd.append("--continue")

        # Timeout → --print-timeout (convert seconds to "Ns" format)
        timeout = task.get("timeout")
        if timeout is not None and timeout > 0:
            cmd.extend(["--print-timeout", f"{int(timeout)}s"])

        # Agent-specific extensions
        ext = task.get("agent_extensions") or {}

        if "effort" in ext:
            cmd.extend(["--effort", ext["effort"]])

        if ext.get("sandbox"):
            cmd.append("--sandbox")

        if ext.get("disable_slash_commands"):
            cmd.append("--disable-slash-commands")

        if "agent" in ext:
            cmd.extend(["--agent", ext["agent"]])

        if "json_schema" in ext:
            cmd.extend(["--json-schema", ext["json_schema"]])

        for d in ext.get("add_dirs") or []:
            cmd.extend(["--add-dir", d])

        # Prompt must come last via -p flag
        cmd.extend(["-p", task["prompt"]])
        return cmd

    # ========== Private: event parsing ==========

    @staticmethod
    def _parse_event(raw: dict[str, Any]) -> list[IREvent]:
        """Convert an agy stream-json event to IR events."""
        event_type = raw.get("event")

        if event_type == "init":
            return AgyNativeProvider._handle_init(raw)
        elif event_type == "step_update":
            return AgyNativeProvider._handle_step_update(raw)
        elif event_type == "result":
            return AgyNativeProvider._handle_result(raw)
        return []

    @staticmethod
    def _handle_init(raw: dict[str, Any]) -> list[IREvent]:
        """Handle init event — emits SessionStartEvent."""
        start: SessionStartEvent = {
            "type": "session_start",
            "session_id": raw.get("conversation_id", ""),
            "agent": "agy",
        }
        init_data = raw.get("init", {})
        cwd = init_data.get("cwd")
        if cwd:
            start["working_dir"] = cwd
        tools = init_data.get("tools")
        if tools:
            start["tools"] = tools
        return [start]

    @staticmethod
    def _handle_step_update(raw: dict[str, Any]) -> list[IREvent]:
        """Handle step_update event — dispatches by step_type."""
        step = raw.get("step_update", {})
        step_type = step.get("step_type", "")

        if step_type == "agent_response":
            return AgyNativeProvider._handle_agent_response(step)
        elif step_type == "tool_use":
            return AgyNativeProvider._handle_tool_use_step(step)
        # user_input, checkpoint, error_message — skip
        return []

    @staticmethod
    def _handle_agent_response(step: dict[str, Any]) -> list[IREvent]:
        """Handle agent_response step — emits message events."""
        results: list[IREvent] = []

        msg_start: MessageStartEvent = {
            "type": "message_start",
            "role": "assistant",
        }
        results.append(msg_start)

        text_delta = step.get("text_delta", "")
        if text_delta:
            delta: MessageDeltaEvent = {
                "type": "message_delta",
                "text": text_delta,
            }
            results.append(delta)

        end: MessageEndEvent = {"type": "message_end"}
        if text_delta:
            end["text"] = text_delta
        results.append(end)

        return results

    @staticmethod
    def _handle_tool_use_step(step: dict[str, Any]) -> list[IREvent]:
        """Handle tool_use step — emits ToolUseEvent and optionally ToolResultEvent."""
        results: list[IREvent] = []

        tool_event: ToolUseEvent = {
            "type": "tool_use",
            "tool_use_id": step.get("tool_use_id", ""),
            "tool_name": step.get("tool_name", ""),
            "tool_input": step.get("tool_input", {}),
        }
        results.append(tool_event)

        # If the step has a result, emit ToolResultEvent
        tool_result = step.get("tool_result")
        if tool_result is not None:
            result: ToolResultEvent = {
                "type": "tool_result",
                "tool_use_id": step.get("tool_use_id", ""),
                "content": str(tool_result),
            }
            if step.get("is_error"):
                result["is_error"] = True
            results.append(result)

        return results

    @staticmethod
    def _handle_result(raw: dict[str, Any]) -> list[IREvent]:
        """Handle result event — emits UsageEvent + SessionEndEvent (+ ErrorEvent)."""
        results: list[IREvent] = []
        result_data = raw.get("result", {})

        # Usage
        raw_usage = result_data.get("usage", {})
        usage: UsageInfo = {}
        if raw_usage.get("input_tokens"):
            usage["input_tokens"] = raw_usage["input_tokens"]
        if raw_usage.get("output_tokens"):
            usage["output_tokens"] = raw_usage["output_tokens"]
        if raw_usage.get("total_tokens"):
            usage["total_tokens"] = raw_usage["total_tokens"]
        if raw_usage.get("cache_read_tokens"):
            usage["cache_read_tokens"] = raw_usage["cache_read_tokens"]
        # thinking_tokens not mapped to IR (no IR field for it)

        usage_event: UsageEvent = {"type": "usage", "usage": usage}
        results.append(usage_event)

        # Error if status != SUCCESS
        status = result_data.get("status", "")
        if status != "SUCCESS":
            error_msg = result_data.get("error", f"agy exited with status: {status}")
            err: ErrorEvent = {
                "type": "error",
                "error": str(error_msg),
                "is_fatal": True,
            }
            results.append(err)

        # Session end
        session_end: SessionEndEvent = {"type": "session_end"}
        conv_id = result_data.get("conversation_id")
        if conv_id:
            session_end["session_id"] = conv_id
        results.append(session_end)

        return results


__all__ = ["AgyNativeProvider"]
