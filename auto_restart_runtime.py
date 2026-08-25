"""Auto Restart runtime validation and session resume policy."""

from __future__ import annotations

import json
import os
from typing import Any

from account_session_manual_resume import UNKNOWN, UNSUPPORTED_RUNTIME_STATUS, build_manual_resume_command
from logs import log

AUTO_RESTART_TICK_SOURCE = "auto_restart_tick"
AUTO_RESTART_RESUME_PLAN_ENV = "AUTO_RESTART_RESUME_PLAN_V1"
AUTO_RESTART_RESUME_PLAN_SCHEMA = "AUTO_RESTART_RESUME_PLAN_V1"
RESUME_PLAN_VERSION = 1
CANONICAL_RESUME_PLAN_SCHEMA_V2 = "AUTO_RESTART_RESUME_PLAN_V2"
CANONICAL_RESUME_PLAN_VERSION_V2 = 2

HARD_STOP_REASONS = frozenset(
    {
        "challenge_blocked",
        "restriction_blocked",
        "account_mismatch_blocked",
        "ui_state_unknown_blocked",
        "quota_inconsistency_blocked",
        "resume_plan_invalid",
        "unknown_required_field",
        "runtime_unknown_blocked",
        "challenge",
        "restriction",
        "account_mismatch",
        "action_block",
        "instagram_human_confirmation_required",
    }
)

_REASON_ALIASES = {
    "challenge": "challenge_blocked",
    "restriction": "restriction_blocked",
    "account_mismatch": "account_mismatch_blocked",
    "action_block": "restriction_blocked",
    "resume_runtime_not_supported": "resume_plan_invalid",
    "instagram_human_confirmation_required": "challenge_blocked",
}


def normalize_hard_stop_reason(reason: str) -> str:
    normalized = str(reason or "").strip()
    return _REASON_ALIASES.get(normalized, normalized)


def is_hard_stop_reason(reason: str) -> bool:
    return normalize_hard_stop_reason(reason) in HARD_STOP_REASONS


HUMAN_CONFIRMED_RESUME_MODE = "human_confirmed_resume"
FOLLOW60_ARMED_CONTROL_RESUME_MODE = "follow60_armed_control_resume"


def is_auto_restart_request(metadata: dict[str, Any] | None) -> bool:
    meta = dict(metadata or {})
    return bool(meta.get("auto_restart")) and str(meta.get("source") or "") == AUTO_RESTART_TICK_SOURCE


def is_human_confirmed_resume_request(metadata: dict[str, Any] | None) -> bool:
    meta = dict(metadata or {})
    return (
        is_auto_restart_request(meta)
        and str(meta.get("recovery_mode") or "") == HUMAN_CONFIRMED_RESUME_MODE
    )


def _read_record(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _as_int(value: Any) -> int | None:
    if value is None or value == UNKNOWN:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _canonical_request_attempt_context(
    meta: dict[str, Any],
    embedded: dict[str, Any],
) -> dict[str, int] | None:
    """Resolve attempt identity exclusively from the claimed request.

    A prior run projection is deliberately not a fallback: it describes the
    run being resumed and can therefore still say attempt 1 while the newly
    claimed Auto Restart request is attempt 2.  Explicit top-level request
    identity wins over the embedded plan; duplicate fields at the selected
    request layer are accepted only when they agree.
    """

    top_level_attempts = [meta.get("attempt_id"), meta.get("current_attempt_id")]
    embedded_attempts = [embedded.get("attempt_id"), embedded.get("current_attempt_id")]
    raw_attempts = (
        top_level_attempts
        if any(raw is not None and raw != "" for raw in top_level_attempts)
        else embedded_attempts
    )
    attempts: list[int] = []
    for raw in raw_attempts:
        if raw is None or raw == "":
            continue
        if isinstance(raw, bool):
            return None
        parsed = _as_int(raw)
        if parsed is None or parsed < 1:
            return None
        attempts.append(parsed)
    if not attempts or len(set(attempts)) != 1:
        return None

    attempt_id = attempts[0]
    raw_retry_indexes = (
        [meta.get("retry_index")]
        if any(raw is not None and raw != "" for raw in top_level_attempts)
        else [embedded.get("retry_index")]
    )
    retry_indexes: list[int] = []
    for raw in raw_retry_indexes:
        if raw is None or raw == "":
            continue
        if isinstance(raw, bool):
            return None
        parsed = _as_int(raw)
        if parsed is None or parsed < 0:
            return None
        retry_indexes.append(parsed)
    if len(set(retry_indexes)) > 1:
        return None
    retry_index = retry_indexes[0] if retry_indexes else attempt_id - 1
    if attempt_id != retry_index + 1 or attempt_id > 3 or retry_index > 2:
        return None
    return {"attempt_id": attempt_id, "retry_index": retry_index}


def _extract_resume_plan_from_run_row(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {}
    performance = _read_record(row.get("performance_summary"))
    plan = _read_record(performance.get("auto_restart_resume_plan"))
    if plan:
        return plan
    return _read_record(performance.get("admin_reliability_snapshot"))


def _quota_record(value: Any) -> dict[str, int | None]:
    raw = _read_record(value)
    return {
        "follow": _as_int(raw.get("follow")),
        "unfollow": _as_int(raw.get("unfollow")),
        "welcome": _as_int(raw.get("welcome")),
        "outreach": _as_int(raw.get("outreach")),
        "total": _as_int(raw.get("total")),
    }


def _validate_quota_consistency(
    *,
    phases: dict[str, Any],
    quota: dict[str, int | None],
) -> str | None:
    if not quota:
        return "unknown_required_field"
    enabled_phases = [phase for phase in ("welcome", "follow", "unfollow") if phases.get(phase) is True]
    if not enabled_phases:
        return "resume_plan_invalid"
    for phase in enabled_phases:
        remaining = quota.get(phase)
        if remaining is None:
            return "unknown_required_field"
        if remaining <= 0:
            return "quota_inconsistency_blocked"
    total = quota.get("total")
    if total is not None:
        if total < 0:
            return "quota_inconsistency_blocked"
        sum_enabled = sum(int(quota.get(phase) or 0) for phase in enabled_phases)
        if sum_enabled > total:
            return "quota_inconsistency_blocked"
    return None


def _validate_resume_plan_schema(meta: dict[str, Any], embedded: dict[str, Any]) -> str | None:
    schema = str(
        embedded.get("schema")
        or meta.get("resume_plan_schema")
        or AUTO_RESTART_RESUME_PLAN_SCHEMA
    ).strip()
    accepted = {
        AUTO_RESTART_RESUME_PLAN_SCHEMA: RESUME_PLAN_VERSION,
        CANONICAL_RESUME_PLAN_SCHEMA_V2: CANONICAL_RESUME_PLAN_VERSION_V2,
    }
    if schema not in accepted:
        return "resume_plan_invalid"
    version = int(meta.get("resume_plan_version") or embedded.get("resume_plan_version") or 0)
    if version != accepted[schema]:
        return "resume_plan_invalid"
    return None


def load_prior_run_summary(account_id: str, prior_run_id: str) -> dict[str, Any] | None:
    import supabase_client

    rows = supabase_client._request_json(
        "GET",
        "ig_runs",
        query={
            "select": "id,account_id,status,performance_summary,finished_at,updated_at",
            "id": f"eq.{prior_run_id}",
            "account_id": f"eq.{account_id}",
            "limit": "1",
        },
    ) or []
    if not rows:
        return None
    row = dict(rows[0])
    performance = _read_record(row.get("performance_summary"))
    account = supabase_client.load_account(account_id=account_id) or {}
    username = str(account.get("username") or "").strip()
    return {
        "account_id": account_id,
        "account_username": username,
        "run_id": prior_run_id,
        "session_status": performance.get("session_status"),
        "session_termination_class": performance.get("session_termination_class"),
        "restart_eligibility": performance.get("restart_eligibility"),
        "restart_block_reason": performance.get("restart_block_reason"),
        "auto_restart_resume_plan": _extract_resume_plan_from_run_row(row),
        "auto_restart_restart_allowed": performance.get("auto_restart_restart_allowed"),
    }


def _validate_human_confirmed_resume_at_claim(
    *,
    account_id: str,
    meta: dict[str, Any],
) -> tuple[bool, str, dict[str, Any] | None]:
    """Validate the durable authorization and its explicit frozen V2 plan."""
    from account_session_resume_plan_store import (
        RESUME_STATE_RESUME_REQUESTED,
        load_resume_plan,
    )

    resume_plan_id = str(meta.get("resume_plan_id") or "").strip()
    original_run_id = str(
        meta.get("original_run_id") or meta.get("prior_run_id") or ""
    ).strip()
    incident_id = str(meta.get("incident_id") or "").strip()
    if not resume_plan_id or not original_run_id or not incident_id:
        return False, "resume_plan_invalid", None

    embedded = _read_record(meta.get("resume_plan"))
    if _validate_resume_plan_schema(meta, embedded):
        return False, "resume_plan_invalid", None
    attempt_fields_present = any(
        record.get(field) not in (None, "")
        for record in (meta, embedded)
        for field in ("attempt_id", "current_attempt_id", "retry_index")
    )
    request_attempt_context = (
        _canonical_request_attempt_context(meta, embedded)
        if attempt_fields_present
        else None
    )
    if attempt_fields_present and request_attempt_context is None:
        return False, "resume_plan_invalid", None
    if str(embedded.get("schema") or "") != CANONICAL_RESUME_PLAN_SCHEMA_V2:
        return False, "resume_plan_invalid", None
    if str(embedded.get("account_id") or "").strip() != str(account_id or "").strip():
        return False, "resume_plan_invalid", None
    if embedded.get("phase_order") != ["welcome", "follow", "unfollow"]:
        return False, "resume_plan_invalid", None
    phases = _read_record(embedded.get("phases_to_run"))
    if any(phases.get(phase) not in (True, False) for phase in ("welcome", "follow", "unfollow")):
        return False, "resume_plan_invalid", None
    quota = _quota_record(embedded.get("quota_remaining"))
    restriction_preflight_only = embedded.get("restriction_preflight_only") is True
    if restriction_preflight_only:
        if (
            str(embedded.get("resume_kind") or "")
            != "instagram_restriction_preflight"
            or str(embedded.get("incident_id") or "").strip() != incident_id
            or any(phases.get(phase) is not False for phase in ("welcome", "follow", "unfollow"))
            or any((quota.get(phase) or 0) != 0 for phase in ("welcome", "follow", "unfollow", "outreach", "total"))
        ):
            return False, "restriction_preflight_contract_invalid", None
    else:
        if embedded.get("package_contract_ready") is not True:
            return False, "resume_plan_invalid", None
        quota_reason = _validate_quota_consistency(phases=phases, quota=quota)
        if quota_reason:
            return False, quota_reason, None

    try:
        plan_row = load_resume_plan(resume_plan_id=resume_plan_id)
    except Exception:
        return False, "resume_plan_invalid", None
    if not plan_row:
        return False, "resume_plan_invalid", None
    if str(plan_row.get("account_id") or "").strip() != str(account_id or "").strip():
        return False, "resume_plan_invalid", None
    if str(plan_row.get("run_id") or "").strip() != original_run_id:
        return False, "resume_plan_invalid", None
    if str(plan_row.get("resume_state") or "").strip() != RESUME_STATE_RESUME_REQUESTED:
        return False, "resume_authorization_consumed", None

    window_end = str(plan_row.get("scheduled_window_end") or "").strip()
    if window_end:
        from datetime import datetime, timezone

        try:
            end_dt = datetime.fromisoformat(window_end.replace("Z", "+00:00"))
            if end_dt <= datetime.now(timezone.utc):
                return False, "resume_authorization_expired", None
        except ValueError:
            return False, "resume_plan_invalid", None

    policy = {
        "schema": CANONICAL_RESUME_PLAN_SCHEMA_V2,
        "resume_plan_version": CANONICAL_RESUME_PLAN_VERSION_V2,
        "prior_run_id": original_run_id,
        "recovery_mode": HUMAN_CONFIRMED_RESUME_MODE,
        "resume_plan_id": resume_plan_id,
        "incident_id": incident_id,
        "restriction_preflight_only": restriction_preflight_only,
        "phases_to_run": {
            phase: bool(phases.get(phase)) for phase in ("welcome", "follow", "unfollow")
        },
        "quota_remaining": {k: v for k, v in quota.items() if v is not None},
        "phase_order": list(embedded.get("phase_order") or []),
        "retry_generation": _as_int(embedded.get("retry_generation")) or 0,
        "frozen_phase_plan": embedded,
        "restart_allowed": True,
        **(request_attempt_context or {}),
        "request_metadata": {
            "source": AUTO_RESTART_TICK_SOURCE,
            "recovery_mode": HUMAN_CONFIRMED_RESUME_MODE,
            "trigger_source": meta.get("trigger_source"),
            "execution_worker_id": meta.get("execution_worker_id"),
            **(request_attempt_context or {}),
        },
    }
    return True, "", policy


def validate_auto_restart_request_at_claim(
    *,
    account_id: str,
    metadata: dict[str, Any] | None,
) -> tuple[bool, str, dict[str, Any] | None]:
    meta = dict(metadata or {})
    if not is_auto_restart_request(meta):
        return True, "", None

    if is_human_confirmed_resume_request(meta):
        return _validate_human_confirmed_resume_at_claim(
            account_id=account_id,
            meta=meta,
        )

    embedded = _read_record(meta.get("resume_plan"))
    failure_category = str(
        meta.get("failure_category") or embedded.get("failure_category") or ""
    ).strip()
    root_failure_code = str(
        meta.get("root_failure_code") or embedded.get("root_failure_code") or ""
    ).strip()
    if (
        failure_category == "systemic_persistence_failure"
        or root_failure_code == "follow_persistence_runtime_unavailable"
    ):
        return False, "same_release_systemic_persistence_failure", None

    prior_run_id = str(meta.get("prior_run_id") or "").strip()
    if not prior_run_id:
        return False, "resume_plan_invalid", None

    schema_reason = _validate_resume_plan_schema(meta, embedded)
    if schema_reason:
        return False, schema_reason, None

    request_attempt_context = _canonical_request_attempt_context(meta, embedded)
    if request_attempt_context is None:
        return False, "resume_plan_invalid", None
    attempt_id = request_attempt_context["attempt_id"]
    retry_index = request_attempt_context["retry_index"]
    if meta.get("source_lineage_valid") is False:
        return False, "resume_source_lineage_invalid", None

    recoverable_python_retry = (
        str(meta.get("failure_category") or embedded.get("failure_category") or "")
        == "recoverable_python_runtime_failure"
    )
    retry_context: dict[str, Any] = dict(request_attempt_context)
    if recoverable_python_retry:
        business_session_id = str(
            meta.get("root_business_session_id")
            or meta.get("business_session_id")
            or ""
        ).strip()
        embedded_business_session_id = str(
            embedded.get("root_business_session_id")
            or embedded.get("business_session_id")
            or ""
        ).strip()
        previous_run_id = str(
            meta.get("previous_run_id") or meta.get("prior_run_id") or ""
        ).strip()
        if (
            not business_session_id
            or not embedded_business_session_id
            or embedded_business_session_id != business_session_id
            or previous_run_id != prior_run_id
            or retry_index not in {1, 2}
            or attempt_id != retry_index + 1
            or embedded.get("cleanup_completed") is not True
            or embedded.get("lock_released") is not True
            or str(meta.get("root_failure_code") or embedded.get("root_failure_code") or "")
            != "unfollow_runtime_exception"
            or str(meta.get("failure_signature") or embedded.get("failure_signature") or "")
            != "python:unfollow:duplicate_stop_reason"
        ):
            return False, "resume_plan_invalid", None
        retry_context = {
            "business_session_id": business_session_id,
            "root_business_session_id": business_session_id,
            "attempt_id": attempt_id,
            "retry_index": retry_index,
            "previous_run_id": previous_run_id,
            "root_failure_code": "unfollow_runtime_exception",
            "failure_signature": "python:unfollow:duplicate_stop_reason",
            "failure_category": "recoverable_python_runtime_failure",
            "scheduled_at": meta.get("scheduled_at"),
            "business_day_sast": meta.get("business_day_sast"),
        }

    summary = load_prior_run_summary(account_id, prior_run_id)
    if not summary:
        return False, "resume_plan_invalid", None

    # The scheduler embeds the phase/quota decision in the new request, while
    # the prior run remains the canonical source for its UI checkpoint.  Merge
    # both records instead of letting a smaller embedded payload erase the
    # persisted cursor.  Request-time fields win, but an omitted checkpoint is
    # inherited from the exact prior run validated above.
    canonical_plan = _read_record(summary.get("auto_restart_resume_plan"))
    embedded_plan = (
        _extract_resume_plan_from_run_row(
            {"performance_summary": {"auto_restart_resume_plan": embedded}}
        )
        if embedded
        else {}
    )
    stored_plan = {**canonical_plan, **embedded_plan}
    if not embedded_plan.get("unfollow_checkpoint") and canonical_plan.get(
        "unfollow_checkpoint"
    ):
        stored_plan["unfollow_checkpoint"] = canonical_plan["unfollow_checkpoint"]

    manual = build_manual_resume_command(summary, resume_plan=stored_plan)
    if not manual.get("manual_resume_allowed"):
        reason = normalize_hard_stop_reason(
            str(manual.get("manual_resume_block_reason") or "resume_plan_invalid")
        )
        return False, reason, None

    unknowns = manual.get("unknown_required_fields") or []
    if unknowns:
        return False, "unknown_required_field", None

    phases = dict(manual.get("phases_to_resume") or {})
    if any(phases.get(phase) not in (True, False) for phase in ("welcome", "follow", "unfollow")):
        return False, "resume_plan_invalid", None
    if not any(phases.get(phase) is True for phase in ("welcome", "follow", "unfollow")):
        return False, "resume_plan_invalid", None

    quota = _quota_record(embedded.get("quota_remaining") or stored_plan.get("quota_remaining"))
    quota_reason = _validate_quota_consistency(phases=phases, quota=quota)
    if quota_reason:
        return False, quota_reason, None

    follow60_contract = _read_record(embedded.get("follow_60s_canary_contract"))
    follow60_armed_control = bool(follow60_contract)
    policy = {
        "schema": AUTO_RESTART_RESUME_PLAN_SCHEMA,
        "resume_plan_version": RESUME_PLAN_VERSION,
        "prior_run_id": prior_run_id,
        "phases_to_run": {phase: bool(phases.get(phase)) for phase in ("welcome", "follow", "unfollow")},
        "quota_remaining": {k: v for k, v in quota.items() if v is not None},
        "restart_allowed": True,
        "unfollow_checkpoint": (
            dict(embedded.get("unfollow_checkpoint") or stored_plan.get("unfollow_checkpoint") or {})
            if isinstance(
                embedded.get("unfollow_checkpoint") or stored_plan.get("unfollow_checkpoint"),
                dict,
            )
            else {}
        ),
        **retry_context,
        "request_metadata": {
            "source": AUTO_RESTART_TICK_SOURCE,
            **(
                {"recovery_mode": FOLLOW60_ARMED_CONTROL_RESUME_MODE}
                if follow60_armed_control
                else {}
            ),
            "session_termination_class": meta.get("session_termination_class"),
            "trigger_source": meta.get("trigger_source"),
            "execution_worker_id": meta.get("execution_worker_id"),
            **retry_context,
        },
    }
    if follow60_armed_control:
        # The scheduler has already projected a validated armed control onto an
        # exact Follow-only request. Preserve that immutable request contract
        # for the Worker binding check instead of treating it as a legacy
        # human-incident resume (which requires an unrelated resume-plan row).
        policy["recovery_mode"] = FOLLOW60_ARMED_CONTROL_RESUME_MODE
        policy["frozen_phase_plan"] = embedded
    return True, "", policy


def runner_env_for_resume_policy(policy: dict[str, Any] | None) -> dict[str, str]:
    if not policy:
        return {}
    return {AUTO_RESTART_RESUME_PLAN_ENV: json.dumps(policy, separators=(",", ":"), sort_keys=True)}


def load_resume_policy_from_env() -> dict[str, Any] | None:
    raw = str(os.environ.get(AUTO_RESTART_RESUME_PLAN_ENV) or "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        log("warning", "auto_restart_resume_policy_env_invalid")
        return None
    return parsed if isinstance(parsed, dict) else None


def phase_enabled(phase: str, *, default: bool, policy: dict[str, Any] | None) -> bool:
    if not policy:
        return default
    phases = policy.get("phases_to_run") or {}
    value = phases.get(phase)
    if value is True:
        return True
    if value is False:
        return False
    return default


def log_auto_restart_claim_validation(
    *,
    account_id: str,
    request_id: str,
    ok: bool,
    reason: str,
) -> None:
    log(
        "info" if ok else "warning",
        "auto_restart_candidate_evaluated" if ok else "auto_restart_runtime_rejected",
        account_id=account_id,
        request_id=request_id,
        restart_allowed=ok,
        reason=reason or None,
    )
