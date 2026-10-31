"""agentabi - POSIX pseudo-terminal transport.

Spawns a process on its own PTY and exposes raw byte I/O.  A PTY is
needed for CLIs that only behave as a person's terminal session (for
example Claude Code's interactive mode); it is not a protocol layer.

The process runs in its own session, so closing the transport can signal
the whole process group without touching agentabi.
"""

from __future__ import annotations

import asyncio
import errno
import fcntl
import os
import pty
import signal
import struct
import termios
from typing import Any


class PtyStartupError(RuntimeError):
    """Raised when a PTY cannot be created or the child cannot start."""


class PtyProcess:
    """A child process attached to a pseudo-terminal.

    Attributes:
        pid: Child process id (also its session and process-group id).
    """

    def __init__(
        self,
        proc: asyncio.subprocess.Process,
        master_fd: int,
        reader: asyncio.StreamReader,
        transport: asyncio.BaseTransport,
        pipe: Any,
    ) -> None:
        self._proc = proc
        self._master_fd = master_fd
        self._reader = reader
        self._transport = transport
        self._pipe = pipe
        self._closed = False

    @property
    def pid(self) -> int:
        """Child process id."""
        return self._proc.pid

    @property
    def returncode(self) -> int | None:
        """Exit status, or ``None`` while the child runs."""
        return self._proc.returncode

    @classmethod
    async def spawn(
        cls,
        argv: list[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        rows: int = 50,
        cols: int = 200,
    ) -> PtyProcess:
        """Start *argv* on a new PTY.

        Args:
            argv: Command and arguments.
            cwd: Working directory for the child.
            env: Complete environment for the child.
            rows: Initial terminal height.
            cols: Initial terminal width.

        Returns:
            A connected :class:`PtyProcess`.

        Raises:
            PtyStartupError: If the platform has no ``pty`` module or the
                child cannot be started.
        """
        if os.name != "posix":
            raise PtyStartupError("PTY transport requires a POSIX platform")

        try:
            master_fd, slave_fd = pty.openpty()
        except OSError as exc:
            raise PtyStartupError(f"cannot open a PTY: {exc}") from exc
        cls._set_winsize(master_fd, rows, cols)
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                cwd=cwd,
                env=env,
                start_new_session=True,
                close_fds=True,
            )
        except BaseException:
            os.close(master_fd)
            os.close(slave_fd)
            raise
        os.close(slave_fd)

        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        pipe = os.fdopen(master_fd, "rb", buffering=0)
        try:
            transport, _ = await loop.connect_read_pipe(lambda: protocol, pipe)
        except BaseException:
            pipe.close()
            proc.kill()
            await proc.wait()
            raise
        return cls(proc, master_fd, reader, transport, pipe)

    async def read(self, size: int = 65536) -> bytes:
        """Read one chunk of terminal output.

        Terminal queries (cursor position, device attributes) are answered
        transparently so the CLI's renderer can settle.

        Returns:
            The bytes read, or ``b""`` at end of file.
        """
        try:
            chunk = await self._reader.read(size)
        except OSError as exc:
            if exc.errno == errno.EIO:
                return b""
            raise
        if chunk:
            self._answer_terminal_queries(chunk)
        return chunk

    def write(self, data: bytes) -> None:
        """Write raw bytes to the terminal."""
        if self._closed:
            return
        try:
            os.write(self._master_fd, data)
        except OSError:
            pass

    def resize(self, rows: int, cols: int) -> None:
        """Resize the terminal."""
        if not self._closed:
            self._set_winsize(self._master_fd, rows, cols)

    async def wait(self) -> int:
        """Wait for the child to exit and return its status."""
        return await self._proc.wait()

    async def close(self, *, grace: float = 3.0) -> int | None:
        """Terminate the child's process group and release the PTY.

        Sends ``SIGTERM``, waits up to *grace* seconds, then ``SIGKILL``.
        Safe to call more than once.

        Returns:
            The child's exit status, if it has exited.
        """
        if self._closed:
            return self._proc.returncode
        self._closed = True
        if self._proc.returncode is None:
            try:
                os.killpg(self._proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(self._proc.wait(), grace)
            except asyncio.TimeoutError:
                try:
                    os.killpg(self._proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(self._proc.wait(), 2.0)
                except asyncio.TimeoutError:
                    pass
        try:
            self._transport.close()
        except Exception:  # noqa: BLE001 - best-effort teardown
            pass
        try:
            self._pipe.close()
        except Exception:  # noqa: BLE001 - best-effort teardown
            pass
        return self._proc.returncode

    async def __aenter__(self) -> PtyProcess:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    def _answer_terminal_queries(self, chunk: bytes) -> None:
        if b"\x1b[6n" in chunk:
            # Cursor position report: row 1, column 1.
            self.write(b"\x1b[1;1R")
        if b"\x1b[c" in chunk or b"\x1b[0c" in chunk:
            # Primary device attributes: a VT220 with ANSI colour.
            self.write(b"\x1b[?62;c")

    @staticmethod
    def _set_winsize(fd: int, rows: int, cols: int) -> None:
        try:
            fcntl.ioctl(
                fd,
                termios.TIOCSWINSZ,
                struct.pack("HHHH", rows, cols, 0, 0),
            )
        except OSError:
            pass


__all__ = ["PtyProcess", "PtyStartupError"]
