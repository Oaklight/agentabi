"""
agentabi - IR Agent Settings

Canonical, transport-neutral settings for pointing an agent CLI at a model
endpoint.  Each transport translates these fields into its own native
mechanism; see :mod:`agentabi.settings`.
"""

from typing import Any, Literal

from typing_extensions import NotRequired, TypedDict


class AgentSettings(TypedDict):
    """Settings shared by every agent CLI.

    Only the fields a transport supports are honoured; the rest raise
    :class:`agentabi.settings.UnsupportedSettingsError` unless
    ``on_unsupported`` is ``"ignore"``.

    Fields:
        base_url: Model endpoint, absolute ``http(s)`` URL.
        api_key: Literal key.  Prefer ``api_key_env`` so the key does not
            live in the task object.
        api_key_env: Name of an environment variable holding the key.
        model_provider: Provider id used by codex, opencode, and pi.
        headers: Extra HTTP headers for the model endpoint.
        env: Extra environment variables for the child process.
        args: Extra CLI arguments, inserted after generated flags and
            before the prompt.
        native: CLI-specific configuration block.  Its schema belongs to
            the transport's adapter.
        on_unsupported: ``"error"`` (default) raises; ``"ignore"`` drops
            unsupported fields with a warning.
    """

    base_url: NotRequired[str]
    api_key: NotRequired[str]
    api_key_env: NotRequired[str]
    model_provider: NotRequired[str]
    headers: NotRequired[dict[str, str]]
    env: NotRequired[dict[str, str]]
    args: NotRequired[list[str]]
    native: NotRequired[dict[str, Any]]
    on_unsupported: NotRequired[Literal["error", "ignore"]]


__all__ = ["AgentSettings"]
