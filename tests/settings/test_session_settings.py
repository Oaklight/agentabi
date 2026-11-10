"""Session-level settings defaults and merging."""

from __future__ import annotations

from typing import cast
from unittest.mock import patch

import pytest

from agentabi import Session
from agentabi.settings import InvalidSettingsError
from agentabi.types.ir.settings import AgentSettings

BASE = "https://rosetta.test/v1"


@pytest.fixture(autouse=True)
def claude_available():
    """Make the native Claude provider resolvable without the CLI."""
    with patch("shutil.which", return_value="/usr/bin/claude"):
        yield


def _session(**kwargs) -> Session:
    return Session(agent="claude_code", **kwargs)


class TestSessionSettings:
    def test_no_settings_leaves_the_task_untouched(self) -> None:
        session = _session()
        assert "settings" not in session._build_task("hi")

    def test_session_defaults_are_copied_into_the_task(self) -> None:
        session = _session(settings={"base_url": BASE, "api_key": "k"})
        task = session._build_task("hi")
        assert task["settings"]["base_url"] == BASE
        assert session.settings == {"base_url": BASE, "api_key": "k"}

    def test_property_returns_a_copy(self) -> None:
        session = _session(settings={"base_url": BASE})
        session.settings["base_url"] = "https://other.test"
        assert session.settings["base_url"] == BASE

    def test_per_call_settings_merge_over_defaults(self) -> None:
        session = _session(
            settings={
                "base_url": BASE,
                "env": {"A": "1"},
                "headers": {"X-One": "1"},
            }
        )
        task = session._build_task(
            "hi",
            settings={
                "api_key": "k",
                "env": {"B": "2"},
                "headers": {"X-Two": "2"},
            },
        )
        merged = task["settings"]
        assert merged["base_url"] == BASE
        assert merged["api_key"] == "k"
        assert merged["env"] == {"A": "1", "B": "2"}
        assert merged["headers"] == {"X-One": "1", "X-Two": "2"}

    def test_invalid_defaults_are_rejected(self) -> None:
        with pytest.raises(InvalidSettingsError):
            _session(settings={"base_url": "not-a-url"})

    def test_invalid_per_call_settings_are_rejected(self) -> None:
        session = _session()
        with pytest.raises(InvalidSettingsError):
            session._build_task("hi", settings=cast(AgentSettings, {"unknown": 1}))
