# 示例

## 快速开始

[`examples/quickstart.py`](https://github.com/oaklight/agentabi/blob/master/examples/quickstart.py) 演示核心工作流程：

1. **发现**可用 agent — `detect_agents()`
2. **查看**能力 — `get_agent_capabilities()`
3. **运行**任务 — `Session.run()`
4. **展示**结果（状态、文本、token、费用）

```bash
# 使用自动检测的 agent
python examples/quickstart.py

# 指定 agent
python examples/quickstart.py --agent codex

# 自定义 prompt
python examples/quickstart.py --agent opencode --prompt "List files"
```

## Settings

[`examples/settings.py`](https://github.com/oaklight/agentabi/blob/master/examples/settings.py) 用统一的 `settings` 映射把任意 agent 指向同一端点：

```bash
LLM_BASE_URL=https://llm.example.com/v1 LLM_API_KEY=... \
python examples/settings.py --agent codex
```

先打印传输的 settings 机制与支持字段，再运行提示词。详见 [Settings](../usage/settings.md)。

## Peer

[`examples/peers.py`](https://github.com/oaklight/agentabi/blob/master/examples/peers.py) 让同一个 Claude Code 进程连续处理两个回合：

```bash
LLM_BASE_URL=https://llm.example.com LLM_API_KEY=... \
python examples/peers.py --cwd /repo --prompt "Review the diff"
```

第二条消息会开启新回合或 steer 正在运行的回合，`inbox()` 报告不是回合结果的事件。详见 [Peer](../usage/peers.md)。

## 流式输出

[`examples/streaming.py`](https://github.com/oaklight/agentabi/blob/master/examples/streaming.py) 演示实时事件流：

1. **连接** agent — `Session`
2. **流式获取**事件 — `session.stream()`
3. **处理**每种事件类型（文本片段、工具调用、使用量、错误）

```bash
python examples/streaming.py
python examples/streaming.py --agent codex --prompt "Explain asyncio"
```

### 演示的事件类型

| 事件 | 处理方式 |
|-----|---------|
| `session_start` | 打印会话 ID 和模型 |
| `message_delta` | 实时打印文本片段 |
| `message_end` | 打印换行 |
| `tool_use` | 打印工具名和输入 |
| `tool_result` | 打印输出预览（成功/错误） |
| `usage` | 打印 token 数量和费用 |
| `error` | 打印错误消息 |
| `session_end` | 打印结束标记 |

## 中间件

[`examples/middleware.py`](https://github.com/oaklight/agentabi/blob/master/examples/middleware.py) 演示中间件管道：

1. **堆叠** `TimeoutMiddleware`、`LoggingMiddleware` 和 `UsageMeterMiddleware`
2. **运行**任务通过中间件包裹的 session
3. **查看** `UsageMeterMiddleware.summary()` 累计用量
4. **添加**构造后中间件 `session.add_middleware()`

```bash
python examples/middleware.py
python examples/middleware.py --agent codex --timeout 10
```
