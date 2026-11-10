"""OpenCode settings adapters (V1 subprocess, V2 HTTP)."""

from __future__ import annotations

import json
from typing import Any, cast

from ...types.ir.task import TaskConfig
from ..base import Adapter, ResolvedSettings
from ..errors import InvalidSettingsError
from ..resolve import deep_merge

#: Config variable the V1 CLI reads before it starts.
CONFIG_ENV = "OPENCODE_CONFIG_CONTENT"
#: Provider id used when settings do not name one.
DEFAULT_PROVIDER = "agentabi"
#: npm package that speaks the OpenAI-compatible protocol.
COMPATIBLE_NPM = "@ai-sdk/openai-compatible"


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


def existing_config(task: TaskConfig) -> dict[str, Any]:
    """Return the inline config the caller already set, if any.

    Raises:
        InvalidSettingsError: If the existing value is not a JSON object.
    """
    task_env = task.get("env") or {}
    raw = task_env.get(CONFIG_ENV)
    if raw is None:
        return {}
    try:
        config = json.loads(raw)
    except json.JSONDecodeError:
        raise InvalidSettingsError(
            f"existing {CONFIG_ENV} is not valid JSON",
            field_path=f"env.{CONFIG_ENV}",
        ) from None
    if not isinstance(config, dict):
        raise InvalidSettingsError(
            f"existing {CONFIG_ENV} must be a JSON object",
            field_path=f"env.{CONFIG_ENV}",
        )
    return config


class OpenCodeV1Settings(Adapter):
    """Provider block for ``opencode run`` through the inline config."""

    name = "opencode_native"

    def resolve(self, task: TaskConfig, *, resume: bool = False) -> ResolvedSettings:
        applied = self.apply(task)
        rewritten = applied.task
        settings = applied.settings
        headers = settings.get("headers") or {}
        native = settings.get("native") or {}
        builds_config = bool(applied.base_url or applied.api_key or headers or native)
        env: dict[str, str] = dict(applied.env)

        if builds_config:
            provider = applied.model_provider or DEFAULT_PROVIDER
            model = rewritten.get("model", "")

            block: dict[str, Any] = {"npm": COMPATIBLE_NPM, "name": provider}
            options: dict[str, Any] = {}
            if applied.base_url:
                options["baseURL"] = applied.base_url
            if applied.api_key:
                options["apiKey"] = applied.api_key
            if headers:
                options["headers"] = dict(headers)
            if options:
                block["options"] = options
            if model and "/" not in model:
                block["models"] = {model: {"name": model}}

            merged = deep_merge(existing_config(rewritten), native)
            merged = deep_merge(merged, {"provider": {provider: block}})

            task_env = dict(rewritten.get("env") or {})
            task_env[CONFIG_ENV] = json.dumps(merged)
            rewritten = {**rewritten, "env": task_env}
            env[CONFIG_ENV] = task_env[CONFIG_ENV]
            if model and "/" not in model:
                rewritten = {**rewritten, "model": f"{provider}/{model}"}

        if not builds_config:
            # Legacy path: a bare model plus OPENAI_BASE_URL needs the
            # built-in openai provider prefix.
            model = qualified_model(cast(TaskConfig, rewritten))
            if model != rewritten.get("model", ""):
                rewritten = {**rewritten, "model": model}

        return ResolvedSettings(
            agent=self.agent,
            provider=self.name,
            env=env,
            task=(cast(TaskConfig, rewritten) if rewritten is not task else None),
            extra_args=applied.extra_args,
            ignored=applied.ignored,
        )


class OpenCodeV2Settings(Adapter):
    """V2 settings policy.

    The V2 server builds its model catalog from the user's own OpenCode
    config; a provider injected through the environment is not in it.  The
    provider therefore routes endpoint-bearing tasks to V1.
    """

    name = "opencode_v2"


__all__ = [
    "CONFIG_ENV",
    "DEFAULT_PROVIDER",
    "OpenCodeV1Settings",
    "OpenCodeV2Settings",
    "existing_config",
    "qualified_model",
]
