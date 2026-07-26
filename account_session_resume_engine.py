"""
Account session Auto Restart / Quota Resume V1A plan builder.

This module is intentionally passive: it only interprets an account session
summary and returns a restart plan. It does not schedule, dispatch, or execute
any Instagram action.
"""

from __future__ import annotations

from typing import Any

import config
from logs import log

UNKNOWN = "unknown"
AUTO_RESTART_MAX_RETRIES_AFTER_INITIAL_FAILURE = 2
AUTO_RESTART_TOTAL_ATTEMPTS = AUTO_RESTART_MAX_RETRIES_AFTER_INITIAL_FAILURE + 1
FOLLOW_SCROLL_RESUMABLE_REASON = "visible_window_exhausted_scroll_failed"


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return None


def _as_int(value: Any) -> int | None:
    if value is None or value == UNKNOWN:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _setting(settings: dict[str, Any], key: str, default: Any) -> Any:
    return settings.get(key, default)


def _nested(summary: dict[str, Any], *path: str) -> Any:
    current: Any = summary
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _flatten_marker_text(value: Any, *, parent_key: str = "") -> list[str]:
    # Avoid counting the plan's own block labels as platform unsafe signals.
    ignored_keys = {
        "restart_eligibility",
        "restart_block_reason",
        "session_termination_class",
    }
    if parent_key in ignored_keys:
        return []
    if isinstance(value, dict):
        out: list[str] = []
        for key, child in value.items():
            out.extend(_flatten_marker_text(child, parent_key=str(key)))
        return out
    if isinstance(value, (list, tuple, set)):
        out = []
        for child in value:
            out.extend(_flatten_marker_text(child, parent_key=parent_key))
        return out
    if value is None:
        return []
    return [str(value).lower()]


def _unsafe_markers(summary: dict[str, Any]) -> list[str]:
    text = " ".join(_flatten_marker_text(summary))
    markers: list[str] = []
    marker_patterns = (
        ("account_mismatch", ("active_instagram_account_mismatch", "account_mismatch")),
        ("challenge", ("challenge", "checkpoint")),
        ("restriction", ("restriction", "restricted")),
        ("action_block", ("action_block", "action block", "action-block", "feedback_required")),
    )
    for marker, patterns in marker_patterns:
        if any(pattern in text for pattern in patterns):
            markers.append(marker)
    return markers


def _follow_quota(summary: dict[str, Any]) -> tuple[int | None, int | None, int | None]:
    target = _as_int(summary.get("follow_quota_target"))
    if target is None:
        target = _as_int(summary.get("follows_goal_effective"))
    done = _as_int(summary.get("follows_completed_count"))
    remaining = _as_int(summary.get("follow_quota_remaining"))
    if remaining is None and target is not None and done is not None:
        remaining = max(0, target - done)
    return target, done, remaining


def _unfollow_quota(
    summary: dict[str, Any],
    settings: dict[str, Any],
) -> tuple[int | None, int | None, int | None]:
    target = _as_int(summary.get("unfollow_quota_target"))
    if target is None:
        target = _as_int(summary.get("unfollow_target"))
    if target is None:
        target = _as_int(_setting(settings, "unfollow_quota_target", None))
    if target is None:
        target = _as_int(_setting(settings, "unfollow_target", None))
    if target is None:
        target = _as_int(
            _nested(summary, "follow_to_unfollow_real", "real_max_actions_effective")
        )
    if target is None:
        target = _as_int(_nested(summary, "follow_to_unfollow_real", "real_max_actions"))

    done = _as_int(summary.get("unfollow_actions_verified"))
    if done is None:
        done = _as_int(_nested(summary, "follow_to_unfollow_real", "unfollow_actions_verified"))
    if done is None:
        done = _as_int(summary.get("unfollow_results_persisted_count"))
    if done is None:
        done = _as_int(
            _nested(summary, "follow_to_unfollow_real", "unfollow_results_persisted_count")
        )

    remaining = _as_int(summary.get("unfollow_quota_remaining"))
    if remaining is None and target is not None and done is not None:
        remaining = max(0, target - done)
    return target, done, remaining


def _phase_to_run_welcome(summary: dict[str, Any]) -> bool | str:
    enabled = _as_bool(summary.get("welcome_enabled"))
    status = str(summary.get("welcome_phase_status") or "").strip()
    if enabled is False or status in {"skipped", "completed"}:
        return False
    if enabled is True and status and status != "completed":
        return True
    if enabled is True and not status:
        return UNKNOWN
    return UNKNOWN


def _phase_to_run_follow(follow_remaining: int | None) -> bool | str:
    if follow_remaining is None:
        return UNKNOWN
    return follow_remaining > 0


def _phase_to_run_unfollow(
    summary: dict[str, Any],
    unfollow_target: int | None,
    unfollow_done: int | None,
    unfollow_remaining: int | None,
) -> bool | str:
    mandatory_done = _as_bool(summary.get("mandatory_unfollow_executed"))
    if unfollow_remaining is not None:
        return bool(mandatory_done is False and unfollow_remaining > 0)
    if unfollow_target is not None and unfollow_done is not None:
        return bool(mandatory_done is False and unfollow_target > unfollow_done)
    if mandatory_done is True:
        return False
    return UNKNOWN


def _attempt_ids(
    summary: dict[str, Any],
    settings: dict[str, Any],
) -> tuple[int | str, int | str]:
    current = _as_int(summary.get("current_attempt_id"))
    if current is None:
        current = _as_int(summary.get("attempt_id"))
    if current is None:
        current = _as_int(_setting(settings, "current_attempt_id", None))
    if current is None:
        # A normal scheduler/manual session is attempt 1. Auto Restart request
        # metadata supplies attempt 2/3 explicitly through ``settings``.
        current = 1
    return current, current + 1


def _business_session_id(summary: dict[str, Any], settings: dict[str, Any]) -> str:
    value = (
        summary.get("business_session_id")
        or _setting(settings, "business_session_id", None)
        or summary.get("run_id")
    )
    return str(value).strip() if value else UNKNOWN


def _initial_block_reason(
    *,
    termination_class: str,
    restart_eligibility: str,
    unsafe_markers: list[str],
) -> str:
    if unsafe_markers:
        return "unsafe_markers:" + ",".join(unsafe_markers)
    if termination_class == "completed":
        return "session_completed"
    if restart_eligibility == "not_needed":
        return "restart_not_needed"
    if restart_eligibility == "blocked":
        return "restart_eligibility_blocked"
    return ""


def _follow_resume_checkpoint_block_reason(summary: dict[str, Any]) -> str:
    """Require an explicit safe CT boundary for the scroll-failure reason.

    Other historically resumable failure classes keep their existing policy.
    This guard only prevents an ambiguous Followers viewport from being turned
    into an automatic retry merely because quota remains.
    """
    reason = str(
        summary.get("follow_stop_reason")
        or summary.get("follow_session_outcome")
        or ""
    ).strip()
    if reason != FOLLOW_SCROLL_RESUMABLE_REASON:
        return ""
    outcome = summary.get("follow_outcome")
    if isinstance(outcome, dict) and outcome:
        if (
            outcome.get("phase_status") == "partial_resumable"
            and outcome.get("resumable") is True
            and outcome.get("safe_boundary") is True
            and str(outcome.get("last_safe_checkpoint") or "").strip()
        ):
            return ""
    if (
        summary.get("target_rotation_safe_after_scroll_failure") is True
        and summary.get("scroll_failure_surface_ambiguous") is not True
    ):
        return ""
    return "unsafe_follow_resume_checkpoint"


def build_account_session_resume_plan(
    summary: dict,
    settings: dict | None = None,
) -> dict:
    """
    Build a passive restart/resume plan from an account_session_summary-like dict.

    The returned plan is advisory only. This function never launches sessions,
    never calls Follow/Unfollow/Welcome code, and never writes to Supabase.
    """
    safe_summary: dict[str, Any] = dict(summary or {})
    safe_settings: dict[str, Any] = dict(settings or {})

    termination_class = str(
        safe_summary.get("session_termination_class") or UNKNOWN
    ).strip()
    restart_eligibility = str(
        safe_summary.get("restart_eligibility") or UNKNOWN
    ).strip()
    unsafe = _unsafe_markers(safe_summary)
    follow_target, follow_done, follow_remaining = _follow_quota(safe_summary)
    unfollow_target, unfollow_done, unfollow_remaining = _unfollow_quota(
        safe_summary,
        safe_settings,
    )
    total_known_remaining = [
        value for value in (follow_remaining, unfollow_remaining) if value is not None
    ]
    total_quota_remaining = sum(total_known_remaining) if total_known_remaining else None

    current_attempt_id, next_attempt_id = _attempt_ids(safe_summary, safe_settings)
    max_retries_after_initial_failure = _as_int(
        _setting(
            safe_settings,
            "auto_restart_max_retries_after_initial_failure",
            getattr(
                config,
                "AUTO_RESTART_MAX_RETRIES_AFTER_INITIAL_FAILURE",
                AUTO_RESTART_MAX_RETRIES_AFTER_INITIAL_FAILURE,
            ),
        )
    )
    if max_retries_after_initial_failure is None:
        max_retries_after_initial_failure = AUTO_RESTART_MAX_RETRIES_AFTER_INITIAL_FAILURE
    max_retries_after_initial_failure = max(0, max_retries_after_initial_failure)
    delay_minutes = _as_int(
        _setting(
            safe_settings,
            "auto_restart_delay_minutes",
            getattr(config, "AUTO_RESTART_DELAY_MINUTES", 20),
        )
    )
    if delay_minutes is None:
        delay_minutes = 20

    phases_to_run = {
        "welcome": _phase_to_run_welcome(safe_summary),
        "follow": _phase_to_run_follow(follow_remaining),
        "unfollow": _phase_to_run_unfollow(
            safe_summary,
            unfollow_target,
            unfollow_done,
            unfollow_remaining,
        ),
    }
    quota_targets = {
        "follow": follow_target if follow_target is not None else UNKNOWN,
        "unfollow": unfollow_target if unfollow_target is not None else UNKNOWN,
    }
    quota_done = {
        "follow": follow_done if follow_done is not None else UNKNOWN,
        "unfollow": unfollow_done if unfollow_done is not None else UNKNOWN,
    }
    quota_remaining = {
        "follow": follow_remaining if follow_remaining is not None else UNKNOWN,
        "unfollow": unfollow_remaining if unfollow_remaining is not None else UNKNOWN,
        "total": total_quota_remaining if total_quota_remaining is not None else UNKNOWN,
    }

    restart_allowed = False
    restart_block_reason = _initial_block_reason(
        termination_class=termination_class,
        restart_eligibility=restart_eligibility,
        unsafe_markers=unsafe,
    )
    if not restart_block_reason:
        restart_block_reason = _follow_resume_checkpoint_block_reason(safe_summary)
    reason = restart_block_reason or "restart_unknown"

    if not restart_block_reason:
        if termination_class in {"partial_safe_stopped", "partial_resumable"}:
            if total_quota_remaining is None:
                restart_block_reason = "quota_remaining_unknown"
                reason = restart_block_reason
            elif total_quota_remaining > 0:
                restart_allowed = True
                reason = "quota_remaining"
            else:
                restart_block_reason = "no_quota_remaining"
                reason = restart_block_reason
        elif restart_eligibility == "eligible":
            if total_quota_remaining is None:
                restart_block_reason = "quota_remaining_unknown"
                reason = restart_block_reason
            elif total_quota_remaining > 0:
                restart_allowed = True
                reason = "eligible_with_quota_remaining"
            else:
                restart_block_reason = "no_quota_remaining"
                reason = restart_block_reason
        elif termination_class == UNKNOWN or restart_eligibility == UNKNOWN:
            restart_block_reason = "unknown_resume_inputs"
            reason = restart_block_reason
        else:
            restart_block_reason = "restart_not_allowed_for_termination_class"
            reason = restart_block_reason

    retry_index = (
        max(0, current_attempt_id - 1)
        if isinstance(current_attempt_id, int)
        else UNKNOWN
    )
    next_retry_index = retry_index + 1 if isinstance(retry_index, int) else UNKNOWN
    if restart_allowed and isinstance(next_retry_index, int):
        if next_retry_index > max_retries_after_initial_failure:
            restart_allowed = False
            restart_block_reason = "auto_restart_retries_exhausted"
            reason = restart_block_reason
            # Exhaustion is terminal for this business session: do not expose
            # a synthetic attempt 4 / retry 3 that another scheduler could
            # mistake for executable work.
            next_attempt_id = None
            next_retry_index = None

    if restart_allowed and any(value == UNKNOWN for value in phases_to_run.values()):
        restart_allowed = False
        restart_block_reason = "phase_plan_unknown"
        reason = restart_block_reason

    plan = {
        "restart_allowed": bool(restart_allowed),
        "restart_block_reason": "" if restart_allowed else restart_block_reason,
        "restart_delay_minutes": int(delay_minutes),
        "business_session_id": _business_session_id(safe_summary, safe_settings),
        "current_attempt_id": current_attempt_id,
        "next_attempt_id": next_attempt_id,
        "attempt_id": current_attempt_id,
        "retry_index": retry_index,
        "next_retry_index": next_retry_index,
        "auto_restart_max_retries_after_initial_failure": max_retries_after_initial_failure,
        "total_attempts_allowed": max_retries_after_initial_failure + 1,
        "previous_run_id": _setting(
            safe_settings,
            "previous_run_id",
            safe_summary.get("previous_run_id"),
        ),
        "root_failure_code": safe_summary.get("root_failure_code"),
        "failure_signature": safe_summary.get("failure_signature"),
        "failure_category": safe_summary.get("failure_category"),
        "follow_stop_reason": safe_summary.get("follow_stop_reason"),
        "target_rotation_safe_after_scroll_failure": safe_summary.get(
            "target_rotation_safe_after_scroll_failure"
        ) is True,
        "scroll_failure_surface_ambiguous": safe_summary.get(
            "scroll_failure_surface_ambiguous"
        ) is True,
        "follow_outcome": (
            dict(safe_summary.get("follow_outcome") or {})
            if isinstance(safe_summary.get("follow_outcome"), dict)
            else {}
        ),
        "follow_partial": safe_summary.get("follow_partial") is True,
        "follow_resume_recommended": safe_summary.get("follow_resume_recommended") is True,
        "remaining_follow_quota": safe_summary.get("remaining_follow_quota"),
        "remaining_ct_count": safe_summary.get("remaining_ct_count"),
        "last_safe_checkpoint": safe_summary.get("last_safe_checkpoint"),
        "suggested_resume_strategy": safe_summary.get("suggested_resume_strategy"),
        "session_termination_class": termination_class,
        "restart_eligibility": restart_eligibility,
        "phases_to_run": phases_to_run,
        "quota_targets": quota_targets,
        "quota_done": quota_done,
        "quota_remaining": quota_remaining,
        "unsafe_markers": unsafe,
        "reason": reason,
    }

    log(
        "info",
        "auto_restart_resume_plan_built"
        if plan["restart_allowed"]
        else "auto_restart_resume_plan_blocked",
        restart_allowed=plan["restart_allowed"],
        restart_block_reason=plan["restart_block_reason"],
        session_termination_class=termination_class,
        restart_eligibility=restart_eligibility,
        business_session_id=plan["business_session_id"],
        current_attempt_id=plan["current_attempt_id"],
        next_attempt_id=plan["next_attempt_id"],
        retry_index=plan["retry_index"],
        next_retry_index=plan["next_retry_index"],
        max_retries_after_initial_failure=max_retries_after_initial_failure,
        quota_remaining=quota_remaining,
        phases_to_run=phases_to_run,
        unsafe_markers=unsafe,
        reason=reason,
    )
    return plan
