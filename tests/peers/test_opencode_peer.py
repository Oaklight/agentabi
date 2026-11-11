"""OpenCode peer behaviour against an in-process V2 API."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from agentabi.peers import PeerBroker
from agentabi.peers.errors import PeerClosed, PeerError, PeerTimeout
from agentabi.peers.opencode import OpenCodePeer

from .fake_opencode_server import FakeOpenCodeServer


@pytest.fixture
def server() -> Iterator[str]:
    with FakeOpenCodeServer() as fake:
        yield fake.base_url


async def _start(base_url: str, **kwargs) -> OpenCodePeer:
    peer = OpenCodePeer(
        name="oc",
        cwd="/tmp",
        base_url=base_url,
        model="fake/model",
        **kwargs,
    )
    await peer.start()
    return peer


class TestOpenCodePeer:
    async def test_send_and_wait(self, server: str) -> None:
        peer = await _start(server)
        try:
            sent = await peer.send("OC-ONE")
            assert sent.mode == "turn"
            turn = await peer.wait(timeout=10)
            assert turn.status == "success"
            assert turn.text == "OC-ONE"
            assert turn.stop_reason == "completed"
            assert turn.usage["input_tokens"] == 11
        finally:
            await peer.close()

    async def test_second_turn_uses_the_same_session(self, server: str) -> None:
        peer = await _start(server)
        try:
            await peer.send("OC-FIRST")
            await peer.wait(timeout=10)
            sent = await peer.send("again")
            assert sent.mode == "turn"
            turn = await peer.wait(timeout=10)
            assert turn.text == "OC-FIRST"
            assert peer.info().session_id == "ses_fake"
        finally:
            await peer.close()

    async def test_steer_during_a_turn(self, server: str) -> None:
        peer = await _start(server)
        try:
            await peer.send("SLEEP=5 first plan")
            sent = await peer.steer("STEERED-OC")
            assert sent.mode == "steer"
            turn = await peer.wait(timeout=10)
            assert turn.text == "STEERED-OC"
        finally:
            await peer.close()

    async def test_interrupt_then_continue(self, server: str) -> None:
        peer = await _start(server)
        try:
            await peer.send("SLEEP=5")
            await peer.interrupt()
            turn = await peer.wait(timeout=10)
            assert turn.status == "cancelled"
            assert turn.stop_reason == "interrupted"
            await peer.send("OC-AFTER")
            turn = await peer.wait(timeout=10)
            assert turn.status == "success"
            assert turn.text == "OC-AFTER"
        finally:
            await peer.close()

    async def test_wait_timeout_leaves_the_turn_running(self, server: str) -> None:
        peer = await _start(server)
        try:
            await peer.send("SLEEP=5")
            with pytest.raises(PeerTimeout):
                await peer.wait(timeout=0.2)
            assert peer.info().status == "running"
        finally:
            await peer.close()

    async def test_steer_requires_a_turn(self, server: str) -> None:
        peer = await _start(server)
        try:
            with pytest.raises(PeerError, match="no running turn"):
                await peer.steer("nothing")
        finally:
            await peer.close()

    async def test_send_after_close_is_rejected(self, server: str) -> None:
        peer = await _start(server)
        await peer.close()
        with pytest.raises(PeerClosed):
            await peer.send("late")

    async def test_failed_turn(self, server: str) -> None:
        peer = await _start(server)
        try:
            await peer.send("FAIL")
            turn = await peer.wait(timeout=10)
            assert turn.status == "error"
            assert turn.errors
        finally:
            await peer.close()

    def test_endpoint_settings_are_refused(self) -> None:
        with pytest.raises(PeerError, match="V2 catalog"):
            OpenCodePeer(
                name="oc",
                cwd="/tmp",
                settings={"base_url": "https://example.test/v1"},
            )

    def test_model_needs_a_provider(self) -> None:
        with pytest.raises(PeerError, match="provider/model"):
            OpenCodePeer(name="oc", cwd="/tmp", model="qwen")

    def test_unsupported_options_are_refused(self) -> None:
        with pytest.raises(PeerError, match="system prompts"):
            OpenCodePeer(name="oc", cwd="/tmp", system_prompt="x")
        with pytest.raises(PeerError, match="agent definitions"):
            OpenCodePeer(name="oc", cwd="/tmp", agents={"a": {}})


class TestBrokerBackend:
    async def test_spawns_an_opencode_peer(self, server: str) -> None:
        async with PeerBroker() as broker:
            peer = await broker.spawn(
                "worker",
                cwd="/tmp",
                backend="opencode",
                base_url=server,
                model="fake/model",
                prompt="BROKER-OC",
            )
            turn = await peer.wait(timeout=10)
            assert turn.text == "BROKER-OC"
            assert peer.info().backend == "opencode"
