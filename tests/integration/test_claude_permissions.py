"""Opt-in integration test for Claude permission forwarding.

Skipped when ``claude`` is not installed.  The provider spawns the binary
directly, so the ``AGENT_YOLO`` shell-function wrapper is not involved; the
variable is removed anyway for fidelity with the design's probe.

The IR stream does not expose Claude's ``result.permission_denials`` field,
so the deny case asserts the observable outcome (the marker file is absent)
and that the permission round trip happened.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from agentabi.providers.claude_native import ClaudeNativeProvider
from agentabi.types.ir.events import PermissionRequestEvent
from agentabi.types.ir.permissions import PermissionDecision
from agentabi.types.ir.task import TaskConfig

pytestmark = [pytest.mark.integration, pytest.mark.claude]

_CLAUDE = shutil.which("claude")
_SKIP = pytest.mark.skipif(_CLAUDE is None, reason="claude CLI is not installed")


def _task(tmp_path: Path, marker: Path) -> TaskConfig:
    return {
        "prompt": f"Run exactly this command and nothing else: touch {marker}",
        "working_dir": str(tmp_path),
        "permissions": {"level": "default"},
        "timeout": 120,
    }


@_SKIP
@pytest.mark.asyncio
async def test_allow_runs_the_tool(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("AGENT_YOLO", raising=False)
    marker = tmp_path / "marker"
    requests: list[PermissionRequestEvent] = []

    provider = ClaudeNativeProvider()

    async def handler(request: PermissionRequestEvent) -> PermissionDecision:
        requests.append(request)
        return {"option_id": "allow_once", "outcome": "selected"}

    provider.set_permission_handler(handler)

    async for _ in provider.stream(_task(tmp_path, marker)):
        pass

    assert requests, "Claude did not send a permission request"
    assert marker.exists()


@_SKIP
@pytest.mark.asyncio
async def test_deny_does_not_run_the_tool(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("AGENT_YOLO", raising=False)
    marker = tmp_path / "marker"
    requests: list[PermissionRequestEvent] = []

    provider = ClaudeNativeProvider()

    async def handler(request: PermissionRequestEvent) -> PermissionDecision:
        requests.append(request)
        return {"option_id": "reject_once", "outcome": "selected"}

    provider.set_permission_handler(handler)

    async for _ in provider.stream(_task(tmp_path, marker)):
        pass

    assert requests, "Claude did not send a permission request"
    assert not marker.exists()
