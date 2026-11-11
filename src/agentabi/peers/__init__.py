"""Peer runtime: long-lived agent processes that accept messages.

A peer is a CLI process kept alive by agentabi.  Unlike a one-shot task,
a peer can receive more messages: a message sent during a turn steers
it, and a message sent while idle starts the next turn.

Example:
    from agentabi.peers import PeerBroker

    async with PeerBroker() as broker:
        peer = await broker.spawn("reviewer", cwd="/repo", prompt="Review PR #12")
        await peer.send("Also check the tests")
        turn = await peer.wait(timeout=300)
        print(turn.text)
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

from .base import (
    PeerAdapter,
    PeerEngine,
    PeerInfo,
    PeerNote,
    PeerStatus,
    PeerTurn,
    SendMode,
    SendResult,
    TurnStatus,
)
from .broker import DEFAULT_MAX_PEERS, PeerBroker
from .claude import ClaudePeer
from .codex import CodexPeer
from .errors import (
    PeerBusy,
    PeerClosed,
    PeerError,
    PeerExited,
    PeerNotFound,
    PeerTimeout,
)

if TYPE_CHECKING:
    from .client import DaemonUnavailable, PeerClient
    from .daemon import PeerDaemon

_LAZY = {
    "DaemonUnavailable": ".client",
    "PeerClient": ".client",
    "PeerDaemon": ".daemon",
}


def __getattr__(name: str) -> Any:
    """Import the client and daemon only when asked.

    The daemon imports this package, so an eager import here makes
    ``python -m agentabi.peers.daemon`` warn about a module that is already
    loaded when runpy runs it.
    """
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module(module, __name__), name)


__all__ = [
    "DEFAULT_MAX_PEERS",
    "DaemonUnavailable",
    "PeerClient",
    "PeerDaemon",
    "ClaudePeer",
    "CodexPeer",
    "PeerAdapter",
    "PeerBroker",
    "PeerBusy",
    "PeerClosed",
    "PeerEngine",
    "PeerError",
    "PeerExited",
    "PeerInfo",
    "PeerNote",
    "PeerNotFound",
    "PeerStatus",
    "PeerTimeout",
    "PeerTurn",
    "SendMode",
    "SendResult",
    "TurnStatus",
]
