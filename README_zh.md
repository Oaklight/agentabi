# agentabi

[![PyPI version](https://img.shields.io/pypi/v/agentabi?color=green)](https://pypi.org/project/agentabi/)
[![GitHub release](https://img.shields.io/github/v/release/Oaklight/agentabi?color=green)](https://github.com/Oaklight/agentabi/releases/latest)
[![CI](https://github.com/Oaklight/agentabi/actions/workflows/ci.yml/badge.svg)](https://github.com/Oaklight/agentabi/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)

AI 编程助手 CLI 的统一接口层。

一套接口，任意编程助手。

## 什么是 agentabi？

`agentabi` 为各种 AI 编程助手 CLI 提供稳定、统一的接口（类似"ABI"）。编写一次集成代码，通过配置切换不同的编程助手。

### 支持的编程助手

| 助手 | 供应商 | 状态 |
|------|--------|------|
| [Claude Code](https://github.com/anthropics/claude-code) | Anthropic | 已实现 |
| [Codex](https://github.com/openai/codex) | OpenAI | 已实现 |
| [Antigravity (agy)](https://github.com/google/anthropic-agy) | Google | 已实现 |
| [OpenCode](https://github.com/opencode-ai/opencode) | 社区 | 已实现 |
| [Pi](https://github.com/anthropics/pi) | 社区 | 已实现 |
| ~~Gemini CLI~~ | Google | 已废弃（请使用 agy） |

## 安装

```bash
pip install agentabi
```

> **注意：** 还需要安装至少一个编程助手 CLI：`claude`、`codex`、`agy`、`opencode` 或 `pi`。

### 可选 SDK 依赖

```bash
pip install agentabi[claude]    # Claude Agent SDK
pip install agentabi[codex]     # Codex SDK
```

## 快速开始

```python
import asyncio
from agentabi import Session

async def main():
    session = Session(agent="claude_code")
    result = await session.run(prompt="What is 2+2?")
    print(result["result_text"])

asyncio.run(main())
```

## 核心特性

### 统一的 TaskConfig

所有编程助手接受相同的 `TaskConfig`，包含以下字段：

- **助手选择**：`agent`、`model`
- **推理控制**：`thinking_level`（`"off"`、`"low"`、`"medium"`、`"high"`、`"max"`）
- **结构化输出**：`output_schema`（JSON Schema）
- **会话管理**：`session_id`、`resume`、`ephemeral`
- **工作空间**：`working_dir`、`additional_dirs`、`files`
- **权限控制**：`permissions`、`allowed_tools`、`disallowed_tools`
- **系统提示**：`system_prompt`、`append_system_prompt`

### 统一的 IR 事件

所有编程助手发出相同的流式事件：

- `session_start` / `session_end` — 会话生命周期
- `message_start` / `message_delta` / `message_end` — 文本流式输出
- `content_block_start` / `content_block_end` — 内容块边界
- `reasoning_delta` — 思考/推理文本（Claude、Pi）
- `tool_use` / `tool_result` — 工具调用
- `usage` — token 使用统计（包含 `reasoning_tokens`）
- `error` — 错误报告

## 文档

- [English docs](https://agentabi-en.readthedocs.io/)
- [中文文档](https://agentabi-zh.readthedocs.io/)

## 许可证

MIT
