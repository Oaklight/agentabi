#!/usr/bin/env python3
"""
agentabi streaming — stream IR events from any agent CLI in real time.

Usage:
    python examples/streaming.py
    python examples/streaming.py --agent codex --prompt "Explain asyncio"
"""

import argparse
import asyncio

from agentabi import Session, detect_agents


def _print_event(event: dict) -> None:
    """Print a single IR event to stdout."""
    etype = event["type"]

    if etype == "session_start":
        sid = event.get("session_id", "")
        model = event.get("model", "")
        print(f"── session {sid}  model={model}")

    elif etype == "message_start":
        print()  # blank line before assistant output

    elif etype == "message_delta":
        print(event["text"], end="", flush=True)

    elif etype == "message_end":
        print()  # newline after message

    elif etype == "reasoning_delta":
        print(event["reasoning"], end="", flush=True)

    elif etype == "content_block_start":
        block_type = event["block_type"]
        if block_type == "thinking":
            print("\n-- thinking --")

    elif etype == "tool_use":
        name = event["tool_name"]
        print(f"\n🔧 {name}({event['tool_input']})")

    elif etype == "tool_result":
        content = event["content"]
        is_err = event.get("is_error", False)
        tag = "ERR" if is_err else "OK"
        preview = content[:120] + ("..." if len(content) > 120 else "")
        print(f"   [{tag}] {preview}")

    elif etype == "usage":
        usage = event["usage"]
        inp = usage.get("input_tokens", 0)
        out = usage.get("output_tokens", 0)
        reasoning = usage.get("reasoning_tokens", 0)
        cost = event.get("cost_usd")
        parts = [f"{inp} in", f"{out} out"]
        if reasoning:
            parts.append(f"{reasoning} reasoning")
        if cost:
            parts.append(f"${cost:.4f}")
        print(f"\n── tokens: {' / '.join(parts)}")

    elif etype == "error":
        print(f"\n!! ERROR: {event['error']}")

    elif etype == "session_end":
        print("── session end")


async def main(agent: str | None, prompt: str) -> None:
    available = detect_agents()
    if not available:
        print("No agents found.")
        return

    agent = agent if agent and agent in available else available[0]
    session = Session(agent=agent)
    print(f"[{agent}] streaming: {prompt!r}\n")

    async for event in session.stream(prompt=prompt, max_turns=2):
        _print_event(event)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="agentabi streaming")
    parser.add_argument("--agent", type=str, default=None)
    parser.add_argument(
        "--prompt",
        type=str,
        default="Briefly explain what Python asyncio is in 2-3 sentences.",
    )
    args = parser.parse_args()
    asyncio.run(main(args.agent, args.prompt))
