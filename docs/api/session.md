# Session API

`Session` 类是与 agent CLI 交互的主接口。

## Session

```python
from agentabi import Session

session = Session(agent="claude_code", model="claude-sonnet-4-20250514")
```

### 构造函数

```python
Session(*, agent: str | None = None, model: str | None = None)
```

| 参数 | 描述 |
|-----|------|
| `agent` | 要使用的 agent 类型（如 `"claude_code"`、`"codex"`）。`None` 时自动检测。 |
| `model` | 默认模型。可在每次任务中覆盖。 |

如果请求的 agent 没有可用的 provider，抛出 `AgentNotAvailable`。

### 属性

| 属性 | 类型 | 描述 |
|-----|------|------|
| `agent` | `str` | 正在使用的 agent 类型 |
| `model` | `str \| None` | 默认模型（如已设置） |
| `provider` | `Provider` | 底层 provider 实例 |

### run()

```python
async def run(
    prompt: str,
    *,
    working_dir: str | None = None,
    max_turns: int | None = None,
    system_prompt: str | None = None,
    **kwargs,
) -> SessionResult
```

运行任务到完成，返回汇总结果。

| 参数 | 描述 |
|-----|------|
| `prompt` | 发送给 agent 的任务指令 |
| `working_dir` | agent 的工作目录 |
| `max_turns` | 最大 LLM 轮次 |
| `system_prompt` | 自定义系统提示词 |

返回 [`SessionResult`](ir-types.md#sessionresult) 字典。

### stream()

```python
async def stream(
    prompt: str,
    *,
    working_dir: str | None = None,
    max_turns: int | None = None,
    system_prompt: str | None = None,
    **kwargs,
) -> AsyncIterator[IREvent]
```

实时流式获取任务执行产生的 IR 事件。

| 参数 | 描述 |
|-----|------|
| `prompt` | 发送给 agent 的任务指令 |
| `working_dir` | agent 的工作目录 |
| `max_turns` | 最大 LLM 轮次 |
| `system_prompt` | 自定义系统提示词 |

Yield [`IREvent`](ir-events.md) 字典。

## run_sync()

```python
from agentabi import run_sync

result = run_sync(prompt="...", agent="claude_code")
```

同步便捷封装。创建 `Session`，通过 `asyncio.run()` 运行 prompt，返回结果。

```python
def run_sync(
    prompt: str,
    *,
    agent: str | None = None,
    model: str | None = None,
    **kwargs,
) -> SessionResult
```
