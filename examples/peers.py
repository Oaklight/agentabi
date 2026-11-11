#!/usr/bin/env python3
"""
agentabi peers — keep one Claude process alive for several turns.

The first message starts a turn; a message sent while the turn runs
steers it.  ``interrupt`` ends a turn without stopping the process.

    python examples/peers.py --cwd /path/to/repo --prompt "Review the diff"
"""

import argparse
import asyncio
import os

from agentabi.peers import PeerBroker


async def main(cwd: str, prompt: str, model: str | None) -> None:
    options = {"model": model} if model else {}
    settings = {}
    base_url = os.environ.get("LLM_BASE_URL")
    if base_url:
        settings["base_url"] = base_url
        if os.environ.get("LLM_API_KEY"):
            settings["api_key_env"] = "LLM_API_KEY"
        options["settings"] = settings

    async with PeerBroker(max_peers=2) as broker:
        peer = await broker.spawn("example", cwd=cwd, prompt=prompt, **options)
        info = peer.info()
        print(f"peer {info.name} pid={info.pid} session={info.session_id}")

        turn = await peer.wait(timeout=300)
        print(f"\n[turn {turn.turn_id}] {turn.status} ({turn.stop_reason})")
        print(turn.text)

        sent = await peer.send("Reply with one sentence summarizing what you did")
        print(f"\nfollow-up admitted as {sent.mode} {sent.turn_id}")
        turn = await peer.wait(timeout=300)
        print(f"[turn {turn.turn_id}] {turn.status}")
        print(turn.text)

        for note in peer.inbox():
            if note.kind != "turn":
                print(f"note {note.kind}: {note.text}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cwd", default=os.getcwd())
    parser.add_argument("--prompt", default="Say hello and name your working directory")
    parser.add_argument("--model")
    args = parser.parse_args()
    asyncio.run(main(args.cwd, args.prompt, args.model))
