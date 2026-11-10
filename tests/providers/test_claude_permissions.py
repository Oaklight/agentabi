"""Tests for the Claude provider's stdio permission control protocol.

The child is a scripted Python process that speaks the control protocol;
``_build_command`` is patched so the provider spawns it instead of the real
``claude`` binary.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from agentabi.providers.claude_native import ClaudeNativeProvider
from agentabi.types.ir.events import PermissionRequestEvent
from agentabi.types.ir.permissions import PermissionDecision
from agentabi.types.ir.task import TaskConfig

_SCRIPT = Path(__file__).with_name("fake_claude_control.py")


def _patch_command(monkeypatch: pytest.MonkeyPatch) -> None:
    def build(task: dict[str, Any], *, permission: bool = False) -> list[str]:
        return [sys.executable, str(_SCRIPT)]

    monkeypatch.setattr(ClaudeNativeProvider, "_build_command", staticmethod(build))


def _task(mode: str, log_path: Path) -> TaskConfig:
    return {
        "prompt": "run",
        "env": {
            "FAKE_CLAUDE_MODE": mode,
            "FAKE_CLAUDE_LOG": str(log_path),
        },
    }


def _logs(log_path: Path) -> list[dict[str, Any]]:
    if not log_path.exists():
        return []
    return [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


async def _collect(provider: ClaudeNativeProvider, task: TaskConfig) -> list:
    return [event async for event in provider.stream(task)]


@pytest.mark.asyncio
async def test_allow_writes_an_allow_control_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_command(monkeypatch)
    log_path = tmp_path / "claude.log"
    seen: list[PermissionRequestEvent] = []

    provider = ClaudeNativeProvider()

    async def handler(request: PermissionRequestEvent) -> PermissionDecision:
        seen.append(request)
        return {"option_id": "allow_once", "outcome": "selected"}

    provider.set_permission_handler(handler)

    events = await _collect(provider, _task("allow", log_path))

    assert any(event["type"] == "permission_request" for event in events)
    assert seen[0]["tool_use_id"] == "toolu_1"
    assert [option["option_id"] for option in seen[0]["options"]] == [
        "allow_once",
        "allow_always",
        "reject_once",
    ]
    messages = _logs(log_path)
    assert len(messages) == 1
    response = messages[0]["response"]
    assert response["subtype"] == "success"
    assert response["request_id"] == "ctrl-1"
    assert response["response"]["behavior"] == "allow"
    assert response["response"]["updatedInput"] == {"command": "touch marker1"}


@pytest.mark.asyncio
async def test_reject_writes_a_deny_control_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_command(monkeypatch)
    log_path = tmp_path / "claude.log"

    provider = ClaudeNativeProvider()

    async def handler(request: PermissionRequestEvent) -> PermissionDecision:
        return {"option_id": "reject_once", "outcome": "selected"}

    provider.set_permission_handler(handler)

    await _collect(provider, _task("deny", log_path))

    messages = _logs(log_path)
    assert len(messages) == 1
    response = messages[0]["response"]["response"]
    assert response["behavior"] == "deny"
    assert response["interrupt"] is False


@pytest.mark.asyncio
async def test_cancel_while_pending_sends_interrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_command(monkeypatch)
    log_path = tmp_path / "claude.log"

    provider = ClaudeNativeProvider()

    async def handler(request: PermissionRequestEvent) -> PermissionDecision:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    provider.set_permission_handler(handler)

    task = asyncio.create_task(_collect(provider, _task("noanswer", log_path)))
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    for _ in range(40):
        messages = _logs(log_path)
        if any(
            message.get("request", {}).get("subtype") == "interrupt"
            for message in messages
        ):
            break
        await asyncio.sleep(0.05)
    assert any(
        message.get("request", {}).get("subtype") == "interrupt"
        for message in _logs(log_path)
    )


@pytest.mark.asyncio
async def test_control_cancel_request_cancels_the_handler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_command(monkeypatch)
    log_path = tmp_path / "claude.log"
    cancelled = asyncio.Event()

    provider = ClaudeNativeProvider()

    async def handler(request: PermissionRequestEvent) -> PermissionDecision:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise
        raise AssertionError("unreachable")

    provider.set_permission_handler(handler)

    events = await asyncio.wait_for(
        _collect(provider, _task("cancel", log_path)), timeout=5
    )

    assert any(event["type"] == "permission_request" for event in events)
    assert cancelled.is_set()
    # The provider must not answer a request the CLI already cancelled.
    assert _logs(log_path) == []


def test_permission_command_uses_the_control_protocol() -> None:
    cmd = ClaudeNativeProvider._build_command({"prompt": "hi"}, permission=True)

    assert "--input-format" in cmd
    assert cmd[cmd.index("--input-format") + 1] == "stream-json"
    assert "--permission-prompt-tool" in cmd
    assert cmd[cmd.index("--permission-prompt-tool") + 1] == "stdio"
    assert "--permission-prompts" in cmd
    assert "--" not in cmd
    assert "hi" not in cmd
    assert cmd[cmd.index("--permission-mode") + 1] == "manual"


def test_plain_command_is_unchanged() -> None:
    cmd = ClaudeNativeProvider._build_command({"prompt": "hi"})

    assert "--input-format" not in cmd
    assert "--permission-prompt-tool" not in cmd
    assert cmd[-2:] == ["--", "hi"]
