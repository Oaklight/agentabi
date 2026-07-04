#!/usr/bin/env python3
"""
agentabi middleware — logging, usage metering, and timeout in action.

Usage:
    python examples/middleware.py
    python examples/middleware.py --agent codex
    python examples/middleware.py --timeout 10
"""

import argparse
import asyncio
import logging

from agentabi import Session, detect_agents
from agentabi.middleware import (
    LoggingMiddleware,
    TimeoutMiddleware,
    UsageMeterMiddleware,
)


async def main(agent: str | None, timeout: float) -> None:
    available = detect_agents()
    if not available:
        print("No agents found.")
        return

    agent = agent if agent and agent in available else available[0]

    # ── 1. Set up middleware stack ───────────────────────────────
    # Middleware is applied in onion order: first = outermost.
    # Here: timeout wraps logging wraps usage wraps provider.stream().
    meter = UsageMeterMiddleware()

    session = Session(
        agent=agent,
        middleware=[
            TimeoutMiddleware(timeout),
            LoggingMiddleware(level=logging.INFO),
            meter,
        ],
    )
    print(f"Agent: {agent}  Timeout: {timeout}s")
    print(f"Middleware: {session.middleware}\n")

    # ── 2. Run a task ───────────────────────────────────────────
    prompt = "What is the capital of France? Reply in one word."
    print(f"Prompt: {prompt!r}\n")

    try:
        result = await session.run(prompt=prompt, max_turns=1)
    except asyncio.TimeoutError:
        print("\n!! Task timed out")
        return

    # ── 3. Show result ──────────────────────────────────────────
    print(f"\nStatus: {result.get('status', 'unknown')}")
    text = result.get("result_text", "")
    if text:
        print(f"Answer: {text}")

    # ── 4. Show accumulated usage from the meter ────────────────
    summary = meter.summary()
    print("\n── Usage Meter ──")
    print(f"  Calls: {summary['call_count']}")
    print(f"  Input tokens:  {summary['input_tokens']}")
    print(f"  Output tokens: {summary['output_tokens']}")
    print(f"  Total cost:    ${summary['total_cost_usd']:.4f}")

    # ── 5. Middleware can also be added after construction ──────
    print("\n── Adding middleware post-construction ──")
    session.add_middleware(LoggingMiddleware(log_events=False))
    print(f"Middleware count: {len(session.middleware)}")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(name)s %(levelname)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="agentabi middleware demo")
    parser.add_argument("--agent", type=str, default=None)
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()
    asyncio.run(main(args.agent, args.timeout))
