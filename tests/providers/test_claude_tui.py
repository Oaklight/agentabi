"""Tests for the Claude Code TUI (PTY) provider."""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path
from typing import cast

import pytest

from agentabi._vendor.ptyx import PtyProcess, PtyWriteError
from agentabi.providers import registry
from agentabi.providers.claude_native import ClaudeNativeProvider
from agentabi.providers.claude_sdk import ClaudeSDKProvider
from agentabi.providers.claude_tui import (
    ClaudeTuiProvider,
    _build_command,
    _prompt_problem,
    _PromptSubmitError,
    _slug_from_resolved,
    _turn_row,
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
    events = [event async for event in provider.stream(_task(tmp_path, binary))]

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
    events, turn_done = _turn_row(row, set())
    assert events
    assert turn_done is False

    duration = {"type": "system", "subtype": "turn_duration"}
    assert _turn_row(duration, set()) == ([], True)


def test_prefer_pty_selects_tui(monkeypatch):
    for cls in (ClaudeNativeProvider, ClaudeSDKProvider, ClaudeTuiProvider):
        monkeypatch.setattr(cls, "is_available", staticmethod(lambda: True))
    provider = registry.resolve_provider("claude_code", prefer="pty")
    assert isinstance(provider, ClaudeTuiProvider)


async def test_resume_skips_existing_transcript(tmp_path):
    binary = _write_script(tmp_path, _FAKE_TUI)
    provider = ClaudeTuiProvider()
    session_id = "resume-sid"

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
    events, turn_done = _turn_row(row, set())

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
    events, turn_done = _turn_row(row, set())

    assert turn_done is False
    assert events[0]["type"] == "error"
    assert events[0]["is_fatal"] is False
    assert "retry 1/10" in events[0]["error"]


def test_system_api_error_without_retry_budget_ends_turn():
    events, turn_done = _turn_row(
        {"type": "system", "subtype": "api_error", "error": "socket closed"}, set()
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
    events, turn_done = _turn_row(row, set())

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
            proc, path, "sid", task, 0
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
            proc, tmp_path, "sid", task, 0
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
    events, turn_done = _turn_row(row, set())

    assert turn_done is True
    assert [event["type"] for event in events].count("session_end") == 0
    assert any(event["type"] == "usage" for event in events)


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
            0,
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
            cast(PtyProcess, proc), "hello", tmp_path / "t.jsonl", 0, task
        )


async def test_submit_write_failure_is_reported(tmp_path):
    proc = _WriteFailingProc(fail_on=1, echo=b"hello")
    task = cast(TaskConfig, {"prompt": "hello"})

    with pytest.raises(_PromptSubmitError, match="cannot submit"):
        await ClaudeTuiProvider()._send_prompt(  # noqa: SLF001
            cast(PtyProcess, proc), "hello", tmp_path / "t.jsonl", 0, task
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
            cast(PtyProcess, proc), tmp_path / "missing.jsonl", 0, 0.1
        )


async def test_busy_screen_confirms_submission(tmp_path):
    proc = _BusyProc("✻ Thinking… (esc to interrupt)".encode())

    await ClaudeTuiProvider()._confirm_submit(  # noqa: SLF001
        cast(PtyProcess, proc), tmp_path / "missing.jsonl", 0, 1.0
    )


async def test_new_transcript_row_confirms_submission(tmp_path):
    path = tmp_path / "s.jsonl"
    path.write_text('{"type": "user"}\n')
    proc = _SilentProc()

    await ClaudeTuiProvider()._confirm_submit(  # noqa: SLF001
        cast(PtyProcess, proc), path, 0, 1.0
    )


async def test_rows_before_the_resume_offset_do_not_confirm(tmp_path):
    row = '{"type": "user"}\n'
    path = tmp_path / "s.jsonl"
    path.write_text(row)
    proc = _SilentProc()

    with pytest.raises(_PromptSubmitError, match="after Enter"):
        await ClaudeTuiProvider()._confirm_submit(  # noqa: SLF001
            cast(PtyProcess, proc), path, len(row), 0.2
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
