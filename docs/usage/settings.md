# Settings

Every transport takes endpoint and authentication configuration through one `settings` mapping. Each adapter translates it into the mechanism its CLI understands: a `--settings` JSON, `-c` overrides, environment variables, an inline config, or a temporary directory.

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

## Fields

`settings` is an [`AgentSettings`](../api/ir-types.md#agentsettings) mapping.

| Field | Type | Description |
| --- | --- | --- |
| `base_url` | `str` | Model endpoint. Absolute `http(s)` URL; userinfo is rejected. |
| `api_key` | `str` | Literal key. Prefer `api_key_env`. |
| `api_key_env` | `str` | Name of an environment variable holding the key. |
| `model_provider` | `str` | Provider id used by codex, opencode, and pi. |
| `headers` | `dict[str, str]` | Extra HTTP headers for the model endpoint. |
| `env` | `dict[str, str]` | Extra environment variables for the child process. |
| `args` | `list[str]` | Extra CLI arguments, inserted after generated flags and before the prompt. |
| `native` | `dict` | CLI-specific block. Its schema belongs to the adapter. |
| `on_unsupported` | `"error"` or `"ignore"` | What to do when a transport cannot honour a field. Default `"error"`. |

`api_key` and `api_key_env` are mutually exclusive. A key read from `api_key_env` is looked up in `settings.env`, then the task `env`, then the process environment.

## Where settings come from

Settings are per call. Pass defaults to `Session`, then override per call:

```python
session = Session(
    agent="claude_code",
    settings={"base_url": BASE, "api_key_env": "LLM_API_KEY"},
)

# headers/env/native merge key by key; other fields are replaced.
await session.run("...", settings={"args": ["--verbose"]})
```

Precedence, highest first:

1. explicit `settings` fields
2. `settings.native` aliases (`base_url`, `model_provider`)
3. the task `env` (`OPENAI_BASE_URL`, `ANTHROPIC_BASE_URL`, ...)
4. the CLI default or the process environment

Callers that only set `env` keep their old behaviour.

## Support per transport

| Agent | Transport | Mechanism | Fields |
| --- | --- | --- | --- |
| `claude_code` | native, TUI | `--settings` with `apiKeyHelper` | endpoint, `env`, `args`, `native` |
| `claude_code` | SDK | none | rejects settings |
| `codex` | native | `-c` overrides, key by `env_key` | endpoint, `model_provider`, `headers`, `env`, `args`, `native` |
| `codex` | SDK | none | rejects settings |
| `agy` | native | `--settings` plus `GOOGLE_GEMINI_BASE_URL` | endpoint, `env`, `args`, `native` |
| `gemini_cli` | native (deprecated) | `GOOGLE_GEMINI_BASE_URL` | endpoint, `env`, `args` |
| `opencode` | V1 | `OPENCODE_CONFIG_CONTENT` | endpoint, `model_provider`, `headers`, `env`, `args`, `native` |
| `opencode` | V2 (`auto`) | delegates to V1 | same as V1 |
| `opencode` | V2 (explicit) | none | a fatal error names the limitation |
| `pi` | native | temporary `models.json` directory | endpoint, `model_provider`, `headers`, `env`, `args`, `native` |

Query the same table at runtime:

```python
from agentabi import get_agent_capabilities

caps = get_agent_capabilities("codex")
print(caps["supported_settings"], caps["settings_mechanism"])
```

## Unsupported fields

By default an unsupported field raises `UnsupportedSettingsError`, which subclasses `ValueError` and names the agent, the transport, the field path, and the fields that would work.

```python
from agentabi import UnsupportedSettingsError
```

Set `on_unsupported="ignore"` to drop those fields instead. The run continues, and one warning listing only the field names is logged to the `agentabi.settings` logger. Header and key values never appear in an error message or a warning.

## Transport notes

- **Claude Code** ignores `ANTHROPIC_BASE_URL` when OAuth is active, so the endpoint and a `CLAUDE_CODE_SKIP_ANTHROPIC_AUTH` switch go into `--settings`. The key arrives through `apiKeyHelper` reading `AGENTABI_CLAUDE_API_KEY` from the child environment, so it never appears in argv.
- **codex** rewrites its provider block with `-c` because it ignores `OPENAI_BASE_URL`. The provider id defaults to `agentabi`; codex rejects built-in ids such as `openai`, and it needs both `name` and `base_url`. The key is referenced by `env_key` and passed as `AGENTABI_CODEX_API_KEY`; codex 0.162.1 logs `model_providers.<id>.api_key` as an unrecognized setting.
- **agy** needs the endpoint without a trailing `/v1` in `GOOGLE_GEMINI_BASE_URL`; the adapter strips it. A model named in `native` suppresses `--model`, because agy validates that flag against its own catalog.
- **opencode V1** writes a provider block into `OPENCODE_CONFIG_CONTENT` and selects `<provider>/<model>`. The provider id defaults to `agentabi`. An existing config is merged; invalid JSON is an error.
- **opencode V2** builds its model catalog from the user's own OpenCode config and cannot see an injected provider. With `auto` such a run uses V1; with an explicit `v2` it fails with a clear error.
- **pi** has no endpoint flag. The provider id in `models.json` defaults to `agentabi`. The adapter creates a private temporary agent directory (mode `0700`) holding `models.json` (mode `0600`) and a copy of `settings.json`, points `PI_CODING_AGENT_DIR` at it, and removes it when the run ends. Extensions installed in the user's agent directory are not available during such a run.

## Secrets

- Keys travel in the child environment, or in a `0600` file for pi. They never enter argv.
- `ResolvedSettings.redacted()` returns a log-safe view with the values masked.
- Validation rejects header values containing CR or LF.

## Limits

- Settings are not part of the ACP gateway yet.
- Profiles in a configuration file are not supported; pass settings per call.
- `model`, `thinking_level`, and `permissions` keep their own fields.
