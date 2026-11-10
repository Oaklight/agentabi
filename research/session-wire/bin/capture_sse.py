#!/usr/bin/env python3
"""Capture an SSE stream, optionally triggering it with a POST.

Example:
  capture_sse.py --url http://127.0.0.1:4099/event \
    --post-url http://127.0.0.1:4099/session --post-body '{}' \
    --out captures/opencode/events.ndjson --timeout 8
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wire_common import write_record  # noqa: E402


def _post(url: str, body: str, delay: float, headers: dict[str, str]) -> None:
    time.sleep(delay)
    data = body.encode()
    request = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json", **headers}
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            response.read()
    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"POST failed: {exc}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--post-url")
    parser.add_argument("--post-body", default="{}")
    parser.add_argument("--post-delay", type=float, default=1.0)
    parser.add_argument("--header", action="append", default=[])
    parser.add_argument("--allow-secrets", action="store_true")
    args = parser.parse_args()

    headers = {}
    for item in args.header:
        key, _, value = item.partition(":")
        headers[key.strip()] = value.strip()

    out_path = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    redact = not args.allow_secrets

    if args.post_url:
        threading.Thread(
            target=_post,
            args=(args.post_url, args.post_body, args.post_delay, headers),
            daemon=True,
        ).start()

    request = urllib.request.Request(
        args.url, headers={"Accept": "text/event-stream", **headers}
    )
    deadline = time.time() + args.timeout
    with open(out_path, "w", encoding="utf-8") as fh:
        try:
            with urllib.request.urlopen(request, timeout=args.timeout) as response:
                while time.time() < deadline:
                    raw = response.readline()
                    if not raw:
                        break
                    write_record(
                        fh, "sse", raw.decode("utf-8", "replace").rstrip("\n"), redact
                    )
        except (urllib.error.URLError, TimeoutError) as exc:
            print(f"SSE failed: {exc}", file=sys.stderr)

    print(f"captured -> {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
