"""Canonical IR stop reasons.

Every transport (native subprocess, SDK, PTY, HTTP/SSE) reports why a
message or turn ended in its own spelling.  The IR contract uses one set,
so a consumer can switch on ``stop_reason`` without knowing which CLI
produced the event:

``end_turn``
    The model finished its answer; a tool-call turn also ends here.
``max_tokens``
    The output limit was reached.
``max_turn_requests``
    The agent's own turn budget was reached.
``refusal``
    The model refused.
``cancelled``
    The user or driver cancelled the turn.
``error``
    The turn failed.
"""

from __future__ import annotations

from typing import Final

CANONICAL_STOP_REASONS: Final[frozenset[str]] = frozenset(
    {
        "end_turn",
        "max_tokens",
        "max_turn_requests",
        "refusal",
        "cancelled",
        "error",
    }
)

#: Provider and API spellings mapped to the canonical value.
STOP_REASON_ALIASES: Final[dict[str, str]] = {
    # Ordinary completion.
    "end_turn": "end_turn",
    "endTurn": "end_turn",
    "stop": "end_turn",
    "stop_sequence": "end_turn",
    "tool_use": "end_turn",
    "toolUse": "end_turn",
    "tool-calls": "end_turn",
    "tool_calls": "end_turn",
    # Length.
    "length": "max_tokens",
    "max_tokens": "max_tokens",
    "maxTokens": "max_tokens",
    # Agent-side turn budget.
    "max_turn_requests": "max_turn_requests",
    "maxTurns": "max_turn_requests",
    # Refusal.
    "refusal": "refusal",
    "content_filter": "refusal",
    # Cancellation.
    "cancelled": "cancelled",
    "canceled": "cancelled",
    "aborted": "cancelled",
    "interrupted": "cancelled",
    # Failure.
    "error": "error",
    "failed": "error",
}


def canonical_stop_reason(value: object, *, default: str | None = None) -> str | None:
    """Return the canonical stop reason for *value*.

    Args:
        value: Raw stop reason reported by a transport.
        default: Canonical value to return when *value* is empty or not
            recognised.  ``None`` means "unknown, omit the field".

    Returns:
        A value from :data:`CANONICAL_STOP_REASONS`, or *default*.

    Examples:
        >>> canonical_stop_reason("tool-calls")
        'end_turn'
        >>> canonical_stop_reason(None) is None
        True
    """
    if isinstance(value, str):
        text = value.strip()
        if text:
            mapped = STOP_REASON_ALIASES.get(text) or STOP_REASON_ALIASES.get(
                text.lower()
            )
            if mapped is not None:
                return mapped
    return default


def stop_reason(value: object, *, default: str = "end_turn") -> str:
    """Return a canonical stop reason, never ``None``.

    This is the form event builders use: every ``message_end`` carries a
    canonical ``stop_reason``, so a consumer never has to know which CLI
    omitted it.

    Args:
        value: Raw stop reason reported by a transport.
        default: Value used for an empty or unrecognised reason.

    Returns:
        A value from :data:`CANONICAL_STOP_REASONS`.
    """
    mapped = canonical_stop_reason(value)
    return mapped if mapped is not None else default


__all__ = [
    "CANONICAL_STOP_REASONS",
    "STOP_REASON_ALIASES",
    "canonical_stop_reason",
    "stop_reason",
]
