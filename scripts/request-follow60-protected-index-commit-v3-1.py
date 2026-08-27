#!/usr/bin/env python3
"""Freeze the staged tree AFTER scoped Approval 1; not a deployment approval."""
from pathlib import Path
import argparse
import json
import sys

ROOTROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOTROOT))
from follow60_candidate_identity_v2 import external_path, freeze_index
from follow60_change_protocol_v1 import verify_initial_authorization

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(ROOTROOT))
    parser.add_argument("--authorization", required=True)
    parser.add_argument("--signature", required=True)
    parser.add_argument("--public-key", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    root = Path(args.root).resolve()
    approved = verify_initial_authorization(root, Path(args.authorization), Path(args.signature), Path(args.public_key))
    if not approved["ok"]:
        raise SystemExit(approved["reason"])
    frozen = freeze_index(root, approved["authorization"])
    output = external_path(root, Path(args.output))
    with output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(frozen, indent=2, sort_keys=True) + "\n")
    print(json.dumps(frozen, sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
