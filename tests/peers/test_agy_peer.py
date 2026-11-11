"""agy peer behaviour against a scripted bidirectional stream."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from agentabi.peers import PeerBroker
from agentabi.peers.agy import AgyPeer
from agentabi.peers.errors import (
    PeerBusy,
    PeerClosed,
    PeerError,
    PeerExited,
    PeerTimeout,
)

FAKE = Path(__file__).with_name("fake_agy_stream.py")
COMMAND = [sys.executable, str(FAKE)]


async def _start(tmp_path: Path, **kwargs) -> AgyPeer:
    peer = AgyPeer(name="agy", cwd=str(tmp_path), command=COMMAND, **kwargs)
    await peer.start()
    return peer


class TestAgyPeer:
    async def test_send_and_wait(self, tmp_path: Path) -> None:
        peer = await _start(tmp_path)
        try:
            sent = await peer.send("AGY-ONE")
            assert sent.mode == "turn"
            turn = await peer.wait(timeout=5)
            assert turn.status == "success"
            assert turn.text == "AGY-ONE"
            assert turn.stop_reason == "SUCCESS"
            assert turn.usage == {
                "input_tokens": 21,
                "output_tokens": 1,
                "cache_read_tokens": 3,
                "total_tokens": 24,
            }
            assert peer.info().session_id == "agy-fake"
        finally:
            await peer.close()

    async def test_second_turn_on_the_same_process(self, tmp_path: Path) -> None:
        peer = await _start(tmp_path)
        try:
            await peer.send("AGY-FIRST")
            await peer.wait(timeout=5)
            await peer.send("again")
            turn = await peer.wait(timeout=5)
            assert turn.text == "AGY-FIRST"
        finally:
            await peer.close()

    async def test_a_second_message_while_busy_is_refused(self, tmp_path: Path) -> None:
        peer = await _start(tmp_path)
        try:
            await peer.send("SLEEP=5")
            with pytest.raises(PeerBusy, match="queues messages"):
                await peer.send("too early")
            await peer.interrupt()
            await peer.wait(timeout=5)
        finally:
            await peer.close()

    async def test_steer_is_not_available(self, tmp_path: Path) -> None:
        peer = await _start(tmp_path)
        try:
            await peer.send("SLEEP=5")
            with pytest.raises(PeerBusy, match="no steer event"):
                await peer.steer("steer me")
            await peer.interrupt()
            await peer.wait(timeout=5)
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
            await peer.interrupt()
            await peer.wait(timeout=5)
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

    def test_unsupported_options_are_refused(self, tmp_path: Path) -> None:
        with pytest.raises(PeerError, match="system prompts"):
            AgyPeer(name="agy", cwd=str(tmp_path), system_prompt="x")
        with pytest.raises(PeerError, match="agent definitions"):
            AgyPeer(name="agy", cwd=str(tmp_path), agents={"a": {}})


class TestBrokerBackend:
    async def test_spawns_an_agy_peer(self, tmp_path: Path) -> None:
        async with PeerBroker() as broker:
            peer = await broker.spawn(
                "worker",
                cwd=str(tmp_path),
                backend="agy",
                command=COMMAND,
                prompt="hello",
            )
            turn = await peer.wait(timeout=5)
            assert turn.text == "hello"
            assert peer.info().backend == "agy"
