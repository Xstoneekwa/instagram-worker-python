#!/usr/bin/env python3
"""Compatibility entrypoint for the canonical Follow60 lock verifier."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from follow60_lock_v3 import verify_repository  # noqa: E402


DEFAULT_MANIFEST = ROOT / "docs/governance/FOLLOW60_MAINLINE_LOCK_V3.json"
DEFAULT_APPROVAL = ROOT / "docs/governance/FOLLOW60_MAINLINE_LOCK_V3_APPROVAL.json"
DEFAULT_SIGNATURE = ROOT / "docs/governance/FOLLOW60_MAINLINE_LOCK_V3.sig"
DEFAULT_PUBLIC_KEY = ROOT / "docs/governance/FOLLOW60_MAINLINE_LOCK_V3_1_PUBLIC_KEY.pem"


def verify(root: Path, manifest_path: Path, approval_path: Path, *, revision: str = "HEAD") -> dict:
    return verify_repository(
        root,
        manifest_path,
        revision=revision,
        approval_path=approval_path,
        signature_path=DEFAULT_SIGNATURE,
        public_key_path=DEFAULT_PUBLIC_KEY,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-root", default=str(ROOT))
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--approval", default=str(DEFAULT_APPROVAL))
    parser.add_argument("--signature", default=str(DEFAULT_SIGNATURE))
    parser.add_argument("--public-key", default=str(DEFAULT_PUBLIC_KEY))
    parser.add_argument("--revision", default="HEAD")
    args = parser.parse_args()
    result = verify_repository(
        Path(args.target_root),
        Path(args.manifest),
        revision=args.revision,
        approval_path=Path(args.approval),
        signature_path=Path(args.signature),
        public_key_path=Path(args.public_key),
    )
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
