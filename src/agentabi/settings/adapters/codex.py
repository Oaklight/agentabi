"""Codex settings adapters (native exec/resume, SDK)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from ...types.ir.task import TaskConfig
from ..base import Adapter, ResolvedSettings

#: ``native`` keys that are canonical aliases, not codex config keys.
_CANONICAL_KEYS = {"base_url", "model_provider", "api_key", "api_key_env"}
#: Child-env variable that carries the key when codex resolves it by name.
KEY_ENV = "AGENTABI_CODEX_API_KEY"


def provider_overrides(task: TaskConfig) -> list[str]:
    """Return ``-c`` overrides that point codex at a custom provider.

    Codex ignores ``OPENAI_BASE_URL`` in favour of its config file, so the
    provider block has to be rewritten through ``-c``.  Both ``codex exec``
    and ``codex exec resume`` accept the same overrides.

    The key is referenced as ``env_key``, never written into argv: codex
    0.162.1 rejects ``api_key`` and logs "unrecognized configuration
    setting" for it.  The value travels in ``AGENTABI_CODEX_API_KEY``.

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
        flags.extend(["-c", f'model_providers.{provider}.env_key="{KEY_ENV}"'])
    return flags


def toml_literal(value: Any) -> str:
    """Render a Python value as a TOML literal.

    Strings are JSON-quoted, booleans use TOML spelling, lists become
    inline arrays, and mappings become inline tables.

    Returns:
        TOML text for the value.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(toml_literal(item) for item in value) + "]"
    if isinstance(value, Mapping):
        pairs = (
            f"{json.dumps(str(key))} = {toml_literal(item)}"
            for key, item in value.items()
        )
        return "{" + ", ".join(pairs) + "}"
    return json.dumps(str(value))


class CodexNativeSettings(Adapter):
    """Endpoint overrides for ``codex exec`` and ``codex exec resume``."""

    name = "codex_native"

    def resolve(self, task: TaskConfig, *, resume: bool = False) -> ResolvedSettings:
        applied = self.apply(
            task,
            base_url_var="OPENAI_BASE_URL",
            api_key_var="OPENAI_API_KEY",
            provider_var="CODEX_PROVIDER",
        )
        rewritten_task = applied.task
        argv = provider_overrides(rewritten_task)
        settings = applied.settings
        provider = applied.model_provider or (rewritten_task.get("env") or {}).get(
            "CODEX_PROVIDER", "openai"
        )

        headers = settings.get("headers") or {}
        if applied.base_url and headers:
            argv.extend(
                [
                    "-c",
                    f"model_providers.{provider}.http_headers={toml_literal(headers)}",
                ]
            )

        for key, value in (settings.get("native") or {}).items():
            if key in _CANONICAL_KEYS:
                continue
            argv.extend(["-c", f"{key}={toml_literal(value)}"])

        env = dict(applied.env)
        if applied.api_key and applied.base_url:
            env[KEY_ENV] = applied.api_key

        return ResolvedSettings(
            agent=self.agent,
            provider=self.name,
            argv=tuple(argv),
            env=env,
            task=rewritten_task if rewritten_task is not task else None,
            extra_args=applied.extra_args,
            ignored=applied.ignored,
        )


class CodexSdkSettings(Adapter):
    """SDK transport: nothing is claimed until the SDK is available."""

    name = "codex_sdk"


__all__ = [
    "KEY_ENV",
    "CodexNativeSettings",
    "CodexSdkSettings",
    "provider_overrides",
    "toml_literal",
]
