"""Rebuild a peer after its daemon went away.

The daemon is the parent of every peer, so a crash or a stop takes the
processes down.  ``peers.json`` keeps the session id that Claude wrote,
and ``--resume <session_id>`` gives the new process the same
conversation.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .client import PeerClient
from .daemon import STATE_FILE
from .errors import PeerError, PeerNotFound

#: Directory of the peerctl background-session manifest.
POOL_ENV = "PEERCTL_STATE"
POOL_FILE = "pool.json"


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


def pool_path(base: Path | str | None = None) -> Path:
    """Return the peerctl pool manifest path."""
    if base is not None:
        root = Path(base)
        return root if root.name == POOL_FILE else root / POOL_FILE
    raw = os.environ.get(POOL_ENV)
    root = Path(raw).expanduser() if raw else Path.home() / ".local/state/peerctl"
    return root / POOL_FILE


def find_pool_entry(
    name: str, *, path: Path | str | None = None
) -> dict[str, Any] | None:
    """Find the newest pool session for ``name``.

    A live session wins over a dead one; among equals the most recently
    started entry is used, so a reused name resumes the latest
    conversation.

    Returns:
        ``{"session_id", "cwd", "model"}`` or None.
    """
    manifest_file = pool_path(path)
    if not manifest_file.exists():
        return None
    try:
        payload = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    entries = payload.get("entries") if isinstance(payload, dict) else None
    if not isinstance(entries, dict):
        return None
    matches = [
        (str(session_id), entry)
        for session_id, entry in entries.items()
        if isinstance(entry, dict) and entry.get("name") == name
    ]
    if not matches:
        return None
    live = [match for match in matches if match[1].get("live")]
    candidates = live or matches

    def recency(match: tuple[str, dict[str, Any]]) -> int:
        entry = match[1]
        for key in ("startedAt", "updatedAt", "createdAt"):
            value = entry.get(key)
            if isinstance(value, int):
                return value
        return 0

    session_id, entry = max(candidates, key=recency)
    return {
        "session_id": session_id,
        "cwd": entry.get("cwd"),
        "model": entry.get("model"),
    }


def recover_from_pool(
    client: PeerClient,
    name: str,
    *,
    path: Path | str | None = None,
    permission_mode: str | None = None,
    settings: dict[str, Any] | None = None,
    env: dict[str, str] | None = None,
    prompt: str | None = None,
    command: list[str] | None = None,
) -> dict[str, Any]:
    """Recover a session recorded only in the peerctl pool.

    Background sessions carry a Claude session id but no daemon entry.
    They resume as process peers, because the CLI's stream-json mode is
    the only messageable form.

    Raises:
        PeerNotFound: The name is not in the pool or has no session id.
    """
    entry = find_pool_entry(name, path=path)
    if entry is None:
        raise PeerNotFound(f"no saved peer named {name!r}")
    session_id = entry.get("session_id")
    cwd = entry.get("cwd")
    if not session_id or not cwd:
        raise PeerError(
            f"pool entry {name!r} has no session id or cwd; it cannot be resumed"
        )
    return recover_peer(
        client,
        name,
        session_id=session_id,
        cwd=cwd,
        model=entry.get("model"),
        permission_mode=permission_mode,
        settings=settings,
        env=env,
        prompt=prompt,
        command=command,
    )


def recover_peer(
    client: PeerClient,
    name: str,
    *,
    permission_mode: str | None = None,
    prompt: str | None = None,
    settings: dict[str, Any] | None = None,
    env: dict[str, str] | None = None,
    session_id: str | None = None,
    cwd: str | None = None,
    model: str | None = None,
    command: list[str] | None = None,
) -> dict[str, Any]:
    """Start a new peer process that resumes a saved session.

    Args:
        client: Client for the daemon that will own the new process.
        name: Saved peer name.
        permission_mode: Override the saved permission mode.
        prompt: Optional first task.
        settings: Endpoint settings for the new process.  They are never
            read from state, because they can hold keys.
        env: Extra child environment for the new process.
        session_id: Explicit session id, used when no daemon entry exists.
        cwd: Explicit working directory for the same case.
        model: Explicit model override.
        command: Explicit CLI argv prefix (used by tests).

    Returns:
        The new peer's info dict.

    Raises:
        PeerNotFound: No saved entry carries that name.
        PeerError: The entry has no session id, or the name is live.
    """
    saved = load_entries(client.state_dir)
    entry = next((item for item in saved if item.get("name") == name), None)
    if entry is None and not (session_id and cwd):
        raise PeerNotFound(f"no saved peer named {name!r}")
    entry = entry or {}
    live = client.request("list")
    if any(peer.get("name") == name for peer in live):
        raise PeerError(f"peer {name!r} is already running")
    session_id = session_id or entry.get("session_id")
    if not session_id:
        raise PeerError(f"peer {name!r} has no saved session id; it cannot be resumed")
    cwd = cwd or entry.get("cwd")
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
    if model:
        params["model"] = model
    if command:
        params["command"] = list(command)
    if settings:
        params["settings"] = settings
    if env:
        params["env"] = env
    for key in ("model", "agents", "add_dirs"):
        if entry.get(key):
            params[key] = entry[key]
    if prompt:
        params["prompt"] = prompt
    return client.request("spawn", params)


__all__ = [
    "POOL_ENV",
    "POOL_FILE",
    "find_entry",
    "find_pool_entry",
    "load_entries",
    "pool_path",
    "recover_from_pool",
    "recover_peer",
]
