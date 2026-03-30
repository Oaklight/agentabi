# agentabi

Unified interface layer for agentic coding CLIs.

One interface. Any coding agent.

## What is agentabi?

`agentabi` provides a stable, unified interface (an "ABI") for interacting with different agentic coding CLIs. Write your integration once, swap agents with a config change.

### Supported Agents

| Agent | Provider | Status |
|-------|----------|--------|
| [Claude Code](https://github.com/anthropics/claude-code) | Anthropic | Implemented |
| [Codex](https://github.com/openai/codex) | OpenAI | Implemented |
| [Gemini CLI](https://github.com/google-gemini/gemini-cli) | Google | Implemented |
| [OpenCode](https://opencode.ai) | Open Source | Implemented |

## Use Cases

- **Fleet Management** — Unified entry point for managing multiple coding agents
- **Agent-to-Agent Calls** — Translation layer for inter-agent invocation
- **Benchmarking** — Run the same task across agents, compare results
- **Fallback & Routing** — Automatic failover and cost-aware routing
- **Middleware Pipeline** — Inject logging, metering, security scanning, audit trails
- **CI/CD Integration** — Vendor-agnostic agent integration for pipelines

## Ecosystem

`agentabi` is part of a layered stack:

```
agentabi  →  Agent CLI unified interface  →  like an OS ABI
llmir     →  LLM API format conversion    →  like a compiler IR
```

- [llmir](https://github.com/Oaklight/llmir) — LLM Intermediate Representation for converting between LLM provider API formats (OpenAI, Anthropic, Google)

## License

[MIT](LICENSE)
