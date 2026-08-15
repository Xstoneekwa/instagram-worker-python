"""Fail-closed runtime integrity gate for FOLLOW60_V2_MAINLINE.

The manifest is approved out-of-band.  This module never writes or updates it.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


MANIFEST_RELATIVE_PATH = "docs/governance/FOLLOW60_MAINLINE_LOCK_V3.json"
EXPECTED_SCHEMA = "FOLLOW60_MAINLINE_LOCK_V3"
EXPECTED_ENGINE = "FOLLOW60_V2_MAINLINE_V1"
EXPECTED_RUNTIME_MODE = "mainline"
EXPECTED_BINDING_KIND = "mainline"


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def verify_runtime_integrity(
    root: Path,
    *,
    runtime_mode: str,
    binding_kind: str,
    engine: str,
    manifest_path: Path | None = None,
) -> dict[str, Any]:
    """Verify the approved manifest and every protected on-disk blob."""
    root = Path(root).resolve()
    path = manifest_path or (root / MANIFEST_RELATIVE_PATH)
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "ok": False,
            "reason": "follow60_integrity_manifest_unreadable",
            "error_type": type(exc).__name__,
        }

    if manifest.get("schema") != EXPECTED_SCHEMA:
        return {"ok": False, "reason": "follow60_integrity_schema_mismatch"}
    if manifest.get("approval", {}).get("status") != "approved_once":
        return {"ok": False, "reason": "follow60_integrity_approval_missing"}
    if manifest.get("runtime_contract", {}).get("runtime_mode") != runtime_mode:
        return {"ok": False, "reason": "follow60_integrity_runtime_mode_mismatch"}
    if manifest.get("runtime_contract", {}).get("binding_kind") != binding_kind:
        return {"ok": False, "reason": "follow60_integrity_binding_mismatch"}
    if manifest.get("runtime_contract", {}).get("engine") != engine:
        return {"ok": False, "reason": "follow60_integrity_engine_mismatch"}

    mismatches: dict[str, dict[str, str | None]] = {}
    protected = manifest.get("protected_files") or {}
    if not isinstance(protected, dict) or not protected:
        return {"ok": False, "reason": "follow60_integrity_empty_scope"}
    for relative, expected in protected.items():
        candidate = root / str(relative)
        actual = _sha256_bytes(candidate.read_bytes()) if candidate.is_file() else None
        if actual != expected:
            mismatches[str(relative)] = {"expected": str(expected), "actual": actual}
    if mismatches:
        return {
            "ok": False,
            "reason": "FOLLOW60_MAINLINE_INTEGRITY_MISMATCH",
            "mismatches": mismatches,
        }

    canonical = json.dumps(protected, sort_keys=True, separators=(",", ":")).encode()
    scope_hash = _sha256_bytes(canonical)
    if scope_hash != manifest.get("protected_scope_sha256"):
        return {"ok": False, "reason": "follow60_integrity_scope_hash_mismatch"}
    return {
        "ok": True,
        "status": "FOLLOW60_MAINLINE_LOCK_V3_RUNTIME_OK",
        "lock_version": manifest.get("lock_version"),
        "approval_id": manifest.get("approval", {}).get("approval_id"),
        "protected_scope_sha256": scope_hash,
        "protected_file_count": len(protected),
    }
