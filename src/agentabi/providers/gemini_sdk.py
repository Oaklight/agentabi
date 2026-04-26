"""
agentabi - Gemini CLI SDK Provider

Wraps gemini-cli-sdk behind the Provider protocol.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from ..types.ir.capabilities import AgentCapabilities
from ..types.ir.events import (
    ErrorEvent,
    IREvent,
    MessageEndEvent,
    MessageStartEvent,
    SessionEndEvent,
    SessionStartEvent,
    ToolUseEvent,
    UsageEvent,
    UsageInfo,
)
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
        from gemini_cli_sdk import GeminiOptions, query

        options = GeminiOptions(
            model=task.get("model"),
            system_prompt=task.get("system_prompt"),
            cwd=task.get("working_dir"),
        )

        permissions = task.get("permissions")
        if permissions:
            level = permissions.get("level")
            if level == "full_auto":
                options.yolo = True

        if "max_turns" in task:
            options.max_turns = task["max_turns"]

        async for msg in query(prompt=task["prompt"], options=options):
            for event in self._convert(msg):
                yield event

    async def run(self, task: TaskConfig) -> SessionResult:
        from .base import default_run

        return await default_run(self, task)

    @staticmethod
    def _convert(msg: Any) -> list[IREvent]:
        """Convert a gemini-cli-sdk Message to IR events."""
        from gemini_cli_sdk import (
            AssistantMessage,
            ResultMessage,
            SystemMessage,
            UserMessage,
        )

        if isinstance(msg, SystemMessage):
            return GeminiSDKProvider._convert_system(msg)
        elif isinstance(msg, AssistantMessage):
            return GeminiSDKProvider._convert_assistant(msg)
        elif isinstance(msg, ResultMessage):
            return GeminiSDKProvider._convert_result(msg)
        elif isinstance(msg, UserMessage):
            # UserMessage is just the user's prompt echo; skip.
            return []
        return []

    @staticmethod
    def _convert_system(msg: Any) -> list[IREvent]:
        """Convert SystemMessage to IR events."""
        data = getattr(msg, "data", {}) or {}
        ir: SessionStartEvent = {
            "type": "session_start",
            "session_id": data.get("session_id", ""),
            "agent": "gemini_cli",
        }
        if "model" in data:
            ir["model"] = data["model"]
        if "cwd" in data:
            ir["working_dir"] = data["cwd"]
        return [ir]

    @staticmethod
    def _convert_assistant(msg: Any) -> list[IREvent]:
        """Convert AssistantMessage to IR events."""
        from gemini_cli_sdk import TextBlock, ToolUseBlock

        # Also try importing CodeBlock (Gemini-specific)
        CodeBlock: Any = None
        try:
            from gemini_cli_sdk import CodeBlock
        except ImportError:
            pass

        results: list[IREvent] = []
        start: MessageStartEvent = {"type": "message_start", "role": "assistant"}
        results.append(start)

        full_text = ""
        for block in getattr(msg, "content", []):
            if isinstance(block, TextBlock):
                full_text += block.text
            elif CodeBlock is not None and isinstance(block, CodeBlock):
                lang = getattr(block, "language", "")
                code = getattr(block, "code", "")
                if lang:
                    full_text += f"\n```{lang}\n{code}\n```\n"
                else:
                    full_text += f"\n```\n{code}\n```\n"
            elif isinstance(block, ToolUseBlock):
                tool_event: ToolUseEvent = {
                    "type": "tool_use",
                    "tool_use_id": block.id,
                    "tool_name": block.name,
                    "tool_input": block.input,
                }
                results.append(tool_event)

        end: MessageEndEvent = {"type": "message_end"}
        if full_text:
            end["text"] = full_text
        results.append(end)

        return results

    @staticmethod
    def _convert_result(msg: Any) -> list[IREvent]:
        """Convert ResultMessage to IR events."""
        results: list[IREvent] = []

        raw_usage = getattr(msg, "usage", {}) or {}
        usage: UsageInfo = {}
        if "input_tokens" in raw_usage:
            usage["input_tokens"] = raw_usage["input_tokens"]
        if "output_tokens" in raw_usage:
            usage["output_tokens"] = raw_usage["output_tokens"]

        total = raw_usage.get("input_tokens", 0) + raw_usage.get("output_tokens", 0)
        if total:
            usage["total_tokens"] = total

        usage_event: UsageEvent = {"type": "usage", "usage": usage}
        cost = getattr(msg, "total_cost_usd", None)
        if cost is not None:
            usage_event["cost_usd"] = cost
        results.append(usage_event)

        if getattr(msg, "is_error", False):
            error_event: ErrorEvent = {
                "type": "error",
                "error": getattr(msg, "result", "Unknown error") or "Unknown error",
                "is_fatal": True,
            }
            results.append(error_event)

        end: SessionEndEvent = {"type": "session_end"}
        session_id = getattr(msg, "session_id", None)
        if session_id:
            end["session_id"] = session_id
        results.append(end)

        return results


__all__ = ["GeminiSDKProvider"]
