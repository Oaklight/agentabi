# 01: How Third-Party Session Managers Interact with Agent CLIs

## Purpose

This document captures how existing multi-agent session managers (Claude Squad, CCManager, Agent Deck, etc.) integrate with different coding agent CLIs. Understanding their interaction mechanisms informs agentabi's positioning — whether to replace, complement, or serve as an alternative integration layer.

## Key Finding

**All session managers use the same fundamental approach: terminal screen scraping + hardcoded string matching.** None of them use any programmatic API, SDK, structured JSON output, or event stream from the agent CLIs. They treat every agent as an opaque terminal process.

## Common Architecture Pattern

```
Session Manager
    │
    ├── Launch:  tmux/PTY spawn("<agent-binary> <args>")
    │
    ├── Output:  tmux capture-pane / headless xterm terminal text capture
    │
    ├── Status:  Hardcoded string matching on rendered terminal content
    │
    └── Input:   Write raw bytes to PTY (Enter = 0x0D, "Y\r", etc.)
```

## Project Comparison

### Claude Squad (6.7k stars, Go/tmux, AGPL-3.0)

**GitHub**: https://github.com/smtg-ai/claude-squad

**Launch**: `tmux new-session -d -s <name> -c <workdir> <program>` via `exec.Command`. The `program` is a plain command string from the Profile system (e.g., `"claude"`, `"aider --model gpt-4"`, `"gemini"`).

**Output Capture**: `tmux capture-pane -p -e -J -t <session>` grabs raw terminal content with ANSI escape codes. Displayed as-is in the preview pane.

**Status Detection**: 500ms polling loop. Computes SHA256 hash of captured content and compares with previous. If content changed → `Running`. If unchanged, checks for agent-specific prompt strings:

```go
// Hardcoded prompt detection per agent type
if t.program == ProgramClaude {
    hasPrompt = strings.Contains(content, "No, and tell Claude what to do differently")
} else if strings.HasPrefix(t.program, ProgramAider) {
    hasPrompt = strings.Contains(content, "(Y)es/(N)o/(D)on't ask again")
} else if strings.HasPrefix(t.program, ProgramGemini) {
    hasPrompt = strings.Contains(content, "Yes, allow once")
}
```

For unrecognized agents, `hasPrompt` is always false — only "output changing" vs "output static" can be distinguished.

**Auto-accept**: When `hasPrompt=true` and auto-yes enabled, writes `0x0D` (Enter) to PTY. A background daemon process handles this when TUI is detached, polling at configurable intervals (default 1000ms).

**Profile System**: Minimal `{name, program}` mapping. No agent-specific configuration beyond the command string.

### CCManager (972 stars, TypeScript/Node.js, MIT)

**GitHub**: https://github.com/kbwo/ccmanager

**Launch**: `Bun.spawn()` with PTY (pseudo-terminal). No tmux dependency. Each session gets an independent PTY process. Automatically injects `--teammate-mode in-process` for Claude Code to prevent conflicts.

**Output Capture**: Uses headless `@xterm/headless` Terminal instances. All PTY output is written to the virtual terminal, which properly parses ANSI escape codes, cursor movements, and screen clears. Status detection reads the rendered viewport (not raw bytes).

**Status Detection**: 100ms polling with 1s debounce (state must persist for 1000ms before confirmation). Each agent has a dedicated `StateDetector` implementation:

- **Claude**: Detects spinner characters (`✱✲✳✴✵✶`), `esc to interrupt` (busy), `Do you want` + `❯` cursor (waiting). Special handling for prompt box borders (`─` lines).
- **Codex**: Detects `press enter to confirm`, `Allow command?`, `[y/n]` (waiting), `esc.*interrupt` (busy).
- **Gemini**: Detects `Apply this change`, `Allow execution` (waiting), `esc to cancel` (busy).

**Auto-approval**: Two-layer safety system:
1. Deterministic dangerous command blacklist (~30 patterns including `rm` on system paths, `sudo`, `dd`, fork bombs, `curl|bash` pipes)
2. AI judgment via Claude Haiku: sends terminal content to `claude --model haiku -p --output-format json` for safety assessment, returns `{"needsPermission": true/false, "reason": "..."}`

**Session Data Copying**: When creating worktrees, can copy `~/.claude/projects/<project>/` directory to preserve Claude Code conversation context across worktrees.

**Prompt Injection**: Agent-specific mechanisms:
- Claude Code, Codex, Cursor, Cline: prompt as final CLI argument
- Gemini, GitHub Copilot, Kimi: prompt via `-i` or `-p` flag
- OpenCode: `--prompt` flag
- Unknown agents: write to stdin + `\r`

### Agent Deck (1.8k stars, Go/Bubble Tea, MIT)

**GitHub**: https://github.com/asheshgoplani/agent-deck

**Launch**: Same tmux-based approach as Claude Squad. Profiles define agent commands.

**Status Detection**: Similar tmux capture-pane + string matching pattern. Enhanced with smart status detection for running/waiting/idle/error states.

**Differentiating Features**:
- **Session Forking**: Branches Claude conversations with full history inheritance (leverages Claude Code's `--resume` capability)
- **MCP Server Manager**: Toggle MCP servers per session by managing Claude Code's MCP config files
- **MCP Socket Pooling**: Shares MCP server sockets across sessions (85-90% memory reduction)
- **Conductor System**: Persistent agent sessions that monitor other sessions via tmux capture-pane, with Telegram/Slack notification integration

## Adding New Agent Support

For every project, supporting a new CLI agent requires:

1. Find the agent's "waiting for input" prompt string(s)
2. Find the agent's "working/busy" indicator string(s)
3. Hardcode them into the status detection logic
4. (Optional) Add prompt injection method (flag vs stdin)

That's it. No SDK, no API, no structured data parsing.

## Fragility Analysis

This approach is inherently fragile:

- **UI text changes break detection**: If Claude Code changes "No, and tell Claude what to do differently" to any other text, all session managers break simultaneously.
- **Locale/i18n**: If agents add internationalization, English string matching fails.
- **Terminal rendering differences**: Different terminal sizes, color schemes, or Unicode support can affect captured content.
- **Version coupling**: Each session manager must track every agent's UI text changes and update hardcoded strings.

## Implications for agentabi

### What agentabi does differently

agentabi uses **structured JSONL output** (`--output-format stream-json`) instead of screen scraping. This provides:
- Typed events (MessageDelta, ToolUse, ToolResult, etc.)
- Reliable status detection from event types rather than string matching
- Machine-readable data (token counts, file diffs, costs)

### The tradeoff

- **More reliable**: Structured data > string matching
- **Still vendor-dependent**: Each agent's JSONL schema is different and can change
- **Narrower compatibility**: Not all agents support JSONL streaming (e.g., original OpenCode only returned final results)

### Potential positioning

agentabi could serve as the **stable integration layer** that session managers depend on, replacing their fragile screen-scraping code with reliable structured event streams. This would:
1. Decouple session managers from agent UI text changes
2. Provide richer data (usage metrics, file diffs, tool calls)
3. Enable features impossible via screen scraping (cost tracking, event filtering, middleware)

But this requires session managers to adopt agentabi as a dependency, which is a go-to-market challenge rather than a technical one.
