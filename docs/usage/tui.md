# Claude Code TUI transport

The Claude provider can drive the real interactive Claude Code TUI instead of `claude --print`. Use it when the account is billed by subscription: Anthropic classifies a `-p` request as `sdk-cli` and counts it at roughly twice the interactive `cli` allowance.

```python
from agentabi import Session

session = Session(agent="claude_code", prefer="pty")
result = await session.run(prompt="What is 2+2?")
```

Through the ACP gateway:

```bash
agentabi acp --agent claude --prefer pty
```

## How it works

The provider starts `claude` on a pseudo-terminal, waits for the input prompt, pastes the message, confirms that the TUI rendered it, submits it with Enter, and reads the session transcript JSONL. Events come from the transcript, so the provider emits whole messages rather than token deltas.

The prompt must not contain control characters. Bracketed paste ends at `ESC[201~`, so an embedded escape sequence would turn the rest of the prompt into keystrokes; such a prompt is rejected before any byte is written.

## Dialogs

The TUI blocks on dialogs: questions, permission prompts, plan approval, and notices. Configure answers in `agent_extensions.tui_answers`; otherwise the turn fails with an `ErrorEvent` prefixed `tui_dialog_unanswered:` instead of waiting.

```python
task = {
    "prompt": "Ask me which color I prefer, then continue.",
    "agent_extensions": {
        "tui_answers": {
            # Question text substring -> option label, 1-based index, or text.
            "ask_user_question": {
                "Which color": "Blue",
                "Which size": 2,
                "Anything else": {"text": "No preference"},
            },
            # Permission dialogs: by tool name, then a default.
            "permission": {
                "by_tool": {"Bash": "allow_once", "Write": "deny"},
                "default": "deny",
            },
            # Plan approval.
            "plan": {"default": "approve_manual"},
        }
    },
}
```

- `ask_user_question`: keys are matched case-insensitively as substrings of the question; the longest match wins and `*` is the catch-all. Values are an option label, a 1-based index, or `{"text": ...}`. Multi-question dialogs submit the review page with Enter. `multiSelect` questions are not supported and fail fast.
- `permission`: values are `allow_once`, `allow_always`, `allow_session`, or `deny`, mapped to the label the TUI rendered.
- `plan`: `approve_auto`, `approve_manual`, or `reject` with `feedback`.
- `notice`: answered with Enter.
- A security confirmation is never accepted automatically. Run the CLI once and accept it by hand.

The answer is latched to the transcript tool call, so a stale screen frame cannot send a key twice.

## Turn end

A turn ends at `system/turn_duration` or `result`, then the provider keeps reading for up to 1.5 s (0.3 s of quiet ends it early) so the `last-prompt` and `cost-state` rows are not cut off. Set `agent_extensions.settle_timeout` to change the bound.

## Startup fallback

If the CLI cannot start because the directory is untrusted or the account is signed out, the provider falls back to the native subprocess transport, then the SDK, and emits a non-fatal `ErrorEvent` that says why. Set `agent_extensions.tui_fallback` to `false` to make that failure fatal instead.

## Limits

- Whole messages, no token deltas.
- The provider reads the terminal rendering, so it is more fragile than `--print`. Dialog detection is anchored on the status bar; a dialog shape that changes between Claude Code releases can fail closed.
- Session ids must be UUIDs. A new session with another value gets a fresh UUID; a resume with another value fails before the child starts.
- On Windows the child runs under ConPTY; the provider sends Ctrl+C, waits, then terminates the process when it closes the PTY after a turn.
