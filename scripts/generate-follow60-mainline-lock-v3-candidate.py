#!/usr/bin/env python3
"""Generate an unsigned Follow60 V3 candidate manifest.

This command deliberately cannot approve or promote its own output.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--scope", required=True, help="JSON array of protected paths")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    root = Path(args.root).resolve()
    scope = json.loads(Path(args.scope).read_text(encoding="utf-8"))
    if not isinstance(scope, list) or not scope:
        raise SystemExit("protected_scope_must_be_non_empty_json_array")
    protected = {}
    for relative in sorted(set(str(item) for item in scope)):
        path = root / relative
        if not path.is_file():
            raise SystemExit(f"protected_file_missing:{relative}")
        protected[relative] = sha256(path.read_bytes())
    scope_hash = sha256(
        json.dumps(protected, sort_keys=True, separators=(",", ":")).encode()
    )
    candidate = {
        "schema": "FOLLOW60_MAINLINE_LOCK_V3_CANDIDATE",
        "approval": None,
        "promotion_authorized": False,
        "protected_files": protected,
        "protected_scope_sha256": scope_hash,
    }
    Path(args.output).write_text(
        json.dumps(candidate, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"ok": True, "status": "CANDIDATE_ONLY", "scope_hash": scope_hash}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
