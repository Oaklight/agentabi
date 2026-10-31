"""Command-line entry point for agentabi."""

from __future__ import annotations

import argparse
import asyncio

from .acp.backends import BACKENDS, create_backend
from .acp.gateway import serve


def main(argv: list[str] | None = None) -> int:
    """Run the agentabi command-line interface.

    Args:
        argv: Optional argument list (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit code.
    """
    parser = argparse.ArgumentParser(prog="agentabi")
    subparsers = parser.add_subparsers(dest="command", required=True)

    acp_parser = subparsers.add_parser(
        "acp",
        help="Run the ACP gateway on stdin/stdout",
    )
    acp_parser.add_argument(
        "--agent",
        required=True,
        choices=sorted(BACKENDS),
        help="Agent backend to expose",
    )

    args = parser.parse_args(argv)

    if args.command == "acp":
        backend = create_backend(args.agent)
        try:
            asyncio.run(serve(backend))
        except KeyboardInterrupt:
            return 130
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
