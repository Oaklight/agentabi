"""Limits for line-delimited child output.

``asyncio`` caps a ``StreamReader`` line at 64 KiB by default.  A single
JSON event (a tool result, a file read) can be larger than that, and the
reader then raises ``LimitOverrunError`` and stops the stream.  A child
whose stdout is read by line gets this limit instead.
"""

from __future__ import annotations

#: Largest single line kept from a child process, in bytes.
STREAM_LIMIT = 32 * 1024 * 1024

__all__ = ["STREAM_LIMIT"]
