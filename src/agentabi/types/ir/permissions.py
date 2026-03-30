"""
agentabi - Permission Types

Defines permission configuration and request types for the agent ABI.
"""

from typing import List, Literal

from typing_extensions import NotRequired, Required, TypedDict

# ============================================================================
# Permission level
# ============================================================================

PermissionLevel = Literal[
    "default",  # prompt for sensitive operations
    "accept_edits",  # auto-approve file edits
    "plan",  # planning mode, no execution
    "full_auto",  # auto-approve everything
]

# ============================================================================
# Permission config
# ============================================================================


class PermissionConfig(TypedDict):
    """Permission configuration for a task.

    Controls what the agent is allowed to do without explicit approval.
    """

    level: NotRequired[PermissionLevel]
    allowed_tools: NotRequired[List[str]]
    disallowed_tools: NotRequired[List[str]]
    sandbox: NotRequired[bool]  # run in sandboxed environment


# ============================================================================
# Permission request (runtime)
# ============================================================================


class PermissionRequest(TypedDict):
    """A runtime permission request from an agent.

    Represents a tool invocation that requires user approval.
    """

    tool_name: Required[str]
    tool_use_id: Required[str]
    tool_input: NotRequired[dict]
    description: NotRequired[str]


__all__ = [
    "PermissionLevel",
    "PermissionConfig",
    "PermissionRequest",
]
