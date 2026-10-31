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
        choices=sorted([*BACKENDS, "acp"]),
        help="Agent backend to expose",
    )
    acp_parser.add_argument(
        "--acp-command",
        help=("Command line of an external ACP server; required with --agent acp"),
    )
    acp_parser.add_argument(
        "--prefer",
        choices=["native", "sdk", "pty"],
        help=(
            "Preferred provider type for agy, claude, codex, and pi "
            "(claude supports 'pty' for its interactive TUI)"
        ),
    )

    args = parser.parse_args(argv)

    if args.command == "acp":
        if args.agent == "acp" and not args.acp_command:
            acp_parser.error("--agent acp requires --acp-command")
        if args.agent != "acp" and args.acp_command:
            acp_parser.error("--acp-command is only valid with --agent acp")
        if args.prefer and args.agent in {"acp", "opencode"}:
            acp_parser.error("--prefer is only valid for agy, claude, codex, and pi")
        backend = create_backend(
            args.agent,
            acp_command=args.acp_command,
            prefer=args.prefer,
        )
        try:
            asyncio.run(serve(backend))
        except KeyboardInterrupt:
            return 130
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
