# ACP 网关

agentabi 可以把任意受支持的 agent 暴露为 [Agent Client Protocol](https://agentclientprotocol.com/)（ACP）端点。客户端通过 stdin/stdout 说 ACP，agentabi 在内部说每个 CLI 的原生接口。不需要外部 ACP 适配器，也不需要 Node 运行时。

## 运行单个 agent

```bash
agentabi acp --agent opencode
```

| Agent | agentabi 的驱动方式 |
| --- | --- |
| `opencode` | 使用 CLI 自带的 ACP 服务器，直接透传 |
| `agy` | agentabi IR 流（`agy -p --output-format stream-json`） |
| `claude` | agentabi IR 流（`claude --print --output-format stream-json`） |
| `codex` | agentabi IR 流（`codex exec --json`） |
| `codex-app-server` | JSON-RPC 驱动 `codex app-server`，支持权限往返 |
| `pi` | agentabi IR 流（`pi --mode rpc`） |

`agy`、`claude`、`codex`、`pi` 支持 `--prefer`：

- `--prefer native`（默认）使用子进程提供者。
- `--prefer sdk` 在已安装时优先使用 SDK 提供者。
- `--prefer pty` 使用交互式 Claude Code TUI 传输。它更慢，且按整条消息输出，但计入交互式订阅额度，而不是 `sdk-cli` 额度。

## 使用外部 ACP 服务器

任何自带 ACP 服务器的 CLI 都可以在没有 agentabi 提供者的情况下使用：

```bash
agentabi acp --agent acp --acp-command "npx -y @agentclientprotocol/claude-agent-acp"
```

网关与该进程说 ACP，并把它的 `session/update` 载荷原样转发给客户端。

## 服务多个 agent

`--router` 配合重复的 `--agent` 用一个网关进程服务多个 agent：

```bash
agentabi acp --router --agent claude --agent opencode
```

客户端在 `session/new` 中用 `_meta.agent` 按会话选择：

```json
{"jsonrpc": "2.0", "id": 1, "method": "session/new",
 "params": {"cwd": "/work", "mcpServers": [],
            "_meta": {"agent": "opencode"}}}
```

- `_meta.agent` 缺省时使用第一个启用的 agent。
- 未知名称返回 `-32602`，并列出已启用的 agent。
- 选中的 agent 会作为 `agent` 配置项出现在会话配置中。该配置项仅作信息展示：ACP 配置项随 `session/new` 返回、之后才能设置，因此无法在创建时选择 agent。对它调用 `session/set_config_option` 会返回错误；切换 agent 请新建会话。
- 每个 agent 一个后端实例，由它的会话共享，最后一个会话关闭时停止；新建会话会重新启动。

## 能力策略

`initialize` 声明所有已启用且可达后端能力的交集，并进一步受网关自身实现的限制。agent 在 `initialize` 之后才选择，因此无法在那里如实声明每个 agent 的能力。

- 永不声明 `session/load`、`session/list`、`session/resume`、`session/delete`。
- 不代理认证。
- 无法启动的后端会被记录并从交集中排除；之后选择它会返回明确的错误。
- 协商出的协议版本是所有可达后端报告的最低版本。

## 权限策略

默认拒绝。每个会话都有 `permission_mode` 配置项：

| 值 | 行为 |
| --- | --- |
| `ask` | 把每个请求转发给客户端并等待答复。后端可询问时这是默认值。 |
| `deny` | 不联系客户端，直接拒绝每个请求。 |
| `allow_all` | 允许每个请求。只能通过显式选择启用。 |

- Claude 通过 SDK 控制协议询问。回答 `allow_once` 会执行工具；`reject_once` 不执行，回合继续。
- `allow_always` 与 `reject_always` 由网关按会话缓存。不使用 Claude 自身的 `updatedPermissions`：它会写入 `.claude/settings.local.json`，且实测不会抑制下一次提示。
- 没有权限通道的后端（`codex`、`agy`）仍提供 `deny` 与 `allow_all`，网关用提供者自身的参数强制执行：codex 以 `-s read-only` 加 `approval_policy="never"` 运行，agy 去掉跳过权限的参数后 print 模式自动拒绝。`pi` 没有这类参数，因此不提供该选项。ACP 直通后端（`opencode`、`--agent acp`）可以用子进程的选项 id 发起询问。
- `--permission-timeout N` 在客户端 N 秒内不答复时拒绝请求；默认 `0` 表示一直等待。
- 请求挂起时 `session/cancel` 会将其解决为取消并结束回合。

客户端用收到的选项 id 回答 `session/request_permission`：

```json
{"outcome": {"outcome": "selected", "optionId": "allow_once"}}
{"outcome": {"outcome": "cancelled"}}
```

## 审计

每次路由调用都以方法、agent、会话、耗时和结果记录到 `agentabi.acp.audit` 日志。内存中保留最近 200 条记录。

## 客户端示例

网关说标准 ACP，任何 ACP 客户端库都可用。最小交互如下：

```json
{"jsonrpc": "2.0", "id": 1, "method": "initialize",
 "params": {"protocolVersion": 1, "clientCapabilities": {}}}
{"jsonrpc": "2.0", "id": 2, "method": "session/new",
 "params": {"cwd": "/work", "mcpServers": []}}
{"jsonrpc": "2.0", "id": 3, "method": "session/prompt",
 "params": {"sessionId": "...", "prompt": [{"type": "text", "text": "What is 2+2?"}]}}
```

更新以 `session/update` 通知到达，回合结束时 `session/prompt` 返回。

## 限制

- 网关给每条更新都带 `sessionId`，但 vendor 的 `ACPClient.prompt()` 只有一个更新队列。通过同一个客户端对象并发提示时，需要在客户端侧按会话路由。
- 子 agent、终端和结构化 diff 不透传；这些特性的适配保真度有损。
- `agentabi acp --agent acp` 需要外部服务器的 `--acp-command`；其他 agent 会忽略该参数。
