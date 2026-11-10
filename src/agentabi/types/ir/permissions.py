"""
agentabi - Permission Types

Defines permission configuration and runtime request types for the agent ABI.
"""

from typing import Literal

from typing_extensions import NotRequired, Required, TypedDict

# ============================================================================
# Permission level
# ============================================================================

PermissionLevel = Literal[
    "default",  # prompt for sensitive operations
    "accept_edits",  # auto-approve file edits
    "plan",  # planning mode, no execution
    "full_auto",  # auto-approve everything (bypass all checks)
    "auto",  # auto mode (agent decides)
    "dont_ask",  # never prompt, skip if not auto-approved
]

# ============================================================================
# Permission config
# ============================================================================


class PermissionConfig(TypedDict):
    """Permission configuration for a task.

    Controls what the agent is allowed to do without explicit approval.
    """

    level: NotRequired[PermissionLevel]
    allowed_tools: NotRequired[list[str]]
    disallowed_tools: NotRequired[list[str]]
    sandbox: NotRequired[bool]  # run in sandboxed environment


# ============================================================================
# Permission options and decisions (runtime)
# ============================================================================

PermissionOptionKind = Literal[
    "allow_once",
    "allow_always",
    "reject_once",
    "reject_always",
]


class PermissionOption(TypedDict):
    """One option offered to the client in a permission prompt.

    ``option_id`` is the id the client must echo back.  IR backends use the
    canonical ids, which equal their ``kind``.  ACP pass-through backends keep
    the child's ids and rely on ``kind`` for the portable meaning.
    """

    option_id: Required[str]
    name: Required[str]
    kind: Required[PermissionOptionKind]


class PermissionDecision(TypedDict):
    """A resolved permission decision returned to a provider.

    ``option_id`` is ``None`` when ``outcome`` is ``"cancelled"``.
    """

    option_id: Required[str | None]
    outcome: Required[Literal["selected", "cancelled"]]


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
    "PermissionOptionKind",
    "PermissionOption",
    "PermissionDecision",
    "PermissionRequest",
]
