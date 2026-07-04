# Middleware

agentabi provides a middleware pipeline for intercepting and extending `Session.stream()` calls. Middleware can log events, track usage, enforce timeouts, or transform the event stream.

## Quick Start

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

# Check accumulated usage
print(meter.summary())
```

Middleware wraps `stream()` only — `run()` consumes the stream internally, so it gets middleware coverage for free.

## How It Works

Middleware follows the **onion model**: the first middleware in the list is the outermost wrapper. When the pipeline executes, control flows inward through each middleware, reaches the provider's `stream()`, then flows back outward.

```
TimeoutMiddleware → LoggingMiddleware → UsageMeterMiddleware → provider.stream()
```

Each middleware is a callable that takes a `StreamHandler` and returns a new `StreamHandler`:

```python
StreamHandler = Callable[[TaskConfig], AsyncIterator[IREvent]]
Middleware = Callable[[StreamHandler], StreamHandler]
```

## Built-in Middleware

### LoggingMiddleware

Logs task start/end at INFO level and individual events at DEBUG level.

```python
import logging

LoggingMiddleware(
    logger=logging.getLogger("my.logger"),  # default: agentabi.middleware
    level=logging.INFO,                     # event log level (default: DEBUG)
    log_events=True,                        # log individual events (default: True)
    log_event_types={"error", "usage"},     # filter to specific types (default: all)
)
```

### UsageMeterMiddleware

Thread-safe cumulative token and cost tracker. Accumulates usage across multiple `run()`/`stream()` calls.

```python
meter = UsageMeterMiddleware()

# Run multiple tasks...
await session.run(prompt="Task 1")
await session.run(prompt="Task 2")

# Get cumulative stats
summary = meter.summary()
# {
#     "call_count": 2,
#     "input_tokens": 500,
#     "output_tokens": 200,
#     "cache_read_tokens": 0,
#     "cache_creation_tokens": 0,
#     "total_cost_usd": 0.003,
# }

meter.reset()  # clear accumulated data
```

### TimeoutMiddleware

Enforces a wall-clock timeout on the entire stream. Wraps each `__anext__()` call with `asyncio.wait_for`, so the timeout fires even if the stream stalls mid-event.

```python
import asyncio

session = Session(
    agent="claude_code",
    middleware=[TimeoutMiddleware(60)],  # 60 seconds
)

try:
    result = await session.run(prompt="Long task")
except asyncio.TimeoutError:
    print("Timed out!")
```

## Custom Middleware

Any callable matching the `Middleware` signature works:

```python
from agentabi.middleware import Middleware, StreamHandler

class PromptPrefixMiddleware:
    """Prepend a prefix to every prompt."""

    def __init__(self, prefix: str) -> None:
        self._prefix = prefix

    def __call__(self, handler: StreamHandler) -> StreamHandler:
        prefix = self._prefix

        async def wrapper(task):
            # Copy-on-mutate: don't modify the original TaskConfig
            task = {**task, "prompt": f"{prefix}\n\n{task['prompt']}"}
            async for event in handler(task):
                yield event

        return wrapper
```

Middleware can:

- **Modify TaskConfig** before passing to the next handler (use copy-on-mutate: `task = {**task, ...}`)
- **Filter events** by not yielding certain events
- **Transform events** by modifying event dicts before yielding
- **Short-circuit** the pipeline (e.g., timeout, caching)
- **Run side effects** (logging, metering, alerting)

## Adding Middleware After Construction

```python
session = Session(agent="claude_code")

# Add middleware later
session.add_middleware(LoggingMiddleware())
session.add_middleware(TimeoutMiddleware(120))

# Inspect current stack (returns a copy)
print(session.middleware)
```

## Chaining Manually

For advanced use, `chain_middleware()` can build a pipeline without a Session:

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
