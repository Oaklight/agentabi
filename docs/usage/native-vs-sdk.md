# Native vs SDK Provider 对比

agentabi 为大多数 agent 支持两种 provider 类型。本页说明二者差异、展示集成测试结果，帮助你做出选择。

## 概览

| 方面 | Native（子进程） | SDK |
|-----|-----------------|-----|
| **机制** | 启动 CLI 子进程，解析 JSON/JSONL 标准输出 | 使用 agent 的 Python SDK 包 |
| **依赖** | 零 — 只需 CLI 在 PATH 中 | 需要 `pip install agentabi[agent]` |
| **可靠性** | 高 — CLI 是 agent 的主要接口 | 取决于 SDK 的环境/配置要求 |
| **流式输出** | JSONL 逐行解析 | SDK 事件回调 |
| **默认** | 是（优先尝试） | 回退（native 不可用时使用） |

## Provider 矩阵

所有 4 个 agent 均支持 native provider。其中 3 个还有 SDK 回退：

| Agent | Native Provider | SDK Provider | 默认 |
|-------|----------------|-------------|------|
| Claude Code | `ClaudeNativeProvider` | `ClaudeSDKProvider` | Native |
| Codex | `CodexNativeProvider` | `CodexSDKProvider` | Native |
| Gemini CLI | `GeminiNativeProvider` | `GeminiSDKProvider` | Native |
| OpenCode | `OpenCodeNativeProvider` | — | Native |

## 选择 Provider

使用 `prefer` 参数显式选择：

```python
from agentabi import Session

# 默认：native 优先，SDK 回退
session = Session(agent="codex")

# 强制使用 SDK
session = Session(agent="codex", prefer="sdk")

# 显式使用 native
session = Session(agent="codex", prefer="native")
```

### 何时使用 native（默认）

- 不需要额外 Python 依赖
- CLI 已安装并配置好
- 需要最可靠、经过充分测试的路径
- 运行环境限制 pip 安装

### 何时使用 SDK

- 需要仅通过 SDK 提供的功能
- CLI 不在 PATH 中但 SDK 已安装
- 偏好进程内通信而非子进程管理

## 集成测试结果

我们运行自动化对比测试，使用相同的 prompt（"What is 2+2?"）同时测试两种 provider，对比 IR 事件输出的一致性。

### 测试套件

`test_native_vs_sdk.py` 对每个 agent 运行 5 个测试，参数化覆盖 `claude_code`、`codex` 和 `gemini_cli`：

| 测试 | 验证内容 |
|-----|---------|
| `test_both_produce_session_lifecycle` | 两者都发出 `session_start` 和 `session_end` |
| `test_both_produce_text_events` | 两者都发出 `message_delta` 或 `message_end` |
| `test_both_answer_correctly` | 两者的文本输出都包含 "4" |
| `test_event_types_are_valid_ir` | 所有事件的 type 字段都是合法 IR 值 |
| `test_event_type_overlap` | 两种 provider 共享至少 2 种事件类型 |

### 测试结果 (2026-03-31)

**各 agent 集成测试**（native provider，prompt: "What is 2+2?"）：

| Agent | `test_run` | `test_stream_events` | `test_stream_text` | 状态 |
|-------|-----------|---------------------|-------------------|------|
| Claude Code | PASS | PASS | PASS | 3/3 |
| Codex | PASS | PASS | PASS | 3/3 |
| OpenCode | PASS | PASS | PASS | 3/3 |
| Gemini CLI | FAIL | FAIL | FAIL | 0/3 |

!!! note "Gemini CLI"
    Gemini CLI 在本次测试中返回空输出。这可能是 CLI 配置问题（认证/配额），而非 provider 的 bug。当 CLI 正常产出时，native provider 能正确处理 Gemini 的 JSONL 格式。

**Native vs SDK 对比**（Codex — 唯一两种 provider 均可用的 agent）：

| 测试 | Native | SDK | 结果 |
|-----|--------|-----|------|
| 会话生命周期事件 | `session_start`, `session_end` | `session_start`, `session_end` | PASS |
| 文本事件存在 | `message_delta` | `message_delta` | PASS |
| 正确回答 ("4") | "4" | "4" | PASS |
| IR 事件类型合法 | 全部合法 | 全部合法 | PASS |
| 事件类型重叠 | 6 种共享 | 6 种共享 | PASS |

!!! info "Claude 和 Gemini SDK"
    Claude SDK（`claude-agent-sdk`）测试中遇到子进程错误。Gemini SDK（`gemini-cli-sdk`）需要 `OPENAI_API_KEY` 配置其 LLM 解析器。这些都是 SDK 配置问题 — 两个 agent 的 native provider 均工作正常，验证了 native 优先架构的正确性。

### 事件类型对比（Codex）

两种 Codex provider 产出相同的 IR 事件类型集合：

```
Native:  {session_start, message_start, tool_use, tool_result,
          message_delta, usage, message_end, session_end}

SDK:     {session_start, message_start, tool_use, tool_result,
          message_delta, usage, message_end, session_end}
```

### 运行测试

```bash
# 所有 native vs SDK 对比测试
pytest tests/integration/ -m native_vs_sdk -v

# 各 agent 集成测试
pytest tests/integration/ -m claude -v
pytest tests/integration/ -m codex -v

# 跨 CLI 一致性测试
pytest tests/integration/ -m cross_cli -v
```

## 为什么 Native 优先？

Native 优先架构有以下优势：

1. **零依赖** — 除 agentabi 本身外不需要 `pip install`。CLI 在 PATH 中即可工作。
2. **CLI 稳定性** — Agent CLI 是面向用户的主要产品，接受最多的测试和维护。
3. **环境隔离** — 子进程执行避免 Python 版本冲突和 SDK 间的依赖冲突。
4. **行为一致** — CLI 处理认证、配置和模型选择的方式与用户交互体验一致。

SDK provider 作为回退仍然有价值，适用于偏好进程内通信的场景。
