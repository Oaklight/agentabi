"""
agentabi - Gemini CLI SDK Provider

Wraps gemini-cli-sdk behind the Provider protocol.
"""

from __future__ import annotations

from typing import AsyncIterator

from ..types.ir.capabilities import AgentCapabilities
from ..types.ir.events import IREvent
from ..types.ir.session import SessionResult
from ..types.ir.task import TaskConfig


class GeminiSDKProvider:
    """Wraps gemini-cli-sdk behind Provider protocol.

    Requires: pip install agentabi[gemini]

    gemini-cli-sdk is API-compatible with claude-agent-sdk,
    so the interface pattern is similar.
    """

    @staticmethod
    def is_available() -> bool:
        """Check if gemini-cli-sdk is installed."""
        try:
            import gemini_cli_sdk  # noqa: F401

            return True
        except ImportError:
            return False

    def capabilities(self) -> AgentCapabilities:
        return {
            "name": "Gemini CLI (SDK)",
            "agent_type": "gemini_cli",
            "supports_streaming": True,
            "supports_mcp": True,
            "supports_session_resume": False,
            "supports_system_prompt": True,
            "supports_tool_filtering": False,
            "supports_permissions": True,
            "supports_multi_turn": True,
        }

    async def stream(self, task: TaskConfig) -> AsyncIterator[IREvent]:
        """Run task via gemini-cli-sdk and yield IR events."""
        from gemini_cli_sdk import GeminiAgentOptions, query

        options = GeminiAgentOptions(
            model=task.get("model"),
            system_prompt=task.get("system_prompt"),
        )

        async for msg in query(prompt=task["prompt"], options=options):
            for event in self._convert(msg):
                yield event

    async def run(self, task: TaskConfig) -> SessionResult:
        from .base import default_run

        return await default_run(self, task)

    @staticmethod
    def _convert(msg: dict) -> list[IREvent]:
        """Convert a gemini-cli-sdk message to IR events.

        TODO: Implement full conversion once SDK API is stable.
        """
        return []


__all__ = ["GeminiSDKProvider"]
