#!/usr/bin/env python3
"""Verify exact HEAD blobs and the out-of-band Follow60 V3 approval."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "docs/governance/FOLLOW60_MAINLINE_LOCK_V3.json"
DEFAULT_APPROVAL = ROOT / "docs/governance/FOLLOW60_MAINLINE_LOCK_V3_APPROVAL.json"


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def git(root: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(root), *args])


def verify(root: Path, manifest_path: Path, approval_path: Path, *, revision: str = "HEAD") -> dict:
    manifest_raw = manifest_path.read_bytes()
    manifest = json.loads(manifest_raw)
    approval = json.loads(approval_path.read_text(encoding="utf-8"))
    if manifest.get("schema") != "FOLLOW60_MAINLINE_LOCK_V3":
        return {"ok": False, "reason": "manifest_schema_mismatch"}
    if approval.get("schema") != "FOLLOW60_MAINLINE_LOCK_V3_EXTERNAL_APPROVAL":
        return {"ok": False, "reason": "approval_schema_mismatch"}
    if approval.get("status") != "consumed_once":
        return {"ok": False, "reason": "approval_not_consumed_once"}
    manifest_approval = manifest.get("approval") or {}
    if manifest_approval.get("status") != "approved_once":
        return {"ok": False, "reason": "manifest_not_approved"}
    if approval.get("approval_id") != manifest_approval.get("approval_id"):
        return {"ok": False, "reason": "approval_id_mismatch"}
    if approval.get("approved_scope_sha256") != manifest.get("protected_scope_sha256"):
        return {"ok": False, "reason": "approval_scope_hash_mismatch"}
    if approval.get("manifest_sha256") != sha256(manifest_raw):
        return {"ok": False, "reason": "approval_manifest_hash_mismatch"}

    protected = manifest.get("protected_files") or {}
    mismatches = {}
    for relative, expected in protected.items():
        try:
            actual = sha256(git(root, "show", f"{revision}:{relative}"))
        except subprocess.CalledProcessError:
            actual = None
        if actual != expected:
            mismatches[relative] = {"expected": expected, "actual": actual}
    if mismatches:
        return {"ok": False, "reason": "head_protected_blob_mismatch", "mismatches": mismatches}
    calculated_scope = sha256(
        json.dumps(protected, sort_keys=True, separators=(",", ":")).encode()
    )
    if calculated_scope != manifest.get("protected_scope_sha256"):
        return {"ok": False, "reason": "protected_scope_hash_mismatch"}
    return {
        "ok": True,
        "status": "FOLLOW60_MAINLINE_LOCK_V3_OK",
        "revision": git(root, "rev-parse", revision).decode().strip(),
        "approval_id": approval.get("approval_id"),
        "protected_file_count": len(protected),
        "protected_scope_sha256": calculated_scope,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-root", default=str(ROOT))
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--approval", default=str(DEFAULT_APPROVAL))
    parser.add_argument("--revision", default="HEAD")
    args = parser.parse_args()
    result = verify(
        Path(args.target_root), Path(args.manifest), Path(args.approval), revision=args.revision
    )
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
