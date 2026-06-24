"""Canonical orphan login-challenge recovery state backed by ig_action_logs."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from supabase_client import _request_json, insert_action_log

ORPHAN_RECOVERY_EVENT_DETECTED = "orphan_login_challenge_detected"
ORPHAN_RECOVERY_EVENT_STARTED = "login_orphan_recovery_started"
ORPHAN_RECOVERY_EVENT_RESTORED = "login_surface_restored"
ORPHAN_RECOVERY_EVENT_BLOCKED = "login_orphan_recovery_blocked"
ORPHAN_RECOVERY_EVENT_FAILED = "login_orphan_recovery_failed"

ORPHAN_RECOVERY_EVENTS = frozenset(
    {
        ORPHAN_RECOVERY_EVENT_DETECTED,
        ORPHAN_RECOVERY_EVENT_STARTED,
        ORPHAN_RECOVERY_EVENT_RESTORED,
        ORPHAN_RECOVERY_EVENT_BLOCKED,
        ORPHAN_RECOVERY_EVENT_FAILED,
    }
)

TERMINAL_RECOVERY_STATES = frozenset({"login_surface_restored"})
BLOCKING_RECOVERY_STATES = frozenset(
    {
        "orphan_challenge_detected",
        "recovery_in_progress",
        "recovery_blocked",
        "recovery_failed",
    }
)

ACTIVE_LOGIN_REQUEST_STATUSES = frozenset({"queued", "claimed", "starting", "running"})
LOGIN_PROVISIONING_RUN_TYPES = frozenset({"login_provisioning", "login_email_code_resume"})


def _event_to_state(action_type: str) -> str:
    mapping = {
        ORPHAN_RECOVERY_EVENT_DETECTED: "orphan_challenge_detected",
        ORPHAN_RECOVERY_EVENT_STARTED: "recovery_in_progress",
        ORPHAN_RECOVERY_EVENT_RESTORED: "login_surface_restored",
        ORPHAN_RECOVERY_EVENT_BLOCKED: "recovery_blocked",
        ORPHAN_RECOVERY_EVENT_FAILED: "recovery_failed",
    }
    return mapping.get(str(action_type or "").strip(), "unknown")


def record_orphan_recovery_event(
    *,
    account_id: str,
    event_type: str,
    run_id: str = "",
    status: str = "recorded",
    message: str = "",
    metadata: Optional[dict[str, Any]] = None,
) -> None:
    aid = str(account_id or "").strip()
    if not aid:
        return
    event = str(event_type or "").strip()
    if event not in ORPHAN_RECOVERY_EVENTS:
        raise ValueError(f"unsupported_orphan_recovery_event:{event}")
    safe_metadata = {
        "recovery_state": _event_to_state(event),
        **dict(metadata or {}),
    }
    insert_action_log(
        run_id=str(run_id or "").strip() or "orphan-login-recovery",
        account_id=aid,
        target_username="",
        action_type=event,
        status=status,
        message=message or event,
        payload=safe_metadata,
    )


def _load_recent_recovery_events(account_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
    aid = str(account_id or "").strip()
    if not aid:
        return []
    rows = _request_json(
        "GET",
        "ig_action_logs",
        query={
            "account_id": f"eq.{aid}",
            "action_type": f"in.({','.join(sorted(ORPHAN_RECOVERY_EVENTS))})",
            "select": "action_type,status,message,payload,created_at",
            "order": "created_at.desc",
            "limit": str(max(1, min(limit, 50))),
        },
    )
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _load_recent_orphan_blocked_request(account_id: str) -> Optional[dict[str, Any]]:
    aid = str(account_id or "").strip()
    if not aid:
        return None
    rows = _request_json(
        "GET",
        "account_run_requests",
        query={
            "account_id": f"eq.{aid}",
            "requested_run_type": "eq.login_provisioning",
            "status": "eq.blocked",
            "error_code": "eq.orphan_challenge_provenance_weak",
            "select": "id,status,error_code,error_message_safe,finished_at,created_at",
            "order": "finished_at.desc",
            "limit": "1",
        },
    )
    if not isinstance(rows, list) or not rows:
        return None
    row = rows[0]
    return row if isinstance(row, dict) else None


def has_active_login_provisioning_request(account_id: str) -> bool:
    aid = str(account_id or "").strip()
    if not aid:
        return False
    rows = _request_json(
        "GET",
        "account_run_requests",
        query={
            "account_id": f"eq.{aid}",
            "status": f"in.({','.join(sorted(ACTIVE_LOGIN_REQUEST_STATUSES))})",
            "requested_run_type": f"in.({','.join(sorted(LOGIN_PROVISIONING_RUN_TYPES))})",
            "select": "id,status,requested_run_type",
            "limit": "1",
        },
    )
    return bool(isinstance(rows, list) and rows)


def resolve_orphan_recovery_state(account_id: str) -> dict[str, Any]:
    """Return the latest canonical orphan-recovery state for an account."""

    events = _load_recent_recovery_events(account_id)
    latest_event = events[0] if events else None
    state = _event_to_state(str(latest_event.get("action_type") or "")) if latest_event else "none"
    detected_at = str(latest_event.get("created_at") or "") if latest_event else ""

    if state == "none":
        blocked_request = _load_recent_orphan_blocked_request(account_id)
        if blocked_request:
            state = "orphan_challenge_detected"
            detected_at = str(blocked_request.get("finished_at") or blocked_request.get("created_at") or "")

    if state == "login_surface_restored":
        blocking_client = False
        botapp_action_available = False
    elif state in BLOCKING_RECOVERY_STATES:
        blocking_client = True
        botapp_action_available = state in {"orphan_challenge_detected", "recovery_blocked", "recovery_failed"}
    else:
        blocking_client = False
        botapp_action_available = False

    if has_active_login_provisioning_request(account_id):
        botapp_action_available = False

    return {
        "state": state,
        "blocking_client": blocking_client,
        "botapp_action_available": botapp_action_available,
        "detected_at": detected_at,
        "latest_event_type": str(latest_event.get("action_type") or "") if latest_event else "",
        "has_active_login_provisioning": has_active_login_provisioning_request(account_id),
    }
