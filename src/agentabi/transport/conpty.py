"""agentabi - Windows pseudoconsole (ConPTY) transport.

Spawns a process attached to a Windows pseudoconsole and exposes raw
byte I/O.  This mirrors the POSIX :mod:`agentabi.transport.pty`
interface.  Only the Windows Console API is used, through :mod:`ctypes`;
no third-party package is required.

ConPTY answers the terminal queries the CLI makes (cursor position and
device attributes) itself, so unlike the POSIX transport this module
does not need to reply to them.
"""

from __future__ import annotations

import asyncio
import ctypes
import os
import subprocess
import threading
from ctypes import wintypes
from typing import Any

_IS_WINDOWS = os.name == "nt"

#: ``STARTUPINFOEX`` is passed to ``CreateProcessW``.
EXTENDED_STARTUPINFO_PRESENT = 0x00080000
#: The environment block is UTF-16.
CREATE_UNICODE_ENVIRONMENT = 0x00000400
#: Give the child its own process group so teardown can target it.
CREATE_NEW_PROCESS_GROUP = 0x00000200
#: Ask the console subsystem to create fresh std handles for the child.
#: With all three handles NULL, the child's console initialization binds
#: ConIn/ConOut to the pseudoconsole instead of copying the parent's
#: (possibly redirected) std handles.
STARTF_USESTDHANDLES = 0x00000100
#: ``ProcThreadAttributeValue(22, False, True, False)``.
PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE = 0x00020016
INFINITE = 0xFFFFFFFF
WAIT_OBJECT_0 = 0
_WAIT_TIMEOUT = 0x00000102


class ConPtyStartupError(RuntimeError):
    """Raised when a pseudoconsole cannot be created or the child fails."""


class _COORD(ctypes.Structure):
    _fields_ = [("X", wintypes.SHORT), ("Y", wintypes.SHORT)]


class _STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class _STARTUPINFOEXW(ctypes.Structure):
    _fields_ = [
        ("StartupInfo", _STARTUPINFOW),
        ("lpAttributeList", ctypes.c_void_p),
    ]


class _PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


class _SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("nLength", wintypes.DWORD),
        ("lpSecurityDescriptor", ctypes.c_void_p),
        ("bInheritHandle", wintypes.BOOL),
    ]


if _IS_WINDOWS:  # pragma: no cover - exercised on Windows only
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _CreatePipe = _kernel32.CreatePipe
    _CreatePipe.argtypes = [
        ctypes.POINTER(wintypes.HANDLE),
        ctypes.POINTER(wintypes.HANDLE),
        ctypes.POINTER(_SECURITY_ATTRIBUTES),
        wintypes.DWORD,
    ]
    _CreatePipe.restype = wintypes.BOOL

    _CreatePseudoConsole = _kernel32.CreatePseudoConsole
    _CreatePseudoConsole.argtypes = [
        _COORD,
        wintypes.HANDLE,
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    _CreatePseudoConsole.restype = ctypes.c_long

    _ResizePseudoConsole = _kernel32.ResizePseudoConsole
    _ResizePseudoConsole.argtypes = [wintypes.HANDLE, _COORD]
    _ResizePseudoConsole.restype = ctypes.c_long

    _ClosePseudoConsole = _kernel32.ClosePseudoConsole
    _ClosePseudoConsole.argtypes = [wintypes.HANDLE]
    _ClosePseudoConsole.restype = None

    _InitializeProcThreadAttributeList = _kernel32.InitializeProcThreadAttributeList
    _InitializeProcThreadAttributeList.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    _InitializeProcThreadAttributeList.restype = wintypes.BOOL

    _UpdateProcThreadAttribute = _kernel32.UpdateProcThreadAttribute
    _UpdateProcThreadAttribute.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_size_t,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    _UpdateProcThreadAttribute.restype = wintypes.BOOL

    _DeleteProcThreadAttributeList = _kernel32.DeleteProcThreadAttributeList
    _DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
    _DeleteProcThreadAttributeList.restype = None

    _CreateProcessW = _kernel32.CreateProcessW
    _CreateProcessW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        ctypes.POINTER(_SECURITY_ATTRIBUTES),
        ctypes.POINTER(_SECURITY_ATTRIBUTES),
        wintypes.BOOL,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.LPCWSTR,
        ctypes.POINTER(_STARTUPINFOW),
        ctypes.POINTER(_PROCESS_INFORMATION),
    ]
    _CreateProcessW.restype = wintypes.BOOL

    _WaitForSingleObject = _kernel32.WaitForSingleObject
    _WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _WaitForSingleObject.restype = wintypes.DWORD

    _GetExitCodeProcess = _kernel32.GetExitCodeProcess
    _GetExitCodeProcess.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _GetExitCodeProcess.restype = wintypes.BOOL

    _TerminateProcess = _kernel32.TerminateProcess
    _TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _TerminateProcess.restype = wintypes.BOOL

    _CloseHandle = _kernel32.CloseHandle
    _CloseHandle.argtypes = [wintypes.HANDLE]
    _CloseHandle.restype = wintypes.BOOL

    _ReadFile = _kernel32.ReadFile
    _ReadFile.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    _ReadFile.restype = wintypes.BOOL

    _WriteFile = _kernel32.WriteFile
    _WriteFile.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    _WriteFile.restype = wintypes.BOOL


def _win_error(prefix: str) -> ConPtyStartupError:
    err = getattr(ctypes, "WinError")()  # noqa: B009 - Windows only
    return ConPtyStartupError(f"{prefix}: {err}")


def _environment_block(env: dict[str, str]) -> Any:
    """Build a UTF-16 environment block for ``CreateProcessW``."""
    items = [
        f"{key}={value}"
        for key, value in sorted(env.items(), key=lambda item: item[0].upper())
    ]
    return ctypes.create_unicode_buffer("\0".join(items) + "\0\0")


class ConPtyProcess:
    """A child process attached to a Windows pseudoconsole.

    Attributes:
        pid: Child process id.
    """

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        process: wintypes.HANDLE,
        pid: int,
        console: wintypes.HANDLE,
        input_handle: wintypes.HANDLE,
        output_handle: wintypes.HANDLE,
    ) -> None:
        self._loop = loop
        self._process = process
        self._pid = pid
        self._console = console
        self._input = input_handle
        self._output = output_handle
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._buffer = bytearray()
        self._eof = False
        self._closed = False
        self._returncode: int | None = None
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    @property
    def pid(self) -> int:
        """Child process id."""
        return self._pid

    @property
    def returncode(self) -> int | None:
        """Exit status, or ``None`` while the child runs."""
        return self._returncode

    @classmethod
    async def spawn(
        cls,
        argv: list[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        rows: int = 50,
        cols: int = 200,
    ) -> ConPtyProcess:
        """Start *argv* on a new pseudoconsole.

        Args:
            argv: Command and arguments.
            cwd: Working directory for the child.
            env: Complete environment for the child.
            rows: Initial terminal height.
            cols: Initial terminal width.

        Returns:
            A connected :class:`ConPtyProcess`.

        Raises:
            ConPtyStartupError: If the pseudoconsole or child cannot be
                created.
        """
        if not _IS_WINDOWS:
            raise ConPtyStartupError("ConPTY transport requires Windows")

        input_read = wintypes.HANDLE()
        input_write = wintypes.HANDLE()
        output_read = wintypes.HANDLE()
        output_write = wintypes.HANDLE()
        if not _CreatePipe(
            ctypes.byref(input_read), ctypes.byref(input_write), None, 0
        ):
            raise _win_error("CreatePipe(input) failed")
        if not _CreatePipe(
            ctypes.byref(output_read), ctypes.byref(output_write), None, 0
        ):
            _CloseHandle(input_read)
            _CloseHandle(input_write)
            raise _win_error("CreatePipe(output) failed")

        console = wintypes.HANDLE()
        hr = _CreatePseudoConsole(
            _COORD(cols, rows),
            input_read,
            output_write,
            0,
            ctypes.byref(console),
        )
        if hr != 0:
            for handle in (input_read, input_write, output_read, output_write):
                _CloseHandle(handle)
            raise ConPtyStartupError(
                f"CreatePseudoConsole failed: 0x{hr & 0xFFFFFFFF:08X}"
            )

        size = ctypes.c_size_t(0)
        _InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
        attribute_buffer = ctypes.create_string_buffer(size.value)
        attributes = ctypes.cast(attribute_buffer, ctypes.c_void_p)
        if not _InitializeProcThreadAttributeList(attributes, 1, 0, ctypes.byref(size)):
            _cleanup_spawn(console, input_read, input_write, output_read, output_write)
            raise _win_error("InitializeProcThreadAttributeList failed")
        if not _UpdateProcThreadAttribute(
            attributes,
            0,
            PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE,
            ctypes.cast(console, ctypes.c_void_p),
            ctypes.sizeof(wintypes.HANDLE),
            None,
            None,
        ):
            _DeleteProcThreadAttributeList(attributes)
            _cleanup_spawn(console, input_read, input_write, output_read, output_write)
            raise _win_error("UpdateProcThreadAttribute failed")

        startup = _STARTUPINFOEXW()
        startup.StartupInfo.cb = ctypes.sizeof(_STARTUPINFOEXW)
        startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES
        startup.lpAttributeList = attributes
        process_info = _PROCESS_INFORMATION()
        command_line = ctypes.create_unicode_buffer(subprocess.list2cmdline(argv))
        env_block = _environment_block(env) if env is not None else None
        env_pointer = (
            ctypes.cast(env_block, ctypes.c_void_p) if env_block is not None else None
        )
        created = _CreateProcessW(
            None,
            command_line,
            None,
            None,
            False,
            EXTENDED_STARTUPINFO_PRESENT
            | CREATE_UNICODE_ENVIRONMENT
            | CREATE_NEW_PROCESS_GROUP,
            env_pointer,
            cwd,
            ctypes.cast(ctypes.pointer(startup), ctypes.POINTER(_STARTUPINFOW)),
            ctypes.byref(process_info),
        )
        _DeleteProcThreadAttributeList(attributes)
        # The pseudoconsole owns these ends once the child exists.
        _CloseHandle(input_read)
        _CloseHandle(output_write)
        _CloseHandle(process_info.hThread)
        if not created:
            _CloseHandle(input_write)
            _CloseHandle(output_read)
            _ClosePseudoConsole(console)
            raise _win_error("CreateProcessW failed")

        return cls(
            asyncio.get_running_loop(),
            process_info.hProcess,
            int(process_info.dwProcessId),
            console,
            input_write,
            output_read,
        )

    async def read(self, size: int = 65536) -> bytes:
        """Read one chunk of terminal output."""
        if self._buffer:
            return self._take(size)
        if self._eof:
            return b""
        item = await self._queue.get()
        if not item:
            self._eof = True
            return b""
        self._buffer.extend(item)
        return self._take(size)

    def write(self, data: bytes) -> None:
        """Write raw bytes to the terminal."""
        if self._closed or not data:
            return
        self._write_raw(data)

    def resize(self, rows: int, cols: int) -> None:
        """Resize the terminal."""
        if not self._closed:
            _ResizePseudoConsole(self._console, _COORD(cols, rows))

    async def wait(self) -> int:
        """Wait for the child to exit and return its status."""
        if self._returncode is not None:
            return self._returncode
        self._returncode = await asyncio.to_thread(self._wait_blocking)
        return self._returncode

    async def close(self, *, grace: float = 3.0) -> int | None:
        """Terminate the child and release the pseudoconsole.

        Asks the TUI to interrupt with Ctrl+C, waits up to *grace*
        seconds, then terminates the process tree by closing the
        pseudoconsole.  Safe to call more than once.
        """
        if self._closed:
            return self._returncode
        self._closed = True
        self._returncode = await asyncio.to_thread(self._close_blocking, grace)
        return self._returncode

    async def __aenter__(self) -> ConPtyProcess:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    def _take(self, size: int) -> bytes:
        chunk = bytes(self._buffer[:size])
        del self._buffer[:size]
        return chunk

    def _write_raw(self, data: bytes) -> None:
        buffer = ctypes.create_string_buffer(data, len(data))
        written = wintypes.DWORD(0)
        _WriteFile(
            self._input,
            buffer,
            len(data),
            ctypes.byref(written),
            None,
        )

    def _read_loop(self) -> None:
        buffer = ctypes.create_string_buffer(65536)
        while True:
            read = wintypes.DWORD(0)
            ok = _ReadFile(
                self._output,
                buffer,
                65536,
                ctypes.byref(read),
                None,
            )
            if not ok or read.value == 0:
                break
            chunk = bytes(buffer.raw[: read.value])
            try:
                self._loop.call_soon_threadsafe(self._queue.put_nowait, chunk)
            except RuntimeError:
                return
        try:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, b"")
        except RuntimeError:
            pass

    def _wait_blocking(self) -> int:
        _WaitForSingleObject(self._process, INFINITE)
        return self._exit_code()

    def _close_blocking(self, grace: float) -> int:
        if self._returncode is None:
            self._write_raw(b"\x03")
            waited = _WaitForSingleObject(self._process, int(grace * 1000))
            if waited == _WAIT_TIMEOUT:
                _TerminateProcess(self._process, 1)
                _WaitForSingleObject(self._process, 5000)
        _ClosePseudoConsole(self._console)
        self._reader.join(timeout=5.0)
        for handle in (self._input, self._output, self._process):
            _CloseHandle(handle)
        return self._exit_code()

    def _exit_code(self) -> int:
        code = wintypes.DWORD(0)
        _GetExitCodeProcess(self._process, ctypes.byref(code))
        return int(code.value)


def _cleanup_spawn(
    console: wintypes.HANDLE,
    input_read: wintypes.HANDLE,
    input_write: wintypes.HANDLE,
    output_read: wintypes.HANDLE,
    output_write: wintypes.HANDLE,
) -> None:
    _ClosePseudoConsole(console)
    for handle in (input_read, input_write, output_read, output_write):
        _CloseHandle(handle)


__all__ = ["ConPtyProcess", "ConPtyStartupError"]
