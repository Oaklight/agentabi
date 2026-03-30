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
from ..types.ir.events import IREvent
from ..types.ir.session import SessionResult
from ..types.ir.task import TaskConfig


class OpenCodeNativeProvider:
    """Native subprocess provider for OpenCode CLI.

    Runs `opencode` as a subprocess and parses output into IR events.
    """

    @staticmethod
    def is_available() -> bool:
        """Check if `opencode` CLI is available."""
        return shutil.which("opencode") is not None

    def capabilities(self) -> AgentCapabilities:
        return {
            "name": "OpenCode",
            "agent_type": "opencode",
            "supports_streaming": False,
            "supports_mcp": False,
            "supports_session_resume": False,
            "supports_system_prompt": True,
            "supports_tool_filtering": False,
            "supports_permissions": False,
            "supports_multi_turn": False,
            "transport": "subprocess",
        }

    async def stream(self, task: TaskConfig) -> AsyncIterator[IREvent]:
        """Run task via opencode CLI and yield IR events.

        TODO: Implement once OpenCode JSONL output format is documented.
        """
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
        cmd = ["opencode"]

        if "system_prompt" in task:
            cmd.extend(["--prompt", task["system_prompt"]])

        cmd.append(task["prompt"])
        return cmd

    @staticmethod
    def _parse_event(raw: Dict[str, Any]) -> List[IREvent]:
        """Convert opencode output to IR events.

        TODO: Implement full parsing once output format is documented.
        """
        return []


__all__ = ["OpenCodeNativeProvider"]
