"""In-process peer broker.

The broker owns the child processes for its lifetime.  Cross-process
callers use the daemon client instead; both share :class:`ClaudePeer`.
"""

from __future__ import annotations

import asyncio
from typing import Any

from .base import PeerInfo, PeerNote
from .claude import ClaudePeer
from .errors import PeerNotFound

#: Default cap on live peers in one broker.
DEFAULT_MAX_PEERS = 8


class PeerBroker:
    """Spawn, look up, and stop Claude peers in this process."""

    def __init__(self, *, max_peers: int = DEFAULT_MAX_PEERS) -> None:
        self._peers: dict[str, ClaudePeer] = {}
        self._max_peers = max_peers
        self._lock = asyncio.Lock()

    async def spawn(
        self,
        name: str,
        *,
        cwd: str,
        prompt: str | None = None,
        **options: Any,
    ) -> ClaudePeer:
        """Spawn a peer and optionally send its first task.

        Args:
            name: Unique name within this broker.
            cwd: Working directory.
            prompt: Initial task; omitted leaves the peer idle.
            **options: Forwarded to :class:`ClaudePeer`.

        Returns:
            The started peer.
        """
        async with self._lock:
            if name in self._peers:
                raise ValueError(f"peer name already in use: {name}")
            live = [
                peer for peer in self._peers.values() if peer.info().status != "exited"
            ]
            if len(live) >= self._max_peers:
                raise ValueError(
                    f"peer pool is full: {len(live)}/{self._max_peers}; "
                    "close a peer or raise max_peers"
                )
            peer = ClaudePeer(name=name, cwd=cwd, **options)
            self._peers[name] = peer
        try:
            await peer.start()
        except Exception:
            self._peers.pop(name, None)
            raise
        if prompt:
            await peer.send(prompt)
        return peer

    def get(self, name: str) -> ClaudePeer:
        """Return a live peer by name."""
        peer = self._peers.get(name)
        if peer is None:
            raise PeerNotFound(f"no peer named {name}")
        return peer

    def list_peers(self) -> list[PeerInfo]:
        """Return every registered peer, including exited ones."""
        return [peer.info() for peer in self._peers.values()]

    def notes(self, name: str | None = None, *, clear: bool = False) -> list[PeerNote]:
        """Collect notes from one peer, or from all peers."""
        if name is not None:
            return self.get(name).inbox(clear=clear)
        notes: list[PeerNote] = []
        for peer in self._peers.values():
            notes.extend(peer.inbox(clear=clear))
        return notes

    async def close(self, name: str | None = None) -> None:
        """Close one peer, or every peer when *name* is None."""
        if name is not None:
            peer = self._peers.pop(name, None)
            if peer is not None:
                await peer.close()
            return
        peers = list(self._peers.values())
        self._peers.clear()
        await asyncio.gather(*(peer.close() for peer in peers), return_exceptions=True)

    async def __aenter__(self) -> PeerBroker:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()


__all__ = ["DEFAULT_MAX_PEERS", "PeerBroker"]
