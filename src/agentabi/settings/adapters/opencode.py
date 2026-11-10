"""OpenCode settings adapters (V1 subprocess, V2 HTTP)."""

from __future__ import annotations

from ...types.ir.task import TaskConfig
from ..base import Adapter, ResolvedSettings


def qualified_model(task: TaskConfig) -> str:
    """Return the model label to pass to the V1 CLI.

    When the task points at an OpenAI-compatible endpoint with a bare model
    name, the CLI needs the ``openai/`` provider prefix to route there.

    Returns:
        ``provider/model`` or the original value.
    """
    model = task.get("model", "")
    task_env = task.get("env") or {}
    if model and task_env.get("OPENAI_BASE_URL") and "/" not in model:
        return f"openai/{model}"
    return model


class OpenCodeV1Settings(Adapter):
    """Model qualification for ``opencode run``."""

    name = "opencode_native"

    def resolve(self, task: TaskConfig, *, resume: bool = False) -> ResolvedSettings:
        model = qualified_model(task)
        rewritten: TaskConfig | None = None
        if model != task.get("model", ""):
            rewritten = {**task, "model": model}
        return ResolvedSettings(
            agent=self.agent,
            provider=self.name,
            task=rewritten,
        )


class OpenCodeV2Settings(Adapter):
    """V2 settings policy.

    The V2 server builds its model catalog from the user's own OpenCode
    config; a provider injected through the environment is not in it.  The
    provider therefore routes endpoint-bearing tasks to V1.
    """

    name = "opencode_v2"


__all__ = ["OpenCodeV1Settings", "OpenCodeV2Settings", "qualified_model"]
