"""Canonical two-approval Follow60 change protocol.

This module is the single fail-closed policy engine used by local, Git, CI,
package, release, runtime-switch and run-start gates.  It verifies signatures;
it never reads the Liam private key and never signs an authorization.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterable

from follow60_lock_v3 import (
    SCHEMA_V3_1,
    canonical_json,
    verify_detached_signature,
    verify_repository,
)


INITIAL_SCHEMA = "FOLLOW60_CHANGE_AUTHORIZATION_REQUEST_V1"
FINAL_SCHEMA = "FOLLOW60_FINAL_APPROVAL_V1"
FREEZE_SCHEMA = "FOLLOW60_CANDIDATE_FREEZE_V1"


class LockState(str, Enum):
    LOCKED_READ_ONLY = "LOCKED_READ_ONLY"
    CHANGE_AUTHORIZED = "CHANGE_AUTHORIZED"
    WORK_IN_PROGRESS = "WORK_IN_PROGRESS"
    CANDIDATE_FROZEN = "CANDIDATE_FROZEN"
    FINAL_APPROVAL_PENDING = "FINAL_APPROVAL_PENDING"
    RELOCKING = "RELOCKING"
    LOCKED_CERTIFIED = "LOCKED_CERTIFIED"
    PROMOTABLE = "PROMOTABLE"
    ACTIVE_CERTIFIED = "ACTIVE_CERTIFIED"


ALLOWED_TRANSITIONS = {
    LockState.LOCKED_READ_ONLY: {LockState.CHANGE_AUTHORIZED},
    LockState.CHANGE_AUTHORIZED: {LockState.WORK_IN_PROGRESS},
    LockState.WORK_IN_PROGRESS: {LockState.CANDIDATE_FROZEN},
    LockState.CANDIDATE_FROZEN: {LockState.FINAL_APPROVAL_PENDING},
    LockState.FINAL_APPROVAL_PENDING: {LockState.RELOCKING},
    LockState.RELOCKING: {LockState.LOCKED_CERTIFIED},
    LockState.LOCKED_CERTIFIED: {LockState.PROMOTABLE},
    LockState.PROMOTABLE: {LockState.ACTIVE_CERTIFIED},
}


READ_GATES = frozenset({"read", "audit", "test"})
WRITE_GATES = frozenset({"edit", "write", "create", "delete", "rename"})
PROMOTION_GATES = frozenset(
    {"commit", "push", "ci", "package", "release", "runtime_switch", "run_start"}
)


@dataclass(frozen=True)
class GateDecision:
    ok: bool
    reason: str


def transition(current: LockState, requested: LockState) -> LockState:
    if requested not in ALLOWED_TRANSITIONS.get(current, set()):
        raise ValueError(f"follow60_transition_forbidden:{current.value}:{requested.value}")
    return requested


def evaluate_gate(
    state: LockState,
    gate: str,
    *,
    path: str = "",
    authorized_paths: Iterable[str] = (),
) -> GateDecision:
    gate = str(gate or "").strip().lower()
    if gate in READ_GATES:
        return GateDecision(True, "read_only_operation_allowed")
    if gate in WRITE_GATES:
        if state != LockState.WORK_IN_PROGRESS:
            return GateDecision(False, "write_requires_work_in_progress")
        if str(path or "") not in set(authorized_paths):
            return GateDecision(False, "path_outside_authorized_scope")
        return GateDecision(True, "authorized_scoped_write")
    if gate in PROMOTION_GATES:
        if state not in {LockState.PROMOTABLE, LockState.ACTIVE_CERTIFIED}:
            return GateDecision(False, "final_approval_and_relock_required")
        return GateDecision(True, "certified_promotion_gate")
    return GateDecision(False, "unknown_gate")


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-c", "safe.directory=", "-c", f"safe.directory={root.resolve()}",
         "-C", str(root.resolve()), *args],
        text=True,
    ).strip()


def verify_initial_authorization(
    root: Path,
    authorization_path: Path,
    signature_path: Path,
    public_key_path: Path,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    signed, reason = verify_detached_signature(
        authorization_path, signature_path, public_key_path
    )
    if not signed:
        return {"ok": False, "reason": reason}
    try:
        payload = json.loads(authorization_path.read_text(encoding="utf-8"))
    except Exception:
        return {"ok": False, "reason": "authorization_unreadable"}
    required = {
        "change_id", "base_sha", "authorized_paths", "authorized_dependency_scope",
        "issued_at", "expires_at", "nonce", "one_task_only",
    }
    if payload.get("schema") != INITIAL_SCHEMA or not required.issubset(payload):
        return {"ok": False, "reason": "authorization_contract_invalid"}
    if not payload.get("one_task_only") or payload.get("permits_final_promotion"):
        return {"ok": False, "reason": "authorization_privilege_invalid"}
    if payload.get("permits_runtime_activation"):
        return {"ok": False, "reason": "authorization_runtime_privilege_forbidden"}
    current = now or datetime.now(timezone.utc)
    if not (_parse_utc(payload["issued_at"]) <= current <= _parse_utc(payload["expires_at"])):
        return {"ok": False, "reason": "authorization_expired_or_not_yet_valid"}
    if _git(root, "rev-parse", "HEAD") != payload["base_sha"]:
        return {"ok": False, "reason": "authorization_base_sha_mismatch"}
    if payload.get("nonce") != payload.get("change_id"):
        return {"ok": False, "reason": "authorization_nonce_mismatch"}
    return {"ok": True, "reason": "initial_approval_valid", "authorization": payload}


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def changed_paths(root: Path, base_sha: str) -> list[str]:
    tracked = set(filter(None, _git(root, "diff", "--name-only", base_sha, "--").splitlines()))
    untracked = set(filter(None, _git(root, "ls-files", "--others", "--exclude-standard").splitlines()))
    return sorted(tracked | untracked)


def freeze_candidate(
    root: Path,
    authorization: dict[str, Any],
    *,
    protected_scope_hash: str,
    manifest_candidate_hash: str = "",
) -> dict[str, Any]:
    root = root.resolve()
    paths = changed_paths(root, authorization["base_sha"])
    unauthorized = sorted(set(paths) - set(authorization["authorized_paths"]))
    if unauthorized:
        raise ValueError(f"candidate_contains_unauthorized_paths:{','.join(unauthorized)}")
    entries: dict[str, Any] = {}
    for relative in paths:
        candidate = root / relative
        if not candidate.is_file():
            raise ValueError(f"candidate_path_not_file:{relative}")
        raw = candidate.read_bytes()
        entries[relative] = {"sha256": _sha256(raw), "size": len(raw)}
    diff_contract = {
        "change_id": authorization["change_id"],
        "base_sha": authorization["base_sha"],
        "changed_entries": entries,
    }
    diff_hash = _sha256(canonical_json(diff_contract))
    freeze = {
        "schema": FREEZE_SCHEMA,
        **diff_contract,
        "final_diff_hash": diff_hash,
        "final_protected_scope_hash": str(protected_scope_hash),
    }
    freeze["final_manifest_hash"] = (
        str(manifest_candidate_hash)
        if manifest_candidate_hash
        else _sha256(canonical_json(freeze))
    )
    return freeze


def freeze_is_current(root: Path, authorization: dict[str, Any], freeze: dict[str, Any]) -> bool:
    refreshed = freeze_candidate(
        root,
        authorization,
        protected_scope_hash=freeze.get("final_protected_scope_hash", ""),
        manifest_candidate_hash=freeze.get("final_manifest_hash", ""),
    )
    return canonical_json(refreshed) == canonical_json(freeze)


def manifest_candidate_hash(manifest: dict[str, Any]) -> str:
    """Hash the immutable manifest body without its self-referential approval."""

    candidate = dict(manifest)
    candidate.pop("approval", None)
    return _sha256(canonical_json(candidate))


def verify_final_approval(
    final_approval_path: Path,
    signature_path: Path,
    public_key_path: Path,
    freeze: dict[str, Any],
) -> dict[str, Any]:
    signed, reason = verify_detached_signature(
        final_approval_path, signature_path, public_key_path
    )
    if not signed:
        return {"ok": False, "reason": reason}
    try:
        approval = json.loads(final_approval_path.read_text(encoding="utf-8"))
    except Exception:
        return {"ok": False, "reason": "final_approval_unreadable"}
    expected = {
        "schema": FINAL_SCHEMA,
        "change_id": freeze["change_id"],
        "base_sha": freeze["base_sha"],
        "final_diff_hash": freeze["final_diff_hash"],
        "final_manifest_hash": freeze["final_manifest_hash"],
        "final_protected_scope_hash": freeze["final_protected_scope_hash"],
    }
    if approval.get("schema") == SCHEMA_V3_1:
        embedded = approval.get("approval") or {}
        expected_embedded = {
            "change_id": freeze["change_id"],
            "base_sha": freeze["base_sha"],
            "final_diff_hash": freeze["final_diff_hash"],
            "final_manifest_hash": freeze["final_manifest_hash"],
            "final_protected_scope_hash": freeze["final_protected_scope_hash"],
        }
        if any(embedded.get(key) != value for key, value in expected_embedded.items()):
            return {"ok": False, "reason": "final_manifest_approval_freeze_mismatch"}
        if embedded.get("token_status") != "consumed_once":
            return {"ok": False, "reason": "final_approval_not_consumed_once"}
        if approval.get("protected_scope_sha256") != freeze["final_protected_scope_hash"]:
            return {"ok": False, "reason": "final_manifest_scope_hash_mismatch"}
        if manifest_candidate_hash(approval) != freeze["final_manifest_hash"]:
            return {"ok": False, "reason": "final_manifest_candidate_hash_mismatch"}
    elif approval != expected:
        return {"ok": False, "reason": "final_approval_freeze_mismatch"}
    return {"ok": True, "reason": "final_approval_valid", "approval": approval}


def verify_staged_final_approval(
    root: Path,
    manifest_path: Path,
    signature_path: Path,
    public_key_path: Path,
) -> dict[str, Any]:
    """Authorize one exact protected commit from the signed final manifest."""

    root = root.resolve()
    signed, reason = verify_detached_signature(manifest_path, signature_path, public_key_path)
    if not signed:
        return {"ok": False, "reason": reason}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return {"ok": False, "reason": "final_manifest_unreadable"}
    approval = manifest.get("approval") or {}
    if manifest.get("schema") != SCHEMA_V3_1 or approval.get("token_status") != "consumed_once":
        return {"ok": False, "reason": "final_manifest_approval_invalid"}
    if _git(root, "rev-parse", "HEAD") != approval.get("base_sha"):
        return {"ok": False, "reason": "final_approval_base_sha_mismatch"}
    if manifest_candidate_hash(manifest) != approval.get("final_manifest_hash"):
        return {"ok": False, "reason": "final_manifest_candidate_hash_mismatch"}
    if manifest.get("protected_scope_sha256") != approval.get("final_protected_scope_hash"):
        return {"ok": False, "reason": "final_manifest_scope_hash_mismatch"}
    matching_audits = [
        entry for entry in (manifest.get("audit_history") or [])
        if entry.get("change_id") == approval.get("change_id")
    ]
    if len(matching_audits) != 1:
        return {"ok": False, "reason": "final_manifest_audit_entry_invalid"}
    approved_paths = sorted(set(matching_audits[0].get("files") or []))
    staged_paths = sorted(set(filter(None, _git(
        root, "diff", "--cached", "--name-only", "--diff-filter=ACMRD"
    ).splitlines())))
    governance_paths = {
        str(manifest_path.resolve().relative_to(root)),
        str(signature_path.resolve().relative_to(root)),
    }
    if staged_paths != sorted(set(approved_paths) | governance_paths):
        return {"ok": False, "reason": "final_approval_staged_scope_mismatch"}
    staged_diff = subprocess.check_output(
        ["git", "-C", str(root), "diff", "--cached", "--binary", "HEAD", "--", *approved_paths]
    )
    if _sha256(staged_diff) != approval.get("final_diff_hash"):
        return {"ok": False, "reason": "final_approval_staged_diff_mismatch"}
    tree_revision = _git(root, "write-tree")
    repository = verify_repository(
        root,
        manifest_path,
        revision=tree_revision,
        signature_path=signature_path,
        public_key_path=public_key_path,
        enforce_exact_candidate=False,
    )
    if not repository.get("ok"):
        return {"ok": False, "reason": "final_approval_candidate_tree_invalid", "detail": repository}
    return {
        "ok": True,
        "reason": "signed_final_manifest_authorizes_exact_staged_candidate",
        "candidate_tree_sha": tree_revision,
        "protected_scope_sha256": repository.get("protected_scope_sha256"),
    }
