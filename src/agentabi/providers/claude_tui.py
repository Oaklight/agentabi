"""agentabi - Claude Code TUI (PTY) provider.

Runs the real interactive Claude Code on a pseudo-terminal and reads its
session transcript.  This matters for Claude subscription (OAuth)
accounts: Anthropic classifies a ``-p`` request as ``sdk-cli`` and counts
it at roughly twice the interactive ``cli`` entrypoint's allowance
(anthropics/claude-code#97074).

The provider is deliberately opt-in.  It drives a terminal UI, so it is
more fragile than ``ClaudeNativeProvider``: it waits for the prompt,
pastes the message, confirms that the TUI rendered it, submits it with
Enter, confirms that a turn started, and tails the transcript JSONL.

The transcript is the source of events, so the reader is hardened against
the ways a long-lived CLI file changes: it resets after truncation or a
rewrite to a new inode, never advances past an incomplete line, and
recovers a row that a killed write glued to a partial prefix.  Rows that
share a message id form one ``message_start``/``message_end`` pair, usage
is summed across the turn, and the turn is allowed to settle so the
``last-prompt`` and ``cost-state`` rows are not cut off.  A CLI that
cannot start (untrusted directory, not signed in) falls back to the
native or SDK transport instead of failing every prompt.

A prompt that carries control characters is rejected before any byte is
written: bracketed paste is ended by ``ESC[201~``, so an embedded escape
sequence would turn the rest of the prompt into keystrokes.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
import uuid
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from .._vendor.ptyx import PtyProcess, PtyStartupError, PtyWriteError
from ..settings.registry import adapter_for
from ..types.ir.capabilities import AgentCapabilities
from ..types.ir.events import (
    ErrorEvent,
    IREvent,
    MessageDeltaEvent,
    MessageEndEvent,
    MessageStartEvent,
    ReasoningDeltaEvent,
    SessionEndEvent,
    SessionStartEvent,
    ToolUseEvent,
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

#: The input box renders the prompt marker alone on its own line:
#: `` ❯`` between two rules, with the status bar below.  A menu item such
#: as ``❯  1.  Red`` and assistant text quoting ``❯`` are not a prompt.
_PROMPT_LINE_RE = re.compile(r"^\s*❯\s*$")
#: A horizontal rule: the top and bottom border of the input box.
_RULE_RE = re.compile(r"^\s*[─━╌╍═]{10,}\s*$")
#: Non-empty screen lines below the prompt line that still count as chrome
#: (the closing rule and the status bar).  Anything further up is scrollback.
_PROMPT_BOTTOM_LINES = 4
#: Characters of the stripped screen kept for startup detection.
_SCREEN_WINDOW = 16384
#: Seconds to keep reading the transcript after a turn ends, and the quiet
#: window that ends the wait early.  Covers the ``last-prompt`` and
#: ``cost-state`` rows Claude Code appends after ``turn_duration``.
_SETTLE_TIMEOUT = 1.5
_SETTLE_QUIET = 0.3
_TRUST_MARKERS = ("trust this folder", "do you trust")
_LOGIN_MARKERS = (
    "select login method",
    "sign in to claude",
    "log in to claude",
    "press enter to log in",
    "choose an account",
    "welcome to claude code",
)

#: Bracketed paste delimiters.  A prompt must never carry these bytes
#: itself: ``ESC[201~`` would end the paste and make the rest keystrokes.
_PASTE_START = b"\x1b[200~"
_PASTE_END = b"\x1b[201~"
#: Characters of the prompt used to confirm that the TUI rendered it.
#: The tail is checked because the input box scrolls to the cursor.
_PASTE_ECHO_CHARS = 24
_PASTE_TIMEOUT = 5.0
_SUBMIT_TIMEOUT = 10.0
#: Claude Code renders a spinner and "esc to interrupt" while a turn runs.
_BUSY_MARKERS = ("esc to interrupt",)
_SPINNER_MARKS = "✱✲✳✴✵✶"
#: Control characters that are safe inside a bracketed paste.
_SAFE_CONTROLS = frozenset("\n\t")


class _PtyReadError(RuntimeError):
    """The PTY could not be read while a turn was running."""


class _PromptSubmitError(RuntimeError):
    """The TUI did not accept the pasted prompt or the Enter key."""


def strip_ansi(text: str) -> str:
    """Remove terminal escape sequences from *text*."""
    return _ANSI_RE.sub(" ", text)


def _slug_from_resolved(resolved: str) -> str:
    """Return Claude Code's ``projects/`` directory name for a path.

    Claude Code replaces every character that is not an ASCII letter or
    digit with a hyphen, keeping case: ``/home/x/.local`` becomes
    ``-home-x--local`` and ``C:\\Users\\user_probe`` becomes
    ``C--Users-user-probe``.
    """
    return re.sub(r"[^A-Za-z0-9]", "-", resolved)


def task_home(env: Mapping[str, str]) -> Path | None:
    """Return the home directory the child CLI will see, if known.

    ``HOME`` wins on POSIX and ``USERPROFILE`` on Windows, because that is
    the order the child resolves its own ``~/.claude``.
    """
    for name in ("HOME", "USERPROFILE"):
        value = env.get(name)
        if value:
            return Path(value)
    return None


def task_transcript_path(
    task: TaskConfig,
    cwd: str,
    session_id: str,
    env: Mapping[str, str] | None = None,
) -> Path:
    """Return the transcript path for the environment the child will see.

    ``transcript_path`` defaults to the host process's home directory.  A
    task that overrides ``HOME``/``USERPROFILE`` — a sandboxed run — makes
    the CLI write somewhere else, so the override must win.  A relative
    ``CLAUDE_CONFIG_DIR`` is resolved against the task cwd, the directory
    the child runs in.

    Args:
        task: The task, for its ``env`` overrides.
        cwd: Working directory the child runs in.
        session_id: Claude Code session id.
        env: The merged environment passed to the child.  Defaults to the
            task's own ``env``; pass the full environment so an inherited
            ``CLAUDE_CONFIG_DIR`` is honoured the same way the child does.
    """
    merged = dict(env) if env is not None else {}
    merged.update(task.get("env") or {})
    config_dir = merged.get("CLAUDE_CONFIG_DIR")
    if config_dir and not Path(config_dir).is_absolute():
        config_dir = str(Path(cwd) / config_dir)
    return transcript_path(cwd, session_id, config_dir, home=task_home(merged))


def transcript_path(
    cwd: str,
    session_id: str,
    config_dir: str | None = None,
    *,
    home: str | Path | None = None,
) -> Path:
    """Return the JSONL transcript path Claude Code uses for a session.

    Args:
        cwd: Session working directory.
        session_id: Claude Code session id.
        config_dir: ``CLAUDE_CONFIG_DIR`` override; defaults to ``~/.claude``.
        home: Directory used for the default config location.  Pass the
            task's home when the child sees a different one; ``None`` uses
            the host process home.

    Returns:
        Path to ``<config>/projects/<slug>/<session_id>.jsonl``.
    """
    if config_dir:
        base = Path(config_dir).expanduser()
    else:
        base = (Path(home) if home is not None else Path.home()) / ".claude"
    slug = _slug_from_resolved(str(Path(cwd).resolve()))
    return base / "projects" / slug / f"{session_id}.jsonl"


@dataclass
class _TranscriptTail:
    """Incremental JSONL reader state.

    Tracks the file identity and the offset of the last complete row.  The
    offset deliberately stops at an incomplete line: the next read starts
    there again, so a row split across two writes is parsed once, whole,
    instead of being spliced onto the row that follows it.
    """

    offset: int = 0
    inode: tuple[int, int] | None = None

    @classmethod
    def at_end(cls, path: Path) -> _TranscriptTail:
        """Start after the rows already in *path*."""
        try:
            stat = path.stat()
        except FileNotFoundError:
            return cls()
        return cls(offset=stat.st_size, inode=(stat.st_dev, stat.st_ino))


def _recover_row(line: bytes) -> dict[str, Any] | None:
    """Recover a row that a killed write glued to a partial prefix.

    A process killed mid-row leaves bytes without a newline.  The next
    process appends a complete row after them, so the physical line is
    ``{"type": "half", "x{"type": "next", ...}``.  The whole line fails to
    parse; the first ``{"`` suffix that parses as an object is the row that
    must not be lost.
    """
    index = line.find(b'{"')
    while index >= 0:
        try:
            row = json.loads(line[index:])
        except json.JSONDecodeError:
            index = line.find(b'{"', index + 1)
            continue
        if isinstance(row, dict):
            return row
        index = line.find(b'{"', index + 1)
    return None


def _read_new_rows(
    path: Path, tail: _TranscriptTail
) -> tuple[list[dict[str, Any]], _TranscriptTail]:
    """Read complete JSONL rows appended after ``tail.offset``.

    Handles three ways the file can change under a reader:

    - Truncation: the file is shorter than the offset.  Reading resumes at
      0 instead of blocking forever past the new end.
    - Replacement: the CLI rewrites the transcript to a new inode.  The
      new file is read from the start; the old offset describes a file
      that no longer exists.
    - A half-written line left by a killed process.  The offset stops at
      that line, so the next read assembles the row whole; if the line was
      never completed, the complete row appended after it is recovered.

    Returns:
        ``(rows, tail)`` with the new state; a partial trailing line is
        left unread.
    """
    try:
        stat = path.stat()
    except FileNotFoundError:
        return [], tail
    inode = (stat.st_dev, stat.st_ino)
    offset = tail.offset
    if tail.inode is not None and inode != tail.inode:
        offset = 0
    elif stat.st_size < offset:
        offset = 0
    try:
        with path.open("rb") as handle:
            handle.seek(offset)
            data = handle.read()
    except FileNotFoundError:
        return [], _TranscriptTail(offset=offset, inode=tail.inode)
    rows: list[dict[str, Any]] = []
    used = 0
    while True:
        newline = data.find(b"\n", used)
        if newline < 0:
            break
        line = data[used:newline]
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            row = _recover_row(line)
        if isinstance(row, dict):
            rows.append(row)
        used = newline + 1
    return rows, _TranscriptTail(offset=offset + used, inode=inode)


#: Usage counters that are summed across the messages of one turn.
_USAGE_COUNTERS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_creation_tokens",
    "reasoning_tokens",
)


def _usage_from_raw(raw: Mapping[str, Any]) -> UsageInfo | None:
    """Map a transcript or result ``usage`` object to IR usage.

    ``reasoning_tokens`` comes from ``output_tokens_details``.  The
    interactive transcript spells the key ``thinking_tokens``; the
    streaming API spells it ``reasoning_tokens``.  Both are accepted.
    """
    usage: UsageInfo = {}
    if "input_tokens" in raw:
        usage["input_tokens"] = raw["input_tokens"]
    if "output_tokens" in raw:
        usage["output_tokens"] = raw["output_tokens"]
    if "cache_read_input_tokens" in raw:
        usage["cache_read_tokens"] = raw["cache_read_input_tokens"]
    if "cache_creation_input_tokens" in raw:
        usage["cache_creation_tokens"] = raw["cache_creation_input_tokens"]
    details = raw.get("output_tokens_details")
    if isinstance(details, dict):
        reasoning = details.get("thinking_tokens") or details.get("reasoning_tokens")
        if reasoning:
            usage["reasoning_tokens"] = reasoning
    total = raw.get("input_tokens", 0) + raw.get("output_tokens", 0)
    if total:
        usage["total_tokens"] = total
    return usage or None


def _merge_usage(total: dict[str, int], usage: UsageInfo) -> None:
    """Add one message's usage to the running turn total."""
    for name in _USAGE_COUNTERS:
        value = usage.get(name)
        if value:
            total[name] = total.get(name, 0) + value
    combined = total.get("input_tokens", 0) + total.get("output_tokens", 0)
    if combined:
        total["total_tokens"] = combined


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


def _block_key(block: Mapping[str, Any]) -> str:
    """Return a fingerprint for a content block, used to drop replays."""
    block_type = str(block.get("type"))
    if block_type == "tool_use":
        return f"tool_use:{block.get('id', '')}"
    if block_type == "thinking":
        return "thinking:" + str(block.get("thinking", ""))
    return "text:" + str(block.get("text", ""))


@dataclass
class _OpenMessage:
    """Content accumulated for one logical assistant message."""

    message_id: str
    text_parts: list[str] = field(default_factory=list)
    stop_reason: str = ""
    blocks: set[str] = field(default_factory=set)


@dataclass
class _TurnState:
    """Per-turn message, usage, and cost accumulation.

    The interactive transcript writes one row per content block, repeating
    the message id and the usage object on every row.  A logical message
    therefore has to be assembled from several rows: one ``message_start``
    when the id first appears, deltas as new blocks arrive, and one
    ``message_end`` when the next message starts or the turn ends.
    """

    open_messages: dict[str, _OpenMessage] = field(default_factory=dict)
    total_usage: dict[str, int] = field(default_factory=dict)
    seen_usage_ids: set[str] = field(default_factory=set)
    usage_emitted: bool = False
    cost_usd: float | None = None
    _anonymous: int = 0

    def close_open(self) -> list[IREvent]:
        """End every open message, oldest first."""
        events: list[IREvent] = []
        for message in self.open_messages.values():
            end: MessageEndEvent = {
                "type": "message_end",
                "message_id": message.message_id,
            }
            if message.stop_reason:
                end["stop_reason"] = message.stop_reason
            if message.text_parts:
                end["text"] = "".join(message.text_parts)
            events.append(end)
        self.open_messages.clear()
        return events

    def flush(self) -> list[IREvent]:
        """End the open messages and report the turn's accumulated usage."""
        events = self.close_open()
        if self.usage_emitted or not self.total_usage:
            return events
        self.usage_emitted = True
        usage: UsageInfo = cast(UsageInfo, dict(self.total_usage))
        usage_event: UsageEvent = {"type": "usage", "usage": usage}
        if self.cost_usd is not None:
            usage_event["cost_usd"] = self.cost_usd
        events.append(usage_event)
        return events

    def message_for(
        self, row: Mapping[str, Any], message: Mapping[str, Any]
    ) -> tuple[_OpenMessage, bool]:
        """Return the open message for *row*, creating it when new.

        Returns:
            ``(message, created)``; ``created`` means the caller must emit
            ``message_start`` and should close the messages it already has.
        """
        key = message.get("id") or row.get("uuid")
        if not key:
            self._anonymous += 1
            key = f"anonymous-{self._anonymous}"
        key = str(key)
        existing = self.open_messages.get(key)
        if existing is not None:
            return existing, False
        return _OpenMessage(message_id=key), True

    def add_usage(self, message: Mapping[str, Any]) -> None:
        """Count one message's usage once, summing across messages."""
        message_id = message.get("id")
        key = str(message_id) if message_id else ""
        if key and key in self.seen_usage_ids:
            return
        usage = _usage_from_raw(message.get("usage") or {})
        if usage is None:
            return
        if key:
            self.seen_usage_ids.add(key)
        _merge_usage(self.total_usage, usage)

    def usage_with_result(self, usage: UsageInfo | None) -> dict[str, int]:
        """Return the turn usage, preferring a result row's cumulative value."""
        merged = dict(self.total_usage)
        if usage is None:
            return merged
        for name in _USAGE_COUNTERS:
            value = usage.get(name)
            if value is not None:
                merged[name] = max(merged.get(name, 0), value)
        input_tokens = merged.get("input_tokens", 0)
        output_tokens = merged.get("output_tokens", 0)
        total = usage.get("total_tokens")
        if total is not None:
            merged["total_tokens"] = max(total, input_tokens + output_tokens)
        elif input_tokens + output_tokens:
            merged["total_tokens"] = input_tokens + output_tokens
        return merged


def _assistant_row_events(row: Mapping[str, Any], state: _TurnState) -> list[IREvent]:
    """Convert one assistant row into events for its logical message."""
    message = row.get("message") or {}
    if not isinstance(message, Mapping):
        return []
    open_message, created = state.message_for(row, message)
    events: list[IREvent] = []
    if created:
        # A new message id ends the previous logical message.
        events.extend(state.close_open())
        state.open_messages[open_message.message_id] = open_message
        start: MessageStartEvent = {
            "type": "message_start",
            "role": "assistant",
            "message_id": open_message.message_id,
        }
        events.append(start)

    content = message.get("content") or []
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, Mapping):
                continue
            key = _block_key(block)
            if key in open_message.blocks:
                continue
            open_message.blocks.add(key)
            block_type = block.get("type")
            if block_type == "text":
                text = str(block.get("text", ""))
                if text:
                    open_message.text_parts.append(text)
                    delta: MessageDeltaEvent = {
                        "type": "message_delta",
                        "text": text,
                        "message_id": open_message.message_id,
                    }
                    events.append(delta)
            elif block_type == "thinking":
                thinking = str(block.get("thinking", ""))
                if thinking:
                    reasoning: ReasoningDeltaEvent = {
                        "type": "reasoning_delta",
                        "reasoning": thinking,
                    }
                    events.append(reasoning)
            elif block_type == "tool_use":
                tool: ToolUseEvent = {
                    "type": "tool_use",
                    "tool_use_id": str(block.get("id", "")),
                    "tool_name": str(block.get("name", "")),
                    "tool_input": dict(block.get("input") or {}),
                }
                events.append(tool)

    stop_reason = message.get("stop_reason")
    if isinstance(stop_reason, str) and stop_reason:
        open_message.stop_reason = stop_reason
    state.add_usage(message)
    return events


def _float_option(value: Any, default: float, name: str) -> float:
    """Parse a numeric task option, naming it when the value is invalid."""
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {name}: {value!r}") from exc


def _collapse_whitespace(text: str, limit: int = 500) -> str:
    """Flatten *text* to one line and truncate it to *limit* characters."""
    flat = " ".join(text.split())
    if len(flat) > limit:
        flat = flat[: limit - 3].rstrip() + "..."
    return flat


def _prompt_problem(prompt: str) -> str | None:
    """Return why *prompt* cannot be pasted safely, or ``None``.

    Bracketed paste ends at ``ESC[201~``.  Any control character in the
    payload could end the paste early or act as a key press, so the prompt
    is rejected before a single byte reaches the terminal.  Newline and tab
    are the only controls a TUI treats as text inside a paste.
    """
    for index, char in enumerate(prompt):
        if char in _SAFE_CONTROLS:
            continue
        code = ord(char)
        if code < 0x20 or 0x7F <= code <= 0x9F:
            return (
                f"prompt contains the control character U+{code:04X} at "
                f"index {index}; it cannot be pasted safely because "
                "ESC[201~ ends the bracketed paste and the rest would "
                "arrive as keystrokes"
            )
    return None


def _echo_fragment(prompt: str) -> str:
    """Return the part of *prompt* to look for in the TUI's screen echo.

    The tail is used because a long input box scrolls to the cursor.
    """
    flat = " ".join(prompt.split())
    return flat[-_PASTE_ECHO_CHARS:]


def _screen_is_busy(screen: str) -> bool:
    """Return whether *screen* shows the TUI working on a turn."""
    lowered = screen.lower()
    return any(marker in lowered for marker in _BUSY_MARKERS) or any(
        mark in screen for mark in _SPINNER_MARKS
    )


def _framed_by_rule(lines: list[str], index: int) -> bool:
    """Return whether *index* sits inside a box drawn by two rule lines."""
    above = next((line for line in reversed(lines[:index]) if line.strip()), "")
    below = next((line for line in lines[index + 1 :] if line.strip()), "")
    return bool(_RULE_RE.match(above) and _RULE_RE.match(below))


def _prompt_is_ready(screen: str) -> bool:
    """Return whether *screen* shows the input prompt, not a menu or text.

    The TUI does not use the alternate screen, so the captured stream holds
    stale frames and scrollback.  Two shapes count as the prompt:

    - the marker alone on its line, with at most four non-empty lines below
      it (the closing rule and the status bar);
    - the marker line inside the input box, whose top and bottom borders are
      rule lines.  A non-empty input box renders the marker followed by the
      typed text or the placeholder hint, so the marker is not alone there.

    A menu item (``❯  1.  Red``), a theme picker, and assistant text that
    quotes the marker are neither: their neighbours are option or message
    text, not rules.

    The last matching line wins: a redraw appends a new frame, and the
    newest frame's prompt is the live one.
    """
    lines = screen.splitlines()
    for index in range(len(lines) - 1, -1, -1):
        line = lines[index]
        if "❯" not in line:
            continue
        if _PROMPT_LINE_RE.match(line):
            below = [text for text in lines[index + 1 :] if text.strip()]
            return len(below) <= _PROMPT_BOTTOM_LINES
        return _framed_by_rule(lines, index)
    return False


def _session_uuid(value: Any) -> str | None:
    """Return *value* as a canonical UUID string, or ``None``.

    Claude Code rejects ``--session-id`` / ``--resume`` values that are not
    UUIDs.  Parsing first keeps a caller's arbitrary label from reaching the
    CLI as an argument that fails every prompt.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError, TypeError):
        return None


# ========== Dialog detection ==========

#: Stripped screen text kept for dialog detection, in characters.
_SCREEN_LIMIT = 32 * 1024
#: Raw PTY text kept before stripping; older frames are dropped.
_SCREEN_RAW_LIMIT = 64 * 1024

#: The TUI status bar is the frame boundary.  Match the mode phrase, not the
#: whole line: the hints after it change with the release.
_STATUS_RE = re.compile(
    r"(?:auto|manual|plan) mode on"
    r"|accept edits on"
    r"|don['’]t ask on"
    r"|bypass permissions on"
)

#: Footer phrases that identify a live modal.  A footer is only a dialog when
#: it is drawn below the most recent status bar.
_FOOTERS: dict[str, tuple[str, ...]] = {
    "ask_user_question": ("Enter to select",),
    "review": ("Ready to submit your answers?",),
    "permission": ("Esc to cancel · Tab to amend",),
    "plan": ("ctrl+g to edit in nano", "shift+tab to approve with this feedback"),
    "notice": ("Enter to continue · Esc to cancel",),
    "security_notice": ("Enter to confirm · Esc to cancel",),
}

_PERMISSION_HEADERS = (
    "Bash command",
    "Edit file",
    "Write file",
    "Do you want to proceed?",
    "Do you want to make this edit",
)
#: Permission headers that name the tool directly.
_PERMISSION_TOOLS = (
    ("bash command", "Bash"),
    ("edit file", "Edit"),
    ("write file", "Write"),
)
_PLAN_HEADERS = (
    "Ready to code?",
    "Here is Claude's plan:",
    "Would you like to proceed?",
)

#: A numbered option row, with or without the selection cursor.
_OPTION_RE = re.compile(r"^(?:❯\s*)?(\d+)\.\s*(.+)$")
#: Options that are dialog affordances, not answers.
_PSEUDO_OPTIONS = frozenset({"type something.", "chat about this"})


@dataclass(frozen=True)
class Dialog:
    """A modal dialog detected on the TUI screen.

    Attributes:
        type: ``ask_user_question``, ``review``, ``permission``, ``plan``,
            ``notice``, or ``security_notice``.
        question: Question or prompt text read from the screen.
        options: Option labels rendered under the question, in order.
        tool_name: Tool named by the transcript, when known.
        tool_use_id: Transcript id of the blocked ``tool_use``, when known.
    """

    type: str
    question: str | None = None
    options: tuple[str, ...] = ()
    tool_name: str | None = None
    tool_use_id: str | None = None


def _latest_footer(screen: str) -> tuple[str, int] | None:
    """Return the dialog type and offset of the last footer in *screen*."""
    best: tuple[str, int] | None = None
    for kind, phrases in _FOOTERS.items():
        for phrase in phrases:
            index = screen.rfind(phrase)
            if index >= 0 and (best is None or index > best[1]):
                best = (kind, index)
    return best


def _option_labels(text: str, *, skip_pseudo: bool = False) -> tuple[str, ...]:
    """Return the numbered option labels rendered in *text*."""
    labels: list[str] = []
    for line in text.splitlines():
        match = _OPTION_RE.match(line.strip())
        if match is None:
            continue
        label = " ".join(match.group(2).split())
        if skip_pseudo and label.lower() in _PSEUDO_OPTIONS:
            continue
        labels.append(label)
    return tuple(labels)


def _question_after_header(lines: list[str]) -> str | None:
    """Return the question line that follows the last ``☐`` header line."""
    for index in range(len(lines) - 1, -1, -1):
        if "☐" not in lines[index]:
            continue
        for line in lines[index + 1 :]:
            text = line.strip()
            if not text or text.startswith("❯") or _OPTION_RE.match(text):
                continue
            return text
        return None
    return None


def _ask_user_question_dialog(window: str) -> Dialog:
    """Build the AskUserQuestion dialog rendered in *window*."""
    return Dialog(
        type="ask_user_question",
        question=_question_after_header(window.splitlines()),
        options=_option_labels(window, skip_pseudo=True),
    )


def _permission_tool(window: str) -> str | None:
    """Return the tool named by a permission dialog header, if any."""
    lowered = window.lower()
    for marker, tool in _PERMISSION_TOOLS:
        if marker in lowered:
            return tool
    return None


def detect_dialog(screen: str) -> Dialog | None:
    """Return the live modal at the bottom of *screen*, or ``None``.

    The TUI draws over the normal buffer, so the accumulated screen keeps
    stale frames.  The most recent status bar is the frame boundary: a
    footer above it is scrollback, including assistant text that quotes a
    dialog verbatim.  The function is pure so it can be tested on captured
    screen text.
    """
    footer = _latest_footer(screen)
    last_status = max(
        (match.start() for match in _STATUS_RE.finditer(screen)), default=-1
    )
    if last_status < 0:
        # The bypass-permissions warning replaces the status bar at startup.
        if footer is not None and footer[0] == "security_notice":
            return Dialog(type="security_notice")
        return None
    if footer is None or footer[1] < last_status:
        return None
    kind, index = footer
    window = screen[last_status:index]
    lowered = window.lower()
    if kind == "ask_user_question":
        return _ask_user_question_dialog(window)
    if kind == "review":
        if "review your answers" in lowered:
            return Dialog(type="review")
        return _ask_user_question_dialog(window)
    if kind == "permission":
        if not any(header.lower() in lowered for header in _PERMISSION_HEADERS):
            return None
        return Dialog(
            type="permission",
            tool_name=_permission_tool(window),
            options=_option_labels(window),
        )
    if kind == "plan":
        if not any(header.lower() in lowered for header in _PLAN_HEADERS):
            return None
        return Dialog(type="plan", options=_option_labels(window))
    return Dialog(type=kind)


def _api_error_event(message: str, status: Any, suffix: str, fatal: bool) -> ErrorEvent:
    """Build an ``ErrorEvent`` for an API failure reported by Claude Code."""
    detail = _collapse_whitespace(message) or "Claude Code reported an API error"
    prefix = f"API error {status}: " if status else "API error: "
    return ErrorEvent(type="error", error=prefix + detail + suffix, is_fatal=fatal)


def _system_api_error(row: dict[str, Any]) -> tuple[ErrorEvent, bool]:
    """Map a ``system/api_error`` row to an event and a turn-end signal.

    Claude Code writes one row per failed attempt.  While retries remain the
    turn continues, so the event is not fatal; once the retry budget is spent
    (or the row carries none) the turn cannot produce an answer.
    """
    raw = row.get("error")
    if isinstance(raw, dict):
        status = raw.get("status")
        message = str(raw.get("message") or raw.get("formatted") or "")
    else:
        status = None
        message = str(raw) if raw else ""
    attempt = row.get("retryAttempt")
    limit = row.get("maxRetries")
    retrying = isinstance(attempt, int) and isinstance(limit, int) and attempt < limit
    suffix = f" (retry {attempt}/{limit})" if retrying else ""
    return _api_error_event(message, status, suffix, fatal=not retrying), not retrying


def _assistant_api_error(row: dict[str, Any]) -> ErrorEvent:
    """Map an ``isApiErrorMessage`` assistant row to a fatal event.

    The row repeats the failure as ordinary answer text; emitting it as a
    message would make the failure look like a response.
    """
    message = row.get("message")
    text = ""
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, list):
        text = "".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    if not text:
        text = str(row.get("error") or "")
    return _api_error_event(text, row.get("apiErrorStatus"), "", fatal=True)


def _turn_row(
    row: dict[str, Any], state: _TurnState | None = None
) -> tuple[list[IREvent], bool]:
    """Convert one transcript row into IR events and a turn-end signal.

    Args:
        row: One transcript JSONL row.
        state: The turn accumulator.  Pass the same object for every row of
            a turn; ``None`` creates a throwaway one (single-row callers).

    Returns:
        ``(events, turn_done)``.  Only ``system/turn_duration``, ``result``,
        and API failures finish the turn; an assistant ``stop_reason`` is not
        a boundary because one API message can be split across several
        JSONL rows written seconds apart.

    A turn that ends flushes the open message and the accumulated usage, so
    the caller does not need a second flush.
    """
    state = state if state is not None else _TurnState()
    row_type = row.get("type")
    if row_type == "system":
        # The provider already emitted session_start; only the duration row
        # and API failures are meaningful here.
        subtype = row.get("subtype")
        if subtype == "turn_duration":
            return state.flush(), True
        if subtype == "api_error":
            event, turn_done = _system_api_error(row)
            events = [event]
            if turn_done:
                events.extend(state.flush())
            return events, turn_done
        return [], False
    if row_type == "assistant" and row.get("isApiErrorMessage"):
        return [_assistant_api_error(row), *state.flush()], True
    if row_type == "assistant" and not isinstance(row.get("message"), dict):
        return [], False
    if row_type == "result":
        events = state.close_open()
        cost = row.get("total_cost_usd")
        if isinstance(cost, (int, float)):
            state.cost_usd = float(cost)
        if not state.usage_emitted:
            # A result row carries the turn's cumulative usage.  When the
            # turn already reported usage, re-emitting would double count.
            merged = state.usage_with_result(_usage_from_raw(row.get("usage") or {}))
            if merged:
                state.total_usage = merged
            events.extend(state.flush())
        return events, True
    if row_type == "user":
        # Tool results live on user rows; the native parser already maps
        # them and emits nothing else that the TUI turn loop wants.
        return ClaudeNativeProvider._parse_event(row), False
    if row_type != "assistant":
        return [], False
    return _assistant_row_events(row, state), False


# ========== Dialog answers ==========

#: Pause between two writes of one answer, in seconds.  One digit per write
#: is the only verified plan-approval sequence.
_ANSWER_KEY_GAP = 0.3
#: Tools that always open a modal and never return without one.
_ALWAYS_MODAL_TOOLS = frozenset({"AskUserQuestion", "ExitPlanMode"})
#: Dialog types that block the turn without a pending ``tool_use``.
_TOOLLESS_DIALOGS = frozenset({"notice", "security_notice"})
#: Dialog types that are the same modal across a redraw.
_DIALOG_FAMILY = {"review": "ask_user_question"}


class _DialogUnanswered(Exception):
    """A live dialog has no configured, safe answer."""


@dataclass(frozen=True)
class _PendingTool:
    """A ``tool_use`` block whose ``tool_result`` has not been read yet."""

    tool_use_id: str
    tool_name: str
    tool_input: dict[str, Any]
    seen_at: float


class _TurnTools:
    """Track ``tool_use`` blocks and their results across transcript rows."""

    def __init__(self) -> None:
        self._pending: dict[str, _PendingTool] = {}
        self._order: list[str] = []
        self._resolved: set[str] = set()

    def feed(self, row: dict[str, Any]) -> None:
        """Record the tool blocks in one transcript row."""
        row_type = row.get("type")
        if row_type not in ("assistant", "user"):
            return
        message = row.get("message")
        if not isinstance(message, dict):
            return
        content = message.get("content")
        if not isinstance(content, list):
            return
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if row_type == "assistant" and block_type == "tool_use":
                self._add(block)
            elif row_type == "user" and block_type == "tool_result":
                tool_id = str(block.get("tool_use_id") or "")
                if tool_id:
                    self._pending.pop(tool_id, None)
                    self._resolved.add(tool_id)

    def _add(self, block: dict[str, Any]) -> None:
        tool_id = str(block.get("id") or "")
        if not tool_id or tool_id in self._pending or tool_id in self._resolved:
            return
        tool_input = block.get("input")
        self._pending[tool_id] = _PendingTool(
            tool_use_id=tool_id,
            tool_name=str(block.get("name") or ""),
            tool_input=dict(tool_input) if isinstance(tool_input, dict) else {},
            seen_at=time.monotonic(),
        )
        self._order.append(tool_id)

    def pending(self) -> _PendingTool | None:
        """Return the most recent unresolved ``tool_use``, if any."""
        for tool_id in reversed(self._order):
            tool = self._pending.get(tool_id)
            if tool is not None:
                return tool
        return None

    @property
    def saw_tool_use(self) -> bool:
        """Return whether the turn has produced any ``tool_use`` yet."""
        return bool(self._order)


def _peek_tools(path: Path, tail: _TranscriptTail, tools: _TurnTools) -> None:
    """Feed new transcript rows into *tools* without consuming the tail."""
    try:
        rows, _ = _read_new_rows(path, tail)
    except OSError:
        return
    for row in rows:
        tools.feed(row)


def _tool_result_present(path: Path, tail: _TranscriptTail, tool_use_id: str) -> bool:
    """Return whether the transcript already holds *tool_use_id*'s result."""
    try:
        rows, _ = _read_new_rows(path, tail)
    except OSError:
        return False
    for row in rows:
        if row.get("type") != "user":
            continue
        message = row.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if (
                block.get("type") == "tool_result"
                and block.get("tool_use_id") == tool_use_id
            ):
                return True
    return False


def _ask_questions(pending: _PendingTool | None) -> list[dict[str, Any]]:
    """Return the ``AskUserQuestion`` question list from *pending*."""
    if pending is None or pending.tool_name != "AskUserQuestion":
        return []
    raw = pending.tool_input.get("questions")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _question_text(question: dict[str, Any]) -> str:
    """Return the question text of one ``AskUserQuestion`` entry."""
    return str(question.get("question") or "")


def _question_options(question: dict[str, Any]) -> tuple[str, ...]:
    """Return the option labels of one ``AskUserQuestion`` entry."""
    raw = question.get("options")
    if not isinstance(raw, list):
        return ()
    labels = []
    for item in raw:
        if isinstance(item, dict) and item.get("label"):
            labels.append(" ".join(str(item["label"]).split()))
    return tuple(labels)


def _quote(text: str, limit: int = 200) -> str:
    """Return *text* flattened and quoted for an error message."""
    flat = _collapse_whitespace(text, limit)
    return f'"{flat}"' if flat else "an unnamed dialog"


def _options_hint(labels: tuple[str, ...]) -> str:
    """Return a compact option list for an error message."""
    return f" (options: {', '.join(labels)})" if labels else ""


def _inline_text(text: str) -> str | None:
    """Return why *text* cannot be typed into a one-line input, or ``None``."""
    problem = _prompt_problem(text)
    if problem is None and ("\n" in text or "\t" in text):
        problem = "the answer must be one line; Enter submits it"
    return problem


def _question_detail(dialog: Dialog, questions: list[dict[str, Any]]) -> str:
    """Describe an AskUserQuestion dialog for an error message."""
    if questions:
        first = questions[0]
        return _quote(_question_text(first)) + _options_hint(_question_options(first))
    if dialog.question:
        return _quote(dialog.question) + _options_hint(dialog.options)
    return "a question dialog"


def _permission_detail(dialog: Dialog, pending: _PendingTool | None) -> str:
    """Describe a permission dialog for an error message."""
    tool = (pending.tool_name if pending else "") or dialog.tool_name or "a tool"
    target = ""
    if pending is not None:
        raw = pending.tool_input.get("command") or pending.tool_input.get("file_path")
        if raw:
            target = _quote(str(raw), 160)
    return f"{tool} {target}".strip()


def _plan_detail(pending: _PendingTool | None) -> str:
    """Describe a plan approval for an error message."""
    if pending is not None:
        plan = str(pending.tool_input.get("plan") or "").strip()
        first = plan.splitlines()[0].strip() if plan else ""
        if first:
            return _quote(first, 160)
    return "a plan approval"


def _match_question(question: dict[str, Any], config: dict[str, Any]) -> Any:
    """Return the configured answer for *question*, or ``None``.

    Keys are matched case-insensitively as substrings of the question text;
    the longest matching key wins, and ``*`` is the catch-all.
    """
    text = _question_text(question).strip().lower()
    best_key: str | None = None
    for key in config:
        if not isinstance(key, str) or key == "*":
            continue
        needle = key.strip().lower()
        if not needle or needle not in text:
            continue
        if best_key is None or len(needle) > len(best_key.strip()):
            best_key = key
    if best_key is not None:
        return config[best_key]
    return config.get("*")


def _ask_key(question: dict[str, Any], spec: Any) -> tuple[bytes, ...]:
    """Map one configured AskUserQuestion answer to selection keys."""
    labels = _question_options(question)
    text = _quote(_question_text(question))
    if isinstance(spec, dict):
        if "text" in spec:
            answer = str(spec["text"])
            problem = _inline_text(answer)
            if problem is not None:
                raise _DialogUnanswered(
                    f"{text}: the free-text answer cannot be typed safely: {problem}"
                )
            # The rendered list puts "Type something." right after the options.
            return (str(len(labels) + 1).encode(), answer.encode(), b"\r")
        if "option" in spec:
            index = spec["option"]
            if (
                isinstance(index, bool)
                or not isinstance(index, int)
                or not 1 <= index <= len(labels)
            ):
                raise _DialogUnanswered(
                    f"{text}: option {index!r} is out of range{_options_hint(labels)}"
                )
            return (str(index).encode(),)
        raise _DialogUnanswered(f"{text}: the answer has no 'option' or 'text' key")
    if isinstance(spec, str):
        wanted = " ".join(spec.split()).lower()
        for index, label in enumerate(labels, start=1):
            if label.lower() == wanted or label.lower().startswith(wanted):
                return (str(index).encode(),)
        raise _DialogUnanswered(
            f"{text}: no option matches {spec!r}{_options_hint(labels)}"
        )
    raise _DialogUnanswered(f"{text}: the answer must be a string or a dict")


def _resolve_ask_user_question(
    dialog: Dialog, pending: _PendingTool | None, answers: dict[str, Any]
) -> tuple[bytes, ...]:
    """Map every question in an AskUserQuestion dialog to its answer keys."""
    config = answers.get("ask_user_question")
    questions = _ask_questions(pending)
    if not isinstance(config, dict) or not config:
        raise _DialogUnanswered(
            _question_detail(dialog, questions)
            + "; set agent_extensions.tui_answers.ask_user_question"
        )
    if not questions:
        raise _DialogUnanswered(
            _question_detail(dialog, questions)
            + "; the transcript does not carry the question list"
        )
    keys: list[bytes] = []
    for question in questions:
        if question.get("multiSelect"):
            raise _DialogUnanswered(
                f"{_quote(_question_text(question))}: multiSelect is not supported"
            )
        spec = _match_question(question, config)
        if spec is None:
            raise _DialogUnanswered(
                f"{_quote(_question_text(question))}: no configured answer"
                f"{_options_hint(_question_options(question))}"
            )
        keys.extend(_ask_key(question, spec))
    if len(questions) > 1:
        # The last answer opens the review page; Enter submits it.
        keys.append(b"\r")
    return tuple(keys)


_PERMISSION_CHOICES = ("allow_once", "allow_always", "allow_session", "deny")


def _permission_index(labels: tuple[str, ...], choice: str) -> int | None:
    """Return the 1-based rendered index for a semantic permission choice."""
    for index, raw in enumerate(labels, start=1):
        label = " ".join(raw.split()).lower()
        if choice == "allow_once":
            blocked = ("always", "don't ask again", "don’t ask again", "switch to")
            if label.startswith("yes") and not any(mark in label for mark in blocked):
                return index
        elif choice == "allow_always":
            if (
                "always allow" in label
                or "don't ask again" in label
                or "don’t ask again" in label
            ):
                return index
        elif choice == "allow_session":
            if "switch to accept edits" in label or "switch to auto mode" in label:
                return index
        elif choice == "deny" and label == "no":
            return index
    return None


def _resolve_permission(
    dialog: Dialog, pending: _PendingTool | None, answers: dict[str, Any]
) -> tuple[bytes, ...]:
    """Map a permission dialog to the digit of the rendered option."""
    config = answers.get("permission")
    detail = _permission_detail(dialog, pending)
    if not isinstance(config, dict) or not config:
        raise _DialogUnanswered(
            detail + "; set agent_extensions.tui_answers.permission"
        )
    tool_name = (pending.tool_name if pending else "") or dialog.tool_name or ""
    by_tool = config.get("by_tool")
    choice = by_tool.get(tool_name) if isinstance(by_tool, dict) else None
    if choice is None:
        choice = config.get("default")
    if choice not in _PERMISSION_CHOICES:
        raise _DialogUnanswered(
            f"{detail}: {choice!r} is not one of {', '.join(_PERMISSION_CHOICES)}"
        )
    index = _permission_index(dialog.options, str(choice))
    if index is not None:
        return (str(index).encode(),)
    if choice == "deny":
        # "No" is absent from some layouts; Esc cancels the dialog instead.
        return (b"\x1b",)
    raise _DialogUnanswered(
        f"{detail}: no rendered option matches {choice}{_options_hint(dialog.options)}"
    )


def _resolve_plan(
    dialog: Dialog, pending: _PendingTool | None, answers: dict[str, Any]
) -> tuple[bytes, ...]:
    """Map a plan approval to the digit and optional feedback keys."""
    config = answers.get("plan")
    detail = _plan_detail(pending)
    if not isinstance(config, dict) or not config:
        raise _DialogUnanswered(detail + "; set agent_extensions.tui_answers.plan")
    choice = config.get("default")
    if choice == "approve_auto":
        return (b"1",)
    if choice == "approve_manual":
        return (b"2",)
    if choice == "reject":
        feedback = str(config.get("feedback") or "").strip()
        if not feedback:
            raise _DialogUnanswered(
                f"{detail}: plan.default is 'reject' but plan.feedback is empty"
            )
        problem = _inline_text(feedback)
        if problem is not None:
            raise _DialogUnanswered(f"{detail}: the plan feedback is unsafe: {problem}")
        return (b"3", feedback.encode() + b"\r")
    raise _DialogUnanswered(
        f"{detail}: {choice!r} is not one of approve_auto, approve_manual, reject"
    )


def _resolve_dialog(
    dialog: Dialog, pending: _PendingTool | None, answers: dict[str, Any]
) -> tuple[bytes, ...]:
    """Map *dialog* to the keystrokes that answer it.

    Raises:
        _DialogUnanswered: When the dialog has no configured answer or the
            rendered options cannot be mapped without guessing.
    """
    if dialog.type == "security_notice":
        raise _DialogUnanswered(
            "a security confirmation is never accepted automatically; "
            "run the CLI once and accept it by hand"
        )
    if dialog.type == "notice":
        return (b"\r",)
    if dialog.type == "ask_user_question":
        return _resolve_ask_user_question(dialog, pending, answers)
    if dialog.type == "review":
        raise _DialogUnanswered(
            "the answer review page is open without a recorded answer"
        )
    if dialog.type == "permission":
        return _resolve_permission(dialog, pending, answers)
    if dialog.type == "plan":
        return _resolve_plan(dialog, pending, answers)
    raise _DialogUnanswered("unknown dialog type")


def _corroborated(dialog: Dialog, pending: _PendingTool | None) -> bool:
    """Return whether the transcript backs the dialog seen on screen."""
    if dialog.type in _TOOLLESS_DIALOGS:
        return True
    if dialog.type in ("ask_user_question", "review"):
        return pending is not None and pending.tool_name == "AskUserQuestion"
    if dialog.type == "permission":
        return pending is not None and pending.tool_name not in _ALWAYS_MODAL_TOOLS
    if dialog.type == "plan":
        return pending is not None and pending.tool_name == "ExitPlanMode"
    return False


def _transcript_dialog(tools: _TurnTools, grace: float) -> Dialog | None:
    """Return a modal the screen missed, from a pending modal tool.

    ``AskUserQuestion`` and ``ExitPlanMode`` always block, so a pending
    ``tool_use`` older than *grace* is a dialog even when the render is
    clipped or the frame was lost.
    """
    pending = tools.pending()
    if pending is None or pending.tool_name not in _ALWAYS_MODAL_TOOLS:
        return None
    if time.monotonic() - pending.seen_at < grace:
        return None
    if pending.tool_name == "AskUserQuestion":
        questions = _ask_questions(pending)
        first = questions[0] if questions else {}
        return Dialog(
            type="ask_user_question",
            question=_question_text(first) or None,
            options=_question_options(first),
            tool_name=pending.tool_name,
            tool_use_id=pending.tool_use_id,
        )
    plan = str(pending.tool_input.get("plan") or "").strip()
    first_line = plan.splitlines()[0].strip() if plan else ""
    return Dialog(
        type="plan",
        question=first_line or None,
        tool_name=pending.tool_name,
        tool_use_id=pending.tool_use_id,
    )


def _live_dialog(screen: str, tools: _TurnTools, grace: float) -> Dialog | None:
    """Return the modal that is live on screen or proven by the transcript."""
    dialog = detect_dialog(screen)
    if dialog is not None:
        return dialog
    return _transcript_dialog(tools, grace)


@dataclass
class _DialogStep:
    """Outcome of one dialog poll."""

    answered: bool
    events: list[IREvent]
    raw: str
    exited: bool
    key: str | None = None

    @property
    def fatal(self) -> bool:
        """Return whether the step ended the turn with a fatal error."""
        return any(
            event.get("type") == "error" and event.get("is_fatal")
            for event in self.events
        )


@dataclass
class _DialogSession:
    """Dialog state shared by the turn loop and the settle window."""

    tools: _TurnTools
    answers: dict[str, Any]
    grace: float
    answer_settle: float
    raw: str = ""
    previous: str | None = None
    latched: str | None = None


def _append_screen(dialogs: _DialogSession | None, chunk: bytes | None) -> None:
    """Append a PTY chunk to the dialog screen buffer, when tracking."""
    if dialogs is None or not chunk:
        return
    text = strip_ansi(chunk.decode("utf-8", "replace"))
    dialogs.raw = (dialogs.raw + text)[-_SCREEN_RAW_LIMIT:]


def _tui_answers(extensions: dict[str, Any]) -> dict[str, Any]:
    """Return the ``agent_extensions.tui_answers`` mapping, if any."""
    answers = extensions.get("tui_answers")
    return answers if isinstance(answers, dict) else {}


def _read_turn_rows(
    path: Path,
    tail: _TranscriptTail,
    tools: _TurnTools,
    state: _TurnState,
) -> tuple[list[IREvent], _TranscriptTail, bool]:
    """Read new transcript rows and convert them to IR events.

    Returns:
        ``(events, new_tail, turn_done)``.

    Raises:
        OSError: If the transcript cannot be read.
    """
    rows, tail = _read_new_rows(path, tail)
    events: list[IREvent] = []
    done = False
    for row in rows:
        tools.feed(row)
        row_events, turn_done = _turn_row(row, state)
        events.extend(row_events)
        done = done or turn_done
    return events, tail, done


def _remove_stale(path: Path) -> str | None:
    """Delete a transcript this provider is about to rewrite.

    Returns:
        ``None`` on success, otherwise an error message.  On Windows an open
        transcript can refuse deletion with ``PermissionError``.
    """
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        return f"cannot replace the stale transcript {path}: {exc}"
    return None


def _fallback_providers() -> list[Any]:
    """Return the providers to try when the TUI cannot start.

    Native first: it uses the same ``claude`` binary, so a directory that
    needs trust or a missing login fails the same way there and the SDK is
    tried next.  The TUI provider itself is excluded to avoid recursion.
    """
    from .claude_sdk import ClaudeSDKProvider

    return [ClaudeNativeProvider, ClaudeSDKProvider]


class ClaudeTuiProvider:
    """Drive Claude Code's interactive TUI over a PTY."""

    @staticmethod
    def is_available() -> bool:
        """Return whether a supported PTY and the ``claude`` CLI exist."""
        return os.name in ("posix", "nt") and shutil.which("claude") is not None

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
            "supports_permissions": False,
            "supports_thinking": True,
            "supports_structured_output": False,
            "supports_ephemeral": False,
            "supports_additional_dirs": True,
            "supports_file_attachments": False,
            "supports_multi_turn": True,
            "transport": "conpty" if os.name == "nt" else "pty",
            "supported_settings": sorted(adapter_for("claude_tui").supported),
            "settings_mechanism": adapter_for("claude_tui").mechanism,
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

        resume = bool(task.get("resume"))
        cwd = task.get("working_dir") or os.getcwd()
        resolved = adapter_for("claude_tui").resolve(task)
        env = _build_env(task)
        env.update(resolved.env)
        target = self._session_target(task, cwd, resume, env)
        if isinstance(target, str):
            yield ErrorEvent(type="error", error=target, is_fatal=True)
            return
        session_id, path = target

        command = _build_command(binary, task, session_id, resume)
        command.extend(resolved.argv)
        command.extend(resolved.extra_args)

        proc: PtyProcess | None = None
        started = False
        ended = False
        try:
            try:
                proc = await PtyProcess.spawn(command, cwd=cwd, env=env)
            except (OSError, PtyStartupError) as exc:
                yield ErrorEvent(
                    type="error",
                    error=f"cannot start Claude Code: {exc}",
                    is_fatal=True,
                )
                return

            failure = await self._wait_ready(proc, task)
            if failure is not None:
                reason, screen = failure
                detail = self._startup_error(reason, cwd, proc, screen)
                if reason in ("trust", "login") and self._fallback_enabled(task):
                    async for event in self._fallback_stream(task, detail):
                        yield event
                    return
                yield ErrorEvent(type="error", error=detail, is_fatal=True)
                return

            # Do not replay history from a resumed session; only rows
            # written after this point belong to the current turn.
            tail = _TranscriptTail.at_end(path) if resume else _TranscriptTail()

            yield SessionStartEvent(
                type="session_start",
                session_id=session_id,
                agent="claude_code",
            )
            started = True
            await self._send_prompt(proc, task["prompt"], path, tail, task)
            async for event in self._turn_events(proc, path, session_id, task, tail):
                if event.get("type") == "session_end":
                    ended = True
                yield event
        except (_PromptSubmitError, _PtyReadError) as exc:
            yield ErrorEvent(type="error", error=str(exc), is_fatal=True)
            if started and not ended:
                yield SessionEndEvent(type="session_end", session_id=session_id)
        except Exception as exc:
            # The IR contract is an event stream: internal failures such as a
            # malformed timeout option must surface as ErrorEvent, not raise.
            yield ErrorEvent(
                type="error", error=f"Claude Code TUI failed: {exc}", is_fatal=True
            )
            if started and not ended:
                yield SessionEndEvent(type="session_end", session_id=session_id)
        finally:
            if proc is not None:
                try:
                    await proc.close()
                except Exception:
                    # Cleanup must not mask the turn's own outcome.
                    pass

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

    @staticmethod
    def _session_target(
        task: TaskConfig,
        cwd: str,
        resume: bool,
        env: Mapping[str, str],
    ) -> tuple[str, Path] | str:
        """Return the session id and transcript path, or an error message.

        Claude Code only accepts UUIDs for ``--session-id`` and
        ``--resume``.  A caller label is replaced with a fresh UUID for a
        new session; it cannot name a session to resume, so that is an
        error.  A caller-supplied id whose transcript already exists is
        never deleted: a fresh id is used instead.
        """
        requested_id = _session_uuid(task.get("session_id"))
        if task.get("session_id") and requested_id is None and resume:
            return (
                f"cannot resume session {task.get('session_id')!r}: "
                "Claude Code session ids are UUIDs"
            )
        session_id = requested_id or str(uuid.uuid4())
        path = task_transcript_path(task, cwd, session_id, env)
        if resume:
            return session_id, path
        if requested_id and path.exists():
            session_id = str(uuid.uuid4())
            path = task_transcript_path(task, cwd, session_id, env)
        problem = _remove_stale(path)
        if problem is not None:
            return problem
        return session_id, path

    async def _wait_ready(
        self, proc: PtyProcess, task: TaskConfig
    ) -> tuple[str, str] | None:
        """Wait for the input prompt.

        Returns:
            ``None`` when ready, otherwise ``(reason, screen_tail)``.
        """
        extensions = task.get("agent_extensions") or {}
        timeout = _float_option(
            extensions.get("ready_timeout"), 30.0, "agent_extensions.ready_timeout"
        )
        deadline = time.monotonic() + timeout
        screen = ""
        while time.monotonic() < deadline:
            chunk = await self._read_chunk(proc, 0.25)
            if chunk is None:
                continue
            if chunk == b"":
                return ("exited", screen[-800:])
            screen = (screen + strip_ansi(chunk.decode("utf-8", "replace")))[
                -_SCREEN_WINDOW:
            ]
            lowered = screen.lower()
            if any(marker in lowered for marker in _TRUST_MARKERS):
                return ("trust", screen[-800:])
            if any(marker in lowered for marker in _LOGIN_MARKERS):
                return ("login", screen[-800:])
            if _prompt_is_ready(screen):
                return None
        return ("timeout", screen[-800:])

    @staticmethod
    def _fallback_enabled(task: TaskConfig) -> bool:
        """Return whether a startup failure may delegate to another provider.

        ``prefer="pty"`` asks for the TUI's quota accounting, but an
        untrusted directory or a signed-out CLI would otherwise fail every
        prompt.  Set ``agent_extensions.tui_fallback`` to ``False`` to make
        that failure fatal instead.
        """
        extensions = task.get("agent_extensions") or {}
        return bool(extensions.get("tui_fallback", True))

    async def _fallback_stream(
        self, task: TaskConfig, detail: str
    ) -> AsyncIterator[IREvent]:
        """Run the task on the native or SDK transport after a TUI failure.

        The TUI failure is reported as a non-fatal error so a caller can
        see why the transport changed, then the fallback provider's own
        event stream follows.
        """
        for provider_cls in _fallback_providers():
            try:
                if not provider_cls.is_available():
                    continue
            except Exception:  # noqa: BLE001 - a broken optional import
                continue
            yield ErrorEvent(
                type="error",
                error=(
                    f"{detail} Falling back to {provider_cls.__name__} for "
                    "this task; set agent_extensions.tui_fallback=false to "
                    "make this failure fatal."
                ),
                is_fatal=False,
            )
            async for event in provider_cls().stream(task):
                yield event
            return
        yield ErrorEvent(
            type="error",
            error=(
                f"{detail} No fallback transport is available: neither the "
                "claude CLI nor claude-agent-sdk is installed."
            ),
            is_fatal=True,
        )

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

    async def _send_prompt(
        self,
        proc: PtyProcess,
        prompt: str,
        path: Path,
        tail: _TranscriptTail,
        task: TaskConfig,
    ) -> None:
        """Paste *prompt*, submit it with Enter, and confirm acceptance.

        A pasted prompt is only believed once the TUI renders it, and a
        submitted prompt only once the turn starts (a busy marker or a new
        transcript row).  Both waits are bounded; a dropped paste or Enter
        raises instead of leaving the caller to the turn timeout.

        Raises:
            _PromptSubmitError: If the prompt is unsafe to paste, the TUI
                does not echo it, or it does not start a turn in time.
        """
        problem = _prompt_problem(prompt)
        if problem is not None:
            raise _PromptSubmitError(problem)

        extensions = task.get("agent_extensions") or {}
        paste_timeout = _float_option(
            extensions.get("paste_timeout"),
            _PASTE_TIMEOUT,
            "agent_extensions.paste_timeout",
        )
        submit_timeout = _float_option(
            extensions.get("submit_timeout"),
            _SUBMIT_TIMEOUT,
            "agent_extensions.submit_timeout",
        )

        if prompt[:1] in ("/", "!", "#", "&"):
            # A leading slash would be read as a command; a newline keeps it text.
            prompt = "\n" + prompt

        try:
            await proc.write(_PASTE_START + prompt.encode() + _PASTE_END)
        except PtyWriteError as exc:
            raise _PromptSubmitError(
                f"cannot paste the prompt into Claude Code: {exc}"
            ) from exc
        await self._confirm_paste(proc, prompt, paste_timeout)

        try:
            await proc.write(b"\r")
        except PtyWriteError as exc:
            raise _PromptSubmitError(
                f"cannot submit the prompt to Claude Code: {exc}"
            ) from exc
        await self._confirm_submit(proc, path, tail, submit_timeout)

    async def _confirm_paste(
        self, proc: PtyProcess, prompt: str, timeout: float
    ) -> None:
        """Wait until the TUI echoes the pasted text back to the terminal."""
        fragment = _echo_fragment(prompt)
        if not fragment:
            return
        deadline = time.monotonic() + timeout
        screen = ""
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise _PromptSubmitError(
                    "Claude Code did not echo the pasted prompt within "
                    f"{timeout:g}s; the paste was dropped"
                )
            chunk = await self._read_chunk(proc, min(0.25, remaining))
            if chunk is None:
                continue
            if chunk == b"":
                raise _PromptSubmitError(
                    "Claude Code exited while the prompt was being pasted"
                )
            screen = (screen + strip_ansi(chunk.decode("utf-8", "replace")))[-8192:]
            if fragment in " ".join(screen.split()):
                return

    async def _confirm_submit(
        self, proc: PtyProcess, path: Path, tail: _TranscriptTail, timeout: float
    ) -> None:
        """Wait until the TUI starts a turn for the submitted prompt.

        A busy marker proves the TUI accepted Enter; a new transcript row is
        the authoritative signal and covers a turn that finishes before the
        marker is read.
        """
        deadline = time.monotonic() + timeout
        screen = ""
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise _PromptSubmitError(
                    f"Claude Code did not start a turn within {timeout:g}s "
                    "after Enter; the prompt was not submitted"
                )
            chunk = await self._read_chunk(proc, min(0.25, remaining))
            if chunk is None:
                pass
            elif chunk == b"":
                raise _PromptSubmitError("Claude Code exited before starting the turn")
            else:
                screen = (screen + strip_ansi(chunk.decode("utf-8", "replace")))[-8192:]
                if _screen_is_busy(screen):
                    return
            try:
                rows, _ = _read_new_rows(path, tail)
            except OSError:
                rows = []
            if rows:
                return

    async def _turn_events(
        self,
        proc: PtyProcess,
        path: Path,
        session_id: str,
        task: TaskConfig,
        tail: _TranscriptTail | None = None,
    ) -> AsyncIterator[IREvent]:
        """Read transcript rows until the turn ends, answering TUI dialogs.

        Only ``system/turn_duration`` and ``result`` rows end a turn.  Do
        not infer the boundary from an assistant ``stop_reason``: Claude
        Code splits one message across one JSONL row per content block and
        can append the text block seconds after the thinking block, so
        ending on ``end_turn`` would kill the CLI before the answer lands.

        After the turn ends the transcript is allowed to settle: Claude Code
        writes bookkeeping rows (``last-prompt``, ``cost-state``) just after
        ``turn_duration``, and closing the PTY mid-write would leave a
        partial row.  The settle window is bounded so a quiet CLI does not
        delay the return.

        A modal blocks the CLI until it is answered.  Each detected dialog is
        answered from ``agent_extensions.tui_answers`` or turned into a fatal
        ``ErrorEvent`` prefixed with ``tui_dialog_unanswered:``, so a turn
        never waits for the full timeout on a prompt.  Detection keeps running
        through the settle window.
        """
        timeout = _float_option(task.get("timeout") or None, 600.0, "timeout")
        extensions = task.get("agent_extensions") or {}
        settle_timeout = _float_option(
            extensions.get("settle_timeout"),
            _SETTLE_TIMEOUT,
            "agent_extensions.settle_timeout",
        )
        poll = _float_option(
            extensions.get("dialog_poll"), 0.1, "agent_extensions.dialog_poll"
        )
        grace = _float_option(
            extensions.get("dialog_grace"), 1.0, "agent_extensions.dialog_grace"
        )
        answer_settle = _float_option(
            extensions.get("answer_settle"), 2.0, "agent_extensions.answer_settle"
        )
        answers = _tui_answers(extensions)
        deadline = time.monotonic() + timeout
        state = _TurnState()
        tail = tail if tail is not None else _TranscriptTail()
        dialogs = _DialogSession(
            tools=_TurnTools(),
            answers=answers,
            grace=grace,
            answer_settle=answer_settle,
        )
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
                try:
                    chunk = await self._read_chunk(proc, min(poll, remaining))
                except _PtyReadError as exc:
                    yield ErrorEvent(type="error", error=str(exc), is_fatal=True)
                    break
                if chunk == b"":
                    exited = True
                else:
                    _append_screen(dialogs, chunk)

            try:
                events, tail, done = _read_turn_rows(path, tail, dialogs.tools, state)
            except OSError as exc:
                yield ErrorEvent(
                    type="error",
                    error=f"cannot read Claude Code transcript {path}: {exc}",
                    is_fatal=True,
                )
                break
            for event in events:
                yield event
            if done:
                break
            if exited:
                yield ErrorEvent(
                    type="error",
                    error=(
                        "Claude Code exited before the turn completed "
                        f"(code {proc.returncode})"
                    ),
                    is_fatal=True,
                )
                break

            step = await self._poll_dialog(proc, path, tail, dialogs)
            dialogs.raw = step.raw
            exited = step.exited
            for event in step.events:
                yield event
            if step.fatal:
                break

        if done:
            async for event in self._settle(
                proc, path, tail, state, settle_timeout, dialogs
            ):
                yield event

        yield SessionEndEvent(type="session_end", session_id=session_id)

    async def _settle(
        self,
        proc: PtyProcess,
        path: Path,
        tail: _TranscriptTail,
        state: _TurnState,
        settle_timeout: float,
        dialogs: _DialogSession | None = None,
    ) -> AsyncIterator[IREvent]:
        """Read the bookkeeping rows Claude Code writes after a turn.

        The CLI appends ``last-prompt`` and ``cost-state`` after
        ``turn_duration``.  Waiting for them keeps a kill from landing in
        the middle of a write, and lets a late ``result`` row supply
        ``cost_usd``.  The wait ends when no row arrives for
        ``_SETTLE_QUIET`` seconds, at ``settle_timeout``, or at end of
        stream.

        A dialog can appear after the turn ends (a billing notice, for
        example), so detection keeps running while the transcript settles.
        """
        if settle_timeout <= 0:
            return
        deadline = time.monotonic() + settle_timeout
        quiet_since = time.monotonic()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            try:
                chunk = await self._read_chunk(proc, min(0.05, remaining))
            except _PtyReadError:
                return
            if chunk == b"":
                return
            _append_screen(dialogs, chunk)
            try:
                rows, tail = _read_new_rows(path, tail)
            except OSError:
                return
            if rows:
                quiet_since = time.monotonic()
            elif time.monotonic() - quiet_since >= _SETTLE_QUIET:
                return
            for row in rows:
                if dialogs is not None:
                    dialogs.tools.feed(row)
                events, _ = _turn_row(row, state)
                for event in events:
                    yield event
            events, fatal, visible = await self._settle_dialog_step(
                proc, path, tail, dialogs
            )
            for event in events:
                yield event
            if fatal:
                return
            if visible:
                # A visible dialog is activity; keep the window open.
                quiet_since = time.monotonic()

    async def _settle_dialog_step(
        self,
        proc: PtyProcess,
        path: Path,
        tail: _TranscriptTail,
        dialogs: _DialogSession | None,
    ) -> tuple[list[IREvent], bool, bool]:
        """Poll a dialog while the transcript settles.

        Returns:
            ``(events, fatal, visible)``; *visible* keeps the window open.
        """
        if dialogs is None:
            return [], False, False
        step = await self._poll_dialog(proc, path, tail, dialogs)
        return step.events, step.fatal, dialogs.previous is not None

    async def _poll_dialog(
        self,
        proc: PtyProcess,
        path: Path,
        tail: _TranscriptTail,
        session: _DialogSession,
    ) -> _DialogStep:
        """Answer one poll's dialog, updating *session* state.

        A dialog is only acted on after two consecutive polls agree on its
        type, so a half-drawn frame cannot trigger a key press.
        """
        dialog = _live_dialog(
            session.raw[-_SCREEN_LIMIT:], session.tools, session.grace
        )
        idle = _DialogStep(answered=False, events=[], raw=session.raw, exited=False)
        if dialog is None:
            # A notice may legitimately appear twice; a tool-backed dialog
            # cannot, so its latch outlives the footer.
            if session.latched in _TOOLLESS_DIALOGS:
                session.latched = None
            session.previous = None
            return idle
        if session.previous != dialog.type:
            session.previous = dialog.type
            return idle
        step = await self._answer_dialog(
            proc,
            path,
            tail,
            session.tools,
            dialog,
            session.answers,
            session.grace,
            session.answer_settle,
            session.raw,
            session.latched,
        )
        session.raw = step.raw
        if step.fatal:
            return step
        if not step.answered:
            session.previous = None
            return step
        session.latched = step.key
        return step

    async def _answer_dialog(
        self,
        proc: PtyProcess,
        path: Path,
        tail: _TranscriptTail,
        tools: _TurnTools,
        dialog: Dialog,
        answers: dict[str, Any],
        grace: float,
        settle: float,
        raw: str,
        latched: str | None,
    ) -> _DialogStep:
        """Answer *dialog* from config, or fail fast when it has no answer."""
        pending = await self._dialog_pending(path, tail, tools, dialog, grace)
        if not _corroborated(dialog, pending):
            if tools.saw_tool_use:
                # Either the frame is stale or the transcript row is still in
                # flight; a later poll decides.
                return _DialogStep(answered=False, events=[], raw=raw, exited=False)
            return self._dialog_failure(
                dialog,
                "the transcript does not show the tool call behind this dialog",
                raw,
            )
        key = dialog.type
        if dialog.type not in _TOOLLESS_DIALOGS and pending is not None:
            key = pending.tool_use_id
        if key == latched:
            return _DialogStep(answered=False, events=[], raw=raw, exited=False)
        try:
            keys = _resolve_dialog(dialog, pending, answers)
        except _DialogUnanswered as exc:
            return self._dialog_failure(dialog, str(exc), raw)
        try:
            await self._send_answer(proc, keys)
        except PtyWriteError as exc:
            return self._dialog_failure(dialog, f"cannot write the answer: {exc}", raw)
        closed, raw, exited = await self._await_dialog_close(
            proc, path, tail, settle, raw, dialog
        )
        if not closed and not exited:
            # One Enter is the documented fallback for a stuck dialog.
            try:
                await proc.write(b"\r")
            except PtyWriteError as exc:
                return self._dialog_failure(
                    dialog, f"cannot write the fallback Enter: {exc}", raw
                )
            closed, raw, exited = await self._await_dialog_close(
                proc, path, tail, min(settle, 1.0), raw, dialog
            )
        if not closed and not exited:
            return self._dialog_failure(
                dialog,
                "the dialog did not close after the answer; the configured "
                "keys did not apply",
                raw,
            )
        return _DialogStep(answered=True, events=[], raw=raw, exited=exited, key=key)

    async def _dialog_pending(
        self,
        path: Path,
        tail: _TranscriptTail,
        tools: _TurnTools,
        dialog: Dialog,
        grace: float,
    ) -> _PendingTool | None:
        """Return the transcript ``tool_use`` behind *dialog*.

        The transcript row can land a poll after the screen frame, so wait up
        to *grace* for it before resolving the answer.
        """
        _peek_tools(path, tail, tools)
        pending = tools.pending()
        if _corroborated(dialog, pending):
            return pending
        deadline = time.monotonic() + max(grace, 0.0)
        while time.monotonic() < deadline:
            await asyncio.sleep(0.05)
            _peek_tools(path, tail, tools)
            pending = tools.pending()
            if _corroborated(dialog, pending):
                return pending
        return tools.pending()

    @staticmethod
    async def _send_answer(proc: PtyProcess, keys: tuple[bytes, ...]) -> None:
        """Write one answer key per write, with a pause between writes.

        A bundled digit and Enter was unreliable on the plan dialog; the
        verified sequence sends each key on its own.
        """
        for index, key in enumerate(keys):
            if index:
                await asyncio.sleep(_ANSWER_KEY_GAP)
            await proc.write(key)

    async def _await_dialog_close(
        self,
        proc: PtyProcess,
        path: Path,
        tail: _TranscriptTail,
        settle: float,
        raw: str,
        dialog: Dialog,
    ) -> tuple[bool, str, bool]:
        """Wait for *dialog* to close after an answer.

        Returns:
            ``(closed, raw_screen, exited)``.
        """
        family = _DIALOG_FAMILY.get(dialog.type, dialog.type)
        deadline = time.monotonic() + max(settle, 0.0)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False, raw, False
            chunk = await self._read_chunk(proc, min(0.1, remaining))
            if chunk == b"":
                return False, raw, True
            if chunk:
                text = strip_ansi(chunk.decode("utf-8", "replace"))
                raw = (raw + text)[-_SCREEN_RAW_LIMIT:]
            current = detect_dialog(raw[-_SCREEN_LIMIT:])
            if current is None:
                return True, raw, False
            if _DIALOG_FAMILY.get(current.type, current.type) != family:
                return True, raw, False
            if dialog.tool_use_id and _tool_result_present(
                path, tail, dialog.tool_use_id
            ):
                return True, raw, False

    @staticmethod
    def _dialog_failure(dialog: Dialog, reason: str, raw: str) -> _DialogStep:
        """Build a fatal step for a dialog that cannot be answered."""
        event = ErrorEvent(
            type="error",
            error=f"tui_dialog_unanswered: {dialog.type}: {reason}",
            is_fatal=True,
        )
        return _DialogStep(answered=False, events=[event], raw=raw, exited=False)

    @staticmethod
    async def _read_chunk(proc: PtyProcess, timeout: float) -> bytes | None:
        """Read a PTY chunk, returning ``None`` when *timeout* elapses.

        Raises:
            _PtyReadError: If the terminal itself cannot be read.
        """
        try:
            return await asyncio.wait_for(proc.read(), timeout)
        except asyncio.TimeoutError:
            return None
        except OSError as exc:
            raise _PtyReadError(f"Claude Code terminal read failed: {exc}") from exc


__all__ = [
    "ClaudeTuiProvider",
    "Dialog",
    "detect_dialog",
    "strip_ansi",
    "transcript_path",
]
