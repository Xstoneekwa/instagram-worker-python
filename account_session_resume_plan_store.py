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

Early/progress writes remain best-effort.  The end-of-session verdict is an
authoritative control-plane boundary: it is written once through an idempotent
RPC and confirmed by a canonical reread after both success and ambiguous
transport failure.  An unconfirmed terminal verdict is surfaced as
``resume_plan_reconciliation_required`` and must fail closed.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from account_session_resume_state_contract import (
    CONTRACT_VERSION,
    RESUME_STATE_RESUME_REQUESTED,  # Public compatibility re-export, not a second definition.
    resolve_end_of_session_transition,
)
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
        "instagram_account_restriction",
    }
)

RESUME_STATE_RUN_ACTIVE = "run_active"
RESUME_STATE_AWAITING_HUMAN = "awaiting_human_resume_authorization"
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


def _business_day_sast(value: str | None) -> str:
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo

    raw = _clean(value)
    if raw:
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            parsed = datetime.now(timezone.utc)
    else:
        parsed = datetime.now(timezone.utc)
    return parsed.astimezone(ZoneInfo("Africa/Johannesburg")).date().isoformat()


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
    business_session_id: str | None = None,
    attempt_id: int = 1,
    retry_index: int = 0,
    previous_run_id: str | None = None,
    scheduled_at: str | None = None,
    business_day_sast: str | None = None,
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
    safe_attempt_id = max(1, int(attempt_id or 1))
    safe_retry_index = max(0, int(retry_index or 0))
    attempt_plan = {
        "business_session_id": _clean(business_session_id) or rid,
        "attempt_id": safe_attempt_id,
        "current_attempt_id": safe_attempt_id,
        "retry_index": safe_retry_index,
        "previous_run_id": _clean(previous_run_id),
        "scheduled_at": _clean(scheduled_at),
        "claimed_at": _utc_now_iso(),
        "completed_at": None,
        "result": "running",
        "business_day_sast": _clean(business_day_sast)
        or _business_day_sast(scheduled_window_start),
        "auto_restart_max_retries_after_initial_failure": 2,
        "total_attempts_allowed": 3,
    }
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
        "attempts_in_window": max(
            safe_retry_index,
            max(0, int(attempts_in_window or 0)),
        ),
        "plan": attempt_plan,
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
    """Persist and confirm the terminal lifecycle transition exactly once."""
    plan = dict(session_plan or {})
    try:
        existing = load_resume_plan(run_id=run_id) or {}
        existing_plan = existing.get("plan")
        if isinstance(existing_plan, dict):
            plan = {**existing_plan, **plan}
    except Exception as exc:
        return {
            "persisted": False,
            "reason": "resume_plan_reconciliation_required",
            "error": str(exc)[:300],
        }

    request_id = _clean(existing.get("run_request_id"))
    existing_plan = existing.get("plan") if isinstance(existing.get("plan"), dict) else {}
    root_business_session_id = _clean(existing_plan.get("root_business_session_id"))
    execution_attempt_no = int(
        existing_plan.get("execution_attempt_no")
        or existing_plan.get("attempt_id")
        or plan.get("execution_attempt_no")
        or plan.get("attempt_id")
        or 0
    )
    if not request_id or not root_business_session_id or execution_attempt_no < 1:
        return {
            "persisted": False,
            "reason": "resume_plan_reconciliation_required",
            "error": "resume_plan_lineage_incomplete",
        }

    # The atomic admission capsule owns lineage.  A later business summary may
    # carry legacy run-scoped aliases, but it cannot rewrite the request root.
    plan["root_business_session_id"] = root_business_session_id
    plan["execution_attempt_no"] = execution_attempt_no
    plan["attempt_id"] = execution_attempt_no
    plan["retry_index"] = execution_attempt_no - 1
    # Derived idempotency metadata from an earlier confirmed invocation must
    # never feed the next digest; replaying the same terminalization must keep
    # the same key and content hash.
    plan.pop("resume_contract_key", None)
    plan.pop("terminal_plan_digest", None)
    plan.pop("resume_state_contract_version", None)
    transition = resolve_end_of_session_transition(
        session_plan=plan,
        session_status=session_status,
    )
    plan.update(
        {
            "resume_state_contract_version": CONTRACT_VERSION,
            "business_outcome": transition.outcome,
            "auto_restart_decision": transition.auto_restart_decision,
        }
    )
    digest = hashlib.sha256(
        json.dumps(plan, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
    contract_key = ":".join(
        (str(run_id), request_id, root_business_session_id, str(execution_attempt_no))
    )
    plan["resume_contract_key"] = contract_key
    plan["terminal_plan_digest"] = digest

    def _confirmed(row: dict[str, Any] | None) -> bool:
        if not isinstance(row, dict):
            return False
        stored_plan = row.get("plan")
        return (
            str(row.get("run_id") or "") == str(run_id)
            and str(row.get("run_request_id") or "") == request_id
            and str(row.get("resume_state") or "") == transition.resume_state
            and row.get("restart_allowed") is transition.restart_allowed
            and isinstance(stored_plan, dict)
            and stored_plan.get("resume_contract_key") == contract_key
            and stored_plan.get("terminal_plan_digest") == digest
        )

    rpc_error: str | None = None
    try:
        import supabase_client

        result = supabase_client.persist_account_session_resume_plan_v1(
            run_id=str(run_id),
            request_id=request_id,
            root_business_session_id=root_business_session_id,
            execution_attempt_no=execution_attempt_no,
            resume_stage=transition.resume_stage,
            resume_state=transition.resume_state,
            restart_allowed=transition.restart_allowed,
            restart_block_reason=transition.restart_block_reason,
            terminal_reason_code=_clean(plan.get("terminal_reason_code")),
            plan=plan,
            terminal_plan_digest=digest,
        )
        if not bool(result.get("ok")):
            rpc_error = str(result.get("reason") or "resume_plan_rpc_rejected")[:300]
    except Exception as exc:
        # The mutation is never replayed.  A lost response is resolved only by
        # reading the exact lineage key and terminal digest.
        rpc_error = str(exc)[:300]

    try:
        persisted = load_resume_plan(run_id=run_id)
    except Exception as exc:
        persisted = None
        rpc_error = rpc_error or str(exc)[:300]
    if _confirmed(persisted):
        return {
            "persisted": True,
            "reason": "confirmed_after_write" if not rpc_error else "confirmed_after_ambiguous_response",
            "row": persisted,
            "transition": transition,
        }

    log(
        "error",
        "resume_plan_reconciliation_required",
        run_id=run_id,
        run_request_id=request_id,
        root_business_session_id=root_business_session_id,
        execution_attempt_no=execution_attempt_no,
        intended_resume_state=transition.resume_state,
        error=rpc_error,
    )
    return {
        "persisted": False,
        "reason": "resume_plan_reconciliation_required",
        "error": rpc_error or "terminal_plan_readback_mismatch",
        "transition": transition,
    }


def record_automatic_retry_terminal_state(
    *,
    run_id: str,
    retry_decision: Any,
    cleanup_completed: bool,
    lock_released: bool,
    quota_remaining: dict[str, Any] | None,
    phases_to_run: dict[str, Any] | None,
    scheduled_at: str | None = None,
    claimed_at: str | None = None,
) -> dict[str, Any]:
    """Persist the terminal state of one allowlisted Python attempt.

    The existing JSON plan is merged so early claim evidence and the final
    quota/phase decision remain in one canonical row without a schema change.
    """
    try:
        existing = load_resume_plan(run_id=run_id) or {}
    except Exception:
        existing = {}
    existing_plan = existing.get("plan")
    plan = dict(existing_plan) if isinstance(existing_plan, dict) else {}
    plan.update(
        {
            "business_session_id": retry_decision.business_session_id,
            "root_business_session_id": retry_decision.business_session_id,
            "attempt_id": retry_decision.attempt_id,
            "current_attempt_id": retry_decision.attempt_id,
            "retry_index": retry_decision.retry_index,
            "next_attempt_id": retry_decision.next_attempt_id,
            "next_retry_index": retry_decision.next_retry_index,
            "previous_run_id": retry_decision.previous_run_id or None,
            "root_failure_code": retry_decision.root_failure_code,
            "failure_signature": retry_decision.failure_signature,
            "failure_category": retry_decision.failure_category,
            "cleanup_completed": bool(cleanup_completed),
            "lock_released": bool(lock_released),
            "quota_remaining": dict(quota_remaining or {}),
            "phases_to_run": dict(phases_to_run or {}),
            "scheduled_at": _clean(scheduled_at) or plan.get("scheduled_at"),
            "claimed_at": _clean(claimed_at) or plan.get("claimed_at"),
            "completed_at": _utc_now_iso(),
            "result": "failed",
            "restart_allowed": bool(retry_decision.restart_allowed),
            "restart_block_reason": retry_decision.block_reason,
            "terminal_event_type": retry_decision.event_type,
            "auto_restart_max_retries_after_initial_failure": 2,
            "total_attempts_allowed": 3,
        }
    )
    plan["session_termination_class"] = (
        "partial_resumable"
        if retry_decision.restart_allowed
        else str(plan.get("session_termination_class") or "not_recoverable")
    )
    plan["unsafe_markers"] = list(plan.get("unsafe_markers") or [])
    return record_end_of_session(
        run_id=run_id,
        session_plan=plan,
        session_status="failed",
    )


def mark_automatic_retry_success(
    *,
    run_id: str,
    request_metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    metadata = dict(request_metadata or {})
    try:
        existing = load_resume_plan(run_id=run_id) or {}
    except Exception:
        existing = {}
    existing_plan = existing.get("plan")
    plan = dict(existing_plan) if isinstance(existing_plan, dict) else {}
    plan.update(
        {
            "business_session_id": metadata.get("business_session_id")
            or plan.get("business_session_id"),
            "attempt_id": metadata.get("attempt_id") or plan.get("attempt_id"),
            "retry_index": metadata.get("retry_index") or plan.get("retry_index"),
            "previous_run_id": metadata.get("previous_run_id")
            or metadata.get("prior_run_id")
            or plan.get("previous_run_id"),
            "completed_at": _utc_now_iso(),
            "result": "succeeded",
            "restart_allowed": False,
            "restart_block_reason": "resume_succeeded",
        }
    )
    return _patch_plan_by_run_id(
        run_id,
        {
            "resume_stage": "completed",
            "resume_state": RESUME_STATE_RESUME_SUCCEEDED,
            "restart_allowed": False,
            "restart_block_reason": "resume_succeeded",
            "plan": plan,
        },
    )


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
