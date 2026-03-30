# API 参考

本节文档记录 agentabi 的公开 API。

## 核心 API

- [**Session**](session.md) — 与 agent CLI 交互的主接口。提供 `run()` 和 `stream()` 方法。
- [**Providers**](providers.md) — Provider 协议和注册表系统，连接 Session 与 agent 后端。

## IR（中间表示）

- [**IR 事件**](ir-events.md) — `stream()` 产出的所有事件类型，跨 agent 归一化。
- [**IR 类型**](ir-types.md) — `TaskConfig`、`SessionResult`、`AgentCapabilities` 等辅助类型。

## 快速导入参考

```python
# 消费者 API
from agentabi import Session, run_sync

# 发现
from agentabi import detect_agents, get_agent_capabilities, get_default_agent

# Provider 访问
from agentabi import get_provider, AgentNotAvailable

# IR 类型
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
