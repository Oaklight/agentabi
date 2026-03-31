# Changelog

## v0.2.0 (2026-03-31)

Native-first provider architecture: all agents now default to subprocess providers with SDK as fallback.

### Features

- **CodexNativeProvider** — Subprocess provider for Codex CLI (`codex exec --json --full-auto`), parsing JSONL events into IR
- **`prefer` parameter** — `Session(prefer="sdk")` or `get_provider(agent, prefer="sdk")` for explicit native vs SDK provider selection
- **Native-first for all agents** — All 4 agents now have native subprocess providers as the default, with SDK as optional fallback

### Testing

- 142 unit tests (+23 for CodexNativeProvider)
- Native vs SDK comparison integration tests — parametrized across all dual-provider agents, verifying IR event consistency
- `native_vs_sdk` pytest marker for targeted test runs

### Provider Changes

- `codex` provider chain updated: `[CodexNativeProvider, CodexSDKProvider]` (was `[CodexSDKProvider]`)
- `CodexSDKProvider` now emits `session_end` event for lifecycle consistency with native provider

## v0.1.0 (2026-03-31)

Initial release with unified provider architecture for 4 coding agent CLIs.

### Features

- **Session API** — Async-first `Session` class with `run()` and `stream()` methods, plus `run_sync()` convenience wrapper
- **Agent auto-detection** — `detect_agents()` discovers installed CLIs, `get_agent_capabilities()` inspects features
- **Provider system** — Protocol-based provider architecture with fallback chains
- **IR event stream** — 12 event types normalized across all agents (session, message, tool, usage, error, file_diff, permissions)

### Providers

- **ClaudeNativeProvider** — Subprocess provider for Claude Code CLI (`claude -p --output-format stream-json`)
- **ClaudeSDKProvider** — SDK provider using `claude-agent-sdk`
- **CodexSDKProvider** — SDK provider using `codex-sdk-python`
- **GeminiNativeProvider** — Subprocess provider for Gemini CLI (`gemini -o stream-json -y -p`)
- **GeminiSDKProvider** — SDK provider using `gemini-cli-sdk` (fallback)
- **OpenCodeNativeProvider** — Subprocess provider for OpenCode CLI (`opencode run --format json`)

### Testing

- 119 unit tests covering all providers, IR types, session, and registry
- 16 integration tests across all 4 CLIs (run, stream events, stream text)
- 4 cross-CLI consistency tests verifying unified IR output

### Examples

- `examples/quickstart.py` — Discovery, run, and result display
- `examples/streaming.py` — Real-time event streaming with all event types
