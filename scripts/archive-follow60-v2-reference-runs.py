#!/usr/bin/env python3
"""Create redacted, hashed excerpts for explicitly selected Follow60 runs."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

SENSITIVE = re.compile(
    r'(?i)(token|secret|password|authorization|cookie|email|xml|hierarchy|'
    r'screenshot|image|username|display_name|phone)'
)


def redact(value):
    if isinstance(value, dict):
        return {key: "[REDACTED]" if SENSITIVE.search(str(key)) else redact(item)
                for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--run-id", action="append", required=True)
    args = parser.parse_args()
    selected = set(args.run_id)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    handles = {run_id: (output / f"{run_id}.jsonl").open("w", encoding="utf-8")
               for run_id in selected}
    try:
        with Path(args.log).open(errors="ignore") as source:
            for raw in source:
                try:
                    row = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                blob = json.dumps(row, separators=(",", ":"))
                for run_id in selected:
                    if run_id in blob:
                        handles[run_id].write(json.dumps(redact(row), sort_keys=True) + "\n")
    finally:
        for handle in handles.values():
            handle.close()
    hashes = {}
    for run_id in sorted(selected):
        path = output / f"{run_id}.jsonl"
        hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    (output / "SHA256SUMS.json").write_text(
        json.dumps(hashes, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
