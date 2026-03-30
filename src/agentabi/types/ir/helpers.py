"""
agentabi - IR Helpers

Convenience constructors for creating IR events and types.
"""

from typing import Any, Dict, List, Optional

from .events import (
    ErrorEvent,
    MessageDeltaEvent,
    SessionStartEvent,
    ToolUseEvent,
    UsageEvent,
    UsageInfo,
)


def create_session_start_event(
    session_id: str,
    *,
    agent: Optional[str] = None,
    model: Optional[str] = None,
    tools: Optional[List[str]] = None,
    working_dir: Optional[str] = None,
) -> SessionStartEvent:
    """Create a SessionStartEvent with optional metadata."""
    event: SessionStartEvent = {
        "type": "session_start",
        "session_id": session_id,
    }
    if agent is not None:
        event["agent"] = agent
    if model is not None:
        event["model"] = model
    if tools is not None:
        event["tools"] = tools
    if working_dir is not None:
        event["working_dir"] = working_dir
    return event


def create_message_delta_event(
    text: str,
    *,
    message_id: Optional[str] = None,
) -> MessageDeltaEvent:
    """Create a MessageDeltaEvent."""
    event: MessageDeltaEvent = {
        "type": "message_delta",
        "text": text,
    }
    if message_id is not None:
        event["message_id"] = message_id
    return event


def create_tool_use_event(
    tool_use_id: str,
    tool_name: str,
    tool_input: Dict[str, Any],
) -> ToolUseEvent:
    """Create a ToolUseEvent."""
    return {
        "type": "tool_use",
        "tool_use_id": tool_use_id,
        "tool_name": tool_name,
        "tool_input": tool_input,
    }


def create_usage_event(
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cost_usd: Optional[float] = None,
    model: Optional[str] = None,
) -> UsageEvent:
    """Create a UsageEvent."""
    usage: UsageInfo = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
    }
    event: UsageEvent = {
        "type": "usage",
        "usage": usage,
    }
    if cost_usd is not None:
        event["cost_usd"] = cost_usd
    if model is not None:
        event["model"] = model
    return event


def create_error_event(
    error: str,
    *,
    is_fatal: bool = False,
) -> ErrorEvent:
    """Create an ErrorEvent."""
    event: ErrorEvent = {
        "type": "error",
        "error": error,
    }
    if is_fatal:
        event["is_fatal"] = is_fatal
    return event


__all__ = [
    "create_session_start_event",
    "create_message_delta_event",
    "create_tool_use_event",
    "create_usage_event",
    "create_error_event",
]
