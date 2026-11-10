"""Unit tests for the Codex app-server backend.

A scripted child (``fake_codex_app_server.py``) stands in for the real
``codex app-server`` process, so these tests need no Codex installation.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from agentabi._vendor.acp import (
    NewSessionParams,
    PermissionOption,
    PermissionOutcome,
    PromptParams,
    StopReason,
    TextContent,
)
from agentabi.acp.backends import CodexAppServerBackend, create_backend
from agentabi.acp.permissions import PermissionBroker

_FAKE = Path(__file__).with_name("fake_codex_app_server.py")
_EXEC_POLICY = "x-codex-accept-with-execpolicy-amendment"


class FakeTransport:
    """Stand-in for the ACP client side of ``request_permission``."""

    def __init__(self, answer: str | None = "allow_once") -> None:
        self.answer = answer
        self.requests: list[dict[str, Any]] = []
        self.answered = asyncio.Event()
        self.cancelled = False

    async def request_permission(
        self,
        session_id: str,
        tool_call: dict[str, Any],
        options: list[PermissionOption],
    ) -> PermissionOutcome:
        self.requests.append(
            {
                "sessionId": session_id,
                "toolCall": tool_call,
                "options": options,
            }
        )
        self.answered.set()
        if self.answer is None:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise
        return PermissionOutcome(outcome="selected", option_id=self.answer)


def _backend() -> CodexAppServerBackend:
    return CodexAppServerBackend([sys.executable, str(_FAKE)], request_timeout=5)


async def _collect(
    backend: CodexAppServerBackend, session_id: str, text: str = "go"
) -> list[dict[str, Any]]:
    return [
        update
        async for update in backend.prompt(
            PromptParams(session_id=session_id, prompt=[TextContent(text=text)])
        )
    ]


def _answers(log_path: Path) -> list[dict[str, Any]]:
    if not log_path.exists():
        return []
    return [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line
    ]


@pytest.mark.asyncio
async def test_command_approval_allow(tmp_path: Path, monkeypatch) -> None:
    log = tmp_path / "answers.jsonl"
    monkeypatch.setenv("FAKE_APPSERVER_LOG", str(log))
    transport = FakeTransport("allow_once")
    backend = _backend()
    backend.set_permission_broker(PermissionBroker(transport))
    try:
        await backend.start()
        session = await backend.new_session(NewSessionParams(cwd=str(tmp_path)))
        updates = await _collect(backend, session.session_id)

        assert transport.requests, "the approval did not reach the client"
        sent = transport.requests[0]
        assert sent["sessionId"] == session.session_id
        assert sent["toolCall"]["toolCallId"] == "item-1"
        assert sent["toolCall"]["kind"] == "execute"
        assert [option.option_id for option in sent["options"]] == [
            "allow_once",
            "allow_always",
            "reject_once",
            "reject_always",
        ]
        assert _answers(log)[-1]["result"] == {"decision": "accept"}
        assert backend.prompt_result(session.session_id) == pytest.approx(
            backend.prompt_result(session.session_id)
        )
        result = backend.prompt_result(session.session_id)
        assert result is not None and result.stop_reason == StopReason.END_TURN
        assert any(
            update["sessionUpdate"] == "agent_message_chunk"
            and update["content"]["text"] == "working"
            for update in updates
        )
    finally:
        await backend.stop()


@pytest.mark.asyncio
async def test_available_decisions_are_authoritative(
    tmp_path: Path, monkeypatch
) -> None:
    log = tmp_path / "answers.jsonl"
    monkeypatch.setenv("FAKE_APPSERVER_LOG", str(log))
    monkeypatch.setenv(
        "FAKE_APPSERVER_DECISIONS",
        json.dumps(
            [
                "accept",
                {
                    "acceptWithExecpolicyAmendment": {
                        "execpolicy_amendment": ["touch", "marker"]
                    }
                },
                "cancel",
            ]
        ),
    )
    transport = FakeTransport(_EXEC_POLICY)
    backend = _backend()
    backend.set_permission_broker(PermissionBroker(transport))
    try:
        await backend.start()
        session = await backend.new_session(NewSessionParams(cwd=str(tmp_path)))
        await _collect(backend, session.session_id)

        sent = transport.requests[0]
        assert [option.option_id for option in sent["options"]] == [
            "allow_once",
            _EXEC_POLICY,
            "x-codex-cancel",
        ]
        assert _answers(log)[-1]["result"] == {
            "decision": {
                "acceptWithExecpolicyAmendment": {
                    "execpolicy_amendment": ["touch", "marker"]
                }
            }
        }
    finally:
        await backend.stop()


@pytest.mark.asyncio
async def test_decline_keeps_the_turn_running(tmp_path: Path, monkeypatch) -> None:
    log = tmp_path / "answers.jsonl"
    monkeypatch.setenv("FAKE_APPSERVER_LOG", str(log))
    monkeypatch.setenv("FAKE_APPSERVER_DECISIONS", '["accept", "decline"]')
    transport = FakeTransport("reject_once")
    backend = _backend()
    backend.set_permission_broker(PermissionBroker(transport))
    try:
        await backend.start()
        session = await backend.new_session(NewSessionParams(cwd=str(tmp_path)))
        await _collect(backend, session.session_id)

        assert _answers(log)[-1]["result"] == {"decision": "decline"}
        result = backend.prompt_result(session.session_id)
        assert result is not None and result.stop_reason == StopReason.END_TURN
    finally:
        await backend.stop()


@pytest.mark.asyncio
async def test_cancel_while_an_approval_is_pending(tmp_path: Path, monkeypatch) -> None:
    log = tmp_path / "answers.jsonl"
    monkeypatch.setenv("FAKE_APPSERVER_LOG", str(log))
    monkeypatch.setenv("FAKE_APPSERVER_DECISIONS", '["accept", "decline", "cancel"]')
    transport = FakeTransport(answer=None)
    backend = _backend()
    backend.set_permission_broker(PermissionBroker(transport))
    try:
        await backend.start()
        session = await backend.new_session(NewSessionParams(cwd=str(tmp_path)))
        task = asyncio.create_task(_collect(backend, session.session_id))
        await asyncio.wait_for(transport.answered.wait(), timeout=2)

        await backend.cancel(session.session_id)
        await asyncio.wait_for(task, timeout=2)

        assert _answers(log)[-1]["result"] == {"decision": "cancel"}
        assert transport.cancelled is True
        result = backend.prompt_result(session.session_id)
        assert result is not None and result.stop_reason == StopReason.CANCELLED
    finally:
        await backend.stop()


@pytest.mark.asyncio
async def test_deny_policy_never_offers_or_sends_an_allow(
    tmp_path: Path, monkeypatch
) -> None:
    log = tmp_path / "answers.jsonl"
    monkeypatch.setenv("FAKE_APPSERVER_LOG", str(log))
    monkeypatch.setenv("FAKE_APPSERVER_DECISIONS", '["accept", "cancel"]')
    transport = FakeTransport("allow_once")
    backend = _backend()
    backend.set_permission_broker(PermissionBroker(transport, policy="deny"))
    try:
        await backend.start()
        session = await backend.new_session(NewSessionParams(cwd=str(tmp_path)))
        await _collect(backend, session.session_id)

        assert transport.requests == []
        # ``decline`` is not listed, so the only deny is ``cancel``.
        assert _answers(log)[-1]["result"] == {"decision": "cancel"}
    finally:
        await backend.stop()


@pytest.mark.asyncio
async def test_no_broker_denies(tmp_path: Path, monkeypatch) -> None:
    log = tmp_path / "answers.jsonl"
    monkeypatch.setenv("FAKE_APPSERVER_LOG", str(log))
    monkeypatch.setenv("FAKE_APPSERVER_DECISIONS", '["accept", "decline"]')
    backend = _backend()
    try:
        await backend.start()
        session = await backend.new_session(NewSessionParams(cwd=str(tmp_path)))
        await _collect(backend, session.session_id)

        assert _answers(log)[-1]["result"] == {"decision": "decline"}
    finally:
        await backend.stop()


@pytest.mark.asyncio
async def test_file_change_approval(tmp_path: Path, monkeypatch) -> None:
    log = tmp_path / "answers.jsonl"
    monkeypatch.setenv("FAKE_APPSERVER_LOG", str(log))
    monkeypatch.setenv("FAKE_APPSERVER_METHOD", "file")
    transport = FakeTransport("allow_once")
    backend = _backend()
    backend.set_permission_broker(PermissionBroker(transport))
    try:
        await backend.start()
        session = await backend.new_session(NewSessionParams(cwd=str(tmp_path)))
        await _collect(backend, session.session_id)

        sent = transport.requests[0]
        assert sent["toolCall"]["kind"] == "edit"
        assert [option.option_id for option in sent["options"]] == [
            "allow_once",
            "allow_always",
            "reject_once",
            "reject_always",
        ]
        assert _answers(log)[-1]["result"] == {"decision": "accept"}
    finally:
        await backend.stop()


def test_registry_and_capability() -> None:
    backend = create_backend("codex-app-server")
    assert isinstance(backend, CodexAppServerBackend)
    assert backend.permission_capable is True
    assert backend.name == "codex-app-server"
