"""
agentabi - Antigravity CLI (agy) Native Provider

Native subprocess provider for the agy CLI (Google's agentic coding CLI).
Runs `agy --output-format stream-json [options] -p <prompt>` and parses JSONL output.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from collections.abc import AsyncIterator
from typing import Any

from ..settings.registry import adapter_for
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
from ..types.ir.stop_reasons import stop_reason
from ..types.ir.task import TaskConfig
from .base import collect_subprocess_errors


class AgyNativeProvider:
    """Native subprocess provider for Antigravity CLI (agy).

    Runs `agy --output-format stream-json [options] -p <prompt>` as a
    subprocess and parses stream-json events into IR events.

    agy stream-json event types:
    - event: "init"        — session metadata (conversation_id, cwd, tools)
    - event: "step_update" — step progress with step_type variants:
        - "user_input"      — user input step (skipped)
        - "agent_response"  — assistant response with text_delta and usage
        - "tool"            — tool call (agy 1.5 and later): ACTIVE, then
                              DONE or ERROR, with tool_info.
        - "tool_use"        — tool invocation in earlier builds
        - "checkpoint"      — checkpoint marker (skipped)
        - "error_message"   — error during step (skipped, errors in result)
    - event: "result"      — final result with status, response, usage
    """

    def __init__(self) -> None:
        # agy streams one agent_response step as several step_update events
        # (ACTIVE text, then DONE with trailing text and usage). Track the
        # current step so the whole response is one message.
        self._step_index: int | None = None
        self._step_text: list[str] = []
        self._step_open = False
        # Tool steps share one step_index across ACTIVE/DONE/ERROR, so the
        # index marks a call that already emitted its tool_use.
        self._tool_steps: set[int] = set()
        # A tool DONE with no ``tool_info.output`` is what agy 1.5 emits
        # when headless mode auto-denies the call. The denial reason is
        # only reported in the later ``result`` event, so hold these
        # results until then.
        self._pending_tool_results: list[str] = []

    @staticmethod
    def is_available() -> bool:
        """Check if `agy` CLI is available."""
        return shutil.which("agy") is not None

    def capabilities(self) -> AgentCapabilities:
        """Declare Antigravity capabilities."""
        return {
            "name": "Antigravity",
            "agent_type": "agy",
            "supports_streaming": True,
            "supports_mcp": True,
            "supports_session_resume": True,
            "supports_system_prompt": False,
            "supports_tool_filtering": False,
            "supports_permissions": True,
            "supports_structured_output": True,
            "supports_additional_dirs": True,
            "supports_multi_turn": True,
            # The provider emits no file_diff event; agy reports edits as
            # tool results.
            "supports_file_diffs": False,
            # Measured with agy 1.5.5: a print run persists its
            # conversation (resuming one with --conversation restored the
            # prior turn) and `agy --help` lists no flag that disables
            # session persistence.
            "supports_ephemeral": False,
            # Measured with agy 1.5.5: `agy --help` lists no flag that
            # accepts a file or image attachment; -p takes a text prompt.
            "supports_file_attachments": False,
            "transport": "subprocess",
        }

    async def stream(self, task: TaskConfig) -> AsyncIterator[IREvent]:
        """Run task via agy CLI and yield IR events."""
        self._reset_state()
        cmd = self._build_command(task)
        merged_env = self._build_env(task)

        timeout = task.get("timeout")
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
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
            for event in self._flush_pending_tool_results(denied_actions=[]):
                yield event
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
        """Run task and return aggregated result."""
        from .base import default_run

        return await default_run(self, task)

    # ========== Private: environment ==========

    @staticmethod
    def _build_env(task: TaskConfig) -> dict[str, str]:
        """Build merged environment with agy-specific variable mapping.

        Maps generic OPENAI_BASE_URL / OPENAI_API_KEY to agy-specific
        equivalents (GOOGLE_GEMINI_BASE_URL / GEMINI_API_KEY) when the
        agy-native env vars are not already set.
        """
        task_env = task.get("env") or {}
        merged_env = {**os.environ, **task_env}
        merged_env.update(adapter_for("agy").resolve(task).env)
        return merged_env

    # ========== Private: command building ==========

    @staticmethod
    def _build_command(task: TaskConfig) -> list[str]:
        """Convert TaskConfig to agy CLI arguments."""
        cmd = ["agy", "--output-format", "stream-json"]

        settings = AgyNativeProvider._settings_arg(task)

        # `--model` is validated against agy's own catalog even when
        # --settings names a gateway model (measured with agy 1.5.5), and
        # --model wins over the settings model. A model supplied through
        # settings must therefore not also go to --model.
        if "model" in task and not AgyNativeProvider._settings_names_model(settings):
            cmd.extend(["--model", task["model"]])

        AgyNativeProvider._add_permission_flags(cmd, task)
        AgyNativeProvider._add_session_flags(cmd, task)

        timeout = task.get("timeout")
        if timeout is not None and timeout > 0:
            cmd.extend(["--print-timeout", f"{int(timeout)}s"])

        AgyNativeProvider._add_extension_flags(cmd, task)

        if settings is not None:
            cmd.extend(["--settings", settings])

        cmd.extend(["-p", task["prompt"]])
        return cmd

    @staticmethod
    def _settings_arg(task: TaskConfig) -> str | None:
        """Return the value for ``--settings`` from task extensions.

        ``agent_extensions.agy.settings`` may be a dict (serialized to a
        JSON object) or a string (passed through unchanged, so agy can
        read it as a file path). Gateway-only models are named here, e.g.
        ``{"agy": {"settings": {"model": "qwen3.8-27b"}}}``.
        """
        ext = task.get("agent_extensions") or {}
        agy_ext = ext.get("agy")
        if not isinstance(agy_ext, dict):
            return None
        settings = agy_ext.get("settings")
        if settings is None:
            return None
        if isinstance(settings, str):
            return settings
        if isinstance(settings, dict):
            return json.dumps(settings)
        raise ValueError(
            "agent_extensions.agy.settings must be a dict or a JSON string"
        )

    @staticmethod
    def _settings_names_model(settings: str | None) -> bool:
        """Return True when the ``--settings`` JSON object sets a model."""
        if not settings:
            return False
        try:
            data = json.loads(settings)
        except json.JSONDecodeError:
            # A file path; agy reads the file, so the model is unknown here.
            return False
        return isinstance(data, dict) and bool(data.get("model"))

    @staticmethod
    def _add_permission_flags(cmd: list[str], task: TaskConfig) -> None:
        """Append permission/mode flags."""
        permissions = task.get("permissions")
        if permissions:
            level = permissions.get("level")
            if level == "full_auto":
                cmd.append("--dangerously-skip-permissions")
            elif level == "accept_edits":
                cmd.extend(["--mode", "accept-edits"])
            elif level == "plan":
                cmd.extend(["--mode", "plan"])

    @staticmethod
    def _add_session_flags(cmd: list[str], task: TaskConfig) -> None:
        """Append session resume flags."""
        if "session_id" in task and task.get("resume"):
            cmd.extend(["--conversation", task["session_id"]])
        elif task.get("resume"):
            cmd.append("--continue")

    @staticmethod
    def _add_extension_flags(cmd: list[str], task: TaskConfig) -> None:
        """Append agent-specific extension flags."""
        ext = task.get("agent_extensions") or {}

        if "effort" in ext:
            cmd.extend(["--effort", ext["effort"]])

        if ext.get("sandbox"):
            cmd.append("--sandbox")

        if ext.get("disable_slash_commands"):
            cmd.append("--disable-slash-commands")

        if "agent" in ext:
            cmd.extend(["--agent", ext["agent"]])

        schema = task.get("output_schema") or ext.get("json_schema")
        if schema:
            if isinstance(schema, dict):
                import json as _json

                schema = _json.dumps(schema)
            cmd.extend(["--json-schema", schema])

        for d in task.get("additional_dirs") or ext.get("add_dirs") or []:
            cmd.extend(["--add-dir", d])

    # ========== Private: event parsing ==========

    def _reset_state(self) -> None:
        """Clear per-run parsing state so a reused provider starts fresh."""
        self._step_index = None
        self._step_text = []
        self._step_open = False
        self._tool_steps.clear()
        self._pending_tool_results.clear()

    def _flush_pending_tool_results(
        self, *, denied_actions: list[Any]
    ) -> list[IREvent]:
        """Emit tool results held back because agy omitted their output.

        A denial is reported only as ``denied_actions`` on the final
        ``result`` event, so the last held-back result becomes an error
        carrying the reason; any earlier one stays an empty success.
        """
        pending = self._pending_tool_results
        self._pending_tool_results = []
        results: list[IREvent] = []
        for index, tool_id in enumerate(pending):
            tool_result: ToolResultEvent = {
                "type": "tool_result",
                "tool_use_id": tool_id,
                "content": "",
            }
            if denied_actions and index == len(pending) - 1:
                tool_result["content"] = AgyNativeProvider._denial_message(
                    denied_actions
                )
                tool_result["is_error"] = True
            results.append(tool_result)
        return results

    @staticmethod
    def _denial_message(denied_actions: list[Any]) -> str:
        """Build the tool error text for agy's ``denied_actions``."""
        names: list[str] = []
        for entry in denied_actions:
            if not isinstance(entry, dict):
                continue
            name = entry.get("display_name") or entry.get("action")
            if name:
                names.append(str(name))
        listed = f" ({', '.join(names)})" if names else ""
        return (
            "agy auto-denied this tool call: headless mode cannot answer a "
            f"permission prompt{listed}. Re-run with permissions level "
            '"full_auto" or add an allow rule to agy settings.'
        )

    def _parse_event(self, raw: dict[str, Any]) -> list[IREvent]:
        """Convert an agy stream-json event to IR events."""
        event_type = raw.get("event")

        if event_type == "init":
            return AgyNativeProvider._handle_init(raw)
        elif event_type == "step_update":
            return self._handle_step_update(raw)
        elif event_type == "result":
            return self._handle_result(raw)
        return []

    @staticmethod
    def _handle_init(raw: dict[str, Any]) -> list[IREvent]:
        """Handle init event — emits SessionStartEvent."""
        start: SessionStartEvent = {
            "type": "session_start",
            "session_id": raw.get("conversation_id", ""),
            "agent": "agy",
        }
        init_data = raw.get("init", {})
        cwd = init_data.get("cwd")
        if cwd:
            start["working_dir"] = cwd
        tools = init_data.get("tools")
        if tools:
            start["tools"] = tools
        return [start]

    def _handle_step_update(self, raw: dict[str, Any]) -> list[IREvent]:
        """Handle step_update event — dispatches by step_type."""
        step = raw.get("step_update", {})
        step_type = step.get("step_type", "")

        if step_type == "agent_response":
            return self._handle_agent_response(step)
        elif step_type == "tool":
            return self._handle_tool_step(step)
        elif step_type == "tool_use":
            # Shape documented by earlier agy builds.
            return AgyNativeProvider._handle_tool_use_step(step)
        # user_input, checkpoint, error_message — skip
        return []

    def _handle_tool_step(self, step: dict[str, Any]) -> list[IREvent]:
        """Handle a ``tool`` step — emits tool_use once, then its result.

        agy 1.5 sends the same ``step_index`` for the tool's ``ACTIVE``,
        ``DONE``, and ``ERROR`` updates, with the arguments under
        ``tool_info.parameters`` and the outcome under ``tool_info.output``
        or ``tool_info.error``. There is no separate tool-call id, so the
        step index identifies the call.

        Returns:
            ``tool_use`` on the first update, then ``tool_result``.
        """
        results: list[IREvent] = []
        step_index = step.get("step_index")
        state = step.get("state", "")
        info = step.get("tool_info") or {}
        tool_id = f"agy-step-{step_index}"

        if step_index is not None and step_index not in self._tool_steps:
            self._tool_steps.add(step_index)
            tool_use: ToolUseEvent = {
                "type": "tool_use",
                "tool_use_id": tool_id,
                "tool_name": step.get("tool_name", "") or info.get("name", ""),
                "tool_input": info.get("parameters") or {},
            }
            results.append(tool_use)

        if state == "DONE":
            output = info.get("output")
            if output:
                tool_result: ToolResultEvent = {
                    "type": "tool_result",
                    "tool_use_id": tool_id,
                    "content": str(output),
                }
                results.append(tool_result)
            else:
                # agy 1.5 omits ``output`` when the call never ran, which
                # is what a headless auto-deny looks like. Hold the
                # result until ``result`` reports ``denied_actions``.
                self._pending_tool_results.append(tool_id)
        elif state == "ERROR":
            error = info.get("error")
            message = error.get("message", "") if isinstance(error, dict) else error
            failed: ToolResultEvent = {
                "type": "tool_result",
                "tool_use_id": tool_id,
                "content": str(message or ""),
                "is_error": True,
            }
            results.append(failed)

        return results

    def _handle_agent_response(self, step: dict[str, Any]) -> list[IREvent]:
        """Handle agent_response step — emits message events.

        agy sends several updates for one step: ``ACTIVE`` with the first
        text, then ``DONE`` with the remaining text. Emit ``message_start``
        once per step, accumulate deltas, and close the message on ``DONE``.
        """
        results: list[IREvent] = []
        step_index = step.get("step_index")
        state = step.get("state", "")
        text_delta = step.get("text_delta", "")

        if not self._step_open or step_index != self._step_index:
            self._step_index = step_index
            self._step_text = []
            self._step_open = True
            msg_start: MessageStartEvent = {
                "type": "message_start",
                "role": "assistant",
            }
            results.append(msg_start)

        if text_delta:
            self._step_text.append(text_delta)
            delta: MessageDeltaEvent = {
                "type": "message_delta",
                "text": text_delta,
            }
            results.append(delta)

        if state == "DONE":
            end: MessageEndEvent = {
                "type": "message_end",
                "stop_reason": stop_reason(step.get("stop_reason"), default="end_turn"),
            }
            if self._step_text:
                end["text"] = "".join(self._step_text)
            results.append(end)
            self._step_index = None
            self._step_text = []
            self._step_open = False

        return results

    @staticmethod
    def _handle_tool_use_step(step: dict[str, Any]) -> list[IREvent]:
        """Handle tool_use step — emits ToolUseEvent and optionally ToolResultEvent."""
        results: list[IREvent] = []

        tool_event: ToolUseEvent = {
            "type": "tool_use",
            "tool_use_id": step.get("tool_use_id", ""),
            "tool_name": step.get("tool_name", ""),
            "tool_input": step.get("tool_input", {}),
        }
        results.append(tool_event)

        # If the step has a result, emit ToolResultEvent
        tool_result = step.get("tool_result")
        if tool_result is not None:
            result: ToolResultEvent = {
                "type": "tool_result",
                "tool_use_id": step.get("tool_use_id", ""),
                "content": str(tool_result),
            }
            if step.get("is_error"):
                result["is_error"] = True
            results.append(result)

        return results

    def _handle_result(self, raw: dict[str, Any]) -> list[IREvent]:
        """Handle result event — emits UsageEvent + SessionEndEvent (+ ErrorEvent).

        Tool results held back because agy omitted their output are
        flushed first: the ``denied_actions`` list on this event is where
        agy reports an auto-denied call.
        """
        results: list[IREvent] = []
        result_data = raw.get("result", {})
        status = result_data.get("status", "")

        denied_actions = result_data.get("denied_actions")
        if not isinstance(denied_actions, list):
            denied_actions = []
        results.extend(self._flush_pending_tool_results(denied_actions=denied_actions))

        # A fatal result can arrive while an assistant message is still
        # open (agy then omits the step's DONE update). Close it with
        # stop_reason "error" so the last message_end names the failure.
        if status != "SUCCESS" and self._step_open:
            end: MessageEndEvent = {"type": "message_end", "stop_reason": "error"}
            if self._step_text:
                end["text"] = "".join(self._step_text)
            results.append(end)
            self._step_index = None
            self._step_text = []
            self._step_open = False

        # Usage
        raw_usage = result_data.get("usage", {})
        usage: UsageInfo = {}
        if raw_usage.get("input_tokens"):
            usage["input_tokens"] = raw_usage["input_tokens"]
        if raw_usage.get("output_tokens"):
            usage["output_tokens"] = raw_usage["output_tokens"]
        if raw_usage.get("total_tokens"):
            usage["total_tokens"] = raw_usage["total_tokens"]
        if raw_usage.get("cache_read_tokens"):
            usage["cache_read_tokens"] = raw_usage["cache_read_tokens"]
        if raw_usage.get("thinking_tokens"):
            usage["reasoning_tokens"] = raw_usage["thinking_tokens"]

        usage_event: UsageEvent = {"type": "usage", "usage": usage}
        results.append(usage_event)

        # Error if status != SUCCESS
        if status != "SUCCESS":
            error_msg = result_data.get("error", f"agy exited with status: {status}")
            err: ErrorEvent = {
                "type": "error",
                "error": str(error_msg),
                "is_fatal": True,
            }
            results.append(err)

        # Session end
        session_end: SessionEndEvent = {"type": "session_end"}
        conv_id = result_data.get("conversation_id")
        if conv_id:
            session_end["session_id"] = conv_id
        results.append(session_end)

        return results


__all__ = ["AgyNativeProvider"]
