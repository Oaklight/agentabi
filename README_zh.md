# agentabi

[![PyPI version](https://img.shields.io/pypi/v/agentabi?color=green)](https://pypi.org/project/agentabi/)
[![TestPyPI](https://img.shields.io/pypi/v/agentabi?pypiBaseUrl=https://test.pypi.org&label=TestPyPI&color=orange)](https://test.pypi.org/project/agentabi/)
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
| [OpenCode](https://github.com/opencode-ai/opencode) | 社区 | 已实现（V2 HTTP/SSE + V1 回退） |
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

## OpenCode V2 传输

OpenCode 1.19.0 及以上版本提供第二套 API：`opencode serve`，用 HTTP 命令
加 server-sent events 事件流。agentabi 默认使用它，同时保留
`opencode run --format json` 的 V1 子进程作为回退。

```python
session = Session(agent="opencode")
result = await session.run(prompt="修复失败的测试")
```

- **选择规则。** 当 `opencode` CLI 不低于 1.19.0，或已配置 server URL 时使用
  V2；否则使用 V1。可用 `AGENTABI_OPENCODE_TRANSPORT=v1|v2` 强制指定，也可按
  任务设置 `agent_extensions={"opencode": {"transport": "v2"}}`。
- **Server。** 未配置 URL 时，provider 会在本机空闲端口启动
  `opencode serve`，并在后续调用中复用该进程。设置 `OPENCODE_BASE_URL`
  （或 `agent_extensions={"opencode": {"base_url": ...}}`）可连接已运行的
  `opencode serve`，也支持 `http://user:pass@host:port` 形式的远程地址。显式
  配置的 URL 是权威的：该服务端不支持 V2 API 时任务直接报错，不会静默退回 V1。
- **会话。** 传入 `session_id` 并设置 `resume=True` 会复用服务端会话，多轮
  对话因此保留上下文。
- **权限。** `permissions={"level": "full_auto"}` 自动批准；`accept_edits`
  逐次批准。其他级别会以明确错误结束本轮，因为流式 API 无法交互询问。交互
  式问题会被拒绝并结束本轮。
- **限制。** 没有 system prompt 入口。`usage` 和 `cost_usd` 按步骤上报，带
  工具调用的轮次每一步各一对。轮次结束以 `session.next.step.ended` 为准，因为
  V2 服务端不发 `session.idle`。

`agentabi acp --agent opencode` 不受影响，仍走原生 ACP 直通。

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

### 权限

`session/new` 返回 `permission_mode` 配置项（`ask`、`deny`、`allow_all`），
可用 `session/set_config_option` 按会话修改。

Claude backend（`agentabi acp --agent claude`）与 ACP 直通 backend
（`opencode`、`--agent acp`）可以发起询问：请求以
`session/request_permission` 到达客户端，选择会写回。没有权限通道的 backend
（`codex`、`agy`、`pi`）不会得到 `permission_mode` 配置项，适用提供者自身的
策略；网关不会把缺失的通道当成允许。

`--permission-timeout SECONDS` 限制等待时间（默认 `0` 表示一直等待），
`--permission-policy ask|deny|allow_all` 设置初始策略。

### Router 模式

一个网关进程可以服务多个 agent。用 `--router` 和重复的 `--agent` 启用：

```bash
agentabi acp --router --agent claude --agent opencode
```

客户端在 `session/new` 里用 `_meta.agent` 为每个会话选择 agent：

```json
{"jsonrpc": "2.0", "id": 1, "method": "session/new",
 "params": {"cwd": "/work", "_meta": {"agent": "opencode"}}}
```

未提供 `_meta.agent` 时使用第一个启用的 agent。未知名字返回 `-32602`
（invalid params），并列出已启用的 agent。选中的 agent 会作为 `agent`
配置项出现在会话配置里。该配置项只用于展示：ACP 配置项由 `session/new`
返回、之后才能设置，因此无法在创建会话时选择 agent。用
`session/set_config_option` 修改它会返回错误；要换 agent 请新建会话。

**能力策略。** `initialize` 只广播所有已启用且可达 backend 都支持的交集，
并进一步受网关自身实现限制：`session/load`、`session/list`、
`session/resume`、`session/delete` 一律不广播，认证也不代理。启动失败的
backend 会记录日志并排除在交集之外；之后选择它会返回明确错误。因为 agent
在 `initialize` 之后才选定，所以不广播 per-agent 能力。协商的协议版本取所有
可达 backend 报告的最低版本；IR backend 被告知按该版本输出，原生 ACP
backend 会重启后重新协商到该版本。

**权限策略。** 默认拒绝。路由模式采用失败关闭：内部 backend 使用自己的会话
id，客户端并不知道，因此路由模式不声明权限往返，并以明确的 `cancelled` 回答
下游的 `session/request_permission`。需要客户端回答权限请求时，每个 agent 单独
运行一个网关（`agentabi acp --agent claude`）。

**审计。** 每个经路由的方法都会在 `agentabi.acp.audit` logger 上记录
method、agent、session、耗时和结果。

**生命周期。** 每个 agent 只有一个 backend 实例，由它的会话共享；最后一个
会话关闭时停止。新会话会重新启动它。

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
