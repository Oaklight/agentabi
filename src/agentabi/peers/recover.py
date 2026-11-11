"""Rebuild a peer after its daemon went away.

The daemon is the parent of every peer, so a crash or a stop takes the
processes down.  ``peers.json`` keeps the session id that Claude wrote,
and ``--resume <session_id>`` gives the new process the same
conversation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .client import PeerClient
from .daemon import STATE_FILE
from .errors import PeerError, PeerNotFound


def load_entries(state_dir: Path) -> list[dict[str, Any]]:
    """Read the peer index left by a daemon, ignoring a damaged file."""
    path = Path(state_dir) / STATE_FILE
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    entries = payload.get("peers") if isinstance(payload, dict) else None
    return [entry for entry in entries or [] if isinstance(entry, dict)]


def find_entry(entries: list[dict[str, Any]], name: str) -> dict[str, Any]:
    """Return the saved entry for ``name`` or raise ``PeerNotFound``."""
    for entry in entries:
        if entry.get("name") == name:
            return entry
    raise PeerNotFound(f"no saved peer named {name!r}")


def recover_peer(
    client: PeerClient,
    name: str,
    *,
    permission_mode: str | None = None,
    prompt: str | None = None,
) -> dict[str, Any]:
    """Start a new peer process that resumes a saved session.

    Args:
        client: Client for the daemon that will own the new process.
        name: Saved peer name.
        permission_mode: Override the saved permission mode.
        prompt: Optional first task.

    Returns:
        The new peer's info dict.

    Raises:
        PeerNotFound: No saved entry carries that name.
        PeerError: The entry has no session id, or the name is live.
    """
    entry = find_entry(load_entries(client.state_dir), name)
    live = client.request("list")
    if any(peer.get("name") == name for peer in live):
        raise PeerError(f"peer {name!r} is already running")
    session_id = entry.get("session_id")
    if not session_id:
        raise PeerError(f"peer {name!r} has no saved session id; it cannot be resumed")
    cwd = entry.get("cwd")
    if not cwd:
        raise PeerError(f"peer {name!r} has no saved cwd")
    params: dict[str, Any] = dict(entry.get("recreate") or {})
    params.update(
        {
            "name": name,
            "cwd": cwd,
            "session_id": session_id,
            "resume": True,
        }
    )
    if permission_mode:
        params["permission_mode"] = permission_mode
    for key in ("model", "agents", "add_dirs"):
        if entry.get(key):
            params[key] = entry[key]
    if prompt:
        params["prompt"] = prompt
    return client.request("spawn", params)


__all__ = ["find_entry", "load_entries", "recover_peer"]
