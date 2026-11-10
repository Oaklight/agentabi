"""Shared helpers for session-wire capture scripts (stdlib only)."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

SECRET_KEY = re.compile(
    r"(?i)^(authorization|cookie|set-cookie|api[-_]?key|token|secret|password|passwd|"
    r"access[-_]?token|refresh[-_]?token|session[-_]?token|bearer)$"
)

_TOKEN_PATTERNS = (
    re.compile(
        r"(?i)(\"?(?:authorization|api[-_]?key|token|secret|password|cookie)\"?\s*[:=]\s*)\"[^\"]*\""
    ),
    re.compile(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(
        r"\b(sk-[A-Za-z0-9_-]{8,}|ghp_[A-Za-z0-9]{8,}|xox[baprs]-[A-Za-z0-9-]{8,})\b"
    ),
)


def now() -> str:
    """Return an ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


def redact_text(text: str) -> str:
    """Redact common secret shapes from a raw line."""
    for pattern in _TOKEN_PATTERNS:
        text = pattern.sub(
            lambda m: m.group(1) + "<redacted>" if m.groups() else "<redacted>", text
        )
    return text


def redact_obj(obj):
    """Recursively redact secret-looking keys in a parsed JSON value."""
    if isinstance(obj, dict):
        return {
            key: "<redacted>" if SECRET_KEY.match(str(key)) else redact_obj(value)
            for key, value in obj.items()
        }
    if isinstance(obj, list):
        return [redact_obj(value) for value in obj]
    return obj


def write_record(fh, direction: str, line: str, redact: bool = True, **extra) -> None:
    """Write one NDJSON capture record."""
    if redact:
        line = redact_text(line)
    record = {"ts": now(), "dir": direction, "line": line}
    record.update(extra)
    fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    fh.flush()


def parse_line(line: str):
    """Parse a capture line, tolerating SSE `data:` prefixes and plain text."""
    text = line.strip()
    if text.startswith("data:"):
        text = text[5:].strip()
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
