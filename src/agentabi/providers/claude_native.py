"""
agentabi - Claude Native Provider

Our own subprocess + JSONL implementation for Claude Code CLI.
Migrated from the old ClaudeCodeAdapter.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import uuid
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Any, cast

from ..settings.registry import adapter_for
from ..types.ir.capabilities import AgentCapabilities
from ..types.ir.events import (
    ContentBlockEndEvent,
    ContentBlockStartEvent,
    ErrorEvent,
    IREvent,
    MessageDeltaEvent,
    MessageEndEvent,
    MessageStartEvent,
    PermissionRequestEvent,
    ReasoningDeltaEvent,
    SessionEndEvent,
    SessionStartEvent,
    ToolResultEvent,
    ToolUseEvent,
    UsageEvent,
    UsageInfo,
)
from ..types.ir.permissions import PermissionDecision, PermissionOption
from ..types.ir.session import SessionResult
from ..types.ir.stop_reasons import stop_reason
from ..types.ir.task import TaskConfig
from .base import PermissionHandler

_PERMISSION_LEVEL_MAP: dict[str, str] = {
    "full_auto": "bypassPermissions",
    "accept_edits": "acceptEdits",
    "plan": "plan",
    "auto": "auto",
    "dont_ask": "dontAsk",
    "default": "default",
}

_THINKING_LEVEL_MAP: dict[str, str] = {
    "low": "low",
    "medium": "medium",
    "high": "high",
    "max": "max",
}


def _inject_settings_override(cmd: list[str], task: TaskConfig) -> None:
    """Add endpoint overrides produced by the shared settings adapter.

    Claude Code ignores ``ANTHROPIC_BASE_URL`` when OAuth auth is active, so
    an endpoint override has to arrive as ``--settings``; the adapter owns
    that translation for every Claude transport.
    """
    resolved = adapter_for("claude_native").resolve(task)
    cmd.extend(resolved.argv)
    cmd.extend(resolved.extra_args)


def _allow_rule(suggestions: list[Any]) -> str | None:
    """Return the first allow ``addRules`` rule content, if any.

    ``None`` means no ``addRules`` suggestion was offered, so
    ``allow_always`` must not be shown.  An empty string means an
    ``addRules`` suggestion exists without rule content; the broker then
    caches a tool-wide rule.
    """
    for suggestion in suggestions:
        if not isinstance(suggestion, dict):
            continue
        if (
            suggestion.get("type") != "addRules"
            or suggestion.get("behavior") != "allow"
        ):
            continue
        rules = suggestion.get("rules")
        if isinstance(rules, list):
            for rule in rules:
                if isinstance(rule, dict) and rule.get("ruleContent"):
                    return str(rule["ruleContent"])
        return ""
    return None


def _permission_options(suggestions: list[Any]) -> list[PermissionOption]:
    """Build the canonical options offered for a Claude request."""
    options: list[PermissionOption] = [
        {
            "option_id": "allow_once",
            "name": "Allow once",
            "kind": "allow_once",
        }
    ]
    if _allow_rule(suggestions) is not None:
        options.append(
            {
                "option_id": "allow_always",
                "name": "Allow always",
                "kind": "allow_always",
            }
        )
    options.append(
        {
            "option_id": "reject_once",
            "name": "Reject",
            "kind": "reject_once",
        }
    )
    return options


class ClaudeNativeProvider:
    """Native subprocess + JSONL provider for Claude Code CLI.

    Runs `claude --print --output-format stream-json` as a subprocess
    and parses the JSONL output into IR events.

    When a permission handler is attached, the provider switches to the
    SDK control protocol on stdin/stdout (``--input-format stream-json``,
    ``--permission-prompt-tool stdio``).  Without a handler it keeps the
    argv-prompt path, so the plain ``Session`` API is unchanged.
    """

    def __init__(self) -> None:
        self._permission_handler: PermissionHandler | None = None
        self._pending_permissions: dict[str, asyncio.Task[None]] = {}

    def set_permission_handler(self, handler: PermissionHandler | None) -> None:
        """Attach or clear the handler called for ``can_use_tool`` requests."""
        self._permission_handler = handler

    @staticmethod
    def is_available() -> bool:
        """Check if `claude` CLI is available."""
        return shutil.which("claude") is not None

    def capabilities(self) -> AgentCapabilities:
        """Declare Claude Code capabilities."""
        return {
            "name": "Claude Code",
            "agent_type": "claude_code",
            "supports_streaming": True,
            "supports_mcp": True,
            "supports_session_resume": True,
            "supports_system_prompt": True,
            "supports_tool_filtering": True,
            "supports_file_diffs": False,
            "supports_permissions": True,
            "supports_thinking": True,
            "supports_structured_output": True,
            "supports_ephemeral": True,
            "supports_additional_dirs": True,
            "supports_file_attachments": True,
            "supports_multi_turn": True,
            "transport": "subprocess",
            "supported_settings": sorted(adapter_for("claude_native").supported),
            "settings_mechanism": adapter_for("claude_native").mechanism,
        }

    async def stream(self, task: TaskConfig) -> AsyncIterator[IREvent]:
        """Run task and yield IR events."""
        from .base import collect_subprocess_errors

        permission = self._permission_handler is not None
        cmd = self._build_command(task, permission=permission)
        proc = await self._spawn(cmd, task, permission)
        timeout = task.get("timeout")
        timed_out = {"value": False}
        timeout_task: asyncio.Task[None] | None = None
        if timeout:
            timeout_task = asyncio.create_task(
                self._kill_after_timeout(proc, float(timeout), timed_out)
            )

        try:
            if permission:
                await self._send_user_message(proc, task["prompt"])
            async for event in self._stream_events(proc, permission):
                yield event
            await self._finish_process(proc, permission)
            for err in await collect_subprocess_errors(
                proc,
                timed_out=timed_out["value"],
                timeout_seconds=timeout,
            ):
                yield err
        except asyncio.CancelledError:
            if permission and self._pending_permissions:
                with suppress(Exception):
                    await self._send_interrupt(proc)
                    # Give the CLI a moment to emit control_cancel_request and
                    # its result before the process is reaped.
                    await asyncio.sleep(0.1)
            raise
        finally:
            self._cancel_pending_permissions()
            if timeout_task and not timeout_task.done():
                timeout_task.cancel()
            await self._reap(proc)

    @staticmethod
    async def _spawn(
        cmd: list[str], task: TaskConfig, permission: bool
    ) -> asyncio.subprocess.Process:
        task_env = task.get("env") or {}
        settings_env = adapter_for("claude_native").resolve(task).env
        return await asyncio.create_subprocess_exec(
            *cmd,
            stdin=(
                asyncio.subprocess.PIPE if permission else asyncio.subprocess.DEVNULL
            ),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, **task_env, **settings_env},
            cwd=task.get("working_dir"),
        )

    @staticmethod
    async def _kill_after_timeout(
        proc: asyncio.subprocess.Process, seconds: float, state: dict[str, bool]
    ) -> None:
        await asyncio.sleep(seconds)
        if proc.returncode is None:
            state["value"] = True
            proc.kill()

    async def _stream_events(
        self, proc: asyncio.subprocess.Process, permission: bool
    ) -> AsyncIterator[IREvent]:
        """Read stdout and yield IR events, dispatching control messages."""
        assert proc.stdout is not None
        async for line in proc.stdout:
            raw = self._decode_line(line)
            if raw is None:
                continue
            if permission:
                kind, control_id, control_event = self._classify_control(raw)
                if kind is not None:
                    if kind == "permission" and control_event is not None:
                        yield control_event
                        pending = asyncio.create_task(
                            self._answer_permission(proc, control_id, control_event)
                        )
                        self._pending_permissions[control_id] = pending
                    elif kind == "cancel":
                        self._cancel_pending(control_id)
                    continue
            for event in self._parse_event(raw):
                yield event
            if permission and raw.get("type") == "result":
                # One prompt per turn: the CLI may keep reading stdin for
                # more user messages, so stop and close stdin below.
                break

    @staticmethod
    def _decode_line(line: bytes) -> dict[str, Any] | None:
        decoded = line.decode().rstrip("\n").rstrip("\r")
        if not decoded:
            return None
        try:
            raw = json.loads(decoded)
        except json.JSONDecodeError:
            return None
        return raw if isinstance(raw, dict) else None

    @staticmethod
    async def _finish_process(
        proc: asyncio.subprocess.Process, permission: bool
    ) -> None:
        if not permission:
            await proc.wait()
            return
        await ClaudeNativeProvider._close_stdin(proc)
        try:
            await asyncio.wait_for(proc.wait(), timeout=10)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()

    @staticmethod
    async def _reap(proc: asyncio.subprocess.Process) -> None:
        if proc.returncode is not None:
            return
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

    # ========== Private: control protocol ==========

    @staticmethod
    async def _send_user_message(proc: asyncio.subprocess.Process, prompt: str) -> None:
        """Write the prompt as a stream-json user message."""
        if proc.stdin is None:  # pragma: no cover - PIPE is always set
            return
        payload = {
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": prompt}],
            },
        }
        proc.stdin.write((json.dumps(payload) + "\n").encode())
        await proc.stdin.drain()

    @staticmethod
    async def _close_stdin(proc: asyncio.subprocess.Process) -> None:
        if proc.stdin is None:
            return
        with suppress(Exception):
            proc.stdin.close()
            await proc.stdin.wait_closed()

    @staticmethod
    def _classify_control(
        raw: dict[str, Any],
    ) -> tuple[str | None, str, PermissionRequestEvent | None]:
        """Classify a control-protocol message before IR parsing.

        Returns:
            ``(kind, control_request_id, event)`` where ``kind`` is
            ``"permission"``, ``"cancel"``, ``"response"``, ``"other"``, or
            ``None`` for an ordinary event.
        """
        msg_type = raw.get("type")
        if msg_type == "control_response":
            return "response", str(raw.get("request_id") or ""), None
        if msg_type == "control_cancel_request":
            request_id = raw.get("request_id")
            if request_id is None:
                request = raw.get("request")
                if isinstance(request, dict):
                    request_id = request.get("request_id")
            return "cancel", str(request_id or ""), None
        if msg_type != "control_request":
            return None, "", None
        control_id = str(raw.get("request_id") or "")
        request = raw.get("request")
        if not isinstance(request, dict):
            return "other", control_id, None
        if request.get("subtype") != "can_use_tool":
            return "other", control_id, None
        return (
            "permission",
            control_id,
            ClaudeNativeProvider._permission_event(raw, request),
        )

    @staticmethod
    def _permission_event(
        raw: dict[str, Any], request: dict[str, Any]
    ) -> PermissionRequestEvent:
        """Translate a ``can_use_tool`` control request to an IR event."""
        tool_use_id = str(request.get("tool_use_id") or "")
        request_id = tool_use_id or f"perm_{uuid.uuid4().hex}"
        tool_name = str(
            request.get("tool_name") or request.get("display_name") or "tool"
        )
        suggestions = request.get("permission_suggestions")
        if not isinstance(suggestions, list):
            suggestions = []

        event: PermissionRequestEvent = {
            "type": "permission_request",
            "request_id": request_id,
            "tool_name": tool_name,
            "tool_use_id": tool_use_id or request_id,
            "options": _permission_options(suggestions),
        }
        tool_input = request.get("input")
        if isinstance(tool_input, dict):
            event["tool_input"] = tool_input
        description = request.get("description") or request.get("display_name")
        if description:
            event["description"] = str(description)
        if suggestions:
            event["suggestions"] = suggestions
        event["raw"] = raw
        return event

    async def _answer_permission(
        self,
        proc: asyncio.subprocess.Process,
        control_id: str,
        event: PermissionRequestEvent,
    ) -> None:
        """Ask the handler and write the control response.

        A cancelled decision sends an ``interrupt`` control request instead of
        a response: the CLI then cancels the pending request itself.
        """
        handler = self._permission_handler
        if handler is None:  # pragma: no cover - only called with a handler
            return
        try:
            decision = await handler(event)
        except asyncio.CancelledError:
            return
        except Exception:  # noqa: BLE001 - fail closed
            decision = PermissionDecision(option_id="reject_once", outcome="selected")
        try:
            if decision.get("outcome") == "cancelled":
                await self._send_interrupt(proc)
                return
            await self._write_control_response(proc, control_id, event, decision)
        finally:
            self._pending_permissions.pop(control_id, None)

    @staticmethod
    async def _write_control_response(
        proc: asyncio.subprocess.Process,
        control_id: str,
        event: PermissionRequestEvent,
        decision: PermissionDecision,
    ) -> None:
        if proc.stdin is None:  # pragma: no cover - PIPE is always set
            return
        option_id = decision.get("option_id")
        if option_id in ("allow_once", "allow_always"):
            response: dict[str, Any] = {
                "behavior": "allow",
                "updatedInput": event.get("tool_input", {}),
            }
        else:
            response = {
                "behavior": "deny",
                "message": "denied by agentabi",
                "interrupt": False,
            }
        payload = {
            "type": "control_response",
            "response": {
                "subtype": "success",
                "request_id": control_id,
                "response": response,
            },
        }
        proc.stdin.write((json.dumps(payload) + "\n").encode())
        await proc.stdin.drain()

    @staticmethod
    async def _send_interrupt(proc: asyncio.subprocess.Process) -> None:
        if proc.stdin is None:  # pragma: no cover - PIPE is always set
            return
        payload = {
            "type": "control_request",
            "request_id": f"interrupt_{uuid.uuid4().hex}",
            "request": {"subtype": "interrupt"},
        }
        proc.stdin.write((json.dumps(payload) + "\n").encode())
        await proc.stdin.drain()

    def _cancel_pending(self, control_id: str) -> None:
        """Cancel one pending permission task after a control cancel."""
        task = self._pending_permissions.pop(control_id, None)
        if task is not None and not task.done():
            task.cancel()

    def _cancel_pending_permissions(self) -> None:
        for task in list(self._pending_permissions.values()):
            if not task.done():
                task.cancel()
        self._pending_permissions.clear()

    # ========== Private: command building ==========

    @staticmethod
    def _build_command(task: TaskConfig, *, permission: bool = False) -> list[str]:
        """Convert TaskConfig to `claude` CLI arguments.

        With *permission*, use the SDK control protocol and leave the prompt
        for stdin.  Without it, keep the argv prompt unchanged.
        """
        cmd = ["claude", "--print", "--output-format", "stream-json", "--verbose"]

        if "model" in task:
            cmd.extend(["--model", task["model"]])

        if "system_prompt" in task:
            cmd.extend(["--system-prompt", task["system_prompt"]])
        if "append_system_prompt" in task:
            cmd.extend(["--append-system-prompt", task["append_system_prompt"]])

        if "max_turns" in task:
            cmd.extend(["--max-turns", str(task["max_turns"])])

        ClaudeNativeProvider._add_session_flags(cmd, task)
        ClaudeNativeProvider._add_permission_flags(cmd, task)
        ClaudeNativeProvider._add_tool_flags(cmd, task)

        if permission:
            ClaudeNativeProvider._add_permission_transport(cmd, task)

        if "mcp_config" in task:
            cmd.extend(["--mcp-config", task["mcp_config"]])

        cmd.append("--include-partial-messages")
        ClaudeNativeProvider._add_extension_flags(cmd, task)

        _inject_settings_override(cmd, task)

        if not permission:
            cmd.append("--")
            cmd.append(task["prompt"])

        return cmd

    @staticmethod
    def _add_permission_transport(cmd: list[str], task: TaskConfig) -> None:
        """Add the SDK control-protocol flags and the prompt-on-stdin mode."""
        cmd.extend(
            [
                "--input-format",
                "stream-json",
                "--permission-prompt-tool",
                "stdio",
                "--permission-prompts",
                "host",
            ]
        )
        if not (task.get("permissions") or {}).get("level"):
            # The measured control-protocol invocation uses manual mode; the
            # CLI default also prompts, but be explicit.
            cmd.extend(["--permission-mode", "manual"])

    @staticmethod
    def _add_extension_flags(cmd: list[str], task: TaskConfig) -> None:
        """Append extension, thinking, schema, and attachment flags."""
        extensions = task.get("agent_extensions", {})
        if "max_budget_usd" in extensions:
            cmd.extend(["--max-budget-usd", str(extensions["max_budget_usd"])])
        if extensions.get("continue_session"):
            cmd.append("--continue")

        effort = _THINKING_LEVEL_MAP.get(task.get("thinking_level", ""))
        if effort:
            cmd.extend(["--effort", effort])

        if "output_schema" in task:
            schema = task["output_schema"]
            if isinstance(schema, dict):
                import json as _json

                schema = _json.dumps(schema)
            cmd.extend(["--json-schema", schema])

        if task.get("ephemeral"):
            cmd.append("--no-session-persistence")

        for d in task.get("additional_dirs") or []:
            cmd.extend(["--add-dir", d])

        for f in task.get("files") or []:
            cmd.extend(["--file", f])

    @staticmethod
    def _add_session_flags(cmd: list[str], task: TaskConfig) -> None:
        """Append session/resume flags to command."""
        if "session_id" in task:
            if task.get("resume"):
                cmd.extend(["--resume", task["session_id"]])
            else:
                cmd.extend(["--session-id", task["session_id"]])
        elif task.get("resume"):
            cmd.append("--continue")

    @staticmethod
    def _add_permission_flags(cmd: list[str], task: TaskConfig) -> None:
        """Append permission mode flag to command."""
        permissions = task.get("permissions")
        if permissions:
            level = permissions.get("level")
            cli_mode = _PERMISSION_LEVEL_MAP.get(level or "")
            if cli_mode:
                cmd.extend(["--permission-mode", cli_mode])

    @staticmethod
    def _add_tool_flags(cmd: list[str], task: TaskConfig) -> None:
        """Append tool allow/deny flags to command."""
        permissions = task.get("permissions")

        if "allowed_tools" in task:
            cmd.extend(["--allowed-tools", ",".join(task["allowed_tools"])])
        elif permissions and "allowed_tools" in permissions:
            cmd.extend(["--allowed-tools", ",".join(permissions["allowed_tools"])])

        if "disallowed_tools" in task:
            cmd.extend(["--disallowed-tools", ",".join(task["disallowed_tools"])])
        elif permissions and "disallowed_tools" in permissions:
            cmd.extend(
                ["--disallowed-tools", ",".join(permissions["disallowed_tools"])]
            )

    # ========== Private: event parsing ==========

    @staticmethod
    def _parse_event(event: dict[str, Any]) -> list[IREvent]:
        """Convert a Claude Code JSONL event to IR event(s)."""
        event_type = event.get("type")

        if event_type == "system":
            return ClaudeNativeProvider._handle_system(event)
        elif event_type == "assistant":
            return ClaudeNativeProvider._handle_assistant(event)
        elif event_type == "user":
            return ClaudeNativeProvider._handle_user(event)
        elif event_type == "stream_event":
            return ClaudeNativeProvider._handle_stream_event(event)
        elif event_type == "result":
            return ClaudeNativeProvider._handle_result(event)
        return []

    @staticmethod
    def _handle_system(event: dict[str, Any]) -> list[IREvent]:
        # Claude emits one ``system`` row per state change (``init``,
        # ``status``, ...).  Only the init row starts a session.
        if event.get("subtype", "init") not in (None, "init"):
            return []
        ir: SessionStartEvent = {
            "type": "session_start",
            "session_id": event.get("session_id", ""),
            "agent": "claude_code",
        }
        if "model" in event:
            ir["model"] = event["model"]
        if "tools" in event:
            ir["tools"] = event["tools"]
        if "cwd" in event:
            ir["working_dir"] = event["cwd"]
        return [ir]

    @staticmethod
    def _handle_assistant(event: dict[str, Any]) -> list[IREvent]:
        results: list[IREvent] = []
        message = event.get("message", {})
        content = message.get("content", [])
        message_id = message.get("id", "")

        start: MessageStartEvent = {"type": "message_start", "role": "assistant"}
        if message_id:
            start["message_id"] = message_id
        results.append(start)

        full_text = ""
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "text":
                full_text += block.get("text", "")
            elif block_type == "thinking":
                thinking_text = block.get("thinking", "")
                if thinking_text:
                    rd: ReasoningDeltaEvent = {
                        "type": "reasoning_delta",
                        "reasoning": thinking_text,
                    }
                    results.append(rd)
            elif block_type == "tool_use":
                tool_event: ToolUseEvent = {
                    "type": "tool_use",
                    "tool_use_id": block.get("id", ""),
                    "tool_name": block.get("name", ""),
                    "tool_input": block.get("input", {}),
                }
                results.append(tool_event)

        end: MessageEndEvent = {
            "type": "message_end",
            "stop_reason": stop_reason(message.get("stop_reason"), default="end_turn"),
        }
        if message_id:
            end["message_id"] = message_id
        if full_text:
            end["text"] = full_text
        results.append(end)

        return results

    @staticmethod
    def _handle_user(event: dict[str, Any]) -> list[IREvent]:
        results: list[IREvent] = []
        message = event.get("message", {})
        content = message.get("content", [])

        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_result":
                tool_result: ToolResultEvent = {
                    "type": "tool_result",
                    "tool_use_id": block.get("tool_use_id", ""),
                    "content": str(block.get("content", "")),
                }
                if block.get("is_error"):
                    tool_result["is_error"] = True
                results.append(tool_result)

        tool_use_result = event.get("tool_use_result")
        if isinstance(tool_use_result, dict) and results:
            duration = tool_use_result.get("durationMs")
            if duration is not None and results[-1].get("type") == "tool_result":
                last = cast(ToolResultEvent, results[-1])
                last["duration_ms"] = duration

        return results

    @staticmethod
    def _handle_stream_event(event: dict[str, Any]) -> list[IREvent]:
        inner = event.get("event", {})
        inner_type = inner.get("type", "")

        if inner_type == "content_block_delta":
            delta = inner.get("delta", {})
            delta_type = delta.get("type", "")
            if delta_type == "text_delta":
                text = delta.get("text", "")
                if text:
                    ir: MessageDeltaEvent = {"type": "message_delta", "text": text}
                    return [ir]
            elif delta_type == "thinking_delta":
                thinking = delta.get("thinking", "")
                if thinking:
                    rd: ReasoningDeltaEvent = {
                        "type": "reasoning_delta",
                        "reasoning": thinking,
                        "block_index": inner.get("index", 0),
                    }
                    return [rd]
        elif inner_type == "content_block_start":
            block = inner.get("content_block", {})
            block_type = block.get("type", "text")
            cbs: ContentBlockStartEvent = {
                "type": "content_block_start",
                "block_index": inner.get("index", 0),
                "block_type": block_type,
            }
            return [cbs]
        elif inner_type == "content_block_stop":
            cbe: ContentBlockEndEvent = {
                "type": "content_block_end",
                "block_index": inner.get("index", 0),
            }
            return [cbe]
        return []

    @staticmethod
    def _handle_result(event: dict[str, Any]) -> list[IREvent]:
        results: list[IREvent] = []

        raw_usage = event.get("usage", {})
        usage: UsageInfo = {}
        if "input_tokens" in raw_usage:
            usage["input_tokens"] = raw_usage["input_tokens"]
        if "output_tokens" in raw_usage:
            usage["output_tokens"] = raw_usage["output_tokens"]
        if "cache_read_input_tokens" in raw_usage:
            usage["cache_read_tokens"] = raw_usage["cache_read_input_tokens"]
        if "cache_creation_input_tokens" in raw_usage:
            usage["cache_creation_tokens"] = raw_usage["cache_creation_input_tokens"]
        thinking_details = raw_usage.get("output_tokens_details", {})
        if thinking_details:
            reasoning_tokens = thinking_details.get("thinking_tokens", 0)
            if reasoning_tokens:
                usage["reasoning_tokens"] = reasoning_tokens

        total = raw_usage.get("input_tokens", 0) + raw_usage.get("output_tokens", 0)
        if total:
            usage["total_tokens"] = total

        usage_event: UsageEvent = {"type": "usage", "usage": usage}
        if "total_cost_usd" in event:
            usage_event["cost_usd"] = event["total_cost_usd"]
        results.append(usage_event)

        if event.get("is_error"):
            error_msg = "; ".join(event.get("errors", ["Unknown error"]))
            error_event: ErrorEvent = {
                "type": "error",
                "error": error_msg,
                "is_fatal": True,
            }
            results.append(error_event)

        end: SessionEndEvent = {"type": "session_end"}
        if "session_id" in event:
            end["session_id"] = event["session_id"]
        results.append(end)

        return results


__all__ = ["ClaudeNativeProvider"]
