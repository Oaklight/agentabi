"""
agentabi - Agent Capabilities

Declares what features an agent supports, enabling capability-based routing
and graceful degradation.
"""

from typing_extensions import NotRequired, Required, TypedDict


class AgentCapabilities(TypedDict):
    """Declares what features an agent supports.

    Used for capability-based routing and to inform consumers which
    features are available before submitting a task.
    """

    # ========== Required ==========
    name: Required[str]
    agent_type: Required[str]

    # ========== Feature Support ==========
    supports_streaming: NotRequired[bool]
    supports_mcp: NotRequired[bool]
    supports_session_resume: NotRequired[bool]
    supports_system_prompt: NotRequired[bool]
    supports_tool_filtering: NotRequired[bool]
    supports_file_diffs: NotRequired[bool]
    supports_permissions: NotRequired[bool]
    supports_multi_turn: NotRequired[bool]

    # ========== Transport ==========
    transport: NotRequired[str]  # "subprocess", "http", "websocket"

    # ========== Limits ==========
    max_context_tokens: NotRequired[int]
    max_output_tokens: NotRequired[int]

    # ========== Version ==========
    version: NotRequired[str]


__all__ = [
    "AgentCapabilities",
]
