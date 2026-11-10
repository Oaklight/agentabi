# Settings 统一配置

所有传输通过一个 `settings` 映射接收端点与认证配置。各适配器把它翻译成对应 CLI 能理解的形式：`--settings` JSON、`-c` 覆盖、环境变量、内联配置，或临时目录。

```python
from agentabi import Session

session = Session(
    agent="codex",
    settings={
        "base_url": "https://llm.example.com/v1",
        "api_key_env": "LLM_API_KEY",
        "model_provider": "gateway",
    },
)
result = await session.run("Reply with exactly: ok")
```

## 字段

`settings` 是 [`AgentSettings`](../api/ir-types.md#agentsettings) 映射。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `base_url` | `str` | 模型端点。绝对 `http(s)` URL；不接受 userinfo。 |
| `api_key` | `str` | 字面密钥。优先用 `api_key_env`。 |
| `api_key_env` | `str` | 保存密钥的环境变量名。 |
| `model_provider` | `str` | codex、opencode、pi 使用的 provider id。 |
| `headers` | `dict[str, str]` | 模型端点的额外 HTTP 头。 |
| `env` | `dict[str, str]` | 子进程的额外环境变量。 |
| `args` | `list[str]` | 额外 CLI 参数，插在生成的参数之后、提示词之前。 |
| `native` | `dict` | CLI 专属配置块，schema 由适配器定义。 |
| `on_unsupported` | `"error"` 或 `"ignore"` | 传输不支持某字段时的行为，默认 `"error"`。 |

`api_key` 与 `api_key_env` 互斥。`api_key_env` 依次在 `settings.env`、任务 `env`、进程环境中查找。

## 配置来源

settings 按次传入。先给 `Session` 设默认值，再在每次调用覆盖：

```python
session = Session(
    agent="claude_code",
    settings={"base_url": BASE, "api_key_env": "LLM_API_KEY"},
)

# headers/env/native 按键合并，其余字段替换。
await session.run("...", settings={"args": ["--verbose"]})
```

优先级从高到低：

1. 显式 `settings` 字段
2. `settings.native` 中的别名（`base_url`、`model_provider`）
3. 任务 `env`（`OPENAI_BASE_URL`、`ANTHROPIC_BASE_URL` 等）
4. CLI 默认值或进程环境

只用 `env` 的调用方行为不变。

## 各传输支持范围

| Agent | 传输 | 机制 | 字段 |
| --- | --- | --- | --- |
| `claude_code` | native、TUI | `--settings` + `apiKeyHelper` | 端点、`env`、`args`、`native` |
| `claude_code` | SDK | 无 | 拒绝 settings |
| `codex` | native | `-c` 覆盖，密钥走 `env_key` | 端点、`model_provider`、`headers`、`env`、`args`、`native` |
| `codex` | SDK | 无 | 拒绝 settings |
| `agy` | native | `--settings` + `GOOGLE_GEMINI_BASE_URL` | 端点、`env`、`args`、`native` |
| `gemini_cli` | native（已弃用） | `GOOGLE_GEMINI_BASE_URL` | 端点、`env`、`args` |
| `opencode` | V1 | `OPENCODE_CONFIG_CONTENT` | 端点、`model_provider`、`headers`、`env`、`args`、`native` |
| `opencode` | V2（`auto`） | 委托 V1 | 同 V1 |
| `opencode` | V2（显式） | 无 | 返回致命错误说明原因 |
| `pi` | native | 临时 `models.json` 目录 | 端点、`model_provider`、`headers`、`env`、`args`、`native` |

运行时查询同一张表：

```python
from agentabi import get_agent_capabilities

caps = get_agent_capabilities("codex")
print(caps["supported_settings"], caps["settings_mechanism"])
```

## 不支持的字段

默认抛出 `UnsupportedSettingsError`。它继承 `ValueError`，消息里给出 agent、传输、字段路径和可用字段。

```python
from agentabi import UnsupportedSettingsError
```

设置 `on_unsupported="ignore"` 则丢弃这些字段，运行继续，并在 `agentabi.settings` logger 记录一条只含字段名的 warning。错误与 warning 都不会包含密钥或 header 值。

## 传输细节

- **Claude Code** 在 OAuth 激活时忽略 `ANTHROPIC_BASE_URL`，因此端点和 `CLAUDE_CODE_SKIP_ANTHROPIC_AUTH` 开关写进 `--settings`。密钥由 `apiKeyHelper` 从子进程环境读取 `AGENTABI_CLAUDE_API_KEY`，不进入 argv。
- **codex** 忽略 `OPENAI_BASE_URL`，所以适配器用 `-c` 重写 provider 块。provider id 默认是 `agentabi`；codex 拒绝内置 id（如 `openai`），且 provider 块必须同时有 `name` 与 `base_url`。密钥通过 `env_key` 引用，以 `AGENTABI_CODEX_API_KEY` 传入；codex 0.162.1 会把 `model_providers.<id>.api_key` 记为未识别配置。
- **agy** 的 `GOOGLE_GEMINI_BASE_URL` 不能带结尾 `/v1`，适配器会去掉。`native` 指定 model 时不再发 `--model`，因为 agy 会用它校验自有目录。
- **opencode V1** 把 provider 块写进 `OPENCODE_CONFIG_CONTENT`，并用 `<provider>/<model>` 选择模型。provider id 默认是 `agentabi`。已有配置会被合并；非法 JSON 报错。
- **opencode V2** 的模型目录来自用户自己的 OpenCode 配置，看不到注入的 provider。`auto` 下这类任务走 V1；显式 `v2` 返回明确错误。
- **pi** 没有端点参数。`models.json` 中的 provider id 默认是 `agentabi`。适配器创建权限为 `0700` 的临时 agent 目录，写入 `models.json`（`0600`）并复制 `settings.json`，把 `PI_CODING_AGENT_DIR` 指向它，运行结束后删除。该次运行中，用户 agent 目录里安装的扩展不可用。

## 密钥

- 密钥只进子进程环境，pi 则进 `0600` 文件。不会进入 argv。
- `ResolvedSettings.redacted()` 返回脱敏后的日志视图。
- 校验会拒绝含 CR 或 LF 的 header 值。

## 限制

- ACP 网关暂未暴露 settings。
- 不支持配置文件 profile；按次传入。
- `model`、`thinking_level`、`permissions` 仍使用各自字段。
