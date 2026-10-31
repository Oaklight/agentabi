"""Backend registry for the ACP gateway."""

from __future__ import annotations

from collections.abc import Callable

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


def create_backend(name: str) -> Backend:
    """Create a backend by agent name.

    Args:
        name: Agent name (for example ``"opencode"``).

    Returns:
        A new backend instance.

    Raises:
        ValueError: If the agent is not supported yet.
    """
    try:
        backend_factory = BACKENDS[name]
    except KeyError as exc:
        supported = ", ".join(sorted(BACKENDS))
        raise ValueError(
            f"Unsupported ACP backend '{name}'. Supported: {supported}"
        ) from exc
    return backend_factory()


__all__ = [
    "BACKENDS",
    "Backend",
    "IRBackend",
    "OpenCodeBackend",
    "create_backend",
]
