"""Pi peer behaviour against a scripted RPC process."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from agentabi.peers import PeerBroker
from agentabi.peers.errors import (
    PeerBusy,
    PeerClosed,
    PeerError,
    PeerExited,
    PeerTimeout,
)
from agentabi.peers.pi import PiPeer

FAKE = Path(__file__).with_name("fake_pi_rpc.py")
COMMAND = [sys.executable, str(FAKE)]


async def _start(tmp_path: Path, **kwargs) -> PiPeer:
    peer = PiPeer(name="pi", cwd=str(tmp_path), command=COMMAND, **kwargs)
    await peer.start()
    return peer


class TestPiPeer:
    async def test_send_and_wait(self, tmp_path: Path) -> None:
        peer = await _start(tmp_path)
        try:
            sent = await peer.send("PI-ONE")
            assert sent.mode == "turn"
            turn = await peer.wait(timeout=5)
            assert turn.status == "success"
            assert turn.text == "PI-ONE"
            assert turn.stop_reason == "completed"
            assert turn.usage == {
                "input_tokens": 11,
                "output_tokens": 1,
                "cache_read_tokens": 2,
                "total_tokens": 13,
            }
            assert peer.info().session_id == "pi-fake"
        finally:
            await peer.close()

    async def test_second_turn_on_the_same_process(self, tmp_path: Path) -> None:
        peer = await _start(tmp_path)
        try:
            await peer.send("PI-FIRST")
            await peer.wait(timeout=5)
            sent = await peer.send("again")
            assert sent.mode == "turn"
            turn = await peer.wait(timeout=5)
            assert turn.text == "PI-FIRST"
        finally:
            await peer.close()

    async def test_steer_during_a_turn(self, tmp_path: Path) -> None:
        peer = await _start(tmp_path)
        try:
            await peer.send("SLEEP=5 first plan")
            sent = await peer.steer("STEERED-PI")
            assert sent.mode == "steer"
            turn = await peer.wait(timeout=5)
            assert turn.text == "STEERED-PI"
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
            await peer.send("after abort")
            turn = await peer.wait(timeout=5)
            assert turn.status == "success"
            assert turn.text == "after abort"
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
                await peer.steer("nothing")
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

    async def test_failed_prompt_is_an_error(self, tmp_path: Path) -> None:
        peer = await _start(tmp_path)
        try:
            with pytest.raises(PeerError, match="fake prompt failed"):
                await peer.send("FAIL")
            assert peer.info().status == "idle"
        finally:
            await peer.close()

    def test_agents_are_refused(self, tmp_path: Path) -> None:
        with pytest.raises(PeerError, match="agent definitions"):
            PiPeer(name="pi", cwd=str(tmp_path), agents={"a": {}})


class TestBrokerBackend:
    async def test_spawns_a_pi_peer(self, tmp_path: Path) -> None:
        async with PeerBroker() as broker:
            peer = await broker.spawn(
                "worker",
                cwd=str(tmp_path),
                backend="pi",
                command=COMMAND,
                prompt="hello",
            )
            turn = await peer.wait(timeout=5)
            assert turn.text == "hello"
            assert peer.info().backend == "pi"
