"""
agentabi - Claude SDK Provider

Wraps claude-agent-sdk behind the Provider protocol.
"""

from __future__ import annotations

from typing import AsyncIterator

from ..types.ir.capabilities import AgentCapabilities
from ..types.ir.events import IREvent
from ..types.ir.session import SessionResult
from ..types.ir.task import TaskConfig


class ClaudeSDKProvider:
    """Wraps claude-agent-sdk behind Provider protocol.

    Requires: pip install agentabi[claude]
    """

    @staticmethod
    def is_available() -> bool:
        """Check if claude-agent-sdk is installed."""
        try:
            import claude_agent_sdk  # noqa: F401

            return True
        except ImportError:
            return False

    def capabilities(self) -> AgentCapabilities:
        return {
            "name": "Claude Code (SDK)",
            "agent_type": "claude_code",
            "supports_streaming": True,
            "supports_mcp": True,
            "supports_session_resume": True,
            "supports_system_prompt": True,
            "supports_tool_filtering": False,
            "supports_permissions": True,
            "supports_multi_turn": True,
        }

    async def stream(self, task: TaskConfig) -> AsyncIterator[IREvent]:
        """Run task via claude-agent-sdk and yield IR events."""
        from claude_agent_sdk import ClaudeAgentOptions, query

        options = ClaudeAgentOptions(
            model=task.get("model"),
            system_prompt=task.get("system_prompt"),
            max_turns=task.get("max_turns"),
        )

        permissions = task.get("permissions")
        if permissions:
            level = permissions.get("level")
            if level == "full_auto":
                options.permission_mode = "bypassPermissions"

        async for msg in query(prompt=task["prompt"], options=options):
            for event in self._convert(msg):
                yield event

    async def run(self, task: TaskConfig) -> SessionResult:
        from .base import default_run

        return await default_run(self, task)

    @staticmethod
    def _convert(msg: dict) -> list[IREvent]:
        """Convert a claude-agent-sdk message to IR events.

        TODO: Implement full conversion once SDK API is stable.
        """
        # Placeholder: SDK message format TBD
        return []


__all__ = ["ClaudeSDKProvider"]
