"""Pi settings adapter.

Pi has no endpoint flag and ignores ``OPENAI_BASE_URL``; a custom endpoint
only works through a ``models.json`` entry in Pi's agent directory.  Calls
that carry endpoint settings get a temporary agent directory for the run,
holding ``models.json`` and a copy of the user's ``settings.json``.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
from collections.abc import Mapping
from contextlib import ExitStack
from pathlib import Path
from typing import Any, cast

from ...types.ir.task import TaskConfig
from ..base import Adapter, ResolvedSettings
from ..errors import InvalidSettingsError

#: Directory variable Pi reads before falling back to ``~/.pi/agent``.
AGENT_DIR_ENV = "PI_CODING_AGENT_DIR"
#: Provider id used when settings do not name one.
DEFAULT_PROVIDER = "agentabi"


def _default_agent_dir() -> Path:
    """Return the agent directory Pi would use without an override."""
    override = os.environ.get(AGENT_DIR_ENV)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".pi" / "agent"


def models_config(
    base_url: str,
    api_key: str,
    provider: str,
    model: str | None,
    headers: Mapping[str, str] | None,
    native: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Build the ``models.json`` payload for one provider.

    Returns:
        A config mapping with one entry under ``providers``.
    """
    entry: dict[str, Any] = {
        "baseUrl": base_url,
        "api": "openai-completions",
        "apiKey": api_key,
    }
    if headers:
        entry["headers"] = dict(headers)
    entry["models"] = [{"id": model}] if model else []
    config: dict[str, Any] = {"providers": {provider: entry}}
    if native:
        config = _deep_merge(config, dict(native))
    return config


def _deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = dict(base)
    for key, value in override.items():
        existing = merged.get(key)
        if isinstance(existing, Mapping) and isinstance(value, Mapping):
            merged[key] = _deep_merge(existing, value)
        else:
            merged[key] = value
    return merged


class PiSettings(Adapter):
    """Settings are applied through a run-scoped agent directory."""

    name = "pi_native"

    def resolve(self, task: TaskConfig, *, resume: bool = False) -> ResolvedSettings:
        applied = self.apply(task)
        settings = applied.settings
        if not settings:
            return ResolvedSettings(agent=self.agent, provider=self.name)

        if not applied.base_url:
            # Settings without an endpoint cannot change the model catalog.
            if applied.api_key or settings.get("headers") or settings.get("native"):
                raise InvalidSettingsError(
                    "pi needs base_url when other settings fields are set",
                    field_path="settings.base_url",
                )
            return ResolvedSettings(
                agent=self.agent,
                provider=self.name,
                env=applied.env,
                task=applied.task if applied.task is not task else None,
                extra_args=applied.extra_args,
                ignored=applied.ignored,
            )

        if not applied.api_key:
            raise InvalidSettingsError(
                "pi requires a key for a custom endpoint", field_path="settings.api_key"
            )

        rewritten = applied.task
        provider = applied.model_provider or DEFAULT_PROVIDER
        model = rewritten.get("model")
        if model and "/" not in model:
            rewritten = {**rewritten, "model": f"{provider}/{model}"}

        return ResolvedSettings(
            agent=self.agent,
            provider=self.name,
            env=applied.env,
            task=cast(TaskConfig, rewritten),
            extra_args=applied.extra_args,
            ignored=applied.ignored,
            sources={
                "base_url": "settings",
                "api_key": "settings",
                "model_provider": "settings",
            },
        )

    def materialize(
        self, resolved: ResolvedSettings, stack: ExitStack
    ) -> dict[str, str]:
        """Create a private agent directory with ``models.json``.

        Returns:
            ``{AGENT_DIR_ENV: path}``, or ``{}`` when no endpoint was set.
        """
        task = resolved.task
        if task is None:
            return {}
        settings = task.get("settings") or {}
        if not settings:
            return {}

        applied = self.apply(task)
        if not applied.base_url or not applied.api_key:
            return {}
        base_url = applied.base_url
        api_key = applied.api_key
        provider = applied.model_provider or DEFAULT_PROVIDER
        model = task.get("model")
        if model and "/" in model:
            model = model.split("/", 1)[1]

        directory = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        os.chmod(directory, 0o700)

        source = _default_agent_dir()
        if os.environ.get(AGENT_DIR_ENV):
            source = Path(os.environ[AGENT_DIR_ENV]).expanduser()
        user_settings = source / "settings.json"
        if user_settings.is_file():
            target = directory / "settings.json"
            target.write_bytes(user_settings.read_bytes())
            os.chmod(target, 0o600)

        config = models_config(
            str(base_url),
            str(api_key),
            str(provider),
            model,
            settings.get("headers"),
            settings.get("native"),
        )
        models_path = directory / "models.json"
        models_path.write_text(json.dumps(config))
        os.chmod(models_path, stat.S_IRUSR | stat.S_IWUSR)

        return {AGENT_DIR_ENV: str(directory)}


__all__ = [
    "AGENT_DIR_ENV",
    "DEFAULT_PROVIDER",
    "PiSettings",
    "models_config",
]
