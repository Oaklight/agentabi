"""Antigravity (agy) settings adapter."""

from __future__ import annotations

import json
from typing import Any, cast

from ...types.ir.task import TaskConfig
from ..base import Adapter, ResolvedSettings
from ..errors import InvalidSettingsError
from ..resolve import deep_merge


def endpoint_env(task: TaskConfig) -> dict[str, str]:
    """Map the generic endpoint variables to agy's Gemini names.

    The guards inspect the task env only, so a variable inherited from the
    process environment can still be overridden.

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


def extension_settings(task: TaskConfig) -> dict[str, Any] | str | None:
    """Return ``agent_extensions.agy.settings`` as a dict or file path."""
    ext = task.get("agent_extensions") or {}
    agy_ext = ext.get("agy")
    if not isinstance(agy_ext, dict):
        return None
    settings = agy_ext.get("settings")
    if settings is None or isinstance(settings, (dict, str)):
        return settings
    raise InvalidSettingsError(
        "agent_extensions.agy.settings must be a dict or a JSON string",
        field_path="agent_extensions.agy.settings",
    )


class AgySettings(Adapter):
    """Endpoint bridge plus a ``--settings`` passthrough."""

    name = "agy"

    def resolve(self, task: TaskConfig, *, resume: bool = False) -> ResolvedSettings:
        applied = self.apply(
            task,
            base_url_var="OPENAI_BASE_URL",
            api_key_var="OPENAI_API_KEY",
        )
        rewritten = applied.task
        env = dict(endpoint_env(rewritten))
        env.update(applied.env)

        argv: tuple[str, ...] = ()
        names_model = False
        legacy = extension_settings(rewritten)
        native = applied.settings.get("native") or {}
        if isinstance(legacy, str):
            if native:
                raise InvalidSettingsError(
                    "settings.native cannot be merged with a settings file path",
                    field_path="settings.native",
                )
            argv = ("--settings", legacy)
        else:
            merged = deep_merge(legacy or {}, native)
            if merged:
                argv = ("--settings", json.dumps(merged))
                names_model = "model" in merged

        if names_model and "model" in rewritten:
            rewritten = {
                key: value for key, value in rewritten.items() if key != "model"
            }

        rewritten_task: TaskConfig | None = (
            cast(TaskConfig, rewritten) if rewritten is not task else None
        )
        return ResolvedSettings(
            agent=self.agent,
            provider=self.name,
            argv=argv,
            env=env,
            task=rewritten_task,
            extra_args=applied.extra_args,
            ignored=applied.ignored,
        )


__all__ = ["AgySettings", "endpoint_env", "extension_settings"]
