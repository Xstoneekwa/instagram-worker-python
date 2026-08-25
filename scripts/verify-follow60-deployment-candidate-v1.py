#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from follow60_deployment_gate_v1 import verify_deployment_candidate  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-root", default=str(ROOT))
    parser.add_argument("--manifest")
    parser.add_argument("--signature")
    parser.add_argument("--public-key")
    parser.add_argument("--revision", default="HEAD")
    args = parser.parse_args()
    result = verify_deployment_candidate(
        Path(args.target_root),
        manifest_path=Path(args.manifest) if args.manifest else None,
        signature_path=Path(args.signature) if args.signature else None,
        public_key_path=Path(args.public_key) if args.public_key else None,
        revision=args.revision,
    )
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())

