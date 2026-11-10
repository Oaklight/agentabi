# Copilot CLI session wire (v1.0.107, 2026-11-07)

Captured with `copilot -p "<prompt>" --output-format json`. The CLI emits one
JSON object per stdout line; `bin/capture_stdio.py` wraps each line as
`{ts, dir, line}`.

## Field map

| Concept | Location |
| --- | --- |
| Session/thread id | `result.sessionId` (UUID) |
| Event causal chain | envelope `id` + `parentId` |
| Message role | `user.message`, `assistant.message`, `assistant.message_delta` |
| Turn correlation | `data.turnId`, `data.interactionId` |
| Tool call | `assistant.tool_call_delta` (`toolCallId`, `toolName`, `inputDelta`) then `tool.execution_start.arguments` |
| Tool output | `tool.shell_output.text`, `tool.execution_partial_result.partialOutput` |
| Tool completion | `tool.execution_complete.data.result`, `.success`, `.shellExecution.exitCode` |
| Permission denial | `tool.execution_complete.data.error.code = "denied"` |
| Permission request event | none observed in non-interactive mode |

## Non-interactive approval behavior

A command outside the allowlist still produces `tool.execution_start`, then
`tool.execution_complete` with:

```json
{"success": false, "error": {"code": "denied",
 "message": "Permission denied because no interactive user response was available. Retry in an interactive session so the user can approve it, ..."}}
```

There is no standalone `permission.request` frame in `--output-format json`.
Interactive TUI approval frames were not captured.

## Cross-session messaging

无此能力. Official docs describe the Sessions sidebar as user switching and
status only. `/fleet` subagents cannot message each other; only the
orchestrator coordinates.

- <https://docs.github.com/en/copilot/how-tos/copilot-cli/use-copilot-cli/work-with-multiple-sessions>
- <https://github.blog/ai-and-ml/github-copilot/run-multiple-agents-at-once-with-fleet-in-copilot-cli/>

Raw captures are gitignored. Only this schema directory is committed.
