# Changelog

## v0.3.0 (2026-07-04)

Fifth agent support (Pi) and documentation improvements.

### Features

- **Pi coding agent** — Add `PiNativeProvider` for the [Pi coding agent](https://pi.dev/) CLI. Supports streaming, system prompts, session resume, and tool filtering (`--tools`/`--exclude-tools`). Thinking events are skipped (no IR type yet). (#14)

### Documentation

- Add middleware usage guide (`docs/usage/middleware.md`) with built-in and custom middleware examples
- Add `examples/middleware.py` demonstrating `LoggingMiddleware`, `UsageMeterMiddleware`, and `TimeoutMiddleware`
- Update providers docs to include Pi in native provider and registry tables
- Update session API docs with `middleware` constructor parameter and `add_middleware()` method
- Update mkdocs navigation to include middleware guide

### Testing

- 265+ unit tests (up from 231 in v0.2.2)

## v0.2.2 (2026-07-01)

Bug fixes, middleware pipeline, and provider reliability improvements.

### Features

- **Middleware pipeline** — Pluggable middleware system for `Session`. Stack `LoggingMiddleware`, `UsageMeterMiddleware`, and `TimeoutMiddleware` (or custom middleware) to intercept and transform the event stream. Middleware wraps `stream()` with an onion model; `run()` gets coverage for free.

### Bug Fixes

- **Codex/OpenCode result_text** — Codex (native + SDK) and OpenCode providers now correctly populate `result_text` on successful completion. Previously `message_end` events lacked the `text` field, causing `run()` to return empty `result_text` even on success. (#7)
- **Capability declarations** — Codex native, Gemini native, and OpenCode native no longer falsely advertise `supports_system_prompt: True`
- **Codex SDK temp file leak** — System prompt temp files are now cleaned up in a `try/finally` block

### Improvements

- **stderr capture** — All 4 native providers now read stderr after the subprocess exits and emit `ErrorEvent` if non-empty
- **Exit code checking** — Non-zero subprocess exit codes now produce an `ErrorEvent`
- **Timeout enforcement** — Native providers respect `TaskConfig.timeout`, killing the process and emitting a fatal error on expiry

### Documentation

- Rename `llmir` to `llm-rosetta` in ecosystem references

### Testing

- 231+ unit tests (up from 166 in v0.2.1)

## v0.2.1 (2026-06-25)

Patch release fixing custom endpoint support for proxy workflows.

### Bug Fixes

- **Provider base URL override** — When agents connect via a custom proxy endpoint (e.g. llm-rosetta gateway) using env vars like `OPENAI_BASE_URL` or `ANTHROPIC_BASE_URL`, the base URL now correctly overrides the provider's default. Previously the env-specified endpoint was ignored.

### CI & Tooling

- Switch to pre-commit for lint checks

### Documentation

- Remove DeepWiki badge from README
- Add PyPI, release, CI, and license badges to READMEs

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
