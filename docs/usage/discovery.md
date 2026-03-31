# Agent 发现

agentabi 可以自动检测系统中安装了哪些 coding agent CLI。

## 检测可用 Agent

```python
from agentabi import detect_agents

agents = detect_agents()
print(agents)
# ['claude_code', 'codex', 'gemini_cli', 'opencode']
```

`detect_agents()` 检查每个已注册 agent 是否有至少一个可用的 provider（CLI 在 PATH 中或已安装 SDK）。

## Agent 能力

每个 agent 提供一个能力字典：

```python
from agentabi import get_agent_capabilities

caps = get_agent_capabilities("claude_code")
```

`AgentCapabilities` 字典包含：

| 字段 | 类型 | 描述 |
|-----|------|------|
| `name` | `str` | 人类可读名称 |
| `agent_type` | `str` | Agent 标识符 |
| `supports_streaming` | `bool` | 实时事件流 |
| `supports_mcp` | `bool` | Model Context Protocol 工具 |
| `supports_session_resume` | `bool` | 恢复历史会话 |
| `supports_system_prompt` | `bool` | 自定义系统提示词 |
| `supports_tool_filtering` | `bool` | 选择性启用工具 |
| `supports_permissions` | `bool` | 权限/审批控制 |
| `supports_multi_turn` | `bool` | 多轮对话 |
| `transport` | `str` | `"subprocess"` 或 `"sdk"`（可选） |

## 默认 Agent

获取第一个可用的 agent：

```python
from agentabi import get_default_agent

agent = get_default_agent()
# 如果没有 agent 可用，抛出 AgentNotAvailable
```

## Provider 查看

进阶用法——查看底层 provider：

```python
from agentabi import get_provider

provider = get_provider("claude_code")
print(type(provider).__name__)
# 'ClaudeNativeProvider' 或 'ClaudeSDKProvider'

caps = provider.capabilities()
print(caps)
```

## Agent 标识符

| 标识符 | CLI | Provider 链 |
|-------|-----|-------------|
| `claude_code` | `claude` | ClaudeNativeProvider, ClaudeSDKProvider |
| `codex` | `codex` | CodexNativeProvider, CodexSDKProvider |
| `gemini_cli` | `gemini` | GeminiNativeProvider, GeminiSDKProvider |
| `opencode` | `opencode` | OpenCodeNativeProvider |

Provider 链决定了回退顺序。agentabi 按顺序尝试每个 provider，使用第一个 `is_available() == True` 的。
