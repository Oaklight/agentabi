"""Errors raised while validating or applying agent settings."""

from __future__ import annotations


class SettingsError(ValueError):
    """Base class for settings problems.

    Subclasses ``ValueError`` so existing callers that catch ``ValueError``
    around task construction keep working.

    Args:
        message: Human-readable explanation.
        field_path: Dotted path of the offending field, for example
            ``settings.api_key``.
    """

    def __init__(self, message: str, *, field_path: str = "") -> None:
        self.field_path = field_path
        super().__init__(f"{field_path}: {message}" if field_path else message)


class InvalidSettingsError(SettingsError):
    """A settings value has the wrong shape or type."""


class UnsupportedSettingsError(SettingsError):
    """A transport cannot honour a settings field.

    Args:
        agent: Canonical agent name, for example ``codex``.
        provider: Transport name, for example ``codex_native``.
        field: The unsupported settings field.
        supported: Fields this transport does support.
    """

    def __init__(
        self,
        agent: str,
        provider: str,
        field: str,
        supported: frozenset[str],
    ) -> None:
        self.agent = agent
        self.provider = provider
        self.field = field
        self.supported = supported
        listed = ", ".join(sorted(supported)) or "none"
        super().__init__(
            f"the {agent} transport {provider} cannot honour this setting "
            f"(supported: {listed})",
            field_path=f"settings.{field}",
        )


__all__ = [
    "InvalidSettingsError",
    "SettingsError",
    "UnsupportedSettingsError",
]
