#!/usr/bin/env python3
"""
agentabi settings — point any agent CLI at one model endpoint.

The endpoint and key come from the environment, never from the CLI's own
configuration file, so the same script works for every supported agent:

    LLM_BASE_URL=https://llm.example.com/v1 \\
    LLM_API_KEY=... \\
    python examples/settings.py --agent codex

Use --agent to pick the CLI; the mapping is identical for each one.
"""

import argparse
import asyncio
import os

from agentabi import Session, UnsupportedSettingsError, get_agent_capabilities


async def main(agent: str, prompt: str) -> None:
    base_url = os.environ.get("LLM_BASE_URL")
    if not base_url:
        raise SystemExit("set LLM_BASE_URL (for example https://host/v1)")
    if not os.environ.get("LLM_API_KEY"):
        raise SystemExit("set LLM_API_KEY")

    caps = get_agent_capabilities(agent)
    print(f"Using: {caps['name']}")
    print(f"  settings mechanism: {caps.get('settings_mechanism')}")
    print(f"  supported fields  : {', '.join(caps.get('supported_settings', []))}\n")

    session = Session(
        agent=agent,
        settings={
            "base_url": base_url,
            "api_key_env": "LLM_API_KEY",
        },
    )

    try:
        result = await session.run(prompt, timeout=120)
    except UnsupportedSettingsError as exc:
        raise SystemExit(f"this transport cannot use the endpoint: {exc}") from exc

    print(f"Status: {result.get('status')}")
    print(result.get("result_text") or result.get("errors"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", default="codex")
    parser.add_argument("--prompt", default="Reply with exactly: settings-ok")
    args = parser.parse_args()
    asyncio.run(main(args.agent, args.prompt))
