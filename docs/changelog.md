# 更新日志

## v0.3.0 (2026-04-26)

同步所有 CLI provider 实现以匹配当前工具版本；CI 流水线对齐 llm-rosetta。

### Bug 修复

- **OpenCode**：移除错误的 `--prompt` 标志映射 — `opencode run` 不支持此参数；系统提示词现记录为该 provider 不支持的功能
- **Claude**：`full_auto` 权限从 `--dangerously-skip-permissions` 改为 `--permission-mode bypassPermissions`，与现代 CLI 接口一致

### 功能

- **Claude**：新增 `auto`、`dont_ask`、`default` 权限级别到 `--permission-mode` 映射
- **Gemini**：将硬编码的 `-y`（yolo）标志替换为基于权限配置的 `--approval-mode`（`yolo`、`auto_edit`、`plan`、`default`）
- **OpenCode**：新增 `--dangerously-skip-permissions` 支持，`supports_permissions` 设为 `True`
- **PermissionLevel**：新增 `"auto"` 和 `"dont_ask"` 权限级别

### CI 与工具

- GitHub Actions 升级到 `actions/checkout@v6` 和 `actions/setup-python@v6`
- CI lint 流水线新增 `ty check`（类型检查）
- 新增 install-smoke-test 矩阵任务（core、claude、codex 变体）
- 新增 `UP` 和 `C901` ruff lint 规则；修复所有 UP006/UP035 警告（使用内建泛型）
- 重构 `default_run()` 和 `ClaudeNativeProvider._build_command()` 以解决 C901 复杂度
- dev 依赖新增 `ty`、`build`、`twine`

### 测试

- 147 个单元测试（+5 个新权限模式映射测试）
- 所有现有测试已更新以匹配新 CLI 标志行为

### 测试的 CLI 版本

| 工具 | 版本 |
|------|------|
| Claude Code | 2.1.87 |
| Codex CLI | 0.117.0 |
| Gemini CLI | 0.35.3 |
| OpenCode | 1.4.3 |

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
