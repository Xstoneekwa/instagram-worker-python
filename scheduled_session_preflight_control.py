"""CP4 scheduled session preflight queue reconciliation and operator signals."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from account_run_control import (
    cancel_account_run_request,
    complete_account_run_request,
    get_account_run_request,
    normalize_request_uuid,
)
from auto_restart_device_lock import release_device_lock_for_request
from logs import log
import supabase_client

PREFLIGHT_RUN_TYPE = "scheduled_session_preflight"
PREFLIGHT_DASHBOARD_ACTION_TYPE = "scheduled_session_preflight"
DISPATCHER_UNAVAILABLE_REASON = "scheduled_session_preflight_dispatcher_unavailable"
STALE_QUEUED_SECONDS = 15 * 60
TERMINAL_PREFLIGHT_DASHBOARD_STATUSES = {
    "preflight_ready",
    "preflight_expired",
    "preflight_invalidated",
    "preflight_blocked",
}


IDENTITY_PREFLIGHT_BLOCKED_REASONS = {
    "own_profile_open_failed",
    "active_instagram_account_mismatch",
    "identity_mismatch",
    "possible_username_rename_detected",
    "login_challenge",
    "checkpoint",
}
ACTIVE_DASHBOARD_ACTION_STATUSES = ("pending", "acknowledged", "pending_verification")


def _preflight_dashboard_dedupe_key(account_id: str, assignment_id: str, starts_at: str) -> str:
    return f"account:{account_id}:scheduled_preflight:{assignment_id}:{starts_at}"


def _preflight_dashboard_action_status(preflight_status: str) -> str:
    """Map terminal preflight statuses to statuses accepted by upsert_account_dashboard_action."""
    return "pending" if str(preflight_status or "").strip().lower() == "preflight_blocked" else "resolved"


def _preflight_dashboard_action_severity(preflight_status: str, reason_code: str | None) -> str:
    if str(preflight_status or "").strip().lower() != "preflight_blocked":
        return "info"
    reason = str(reason_code or "").strip().lower()
    return "error" if reason in IDENTITY_PREFLIGHT_BLOCKED_REASONS else "warning"


def _find_active_preflight_dashboard_action_id(dedupe_key: str) -> str | None:
    rows = supabase_client._request_json(
        "GET",
        "account_dashboard_actions",
        query={
            "select": "id,status",
            "dedupe_key": f"eq.{dedupe_key}",
            "status": f"in.({','.join(ACTIVE_DASHBOARD_ACTION_STATUSES)})",
            "order": "created_at.desc",
            "limit": "1",
        },
    ) or []
    return str(rows[0].get("id") or "").strip() or None if rows else None


def _resolve_active_preflight_dashboard_action(
    *,
    dedupe_key: str,
    preflight_status: str,
    reason_code: str | None,
    metadata: dict[str, Any],
) -> None:
    """Transition an existing active CP4 action to resolved (audited RPC path)."""
    action_id = _find_active_preflight_dashboard_action_id(dedupe_key)
    if not action_id:
        return
    supabase_client.call_rpc(
        "transition_account_dashboard_action",
        {
            "p_action_id": action_id,
            "p_new_status": "resolved",
            "p_actor_type": "system",
            "p_actor_id": None,
            "p_reason": f"preflight_terminal:{preflight_status}" + (f":{reason_code}" if reason_code else ""),
            "p_metadata": metadata,
        },
    )


def _resolve_preflight_dashboard_context(
    *,
    request: dict[str, Any] | None = None,
    row: dict[str, Any] | None = None,
    account_id: str | None = None,
    assignment_id: str | None = None,
    starts_at: str | None = None,
) -> tuple[str, str, str] | None:
    metadata = dict((request or {}).get("metadata_safe") or {})
    resolved_account_id = normalize_request_uuid(account_id or (request or {}).get("account_id"))
    resolved_assignment_id = str(
        assignment_id
        or metadata.get("assignment_id")
        or (row or {}).get("assignment_id")
        or ""
    ).strip()
    resolved_starts_at = str(
        starts_at
        or metadata.get("scheduled_session_at")
        or (row or {}).get("scheduled_window_start")
        or ""
    ).strip()
    if not resolved_account_id or not resolved_assignment_id or not resolved_starts_at:
        return None
    return resolved_account_id, resolved_assignment_id, resolved_starts_at


def reconcile_preflight_dashboard_action(
    *,
    request: dict[str, Any] | None = None,
    row: dict[str, Any] | None = None,
    account_id: str | None = None,
    assignment_id: str | None = None,
    starts_at: str | None = None,
    preflight_status: str,
    reason_code: str | None = None,
    source: str = "run_control_dispatcher",
    metadata_safe: dict[str, Any] | None = None,
) -> None:
    context = _resolve_preflight_dashboard_context(
        request=request,
        row=row,
        account_id=account_id,
        assignment_id=assignment_id,
        starts_at=starts_at,
    )
    if not context:
        return
    resolved_account_id, resolved_assignment_id, resolved_starts_at = context
    status = str(preflight_status or "").strip().lower()
    if status not in TERMINAL_PREFLIGHT_DASHBOARD_STATUSES:
        return
    action_status = _preflight_dashboard_action_status(status)
    reason_suffix = f" ({reason_code})" if reason_code else ""
    metadata = {
        "source": source,
        "assignment_id": resolved_assignment_id,
        "scheduled_session_at": resolved_starts_at,
        "preflight_status": status,
        **(metadata_safe or {}),
    }
    if reason_code:
        metadata["reason_code"] = reason_code
    dedupe_key = _preflight_dashboard_dedupe_key(
        resolved_account_id,
        resolved_assignment_id,
        resolved_starts_at,
    )
    try:
        if action_status == "resolved":
            # Non-blocked terminals resolve any active CP4 action instead of
            # upserting an invalid status (upsert RPC only accepts active statuses
            # for existing rows and would reject "completed"/"action_required").
            _resolve_active_preflight_dashboard_action(
                dedupe_key=dedupe_key,
                preflight_status=status,
                reason_code=reason_code,
                metadata=metadata,
            )
            return
        supabase_client.call_rpc(
            "upsert_account_dashboard_action",
            {
                "p_account_id": resolved_account_id,
                "p_client_id": None,
                "p_incident_id": None,
                "p_action_type": PREFLIGHT_DASHBOARD_ACTION_TYPE,
                "p_status": action_status,
                "p_title": "Scheduled session preflight",
                "p_dedupe_key": dedupe_key,
                "p_safe_client_message": None,
                "p_admin_message": f"Scheduled session preflight blocked: {reason_code or status}.",
                "p_assistant_message": None,
                "p_action_label": "Review preflight",
                "p_action_deep_link": "/instagram-dashboard/devices",
                "p_severity": _preflight_dashboard_action_severity(status, reason_code),
                "p_audience": "admin",
                "p_requires_client_action": True,
                "p_blocking_campaign": False,
                "p_metadata": metadata,
            },
        )
    except Exception as exc:
        log(
            "warning",
            "scheduled_session_preflight_dashboard_action_failed",
            account_id=resolved_account_id,
            preflight_status=status,
            error=str(exc)[:200],
        )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: str | None) -> datetime | None:
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


def _load_preflight_row(preflight_id: str) -> dict[str, Any] | None:
    pid = str(preflight_id or "").strip()
    if not pid:
        return None
    rows = supabase_client._request_json(
        "GET",
        "scheduled_session_preflights",
        query={
            "select": "id,account_id,assignment_id,device_id,status,reason_code,expires_at,request_id,scheduled_window_start,scheduled_window_end,business_action_deadline,metadata_safe",
            "id": f"eq.{pid}",
            "limit": "1",
        },
    ) or []
    return dict(rows[0]) if rows else None


def _complete_preflight_terminal(
    *,
    preflight_id: str,
    status: str,
    reason_code: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    supabase_client.call_rpc(
        "complete_scheduled_session_preflight",
        {
            "p_preflight_id": preflight_id,
            "p_status": status,
            "p_reason_code": reason_code,
            "p_metadata_safe": metadata or {},
        },
    )


def _upsert_dispatcher_unavailable_action(*, account_id: str, request_id: str, reason: str) -> None:
    try:
        supabase_client.call_rpc(
            "upsert_account_dashboard_action",
            {
                "p_account_id": account_id,
                "p_client_id": None,
                "p_incident_id": None,
                "p_action_type": "scheduler_launch_blocked",
                "p_status": "pending",
                "p_title": "Scheduled preflight dispatcher unavailable",
                "p_dedupe_key": f"account:{account_id}:scheduler_launch_blocked:{DISPATCHER_UNAVAILABLE_REASON}",
                "p_safe_client_message": None,
                "p_admin_message": (
                    "Scheduled session preflight is queued but the Run Control dispatcher "
                    f"cannot claim it ({reason})."
                ),
                "p_assistant_message": None,
                "p_action_label": "Review dispatcher",
                "p_action_deep_link": "/instagram-dashboard/devices",
                "p_severity": "warning",
                "p_audience": "admin",
                "p_requires_client_action": False,
                "p_blocking_campaign": False,
                "p_metadata": {
                    "source": "run_control_dispatcher",
                    "reason": DISPATCHER_UNAVAILABLE_REASON,
                    "request_id": request_id,
                },
            },
        )
    except Exception as exc:
        log(
            "warning",
            "scheduled_session_preflight_dispatcher_action_failed",
            account_id=account_id,
            request_id=request_id,
            error=str(exc)[:200],
        )


def terminalize_scheduled_session_preflight_request(
    *,
    request: dict[str, Any],
    worker_id: str,
    preflight_status: str,
    reason_code: str,
    request_status: str = "failed",
) -> bool:
    request_id = normalize_request_uuid(request.get("id"))
    account_id = normalize_request_uuid(request.get("account_id"))
    if not request_id or not account_id:
        return False
    metadata = dict(request.get("metadata_safe") or {})
    preflight_id = str(metadata.get("preflight_id") or "").strip()
    row: dict[str, Any] | None = None
    if preflight_id:
        row = _load_preflight_row(preflight_id)
        terminal_statuses = {"preflight_ready", "preflight_blocked", "preflight_expired", "preflight_invalidated"}
        if row and str(row.get("status") or "") in terminal_statuses:
            pass
        else:
            try:
                _complete_preflight_terminal(
                    preflight_id=preflight_id,
                    status=preflight_status,
                    reason_code=reason_code,
                    metadata={"request_id": request_id, "source": "run_control_dispatcher"},
                )
            except Exception as exc:
                log(
                    "warning",
                    "scheduled_session_preflight_terminalize_row_failed",
                    request_id=request_id,
                    preflight_id=preflight_id,
                    error=str(exc)[:200],
                )
    latest = get_account_run_request(request_id) or request
    request_status_value = str(latest.get("status") or request.get("status") or "").strip().lower()
    if request_status_value == "queued":
        cancel_account_run_request(
            request_id=request_id,
            reason=reason_code,
        )
    else:
        complete_account_run_request(
            request_id,
            worker_id,
            request_status,
            error_code=reason_code,
            error_message_safe=f"Scheduled session preflight terminalized: {reason_code}.",
        )
    try:
        release_device_lock_for_request(request_id=request_id, worker_id=worker_id)
    except Exception as exc:
        log(
            "warning",
            "scheduled_session_preflight_lock_release_failed",
            request_id=request_id,
            error=str(exc)[:200],
        )
    reconcile_preflight_dashboard_action(
        request=request,
        row=row,
        preflight_status=preflight_status,
        reason_code=reason_code,
        source="run_control_dispatcher",
        metadata_safe={"request_id": request_id},
    )
    log(
        "info",
        "scheduled_session_preflight_request_terminalized",
        request_id=request_id,
        account_id=account_id,
        preflight_status=preflight_status,
        reason_code=reason_code,
    )
    return True


def evaluate_scheduled_session_preflight_claim(
    *,
    request: dict[str, Any],
    now: datetime | None = None,
) -> tuple[bool, str | None, str | None]:
    """Return (ok, preflight_terminal_status, reason_code)."""
    metadata = dict(request.get("metadata_safe") or {})
    preflight_id = str(metadata.get("preflight_id") or "").strip()
    if not preflight_id:
        return False, "preflight_invalidated", "preflight_id_missing"

    row = _load_preflight_row(preflight_id)
    if not row:
        return False, "preflight_invalidated", "preflight_row_missing"

    current = now or _utc_now()
    expires_at = _parse_iso(str(row.get("expires_at") or ""))
    window_end = _parse_iso(str(row.get("scheduled_window_end") or metadata.get("scheduled_session_ends_at") or ""))
    deadline = _parse_iso(str(row.get("business_action_deadline") or metadata.get("business_action_deadline") or ""))

    if deadline and current >= deadline:
        return False, "preflight_expired", "business_action_deadline_passed"
    if window_end and current >= window_end:
        return False, "preflight_expired", "scheduled_window_ended"

    late_preflight = metadata.get("late_preflight") is True
    status = str(row.get("status") or "").strip().lower()
    if expires_at and status == "preflight_running":
        if late_preflight:
            if deadline and current < deadline:
                pass
            elif deadline and current >= deadline:
                return False, "preflight_expired", "business_action_deadline_passed"
            elif current > expires_at:
                return False, "preflight_expired", "business_action_deadline_passed"
        elif current > expires_at:
            return False, "preflight_expired", "preflight_start_window_elapsed"

    if status == "preflight_ready":
        return True, None, None
    if status in {"preflight_blocked", "preflight_expired", "preflight_invalidated"}:
        return False, status, str(row.get("reason_code") or status)
    if status not in {"preflight_due", "preflight_running", "preflight_lease_unavailable"}:
        return False, "preflight_invalidated", f"unexpected_preflight_status:{status or 'unknown'}"
    return True, None, None


def _list_queued_preflight_requests() -> list[dict[str, Any]]:
    rows = supabase_client._request_json(
        "GET",
        "account_run_requests",
        query={
            "select": "id,account_id,status,requested_run_type,created_at,metadata_safe",
            "status": "eq.queued",
            "requested_run_type": f"eq.{PREFLIGHT_RUN_TYPE}",
            "order": "created_at.asc",
            "limit": "20",
        },
    ) or []
    return [dict(row) for row in rows if isinstance(row, dict)]


def reconcile_stale_scheduled_session_preflight_requests(
    *,
    worker_id: str,
    allowed_run_types: list[str],
    now: datetime | None = None,
) -> int:
    current = now or _utc_now()
    allowed = {str(item or "").strip().lower() for item in allowed_run_types}
    handled = 0
    for request in _list_queued_preflight_requests():
        request_id = normalize_request_uuid(request.get("id"))
        account_id = normalize_request_uuid(request.get("account_id"))
        if not request_id:
            continue
        created_at = _parse_iso(str(request.get("created_at") or ""))
        age_seconds = (current - created_at).total_seconds() if created_at else 0.0

        if PREFLIGHT_RUN_TYPE not in allowed:
            if age_seconds >= STALE_QUEUED_SECONDS and account_id:
                _upsert_dispatcher_unavailable_action(
                    account_id=account_id,
                    request_id=request_id,
                    reason=DISPATCHER_UNAVAILABLE_REASON,
                )
            continue

        ok, terminal_status, reason = evaluate_scheduled_session_preflight_claim(request=request, now=current)
        if ok:
            continue
        if not terminal_status:
            continue
        if terminalize_scheduled_session_preflight_request(
            request=request,
            worker_id=worker_id,
            preflight_status=terminal_status,
            reason_code=str(reason or terminal_status),
        ):
            handled += 1
    return handled
