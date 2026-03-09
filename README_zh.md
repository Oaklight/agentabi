# agentabi

Agentic 编程 CLI 的统一接口层。

一套接口，任意 Agent。

## 什么是 agentabi？

`agentabi` 为不同的 agentic 编程 CLI 提供稳定、统一的接口（一个"ABI"）。只需编写一次集成代码，通过配置切换不同的 agent 后端。

### 支持的 Agent

| Agent | 提供方 | 状态 |
|-------|--------|------|
| [Claude Code](https://github.com/anthropics/claude-code) | Anthropic | 计划中 |
| [Codex](https://github.com/openai/codex) | OpenAI | 计划中 |
| [Pi](https://github.com/mariozechner/pi-coding-agent) | OpenClaw | 计划中 |
| [OpenCode](https://opencode.ai) | 开源 | 计划中 |
| [OpenClaw](https://openclaw.com) | 开源 | 计划中 |
| [Gemini CLI](https://github.com/google-gemini/gemini-cli) | Google | 计划中 |

## 使用场景

- **Fleet 管理** — 多个编程 agent 的统一入口
- **Agent 间调用** — 跨 agent 互操作的翻译层
- **基准测试** — 同一任务分发给多个 agent，对比结果质量、速度、成本
- **容错与路由** — 自动 failover 和基于成本的智能路由
- **中间件管道** — 注入日志、计量、安全扫描、审计追踪
- **CI/CD 集成** — 无供应商锁定的 agent 流水线集成

## 生态系统

`agentabi` 是分层架构栈的一部分：

```
agentabi  →  Agent CLI 统一接口    →  类似操作系统的 ABI
llmir     →  LLM API 格式转换      →  类似编译器的 IR
```

- [llmir](https://github.com/Oaklight/llmir) — LLM 中间表示，用于在不同 LLM 提供商 API 格式之间转换（OpenAI、Anthropic、Google）

## 许可证

[MIT](LICENSE)
