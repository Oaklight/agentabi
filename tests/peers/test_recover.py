"""Resume a saved peer after its daemon went away."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from agentabi.peers import PeerClient, PeerNotFound
from agentabi.peers.errors import PeerError
from agentabi.peers.recover import (
    find_entry,
    find_pool_entry,
    load_entries,
    recover_from_pool,
    recover_peer,
)

FAKE = Path(__file__).with_name("fake_claude_peer.py")
COMMAND = [sys.executable, str(FAKE)]
CODEX_FAKE = Path(__file__).with_name("fake_codex_app_server.py")
CODEX_COMMAND = [sys.executable, str(CODEX_FAKE)]


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

    def test_resumes_a_codex_thread(self, client: PeerClient, tmp_path: Path) -> None:
        info = _spawn(client, "cx", tmp_path, backend="codex", command=CODEX_COMMAND)
        session_id = info["session_id"]
        client.stop_daemon()
        recovered = recover_peer(client, "cx")
        assert recovered["session_id"] == session_id
        client.request("send", {"name": "cx", "text": "after resume"})
        turn = client.request("wait", {"name": "cx", "timeout": 10}, timeout=20)
        assert turn["status"] == "success"
        assert turn["text"] == "after resume"

    def test_explicit_session_resumes_without_state(
        self, client: PeerClient, tmp_path: Path
    ) -> None:
        info = recover_peer(
            client,
            "pooled",
            session_id="session-from-pool",
            cwd=str(tmp_path),
            command=COMMAND,
        )
        assert info["session_id"] == "session-from-pool"
        client.request("send", {"name": "pooled", "text": "after pool"})
        turn = client.request("wait", {"name": "pooled", "timeout": 10}, timeout=20)
        assert turn["text"] == "after pool"
        client.request("stop", {"name": "pooled"})

    def test_pool_entry_prefers_live_then_recency(self, tmp_path: Path) -> None:
        pool = tmp_path / "pool.json"
        pool.write_text(
            json.dumps(
                {
                    "entries": {
                        "old": {"name": "s", "cwd": "/x", "startedAt": 1},
                        "new": {"name": "s", "cwd": "/y", "startedAt": 9},
                        "live": {
                            "name": "s",
                            "cwd": "/z",
                            "startedAt": 2,
                            "live": True,
                        },
                    }
                }
            )
        )
        entry = find_pool_entry("s", path=pool)
        assert entry is not None
        assert entry["cwd"] == "/z"
        assert entry["session_id"] == "live"

    def test_pool_fallback_spawns_a_process_peer(
        self, client: PeerClient, tmp_path: Path
    ) -> None:
        pool = tmp_path / "pool.json"
        pool.write_text(
            json.dumps(
                {
                    "entries": {
                        "bg-session-id": {
                            "name": "id-worker",
                            "cwd": str(tmp_path),
                            "startedAt": 5,
                        }
                    }
                }
            )
        )
        info = recover_from_pool(client, "id-worker", path=pool, command=COMMAND)
        assert info["session_id"] == "bg-session-id"
        client.request("send", {"name": "id-worker", "text": "from pool"})
        turn = client.request("wait", {"name": "id-worker", "timeout": 10}, timeout=20)
        assert turn["text"] == "from pool"
        client.request("stop", {"name": "id-worker"})

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
