"""Sanitized structured root cause for account-session incident channels."""

from __future__ import annotations

from typing import Any


def _record(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def build_account_session_phase_notification_summary(
    performance_summary: dict[str, Any] | None,
    request_metadata: dict[str, Any] | None,
    *,
    primary_failure_reason: str | None = None,
) -> dict[str, Any]:
    summary = _record(performance_summary)
    request = _record(request_metadata)
    contract = _record(summary.get("phase_terminal_contract"))
    non_terminal = _record(contract.get("non_terminal_phases"))
    real = _record(summary.get("follow_to_unfollow_real"))
    outcome = _record(real.get("unfollow_outcome") or summary.get("unfollow_outcome"))
    plan = _record(request.get("resume_plan"))
    requested_phases = _record(plan.get("phases_to_run"))
    quota_remaining = _record(plan.get("quota_remaining"))

    stable_reason = str(
        outcome.get("stable_reason")
        or real.get("stop_reason")
        or real.get("failure_reason")
        or summary.get("root_failure_code")
        or contract.get("reason")
        or primary_failure_reason
        or "account_session_phase_not_terminal"
    ).strip()
    if stable_reason == "global_follow_cap_reached":
        reason_code = "FOLLOW_ZERO_QUOTA_MISCLASSIFIED"
    elif (
        "fallback" in stable_reason
        or "coverage_budget_exhausted" in stable_reason
        or "progressive_search_limit" in stable_reason
    ):
        reason_code = "UNFOLLOW_FALLBACK_NOT_ARMED_AFTER_COVERAGE_EXHAUSTED"
    elif stable_reason == "username_not_found_confirmed":
        reason_code = "USERNAME_NOT_FOUND_REMOVED_FROM_BACKLOG"
    elif "circuit" in stable_reason or "consecutive_failure_limit" in stable_reason:
        reason_code = "UNFOLLOW_AUTO_RESTART_CIRCUIT_BREAKER"
    elif "search" in stable_reason:
        reason_code = "SEARCH_SURFACE_UNHEALTHY_BATCH_CONTINUED"
    else:
        reason_code = "ACCOUNT_SESSION_PHASE_NOT_TERMINAL_STRUCTURED"

    blocked_phase = next(iter(non_terminal), "unknown")
    follow_remaining = int(
        summary.get("follow_quota_remaining")
        or quota_remaining.get("follow")
        or 0
    )
    unfollow_actionable = int(
        real.get("last_run_remaining_eligible")
        or outcome.get("remaining_count")
        or quota_remaining.get("unfollow")
        or 0
    )
    hold_count = int(
        real.get("technical_hold_candidates_count")
        or real.get("backlog_technical_hold")
        or 0
    )
    terminal_unavailable = int(
        real.get("direct_search_unavailable_count")
        or real.get("backlog_terminal_unavailable")
        or 0
    )
    restart_allowed = summary.get("restart_allowed")
    next_action = (
        "wait_until_next_retry_at"
        if (
            hold_count > 0
            or "circuit" in stable_reason
            or "consecutive_failure_limit" in stable_reason
        )
        else "continue_next_candidate"
        if stable_reason == "username_not_found_confirmed"
        else "rebuild_phase_plan_from_canonical_backlog"
    )
    return {
        "reason_code": reason_code,
        "blocked_phase": blocked_phase,
        "requested_phases": {
            phase: requested_phases.get(phase) is True
            for phase in ("welcome", "follow", "unfollow")
        },
        "follow_target": int(quota_remaining.get("follow") or 0),
        "follow_remaining": follow_remaining,
        "unfollow_actionable_remaining": unfollow_actionable,
        "unfollow_candidates_on_hold": hold_count,
        "unfollow_terminally_unavailable": terminal_unavailable,
        "stable_reason": stable_reason,
        "auto_restart_allowed": restart_allowed is True,
        "auto_restart_blocked": restart_allowed is False,
        "next_retry_at": (
            real.get("phase_circuit_next_retry_at")
            or real.get("next_candidate_retry_at")
        ),
        "suggested_next_action": next_action,
    }
