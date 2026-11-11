# Peer

`Session.run` 调用是一件事：启动 CLI、流式跑完一个回合、退出。**peer**
则是回合之间保持存活的 CLI 进程，协调者可以给它追加任务、在运行中 steer，
或中断后继续。

涉及两部分：

- `agentabi.peers` 是库。`PeerBroker` 负责派出 peer；`ClaudePeer` 持有一个
  `claude --print --input-format stream-json` 进程。
- `peerctl` 是命令行入口。它通过后台 daemon 持有 peer，因此 peer 的生命周期
  长于启动它的命令。

目前实现了两个后端：

| 后端 | 进程 | steer | 中断 |
| --- | --- | --- | --- |
| `claude`（默认） | `claude --print --input-format stream-json` | stdin 上的第二条 user 消息 | control request |
| `codex` | `codex app-server --listen stdio://` | `turn/steer` | `turn/interrupt` |
| `opencode` | `opencode serve`（V2 HTTP/SSE） | 带 `delivery: "steer"` 的第二条 prompt | `/api/session/{id}/interrupt` |
| `pi` | `pi --mode rpc`（NDJSON） | `{"type":"steer"}` | `{"type":"abort"}` |

两种后端的 peer API 与 `peerctl` 用法一致，差异见下文“后端”一节。

## 库

```python
import asyncio
from agentabi.peers import PeerBroker

async def main():
    async with PeerBroker() as broker:
        peer = await broker.spawn(
            "reviewer",
            cwd="/repo",
            prompt="Review the diff of the current branch",
        )
        turn = await peer.wait(timeout=300)
        print(turn.status, turn.text)

        await peer.send("Now check the tests for the same change")
        turn = await peer.wait(timeout=300)
        print(turn.text)

asyncio.run(main())
```

peer 空闲时 `send` 开启新回合；回合运行中 `send` 则是 steer：当前工具调用
一结束，Claude 就读到这条消息，回合带着新指令继续。`SendResult.mode` 说明
属于哪种情况（`"turn"` 或 `"steer"`）。

```python
result = await peer.send("Stop and explain the plan first")
assert result.mode in ("turn", "steer")

await peer.steer("Only list files, do not edit", interrupt=False)
await peer.interrupt()          # 结束当前回合，进程仍存活
notes = peer.inbox()            # 回合结果、进程退出、被拒绝的权限
await peer.close()
```

`wait()` 返回 `PeerTurn`：

| 字段 | 含义 |
| --- | --- |
| `turn_id` | `"<name>:<n>"`，按回合递增 |
| `status` | `success`、`error` 或 `cancelled` |
| `text` | 本回合的助手文本 |
| `usage` | `input_tokens`、`output_tokens`、`cache_read_tokens`、`cache_creation_tokens`、`total_tokens` |
| `num_turns` | Claude 自己的回合计数；发生 steer 时递增 |
| `stop_reason` | CLI 的停止原因，例如 `success`、`error_during_execution` |
| `errors` | CLI result 行里的诊断信息 |

`PeerBroker` 选项（默认后端为 `claude`；Codex 传
`backend="codex"`）：


- `spawn(name, cwd, prompt=None, backend="claude", **options)`——
  `options` 接受 `model`、`permission_mode`、`agents`、`system_prompt`、
  `settings`、`env`、`add_dirs`、`command`、`session_id`、`resume`、
  `extra_args`；Codex 后端另外接受 `sandbox` 与 `approval_policy`。
  `settings` 与 `Session(settings=...)` 是同一个映射。
- `max_peers`（默认 8）限制 broker 内的 peer 数量。
- 其余接口：`get(name)`、`list_peers()`、`notes()`、`close(name)`、
  `close()`。

## 命令行：peerctl

`peerctl` 把 peer 放在 daemon 里，因此它们不随发起命令结束。第一条命令会
启动 daemon；状态文件与日志位于 `$AGENTABI_PEER_STATE`（默认
`~/.local/state/agentabi/peers`）。

```bash
peerctl spawn reviewer --cwd /repo --prompt "Review the current branch"
peerctl spawn cx --backend codex --cwd /repo --prompt "Review the tests"
peerctl spawn oc --backend opencode --cwd /repo \
    --model rosetta/qwen3.8-27b --env OPENCODE_AUTH_CONTENT="$AUTH"
peerctl wait reviewer --timeout 300
peerctl send reviewer "Check the tests too"
peerctl steer reviewer "Do not edit files" --interrupt
peerctl interrupt reviewer
peerctl inbox reviewer --clear
peerctl status reviewer --json
peerctl ls
peerctl stop reviewer
peerctl daemon start|status|stop
```

`peerctl ls` 合并两个引擎，并新增 `ENGINE` 列：

| 引擎 | 归属 | 可发消息 |
| --- | --- | --- |
| `process`（默认） | peer daemon | `send`、`steer`、`interrupt`、`wait`、`inbox` |
| `bg` | Claude 自己的 daemon（`claude --bg`） | 仅 `attach` |

`BACKEND` 列标明 process peer 背后的 CLI（`claude` 或 `codex`）。

输出为结构化内容的命令都接受 `--json`，便于其他 agent 调用。

## 连接别的进程启动的会话

`peerctl attach` 向会话的 peer socket 写入一条消息，路径为
`$XDG_RUNTIME_DIR/cc-socks/<pid>.sock`。目标可以是 `bg` 会话，也可以是
`claude agents --json` 里的交互式 Claude Code 会话：

```bash
peerctl attach reviewer "Run the linter and report only failures"
peerctl attach reviewer "..." --from-name coordinator --from-mode bypass
```

这条通道是单向的：接收方不会回复，发送方也得不到确认，所以命令只报告
消息 id 与字节数，不表示送达。帧格式是在 Claude Code 2.1.299 上实测得到的，
并非公开协议。接收方的权限模式与声明的 `from-mode` 不同时，消息可能被挂起
而不是投递。

`attach` 会拒绝 `process` peer：直接写它的 socket 会绕过 broker 的回合跟踪。
这类 peer 请用 `peerctl send`。

## daemon 停止后的恢复

daemon 是 peer 的父进程，停止 daemon 会一并停止 peer。`peers.json` 保留了
Claude 的 session id 和可重放的启动选项（model、permission mode、add-dirs、
agents、command）。settings 与 env 永不写入，因为它们可能含密钥。

```bash
peerctl recover reviewer
peerctl recover cx --settings "$SETTINGS_JSON"
```

Claude 后端以 `--resume <session_id>` 启动新进程；Codex 后端调用
`thread/resume`。两者都能继续之前的对话。正在进行的回合不会被重放，端点
settings 也不会保存：新进程需要时请再次传入 `--settings`。

## subagent 事件

Claude 会话通过 `Task` 或 `Agent` 工具派发自己的 subagent 时，native provider
会发出一对互补事件：

```python
{"type": "subagent_start", "subagent_id": "call_abc", "agent": "reviewer",
 "prompt": "check the tests", "background": False}
{"type": "subagent_end", "subagent_id": "call_abc", "status": "completed",
 "result": "..."}
```

`subagent_id` 就是发起它的 `tool_use` id，因此与同一次调用的 `tool_use`、
`tool_result` 事件对应。`status` 取 `completed`、`failed` 或 `cancelled`。
这些事件只用于观测：agentabi 只报告 CLI 自己派发了什么，不自行调度 subagent。
等待这些事件前先检查 `AgentCapabilities.supports_subagent_events`。

## 后端

### Claude

CLI 从 stdin 读取 stream-json user 消息。回合运行中发送的消息会在当前工具
调用结束后被模型读到。被拒绝的权限请求记入 inbox。

### Codex

一个 peer 是一个 `codex app-server` 进程加一个 thread。`send` 使用
`turn/start`；回合中发送使用 `turn/steer` 并带上活动回合 id，服务端会确认。
`interrupt` 发送 `turn/interrupt`，thread 仍可继续使用。token 用量通过
`thread/tokenUsage/updated` 上报。

`permission_mode` 映射为 app-server 的 sandbox 与审批策略：
`bypassPermissions` 为 `danger-full-access` + `never`，`full_auto` 与
`acceptEdits` 为 `workspace-write` + `never`，`default` 或 `plan` 保持
`on-request`。peer 没有审批通道，`on-request` 的审批会被拒绝并记录。
该后端不支持 `agents`。

```python
peer = await broker.spawn(
    "cx",
    cwd="/repo",
    backend="codex",
    prompt="Review the tests",
    settings={"base_url": "...", "api_key_env": "OPENAI_API_KEY"},
)
```

### OpenCode

一个 peer 是一个 `opencode serve` 进程加一个 V2 会话。`send` 以
`delivery: "steer"` 提交 prompt；回合中的第二条 prompt 即为 steer。
`interrupt` 调用 `/interrupt`，会话仍可使用。完成状态与文本从会话消息读取，
因为 opencode 1.19.0 在 steer 后会关闭事件流。事件流仍会保持订阅：1.19.0
在没有订阅者时不会为 prompt 启动回合。

端点 `settings` 会被拒绝，因为 V2 模型目录看不到经 `OPENCODE_CONFIG` 或
`OPENCODE_CONFIG_CONTENT` 注入的 provider（已实测）。请改为在 opencode 自身
完成 provider 配置，或把凭据交给 peer 的服务进程：

```python
peer = await broker.spawn(
    "oc",
    cwd="/repo",
    backend="opencode",
    model="rosetta/qwen3.8-27b",
    env={"OPENCODE_AUTH_CONTENT": '{"rosetta":{"type":"api","key":"..."}}'},
)
```

`peerctl spawn ... --env OPENCODE_AUTH_CONTENT=...` 等价。model 必须是
`provider/model`。审批模式下 peer 对权限请求回答 `once`，其他模式回答
`reject` 并记录 `permission_denied`。

### Pi

一个 peer 是一个 `pi -p --mode rpc` 进程。命令为 stdin 上每行一个 JSON 对象；
进程以 `response` 对象应答，并发出 `agent_start`、`turn_start`、
`message_update`、`turn_end`、`agent_end`、`agent_settled` 事件。`send` 使用
`prompt`，回合中发送使用 `steer`（模型在当前工具调用后读到），`interrupt`
使用 `abort`；回合以 `agent_settled` 结束，其 `aborted` 标记表示已取消。
session id 来自 `get_state`，`recover` 用 `--session-id` 复用它。

端点 settings 走与 pi 传输相同的适配器：私有 agent 目录加 `models.json`，
peer 关闭时删除。该传输没有审批参数，`permission_mode` 被接受但不生效。

```python
peer = await broker.spawn(
    "pi",
    cwd="/repo",
    backend="pi",
    model="rosetta/qwen3.8-27b",
    prompt="Review the tests",
)
```

## 限制

- Claude 后端的 steer 只以写入子进程 stdin 作为确认，不做应答，以回合结果
  为准。Codex 的 `turn/steer` 返回回合 id，opencode 返回已接收的消息 id。
- daemon 是 peer 的父进程。daemon 崩溃会带走 peer；用 `recover` 从保存的
  会话恢复。
- `wait` 在整个回合期间保持一个请求连接。超时抛出 `PeerTimeout`，回合继续
  运行。
- 权限请求会被自动拒绝（`--permission-prompts none`），每次拒绝以
  `permission_denied` 记入 inbox。
