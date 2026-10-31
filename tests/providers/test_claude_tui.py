"""Tests for the Claude Code TUI (PTY) provider."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import cast

from agentabi.providers import registry
from agentabi.providers.claude_native import ClaudeNativeProvider
from agentabi.providers.claude_sdk import ClaudeSDKProvider
from agentabi.providers.claude_tui import (
    ClaudeTuiProvider,
    _build_command,
    transcript_path,
)
from agentabi.types.ir.task import TaskConfig

_FAKE_TUI = r"""#!__PYTHON__
import json, os, sys, time


def arg(name):
    try:
        return sys.argv[sys.argv.index(name) + 1]
    except ValueError:
        return None


sid = arg("--session-id") or arg("--resume") or "fake-session"
cwd = os.getcwd()
base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
slug = cwd.replace("/", "-")
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


def _write_script(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "fake_claude.py"
    path.write_text(body.replace("__PYTHON__", sys.executable))
    path.chmod(0o755)
    return path


def _task(tmp_path: Path, binary: Path, prompt: str = "hello") -> TaskConfig:
    return cast(
        TaskConfig,
        {
            "prompt": prompt,
            "working_dir": str(tmp_path),
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
