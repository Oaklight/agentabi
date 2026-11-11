"""Claude peer process tests against a scripted CLI stand-in."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from agentabi.peers import (
    ClaudePeer,
    PeerBroker,
    PeerBusy,
    PeerClosed,
    PeerExited,
    PeerTimeout,
)

FAKE = Path(__file__).with_name("fake_claude_peer.py")
COMMAND = [sys.executable, str(FAKE)]


def _peer(tmp_path: Path, **kwargs) -> ClaudePeer:
    return ClaudePeer(
        name="worker",
        cwd=str(tmp_path),
        command=COMMAND,
        **kwargs,
    )


async def _start(tmp_path: Path, **kwargs) -> ClaudePeer:
    peer = _peer(tmp_path, **kwargs)
    await peer.start()
    return peer


class TestClaudePeer:
    async def test_send_and_wait(self, tmp_path: Path) -> None:
        peer = await _start(tmp_path)
        try:
            result = await peer.send("hello")
            assert result.accepted is True
            assert result.mode == "turn"
            turn = await peer.wait(timeout=10)
            assert turn.status == "success"
            assert turn.text == "hello"
            assert turn.turn_id == result.turn_id
        finally:
            await peer.close()

    async def test_a_large_stream_line_does_not_kill_the_reader(
        self, tmp_path: Path
    ) -> None:
        peer = await _start(tmp_path)
        try:
            await peer.send("BIGLINE")
            turn = await peer.wait(timeout=5)
            assert turn.status == "success"
            assert len(turn.text) == 200_000
            assert not [note for note in peer.inbox() if note.kind == "error"]
        finally:
            await peer.close()

    async def test_second_message_starts_a_new_turn(self, tmp_path: Path) -> None:
        peer = await _start(tmp_path)
        try:
            await peer.send("one")
            await peer.wait(timeout=10)
            second = await peer.send("two")
            assert second.mode == "turn"
            turn = await peer.wait(timeout=10)
            assert turn.text == "two"
        finally:
            await peer.close()

    async def test_message_during_a_turn_steers(self, tmp_path: Path) -> None:
        peer = await _start(tmp_path)
        try:
            first = await peer.send("SLEEP=5 original")
            await asyncio.sleep(0.3)
            second = await peer.send("steered")
            assert second.mode == "steer"
            assert second.turn_id == first.turn_id
            turn = await peer.wait(timeout=10)
            assert turn.text == "steered"
            assert turn.num_turns == 2
        finally:
            await peer.close()

    async def test_interrupt_ends_the_turn_but_keeps_the_peer(
        self, tmp_path: Path
    ) -> None:
        peer = await _start(tmp_path)
        try:
            await peer.send("SLEEP=10 slow")
            await asyncio.sleep(0.2)
            await peer.interrupt()
            turn = await peer.wait(timeout=5)
            assert turn.status == "cancelled"
            assert peer.info().status == "idle"
            await peer.send("after interrupt")
            again = await peer.wait(timeout=10)
            assert again.status == "success"
            assert again.text == "after interrupt"
        finally:
            await peer.close()

    async def test_wait_times_out_without_ending_the_turn(self, tmp_path: Path) -> None:
        peer = await _start(tmp_path)
        try:
            await peer.send("SLEEP=5 slow")
            with pytest.raises(PeerTimeout):
                await peer.wait(timeout=0.2)
        finally:
            await peer.close()

    async def test_steer_requires_a_running_turn(self, tmp_path: Path) -> None:
        peer = await _start(tmp_path)
        try:
            with pytest.raises(PeerBusy):
                await peer.steer("nothing to steer")
        finally:
            await peer.close()

    async def test_close_rejects_new_work(self, tmp_path: Path) -> None:
        peer = await _start(tmp_path)
        await peer.close()
        with pytest.raises(PeerClosed):
            await peer.send("late")

    async def test_process_exit_surfaces_as_peer_exited(self, tmp_path: Path) -> None:
        peer = await _start(tmp_path)
        try:
            assert peer._proc is not None
            peer._proc.kill()
            await asyncio.sleep(0.3)
            with pytest.raises(PeerExited):
                await peer.send("after crash")
            notes = peer.inbox()
            assert any(note.kind == "exit" for note in notes)
        finally:
            await peer.close()

    async def test_session_id_is_reported(self, tmp_path: Path) -> None:
        peer = await _start(tmp_path)
        try:
            assert peer.info().session_id
            assert peer.info().engine == "process"
        finally:
            await peer.close()


class TestPeerBroker:
    async def test_spawn_with_prompt_and_list(self, tmp_path: Path) -> None:
        broker = PeerBroker()
        try:
            peer = await broker.spawn(
                "reviewer",
                cwd=str(tmp_path),
                prompt="review",
                command=COMMAND,
            )
            turn = await peer.wait(timeout=10)
            assert turn.text == "review"
            assert [item.name for item in broker.list_peers()] == ["reviewer"]
            assert broker.get("reviewer") is peer
        finally:
            await broker.close()

    async def test_duplicate_name_is_rejected(self, tmp_path: Path) -> None:
        broker = PeerBroker()
        try:
            await broker.spawn("a", cwd=str(tmp_path), command=COMMAND)
            with pytest.raises(ValueError):
                await broker.spawn("a", cwd=str(tmp_path), command=COMMAND)
        finally:
            await broker.close()

    async def test_pool_cap(self, tmp_path: Path) -> None:
        broker = PeerBroker(max_peers=1)
        try:
            await broker.spawn("a", cwd=str(tmp_path), command=COMMAND)
            with pytest.raises(ValueError):
                await broker.spawn("b", cwd=str(tmp_path), command=COMMAND)
        finally:
            await broker.close()

    async def test_close_all(self, tmp_path: Path) -> None:
        broker = PeerBroker()
        await broker.spawn("a", cwd=str(tmp_path), command=COMMAND)
        await broker.close()
        assert broker.list_peers() == []
