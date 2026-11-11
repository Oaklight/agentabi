"""Observation events for Claude Code's native subagent tool."""

from __future__ import annotations

from agentabi.providers.claude_native import ClaudeNativeProvider


def _assistant_tool(tool_id: str, name: str, tool_input: dict) -> dict:
    return {
        "type": "assistant",
        "message": {
            "id": "msg_1",
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": tool_id, "name": name, "input": tool_input}
            ],
            "stop_reason": "tool_use",
        },
    }


def _tool_result(tool_id: str, content: str, *, is_error: bool = False) -> dict:
    block = {"type": "tool_result", "tool_use_id": tool_id, "content": content}
    if is_error:
        block["is_error"] = True
    return {"type": "user", "message": {"role": "user", "content": [block]}}


class TestSubagentObservation:
    def test_agent_tool_emits_start_and_result_emits_end(self) -> None:
        provider = ClaudeNativeProvider()
        events = provider.parse_event(
            _assistant_tool(
                "call_1",
                "Agent",
                {
                    "subagent_type": "probe",
                    "prompt": "say hi",
                    "run_in_background": True,
                },
            )
        )
        types = [event["type"] for event in events]
        assert types == ["message_start", "tool_use", "subagent_start", "message_end"]
        start = events[2]
        assert start["subagent_id"] == "call_1"
        assert start["agent"] == "probe"
        assert start["prompt"] == "say hi"
        assert start["background"] is True

        end_events = provider.parse_event(_tool_result("call_1", "OK"))
        assert [event["type"] for event in end_events] == [
            "tool_result",
            "subagent_end",
        ]
        end = end_events[1]
        assert end["subagent_id"] == "call_1"
        assert end["status"] == "completed"
        assert end["result"] == "OK"

        # The id is consumed; a second result is a plain tool result.
        again = provider.parse_event(_tool_result("call_1", "OK"))
        assert [event["type"] for event in again] == ["tool_result"]

    def test_task_tool_is_recognised(self) -> None:
        provider = ClaudeNativeProvider()
        events = provider.parse_event(
            _assistant_tool("call_2", "Task", {"subagent_type": "reviewer"})
        )
        start = next(event for event in events if event["type"] == "subagent_start")
        assert start["agent"] == "reviewer"
        assert "prompt" not in start

    def test_failed_subagent(self) -> None:
        provider = ClaudeNativeProvider()
        provider.parse_event(_assistant_tool("call_3", "Agent", {"prompt": "x"}))
        events = provider.parse_event(_tool_result("call_3", "boom", is_error=True))
        end = next(event for event in events if event["type"] == "subagent_end")
        assert end["status"] == "failed"

    def test_unrelated_tool_has_no_subagent_events(self) -> None:
        provider = ClaudeNativeProvider()
        events = provider.parse_event(
            _assistant_tool("call_4", "Bash", {"command": "ls"})
        )
        assert all(event["type"] != "subagent_start" for event in events)

    def test_capability_declared(self) -> None:
        caps = ClaudeNativeProvider().capabilities()
        assert caps.get("supports_subagent_events") is True

    def test_static_parse_keeps_working_for_tui(self) -> None:
        events = ClaudeNativeProvider._parse_event(
            _assistant_tool("call_5", "Agent", {"prompt": "x"})
        )
        assert [event["type"] for event in events][:3] == [
            "message_start",
            "tool_use",
            "subagent_start",
        ]
