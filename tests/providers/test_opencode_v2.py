"""Tests for the OpenCode V2 HTTP/SSE provider."""

from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest

from agentabi.providers import opencode_v2
from agentabi.providers._http_sse import SSEMessage, SSEParser
from agentabi.providers.opencode_v2 import (
    OpenCodeV2Provider,
    _error_text,
    _parse_version,
    _prompt_files,
    _split_model,
    _transport_mode,
    _TurnState,
    _usage_from_tokens,
)
from agentabi.types.ir.task import TaskConfig
from tests.providers.fake_opencode_v2 import MockOpenCodeV2

SESSION = "ses_mock"


def _task(**overrides: Any) -> TaskConfig:
    task: dict[str, Any] = {"prompt": "hi", "agent": "opencode"}
    task.update(overrides)
    return cast(TaskConfig, task)


def _event(etype: str, **data: Any) -> dict[str, Any]:
    payload = {"sessionID": SESSION, "timestamp": 1000, **data}
    return {"id": "evt_1", "type": etype, "data": payload}


def _armed_state(**kwargs: Any) -> _TurnState:
    state = _TurnState(session_id=SESSION, **kwargs)
    state.prompt_message_id = "msg_user"
    state.armed = True
    return state


# ============================================================================
# SSE parsing
# ============================================================================


class TestSSEParser:
    def test_single_event(self):
        parser = SSEParser()
        messages = parser.feed('data: {"a": 1}\n\n')
        assert messages == [SSEMessage(event="message", data='{"a": 1}', id="")]

    def test_named_event_and_id(self):
        parser = SSEParser()
        messages = parser.feed("event: ping\nid: 7\ndata: hello\n\n")
        assert messages[0].event == "ping"
        assert messages[0].id == "7"
        assert messages[0].data == "hello"

    def test_multi_line_data_joined(self):
        parser = SSEParser()
        messages = parser.feed("data: one\ndata: two\n\n")
        assert messages[0].data == "one\ntwo"

    def test_comments_and_heartbeats_ignored(self):
        parser = SSEParser()
        assert parser.feed(": heartbeat\n\n") == []

    def test_crlf_and_split_chunks(self):
        parser = SSEParser()
        assert parser.feed("data: hel") == []
        assert parser.feed("lo\r\n\r\n")[0].data == "hello"

    def test_multiple_events_in_one_chunk(self):
        parser = SSEParser()
        messages = parser.feed("data: a\n\ndata: b\n\n")
        assert [m.data for m in messages] == ["a", "b"]


class TestHelpers:
    def test_parse_version(self):
        assert _parse_version("1.19.0") == (1, 19, 0)
        assert _parse_version("opencode v1.18.29\n") == (1, 18, 29)
        assert _parse_version("no version") is None

    def test_split_model(self):
        assert _split_model("rosetta/deepseek") == {
            "providerID": "rosetta",
            "id": "deepseek",
        }
        assert _split_model("openai/gpt/x") == {
            "providerID": "openai",
            "id": "gpt/x",
        }
        assert _split_model("bare-model") is None
        assert _split_model("") is None
        assert _split_model(None) is None

    def test_error_text_shapes(self):
        assert _error_text("boom") == "boom"
        assert _error_text({"type": "unknown", "message": "bad"}) == "bad"
        assert (
            _error_text({"name": "APIError", "data": {"message": "nope"}})
            == "APIError: nope"
        )
        assert _error_text({}) == ""

    def test_usage_from_tokens(self):
        usage = _usage_from_tokens(
            {
                "tokens": {
                    "input": 10,
                    "output": 5,
                    "reasoning": 3,
                    "cache": {"read": 7, "write": 2},
                }
            }
        )
        assert usage["input_tokens"] == 10
        assert usage["output_tokens"] == 5
        assert usage["reasoning_tokens"] == 3
        assert usage["cache_read_tokens"] == 7
        assert usage["cache_creation_tokens"] == 2
        assert usage["total_tokens"] == 15

    def test_usage_omits_zero_values(self):
        assert _usage_from_tokens({"tokens": {"input": 0, "output": 0}}) == {}

    def test_prompt_files(self, tmp_path):
        target = tmp_path / "note.txt"
        target.write_text("x")
        files = _prompt_files([str(target)])
        assert files[0]["uri"].startswith("file://")
        assert files[0]["name"] == "note.txt"
        assert files[0]["mime"] == "text/plain"
        assert _prompt_files(None) == []

    def test_transport_mode(self, monkeypatch):
        monkeypatch.delenv("AGENTABI_OPENCODE_TRANSPORT", raising=False)
        assert _transport_mode(_task()) == "auto"
        assert (
            _transport_mode(_task(agent_extensions={"opencode": {"transport": "v1"}}))
            == "v1"
        )
        assert (
            _transport_mode(_task(agent_extensions={"opencode_transport": "v2"}))
            == "v2"
        )
        monkeypatch.setenv("AGENTABI_OPENCODE_TRANSPORT", "v1")
        assert _transport_mode(_task()) == "v1"


# ============================================================================
# Turn state machine
# ============================================================================


class TestTurnState:
    def test_ignores_other_sessions(self):
        state = _armed_state()
        event = _event("session.next.step.started", sessionID="ses_other")
        assert state.handle(event) == []

    def test_not_armed_does_nothing(self):
        state = _TurnState(session_id=SESSION, prompt_message_id="msg_user")
        assert state.handle(_event("session.next.text.delta", delta="x")) == []

    def test_admitted_arms_and_is_not_emitted(self):
        state = _TurnState(session_id=SESSION, prompt_message_id="msg_user")
        event = _event("session.next.prompt.admitted", messageID="msg_user")
        assert state.handle(event) == []
        assert state.armed is True

    def test_step_started_arms_without_admitted(self):
        state = _TurnState(session_id=SESSION, prompt_message_id="msg_user")
        events = state.handle(
            _event("session.next.step.started", assistantMessageID="msg_a")
        )
        assert state.armed is True
        assert events[0]["type"] == "message_start"

    def test_step_started_emits_message_start(self):
        state = _armed_state()
        events = state.handle(
            _event(
                "session.next.step.started",
                assistantMessageID="msg_a",
                model={"providerID": "rosetta", "id": "deepseek"},
            )
        )
        assert [e["type"] for e in events] == ["message_start"]
        assert events[0]["message_id"] == "msg_a"
        assert state.model == "rosetta/deepseek"

    def test_text_delta_and_step_end(self):
        state = _armed_state(model="rosetta/deepseek")
        state.handle(_event("session.next.step.started", assistantMessageID="msg_a"))
        deltas = state.handle(
            _event(
                "session.next.text.delta",
                assistantMessageID="msg_a",
                textID="text-0",
                delta="OK",
            )
        )
        assert deltas[0]["type"] == "message_delta"
        assert deltas[0]["text"] == "OK"

        end = state.handle(
            _event(
                "session.next.step.ended",
                assistantMessageID="msg_a",
                finish="stop",
                cost=0.25,
                tokens={"input": 10, "output": 2, "cache": {"read": 0, "write": 0}},
            )
        )
        types = [e["type"] for e in end]
        assert types == ["usage", "message_end", "session_end"]
        assert end[0]["cost_usd"] == 0.25
        assert end[0]["usage"]["total_tokens"] == 12
        assert end[1]["text"] == "OK"
        assert end[1]["stop_reason"] == "end_turn"
        assert state.finished is True

    def test_tool_calls_finish_does_not_end_session(self):
        state = _armed_state()
        events = state.handle(
            _event("session.next.step.ended", finish="tool-calls", tokens={})
        )
        assert [e["type"] for e in events] == ["message_end"]
        assert state.finished is False

    def test_text_ended_backfills_missing_deltas(self):
        state = _armed_state()
        state.handle(_event("session.next.step.started", assistantMessageID="msg_a"))
        events = state.handle(
            _event(
                "session.next.text.ended",
                assistantMessageID="msg_a",
                textID="text-0",
                text="full text",
            )
        )
        assert events[0]["text"] == "full text"

    def test_text_ended_skipped_when_deltas_arrived(self):
        state = _armed_state()
        state.handle(_event("session.next.step.started", assistantMessageID="msg_a"))
        state.handle(
            _event(
                "session.next.text.delta",
                assistantMessageID="msg_a",
                textID="text-0",
                delta="full text",
            )
        )
        events = state.handle(
            _event(
                "session.next.text.ended",
                assistantMessageID="msg_a",
                textID="text-0",
                text="full text",
            )
        )
        assert events == []

    def test_reasoning_delta(self):
        state = _armed_state()
        events = state.handle(_event("session.next.reasoning.delta", delta="hmm"))
        assert events[0]["type"] == "reasoning_delta"
        assert events[0]["reasoning"] == "hmm"

    def test_tool_use_and_result(self):
        state = _armed_state()
        called = state.handle(
            _event(
                "session.next.tool.called",
                callID="call_1",
                tool="bash",
                input={"command": "echo hi"},
                timestamp=1000,
            )
        )
        assert called[0]["type"] == "tool_use"
        assert called[0]["tool_name"] == "bash"
        assert called[0]["tool_input"] == {"command": "echo hi"}

        done = state.handle(
            _event(
                "session.next.tool.success",
                callID="call_1",
                content=[{"type": "text", "text": "hi\n"}],
                timestamp=1050,
            )
        )
        assert done[0]["type"] == "tool_result"
        assert done[0]["content"] == "hi\n"
        assert done[0]["duration_ms"] == 50
        assert "is_error" not in done[0]

    def test_tool_failed(self):
        state = _armed_state()
        state.handle(
            _event(
                "session.next.tool.called",
                callID="call_2",
                tool="bash",
                input={},
            )
        )
        events = state.handle(
            _event(
                "session.next.tool.failed",
                callID="call_2",
                error={"type": "unknown", "message": "no such file"},
            )
        )
        assert events[0]["is_error"] is True
        assert events[0]["content"] == "no such file"

    def test_step_failed_is_fatal(self):
        state = _armed_state()
        events = state.handle(
            _event(
                "session.next.step.failed",
                assistantMessageID="msg_a",
                error={"type": "unknown", "message": "provider down"},
            )
        )
        assert [e["type"] for e in events] == ["error", "session_end"]
        assert events[0]["is_fatal"] is True
        assert state.finished is True

    def test_session_error_with_named_shape(self):
        state = _armed_state()
        events = state.handle(
            _event(
                "session.error",
                error={"name": "ProviderAuthError", "data": {"message": "no key"}},
            )
        )
        assert events[0]["error"] == "ProviderAuthError: no key"

    def test_retried_is_non_fatal(self):
        state = _armed_state()
        events = state.handle(
            _event(
                "session.next.retried",
                attempt=1,
                error={"message": "rate limited", "isRetryable": True},
            )
        )
        assert events[0]["type"] == "error"
        assert events[0].get("is_fatal") is not True
        assert "rate limited" in events[0]["error"]

    def test_session_idle_ends_turn(self):
        state = _armed_state()
        events = state.handle(_event("session.idle"))
        assert events[0]["type"] == "session_end"
        assert state.finished is True

    def test_max_turns_interrupts(self):
        state = _armed_state(max_turns=1)
        state.handle(_event("session.next.step.started", assistantMessageID="a"))
        events = state.handle(
            _event("session.next.step.started", assistantMessageID="b")
        )
        assert [e["type"] for e in events] == ["error", "session_end"]
        assert state.actions == [("interrupt", "", "")]

    def test_permission_full_auto_replies_always(self):
        state = _armed_state(permission_level="full_auto")
        events = state.handle(
            _event(
                "permission.v2.asked",
                id="per_1",
                action="bash",
                resources=["echo hi"],
            )
        )
        assert [e["type"] for e in events] == [
            "permission_request",
            "permission_response",
        ]
        assert events[1]["granted"] is True
        assert state.actions == [("permission", "per_1", "always")]

    def test_permission_default_fails_fast(self):
        state = _armed_state(permission_level=None)
        events = state.handle(_event("permission.v2.asked", id="per_1", action="bash"))
        types = [e["type"] for e in events]
        assert types == [
            "permission_request",
            "permission_response",
            "error",
            "session_end",
        ]
        assert events[1]["granted"] is False
        assert events[2]["is_fatal"] is True
        assert state.actions == [("permission", "per_1", "reject")]

    def test_question_fails_fast(self):
        state = _armed_state(permission_level="full_auto")
        events = state.handle(_event("question.v2.asked", id="que_1"))
        assert [e["type"] for e in events] == ["error", "session_end"]
        assert state.actions == [("question", "que_1", "")]

    def test_unknown_events_ignored(self):
        state = _armed_state()
        assert state.handle(_event("session.next.compaction.started")) == []
        assert state.handle(_event("server.heartbeat")) == []


# ============================================================================
# Provider against a mock server
# ============================================================================


def _script(tool: bool = False) -> list[dict[str, Any]]:
    events = [
        _event(
            "session.next.prompt.admitted",
            messageID="msg_user_mock",
            prompt={"text": "hi"},
            delivery="steer",
        ),
        _event(
            "session.next.step.started",
            assistantMessageID="msg_a",
            model={"providerID": "rosetta", "id": "deepseek"},
        ),
        _event(
            "session.next.text.delta",
            assistantMessageID="msg_a",
            textID="text-0",
            delta="O",
        ),
        _event(
            "session.next.text.delta",
            assistantMessageID="msg_a",
            textID="text-0",
            delta="K",
        ),
        _event(
            "session.next.text.ended",
            assistantMessageID="msg_a",
            textID="text-0",
            text="OK",
        ),
    ]
    if tool:
        events.extend(
            [
                _event(
                    "session.next.tool.called",
                    assistantMessageID="msg_a",
                    callID="call_1",
                    tool="bash",
                    input={"command": "echo hi"},
                    timestamp=1000,
                ),
                _event(
                    "session.next.tool.success",
                    assistantMessageID="msg_a",
                    callID="call_1",
                    content=[{"type": "text", "text": "hi\n"}],
                    timestamp=1010,
                ),
            ]
        )
    events.append(
        _event(
            "session.next.step.ended",
            assistantMessageID="msg_a",
            finish="stop",
            cost=0.5,
            tokens={
                "input": 10,
                "output": 2,
                "reasoning": 0,
                "cache": {"read": 0, "write": 0},
            },
        )
    )
    return events


def _task_for(mock: MockOpenCodeV2, **overrides: Any) -> TaskConfig:
    extensions = {"opencode": {"base_url": mock.base_url}}
    return _task(agent_extensions=extensions, **overrides)


class TestProviderAgainstMock:
    async def test_run_streams_text_and_result(self):
        with MockOpenCodeV2(_script()) as mock:
            provider = OpenCodeV2Provider()
            result = await provider.run(_task_for(mock, model="rosetta/deepseek"))
            assert result["status"] == "success"
            assert result["result_text"] == "OK"
            assert result["session_id"] == SESSION
            assert result["model"] == "rosetta/deepseek"
            assert result["usage"]["total_tokens"] == 12
            assert result["cost_usd"] == 0.5

    async def test_stream_event_order(self):
        with MockOpenCodeV2(_script(tool=True)) as mock:
            provider = OpenCodeV2Provider()
            task = _task_for(mock, model="rosetta/deepseek")
            events = [event async for event in provider.stream(task)]
            types = [event["type"] for event in events]
            assert types[0] == "session_start"
            assert events[0]["model"] == "rosetta/deepseek"
            assert "tool_use" in types
            assert "tool_result" in types
            deltas = [e["text"] for e in events if e["type"] == "message_delta"]
            assert deltas == ["O", "K"]
            assert types[-1] == "session_end"

    async def test_model_is_set_from_task(self):
        with MockOpenCodeV2(_script()) as mock:
            provider = OpenCodeV2Provider()
            await provider.run(_task_for(mock, model="rosetta/other"))
            assert mock.models == [{"model": {"providerID": "rosetta", "id": "other"}}]

    async def test_default_model_from_config(self):
        with MockOpenCodeV2(_script()) as mock:
            provider = OpenCodeV2Provider()
            await provider.run(_task_for(mock))
            assert mock.models == [
                {"model": {"providerID": "mock", "id": "default-model"}}
            ]

    async def test_prompt_body_and_delivery(self):
        with MockOpenCodeV2(_script()) as mock:
            provider = OpenCodeV2Provider()
            await provider.run(_task_for(mock))
            assert mock.prompts == [{"prompt": {"text": "hi"}, "delivery": "steer"}]

    async def test_session_created_with_directory(self, tmp_path):
        with MockOpenCodeV2(_script()) as mock:
            provider = OpenCodeV2Provider()
            await provider.run(_task_for(mock, working_dir=str(tmp_path)))
            assert ("POST", "/session") in mock.requests

    async def test_resume_reuses_existing_session(self):
        with MockOpenCodeV2(_script()) as mock:
            provider = OpenCodeV2Provider()
            result = await provider.run(
                _task_for(mock, session_id="ses_existing", resume=True)
            )
            assert result["session_id"] == "ses_existing"
            assert ("GET", "/api/session/ses_existing") in mock.requests
            assert ("POST", "/session") not in mock.requests

    async def test_timeout_interrupts(self):
        with MockOpenCodeV2(_script()[:-1], hold_open=True) as mock:
            provider = OpenCodeV2Provider()
            result = await provider.run(_task_for(mock, timeout=0.5))
            assert result["status"] == "error"
            assert "Timed out" in result["errors"][0]
            assert mock.interrupts >= 1

    async def test_cancellation_interrupts(self):
        with MockOpenCodeV2(_script()[:-1], hold_open=True) as mock:
            provider = OpenCodeV2Provider()
            task = asyncio.create_task(provider.run(_task_for(mock)))
            for _ in range(100):
                if mock.prompts:
                    break
                await asyncio.sleep(0.02)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert mock.interrupts >= 1

    async def test_configured_server_without_v2_is_an_error(self):
        with MockOpenCodeV2(_script(), support_v2=False) as mock:
            provider = OpenCodeV2Provider()
            events = [event async for event in provider.stream(_task_for(mock))]
            assert [e["type"] for e in events] == ["error"]
            assert "not usable" in events[0]["error"]

    async def test_v1_transport_skips_v2(self, monkeypatch):
        provider = OpenCodeV2Provider()
        from agentabi.providers.opencode_native import OpenCodeNativeProvider

        async def fake_stream(self, task):  # noqa: ANN001
            yield {"type": "message_end", "text": "v1"}

        monkeypatch.setattr(OpenCodeNativeProvider, "stream", fake_stream)
        task = _task(agent_extensions={"opencode": {"transport": "v1"}})
        events = [event async for event in provider.stream(task)]
        assert events == [{"type": "message_end", "text": "v1"}]

    async def test_missing_resume_session_is_an_error(self):
        with MockOpenCodeV2(_script()) as mock:
            mock.known_sessions = set()
            provider = OpenCodeV2Provider()
            task = _task_for(mock, session_id="ses_missing", resume=True)
            events = [event async for event in provider.stream(task)]
            assert [e["type"] for e in events] == ["error"]
            assert "ses_missing" in events[0]["error"]

    async def test_spawned_server_falls_back_to_v1(self, monkeypatch):
        """A CLI that starts but has no V2 API falls back in auto mode."""
        provider = OpenCodeV2Provider()

        async def fake_start(self, _task: TaskConfig) -> opencode_v2._Server:
            raise opencode_v2._V2Unavailable("no V2 API")

        monkeypatch.setattr(OpenCodeV2Provider, "_start_server", fake_start)
        from agentabi.providers.opencode_native import OpenCodeNativeProvider

        async def fake_stream(self, task):  # noqa: ANN001
            yield {"type": "session_start", "session_id": "v1", "agent": "opencode"}
            yield {"type": "message_end", "text": "from-v1"}

        monkeypatch.setattr(OpenCodeNativeProvider, "stream", fake_stream)
        events = [event async for event in provider.stream(_task())]
        assert [e["type"] for e in events] == ["session_start", "message_end"]
        assert events[1]["text"] == "from-v1"

    async def test_spawn_failure_raises_when_forced_v2(self, monkeypatch):
        provider = OpenCodeV2Provider()

        async def fake_start(self, _task: TaskConfig) -> opencode_v2._Server:
            raise opencode_v2.OpenCodeV2Error("cannot start")

        monkeypatch.setattr(OpenCodeV2Provider, "_start_server", fake_start)
        monkeypatch.setenv("AGENTABI_OPENCODE_TRANSPORT", "v2")
        events = [event async for event in provider.stream(_task())]
        assert [e["type"] for e in events] == ["error"]
        assert events[0]["is_fatal"] is True
        assert "cannot start" in events[0]["error"]

    async def test_no_model_task_and_no_config(self, monkeypatch):
        with MockOpenCodeV2(_script()) as mock:
            provider = OpenCodeV2Provider()

            async def no_default(_base: str) -> None:
                return None

            monkeypatch.setattr(
                OpenCodeV2Provider, "_default_model", staticmethod(no_default)
            )
            result = await provider.run(_task_for(mock))
            assert mock.models == []
            assert result["status"] == "success"


class TestAvailability:
    def test_capabilities(self):
        capabilities = OpenCodeV2Provider().capabilities()
        assert capabilities["transport"] == "http"
        assert capabilities["supports_streaming"] is True
        assert capabilities["supports_session_resume"] is True
        assert capabilities["supports_multi_turn"] is True
        assert capabilities["supports_system_prompt"] is False

    def test_explicit_v1_disables_provider(self, monkeypatch):
        monkeypatch.setenv("AGENTABI_OPENCODE_TRANSPORT", "v1")
        monkeypatch.setattr(opencode_v2.shutil, "which", lambda _: "/usr/bin/opencode")
        assert OpenCodeV2Provider.is_available() is False

    def test_configured_base_url_is_available(self, monkeypatch):
        monkeypatch.delenv("AGENTABI_OPENCODE_TRANSPORT", raising=False)
        monkeypatch.setenv("OPENCODE_BASE_URL", "http://127.0.0.1:9999")
        assert OpenCodeV2Provider.is_available() is True

    def test_auto_requires_new_enough_cli(self, monkeypatch):
        monkeypatch.delenv("AGENTABI_OPENCODE_TRANSPORT", raising=False)
        monkeypatch.delenv("OPENCODE_BASE_URL", raising=False)
        monkeypatch.delenv("OPENCODE_SERVER_URL", raising=False)
        monkeypatch.setattr(opencode_v2.shutil, "which", lambda _: "/usr/bin/opencode")
        monkeypatch.setitem(opencode_v2._version_cache, "opencode", (1, 18, 0))
        assert OpenCodeV2Provider.is_available() is False
        monkeypatch.setitem(opencode_v2._version_cache, "opencode", (1, 19, 0))
        assert OpenCodeV2Provider.is_available() is True

    def test_missing_cli_is_unavailable(self, monkeypatch):
        monkeypatch.delenv("AGENTABI_OPENCODE_TRANSPORT", raising=False)
        monkeypatch.delenv("OPENCODE_BASE_URL", raising=False)
        monkeypatch.delenv("OPENCODE_SERVER_URL", raising=False)
        monkeypatch.setattr(opencode_v2.shutil, "which", lambda _: None)
        assert OpenCodeV2Provider.is_available() is False
