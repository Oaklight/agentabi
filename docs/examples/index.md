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
