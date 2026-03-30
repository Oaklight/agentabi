"""
agentabi - Claude Code Configuration Types

TypedDict definitions for Claude Code configuration.
"""

from typing import List

from typing_extensions import NotRequired, TypedDict


class ClaudeCodeConfig(TypedDict):
    """Claude Code configuration options that map to CLI flags."""

    model: NotRequired[str]
    max_turns: NotRequired[int]
    max_budget_usd: NotRequired[float]
    system_prompt: NotRequired[str]
    append_system_prompt: NotRequired[str]
    allowed_tools: NotRequired[List[str]]
    disallowed_tools: NotRequired[List[str]]
    permission_mode: NotRequired[
        str
    ]  # "default", "acceptEdits", "bypassPermissions", "plan"
    mcp_config: NotRequired[str]
    output_format: NotRequired[str]  # "text", "json", "stream-json"
    verbose: NotRequired[bool]
    resume: NotRequired[str]  # session ID
    continue_session: NotRequired[bool]
    include_partial_messages: NotRequired[bool]


__all__ = [
    "ClaudeCodeConfig",
]
