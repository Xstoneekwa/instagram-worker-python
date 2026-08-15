#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from follow60_write_lock_v3_1 import verify_physical_write_lock  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-root", default=str(ROOT))
    parser.add_argument("--manifest", default="docs/governance/FOLLOW60_MAINLINE_LOCK_V3.json")
    parser.add_argument("--test-mode", action="store_true")
    args = parser.parse_args()
    root = Path(args.target_root).resolve()
    result = verify_physical_write_lock(
        root,
        root / args.manifest,
        require_root_owner=not args.test_mode,
        require_immutable=not args.test_mode,
    )
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
