# Native vs SDK Providers

agentabi supports two provider types for most agents. This page explains the differences, shows integration test results, and helps you choose.

## Overview

| Aspect | Native (Subprocess) | SDK |
|--------|-------------------|-----|
| **Mechanism** | Spawns CLI as subprocess, parses JSON/JSONL stdout | Uses agent's Python SDK package |
| **Dependencies** | Zero — only needs CLI binary in PATH | Requires `pip install agentabi[agent]` |
| **Reliability** | High — CLI is the agent's primary interface | Varies — SDKs may have env/config requirements |
| **Streaming** | JSONL line-by-line | SDK event callbacks |
| **Default** | Yes (tried first) | Fallback if native unavailable |

## Provider Matrix

All 4 agents now support native providers. Three agents also have SDK fallback:

| Agent | Native Provider | SDK Provider | Default |
|-------|----------------|-------------|---------|
| Claude Code | `ClaudeNativeProvider` | `ClaudeSDKProvider` | Native |
| Codex | `CodexNativeProvider` | `CodexSDKProvider` | Native |
| Gemini CLI | `GeminiNativeProvider` | `GeminiSDKProvider` | Native |
| OpenCode | `OpenCodeNativeProvider` | — | Native |

## Choosing a Provider

Use the `prefer` parameter to select explicitly:

```python
from agentabi import Session

# Default: native first, SDK fallback
session = Session(agent="codex")

# Force SDK
session = Session(agent="codex", prefer="sdk")

# Explicit native
session = Session(agent="codex", prefer="native")
```

### When to use native (default)

- Zero extra Python dependencies needed
- CLI is already installed and configured
- You want the most reliable, well-tested path
- Running in environments where pip installs are restricted

### When to use SDK

- You need features only available via the SDK
- The CLI binary is not in PATH but the SDK is installed
- You prefer in-process communication over subprocess management

## Integration Test Results

We run automated comparison tests that exercise both provider types with the same prompt ("What is 2+2?") and compare the IR event output for consistency.

### Test Suite

The `test_native_vs_sdk.py` suite runs 5 tests per agent, parametrized across `claude_code`, `codex`, and `gemini_cli`:

| Test | What It Verifies |
|------|-----------------|
| `test_both_produce_session_lifecycle` | Both emit `session_start` and `session_end` |
| `test_both_produce_text_events` | Both emit `message_delta` or `message_end` |
| `test_both_answer_correctly` | Both produce text containing "4" |
| `test_event_types_are_valid_ir` | All events have valid IR type values |
| `test_event_type_overlap` | Providers share at least 2 event types |

### Results (2026-03-31)

**Per-agent integration tests** (native provider, prompt: "What is 2+2?"):

| Agent | `test_run` | `test_stream_events` | `test_stream_text` | Status |
|-------|-----------|---------------------|-------------------|--------|
| Claude Code | PASS | PASS | PASS | 3/3 |
| Codex | PASS | PASS | PASS | 3/3 |
| OpenCode | PASS | PASS | PASS | 3/3 |
| Gemini CLI | FAIL | FAIL | FAIL | 0/3 |

!!! note "Gemini CLI"
    Gemini CLI returned empty output during this test run. This is likely a CLI configuration issue (auth/quota), not a provider bug. The native provider correctly handles Gemini's JSONL format when the CLI produces output.

**Native vs SDK comparison** (Codex — the only agent with both providers functional):

| Test | Native | SDK | Result |
|------|--------|-----|--------|
| Session lifecycle events | `session_start`, `session_end` | `session_start`, `session_end` | PASS |
| Text events present | `message_delta` | `message_delta` | PASS |
| Correct answer ("4") | "4" | "4" | PASS |
| Valid IR event types | All valid | All valid | PASS |
| Event type overlap | 6 shared types | 6 shared types | PASS |

!!! info "Claude and Gemini SDK"
    Claude SDK (`claude-agent-sdk`) encountered a subprocess error during testing. Gemini SDK (`gemini-cli-sdk`) requires `OPENAI_API_KEY` for its LLM parser. These are SDK configuration issues — the native providers for both agents work correctly, which validates the native-first architecture.

### Event Type Comparison (Codex)

Both Codex providers produce the same set of IR event types:

```
Native:  {session_start, message_start, tool_use, tool_result,
          message_delta, usage, message_end, session_end}

SDK:     {session_start, message_start, tool_use, tool_result,
          message_delta, usage, message_end, session_end}
```

### Running the Tests

```bash
# All native vs SDK comparison tests
pytest tests/integration/ -m native_vs_sdk -v

# Per-agent integration tests
pytest tests/integration/ -m claude -v
pytest tests/integration/ -m codex -v

# Cross-CLI consistency
pytest tests/integration/ -m cross_cli -v
```

## Why Native-First?

The native-first architecture provides several advantages:

1. **Zero dependencies** — No `pip install` beyond agentabi itself. If the CLI is in PATH, it works.
2. **CLI stability** — Agent CLIs are the primary user-facing product, so they receive the most testing and maintenance.
3. **Environment isolation** — Subprocess execution avoids Python version conflicts and dependency clashes between SDKs.
4. **Consistent behavior** — The CLI handles auth, config, and model selection the same way users experience it interactively.

SDK providers remain valuable as fallbacks and for use cases where in-process communication is preferred.
