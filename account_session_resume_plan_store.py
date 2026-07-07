"""P3 canonical per-run resume plan store (account_session_resume_plans).

Contract:
  - the runner creates the plan EARLY, before any real UI action, as soon as
    the run safely knows account / assignment / device / clone / package /
    session window;
  - the dispatcher updates the plan on terminal failure, preserving the true
    terminal reason and marking whether a human-confirmed resume is possible;
  - the orchestrator updates the plan at end of session with the restart
    verdict (V1A builder output);
  - the atomic human-resume authorization is NOT stored here (it lives in
    ``incident_resume_authorizations``, consumed atomically by the backend
    Auto Restart tick).

Every write here is best-effort: failures are logged with a stable reason and
never break the run. Reads used for claim validation raise on transport errors
so the dispatcher can refuse an unverifiable resume.
"""

from __future__ import annotations

from typing import Any

from logs import log

# Incident types eligible for the human-confirmed resume flow (P3).
RECOVERY_ELIGIBLE_INCIDENT_TYPES = frozenset(
    {
        "run_identity_verification_failed",
        "active_instagram_account_mismatch",
        "account_login_required",
        "assigned_instagram_package_unavailable",
        "run_device_unavailable",
        "run_worker_failure",
    }
)

RESUME_STATE_RUN_ACTIVE = "run_active"
RESUME_STATE_AWAITING_HUMAN = "awaiting_human_resume_authorization"
RESUME_STATE_RESUME_REQUESTED = "resume_requested"
RESUME_STATE_RESUME_SUCCEEDED = "resume_succeeded"
RESUME_STATE_NOT_RECOVERABLE = "not_recoverable"
RESUME_STATE_COMPLETED = "completed"

_TABLE = "account_session_resume_plans"


def build_resume_window_key(
    *,
    assignment_id: str | None,
    account_id: str | None,
    scheduled_window_start: str | None,
) -> str | None:
    """Stable key identifying one account's active session window."""
    anchor = str(assignment_id or "").strip() or str(account_id or "").strip()
    start = str(scheduled_window_start or "").strip()
    if not anchor or not start:
        return None
    return f"{anchor}:{start}"


def _clean(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def create_early_resume_plan(
    *,
    run_id: str,
    run_request_id: str | None,
    account_id: str,
    assignment_id: str | None,
    device_id: str | None,
    app_instance_id: str | None,
    expected_username: str | None,
    expected_package: str | None,
    scheduled_window_start: str | None,
    scheduled_window_end: str | None,
    source_surface: str | None = None,
    run_trigger: str | None = None,
    attempts_in_window: int = 0,
    test: bool = False,
) -> dict[str, Any]:
    """Create (or refresh) the canonical resume plan row for one run.

    Called by the runner before any real UI action. Idempotent per run_id.
    Best-effort: returns ``{"persisted": False, "reason": ...}`` on failure.
    """
    rid = _clean(run_id)
    aid = _clean(account_id)
    if not rid or not aid:
        return {"persisted": False, "reason": "missing_run_or_account"}
    row = {
        "run_id": rid,
        "run_request_id": _clean(run_request_id),
        "account_id": aid,
        "assignment_id": _clean(assignment_id),
        "device_id": _clean(device_id),
        "app_instance_id": _clean(app_instance_id),
        "expected_username": _clean(expected_username),
        "expected_package": _clean(expected_package),
        "scheduled_window_start": _clean(scheduled_window_start),
        "scheduled_window_end": _clean(scheduled_window_end),
        "resume_window_key": build_resume_window_key(
            assignment_id=assignment_id,
            account_id=account_id,
            scheduled_window_start=scheduled_window_start,
        ),
        "source_surface": _clean(source_surface),
        "run_trigger": _clean(run_trigger),
        "resume_stage": "preflight",
        "resume_state": RESUME_STATE_RUN_ACTIVE,
        "restart_allowed": False,
        "restart_block_reason": "run_in_progress",
        "attempts_in_window": max(0, int(attempts_in_window or 0)),
        "test": bool(test),
        "last_updated_at": _utc_now_iso(),
    }
    try:
        import supabase_client

        rows = supabase_client._request_json(
            "POST",
            _TABLE,
            query={"on_conflict": "run_id"},
            body=row,
            prefer_representation=True,
            prefer_resolution="resolution=merge-duplicates",
        )
        persisted = dict(rows[0]) if isinstance(rows, list) and rows else {}
        log(
            "info",
            "resume_plan_created_early",
            account_id=aid,
            run_id=rid,
            run_request_id=row["run_request_id"],
            assignment_id=row["assignment_id"],
            resume_window_key=row["resume_window_key"],
            resume_plan_id=persisted.get("id"),
        )
        return {"persisted": True, "reason": "created", "row": persisted}
    except Exception as exc:
        log(
            "warning",
            "resume_plan_create_failed",
            account_id=aid,
            run_id=rid,
            error=str(exc)[:300],
        )
        return {"persisted": False, "reason": "write_failed", "error": str(exc)[:300]}


def _patch_plan_by_run_id(run_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    rid = _clean(run_id)
    if not rid:
        return {"persisted": False, "reason": "missing_run_id"}
    body = dict(patch)
    body["last_updated_at"] = _utc_now_iso()
    try:
        import supabase_client

        rows = supabase_client._request_json(
            "PATCH",
            _TABLE,
            query={"run_id": f"eq.{rid}"},
            body=body,
            prefer_representation=True,
        )
        if isinstance(rows, list) and rows:
            return {"persisted": True, "reason": "updated", "row": dict(rows[0])}
        return {"persisted": False, "reason": "plan_row_not_found"}
    except Exception as exc:
        log(
            "warning",
            "resume_plan_update_failed",
            run_id=rid,
            error=str(exc)[:300],
        )
        return {"persisted": False, "reason": "write_failed", "error": str(exc)[:300]}


def record_terminal_failure(
    *,
    run_id: str,
    incident_type: str,
    reason_code: str,
    incident_id: str | None = None,
) -> dict[str, Any]:
    """Dispatcher hook: preserve the true terminal reason on the plan.

    Recovery-eligible incident types leave the plan waiting for an explicit
    human authorization (``awaiting_human_resume_authorization``); everything
    else is marked not recoverable. Never allows an automatic restart.
    """
    itype = str(incident_type or "").strip()
    recoverable = itype in RECOVERY_ELIGIBLE_INCIDENT_TYPES
    patch: dict[str, Any] = {
        "resume_state": (
            RESUME_STATE_AWAITING_HUMAN if recoverable else RESUME_STATE_NOT_RECOVERABLE
        ),
        "restart_allowed": False,
        "restart_block_reason": (
            "awaiting_human_resume_authorization"
            if recoverable
            else "resume_plan_not_recoverable"
        ),
        "terminal_reason_code": _clean(reason_code),
    }
    if incident_id:
        patch["incident_id"] = _clean(incident_id)
    result = _patch_plan_by_run_id(run_id, patch)
    log(
        "info",
        "resume_plan_terminal_recorded",
        run_id=run_id,
        incident_type=itype or None,
        reason_code=_clean(reason_code),
        resume_state=patch["resume_state"],
        persisted=result.get("persisted"),
        persist_reason=result.get("reason"),
    )
    return result


def record_end_of_session(
    *,
    run_id: str,
    session_plan: dict[str, Any] | None,
    session_status: str | None,
) -> dict[str, Any]:
    """Orchestrator hook: persist the V1A restart verdict at end of session."""
    plan = dict(session_plan or {})
    completed = str(session_status or "").strip().lower() == "success"
    patch: dict[str, Any] = {
        "resume_stage": "completed" if completed else "phases",
        "resume_state": RESUME_STATE_COMPLETED if completed else RESUME_STATE_RUN_ACTIVE,
        "restart_allowed": bool(plan.get("restart_allowed")),
        "restart_block_reason": str(
            plan.get("restart_block_reason")
            or ("session_completed" if completed else "")
        ),
        "plan": plan,
    }
    return _patch_plan_by_run_id(run_id, patch)


def mark_resume_outcome(
    *,
    original_run_id: str,
    succeeded: bool,
    reason_code: str | None = None,
) -> dict[str, Any]:
    """Dispatcher hook after a human-confirmed resume run finished."""
    patch: dict[str, Any] = {
        "resume_state": (
            RESUME_STATE_RESUME_SUCCEEDED if succeeded else RESUME_STATE_AWAITING_HUMAN
        ),
        "restart_allowed": False,
        "restart_block_reason": (
            "resume_succeeded" if succeeded else "reintervention_required"
        ),
    }
    if reason_code:
        patch["terminal_reason_code"] = _clean(reason_code)
    return _patch_plan_by_run_id(original_run_id, patch)


def load_resume_plan(
    *,
    resume_plan_id: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any] | None:
    """Load one resume plan row. Transport errors propagate to the caller."""
    import supabase_client

    query: dict[str, str] = {"select": "*", "limit": "1"}
    if resume_plan_id:
        query["id"] = f"eq.{str(resume_plan_id).strip()}"
    elif run_id:
        query["run_id"] = f"eq.{str(run_id).strip()}"
    else:
        return None
    rows = supabase_client._request_json("GET", _TABLE, query=query) or []
    return dict(rows[0]) if rows else None


def _utc_now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
