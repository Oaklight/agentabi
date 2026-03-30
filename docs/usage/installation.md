# 安装

## 环境要求

- Python 3.9+
- 至少安装一个支持的 coding agent CLI

## 从 PyPI 安装

```bash
pip install agentabi
```

## 可选依赖

agentabi 通过可选依赖支持 SDK 类型的 provider。按需安装：

=== "Claude Code SDK"

    ```bash
    pip install agentabi[claude]
    ```

    安装 `claude-agent-sdk`。同时需要 `claude` CLI 在 PATH 中。

=== "Codex SDK"

    ```bash
    pip install agentabi[codex]
    ```

    安装 `codex-sdk-python`。同时需要 `codex` CLI 在 PATH 中。

=== "Gemini CLI SDK"

    ```bash
    pip install agentabi[gemini]
    ```

    安装 `gemini-cli-sdk`。同时需要 `gemini` CLI 在 PATH 中。

    !!! note
        Gemini Native provider（基于子进程）优先于 SDK provider，不需要额外 Python 依赖 — 只需 `gemini` CLI。

=== "所有 SDK"

    ```bash
    pip install agentabi[all]
    ```

## Agent CLI 安装

agentabi 驱动外部 CLI 工具。安装你需要的：

| Agent | 安装命令 | 验证 |
|-------|---------|------|
| Claude Code | `npm install -g @anthropic-ai/claude-code` | `claude --version` |
| Codex | `npm install -g @openai/codex` | `codex --version` |
| Gemini CLI | `npm install -g @google/gemini-cli` | `gemini --version` |
| OpenCode | `curl -fsSL https://opencode.ai/install \| bash` | `opencode --version` |

## 开发环境

```bash
git clone https://github.com/oaklight/agentabi.git
cd agentabi
pip install -e ".[dev]"
```

运行测试：

```bash
pytest tests/ --ignore=tests/integration -v
```

运行集成测试（需要安装 CLI）：

```bash
pytest tests/integration/ -m integration -v
```
