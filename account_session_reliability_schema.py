"""
Account session reliability schema helpers.

V1C is intentionally non-destructive: this module only builds dictionaries for
future admin dashboard fields and escalation payloads. It does not send alerts,
write to Supabase, schedule restarts, or execute Instagram flows.
"""

from __future__ import annotations

from typing import Any

UNKNOWN = "unknown"


def _value(*values: Any, default: Any = UNKNOWN) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return default


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


def _nested(mapping: dict[str, Any] | None, *path: str) -> Any:
    current: Any = mapping or {}
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


_UNSAFE_SEMANTIC_KEYS = frozenset(
    {
        "failure_reason",
        "root_failure_code",
        "specific_failure_reason",
        "hard_stop_reason",
        "safety_status",
        "runtime_status",
        "instagram_state",
        "login_state",
        "account_identity_status",
        "last_run_stop_reason",
        "follow_stop_reason",
        "unfollow_stop_reason",
    }
)


def _semantic_safety_text(value: Any) -> list[str]:
    """Read safety evidence only from fields that carry failure/state values.

    Session summaries also contain benign structural keys such as
    ``checkpoint`` and ``last_safe_checkpoint``.  Flattening the complete JSON
    makes those keys look like an Instagram challenge and creates false
    critical incidents after an otherwise safe partial run.
    """
    if not isinstance(value, dict):
        return []
    out: list[str] = []
    for key, child in value.items():
        normalized_key = str(key).strip().lower()
        if normalized_key in _UNSAFE_SEMANTIC_KEYS and isinstance(
            child, (str, int, float, bool)
        ):
            out.append(str(child).strip().lower())
        if isinstance(child, dict):
            out.extend(_semantic_safety_text(child))
        elif isinstance(child, (list, tuple)):
            for item in child:
                if isinstance(item, dict):
                    out.extend(_semantic_safety_text(item))
    return out


def _unsafe_markers(summary: dict[str, Any], resume_plan: dict[str, Any]) -> list[str]:
    explicit = resume_plan.get("unsafe_markers")
    if isinstance(explicit, list):
        markers = [str(item) for item in explicit if str(item).strip()]
    else:
        markers = []
    text = " ".join(
        _semantic_safety_text({"summary": summary, "resume_plan": resume_plan})
    )
    marker_patterns = (
        ("challenge", ("challenge", "checkpoint")),
        ("restriction", ("restriction", "restricted")),
        ("account_mismatch", ("active_instagram_account_mismatch", "account_mismatch")),
        ("action_block", ("action_block", "action block", "action-block", "feedback_required")),
        ("device_offline", ("device_offline", "device offline", "adb_offline", "offline")),
    )
    for marker, patterns in marker_patterns:
        if marker not in markers and any(pattern in text for pattern in patterns):
            markers.append(marker)
    return markers


def _quota_value(
    summary: dict[str, Any],
    resume_plan: dict[str, Any],
    *,
    phase: str,
    field: str,
    fallback_keys: tuple[str, ...],
) -> Any:
    bucket_name = {
        "target": "quota_targets",
        "done": "quota_done",
        "remaining": "quota_remaining",
    }[field]
    from_plan = _nested(resume_plan, bucket_name, phase)
    if from_plan is not None and from_plan != UNKNOWN:
        return from_plan
    for key in fallback_keys:
        value = summary.get(key)
        if value is not None:
            return value
    return UNKNOWN


def _current_phase(summary: dict[str, Any], resume_plan: dict[str, Any] | None = None) -> str:
    statuses = {
        "welcome": str(summary.get("welcome_phase_status") or ""),
        "follow": str(summary.get("follow_phase_status") or ""),
        "unfollow": str(summary.get("unfollow_phase_status") or ""),
    }
    for phase in ("welcome", "follow", "unfollow"):
        if statuses[phase] in {"running", "in_progress"}:
            return phase
    session_status = str(summary.get("session_status") or "")
    restart_allowed = _as_bool(
        _value(
            summary.get("restart_allowed"),
            _nested(resume_plan or {}, "restart_allowed"),
            default=None,
        )
    )
    if session_status == "success":
        if restart_allowed is True:
            return "idle_waiting_restart"
        if restart_allowed is False:
            return "completed"
        return UNKNOWN
    return UNKNOWN


def _restart_count(summary: dict[str, Any], resume_plan: dict[str, Any]) -> Any:
    restart_count = _as_int(summary.get("restart_count"))
    if restart_count is not None:
        return restart_count
    current_attempt = _as_int(
        _value(
            summary.get("current_attempt_id"),
            summary.get("attempt_id"),
            resume_plan.get("current_attempt_id"),
            default=None,
        )
    )
    if current_attempt is None:
        return UNKNOWN
    return max(0, current_attempt - 1)


def _build_badges(snapshot: dict[str, Any]) -> list[str]:
    badges: list[str] = []
    termination = str(snapshot.get("session_termination_class") or "")
    session_status = str(snapshot.get("session_status") or "")
    restart_allowed = _as_bool(snapshot.get("restart_allowed"))
    restart_block_reason = str(snapshot.get("restart_block_reason") or "")
    restart_eligibility = str(snapshot.get("restart_eligibility") or "")
    unsafe = set(snapshot.get("unsafe_markers") or [])
    phase_statuses = {
        str(snapshot.get("welcome_phase_status") or ""),
        str(snapshot.get("follow_phase_status") or ""),
        str(snapshot.get("unfollow_phase_status") or ""),
    }
    quota_remaining_values = [
        _as_int(snapshot.get("follow_quota_remaining")),
        _as_int(snapshot.get("unfollow_quota_remaining")),
        _as_int(snapshot.get("welcome_remaining")),
    ]
    quota_remaining_total = sum(
        value for value in quota_remaining_values if value is not None
    )
    has_known_quota_remaining = any(value is not None for value in quota_remaining_values)
    non_blocking_restart_reasons = {
        "",
        "session_completed",
        "restart_not_needed",
        "safe_continued_no_known_quota_remaining",
        "no_quota_remaining",
    }

    if (
        session_status == "running"
        or "running" in phase_statuses
    ):
        badges.append("running")
    if termination == "completed":
        badges.append("completed")
    if termination == "partial_resumable":
        badges.append("partial_resumable")
    if restart_allowed:
        badges.append("restart_scheduled")
    elif (
        restart_eligibility == "blocked"
        or (has_known_quota_remaining and quota_remaining_total > 0)
        or restart_block_reason not in non_blocking_restart_reasons
    ):
        badges.append("restart_blocked")
    if termination == "completed" and restart_eligibility in {"not_needed", ""}:
        badges.append("healthy")
    if "challenge" in unsafe:
        badges.append("challenge")
        badges.append("needs_human_review")
    if "restriction" in unsafe or "action_block" in unsafe:
        badges.append("restriction")
        badges.append("needs_human_review")
    if "account_mismatch" in unsafe:
        badges.append("account_mismatch")
        badges.append("needs_human_review")
    if "device_offline" in unsafe:
        badges.append("device_offline")
        badges.append("needs_human_review")
    if (
        snapshot.get("mandatory_unfollow_executed") is False
        and snapshot.get("restart_allowed") is not True
        and snapshot.get("failure_category") != "recoverable_python_runtime_failure"
    ):
        if str(snapshot.get("follow_phase_status") or "") in {"completed", "partial_safe_stopped"}:
            badges.append("mandatory_unfollow_missing")
            badges.append("needs_human_review")

    deduped: list[str] = []
    for badge in badges:
        if badge not in deduped:
            deduped.append(badge)
    return deduped


def build_admin_reliability_snapshot(
    summary: dict,
    resume_plan: dict | None = None,
) -> dict:
    """Build the future admin dashboard reliability snapshot from known fields."""
    safe_summary: dict[str, Any] = dict(summary or {})
    safe_plan: dict[str, Any] = dict(resume_plan or safe_summary.get("auto_restart_resume_plan") or {})
    phases_to_run = safe_plan.get("phases_to_run") if isinstance(safe_plan.get("phases_to_run"), dict) else {}
    unsafe = _unsafe_markers(safe_summary, safe_plan)

    snapshot = {
        "account_id": _value(safe_summary.get("account_id")),
        "account_username": _value(safe_summary.get("account_username")),
        "package_name": _value(
            safe_summary.get("package_name"),
            safe_summary.get("package_id"),
            safe_summary.get("instagram_expected_package"),
        ),
        "package_id": _value(safe_summary.get("package_id"), safe_summary.get("package_name")),
        "phone_id": _value(safe_summary.get("phone_id"), safe_summary.get("device_id")),
        "device_id": _value(safe_summary.get("device_id"), safe_summary.get("phone_id")),
        "clone_id": _value(safe_summary.get("clone_id")),
        "business_session_id": _value(
            safe_plan.get("business_session_id"),
            safe_summary.get("business_session_id"),
            safe_summary.get("run_id"),
        ),
        "run_id": _value(safe_summary.get("run_id")),
        "attempt_id": _value(
            safe_summary.get("attempt_id"),
            safe_summary.get("current_attempt_id"),
            safe_plan.get("current_attempt_id"),
        ),
        "current_attempt_id": _value(
            safe_summary.get("current_attempt_id"),
            safe_summary.get("attempt_id"),
            safe_plan.get("current_attempt_id"),
        ),
        "retry_index": _value(
            safe_summary.get("retry_index"),
            safe_plan.get("retry_index"),
        ),
        "next_retry_index": _value(safe_plan.get("next_retry_index")),
        "restart_count": UNKNOWN,
        "session_status": _value(safe_summary.get("session_status")),
        "session_termination_class": _value(safe_summary.get("session_termination_class")),
        "welcome_phase_status": _value(safe_summary.get("welcome_phase_status")),
        "follow_phase_status": _value(safe_summary.get("follow_phase_status")),
        "unfollow_phase_status": _value(safe_summary.get("unfollow_phase_status")),
        "current_phase": UNKNOWN,
        "follow_quota_target": _quota_value(
            safe_summary,
            safe_plan,
            phase="follow",
            field="target",
            fallback_keys=("follow_quota_target", "follows_goal_effective"),
        ),
        "follows_completed_count": _quota_value(
            safe_summary,
            safe_plan,
            phase="follow",
            field="done",
            fallback_keys=("follows_completed_count",),
        ),
        "follow_quota_remaining": _quota_value(
            safe_summary,
            safe_plan,
            phase="follow",
            field="remaining",
            fallback_keys=("follow_quota_remaining",),
        ),
        "unfollow_quota_target": _quota_value(
            safe_summary,
            safe_plan,
            phase="unfollow",
            field="target",
            fallback_keys=("unfollow_quota_target", "unfollow_target"),
        ),
        "unfollow_actions_verified": _quota_value(
            safe_summary,
            safe_plan,
            phase="unfollow",
            field="done",
            fallback_keys=("unfollow_actions_verified", "unfollow_results_persisted_count"),
        ),
        "unfollow_quota_remaining": _quota_value(
            safe_summary,
            safe_plan,
            phase="unfollow",
            field="remaining",
            fallback_keys=("unfollow_quota_remaining",),
        ),
        "welcome_quota_target": _quota_value(
            safe_summary,
            safe_plan,
            phase="welcome",
            field="target",
            fallback_keys=("welcome_quota_target",),
        ),
        "welcome_done": _quota_value(
            safe_summary,
            safe_plan,
            phase="welcome",
            field="done",
            fallback_keys=("welcome_done", "welcome_sender_jobs_sent_count"),
        ),
        "welcome_remaining": _quota_value(
            safe_summary,
            safe_plan,
            phase="welcome",
            field="remaining",
            fallback_keys=("welcome_remaining",),
        ),
        "restart_eligibility": _value(
            safe_plan.get("restart_eligibility"),
            safe_summary.get("restart_eligibility"),
        ),
        "restart_allowed": _value(
            safe_plan.get("restart_allowed"),
            safe_summary.get("auto_restart_restart_allowed"),
        ),
        "restart_block_reason": _value(
            safe_plan.get("restart_block_reason"),
            safe_summary.get("auto_restart_restart_block_reason"),
            safe_summary.get("restart_block_reason"),
        ),
        "next_restart_at": _value(safe_plan.get("next_restart_at"), safe_summary.get("next_restart_at")),
        "failure_reason": _value(
            safe_summary.get("failure_reason"),
            safe_summary.get("auto_restart_resume_plan_error"),
            _nested(safe_summary, "follow_to_unfollow_real", "failure_reason"),
        ),
        "failure_category": _value(
            safe_summary.get("failure_category"),
            safe_plan.get("failure_category"),
        ),
        "root_failure_code": _value(
            safe_summary.get("root_failure_code"),
            safe_plan.get("root_failure_code"),
        ),
        "failure_signature": _value(
            safe_summary.get("failure_signature"),
            safe_plan.get("failure_signature"),
        ),
        "account_health_status": _value(safe_summary.get("account_health_status")),
        "last_error": _value(
            safe_summary.get("last_error"),
            safe_summary.get("auto_restart_resume_plan_error"),
            safe_summary.get("failure_reason"),
        ),
        "mandatory_unfollow_executed": _value(safe_summary.get("mandatory_unfollow_executed")),
        "phases_to_run": phases_to_run,
        "unsafe_markers": unsafe,
    }
    snapshot["restart_count"] = _restart_count(safe_summary, safe_plan)
    snapshot["current_phase"] = _current_phase(snapshot, safe_plan)
    snapshot["badges"] = _build_badges(snapshot)
    return snapshot


def _total_quota_remaining(snapshot: dict[str, Any], resume_plan: dict[str, Any]) -> int | None:
    total = _as_int(_nested(resume_plan, "quota_remaining", "total"))
    if total is not None:
        return total
    values = [
        _as_int(snapshot.get("follow_quota_remaining")),
        _as_int(snapshot.get("unfollow_quota_remaining")),
        _as_int(snapshot.get("welcome_remaining")),
    ]
    known = [value for value in values if value is not None]
    return sum(known) if known else None


def _mandatory_unfollow_expected(snapshot: dict[str, Any], resume_plan: dict[str, Any]) -> bool:
    if (
        snapshot.get("failure_category") == "recoverable_python_runtime_failure"
        and not snapshot.get("unsafe_markers")
    ):
        return False
    if _as_bool(snapshot.get("restart_allowed")) is True and not snapshot.get("unsafe_markers"):
        return False
    phase_status = str(snapshot.get("follow_phase_status") or "")
    unfollow_remaining = _as_int(snapshot.get("unfollow_quota_remaining"))
    planned_unfollow = _as_bool(_nested(resume_plan, "phases_to_run", "unfollow"))
    return (
        phase_status in {"completed", "partial_safe_stopped"}
        and (unfollow_remaining is None or unfollow_remaining > 0 or planned_unfollow is True)
    )


def _event_type_and_severity(
    snapshot: dict[str, Any],
    resume_plan: dict[str, Any],
) -> tuple[str | None, str, str, str]:
    unsafe = set(snapshot.get("unsafe_markers") or [])
    if "challenge" in unsafe:
        return "challenge_detected", "critical", "challenge_detected", "Human review required."
    if "restriction" in unsafe or "action_block" in unsafe:
        return "restriction_detected", "critical", "restriction_detected", "Pause automation and review account."
    if "account_mismatch" in unsafe:
        return "account_mismatch_detected", "critical", "account_mismatch_detected", "Verify logged-in Instagram account."
    if "device_offline" in unsafe:
        return "device_offline", "critical", "device_offline", "Recover device connectivity."

    if snapshot.get("mandatory_unfollow_executed") is False and _mandatory_unfollow_expected(
        snapshot,
        resume_plan,
    ):
        return (
            "mandatory_unfollow_not_executed",
            "critical",
            "mandatory_unfollow_missing",
            "Run or inspect mandatory Unfollow path before restart.",
        )

    failure_reason = str(snapshot.get("failure_reason") or "")
    if failure_reason == "resume_plan_builder_failed" or snapshot.get("last_error") == "resume_plan_builder_failed":
        return "resume_plan_builder_failed", "warning", "resume_plan_builder_failed", "Inspect resume-plan inputs."

    restart_allowed = _as_bool(snapshot.get("restart_allowed"))
    block_reason = str(snapshot.get("restart_block_reason") or "")
    remaining = _total_quota_remaining(snapshot, resume_plan)
    if (
        snapshot.get("failure_category") == "recoverable_python_runtime_failure"
        and not unsafe
    ):
        next_retry_index = _as_int(resume_plan.get("next_retry_index"))
        if restart_allowed is True and next_retry_index == 1:
            return (
                "recoverable_python_failure_restart_1_scheduled",
                "info",
                "recoverable_python_runtime_failure",
                "",
            )
        if restart_allowed is True and next_retry_index == 2:
            return (
                "recoverable_python_failure_restart_2_scheduled",
                "info",
                "recoverable_python_runtime_failure",
                "",
            )
        if block_reason == "auto_restart_retries_exhausted":
            return (
                "recoverable_python_bug_retries_exhausted",
                "warning",
                "auto_restart_retries_exhausted",
                "",
            )
    if restart_allowed is True:
        return "restart_scheduled", "info", "restart_allowed", "Monitor next restart attempt."
    if block_reason and remaining is not None and remaining > 0:
        severity = "critical" if "max_attempts" in block_reason else "warning"
        event_type = (
            "max_restart_attempts_reached"
            if "max_attempts" in block_reason
            else "quota_not_reached_restart_blocked"
        )
        return event_type, severity, block_reason, "Review restart block reason in dashboard."

    termination = str(snapshot.get("session_termination_class") or "")
    restart_eligibility = str(snapshot.get("restart_eligibility") or "")
    if termination == "completed" and restart_eligibility == "not_needed":
        return None, "info", "session_completed", ""

    important_unknowns = [
        key
        for key in ("business_session_id", "restart_allowed", "follow_quota_remaining")
        if snapshot.get(key) == UNKNOWN
    ]
    if important_unknowns and block_reason:
        return (
            "quota_not_reached_restart_blocked",
            "warning",
            "unknown_fields_block_restart:" + ",".join(important_unknowns),
            "Backfill missing dashboard fields.",
        )
    return None, "info", "no_escalation", ""


def _progress_summary(snapshot: dict[str, Any]) -> str:
    follow = (
        f"follow {snapshot.get('follows_completed_count')}/"
        f"{snapshot.get('follow_quota_target')}"
        f" remaining={snapshot.get('follow_quota_remaining')}"
    )
    unfollow = (
        f"unfollow {snapshot.get('unfollow_actions_verified')}/"
        f"{snapshot.get('unfollow_quota_target')}"
        f" remaining={snapshot.get('unfollow_quota_remaining')}"
    )
    return f"{follow}; {unfollow}"


def _alert_payload(
    *,
    channel: str,
    event_type: str,
    severity: str,
    reason: str,
    action_required: str,
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    return {
        "channel": channel,
        "title": f"{severity.upper()}: {event_type}",
        "severity": severity,
        "account_username": snapshot.get("account_username"),
        "package": snapshot.get("package_name"),
        "phase": snapshot.get("current_phase"),
        "progress_summary": _progress_summary(snapshot),
        "reason": reason,
        "action_required": action_required,
        "dashboard_url": "DASHBOARD_URL_PLACEHOLDER",
        "business_session_id": snapshot.get("business_session_id"),
        "attempt_id": snapshot.get("current_attempt_id"),
    }


def build_escalation_event(
    summary: dict,
    resume_plan: dict | None = None,
) -> dict | None:
    """
    Build a future production escalation event.

    Returns None when no escalation is required. The returned payloads are
    dictionaries only; no Slack/Discord SDK or network calls are used.
    """
    safe_plan: dict[str, Any] = dict(resume_plan or (summary or {}).get("auto_restart_resume_plan") or {})
    snapshot = build_admin_reliability_snapshot(summary, safe_plan)
    event_type, severity, reason, action_required = _event_type_and_severity(snapshot, safe_plan)
    if event_type is None:
        return None
    return {
        "event_type": event_type,
        "severity": severity,
        "reason": reason,
        "action_required": action_required,
        "account_id": snapshot.get("account_id"),
        "account_username": snapshot.get("account_username"),
        "business_session_id": snapshot.get("business_session_id"),
        "run_id": snapshot.get("run_id"),
        "attempt_id": snapshot.get("current_attempt_id"),
        "session_status": snapshot.get("session_status"),
        "session_termination_class": snapshot.get("session_termination_class"),
        "current_phase": snapshot.get("current_phase"),
        "restart_allowed": snapshot.get("restart_allowed"),
        "restart_block_reason": snapshot.get("restart_block_reason"),
        "quota_remaining": {
            "follow": snapshot.get("follow_quota_remaining"),
            "unfollow": snapshot.get("unfollow_quota_remaining"),
            "welcome": snapshot.get("welcome_remaining"),
            "total": _total_quota_remaining(snapshot, safe_plan) or UNKNOWN,
        },
        "badges": snapshot.get("badges"),
        "unsafe_markers": snapshot.get("unsafe_markers"),
        "alert_payloads": {
            "slack": _alert_payload(
                channel="slack",
                event_type=event_type,
                severity=severity,
                reason=reason,
                action_required=action_required,
                snapshot=snapshot,
            ),
            "discord": _alert_payload(
                channel="discord",
                event_type=event_type,
                severity=severity,
                reason=reason,
                action_required=action_required,
                snapshot=snapshot,
            ),
        },
    }
