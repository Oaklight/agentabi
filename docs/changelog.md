# 更新日志

## [未发布]

### 新增

- **统一 settings** — 一个 `settings` 映射为所有传输提供模型端点与认证：`base_url`、`api_key`、`api_key_env`、`model_provider`、`headers`、`env`、`args`，以及 CLI 专属的 `native` 块。`Session(settings=...)` 设置默认值，单次 settings 合并覆盖。优先级为 settings、`native`、任务 env。不支持的字段抛出 `UnsupportedSettingsError`（或设 `on_unsupported="ignore"` 丢弃），错误消息不会重复密钥。密钥走子进程环境或 `0600` 文件，不进入 argv。`AgentCapabilities` 新增 `supported_settings` 与 `settings_mechanism`。已在 claude、codex、agy、opencode V1、pi 上用同一端点验证。相关实现移入 `agentabi.settings`；只用 `env` 的调用方行为不变。(#89, PR #90, #91, #92)
- **Codex 思考等级** — `thinking_level` 现在以 `-c model_reasoning_effort="<level>"` 传给 codex，`codex exec` 与 `codex exec resume` 都适用；`max` 映射为 `high`，`off` 不传。`supports_thinking` 声明为 `True`。codex 不输出推理文本，因此这是控制项，不是 `reasoning_delta` 流。已在 codex 0.162.1 上验证。(#86)
- **ACP 网关** — `agentabi acp --agent <name>` 通过 stdio 把一个 agent 暴露为 ACP 端点。`opencode` 使用自带的 ACP 服务器；`agy`、`claude`、`codex`、`pi` 走 IR 流。ACP 与 JSON-RPC 已 vendor 自 zerodep，无需外部 ACP SDK 或 Node 运行时。(#44, #47)
- **ACP v2 与外部 ACP 服务器** — `initialize` 协商 ACP v2，`session/prompt` 返回后端真实的 `stopReason`。`--agent acp --acp-command "<cmd>"` 可转发到任意外部 ACP 服务器，并原样传递其更新。(#48, #50)
- **路由模式** — `--router` 配合重复的 `--agent`，一个网关进程服务多个 agent。客户端在 `session/new` 中用 `_meta.agent` 按会话选择；未知名称返回 `-32602` 并列出已启用的 agent。`initialize` 只声明可达后端能力的交集。权限默认拒绝，每次路由调用都记录到 `agentabi.acp.audit` 日志。(#64, PR #68)
- **Claude Code TUI 传输** — 通过 `--prefer pty` 启用。在 PTY 上运行交互式 Claude CLI 并读取会话 transcript。Anthropic 把 `-p` 归类为 `sdk-cli`，计入额度约为交互式 `cli` 的两倍，因此订阅账号应使用此传输。第一阶段按整条消息输出，不做 token 增量。(#49)
- **OpenCode V2 提供者** — `OpenCodeV2Provider` 使用 V2 HTTP/SSE API：服务器启动与复用、会话创建与恢复、模型切换、流式回合状态机。传输选择由 `agent_extensions.opencode.transport` 或 `AGENTABI_OPENCODE_TRANSPORT`（`auto`、`v1`、`v2`）决定；原生提供者仍留在链中作为回退。V2 服务器不发送 `session.idle`，回合在 `session.next.step.ended` 结束，除非该步以 `tool-calls` 结束。(#62, PR #67)
- **TestPyPI 发布** — `.github/workflows/test-release.yml` 通过可信发布把手工构建上传到 TestPyPI。两个 README 和文档首页已添加徽章。
- **每周 zerodep 检查** — 定时任务运行 `zerodep outdated`，更新 vendor 的 `acp`/`jsonrpc`/`ptyx`，落后于上游时自动开 PR。
- **`tools/peerctl.py`** — Claude Code 后台 peer 的原型工具：spawn、按 workspace 与 name 列出、stop，以及按 `sessionId` 导出/导入交接快照。

### 修复

- **提供者 CLI 变化** — codex 先探测 `codex exec --help`，使用当前 CLI 接受的审批参数；opencode 使用 `--auto`；agy 每个 `agent_response` 步输出一条消息，并在 `ACTIVE` 与 `DONE` 更新之间累计文本。`turn.failed` 与 `error` 事件现在转为致命 `ErrorEvent`，不再被丢弃。codex 还会传 `--skip-git-repo-check`，因此非 git 目录也可用。
- **提供者 stdin** — 子进程的 stdin 改为 `/dev/null`。在网关下宿主 stdin 是 JSON-RPC 管道，pi 会从其中读走 ACP 消息。
- **Codex 临时 schema 泄漏** — 生成的 `--output-schema` 文件在流结束后删除；类路径的 schema 值按文件路径处理。(#51, PR #52)
- **Claude TUI 错误路径** — API 错误与内部失败以 `ErrorEvent` 呈现，不再静默结束。含控制字符的提示词在任何字节写入前被拒绝；粘贴与提交在回合开始前都会确认。(#55, #58)
- **Claude TUI transcript 与启动检测** — 截断或 inode 重写后重置读取位置，不越过未完成行，并能恢复被中断写入粘在前缀上的行。usage 跨消息累加，`cost_usd` 取自 result 行。提示符按输入框上下边框识别，因此占位提示（`❯ Try "..."`）会被接受，菜单项不会。trust 或登录失败时回退到 native 或 SDK 传输，并发出非致命 `ErrorEvent`。已在 Claude Code 2.1.299 上验证。(#59, PR #72)
- **Claude TUI 对话框处理** — 传输层按 `agent_extensions.tui_answers` 检测并回答 `AskUserQuestion`、权限、计划、通知和安全确认对话框。以状态栏作为帧边界，以 transcript 中的工具调用作为闩锁，过期帧不会重发按键。未配置或无法解析的对话框以 `tui_dialog_unanswered` 结束回合，而不是等待；安全确认永不自动接受。已在 Claude Code 2.1.299 上验证 AskUserQuestion。(#57, PR #73)
- **ACP 权限转发** — 后端可以向 ACP 客户端请求权限并取得答复。Claude 走 stdin 上的 SDK 控制协议（`--permission-prompt-tool stdio`），其他提供者保持自身策略。网关新增 `permission_mode` 配置项（可询问的后端提供 `ask`/`deny`/`allow_all`；没有权限通道的后端不提供该选项），在 `session/cancel` 时解决挂起请求，并把 `allow_always` 实现为会话内缓存，因为实测 Claude 的 `updatedPermissions` 不会抑制下一次提示。`agentabi acp` 新增 `--permission-timeout` 与 `--permission-policy`。已在 Claude Code 2.1.299 上端到端验证：allow 执行工具，reject 与超时拒绝，cancel 返回 `cancelled`。(#63, PR #74)
- **ACP 静态权限策略** — 无法往返权限请求的后端仍会执行 `deny` 与 `allow_all`。网关把策略传给提供者：codex 以 `-s read-only` 加 `approval_policy="never"` 运行，agy 去掉跳过权限参数后 print 模式自动拒绝；`allow_all` 映射为 `full_auto`。`pi` 没有这类参数，因此不提供 `permission_mode`。已在线验证：codex 在 `deny` 下无法创建文件。(#76, PR #77)
- **Codex app-server 后端** — `agentabi acp --agent codex-app-server` 通过 stdio JSON-RPC 驱动 `codex app-server`，把 `item/commandExecution/requestApproval` 与 `item/fileChange/requestApproval` 经 ACP 客户端往返。`availableDecisions` 为准；未列出的决定绝不会作为 allow 发送。取消挂起审批时发送 `decision: cancel`，因为单独 `turn/interrupt` 会挂住。已在 codex 0.162.1 上验证：allow 创建文件，reject 取消回合。(#70, PR #78)
- **agy 工具事件** — agy 1.5 用 `step_type: "tool"` 报告工具调用（ACTIVE，随后 DONE 或 ERROR，参数在 `tool_info.parameters` 下），而提供者只识别旧的 `tool_use` 形状。每个 agy 工具调用都被丢弃，调用方拿不到 `tool_use` 或 `tool_result`。现在 `tool` 步按 `step_index` 发一次 `tool_use`，在 DONE 时发 `tool_result`，在 ERROR 时发失败的 `tool_result`。已在 agy 1.5.5 上验证。(#81)
- **agy 网关模型与拒绝结果** — `agent_extensions.agy.settings` 把设置对象（或文件路径）传给 `--settings`；在那里指定的模型不再同时传给 `--model`，因为 agy 会用自身目录校验 `--model` 并让它优先。headless 自动拒绝的工具调用现在返回带 agy `denied_actions` 的失败 `tool_result`，不再是空的成功结果。`supports_ephemeral` 与 `supports_file_attachments` 依实测声明为 `False`。已通过 llm-rosetta 网关在 agy 1.5.5 上验证。(#83)
- **pi 会话结束** — pi 只在 `agent_end` 事件发 `session_end`，因此会话开始后中途失败的运行（认证失败、被 kill、模型无法解析）不会发结束事件，流一直开着。现在在错误事件之后补发缺失的 `session_end`。已用同一失败形态在修复前后对照验证。(#88)
- **能力声明** — opencode V1、opencode V2、pi、agy 四个提供者的 `supports_file_diffs` 现在声明为 `False`，它们都不发 `file_diff` 事件；声明为未知会让调用方等待一个不可能到达的事件。codex 保持未声明，因为测试中无法触发它的 `file_change` 路径。(#87)
- **OpenCode V1 生命周期** — 工具循环每个模型步发一次 `step_start`，提供者因此每次运行发出多个 `session_start`；现在只在第一步开始会话。没有 `reason=stop` 就结束的运行（上游流中断、被取消）从不发 `session_end`，调用方会一直等待；现在流结束时会补发。没有会话 id 的 `resume` 映射为 `--continue`。`supports_multi_turn` 改为 `True`（恢复保持上下文），CLI 没有对应参数的能力声明为 `False`。已在 opencode 1.19.0 上验证。(#84)
- **OpenCode V2 模型目录** — V2 API 用自身目录解析会话模型，目录里没有的模型会在服务器内部失败且事件流上没有任何事件，于是回合一直等到超时。提供者现在先短暂轮询 `GET /api/model`，`auto` 模式下目录缺该模型时回退到 V1，显式 `v2` 模式下直接报告目录错误。实测 opencode 1.19.0：通过 `OPENCODE_CONFIG` 添加的模型在 V1 可用，但永远不会进入 V2 目录。(#85)
- **Codex 原生会话恢复** — `CodexNativeProvider` 现在把带 `session_id` 与 `resume` 的任务交给 `codex exec resume`，CLI 因而复用已存线程，不再新建。该子命令不接受 `-s`、`-C`、`--add-dir`、`--approve-for-me` 与 `--full-auto`，因此沙箱与审批策略改走 `-c sandbox_mode` / `-c approval_policy`，额外目录改走 `-c sandbox_workspace_write.writable_roots`，图片改走 `-i`。没有会话 id 的恢复使用 `--last`。已在 codex 0.162.1 上验证：恢复运行保持同一线程 id 与上下文，只读恢复无法写文件。(#80)
- **统一 IR stop reason** — 每个提供者把各自 CLI 的停止原因映射到同一组值（`end_turn`、`max_tokens`、`max_turn_requests`、`refusal`、`cancelled`、`error`），`message_end` 总是带规范值，而不再是 `stop`、`tool-calls` 或空串。Claude 每次运行只发一个 `session_start`（`system/status` 行不再像第二个会话），codex 与 pi 的 `session_end` 现在带会话 id。已在真实 CLI 上跨全部传输验证。(PR #79)
- **ptyx** — Windows ConPTY 数据块不再丢失；管道失败抛出 `PtyReadError`/`PtyWriteError`；只回应光标位置查询。(#56)

## v0.4.0 (2026-09-08)

### 新增

- **Antigravity CLI (agy) 提供者** — Google 的新 AI 编程助手 CLI，替代已停止维护的 Gemini CLI
- `ThinkingLevel` 类型和 `thinking_level` 字段 — 跨 Claude（`--effort`）、Pi（`--thinking`）、OpenCode（`--variant`）的统一推理控制
- `output_schema` 字段 — 结构化输出支持（Claude `--json-schema`、Codex `--output-schema`、agy `--json-schema`）
- `ephemeral` 字段 — 不持久化会话（Claude `--no-session-persistence`、Codex `--ephemeral`、Pi `--no-session`）
- `additional_dirs` 字段 — 多目录工作空间（Claude/Codex/agy `--add-dir`）
- `files` 字段 — 文件/图片附件（Claude `--file`、Codex `--image`、OpenCode `--file`）
- `ReasoningDeltaEvent` — 思考/推理文本流式事件（对齐 llm-rosetta）
- `ContentBlockStartEvent` / `ContentBlockEndEvent` — 内容块边界事件（对齐 llm-rosetta）
- `reasoning_tokens` 字段 — UsageInfo 中的推理 token 统计
- `reasoning_text` 字段 — SessionResult 中的推理文本

### 修复

- 修正 `session_id` 映射 — 区分 `--session-id`（创建）、`--session`（恢复）、`--continue`（最近会话）
- 修正 Pi 的 `supports_mcp` 能力声明（Pi 通过 `--extension` 支持 MCP）
- 添加 `"pi"` 到 `AgentType` 字面量类型

### 废弃

- Gemini CLI 提供者已标记为废弃（Gemini CLI 于 2026 年 6 月停止维护），请使用 agy 替代

## v0.3.0 (2026-07-04)

第五个 agent 支持（Pi）和文档改进。

### 功能

- **Pi coding agent** — 新增 `PiNativeProvider`，支持 [Pi coding agent](https://pi.dev/) CLI。支持流式输出、系统提示词、会话恢复和工具过滤（`--tools`/`--exclude-tools`）。Thinking 事件暂时跳过（IR 尚无对应类型）。(#14)

### 文档

- 新增中间件使用指南（`docs/usage/middleware.md`），包含内置和自定义中间件示例
- 新增 `examples/middleware.py`，演示 `LoggingMiddleware`、`UsageMeterMiddleware` 和 `TimeoutMiddleware`
- Providers 文档新增 Pi 的 native provider 和注册表条目
- Session API 文档新增 `middleware` 构造函数参数和 `add_middleware()` 方法
- mkdocs 导航新增中间件指南

### 测试

- 265+ 个单元测试（v0.2.2 为 231 个）

## v0.2.2 (2026-07-01)

Bug 修复、中间件管道和 provider 可靠性改进。

### 功能

- **中间件管道** — `Session` 的可插拔中间件系统。支持堆叠 `LoggingMiddleware`、`UsageMeterMiddleware` 和 `TimeoutMiddleware`（或自定义中间件）来拦截和转换事件流。中间件以洋葱模型包裹 `stream()`；`run()` 自动受益。

### Bug 修复

- **Codex/OpenCode result_text** — Codex（native + SDK）和 OpenCode provider 现在能正确填充成功完成时的 `result_text`。此前 `message_end` 事件缺少 `text` 字段，导致 `run()` 即使成功也返回空的 `result_text`。(#7)
- **能力声明** — Codex native、Gemini native 和 OpenCode native 不再错误地声明 `supports_system_prompt: True`
- **Codex SDK 临时文件泄漏** — system prompt 临时文件现在在 `try/finally` 块中清理

### 改进

- **stderr 捕获** — 所有 4 个 native provider 现在在子进程退出后读取 stderr，非空时发送 `ErrorEvent`
- **退出码检查** — 非零子进程退出码现在会产生 `ErrorEvent`
- **超时强制执行** — Native provider 遵守 `TaskConfig.timeout`，超时后杀死进程并发送致命错误

### 文档

- 生态系统引用中的 `llmir` 更名为 `llm-rosetta`

### 测试

- 231+ 个单元测试（v0.2.1 为 166 个）

## v0.2.1 (2026-06-25)

补丁版本，修复自定义端点的代理工作流支持。

### Bug 修复

- **Provider base URL 覆盖** — 当 agent 通过自定义代理端点（如 llm-rosetta gateway）连接时，通过 `OPENAI_BASE_URL` 或 `ANTHROPIC_BASE_URL` 等环境变量指定的 base URL 现在能正确覆盖 provider 默认值。此前环境变量中指定的端点会被忽略。

### CI 与工具

- 切换到 pre-commit 进行 lint 检查

### 文档

- 移除 README 中的 DeepWiki 徽章
- README 新增 PyPI、release、CI 和 license 徽章

## v0.2.0 (2026-04-26)

Native 优先的 provider 架构，CLI 对齐的权限模式。

### Bug 修复

- **OpenCode**：移除错误的 `--prompt` 标志映射 — `opencode run` 不支持此参数；系统提示词现记录为该 provider 不支持的功能
- **Claude**：`full_auto` 权限从 `--dangerously-skip-permissions` 改为 `--permission-mode bypassPermissions`，与现代 CLI 接口一致

### 功能

- **CodexNativeProvider** — Codex CLI 的子进程 provider（`codex exec --json --full-auto`），解析 JSONL 事件为 IR
- **`prefer` 参数** — `Session(prefer="sdk")` 或 `get_provider(agent, prefer="sdk")` 显式选择 native 或 SDK provider
- **所有 agent 均 native 优先** — 4 个 agent 全部支持 native 子进程 provider 作为默认，SDK 作为可选回退
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

- 147 个单元测试（相比 v0.1.0 新增 28 个：CodexNativeProvider、权限模式映射）
- Native vs SDK 对比集成测试 — 跨所有双 provider agent 参数化运行，验证 IR 事件一致性
- `native_vs_sdk` pytest marker 用于定向测试
- 所有现有测试已更新以匹配新 CLI 标志行为

### Provider 变更

- `codex` provider 链更新：`[CodexNativeProvider, CodexSDKProvider]`（原为 `[CodexSDKProvider]`）
- `CodexSDKProvider` 现在发送 `session_end` 事件，与 native provider 保持生命周期一致

### 测试的 CLI 版本

| 工具 | 版本 |
|------|------|
| Claude Code | 2.1.87 |
| Codex CLI | 0.117.0 |
| Gemini CLI | 0.35.3 |
| OpenCode | 1.4.3 |

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
