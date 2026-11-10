"""Unit tests for the ACP permission broker and IR backend wiring."""

from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest

from agentabi._vendor.acp import (
    NewSessionParams,
    PermissionOption,
    PermissionOptionKind,
    PermissionOutcome,
    PromptParams,
    StopReason,
    TextContent,
)
from agentabi.acp.backends import IRBackend
from agentabi.acp.permissions import PermissionBroker
from agentabi.types.ir.events import PermissionRequestEvent


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
                "method": "session/request_permission",
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


def _request(**overrides: Any) -> PermissionRequestEvent:
    request: dict[str, Any] = {
        "type": "permission_request",
        "request_id": "toolu_1",
        "tool_name": "Bash",
        "tool_use_id": "toolu_1",
        "tool_input": {"command": "touch marker1"},
        "description": "Create marker1",
        "options": [
            {"option_id": "allow_once", "name": "Allow once", "kind": "allow_once"},
            {
                "option_id": "allow_always",
                "name": "Allow always",
                "kind": "allow_always",
            },
            {"option_id": "reject_once", "name": "Reject", "kind": "reject_once"},
        ],
        "suggestions": [
            {
                "type": "addRules",
                "rules": [{"toolName": "Bash", "ruleContent": "touch marker1 *"}],
                "behavior": "allow",
                "destination": "localSettings",
            }
        ],
    }
    request.update(overrides)
    return cast(PermissionRequestEvent, request)


@pytest.mark.asyncio
async def test_request_reaches_the_client():
    transport = FakeTransport()
    broker = PermissionBroker(transport)

    decision = await broker.ask("s1", _request())

    assert decision == {"option_id": "allow_once", "outcome": "selected"}
    assert len(transport.requests) == 1
    sent = transport.requests[0]
    assert sent["method"] == "session/request_permission"
    assert sent["sessionId"] == "s1"
    assert sent["toolCall"]["toolCallId"] == "toolu_1"
    assert sent["toolCall"]["status"] == "pending"
    assert sent["toolCall"]["kind"] == "execute"
    assert [option.option_id for option in sent["options"]] == [
        "allow_once",
        "allow_always",
        "reject_once",
    ]
    assert [option.kind for option in sent["options"]] == [
        PermissionOptionKind.ALLOW_ONCE,
        PermissionOptionKind.ALLOW_ALWAYS,
        PermissionOptionKind.REJECT_ONCE,
    ]


@pytest.mark.asyncio
async def test_allow_returns_selected_allow_once():
    transport = FakeTransport("allow_once")
    broker = PermissionBroker(transport)

    decision = await broker.ask("s1", _request())

    assert decision == {"option_id": "allow_once", "outcome": "selected"}


@pytest.mark.asyncio
async def test_reject_returns_selected_reject_once():
    transport = FakeTransport("reject_once")
    broker = PermissionBroker(transport)

    decision = await broker.ask("s1", _request())

    assert decision == {"option_id": "reject_once", "outcome": "selected"}


@pytest.mark.asyncio
async def test_cancel_resolves_pending_as_cancelled():
    transport = FakeTransport(answer=None)
    broker = PermissionBroker(transport)

    task = asyncio.create_task(broker.ask("s1", _request()))
    await asyncio.wait_for(transport.answered.wait(), timeout=1)
    assert broker.has_pending("s1")

    broker.cancel("s1")
    decision = await asyncio.wait_for(task, timeout=1)

    assert decision == {"option_id": None, "outcome": "cancelled"}
    assert transport.cancelled is True
    assert broker.has_pending("s1") is False


@pytest.mark.asyncio
async def test_timeout_denies_without_hanging():
    transport = FakeTransport(answer=None)
    broker = PermissionBroker(transport, timeout=0.1)

    decision = await asyncio.wait_for(broker.ask("s1", _request()), timeout=2)

    assert decision == {"option_id": "reject_once", "outcome": "selected"}
    assert transport.cancelled is True


@pytest.mark.asyncio
async def test_allow_always_is_cached_for_matching_requests():
    transport = FakeTransport("allow_always")
    broker = PermissionBroker(transport)

    first = await broker.ask("s1", _request())
    second = await broker.ask(
        "s1",
        _request(
            request_id="toolu_2",
            tool_use_id="toolu_2",
            tool_input={"command": "touch marker1 again"},
        ),
    )

    assert first == {"option_id": "allow_always", "outcome": "selected"}
    assert second == {"option_id": "allow_once", "outcome": "selected"}
    assert len(transport.requests) == 1


@pytest.mark.asyncio
async def test_allow_always_does_not_match_a_different_command():
    transport = FakeTransport("allow_always")
    broker = PermissionBroker(transport)

    await broker.ask("s1", _request())
    second = await broker.ask(
        "s1",
        _request(
            request_id="toolu_2",
            tool_use_id="toolu_2",
            tool_input={"command": "rm -rf /tmp/other"},
        ),
    )

    assert second == {"option_id": "allow_always", "outcome": "selected"}
    assert len(transport.requests) == 2


@pytest.mark.asyncio
async def test_deny_policy_never_contacts_the_client():
    transport = FakeTransport()
    broker = PermissionBroker(transport, policy="deny")

    decision = await broker.ask("s1", _request())

    assert decision == {"option_id": "reject_once", "outcome": "selected"}
    assert transport.requests == []


@pytest.mark.asyncio
async def test_allow_all_policy_never_contacts_the_client():
    transport = FakeTransport()
    broker = PermissionBroker(transport, policy="allow_all")

    decision = await broker.ask("s1", _request())

    assert decision == {"option_id": "allow_once", "outcome": "selected"}
    assert transport.requests == []


@pytest.mark.asyncio
async def test_set_policy_applies_per_session():
    transport = FakeTransport()
    broker = PermissionBroker(transport)
    broker.set_policy("s1", "deny")

    decision = await broker.ask("s1", _request())

    assert decision == {"option_id": "reject_once", "outcome": "selected"}
    assert broker.policy_for("s1") == "deny"
    assert broker.policy_for("s2") == "ask"


# ============================================================================
# IR backend wiring
# ============================================================================


class _Provider:
    """Provider that records the handler the backend attaches."""

    def __init__(self, *, supports: bool) -> None:
        self.supports = supports
        self.handler: Any = "unset"

    def set_permission_handler(self, handler: Any) -> None:
        self.handler = handler

    def capabilities(self) -> dict[str, Any]:
        return {"supports_permissions": self.supports}


class _Session:
    def __init__(self, *, supports: bool, emit_request: bool) -> None:
        self.provider = _Provider(supports=supports)
        self.emit_request = emit_request
        self.decision: dict[str, Any] | None = None
        self.transport: FakeTransport | None = None

    async def stream(self, prompt: str, **kwargs: Any):
        if self.emit_request and callable(self.provider.handler):
            request = _request()
            self.decision = await self.provider.handler(request)
            yield {
                "type": "permission_response",
                "request_id": request["request_id"],
                "tool_use_id": request["tool_use_id"],
                "option_id": self.decision["option_id"],
                "outcome": self.decision["outcome"],
                "granted": True,
            }
        elif self.emit_request:
            yield _request()
        yield {"type": "message_end", "stop_reason": "end_turn"}


async def _run_prompt(session: _Session) -> list[dict[str, Any]]:
    backend = IRBackend("agy", session_factory=lambda: session)
    transport = FakeTransport("allow_once")
    backend.set_permission_broker(PermissionBroker(transport))
    created = await backend.new_session(NewSessionParams(cwd="/tmp"))
    updates = [
        update
        async for update in backend.prompt(
            PromptParams(
                session_id=created.session_id,
                prompt=[TextContent(text="run")],
            )
        )
    ]
    session.transport = transport
    return updates


@pytest.mark.asyncio
async def test_ir_backend_passes_a_handler_to_the_provider():
    session = _Session(supports=True, emit_request=True)

    updates = await _run_prompt(session)

    assert session.provider.handler != "unset"
    assert session.decision == {"option_id": "allow_once", "outcome": "selected"}
    assert session.transport is not None
    assert session.transport.requests
    # Permission events never become session/update payloads by themselves.
    assert all("permission" not in update["sessionUpdate"] for update in updates)


@pytest.mark.asyncio
async def test_ir_backend_never_asks_an_unsupported_provider():
    session = _Session(supports=False, emit_request=True)

    updates = await _run_prompt(session)

    assert session.provider.handler is None
    assert session.transport is not None
    assert session.transport.requests == []
    assert all("permission" not in update["sessionUpdate"] for update in updates)


@pytest.mark.asyncio
async def test_ir_backend_cancel_resolves_pending_and_returns_cancelled():
    session = _Session(supports=True, emit_request=True)
    backend = IRBackend("agy", session_factory=lambda: session)
    transport = FakeTransport(answer=None)
    backend.set_permission_broker(PermissionBroker(transport))
    created = await backend.new_session(NewSessionParams(cwd="/tmp"))

    async def consume():
        return [
            update
            async for update in backend.prompt(
                PromptParams(
                    session_id=created.session_id,
                    prompt=[TextContent(text="run")],
                )
            )
        ]

    task = asyncio.create_task(consume())
    await asyncio.wait_for(transport.answered.wait(), timeout=1)
    await backend.cancel(created.session_id)
    await asyncio.wait_for(task, timeout=2)

    result = backend.prompt_result(created.session_id)
    assert result is not None
    assert result.stop_reason == StopReason.CANCELLED
    assert session.decision == {"option_id": None, "outcome": "cancelled"}
