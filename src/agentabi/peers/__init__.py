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
from .errors import (
    PeerBusy,
    PeerClosed,
    PeerError,
    PeerExited,
    PeerNotFound,
    PeerTimeout,
)

__all__ = [
    "DEFAULT_MAX_PEERS",
    "ClaudePeer",
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
