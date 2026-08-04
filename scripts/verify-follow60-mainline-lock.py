#!/usr/bin/env python3
"""Verify or explicitly unlock FOLLOW60_MAINLINE_V1 critical source files."""

import argparse
import datetime as dt
import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Dict

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "FOLLOW60_MAINLINE_MANIFEST_V1.json"
LEDGER = ROOT / "FOLLOW60_MAINLINE_APPROVAL_LEDGER.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def mismatches(root: Path, manifest: dict) -> Dict[str, dict]:
    result = {}
    for relative, expected in manifest["critical_files"].items():
        path = root / relative
        actual = sha256(path) if path.is_file() else None
        if actual != expected:
            result[relative] = {"expected": expected, "actual": actual}
    return result


def canonical_diff_hash(changes: Dict[str, dict]) -> str:
    payload = json.dumps(changes, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def parse_expiry(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def validate_approval(approval: dict, changes: Dict[str, dict], ledger: dict, now=None, current_base=None) -> None:
    now = now or dt.datetime.now(dt.timezone.utc)
    required = {"approval_id", "approved_by", "expires_at", "base_sha", "scope", "diff_sha256"}
    missing = sorted(required - set(approval))
    if missing:
        raise ValueError("approval_missing_fields:" + ",".join(missing))
    if approval["approval_id"] in ledger.get("consumed_approval_ids", []):
        raise ValueError("approval_already_consumed")
    if parse_expiry(approval["expires_at"]) <= now:
        raise ValueError("approval_expired")
    if not approval["approved_by"].strip() or not approval["base_sha"].strip():
        raise ValueError("approval_identity_or_base_missing")
    if current_base is not None and approval["base_sha"] != current_base:
        raise ValueError("approval_base_sha_mismatch")
    if sorted(approval["scope"]) != sorted(changes):
        raise ValueError("approval_scope_mismatch")
    if approval["diff_sha256"] != canonical_diff_hash(changes):
        raise ValueError("approval_diff_hash_mismatch")


def atomic_json_write(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--approve-current-tree", action="store_true")
    parser.add_argument("--approval-file")
    args = parser.parse_args()

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    changes = mismatches(ROOT, manifest)
    if not changes:
        print("FOLLOW60_MAINLINE_LOCK_V1_OK")
        return 0
    if not args.approve_current_tree:
        print(json.dumps({"ok": False, "reason": "follow60_mainline_locked", "changes": changes}, sort_keys=True))
        return 2
    if args.approval_file:
        approval = json.loads(Path(args.approval_file).read_text(encoding="utf-8"))
    else:
        approval = {
            "approval_id": os.environ.get("FOLLOW60_MAINLINE_EXPLICIT_GO", ""),
            "approved_by": os.environ.get("FOLLOW60_MAINLINE_GO_APPROVED_BY", ""),
            "expires_at": os.environ.get("FOLLOW60_MAINLINE_GO_EXPIRES_AT", ""),
            "base_sha": os.environ.get("FOLLOW60_MAINLINE_GO_BASE_SHA", ""),
            "scope": [item for item in os.environ.get("FOLLOW60_MAINLINE_GO_SCOPE", "").split(",") if item],
            "diff_sha256": os.environ.get("FOLLOW60_MAINLINE_GO_DIFF_SHA256", ""),
        }
    ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
    current_base = subprocess.check_output(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True
    ).strip()
    validate_approval(approval, changes, ledger, current_base=current_base)
    for relative, change in changes.items():
        manifest["critical_files"][relative] = change["actual"]
    ledger.setdefault("consumed_approval_ids", []).append(approval["approval_id"])
    atomic_json_write(MANIFEST, manifest)
    atomic_json_write(LEDGER, ledger)
    print("FOLLOW60_MAINLINE_ONE_SHOT_APPROVAL_CONSUMED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
