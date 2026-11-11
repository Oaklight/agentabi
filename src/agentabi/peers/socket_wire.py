"""Send one message to a running Claude Code session over its peer socket.

Claude Code listens on ``$XDG_RUNTIME_DIR/cc-socks/<pid>.sock`` for
messages from other sessions.  One connection carries one ``auth`` frame
(optional) and one user frame, newline-delimited.  The server never
replies, so a successful write is the only confirmation available.

The frame format is what Claude Code 2.1.299 was observed to accept
(``research/session-wire/FORMATS.md`` section 3.3); it is not a published
protocol.
"""

from __future__ import annotations

import json
import os
import socket
import uuid
from pathlib import Path
from typing import Any

from .errors import PeerError

#: Largest frame the receiver keeps; a bigger one is dropped silently.
MAX_FRAME_BYTES = 1_048_576
#: Envelope attributes the receiver reads when deciding to hold a message.
FROM_MODES = ("bypass", "default")


def socket_dir() -> Path:
    """Return the directory where Claude sessions keep their sockets."""
    raw = os.environ.get("XDG_RUNTIME_DIR")
    if raw:
        return Path(raw) / "cc-socks"
    return Path(f"/run/user/{os.getuid()}/cc-socks")


def session_socket(pid: int) -> Path:
    """Return the peer socket of the Claude session with ``pid``."""
    if pid <= 0:
        raise PeerError(f"invalid session pid: {pid}")
    return socket_dir() / f"{pid}.sock"


def envelope(
    text: str,
    *,
    from_addr: str,
    from_name: str,
    from_mode: str = "bypass",
) -> str:
    """Wrap ``text`` the way Claude wraps a cross-session message."""
    if from_mode not in FROM_MODES:
        raise PeerError(f"from_mode must be one of {FROM_MODES}")
    for label, value in (("from_addr", from_addr), ("from_name", from_name)):
        if not value or any(ch in value for ch in '"<>\n\r'):
            raise PeerError(f"{label} must be non-empty without quotes or newlines")
    return (
        f'<cross-session-message from="{from_addr}" '
        f'from-name="{from_name}" from-mode="{from_mode}">\n'
        f"{text}\n"
        "</cross-session-message>"
    )


def build_frame(
    text: str,
    *,
    from_addr: str,
    from_name: str,
    from_mode: str = "bypass",
    priority: str = "next",
    msg_id: str | None = None,
) -> dict[str, Any]:
    """Build the user frame, without the optional auth frame."""
    return {
        "msgV": 1,
        "msg_id": msg_id or str(uuid.uuid4()),
        "type": "user",
        "message": {
            "role": "user",
            "content": envelope(
                text,
                from_addr=from_addr,
                from_name=from_name,
                from_mode=from_mode,
            ),
        },
        "priority": priority,
        "from": from_addr,
    }


def send_cross_session(
    path: Path,
    text: str,
    *,
    from_name: str = "agentabi",
    from_addr: str = "uds:agentabi-peerctl",
    from_mode: str = "bypass",
    timeout: float = 5.0,
) -> dict[str, Any]:
    """Write one cross-session message to ``path``.

    Args:
        path: The receiver's ``<pid>.sock``.
        text: Message body, delivered verbatim inside the envelope.
        from_name: Name the receiver shows for the sender.
        from_addr: Self-asserted sender address.
        from_mode: ``bypass`` or ``default``; the receiver holds a message
            whose mode class differs from its own.
        timeout: Connect and send timeout in seconds.

    Returns:
        A receipt with the socket, message id, and byte count.

    Raises:
        PeerError: The socket is missing, the frame is too large, or the
            connection fails.
    """
    if not path.exists():
        raise PeerError(f"no Claude session socket at {path}")
    frame = build_frame(
        text,
        from_addr=from_addr,
        from_name=from_name,
        from_mode=from_mode,
    )
    payload = (json.dumps(frame, ensure_ascii=False) + "\n").encode()
    if len(payload) > MAX_FRAME_BYTES:
        raise PeerError(
            f"frame is {len(payload)} bytes; the receiver drops frames over "
            f"{MAX_FRAME_BYTES}"
        )
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(str(path))
            sock.sendall(payload)
    except OSError as exc:
        raise PeerError(f"send to {path} failed: {exc}") from exc
    return {
        "sent": True,
        "socket": str(path),
        "msg_id": frame["msg_id"],
        "bytes": len(payload),
        "acknowledged": False,
    }


__all__ = [
    "FROM_MODES",
    "MAX_FRAME_BYTES",
    "build_frame",
    "envelope",
    "send_cross_session",
    "session_socket",
    "socket_dir",
]
