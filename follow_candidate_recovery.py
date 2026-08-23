"""Account-scoped recovery contract for candidates interrupted before Follow.

The queue is durable and independent of the followers-list cursor.  Decisions
are deliberately mutation-safe: an already/possibly liked post is never
unliked or liked again, and an already-following profile is never attributed
to the current worker run.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

import supabase_client


def normalize_username(value: str) -> str:
    return str(value or "").strip().lstrip("@").lower()


def recovery_key(account_id: str, candidate_username: str, source_target_id: str | None) -> str:
    canonical = ":".join(
        (str(account_id or ""), normalize_username(candidate_username), str(source_target_id or ""))
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RecoveryDecision:
    action: str
    retry_like: bool
    retry_follow: bool
    create_follow_receipt: bool
    increment_follow_counter: bool
    terminal: bool
    reason: str


@dataclass(frozen=True)
class RecoveryEvidenceClassification:
    kind: str
    durable_like: bool
    durable_mute: bool
    physical_follow_ambiguous: bool
    enqueue_allowed: bool
    reason: str


def classify_evidence(evidence: dict[str, Any] | None) -> RecoveryEvidenceClassification:
    """Classify durable queue evidence without observing or mutating Instagram."""
    ev = dict(evidence or {})
    follow_tap_sent = ev.get("follow_tap_sent") is True
    follow_verified = ev.get("follow_verified") is True
    follow_receipt = ev.get("follow_receipt_exists") is True
    durable_like = bool(
        str(ev.get("like_state") or "").strip().lower() == "liked"
        and ev.get("like_tap_sent") is True
        and ev.get("like_verify_success") is True
    )
    durable_mute = bool(
        ev.get("mute_verify_success") is True
        and (ev.get("muted_posts") is True or ev.get("muted_stories") is True)
    )
    physical_ambiguous = bool(follow_tap_sent and not follow_verified and not follow_receipt)
    if physical_ambiguous:
        return RecoveryEvidenceClassification(
            "physical_follow_ambiguous", durable_like, durable_mute, True, True,
            "backend_follow_reconciliation_required",
        )
    if durable_like or durable_mute:
        return RecoveryEvidenceClassification(
            "already_interacted_no_follow", durable_like, durable_mute, False, False,
            "non_actionable_already_interacted_no_follow_recovery",
        )
    return RecoveryEvidenceClassification(
        "unknown", False, False, False, False, "recovery_evidence_unknown_quarantined",
    )


def decide_recovery(*, like_state: str, follow_state: str, quota_available: bool) -> RecoveryDecision:
    """Legacy pure decision retained for audit; P0C never retries on-device."""
    liked = str(like_state or "unknown").strip().lower()
    followed = str(follow_state or "unknown").strip().lower()

    if followed in {"following", "requested"}:
        return RecoveryDecision(
            "terminal_external_follow", False, False, False, False, True,
            "already_following_unattributed",
        )
    if not quota_available:
        return RecoveryDecision(
            "defer", False, False, False, False, False, "quota_unavailable",
        )
    if followed != "follow":
        return RecoveryDecision(
            "defer", False, False, False, False, False, "follow_surface_unproved",
        )

    # Like/Mute-only evidence is non-actionable and UNKNOWN is quarantined.
    # Physical Follow ambiguity is handled exclusively by backend truth in
    # reconcile_follow_candidate_recovery_backend_only_v1.
    reason = (
        "non_actionable_already_interacted_no_follow_recovery"
        if liked == "liked"
        else "recovery_evidence_unknown_quarantined"
    )
    return RecoveryDecision("backend_only", False, False, False, False, True, reason)


def claim_pending(*, account_id: str, worker_id: str, limit: int = 20) -> list[dict[str, Any]]:
    rows = supabase_client.claim_follow_candidate_recovery_v1(
        account_id=account_id, worker_id=worker_id, limit=limit
    )
    return [dict(row) for row in (rows or []) if isinstance(row, dict)]


def enqueue(
    *,
    account_id: str,
    candidate_username: str,
    source_target_id: str | None,
    source_ct_username: str | None,
    original_run_id: str | None,
    original_request_id: str | None,
    business_session_id: str | None,
    evidence: dict[str, Any],
) -> Any:
    classification = classify_evidence(evidence)
    if not classification.enqueue_allowed:
        return {
            "ok": True,
            "enqueued": False,
            "classification": classification.kind,
            "reason": classification.reason,
        }
    return supabase_client.enqueue_follow_candidate_recovery_v1(
        recovery_key=recovery_key(account_id, candidate_username, source_target_id),
        account_id=account_id,
        candidate_username=normalize_username(candidate_username),
        source_target_id=source_target_id,
        source_ct_username=normalize_username(source_ct_username or "") or None,
        original_run_id=original_run_id,
        original_request_id=original_request_id,
        business_session_id=business_session_id,
        evidence=dict(evidence or {}),
    )


def complete(*, recovery_id: str, worker_id: str, outcome: str, receipt_id: str | None = None) -> Any:
    return supabase_client.complete_follow_candidate_recovery_v1(
        recovery_id=recovery_id,
        worker_id=worker_id,
        outcome=outcome,
        receipt_id=receipt_id,
    )


def defer(*, recovery_id: str, worker_id: str, reason: str) -> Any:
    return supabase_client.defer_follow_candidate_recovery_v1(
        recovery_id=recovery_id, worker_id=worker_id, reason=reason
    )


def project_already_interacted(
    *, recovery_id: str, worker_id: str
) -> Any:
    return supabase_client.project_follow_recovery_already_interacted_v1(
        recovery_id=recovery_id, worker_id=worker_id
    )


def reconcile_backend_only(
    *, recovery_id: str, worker_id: str
) -> Any:
    return supabase_client.reconcile_follow_candidate_recovery_backend_only_v1(
        recovery_id=recovery_id, worker_id=worker_id
    )


def quarantine(*, recovery_id: str, worker_id: str, reason: str) -> Any:
    return supabase_client.quarantine_follow_candidate_recovery_v1(
        recovery_id=recovery_id, worker_id=worker_id, reason=reason
    )
