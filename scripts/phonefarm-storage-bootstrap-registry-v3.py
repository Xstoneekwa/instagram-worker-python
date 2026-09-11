#!/usr/bin/env python3
"""Print a dynamic V3 protection registry; writing it is an explicit caller step."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storage_protection_registry_v3 import bootstrap_registry


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--current-link", type=Path, required=True)
    parser.add_argument("--releases-root", type=Path, required=True)
    parser.add_argument("--stable-controller-link", type=Path, required=True)
    parser.add_argument("--canonical-registry", type=Path, required=True)
    parser.add_argument("--storage-registry", type=Path)
    parser.add_argument("--projects-root", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(bootstrap_registry(**vars(args)), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
