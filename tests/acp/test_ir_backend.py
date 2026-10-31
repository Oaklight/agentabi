"""Tests for the IR backend that bridges agentabi providers to ACP."""

from __future__ import annotations

import asyncio

import pytest

from agentabi._vendor.acp import (
    InitializeParams,
    JSONRPCException,
    NewSessionParams,
    PromptParams,
    StopReason,
    TextContent,
)
from agentabi._vendor.jsonrpc import INTERNAL_ERROR
from agentabi.acp.backends import IRBackend, create_backend
from agentabi.acp.gateway import GatewayAgent


class FakeSession:
    """Stand-in for ``agentabi.Session`` with a scripted event stream."""

    def __init__(self, events: list[dict]) -> None:
        self.events = events
        self.calls: list[tuple[str, dict]] = []

    async def stream(self, prompt: str, **kwargs):
        self.calls.append((prompt, kwargs))
        for event in self.events:
            yield event


class SlowSession(FakeSession):
    async def stream(self, prompt: str, **kwargs):
        self.calls.append((prompt, kwargs))
        yield {"type": "session_start", "session_id": "conv-1", "agent": "agy"}
        await asyncio.sleep(30)


def _result(backend: IRBackend, session_id: str):
    result = backend.prompt_result(session_id)
    assert result is not None
    return result


def _backend(events: list[dict]) -> tuple[IRBackend, FakeSession]:
    session = FakeSession(events)
    backend = IRBackend("agy", session_factory=lambda: session)
    return backend, session


@pytest.mark.asyncio
async def test_initialize_does_not_advertise_replay():
    backend, _ = _backend([])
    result = await backend.initialize(InitializeParams())
    assert result.agent_capabilities is not None
    assert result.agent_capabilities.load_session is False


@pytest.mark.asyncio
async def test_new_session_returns_generated_id():
    backend, _ = _backend([])
    result = await backend.new_session(NewSessionParams(cwd="/tmp"))
    assert result.session_id.startswith("agentabi-")


@pytest.mark.asyncio
async def test_prompt_maps_ir_events_to_acp_updates():
    events = [
        {"type": "session_start", "session_id": "conv-1", "agent": "agy"},
        {"type": "message_start", "role": "assistant"},
        {"type": "message_delta", "text": "4"},
        {"type": "reasoning_delta", "reasoning": "thinking"},
        {
            "type": "tool_use",
            "tool_use_id": "t1",
            "tool_name": "run_command",
            "tool_input": {"command": "ls"},
        },
        {
            "type": "tool_result",
            "tool_use_id": "t1",
            "content": "file.txt",
            "is_error": False,
        },
        {"type": "message_end", "stop_reason": "end_turn", "text": "4"},
    ]
    backend, session = _backend(events)
    created = await backend.new_session(NewSessionParams(cwd="/work"))

    updates = [
        u
        async for u in backend.prompt(
            PromptParams(
                session_id=created.session_id,
                prompt=[TextContent(text="2+2?")],
            )
        )
    ]

    kinds = [u["sessionUpdate"] for u in updates]
    assert kinds == [
        "agent_message_chunk",
        "agent_thought_chunk",
        "tool_call",
        "tool_call_update",
    ]
    assert updates[0]["content"]["text"] == "4"
    assert updates[2]["kind"] == "execute"
    assert updates[3]["status"] == "completed"
    assert _result(backend, created.session_id).stop_reason == StopReason.END_TURN
    assert session.calls[0][1]["working_dir"] == "/work"


@pytest.mark.asyncio
async def test_second_prompt_resumes_the_agent_session():
    events = [
        {"type": "session_start", "session_id": "conv-9", "agent": "agy"},
        {"type": "message_end", "stop_reason": "stop"},
    ]
    backend, session = _backend(events)
    created = await backend.new_session(NewSessionParams(cwd="/work"))

    async for _ in backend.prompt(
        PromptParams(session_id=created.session_id, prompt=[TextContent(text="one")])
    ):
        pass
    async for _ in backend.prompt(
        PromptParams(session_id=created.session_id, prompt=[TextContent(text="two")])
    ):
        pass

    assert session.calls[0][1].get("resume") is None
    assert session.calls[1][1]["session_id"] == "conv-9"
    assert session.calls[1][1]["resume"] is True


@pytest.mark.asyncio
async def test_missing_stop_reason_means_end_turn():
    events = [{"type": "message_end"}]
    backend, _ = _backend(events)
    created = await backend.new_session(NewSessionParams(cwd="/tmp"))

    async for _ in backend.prompt(
        PromptParams(session_id=created.session_id, prompt=[TextContent(text="x")])
    ):
        pass

    assert _result(backend, created.session_id).stop_reason == StopReason.END_TURN


@pytest.mark.asyncio
async def test_unknown_stop_reason_is_preserved():
    events = [{"type": "message_end", "stop_reason": "budget_exhausted"}]
    backend, _ = _backend(events)
    created = await backend.new_session(NewSessionParams(cwd="/tmp"))

    async for _ in backend.prompt(
        PromptParams(session_id=created.session_id, prompt=[TextContent(text="x")])
    ):
        pass

    assert _result(backend, created.session_id).stop_reason == StopReason.UNKNOWN
    assert _result(backend, created.session_id).raw_stop_reason == "budget_exhausted"


@pytest.mark.asyncio
async def test_fatal_error_is_exposed_to_the_gateway():
    events = [{"type": "error", "error": "auth failed", "is_fatal": True}]
    backend, _ = _backend(events)
    created = await backend.new_session(NewSessionParams(cwd="/tmp"))
    agent = GatewayAgent(backend)
    agent._transport = None  # type: ignore[assignment]

    with pytest.raises(JSONRPCException) as exc:
        await agent.on_prompt(
            PromptParams(
                session_id=created.session_id,
                prompt=[TextContent(text="x")],
            )
        )
    assert isinstance(exc.value, JSONRPCException)
    assert exc.value.error.code == INTERNAL_ERROR
    assert exc.value.error.message == "auth failed"


@pytest.mark.asyncio
async def test_cancel_stops_the_running_prompt():
    session = SlowSession([])
    backend = IRBackend("agy", session_factory=lambda: session)
    created = await backend.new_session(NewSessionParams(cwd="/tmp"))

    async def consume():
        async for _ in backend.prompt(
            PromptParams(
                session_id=created.session_id,
                prompt=[TextContent(text="slow")],
            )
        ):
            pass

    task = asyncio.create_task(consume())
    await asyncio.sleep(0)
    await backend.cancel(created.session_id)
    await asyncio.wait_for(task, timeout=2)
    assert _result(backend, created.session_id).stop_reason == StopReason.CANCELLED


@pytest.mark.asyncio
async def test_cancel_does_not_leak_to_another_session():
    sessions: list[FakeSession] = []

    def factory():
        session = (
            SlowSession([]) if not sessions else FakeSession([{"type": "message_end"}])
        )
        sessions.append(session)
        return session

    backend = IRBackend("agy", session_factory=factory)
    first = await backend.new_session(NewSessionParams(cwd="/tmp"))
    second = await backend.new_session(NewSessionParams(cwd="/tmp"))

    async def consume_first():
        async for _ in backend.prompt(
            PromptParams(session_id=first.session_id, prompt=[TextContent(text="slow")])
        ):
            pass

    task = asyncio.create_task(consume_first())
    for _ in range(50):
        if sessions:
            break
        await asyncio.sleep(0)
    await backend.cancel(first.session_id)
    await asyncio.wait_for(task, timeout=2)
    assert _result(backend, first.session_id).stop_reason == StopReason.CANCELLED

    async for _ in backend.prompt(
        PromptParams(session_id=second.session_id, prompt=[TextContent(text="ok")])
    ):
        pass
    assert _result(backend, second.session_id).stop_reason == StopReason.END_TURN


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("end_turn", StopReason.END_TURN),
        ("stop", StopReason.END_TURN),
        ("max_tokens", StopReason.MAX_TOKENS),
        ("length", StopReason.MAX_TOKENS),
        ("max_turn_requests", StopReason.MAX_TURN_REQUESTS),
        ("refusal", StopReason.REFUSAL),
        ("cancelled", StopReason.CANCELLED),
        ("canceled", StopReason.CANCELLED),
        ("tool_use", StopReason.END_TURN),
        ("toolUse", StopReason.END_TURN),
        ("stop_sequence", StopReason.END_TURN),
        ("aborted", StopReason.CANCELLED),
        ("budget_exhausted", StopReason.UNKNOWN),
    ],
)
@pytest.mark.asyncio
async def test_stop_reason_mapping(reason, expected):
    backend, _ = _backend([{"type": "message_end", "stop_reason": reason}])
    created = await backend.new_session(NewSessionParams(cwd="/tmp"))

    async for _ in backend.prompt(
        PromptParams(session_id=created.session_id, prompt=[TextContent(text="x")])
    ):
        pass

    assert _result(backend, created.session_id).stop_reason == expected


@pytest.mark.asyncio
async def test_close_session_does_not_mark_other_prompts_cancelled():
    backend, _ = _backend([{"type": "message_end"}])
    first = await backend.new_session(NewSessionParams(cwd="/tmp"))
    second = await backend.new_session(NewSessionParams(cwd="/tmp"))

    await backend.close_session(first.session_id)

    async for _ in backend.prompt(
        PromptParams(session_id=second.session_id, prompt=[TextContent(text="ok")])
    ):
        pass

    assert _result(backend, second.session_id).stop_reason == StopReason.END_TURN


@pytest.mark.asyncio
async def test_concurrent_sessions_get_their_own_streams():
    made: list[FakeSession] = []

    def factory():
        text = "A" if not made else "B"
        session = FakeSession(
            [
                {"type": "message_delta", "text": text},
                {"type": "message_end"},
            ]
        )
        made.append(session)
        return session

    backend = IRBackend("agy", session_factory=factory)
    first = await backend.new_session(NewSessionParams(cwd="/tmp"))
    second = await backend.new_session(NewSessionParams(cwd="/tmp"))

    async def run(session_id):
        return [
            u
            async for u in backend.prompt(
                PromptParams(session_id=session_id, prompt=[TextContent(text="go")])
            )
        ]

    first_updates, second_updates = await asyncio.gather(
        run(first.session_id), run(second.session_id)
    )
    assert first_updates[0]["content"]["text"] == "A"
    assert second_updates[0]["content"]["text"] == "B"


@pytest.mark.parametrize(
    ("name", "agent"),
    [("agy", "agy"), ("claude", "claude_code"), ("codex", "codex"), ("pi", "pi")],
)
def test_registry_exposes_ir_backends(name, agent):
    backend = create_backend(name)
    assert isinstance(backend, IRBackend)
    assert backend.name == agent


@pytest.mark.asyncio
async def test_prompt_maps_ir_events_to_v2_updates():
    events = [
        {"type": "session_start", "session_id": "conv-1", "agent": "agy"},
        {"type": "message_start", "role": "assistant"},
        {"type": "message_delta", "text": "4"},
        {"type": "reasoning_delta", "reasoning": "thinking"},
        {
            "type": "tool_use",
            "tool_use_id": "t1",
            "tool_name": "run_command",
            "tool_input": {"command": "ls"},
        },
        {
            "type": "tool_result",
            "tool_use_id": "t1",
            "content": "file.txt",
            "is_error": False,
        },
        {"type": "message_end", "stop_reason": "end_turn", "text": "4"},
    ]
    backend, _ = _backend(events)
    backend.set_protocol_version(2)
    created = await backend.new_session(NewSessionParams(cwd="/work"))

    updates = [
        u
        async for u in backend.prompt(
            PromptParams(
                session_id=created.session_id,
                prompt=[TextContent(text="2+2?")],
            )
        )
    ]

    kinds = [u["sessionUpdate"] for u in updates]
    assert kinds == [
        "agent_message_chunk",
        "agent_thought_chunk",
        "tool_call_update",
        "tool_call_update",
    ]
    assert updates[0]["messageId"]
    assert updates[0]["messageId"] == updates[1]["messageId"]
