"""Tests for IR types."""

from agentabi.types.ir.events import (
    MessageDeltaEvent,
    SessionStartEvent,
)
from agentabi.types.ir.helpers import (
    create_error_event,
    create_message_delta_event,
    create_session_start_event,
    create_usage_event,
)
from agentabi.types.ir.type_guards import get_event_type, is_event_type


class TestHelpers:
    def test_create_session_start(self):
        event = create_session_start_event("sess-1", agent="claude_code")
        assert event["type"] == "session_start"
        assert event["session_id"] == "sess-1"
        assert event["agent"] == "claude_code"

    def test_create_message_delta(self):
        event = create_message_delta_event("hello")
        assert event["type"] == "message_delta"
        assert event["text"] == "hello"

    def test_create_usage_event(self):
        event = create_usage_event(input_tokens=100, output_tokens=50)
        assert event["usage"]["total_tokens"] == 150

    def test_create_error_event(self):
        event = create_error_event("bad", is_fatal=True)
        assert event["error"] == "bad"
        assert event["is_fatal"] is True


class TestTypeGuards:
    def test_is_event_type_true(self):
        event = {"type": "message_delta", "text": "hi"}
        assert is_event_type(event, MessageDeltaEvent)

    def test_is_event_type_false(self):
        event = {"type": "message_delta", "text": "hi"}
        assert not is_event_type(event, SessionStartEvent)

    def test_get_event_type(self):
        event = {"type": "session_start", "session_id": "1"}
        assert get_event_type(event) is SessionStartEvent

    def test_get_event_type_unknown(self):
        event = {"type": "xyz"}
        assert get_event_type(event) is None
