"""Minimal Supabase REST client for worker control/logging."""

from __future__ import annotations

import json
import os
import re
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


def _strip_one_unknown_column(body: dict[str, Any], err: str) -> dict[str, Any] | None:
    """Parse PostgREST/Postgres unknown-column errors and drop that key for retry."""
    m = re.search(r'column "([^"]+)"', err, re.I)
    if not m:
        m = re.search(r"Could not find the '([^']+)' column", err, re.I)
    if not m:
        return None
    col = m.group(1)
    if col not in body:
        return None
    return {k: v for k, v in body.items() if k != col}


def _request_json_tolerate_unknown_columns(
    method: str,
    table: str,
    *,
    query: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
    prefer_representation: bool = False,
) -> Any:
    """Same as _request_json but retries after dropping columns the server does not have."""
    if body is None:
        return _request_json(
            method,
            table,
            query=query,
            body=None,
            prefer_representation=prefer_representation,
        )
    cur = dict(body)
    last_err: RuntimeError | None = None
    for _ in range(48):
        try:
            return _request_json(
                method,
                table,
                query=query,
                body=cur,
                prefer_representation=prefer_representation,
            )
        except RuntimeError as e:
            last_err = e
            nxt = _strip_one_unknown_column(cur, str(e))
            if nxt is None:
                raise
            cur = nxt
    if last_err:
        raise last_err
    raise RuntimeError("Supabase retry strip exhausted")


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
    mark_target_dm_sent_business(
        target_id,
        navigation_status_post_send="full",
    )


def mark_target_follow_business(
    target_id: str,
    *,
    run_id: str | None = None,
    follow_status: str | None = None,
    follow_method: str | None = None,
    last_follow_error: str | None = None,
) -> dict[str, Any]:
    """
    Persist follow business fields without touching DM columns or terminal DM status.
    """
    now = _utc_now_iso()
    body: dict[str, Any] = {
        "followed": True,
        "followed_at": now,
        "processed_at": now,
        "updated_at": now,
    }
    if run_id:
        body["last_follow_run_id"] = str(run_id)
    if follow_status is not None:
        body["follow_status"] = str(follow_status)[:200]
    if follow_method is not None:
        body["follow_method"] = str(follow_method)[:120]
    if last_follow_error is not None:
        body["last_follow_error"] = str(last_follow_error)[:500]

    def _patch(b: dict[str, Any]) -> None:
        _request_json(
            "PATCH",
            "ig_targets",
            query={"id": f"eq.{target_id}"},
            body=b,
            prefer_representation=False,
        )

    out: dict[str, Any] = {"ok": False, "applied": "none", "error": None}
    try:
        _patch(body)
        out["ok"] = True
        out["applied"] = "full"
        return out
    except RuntimeError as e:
        out["error"] = str(e)
        minimal: dict[str, Any] = {
            "followed": True,
            "followed_at": now,
            "processed_at": now,
            "updated_at": now,
        }
        if run_id:
            minimal["last_follow_run_id"] = str(run_id)
        try:
            _patch(minimal)
            out["ok"] = True
            out["applied"] = "minimal"
            return out
        except RuntimeError as e2:
            out["error"] = str(e2)
            return out


def patch_target_follow_fields(
    target_id: str,
    *,
    follow_status: str | None = None,
    follow_method: str | None = None,
    last_follow_error: str | None = None,
    last_follow_run_id: str | None = None,
) -> None:
    """Non-terminal follow diagnostics (failed verify / missing button)."""
    now = _utc_now_iso()
    body: dict[str, Any] = {"updated_at": now}
    if follow_status is not None:
        body["follow_status"] = str(follow_status)[:200]
    if follow_method is not None:
        body["follow_method"] = str(follow_method)[:120]
    if last_follow_error is not None:
        body["last_follow_error"] = str(last_follow_error)[:500]
    if last_follow_run_id is not None:
        body["last_follow_run_id"] = str(last_follow_run_id)
    try:
        _request_json(
            "PATCH",
            "ig_targets",
            query={"id": f"eq.{target_id}"},
            body=body,
            prefer_representation=False,
        )
    except RuntimeError:
        pass


def mark_target_dm_sent_business(
    target_id: str,
    *,
    run_id: str | None = None,
    message_preview: str | None = None,
    thread_state_at_send: str | None = None,
    send_method: str | None = None,
    navigation_status_post_send: str | None = None,
) -> dict[str, Any]:
    """
    Persist business truth after a real DM was sent (best-effort extended fields).
    navigation_status_post_send: 'full' | 'partial' — partial keeps dm_sent but records UI incomplete.
    Returns {ok, applied, error} for observability; never raises.
    """
    now = _utc_now_iso()
    nav = (navigation_status_post_send or "full").strip().lower()
    is_partial = nav == "partial"
    status = "sent_navigation_partial" if is_partial else "completed"
    last_error: str | None = "sent_navigation_partial" if is_partial else None

    body: dict[str, Any] = {
        "dm_sent": True,
        "dm_sent_at": now,
        "processed_at": now,
        "updated_at": now,
        "last_run_at": now,
        "status": status,
        "last_error": last_error,
    }
    if run_id:
        body["last_dm_run_id"] = str(run_id)
    if message_preview is not None:
        body["last_dm_message_preview"] = str(message_preview)[:500]
    if thread_state_at_send:
        body["thread_state_at_send"] = str(thread_state_at_send)
    if send_method:
        body["send_method"] = str(send_method)

    def _patch(b: dict[str, Any]) -> None:
        _request_json(
            "PATCH",
            "ig_targets",
            query={"id": f"eq.{target_id}"},
            body=b,
            prefer_representation=False,
        )

    out: dict[str, Any] = {"ok": False, "applied": "none", "error": None}
    try:
        _patch(body)
        out["ok"] = True
        out["applied"] = "full"
        return out
    except RuntimeError as e:
        out["error"] = str(e)
        minimal: dict[str, Any] = {
            "dm_sent": True,
            "processed_at": now,
            "updated_at": now,
            "last_run_at": now,
            "status": status,
            "last_error": last_error,
        }
        try:
            _patch(minimal)
            out["ok"] = True
            out["applied"] = "minimal"
            return out
        except RuntimeError as e2:
            out["error"] = str(e2)
            return out


def update_target_post_send_navigation(
    target_id: str, *, navigation_status_post_send: str
) -> None:
    """After finalize: record whether return-to-search succeeded (does not clear dm_sent)."""
    now = _utc_now_iso()
    nav = (navigation_status_post_send or "").strip().lower()
    is_partial = nav == "partial"
    body: dict[str, Any] = {
        "updated_at": now,
        "status": "sent_navigation_partial" if is_partial else "completed",
        "last_error": ("sent_navigation_partial" if is_partial else None),
    }
    try:
        _request_json(
            "PATCH",
            "ig_targets",
            query={"id": f"eq.{target_id}"},
            body=body,
            prefer_representation=False,
        )
    except RuntimeError:
        _request_json(
            "PATCH",
            "ig_targets",
            query={"id": f"eq.{target_id}"},
            body={
                "updated_at": now,
                "last_error": ("sent_navigation_partial" if is_partial else None),
            },
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


def _canonical_interaction_username(username: str) -> str:
    return (username or "").strip().lstrip("@").lower()


_VISUAL_CANDIDATE_SENTINEL_PREFIX = "__visual_candidate:"


def _invalid_interacted_username_reason(canonical_username: str) -> str | None:
    """
    ig_interacted_users must only store real Instagram handles (normalized).
    Returns a machine reason if persist must be skipped, else None.
    """
    u = canonical_username
    if not u:
        return "empty_username"
    if u.startswith(_VISUAL_CANDIDATE_SENTINEL_PREFIX):
        return "visual_candidate_sentinel"
    return None


def _emit_interacted_user_skip_log(
    event: str,
    *,
    account_id: str,
    raw_username: str,
    normalized_username: str,
    source_profile: str,
    reason: str,
) -> None:
    print(
        json.dumps(
            {
                "level": "info",
                "event": event,
                "account_id": account_id,
                "username": raw_username,
                "normalized_username": normalized_username,
                "source_profile": source_profile,
                "reason": reason,
            },
            ensure_ascii=False,
        )
    )


def _canonical_source_profile(source_profile: str) -> str:
    return (source_profile or "").strip().lstrip("@").lower()


def _nonblank_str(v: Any) -> bool:
    return bool(str(v or "").strip())


def _apply_interacted_user_row_attribution(
    row: dict[str, Any] | None,
    body: dict[str, Any],
    source_profile_canonical: str | None,
) -> None:
    """
    first_* only when missing on existing row; last_* always from current write.
    Also refreshes legacy source_profile when we have a canonical CT handle.
    """
    if source_profile_canonical:
        body["last_source_profile"] = source_profile_canonical
        if not _nonblank_str((row or {}).get("first_source_profile")):
            body["first_source_profile"] = source_profile_canonical
        body["source_profile"] = source_profile_canonical
    rid = body.get("run_id")
    if rid is not None and str(rid).strip():
        rs = str(rid).strip()
        body["last_run_id"] = rs
        if not _nonblank_str((row or {}).get("first_run_id")):
            body["first_run_id"] = rs
    ls = body.get("last_session_id")
    if ls is not None and str(ls).strip():
        body["last_session_id"] = str(ls).strip()


def load_interacted_user(
    account_id: str,
    username: str,
    source_profile: str = "",
) -> dict[str, Any] | None:
    """
    Latest row for (account_id, username). Unique constraint is (account_id, username);
    source_profile is stored on the row but not part of the lookup key.
    """
    u = _canonical_interaction_username(username)
    rows = _request_json(
        "GET",
        "ig_interacted_users",
        query={
            "select": "*",
            "account_id": f"eq.{account_id}",
            "username": f"eq.{u}",
            "order": "last_interaction_at.desc",
            "limit": "1",
        },
    )
    if not rows:
        return None
    return rows[0]


def merge_interacted_user_row(
    account_id: str,
    username: str,
    source_profile: str,
    patch: dict[str, Any],
) -> dict[str, Any]:
    """Insert or patch ig_interacted_users (fail-silent friendly return)."""
    now = _utc_now_iso()
    u = _canonical_interaction_username(username)
    inv = _invalid_interacted_username_reason(u)
    if inv is not None:
        _emit_interacted_user_skip_log(
            "interacted_user_persist_skipped_invalid_username",
            account_id=str(account_id or ""),
            raw_username=str(username or ""),
            normalized_username=u,
            source_profile=str(source_profile or ""),
            reason=inv,
        )
        return {
            "ok": False,
            "error": "skipped_invalid_interacted_username",
            "username": u,
            "source_profile": source_profile,
        }
    sp_raw = _canonical_source_profile(source_profile)
    sp_col = sp_raw if sp_raw else None
    row = load_interacted_user(account_id, username, source_profile)
    body = {k: v for k, v in patch.items() if v is not None}
    _apply_interacted_user_row_attribution(row, body, sp_col)
    if "payload" in body and isinstance(body["payload"], dict):
        prev: dict[str, Any] = {}
        if row and isinstance(row.get("payload"), dict):
            prev = dict(row["payload"])
        prev.update(body["payload"])
        body["payload"] = prev
    body["updated_at"] = now
    out: dict[str, Any] = {"ok": False, "error": None}
    try:
        if row and row.get("id"):
            _request_json_tolerate_unknown_columns(
                "PATCH",
                "ig_interacted_users",
                query={"id": f"eq.{row.get('id')}"},
                body=body,
                prefer_representation=False,
            )
        else:
            insert_body: dict[str, Any] = {
                "account_id": account_id,
                "username": u,
                "created_at": now,
                **body,
            }
            if sp_col is not None:
                insert_body["source_profile"] = sp_col
            try:
                _request_json_tolerate_unknown_columns(
                    "POST",
                    "ig_interacted_users",
                    body=insert_body,
                    prefer_representation=False,
                )
            except RuntimeError as e:
                msg = str(e)
                if " 409 " in msg or "23505" in msg:
                    row2 = load_interacted_user(account_id, username, source_profile)
                    if row2 and row2.get("id"):
                        _request_json_tolerate_unknown_columns(
                            "PATCH",
                            "ig_interacted_users",
                            query={"id": f"eq.{row2.get('id')}"},
                            body=body,
                            prefer_representation=False,
                        )
                    else:
                        raise
                else:
                    raise
        out["ok"] = True
        return out
    except RuntimeError as e:
        out["error"] = str(e)
        return out


def record_follow_interaction_outcome(
    account_id: str,
    username: str,
    source_profile: str,
    *,
    run_id: str | None,
    session_id: str | None,
    follow_ok: bool,
    skipped_tap: bool,
    follow_state_after: str | None,
    follow_status: str | None,
    failure_code: int | None = None,
    failure_reason: str | None = None,
) -> dict[str, Any]:
    u_gate = _canonical_interaction_username(username)
    inv_gate = _invalid_interacted_username_reason(u_gate)
    if inv_gate is not None:
        print(
            json.dumps(
                {
                    "level": "info",
                    "event": "follow_interaction_persist_skipped_invalid_username",
                    "account_id": str(account_id or ""),
                    "username": str(username or ""),
                    "normalized_username": u_gate,
                    "source_profile": str(source_profile or ""),
                    "reason": inv_gate,
                    "follow_ok": follow_ok,
                },
                ensure_ascii=False,
            )
        )
        return {"ok": False, "error": "skipped_invalid_interacted_username"}

    now = _utc_now_iso()
    payload_delta: dict[str, Any] = {}
    if follow_state_after is not None:
        payload_delta["follow_state_after"] = follow_state_after
    if skipped_tap:
        payload_delta["skipped_tap"] = True
    if failure_code is not None:
        payload_delta["failure_code"] = failure_code
    if failure_reason:
        payload_delta["failure_reason"] = failure_reason

    if follow_ok:
        fs = (follow_status or ("already_following" if skipped_tap else "following"))[:200]
        payload_delta = {
            **payload_delta,
            "interaction_lifecycle_state": "active_following",
            "interaction_status": "success",
        }
        patch: dict[str, Any] = {
            "interaction_type": "follow",
            "was_successful": True,
            "follow_status": fs,
            "last_interaction_at": now,
            "payload": payload_delta,
            "interaction_status": "success",
            "interaction_lifecycle_state": "active_following",
            "followed": True,
            "unfollowed": False,
        }
        if not skipped_tap:
            patch["followed_at"] = now
        if str(fs).lower() == "requested":
            patch["follow_requested_at"] = now
        if run_id:
            patch["run_id"] = str(run_id)
        if session_id:
            patch["last_session_id"] = str(session_id)
    else:
        payload_delta = {
            **payload_delta,
            "interaction_lifecycle_state": "failed",
            "interaction_status": "failed",
        }
        patch = {
            "interaction_type": "follow",
            "was_successful": False,
            "last_interaction_at": now,
            "follow_status": (follow_status[:200] if follow_status else None),
            "payload": payload_delta,
            "interaction_status": "failed",
            "interaction_lifecycle_state": "failed",
            "followed": False,
            "skip_reason": (failure_reason or f"follow_failure_{failure_code}")[:500],
        }
        if run_id:
            patch["run_id"] = str(run_id)
        if session_id:
            patch["last_session_id"] = str(session_id)
    patch = {k: v for k, v in patch.items() if v is not None}
    mout = merge_interacted_user_row(account_id, username, source_profile, patch)
    if mout.get("ok") and follow_ok:
        _ev = (
            "follow_requested"
            if str(fs or "").strip().lower() == "requested"
            else "follow_verified"
        )
        record_interaction_event(
            account_id,
            username,
            source_profile,
            run_id=run_id,
            session_id=session_id,
            event_type=_ev,
            event_status="success",
            event_reason=None,
            payload={
                "follow_status": str(fs or ""),
                "skipped_tap": bool(skipped_tap),
                "follow_state_after": str(follow_state_after or ""),
            },
        )
    return mout


def record_interaction_skip_memory(
    account_id: str,
    username: str,
    source_profile: str,
    *,
    skip_reason: str,
    run_id: str | None = None,
    session_id: str | None = None,
    lifecycle_state: str = "skipped",
) -> dict[str, Any]:
    u_gate = _canonical_interaction_username(username)
    inv_gate = _invalid_interacted_username_reason(u_gate)
    if inv_gate is not None:
        print(
            json.dumps(
                {
                    "level": "info",
                    "event": "interaction_skip_memory_persist_skipped_invalid_username",
                    "account_id": str(account_id or ""),
                    "username": str(username or ""),
                    "normalized_username": u_gate,
                    "source_profile": str(source_profile or ""),
                    "reason": inv_gate,
                    "skip_reason": skip_reason,
                },
                ensure_ascii=False,
            )
        )
        return {"ok": False, "error": "skipped_invalid_interacted_username"}

    now = _utc_now_iso()
    patch: dict[str, Any] = {
        "interaction_type": "follow",
        "last_interaction_at": now,
        "interaction_lifecycle_state": lifecycle_state[:120],
        "interaction_status": "skipped",
        "skip_reason": (skip_reason or "")[:500],
        "was_successful": False,
        "payload": {
            "skip_reason_detail": skip_reason or "",
            "interaction_lifecycle_state": lifecycle_state[:120],
        },
    }
    if run_id:
        patch["run_id"] = str(run_id)
    if session_id:
        patch["last_session_id"] = str(session_id)
    return merge_interacted_user_row(account_id, username, source_profile, patch)


def record_interaction_event(
    account_id: str,
    username: str,
    source_profile: str,
    *,
    run_id: str | None = None,
    session_id: str | None = None,
    event_type: str,
    event_status: str = "success",
    event_reason: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Append-only ig_interaction_events row. Skips invalid usernames (same rules as ig_interacted_users).
    """
    u = _canonical_interaction_username(username)
    inv = _invalid_interacted_username_reason(u)
    if inv is not None:
        print(
            json.dumps(
                {
                    "level": "info",
                    "event": "interaction_event_persist_skipped_invalid_username",
                    "account_id": str(account_id or ""),
                    "username": str(username or ""),
                    "normalized_username": u,
                    "source_profile": str(source_profile or ""),
                    "reason": inv,
                    "event_type": str(event_type or ""),
                },
                ensure_ascii=False,
            )
        )
        return {"ok": False, "error": "skipped_invalid_interacted_username"}
    now = _utc_now_iso()
    sp = _canonical_source_profile(source_profile)
    body: dict[str, Any] = {
        "username": u,
        "event_type": str(event_type or "")[:200],
        "event_status": str(event_status or "success")[:80],
        "event_at": now,
        "created_at": now,
        "payload": dict(payload) if isinstance(payload, dict) else {},
    }
    if str(account_id or "").strip():
        body["account_id"] = str(account_id).strip()
    if run_id and str(run_id).strip():
        body["run_id"] = str(run_id).strip()
    if session_id and str(session_id).strip():
        body["session_id"] = str(session_id).strip()
    if sp:
        body["source_profile"] = sp
    if event_reason:
        body["event_reason"] = str(event_reason)[:500]
    out: dict[str, Any] = {"ok": False, "error": None}
    try:
        _request_json_tolerate_unknown_columns(
            "POST",
            "ig_interaction_events",
            body=body,
            prefer_representation=False,
        )
        out["ok"] = True
        return out
    except RuntimeError as e:
        out["error"] = str(e)
        return out


def record_mute_interaction_success(
    account_id: str,
    username: str,
    source_profile: str,
    *,
    run_id: str | None,
    session_id: str | None,
    muted_posts: bool,
    muted_stories: bool,
    mute_partial: bool,
    visual_candidate_id: str,
    timings_ms: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist mute outcome on ig_interacted_users + mute_success event (real username only)."""
    u_gate = _canonical_interaction_username(username)
    inv_gate = _invalid_interacted_username_reason(u_gate)
    if inv_gate is not None:
        print(
            json.dumps(
                {
                    "level": "info",
                    "event": "mute_interaction_persist_skipped_invalid_username",
                    "account_id": str(account_id or ""),
                    "username": str(username or ""),
                    "normalized_username": u_gate,
                    "source_profile": str(source_profile or ""),
                    "reason": inv_gate,
                },
                ensure_ascii=False,
            )
        )
        return {"ok": False, "error": "skipped_invalid_interacted_username"}
    now = _utc_now_iso()
    mute_delta: dict[str, Any] = {
        "last_mute": {
            "mute_partial": bool(mute_partial),
            "muted_posts": bool(muted_posts),
            "muted_stories": bool(muted_stories),
            "visual_candidate_id": str(visual_candidate_id or ""),
            "timings_ms": timings_ms or {},
        }
    }
    patch: dict[str, Any] = {
        "last_interaction_at": now,
        "last_muted_at": now,
        "muted_posts": bool(muted_posts),
        "muted_stories": bool(muted_stories),
        "payload": mute_delta,
    }
    if run_id:
        patch["run_id"] = str(run_id)
    if session_id:
        patch["last_session_id"] = str(session_id)
    mout = merge_interacted_user_row(account_id, username, source_profile, patch)
    if mout.get("ok"):
        record_interaction_event(
            account_id,
            username,
            source_profile,
            run_id=run_id,
            session_id=session_id,
            event_type="mute_success",
            event_status="success",
            event_reason="mute_partial" if mute_partial else None,
            payload={
                "mute_partial": bool(mute_partial),
                "muted_posts": bool(muted_posts),
                "muted_stories": bool(muted_stories),
                "visual_candidate_id": str(visual_candidate_id or ""),
                "timings_ms": timings_ms or {},
            },
        )
    return mout
