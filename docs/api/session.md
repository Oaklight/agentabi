# Session API

The `Session` class is the primary interface for interacting with agent CLIs.

## Session

```python
from agentabi import Session

session = Session(agent="claude_code", model="claude-sonnet-4-20250514")
```

### Constructor

```python
Session(*, agent: str | None = None, model: str | None = None)
```

| Parameter | Description |
|-----------|-------------|
| `agent` | Agent type to use (e.g., `"claude_code"`, `"codex"`). Auto-detects if `None`. |
| `model` | Default model to use. Can be overridden per-task. |

Raises `AgentNotAvailable` if no provider is available for the requested agent.

### Properties

| Property | Type | Description |
|----------|------|-------------|
| `agent` | `str` | The agent type being used |
| `model` | `str \| None` | The default model, if set |
| `provider` | `Provider` | The underlying provider instance |

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

Run a task to completion and return the aggregated result.

| Parameter | Description |
|-----------|-------------|
| `prompt` | The task instruction to send to the agent |
| `working_dir` | Working directory for the agent |
| `max_turns` | Maximum number of LLM turns |
| `system_prompt` | Custom system prompt |

Returns a [`SessionResult`](ir-types.md#sessionresult) dictionary.

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

Stream IR events from a task execution in real time.

| Parameter | Description |
|-----------|-------------|
| `prompt` | The task instruction to send to the agent |
| `working_dir` | Working directory for the agent |
| `max_turns` | Maximum number of LLM turns |
| `system_prompt` | Custom system prompt |

Yields [`IREvent`](ir-events.md) dictionaries.

## run_sync()

```python
from agentabi import run_sync

result = run_sync(prompt="...", agent="claude_code")
```

Synchronous convenience wrapper. Creates a `Session`, runs the prompt with `asyncio.run()`, and returns the result.

```python
def run_sync(
    prompt: str,
    *,
    agent: str | None = None,
    model: str | None = None,
    **kwargs,
) -> SessionResult
```
