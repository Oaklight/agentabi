"""Claude Code settings adapters (native, TUI, SDK)."""

from __future__ import annotations

import base64
import json

from ...types.ir.task import TaskConfig
from ..base import Adapter, ResolvedSettings


def settings_json(base_url: str, api_key: str) -> str:
    """Build the ``--settings`` payload that pins Claude to an endpoint.

    The key is base64-encoded so a quote in the key cannot break the shell
    command inside ``apiKeyHelper``.

    Returns:
        JSON text for ``--settings``.
    """
    encoded = base64.b64encode(api_key.encode()).decode()
    return json.dumps(
        {
            "apiKeyHelper": f"echo {encoded} | base64 -d",
            "env": {
                "ANTHROPIC_BASE_URL": base_url,
                "CLAUDE_CODE_SKIP_ANTHROPIC_AUTH": "1",
            },
        }
    )


class ClaudeNativeSettings(Adapter):
    """Endpoint override for ``claude -p`` through ``--settings``."""

    name = "claude_native"

    def resolve(self, task: TaskConfig, *, resume: bool = False) -> ResolvedSettings:
        task_env = task.get("env") or {}
        base_url = task_env.get("ANTHROPIC_BASE_URL")
        api_key = task_env.get("ANTHROPIC_API_KEY")
        argv: tuple[str, ...] = ()
        if base_url and api_key:
            argv = ("--settings", settings_json(base_url, api_key))
        return ResolvedSettings(agent=self.agent, provider=self.name, argv=argv)


class ClaudeTuiSettings(Adapter):
    """Endpoint override for the interactive Claude transport.

    The TUI transport never read the endpoint variables; the adapter exists
    so the transport can gain the same behaviour without a second code path.
    """

    name = "claude_tui"


class ClaudeSdkSettings(Adapter):
    """SDK transport: nothing is claimed until the SDK is available."""

    name = "claude_sdk"


__all__ = [
    "ClaudeNativeSettings",
    "ClaudeSdkSettings",
    "ClaudeTuiSettings",
    "settings_json",
]
