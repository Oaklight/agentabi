# 更新日志

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
