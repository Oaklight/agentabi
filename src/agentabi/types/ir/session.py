"""
agentabi - IR Session Result

Defines SessionResult, the unified output type after an agent session completes.
Analogous to llmir's IRResponse.
"""

from typing import Any, Dict, List, Literal

from typing_extensions import NotRequired, Required, TypedDict

from .events import FileDiffEvent, UsageInfo

# ============================================================================
# Session status
# ============================================================================

SessionStatus = Literal[
    "success",
    "error",
    "error_max_turns",
    "error_max_budget",
    "error_timeout",
    "cancelled",
]

# ============================================================================
# Session result
# ============================================================================


class SessionResult(TypedDict):
    """Unified result after an agent session completes.

    Analogous to llmir's IRResponse. Contains the final output text,
    file changes, usage statistics, and error information.
    """

    # ========== Required ==========
    session_id: Required[str]
    status: Required[SessionStatus]

    # ========== Agent/Model Info ==========
    agent: NotRequired[str]
    model: NotRequired[str]

    # ========== Output ==========
    result_text: NotRequired[str]

    # ========== File Changes ==========
    file_diffs: NotRequired[List[FileDiffEvent]]

    # ========== Usage ==========
    usage: NotRequired[UsageInfo]
    cost_usd: NotRequired[float]
    duration_ms: NotRequired[int]
    num_turns: NotRequired[int]

    # ========== Error ==========
    error: NotRequired[str]
    errors: NotRequired[List[str]]

    # ========== Agent-Specific ==========
    agent_extensions: NotRequired[Dict[str, Any]]


# ============================================================================
# Session info (for listing/querying sessions)
# ============================================================================


class SessionInfo(TypedDict):
    """Metadata about an existing session, used for listing/resuming."""

    session_id: Required[str]
    agent: NotRequired[str]
    model: NotRequired[str]
    created_at: NotRequired[str]  # ISO 8601
    working_dir: NotRequired[str]
    summary: NotRequired[str]


__all__ = [
    "SessionStatus",
    "SessionResult",
    "SessionInfo",
]
