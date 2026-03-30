"""
agentabi - Codex SDK Provider

Wraps codex-sdk-python behind the Provider protocol.
"""

from __future__ import annotations

from typing import AsyncIterator

from ..types.ir.capabilities import AgentCapabilities
from ..types.ir.events import IREvent
from ..types.ir.session import SessionResult
from ..types.ir.task import TaskConfig


class CodexSDKProvider:
    """Wraps codex-sdk-python behind Provider protocol.

    Requires: pip install agentabi[codex]
    """

    @staticmethod
    def is_available() -> bool:
        """Check if codex-sdk-python is installed."""
        try:
            import codex_sdk  # noqa: F401

            return True
        except ImportError:
            return False

    def capabilities(self) -> AgentCapabilities:
        return {
            "name": "Codex (SDK)",
            "agent_type": "codex",
            "supports_streaming": True,
            "supports_mcp": False,
            "supports_session_resume": True,
            "supports_system_prompt": True,
            "supports_tool_filtering": False,
            "supports_permissions": True,
            "supports_multi_turn": True,
        }

    async def stream(self, task: TaskConfig) -> AsyncIterator[IREvent]:
        """Run task via codex-sdk-python and yield IR events."""
        from codex_sdk import CodexAgentOptions, query

        options = CodexAgentOptions(
            model=task.get("model"),
            system_prompt=task.get("system_prompt"),
        )

        permissions = task.get("permissions")
        if permissions:
            level = permissions.get("level")
            if level == "full_auto":
                options.approval_mode = "full-auto"

        async for msg in query(prompt=task["prompt"], options=options):
            for event in self._convert(msg):
                yield event

    async def run(self, task: TaskConfig) -> SessionResult:
        from .base import default_run

        return await default_run(self, task)

    @staticmethod
    def _convert(msg: dict) -> list[IREvent]:
        """Convert a codex-sdk message to IR events.

        TODO: Implement full conversion once SDK API is stable.
        """
        return []


__all__ = ["CodexSDKProvider"]
