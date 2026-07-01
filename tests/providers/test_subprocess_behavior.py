"""Tests for cross-cutting subprocess behaviors in native providers.

Tests stderr capture, exit code checking, and timeout enforcement
using real subprocesses (simple shell commands, not actual CLIs).
"""

import sys

import pytest

from agentabi.providers.claude_native import ClaudeNativeProvider
from agentabi.providers.codex_native import CodexNativeProvider
from agentabi.providers.gemini_native import GeminiNativeProvider
from agentabi.providers.opencode_native import OpenCodeNativeProvider


async def _collect_events(provider, task):
    """Collect all IR events from a provider stream."""
    events = []
    async for event in provider.stream(task):
        events.append(event)
    return events


def _error_events(events):
    """Filter error events from an event list."""
    return [e for e in events if e.get("type") == "error"]


# ---------------------------------------------------------------------------
# Helpers to create providers that run arbitrary commands instead of real CLIs
# ---------------------------------------------------------------------------


def _make_provider_with_cmd(provider_cls, cmd_list):
    """Patch _build_command to return an arbitrary shell command."""
    provider = provider_cls()
    provider._build_command = staticmethod(lambda task: cmd_list)
    return provider


# ---------------------------------------------------------------------------
# stderr capture tests
# ---------------------------------------------------------------------------


class TestStderrCapture:
    """All native providers should report stderr as ErrorEvent."""

    @pytest.mark.parametrize(
        "provider_cls",
        [
            ClaudeNativeProvider,
            CodexNativeProvider,
            GeminiNativeProvider,
            OpenCodeNativeProvider,
        ],
    )
    async def test_stderr_emitted_as_error(self, provider_cls):
        """Non-empty stderr should produce an ErrorEvent."""
        # Command that writes to stderr and exits 0
        provider = _make_provider_with_cmd(
            provider_cls,
            [sys.executable, "-c", "import sys; sys.stderr.write('oops\\n')"],
        )
        events = await _collect_events(provider, {"prompt": "test"})
        errors = _error_events(events)
        assert len(errors) >= 1
        assert "oops" in errors[0]["error"]

    @pytest.mark.parametrize(
        "provider_cls",
        [
            ClaudeNativeProvider,
            CodexNativeProvider,
            GeminiNativeProvider,
            OpenCodeNativeProvider,
        ],
    )
    async def test_no_error_on_empty_stderr(self, provider_cls):
        """Empty stderr should not produce an ErrorEvent."""
        provider = _make_provider_with_cmd(
            provider_cls,
            [sys.executable, "-c", "pass"],
        )
        events = await _collect_events(provider, {"prompt": "test"})
        errors = _error_events(events)
        assert len(errors) == 0


# ---------------------------------------------------------------------------
# Exit code tests
# ---------------------------------------------------------------------------


class TestExitCode:
    """All native providers should report non-zero exit codes."""

    @pytest.mark.parametrize(
        "provider_cls",
        [
            ClaudeNativeProvider,
            CodexNativeProvider,
            GeminiNativeProvider,
            OpenCodeNativeProvider,
        ],
    )
    async def test_nonzero_exit_emits_error(self, provider_cls):
        """Non-zero exit code should produce an ErrorEvent."""
        provider = _make_provider_with_cmd(
            provider_cls,
            [sys.executable, "-c", "raise SystemExit(42)"],
        )
        events = await _collect_events(provider, {"prompt": "test"})
        errors = _error_events(events)
        assert len(errors) >= 1
        assert any("42" in e["error"] for e in errors)

    @pytest.mark.parametrize(
        "provider_cls",
        [
            ClaudeNativeProvider,
            CodexNativeProvider,
            GeminiNativeProvider,
            OpenCodeNativeProvider,
        ],
    )
    async def test_zero_exit_no_error(self, provider_cls):
        """Exit code 0 should not produce an exit-code ErrorEvent."""
        provider = _make_provider_with_cmd(
            provider_cls,
            [sys.executable, "-c", "pass"],
        )
        events = await _collect_events(provider, {"prompt": "test"})
        errors = _error_events(events)
        assert len(errors) == 0

    @pytest.mark.parametrize(
        "provider_cls",
        [
            ClaudeNativeProvider,
            CodexNativeProvider,
            GeminiNativeProvider,
            OpenCodeNativeProvider,
        ],
    )
    async def test_stderr_and_nonzero_exit(self, provider_cls):
        """Stderr + non-zero exit: stderr error emitted, no dup."""
        provider = _make_provider_with_cmd(
            provider_cls,
            [
                sys.executable,
                "-c",
                "import sys; sys.stderr.write('fail msg\\n'); sys.exit(1)",
            ],
        )
        events = await _collect_events(provider, {"prompt": "test"})
        errors = _error_events(events)
        # At least one error about the stderr content
        assert any("fail msg" in e["error"] for e in errors)


# ---------------------------------------------------------------------------
# Timeout tests
# ---------------------------------------------------------------------------


class TestTimeout:
    """All native providers should enforce TaskConfig.timeout."""

    @pytest.mark.parametrize(
        "provider_cls",
        [
            ClaudeNativeProvider,
            CodexNativeProvider,
            GeminiNativeProvider,
            OpenCodeNativeProvider,
        ],
    )
    async def test_timeout_kills_process(self, provider_cls):
        """Process exceeding timeout should be killed and produce a timeout error."""
        provider = _make_provider_with_cmd(
            provider_cls,
            [sys.executable, "-c", "import time; time.sleep(60)"],
        )
        events = await _collect_events(provider, {"prompt": "test", "timeout": 0.5})
        errors = _error_events(events)
        assert len(errors) >= 1
        assert any("timed out" in e["error"].lower() for e in errors)
        assert any(e.get("is_fatal") for e in errors)

    @pytest.mark.parametrize(
        "provider_cls",
        [
            ClaudeNativeProvider,
            CodexNativeProvider,
            GeminiNativeProvider,
            OpenCodeNativeProvider,
        ],
    )
    async def test_timeout_with_stderr(self, provider_cls):
        """Timeout + non-empty stderr: both errors should be emitted."""
        provider = _make_provider_with_cmd(
            provider_cls,
            [
                sys.executable,
                "-c",
                "import sys, time; sys.stderr.write('partial error\\n'); "
                "sys.stderr.flush(); time.sleep(60)",
            ],
        )
        events = await _collect_events(provider, {"prompt": "test", "timeout": 0.5})
        errors = _error_events(events)
        assert any("partial error" in e["error"] for e in errors)
        assert any("timed out" in e.get("error", "").lower() for e in errors)

    @pytest.mark.parametrize(
        "provider_cls",
        [
            ClaudeNativeProvider,
            CodexNativeProvider,
            GeminiNativeProvider,
            OpenCodeNativeProvider,
        ],
    )
    async def test_no_timeout_when_fast(self, provider_cls):
        """Fast-completing process with timeout set should not trigger timeout."""
        provider = _make_provider_with_cmd(
            provider_cls,
            [sys.executable, "-c", "pass"],
        )
        events = await _collect_events(provider, {"prompt": "test", "timeout": 10.0})
        errors = _error_events(events)
        timeout_errors = [
            e for e in errors if "timed out" in e.get("error", "").lower()
        ]
        assert len(timeout_errors) == 0
