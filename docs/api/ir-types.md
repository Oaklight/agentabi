# IR 类型

agentabi API 中使用的辅助类型。

## TaskConfig

提交给 provider 执行的任务配置。

```python
class TaskConfig(TypedDict, total=False):
    prompt: str               # 必需：任务指令
    agent: str                # Agent 标识符
    model: str                # 使用的模型
    working_dir: str          # 工作目录
    max_turns: int            # 最大 LLM 轮次
    system_prompt: str        # 自定义系统提示词
    resume: bool              # 恢复历史会话
    session_id: str           # 会话 ID（用于恢复）
    permissions: PermissionConfig
    env: dict[str, str]       # 额外环境变量
```

## SessionResult

`Session.run()` 或 `Provider.run()` 的汇总结果。

```python
class SessionResult(TypedDict, total=False):
    session_id: str           # 会话标识符
    status: SessionStatus     # "success" | "error"
    model: str                # 使用的模型
    result_text: str          # Agent 的文本输出
    usage: UsageInfo          # Token 使用量
    cost_usd: float           # 预估费用
    errors: list[str]         # 错误消息（如有）
```

## SessionStatus

```python
SessionStatus = Literal["success", "error"]
```

## UsageInfo

Token 使用量明细。

```python
class UsageInfo(TypedDict, total=False):
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
```

## AgentCapabilities

描述 provider/agent 支持的功能。

```python
class AgentCapabilities(TypedDict, total=False):
    name: str                     # 人类可读名称
    agent_type: str               # Agent 标识符
    supports_streaming: bool
    supports_mcp: bool
    supports_session_resume: bool
    supports_system_prompt: bool
    supports_tool_filtering: bool
    supports_permissions: bool
    supports_multi_turn: bool
    transport: str                # "subprocess" 或 "sdk"
```

## AgentType

```python
AgentType = Literal["claude_code", "codex", "gemini_cli", "opencode"]
```

## PermissionConfig

```python
class PermissionConfig(TypedDict, total=False):
    level: PermissionLevel
    allowed_tools: list[str]
    disallowed_tools: list[str]
    sandbox: bool
```

## PermissionLevel

```python
PermissionLevel = Literal[
    "default",       # 敏感操作时提示确认
    "accept_edits",  # 自动批准文件编辑
    "plan",          # 规划模式，不执行
    "full_auto",     # 自动批准所有操作（绕过所有检查）
    "auto",          # 自动模式（agent 自行决定）
    "dont_ask",      # 从不提示，未自动批准则跳过
]
```

**Provider 映射：**

| 级别 | Claude CLI | Gemini CLI | OpenCode CLI |
|------|-----------|-----------|-------------|
| `"default"` | `--permission-mode default` | `--approval-mode default` | *（默认）* |
| `"accept_edits"` | `--permission-mode acceptEdits` | `--approval-mode auto_edit` | *（不支持）* |
| `"plan"` | `--permission-mode plan` | `--approval-mode plan` | *（不支持）* |
| `"full_auto"` | `--permission-mode bypassPermissions` | `--approval-mode yolo` | `--dangerously-skip-permissions` |
| `"auto"` | `--permission-mode auto` | *（回退到 yolo）* | *（不支持）* |
| `"dont_ask"` | `--permission-mode dontAsk` | *（回退到 yolo）* | *（不支持）* |

## PermissionRequest

```python
class PermissionRequest(TypedDict, total=False):
    tool_name: str
    tool_use_id: str
    tool_input: dict
    description: str
```
