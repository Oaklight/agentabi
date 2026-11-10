#!/usr/bin/env python3
"""Redact common secret shapes from a session-wire capture."""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wire_common import redact_text  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture")
    parser.add_argument("-o", "--out", required=True)
    parser.add_argument("--allow-secrets", action="store_true")
    args = parser.parse_args()

    out_path = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    with (
        open(args.capture, encoding="utf-8") as source,
        open(out_path, "w", encoding="utf-8") as target,
    ):
        for raw in source:
            try:
                record = json.loads(raw)
            except json.JSONDecodeError:
                target.write(raw)
                continue
            if not args.allow_secrets and isinstance(record.get("line"), str):
                record["line"] = redact_text(record["line"])
            target.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(f"redacted -> {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
