"""Sanitized structured root cause for account-session incident channels."""

from __future__ import annotations

from typing import Any


def _record(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _text(*values: object) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _count(*values: object) -> int | None:
    """Return the first canonical non-negative integer, preserving zero."""

    for value in values:
        if value is None or isinstance(value, bool):
            continue
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if number >= 0:
            return number
    return None


def _remaining(*, target: int | None, done: int | None, direct: int | None) -> int:
    if direct is not None:
        return direct
    if target is not None and done is not None:
        return max(0, target - done)
    return 0


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

    terminal_reason = _text(
        summary.get("terminal_reason"),
        summary.get("root_failure_code"),
        contract.get("reason"),
        primary_failure_reason,
        "account_session_phase_not_terminal",
    )
    safe_stop_reason = _text(
        summary.get("safe_stop_reason"),
        summary.get("follow_stop_reason"),
        outcome.get("stable_reason"),
        real.get("stop_reason"),
        real.get("failure_reason"),
    )
    root_failure_reason = _text(
        summary.get("root_failure_reason"),
        summary.get("first_causal_reason"),
        _record(summary.get("follow_outcome")).get("first_causal_reason"),
        safe_stop_reason,
        terminal_reason,
    )
    blocked_phase = next(iter(non_terminal), "unknown")
    root_failure_phase = _text(
        summary.get("root_failure_phase"),
        summary.get("failure_phase"),
        "follow" if root_failure_reason and blocked_phase == "follow" else "",
        blocked_phase,
        "unknown",
    )
    stable_reason = root_failure_reason
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

    follow_target = _count(
        summary.get("follow_quota_target"),
        summary.get("follows_goal_effective"),
    )
    follows_completed = _count(summary.get("follows_completed_count"))
    follow_remaining_direct = _count(
        summary.get("follow_quota_remaining"),
        summary.get("remaining_follow_quota"),
        quota_remaining.get("follow"),
    )
    if follow_target is None and follow_remaining_direct is not None:
        follow_target = (follows_completed or 0) + follow_remaining_direct
    if follows_completed is None and follow_target is not None and follow_remaining_direct is not None:
        follows_completed = max(0, follow_target - follow_remaining_direct)
    follow_remaining = _remaining(
        target=follow_target,
        done=follows_completed,
        direct=follow_remaining_direct,
    )

    unfollow_target = _count(
        summary.get("unfollow_quota_target"),
        real.get("real_max_actions_effective"),
        real.get("real_max_actions"),
    )
    unfollows_completed = _count(
        summary.get("unfollow_actions_verified"),
        summary.get("unfollow_results_persisted_count"),
        real.get("unfollow_actions_verified"),
        real.get("unfollow_results_persisted_count"),
    )
    unfollow_remaining_direct = _count(
        summary.get("unfollow_quota_remaining"),
        quota_remaining.get("unfollow"),
    )
    if unfollow_target is None and unfollow_remaining_direct is not None:
        unfollow_target = (unfollows_completed or 0) + unfollow_remaining_direct
    if (
        unfollows_completed is None
        and unfollow_target is not None
        and unfollow_remaining_direct is not None
    ):
        unfollows_completed = max(0, unfollow_target - unfollow_remaining_direct)
    unfollow_remaining = _remaining(
        target=unfollow_target,
        done=unfollows_completed,
        direct=unfollow_remaining_direct,
    )
    unfollow_actionable = _count(
        real.get("last_run_remaining_eligible"),
        outcome.get("remaining_count"),
        unfollow_remaining_direct,
    )
    hold_count = _count(
        real.get("technical_hold_candidates_count"),
        real.get("backlog_technical_hold"),
    )
    terminal_unavailable = _count(
        real.get("direct_search_unavailable_count"),
        real.get("backlog_terminal_unavailable"),
    )
    restart_allowed = summary.get("restart_allowed")
    next_action = (
        "wait_until_next_retry_at"
        if (
            (hold_count or 0) > 0
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
        "follow_quota_target": follow_target or 0,
        "follows_completed_count": follows_completed or 0,
        "follow_quota_remaining": follow_remaining,
        "unfollow_quota_target": unfollow_target or 0,
        "unfollow_actions_verified": unfollows_completed or 0,
        "unfollow_quota_remaining": unfollow_remaining,
        # Compatibility aliases for the current Router/BotApp consumers.
        "follow_target": follow_target or 0,
        "follow_remaining": follow_remaining,
        "unfollow_actionable_remaining": unfollow_actionable or 0,
        "unfollow_candidates_on_hold": hold_count or 0,
        "unfollow_terminally_unavailable": terminal_unavailable or 0,
        "root_failure_reason": root_failure_reason,
        "root_failure_phase": root_failure_phase,
        "safe_stop_reason": safe_stop_reason,
        "terminal_reason": terminal_reason,
        "stable_reason": stable_reason,
        "auto_restart_allowed": restart_allowed is True,
        "auto_restart_blocked": restart_allowed is False,
        "next_retry_at": (
            real.get("phase_circuit_next_retry_at")
            or real.get("next_candidate_retry_at")
        ),
        "suggested_next_action": next_action,
    }
