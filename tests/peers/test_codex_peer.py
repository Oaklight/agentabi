"""Codex peer behaviour against a scripted app-server."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from agentabi.peers import PeerBroker
from agentabi.peers.codex import CodexPeer
from agentabi.peers.errors import (
    PeerBusy,
    PeerClosed,
    PeerError,
    PeerExited,
    PeerTimeout,
)

FAKE = Path(__file__).with_name("fake_codex_app_server.py")
COMMAND = [sys.executable, str(FAKE)]


async def _start(tmp_path: Path, **kwargs) -> CodexPeer:
    peer = CodexPeer(name="cx", cwd=str(tmp_path), command=COMMAND, **kwargs)
    await peer.start()
    return peer


class TestCodexPeer:
    async def test_send_and_wait(self, tmp_path: Path) -> None:
        peer = await _start(tmp_path)
        try:
            sent = await peer.send("Reply with CODEX-OK")
            assert sent.mode == "turn"
            turn = await peer.wait(timeout=5)
            assert turn.status == "success"
            assert turn.text == "Reply with CODEX-OK"
            assert turn.stop_reason == "completed"
            assert turn.usage == {
                "input_tokens": 7,
                "output_tokens": 3,
                "cache_read_tokens": 0,
                "total_tokens": 10,
            }
            assert turn.turn_id == sent.turn_id
        finally:
            await peer.close()

    async def test_second_turn_on_the_same_thread(self, tmp_path: Path) -> None:
        peer = await _start(tmp_path)
        try:
            await peer.send("one")
            await peer.wait(timeout=5)
            sent = await peer.send("two")
            assert sent.mode == "turn"
            turn = await peer.wait(timeout=5)
            assert turn.text == "two"
            assert peer.info().session_id is not None
        finally:
            await peer.close()

    async def test_steer_during_a_turn(self, tmp_path: Path) -> None:
        peer = await _start(tmp_path)
        try:
            await peer.send("SLEEP=5 first plan")
            await peer.steer("STEERED-CODEX", interrupt=False)
            turn = await peer.wait(timeout=5)
            assert turn.text == "STEERED-CODEX"
            assert turn.usage["input_tokens"] == 10
        finally:
            await peer.close()

    async def test_interrupt_then_continue(self, tmp_path: Path) -> None:
        peer = await _start(tmp_path)
        try:
            await peer.send("SLEEP=5")
            await peer.interrupt()
            turn = await peer.wait(timeout=5)
            assert turn.status == "cancelled"
            assert turn.stop_reason == "interrupted"
            await peer.send("after interrupt")
            turn = await peer.wait(timeout=5)
            assert turn.status == "success"
            assert turn.text == "after interrupt"
        finally:
            await peer.close()

    async def test_wait_timeout_leaves_the_turn_running(self, tmp_path: Path) -> None:
        peer = await _start(tmp_path)
        try:
            await peer.send("SLEEP=5")
            with pytest.raises(PeerTimeout):
                await peer.wait(timeout=0.2)
            assert peer.info().status == "running"
        finally:
            await peer.close()

    async def test_steer_requires_a_turn(self, tmp_path: Path) -> None:
        peer = await _start(tmp_path)
        try:
            with pytest.raises(PeerBusy):
                await peer.steer("nothing running")
        finally:
            await peer.close()

    async def test_send_after_close_is_rejected(self, tmp_path: Path) -> None:
        peer = await _start(tmp_path)
        await peer.close()
        with pytest.raises(PeerClosed):
            await peer.send("late")

    async def test_process_exit_is_reported(self, tmp_path: Path) -> None:
        peer = await _start(tmp_path)
        try:
            await peer.send("EXIT")
            turn = await peer.wait(timeout=5)
            assert turn.status == "cancelled"
            assert any(note.kind == "exit" for note in peer.inbox())
            with pytest.raises(PeerExited):
                await peer.send("again")
        finally:
            await peer.close()

    async def test_failed_turn(self, tmp_path: Path) -> None:
        peer = await _start(tmp_path)
        try:
            await peer.send("FAIL")
            turn = await peer.wait(timeout=5)
            assert turn.status == "error"
            assert turn.stop_reason == "failed"
            assert turn.errors
        finally:
            await peer.close()

    def test_unknown_permission_mode(self, tmp_path: Path) -> None:
        with pytest.raises(PeerError, match="permission_mode"):
            CodexPeer(name="cx", cwd=str(tmp_path), permission_mode="nope")

    def test_agents_are_refused(self, tmp_path: Path) -> None:
        with pytest.raises(PeerError, match="agent definitions"):
            CodexPeer(name="cx", cwd=str(tmp_path), agents={"a": {}})


class TestBrokerBackend:
    async def test_spawns_a_codex_peer(self, tmp_path: Path) -> None:
        async with PeerBroker() as broker:
            peer = await broker.spawn(
                "worker",
                cwd=str(tmp_path),
                backend="codex",
                command=COMMAND,
                prompt="hello",
            )
            turn = await peer.wait(timeout=5)
            assert turn.text == "hello"
            info = peer.info()
            assert info.backend == "codex"
            assert info.engine == "process"

    async def test_unknown_backend(self, tmp_path: Path) -> None:
        async with PeerBroker() as broker:
            with pytest.raises(ValueError, match="unknown backend"):
                await broker.spawn("worker", cwd=str(tmp_path), backend="gemini")
