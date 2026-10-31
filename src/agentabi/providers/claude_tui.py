"""agentabi - Claude Code TUI (PTY) provider.

Runs the real interactive Claude Code on a pseudo-terminal and reads its
session transcript.  This matters for Claude subscription (OAuth)
accounts: Anthropic classifies a ``-p`` request as ``sdk-cli`` and counts
it at roughly twice the interactive ``cli`` entrypoint's allowance
(anthropics/claude-code#97074).

The provider is deliberately opt-in.  It drives a terminal UI, so it is
more fragile than ``ClaudeNativeProvider``: it waits for the prompt,
pastes the message, and tails the transcript JSONL.  Phase 1 emits whole
messages (no token deltas); a streaming tap can be added later.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from ..transport.pty import PtyProcess, PtyStartupError
from ..types.ir.capabilities import AgentCapabilities
from ..types.ir.events import (
    ErrorEvent,
    IREvent,
    MessageDeltaEvent,
    SessionEndEvent,
    SessionStartEvent,
    UsageEvent,
    UsageInfo,
)
from ..types.ir.session import SessionResult
from ..types.ir.task import TaskConfig
from .claude_native import ClaudeNativeProvider

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

#: Environment that ties the child to a previous agentabi/Claude process.
_DROP_ENV = {
    "CLAUDECODE",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_CODE_SESSION_ATTENDED",
    "CLAUDE_CODE_EXECPATH",
    "CLAUDE_PID",
    "CLAUDE_EFFORT",
    "COLUMNS",
    "LINES",
}

_ANSI_RE = re.compile(
    r"\x1b\[[0-9;?<>=]*[ -/]*[@-~]"
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"
    r"|\x1b[P^_][^\x1b]*\x1b\\"
    r"|\x1b."
)

_PROMPT_MARK = "❯"
_TRUST_MARKERS = ("trust this folder", "do you trust")
_LOGIN_MARKERS = ("select login method", "sign in to claude", "log in to claude")
_THEME_MARKERS = ("select a theme", "choose a theme")


def strip_ansi(text: str) -> str:
    """Remove terminal escape sequences from *text*."""
    return _ANSI_RE.sub(" ", text)


def transcript_path(cwd: str, session_id: str, config_dir: str | None = None) -> Path:
    """Return the JSONL transcript path Claude Code uses for a session.

    Args:
        cwd: Session working directory.
        session_id: Claude Code session id.
        config_dir: ``CLAUDE_CONFIG_DIR`` override; defaults to ``~/.claude``.

    Returns:
        Path to ``<config>/projects/<slug>/<session_id>.jsonl``.
    """
    base = Path(config_dir).expanduser() if config_dir else Path.home() / ".claude"
    slug = str(Path(cwd).resolve()).replace(os.sep, "-")
    return base / "projects" / slug / f"{session_id}.jsonl"


def _read_new_rows(path: Path, offset: int) -> tuple[list[dict[str, Any]], int]:
    """Read complete JSONL rows appended after *offset*.

    Returns:
        ``(rows, new_offset)``; a partial trailing line is left unread.
    """
    try:
        with path.open("rb") as handle:
            handle.seek(offset)
            data = handle.read()
    except FileNotFoundError:
        return [], offset
    rows: list[dict[str, Any]] = []
    used = 0
    while True:
        newline = data.find(b"\n", used)
        if newline < 0:
            break
        try:
            rows.append(json.loads(data[used:newline]))
        except json.JSONDecodeError:
            pass
        used = newline + 1
    return rows, offset + used


def _usage_event(message: dict[str, Any]) -> UsageEvent | None:
    """Build a usage event from an assistant message, if it carries usage."""
    raw = message.get("usage") or {}
    usage: UsageInfo = {}
    if "input_tokens" in raw:
        usage["input_tokens"] = raw["input_tokens"]
    if "output_tokens" in raw:
        usage["output_tokens"] = raw["output_tokens"]
    if "cache_read_input_tokens" in raw:
        usage["cache_read_tokens"] = raw["cache_read_input_tokens"]
    if "cache_creation_input_tokens" in raw:
        usage["cache_creation_tokens"] = raw["cache_creation_input_tokens"]
    total = raw.get("input_tokens", 0) + raw.get("output_tokens", 0)
    if total:
        usage["total_tokens"] = total
    if not usage:
        return None
    return {"type": "usage", "usage": usage}


def _build_env(task: TaskConfig) -> dict[str, str]:
    """Build a clean environment for the interactive CLI."""
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in _DROP_ENV and not key.startswith("CLAUDE_CODE_MESSAGING_")
    }
    env.update(task.get("env") or {})
    env["TERM"] = "xterm-256color"
    env.pop("COLUMNS", None)
    env.pop("LINES", None)
    return env


def _build_command(
    binary: str, task: TaskConfig, session_id: str, resume: bool
) -> list[str]:
    """Convert TaskConfig to interactive ``claude`` arguments."""
    cmd = [binary, "--no-chrome"]
    if resume:
        cmd.extend(["--resume", session_id])
    else:
        cmd.extend(["--session-id", session_id])

    if "model" in task:
        cmd.extend(["--model", task["model"]])
    if "system_prompt" in task:
        cmd.extend(["--system-prompt", task["system_prompt"]])
    if "append_system_prompt" in task:
        cmd.extend(["--append-system-prompt", task["append_system_prompt"]])

    permissions = task.get("permissions")
    if permissions:
        cli_mode = _PERMISSION_LEVEL_MAP.get(permissions.get("level") or "")
        if cli_mode:
            cmd.extend(["--permission-mode", cli_mode])

    if "allowed_tools" in task:
        cmd.extend(["--allowedTools", ",".join(task["allowed_tools"])])
    elif permissions and "allowed_tools" in permissions:
        cmd.extend(["--allowedTools", ",".join(permissions["allowed_tools"])])

    if "disallowed_tools" in task:
        cmd.extend(["--disallowedTools", ",".join(task["disallowed_tools"])])
    elif permissions and "disallowed_tools" in permissions:
        cmd.extend(["--disallowedTools", ",".join(permissions["disallowed_tools"])])

    if "mcp_config" in task:
        cmd.extend(["--mcp-config", task["mcp_config"]])

    for directory in task.get("additional_dirs") or []:
        cmd.extend(["--add-dir", directory])

    effort = _THINKING_LEVEL_MAP.get(task.get("thinking_level", ""))
    if effort:
        cmd.extend(["--effort", effort])

    return cmd


def _with_message_delta(events: list[IREvent]) -> list[IREvent]:
    """Add one ``message_delta`` before a whole-message ``message_end``.

    The transcript has no token deltas.  Emitting one delta lets consumers
    that render only deltas (the ACP IR backend) show the text without
    special-casing ``message_end``.
    """
    out: list[IREvent] = []
    for event in events:
        text = event.get("text")
        if event.get("type") == "message_end" and text:
            delta: MessageDeltaEvent = {"type": "message_delta", "text": text}
            out.append(delta)
        out.append(event)
    return out


class ClaudeTuiProvider:
    """Drive Claude Code's interactive TUI over a PTY."""

    @staticmethod
    def is_available() -> bool:
        """Return whether a POSIX PTY and the ``claude`` CLI exist."""
        return os.name == "posix" and shutil.which("claude") is not None

    def capabilities(self) -> AgentCapabilities:
        """Declare Claude Code capabilities for the PTY transport."""
        return {
            "name": "Claude Code (TUI)",
            "agent_type": "claude_code",
            "supports_streaming": False,
            "supports_mcp": True,
            "supports_session_resume": True,
            "supports_system_prompt": True,
            "supports_tool_filtering": True,
            "supports_file_diffs": False,
            "supports_permissions": True,
            "supports_thinking": True,
            "supports_structured_output": False,
            "supports_ephemeral": False,
            "supports_additional_dirs": True,
            "supports_file_attachments": False,
            "supports_multi_turn": True,
            "transport": "pty",
        }

    async def stream(self, task: TaskConfig) -> AsyncIterator[IREvent]:
        """Run one turn in the interactive CLI and yield IR events."""
        binary = self._binary(task)
        if binary is None:
            yield ErrorEvent(
                type="error",
                error="claude CLI not found; install Claude Code or set "
                "agent_extensions.claude_binary",
                is_fatal=True,
            )
            return

        session_id = task.get("session_id") or str(uuid.uuid4())
        resume = bool(task.get("resume"))
        cwd = task.get("working_dir") or os.getcwd()
        env = _build_env(task)
        command = _build_command(binary, task, session_id, resume)

        try:
            proc = await PtyProcess.spawn(command, cwd=cwd, env=env)
        except (OSError, PtyStartupError) as exc:
            yield ErrorEvent(
                type="error", error=f"cannot start Claude Code: {exc}", is_fatal=True
            )
            return

        try:
            failure = await self._wait_ready(proc, task)
            if failure is not None:
                reason, screen = failure
                yield ErrorEvent(
                    type="error",
                    error=self._startup_error(reason, cwd, proc, screen),
                    is_fatal=True,
                )
                return

            path = transcript_path(cwd, session_id, env.get("CLAUDE_CONFIG_DIR"))
            offset = 0
            if resume:
                # Do not replay history from a resumed session; only rows
                # written after this point belong to the current turn.
                try:
                    offset = path.stat().st_size
                except FileNotFoundError:
                    offset = 0
            else:
                path.unlink(missing_ok=True)

            yield SessionStartEvent(
                type="session_start",
                session_id=session_id,
                agent="claude_code",
            )
            await self._send_prompt(proc, task["prompt"])
            async for event in self._turn_events(proc, path, session_id, task, offset):
                yield event
        finally:
            await proc.close()

    async def run(self, task: TaskConfig) -> SessionResult:
        """Run task and return an aggregated result."""
        from .base import default_run

        return await default_run(self, task)

    # ========== Private ==========

    @staticmethod
    def _binary(task: TaskConfig) -> str | None:
        """Return the CLI path, honouring an explicit override."""
        override = (task.get("agent_extensions") or {}).get("claude_binary")
        if override:
            return str(override)
        return shutil.which("claude")

    async def _wait_ready(
        self, proc: PtyProcess, task: TaskConfig
    ) -> tuple[str, str] | None:
        """Wait for the input prompt.

        Returns:
            ``None`` when ready, otherwise ``(reason, screen_tail)``.
        """
        extensions = task.get("agent_extensions") or {}
        timeout = float(extensions.get("ready_timeout", 30.0))
        deadline = time.monotonic() + timeout
        screen = ""
        while time.monotonic() < deadline:
            chunk = await self._read_chunk(proc, 0.25)
            if chunk is None:
                continue
            if chunk == b"":
                return ("exited", screen[-800:])
            screen = (screen + strip_ansi(chunk.decode("utf-8", "replace")))[-8192:]
            lowered = screen.lower()
            if any(marker in lowered for marker in _TRUST_MARKERS):
                return ("trust", screen[-800:])
            if any(marker in lowered for marker in _LOGIN_MARKERS):
                return ("login", screen[-800:])
            if _PROMPT_MARK in screen and not any(
                marker in lowered for marker in _THEME_MARKERS
            ):
                return None
        return ("timeout", screen[-800:])

    @staticmethod
    def _startup_error(reason: str, cwd: str, proc: PtyProcess, screen: str) -> str:
        if reason == "trust":
            return (
                f"Claude Code is asking whether to trust {cwd}. Run `claude` "
                "there once and accept the prompt, then retry."
            )
        if reason == "login":
            return (
                "Claude Code is not signed in. Run `claude` once to sign in, "
                "then retry."
            )
        if reason == "exited":
            return (
                "Claude Code exited during startup "
                f"(code {proc.returncode}): {screen.strip()[-300:]}"
            )
        return f"Claude Code did not reach its prompt: {screen.strip()[-300:]}"

    @staticmethod
    async def _send_prompt(proc: PtyProcess, prompt: str) -> None:
        """Paste *prompt* into the TUI and submit it."""
        if prompt[:1] in ("/", "!", "#", "&"):
            # A leading slash would be read as a command; a newline keeps it text.
            prompt = "\n" + prompt
        proc.write(b"\x1b[200~" + prompt.encode() + b"\x1b[201~")
        await asyncio.sleep(0.15)
        proc.write(b"\r")

    async def _turn_events(
        self,
        proc: PtyProcess,
        path: Path,
        session_id: str,
        task: TaskConfig,
        offset: int = 0,
    ) -> AsyncIterator[IREvent]:
        """Read transcript rows until the turn ends."""
        timeout = float(task.get("timeout") or 600.0)
        deadline = time.monotonic() + timeout
        done = False
        exited = False

        while not done:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                yield ErrorEvent(
                    type="error",
                    error=f"Claude Code turn timed out after {timeout:g}s",
                    is_fatal=True,
                )
                break

            if not exited:
                chunk = await self._read_chunk(proc, min(0.1, remaining))
                if chunk == b"":
                    exited = True

            rows, offset = _read_new_rows(path, offset)
            for row in rows:
                row_type = row.get("type")
                if row_type == "system":
                    if row.get("subtype") == "turn_duration":
                        done = True
                    continue
                for event in _with_message_delta(
                    ClaudeNativeProvider._parse_event(row)
                ):
                    yield event
                if row_type == "assistant":
                    usage = _usage_event(row.get("message", {}))
                    if usage is not None:
                        yield usage
                if row_type == "result":
                    done = True

            if exited and not done:
                yield ErrorEvent(
                    type="error",
                    error=(
                        "Claude Code exited before the turn completed "
                        f"(code {proc.returncode})"
                    ),
                    is_fatal=True,
                )
                break

        yield SessionEndEvent(type="session_end", session_id=session_id)

    @staticmethod
    async def _read_chunk(proc: PtyProcess, timeout: float) -> bytes | None:
        """Read a PTY chunk, returning ``None`` when *timeout* elapses."""
        try:
            return await asyncio.wait_for(proc.read(), timeout)
        except asyncio.TimeoutError:
            return None


__all__ = ["ClaudeTuiProvider", "strip_ansi", "transcript_path"]
