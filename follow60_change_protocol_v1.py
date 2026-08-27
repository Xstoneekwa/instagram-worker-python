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
from follow60_candidate_identity_v2 import FREEZE_SCHEMA


class LockState(str, Enum):
    LOCKED_READ_ONLY = "LOCKED_READ_ONLY"
    CHANGE_AUTHORIZED = "CHANGE_AUTHORIZED"
    WORK_IN_PROGRESS = "WORK_IN_PROGRESS"
    CANDIDATE_FROZEN = "CANDIDATE_FROZEN"
    CANDIDATE_COMMITTED = "CANDIDATE_COMMITTED"
    MANIFEST_GENERATED = "MANIFEST_GENERATED"
    FINAL_APPROVAL_PENDING = "FINAL_APPROVAL_PENDING"
    RELOCKING = "RELOCKING"
    LOCKED_CERTIFIED = "LOCKED_CERTIFIED"
    PROMOTABLE = "PROMOTABLE"
    ACTIVE_CERTIFIED = "ACTIVE_CERTIFIED"


ALLOWED_TRANSITIONS = {
    LockState.LOCKED_READ_ONLY: {LockState.CHANGE_AUTHORIZED},
    LockState.CHANGE_AUTHORIZED: {LockState.WORK_IN_PROGRESS},
    LockState.WORK_IN_PROGRESS: {LockState.CANDIDATE_FROZEN},
    LockState.CANDIDATE_FROZEN: {LockState.CANDIDATE_COMMITTED},
    LockState.CANDIDATE_COMMITTED: {LockState.MANIFEST_GENERATED},
    LockState.MANIFEST_GENERATED: {LockState.FINAL_APPROVAL_PENDING},
    LockState.FINAL_APPROVAL_PENDING: {LockState.RELOCKING},
    LockState.RELOCKING: {LockState.LOCKED_CERTIFIED},
    LockState.LOCKED_CERTIFIED: {LockState.PROMOTABLE},
    LockState.PROMOTABLE: {LockState.ACTIVE_CERTIFIED},
}


READ_GATES = frozenset({"read", "audit", "test"})
WRITE_GATES = frozenset({"edit", "write", "create", "delete", "rename"})
PROMOTION_GATES = frozenset(
    {"push", "ci", "package", "release", "runtime_switch", "run_start"}
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
    frozen_index_verified: bool = False,
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
    if gate == "commit":
        allowed = state == LockState.CANDIDATE_FROZEN and frozen_index_verified
        return GateDecision(allowed, "frozen_candidate_commit_only" if allowed else "verified_frozen_index_required")
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


def freeze_candidate(root: Path, authorization: dict[str, Any], *, protected_scope_hash: str = "",
                     manifest_candidate_hash: str = "") -> dict[str, Any]:
    from follow60_candidate_identity_v2 import freeze_index
    if manifest_candidate_hash:
        raise ValueError("precommit_manifest_hash_forbidden")
    return freeze_index(root, authorization)


def freeze_is_current(root: Path, authorization: dict[str, Any], freeze: dict[str, Any]) -> bool:
    try:
        return freeze_candidate(root, authorization) == freeze
    except (ValueError, subprocess.CalledProcessError):
        return False


def manifest_candidate_hash(manifest: dict[str, Any]) -> str:
    raise ValueError("retired_body_hash_use_manifest_sha256_on_exact_file")


def verify_final_approval(final_approval_path: Path, signature_path: Path,
                          public_key_path: Path, freeze: dict[str, Any],
                          *, root: Path | None = None) -> dict[str, Any]:
    from follow60_candidate_identity_v2 import committed_identity, external_path
    from follow60_deployment_gate_v1 import verify_deployment_candidate
    if root is None:
        return {"ok": False, "reason": "exact_candidate_repository_required"}
    try:
        external_path(root, final_approval_path)
        committed_identity(root, freeze["base_commit_sha"], freeze=freeze)
    except (ValueError, KeyError, subprocess.CalledProcessError):
        return {"ok": False, "reason": "final_candidate_freeze_invalid"}
    return verify_deployment_candidate(root, manifest_path=final_approval_path,
                                       signature_path=signature_path, public_key_path=public_key_path)


def verify_staged_final_approval(root: Path, manifest_path: Path, signature_path: Path,
                                public_key_path: Path) -> dict[str, Any]:
    # Old circular path is intentionally retired, never silently reinterpreted.
    return {"ok": False, "reason": "final_signature_cannot_authorize_precommit_tree"}


def verify_candidate_commit(root: Path, authorization_path: Path, signature_path: Path,
                            public_key_path: Path, freeze_path: Path) -> dict[str, Any]:
    from follow60_candidate_identity_v2 import external_path, freeze_index
    try:
        for path in (authorization_path, signature_path, freeze_path):
            external_path(root, path)
        approved = verify_initial_authorization(root, authorization_path, signature_path, public_key_path)
        if not approved.get("ok"):
            return approved
        frozen = json.loads(freeze_path.read_text(encoding="utf-8"))
        actual = freeze_index(root, approved["authorization"])
        if frozen != actual:
            return {"ok": False, "reason": "staged_candidate_changed_after_freeze"}
        return {"ok": True, "reason": "candidate_commit_only_not_deployable", **actual}
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError):
        return {"ok": False, "reason": "candidate_commit_evidence_invalid"}
