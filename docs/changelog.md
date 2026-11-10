# Changelog

## [Unreleased]

### Added

- **ACP gateway** — `agentabi acp --agent <name>` exposes one agent as an ACP endpoint on stdio. `opencode` uses its own ACP server; `agy`, `claude`, `codex`, and `pi` run through the IR stream. ACP and JSON-RPC are vendored from zerodep, so no external ACP SDK or Node runtime is needed. (#44, #47)
- **ACP v2 and external ACP servers** — `initialize` negotiates ACP v2 and `session/prompt` returns the backend's real `stopReason`. `--agent acp --acp-command "<cmd>"` forwards to any external ACP server and passes its updates through unchanged. (#48, #50)
- **Router mode** — `--router` with repeated `--agent` serves several agents from one gateway process. The client chooses an agent per session with `_meta.agent`; an unknown name fails with `-32602` and lists the enabled agents. `initialize` advertises the intersection of the reachable backends' capabilities. Permissions are deny-by-default, and every routed call is recorded on the `agentabi.acp.audit` logger. (#64, PR #68)
- **Claude Code TUI transport** — opt in with `--prefer pty`. It runs the interactive Claude CLI on a PTY and tails the session transcript. Anthropic classifies `-p` as `sdk-cli` and counts it at roughly twice the interactive `cli` allowance, so this is the transport to use with a subscription account. Phase 1 emits whole messages, not token deltas. (#49)
- **OpenCode V2 provider** — `OpenCodeV2Provider` speaks the V2 HTTP/SSE API: server spawn and reuse, session create and resume, model switch, and a streaming turn state machine. Choose the transport with `agent_extensions.opencode.transport` or `AGENTABI_OPENCODE_TRANSPORT` (`auto`, `v1`, `v2`); the native provider stays in the chain as fallback. V2 servers emit no `session.idle`, so a turn ends at `session.next.step.ended` unless the step ended with `tool-calls`. (#62, PR #67)
- **TestPyPI publishing** — `.github/workflows/test-release.yml` publishes a manual build to TestPyPI with trusted publishing. Badges added to both READMEs and the docs indexes.
- **Weekly zerodep check** — a scheduled job runs `zerodep outdated`, updates the vendored `acp`/`jsonrpc`/`ptyx` copies, and opens a PR when they fall behind upstream.
- **`tools/peerctl.py`** — prototype for Claude Code background peers: spawn, list by workspace and name, stop, and export/import a handover snapshot keyed by `sessionId`.

### Fixed

- **Provider CLI drift** — codex probes `codex exec --help` and uses the approval flag the installed CLI accepts; opencode uses `--auto`; agy emits one message per `agent_response` step and keeps text across the `ACTIVE` and `DONE` updates. `turn.failed` and `error` events become fatal `ErrorEvent`s instead of being dropped. Codex also passes `--skip-git-repo-check`, so a working directory outside a git tree works.
- **Provider stdin** — child processes get `/dev/null` on stdin. Under the gateway the host stdin is the JSON-RPC pipe, and pi consumed ACP messages from it.
- **Codex temp schema leak** — generated `--output-schema` files are deleted after streaming; a path-like schema value is accepted as a file path. (#51, PR #52)
- **Claude TUI error paths** — API errors and internal failures surface as `ErrorEvent` instead of a silent end. Prompt text with control characters is rejected before any byte is written, and paste and submit are confirmed before a turn starts. (#55, #58)
- **Claude TUI transcript and startup detection** — the transcript reader resets after truncation or an inode rewrite, never advances past a partial line, and recovers a row glued to a partial prefix. Usage is summed across messages, and `cost_usd` is carried from the result row. The prompt is detected from the input box's rule lines, so the placeholder hint (`❯ Try "..."`) is accepted and menu items are not. A trust or login failure falls back to the native or SDK transport with a non-fatal `ErrorEvent`. Verified against Claude Code 2.1.299. (#59, PR #72)
- **Claude TUI dialog handling** — the transport detects and answers `AskUserQuestion`, permission, plan, notice, and security dialogs from `agent_extensions.tui_answers`. The status bar anchors the frame, the transcript tool call latches the answer, and a stale frame cannot resend a key. An unconfigured or unparseable dialog fails the turn with `tui_dialog_unanswered` instead of waiting; a security notice is never accepted automatically. Verified on Claude Code 2.1.299 for AskUserQuestion. (#57, PR #73)
- **ACP permission forwarding** — a backend can ask the ACP client for permission and receive the answer. Claude uses the SDK control protocol on stdin (`--permission-prompt-tool stdio`); other providers keep their own policy. The gateway adds a `permission_mode` config option (`ask`/`deny`/`allow_all` where the backend can ask; backends without a permission channel get no option), answers pending requests on `session/cancel`, and implements `allow_always` as a session cache because Claude's `updatedPermissions` was measured not to suppress the next prompt. `agentabi acp` gains `--permission-timeout` and `--permission-policy`. Verified end to end on Claude Code 2.1.299: allow runs the tool, reject and timeout deny it, cancel returns `cancelled`. (#63, PR #74)
- **Static ACP permission policy** — backends that cannot round-trip a permission request still honour `deny` and `allow_all`. The gateway passes the policy to the provider: codex runs `-s read-only` with `approval_policy="never"`, and agy drops its skip-permissions flag so print mode auto-denies; `allow_all` maps to `full_auto`. `pi` has no such flags and gets no `permission_mode` option. Verified over the wire: codex with `deny` could not create a file. (#76, PR #77)
- **Codex app-server backend** — `agentabi acp --agent codex-app-server` drives `codex app-server` over stdio JSON-RPC and round-trips `item/commandExecution/requestApproval` and `item/fileChange/requestApproval` through the ACP client. `availableDecisions` is authoritative; an unlisted decision is never sent as an allow. Cancel answers a pending approval with `decision: cancel` because `turn/interrupt` alone hangs. Verified against codex 0.162.1: allow created the file, reject cancelled the turn. (#70, PR #78)
- **agy tool events** — agy 1.5 reports a tool call as `step_type: "tool"` (ACTIVE, then DONE or ERROR, with the arguments under `tool_info.parameters`), but the provider only recognized the older `tool_use` shape. Every agy tool call was dropped, so no `tool_use` or `tool_result` reached the caller. A `tool` step now emits one `tool_use` keyed by its `step_index`, then a `tool_result` on DONE or a failed one on ERROR. Verified against agy 1.5.5. (#81)
- **Codex native session resume** — `CodexNativeProvider` now sends a task with `session_id` and `resume` set to `codex exec resume`, so the CLI reuses the stored thread instead of starting a new one. That subcommand rejects `-s`, `-C`, `--add-dir`, `--approve-for-me`, and `--full-auto`, so the sandbox and approval policy go through `-c sandbox_mode` / `-c approval_policy`, extra directories through `-c sandbox_workspace_write.writable_roots`, and images through `-i`. A resume with no session id uses `--last`. Verified against codex 0.162.1: the resumed run kept the thread id and its context, and a read-only resume could not write a file. (#80)
- **Canonical IR stop reasons** — every provider maps its CLI's stop reason to one set (`end_turn`, `max_tokens`, `max_turn_requests`, `refusal`, `cancelled`, `error`), so `message_end` always carries a canonical value instead of `stop`, `tool-calls`, or an empty string. Claude emits one `session_start` per run (the `system/status` row no longer looks like a second session), and codex and pi now put the session id on `session_end`. Verified across all transports against the real CLIs. (PR #79)
- **ptyx** — Windows ConPTY chunks are kept instead of dropped, pipe failures raise `PtyReadError`/`PtyWriteError`, and only cursor-position queries are answered. (#56)

## v0.4.0 (2026-09-08)

### Added

- **Antigravity CLI (agy) provider** — Google's new agentic coding CLI, replacing the discontinued Gemini CLI. Full stream-json event parser, env bridging (OPENAI_BASE_URL → GOOGLE_GEMINI_BASE_URL), permission mapping. 43 unit tests. (#20, PR #35)
- **`ThinkingLevel`** type and `thinking_level` field — Unified reasoning effort control across Claude (`--effort`), Pi (`--thinking`), and OpenCode (`--variant`). Values: `"off"`, `"low"`, `"medium"`, `"high"`, `"max"`. (#22, PR #33)
- **`output_schema`** field — Structured output via JSON Schema. Maps to Claude `--json-schema`, Codex `--output-schema`, agy `--json-schema`. (#23, PR #37)
- **`ephemeral`** field — Don't persist session state. Maps to Claude `--no-session-persistence`, Codex `--ephemeral`, Pi `--no-session`. (#24, PR #37)
- **`additional_dirs`** field — Multi-directory workspace. Maps to Claude/Codex/agy `--add-dir`. (#30, PR #37)
- **`files`** field — File/image attachments. Maps to Claude `--file`, Codex `--image`, OpenCode `--file`. (#30, PR #37)
- **`ReasoningDeltaEvent`** — Thinking/reasoning text streaming event, aligned with llm-rosetta. Pi and Claude now emit thinking content instead of dropping it. (#32, PR #38)
- **`ContentBlockStartEvent` / `ContentBlockEndEvent`** — Content block boundary events, aligned with llm-rosetta. (#32, PR #38)
- **`reasoning_tokens`** in `UsageInfo` — Tracks reasoning/thinking token usage. (#32, PR #38)
- **`reasoning_text`** in `SessionResult` — Accumulated reasoning text from `run()`. (#32, PR #38)

### Fixed

- **Session ID mapping** — Corrected `session_id` vs `resume` flag mapping for Claude (`--session-id` vs `--resume` vs `--continue`) and Gemini (`--session-id` vs `-r`). (#25, PR #34)
- **Pi `supports_mcp`** — Corrected to `True` (Pi supports MCP via `--extension` flag). (#29, PR #31)
- **Pi `AgentType`** — Added `"pi"` to `AgentType` Literal (was registered in registry but missing from type). (#29, PR #31)
- **Pi session semantics** — `session_id` alone → `--session-id` (create-if-missing), `session_id` + `resume` → `--session` (must exist), `resume` alone → `--continue`. (#29, PR #31)
- **Pi flag coverage** — Added `--thinking`, `--approve`, `--no-extensions`, `--skill`, `--no-session`, `--extension`, `--no-context-files`, `--no-skills`. (#29, PR #31)

### Deprecated

- **Gemini CLI provider** — `GeminiNativeProvider` and `GeminiSDKProvider` now emit `DeprecationWarning`. Gemini CLI was discontinued June 2026. Use `AgyNativeProvider` (`"agy"`) instead. (#21, PR #36)


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
