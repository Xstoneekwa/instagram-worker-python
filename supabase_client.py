"""Minimal Supabase REST client for worker control/logging."""

from __future__ import annotations

import json
import os
from typing import Any
from urllib import error, parse, request
from datetime import datetime, timezone

_LOG_CONTEXT_ACCOUNT_ID: str | None = None
_LOG_CONTEXT_RUN_ID: str | None = None


def _base_url() -> str:
    url = (os.getenv("SUPABASE_URL") or "").strip().rstrip("/")
    if not url:
        raise RuntimeError("SUPABASE_URL is not set")
    return url


def _service_key() -> str:
    key = (os.getenv("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not key:
        raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY is not set")
    return key


def _request_json(
    method: str,
    table: str,
    *,
    query: dict[str, str] | None = None,
    body: dict[str, Any] | list[dict[str, Any]] | None = None,
    prefer_representation: bool = False,
) -> Any:
    base = _base_url()
    key = _service_key()
    params = f"?{parse.urlencode(query or {})}" if query else ""
    url = f"{base}/rest/v1/{table}{params}"

    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    if prefer_representation:
        headers["Prefer"] = "return=representation"

    data = None
    if body is not None:
        data = json.dumps(body, separators=(",", ":")).encode("utf-8")

    req = request.Request(url=url, method=method, headers=headers, data=data)
    try:
        with request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
            if not raw:
                return None
            return json.loads(raw.decode("utf-8"))
    except error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Supabase {method} {table} failed: {e.code} {detail}") from e
    except error.URLError as e:
        raise RuntimeError(f"Supabase request error: {e}") from e


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def set_log_context(account_id: str | None, run_id: str | None) -> None:
    global _LOG_CONTEXT_ACCOUNT_ID, _LOG_CONTEXT_RUN_ID
    _LOG_CONTEXT_ACCOUNT_ID = (account_id or "").strip() or None
    _LOG_CONTEXT_RUN_ID = (run_id or "").strip() or None
    print(
        json.dumps(
            {
                "level": "info",
                "event": "supabase_log_context_set",
                "account_id": _LOG_CONTEXT_ACCOUNT_ID,
                "run_id": _LOG_CONTEXT_RUN_ID,
            },
            ensure_ascii=False,
        )
    )


def load_account(account_id: str | None = None, username: str | None = None) -> dict[str, Any] | None:
    query: dict[str, str] = {"select": "*", "limit": "1"}
    if account_id:
        query["id"] = f"eq.{account_id}"
    elif username:
        query["username"] = f"eq.{username}"
    else:
        raise ValueError("Either account_id or username is required")

    rows = _request_json("GET", "ig_accounts", query=query)
    if not rows:
        return None
    return rows[0]


def load_pending_targets(account_id: str, limit: int = 25) -> list[dict[str, Any]]:
    # Eligible queue rows only. Excludes terminal statuses (e.g. completed, failed, success,
    # send_blocked_tested) because they are not pending or queued.
    query = {
        "select": "*",
        "account_id": f"eq.{account_id}",
        "status": "in.(pending,queued)",
        "order": "created_at.asc",
        "limit": str(max(1, int(limit))),
    }
    rows = _request_json("GET", "ig_targets", query=query) or []
    out: list[dict[str, Any]] = []
    for row in rows:
        username = (row.get("target_username") or row.get("username") or "").strip()
        if not username:
            continue
        out.append(
            {
                "id": row.get("id"),
                "account_id": row.get("account_id") or account_id,
                "target_username": username,
                "raw": row,
            }
        )
    return out


def load_target_by_id(target_id: str) -> dict[str, Any] | None:
    rows = _request_json(
        "GET",
        "ig_targets",
        query={"select": "*", "id": f"eq.{target_id}", "limit": "1"},
    )
    if not rows:
        return None
    return rows[0]


def create_run(account_id: str) -> dict[str, Any]:
    now = _utc_now_iso()
    row = _request_json(
        "POST",
        "ig_runs",
        body={
            "account_id": account_id,
            "status": "running",
            "started_at": now,
            "updated_at": now,
        },
        prefer_representation=True,
    )
    if not row:
        raise RuntimeError("Supabase create_run returned empty response")
    return row[0]


def insert_action_log(
    run_id: str,
    account_id: str,
    target_username: str,
    action_type: str,
    status: str,
    message: str,
    payload: dict[str, Any] | None = None,
) -> None:
    body = {
        "run_id": run_id,
        "account_id": account_id,
        "target_username": target_username,
        "action_type": action_type,
        "status": status,
        "message": message,
        "payload": payload or {},
    }
    _request_json(
        "POST",
        "ig_action_logs",
        body=body,
        prefer_representation=False,
    )


def log_performance_event(
    action_type: str,
    status: str,
    target_username: str | None,
    payload: dict,
) -> bool:
    """
    Fail-silent performance logging to ig_action_logs using contextual run/account ids.
    Never raises.
    """
    try:
        payload_obj = payload or {}
        print(
            json.dumps(
                {
                    "level": "info",
                    "event": "supabase_performance_event_attempt",
                    "action_type": action_type,
                    "status": status,
                    "account_id": _LOG_CONTEXT_ACCOUNT_ID,
                    "run_id": _LOG_CONTEXT_RUN_ID,
                    "target_username": (target_username or "").strip() or None,
                    "payload_keys": sorted(list(payload_obj.keys())),
                },
                ensure_ascii=False,
            )
        )
        if not _LOG_CONTEXT_ACCOUNT_ID or not _LOG_CONTEXT_RUN_ID:
            return False
        insert_action_log(
            run_id=_LOG_CONTEXT_RUN_ID,
            account_id=_LOG_CONTEXT_ACCOUNT_ID,
            target_username=(target_username or "").strip(),
            action_type=action_type,
            status=status,
            message=f"{action_type} event",
            payload=payload_obj,
        )
        print(
            json.dumps(
                {
                    "level": "info",
                    "event": "supabase_performance_event_success",
                    "action_type": action_type,
                    "status": status,
                    "account_id": _LOG_CONTEXT_ACCOUNT_ID,
                    "run_id": _LOG_CONTEXT_RUN_ID,
                    "target_username": (target_username or "").strip() or None,
                    "payload_keys": sorted(list(payload_obj.keys())),
                },
                ensure_ascii=False,
            )
        )
        return True
    except Exception as e:
        # Console warning only; never break worker flow.
        print(
            json.dumps(
                {
                    "level": "warning",
                    "event": "supabase_performance_event_failed",
                    "action_type": action_type,
                    "status": status,
                    "account_id": _LOG_CONTEXT_ACCOUNT_ID,
                    "run_id": _LOG_CONTEXT_RUN_ID,
                    "target_username": target_username,
                    "payload_keys": sorted(list((payload or {}).keys())),
                    "payload": payload or {},
                    "error": str(e),
                },
                ensure_ascii=False,
            )
        )
        return False


def update_run_status(
    run_id: str,
    status: str,
    totals: dict[str, Any],
    performance_summary: dict[str, Any],
) -> None:
    now = _utc_now_iso()
    body: dict[str, Any] = {
        "status": status,
        "totals": totals,
        "performance_summary": performance_summary,
        "total_targets": int(totals.get("total", 0)),
        "updated_at": now,
    }
    if status == "completed":
        body["finished_at"] = now
        body["completed_at"] = now
    elif status == "failed":
        body["finished_at"] = now
    _request_json(
        "PATCH",
        "ig_runs",
        query={"id": f"eq.{run_id}"},
        body=body,
        prefer_representation=False,
    )


def mark_target_dm_sent_completed(target_id: str) -> None:
    """After verified real DM send: dm_sent, processed_at, status completed."""
    now = _utc_now_iso()
    body: dict[str, Any] = {
        "dm_sent": True,
        "processed_at": now,
        "status": "completed",
        "updated_at": now,
        "last_run_at": now,
        "last_error": None,
    }
    _request_json(
        "PATCH",
        "ig_targets",
        query={"id": f"eq.{target_id}"},
        body=body,
        prefer_representation=False,
    )


def update_target_status(
    target_id: str,
    status: str,
    last_error: str | None = None,
    *,
    attempted: bool = True,
) -> None:
    now = _utc_now_iso()
    # Terminal send_blocked_tested: no processed_at / no dm_sent (SEND_DM_SAFE dry-run only).
    body: dict[str, Any] = {
        "status": status,
        "last_error": last_error,
        "updated_at": now,
        "last_run_at": now if attempted else None,
    }
    if status == "success":
        body["processed_at"] = now
    _request_json(
        "PATCH",
        "ig_targets",
        query={"id": f"eq.{target_id}"},
        body=body,
        prefer_representation=False,
    )


def increment_target_retry_count(target_id: str) -> None:
    now = _utc_now_iso()
    row = load_target_by_id(target_id) or {}
    current = int(row.get("retry_count") or 0)
    _request_json(
        "PATCH",
        "ig_targets",
        query={"id": f"eq.{target_id}"},
        body={"retry_count": current + 1, "updated_at": now},
        prefer_representation=False,
    )
