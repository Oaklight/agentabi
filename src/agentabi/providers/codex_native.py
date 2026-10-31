"""
agentabi - Codex CLI Native Provider

Native subprocess provider for Codex CLI.
Runs `codex exec --json <prompt>` and parses JSONL output.

The automatic-approval flag differs across Codex versions (`--approve-for-me`
replaced `--full-auto`); the provider probes `codex exec --help` once and
picks a flag the installed CLI accepts.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from collections.abc import AsyncIterator
from typing import Any, Literal, cast

from ..types.ir.capabilities import AgentCapabilities
from ..types.ir.events import (
    ErrorEvent,
    FileDiffEvent,
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

# Cached result of the one-time `codex exec --help` probe.
_AUTO_APPROVAL_FLAGS: list[str] | None = None


def _auto_approval_flags() -> list[str]:
    """Return automatic-approval flags the installed Codex CLI accepts.

    Codex renamed the non-interactive auto-approval flag: older releases
    document ``--full-auto``, current releases document ``--approve-for-me``.
    Probing the help text keeps both working without pinning a version.

    Returns:
        Flag list for ``codex exec``.
    """
    global _AUTO_APPROVAL_FLAGS
    if _AUTO_APPROVAL_FLAGS is None:
        try:
            help_text = subprocess.run(
                ["codex", "exec", "--help"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            help_text = ""
        if "--approve-for-me" in help_text:
            _AUTO_APPROVAL_FLAGS = ["--approve-for-me"]
        elif "--full-auto" in help_text:
            _AUTO_APPROVAL_FLAGS = ["--full-auto"]
        else:
            # Every version with `codex exec` supports an explicit sandbox.
            _AUTO_APPROVAL_FLAGS = ["-s", "workspace-write"]
    return list(_AUTO_APPROVAL_FLAGS)


class CodexNativeProvider:
    """Native subprocess provider for Codex CLI.

    Runs `codex exec --json <prompt>` as a subprocess and parses JSONL
    events into IR events. The auto-approval flag is chosen from the
    installed CLI's help text (``--approve-for-me`` or ``--full-auto``).

    Codex CLI JSONL event types:
    - thread.started  — session metadata (thread_id)
    - turn.started    — new assistant turn begins
    - item.started    — tool/message item begins (command_execution, mcp, etc.)
    - item.completed  — tool/message item completed (with output)
    - turn.completed  — turn ends with usage stats
    """

    def __init__(self) -> None:
        self._pending_text: list[str] = []

    @staticmethod
    def is_available() -> bool:
        """Check if `codex` CLI is available."""
        return shutil.which("codex") is not None

    def capabilities(self) -> AgentCapabilities:
        return {
            "name": "Codex",
            "agent_type": "codex",
            "supports_streaming": True,
            "supports_mcp": True,
            "supports_session_resume": True,
            "supports_system_prompt": False,
            "supports_tool_filtering": False,
            "supports_permissions": True,
            "supports_multi_turn": True,
            "supports_structured_output": True,
            "supports_ephemeral": True,
            "supports_additional_dirs": True,
            "supports_file_attachments": True,
            "transport": "subprocess",
        }

    async def stream(self, task: TaskConfig) -> AsyncIterator[IREvent]:
        """Run task via codex CLI and yield IR events."""
        from .base import collect_subprocess_errors

        cmd = self._build_command(task)
        merged_env = {**os.environ, **(task.get("env") or {})}

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
        from .base import default_run

        return await default_run(self, task)

    @staticmethod
    def _build_command(task: TaskConfig) -> list[str]:
        """Convert TaskConfig to codex CLI arguments."""
        cmd = ["codex", "exec", "--json"]

        permissions = task.get("permissions")
        level = permissions.get("level") if permissions else None
        if level == "full_auto":
            cmd.append("--dangerously-bypass-approvals-and-sandbox")
        else:
            cmd.extend(_auto_approval_flags())

        if "model" in task:
            cmd.extend(["-m", task["model"]])

        if task.get("working_dir"):
            cmd.extend(["-C", task["working_dir"]])

        # Override codex config.toml provider when env specifies a
        # custom base URL — codex ignores OPENAI_BASE_URL env var
        # in favor of its config file, so we must use -c flags to
        # override the existing provider's connection settings.
        task_env = task.get("env") or {}
        base_url = task_env.get("OPENAI_BASE_URL")
        api_key = task_env.get("OPENAI_API_KEY")
        provider = task_env.get("CODEX_PROVIDER", "openai")
        if base_url:
            cmd.extend(
                [
                    "-c",
                    f"model_provider={provider}",
                    "-c",
                    f"model_providers.{provider}.base_url={base_url}",
                ]
            )
            if api_key:
                cmd.extend(
                    [
                        "-c",
                        f"model_providers.{provider}.api_key={api_key}",
                    ]
                )

        if "output_schema" in task:
            schema = task["output_schema"]
            if isinstance(schema, dict):
                import json as _json
                import tempfile

                tmp = tempfile.NamedTemporaryFile(
                    mode="w", suffix=".json", delete=False
                )
                _json.dump(schema, tmp)
                tmp.close()
                schema = tmp.name
            cmd.extend(["--output-schema", schema])

        if task.get("ephemeral"):
            cmd.append("--ephemeral")

        for d in task.get("additional_dirs") or []:
            cmd.extend(["--add-dir", d])

        for f in task.get("files") or []:
            cmd.extend(["--image", f])

        cmd.append(task["prompt"])
        return cmd

    def _parse_event(self, raw: dict[str, Any]) -> list[IREvent]:
        """Convert a Codex CLI JSONL event to IR events."""
        event_type = raw.get("type")

        if event_type == "thread.started":
            return self._handle_thread_started(raw)
        elif event_type == "turn.started":
            return self._handle_turn_started()
        elif event_type == "item.started":
            return self._handle_item(raw, started=True)
        elif event_type == "item.completed":
            return self._handle_item(raw, started=False)
        elif event_type == "turn.completed":
            return self._handle_turn_completed(raw)
        elif event_type == "turn.failed":
            return self._handle_turn_failed(raw)
        elif event_type == "error":
            return self._handle_error(raw)
        return []

    @staticmethod
    def _handle_turn_failed(raw: dict[str, Any]) -> list[IREvent]:
        """Convert a failed turn into an error plus a session end."""
        error = raw.get("error")
        if isinstance(error, dict):
            message = error.get("message", "")
        elif error:
            message = str(error)
        else:
            message = ""
        err: ErrorEvent = {
            "type": "error",
            "error": message or "Codex turn failed",
        }
        session_end: SessionEndEvent = {"type": "session_end"}
        return [err, session_end]

    @staticmethod
    def _handle_error(raw: dict[str, Any]) -> list[IREvent]:
        """Convert a top-level error event."""
        err: ErrorEvent = {
            "type": "error",
            "error": raw.get("message", "Unknown error"),
        }
        return [err]

    @staticmethod
    def _handle_thread_started(raw: dict[str, Any]) -> list[IREvent]:
        start: SessionStartEvent = {
            "type": "session_start",
            "session_id": raw.get("thread_id", ""),
            "agent": "codex",
        }
        return [start]

    @staticmethod
    def _handle_turn_started() -> list[IREvent]:
        msg_start: MessageStartEvent = {
            "type": "message_start",
            "role": "assistant",
        }
        return [msg_start]

    def _handle_turn_completed(self, raw: dict[str, Any]) -> list[IREvent]:
        results: list[IREvent] = []

        usage_raw = raw.get("usage", {})
        usage: UsageInfo = {}
        input_tokens = usage_raw.get("input_tokens", 0)
        output_tokens = usage_raw.get("output_tokens", 0)
        cached = usage_raw.get("cached_input_tokens", 0)

        if input_tokens:
            usage["input_tokens"] = input_tokens
        if output_tokens:
            usage["output_tokens"] = output_tokens
        if cached:
            usage["cache_read_tokens"] = cached
        total = input_tokens + output_tokens
        if total:
            usage["total_tokens"] = total

        usage_event: UsageEvent = {"type": "usage", "usage": usage}
        results.append(usage_event)

        end: MessageEndEvent = {"type": "message_end", "stop_reason": "end_turn"}
        if self._pending_text:
            end["text"] = "".join(self._pending_text)
            self._pending_text = []
        results.append(end)

        session_end: SessionEndEvent = {"type": "session_end"}
        results.append(session_end)

        return results

    def _handle_item(self, raw: dict[str, Any], *, started: bool) -> list[IREvent]:
        """Convert an item.started or item.completed event to IR events."""
        item = raw.get("item", {})
        item_type = item.get("type", "")

        if item_type == "agent_message":
            return self._handle_agent_message(item, started=started)
        elif item_type == "command_execution":
            return self._handle_command(item, started=started)
        elif item_type == "file_change":
            return self._handle_file_change(item, started=started)
        elif item_type == "mcp_tool_call":
            return self._handle_mcp_tool(item, started=started)
        elif item_type == "error":
            if not started:
                err: ErrorEvent = {
                    "type": "error",
                    "error": item.get("message", "Unknown error"),
                }
                return [err]
        return []

    def _handle_agent_message(
        self, item: dict[str, Any], *, started: bool
    ) -> list[IREvent]:
        if started:
            return []
        text = item.get("text", "")
        if not text:
            return []
        self._pending_text.append(text)
        delta: MessageDeltaEvent = {
            "type": "message_delta",
            "text": text,
        }
        return [delta]

    @staticmethod
    def _handle_command(item: dict[str, Any], *, started: bool) -> list[IREvent]:
        item_id = item.get("id", "")
        if started:
            tool_use: ToolUseEvent = {
                "type": "tool_use",
                "tool_use_id": item_id,
                "tool_name": "command_execution",
                "tool_input": {"command": item.get("command", "")},
            }
            return [tool_use]
        else:
            tool_result: ToolResultEvent = {
                "type": "tool_result",
                "tool_use_id": item_id,
                "content": item.get("aggregated_output", ""),
            }
            if item.get("status") == "failed":
                tool_result["is_error"] = True
            return [tool_result]

    @staticmethod
    def _handle_file_change(item: dict[str, Any], *, started: bool) -> list[IREvent]:
        if started:
            return []
        results: list[IREvent] = []
        for change in item.get("changes", []):
            kind = change.get("kind", "update")
            action_map: dict[str, str] = {
                "add": "create",
                "delete": "delete",
                "update": "modify",
            }
            action_str = action_map.get(kind, "modify")
            action = cast(Literal["create", "modify", "delete"], action_str)
            diff_event: FileDiffEvent = {
                "type": "file_diff",
                "file_path": change.get("path", ""),
                "action": action,
            }
            results.append(diff_event)
        return results

    @staticmethod
    def _handle_mcp_tool(item: dict[str, Any], *, started: bool) -> list[IREvent]:
        item_id = item.get("id", "")
        if started:
            server = item.get("server", "")
            tool = item.get("tool", "")
            mcp_use: ToolUseEvent = {
                "type": "tool_use",
                "tool_use_id": item_id,
                "tool_name": f"mcp:{server}/{tool}",
                "tool_input": item.get("arguments", {}),
            }
            return [mcp_use]
        else:
            result_content = ""
            result_obj = item.get("result")
            if result_obj:
                content_list = result_obj.get("content", [])
                result_content = str(content_list)
            mcp_result: ToolResultEvent = {
                "type": "tool_result",
                "tool_use_id": item_id,
                "content": result_content,
            }
            error_obj = item.get("error")
            if error_obj or item.get("status") == "failed":
                mcp_result["is_error"] = True
                if isinstance(error_obj, dict):
                    mcp_result["content"] = error_obj.get("message", result_content)
            return [mcp_result]


__all__ = ["CodexNativeProvider"]
