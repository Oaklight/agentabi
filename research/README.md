# Phase 0: Agent CLI Interface Research

## Purpose

This directory contains structured research on the CLI interfaces of major coding agent tools. The goal is to understand each agent's invocation model, output format, permission system, session management, tool ecosystem, and streaming event schema so that we can design a **common intermediate representation (IR)** — the AgentABI — that can normalize interactions across all of them.

## Research Methodology

1. **Primary sources**: Official documentation sites, GitHub READMEs, and CLI `--help` output.
2. **Secondary sources**: Community gists, forum posts, and third-party guides that document undocumented behavior.
3. **Hands-on verification**: Where possible, CLI flags and output formats were tested locally.
4. **Template-driven**: Each agent file follows the same template (invocation, output, input, permissions, sessions, tools, config, streaming events, SDK) so that cross-agent comparison is straightforward.

## Agent Files

| Agent | File | Command | Vendor | Language | Status |
|-------|------|---------|--------|----------|--------|
| Claude Code | [claude_code.md](claude_code.md) | `claude` | Anthropic | TypeScript | Active |
| Codex CLI | [codex.md](codex.md) | `codex` | OpenAI | Rust/TS | Active |
| Cursor CLI | [pi.md](pi.md) | `cursor-agent` | Cursor/Anysphere | TypeScript | Active (Beta) |
| OpenCode | [opencode.md](opencode.md) | `opencode` | opencode-ai | Go | Archived (moved to Crush) |
| OpenClaw | [openclaw.md](openclaw.md) | `openclaw` | OpenClaw (steipete) | TypeScript | Active |
| Gemini CLI | [gemini_cli.md](gemini_cli.md) | `gemini` | Google | TypeScript | Active |

## Synthesis

The [synthesis.md](synthesis.md) file contains cross-agent comparison tables covering:

- Event type mapping
- Permission model comparison
- Transport mechanism comparison
- Session model comparison
- Configuration comparison
- Common denominator features (IR candidates)
- Agent-specific features (extension mechanism candidates)

## Comparison Summary

| Capability | Claude Code | Codex CLI | Cursor CLI | OpenCode | OpenClaw | Gemini CLI |
|---|---|---|---|---|---|---|
| Headless/print mode | `-p` | `codex exec` | `-p` | `-p` | `openclaw agent` | positional arg |
| JSON output | `--output-format json` | `--json` | `--output-format json` | `-f json` | N/A (WS) | `--output-format json` |
| Streaming JSONL | `--output-format stream-json` | `--json` (JSONL) | `--output-format stream-json` | No | WS events | `--output-format stream-json` |
| MCP support | Yes (`--mcp-config`) | Yes (`codex mcp`) | Yes (`mcp.json`) | Yes (config) | Yes (skills) | Yes (`settings.json`) |
| Permission model | allowedTools + permission-mode | approval-mode + sandbox | allow/deny tokens | Auto-approve in `-p` | sandbox modes | allowed-tools |
| Session resume | `--resume <id>`, `--continue` | `codex resume`, `codex exec resume` | `--resume <id>` | Session list in TUI | Session per channel | Checkpointing |
| Config file | CLAUDE.md | AGENTS.md | .cursor/rules, AGENTS.md | .opencode.json | openclaw.json | GEMINI.md |
| Built-in tools | Read, Write, Edit, Bash, Glob, Grep, WebFetch, WebSearch, Task | Shell, file ops, web search | Read, Write, Edit, Shell, search | bash, view, write, edit, grep, glob, fetch | browser, canvas, bash, nodes | read_file, write_file, shell, web_fetch, google_search |
