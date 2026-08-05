#!/usr/bin/env python3
"""Verify the immutable Follow60 V1.1 approved patch against a target tree."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "FOLLOW60_MAINLINE_MANIFEST_V1_1.json"
LEDGER = ROOT / "FOLLOW60_MAINLINE_APPROVAL_LEDGER_V1_1.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), *args], text=True, stderr=subprocess.DEVNULL
    ).strip()


def canonical_git_diff_hash(root: Path, base_sha: str, target_sha: str, scope: list[str]) -> str:
    payload = subprocess.check_output(
        [
            "git", "-C", str(root), "diff", "--no-ext-diff", "--binary",
            base_sha, target_sha, "--", *scope,
        ]
    )
    return hashlib.sha256(payload).hexdigest()


def _parse_time(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def validate_one_shot_approval(
    approval: dict[str, Any],
    *,
    expected_base: str,
    expected_target: str,
    expected_scope: list[str],
    expected_diff_hash: str,
    ledger: dict[str, Any],
    now: dt.datetime,
) -> None:
    required = {
        "approval_id", "approved_by", "approved_at", "expires_at", "base_sha",
        "target_sha", "scope", "diff_sha256",
    }
    if required - set(approval):
        raise ValueError("approval_missing_fields")
    if approval["approval_id"] in ledger.get("consumed_approval_ids", []):
        raise ValueError("approval_already_consumed")
    if _parse_time(str(approval["expires_at"])) <= now:
        raise ValueError("approval_expired")
    if approval["base_sha"] != expected_base:
        raise ValueError("approval_base_sha_mismatch")
    if approval["target_sha"] != expected_target:
        raise ValueError("approval_target_sha_mismatch")
    if sorted(approval["scope"]) != sorted(expected_scope):
        raise ValueError("approval_scope_mismatch")
    if approval["diff_sha256"] != expected_diff_hash:
        raise ValueError("approval_diff_hash_mismatch")


def verify_target(
    target_root: Path,
    manifest: dict[str, Any],
    ledger: dict[str, Any],
) -> dict[str, Any]:
    try:
        target_head = _git(target_root, "rev-parse", "HEAD")
    except (subprocess.CalledProcessError, FileNotFoundError):
        return {"ok": False, "reason": "target_git_head_unavailable"}

    base_sha = str(manifest.get("worker_parent_sha") or "")
    approved_sha = str(manifest.get("approved_worker_sha") or "")
    approval = dict(manifest.get("approval") or {})
    scope = list(dict(manifest.get("approved_diff") or {}).get("scope") or [])
    expected_diff_hash = str(dict(manifest.get("approved_diff") or {}).get("sha256") or "")

    if manifest.get("status") != "FOLLOW60_MAINLINE_APPROVED_PATCH":
        return {"ok": False, "reason": "manifest_status_mismatch"}
    if target_head != approved_sha:
        return {"ok": False, "reason": "target_sha_mismatch", "actual": target_head}
    try:
        if _git(target_root, "merge-base", "--is-ancestor", base_sha, approved_sha):
            pass
    except subprocess.CalledProcessError:
        return {"ok": False, "reason": "approved_base_not_ancestor"}
    try:
        actual_scope = [
            item for item in _git(target_root, "diff", "--name-only", base_sha, approved_sha).splitlines()
            if item
        ]
    except subprocess.CalledProcessError:
        return {"ok": False, "reason": "approved_base_unavailable"}
    if sorted(actual_scope) != sorted(scope):
        return {
            "ok": False,
            "reason": "approved_scope_mismatch",
            "actual_scope": actual_scope,
        }
    actual_diff_hash = canonical_git_diff_hash(target_root, base_sha, approved_sha, scope)
    if actual_diff_hash != expected_diff_hash:
        return {"ok": False, "reason": "approved_diff_hash_mismatch"}

    mismatches: dict[str, dict[str, str | None]] = {}
    for relative, expected in dict(manifest.get("critical_files") or {}).items():
        path = target_root / relative
        actual = sha256(path) if path.is_file() else None
        if actual != expected:
            mismatches[relative] = {"expected": expected, "actual": actual}
    if mismatches:
        return {"ok": False, "reason": "manifest_hash_mismatch", "mismatches": mismatches}

    approval_id = str(approval.get("approval_id") or "")
    consumed = list(ledger.get("consumed_approval_ids") or [])
    if approval.get("one_shot_status") != "consumed" or consumed.count(approval_id) != 1:
        return {"ok": False, "reason": "approval_consumption_mismatch"}
    if approval.get("base_sha") != base_sha or approval.get("target_sha") != approved_sha:
        return {"ok": False, "reason": "approval_lineage_mismatch"}
    if sorted(approval.get("scope") or []) != sorted(scope):
        return {"ok": False, "reason": "approval_scope_mismatch"}
    if approval.get("diff_sha256") != expected_diff_hash:
        return {"ok": False, "reason": "approval_diff_hash_mismatch"}

    return {
        "ok": True,
        "status": "FOLLOW60_MAINLINE_LOCK_V1_1_OK",
        "target_sha": target_head,
        "base_sha": base_sha,
        "scope": scope,
        "approval_id": approval_id,
        "approval_status": "consumed_once",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-root", required=True)
    parser.add_argument("--manifest", default=str(MANIFEST))
    parser.add_argument("--ledger", default=str(LEDGER))
    args = parser.parse_args()
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    ledger = json.loads(Path(args.ledger).read_text(encoding="utf-8"))
    result = verify_target(Path(args.target_root).resolve(), manifest, ledger)
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
