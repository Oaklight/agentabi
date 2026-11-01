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

### Claude 订阅额度（PTY 传输）

默认情况下 Claude Code 以 `claude -p` 运行。Anthropic 把该入口标记为
`sdk-cli`，消耗的额度约为交互式 `cli` 模式的两倍。要改用真正的交互式
TUI：

```python
session = Session(agent="claude_code", prefer="pty")
```

PTY provider 需要显式启用。Linux 和 macOS 使用 POSIX PTY，Windows 使用
ConPTY；两者都来自 vendored 的 `ptyx` 模块，无需额外依赖。它会等待 TUI
提示符、粘贴消息、并跟踪会话 transcript，因此输出整条消息而非 token
增量。如果 Claude Code 要求信任目录或尚未登录，它会返回明确的错误，
而不会修改你的配置。

ACP 网关使用同一个选项：

```bash
agentabi acp --agent claude --prefer pty
```

## ACP 网关

把 agentabi 暴露为单一的 ACP 端点。网关对客户端说 ACP，对内委托给每个 CLI 的原生接口：

```bash
pip install agentabi
agentabi acp --agent opencode
```

ACP 传输层从 [zerodep](https://github.com/Oaklight/zerodep) vendor（`acp` + `jsonrpc`），不依赖官方 ACP SDK。当前 backend：`opencode`（原生 ACP）、`agy`/`claude`/`codex`/`pi`（经由 agentabi IR 流）。更多 backend 见 [ACP gateway epic](https://github.com/Oaklight/agentabi/issues/43)。

任何自带 ACP server 的 CLI 都可以不写 provider 直接接入：

```bash
agentabi acp --agent acp --acp-command "npx -y @agentclientprotocol/claude-agent-acp"
```

网关对该进程说 ACP，并把其 `session/update` 原样转发。

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
