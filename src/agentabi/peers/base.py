"""Shared peer types and the adapter protocol."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

PeerStatus = Literal["starting", "idle", "running", "exited"]
PeerEngine = Literal["process", "bg"]
TurnStatus = Literal["success", "error", "cancelled"]
SendMode = Literal["turn", "steer"]


@dataclass(frozen=True)
class PeerInfo:
    """A stable description of one peer."""

    name: str
    peer_id: str
    cwd: str
    status: PeerStatus
    engine: str = "process"
    session_id: str | None = None
    model: str | None = None
    pid: int | None = None
    turn_id: str | None = None
    started_at: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-ready view."""
        return {
            "name": self.name,
            "peer_id": self.peer_id,
            "cwd": self.cwd,
            "status": self.status,
            "engine": self.engine,
            "session_id": self.session_id,
            "model": self.model,
            "pid": self.pid,
            "turn_id": self.turn_id,
            "started_at": self.started_at,
        }


@dataclass(frozen=True)
class PeerTurn:
    """One completed turn of a peer."""

    turn_id: str
    status: TurnStatus
    text: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    num_turns: int = 0
    stop_reason: str = ""
    started_at: float = 0.0
    ended_at: float = 0.0
    errors: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-ready view."""
        return {
            "turn_id": self.turn_id,
            "status": self.status,
            "text": self.text,
            "usage": dict(self.usage),
            "num_turns": self.num_turns,
            "stop_reason": self.stop_reason,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "errors": list(self.errors),
        }


@dataclass(frozen=True)
class SendResult:
    """Admission receipt for a message written to a peer.

    Claude has no server-side acknowledgement, so ``accepted`` means the
    message reached the child process stdin.  ``mode`` says whether it
    started a new turn or joined the running one.
    """

    accepted: bool
    mode: SendMode
    turn_id: str

    def as_dict(self) -> dict[str, Any]:
        return {"accepted": self.accepted, "mode": self.mode, "turn_id": self.turn_id}


@dataclass(frozen=True)
class PeerNote:
    """An inbox entry: a turn result, a process exit, or a denial."""

    kind: Literal["turn", "exit", "permission_denied", "error"]
    text: str = ""
    turn: PeerTurn | None = None
    at: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"kind": self.kind, "text": self.text, "at": self.at}
        if self.turn is not None:
            out["turn"] = self.turn.as_dict()
        return out


@runtime_checkable
class PeerAdapter(Protocol):
    """One long-lived peer backed by a CLI process.

    The protocol is deliberately small so another CLI (Codex app-server,
    OpenCode HTTP) can implement it later without changing the broker.
    """

    name: str

    async def start(self) -> PeerInfo:
        """Spawn the process and return the peer description."""
        ...

    def info(self) -> PeerInfo:
        """Return the current peer description."""
        ...

    async def send(self, text: str) -> SendResult:
        """Send a message: start a turn when idle, steer when running."""
        ...

    async def steer(self, text: str, *, interrupt: bool = False) -> SendResult:
        """Send a message into the running turn."""
        ...

    async def interrupt(self) -> None:
        """Cancel the running turn without stopping the peer."""
        ...

    async def wait(self, timeout: float | None = None) -> PeerTurn:
        """Wait for the running turn (or return the last completed turn)."""
        ...

    def inbox(self, *, clear: bool = False) -> list[PeerNote]:
        """Return notes collected since the last read."""
        ...

    async def close(self) -> None:
        """Stop the peer."""
        ...


__all__ = [
    "PeerAdapter",
    "PeerEngine",
    "PeerInfo",
    "PeerNote",
    "PeerStatus",
    "PeerTurn",
    "SendMode",
    "SendResult",
    "TurnStatus",
]
