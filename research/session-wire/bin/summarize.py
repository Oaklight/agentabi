#!/usr/bin/env python3
"""Summarize a session-wire capture: direction counts and one sample per type."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wire_common import parse_line  # noqa: E402


def _type_of(obj) -> str:
    if not isinstance(obj, dict):
        return "non-json"
    for key in ("type", "method", "event", "sessionUpdate", "dir"):
        value = obj.get(key)
        if isinstance(value, str) and value:
            return f"{key}:{value}"
    return "json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture")
    parser.add_argument("--max-samples", type=int, default=1)
    args = parser.parse_args()

    directions = Counter()
    types = Counter()
    samples: dict[str, list[str]] = defaultdict(list)

    with open(args.capture, encoding="utf-8") as handle:
        for raw in handle:
            try:
                record = json.loads(raw)
            except json.JSONDecodeError:
                continue
            directions[record.get("dir", "?")] += 1
            line = record.get("line", "")
            obj = parse_line(line)
            kind = _type_of(obj)
            types[kind] += 1
            if len(samples[kind]) < args.max_samples:
                samples[kind].append(line[:600])

    print("directions:")
    for key, value in directions.most_common():
        print(f"  {key:12} {value}")
    print("\ntypes:")
    for key, value in types.most_common():
        print(f"  {key:40} {value}")
    print("\nsamples:")
    for key, values in samples.items():
        print(f"\n[{key}]")
        for value in values:
            print(value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
