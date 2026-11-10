"""Claude Code settings adapters (native, TUI, SDK)."""

from __future__ import annotations

import base64
import json
from typing import Any

from ...types.ir.settings import AgentSettings
from ...types.ir.task import TaskConfig
from ..base import Adapter, ResolvedSettings
from ..resolve import deep_merge

_KEY_HELPER_ENV = "AGENTABI_CLAUDE_API_KEY"


def settings_json(base_url: str, api_key: str) -> str:
    """Build the legacy ``--settings`` payload that pins Claude to an endpoint.

    The key is base64-encoded so a quote in the key cannot break the shell
    command inside ``apiKeyHelper``.  Callers that use the canonical
    ``settings`` field get :func:`canonical_settings_json` instead, which
    keeps the key out of argv.

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


def canonical_settings_json(base_url: str, native: dict[str, Any] | None = None) -> str:
    """Build a ``--settings`` payload that reads the key from the child env.

    ``native`` is merged over the generated object, so a caller can add
    Claude settings such as model or permission defaults.

    Returns:
        JSON text for ``--settings``.
    """
    payload: dict[str, Any] = {
        "apiKeyHelper": f"printenv {_KEY_HELPER_ENV}",
        "env": {
            "ANTHROPIC_BASE_URL": base_url,
            "CLAUDE_CODE_SKIP_ANTHROPIC_AUTH": "1",
        },
    }
    extra = {
        key: value
        for key, value in (native or {}).items()
        if key not in {"base_url", "model_provider", "api_key", "api_key_env"}
    }
    return json.dumps(deep_merge(payload, extra))


class _ClaudeSettings(Adapter):
    """Shared endpoint logic for the subprocess and TUI transports."""

    def resolve(self, task: TaskConfig, *, resume: bool = False) -> ResolvedSettings:
        applied = self.apply(
            task,
            base_url_var="ANTHROPIC_BASE_URL",
            api_key_var="ANTHROPIC_API_KEY",
        )
        settings: AgentSettings = applied.settings
        honoured = {key for key in settings if key != "on_unsupported"}
        argv: tuple[str, ...] = ()
        env: dict[str, str] = dict(applied.env)

        if honoured:
            if applied.base_url and applied.api_key:
                env[_KEY_HELPER_ENV] = applied.api_key
                argv = (
                    "--settings",
                    canonical_settings_json(applied.base_url, settings.get("native")),
                )
        else:
            task_env = applied.task.get("env") or {}
            base_url = task_env.get("ANTHROPIC_BASE_URL")
            api_key = task_env.get("ANTHROPIC_API_KEY")
            if base_url and api_key:
                argv = ("--settings", settings_json(base_url, api_key))

        rewritten = applied.task if applied.task is not task else None
        return ResolvedSettings(
            agent=self.agent,
            provider=self.name,
            argv=argv,
            env=env,
            task=rewritten,
            extra_args=applied.extra_args,
            ignored=applied.ignored,
        )


class ClaudeNativeSettings(_ClaudeSettings):
    """Endpoint override for ``claude -p`` through ``--settings``."""

    name = "claude_native"


class ClaudeTuiSettings(_ClaudeSettings):
    """Endpoint override for the interactive Claude transport."""

    name = "claude_tui"


class ClaudeSdkSettings(Adapter):
    """SDK transport: nothing is claimed until the SDK is available."""

    name = "claude_sdk"


__all__ = [
    "ClaudeNativeSettings",
    "ClaudeSdkSettings",
    "ClaudeTuiSettings",
    "canonical_settings_json",
    "settings_json",
]
