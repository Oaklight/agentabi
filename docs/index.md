---
title: Home
hide:
  - navigation
---

# agentabi

**Coding Agent CLI 的统一 Python 接口。**

agentabi 将多个 coding agent CLI — [Claude Code](https://github.com/anthropics/claude-code)、[Codex](https://github.com/openai/codex)、[Gemini CLI](https://github.com/google-gemini/gemini-cli) 和 [OpenCode](https://github.com/opencode-ai/opencode) — 封装在统一的异步 Python API 之下，并提供流式事件支持。

## 为什么选择 agentabi？

每个 coding agent CLI 都有各自的调用方式、输出格式和事件模型。agentabi 提供：

- **统一 API** — 一个 `Session` 类应对所有 agent，切换只需改一个参数
- **中间表示（IR）** — 所有 agent 事件被归一化为通用 IR 事件流，使跨 agent 工具成为可能
- **流式输出** — 实时事件流，支持所有 agent
- **自动检测** — 自动发现系统中安装了哪些 agent CLI
- **Provider 回退** — Native（子进程）和 SDK 两种 provider，自动回退

## 快速示例

```python
import asyncio
from agentabi import Session, detect_agents

async def main():
    # 发现可用 agent
    agents = detect_agents()
    print(f"可用 agent: {agents}")

    # 运行任务
    session = Session(agent="claude_code")
    result = await session.run(prompt="What is 2+2?", max_turns=2)
    print(result["result_text"])

    # 流式事件
    async for event in session.stream(prompt="Explain asyncio"):
        if event["type"] == "message_delta":
            print(event["text"], end="", flush=True)

asyncio.run(main())
```

## 支持的 Agent

| Agent | Provider 类型 | 传输方式 |
|-------|-------------|---------|
| Claude Code | Native（子进程）+ SDK | 子进程 / SDK |
| Codex | SDK | SDK |
| Gemini CLI | Native（子进程）+ SDK | 子进程 / SDK |
| OpenCode | Native（子进程） | 子进程 |

## 开始使用

- [安装](usage/installation.md) — 安装 agentabi 及可选依赖
- [快速开始](usage/quickstart.md) — 5 分钟运行第一个任务
- [流式输出](usage/streaming.md) — 处理实时事件流
- [架构设计](architecture.md) — 了解 provider 模型和 IR 设计
