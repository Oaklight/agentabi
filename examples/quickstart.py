#!/usr/bin/env python3
"""
agentabi quickstart — discover agents, run a task, print the result.

Usage:
    python examples/quickstart.py
    python examples/quickstart.py --agent codex
    python examples/quickstart.py --agent opencode --prompt "List files"
"""

import argparse
import asyncio

from agentabi import (
    Session,
    detect_agents,
    get_agent_capabilities,
)


async def main(agent: str | None, prompt: str) -> None:
    # ── 1. Discovery ────────────────────────────────────────────
    available = detect_agents()
    print(f"Available agents: {available}\n")

    if not available:
        print(
            "No agents found. Install at least one CLI "
            "(claude, codex, gemini, opencode)."
        )
        return

    # Pick the requested agent, or the first available one
    agent = agent if agent and agent in available else available[0]
    caps = get_agent_capabilities(agent)
    print(f"Using: {caps['name']}  (agent_type={caps['agent_type']})")
    print(f"  streaming={caps['supports_streaming']}  mcp={caps['supports_mcp']}\n")

    # ── 2. Run ──────────────────────────────────────────────────
    session = Session(agent=agent)
    print(f"Prompt: {prompt!r}\n")

    result = await session.run(prompt=prompt, max_turns=2)

    # ── 3. Result ───────────────────────────────────────────────
    print("─" * 40)
    print(f"Status : {result.get('status', 'unknown')}")
    print(f"Session: {result.get('session_id', 'n/a')}")

    text = result.get("result_text", "")
    if text:
        print(f"\n{text}")

    usage = result.get("usage")
    if usage:
        inp = usage.get("input_tokens", 0)
        out = usage.get("output_tokens", 0)
        print(f"\nTokens: {inp} in / {out} out")

    cost = result.get("cost_usd")
    if cost is not None:
        print(f"Cost  : ${cost:.4f}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="agentabi quickstart")
    parser.add_argument("--agent", type=str, default=None, help="Agent to use")
    parser.add_argument(
        "--prompt",
        type=str,
        default="What is 2+2? Reply with just the number.",
        help="Prompt",
    )
    args = parser.parse_args()
    asyncio.run(main(args.agent, args.prompt))
