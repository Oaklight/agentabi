"""
agentabi - Antigravity CLI (agy) Native Provider

Native subprocess provider for Antigravity CLI.
Runs `agy --print --dangerously-skip-permissions <prompt>` and captures
plain-text output (agy does not support stream-json).
"""

from __future__ import annotations

import asyncio
import os
import shutil
import uuid
from collections.abc import AsyncIterator

from ..types.ir.capabilities import AgentCapabilities
from ..types.ir.events import (
    IREvent,
    MessageDeltaEvent,
    MessageEndEvent,
    MessageStartEvent,
    SessionEndEvent,
    SessionStartEvent,
)
from ..types.ir.session import SessionResult
from ..types.ir.task import TaskConfig
from .base import collect_subprocess_errors


class AntigravityNativeProvider:
    """Native subprocess provider for Antigravity CLI (agy).

    Runs `agy --print --dangerously-skip-permissions <prompt>` as a
    subprocess and streams stdout line-by-line as MessageDelta events.

    Unlike Gemini CLI, agy does not support `--output-format stream-json`.
    The provider treats each stdout line as a text delta and wraps the
    session in synthetic SessionStart/SessionEnd events.
    """

    @staticmethod
    def is_available() -> bool:
        """Check if `agy` CLI is available."""
        return shutil.which("agy") is not None

    def capabilities(self) -> AgentCapabilities:
        return {
            "name": "Antigravity CLI",
            "agent_type": "antigravity",
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

        session_id = str(uuid.uuid4())
        try:
            start: SessionStartEvent = {
                "type": "session_start",
                "session_id": session_id,
                "agent": "antigravity",
            }
            model = task.get("model")
            if model:
                start["model"] = model
            yield start

            msg_start: MessageStartEvent = {
                "type": "message_start",
                "role": "assistant",
            }
            yield msg_start

            assert proc.stdout is not None
            async for line_bytes in proc.stdout:
                line = line_bytes.decode().rstrip("\r\n")
                if not line:
                    continue
                delta: MessageDeltaEvent = {
                    "type": "message_delta",
                    "text": line + "\n",
                }
                yield delta

            await proc.wait()

            end: MessageEndEvent = {
                "type": "message_end",
                "stop_reason": "end_turn",
            }
            yield end

            for err in await collect_subprocess_errors(
                proc, timed_out=timed_out, timeout_seconds=timeout
            ):
                yield err

            session_end: SessionEndEvent = {"type": "session_end"}
            yield session_end

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
    def _build_env(task: TaskConfig) -> dict[str, str]:
        """Build merged environment with Gemini-compatible variable mapping.

        Maps generic OPENAI_BASE_URL / OPENAI_API_KEY to Gemini-specific
        equivalents when the Gemini-native env vars are not already set.
        agy inherits GOOGLE_GEMINI_BASE_URL and GEMINI_API_KEY from
        the Gemini CLI ecosystem.
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

    @staticmethod
    def _build_command(task: TaskConfig) -> list[str]:
        """Convert TaskConfig to agy CLI arguments."""
        cmd = ["agy"]

        permissions = task.get("permissions")
        if permissions:
            level = permissions.get("level")
            if level == "accept_edits":
                cmd.append("--mode")
                cmd.append("accept-edits")
            elif level == "plan":
                cmd.append("--mode")
                cmd.append("plan")
            # full_auto / default: use --dangerously-skip-permissions
        else:
            cmd.append("--dangerously-skip-permissions")

        if permissions and permissions.get("level") in ("full_auto", "default"):
            cmd.append("--dangerously-skip-permissions")

        timeout = task.get("timeout")
        if timeout:
            cmd.extend(["--print-timeout", f"{int(timeout)}s"])

        if task.get("resume"):
            session_id = task.get("session_id")
            if session_id:
                cmd.extend(["--conversation", session_id])
            else:
                cmd.append("--continue")

        cmd.extend(["--print", task["prompt"]])
        return cmd


__all__ = ["AntigravityNativeProvider"]
