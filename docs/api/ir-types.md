# IR Types

Supporting types used across the agentabi API.

## TaskConfig

Configuration for a task to be executed by a provider.

```python
class TaskConfig(TypedDict, total=False):
    prompt: str               # Required: the task instruction
    agent: str                # Agent identifier
    model: str                # Model to use
    working_dir: str          # Working directory
    max_turns: int            # Maximum LLM turns
    system_prompt: str        # Custom system prompt
    resume: bool              # Resume a previous session
    session_id: str           # Session ID (for resume)
    permissions: PermissionConfig
    env: dict[str, str]       # Extra environment variables
```

## SessionResult

Aggregated result from `Session.run()` or `Provider.run()`.

```python
class SessionResult(TypedDict, total=False):
    session_id: str           # Session identifier
    status: SessionStatus     # "success" | "error"
    model: str                # Model used
    result_text: str          # Agent's text output
    usage: UsageInfo          # Token usage
    cost_usd: float           # Estimated cost
    errors: list[str]         # Error messages (if any)
```

## SessionStatus

```python
SessionStatus = Literal["success", "error"]
```

## UsageInfo

Token usage breakdown.

```python
class UsageInfo(TypedDict, total=False):
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
```

## AgentCapabilities

Describes what a provider/agent supports.

```python
class AgentCapabilities(TypedDict, total=False):
    name: str                     # Human-readable name
    agent_type: str               # Agent identifier
    supports_streaming: bool
    supports_mcp: bool
    supports_session_resume: bool
    supports_system_prompt: bool
    supports_tool_filtering: bool
    supports_permissions: bool
    supports_multi_turn: bool
    transport: str                # "subprocess" or "sdk"
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
    "default",       # Prompt for sensitive operations
    "accept_edits",  # Auto-approve file edits
    "plan",          # Planning mode, no execution
    "full_auto",     # Auto-approve everything (bypass all checks)
    "auto",          # Auto mode (agent decides)
    "dont_ask",      # Never prompt, skip if not auto-approved
]
```

**Provider mapping:**

| Level | Claude CLI | Gemini CLI | OpenCode CLI |
|-------|-----------|-----------|-------------|
| `"default"` | `--permission-mode default` | `--approval-mode default` | *(default)* |
| `"accept_edits"` | `--permission-mode acceptEdits` | `--approval-mode auto_edit` | *(not supported)* |
| `"plan"` | `--permission-mode plan` | `--approval-mode plan` | *(not supported)* |
| `"full_auto"` | `--permission-mode bypassPermissions` | `--approval-mode yolo` | `--dangerously-skip-permissions` |
| `"auto"` | `--permission-mode auto` | *(fallback to yolo)* | *(not supported)* |
| `"dont_ask"` | `--permission-mode dontAsk` | *(fallback to yolo)* | *(not supported)* |

## PermissionRequest

```python
class PermissionRequest(TypedDict, total=False):
    tool_name: str
    tool_use_id: str
    tool_input: dict
    description: str
```
