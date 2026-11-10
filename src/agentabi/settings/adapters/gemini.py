"""Gemini CLI settings adapters (deprecated native provider, SDK)."""

from __future__ import annotations

from ...types.ir.task import TaskConfig
from ..base import Adapter, ResolvedSettings


def endpoint_env(task: TaskConfig) -> dict[str, str]:
    """Map the generic endpoint variables to Gemini's names.

    Returns:
        Environment overrides, empty when the task sets nothing.
    """
    task_env = task.get("env") or {}
    mapped: dict[str, str] = {}
    if not task_env.get("GOOGLE_GEMINI_BASE_URL") and task_env.get("OPENAI_BASE_URL"):
        base = task_env["OPENAI_BASE_URL"].rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3]
        mapped["GOOGLE_GEMINI_BASE_URL"] = base
    if not task_env.get("GEMINI_API_KEY") and task_env.get("OPENAI_API_KEY"):
        mapped["GEMINI_API_KEY"] = task_env["OPENAI_API_KEY"]
    return mapped


class GeminiNativeSettings(Adapter):
    """Environment bridge for the deprecated Gemini CLI."""

    name = "gemini_native"

    def resolve(self, task: TaskConfig, *, resume: bool = False) -> ResolvedSettings:
        return ResolvedSettings(
            agent=self.agent,
            provider=self.name,
            env=endpoint_env(task),
        )


class GeminiSdkSettings(Adapter):
    """SDK transport: nothing is claimed until the SDK is available."""

    name = "gemini_sdk"


__all__ = ["GeminiNativeSettings", "GeminiSdkSettings", "endpoint_env"]
