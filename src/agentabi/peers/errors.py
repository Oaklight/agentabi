"""Errors raised by the peer runtime."""

from __future__ import annotations


class PeerError(RuntimeError):
    """Base class for peer problems."""


class PeerNotFound(PeerError):
    """No peer with that name exists in this broker or daemon."""


class PeerBusy(PeerError):
    """The peer already has a turn and the operation needs an idle peer."""


class PeerClosed(PeerError):
    """The peer has been closed and cannot accept work."""


class PeerExited(PeerError):
    """The peer process exited unexpectedly."""

    def __init__(self, name: str, exit_code: int | None, stderr: str = "") -> None:
        self.name = name
        self.exit_code = exit_code
        self.stderr = stderr
        detail = f" exit code {exit_code}" if exit_code is not None else ""
        tail = f": {stderr.strip()[-400:]}" if stderr.strip() else ""
        super().__init__(f"peer {name} exited{detail}{tail}")


class PeerTimeout(PeerError):
    """A wait or request exceeded its timeout."""


__all__ = [
    "PeerBusy",
    "PeerClosed",
    "PeerError",
    "PeerExited",
    "PeerNotFound",
    "PeerTimeout",
]
