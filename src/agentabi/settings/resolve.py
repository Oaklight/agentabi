"""Validation and merging for :class:`AgentSettings`.

Validation runs at task construction and again in each adapter, so a task
handed straight to a provider gets the same checks.  Error messages name
the field path only; they never repeat a key or header value.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any, cast
from urllib.parse import urlsplit

from ..types.ir.settings import AgentSettings
from .errors import InvalidSettingsError

SETTINGS_FIELDS = frozenset(
    {
        "base_url",
        "api_key",
        "api_key_env",
        "model_provider",
        "headers",
        "env",
        "args",
        "native",
        "on_unsupported",
    }
)

_MERGE_AS_MAPPING = ("headers", "env", "native")
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_NATIVE_LIMIT = 100 * 1024


def _fail(message: str, field: str) -> InvalidSettingsError:
    return InvalidSettingsError(message, field_path=f"settings.{field}")


def _check_base_url(value: Any) -> None:
    if not isinstance(value, str) or not value:
        raise _fail("must be a non-empty string", "base_url")
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"}:
        raise _fail("must be an absolute http(s) URL", "base_url")
    if not parts.netloc:
        raise _fail("must include a host", "base_url")
    if "@" in parts.netloc:
        raise _fail("must not include userinfo; use api_key instead", "base_url")


def _check_string(value: Any, field: str) -> None:
    if not isinstance(value, str) or not value:
        raise _fail("must be a non-empty string", field)


def _check_mapping_of_strings(value: Any, field: str, *, no_newlines: bool) -> None:
    if not isinstance(value, Mapping):
        raise _fail("must be a mapping of strings to strings", field)
    for key, item in value.items():
        if not isinstance(key, str) or not key:
            raise _fail("keys must be non-empty strings", field)
        if "\x00" in key or (no_newlines and ("\r" in key or "\n" in key)):
            raise _fail("keys must not contain control characters", field)
        if not isinstance(item, str):
            raise _fail("values must be strings", field)
        if "\x00" in item or (no_newlines and ("\r" in item or "\n" in item)):
            raise _fail("values must not contain control characters", field)


def _check_base_url_field(settings: Mapping[str, Any]) -> None:
    _check_base_url(settings["base_url"])


def _check_api_key_field(settings: Mapping[str, Any]) -> None:
    _check_string(settings["api_key"], "api_key")


def _check_api_key_env_field(settings: Mapping[str, Any]) -> None:
    value = settings["api_key_env"]
    if not isinstance(value, str) or not _ENV_NAME.match(value):
        raise _fail("must be an environment variable name", "api_key_env")


def _check_model_provider_field(settings: Mapping[str, Any]) -> None:
    _check_string(settings["model_provider"], "model_provider")


def _check_headers_field(settings: Mapping[str, Any]) -> None:
    _check_mapping_of_strings(settings["headers"], "headers", no_newlines=True)


def _check_env_field(settings: Mapping[str, Any]) -> None:
    _check_mapping_of_strings(settings["env"], "env", no_newlines=False)


def _check_args_field(settings: Mapping[str, Any]) -> None:
    args = settings["args"]
    if not isinstance(args, (list, tuple)):
        raise _fail("must be a list of strings", "args")
    if not all(isinstance(arg, str) for arg in args):
        raise _fail("must be a list of strings", "args")


def _check_native_field(settings: Mapping[str, Any]) -> None:
    native = settings["native"]
    if not isinstance(native, Mapping):
        raise _fail("must be a mapping", "native")
    try:
        size = len(json.dumps(native, default=str))
    except (TypeError, ValueError):
        raise _fail("must be JSON-serializable", "native") from None
    if size > _NATIVE_LIMIT:
        raise _fail("must be at most 100 KiB when serialized", "native")


def _check_on_unsupported_field(settings: Mapping[str, Any]) -> None:
    if settings["on_unsupported"] not in {"error", "ignore"}:
        raise _fail('must be "error" or "ignore"', "on_unsupported")


_FIELD_CHECKS = {
    "base_url": _check_base_url_field,
    "api_key": _check_api_key_field,
    "api_key_env": _check_api_key_env_field,
    "model_provider": _check_model_provider_field,
    "headers": _check_headers_field,
    "env": _check_env_field,
    "args": _check_args_field,
    "native": _check_native_field,
    "on_unsupported": _check_on_unsupported_field,
}


def validate_settings(settings: Any) -> None:
    """Check the shape of a settings mapping.

    Args:
        settings: Value expected to be an :class:`AgentSettings` mapping.

    Raises:
        InvalidSettingsError: On an unknown key, a wrong type, a malformed
            URL, a header with a line break, or both key fields set.
    """
    if not isinstance(settings, Mapping):
        raise InvalidSettingsError("must be a mapping", field_path="settings")

    unknown = sorted(set(settings) - SETTINGS_FIELDS)
    if unknown:
        raise _fail(f"unknown key {unknown[0]!r}", unknown[0])

    for field, check in _FIELD_CHECKS.items():
        if field in settings:
            check(settings)

    if "api_key" in settings and "api_key_env" in settings:
        raise _fail("set api_key or api_key_env, not both", "api_key")


def merge_settings(
    base: Mapping[str, Any] | None,
    override: Mapping[str, Any] | None,
) -> AgentSettings:
    """Merge two settings mappings.

    ``headers``, ``env``, and ``native`` merge key by key; every other
    field is replaced.

    Returns:
        A new mapping; neither input is modified.
    """
    merged: dict[str, Any] = dict(base or {})
    for key, value in (override or {}).items():
        existing = merged.get(key)
        if (
            key in _MERGE_AS_MAPPING
            and isinstance(value, Mapping)
            and isinstance(existing, Mapping)
        ):
            merged[key] = {**existing, **value}
        else:
            merged[key] = value
    return cast(AgentSettings, merged)


def deep_merge(
    base: Mapping[str, Any] | None,
    override: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Merge two nested mappings recursively.

    Returns:
        A new mapping; neither input is modified.
    """
    merged: dict[str, Any] = dict(base or {})
    for key, value in (override or {}).items():
        existing = merged.get(key)
        if isinstance(existing, Mapping) and isinstance(value, Mapping):
            merged[key] = deep_merge(existing, value)
        else:
            merged[key] = value
    return merged


__all__ = [
    "SETTINGS_FIELDS",
    "deep_merge",
    "merge_settings",
    "validate_settings",
]
