"""Codex settings adapters (native exec/resume, SDK)."""

from __future__ import annotations

from ...types.ir.task import TaskConfig
from ..base import Adapter, ResolvedSettings


def provider_overrides(task: TaskConfig) -> list[str]:
    """Return ``-c`` overrides that point codex at a custom provider.

    Codex ignores ``OPENAI_BASE_URL`` in favour of its config file, so the
    provider block has to be rewritten through ``-c``.  Both ``codex exec``
    and ``codex exec resume`` accept the same overrides.

    Returns:
        Flag list, empty when the task sets no custom endpoint.
    """
    task_env = task.get("env") or {}
    base_url = task_env.get("OPENAI_BASE_URL")
    if not base_url:
        return []
    api_key = task_env.get("OPENAI_API_KEY")
    provider = task_env.get("CODEX_PROVIDER", "openai")
    flags = [
        "-c",
        f"model_provider={provider}",
        "-c",
        f"model_providers.{provider}.base_url={base_url}",
    ]
    if api_key:
        flags.extend(["-c", f"model_providers.{provider}.api_key={api_key}"])
    return flags


class CodexNativeSettings(Adapter):
    """Endpoint overrides for ``codex exec`` and ``codex exec resume``."""

    name = "codex_native"

    def resolve(self, task: TaskConfig, *, resume: bool = False) -> ResolvedSettings:
        return ResolvedSettings(
            agent=self.agent,
            provider=self.name,
            argv=tuple(provider_overrides(task)),
        )


class CodexSdkSettings(Adapter):
    """SDK transport: nothing is claimed until the SDK is available."""

    name = "codex_sdk"


__all__ = [
    "CodexNativeSettings",
    "CodexSdkSettings",
    "provider_overrides",
]
