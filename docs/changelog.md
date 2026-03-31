# 更新日志

## v0.2.0 (2026-03-31)

Native 优先的 provider 架构：所有 agent 默认使用子进程 provider，SDK 作为回退。

### 功能

- **CodexNativeProvider** — Codex CLI 的子进程 provider（`codex exec --json --full-auto`），解析 JSONL 事件为 IR
- **`prefer` 参数** — `Session(prefer="sdk")` 或 `get_provider(agent, prefer="sdk")` 显式选择 native 或 SDK provider
- **所有 agent 均 native 优先** — 4 个 agent 全部支持 native 子进程 provider 作为默认，SDK 作为可选回退

### 测试

- 142 个单元测试（+23 个 CodexNativeProvider 测试）
- Native vs SDK 对比集成测试 — 跨所有双 provider agent 参数化运行，验证 IR 事件一致性
- `native_vs_sdk` pytest marker 用于定向测试

### Provider 变更

- `codex` provider 链更新：`[CodexNativeProvider, CodexSDKProvider]`（原为 `[CodexSDKProvider]`）
- `CodexSDKProvider` 现在发送 `session_end` 事件，与 native provider 保持生命周期一致

## v0.1.0 (2026-03-31)

首个发布版本，提供 4 个 coding agent CLI 的统一 provider 架构。

### 功能

- **Session API** — 异步优先的 `Session` 类，提供 `run()` 和 `stream()` 方法，以及 `run_sync()` 同步便捷封装
- **Agent 自动检测** — `detect_agents()` 发现已安装 CLI，`get_agent_capabilities()` 查看功能
- **Provider 系统** — 基于协议的 provider 架构，支持回退链
- **IR 事件流** — 12 种事件类型，跨所有 agent 归一化（session、message、tool、usage、error、file_diff、permissions）

### Providers

- **ClaudeNativeProvider** — Claude Code CLI 的子进程 provider（`claude -p --output-format stream-json`）
- **ClaudeSDKProvider** — 使用 `claude-agent-sdk` 的 SDK provider
- **CodexSDKProvider** — 使用 `codex-sdk-python` 的 SDK provider
- **GeminiNativeProvider** — Gemini CLI 的子进程 provider（`gemini -o stream-json -y -p`）
- **GeminiSDKProvider** — 使用 `gemini-cli-sdk` 的 SDK provider（回退）
- **OpenCodeNativeProvider** — OpenCode CLI 的子进程 provider（`opencode run --format json`）

### 测试

- 119 个单元测试覆盖所有 provider、IR 类型、session 和 registry
- 16 个集成测试跨所有 4 个 CLI（run、stream events、stream text）
- 4 个跨 CLI 一致性测试验证统一 IR 输出

### 示例

- `examples/quickstart.py` — 发现、运行和结果展示
- `examples/streaming.py` — 实时事件流，覆盖所有事件类型
