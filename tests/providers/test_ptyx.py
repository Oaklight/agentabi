"""Tests for the vendored ptyx transport.

The ConPTY tests stub the Win32 calls and instantiate the backend without
spawning a child, so they run on every platform.  The query tests exercise
the POSIX answerer directly.
"""

from __future__ import annotations

import asyncio
import ctypes
import os
import threading

import pytest

from agentabi._vendor.ptyx import PtyReadError, PtyWriteError
from agentabi._vendor.ptyx import ptyx as ptyx_module
from agentabi._vendor.ptyx.ptyx import _ConPtyProcess, _PosixPtyProcess


def _conpty_stub(queue_size: int = 1) -> _ConPtyProcess:
    """Build a ConPTY backend whose reader thread has not started."""
    proc = _ConPtyProcess.__new__(_ConPtyProcess)
    proc._loop = asyncio.get_running_loop()
    proc._process = object()
    proc._pid = 0
    proc._console = object()
    proc._input = object()
    proc._output = object()
    proc._queue = asyncio.Queue(maxsize=queue_size)
    proc._buffer = bytearray()
    proc._read_error = None
    proc._eof = False
    proc._closed = False
    proc._returncode = None
    return proc


def _posix_stub(write_fd: int) -> _PosixPtyProcess:
    proc = _PosixPtyProcess.__new__(_PosixPtyProcess)
    proc._master_fd = write_fd
    proc._query_tail = b""
    return proc


async def test_conpty_full_queue_does_not_drop_chunks(monkeypatch):
    chunks = [b"chunk-%d" % index for index in range(8)]
    state = {"index": 0}
    proc = _conpty_stub(queue_size=1)

    def fake_wait(handle, timeout):
        if state["index"] < len(chunks):
            return ptyx_module._WAIT_TIMEOUT
        return ptyx_module._WAIT_OBJECT_0

    def fake_peek(handle, buffer, size, read, available, overlapped):
        if state["index"] < len(chunks):
            available._obj.value = len(chunks[state["index"]])
        else:
            available._obj.value = 0
        return 1

    def fake_read(handle, buffer, size, read, overlapped):
        data = chunks[state["index"]]
        ctypes.memmove(buffer, data, len(data))
        read._obj.value = len(data)
        state["index"] += 1
        return 1

    monkeypatch.setattr(ptyx_module, "_WaitForSingleObject", fake_wait, raising=False)
    monkeypatch.setattr(ptyx_module, "_PeekNamedPipe", fake_peek, raising=False)
    monkeypatch.setattr(ptyx_module, "_ReadFile", fake_read, raising=False)

    reader = threading.Thread(target=proc._read_loop, daemon=True)
    reader.start()
    # Let the reader fill the one-slot queue and try to enqueue more.  The
    # old put_nowait handoff dropped these chunks.
    await asyncio.sleep(0.2)

    received: list[bytes] = []
    while True:
        item = await asyncio.wait_for(proc._queue.get(), 2.0)
        if item == b"":
            break
        received.append(item)

    # Join off the event loop: _emit() needs the loop to finish its handoff.
    await asyncio.to_thread(reader.join, 2.0)
    assert received == chunks
    assert not reader.is_alive()


async def test_conpty_peek_failure_raises_instead_of_eof(monkeypatch):
    proc = _conpty_stub(queue_size=1)

    monkeypatch.setattr(
        ptyx_module,
        "_WaitForSingleObject",
        lambda handle, timeout: ptyx_module._WAIT_TIMEOUT,
        raising=False,
    )
    monkeypatch.setattr(ptyx_module, "_PeekNamedPipe", lambda *args: 0, raising=False)
    monkeypatch.setattr(ptyx_module, "_last_error", lambda: 5, raising=False)

    reader = threading.Thread(target=proc._read_loop, daemon=True)
    reader.start()
    with pytest.raises(PtyReadError, match="PeekNamedPipe.*WinError 5"):
        await asyncio.wait_for(proc.read(), 2.0)

    # Join off the event loop so the reader can finish its queued handoff.
    await asyncio.to_thread(reader.join, 2.0)
    assert not reader.is_alive()


@pytest.mark.skipif(os.name == "nt", reason="POSIX terminal-query handling")
def test_cursor_position_query_gets_a_reply():
    read_fd, write_fd = os.pipe()
    try:
        proc = _posix_stub(write_fd)
        proc._answer_terminal_queries(b"before\x1b[6nafter")
        assert os.read(read_fd, 64) == b"\x1b[1;1R"
    finally:
        os.close(read_fd)
        os.close(write_fd)


@pytest.mark.skipif(os.name == "nt", reason="POSIX terminal-query handling")
def test_split_cursor_query_is_answered_once():
    read_fd, write_fd = os.pipe()
    try:
        proc = _posix_stub(write_fd)
        proc._answer_terminal_queries(b"\x1b[")
        proc._answer_terminal_queries(b"6n")
        proc._answer_terminal_queries(b"more")
        assert os.read(read_fd, 64) == b"\x1b[1;1R"
    finally:
        os.close(read_fd)
        os.close(write_fd)


@pytest.mark.skipif(os.name == "nt", reason="POSIX terminal-query handling")
def test_other_terminal_queries_get_no_reply():
    read_fd, write_fd = os.pipe()
    os.set_blocking(read_fd, False)
    try:
        proc = _posix_stub(write_fd)
        # DA1, DA2, and the kitty-keyboard query must not receive a fixed
        # reply that claims capabilities the PTY does not have.
        proc._answer_terminal_queries(b"\x1b[?u\x1b[>c\x1b[c\x1b[0c")
        with pytest.raises(BlockingIOError):
            os.read(read_fd, 64)
    finally:
        os.close(read_fd)
        os.close(write_fd)


# ========== Write failures are surfaced, not swallowed ==========


@pytest.mark.skipif(os.name == "nt", reason="POSIX write path")
async def test_posix_write_delivers_all_bytes():
    read_fd, write_fd = os.pipe()
    try:
        proc = _posix_stub(write_fd)
        proc._closed = False
        await proc.write(b"hello")
        assert os.read(read_fd, 64) == b"hello"
    finally:
        os.close(read_fd)
        os.close(write_fd)


@pytest.mark.skipif(os.name == "nt", reason="POSIX write path")
async def test_posix_write_failure_raises():
    read_fd, write_fd = os.pipe()
    os.close(read_fd)  # no reader: the write fails with EPIPE
    try:
        proc = _posix_stub(write_fd)
        proc._closed = False
        with pytest.raises(PtyWriteError, match="terminal write failed"):
            await proc.write(b"hello")
    finally:
        os.close(write_fd)


async def test_posix_write_after_close_raises():
    read_fd, write_fd = os.pipe()
    try:
        proc = _posix_stub(write_fd)
        proc._closed = True
        with pytest.raises(PtyWriteError, match="closed"):
            await proc.write(b"hello")
    finally:
        os.close(read_fd)
        os.close(write_fd)


async def test_conpty_write_failure_raises(monkeypatch):
    proc = _conpty_stub()
    monkeypatch.setattr(ptyx_module, "_WriteFile", lambda *args: 0, raising=False)
    monkeypatch.setattr(ptyx_module, "_last_error", lambda: 5, raising=False)

    with pytest.raises(PtyWriteError, match="WinError 5"):
        await proc.write(b"hello")


async def test_conpty_zero_byte_write_raises(monkeypatch):
    proc = _conpty_stub()

    def fake_write(handle, buffer, size, written, overlapped):
        written._obj.value = 0
        return 1

    monkeypatch.setattr(ptyx_module, "_WriteFile", fake_write, raising=False)

    with pytest.raises(PtyWriteError, match="wrote 0 bytes"):
        await proc.write(b"hello")


async def test_conpty_write_after_close_raises():
    proc = _conpty_stub()
    proc._closed = True

    with pytest.raises(PtyWriteError, match="closed"):
        await proc.write(b"hello")
