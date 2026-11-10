"""Tests for the Claude Code TUI (PTY) provider."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import cast

from agentabi.providers import registry
from agentabi.providers.claude_native import ClaudeNativeProvider
from agentabi.providers.claude_sdk import ClaudeSDKProvider
from agentabi.providers.claude_tui import (
    ClaudeTuiProvider,
    _build_command,
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
