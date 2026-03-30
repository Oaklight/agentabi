# Changelog

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
