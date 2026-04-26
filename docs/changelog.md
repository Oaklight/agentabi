# Changelog

## v0.2.0 (2026-04-26)

Native-first provider architecture with CLI-aligned permission modes.

### Bug Fixes

- **OpenCode**: remove incorrect `--prompt` flag mapping for `system_prompt` — `opencode run` does not have this flag; system prompts are now documented as unsupported for this provider
- **Claude**: switch `full_auto` from `--dangerously-skip-permissions` to `--permission-mode bypassPermissions`, matching the modern CLI interface

### Features

- **CodexNativeProvider** — Subprocess provider for Codex CLI (`codex exec --json --full-auto`), parsing JSONL events into IR
- **`prefer` parameter** — `Session(prefer="sdk")` or `get_provider(agent, prefer="sdk")` for explicit native vs SDK provider selection
- **Native-first for all agents** — All 4 agents now have native subprocess providers as the default, with SDK as optional fallback
- **Claude**: add `auto`, `dont_ask`, `default` permission level mappings to `--permission-mode`
- **Gemini**: replace hardcoded `-y` (yolo) flag with `--approval-mode` driven by permission config (`yolo`, `auto_edit`, `plan`, `default`)
- **OpenCode**: add `--dangerously-skip-permissions` support, set `supports_permissions` to `True`
- **PermissionLevel**: add `"auto"` and `"dont_ask"` to the `PermissionLevel` type

### CI & Tooling

- Upgrade GitHub Actions to `actions/checkout@v6` and `actions/setup-python@v6`
- Add `ty check` (type checking) to the CI lint pipeline
- Add install-smoke-test matrix job (core, claude, codex variants)
- Add `UP` and `C901` ruff lint rules; fix all UP006/UP035 warnings (use builtin generics)
- Refactor `default_run()` and `ClaudeNativeProvider._build_command()` to resolve C901 complexity
- Add `ty`, `build`, `twine` to dev dependencies

### Testing

- 147 unit tests (+28 vs v0.1.0: CodexNativeProvider, permission mode mappings)
- Native vs SDK comparison integration tests — parametrized across all dual-provider agents, verifying IR event consistency
- `native_vs_sdk` pytest marker for targeted test runs
- All existing tests updated to match new CLI flag behavior

### Provider Changes

- `codex` provider chain updated: `[CodexNativeProvider, CodexSDKProvider]` (was `[CodexSDKProvider]`)
- `CodexSDKProvider` now emits `session_end` event for lifecycle consistency with native provider

### CLI Versions Tested

| Tool | Version |
|------|---------|
| Claude Code | 2.1.87 |
| Codex CLI | 0.117.0 |
| Gemini CLI | 0.35.3 |
| OpenCode | 1.4.3 |

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
