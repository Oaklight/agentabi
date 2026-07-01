"""
agentabi - Unified interface layer for agentic coding CLIs.

agentabi wraps multiple coding agent CLIs (Claude Code, Codex, Gemini CLI,
OpenCode) behind a unified async Python API with streaming support.

Quick start:
    from agentabi import Session

    session = Session(agent="claude_code")
    result = await session.run(prompt="Fix the bug in auth.py")

Streaming:
    async for event in session.stream(prompt="Explain this code"):
        if event["type"] == "message_delta":
            print(event["text"], end="")

Sync convenience:
    from agentabi import run_sync
    result = run_sync(prompt="List Python files", agent="claude_code")

Middleware:
    from agentabi.middleware import LoggingMiddleware, TimeoutMiddleware

    session = Session(
        agent="claude_code",
        middleware=[LoggingMiddleware(), TimeoutMiddleware(120)],
    )

Discovery:
    from agentabi import detect_agents, get_agent_capabilities
    agents = detect_agents()
    caps = get_agent_capabilities("claude_code")
"""

from .auto_detect import detect_agents, get_agent_capabilities, get_default_agent
from .middleware import (
    LoggingMiddleware,
    Middleware,
    StreamHandler,
    TimeoutMiddleware,
    UsageMeterMiddleware,
    chain_middleware,
)
from .providers.base import Provider
from .providers.registry import AgentNotAvailable, get_provider
from .session import Session, run_sync
from .types.ir import (
    AgentCapabilities,
    AgentType,
    ErrorEvent,
    FileDiffEvent,
    IREvent,
    MessageDeltaEvent,
    MessageEndEvent,
    MessageStartEvent,
    PermissionConfig,
    PermissionLevel,
    PermissionRequest,
    PermissionRequestEvent,
    PermissionResponseEvent,
    SessionEndEvent,
    SessionResult,
    SessionStartEvent,
    SessionStatus,
    TaskConfig,
    ToolResultEvent,
    ToolUseEvent,
    UsageEvent,
)

__version__ = "0.2.1"

__all__ = [
    # Consumer API
    "Session",
    "run_sync",
    # Discovery
    "detect_agents",
    "get_agent_capabilities",
    "get_default_agent",
    # Provider
    "Provider",
    "get_provider",
    "AgentNotAvailable",
    # IR types - Task
    "TaskConfig",
    "AgentType",
    # IR types - Events
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
    # IR types - Session
    "SessionResult",
    "SessionStatus",
    # IR types - Capabilities & Permissions
    "AgentCapabilities",
    "PermissionConfig",
    "PermissionLevel",
    "PermissionRequest",
    # Middleware
    "Middleware",
    "StreamHandler",
    "chain_middleware",
    "LoggingMiddleware",
    "TimeoutMiddleware",
    "UsageMeterMiddleware",
]
