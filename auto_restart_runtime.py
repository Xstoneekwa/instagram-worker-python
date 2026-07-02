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
    }
)

_REASON_ALIASES = {
    "challenge": "challenge_blocked",
    "restriction": "restriction_blocked",
    "account_mismatch": "account_mismatch_blocked",
    "action_block": "restriction_blocked",
    "resume_runtime_not_supported": "resume_plan_invalid",
}


def normalize_hard_stop_reason(reason: str) -> str:
    normalized = str(reason or "").strip()
    return _REASON_ALIASES.get(normalized, normalized)


def is_hard_stop_reason(reason: str) -> bool:
    return normalize_hard_stop_reason(reason) in HARD_STOP_REASONS


def is_auto_restart_request(metadata: dict[str, Any] | None) -> bool:
    meta = dict(metadata or {})
    return bool(meta.get("auto_restart")) and str(meta.get("source") or "") == AUTO_RESTART_TICK_SOURCE


def _read_record(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _as_int(value: Any) -> int | None:
    if value is None or value == UNKNOWN:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


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
        if remaining < 0:
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
    if schema != AUTO_RESTART_RESUME_PLAN_SCHEMA:
        return "resume_plan_invalid"
    version = int(meta.get("resume_plan_version") or embedded.get("resume_plan_version") or 0)
    if version != RESUME_PLAN_VERSION:
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


def validate_auto_restart_request_at_claim(
    *,
    account_id: str,
    metadata: dict[str, Any] | None,
) -> tuple[bool, str, dict[str, Any] | None]:
    meta = dict(metadata or {})
    if not is_auto_restart_request(meta):
        return True, "", None

    prior_run_id = str(meta.get("prior_run_id") or "").strip()
    if not prior_run_id:
        return False, "resume_plan_invalid", None

    embedded = _read_record(meta.get("resume_plan"))
    schema_reason = _validate_resume_plan_schema(meta, embedded)
    if schema_reason:
        return False, schema_reason, None

    summary = load_prior_run_summary(account_id, prior_run_id)
    if not summary:
        return False, "resume_plan_invalid", None

    stored_plan = (
        _extract_resume_plan_from_run_row({"performance_summary": {"auto_restart_resume_plan": embedded}})
        if embedded
        else _extract_resume_plan_from_run_row({"performance_summary": summary})
    )
    if not stored_plan:
        stored_plan = _read_record(summary.get("auto_restart_resume_plan"))

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

    policy = {
        "schema": AUTO_RESTART_RESUME_PLAN_SCHEMA,
        "resume_plan_version": RESUME_PLAN_VERSION,
        "prior_run_id": prior_run_id,
        "phases_to_run": {phase: bool(phases.get(phase)) for phase in ("welcome", "follow", "unfollow")},
        "quota_remaining": {k: v for k, v in quota.items() if v is not None},
        "restart_allowed": True,
        "request_metadata": {
            "source": AUTO_RESTART_TICK_SOURCE,
            "session_termination_class": meta.get("session_termination_class"),
            "trigger_source": meta.get("trigger_source"),
            "execution_worker_id": meta.get("execution_worker_id"),
        },
    }
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
