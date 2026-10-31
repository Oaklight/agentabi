"""Backend registry for the ACP gateway."""

from __future__ import annotations

import shlex
from collections.abc import Callable

from .acp_server import ACPBackend
from .base import Backend
from .ir import IRBackend
from .opencode import OpenCodeBackend

BACKENDS: dict[str, Callable[[], Backend]] = {
    "opencode": OpenCodeBackend,
    "agy": lambda: IRBackend("agy"),
    "claude": lambda: IRBackend("claude_code"),
    "codex": lambda: IRBackend("codex"),
    "pi": lambda: IRBackend("pi"),
}


def create_backend(name: str, *, acp_command: str | None = None) -> Backend:
    """Create a backend by agent name.

    Args:
        name: Agent name (for example ``"opencode"``), or ``"acp"`` to run
            an arbitrary ACP server process.
        acp_command: Command line of the ACP server; required for ``"acp"``.

    Returns:
        A new backend instance.

    Raises:
        ValueError: If the agent is not supported, or ``"acp"`` is used
            without a command.
    """
    if name == "acp":
        if not acp_command:
            raise ValueError("the 'acp' backend requires a command")
        return ACPBackend(shlex.split(acp_command), name="acp")
    try:
        backend_factory = BACKENDS[name]
    except KeyError as exc:
        supported = ", ".join(sorted([*BACKENDS, "acp"]))
        raise ValueError(
            f"Unsupported ACP backend '{name}'. Supported: {supported}"
        ) from exc
    return backend_factory()


__all__ = [
    "ACPBackend",
    "BACKENDS",
    "Backend",
    "IRBackend",
    "OpenCodeBackend",
    "create_backend",
]
