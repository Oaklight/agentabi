"""Adapter interface for translating canonical settings into CLI specifics."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from contextlib import ExitStack
from dataclasses import dataclass, field
from typing import Any, Protocol, cast, runtime_checkable

from ..types.ir.settings import AgentSettings
from ..types.ir.task import TaskConfig
from .errors import InvalidSettingsError, UnsupportedSettingsError
from .resolve import validate_settings
from .specs import SettingsSpec, spec_for

logger = logging.getLogger("agentabi.settings")

_REDACTED = "<redacted>"


@dataclass(frozen=True)
class ResolvedSettings:
    """What one adapter produced for a single run.

    Args:
        agent: Canonical agent name.
        provider: Transport name, the registry key.
        argv: Additional arguments, inserted after the provider's own flags
            and before the prompt.
        env: Environment overrides, applied after the task's own env.
        task: A rewritten task when the adapter had to change a field, for
            example dropping ``model`` because a native settings object
            names it.
        extra_args: ``settings.args``, inserted immediately before the
            prompt by the provider.
        sources: Which layer supplied each canonical field, for debugging.
        ignored: Fields dropped under ``on_unsupported="ignore"``.
    """

    agent: str
    provider: str
    argv: tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)
    task: TaskConfig | None = None
    extra_args: tuple[str, ...] = ()
    sources: Mapping[str, str] = field(default_factory=dict)
    ignored: tuple[str, ...] = ()

    def task_or(self, task: Mapping[str, Any]) -> TaskConfig:
        """Return the rewritten task, or ``task`` when nothing changed."""
        return cast(TaskConfig, self.task if self.task is not None else task)

    def redacted(self) -> dict[str, Any]:
        """Return a log-safe view that never renders secrets."""
        safe_argv = list(self.argv)
        for index, part in enumerate(safe_argv[:-1]):
            if part in (
                "--settings",
                "--api-key",
                "-c",
                "-H",
                "--header",
            ):
                safe_argv[index + 1] = _REDACTED
        return {
            "agent": self.agent,
            "provider": self.provider,
            "argv": safe_argv,
            "extra_args": list(self.extra_args),
            "env_keys": sorted(self.env),
            "sources": dict(self.sources),
            "ignored": list(self.ignored),
        }


@dataclass(frozen=True)
class AppliedSettings:
    """Settings after validation, precedence, and unsupported fields.

    Args:
        task: Task with ``settings.env`` and the resolved endpoint written
            into ``env``.
        settings: The honoured settings mapping.
        base_url: Effective endpoint, from settings, native, or task env.
        api_key: Effective literal key, or the value named by
            ``api_key_env``.
        model_provider: Effective provider id.
        extra_args: ``settings.args`` to insert before the prompt.
        ignored: Fields dropped under ``on_unsupported="ignore"``.
    """

    task: TaskConfig
    settings: AgentSettings
    base_url: str | None = None
    api_key: str | None = None
    model_provider: str | None = None
    extra_args: tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)
    ignored: tuple[str, ...] = ()


@runtime_checkable
class SettingsAdapter(Protocol):
    """Translate canonical settings for one transport."""

    name: str
    spec: SettingsSpec
    agent: str
    transport: str
    mechanism: str
    supported: frozenset[str]

    def resolve(self, task: TaskConfig, *, resume: bool = False) -> ResolvedSettings:
        """Validate and translate settings without touching the filesystem."""
        ...

    def materialize(
        self, resolved: ResolvedSettings, stack: ExitStack
    ) -> dict[str, str]:
        """Create run-scoped files and return the env that points at them."""
        ...


class Adapter:
    """Base adapter: zero overrides, empty result.

    Subclasses set ``name`` and override what they need.  The spec table
    supplies the rest, so a CLI difference lives in exactly one place.
    """

    name: str = ""

    @property
    def spec(self) -> SettingsSpec:
        return spec_for(self.name)

    @property
    def agent(self) -> str:
        return self.spec.agent

    @property
    def transport(self) -> str:
        return self.spec.transport

    @property
    def mechanism(self) -> str:
        return self.spec.mechanism

    @property
    def supported(self) -> frozenset[str]:
        return self.spec.supported

    def resolve(self, task: TaskConfig, *, resume: bool = False) -> ResolvedSettings:
        applied = self.apply(task)
        return ResolvedSettings(
            agent=self.agent,
            provider=self.name,
            env=applied.env,
            task=applied.task if applied.task is not task else None,
            extra_args=applied.extra_args,
            ignored=applied.ignored,
        )

    def materialize(
        self, resolved: ResolvedSettings, stack: ExitStack
    ) -> dict[str, str]:
        return {}

    def apply(
        self,
        task: TaskConfig,
        *,
        base_url_var: str | None = None,
        api_key_var: str | None = None,
        provider_var: str | None = None,
    ) -> AppliedSettings:
        """Validate settings and fold the endpoint into the task env.

        Precedence: explicit settings, then ``settings.native``, then the
        existing task env.  ``settings.env`` is applied last, so an escape
        hatch can override an inferred variable.

        Args:
            task: Input task.
            base_url_var: Env variable the transport reads for the endpoint.
            api_key_var: Env variable the transport reads for the key.
            provider_var: Env variable the transport reads for the provider id.

        Returns:
            The applied view and a task whose ``env`` carries the values.

        Raises:
            InvalidSettingsError: On a malformed settings value or an
                ``api_key_env`` variable that is not set.
            UnsupportedSettingsError: When a field is unsupported and
                ``on_unsupported`` is not ``"ignore"``.
        """
        raw: Any = task.get("settings")
        if raw is None:
            return AppliedSettings(task=task, settings={})

        validate_settings(raw)
        settings, ignored = self._honoured(dict(raw))

        task_env = dict(task.get("env") or {})
        native = settings.get("native") or {}

        base_url = settings.get("base_url") or native.get("base_url")
        if base_url is None and base_url_var:
            base_url = task_env.get(base_url_var)
        model_provider = settings.get("model_provider") or native.get("model_provider")
        if model_provider is None and provider_var:
            model_provider = task_env.get(provider_var)
        api_key = self._resolve_key(settings, task_env)
        if api_key is None and api_key_var:
            api_key = task_env.get(api_key_var)

        updated = dict(task_env)
        if base_url and base_url_var:
            updated[base_url_var] = base_url
        if api_key and api_key_var:
            updated[api_key_var] = api_key
        if model_provider and provider_var:
            updated[provider_var] = model_provider
        updated.update(settings.get("env") or {})

        rewritten = task if updated == task_env else {**task, "env": updated}
        delta = {
            key: value for key, value in updated.items() if task_env.get(key) != value
        }
        return AppliedSettings(
            task=rewritten,
            settings=settings,
            base_url=base_url,
            api_key=api_key,
            model_provider=model_provider,
            extra_args=tuple(settings.get("args") or ()),
            env=delta,
            ignored=ignored,
        )

    def _honoured(
        self, settings: dict[str, Any]
    ) -> tuple[AgentSettings, tuple[str, ...]]:
        """Drop or reject settings fields this transport cannot honour."""
        unsupported = sorted(set(settings) - {"on_unsupported"} - self.supported)
        if not unsupported:
            return cast(AgentSettings, settings), ()
        if settings.get("on_unsupported") == "ignore":
            logger.warning(
                "ignoring unsupported settings for %s (%s): %s",
                self.name,
                self.agent,
                ", ".join(unsupported),
            )
            kept = {
                key: value for key, value in settings.items() if key not in unsupported
            }
            return cast(AgentSettings, kept), tuple(unsupported)
        raise UnsupportedSettingsError(
            self.agent, self.name, unsupported[0], self.supported
        )

    @staticmethod
    def _resolve_key(
        settings: Mapping[str, Any], task_env: Mapping[str, str]
    ) -> str | None:
        """Return the literal key, resolving ``api_key_env`` if needed."""
        literal = settings.get("api_key")
        if literal:
            return str(literal)
        name = settings.get("api_key_env")
        if not name:
            return None
        extra = settings.get("env") or {}
        value = extra.get(name) or task_env.get(name) or os.environ.get(name)
        if not value:
            raise InvalidSettingsError(
                f"environment variable {name} is not set",
                field_path="settings.api_key_env",
            )
        return value


__all__ = [
    "Adapter",
    "AppliedSettings",
    "ResolvedSettings",
    "SettingsAdapter",
]
