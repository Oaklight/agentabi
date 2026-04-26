# agentabi

[![PyPI version](https://img.shields.io/pypi/v/agentabi?color=green)](https://pypi.org/project/agentabi/)
[![GitHub release](https://img.shields.io/github/v/release/Oaklight/agentabi?color=green)](https://github.com/Oaklight/agentabi/releases/latest)
[![CI](https://github.com/Oaklight/agentabi/actions/workflows/ci.yml/badge.svg)](https://github.com/Oaklight/agentabi/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/Oaklight/agentabi)

Agentic 编程 CLI 的统一接口层。

一套接口，任意 Agent。

## 什么是 agentabi？

`agentabi` 为不同的 agentic 编程 CLI 提供稳定、统一的接口（一个"ABI"）。只需编写一次集成代码，通过配置切换不同的 agent 后端。

### 支持的 Agent

| Agent | 提供方 | 状态 |
|-------|--------|------|
| [Claude Code](https://github.com/anthropics/claude-code) | Anthropic | 已实现 |
| [Codex](https://github.com/openai/codex) | OpenAI | 已实现 |
| [Gemini CLI](https://github.com/google-gemini/gemini-cli) | Google | 已实现 |
| [OpenCode](https://opencode.ai) | 开源 | 已实现 |

## 安装

```bash
pip install agentabi
```

安装可选的 SDK 集成：

```bash
pip install agentabi[claude]   # Claude Code SDK 支持
pip install agentabi[codex]    # Codex SDK 支持
pip install agentabi[gemini]   # Gemini CLI SDK 支持
pip install agentabi[all]      # 所有可选 SDK
```

> **注意：** 各 agent 的 CLI 需要单独安装（如 `claude`、`codex`、`gemini`、`opencode`）。

## 快速开始

### 运行任务

```python
import asyncio
from agentabi import Session

async def main():
    session = Session(agent="claude_code")
    result = await session.run(prompt="Fix the bug in auth.py")
    print(result["status"])       # "success"
    print(result["result_text"])  # agent 的回复

asyncio.run(main())
```

### 流式事件

```python
async for event in session.stream(prompt="Explain this code"):
    if event["type"] == "message_delta":
        print(event["text"], end="")
```

### 同步便捷接口

```python
from agentabi import run_sync

result = run_sync(prompt="List Python files", agent="codex")
```

### 发现可用 agent

```python
from agentabi import detect_agents, get_agent_capabilities

agents = detect_agents()          # ["claude_code", "codex", ...]
caps = get_agent_capabilities("claude_code")
print(caps["supports_streaming"]) # True
```

## 使用场景

- **Fleet 管理** — 多个编程 agent 的统一入口
- **Agent 间调用** — 跨 agent 互操作的翻译层
- **基准测试** — 同一任务分发给多个 agent，对比结果质量、速度、成本
- **容错与路由** — 自动 failover 和基于成本的智能路由
- **中间件管道** — 注入日志、计量、安全扫描、审计追踪
- **CI/CD 集成** — 无供应商锁定的 agent 流水线集成

## 生态系统

`agentabi` 是分层架构栈的一部分：

```
agentabi  →  Agent CLI 统一接口    →  类似操作系统的 ABI
llmir     →  LLM API 格式转换      →  类似编译器的 IR
```

- [llmir](https://github.com/Oaklight/llmir) — LLM 中间表示，用于在不同 LLM 提供商 API 格式之间转换（OpenAI、Anthropic、Google）

## 许可证

[MIT](LICENSE)
