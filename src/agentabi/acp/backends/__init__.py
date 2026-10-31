"""Backend registry for the ACP gateway."""

from __future__ import annotations

from .base import Backend
from .opencode import OpenCodeBackend

BACKENDS: dict[str, type[Backend]] = {
    "opencode": OpenCodeBackend,
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
        backend_cls = BACKENDS[name]
    except KeyError as exc:
        supported = ", ".join(sorted(BACKENDS))
        raise ValueError(
            f"Unsupported ACP backend '{name}'. Supported: {supported}"
        ) from exc
    return backend_cls()


__all__ = [
    "BACKENDS",
    "Backend",
    "OpenCodeBackend",
    "create_backend",
]
