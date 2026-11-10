"""Command-line entry point for agentabi."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence

from .acp.backends import BACKENDS, create_backend
from .acp.gateway import serve
from .acp.router import RouterBackend

#: Agents that run through the IR stream and accept ``--prefer``.
_IR_AGENTS = frozenset({"agy", "claude", "codex", "pi"})


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
        action="append",
        choices=sorted([*BACKENDS, "acp"]),
        help=(
            "Agent backend to expose. Repeat with --router to serve several "
            "agents in one process"
        ),
    )
    acp_parser.add_argument(
        "--router",
        action="store_true",
        help=(
            "Serve several agents; the client picks one per session with "
            "_meta.agent in session/new"
        ),
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
        agents = _unique(args.agent or [])
        if not agents:
            acp_parser.error("--agent is required")

        if args.router:
            agents = _validate_router(acp_parser, agents, args)
            factories = {
                name: (
                    lambda name=name: create_backend(
                        name,
                        acp_command=args.acp_command,
                        prefer=args.prefer,
                    )
                )
                for name in agents
            }
            backend = RouterBackend(factories)
        else:
            if len(agents) != 1:
                acp_parser.error("pass --router to enable more than one --agent")
            agent = agents[0]
            if agent == "acp" and not args.acp_command:
                acp_parser.error("--agent acp requires --acp-command")
            if agent != "acp" and args.acp_command:
                acp_parser.error("--acp-command is only valid with --agent acp")
            if args.prefer and agent not in _IR_AGENTS:
                acp_parser.error(
                    "--prefer is only valid for agy, claude, codex, and pi"
                )
            backend = create_backend(
                agent,
                acp_command=args.acp_command,
                prefer=args.prefer,
            )

        try:
            asyncio.run(serve(backend))
        except KeyboardInterrupt:
            return 130
        return 0

    return 2


def _unique(agents: Sequence[str]) -> list[str]:
    """Drop duplicate agent names, keeping first-seen order."""
    return list(dict.fromkeys(agents))


def _validate_router(
    parser: argparse.ArgumentParser,
    agents: list[str],
    args: argparse.Namespace,
) -> list[str]:
    """Validate router-mode options and return the enabled agents."""
    if "acp" in agents and not args.acp_command:
        parser.error("--agent acp requires --acp-command")
    if "acp" not in agents and args.acp_command:
        parser.error("--acp-command is only valid with --agent acp")
    if args.prefer and not any(agent in _IR_AGENTS for agent in agents):
        parser.error("--prefer is only valid for agy, claude, codex, and pi")
    return agents


if __name__ == "__main__":
    raise SystemExit(main())
