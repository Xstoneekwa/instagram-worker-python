#!/usr/bin/env python3
"""Verify FOLLOW60_MAINLINE_LOCK_V2_1 without mutating runtime state."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "FOLLOW60_V2_MAINLINE_MANIFEST_V1.json"
DEFAULT_LEDGER = ROOT / "FOLLOW60_V2_MAINLINE_APPROVAL_LEDGER_V1.json"


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def git(root: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(root), *args])


def verify(root: Path, manifest_path: Path, ledger_path: Path) -> dict:
    raw_manifest = manifest_path.read_bytes()
    manifest = json.loads(raw_manifest)
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    manifest_hash = sha256_bytes(raw_manifest)
    if manifest_hash != ledger.get("manifest_sha256"):
        return {"ok": False, "reason": "manifest_hash_mismatch"}
    approval = manifest.get("approval") or {}
    approval_id = approval.get("approval_id")
    consumed = ledger.get("consumed_approval_ids") or []
    if approval.get("one_shot_status") != "consumed" or consumed.count(approval_id) != 1:
        return {"ok": False, "reason": "approval_not_consumed_exactly_once"}
    base = manifest["worker_parent_sha"]
    source = manifest["approved_source_sha"]
    if approval.get("base_sha") != base or approval.get("target_sha") != source:
        return {"ok": False, "reason": "approval_lineage_mismatch"}
    scope = manifest["approved_diff"]["scope"]
    actual_scope = git(root, "diff", "--name-only", base, source).decode().splitlines()
    if sorted(scope) != sorted(actual_scope):
        return {"ok": False, "reason": "approval_scope_mismatch"}
    diff = git(root, "diff", "--no-ext-diff", "--binary", base, source, "--", *scope)
    if sha256_bytes(diff) != manifest["approved_diff"]["sha256"]:
        return {"ok": False, "reason": "approval_diff_hash_mismatch"}
    if git(root, "merge-base", "--is-ancestor", source, "HEAD") is None:
        return {"ok": False, "reason": "approved_source_not_ancestor"}
    mismatches = {}
    for relative, expected in manifest["critical_files"].items():
        actual = sha256_bytes(git(root, "show", f"{source}:{relative}"))
        if actual != expected:
            mismatches[relative] = {"expected": expected, "actual": actual}
    if mismatches:
        return {"ok": False, "reason": "critical_file_hash_mismatch", "mismatches": mismatches}
    return {
        "ok": True,
        "status": "FOLLOW60_MAINLINE_LOCK_V2_1_OK",
        "approved_source_sha": source,
        "manifest_sha256": manifest_hash,
        "approval_id": approval_id,
        "approval_status": "consumed_once"
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-root", default=str(ROOT))
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    args = parser.parse_args()
    result = verify(Path(args.target_root), Path(args.manifest), Path(args.ledger))
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
