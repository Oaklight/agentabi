"""
agentabi - Claude SDK Provider

Wraps claude-agent-sdk behind the Provider protocol.
"""

from __future__ import annotations

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
            cwd=task.get("working_dir"),
        )

        permissions = task.get("permissions")
        if permissions:
            level = permissions.get("level")
            if level == "full_auto":
                options.permission_mode = "bypassPermissions"
            elif level == "accept_edits":
                options.permission_mode = "acceptEdits"
            elif level == "plan":
                options.permission_mode = "plan"
            elif level == "auto":
                options.permission_mode = "auto"
            elif level == "dont_ask":
                options.permission_mode = "dontAsk"
            elif level == "default":
                options.permission_mode = "default"

        async for msg in query(prompt=task["prompt"], options=options):
            for event in self._convert(msg):
                yield event

    async def run(self, task: TaskConfig) -> SessionResult:
        from .base import default_run

        return await default_run(self, task)

    @staticmethod
    def _convert(msg: Any) -> list[IREvent]:
        """Convert a claude-agent-sdk Message to IR events."""
        from claude_agent_sdk import (
            AssistantMessage,
            ResultMessage,
            StreamEvent,
            SystemMessage,
            UserMessage,
        )

        if isinstance(msg, SystemMessage):
            return ClaudeSDKProvider._convert_system(msg)
        elif isinstance(msg, AssistantMessage):
            return ClaudeSDKProvider._convert_assistant(msg)
        elif isinstance(msg, UserMessage):
            return ClaudeSDKProvider._convert_user(msg)
        elif isinstance(msg, StreamEvent):
            return ClaudeSDKProvider._convert_stream_event(msg)
        elif isinstance(msg, ResultMessage):
            return ClaudeSDKProvider._convert_result(msg)
        return []

    @staticmethod
    def _convert_system(msg: Any) -> list[IREvent]:
        """Convert SystemMessage to IR events."""
        data = getattr(msg, "data", {}) or {}
        ir: SessionStartEvent = {
            "type": "session_start",
            "session_id": data.get("session_id", ""),
            "agent": "claude_code",
        }
        if "model" in data:
            ir["model"] = data["model"]
        if "tools" in data:
            ir["tools"] = data["tools"]
        if "cwd" in data:
            ir["working_dir"] = data["cwd"]
        return [ir]

    @staticmethod
    def _convert_assistant(msg: Any) -> list[IREvent]:
        """Convert AssistantMessage to IR events."""
        from claude_agent_sdk import TextBlock, ToolUseBlock

        results: list[IREvent] = []
        message_id = getattr(msg, "message_id", "") or ""

        start: MessageStartEvent = {"type": "message_start", "role": "assistant"}
        if message_id:
            start["message_id"] = message_id
        results.append(start)

        full_text = ""
        for block in getattr(msg, "content", []):
            if isinstance(block, TextBlock):
                full_text += block.text
            elif isinstance(block, ToolUseBlock):
                tool_event: ToolUseEvent = {
                    "type": "tool_use",
                    "tool_use_id": block.id,
                    "tool_name": block.name,
                    "tool_input": block.input,
                }
                results.append(tool_event)

        end: MessageEndEvent = {
            "type": "message_end",
            "stop_reason": getattr(msg, "stop_reason", "") or "",
        }
        if message_id:
            end["message_id"] = message_id
        if full_text:
            end["text"] = full_text
        results.append(end)

        return results

    @staticmethod
    def _convert_user(msg: Any) -> list[IREvent]:
        """Convert UserMessage to IR events (tool results)."""
        from claude_agent_sdk import ToolResultBlock

        results: list[IREvent] = []
        content = getattr(msg, "content", None)
        if isinstance(content, list):
            for block in content:
                if isinstance(block, ToolResultBlock):
                    tr: ToolResultEvent = {
                        "type": "tool_result",
                        "tool_use_id": block.tool_use_id,
                        "content": str(block.content) if block.content else "",
                    }
                    if block.is_error:
                        tr["is_error"] = True
                    results.append(tr)
        return results

    @staticmethod
    def _convert_stream_event(msg: Any) -> list[IREvent]:
        """Convert StreamEvent to IR events (text deltas)."""
        event = getattr(msg, "event", {}) or {}
        if event.get("type") == "content_block_delta":
            delta = event.get("delta", {})
            if delta.get("type") == "text_delta":
                text = delta.get("text", "")
                if text:
                    ir: MessageDeltaEvent = {"type": "message_delta", "text": text}
                    return [ir]
        return []

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
        if "cache_read_input_tokens" in raw_usage:
            usage["cache_read_tokens"] = raw_usage["cache_read_input_tokens"]
        if "cache_creation_input_tokens" in raw_usage:
            usage["cache_creation_tokens"] = raw_usage["cache_creation_input_tokens"]

        total = raw_usage.get("input_tokens", 0) + raw_usage.get("output_tokens", 0)
        if total:
            usage["total_tokens"] = total

        usage_event: UsageEvent = {"type": "usage", "usage": usage}
        cost = getattr(msg, "total_cost_usd", None)
        if cost is not None:
            usage_event["cost_usd"] = cost
        results.append(usage_event)

        if getattr(msg, "is_error", False):
            errors = getattr(msg, "errors", None) or ["Unknown error"]
            error_msg = "; ".join(errors)
            error_event: ErrorEvent = {
                "type": "error",
                "error": error_msg,
                "is_fatal": True,
            }
            results.append(error_event)

        end: SessionEndEvent = {"type": "session_end"}
        session_id = getattr(msg, "session_id", None)
        if session_id:
            end["session_id"] = session_id
        results.append(end)

        return results


__all__ = ["ClaudeSDKProvider"]
