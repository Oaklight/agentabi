"""Daemon round-trip tests with a real child process and the fake CLI."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from agentabi.peers import PeerClient, PeerNotFound, PeerTimeout
from agentabi.peers.errors import PeerBusy, PeerError

FAKE = Path(__file__).with_name("fake_claude_peer.py")
COMMAND = [sys.executable, str(FAKE)]


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AGENTABI_PEER_STATE", str(tmp_path / "state"))
    instance = PeerClient(autostart=True, start_timeout=20)
    instance.ensure_daemon()
    try:
        yield instance
    finally:
        instance.stop_daemon()


def _spawn(client: PeerClient, name: str, cwd: Path, **extra) -> dict:
    params = {"name": name, "cwd": str(cwd), "command": COMMAND}
    params.update(extra)
    return client.request("spawn", params)


class TestDaemon:
    def test_ping_and_shutdown(self, client: PeerClient) -> None:
        ping = client.ping()
        assert ping is not None and ping["pid"] > 0
        client.stop_daemon()
        assert client.ping() is None

    def test_spawn_list_status(self, client: PeerClient, tmp_path: Path) -> None:
        info = _spawn(client, "worker", tmp_path)
        assert info["status"] == "idle"
        assert info["engine"] == "process"
        listed = client.request("list")
        assert [item["name"] for item in listed] == ["worker"]
        status = client.request("status", {"name": "worker"})
        assert status["session_id"] == info["session_id"]

    def test_send_wait_and_follow_up(self, client: PeerClient, tmp_path: Path) -> None:
        _spawn(client, "worker", tmp_path)
        first = client.request("send", {"name": "worker", "text": "one"})
        assert first["mode"] == "turn"
        turn = client.request("wait", {"name": "worker", "timeout": 10})
        assert turn["text"] == "one"
        client.request("send", {"name": "worker", "text": "two"})
        again = client.request("wait", {"name": "worker", "timeout": 10})
        assert again["text"] == "two"

    def test_steer_during_a_turn(self, client: PeerClient, tmp_path: Path) -> None:
        _spawn(client, "worker", tmp_path)
        client.request("send", {"name": "worker", "text": "SLEEP=5 original"})
        steer = client.request("steer", {"name": "worker", "text": "steered"})
        assert steer["mode"] == "steer"
        turn = client.request("wait", {"name": "worker", "timeout": 10})
        assert turn["text"] == "steered"
        assert turn["num_turns"] == 2

    def test_inbox_records_turns(self, client: PeerClient, tmp_path: Path) -> None:
        _spawn(client, "worker", tmp_path)
        client.request("send", {"name": "worker", "text": "note"})
        client.request("wait", {"name": "worker", "timeout": 10})
        notes = client.request("inbox", {"name": "worker"})
        assert any(note["kind"] == "turn" for note in notes)
        cleared = client.request("inbox", {"name": "worker", "clear": True})
        assert any(note["kind"] == "turn" for note in cleared)
        assert client.request("inbox", {"name": "worker"}) == []

    def test_stop_removes_the_peer(self, client: PeerClient, tmp_path: Path) -> None:
        _spawn(client, "worker", tmp_path)
        assert client.request("stop", {"name": "worker"}) == {"stopped": "worker"}
        assert client.request("list") == []
        with pytest.raises(PeerNotFound):
            client.request("status", {"name": "worker"})

    def test_unknown_peer_error_mapping(self, client: PeerClient) -> None:
        with pytest.raises(PeerNotFound):
            client.request("status", {"name": "ghost"})

    def test_busy_error_mapping(self, client: PeerClient, tmp_path: Path) -> None:
        _spawn(client, "worker", tmp_path)
        with pytest.raises(PeerBusy):
            client.request("steer", {"name": "worker", "text": "x"})

    def test_wait_timeout_error_mapping(
        self, client: PeerClient, tmp_path: Path
    ) -> None:
        _spawn(client, "worker", tmp_path)
        client.request("send", {"name": "worker", "text": "SLEEP=10 slow"})
        with pytest.raises(PeerTimeout):
            client.request("wait", {"name": "worker", "timeout": 0.2}, timeout=5)

    def test_invalid_spawn_params(self, client: PeerClient) -> None:
        with pytest.raises(PeerError):
            client.request("spawn", {"name": "x"})

    def test_state_file_written(self, client: PeerClient, tmp_path: Path) -> None:
        _spawn(client, "worker", tmp_path)
        state = json.loads((client.state_dir / "peers.json").read_text())
        assert state["peers"][0]["name"] == "worker"
        assert "api_key" not in json.dumps(state)
