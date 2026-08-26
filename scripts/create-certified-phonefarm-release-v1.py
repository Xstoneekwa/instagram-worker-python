#!/usr/bin/env python3
"""Create one release only after the exact-candidate Follow60 gate passes."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from follow60_deployment_gate_v1 import (  # noqa: E402
    MANIFEST_RELATIVE, SIGNATURE_RELATIVE, verify_deployment_candidate,
)


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def _load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit(f"json_root_not_object:{path}")
    return value


def _canonical_hash(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _install_immutable_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(path, 0o444)
    immutable_flag = getattr(stat, "UF_IMMUTABLE", 0x00000002)
    os.chflags(path, immutable_flag)
    if not (path.stat().st_flags & immutable_flag):
        raise SystemExit(f"immutable_receipt_install_failed:{path}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--release", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--signature", required=True)
    parser.add_argument("--lineage-receipt", required=True)
    parser.add_argument("--migration-attestation", required=True)
    args = parser.parse_args()
    source = Path(args.source).resolve(strict=True)
    release = Path(args.release).resolve()
    releases_root = Path("/Users/admin/phonefarm-worker-releases").resolve()
    try:
        release.relative_to(releases_root)
    except ValueError:
        raise SystemExit("release_outside_canonical_root")
    if release.exists() or release.is_symlink():
        raise SystemExit("release_target_already_exists")
    if _git(source, "status", "--porcelain"):
        raise SystemExit("candidate_worktree_not_clean")
    candidate_sha = _git(source, "rev-parse", "HEAD")
    lineage_receipt = _load_json(Path(args.lineage_receipt).resolve())
    migration_attestation = _load_json(Path(args.migration_attestation).resolve())
    if lineage_receipt.get("candidate_sha") != candidate_sha:
        raise SystemExit("lineage_receipt_candidate_mismatch")
    if lineage_receipt.get("status") != "PRODUCTION_LINEAGE_GATE_V1_PASS":
        raise SystemExit("lineage_receipt_gate_not_pass")
    if lineage_receipt.get("worktree_clean") is not True:
        raise SystemExit("lineage_receipt_worktree_not_clean")
    if lineage_receipt.get("migration_gate") != "PASS":
        raise SystemExit("lineage_receipt_migration_gate_not_pass")
    if lineage_receipt.get("migration_attestation_sha256") != _canonical_hash(migration_attestation):
        raise SystemExit("lineage_receipt_migration_attestation_mismatch")
    admission = verify_deployment_candidate(
        source,
        manifest_path=Path(args.manifest),
        signature_path=Path(args.signature),
    )
    if not admission.get("ok"):
        raise SystemExit(f"pre_release_follow60_gate_failed:{admission.get('reason')}")
    subprocess.check_call(["git", "clone", "--no-hardlinks", str(source), str(release)])
    destination_manifest = release / MANIFEST_RELATIVE
    destination_signature = release / SIGNATURE_RELATIVE
    destination_manifest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(Path(args.manifest), destination_manifest)
    shutil.copyfile(Path(args.signature), destination_signature)
    post = verify_deployment_candidate(release)
    if not post.get("ok"):
        raise SystemExit(f"created_release_follow60_gate_failed:{post.get('reason')}")
    tracked_status = _git(source, "status", "--porcelain=v1", "--untracked-files=all")
    manifest = _load_json(Path(args.manifest).resolve())
    build_receipt = {
        "schema": "PHONE_FARM_CANDIDATE_BUILD_RECEIPT_V1",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "candidate_sha": candidate_sha,
        "release_sha": _git(release, "rev-parse", "HEAD"),
        "release_path": str(release),
        "manifest_certified_sha": post.get("manifest_certified_sha"),
        "manifest_sha256": post.get("manifest_sha256"),
        "candidate_worktree_clean": tracked_status == "",
        "uncommitted_protected_diff": False,
        "untracked_protected_file": False,
        "protected_diff_status": "PASS",
        "protected_file_count": len(manifest.get("protected_entries") or {}),
        "follow60_lock_result": "PASS",
        "signature_result": "PASS",
        "lineage_gate_result": "PASS",
        "lineage_receipt_sha256": hashlib.sha256(Path(args.lineage_receipt).read_bytes()).hexdigest(),
        "migration_attestation": lineage_receipt.get("validated_migrations"),
        "migration_attestation_sha256": _canonical_hash(migration_attestation),
    }
    if not build_receipt["candidate_worktree_clean"]:
        raise SystemExit("candidate_worktree_changed_during_release_build")
    evidence_dir = release / ".deployment"
    _install_immutable_json(evidence_dir / "candidate-build-receipt.json", build_receipt)
    _install_immutable_json(evidence_dir / "migration-attestation.json", migration_attestation)
    _install_immutable_json(evidence_dir / "lineage-gate-receipt.json", lineage_receipt)
    print(f"CERTIFIED_RELEASE_CREATED={release} SHA={post['candidate_sha']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
