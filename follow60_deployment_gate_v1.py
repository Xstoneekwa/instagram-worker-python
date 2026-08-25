"""Machine-enforced exact-candidate Follow60 deployment admission gate.

This module is deliberately read-only.  It validates one immutable candidate
against its detached signed V3.1 manifest and refuses promotion when the
manifest was produced for any other commit, when recertification evidence is
missing, or when any protected byte/import changed.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import Any

from follow60_lock_v3 import sha256_bytes, verify_repository


MANIFEST_RELATIVE = Path("docs/governance/FOLLOW60_MAINLINE_LOCK_V3.json")
SIGNATURE_RELATIVE = Path("docs/governance/FOLLOW60_MAINLINE_LOCK_V3.sig")
PUBLIC_KEY_RELATIVE = Path("docs/governance/FOLLOW60_MAINLINE_LOCK_V3_1_PUBLIC_KEY.pem")


def _git_sha(root: Path, revision: str = "HEAD") -> str:
    return subprocess.check_output(
        ["git", "-c", "safe.directory=", "-c", f"safe.directory={root.resolve()}",
         "-C", str(root.resolve()), "rev-parse", revision],
        text=True,
    ).strip()


def verify_deployment_candidate(
    root: Path,
    *,
    manifest_path: Path | None = None,
    signature_path: Path | None = None,
    public_key_path: Path | None = None,
    revision: str = "HEAD",
) -> dict[str, Any]:
    root = Path(root).resolve()
    manifest_path = Path(manifest_path or root / MANIFEST_RELATIVE).resolve()
    signature_path = Path(signature_path or root / SIGNATURE_RELATIVE).resolve()
    public_key_path = Path(public_key_path or root / PUBLIC_KEY_RELATIVE).resolve()
    try:
        candidate_sha = _git_sha(root, revision)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"ok": False, "reason": "deployment_candidate_unreadable", "error_type": type(exc).__name__}

    certified_sha = str(manifest.get("certified_candidate_sha") or "").strip()
    if not certified_sha:
        return {"ok": False, "reason": "manifest_exact_candidate_binding_missing", "candidate_sha": candidate_sha}
    if certified_sha != candidate_sha:
        return {
            "ok": False,
            "reason": "manifest_certified_sha_mismatch",
            "candidate_sha": candidate_sha,
            "manifest_certified_sha": certified_sha,
        }

    recertification = manifest.get("recertification") or {}
    if recertification.get("status") != "PASS":
        return {"ok": False, "reason": "follow60_recertification_missing_or_failed"}
    if int(recertification.get("missing_canonical_delta", -1)) != 0:
        return {"ok": False, "reason": "follow60_canonical_delta_missing"}
    if int(recertification.get("regression_test_count", 0)) <= 0:
        return {"ok": False, "reason": "follow60_recertification_empty"}
    receipt_sha = str(recertification.get("receipt_sha256") or "")
    if len(receipt_sha) != 64:
        return {"ok": False, "reason": "follow60_recertification_receipt_invalid"}

    verified = verify_repository(
        root,
        manifest_path,
        revision=revision,
        signature_path=signature_path,
        public_key_path=public_key_path,
    )
    if not verified.get("ok"):
        reason = verified.get("reason")
        if reason in {"protected_entry_mismatch", "import_graph_mismatch", "unprotected_transitive_dependency"}:
            reason = "manifest_regen_required"
        return {**verified, "ok": False, "reason": reason, "candidate_sha": candidate_sha}
    return {
        **verified,
        "ok": True,
        "status": "FOLLOW60_DEPLOYMENT_GATE_PASS",
        "candidate_sha": candidate_sha,
        "manifest_certified_sha": certified_sha,
        "manifest_sha256": sha256_bytes(manifest_path.read_bytes()),
        "predeploy_follow60_integrity_check": "PASS",
        "write_lock_bypass_available_to_codex": False,
    }

