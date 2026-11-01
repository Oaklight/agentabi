"""agentabi - Transport helpers.

Low-level I/O mechanisms that providers build on.  Each transport is
independent of any one CLI.

The PTY transport is selected per platform: the POSIX ``pty`` module on
Unix, and the Windows pseudoconsole (ConPTY) API on Windows.
"""

import os

if os.name == "nt":
    from .conpty import ConPtyProcess as PtyProcess
    from .conpty import ConPtyStartupError as PtyStartupError
else:
    from .pty import PtyProcess, PtyStartupError

__all__ = ["PtyProcess", "PtyStartupError"]
