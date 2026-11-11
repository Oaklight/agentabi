"""Cross-session frames for Claude Code's peer socket."""

from __future__ import annotations

import json
import socket
import threading
from pathlib import Path

import pytest

from agentabi.peers.errors import PeerError
from agentabi.peers.socket_wire import (
    MAX_FRAME_BYTES,
    send_cross_session,
    session_socket,
    socket_dir,
)


class Receiver:
    """A one-shot Unix socket server that records what it receives."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.data = b""
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.bind(str(path))
        self._sock.listen(1)
        self._sock.settimeout(0.05)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except TimeoutError:
                continue
            with conn:
                while chunk := conn.recv(65536):
                    self.data += chunk
            return

    def collect(self, timeout: float = 5) -> None:
        """Wait until the sender's connection is closed and read."""
        self._thread.join(timeout=timeout)

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)
        self._sock.close()


@pytest.fixture
def receiver(tmp_path: Path):
    path = tmp_path / "1234.sock"
    server = Receiver(path)
    try:
        yield server
    finally:
        server.close()


def frames(data: bytes) -> list[dict]:
    return [json.loads(line) for line in data.decode().splitlines() if line]


class TestSendCrossSession:
    def test_sends_one_user_frame(self, receiver: Receiver) -> None:
        receipt = send_cross_session(
            receiver.path,
            "please review src/auth.py",
            from_name="coordinator",
        )
        receiver.collect()
        rows = frames(receiver.data)
        assert len(rows) == 1
        frame = rows[0]
        assert frame["msgV"] == 1
        assert frame["type"] == "user"
        assert frame["priority"] == "next"
        assert frame["from"] == "uds:agentabi-peerctl"
        assert receipt["msg_id"] == frame["msg_id"]
        content = frame["message"]["content"]
        assert content.startswith(
            '<cross-session-message from="uds:agentabi-peerctl" '
            'from-name="coordinator" from-mode="bypass">'
        )
        assert "please review src/auth.py" in content
        assert content.endswith("</cross-session-message>")
        assert receipt["acknowledged"] is False

    def test_mode_and_address_are_configurable(self, receiver: Receiver) -> None:
        send_cross_session(
            receiver.path,
            "hi",
            from_name="agentabi-c1",
            from_addr="uds:/run/user/1000/cc-socks/1.sock",
            from_mode="default",
        )
        receiver.collect()
        content = frames(receiver.data)[0]["message"]["content"]
        assert 'from="uds:/run/user/1000/cc-socks/1.sock"' in content
        assert 'from-name="agentabi-c1"' in content
        assert 'from-mode="default"' in content

    def test_rejects_a_quote_in_the_name(self, receiver: Receiver) -> None:
        with pytest.raises(PeerError, match="from_name"):
            send_cross_session(receiver.path, "hi", from_name='bad"name')

    def test_rejects_an_oversize_frame(self, receiver: Receiver) -> None:
        with pytest.raises(PeerError, match="drops frames over"):
            send_cross_session(receiver.path, "x" * MAX_FRAME_BYTES)

    def test_missing_socket_is_reported(self, tmp_path: Path) -> None:
        with pytest.raises(PeerError, match="no Claude session socket"):
            send_cross_session(tmp_path / "missing.sock", "hi")

    def test_non_socket_path_is_reported(self, tmp_path: Path) -> None:
        path = tmp_path / "not-a.sock"
        path.write_text("x")
        with pytest.raises(PeerError, match="send to"):
            send_cross_session(path, "hi")


class TestSocketLocation:
    def test_uses_xdg_runtime_dir(self, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
        assert socket_dir() == tmp_path / "cc-socks"
        assert session_socket(42) == tmp_path / "cc-socks" / "42.sock"

    def test_falls_back_to_run_user(self, monkeypatch) -> None:
        monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
        assert socket_dir() == Path(f"/run/user/{portable_uid()}/cc-socks")


def portable_uid() -> int:
    import os

    return os.getuid()
