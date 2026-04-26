# Providers

Providers are the bridge between the unified `Session` API and individual agent CLIs/SDKs.

## Provider Protocol

All providers implement the `Provider` protocol:

```python
from agentabi import Provider

class Provider(Protocol):
    @staticmethod
    def is_available() -> bool: ...
    def capabilities(self) -> AgentCapabilities: ...
    def stream(self, task: TaskConfig) -> AsyncIterator[IREvent]: ...
    async def run(self, task: TaskConfig) -> SessionResult: ...
```

| Method | Description |
|--------|-------------|
| `is_available()` | Check if this provider can be used (CLI/SDK installed) |
| `capabilities()` | Declare supported features |
| `stream(task)` | Run task and yield IR events as they arrive |
| `run(task)` | Run task and return aggregated result |

## Provider Types

### Native Providers (Subprocess)

Native providers run the agent CLI as a subprocess and parse its structured output (JSON/JSONL).

| Provider | Agent | CLI Command |
|----------|-------|-------------|
| `ClaudeNativeProvider` | `claude_code` | `claude -p <prompt> --output-format stream-json` |
| `CodexNativeProvider` | `codex` | `codex exec --json --full-auto <prompt>` |
| `GeminiNativeProvider` | `gemini_cli` | `gemini -o stream-json --approval-mode <mode> -p <prompt>` |
| `OpenCodeNativeProvider` | `opencode` | `opencode run --format json -- <prompt>` |

Native providers have **no extra Python dependencies** — they only require the CLI binary in PATH.

### SDK Providers

SDK providers use the agent's official Python SDK for direct API integration.

| Provider | Agent | SDK Package |
|----------|-------|-------------|
| `ClaudeSDKProvider` | `claude_code` | `claude-agent-sdk` |
| `CodexSDKProvider` | `codex` | `codex-sdk-python` |
| `GeminiSDKProvider` | `gemini_cli` | `gemini-cli-sdk` |

SDK providers require installing the corresponding optional dependency (e.g., `pip install agentabi[claude]`).

## Provider Registry

The registry maps agent identifiers to ordered provider chains:

```python
{
    "claude_code": [ClaudeNativeProvider, ClaudeSDKProvider],
    "codex":       [CodexNativeProvider, CodexSDKProvider],
    "gemini_cli":  [GeminiNativeProvider, GeminiSDKProvider],
    "opencode":    [OpenCodeNativeProvider],
}
```

`resolve_provider(agent)` tries each provider in order and returns the first one where `is_available()` returns `True`.

### Provider Selection with `prefer`

By default, native (subprocess) providers are tried first. Use the `prefer` parameter to override:

```python
from agentabi import get_provider, Session

# Explicit SDK preference
provider = get_provider("codex", prefer="sdk")

# Or via Session
session = Session(agent="codex", prefer="sdk")
```

| Value | Behavior |
|-------|----------|
| `None` (default) | Native first, SDK fallback |
| `"native"` | Same as default |
| `"sdk"` | SDK first, native fallback |

## Custom Provider Access

```python
from agentabi import get_provider

provider = get_provider("opencode")
caps = provider.capabilities()
print(caps["transport"])  # "subprocess"
```

## default_run()

Providers that only implement `stream()` can delegate `run()` to the shared `default_run()` helper, which consumes the event stream and aggregates it into a `SessionResult`:

```python
from agentabi.providers.base import default_run

class MyProvider:
    async def run(self, task):
        return await default_run(self, task)
```

`default_run()` accumulates `message_delta` text, captures `message_end` text, collects `usage` and `error` events, and returns a complete `SessionResult`.
