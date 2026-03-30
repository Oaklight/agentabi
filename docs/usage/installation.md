# Installation

## Requirements

- Python 3.9+
- At least one supported coding agent CLI installed

## Install from PyPI

```bash
pip install agentabi
```

## Optional Dependencies

agentabi uses optional dependencies for SDK-based providers. Install the ones you need:

=== "Claude Code SDK"

    ```bash
    pip install agentabi[claude]
    ```

    Installs `claude-agent-sdk`. Also requires the `claude` CLI in your PATH.

=== "Codex SDK"

    ```bash
    pip install agentabi[codex]
    ```

    Installs `codex-sdk-python`. Also requires the `codex` CLI in your PATH.

=== "Gemini CLI SDK"

    ```bash
    pip install agentabi[gemini]
    ```

    Installs `gemini-cli-sdk`. Also requires the `gemini` CLI in your PATH.

    !!! note
        The Gemini Native provider (subprocess-based) is preferred over the SDK provider and requires no extra Python dependencies — only the `gemini` CLI.

=== "All SDKs"

    ```bash
    pip install agentabi[all]
    ```

## Agent CLI Installation

agentabi drives external CLI tools. Install the ones you want to use:

| Agent | Install Command | Verify |
|-------|----------------|--------|
| Claude Code | `npm install -g @anthropic-ai/claude-code` | `claude --version` |
| Codex | `npm install -g @openai/codex` | `codex --version` |
| Gemini CLI | `npm install -g @google/gemini-cli` | `gemini --version` |
| OpenCode | `curl -fsSL https://opencode.ai/install \| bash` | `opencode --version` |

## Development Setup

```bash
git clone https://github.com/oaklight/agentabi.git
cd agentabi
pip install -e ".[dev]"
```

Run tests:

```bash
pytest tests/ --ignore=tests/integration -v
```

Run integration tests (requires installed CLIs):

```bash
pytest tests/integration/ -m integration -v
```
