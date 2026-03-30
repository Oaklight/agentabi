"""
agentabi - Codex SDK Provider

Wraps codex-sdk-python behind the Provider protocol.
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Literal, cast

from ..types.ir.capabilities import AgentCapabilities
from ..types.ir.events import (
    ErrorEvent,
    FileDiffEvent,
    IREvent,
    MessageDeltaEvent,
    MessageEndEvent,
    MessageStartEvent,
    SessionStartEvent,
    ToolResultEvent,
    ToolUseEvent,
    UsageEvent,
    UsageInfo,
)
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
            "supports_mcp": True,
            "supports_session_resume": True,
            "supports_system_prompt": True,
            "supports_tool_filtering": False,
            "supports_permissions": True,
            "supports_multi_turn": True,
        }

    async def stream(self, task: TaskConfig) -> AsyncIterator[IREvent]:
        """Run task via codex-sdk-python and yield IR events."""
        from codex_sdk import Codex, CodexOptions, ThreadOptions

        codex_opts = CodexOptions()
        thread_opts = ThreadOptions(
            model=task.get("model"),
            working_directory=task.get("working_dir"),
        )

        permissions = task.get("permissions")
        if permissions:
            level = permissions.get("level")
            if level == "full_auto":
                thread_opts.approval_policy = "never"

        if "system_prompt" in task:
            # Codex uses model_instructions_file, not inline system prompt.
            # Write to a temp file if system_prompt is provided.
            import os
            import tempfile

            fd, path = tempfile.mkstemp(suffix=".md", prefix="agentabi_")
            try:
                with os.fdopen(fd, "w") as f:
                    f.write(task["system_prompt"])
                thread_opts.model_instructions_file = path
            except Exception:
                os.close(fd)

        codex = Codex(options=codex_opts)
        thread = codex.start_thread(options=thread_opts)

        async for event in thread.run_streamed_events(task["prompt"]):
            for ir_event in self._convert(event):
                yield ir_event

    async def run(self, task: TaskConfig) -> SessionResult:
        from .base import default_run

        return await default_run(self, task)

    @staticmethod
    def _convert(event: Any) -> list[IREvent]:
        """Convert a codex-sdk ThreadEvent to IR events."""
        from codex_sdk import (
            ItemCompletedEvent,
            ItemStartedEvent,
            ThreadErrorEvent,
            ThreadStartedEvent,
            TurnCompletedEvent,
            TurnFailedEvent,
            TurnStartedEvent,
        )

        if isinstance(event, ThreadStartedEvent):
            return CodexSDKProvider._convert_thread_started(event)
        elif isinstance(event, TurnStartedEvent):
            return CodexSDKProvider._convert_turn_started()
        elif isinstance(event, TurnCompletedEvent):
            return CodexSDKProvider._convert_turn_completed(event)
        elif isinstance(event, TurnFailedEvent):
            return CodexSDKProvider._convert_turn_failed(event)
        elif isinstance(event, ItemStartedEvent):
            return CodexSDKProvider._convert_item(event.item, started=True)
        elif isinstance(event, ItemCompletedEvent):
            return CodexSDKProvider._convert_item(event.item, started=False)
        elif isinstance(event, ThreadErrorEvent):
            err: ErrorEvent = {
                "type": "error",
                "error": event.message,
                "is_fatal": True,
            }
            return [err]
        return []

    @staticmethod
    def _convert_thread_started(event: Any) -> list[IREvent]:
        ir: SessionStartEvent = {
            "type": "session_start",
            "session_id": event.thread_id,
            "agent": "codex",
        }
        return [ir]

    @staticmethod
    def _convert_turn_started() -> list[IREvent]:
        start: MessageStartEvent = {
            "type": "message_start",
            "role": "assistant",
        }
        return [start]

    @staticmethod
    def _convert_turn_completed(event: Any) -> list[IREvent]:
        results: list[IREvent] = []
        usage_obj = getattr(event, "usage", None)
        usage: UsageInfo = {}
        if usage_obj:
            usage["input_tokens"] = getattr(usage_obj, "input_tokens", 0)
            usage["output_tokens"] = getattr(usage_obj, "output_tokens", 0)
            cached = getattr(usage_obj, "cached_input_tokens", 0)
            if cached:
                usage["cache_read_tokens"] = cached
            total = usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
            if total:
                usage["total_tokens"] = total

        usage_event: UsageEvent = {"type": "usage", "usage": usage}
        results.append(usage_event)

        end: MessageEndEvent = {"type": "message_end", "stop_reason": "end_turn"}
        results.append(end)
        return results

    @staticmethod
    def _convert_turn_failed(event: Any) -> list[IREvent]:
        error_obj = getattr(event, "error", None)
        msg = (
            getattr(error_obj, "message", "Unknown error")
            if error_obj
            else "Unknown error"
        )
        err: ErrorEvent = {"type": "error", "error": msg, "is_fatal": True}
        return [err]

    @staticmethod
    def _convert_item(item: Any, *, started: bool) -> list[IREvent]:
        """Convert a ThreadItem to IR events."""
        from codex_sdk import (
            AgentMessageItem,
            CommandExecutionItem,
            ErrorItem,
            FileChangeItem,
            McpToolCallItem,
        )

        if isinstance(item, AgentMessageItem):
            if not started:
                # Emit text on completion
                delta: MessageDeltaEvent = {
                    "type": "message_delta",
                    "text": item.text,
                }
                return [delta]
        elif isinstance(item, CommandExecutionItem):
            if started:
                tool_use: ToolUseEvent = {
                    "type": "tool_use",
                    "tool_use_id": item.id,
                    "tool_name": "command_execution",
                    "tool_input": {"command": item.command},
                }
                return [tool_use]
            else:
                tool_result: ToolResultEvent = {
                    "type": "tool_result",
                    "tool_use_id": item.id,
                    "content": item.aggregated_output,
                }
                if item.status == "failed":
                    tool_result["is_error"] = True
                return [tool_result]
        elif isinstance(item, FileChangeItem):
            if not started:
                results: list[IREvent] = []
                for change in getattr(item, "changes", []):
                    kind = getattr(change, "kind", "update")
                    action_map: dict[str, str] = {
                        "add": "create",
                        "delete": "delete",
                        "update": "modify",
                    }
                    action_str = action_map.get(kind, "modify")
                    action = cast(
                        Literal["create", "modify", "delete"],
                        action_str,
                    )
                    diff_event: FileDiffEvent = {
                        "type": "file_diff",
                        "file_path": change.path,
                        "action": action,
                    }
                    results.append(diff_event)
                return results
        elif isinstance(item, McpToolCallItem):
            if started:
                mcp_use: ToolUseEvent = {
                    "type": "tool_use",
                    "tool_use_id": item.id,
                    "tool_name": f"mcp:{item.server}/{item.tool}",
                    "tool_input": item.arguments or {},
                }
                return [mcp_use]
            else:
                result_content = ""
                result_obj = getattr(item, "result", None)
                if result_obj:
                    content_list = getattr(result_obj, "content", [])
                    result_content = str(content_list)
                error_obj = getattr(item, "error", None)
                mcp_result: ToolResultEvent = {
                    "type": "tool_result",
                    "tool_use_id": item.id,
                    "content": result_content,
                }
                if error_obj or item.status == "failed":
                    mcp_result["is_error"] = True
                    if error_obj:
                        mcp_result["content"] = getattr(
                            error_obj, "message", result_content
                        )
                return [mcp_result]
        elif isinstance(item, ErrorItem):
            err: ErrorEvent = {
                "type": "error",
                "error": item.message,
            }
            return [err]
        return []


__all__ = ["CodexSDKProvider"]
