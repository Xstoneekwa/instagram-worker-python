"""Minimal Supabase REST client for worker control/logging."""

from __future__ import annotations

import json
import os
import re
from typing import Any
from urllib import error, parse, request
from datetime import datetime, timezone

from logs import log

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

    eligible_unfollow_log_payload: dict[str, Any] | None = None
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
            patch["followed_by_bot"] = True
            try:
                from unfollow_settings import (
                    compute_eligible_unfollow_at_iso,
                    load_unfollow_settings,
                )

                unfollow_settings = load_unfollow_settings(
                    str(account_id or ""),
                    ensure_row=False,
                )
                eligible_unfollow_at = compute_eligible_unfollow_at_iso(
                    now,
                    after_days=int(unfollow_settings.after_days),
                )
                if eligible_unfollow_at:
                    patch["eligible_unfollow_at"] = eligible_unfollow_at
                    eligible_unfollow_log_payload = {
                        "level": "info",
                        "event": "follow_eligible_unfollow_at_persisted",
                        "account_id": str(account_id or ""),
                        "username": str(username or ""),
                        "followed_at": now,
                        "unfollow_after_days": int(unfollow_settings.after_days),
                        "eligible_unfollow_at": eligible_unfollow_at,
                    }
                else:
                    raise ValueError("eligible_unfollow_at_empty")
            except Exception as e:
                log(
                    "warning",
                    "follow_eligible_unfollow_at_compute_failed",
                    account_id=str(account_id or ""),
                    username=str(username or ""),
                    followed_at=now,
                    error=str(e)[:300],
                )
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
        if eligible_unfollow_log_payload is not None:
            log(
                "info",
                "follow_eligible_unfollow_at_persisted",
                account_id=eligible_unfollow_log_payload.get("account_id"),
                username=eligible_unfollow_log_payload.get("username"),
                followed_at=eligible_unfollow_log_payload.get("followed_at"),
                unfollow_after_days=eligible_unfollow_log_payload.get(
                    "unfollow_after_days"
                ),
                eligible_unfollow_at=eligible_unfollow_log_payload.get(
                    "eligible_unfollow_at"
                ),
            )
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


def record_unfollow_interaction_outcome(
    account_id: str,
    username: str,
    *,
    run_id: str | None,
    session_id: str | None = None,
    unfollow_ok: bool,
    unfollow_mode_applied: str,
    interaction_row_id: str | None = None,
    failure_reason: str | None = None,
    allow_any_upsert: bool = False,
) -> dict[str, Any]:
    """Persist a real Unfollow action result on ig_interacted_users."""
    u_gate = _canonical_interaction_username(username)
    inv_gate = _invalid_interacted_username_reason(u_gate)
    if inv_gate is not None:
        log(
            "info",
            "unfollow_result_persist_failed",
            account_id=str(account_id or ""),
            username=str(username or ""),
            normalized_username=u_gate,
            reason=inv_gate,
        )
        return {"ok": False, "error": "skipped_invalid_interacted_username"}

    now = _utc_now_iso()
    row: dict[str, Any] | None = None
    rid = str(interaction_row_id or "").strip()
    if rid:
        rows = _request_json(
            "GET",
            "ig_interacted_users",
            query={
                "select": "*",
                "id": f"eq.{rid}",
                "account_id": f"eq.{str(account_id or '').strip()}",
                "limit": "1",
            },
        )
        if rows:
            row = rows[0]
    if row is None:
        row = load_interacted_user(account_id, username, "")
        rid = str((row or {}).get("id") or "").strip()
    if not rid and allow_any_upsert and str(unfollow_mode_applied or "") == "unfollow-any":
        log(
            "info",
            "unfollow_any_interaction_row_missing_before_persist",
            account_id=str(account_id or ""),
            username=str(username or ""),
            normalized_username=u_gate,
            unfollow_mode_applied=str(unfollow_mode_applied or ""),
        )
        upsert_out = merge_interacted_user_row(
            account_id,
            username,
            "",
            {
                "interaction_type": "unfollow",
                "followed_by_bot": False,
                "followed": True,
                "follow_status": "following",
                "interaction_lifecycle_state": "active_following",
                "unfollowed": False,
            },
        )
        if upsert_out.get("ok"):
            row = load_interacted_user(account_id, username, "")
            rid = str((row or {}).get("id") or "").strip()
            if rid:
                log(
                    "info",
                    "unfollow_any_interaction_row_upserted",
                    account_id=str(account_id or ""),
                    username=str(username or ""),
                    normalized_username=u_gate,
                    interaction_row_id=rid,
                    unfollow_mode_applied=str(unfollow_mode_applied or ""),
                )
            else:
                log(
                    "info",
                    "unfollow_any_interaction_row_upserted_but_id_missing",
                    account_id=str(account_id or ""),
                    username=str(username or ""),
                    normalized_username=u_gate,
                    unfollow_mode_applied=str(unfollow_mode_applied or ""),
                    upsert_ok=True,
                )
                log(
                    "info",
                    "unfollow_result_persist_failed",
                    account_id=str(account_id or ""),
                    username=str(username or ""),
                    normalized_username=u_gate,
                    reason="interaction_row_id_missing_after_any_upsert",
                )
                return {"ok": False, "error": "interaction_row_id_missing_after_any_upsert"}
        else:
            upsert_error = str(upsert_out.get("error") or "interaction_row_upsert_failed")[:500]
            log(
                "info",
                "unfollow_any_interaction_row_upsert_failed",
                account_id=str(account_id or ""),
                username=str(username or ""),
                normalized_username=u_gate,
                unfollow_mode_applied=str(unfollow_mode_applied or ""),
                error=upsert_error,
                upsert_ok=bool(upsert_out.get("ok")),
            )
            log(
                "info",
                "unfollow_result_persist_failed",
                account_id=str(account_id or ""),
                username=str(username or ""),
                normalized_username=u_gate,
                reason="interaction_row_not_found",
                upsert_error=upsert_error,
            )
            return {"ok": False, "error": upsert_error}
    if not rid:
        log(
            "info",
            "unfollow_result_persist_failed",
            account_id=str(account_id or ""),
            username=str(username or ""),
            reason="interaction_row_not_found",
        )
        return {"ok": False, "error": "interaction_row_not_found"}

    attempts = int((row or {}).get("unfollow_attempts") or 0) + 1
    patch: dict[str, Any] = {
        "unfollow_attempts": attempts,
        "last_unfollow_attempt_at": now,
        "unfollow_result": "success" if unfollow_ok else "failed",
        "unfollow_mode_applied": str(unfollow_mode_applied or "")[:120],
    }
    if run_id:
        patch["run_id"] = str(run_id)
        patch["last_run_id"] = str(run_id)
    if session_id:
        patch["last_session_id"] = str(session_id)
    if unfollow_ok:
        patch.update(
            {
                "unfollowed_at": now,
                "unfollowed": True,
                "followed": False,
                "follow_status": "unfollowed",
                "interaction_lifecycle_state": "unfollowed_completed",
                "interaction_status": "success",
                "last_interaction_at": now,
                "was_successful": True,
                "unfollow_skip_reason": None,
            }
        )
    else:
        patch.update(
            {
                "interaction_status": "failed",
                "unfollow_skip_reason": str(failure_reason or "unfollow_failed")[:500],
            }
        )

    try:
        _request_json_tolerate_unknown_columns(
            "PATCH",
            "ig_interacted_users",
            query={"id": f"eq.{rid}"},
            body=patch,
            prefer_representation=False,
        )
        if str(unfollow_mode_applied or "") == "unfollow-any":
            log(
                "info",
                "unfollow_any_persisted",
                account_id=str(account_id or ""),
                username=u_gate,
                interaction_row_id=rid,
                unfollow_ok=bool(unfollow_ok),
                unfollow_result=patch["unfollow_result"],
                unfollow_attempts=attempts,
                unfollow_mode_applied=str(unfollow_mode_applied or ""),
            )
        log(
            "info",
            "unfollow_result_persisted",
            account_id=str(account_id or ""),
            username=u_gate,
            interaction_row_id=rid,
            unfollow_ok=bool(unfollow_ok),
            unfollow_result=patch["unfollow_result"],
            unfollow_attempts=attempts,
        )
        return {"ok": True, "interaction_row_id": rid, "unfollow_attempts": attempts}
    except RuntimeError as exc:
        log(
            "info",
            "unfollow_result_persist_failed",
            account_id=str(account_id or ""),
            username=u_gate,
            interaction_row_id=rid,
            error=str(exc)[:500],
        )
        return {"ok": False, "error": str(exc)}


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


def record_post_like_interaction_success(
    account_id: str,
    username: str,
    source_profile: str,
    *,
    run_id: str | None,
    session_id: str | None,
    liked_count: int,
    target_count: int,
    attempted_count: int,
    skipped_already_liked_count: int,
    phase_outcome: str,
    post_like_mode: str,
    visual_candidate_id: str,
    timings_ms: dict[str, Any] | None = None,
    per_post: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Persist post-like outcome on ig_interacted_users + aggregated post_like_success event."""
    u_gate = _canonical_interaction_username(username)
    inv_gate = _invalid_interacted_username_reason(u_gate)
    if inv_gate is not None:
        print(
            json.dumps(
                {
                    "level": "info",
                    "event": "post_like_interaction_persist_skipped_invalid_username",
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
    liked_n = max(0, int(liked_count or 0))
    if liked_n <= 0:
        return {"ok": False, "error": "skipped_zero_liked_count"}
    now = _utc_now_iso()
    row = load_interacted_user(account_id, username, source_profile)
    posts_prev = 0
    if row:
        try:
            posts_prev = int(row.get("posts_liked_count") or 0)
        except (TypeError, ValueError):
            posts_prev = 0
    last_post_likes: dict[str, Any] = {
        "status": "complete" if str(phase_outcome or "") == "success" else str(phase_outcome or ""),
        "target_count": int(target_count or 0),
        "attempted_count": int(attempted_count or 0),
        "liked_count": liked_n,
        "skipped_already_liked_count": int(skipped_already_liked_count or 0),
        "post_like_mode": str(post_like_mode or ""),
        "visual_candidate_id": str(visual_candidate_id or ""),
        "timings_ms": timings_ms or {},
    }
    if per_post:
        last_post_likes["per_post"] = list(per_post)
    patch: dict[str, Any] = {
        "last_interaction_at": now,
        "posts_liked_count": posts_prev + liked_n,
        "payload": {"last_post_likes": last_post_likes},
    }
    if run_id:
        patch["run_id"] = str(run_id)
    if session_id:
        patch["last_session_id"] = str(session_id)
    mout = merge_interacted_user_row(account_id, username, source_profile, patch)
    if mout.get("ok"):
        ev_status = "success"
        if str(phase_outcome or "") == "partial_success":
            ev_status = "partial"
        record_interaction_event(
            account_id,
            username,
            source_profile,
            run_id=run_id,
            session_id=session_id,
            event_type="post_like_success",
            event_status=ev_status,
            event_reason=None if ev_status == "success" else str(phase_outcome or "")[:200],
            payload={
                "target_count": int(target_count or 0),
                "attempted_count": int(attempted_count or 0),
                "liked_count": liked_n,
                "skipped_already_liked_count": int(skipped_already_liked_count or 0),
                "post_like_mode": str(post_like_mode or ""),
                "visual_candidate_id": str(visual_candidate_id or ""),
                "timings_ms": timings_ms or {},
                "per_post": list(per_post) if per_post else [],
            },
        )
    return mout


def call_rpc(function_name: str, params: dict[str, Any] | None = None) -> Any:
    """Invoke a Postgres RPC via PostgREST (service role)."""
    fn = str(function_name or "").strip()
    if not fn:
        raise ValueError("function_name is required")
    base = _base_url()
    key = _service_key()
    url = f"{base}/rest/v1/rpc/{fn}"
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    body = params if params is not None else {}
    data = json.dumps(body, separators=(",", ":")).encode("utf-8")
    req = request.Request(url=url, method="POST", headers=headers, data=data)
    try:
        with request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            if not raw:
                return None
            return json.loads(raw.decode("utf-8"))
    except error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Supabase RPC {fn} failed: {e.code} {detail}") from e
    except error.URLError as e:
        raise RuntimeError(f"Supabase RPC request error: {e}") from e


def ensure_account_dm_settings(account_id: str) -> dict[str, Any]:
    """
    Ensure ig_account_dm_settings exists. Does not enable welcome or outreach.
    """
    aid = str(account_id or "").strip()
    if not aid:
        raise ValueError("account_id is required")
    rows = _request_json(
        "GET",
        "ig_account_dm_settings",
        query={"select": "*", "account_id": f"eq.{aid}", "limit": "1"},
    )
    if rows:
        return rows[0]
    now = _utc_now_iso()
    created = _request_json(
        "POST",
        "ig_account_dm_settings",
        body={
            "account_id": aid,
            "welcome_enabled": False,
            "outreach_enabled": False,
            "created_at": now,
            "updated_at": now,
        },
        prefer_representation=True,
    )
    if not created:
        raise RuntimeError("ensure_account_dm_settings: empty insert response")
    return created[0]


def upsert_account_follower_seen_baseline(
    account_id: str,
    follower_username: str,
    *,
    scan_run_id: str | None = None,
) -> dict[str, Any] | None:
    """RPC upsert_account_follower_seen with is_baseline_scan=true."""
    row = call_rpc(
        "upsert_account_follower_seen",
        {
            "p_account_id": str(account_id),
            "p_follower_username": str(follower_username),
            "p_source_scan_run_id": scan_run_id,
            "p_is_baseline_scan": True,
        },
    )
    if isinstance(row, dict):
        return row
    if isinstance(row, list) and row:
        first = row[0]
        return first if isinstance(first, dict) else None
    return None


def _normalize_follower_username(username: str) -> str:
    return str(username or "").strip().lstrip("@").lower()


def get_account_dm_settings(account_id: str) -> dict[str, Any] | None:
    """Load ig_account_dm_settings row (no insert)."""
    aid = str(account_id or "").strip()
    if not aid:
        return None
    rows = _request_json(
        "GET",
        "ig_account_dm_settings",
        query={"select": "*", "account_id": f"eq.{aid}", "limit": "1"},
    )
    if rows and isinstance(rows, list):
        return rows[0]
    return None


def parse_utc_iso_timestamp(raw: Any) -> datetime | None:
    """Parse ISO-8601 timestamp to timezone-aware UTC datetime."""
    if not raw:
        return None
    try:
        ts = str(raw).replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def get_account_unfollow_settings(account_id: str) -> dict[str, Any] | None:
    """Load ig_account_unfollow_settings row (no insert)."""
    aid = str(account_id or "").strip()
    if not aid:
        return None
    rows = _request_json(
        "GET",
        "ig_account_unfollow_settings",
        query={"select": "*", "account_id": f"eq.{aid}", "limit": "1"},
    )
    if rows and isinstance(rows, list):
        return rows[0]
    return None


def get_account_follow_settings(account_id: str) -> dict[str, Any] | None:
    """Load ig_account_follow_settings row (no insert)."""
    aid = str(account_id or "").strip()
    if not aid:
        return None
    rows = _request_json(
        "GET",
        "ig_account_follow_settings",
        query={"select": "*", "account_id": f"eq.{aid}", "limit": "1"},
    )
    if rows and isinstance(rows, list):
        return rows[0]
    return None


def ensure_account_follow_settings(account_id: str) -> dict[str, Any]:
    """Ensure ig_account_follow_settings exists. Defaults to skipping private profiles."""
    aid = str(account_id or "").strip()
    if not aid:
        raise ValueError("account_id is required")
    existing = get_account_follow_settings(aid)
    if existing:
        return existing
    now = _utc_now_iso()
    created = _request_json(
        "POST",
        "ig_account_follow_settings",
        body={
            "account_id": aid,
            "dont_follow_private_accounts": True,
            "created_at": now,
            "updated_at": now,
        },
        prefer_representation=True,
    )
    if not created:
        raise RuntimeError("ensure_account_follow_settings: empty insert response")
    return created[0]


def ensure_account_unfollow_settings(account_id: str) -> dict[str, Any]:
    """
    Ensure ig_account_unfollow_settings exists with package defaults (Growth/Pro/Premium).
    Does not enable unfollow by default.
    """
    aid = str(account_id or "").strip()
    if not aid:
        raise ValueError("account_id is required")
    existing = get_account_unfollow_settings(aid)
    if existing:
        return existing
    now = _utc_now_iso()
    snap = {
        "unfollow_mode": "unfollow",
        "unfollow_after_days": 3,
        "unfollow_per_session_limit": 50,
        "unfollow_per_day_limit": 200,
        "unfollow_sort_mode": "default",
        "source": "package_default_growth_pro_premium",
    }
    created = _request_json(
        "POST",
        "ig_account_unfollow_settings",
        body={
            "account_id": aid,
            "unfollow_enabled": False,
            "unfollow_only": False,
            "do_unfollow_first": False,
            "unfollow_after_days": 3,
            "unfollow_mode": "unfollow",
            "unfollow_sort_mode": "default",
            "unfollow_per_session_limit": 50,
            "unfollow_per_day_limit": 200,
            "package_default_snapshot": snap,
            "created_at": now,
            "updated_at": now,
        },
        prefer_representation=True,
    )
    if not created:
        raise RuntimeError("ensure_account_unfollow_settings: empty insert response")
    return created[0]


def fetch_unfollow_strict_candidate_rows(
    account_id: str,
    *,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """
    Load ig_interacted_users rows that may qualify for strict unfollow modes (DB pre-filter).
    Final eligibility (delay, lifecycle, followback) is applied in unfollow_eligibility_engine.
    """
    aid = str(account_id or "").strip()
    if not aid:
        return []
    cap = max(1, min(int(limit), 2000))
    rows = _request_json(
        "GET",
        "ig_interacted_users",
        query={
            "select": "*",
            "account_id": f"eq.{aid}",
            "followed_by_bot": "eq.true",
            "followed_at": "not.is.null",
            "unfollowed_at": "is.null",
            "whitelist_protected": "eq.false",
            "follow_status": "eq.following",
            "order": "eligible_unfollow_at.asc.nullslast,followed_at.asc",
            "limit": str(cap),
        },
    )
    if not rows or not isinstance(rows, list):
        return []
    return [r for r in rows if isinstance(r, dict)]


def fetch_visible_unfollow_eligibility_rows(
    account_id: str,
    usernames: list[str],
) -> dict[str, dict[str, Any]]:
    """
    Batch lookup ig_interacted_users rows for visible Following-list usernames.
    Final eligibility remains in unfollow_eligibility_engine.
    """
    aid = str(account_id or "").strip()
    keys: list[str] = []
    seen: set[str] = set()
    for raw in usernames:
        key = _canonical_interaction_username(str(raw or ""))
        if not key or key in seen:
            continue
        if _invalid_interacted_username_reason(key) is not None:
            continue
        seen.add(key)
        keys.append(key)

    if not aid or not keys:
        return {}

    # Viewport batches are small (typically 7-20). Cap defensively to avoid
    # accidentally turning visible matching into a broad table scan.
    keys = keys[:50]
    rows = _request_json(
        "GET",
        "ig_interacted_users",
        query={
            # Viewport batches are tiny; select all avoids brittle failures when
            # optional rollout columns are absent/present across environments.
            "select": "*",
            "account_id": f"eq.{aid}",
            "username": f"in.({','.join(keys)})",
            "limit": str(len(keys)),
        },
    )
    out: dict[str, dict[str, Any]] = {}
    if not rows or not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = _canonical_interaction_username(str(row.get("username") or ""))
        if key:
            out[key] = row
    return out


def fetch_followers_by_usernames(
    account_id: str,
    usernames: list[str],
) -> dict[str, dict[str, Any]]:
    """
    Batch lookup ig_account_followers for handles on current screen.
    Returns map follower_username_normalized -> row.
    """
    aid = str(account_id or "").strip()
    keys = []
    seen: set[str] = set()
    for raw in usernames:
        k = _normalize_follower_username(raw)
        if k and k not in seen:
            seen.add(k)
            keys.append(k)
    if not aid or not keys:
        return {}
    in_clause = ",".join(keys)
    rows = _request_json(
        "GET",
        "ig_account_followers",
        query={
            "select": "id,follower_username,follower_username_normalized,baseline_existing,welcome_dm_status,skip_reason,last_seen_at",
            "account_id": f"eq.{aid}",
            "follower_username_normalized": f"in.({in_clause})",
        },
    )
    out: dict[str, dict[str, Any]] = {}
    if not rows or not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        nk = str(row.get("follower_username_normalized") or "").strip().lower()
        if nk:
            out[nk] = row
    return out


def upsert_account_follower_seen_scan(
    account_id: str,
    follower_username: str,
    *,
    scan_run_id: str | None = None,
) -> dict[str, Any] | None:
    """RPC upsert_account_follower_seen with is_baseline_scan=false (welcome scan path)."""
    row = call_rpc(
        "upsert_account_follower_seen",
        {
            "p_account_id": str(account_id),
            "p_follower_username": str(follower_username),
            "p_source_scan_run_id": scan_run_id,
            "p_is_baseline_scan": False,
        },
    )
    if isinstance(row, dict):
        return row
    if isinstance(row, list) and row:
        first = row[0]
        return first if isinstance(first, dict) else None
    return None


def persist_welcome_scan_anchor_gap(
    account_id: str,
    follower_username: str,
    *,
    scan_run_id: str | None = None,
) -> dict[str, Any] | None:
    """
    Post-anchor unknown: remember follower without Welcome eligibility.
    welcome_dm_status=skipped, skip_reason=baseline_anchor_gap, baseline_existing=false.
    """
    row = upsert_account_follower_seen_scan(
        account_id,
        follower_username,
        scan_run_id=scan_run_id,
    )
    if not row or not row.get("id"):
        return row
    fid = str(row["id"])
    now = _utc_now_iso()
    patched = _request_json(
        "PATCH",
        "ig_account_followers",
        query={"id": f"eq.{fid}"},
        body={
            "welcome_dm_status": "skipped",
            "skip_reason": "baseline_anchor_gap",
            "baseline_existing": False,
            "updated_at": now,
        },
        prefer_representation=True,
    )
    if patched and isinstance(patched, list) and patched:
        return patched[0]
    return row


def enqueue_welcome_dm_job_if_eligible(
    account_id: str,
    follower_username: str,
    *,
    scan_run_id: str | None = None,
    template_id: str | None = None,
    message_body: str | None = None,
    priority: int = 10,
) -> dict[str, Any] | None:
    """RPC enqueue_welcome_dm_job_if_eligible (no DM send). Returns job row or None."""
    row = call_rpc(
        "enqueue_welcome_dm_job_if_eligible",
        {
            "p_account_id": str(account_id),
            "p_follower_username": str(follower_username),
            "p_source_scan_run_id": scan_run_id,
            "p_message_body": message_body,
            "p_template_id": template_id,
            "p_priority": int(priority),
        },
    )
    if isinstance(row, dict):
        return row
    if isinstance(row, list) and row:
        first = row[0]
        return first if isinstance(first, dict) else None
    return None


def is_valid_dm_job_row(row: dict[str, Any] | None) -> bool:
    """True when row looks like a claimed ig_dm_jobs record (non-empty UUID id)."""
    return bool(row) and bool(str(row.get("id") or "").strip())


def _parse_rpc_job_row(row: Any) -> dict[str, Any] | None:
    parsed: dict[str, Any] | None = None
    if isinstance(row, dict):
        parsed = row
    elif isinstance(row, list) and row:
        first = row[0]
        parsed = first if isinstance(first, dict) else None
    if not is_valid_dm_job_row(parsed):
        return None
    return parsed


def claim_next_dm_job(
    account_id: str,
    reserved_by: str,
    *,
    dm_type: str | None = None,
) -> dict[str, Any] | None:
    """RPC claim_next_dm_job: pending → reserved."""
    params: dict[str, Any] = {
        "p_account_id": str(account_id),
        "p_reserved_by": str(reserved_by or "").strip(),
    }
    if dm_type:
        params["p_dm_type"] = str(dm_type)
    return _parse_rpc_job_row(call_rpc("claim_next_dm_job", params))


def claim_dm_job_by_id(
    account_id: str,
    job_id: str,
    reserved_by: str,
) -> dict[str, Any] | None:
    """
    Reserve a specific pending job (test harness). Atomic via status filter on PATCH.
    """
    aid = str(account_id or "").strip()
    jid = str(job_id or "").strip()
    rb = str(reserved_by or "").strip()
    if not aid or not jid or not rb:
        return None
    now = _utc_now_iso()
    rows = _request_json(
        "PATCH",
        "ig_dm_jobs",
        query={
            "id": f"eq.{jid}",
            "account_id": f"eq.{aid}",
            "status": "eq.pending",
        },
        body={
            "status": "reserved",
            "reserved_at": now,
            "reserved_by": rb,
            "updated_at": now,
        },
        prefer_representation=True,
    )
    if rows and isinstance(rows, list) and rows:
        return rows[0]
    return None


def get_dm_job_by_id(job_id: str) -> dict[str, Any] | None:
    jid = str(job_id or "").strip()
    if not jid:
        return None
    rows = _request_json(
        "GET",
        "ig_dm_jobs",
        query={"select": "*", "id": f"eq.{jid}", "limit": "1"},
    )
    if rows and isinstance(rows, list) and rows:
        return rows[0]
    return None


def mark_dm_job_running(job_id: str) -> dict[str, Any] | None:
    return _parse_rpc_job_row(
        call_rpc("mark_dm_job_running", {"p_job_id": str(job_id)})
    )


def release_dm_job_after_dry_run(
    job_id: str,
    *,
    thread_state: str,
    sendable: bool,
    skip_reason_candidate: str | None = None,
    metadata_patch: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """RPC release_dm_job_after_dry_run: running/reserved → pending + dry-run metadata."""
    return _parse_rpc_job_row(
        call_rpc(
            "release_dm_job_after_dry_run",
            {
                "p_job_id": str(job_id),
                "p_thread_state": str(thread_state or ""),
                "p_sendable": bool(sendable),
                "p_skip_reason_candidate": skip_reason_candidate,
                "p_metadata_patch": metadata_patch or {},
            },
        )
    )


def complete_dm_job(
    job_id: str,
    final_status: str,
    *,
    skip_reason: str | None = None,
    last_error: str | None = None,
    increment_attempt: bool = False,
    retry_delay_seconds: int | None = None,
    metadata_patch: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """RPC complete_dm_job: terminal or failed→pending retry."""
    params: dict[str, Any] = {
        "p_job_id": str(job_id),
        "p_final_status": str(final_status),
        "p_skip_reason": skip_reason,
        "p_last_error": last_error,
        "p_increment_attempt": bool(increment_attempt),
        "p_retry_delay_seconds": retry_delay_seconds,
        "p_metadata_patch": metadata_patch or {},
    }
    return _parse_rpc_job_row(call_rpc("complete_dm_job", params))


def mark_welcome_baseline_completed(
    account_id: str,
    *,
    scan_run_id: str | None = None,
) -> dict[str, Any] | None:
    """Set welcome_baseline_completed_at on ig_account_dm_settings."""
    aid = str(account_id or "").strip()
    if not aid:
        raise ValueError("account_id is required")
    ensure_account_dm_settings(aid)
    now = _utc_now_iso()
    patch: dict[str, Any] = {
        "welcome_baseline_completed_at": now,
        "updated_at": now,
    }
    if scan_run_id:
        patch["welcome_baseline_scan_run_id"] = str(scan_run_id)
    rows = _request_json(
        "PATCH",
        "ig_account_dm_settings",
        query={"account_id": f"eq.{aid}"},
        body=patch,
        prefer_representation=True,
    )
    if rows and isinstance(rows, list):
        return rows[0]
    return None
