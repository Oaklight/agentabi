# API Reference

This section documents the agentabi public API.

## Core API

- [**Session**](session.md) — The primary interface for interacting with agent CLIs. Provides `run()` and `stream()` methods.
- [**Providers**](providers.md) — The Provider protocol and registry system that connects Session to agent backends.

## IR (Intermediate Representation)

- [**IR Events**](ir-events.md) — All event types produced by `stream()`, normalized across agents.
- [**IR Types**](ir-types.md) — `TaskConfig`, `SessionResult`, `AgentCapabilities`, and supporting types.

## Quick Import Reference

```python
# Consumer API
from agentabi import Session, run_sync

# Discovery
from agentabi import detect_agents, get_agent_capabilities, get_default_agent

# Provider access
from agentabi import get_provider, AgentNotAvailable

# IR Types
from agentabi import (
    TaskConfig,
    SessionResult,
    AgentCapabilities,
    IREvent,
    SessionStartEvent,
    MessageDeltaEvent,
    MessageEndEvent,
    ToolUseEvent,
    ToolResultEvent,
    UsageEvent,
    ErrorEvent,
)
```
