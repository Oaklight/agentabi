"""Tests for the Claude Code TUI (PTY) provider."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, cast

import pytest

from agentabi._vendor.ptyx import PtyProcess, PtyWriteError
from agentabi.providers import claude_tui as claude_tui_module
from agentabi.providers import registry
from agentabi.providers.claude_native import ClaudeNativeProvider
from agentabi.providers.claude_sdk import ClaudeSDKProvider
from agentabi.providers.claude_tui import (
    ClaudeTuiProvider,
    _build_command,
    _prompt_is_ready,
    _prompt_problem,
    _PromptSubmitError,
    _read_new_rows,
    _session_uuid,
    _slug_from_resolved,
    _TranscriptTail,
    _turn_row,
    _TurnState,
    _usage_from_raw,
    task_transcript_path,
    transcript_path,
)
from agentabi.types.ir.task import TaskConfig

_FAKE_TUI = r"""#!__PYTHON__
import json, os, re, sys, time


def arg(name):
    try:
        return sys.argv[sys.argv.index(name) + 1]
    except ValueError:
        return None


sid = arg("--session-id") or arg("--resume") or "fake-session"
cwd = os.getcwd()
base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
slug = re.sub(r"[^A-Za-z0-9]", "-", cwd)
path = os.path.join(base, "projects", slug, sid + ".jsonl")
os.makedirs(os.path.dirname(path), exist_ok=True)

sys.stdout.write("❯ ")
sys.stdout.flush()

buf = b""
while b"\n" not in buf and b"\r" not in buf:
    ch = os.read(0, 1)
    if not ch:
        sys.exit(0)
    buf += ch
text = buf.decode("utf-8", "replace")
text = text.replace("\x1b[200~", "").replace("\x1b[201~", "")
text = text.replace("\r", "\n").split("\n")[0]

rows = [
    {"type": "assistant", "message": {"id": "msg_1", "role": "assistant",
     "content": [{"type": "text", "text": "ECHO: " + text}],
     "usage": {"input_tokens": 10, "output_tokens": 2}}},
    {"type": "system", "subtype": "turn_duration"},
]
with open(path, "a", encoding="utf-8") as fh:
    for row in rows:
        fh.write(json.dumps(row) + "\n")
time.sleep(0.2)
while True:
    if not os.read(0, 1):
        break
"""

_TRUST_TUI = r"""#!__PYTHON__
import sys, time
sys.stdout.write("Do you trust this folder?\n")
sys.stdout.flush()
time.sleep(5)
"""

_SPLIT_MESSAGE_TUI = r"""#!__PYTHON__
import json, os, re, sys, time


def arg(name):
    try:
        return sys.argv[sys.argv.index(name) + 1]
    except ValueError:
        return None


sid = arg("--session-id") or arg("--resume") or "fake-session"
cwd = os.getcwd()
base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
slug = re.sub(r"[^A-Za-z0-9]", "-", cwd)
path = os.path.join(base, "projects", slug, sid + ".jsonl")
os.makedirs(os.path.dirname(path), exist_ok=True)

sys.stdout.write("❯ ")
sys.stdout.flush()

buf = b""
while b"\n" not in buf and b"\r" not in buf:
    ch = os.read(0, 1)
    if not ch:
        sys.exit(0)
    buf += ch
text = buf.decode("utf-8", "replace")
text = text.replace("\x1b[200~", "").replace("\x1b[201~", "")
text = text.replace("\r", "\n").split("\n")[0]


def append(row):
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


# The real transcript writes one row per content block and can delay the
# text block well past the thinking block; both carry stop_reason=end_turn.
append({"type": "assistant", "message": {"id": "msg_2", "role": "assistant",
        "content": [{"type": "thinking", "thinking": "pondering"}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 7, "output_tokens": 3}}})
time.sleep(1.2)
append({"type": "assistant", "message": {"id": "msg_2", "role": "assistant",
        "content": [{"type": "text", "text": "DONE: " + text}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 7, "output_tokens": 3}}})
time.sleep(0.2)
append({"type": "system", "subtype": "turn_duration"})
while True:
    if not os.read(0, 1):
        break
"""


def _write_script(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "fake_claude.py"
    path.write_text(body.replace("__PYTHON__", sys.executable))
    path.chmod(0o755)
    return path


_SCRIPT_HEADER = r"""#!__PYTHON__
import json, os, re, sys, time


def arg(name):
    try:
        return sys.argv[sys.argv.index(name) + 1]
    except ValueError:
        return None


sid = arg("--session-id") or arg("--resume") or "fake-session"
cwd = os.getcwd()
base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
slug = re.sub(r"[^A-Za-z0-9]", "-", cwd)
path = os.path.join(base, "projects", slug, sid + ".jsonl")
os.makedirs(os.path.dirname(path), exist_ok=True)

sys.stdout.write("❯ ")
sys.stdout.flush()

buf = b""
while b"\n" not in buf and b"\r" not in buf:
    ch = os.read(0, 1)
    if not ch:
        sys.exit(0)
    buf += ch
text = buf.decode("utf-8", "replace")
text = text.replace("\x1b[200~", "").replace("\x1b[201~", "")
text = text.replace("\r", "\n").split("\n")[0]


def append(row):
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


def wait():
    while True:
        if not os.read(0, 1):
            break
"""


def _script(body: str) -> str:
    """Combine the fake TUI header with a per-test row-writing body."""
    return _SCRIPT_HEADER + body


_API_RETRY_TUI = _script(
    r"""
append({"type": "system", "subtype": "api_error", "level": "error",
        "error": {"message": "502 upstream unavailable", "status": 502},
        "retryAttempt": 1, "maxRetries": 10})
append({"type": "assistant", "message": {"id": "msg_ok", "role": "assistant",
        "content": [{"type": "text", "text": "RECOVERED: " + text}],
        "usage": {"input_tokens": 5, "output_tokens": 1}}})
append({"type": "system", "subtype": "turn_duration"})
wait()
"""
)

_API_EXHAUSTED_TUI = _script(
    r"""
append({"type": "system", "subtype": "api_error", "level": "error",
        "error": {"message": "502 upstream unavailable", "status": 502},
        "retryAttempt": 10, "maxRetries": 10})
wait()
"""
)

_API_MESSAGE_TUI = _script(
    r"""
append({"type": "assistant", "isApiErrorMessage": True, "error": "server_error",
        "apiErrorStatus": 502,
        "message": {"id": "msg_err", "role": "assistant", "model": "<synthetic>",
                    "content": [{"type": "text",
                                 "text": "API Error: 502 bad gateway"}],
                    "usage": {"input_tokens": 0, "output_tokens": 0}}})
wait()
"""
)

_RESULT_TUI = _script(
    r"""
append({"type": "assistant", "message": {"id": "msg_r", "role": "assistant",
        "content": [{"type": "text", "text": "RESULT: " + text}],
        "usage": {"input_tokens": 3, "output_tokens": 2}}})
append({"type": "result", "subtype": "success", "session_id": sid,
        "usage": {"input_tokens": 3, "output_tokens": 2},
        "total_cost_usd": 0.01})
wait()
"""
)


def _task(
    tmp_path: Path, binary: Path, prompt: str = "hello", timeout: float = 600.0
) -> TaskConfig:
    return cast(
        TaskConfig,
        {
            "prompt": prompt,
            "working_dir": str(tmp_path),
            "timeout": timeout,
            "env": {"CLAUDE_CONFIG_DIR": str(tmp_path / "claude")},
            "agent_extensions": {
                "claude_binary": str(binary),
                "ready_timeout": 5.0,
            },
        },
    )


async def test_stream_reads_transcript(tmp_path):
    binary = _write_script(tmp_path, _FAKE_TUI)
    provider = ClaudeTuiProvider()
    events = [event async for event in provider.stream(_task(tmp_path, binary))]

    ends = [event for event in events if event["type"] == "message_end"]
    assert ends
    assert ends[0]["text"] == "ECHO: hello"
    deltas = [event for event in events if event["type"] == "message_delta"]
    assert deltas
    assert deltas[0]["text"] == "ECHO: hello"
    kinds = [event["type"] for event in events]
    assert kinds.index("message_delta") < kinds.index("message_end")
    assert any(event["type"] == "session_end" for event in events)
    assert not [
        event for event in events if event["type"] == "error" and event.get("is_fatal")
    ]


async def test_trust_prompt_fails_with_clear_error(tmp_path):
    binary = _write_script(tmp_path, _TRUST_TUI)
    provider = ClaudeTuiProvider()
    task = _task(tmp_path, binary)
    task["agent_extensions"]["tui_fallback"] = False  # type: ignore[index]
    events = [event async for event in provider.stream(task)]

    errors = [event for event in events if event["type"] == "error"]
    assert errors
    assert errors[0].get("is_fatal") is True
    assert "trust" in errors[0]["error"].lower()


async def test_existing_transcript_is_not_deleted_without_resume(tmp_path):
    """A caller-supplied session id must not erase an existing transcript."""
    binary = _write_script(tmp_path, _FAKE_TUI)
    path = transcript_path(str(tmp_path), "existing-sid", str(tmp_path / "claude"))
    path.parent.mkdir(parents=True, exist_ok=True)
    original = '{"type": "user", "message": {"content": "old"}}\n'
    path.write_text(original)

    task = _task(tmp_path, binary)
    task["session_id"] = "existing-sid"
    events = [event async for event in ClaudeTuiProvider().stream(task)]

    assert path.exists()
    assert path.read_text() == original
    starts = [event for event in events if event["type"] == "session_start"]
    assert starts
    assert starts[0]["session_id"] != "existing-sid"
    assert [event["text"] for event in events if event["type"] == "message_end"] == [
        "ECHO: hello"
    ]


def test_capabilities_do_not_claim_permission_dialogs():
    caps = ClaudeTuiProvider().capabilities()
    assert caps["supports_permissions"] is False


async def test_split_message_keeps_late_text_block(tmp_path):
    """A delayed text block must not be lost to an early turn end."""
    binary = _write_script(tmp_path, _SPLIT_MESSAGE_TUI)
    provider = ClaudeTuiProvider()
    started = time.monotonic()
    events = [
        event async for event in provider.stream(_task(tmp_path, binary, timeout=5.0))
    ]
    elapsed = time.monotonic() - started

    ends = [event for event in events if event["type"] == "message_end"]
    texts = [event.get("text") for event in ends if event.get("text")]
    assert texts == ["DONE: hello"]
    reasoning = [event for event in events if event["type"] == "reasoning_delta"]
    assert [event["reasoning"] for event in reasoning] == ["pondering"]
    usage = [event for event in events if event["type"] == "usage"]
    assert len(usage) == 1
    assert usage[0]["usage"]["total_tokens"] == 10
    # The thinking row carried end_turn; the turn must wait for the text row.
    assert elapsed >= 1.1
    assert not [
        event for event in events if event["type"] == "error" and event.get("is_fatal")
    ]


def test_build_command_uses_interactive_flags():
    task = cast(
        TaskConfig,
        {
            "prompt": "hello",
            "model": "opus",
            "permissions": {"level": "accept_edits", "allowed_tools": ["Read"]},
            "append_system_prompt": "be brief",
            "thinking_level": "high",
        },
    )
    cmd = _build_command("claude", task, "sid-1", resume=False)
    assert cmd[:2] == ["claude", "--no-chrome"]
    assert "--session-id" in cmd
    assert "sid-1" in cmd
    assert "--permission-mode" in cmd
    assert "acceptEdits" in cmd
    assert "--allowedTools" in cmd
    assert "--append-system-prompt" in cmd
    assert "--effort" in cmd
    assert "-p" not in cmd
    assert "--print" not in cmd


def test_build_command_resumes_session():
    task = cast(TaskConfig, {"prompt": "hi"})
    cmd = _build_command("claude", task, "sid-2", resume=True)
    assert "--resume" in cmd
    assert "--session-id" not in cmd


def test_transcript_path_uses_config_dir(tmp_path):
    path = transcript_path("/home/x/proj", "sid-3", str(tmp_path))
    assert path == tmp_path / "projects" / "-home-x-proj" / "sid-3.jsonl"


def test_transcript_slug_handles_windows_paths():
    assert _slug_from_resolved("/home/x/proj") == "-home-x-proj"
    assert _slug_from_resolved(r"C:\Users\user\probe") == "C--Users-user-probe"


def test_transcript_slug_replaces_dots_and_underscores():
    assert _slug_from_resolved("/home/x/.local_proj") == "-home-x--local-proj"
    assert _slug_from_resolved("/home/x/My Proj") == "-home-x-My-Proj"


def test_assistant_end_turn_is_not_a_turn_boundary():
    row = {
        "type": "assistant",
        "message": {"id": "msg-1", "stop_reason": "end_turn", "content": []},
    }
    events, turn_done = _turn_row(row, _TurnState())
    assert events
    assert turn_done is False

    duration = {"type": "system", "subtype": "turn_duration"}
    assert _turn_row(duration, _TurnState()) == ([], True)


def test_prefer_pty_selects_tui(monkeypatch):
    for cls in (ClaudeNativeProvider, ClaudeSDKProvider, ClaudeTuiProvider):
        monkeypatch.setattr(cls, "is_available", staticmethod(lambda: True))
    provider = registry.resolve_provider("claude_code", prefer="pty")
    assert isinstance(provider, ClaudeTuiProvider)


async def test_resume_skips_existing_transcript(tmp_path):
    binary = _write_script(tmp_path, _FAKE_TUI)
    provider = ClaudeTuiProvider()
    session_id = str(uuid.uuid4())

    first = _task(tmp_path, binary, "first")
    first["session_id"] = session_id
    first_events = [event async for event in provider.stream(first)]
    assert [
        event["text"] for event in first_events if event["type"] == "message_end"
    ] == ["ECHO: first"]

    second = _task(tmp_path, binary, "second")
    second["session_id"] = session_id
    second["resume"] = True
    second_events = [event async for event in provider.stream(second)]
    assert [
        event["text"] for event in second_events if event["type"] == "message_end"
    ] == ["ECHO: second"]


# ========== API errors end the turn instead of timing out ==========


def test_system_api_error_ends_turn_when_retries_are_exhausted():
    row = {
        "type": "system",
        "subtype": "api_error",
        "level": "error",
        "error": {"message": "502 upstream unavailable", "status": 502},
        "retryAttempt": 10,
        "maxRetries": 10,
    }
    events, turn_done = _turn_row(row, _TurnState())

    assert turn_done is True
    assert len(events) == 1
    assert events[0]["type"] == "error"
    assert events[0]["is_fatal"] is True
    assert "502" in events[0]["error"]
    assert "upstream unavailable" in events[0]["error"]


def test_system_api_error_stays_non_fatal_while_retries_remain():
    row = {
        "type": "system",
        "subtype": "api_error",
        "error": {"message": "502 upstream unavailable", "status": 502},
        "retryAttempt": 1,
        "maxRetries": 10,
    }
    events, turn_done = _turn_row(row, _TurnState())

    assert turn_done is False
    assert events[0]["type"] == "error"
    assert events[0]["is_fatal"] is False
    assert "retry 1/10" in events[0]["error"]


def test_system_api_error_without_retry_budget_ends_turn():
    events, turn_done = _turn_row(
        {"type": "system", "subtype": "api_error", "error": "socket closed"},
        _TurnState(),
    )

    assert turn_done is True
    assert events[0]["is_fatal"] is True
    assert "socket closed" in events[0]["error"]


def test_assistant_api_error_message_becomes_fatal_error():
    row = {
        "type": "assistant",
        "isApiErrorMessage": True,
        "error": "server_error",
        "apiErrorStatus": 502,
        "message": {
            "id": "msg-err",
            "role": "assistant",
            "content": [{"type": "text", "text": "API Error: 502 bad gateway"}],
        },
    }
    events, turn_done = _turn_row(row, _TurnState())

    assert turn_done is True
    assert [event["type"] for event in events] == ["error"]
    assert events[0]["is_fatal"] is True
    assert "502" in events[0]["error"]
    assert "API Error" in events[0]["error"]


async def test_exhausted_api_error_row_ends_turn_promptly(tmp_path):
    binary = _write_script(tmp_path, _API_EXHAUSTED_TUI)
    started = time.monotonic()
    events = [
        event
        async for event in ClaudeTuiProvider().stream(
            _task(tmp_path, binary, timeout=30.0)
        )
    ]
    elapsed = time.monotonic() - started

    errors = [event for event in events if event["type"] == "error"]
    assert errors
    assert errors[0]["is_fatal"] is True
    assert "timed out" not in errors[0]["error"]
    assert elapsed < 10.0
    assert [event["type"] for event in events].count("session_end") == 1


async def test_retrying_api_error_does_not_end_the_turn(tmp_path):
    binary = _write_script(tmp_path, _API_RETRY_TUI)
    events = [
        event async for event in ClaudeTuiProvider().stream(_task(tmp_path, binary))
    ]

    texts = [event["text"] for event in events if event["type"] == "message_end"]
    assert texts == ["RECOVERED: hello"]
    errors = [event for event in events if event["type"] == "error"]
    assert errors and errors[0]["is_fatal"] is False


async def test_assistant_api_error_message_is_not_answer_text(tmp_path):
    binary = _write_script(tmp_path, _API_MESSAGE_TUI)
    events = [
        event
        async for event in ClaudeTuiProvider().stream(
            _task(tmp_path, binary, timeout=30.0)
        )
    ]

    errors = [event for event in events if event["type"] == "error"]
    assert errors and errors[0]["is_fatal"] is True
    assert "502 bad gateway" in errors[0]["error"]
    assert [event for event in events if event["type"] == "message_end"] == []


# ========== Internal failures surface as ErrorEvent ==========


class _RaisingProc:
    """A PTY stand-in whose reads fail."""

    returncode = None

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    async def read(self, size: int = 65536) -> bytes:
        raise self._exc


class _ExitedProc:
    """A PTY stand-in that reports end of stream."""

    returncode = 1

    async def read(self, size: int = 65536) -> bytes:
        return b""


async def test_pty_read_failure_becomes_error_event(tmp_path):
    proc = cast(PtyProcess, _RaisingProc(OSError("EIO")))
    task = cast(TaskConfig, {"timeout": 30.0})
    path = tmp_path / "missing.jsonl"

    events = [
        event
        async for event in ClaudeTuiProvider()._turn_events(  # noqa: SLF001
            proc, path, "sid", task, _TranscriptTail()
        )
    ]

    errors = [event for event in events if event["type"] == "error"]
    assert errors
    assert errors[0]["is_fatal"] is True
    assert "terminal read failed" in errors[0]["error"]


async def test_transcript_read_failure_becomes_error_event(tmp_path):
    # A directory cannot be opened as a file: reading it raises OSError.
    proc = cast(PtyProcess, _ExitedProc())
    task = cast(TaskConfig, {"timeout": 30.0})
    events = [
        event
        async for event in ClaudeTuiProvider()._turn_events(  # noqa: SLF001
            proc, tmp_path, "sid", task, _TranscriptTail()
        )
    ]

    errors = [event for event in events if event["type"] == "error"]
    assert errors
    assert errors[0]["is_fatal"] is True
    assert "cannot read Claude Code transcript" in errors[0]["error"]


async def test_invalid_timeout_becomes_error_event(tmp_path):
    binary = _write_script(tmp_path, _FAKE_TUI)
    task = _task(tmp_path, binary, timeout=30.0)
    task["timeout"] = "not-a-number"  # type: ignore[typeddict-item]

    events = [event async for event in ClaudeTuiProvider().stream(task)]

    errors = [event for event in events if event["type"] == "error"]
    assert errors
    assert errors[0]["is_fatal"] is True
    assert "invalid timeout" in errors[0]["error"]
    assert [event["type"] for event in events].count("session_end") == 1


async def test_invalid_ready_timeout_becomes_error_event(tmp_path):
    binary = _write_script(tmp_path, _FAKE_TUI)
    task = _task(tmp_path, binary)
    task["agent_extensions"] = {
        "claude_binary": str(binary),
        "ready_timeout": "soon",
    }

    events = [event async for event in ClaudeTuiProvider().stream(task)]

    errors = [event for event in events if event["type"] == "error"]
    assert errors
    assert errors[0]["is_fatal"] is True
    assert "invalid agent_extensions.ready_timeout" in errors[0]["error"]


# ========== A result row emits session_end once ==========


def test_result_row_does_not_emit_session_end():
    row = {"type": "result", "subtype": "success", "session_id": "sid-1"}
    events, turn_done = _turn_row(row, _TurnState())

    assert turn_done is True
    assert [event["type"] for event in events].count("session_end") == 0
    # A result row with no usage counters reports no usage event; an empty
    # usage event would make consumers think the turn was measured.
    assert not [event for event in events if event["type"] == "usage"]


async def test_result_row_yields_one_session_end(tmp_path):
    binary = _write_script(tmp_path, _RESULT_TUI)
    events = [
        event async for event in ClaudeTuiProvider().stream(_task(tmp_path, binary))
    ]

    assert [event["type"] for event in events].count("session_end") == 1
    assert [event["text"] for event in events if event["type"] == "message_end"] == [
        "RESULT: hello"
    ]


# ========== Prompt submission is confirmed and bounded ==========


def _problem(prompt: str) -> str:
    problem = _prompt_problem(prompt)
    assert problem is not None
    return problem


def test_prompt_problem_rejects_control_characters():
    assert "U+001B" in _problem("hi\x1b[201~rm -rf /")
    assert "U+000D" in _problem("a\rb")
    assert "U+009B" in _problem("a\x9bb")
    assert "U+007F" in _problem("a\x7fb")
    assert _prompt_problem("line one\nline two\tindented") is None


class _RecordingProc:
    """A PTY stand-in that records writes."""

    returncode = None

    def __init__(self) -> None:
        self.written: list[bytes] = []

    async def write(self, data: bytes) -> None:
        self.written.append(data)

    async def read(self, size: int = 65536) -> bytes:
        return b""


class _WriteFailingProc:
    """A PTY stand-in whose nth write fails."""

    returncode = None

    def __init__(self, fail_on: int, echo: bytes = b"") -> None:
        self.fail_on = fail_on
        self.echo = echo
        self.writes = 0

    async def write(self, data: bytes) -> None:
        if self.writes == self.fail_on:
            raise PtyWriteError("terminal write failed after 0 of 4 bytes: EIO")
        self.writes += 1

    async def read(self, size: int = 65536) -> bytes:
        if self.echo:
            data, self.echo = self.echo, b""
            return data
        return b""


class _SilentProc:
    """A PTY stand-in that never produces output."""

    returncode = None

    async def write(self, data: bytes) -> None:
        return None

    async def read(self, size: int = 65536) -> bytes:
        await asyncio.sleep(10)
        return b""


class _BusyProc:
    """A PTY stand-in that renders a busy screen once."""

    returncode = None

    def __init__(self, chunk: bytes) -> None:
        self.chunk = chunk

    async def write(self, data: bytes) -> None:
        return None

    async def read(self, size: int = 65536) -> bytes:
        if self.chunk:
            data, self.chunk = self.chunk, b""
            return data
        await asyncio.sleep(10)
        return b""


async def test_escape_injection_is_rejected_before_any_write(tmp_path):
    proc = _RecordingProc()
    task = cast(TaskConfig, {"prompt": "ignored"})

    with pytest.raises(_PromptSubmitError) as info:
        await ClaudeTuiProvider()._send_prompt(  # noqa: SLF001
            cast(PtyProcess, proc),
            "hi\x1b[201~rm -rf /",
            tmp_path / "t.jsonl",
            _TranscriptTail(),
            task,
        )

    assert proc.written == []
    assert "U+001B" in str(info.value)
    assert "ESC[201~" in str(info.value)


async def test_escape_injection_through_stream_is_a_fatal_error(tmp_path):
    binary = _write_script(tmp_path, _FAKE_TUI)
    task = _task(tmp_path, binary, "hello\x1b[201~rm -rf /")

    events = [event async for event in ClaudeTuiProvider().stream(task)]

    errors = [event for event in events if event["type"] == "error"]
    assert errors
    assert errors[0]["is_fatal"] is True
    assert "control character" in errors[0]["error"]
    assert not [event for event in events if event["type"] == "message_end"]
    assert [event["type"] for event in events].count("session_end") == 1


async def test_paste_write_failure_is_reported(tmp_path):
    proc = _WriteFailingProc(fail_on=0)
    task = cast(TaskConfig, {"prompt": "hello"})

    with pytest.raises(_PromptSubmitError, match="cannot paste"):
        await ClaudeTuiProvider()._send_prompt(  # noqa: SLF001
            cast(PtyProcess, proc),
            "hello",
            tmp_path / "t.jsonl",
            _TranscriptTail(),
            task,
        )


async def test_submit_write_failure_is_reported(tmp_path):
    proc = _WriteFailingProc(fail_on=1, echo=b"hello")
    task = cast(TaskConfig, {"prompt": "hello"})

    with pytest.raises(_PromptSubmitError, match="cannot submit"):
        await ClaudeTuiProvider()._send_prompt(  # noqa: SLF001
            cast(PtyProcess, proc),
            "hello",
            tmp_path / "t.jsonl",
            _TranscriptTail(),
            task,
        )


async def test_paste_confirmation_times_out():
    proc = _SilentProc()

    with pytest.raises(_PromptSubmitError, match="did not echo"):
        await ClaudeTuiProvider()._confirm_paste(  # noqa: SLF001
            cast(PtyProcess, proc), "hello", 0.1
        )


async def test_submit_confirmation_times_out(tmp_path):
    proc = _SilentProc()

    with pytest.raises(_PromptSubmitError, match="after Enter"):
        await ClaudeTuiProvider()._confirm_submit(  # noqa: SLF001
            cast(PtyProcess, proc),
            tmp_path / "missing.jsonl",
            _TranscriptTail(),
            0.1,
        )


async def test_busy_screen_confirms_submission(tmp_path):
    proc = _BusyProc("✻ Thinking… (esc to interrupt)".encode())

    await ClaudeTuiProvider()._confirm_submit(  # noqa: SLF001
        cast(PtyProcess, proc), tmp_path / "missing.jsonl", _TranscriptTail(), 1.0
    )


async def test_new_transcript_row_confirms_submission(tmp_path):
    path = tmp_path / "s.jsonl"
    path.write_text('{"type": "user"}\n')
    proc = _SilentProc()

    await ClaudeTuiProvider()._confirm_submit(  # noqa: SLF001
        cast(PtyProcess, proc), path, _TranscriptTail(), 1.0
    )


async def test_rows_before_the_resume_offset_do_not_confirm(tmp_path):
    row = '{"type": "user"}\n'
    path = tmp_path / "s.jsonl"
    path.write_text(row)
    proc = _SilentProc()

    with pytest.raises(_PromptSubmitError, match="after Enter"):
        await ClaudeTuiProvider()._confirm_submit(  # noqa: SLF001
            cast(PtyProcess, proc), path, _TranscriptTail(offset=len(row)), 0.2
        )


_PASTE_DROPPED_TUI = r"""#!__PYTHON__
import sys, time

try:
    import tty
    tty.setraw(0)  # no echo, so a dropped paste leaves no trace
except Exception:
    pass

sys.stdout.write("❯ ")
sys.stdout.flush()
time.sleep(30)
"""

_ENTER_DROPPED_TUI = r"""#!__PYTHON__
import os, sys

sys.stdout.write("❯ ")
sys.stdout.flush()

while True:
    chunk = os.read(0, 4096)
    if not chunk:
        sys.exit(0)
"""


@pytest.mark.skipif(os.name == "nt", reason="tty.setraw is POSIX-only")
async def test_dropped_paste_errors_without_the_turn_timeout(tmp_path):
    binary = _write_script(tmp_path, _PASTE_DROPPED_TUI)
    task = _task(tmp_path, binary, timeout=600.0)
    task["agent_extensions"] = {
        "claude_binary": str(binary),
        "ready_timeout": 5.0,
        "paste_timeout": 0.5,
    }

    started = time.monotonic()
    events = [event async for event in ClaudeTuiProvider().stream(task)]
    elapsed = time.monotonic() - started

    errors = [event for event in events if event["type"] == "error"]
    assert errors
    assert errors[0]["is_fatal"] is True
    assert "paste" in errors[0]["error"].lower()
    assert elapsed < 5.0
    assert [event["type"] for event in events].count("session_end") == 1


async def test_dropped_enter_errors_without_the_turn_timeout(tmp_path):
    binary = _write_script(tmp_path, _ENTER_DROPPED_TUI)
    task = _task(tmp_path, binary, timeout=600.0)
    task["agent_extensions"] = {
        "claude_binary": str(binary),
        "ready_timeout": 5.0,
        "submit_timeout": 0.5,
    }

    started = time.monotonic()
    events = [event async for event in ClaudeTuiProvider().stream(task)]
    elapsed = time.monotonic() - started

    errors = [event for event in events if event["type"] == "error"]
    assert errors
    assert errors[0]["is_fatal"] is True
    assert "after Enter" in errors[0]["error"]
    assert elapsed < 5.0
    assert [event["type"] for event in events].count("session_end") == 1


# ========== Transcript tail: truncation, rewrite, partial line ==========


def _row_bytes(**fields: object) -> bytes:
    return (json.dumps(fields) + "\n").encode()


def test_transcript_tail_reads_appended_rows_once(tmp_path):
    path = tmp_path / "t.jsonl"
    path.write_bytes(_row_bytes(type="a"))
    rows, tail = _read_new_rows(path, _TranscriptTail())
    assert [row["type"] for row in rows] == ["a"]
    assert _read_new_rows(path, tail)[0] == []

    path.write_bytes(_row_bytes(type="a") + _row_bytes(type="b"))
    rows, tail = _read_new_rows(path, tail)
    assert [row["type"] for row in rows] == ["b"]


def test_transcript_tail_resets_after_truncation(tmp_path):
    path = tmp_path / "t.jsonl"
    path.write_bytes(_row_bytes(type="a") + _row_bytes(type="b"))
    _, tail = _read_new_rows(path, _TranscriptTail())

    # The CLI rewrote the file shorter than the offset the reader holds.
    path.write_bytes(_row_bytes(type="c"))
    rows, tail = _read_new_rows(path, tail)
    assert [row["type"] for row in rows] == ["c"]

    path.write_bytes(_row_bytes(type="c") + _row_bytes(type="d"))
    rows, _ = _read_new_rows(path, tail)
    assert [row["type"] for row in rows] == ["d"]


def test_transcript_tail_resets_after_inode_change(tmp_path):
    path = tmp_path / "t.jsonl"
    path.write_bytes(_row_bytes(type="a") + _row_bytes(type="b"))
    _, tail = _read_new_rows(path, _TranscriptTail())

    # A replacement file that is longer than the old offset: only the
    # inode check can notice, a size check would not.
    replacement = tmp_path / "replacement.jsonl"
    replacement.write_bytes(_row_bytes(type="x", pad="z" * 200))
    os.replace(replacement, path)

    rows, _ = _read_new_rows(path, tail)
    assert [row["type"] for row in rows] == ["x"]


def test_transcript_tail_joins_a_half_written_line(tmp_path):
    path = tmp_path / "t.jsonl"
    path.write_bytes(b'{"type": "a", "value": 1}\n{"type": "b", "va')
    rows, tail = _read_new_rows(path, _TranscriptTail())
    assert [row["type"] for row in rows] == ["a"]

    # The rest of the row arrives later.  It must parse whole, not be
    # spliced into the next row's bytes.
    with path.open("ab") as handle:
        handle.write(b'lue": 2}\n')
    rows, tail = _read_new_rows(path, tail)
    assert rows == [{"type": "b", "value": 2}]

    with path.open("ab") as handle:
        handle.write(_row_bytes(type="c"))
    rows, _ = _read_new_rows(path, tail)
    assert rows == [{"type": "c"}]


def test_transcript_tail_recovers_a_row_glued_to_a_killed_write(tmp_path):
    path = tmp_path / "t.jsonl"
    path.write_bytes(_row_bytes(type="a") + b'{"type": "half", "x')
    _, tail = _read_new_rows(path, _TranscriptTail())

    # The killed process never finished its row; the next process appended
    # a complete row on the same physical line.  That row must survive.
    with path.open("ab") as handle:
        handle.write(_row_bytes(type="b", value=2))
    rows, tail = _read_new_rows(path, tail)

    assert rows == [{"type": "b", "value": 2}]
    with path.open("ab") as handle:
        handle.write(_row_bytes(type="c"))
    assert [row["type"] for row in _read_new_rows(path, tail)[0]] == ["c"]


def test_transcript_tail_skips_a_corrupt_line(tmp_path):
    path = tmp_path / "t.jsonl"
    path.write_bytes(b"not json\n" + _row_bytes(type="ok") + b"{broken\n")
    rows, tail = _read_new_rows(path, _TranscriptTail())
    assert rows == [{"type": "ok"}]

    with path.open("ab") as handle:
        handle.write(_row_bytes(type="after"))
    rows, _ = _read_new_rows(path, tail)
    assert rows == [{"type": "after"}]


def test_transcript_tail_missing_file_keeps_state(tmp_path):
    tail = _TranscriptTail(offset=7)
    rows, after = _read_new_rows(tmp_path / "missing.jsonl", tail)
    assert rows == []
    assert after == tail


# ========== Transcript path follows the task environment ==========


def _expected_path(base: Path, cwd: str, session_id: str) -> Path:
    return (
        base
        / "projects"
        / _slug_from_resolved(str(Path(cwd).resolve()))
        / (session_id + ".jsonl")
    )


def test_task_transcript_path_uses_task_home(tmp_path):
    work = str(tmp_path / "work")
    home = tmp_path / "home"
    task = cast(TaskConfig, {"env": {"HOME": str(home)}})

    assert task_transcript_path(task, work, "sid") == _expected_path(
        home / ".claude", work, "sid"
    )


def test_task_transcript_path_uses_userprofile_without_home(tmp_path):
    work = str(tmp_path / "work")
    profile = tmp_path / "profile"
    task = cast(TaskConfig, {"env": {"USERPROFILE": str(profile)}})

    assert task_transcript_path(task, work, "sid") == _expected_path(
        profile / ".claude", work, "sid"
    )


def test_task_transcript_path_prefers_home_over_userprofile(tmp_path):
    work = str(tmp_path / "work")
    task = cast(
        TaskConfig,
        {"env": {"HOME": str(tmp_path / "home"), "USERPROFILE": str(tmp_path / "p")}},
    )

    assert task_transcript_path(task, work, "sid") == _expected_path(
        tmp_path / "home" / ".claude", work, "sid"
    )


def test_task_transcript_path_resolves_relative_config_dir_against_cwd(tmp_path):
    work = tmp_path / "work"
    task = cast(
        TaskConfig,
        {"env": {"CLAUDE_CONFIG_DIR": "rel/config", "HOME": str(tmp_path / "home")}},
    )

    assert task_transcript_path(task, str(work), "sid") == _expected_path(
        work / "rel" / "config", str(work), "sid"
    )


def test_task_transcript_path_keeps_absolute_config_dir(tmp_path):
    work = tmp_path / "work"
    config = tmp_path / "config"
    task = cast(TaskConfig, {"env": {"CLAUDE_CONFIG_DIR": str(config)}})

    assert task_transcript_path(task, str(work), "sid") == _expected_path(
        config, str(work), "sid"
    )


def test_task_transcript_path_honours_an_inherited_config_dir(tmp_path):
    work = str(tmp_path / "work")
    inherited = tmp_path / "inherited"
    task = cast(TaskConfig, {"env": {}})

    # The child inherits CLAUDE_CONFIG_DIR from the environment it is
    # started with, so the reader must resolve the same path.
    path = task_transcript_path(
        task, work, "sid", {"CLAUDE_CONFIG_DIR": str(inherited)}
    )
    assert path == _expected_path(inherited, work, "sid")


def test_task_env_config_dir_wins_over_an_inherited_one(tmp_path):
    work = str(tmp_path / "work")
    task = cast(TaskConfig, {"env": {"CLAUDE_CONFIG_DIR": str(tmp_path / "task")}})

    path = task_transcript_path(
        task, work, "sid", {"CLAUDE_CONFIG_DIR": str(tmp_path / "inherited")}
    )
    assert path == _expected_path(tmp_path / "task", work, "sid")


def test_task_transcript_path_ignores_host_home_when_task_sets_one(tmp_path):
    work = str(tmp_path / "work")
    task_home_dir = tmp_path / "task-home"
    task = cast(TaskConfig, {"env": {"HOME": str(task_home_dir)}})

    path = task_transcript_path(task, work, "sid")
    assert task_home_dir in path.parents
    assert Path.home() / ".claude" not in path.parents


# ========== Usage accumulates per turn ==========


def _assistant(message_id: str, text: str, **usage: object) -> dict:
    return {
        "type": "assistant",
        "message": {
            "id": message_id,
            "role": "assistant",
            "content": [{"type": "text", "text": text}],
            "usage": usage,
        },
    }


def test_turn_usage_accumulates_across_messages():
    state = _TurnState()
    events: list[Any] = []
    rows = [
        _assistant(
            "m1",
            "first",
            input_tokens=10,
            output_tokens=2,
            output_tokens_details={"thinking_tokens": 1},
        ),
        # The same message again: one row per content block repeats usage.
        _assistant(
            "m1",
            "second block",
            input_tokens=10,
            output_tokens=2,
            output_tokens_details={"thinking_tokens": 1},
        ),
        _assistant("m2", "third", input_tokens=5, output_tokens=3),
        {"type": "system", "subtype": "turn_duration"},
    ]
    for row in rows:
        got, done = _turn_row(row, state)
        events.extend(got)
        if done:
            break

    usage = [event for event in events if event["type"] == "usage"]
    assert len(usage) == 1
    assert usage[0]["usage"] == {
        "input_tokens": 15,
        "output_tokens": 5,
        "reasoning_tokens": 1,
        "total_tokens": 20,
    }


def test_result_row_reports_cost_usd():
    state = _TurnState()
    events = _turn_row(
        _assistant("m", "answer", input_tokens=3, output_tokens=2), state
    )[0]
    events.extend(
        _turn_row(
            {
                "type": "result",
                "subtype": "success",
                "usage": {"input_tokens": 3, "output_tokens": 2},
                "total_cost_usd": 0.0123,
            },
            state,
        )[0]
    )

    usage = [event for event in events if event["type"] == "usage"]
    assert len(usage) == 1
    assert usage[0]["usage"]["total_tokens"] == 5
    assert usage[0]["cost_usd"] == pytest.approx(0.0123)


def test_result_row_usage_without_assistant_rows():
    events, done = _turn_row(
        {
            "type": "result",
            "usage": {"input_tokens": 4, "output_tokens": 1},
            "total_cost_usd": 0.5,
        },
        _TurnState(),
    )

    assert done is True
    usage = [event for event in events if event["type"] == "usage"]
    assert usage[0]["usage"]["total_tokens"] == 5
    assert usage[0]["cost_usd"] == 0.5


def test_usage_maps_reasoning_tokens_from_both_spellings():
    assert _usage_from_raw({"output_tokens_details": {"thinking_tokens": 7}}) == {
        "reasoning_tokens": 7
    }
    assert _usage_from_raw({"output_tokens_details": {"reasoning_tokens": 9}}) == {
        "reasoning_tokens": 9
    }
    assert _usage_from_raw({}) is None


def test_a_result_row_does_not_double_count_after_turn_duration():
    state = _TurnState()
    row = _assistant("m", "answer", input_tokens=3, output_tokens=2)
    events = _turn_row(row, state)[0]
    events.extend(_turn_row({"type": "system", "subtype": "turn_duration"}, state)[0])
    events.extend(
        _turn_row(
            {
                "type": "result",
                "usage": {"input_tokens": 3, "output_tokens": 2},
                "total_cost_usd": 0.01,
            },
            state,
        )[0]
    )

    usage = [event for event in events if event["type"] == "usage"]
    assert len(usage) == 1


# ========== Startup detection is anchored at the screen bottom ==========


def test_prompt_ready_accepts_an_empty_input_box():
    assert _prompt_is_ready("output\n ❯ \n ─────\n  auto mode on\n") is True
    assert _prompt_is_ready("❯ ") is True


def test_prompt_ready_rejects_menu_items():
    assert _prompt_is_ready("Select a theme\n❯   1.   Red\n  2.   Blue\n") is False
    assert _prompt_is_ready("Do you want to proceed?\n❯  1.  Yes\n  2.  No\n") is False


def test_prompt_ready_rejects_a_prompt_buried_in_scrollback():
    screen = "❯\n" + "\n".join(f"assistant line {index}" for index in range(20))
    assert _prompt_is_ready(screen) is False


def test_prompt_ready_uses_the_newest_frame():
    stale = "❯\n" + "\n".join(f"old {index}" for index in range(20))
    assert _prompt_is_ready(stale + "\n❯ \n ─────\n auto mode on\n") is True


_THEME_THEN_PROMPT_TUI = r"""#!__PYTHON__
import sys, time

sys.stdout.write("Select a theme\n❯   1.   Red\n  2.   Blue\n")
sys.stdout.flush()
time.sleep(0.3)
sys.stdout.write("\x1b[2J\x1b[H❯ ")
sys.stdout.flush()
time.sleep(30)
"""


async def test_theme_picker_is_not_mistaken_for_the_prompt(tmp_path):
    binary = _write_script(tmp_path, _THEME_THEN_PROMPT_TUI)
    task = _task(tmp_path, binary, timeout=5.0)
    task["agent_extensions"] = {  # type: ignore[typeddict-item]
        "claude_binary": str(binary),
        "ready_timeout": 0.15,
        "tui_fallback": False,
    }

    started = time.monotonic()
    events = [event async for event in ClaudeTuiProvider().stream(task)]
    elapsed = time.monotonic() - started

    # 0.15 s is too short for the real prompt, so startup must time out
    # rather than accept the picker's ❯ as ready.
    errors = [event for event in events if event["type"] == "error"]
    assert errors and errors[0]["is_fatal"] is True
    assert "did not reach its prompt" in errors[0]["error"]
    assert elapsed < 5.0


_THEME_HEADER = _SCRIPT_HEADER.replace(
    'sys.stdout.write("❯ ")',
    'sys.stdout.write("Select a theme\\n❯   1.   Red\\n  2.   Blue\\n")\n'
    'sys.stdout.write("\\x1b[2J\\x1b[H❯ ")\n',
)

_THEME_TURN_TUI = (
    _THEME_HEADER
    + r"""
append({"type": "assistant", "message": {"id": "msg_t", "role": "assistant",
        "content": [{"type": "text", "text": "THEMED: " + text}],
        "usage": {"input_tokens": 2, "output_tokens": 1}}})
append({"type": "system", "subtype": "turn_duration"})
wait()
"""
)


async def test_prompt_after_a_theme_picker_is_ready(tmp_path):
    binary = _write_script(tmp_path, _THEME_TURN_TUI)
    task = _task(tmp_path, binary, timeout=5.0)
    task["agent_extensions"] = {  # type: ignore[typeddict-item]
        "claude_binary": str(binary),
        "ready_timeout": 5.0,
        "tui_fallback": False,
    }

    events = [event async for event in ClaudeTuiProvider().stream(task)]

    assert [event["type"] for event in events].count("session_start") == 1
    assert [event["text"] for event in events if event["type"] == "message_end"] == [
        "THEMED: hello"
    ]
    assert not [
        event for event in events if event["type"] == "error" and event.get("is_fatal")
    ]


# ========== A TUI that cannot start falls back ==========


class _FakeFallbackProvider:
    """A fallback provider stand-in that answers immediately."""

    @staticmethod
    def is_available() -> bool:
        return True

    async def stream(self, task):
        yield {"type": "message_end", "text": "fallback answer"}


_LOGIN_TUI = r"""#!__PYTHON__
import sys, time
sys.stdout.write("Select login method\n")
sys.stdout.flush()
time.sleep(5)
"""


async def test_trust_failure_falls_back_to_another_transport(tmp_path, monkeypatch):
    binary = _write_script(tmp_path, _TRUST_TUI)
    monkeypatch.setattr(
        claude_tui_module, "_fallback_providers", lambda: [_FakeFallbackProvider]
    )

    provider = ClaudeTuiProvider()
    events = [event async for event in provider.stream(_task(tmp_path, binary))]

    errors = [event for event in events if event["type"] == "error"]
    assert errors
    assert errors[0]["is_fatal"] is False
    assert "trust" in errors[0]["error"].lower()
    assert "Falling back to _FakeFallbackProvider" in errors[0]["error"]
    assert [event["text"] for event in events if event["type"] == "message_end"] == [
        "fallback answer"
    ]


async def test_login_failure_falls_back_to_another_transport(tmp_path, monkeypatch):
    binary = _write_script(tmp_path, _LOGIN_TUI)
    monkeypatch.setattr(
        claude_tui_module, "_fallback_providers", lambda: [_FakeFallbackProvider]
    )

    provider = ClaudeTuiProvider()
    events = [event async for event in provider.stream(_task(tmp_path, binary))]

    errors = [event for event in events if event["type"] == "error"]
    assert errors[0]["is_fatal"] is False
    assert "signed in" in errors[0]["error"]
    assert [event["text"] for event in events if event["type"] == "message_end"] == [
        "fallback answer"
    ]


async def test_fallback_can_be_disabled(tmp_path):
    binary = _write_script(tmp_path, _TRUST_TUI)
    task = _task(tmp_path, binary)
    task["agent_extensions"]["tui_fallback"] = False  # type: ignore[index]

    events = [event async for event in ClaudeTuiProvider().stream(task)]

    errors = [event for event in events if event["type"] == "error"]
    assert errors and errors[0]["is_fatal"] is True
    assert not [event for event in events if event["type"] == "message_end"]


async def test_no_fallback_transport_is_a_fatal_error(tmp_path, monkeypatch):
    binary = _write_script(tmp_path, _TRUST_TUI)
    monkeypatch.setattr(claude_tui_module, "_fallback_providers", lambda: [])

    provider = ClaudeTuiProvider()
    events = [event async for event in provider.stream(_task(tmp_path, binary))]

    errors = [event for event in events if event["type"] == "error"]
    assert errors and errors[0]["is_fatal"] is True
    assert "No fallback transport is available" in errors[0]["error"]


async def test_unavailable_fallback_is_skipped(tmp_path, monkeypatch):
    binary = _write_script(tmp_path, _TRUST_TUI)

    class _Unavailable(_FakeFallbackProvider):
        @staticmethod
        def is_available() -> bool:
            return False

    monkeypatch.setattr(
        claude_tui_module,
        "_fallback_providers",
        lambda: [_Unavailable, _FakeFallbackProvider],
    )

    provider = ClaudeTuiProvider()
    events = [event async for event in provider.stream(_task(tmp_path, binary))]

    assert [event["text"] for event in events if event["type"] == "message_end"] == [
        "fallback answer"
    ]


# ========== One logical message, one start and end ==========


def _message_row(message_id: str, *blocks: dict, stop: str = "end_turn") -> dict:
    return {
        "type": "assistant",
        "message": {
            "id": message_id,
            "role": "assistant",
            "content": list(blocks),
            "stop_reason": stop,
        },
    }


def test_rows_of_one_message_share_one_start_and_end():
    state = _TurnState()
    events: list[Any] = []
    rows = [
        _message_row("m1", {"type": "thinking", "thinking": "plan"}),
        _message_row("m1", {"type": "text", "text": "answer"}),
        _message_row(
            "m1", {"type": "tool_use", "id": "t1", "name": "Read", "input": {}}
        ),
        {"type": "system", "subtype": "turn_duration"},
    ]
    for row in rows:
        got, _ = _turn_row(row, state)
        events.extend(got)

    kinds = [event["type"] for event in events]
    assert kinds.count("message_start") == 1
    assert kinds.count("message_end") == 1
    assert kinds.count("message_delta") == 1
    assert kinds.count("reasoning_delta") == 1
    assert kinds.count("tool_use") == 1
    ends = [event for event in events if event["type"] == "message_end"]
    assert ends[0]["text"] == "answer"
    assert ends[0]["message_id"] == "m1"
    assert ends[0]["stop_reason"] == "end_turn"


def test_a_new_message_ends_the_previous_one_first():
    state = _TurnState()
    first, _ = _turn_row(_message_row("m1", {"type": "text", "text": "one"}), state)
    second, _ = _turn_row(_message_row("m2", {"type": "text", "text": "two"}), state)

    kinds = [event["type"] for event in first + second]
    assert kinds == [
        "message_start",
        "message_delta",
        "message_end",
        "message_start",
        "message_delta",
    ]
    assert (first + second)[2]["message_id"] == "m1"


def test_a_replayed_block_is_not_emitted_twice():
    state = _TurnState()
    row = _message_row("m1", {"type": "text", "text": "once"})
    first, _ = _turn_row(row, state)
    replay, _ = _turn_row(row, state)

    assert [event["type"] for event in replay] == []
    assert [event["text"] for event in first if event["type"] == "message_delta"] == [
        "once"
    ]


def test_tool_results_on_user_rows_are_kept():
    events, done = _turn_row(
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "t1", "content": "ok"}
                ],
            },
        },
        _TurnState(),
    )

    assert done is False
    assert [event["type"] for event in events] == ["tool_result"]
    assert events[0]["tool_use_id"] == "t1"


# ========== session_id must be a UUID ==========


def test_session_uuid_accepts_and_normalizes_uuids():
    value = str(uuid.uuid4())
    assert _session_uuid(value) == value
    assert _session_uuid(value.upper()) == value
    assert _session_uuid(f"{{{value}}}") == value


def test_session_uuid_rejects_labels_and_non_strings():
    for value in ("resume-sid", "", None, 123, "not-a-uuid"):
        assert _session_uuid(value) is None


async def test_non_uuid_resume_is_rejected_before_start(tmp_path):
    binary = _write_script(tmp_path, _FAKE_TUI)
    task = _task(tmp_path, binary)
    task["session_id"] = "resume-sid"
    task["resume"] = True

    events = [event async for event in ClaudeTuiProvider().stream(task)]

    errors = [event for event in events if event["type"] == "error"]
    assert errors and errors[0]["is_fatal"] is True
    assert "UUID" in errors[0]["error"]
    assert not [event for event in events if event["type"] == "session_start"]


async def test_non_uuid_session_id_is_replaced_with_a_uuid(tmp_path):
    binary = _write_script(tmp_path, _FAKE_TUI)
    task = _task(tmp_path, binary)
    task["session_id"] = "my-label"

    events = [event async for event in ClaudeTuiProvider().stream(task)]

    starts = [event for event in events if event["type"] == "session_start"]
    assert starts
    assert uuid.UUID(starts[0]["session_id"])  # parses, so the CLI will accept it
    assert starts[0]["session_id"] != "my-label"


async def test_uuid_session_id_is_preserved(tmp_path):
    binary = _write_script(tmp_path, _FAKE_TUI)
    session_id = str(uuid.uuid4())
    task = _task(tmp_path, binary)
    task["session_id"] = session_id

    events = [event async for event in ClaudeTuiProvider().stream(task)]

    starts = [event for event in events if event["type"] == "session_start"]
    assert starts[0]["session_id"] == session_id


# ========== The transcript settles before the CLI is killed ==========

_SETTLE_TUI = _script(
    r"""
append({"type": "assistant", "message": {"id": "msg_s", "role": "assistant",
        "content": [{"type": "text", "text": "SETTLED: " + text}],
        "usage": {"input_tokens": 2, "output_tokens": 1}}})
time.sleep(0.2)
append({"type": "system", "subtype": "turn_duration"})
time.sleep(0.15)
append({"type": "last-prompt", "lastPrompt": text})
append({"type": "cost-state", "totalCostUSD": 0.5})
time.sleep(0.3)
wait()
"""
)

_HALF_ROW_TUI = _script(
    r"""
append({"type": "assistant", "message": {"id": "msg_h", "role": "assistant",
        "content": [{"type": "text", "text": "HALF: " + text}],
        "usage": {"input_tokens": 2, "output_tokens": 1}}})
append({"type": "system", "subtype": "turn_duration"})
with open(path, "ab") as fh:
    fh.write(b'{"type": "last-prompt", "lastProm')
time.sleep(0.5)
wait()
"""
)


async def test_turn_waits_for_bookkeeping_rows_before_closing(tmp_path):
    binary = _write_script(tmp_path, _SETTLE_TUI)
    started = time.monotonic()
    events = [
        event async for event in ClaudeTuiProvider().stream(_task(tmp_path, binary))
    ]
    elapsed = time.monotonic() - started

    # turn_duration lands 0.2 s after the answer, last-prompt 0.15 s after
    # that; the provider must still be reading at 0.35 s.
    assert elapsed >= 0.35
    assert [event["text"] for event in events if event["type"] == "message_end"] == [
        "SETTLED: hello"
    ]
    assert not [
        event for event in events if event["type"] == "error" and event.get("is_fatal")
    ]


async def test_a_half_written_trailing_row_is_not_an_error(tmp_path):
    binary = _write_script(tmp_path, _HALF_ROW_TUI)
    events = [
        event async for event in ClaudeTuiProvider().stream(_task(tmp_path, binary))
    ]

    assert [event["text"] for event in events if event["type"] == "message_end"] == [
        "HALF: hello"
    ]
    assert not [event for event in events if event["type"] == "error"]


async def test_settle_returns_after_the_quiet_window(tmp_path):
    proc = _SilentProc()
    started = time.monotonic()
    events = [
        event
        async for event in ClaudeTuiProvider()._settle(  # noqa: SLF001
            cast(PtyProcess, proc),
            tmp_path / "missing.jsonl",
            _TranscriptTail(),
            _TurnState(),
            0.4,
        )
    ]
    elapsed = time.monotonic() - started

    assert events == []
    assert elapsed < 2.0


async def test_settle_is_skipped_when_disabled(tmp_path):
    proc = _SilentProc()
    events = [
        event
        async for event in ClaudeTuiProvider()._settle(  # noqa: SLF001
            cast(PtyProcess, proc),
            tmp_path / "missing.jsonl",
            _TranscriptTail(),
            _TurnState(),
            0,
        )
    ]
    assert events == []
