"""Resume a saved peer after its daemon went away."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from agentabi.peers import PeerClient, PeerNotFound
from agentabi.peers.errors import PeerError
from agentabi.peers.recover import find_entry, load_entries, recover_peer

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


class TestRecover:
    def test_resumes_the_saved_session(
        self, client: PeerClient, tmp_path: Path
    ) -> None:
        info = _spawn(client, "worker", tmp_path, model="fake-model")
        session_id = info["session_id"]
        assert session_id

        # A daemon stop leaves no live peer but keeps the saved entry.
        client.stop_daemon()
        entries = load_entries(client.state_dir)
        assert find_entry(entries, "worker")["session_id"] == session_id

        recovered = recover_peer(client, "worker")
        assert recovered["name"] == "worker"
        assert recovered["session_id"] == session_id

        client.request("send", {"name": "worker", "text": "ARGV"})
        turn = client.request("wait", {"name": "worker", "timeout": 10}, timeout=20)
        assert turn["status"] == "success"
        argv = json.loads(turn["text"].removeprefix("ARGV "))
        assert "--resume" in argv
        assert argv[argv.index("--resume") + 1] == session_id
        assert info["model"] == "fake-model"

    def test_unknown_name(self, client: PeerClient) -> None:
        with pytest.raises(PeerNotFound, match="no saved peer"):
            recover_peer(client, "ghost")

    def test_a_live_peer_is_not_recovered(
        self, client: PeerClient, tmp_path: Path
    ) -> None:
        _spawn(client, "live", tmp_path)
        with pytest.raises(PeerError, match="already running"):
            recover_peer(client, "live")

    def test_an_entry_without_a_session_id(self, client: PeerClient) -> None:
        state = client.state_dir / "peers.json"
        state.write_text(
            json.dumps(
                {
                    "version": 1,
                    "peers": [{"name": "broken", "cwd": "/tmp"}],
                }
            )
        )
        with pytest.raises(PeerError, match="no saved session id"):
            recover_peer(client, "broken")
