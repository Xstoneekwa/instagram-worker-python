"""Fail-closed reconciliation for active runs orphaned by worker loss."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import supabase_client
from account_run_control import (
    complete_account_run_request,
    insert_manual_run_audit,
    normalize_request_uuid,
    reconcile_linked_ig_run_terminal,
)
from logs import log


ACTIVE_RUN_STATUSES = {"running", "queued", "pending", "in_progress", "active", "starting"}
LIVE_WORKER_STATUSES = {"starting", "idle", "running"}


def _timestamp(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _latest_timestamp(*values: Any) -> datetime | None:
    parsed = [item for item in (_timestamp(value) for value in values) if item is not None]
    return max(parsed) if parsed else None


def _is_fresh(value: Any, *, now: datetime, max_age_seconds: float) -> bool:
    observed = _timestamp(value)
    return observed is not None and (now - observed).total_seconds() <= max_age_seconds


def reconcile_orphaned_active_runs(
    cfg: Any,
    *,
    limit: int = 50,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Terminalize only runs older than the maximum child lifetime with no live owner.

    Lease expiry is necessary but never sufficient. A candidate is reclaimed
    only after the configured subprocess timeout plus a grace period, with no
    fresh matching worker heartbeat and no live device lock. All writes are
    linked by exact request/run/account IDs and remain idempotent on restart.
    """

    observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    heartbeat_grace = max(float(getattr(cfg, "heartbeat_seconds", 20.0)) * 3.0, 60.0)
    stale_after = max(float(getattr(cfg, "subprocess_timeout_seconds", 7200)), 60.0) + max(
        heartbeat_grace, 300.0
    )

    try:
        requests = supabase_client._request_json(
            "GET",
            "account_run_requests",
            query={
                "select": (
                    "id,account_id,status,run_id,requested_run_type,started_at,"
                    "lease_expires_at,cancel_requested_at"
                ),
                "status": "eq.running",
                "run_id": "not.is.null",
                "order": "started_at.asc",
                "limit": str(max(1, int(limit))),
            },
        ) or []
        run_ids = [
            str(row.get("run_id") or "").strip()
            for row in requests
            if isinstance(row, dict) and str(row.get("run_id") or "").strip()
        ]
        if not run_ids:
            return {"ok": True, "observed": 0, "reconciled": 0, "protected": 0}
        in_filter = f"in.({','.join(run_ids)})"
        runs = supabase_client._request_json(
            "GET",
            "ig_runs",
            query={
                "select": "id,account_id,status,started_at,updated_at",
                "id": in_filter,
            },
        ) or []
        heartbeats = supabase_client._request_json(
            "GET",
            "worker_heartbeats",
            query={
                "select": "current_run_id,status,last_seen_at",
                "current_run_id": in_filter,
            },
        ) or []
        locks = supabase_client._request_json(
            "GET",
            "auto_restart_device_locks",
            query={
                "select": "run_id,request_id,lease_expires_at,heartbeat_at,release_reason",
                "run_id": in_filter,
            },
        ) or []
    except Exception as exc:
        log(
            "warning",
            "orphan_run_reconciliation_read_failed",
            worker_id=str(getattr(cfg, "worker_id", "")),
            error=str(exc)[:200],
        )
        return {"ok": False, "observed": 0, "reconciled": 0, "protected": 0}

    runs_by_id = {
        str(row.get("id") or "").strip(): row
        for row in runs
        if isinstance(row, dict)
    }
    live_heartbeat_runs = {
        str(row.get("current_run_id") or "").strip()
        for row in heartbeats
        if isinstance(row, dict)
        and str(row.get("status") or "").strip().lower() in LIVE_WORKER_STATUSES
        and _is_fresh(row.get("last_seen_at"), now=observed_at, max_age_seconds=heartbeat_grace)
    }
    live_lock_runs = {
        str(row.get("run_id") or "").strip()
        for row in locks
        if isinstance(row, dict)
        and not str(row.get("release_reason") or "").strip()
        and (
            (_timestamp(row.get("lease_expires_at")) or datetime.min.replace(tzinfo=timezone.utc))
            > observed_at
            or _is_fresh(row.get("heartbeat_at"), now=observed_at, max_age_seconds=heartbeat_grace)
        )
    }

    reconciled = 0
    protected = 0
    for request in requests:
        if not isinstance(request, dict):
            continue
        request_id = normalize_request_uuid(request.get("id"))
        account_id = normalize_request_uuid(request.get("account_id"))
        run_id = normalize_request_uuid(request.get("run_id"))
        run = runs_by_id.get(str(run_id or ""))
        if not request_id or not account_id or not run_id or not isinstance(run, dict):
            protected += 1
            continue
        if normalize_request_uuid(run.get("account_id")) != account_id:
            protected += 1
            continue
        if str(run.get("status") or "").strip().lower() not in ACTIVE_RUN_STATUSES:
            continue

        lease_expires_at = _timestamp(request.get("lease_expires_at"))
        last_progress_at = _latest_timestamp(
            run.get("updated_at"), run.get("started_at"), request.get("started_at")
        )
        stale = (
            lease_expires_at is not None
            and lease_expires_at <= observed_at
            and last_progress_at is not None
            and (observed_at - last_progress_at).total_seconds() > stale_after
        )
        if not stale or run_id in live_heartbeat_runs or run_id in live_lock_runs:
            protected += 1
            continue

        canceled = bool(_timestamp(request.get("cancel_requested_at")))
        run_terminal_status = "canceled" if canceled else "failed"
        request_terminal_status = "canceled" if canceled else "failed"
        error_code = None if canceled else "orphaned_worker_process"
        error_message = None if canceled else "Worker process disappeared before terminal state persisted."
        try:
            run_result = reconcile_linked_ig_run_terminal(
                run_id=run_id,
                terminal_status=run_terminal_status,
                account_id=account_id,
                reason="orphaned_worker_process",
            )
            if not bool(run_result.get("reconciled")):
                protected += 1
                continue
            request_result = complete_account_run_request(
                request_id,
                str(getattr(cfg, "worker_id", "")),
                request_terminal_status,
                error_code=error_code,
                error_message_safe=error_message,
            )
            if not request_result:
                protected += 1
                continue
            supabase_client._request_json(
                "PATCH",
                "account_session_resume_plans",
                query={"run_id": f"eq.{run_id}"},
                body={
                    "resume_stage": "completed",
                    "resume_state": "completed",
                    "restart_allowed": False,
                    "restart_block_reason": "orphaned_worker_process",
                    "terminal_reason_code": "operator_canceled" if canceled else "orphaned_worker_process",
                    "last_updated_at": observed_at.isoformat(),
                },
                prefer_representation=False,
            )
            insert_manual_run_audit(
                account_id=account_id,
                action_type="orphaned_active_run_reconciled",
                status="success",
                message="Stale active run reconciled after worker ownership disappeared.",
                run_id=run_id,
                payload={
                    "request_id": request_id,
                    "request_status": request_terminal_status,
                    "run_status": "stopped" if canceled else "failed",
                    "source": "dispatcher_startup_reconciliation",
                },
            )
        except Exception as exc:
            log(
                "warning",
                "orphan_run_reconciliation_write_failed",
                worker_id=str(getattr(cfg, "worker_id", "")),
                request_id=request_id,
                run_id=run_id,
                error=str(exc)[:200],
            )
            continue
        reconciled += 1
        log(
            "info",
            "orphaned_active_run_reconciled",
            worker_id=str(getattr(cfg, "worker_id", "")),
            request_id=request_id,
            run_id=run_id,
            request_status=request_terminal_status,
            persisted=True,
        )

    return {
        "ok": True,
        "observed": len(requests),
        "reconciled": reconciled,
        "protected": protected,
    }
