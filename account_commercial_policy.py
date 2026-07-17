"""Per-account commercial policy revision guards for dispatcher and account session."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import time
from typing import Any

import supabase_client
from assignment_dispatch_resolver import sensitive_log_fields
from logs import log


_COMMERCIAL_POLICY_BOUNDARY_EVIDENCE_TTL_S = 12.0


@dataclass(frozen=True)
class CommercialPolicyBoundaryEvidence:
    account_id: str
    run_id: str
    boundary: str
    bound_revision: str
    current_revision: str
    changed: bool
    package_code: str
    observed_at_monotonic: float


def _read_revision_token(row: dict[str, Any] | None) -> str:
    if not row:
        return ""
    return str(row.get("revision_token") or "").strip()


def _parse_iso_ts(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        normalized = raw.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def load_account_commercial_policy_revision(account_id: str) -> dict[str, Any] | None:
    """Latest policy revision for one account (package change signal)."""
    aid = str(account_id or "").strip()
    if not aid:
        return None
    return supabase_client.get_account_commercial_policy_revision(aid)


def stamp_commercial_policy_metadata(
    account_id: str,
    metadata_safe: dict[str, Any] | None,
) -> dict[str, Any]:
    """Attach current commercial_policy_revision when enqueueing a run request."""
    meta = dict(metadata_safe or {})
    if str(meta.get("commercial_policy_revision") or "").strip():
        return meta
    try:
        current = load_account_commercial_policy_revision(account_id) or {}
    except RuntimeError:
        return meta
    token = _read_revision_token(current)
    if token:
        meta["commercial_policy_revision"] = token
    package_code = str(current.get("package_code") or "").strip()
    if package_code:
        meta["commercial_package_code"] = package_code
    return meta


def policy_revision_changed(account_id: str, queued_revision: str | None) -> bool:
    queued = str(queued_revision or "").strip()
    if not queued:
        return False
    current = load_account_commercial_policy_revision(account_id)
    current_token = _read_revision_token(current)
    return bool(current_token and current_token != queued)


def evaluate_queued_run_commercial_policy(
    account_id: str,
    request_metadata: dict[str, Any] | None,
    *,
    request_created_at: str | None = None,
) -> tuple[bool, str | None, dict[str, Any]]:
    """Reject queued runs when commercial package revision changed since enqueue."""
    meta = dict(request_metadata or {})
    queued_revision = str(meta.get("commercial_policy_revision") or "").strip()
    current = load_account_commercial_policy_revision(account_id) or {}
    current_token = _read_revision_token(current)
    policy_ctx = {
        "queued_revision": queued_revision or None,
        "current_revision": current_token or None,
        "package_code": current.get("package_code"),
    }

    if queued_revision and current_token and queued_revision != current_token:
        return False, "commercial_policy_revision_changed", policy_ctx

    created_at = _parse_iso_ts(request_created_at)
    package_started_at = _parse_iso_ts(
        current.get("package_starts_at") or current.get("created_at"),
    )
    if not queued_revision and created_at and package_started_at and package_started_at > created_at:
        return False, "commercial_policy_revision_changed", {
            **policy_ctx,
            "package_started_after_queue": True,
        }

    return True, None, {
        **policy_ctx,
        "commercial_policy_revision": current_token or queued_revision or None,
    }


def revalidate_commercial_policy_at_boundary(
    account_id: str,
    *,
    run_id: str | None,
    boundary: str,
    bound_revision: str | None,
) -> dict[str, Any]:
    """Safe-boundary check during an active account session run."""
    current = load_account_commercial_policy_revision(account_id) or {}
    current_token = _read_revision_token(current)
    queued = str(bound_revision or "").strip()
    changed = bool(queued and current_token and queued != current_token)
    if changed:
        log(
            "info",
            "account_commercial_policy_revision_changed",
            account_id=account_id,
            run_id=run_id,
            boundary=boundary,
            queued_revision=queued,
            current_revision=current_token,
            package_code=current.get("package_code"),
        )
    return {
        "changed": changed,
        "current_revision": current_token or None,
        "package_code": current.get("package_code"),
        "boundary": boundary,
    }


def commercial_policy_boundary_blocks_phase(
    account_id: str,
    *,
    bound_revision: str | None,
    run_id: str | None,
    boundary: str,
    evidence: CommercialPolicyBoundaryEvidence | None = None,
    evidence_out: dict[str, Any] | None = None,
) -> bool:
    """Central guard for all package-gated account_session phases."""
    aid = str(account_id or "").strip()
    rid = str(run_id or "").strip()
    bound = str(bound_revision or "").strip()
    boundary_key = str(boundary or "").strip()
    invalid_reason = "missing_evidence"
    age_s = 0.0
    if isinstance(evidence, CommercialPolicyBoundaryEvidence):
        age_s = max(0.0, time.monotonic() - evidence.observed_at_monotonic)
        if evidence.account_id != aid:
            invalid_reason = "account_mismatch"
        elif evidence.run_id != rid:
            invalid_reason = "run_mismatch"
        elif evidence.boundary != boundary_key:
            invalid_reason = "boundary_mismatch"
        elif evidence.bound_revision != bound:
            invalid_reason = "bound_revision_mismatch"
        elif age_s > _COMMERCIAL_POLICY_BOUNDARY_EVIDENCE_TTL_S:
            invalid_reason = "ttl_expired"
        else:
            log(
                "info",
                "commercial_policy_boundary_evidence_reused",
                account_id=aid,
                run_id=rid or None,
                boundary=boundary_key,
                proof_age_ms=round(age_s * 1000.0, 2),
                avoided_blocks=["commercial_policy_revision_db_read"],
                fallback_full_read_used=False,
            )
            return bool(evidence.changed)
        log(
            "info",
            "commercial_policy_boundary_evidence_invalidated",
            account_id=aid,
            run_id=rid or None,
            boundary=boundary_key,
            proof_age_ms=round(age_s * 1000.0, 2),
            invalidation_reason=invalid_reason,
            fallback_full_read_used=True,
        )

    result = revalidate_commercial_policy_at_boundary(
        aid,
        run_id=rid or None,
        boundary=boundary_key,
        bound_revision=bound,
    )
    captured = CommercialPolicyBoundaryEvidence(
        account_id=aid,
        run_id=rid,
        boundary=boundary_key,
        bound_revision=bound,
        current_revision=str(result.get("current_revision") or "").strip(),
        changed=bool(result.get("changed")),
        package_code=str(result.get("package_code") or "").strip(),
        observed_at_monotonic=time.monotonic(),
    )
    if isinstance(evidence_out, dict):
        evidence_out["evidence"] = captured
    log(
        "info",
        "commercial_policy_boundary_evidence_captured",
        account_id=aid,
        run_id=rid or None,
        boundary=boundary_key,
        proof_age_ms=0.0,
        fallback_full_read_used=bool(evidence is not None),
        invalidation_reason=invalid_reason if evidence is not None else "",
    )
    return bool(result.get("changed"))


def load_account_effective_package_policy(account_id: str) -> dict[str, Any]:
    """Read live package caps/features for one account (no preference mutation)."""
    aid = str(account_id or "").strip()
    if not aid:
        return {}
    summary = supabase_client.get_account_package_summary(aid) or {}
    package_caps = summary.get("package_caps") if isinstance(summary.get("package_caps"), dict) else {}
    preview = summary.get("effective_caps_preview") if isinstance(summary.get("effective_caps_preview"), dict) else {}
    return {
        "account_id": aid,
        "commercial_package_code": str(summary.get("commercial_package_code") or "").strip() or None,
        "package_caps": package_caps,
        "effective_caps_preview": preview,
        "revision": _read_revision_token(load_account_commercial_policy_revision(aid)),
    }
