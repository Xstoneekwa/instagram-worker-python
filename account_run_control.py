"""Run Control helpers for account_run_requests queue RPCs."""

from __future__ import annotations

import uuid
from typing import Any

import supabase_client

ACTIVE_RUN_STATUSES = {"running", "queued", "pending", "in_progress", "active", "starting"}
ACTIVE_REQUEST_STATUSES = {"queued", "claimed", "starting", "running"}
TERMINAL_REQUEST_STATUSES = {"completed", "failed", "canceled", "blocked"}
TERMINAL_IG_RUN_STATUSES = {"completed", "failed", "stopped", "canceled", "blocked", "aborted"}


def _row_or_none(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return dict(value[0])
    return None


def normalize_request_uuid(value: str | None) -> str | None:
    """Return canonical UUID string or None when value is missing/invalid."""
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return str(uuid.UUID(raw))
    except (TypeError, ValueError):
        return None


def create_account_run_request(
    *,
    account_id: str,
    requested_by: str | None = None,
    actor_type: str = "admin",
    source_surface: str = "instagram_dashboard",
    requested_run_type: str = "account_session",
    idempotency_key: str | None = None,
    priority: int = 0,
    metadata_safe: dict[str, Any] | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "p_account_id": account_id,
        "p_actor_type": actor_type,
        "p_source_surface": source_surface,
        "p_requested_run_type": requested_run_type,
        "p_priority": int(priority),
        "p_metadata_safe": metadata_safe or {},
    }
    if requested_by:
        params["p_requested_by"] = requested_by
    if idempotency_key:
        params["p_idempotency_key"] = idempotency_key
    row = _row_or_none(supabase_client.call_rpc("create_account_run_request", params))
    if not row:
        raise RuntimeError("create_account_run_request returned empty response")
    return row


def claim_next_account_run_request(
    worker_id: str,
    *,
    lease_seconds: int = 120,
    allowed_run_types: list[str] | None = None,
) -> dict[str, Any] | None:
    params: dict[str, Any] = {
        "p_worker_id": worker_id,
        "p_lease_seconds": int(lease_seconds),
    }
    if allowed_run_types:
        params["p_allowed_run_types"] = allowed_run_types
    row = _row_or_none(supabase_client.call_rpc("claim_next_account_run_request", params))
    if not row:
        return None
    if not normalize_request_uuid(row.get("id")):
        return None
    return row


def mark_account_run_request_starting(request_id: str, worker_id: str) -> dict[str, Any] | None:
    normalized_request_id = normalize_request_uuid(request_id)
    if not normalized_request_id:
        return None
    return _row_or_none(
        supabase_client.call_rpc(
            "mark_account_run_request_starting",
            {"p_request_id": normalized_request_id, "p_worker_id": worker_id},
        )
    )


def link_account_run_request_run(request_id: str, worker_id: str, run_id: str) -> dict[str, Any] | None:
    normalized_request_id = normalize_request_uuid(request_id)
    normalized_run_id = normalize_request_uuid(run_id)
    if not normalized_request_id or not normalized_run_id:
        return None
    return _row_or_none(
        supabase_client.call_rpc(
            "link_account_run_request_run",
            {
                "p_request_id": normalized_request_id,
                "p_worker_id": worker_id,
                "p_run_id": normalized_run_id,
            },
        )
    )


def complete_account_run_request(
    request_id: str,
    worker_id: str,
    status: str,
    *,
    error_code: str | None = None,
    error_message_safe: str | None = None,
) -> dict[str, Any] | None:
    normalized_request_id = normalize_request_uuid(request_id)
    if not normalized_request_id:
        return None
    params: dict[str, Any] = {
        "p_request_id": normalized_request_id,
        "p_worker_id": worker_id,
        "p_status": status,
    }
    if error_code:
        params["p_error_code"] = error_code
    if error_message_safe:
        params["p_error_message_safe"] = error_message_safe
    return _row_or_none(supabase_client.call_rpc("complete_account_run_request", params))


def cancel_account_run_request(
    *,
    request_id: str | None = None,
    account_id: str | None = None,
    actor_id: str | None = None,
    reason: str = "manual_stop",
) -> dict[str, Any] | None:
    params: dict[str, Any] = {"p_reason": reason}
    normalized_request_id = normalize_request_uuid(request_id)
    if normalized_request_id:
        params["p_request_id"] = normalized_request_id
    normalized_account_id = normalize_request_uuid(account_id)
    if normalized_account_id:
        params["p_account_id"] = normalized_account_id
    if actor_id:
        params["p_actor_id"] = actor_id
    return _row_or_none(supabase_client.call_rpc("cancel_account_run_request", params))


def reclaim_stale_account_run_requests(worker_id: str | None = None) -> int:
    params: dict[str, Any] = {}
    if worker_id:
        params["p_worker_id"] = worker_id
    value = supabase_client.call_rpc("reclaim_stale_account_run_requests", params)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def is_account_run_request_cancel_requested(request_id: str) -> bool:
    normalized_request_id = normalize_request_uuid(request_id)
    if not normalized_request_id:
        return False
    value = supabase_client.call_rpc(
        "is_account_run_request_cancel_requested",
        {"p_request_id": normalized_request_id},
    )
    return bool(value)


def get_account_run_request(request_id: str) -> dict[str, Any] | None:
    normalized_request_id = normalize_request_uuid(request_id)
    if not normalized_request_id:
        return None
    rows = supabase_client._request_json(
        "GET",
        "account_run_requests",
        query={"select": "*", "id": f"eq.{normalized_request_id}", "limit": "1"},
    ) or []
    if not rows:
        return None
    return dict(rows[0])


def _utc_now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _normalize_run_id(run_id: str | None) -> str | None:
    value = str(run_id or "").strip()
    return value if value else None


def _map_ig_run_terminal_status(terminal_status: str) -> str:
    normalized = str(terminal_status or "").strip().lower()
    if normalized in {"canceled", "cancelled"}:
        return "stopped"
    if normalized in TERMINAL_IG_RUN_STATUSES | ACTIVE_RUN_STATUSES:
        return normalized
    return "failed"


def get_ig_run_by_id(run_id: str) -> dict[str, Any] | None:
    normalized_run_id = _normalize_run_id(run_id)
    if not normalized_run_id:
        return None
    rows = supabase_client._request_json(
        "GET",
        "ig_runs",
        query={
            "select": "id,account_id,status,started_at,finished_at",
            "id": f"eq.{normalized_run_id}",
            "limit": "1",
        },
    ) or []
    if not rows:
        return None
    return dict(rows[0])


def reconcile_linked_ig_run_terminal(
    *,
    run_id: str | None,
    terminal_status: str,
    account_id: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Force a linked ig_runs row out of active statuses after subprocess exit.

    Updates only the explicit run_id row. No-op when run_id is missing, the row is
    not found, account_id mismatches, or the run is already terminal.
    """
    normalized_run_id = _normalize_run_id(run_id)
    if not normalized_run_id:
        return {
            "reconciled": False,
            "reason": "no_run_id",
            "run_id": None,
            "terminal_status": None,
            "previous_status": None,
        }

    row = get_ig_run_by_id(normalized_run_id)
    if not row:
        return {
            "reconciled": False,
            "reason": "run_not_found",
            "run_id": normalized_run_id,
            "terminal_status": None,
            "previous_status": None,
        }

    row_account_id = str(row.get("account_id") or "").strip()
    if account_id and row_account_id and row_account_id != str(account_id).strip():
        return {
            "reconciled": False,
            "reason": "account_mismatch",
            "run_id": normalized_run_id,
            "terminal_status": None,
            "previous_status": str(row.get("status") or "").strip().lower() or None,
        }

    previous_status = str(row.get("status") or "").strip().lower()
    if previous_status in TERMINAL_IG_RUN_STATUSES:
        return {
            "reconciled": False,
            "reason": "already_terminal",
            "run_id": normalized_run_id,
            "terminal_status": previous_status,
            "previous_status": previous_status,
        }

    if previous_status not in ACTIVE_RUN_STATUSES:
        return {
            "reconciled": False,
            "reason": "not_active",
            "run_id": normalized_run_id,
            "terminal_status": previous_status or None,
            "previous_status": previous_status or None,
        }

    mapped_status = _map_ig_run_terminal_status(terminal_status)
    now = _utc_now_iso()
    body: dict[str, Any] = {
        "status": mapped_status,
        "updated_at": now,
        "finished_at": now,
    }
    if mapped_status == "completed":
        body["completed_at"] = now

    supabase_client._request_json(
        "PATCH",
        "ig_runs",
        query={"id": f"eq.{normalized_run_id}"},
        body=body,
        prefer_representation=False,
    )
    return {
        "reconciled": True,
        "reason": "reconciled",
        "run_id": normalized_run_id,
        "terminal_status": mapped_status,
        "previous_status": previous_status,
    }


def get_active_account_run_request(account_id: str) -> dict[str, Any] | None:
    rows = supabase_client._request_json(
        "GET",
        "account_run_requests",
        query={
            "select": "*",
            "account_id": f"eq.{account_id}",
            "status": f"in.({','.join(sorted(ACTIVE_REQUEST_STATUSES))})",
            "order": "created_at.desc",
            "limit": "1",
        },
    ) or []
    if not rows:
        return None
    return dict(rows[0])


def insert_manual_run_audit(
    *,
    account_id: str,
    action_type: str,
    status: str,
    message: str,
    run_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    body = {
        "account_id": account_id,
        "run_id": run_id,
        "target_username": None,
        "action_type": action_type,
        "status": status,
        "message": message[:500],
        "payload": payload or {},
    }
    supabase_client._request_json(
        "POST",
        "ig_action_logs",
        body=body,
        prefer_representation=False,
    )
