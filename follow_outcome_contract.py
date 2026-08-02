"""Canonical, account-agnostic outcome contract for the Follow phase."""

from __future__ import annotations

from typing import Any, Iterable


FOLLOW_OUTCOME_CONTRACT_VERSION = "follow_outcome_v1"
LOCAL_ROTATABLE_REASONS = frozenset({"visible_window_exhausted_scroll_failed"})
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
