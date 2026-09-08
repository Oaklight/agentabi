# IR 类型

agentabi API 中使用的辅助类型。

## AgentType

支持的 agent 标识符字面量类型。

```python
AgentType = Literal[
    "claude_code",
    "codex",
    "gemini_cli",
    "opencode",
    "pi",
    "agy",
]
```

## ThinkingLevel

统一的推理/思考深度控制。跨 Claude（`--effort`）、Pi（`--thinking`）、OpenCode（`--variant`）使用。

```python
ThinkingLevel = Literal["off", "low", "medium", "high", "max"]
```

## TaskConfig

提交给 provider 执行的任务配置。`prompt` 为唯一必需字段，其余均有合理默认值或自动检测。

```python
class TaskConfig(TypedDict):
    # ========== 必需 ==========
    prompt: Required[str]               # 任务指令

    # ========== Agent 选择 ==========
    agent: NotRequired[AgentType]       # Agent 标识符
    model: NotRequired[str]             # 使用的模型

    # ========== 执行上下文 ==========
    working_dir: NotRequired[str]       # 工作目录
    env: NotRequired[dict[str, str]]    # 额外环境变量

    # ========== 会话管理 ==========
    session_id: NotRequired[str]        # 会话 ID（用于恢复）
    resume: NotRequired[bool]           # 恢复历史会话

    # ========== 系统配置 ==========
    system_prompt: NotRequired[str]           # 自定义系统提示词
    append_system_prompt: NotRequired[str]    # 追加到默认系统提示词
    max_turns: NotRequired[int]               # 最大 LLM 轮次
    timeout: NotRequired[float]               # 超时时间（秒）
    thinking_level: NotRequired[ThinkingLevel]  # 推理深度

    # ========== 权限控制 ==========
    permissions: NotRequired[PermissionConfig]
    allowed_tools: NotRequired[list[str]]       # 允许的工具列表
    disallowed_tools: NotRequired[list[str]]    # 禁止的工具列表

    # ========== MCP ==========
    mcp_config: NotRequired[str]        # MCP 配置文件路径

    # ========== 输出控制 ==========
    output_schema: NotRequired[str | dict]  # 结构化输出的 JSON Schema
    ephemeral: NotRequired[bool]            # 不持久化会话状态

    # ========== 工作空间 ==========
    additional_dirs: NotRequired[list[str]]  # 额外目录（多目录工作空间）
    files: NotRequired[list[str]]            # 文件/图片附件

    # ========== Agent 扩展 ==========
    agent_extensions: NotRequired[dict[str, Any]]
```

### 新增字段说明

| 字段 | 说明 | CLI 映射示例 |
|------|------|-------------|
| `thinking_level` | 统一推理控制 | Claude `--effort`、Pi `--thinking`、OpenCode `--variant` |
| `output_schema` | 结构化输出 | Claude `--json-schema`、Codex `--output-schema`、agy `--json-schema` |
| `ephemeral` | 不持久化会话 | Claude `--no-session-persistence`、Codex `--ephemeral`、Pi `--no-session` |
| `additional_dirs` | 多目录工作空间 | Claude/Codex/agy `--add-dir` |
| `files` | 文件附件 | Claude `--file`、Codex `--image`、OpenCode `--file` |
| `append_system_prompt` | 追加系统提示词 | Claude `--append-system-prompt` |
| `allowed_tools` | 允许的工具 | Claude `--allowedTools`、Pi `--tools` |
| `disallowed_tools` | 禁止的工具 | Claude `--disallowedTools`、Pi `--exclude-tools` |
| `mcp_config` | MCP 配置路径 | Claude `--mcp-config`、Pi `--extension` |

## SessionResult

`Session.run()` 或 `Provider.run()` 的汇总结果。

```python
class SessionResult(TypedDict):
    # ========== 必需 ==========
    session_id: Required[str]           # 会话标识符
    status: Required[SessionStatus]     # 会话状态

    # ========== Agent/模型信息 ==========
    agent: NotRequired[str]
    model: NotRequired[str]             # 使用的模型

    # ========== 输出 ==========
    result_text: NotRequired[str]       # Agent 的文本输出
    reasoning_text: NotRequired[str]    # 推理/思考文本

    # ========== 文件变更 ==========
    file_diffs: NotRequired[list[FileDiffEvent]]

    # ========== 使用量 ==========
    usage: NotRequired[UsageInfo]       # Token 使用量
    cost_usd: NotRequired[float]        # 预估费用
    duration_ms: NotRequired[int]       # 持续时间（毫秒）
    num_turns: NotRequired[int]         # 实际轮次数

    # ========== 错误 ==========
    error: NotRequired[str]
    errors: NotRequired[list[str]]      # 错误消息（如有）

    # ========== Agent 扩展 ==========
    agent_extensions: NotRequired[dict[str, Any]]
```

## SessionStatus

会话状态的所有可能值：

```python
SessionStatus = Literal[
    "success",
    "error",
    "error_max_turns",
    "error_max_budget",
    "error_timeout",
    "cancelled",
]
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
    reasoning_tokens: int              # 推理 token 数量
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
