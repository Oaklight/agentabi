# Providers

Provider 是统一 `Session` API 与各 agent CLI/SDK 之间的桥梁。

## Provider 协议

所有 provider 实现 `Provider` 协议：

```python
from agentabi import Provider

class Provider(Protocol):
    @staticmethod
    def is_available() -> bool: ...
    def capabilities(self) -> AgentCapabilities: ...
    def stream(self, task: TaskConfig) -> AsyncIterator[IREvent]: ...
    async def run(self, task: TaskConfig) -> SessionResult: ...
```

| 方法 | 描述 |
|-----|------|
| `is_available()` | 检查此 provider 是否可用（CLI/SDK 已安装） |
| `capabilities()` | 声明支持的功能 |
| `stream(task)` | 运行任务并逐个产出 IR 事件 |
| `run(task)` | 运行任务并返回汇总结果 |

## Provider 类型

### Native Provider（子进程）

Native provider 将 agent CLI 作为子进程运行，解析其结构化输出（JSON/JSONL）为 IR 事件。

| Provider | Agent | CLI 命令 |
|----------|-------|---------|
| `ClaudeNativeProvider` | `claude_code` | `claude -p <prompt> --output-format stream-json` |
| `CodexNativeProvider` | `codex` | `codex exec --json --full-auto <prompt>` |
| `GeminiNativeProvider` | `gemini_cli` | `gemini -o stream-json --approval-mode <mode> -p <prompt>` |
| `OpenCodeNativeProvider` | `opencode` | `opencode run --format json -- <prompt>` |

Native provider **不需要额外的 Python 依赖** — 只需 CLI 可执行文件在 PATH 中。

### SDK Provider

SDK provider 使用 agent 的官方 Python SDK 进行直接 API 集成。

| Provider | Agent | SDK 包 |
|----------|-------|--------|
| `ClaudeSDKProvider` | `claude_code` | `claude-agent-sdk` |
| `CodexSDKProvider` | `codex` | `codex-sdk-python` |
| `GeminiSDKProvider` | `gemini_cli` | `gemini-cli-sdk` |

SDK provider 需要安装对应的可选依赖（如 `pip install agentabi[claude]`）。

## Provider 注册表

注册表将 agent 标识符映射到有序的 provider 链：

```python
{
    "claude_code": [ClaudeNativeProvider, ClaudeSDKProvider],
    "codex":       [CodexNativeProvider, CodexSDKProvider],
    "gemini_cli":  [GeminiNativeProvider, GeminiSDKProvider],
    "opencode":    [OpenCodeNativeProvider],
}
```

`resolve_provider(agent)` 按顺序尝试每个 provider，返回第一个 `is_available()` 为 `True` 的。

### 使用 `prefer` 选择 Provider

默认优先使用 native（子进程）provider。使用 `prefer` 参数可以覆盖此行为：

```python
from agentabi import get_provider, Session

# 显式偏好 SDK
provider = get_provider("codex", prefer="sdk")

# 或通过 Session
session = Session(agent="codex", prefer="sdk")
```

| 值 | 行为 |
|---|------|
| `None`（默认） | Native 优先，SDK 回退 |
| `"native"` | 同默认行为 |
| `"sdk"` | SDK 优先，native 回退 |

## 自定义 Provider 访问

```python
from agentabi import get_provider

provider = get_provider("opencode")
caps = provider.capabilities()
print(caps["transport"])  # "subprocess"
```

## default_run()

只实现了 `stream()` 的 provider 可以将 `run()` 委托给共享的 `default_run()` 辅助函数，它消费事件流并汇总为 `SessionResult`：

```python
from agentabi.providers.base import default_run

class MyProvider:
    async def run(self, task):
        return await default_run(self, task)
```

`default_run()` 累积 `message_delta` 文本、捕获 `message_end` 文本、收集 `usage` 和 `error` 事件，返回完整的 `SessionResult`。
