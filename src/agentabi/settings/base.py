"""Adapter interface for translating canonical settings into CLI specifics."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import ExitStack
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from ..types.ir.task import TaskConfig
from .specs import SettingsSpec, spec_for

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
        sources: Which layer supplied each canonical field, for debugging.
        ignored: Fields dropped under ``on_unsupported="ignore"``.
    """

    agent: str
    provider: str
    argv: tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)
    task: TaskConfig | None = None
    sources: Mapping[str, str] = field(default_factory=dict)
    ignored: tuple[str, ...] = ()

    def task_or(self, task: TaskConfig) -> TaskConfig:
        """Return the rewritten task, or ``task`` when nothing changed."""
        return self.task if self.task is not None else task

    def redacted(self) -> dict[str, Any]:
        """Return a log-safe view that never renders secrets."""
        safe_argv = list(self.argv)
        for index, part in enumerate(safe_argv[:-1]):
            if part in ("--settings", "--api-key", "-c"):
                safe_argv[index + 1] = _REDACTED
        return {
            "agent": self.agent,
            "provider": self.provider,
            "argv": safe_argv,
            "env_keys": sorted(self.env),
            "sources": dict(self.sources),
            "ignored": list(self.ignored),
        }


@runtime_checkable
class SettingsAdapter(Protocol):
    """Translate canonical settings for one transport."""

    name: str
    spec: SettingsSpec

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
        return ResolvedSettings(agent=self.agent, provider=self.name)

    def materialize(
        self, resolved: ResolvedSettings, stack: ExitStack
    ) -> dict[str, str]:
        return {}


__all__ = [
    "Adapter",
    "ResolvedSettings",
    "SettingsAdapter",
]
