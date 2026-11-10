"""Stop reasons are canonical across every transport."""

from __future__ import annotations

import pytest

from agentabi.types.ir.stop_reasons import (
    CANONICAL_STOP_REASONS,
    STOP_REASON_ALIASES,
    canonical_stop_reason,
    stop_reason,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("end_turn", "end_turn"),
        ("stop", "end_turn"),
        ("tool_use", "end_turn"),
        ("tool-calls", "end_turn"),
        ("toolUse", "end_turn"),
        ("max_tokens", "max_tokens"),
        ("length", "max_tokens"),
        ("max_turn_requests", "max_turn_requests"),
        ("refusal", "refusal"),
        ("cancelled", "cancelled"),
        ("aborted", "cancelled"),
        ("interrupted", "cancelled"),
        ("error", "error"),
        ("failed", "error"),
        ("END_TURN", "end_turn"),
        ("stop ", "end_turn"),
    ],
)
def test_aliases_map_to_canonical(raw, expected):
    assert canonical_stop_reason(raw) == expected
    assert expected in CANONICAL_STOP_REASONS


def test_unknown_and_empty_values_use_the_default():
    assert canonical_stop_reason("budget_exhausted") is None
    assert canonical_stop_reason("budget_exhausted", default="end_turn") == "end_turn"
    assert canonical_stop_reason("", default="end_turn") == "end_turn"
    assert canonical_stop_reason(None, default="end_turn") == "end_turn"
    assert canonical_stop_reason(42, default="end_turn") == "end_turn"


def test_every_alias_target_is_canonical():
    assert set(STOP_REASON_ALIASES.values()) <= CANONICAL_STOP_REASONS


def test_stop_reason_never_returns_none():
    assert stop_reason("stop") == "end_turn"
    assert stop_reason(None) == "end_turn"
    assert stop_reason("budget_exhausted") == "end_turn"
    assert stop_reason("budget_exhausted", default="max_turns") == "max_turns"
    assert stop_reason("aborted") == "cancelled"
