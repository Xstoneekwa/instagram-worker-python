"""Canonical, account-agnostic outcome contract for the Follow phase."""

from __future__ import annotations

from typing import Any, Iterable


FOLLOW_OUTCOME_CONTRACT_VERSION = "follow_outcome_v1"
FOLLOW_TERMINATION_DECISION_SCHEMA = "FOLLOW_TERMINATION_DECISION_V1"
FOLLOW_TERMINATION_DECISION_VERSION = "follow_termination_decision_v1"
LOCAL_ROTATABLE_REASONS = frozenset(
    {
        "visible_window_exhausted_scroll_failed",
        "see_more_click_exhausted_after_bounded_recovery",
    }
)
GLOBAL_COMPLETION_REASONS = frozenset(
    {
        "global_follow_cap_reached",
        "follow_target_reached",
        "follow_quota_reached",
        "all_targets_exhausted",
    }
)
RESUMABLE_PHASE_REASONS = frozenset(
    {
        "max_targets_per_run_reached",
        "safe_partial_ct_failure_limit_reached",
        "target_budget_reached",
    }
)
EVALUATION_BARRIER_REASONS = frozenset(
    {
        "evaluation_barrier_reached",
        "follow60_evaluation_barrier_reached",
    }
)
CRITICAL_MARKERS = (
    "account_mismatch",
    "active_instagram_account_mismatch",
    "challenge",
    "instagram_checkpoint",
    "checkpoint_required",
    "credential",
    "password",
    "login_required",
    "restriction",
    "restricted",
)


def build_follow_termination_decision(
    *,
    exit_code: int,
    first_causal_reason: str,
    follows_completed_count: int,
    target_follow_budget_effective: int | None,
    target_attribution: dict[str, Any],
    physical_follow_preserved: bool,
    canonical_follow_receipt_present: bool,
    candidate_local_failure: bool,
    post_follow_recovery_required: bool,
    no_new_follow_until_recovered: bool,
    safe_boundary: bool,
    safe_next_step: str,
) -> dict[str, Any]:
    """Build the runner-owned, JSON-safe Follow termination decision.

    This envelope contains execution facts only.  The account orchestrator may
    validate it and apply independent global safety gates, but must never
    reconstruct a second Follow outcome from legacy target-local heuristics.
    """

    completed = max(0, int(follows_completed_count or 0))
    budget = (
        max(0, int(target_follow_budget_effective))
        if target_follow_budget_effective is not None
        else None
    )
    reason = str(first_causal_reason or "post_follow_recovery_required").strip()
    attribution = {
        str(key): value
        for key, value in dict(target_attribution or {}).items()
        if value is not None and str(value).strip()
    }
    return {
        "schema": FOLLOW_TERMINATION_DECISION_SCHEMA,
        "schema_version": FOLLOW_TERMINATION_DECISION_VERSION,
        "contract_version": FOLLOW_TERMINATION_DECISION_VERSION,
        "phase_status": "partial_resumable",
        "scope": "follow_phase",
        "exit_code": int(exit_code),
        "first_causal_reason": reason,
        "stable_reason": reason,
        "physical_follow_preserved": bool(physical_follow_preserved),
        "canonical_follow_receipt_present": bool(
            canonical_follow_receipt_present
        ),
        "candidate_local_failure": bool(candidate_local_failure),
        "post_follow_recovery_required": bool(post_follow_recovery_required),
        "no_new_follow_until_recovered": bool(no_new_follow_until_recovered),
        "safe_boundary": bool(safe_boundary),
        "safe_next_step": str(safe_next_step or "").strip(),
        "follow_quota_state": {
            "follows_completed_count": completed,
            "target_follow_budget_effective": budget,
        },
        "target_attribution": attribution,
        "completed": False,
        "partial": True,
        "resumable": True,
        "verified_actions": completed,
        "target_actions": budget,
        "remaining_actions": (
            max(0, budget - completed) if budget is not None else None
        ),
        "remaining_target_ids": [],
        "remaining_target_count": 0,
        "current_ct_status": "retryable",
        "current_ct_result": "retryable",
        "suggested_next_action": "handoff_to_unfollow",
        "suggested_resume_strategy": (
            "resume_post_follow_recovery_before_new_follow"
        ),
        "last_safe_checkpoint": "post_follow_recovery_pending",
        "follow_retap_allowed": False,
        "target_rotation_allowed": False,
    }


def build_follow_time_handoff_termination_decision(
    *,
    follows_completed_count: int,
    target_follow_budget_effective: int | None,
    target_attribution: dict[str, Any],
    account_id: str,
    request_id: str,
    run_id: str,
    business_session_id: str,
    attempt_id: str,
    generation: str,
) -> dict[str, Any]:
    """Build the authoritative, scope-bound exit-97 Follow handoff."""

    completed = max(0, int(follows_completed_count or 0))
    budget = (
        max(0, int(target_follow_budget_effective))
        if target_follow_budget_effective is not None
        else None
    )
    attribution = {
        str(key): value
        for key, value in dict(target_attribution or {}).items()
        if value is not None and str(value).strip()
    }
    return {
        "schema": FOLLOW_TERMINATION_DECISION_SCHEMA,
        "schema_version": FOLLOW_TERMINATION_DECISION_VERSION,
        "contract_version": FOLLOW_TERMINATION_DECISION_VERSION,
        "phase_status": "partial_resumable",
        "scope": "follow_phase",
        "exit_code": 97,
        "first_causal_reason": "follow_to_unfollow_time_handoff",
        "stable_reason": "follow_to_unfollow_time_handoff",
        "safe_boundary": True,
        "safe_next_step": "handoff_to_unfollow",
        "follow_remaining_preserved": True,
        "no_new_follow_after_deadline": True,
        "follow_quota_state": {
            "follows_completed_count": completed,
            "target_follow_budget_effective": budget,
        },
        "target_attribution": attribution,
        "scope_binding": {
            "account_id": str(account_id or "").strip(),
            "request_id": str(request_id or "").strip(),
            "run_id": str(run_id or "").strip(),
            "business_session_id": str(business_session_id or "").strip(),
            "attempt_id": str(attempt_id or "").strip(),
            "generation": str(generation or "").strip(),
        },
        "completed": False,
        "partial": True,
        "resumable": True,
        "verified_actions": completed,
        "target_actions": budget,
        "remaining_actions": (
            max(0, budget - completed) if budget is not None else None
        ),
        "remaining_target_ids": [],
        "remaining_target_count": 0,
        "current_ct_status": "retryable",
        "current_ct_result": "retryable",
        "suggested_next_action": "handoff_to_unfollow",
        "suggested_resume_strategy": "resume_follow_on_next_eligible_session",
        "last_safe_checkpoint": "follow_to_unfollow_safe_boundary",
        "follow_retap_allowed": False,
        "target_rotation_allowed": False,
    }


def validate_follow_termination_decision(
    decision: dict[str, Any] | None,
    *,
    expected_exit_code: int = 53,
    expected_account_id: str | None = None,
    expected_request_id: str | None = None,
    expected_run_id: str | None = None,
    expected_business_session_id: str | None = None,
    expected_attempt_id: str | None = None,
    expected_generation: str | None = None,
) -> tuple[bool, str]:
    """Validate an authoritative Follow terminal envelope, fail closed."""

    if not isinstance(decision, dict) or not decision:
        return False, "decision_missing"
    if decision.get("schema") != FOLLOW_TERMINATION_DECISION_SCHEMA:
        return False, "schema_unknown"
    if decision.get("schema_version") != FOLLOW_TERMINATION_DECISION_VERSION:
        return False, "schema_version_unknown"
    try:
        actual_exit_code = int(decision.get("exit_code"))
    except (TypeError, ValueError):
        return False, "exit_code_invalid"
    if actual_exit_code != int(expected_exit_code):
        return False, "exit_code_mismatch"

    if actual_exit_code == 97:
        required_exact = {
            "phase_status": "partial_resumable",
            "scope": "follow_phase",
            "first_causal_reason": "follow_to_unfollow_time_handoff",
            "stable_reason": "follow_to_unfollow_time_handoff",
            "safe_boundary": True,
            "safe_next_step": "handoff_to_unfollow",
            "follow_remaining_preserved": True,
            "no_new_follow_after_deadline": True,
            "follow_retap_allowed": False,
            "target_rotation_allowed": False,
        }
        for field, expected in required_exact.items():
            if decision.get(field) != expected:
                return False, f"contract_contradiction:{field}"
        binding = decision.get("scope_binding")
        if not isinstance(binding, dict):
            return False, "scope_binding_invalid"
        expected_binding = {
            "account_id": expected_account_id,
            "request_id": expected_request_id,
            "run_id": expected_run_id,
            "business_session_id": expected_business_session_id,
            "attempt_id": expected_attempt_id,
            "generation": expected_generation,
        }
        for field, expected in expected_binding.items():
            expected_text = str(expected or "").strip()
            if not expected_text:
                return False, f"expected_scope_missing:{field}"
            if str(binding.get(field) or "").strip() != expected_text:
                return False, f"scope_binding_mismatch:{field}"
        return True, "follow_time_handoff_partial_safe_for_unfollow"

    required_exact = {
        "phase_status": "partial_resumable",
        "scope": "follow_phase",
        "physical_follow_preserved": True,
        "canonical_follow_receipt_present": True,
        "candidate_local_failure": True,
        "post_follow_recovery_required": True,
        "no_new_follow_until_recovered": True,
        "safe_boundary": True,
        "safe_next_step": "handoff_to_unfollow",
        "follow_retap_allowed": False,
        "target_rotation_allowed": False,
    }
    for field, expected in required_exact.items():
        if decision.get(field) != expected:
            return False, f"contract_contradiction:{field}"

    quota = decision.get("follow_quota_state")
    if not isinstance(quota, dict):
        return False, "follow_quota_state_invalid"
    try:
        if int(quota.get("follows_completed_count")) < 1:
            return False, "durable_follow_count_invalid"
    except (TypeError, ValueError):
        return False, "durable_follow_count_invalid"
    attribution = decision.get("target_attribution")
    if not isinstance(attribution, dict) or not attribution:
        return False, "target_attribution_invalid"
    if not str(decision.get("first_causal_reason") or "").strip():
        return False, "first_causal_reason_missing"
    return True, "follow_candidate_local_post_follow_partial_safe_for_unfollow"


def _clean_ids(values: Iterable[Any] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values or ():
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def _critical_reason(reason: str, extra_text: str = "") -> bool:
    haystack = f"{reason} {extra_text}".lower()
    return any(marker in haystack for marker in CRITICAL_MARKERS)


def build_follow_outcome(
    *,
    stable_reason: str,
    verified_actions: int,
    target_actions: int | None,
    current_target_id: str | None = None,
    remaining_target_ids: Iterable[Any] | None = None,
    all_targets_exhausted: bool = False,
    deadline_insufficient: bool = False,
    internal_failure: bool = False,
    current_ct_exhausted: bool = False,
    target_budget_reached: bool = False,
    safe_boundary: bool = False,
    extra_diagnostics: Any = None,
) -> dict[str, Any]:
    """Return the single semantic contract shared by Runner and orchestrator.

    Positive verified work is deliberately only a counter.  It never promotes a
    local stop to global Follow completion.
    """
    reason = str(stable_reason or "unknown_follow_outcome").strip()
    verified = max(0, int(verified_actions or 0))
    target = max(0, int(target_actions)) if target_actions is not None else None
    remaining_actions = max(0, target - verified) if target is not None else None
    remaining_ids = _clean_ids(remaining_target_ids)
    current_id = str(current_target_id or "").strip() or None

    phase_status = "partial_not_resumable"
    scope = "follow_phase"
    current_ct_status = "unsafe"
    safe_next_step = "end_session"
    suggested_next_action = "end_session"
    suggested_resume_strategy = "manual_review"
    boundary = bool(safe_boundary)

    if internal_failure:
        phase_status = "failed_internal"
        scope = "account_session"
    elif _critical_reason(reason, str(extra_diagnostics or "")):
        phase_status = "blocked_critical"
        scope = "account_session"
    elif reason in LOCAL_ROTATABLE_REASONS:
        phase_status = "partial_resumable"
        scope = "current_ct"
        current_ct_status = "locally_blocked"
        safe_next_step = "rotate_next_ct" if remaining_ids else "schedule_resume"
        suggested_next_action = safe_next_step
        suggested_resume_strategy = (
            "validate_global_search_then_open_next_ct_followers"
            if remaining_ids
            else "resume_follow_from_last_safe_checkpoint"
        )
        # Missing a local scroll anchor is not itself a dangerous surface.  The
        # boundary becomes actionable only after the CT switcher proves it.
        boundary = bool(safe_boundary)
    elif reason in EVALUATION_BARRIER_REASONS:
        phase_status = "completed_waiting_operator_evaluation"
        scope = "follow_phase"
        current_ct_status = "completed"
        safe_next_step = "wait_operator_evaluation"
        suggested_next_action = "wait_operator_evaluation"
        suggested_resume_strategy = "operator_evaluation_required"
        boundary = True
    elif reason in GLOBAL_COMPLETION_REASONS or all_targets_exhausted:
        phase_status = "completed"
        scope = "follow_phase"
        current_ct_status = "exhausted" if all_targets_exhausted else "completed"
        safe_next_step = "end_follow_phase"
        suggested_next_action = "end_follow_phase"
        suggested_resume_strategy = "none"
        boundary = True
    elif deadline_insufficient:
        phase_status = "partial_resumable"
        scope = "account_session"
        current_ct_status = "retryable"
        safe_next_step = "schedule_resume"
        suggested_next_action = "schedule_resume"
        suggested_resume_strategy = "resume_follow_from_last_safe_checkpoint"
        boundary = True
    elif reason in RESUMABLE_PHASE_REASONS:
        phase_status = "partial_resumable"
        scope = "follow_phase"
        current_ct_status = "retryable"
        safe_next_step = "schedule_resume"
        suggested_next_action = "schedule_resume"
        suggested_resume_strategy = "resume_follow_with_remaining_ct_plan"
        boundary = bool(safe_boundary)
    elif target_budget_reached:
        phase_status = "completed"
        scope = "current_ct"
        current_ct_status = "completed"
        safe_next_step = "rotate_next_ct" if remaining_ids else "schedule_resume"
        suggested_next_action = safe_next_step
        suggested_resume_strategy = (
            "continue_with_next_ct" if remaining_ids else "resume_follow_with_remaining_quota"
        )
        boundary = True
    elif current_ct_exhausted:
        phase_status = "completed"
        scope = "current_ct"
        current_ct_status = "exhausted"
        safe_next_step = "rotate_next_ct" if remaining_ids else "end_follow_phase"
        suggested_next_action = safe_next_step
        suggested_resume_strategy = "continue_with_next_ct" if remaining_ids else "none"
        boundary = True

    return {
        "contract_version": FOLLOW_OUTCOME_CONTRACT_VERSION,
        "phase_status": phase_status,
        "scope": scope,
        "stable_reason": reason,
        "completed": phase_status in {
            "completed",
            "completed_waiting_operator_evaluation",
        },
        "partial": phase_status.startswith("partial_"),
        "resumable": phase_status == "partial_resumable",
        "verified_actions": verified,
        "target_actions": target,
        "remaining_actions": remaining_actions,
        "current_target_id": current_id,
        "remaining_target_ids": remaining_ids,
        "remaining_target_count": len(remaining_ids),
        "current_ct_status": current_ct_status,
        "current_ct_result": current_ct_status,
        "safe_boundary": boundary,
        "safe_next_step": safe_next_step,
        "suggested_next_action": suggested_next_action,
        "suggested_resume_strategy": suggested_resume_strategy,
        "last_safe_checkpoint": (
            "next_ct_followers_list_validated"
            if boundary and safe_next_step == "rotate_next_ct"
            else "follow_actions_verified"
        ),
    }


def merge_follow_outcome(summary: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    contract = build_follow_outcome(**kwargs)
    summary["follow_outcome"] = contract
    summary.update(contract)
    return contract
