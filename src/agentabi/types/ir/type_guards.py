"""
agentabi - IR Type Guards

Type guard functions for discriminating IREvent union types.
Follows the llmir is_part_type() pattern.
"""

from typing import Any, Union

from .events import (
    ErrorEvent,
    FileDiffEvent,
    IREvent,
    MessageDeltaEvent,
    MessageEndEvent,
    MessageStartEvent,
    PermissionRequestEvent,
    PermissionResponseEvent,
    SessionEndEvent,
    SessionStartEvent,
    ToolResultEvent,
    ToolUseEvent,
    UsageEvent,
)

# ============================================================================
# Type → string mapping
# ============================================================================

EVENT_TYPE_MAP: dict[str, type[IREvent]] = {
    "session_start": SessionStartEvent,
    "session_end": SessionEndEvent,
    "message_start": MessageStartEvent,
    "message_delta": MessageDeltaEvent,
    "message_end": MessageEndEvent,
    "tool_use": ToolUseEvent,
    "tool_result": ToolResultEvent,
    "permission_request": PermissionRequestEvent,
    "permission_response": PermissionResponseEvent,
    "usage": UsageEvent,
    "error": ErrorEvent,
    "file_diff": FileDiffEvent,
}


def is_event_type(event: Any, event_class: type) -> bool:
    """Check if an event dict matches a specific IREvent type.

    Args:
        event: The event dict to check.
        event_class: The target TypedDict class (e.g., MessageDeltaEvent).

    Returns:
        True if the event matches the specified type.

    Examples:
        >>> event = {"type": "message_delta", "text": "Hello"}
        >>> is_event_type(event, MessageDeltaEvent)  # True
        >>> is_event_type(event, ToolUseEvent)  # False
    """
    if not isinstance(event, dict):
        return False

    event_type = event.get("type")
    if not event_type:
        return False

    # Find expected type string for the class
    expected_type = None
    for type_str, cls in EVENT_TYPE_MAP.items():
        if cls is event_class:
            expected_type = type_str
            break

    if expected_type is None:
        return False

    return event_type == expected_type


def get_event_type(event: Any) -> Union[type[IREvent], None]:
    """Get the TypedDict class for an event dict.

    Args:
        event: The event dict to classify.

    Returns:
        The corresponding TypedDict class, or None if unknown.

    Examples:
        >>> event = {"type": "tool_use", "tool_use_id": "1",
        ...          "tool_name": "Read", "tool_input": {}}
        >>> get_event_type(event)  # ToolUseEvent
    """
    if not isinstance(event, dict):
        return None

    event_type = event.get("type")
    return EVENT_TYPE_MAP.get(event_type) if event_type else None


__all__ = [
    "EVENT_TYPE_MAP",
    "is_event_type",
    "get_event_type",
]
