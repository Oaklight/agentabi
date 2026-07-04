# 中间件

agentabi 提供中间件管道来拦截和扩展 `Session.stream()` 调用。中间件可以记录事件、追踪用量、强制超时或转换事件流。

## 快速开始

```python
from agentabi import Session
from agentabi.middleware import (
    LoggingMiddleware,
    TimeoutMiddleware,
    UsageMeterMiddleware,
)

meter = UsageMeterMiddleware()

session = Session(
    agent="claude_code",
    middleware=[
        TimeoutMiddleware(120),
        LoggingMiddleware(),
        meter,
    ],
)

result = await session.run(prompt="Fix the bug")

# 查看累计用量
print(meter.summary())
```

中间件只包裹 `stream()` —— `run()` 内部消费 stream，因此自动获得中间件覆盖。

## 工作原理

中间件采用**洋葱模型**：列表中第一个中间件是最外层包裹。执行时控制流从外层向内层依次穿过各中间件，到达 provider 的 `stream()`，然后从内向外返回。

```
TimeoutMiddleware → LoggingMiddleware → UsageMeterMiddleware → provider.stream()
```

每个中间件是一个接受 `StreamHandler` 并返回新 `StreamHandler` 的可调用对象：

```python
StreamHandler = Callable[[TaskConfig], AsyncIterator[IREvent]]
Middleware = Callable[[StreamHandler], StreamHandler]
```

## 内置中间件

### LoggingMiddleware

以 INFO 级别记录任务开始/结束，以 DEBUG 级别记录单个事件。

```python
import logging

LoggingMiddleware(
    logger=logging.getLogger("my.logger"),  # 默认: agentabi.middleware
    level=logging.INFO,                     # 事件日志级别（默认: DEBUG）
    log_events=True,                        # 是否记录单个事件（默认: True）
    log_event_types={"error", "usage"},     # 过滤特定类型（默认: 全部）
)
```

### UsageMeterMiddleware

线程安全的累计 token 和成本追踪器。跨多次 `run()`/`stream()` 调用累计用量。

```python
meter = UsageMeterMiddleware()

# 运行多个任务...
await session.run(prompt="任务 1")
await session.run(prompt="任务 2")

# 获取累计统计
summary = meter.summary()
# {
#     "call_count": 2,
#     "input_tokens": 500,
#     "output_tokens": 200,
#     "cache_read_tokens": 0,
#     "cache_creation_tokens": 0,
#     "total_cost_usd": 0.003,
# }

meter.reset()  # 清除累计数据
```

### TimeoutMiddleware

对整个 stream 强制执行挂钟超时。对每个 `__anext__()` 调用使用 `asyncio.wait_for` 包裹，即使 stream 在事件中间卡住也能触发超时。

```python
import asyncio

session = Session(
    agent="claude_code",
    middleware=[TimeoutMiddleware(60)],  # 60 秒
)

try:
    result = await session.run(prompt="耗时任务")
except asyncio.TimeoutError:
    print("超时！")
```

## 自定义中间件

任何符合 `Middleware` 签名的可调用对象都可以使用：

```python
from agentabi.middleware import Middleware, StreamHandler

class PromptPrefixMiddleware:
    """为每个 prompt 添加前缀。"""

    def __init__(self, prefix: str) -> None:
        self._prefix = prefix

    def __call__(self, handler: StreamHandler) -> StreamHandler:
        prefix = self._prefix

        async def wrapper(task):
            # 写时复制：不修改原始 TaskConfig
            task = {**task, "prompt": f"{prefix}\n\n{task['prompt']}"}
            async for event in handler(task):
                yield event

        return wrapper
```

中间件可以：

- **修改 TaskConfig** —— 传递给下一个 handler 前修改（使用写时复制：`task = {**task, ...}`）
- **过滤事件** —— 不 yield 某些事件
- **转换事件** —— 修改事件字典后再 yield
- **短路管道** —— 例如超时、缓存
- **执行副作用** —— 日志、计量、告警

## 构造后添加中间件

```python
session = Session(agent="claude_code")

# 后续添加中间件
session.add_middleware(LoggingMiddleware())
session.add_middleware(TimeoutMiddleware(120))

# 查看当前栈（返回副本）
print(session.middleware)
```

## 手动链式组合

高级用法中，`chain_middleware()` 可以不通过 Session 构建管道：

```python
from agentabi import get_provider
from agentabi.middleware import chain_middleware, LoggingMiddleware

provider = get_provider("claude_code")
pipeline = chain_middleware(
    provider.stream,
    [LoggingMiddleware()],
)

async for event in pipeline(task_config):
    ...
```
