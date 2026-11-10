# Gemini CLI output formats (0.67.0, 2026-11-07)

Status: **blocked** — no auth method configured.

## `--output-format json`

On an auth failure, output goes to **stderr**, pretty-printed, not NDJSON:

```json
{
  "session_id": "4699b32d-17a8-4bff-9ae9-dd3364cc0a1c",
  "error": {
    "type": "Error",
    "message": "Please set an Auth method in your /home/pding/.gemini/settings.json or specify one of the following environment variables before running: GEMINI_API_KEY, GOOGLE_GENAI_USE_VERTEXAI, GOOGLE_GENAI_USE_GCA",
    "code": 41
  }
}
```

Fields:

| field | type | notes |
| --- | --- | --- |
| `session_id` | string | UUID assigned before auth check |
| `error.type` | string | `Error` |
| `error.message` | string | human-readable |
| `error.code` | int | `41` = missing auth |

## `--output-format stream-json`

On an auth failure, it prints one plain line to stderr, not an NDJSON event:

```
Please set an Auth method ... GEMINI_API_KEY, GOOGLE_GENAI_USE_VERTEXAI, GOOGLE_GENAI_USE_GCA
```

No `init` / `message` / `result` frames are emitted.

## Not captured

- successful JSON or stream-json turn
- tool call / tool result frames
- permission request frames
- session resume / list frames

To unblock, set `GEMINI_API_KEY` (or `GOOGLE_GENAI_USE_VERTEXAI` /
`GOOGLE_GENAI_USE_GCA`) in the environment, or run interactive OAuth once.
Do not write the key into the repo.

## Version / install

- Version: `0.67.0`
- Install: `npm install -g @google/gemini-cli` (npm prefix under
  `~/.local/share/fnm/...`, user-level)
