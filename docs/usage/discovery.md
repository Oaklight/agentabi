# Agent Discovery

agentabi can automatically detect which coding agent CLIs are installed on your system.

## Detecting Available Agents

```python
from agentabi import detect_agents

agents = detect_agents()
print(agents)
# ['claude_code', 'codex', 'gemini_cli', 'opencode']
```

`detect_agents()` checks each registered agent for at least one available provider (either a CLI binary in PATH or an installed SDK).

## Agent Capabilities

Each agent exposes a capabilities dictionary:

```python
from agentabi import get_agent_capabilities

caps = get_agent_capabilities("claude_code")
```

The `AgentCapabilities` dictionary includes:

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Human-readable name |
| `agent_type` | `str` | Agent identifier |
| `supports_streaming` | `bool` | Real-time event streaming |
| `supports_mcp` | `bool` | Model Context Protocol tools |
| `supports_session_resume` | `bool` | Resume previous sessions |
| `supports_system_prompt` | `bool` | Custom system prompts |
| `supports_tool_filtering` | `bool` | Selective tool enabling |
| `supports_permissions` | `bool` | Permission/approval control |
| `supports_multi_turn` | `bool` | Multi-turn conversations |
| `transport` | `str` | `"subprocess"` or `"sdk"` (optional) |

## Default Agent

Get the first available agent:

```python
from agentabi import get_default_agent

agent = get_default_agent()
# Raises AgentNotAvailable if no agents are found
```

## Provider Inspection

For advanced use, you can inspect the underlying provider:

```python
from agentabi import get_provider

provider = get_provider("claude_code")
print(type(provider).__name__)
# 'ClaudeNativeProvider' or 'ClaudeSDKProvider'

caps = provider.capabilities()
print(caps)
```

## Agent Identifiers

| Identifier | CLI | Provider Chain |
|-----------|-----|----------------|
| `claude_code` | `claude` | ClaudeNativeProvider, ClaudeSDKProvider |
| `codex` | `codex` | CodexNativeProvider, CodexSDKProvider |
| `gemini_cli` | `gemini` | GeminiNativeProvider, GeminiSDKProvider |
| `opencode` | `opencode` | OpenCodeNativeProvider |

The provider chain determines fallback order. agentabi tries each provider in order and uses the first one that reports `is_available() == True`.
