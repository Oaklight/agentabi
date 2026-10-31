"""OpenCode backend: pass ACP through to ``opencode acp``."""

from __future__ import annotations

from .acp_server import ACPBackend


class OpenCodeBackend(ACPBackend):
    """Backend that delegates to OpenCode's native ACP server."""

    name = "opencode"

    def __init__(self) -> None:
        super().__init__(["opencode", "acp"], name="opencode")


__all__ = ["OpenCodeBackend"]
