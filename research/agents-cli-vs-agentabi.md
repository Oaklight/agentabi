# google/agents-cli vs agentabi 对比分析报告

> 调研日期: 2026-07-01

## 1. 项目概览

| 维度 | google/agents-cli | agentabi |
|------|-------------------|----------|
| 仓库 | [google/agents-cli](https://github.com/google/agents-cli) | [Oaklight/agentabi](https://github.com/Oaklight/agentabi) |
| 一句话定位 | 让 coding agent 学会构建、评估和部署 AI agent 到 Google Cloud | 为不同 coding agent CLI 提供统一的 Python 调用接口 |
| 语言 | Python (CLI) + Markdown (Skills) | Python |
| Stars | ~4,500 | 初期 |
| 协议 | Apache 2.0 | MIT |
| 状态 | Pre-GA (预览) | Alpha (v0.2.1) |
| 维护方 | Google | 个人 (Peng Ding) |
| Python 版本 | 3.11+ | 3.9+ |

## 2. 核心定位差异

这两个项目解决的是完全不同的问题，处于 agent 生态的不同层次。

### google/agents-cli：给 coding agent 的"技能包"

```
用户 → Coding Agent (Claude Code / Codex / ...) → [agents-cli Skills 指导] → 构建 ADK Agent → 部署到 Google Cloud
       ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
       agents-cli 增强这一步的能力
```

agents-cli 本身**不是** coding agent，而是一套 CLI 工具 + Markdown 技能定义。它教会 coding agent 如何使用 Google ADK 框架来构建 AI agent，并通过 CLI 命令完成脚手架、评估、部署等操作。本质上是 **Google Cloud agent 开发的生命周期管理工具**。

### agentabi：coding agent 的"统一调用层"

```
开发者代码 → agentabi (统一接口) → Claude Code CLI / Codex CLI / Gemini CLI / OpenCode CLI
             ^^^^^^^^^^^^^^^^
             agentabi 统一这一层
```

agentabi 解决的是 coding agent CLI 碎片化问题：每个 agent 的 CLI 接口、输出格式、SDK 都不同。agentabi 提供一个统一的 async Python API，让开发者写一次集成代码，就能驱动任何 coding agent。本质上是 **coding agent CLI 的抽象适配层**。

### 关键区别

| 维度 | agents-cli | agentabi |
|------|-----------|----------|
| 面向谁 | Coding agent（作为 agent 的技能） | 开发者（作为 Python 库） |
| 做什么 | 构建 + 部署 AI agent | 调用 coding agent |
| 方向 | Coding agent → AI agent 产物 | 代码 → Coding agent |
| 层次 | 应用层（scaffold/eval/deploy） | 基础设施层（subprocess/SDK 抽象） |
| 厂商绑定 | Google Cloud + Gemini + ADK | 无（纯 Python，厂商中立） |

## 3. 架构对比

### agents-cli 架构

```
agents-cli
├── CLI 命令层 (Click)
│   ├── scaffold    — 项目脚手架（基于 Cookiecutter 模板）
│   ├── eval        — 评估流水线（generate → grade → compare → analyze → optimize）
│   ├── deploy      — 部署到 Google Cloud（Agent Runtime / Cloud Run / GKE）
│   ├── publish     — 注册到 Gemini Enterprise
│   ├── run         — 本地运行 agent
│   ├── infra       — Terraform 基础设施配置
│   └── data        — RAG 数据摄取
│
└── Skills 层 (Markdown)
    ├── workflow     — 开发生命周期、阶段定义
    ├── adk-code     — ADK Python API 知识
    ├── scaffold     — 脚手架使用指南
    ├── eval         — 评估方法论
    ├── deploy       — 部署指南
    ├── publish      — 发布到 Gemini Enterprise
    └── observability — 可观测性配置
```

Skills 通过 Vercel Skills 协议 (`npx skills add`) 或 Claude Plugin 格式注入到 coding agent 的上下文中。

### agentabi 架构

```
agentabi
├── Session（用户 API 入口）
│   ├── stream()     — 异步事件流
│   └── run()        — 聚合结果
│
├── Registry（注册表）
│   └── agent name → [Provider chain] （native 优先，SDK 回退）
│
├── Provider Protocol（统一接口协议）
│   ├── is_available()
│   ├── capabilities()
│   ├── stream()       → AsyncIterator[IREvent]
│   └── run()          → SessionResult
│
├── Native Providers（subprocess + JSONL 解析）
│   ├── ClaudeNativeProvider
│   ├── CodexNativeProvider
│   ├── GeminiNativeProvider
│   └── OpenCodeNativeProvider
│
├── SDK Providers（可选依赖）
│   ├── ClaudeSDKProvider
│   ├── CodexSDKProvider
│   └── GeminiSDKProvider
│
└── IR Types（TypedDict）
    ├── TaskConfig      — 统一输入
    ├── IREvent         — 12 种事件类型的判别联合
    └── SessionResult   — 聚合输出
```

## 4. 功能对比

| 功能域 | agents-cli | agentabi |
|--------|-----------|----------|
| **脚手架/模板** | ✅ scaffold 命令生成 ADK 项目（Python/Go/Java） | ❌ 不涉及 |
| **AI Agent 评估** | ✅ 完整流水线（生成 trace → LLM 评分 → 对比 → 聚类分析 → 自动调优） | ❌ 不涉及 |
| **云端部署** | ✅ 一键部署到 Agent Runtime / Cloud Run / GKE | ❌ 不涉及 |
| **基础设施配置** | ✅ Terraform 配置（CI/CD、datastore） | ❌ 不涉及 |
| **可观测性** | ✅ Cloud Trace、日志、第三方集成 | ❌ 不涉及 |
| **统一调用接口** | ❌ 不涉及（它是被 coding agent 调用的工具） | ✅ 核心功能 |
| **多 agent 支持** | ⚠️ 被多个 coding agent 使用，但不控制它们 | ✅ 统一驱动 Claude/Codex/Gemini/OpenCode |
| **流式事件** | ❌ | ✅ 12 种 IREvent 类型的实时流 |
| **Agent 探测** | ❌ | ✅ detect_agents() 自动发现可用 CLI |
| **能力查询** | ❌ | ✅ get_agent_capabilities() 结构化能力标志 |
| **Provider 回退** | ❌ | ✅ native → SDK 自动回退链 |
| **权限控制** | ❌ | ✅ 统一 PermissionConfig 映射到各 agent CLI 标志 |
| **厂商中立** | ❌ Google Cloud 绑定 | ✅ 纯 Python，无厂商依赖 |

## 5. 技术栈对比

| 维度 | agents-cli | agentabi |
|------|-----------|----------|
| 运行时依赖 | Click, Rich, Cookiecutter, Google Cloud SDK, ADK 等 | typing_extensions（唯一硬依赖） |
| CLI 框架 | Click (LazyGroup 延迟加载) | 无 CLI（纯库） |
| 模型框架 | Google ADK (Gemini 模型) | 无（驱动已有 agent CLI） |
| 云服务 | Vertex AI, Cloud Run, GKE, Cloud Trace, BigQuery | 无 |
| 分发方式 | `uvx google-agents-cli setup` + `npx skills add` | `pip install agentabi` |
| Agent 集成 | Markdown Skills (Vercel 协议) + Claude Plugin | subprocess + 可选 SDK |
| 类型系统 | 未调研 | TypedDict（无 Pydantic/dataclass） |

## 6. 适用场景

### 适合用 agents-cli 的场景

- 在 Google Cloud 上构建企业级 AI agent
- 需要完整的 agent 开发生命周期管理（脚手架 → 评估 → 部署 → 发布）
- 团队已经使用 Google Cloud 生态和 Gemini 模型
- 想让 coding agent（Claude Code/Codex）自动完成 agent 开发的重复性工作

### 适合用 agentabi 的场景

- 需要程序化地调用多个 coding agent CLI
- 构建多 agent 编排、CI/CD 集成、基准测试工具
- 需要统一的事件流格式来处理不同 agent 的输出
- 想要厂商中立的 agent 调用层，不被某个 agent 锁定
- Agent-to-Agent 调用场景中的翻译层

## 7. 互补性分析

这两个项目**没有竞争关系**，它们处于生态的不同层次，理论上可以协作：

```
开发者代码
    │
    ▼
agentabi (统一接口) ─── 选择 Claude Code
    │
    ▼
Claude Code (coding agent)
    │
    ▼
agents-cli Skills (增强 Claude Code 的 agent 构建能力)
    │
    ▼
ADK Agent → 部署到 Google Cloud
```

在这个场景中：
- agentabi 负责选择和调用 coding agent
- agents-cli 负责增强 coding agent 构建 AI agent 的能力

## 8. 对 agentabi 的启发与借鉴

### 可借鉴的方面

1. **Skills 系统的思路**：agents-cli 的 Markdown Skills 是一种轻量级的"教 agent 做事"的方式。agentabi 未来如果要支持"给 agent 注入上下文/技能"的功能，可以参考这种 Markdown 指令 + 元数据的模式。

2. **生命周期管理思维**：agents-cli 把 agent 开发分成明确的阶段（Understand → Scaffold → Build → Evaluate → Deploy → Publish → Observe）。agentabi 可以在更高层面为 coding agent 的调用提供类似的生命周期概念（发现 → 配置 → 执行 → 评估 → 路由）。

3. **评估框架**：agents-cli 的 eval 流水线（特别是 LLM-as-judge、自适应评分标准、自动提示词调优）很成熟。agentabi 在做基准测试/agent 对比时可以参考其评估方法论。

4. **多 coding agent 分发**：agents-cli 同时支持 Vercel Skills 协议和 Claude Plugin 格式，这种多渠道分发策略值得注意。

### 不建议直接模仿的方面

1. **Google Cloud 绑定**：agentabi 的核心优势就是厂商中立，不应引入任何云厂商锁定。

2. **Markdown-as-skill 的局限**：Skills 的效果完全取决于 coding agent 的指令遵循能力，不够可靠。agentabi 的编程接口方式（Python API + 类型系统）更可控。

3. **单一 agent 框架依赖**：agents-cli 深度绑定 ADK。agentabi 如果要支持 agent 构建类功能，应保持框架中立。

## 9. 总结

| 结论 | 说明 |
|------|------|
| **定位不同** | agents-cli 是"AI agent 开发工具链"，agentabi 是"coding agent 统一调用层" |
| **不存在竞争** | 两者在技术栈的不同层次，可以互补使用 |
| **agentabi 的独特价值** | 厂商中立、纯 Python、零硬依赖、统一事件模型——这些是 agents-cli 不具备也不关心的 |
| **可借鉴点** | 评估方法论、Skills 分发策略、生命周期阶段划分 |
| **风险点** | agents-cli 有 Google 背书和资源，但其 Pre-GA 状态和 GCP 锁定限制了通用性 |
