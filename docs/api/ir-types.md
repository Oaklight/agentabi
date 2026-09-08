# IR Types Reference

## TaskConfig

The unified input type for submitting work to any agent CLI.

```python
class TaskConfig(TypedDict):
    # Required
    prompt: Required[str]

    # Agent selection
    agent: NotRequired[AgentType]
    model: NotRequired[str]

    # Execution context
    working_dir: NotRequired[str]
    env: NotRequired[dict[str, str]]

    # Session management
    session_id: NotRequired[str]     # Specify or create session
    resume: NotRequired[bool]        # Resume existing session

    # System configuration
    system_prompt: NotRequired[str]
    append_system_prompt: NotRequired[str]
    max_turns: NotRequired[int]
    timeout: NotRequired[float]      # seconds
    thinking_level: NotRequired[ThinkingLevel]

    # Permission control
    permissions: NotRequired[PermissionConfig]
    allowed_tools: NotRequired[list[str]]
    disallowed_tools: NotRequired[list[str]]

    # MCP
    mcp_config: NotRequired[str]     # path to MCP config file

    # Output control
    output_schema: NotRequired[str | dict]  # JSON Schema
    ephemeral: NotRequired[bool]            # don't persist session

    # Workspace
    additional_dirs: NotRequired[list[str]]  # extra directories
    files: NotRequired[list[str]]            # file/image attachments

    # Agent-specific extensions
    agent_extensions: NotRequired[dict[str, Any]]
```

### Session ID semantics

The `session_id` and `resume` fields interact to control session behavior:

| `session_id` | `resume` | Behavior |
|---|---|---|
| set | `False` | Create/specify session with this ID |
| set | `True` | Resume existing session by ID |
| not set | `True` | Resume most recent session |
| not set | `False` | Default session behavior |

## AgentType

```python
AgentType = Literal[
    "claude_code",
    "codex",
    "gemini_cli",  # deprecated — use "agy"
    "opencode",
    "pi",
    "agy",
]
```

## ThinkingLevel

Controls reasoning effort across providers.

```python
ThinkingLevel = Literal["off", "low", "medium", "high", "max"]
```

### Provider mapping

| ThinkingLevel | Claude `--effort` | Pi `--thinking` | OpenCode `--variant` | agy `--effort` |
|---|---|---|---|---|
| `"off"` | (omit) | `off` | (omit) | (omit) |
| `"low"` | `low` | `low` | `minimal` | `low` |
| `"medium"` | `medium` | `medium` | (omit) | `medium` |
| `"high"` | `high` | `high` | `high` | `high` |
| `"max"` | `max` | `xhigh` | `max` | `high` |

## SessionResult

Aggregated result from a completed agent session.

```python
class SessionResult(TypedDict):
    # Required
    session_id: Required[str]
    status: Required[SessionStatus]

    # Optional
    agent: NotRequired[str]
    model: NotRequired[str]
    result_text: NotRequired[str]
    reasoning_text: NotRequired[str]
    file_diffs: NotRequired[list[FileDiffEvent]]
    usage: NotRequired[UsageInfo]
    cost_usd: NotRequired[float]
    duration_ms: NotRequired[int]
    num_turns: NotRequired[int]
    error: NotRequired[str]
    errors: NotRequired[list[str]]
    agent_extensions: NotRequired[dict[str, Any]]
```

## SessionStatus

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

Token usage statistics.

```python
class UsageInfo(TypedDict):
    input_tokens: NotRequired[int]
    output_tokens: NotRequired[int]
    cache_read_tokens: NotRequired[int]
    cache_creation_tokens: NotRequired[int]
    total_tokens: NotRequired[int]
    reasoning_tokens: NotRequired[int]
```

## PermissionConfig

```python
class PermissionConfig(TypedDict, total=False):
    level: PermissionLevel
    allowed_tools: list[str]
    disallowed_tools: list[str]
    sandbox: str

PermissionLevel = Literal[
    "default", "accept_edits", "plan",
    "full_auto", "auto", "dont_ask",
]
```

## AgentCapabilities

```python
class AgentCapabilities(TypedDict):
    # Required
    name: Required[str]
    agent_type: Required[str]

    # Feature support
    supports_streaming: NotRequired[bool]
    supports_mcp: NotRequired[bool]
    supports_session_resume: NotRequired[bool]
    supports_system_prompt: NotRequired[bool]
    supports_tool_filtering: NotRequired[bool]
    supports_file_diffs: NotRequired[bool]
    supports_permissions: NotRequired[bool]
    supports_multi_turn: NotRequired[bool]
    supports_thinking: NotRequired[bool]
    supports_structured_output: NotRequired[bool]
    supports_ephemeral: NotRequired[bool]
    supports_additional_dirs: NotRequired[bool]
    supports_file_attachments: NotRequired[bool]

    # Transport and limits
    transport: NotRequired[str]
    max_context_tokens: NotRequired[int]
    max_output_tokens: NotRequired[int]
    version: NotRequired[str]
```
