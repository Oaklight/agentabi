"""
agentabi - IR Types

Intermediate representation types for the agent ABI layer.
All IR types are TypedDicts following the llmir pattern.
"""

from .capabilities import AgentCapabilities
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
from .helpers import (
    create_error_event,
    create_message_delta_event,
    create_session_start_event,
    create_tool_use_event,
    create_usage_event,
)
from .permissions import PermissionConfig, PermissionLevel, PermissionRequest
from .session import SessionResult, SessionStatus
from .task import AgentType, TaskConfig
from .type_guards import (
    EVENT_TYPE_MAP,
    get_event_type,
    is_event_type,
)

__all__ = [
    # Task
    "TaskConfig",
    "AgentType",
    # Events
    "IREvent",
    "SessionStartEvent",
    "SessionEndEvent",
    "MessageStartEvent",
    "MessageDeltaEvent",
    "MessageEndEvent",
    "ToolUseEvent",
    "ToolResultEvent",
    "PermissionRequestEvent",
    "PermissionResponseEvent",
    "UsageEvent",
    "ErrorEvent",
    "FileDiffEvent",
    # Session
    "SessionResult",
    "SessionStatus",
    # Capabilities
    "AgentCapabilities",
    # Permissions
    "PermissionConfig",
    "PermissionLevel",
    "PermissionRequest",
    # Type guards
    "is_event_type",
    "get_event_type",
    "EVENT_TYPE_MAP",
    # Helpers
    "create_session_start_event",
    "create_message_delta_event",
    "create_tool_use_event",
    "create_usage_event",
    "create_error_event",
]
