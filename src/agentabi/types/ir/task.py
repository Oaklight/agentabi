"""
agentabi - IR Task Configuration

Defines TaskConfig, the unified input type for submitting work to any agent CLI.
Analogous to llmir's IRRequest.
"""

from typing import Any, Literal

from typing_extensions import NotRequired, Required, TypedDict

from .permissions import PermissionConfig

# ============================================================================
# Agent type
# ============================================================================

AgentType = Literal[
    "claude_code",
    "codex",
    "gemini_cli",
    "opencode",
]

# ============================================================================
# Task configuration
# ============================================================================


class TaskConfig(TypedDict):
    """Unified task configuration for submitting work to an agent CLI.

    Analogous to llmir's IRRequest. The prompt is the only required field;
    everything else has sensible defaults or is auto-detected.

    Required fields:
        prompt: The task instruction to send to the agent.

    Optional fields (grouped by function):
        Agent selection: agent, model
        Execution context: working_dir, env
        Session management: session_id, resume
        System configuration: system_prompt, max_turns, timeout
        Permission control: permissions, allowed_tools, disallowed_tools
        Extension: agent_extensions
    """

    # ========== Required ==========
    prompt: Required[str]

    # ========== Agent Selection ==========
    agent: NotRequired[AgentType]
    model: NotRequired[str]

    # ========== Execution Context ==========
    working_dir: NotRequired[str]
    env: NotRequired[dict[str, str]]

    # ========== Session Management ==========
    session_id: NotRequired[str]
    resume: NotRequired[bool]

    # ========== System Configuration ==========
    system_prompt: NotRequired[str]
    append_system_prompt: NotRequired[str]
    max_turns: NotRequired[int]
    timeout: NotRequired[float]  # seconds

    # ========== Permission Control ==========
    permissions: NotRequired[PermissionConfig]
    allowed_tools: NotRequired[list[str]]
    disallowed_tools: NotRequired[list[str]]

    # ========== MCP ==========
    mcp_config: NotRequired[str]  # path to MCP config file

    # ========== Agent-Specific Extensions ==========
    agent_extensions: NotRequired[dict[str, Any]]


__all__ = [
    "AgentType",
    "TaskConfig",
]
