"""agentabi - Transport helpers.

Low-level I/O mechanisms that providers build on.  Each transport is
independent of any one CLI.
"""

from .pty import PtyProcess, PtyStartupError

__all__ = ["PtyProcess", "PtyStartupError"]
