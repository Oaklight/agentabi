# 快速开始

本指南介绍 agentabi 的基本工作流程：检测 agent、运行任务、查看结果。

## 1. 检测可用 Agent

```python
from agentabi import detect_agents, get_agent_capabilities

agents = detect_agents()
print(f"可用 agent: {agents}")
# 例如 ['claude_code', 'codex', 'gemini_cli', 'opencode']

# 查看能力
for agent in agents:
    caps = get_agent_capabilities(agent)
    print(f"  {caps['name']}: streaming={caps['supports_streaming']}")
```

## 2. 运行任务

```python
import asyncio
from agentabi import Session

async def main():
    session = Session(agent="claude_code")
    result = await session.run(
        prompt="What is 2+2? Reply with just the number.",
        max_turns=2,
    )
    print(f"状态: {result.get('status')}")
    print(f"回答: {result.get('result_text')}")
    print(f"Token: {result.get('usage')}")

asyncio.run(main())
```

`Session.run()` 方法执行任务并返回 `SessionResult` 字典，包含：

- `session_id` — 唯一会话标识
- `status` — `"success"` 或 `"error"`
- `result_text` — agent 的文本输出
- `usage` — token 使用统计
- `cost_usd` — 预估费用（如可用）

## 3. 自动检测 Agent

不指定 agent 时，agentabi 会选择第一个可用的：

```python
session = Session()  # 自动检测
print(f"使用: {session.agent}")
```

## 4. 同步便捷接口

不需要 async 的简单脚本：

```python
from agentabi import run_sync

result = run_sync(
    prompt="Explain Python generators in one sentence.",
    agent="opencode",
    max_turns=2,
)
print(result["result_text"])
```

## 5. 完整示例

参见 [`examples/quickstart.py`](https://github.com/oaklight/agentabi/blob/master/examples/quickstart.py)，包含命令行参数解析和格式化输出。

## 下一步

- [流式输出](streaming.md) — 处理实时事件流
- [Agent 发现](discovery.md) — 高级发现与能力查询
- [API 参考](../api/session.md) — 完整 Session API 文档
