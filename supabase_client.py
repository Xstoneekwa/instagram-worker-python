"""Minimal Supabase REST client for worker control/logging."""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import time
from typing import Any
from urllib import error, parse, request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from logs import log
import account_protection_lists

BUSINESS_TIMEZONE = ZoneInfo("Africa/Johannesburg")


def sast_business_day_window(now: datetime | None = None) -> tuple[str, datetime, datetime]:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    local_start = current.astimezone(BUSINESS_TIMEZONE).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    start = local_start.astimezone(timezone.utc)
    end = (local_start + timedelta(days=1)).astimezone(timezone.utc)
    return local_start.date().isoformat(), start, end


class SupabaseRestError(RuntimeError):
    """Structured Supabase REST failure without leaking secrets."""

    def __init__(
        self,
        reason: str,
        *,
        method: str = "",
        path: str = "",
        status: int | None = None,
        latency_ms: int | None = None,
        detail: str = "",
    ) -> None:
        self.reason = str(reason or "supabase_queue_read_failed")
        self.method = str(method or "")
        self.path = str(path or "")
        self.status = status
        self.latency_ms = latency_ms
        self.detail = str(detail or "")
        parts = [self.reason]
        if self.status is not None:
            parts.append(f"status={self.status}")
        if self.latency_ms is not None:
            parts.append(f"latency_ms={self.latency_ms}")
        if self.detail:
            parts.append(self.detail[:200])
        super().__init__(" ".join(parts))


def _rest_timeout_seconds() -> float:
    raw = (os.getenv("SUPABASE_REST_TIMEOUT_SECONDS") or "15").strip()
    try:
        return max(1.0, float(raw))
    except ValueError:
        return 15.0


def _rest_max_retries() -> int:
    raw = (os.getenv("SUPABASE_REST_MAX_RETRIES") or "2").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 2


def _rest_retry_backoff_seconds() -> float:
    raw = (os.getenv("SUPABASE_REST_RETRY_BACKOFF_SECONDS") or "0.75").strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 0.75


def _classify_request_error(exc: BaseException) -> str:
    if isinstance(exc, TimeoutError):
        return "supabase_rest_timeout"
    if isinstance(exc, socket.timeout):
        return "supabase_network_timeout"
    if isinstance(exc, error.HTTPError):
        if exc.code == 401:
            return "supabase_auth_401"
        if exc.code == 403:
            return "supabase_auth_403"
        if exc.code in {408, 504}:
            return "supabase_rest_timeout"
        if exc.code in {502, 503, 522, 524}:
            return "supabase_network_timeout"
    if isinstance(exc, error.URLError):
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, socket.gaierror):
            return "supabase_dns_failed"
        reason_text = str(reason).lower()
        if "timed out" in reason_text:
            return "supabase_network_timeout"
        if "ssl" in reason_text or "tls" in reason_text or "certificate" in reason_text:
            return "supabase_tls_failed"
    err_text = str(exc).lower()
    if "timed out" in err_text:
        return "supabase_rest_timeout"
    return "supabase_queue_read_failed"


def _classify_http_error(exc: error.HTTPError, detail: str) -> str:
    """Classify definitive PostgREST contract failures before retry policy."""
    base_reason = _classify_request_error(exc)
    detail_lower = str(detail or "").lower()
    if exc.code == 400 and (
        "pgrst204" in detail_lower
        or "could not find" in detail_lower and "column" in detail_lower
        or "column" in detail_lower and "does not exist" in detail_lower
        or "42703" in detail_lower
    ):
        return "supabase_schema_payload_incompatible"
    if exc.code in {404, 400} and (
        "pgrst202" in detail_lower
        or "could not find the function" in detail_lower
        or "function" in detail_lower and "does not exist" in detail_lower
    ):
        return "supabase_rpc_not_available"
    return base_reason


def _request_urlopen(
    req: request.Request,
    *,
    op: str,
    timeout: float | None = None,
    max_retries: int | None = None,
) -> bytes:
    """Issue a Supabase REST request with bounded retries and safe errors."""
    timeout_s = float(timeout if timeout is not None else _rest_timeout_seconds())
    retry_limit = (
        _rest_max_retries()
        if max_retries is None
        else max(0, int(max_retries))
    )
    backoff_s = _rest_retry_backoff_seconds()
    last_exc: BaseException | None = None

    for attempt in range(retry_limit + 1):
        started = time.monotonic()
        try:
            with request.urlopen(req, timeout=timeout_s) as resp:
                return resp.read()
        except error.HTTPError as exc:
            latency_ms = int((time.monotonic() - started) * 1000)
            detail = exc.read().decode("utf-8", errors="replace")
            reason = _classify_http_error(exc, detail)
            rest_exc = SupabaseRestError(
                reason,
                method=str(getattr(req, "method", "") or ""),
                path=op,
                status=int(exc.code),
                latency_ms=latency_ms,
                detail=detail[:200],
            )
            if reason in {
                "supabase_auth_401",
                "supabase_auth_403",
                "supabase_schema_payload_incompatible",
                "supabase_rpc_not_available",
            } or attempt >= retry_limit:
                raise rest_exc from exc
            last_exc = rest_exc
        except (error.URLError, TimeoutError, socket.timeout) as exc:
            latency_ms = int((time.monotonic() - started) * 1000)
            reason = _classify_request_error(exc)
            rest_exc = SupabaseRestError(
                reason,
                method=str(getattr(req, "method", "") or ""),
                path=op,
                latency_ms=latency_ms,
                detail=str(getattr(exc, "reason", exc))[:200],
            )
            if attempt >= retry_limit:
                raise rest_exc from exc
            last_exc = rest_exc

        log(
            "warning",
            "supabase_rest_retry",
            op=op,
            attempt=attempt + 1,
            max_retries=retry_limit,
            reason=getattr(last_exc, "reason", "supabase_queue_read_failed"),
            latency_ms=getattr(last_exc, "latency_ms", None),
        )
        if backoff_s > 0:
            time.sleep(backoff_s * (attempt + 1))

    if last_exc is not None:
        raise last_exc
    raise SupabaseRestError("supabase_queue_read_failed", path=op)

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
    prefer_resolution: str | None = None,
    request_timeout: float | None = None,
    max_retries: int | None = None,
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
    prefer_parts: list[str] = []
    if prefer_representation:
        prefer_parts.append("return=representation")
    if prefer_resolution:
        prefer_parts.append(prefer_resolution)
    if prefer_parts:
        headers["Prefer"] = ",".join(prefer_parts)

    data = None
    if body is not None:
        data = json.dumps(body, separators=(",", ":")).encode("utf-8")

    req = request.Request(url=url, method=method, headers=headers, data=data)
    try:
        raw = _request_urlopen(
            req,
            op=f"{method} {table}",
            timeout=request_timeout,
            max_retries=max_retries,
        )
        if not raw:
            return None
        return json.loads(raw.decode("utf-8"))
    except SupabaseRestError:
        raise
    except Exception as e:
        raise SupabaseRestError(
            _classify_request_error(e),
            method=method,
            path=table,
            detail=str(e)[:200],
        ) from e


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


def load_open_account_assignment_for_dispatch(account_id: str) -> dict[str, Any] | None:
    """Read the latest reserved/active assignment with device and app instance context."""
    aid = (account_id or "").strip()
    if not aid:
        raise ValueError("account_id is required")

    assignment_rows = _request_json(
        "GET",
        "account_assignments",
        query={
            "select": "*",
            "account_id": f"eq.{aid}",
            "status": "in.(reserved,active)",
            "order": "created_at.desc",
            "limit": "1",
        },
    ) or []
    if not assignment_rows:
        return None

    assignment = dict(assignment_rows[0])
    device_id = str(assignment.get("device_id") or "").strip()
    clone_id = str(assignment.get("clone_id") or "").strip()
    app_instance_id = str(assignment.get("app_instance_id") or "").strip()

    device: dict[str, Any] = {}
    if device_id:
        device_rows = _request_json(
            "GET",
            "phone_devices",
            query={"select": "*", "id": f"eq.{device_id}", "limit": "1"},
        ) or []
        if device_rows:
            device = dict(device_rows[0])

    clone: dict[str, Any] = {}
    if clone_id:
        clone_rows = _request_json(
            "GET",
            "phone_clones",
            query={"select": "*", "id": f"eq.{clone_id}", "limit": "1"},
        ) or []
        if clone_rows:
            clone = dict(clone_rows[0])

    app_instance: dict[str, Any] = {}
    if app_instance_id:
        app_instance_rows = _request_json(
            "GET",
            "phone_app_instances",
            query={"select": "*", "id": f"eq.{app_instance_id}", "limit": "1"},
        ) or []
        if app_instance_rows:
            app_instance = dict(app_instance_rows[0])

    assignment["phone_device"] = device
    assignment["phone_clone"] = clone
    assignment["phone_app_instance"] = app_instance
    return assignment


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


def _normalize_target_username(row: dict[str, Any]) -> str:
    return (
        row.get("normalized_username")
        or row.get("canonical_username")
        or row.get("target_username")
        or row.get("username")
        or ""
    ).strip().lstrip("@").lower()


def _is_eligible_follow_target_row(row: dict[str, Any]) -> tuple[bool, str]:
    status = str(row.get("status") or "").strip().lower()
    if status not in {"valid", "active"}:
        return False, "status_not_active"
    if status in {"rejected", "archived", "invalid", "deleted", "poor_performance", "paused"}:
        return False, "status_blocked"

    if "quality_status" in row:
        quality_status = str(row.get("quality_status") or "").strip().lower()
        if quality_status != "eligible":
            return False, "quality_not_eligible"

    if "verification_status" in row:
        verification_status = str(row.get("verification_status") or "").strip().lower()
        if verification_status and verification_status != "found":
            return False, "verification_not_found"

    if "archived_at" in row and row.get("archived_at"):
        return False, "archived"
    if "deleted_at" in row and row.get("deleted_at"):
        return False, "deleted"
    if "disabled" in row and bool(row.get("disabled")):
        return False, "disabled"
    # P1c records exhaustion/cooldown for observability only. Do not exclude
    # targets from runtime until a controlled P2 cooldown policy is activated.

    if not _normalize_target_username(row):
        return False, "missing_username"
    return True, "eligible"


def _eligible_follow_target_sort_key(item: dict[str, Any]) -> tuple[int, str, str]:
    raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
    if "last_used_at" in raw:
        last_used_at = raw.get("last_used_at")
        return (0 if not last_used_at else 1, str(last_used_at or ""), str(raw.get("created_at") or ""))
    return (0, str(raw.get("created_at") or ""), str(item.get("target_username") or ""))


def load_eligible_follow_targets(account_id: str, limit: int = 25) -> list[dict[str, Any]]:
    """
    P1a Follow source planner.

    Uses ig_targets as the source of truth for account_session Follow sources.
    Keeps load_pending_targets intact for legacy queue/DM flows.
    """
    query = {
        "select": "*",
        "account_id": f"eq.{account_id}",
        "status": "in.(valid,active)",
        "order": "created_at.asc",
        "limit": str(max(1, int(limit))),
    }
    rows = _request_json("GET", "ig_targets", query=query) or []
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        ok, reason = _is_eligible_follow_target_row(row)
        if not ok:
            log(
                "info",
                "follow_target_candidate_skipped",
                account_id=str(account_id or ""),
                target_id=str(row.get("id") or ""),
                target_username=_normalize_target_username(row),
                reason=reason,
                status=str(row.get("status") or ""),
                quality_status=str(row.get("quality_status") or ""),
                verification_status=str(row.get("verification_status") or ""),
            )
            continue
        username = _normalize_target_username(row)
        out.append(
            {
                "id": row.get("id"),
                "account_id": row.get("account_id") or account_id,
                "target_username": username,
                "source_profile_username": username,
                "status": row.get("status"),
                "quality_status": row.get("quality_status"),
                "verification_status": row.get("verification_status"),
                "selection_source": "ig_targets",
                "raw": row,
            }
        )
    out.sort(key=_eligible_follow_target_sort_key)
    return out


def load_account_follow_source_settings(account_id: str) -> dict[str, Any] | None:
    aid = str(account_id or "").strip()
    if not aid:
        raise ValueError("account_id is required")
    rows = _request_json(
        "GET",
        "account_follow_source_settings",
        query={
            "select": (
                "account_id,max_follows_per_target_per_run,max_targets_per_run,"
                "updated_at,updated_by,metadata"
            ),
            "account_id": f"eq.{aid}",
            "limit": "1",
        },
    ) or []
    if not rows:
        return None
    row = rows[0]
    return dict(row) if isinstance(row, dict) else None


def ensure_account_follow_source_settings(
    account_id: str,
    *,
    max_follows_per_target_per_run: int,
    max_targets_per_run: int,
    updated_by: str | None = None,
) -> dict[str, Any]:
    """Create account_follow_source_settings when missing. Does not update existing rows."""
    aid = str(account_id or "").strip()
    if not aid:
        raise ValueError("account_id is required")
    existing = load_account_follow_source_settings(aid)
    if existing:
        return existing
    now = _utc_now_iso()
    body: dict[str, Any] = {
        "account_id": aid,
        "max_follows_per_target_per_run": int(max_follows_per_target_per_run),
        "max_targets_per_run": int(max_targets_per_run),
        "updated_at": now,
        "metadata": {"source": "worker_follow_source_rotation_contract"},
    }
    if updated_by:
        body["updated_by"] = str(updated_by)
    created = _request_json(
        "POST",
        "account_follow_source_settings",
        body=body,
        prefer_representation=True,
    )
    if not created:
        raise RuntimeError("ensure_account_follow_source_settings: empty insert response")
    return created[0]


def upsert_account_follow_source_settings(
    account_id: str,
    *,
    max_follows_per_target_per_run: int,
    max_targets_per_run: int,
    updated_by: str | None = None,
) -> dict[str, Any]:
    """Explicit repair upsert for follow-source rotation contract values."""
    aid = str(account_id or "").strip()
    if not aid:
        raise ValueError("account_id is required")
    now = _utc_now_iso()
    body: dict[str, Any] = {
        "account_id": aid,
        "max_follows_per_target_per_run": int(max_follows_per_target_per_run),
        "max_targets_per_run": int(max_targets_per_run),
        "updated_at": now,
        "metadata": {"source": "worker_follow_source_rotation_contract_repair"},
    }
    if updated_by:
        body["updated_by"] = str(updated_by)
    rows = _request_json(
        "POST",
        "account_follow_source_settings",
        body=body,
        prefer_representation=True,
        prefer_resolution="resolution=merge-duplicates",
    )
    if not rows:
        raise RuntimeError("upsert_account_follow_source_settings: empty upsert response")
    return rows[0]


def load_target_by_id(target_id: str) -> dict[str, Any] | None:
    rows = _request_json(
        "GET",
        "ig_targets",
        query={"select": "*", "id": f"eq.{target_id}", "limit": "1"},
    )
    if not rows:
        return None
    return rows[0]


def _safe_metrics_target_id(target_id: str | None) -> str:
    return str(target_id or "").strip()


def _safe_metrics_reason(value: str | None) -> str:
    return str(value or "").strip()[:500]


def classify_follow_source_performance(
    *,
    follows_sent_count: int | None,
    followbacks_count: int | None = None,
    followback_ratio: float | int | None = None,
) -> dict[str, Any]:
    """
    P1c read-only performance classifier.

    Returns dashboard/API labels only; never changes target status, archive state,
    or quality flags. Ratio values are expressed as percentages.
    """
    try:
        follows_sent = max(0, int(follows_sent_count or 0))
    except (TypeError, ValueError):
        follows_sent = 0
    try:
        followbacks = max(0, int(followbacks_count or 0))
    except (TypeError, ValueError):
        followbacks = 0

    ratio: float | None
    if followback_ratio is not None:
        try:
            ratio = max(0.0, float(followback_ratio))
        except (TypeError, ValueError):
            ratio = None
    elif follows_sent > 0:
        ratio = (followbacks / follows_sent) * 100.0
    else:
        ratio = None

    if follows_sent <= 0:
        status = "pending"
        label = "Pending runtime data"
    elif follows_sent < 100:
        status = "insufficient_data"
        label = "Insufficient data"
    elif ratio is None:
        status = "pending"
        label = "Pending runtime data"
    elif ratio <= 8:
        status = "bad"
        label = "Bad"
    elif ratio < 15:
        status = "avg"
        label = "Avg"
    else:
        status = "good"
        label = "Good"

    return {
        "status": status,
        "label": label,
        "follows_sent_count": follows_sent,
        "followbacks_count": followbacks,
        "followback_ratio": ratio,
        "auto_archive": False,
        "review_candidate": bool(status == "bad"),
    }


def _patch_follow_source_target_metrics(
    target_id: str | None,
    body: dict[str, Any],
    *,
    account_id: str | None = None,
    event: str,
) -> dict[str, Any]:
    tid = _safe_metrics_target_id(target_id)
    if not tid:
        log(
            "warning",
            "missing_target_id_for_metrics",
            account_id=str(account_id or ""),
            metrics_event=event,
        )
        return {"ok": False, "error": "missing_target_id_for_metrics"}
    safe_body = {k: v for k, v in body.items() if v is not None}
    if not safe_body:
        return {"ok": True, "applied": "empty"}
    try:
        _request_json_tolerate_unknown_columns(
            "PATCH",
            "ig_targets",
            query={"id": f"eq.{tid}"},
            body=safe_body,
            prefer_representation=False,
        )
        return {"ok": True, "applied": "metrics_patch"}
    except RuntimeError as e:
        log(
            "warning",
            "follow_source_target_metrics_patch_failed",
            account_id=str(account_id or ""),
            target_id=tid,
            metrics_event=event,
            error=str(e)[:300],
        )
        return {"ok": False, "error": str(e)}


def _record_follow_source_metric_event(
    *,
    account_id: str | None,
    target_id: str | None,
    source_profile: str | None,
    event_type: str,
    event_status: str = "success",
    event_reason: str | None = None,
    run_id: str | None = None,
    candidate_username: str | None = None,
    outcome: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    username = str(candidate_username or source_profile or "target_metrics").strip() or "target_metrics"
    safe_payload: dict[str, Any] = {
        "target_id": _safe_metrics_target_id(target_id) or None,
        "source_profile": _canonical_source_profile(source_profile or ""),
        "outcome": str(outcome or event_status or "")[:120],
    }
    if isinstance(payload, dict):
        safe_payload.update({k: v for k, v in payload.items() if v is not None})
    return record_interaction_event(
        str(account_id or ""),
        username,
        source_profile or "",
        run_id=run_id,
        session_id=None,
        event_type=event_type,
        event_status=event_status,
        event_reason=_safe_metrics_reason(event_reason) or None,
        target_id=_safe_metrics_target_id(target_id) or None,
        payload=safe_payload,
    )


def _increment_follow_source_follows_sent_rpc(
    *,
    account_id: str | None,
    target_id: str,
    occurred_at: str,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "p_target_id": target_id,
        "p_last_successful_candidate_at": occurred_at,
    }
    if str(account_id or "").strip():
        body["p_account_id"] = str(account_id).strip()
    rows = _request_json(
        "POST",
        "rpc/increment_ig_target_follows_sent_p1c",
        body=body,
        prefer_representation=True,
    )
    row = rows[0] if isinstance(rows, list) and rows else {}
    return {
        "ok": True,
        "applied": "rpc_increment",
        "follows_sent_count": row.get("follows_sent_count") if isinstance(row, dict) else None,
        "followback_ratio": row.get("followback_ratio") if isinstance(row, dict) else None,
    }


def record_follow_source_target_selected(
    *,
    account_id: str,
    target_id: str | None,
    source_profile: str | None,
    run_id: str | None = None,
) -> dict[str, Any]:
    now = _utc_now_iso()
    out = _patch_follow_source_target_metrics(
        target_id,
        {
            "last_selected_at": now,
            "last_used_at": now,
            "metrics_updated_at": now,
            "updated_at": now,
        },
        account_id=account_id,
        event="target_selected",
    )
    _record_follow_source_metric_event(
        account_id=account_id,
        target_id=target_id,
        source_profile=source_profile,
        run_id=run_id,
        event_type="target_selected",
        payload={"metrics_patch_ok": bool(out.get("ok"))},
    )
    return out


def record_follow_source_follow_success(
    *,
    account_id: str,
    target_id: str | None,
    source_profile: str | None,
    candidate_username: str | None,
    run_id: str | None = None,
    outcome: str | None = None,
) -> dict[str, Any]:
    tid = _safe_metrics_target_id(target_id)
    if not tid:
        log(
            "warning",
            "missing_target_id_for_metrics",
            account_id=str(account_id or ""),
            source_profile=str(source_profile or ""),
            candidate_username=str(candidate_username or ""),
            metrics_event="follow_success",
        )
        return {"ok": False, "error": "missing_target_id_for_metrics"}
    now = _utc_now_iso()
    try:
        out = _increment_follow_source_follows_sent_rpc(
            account_id=account_id,
            target_id=tid,
            occurred_at=now,
        )
    except RuntimeError as e:
        log(
            "warning",
            "follow_source_target_metrics_rpc_increment_failed",
            account_id=str(account_id or ""),
            target_id=tid,
            error=str(e)[:300],
        )
        current = load_target_by_id(tid) or {}
        current_count = current.get("follows_sent_count")
        try:
            follows_sent_count = max(0, int(current_count or 0)) + 1
        except (TypeError, ValueError):
            follows_sent_count = 1
        out = _patch_follow_source_target_metrics(
            tid,
            {
                "follows_sent_count": follows_sent_count,
                "last_successful_candidate_at": now,
                "last_used_at": now,
                "metrics_updated_at": now,
                "updated_at": now,
            },
            account_id=account_id,
            event="follow_success",
        )
        out["follows_sent_count"] = follows_sent_count
    follows_sent_count = out.get("follows_sent_count")
    _record_follow_source_metric_event(
        account_id=account_id,
        target_id=tid,
        source_profile=source_profile,
        candidate_username=candidate_username,
        run_id=run_id,
        event_type="follow_sent",
        event_status="success",
        outcome=outcome or "follow_verified",
        payload={
            "follows_sent_count": follows_sent_count,
            "metrics_patch_ok": bool(out.get("ok")),
            "metrics_applied": out.get("applied"),
        },
    )
    return out


def record_follow_source_target_exhausted(
    *,
    account_id: str,
    target_id: str | None,
    source_profile: str | None,
    reason: str | None,
    run_id: str | None = None,
    outcome: str | None = None,
) -> dict[str, Any]:
    now = _utc_now_iso()
    safe_reason = _safe_metrics_reason(reason) or "target_exhausted"
    out = _patch_follow_source_target_metrics(
        target_id,
        {
            "last_exhausted_at": now,
            "exhaustion_reason": safe_reason,
            "metrics_updated_at": now,
            "updated_at": now,
        },
        account_id=account_id,
        event="target_exhausted",
    )
    _record_follow_source_metric_event(
        account_id=account_id,
        target_id=target_id,
        source_profile=source_profile,
        run_id=run_id,
        event_type="target_exhausted",
        event_status="success",
        event_reason=safe_reason,
        outcome=outcome or safe_reason,
        payload={"metrics_patch_ok": bool(out.get("ok"))},
    )
    return out


def record_follow_source_target_budget_reached(
    *,
    account_id: str,
    target_id: str | None,
    source_profile: str | None,
    run_id: str | None = None,
    target_follows_completed: int | None = None,
    target_budget: int | None = None,
) -> dict[str, Any]:
    now = _utc_now_iso()
    out = _patch_follow_source_target_metrics(
        target_id,
        {
            "last_used_at": now,
            "metrics_updated_at": now,
            "updated_at": now,
        },
        account_id=account_id,
        event="target_budget_reached",
    )
    _record_follow_source_metric_event(
        account_id=account_id,
        target_id=target_id,
        source_profile=source_profile,
        run_id=run_id,
        event_type="target_budget_reached",
        event_status="success",
        event_reason="target_budget_reached",
        outcome="target_budget_reached",
        payload={
            "target_follows_completed": target_follows_completed,
            "target_budget": target_budget,
            "metrics_patch_ok": bool(out.get("ok")),
        },
    )
    return out


def record_follow_source_runtime_error_non_exhaustion(
    *,
    account_id: str,
    target_id: str | None,
    source_profile: str | None,
    reason: str | None,
    run_id: str | None = None,
    outcome: str | None = None,
) -> dict[str, Any]:
    return _record_follow_source_metric_event(
        account_id=account_id,
        target_id=target_id,
        source_profile=source_profile,
        run_id=run_id,
        event_type="target_runtime_error_non_exhaustion",
        event_status="failed",
        event_reason=_safe_metrics_reason(reason) or "non_exhaustion_error",
        outcome=outcome or "non_exhaustion_error",
    )


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


def load_run_row(run_id: str) -> dict[str, Any] | None:
    """Load one ig_runs row (status + structured performance_summary) by id."""
    rid = str(run_id or "").strip()
    if not rid:
        return None
    rows = _request_json(
        "GET",
        "ig_runs",
        query={
            "select": "id,account_id,status,performance_summary,started_at,updated_at",
            "id": f"eq.{rid}",
            "limit": "1",
        },
    ) or []
    for row in rows:
        if isinstance(row, dict):
            return dict(row)
    return None


def insert_runtime_event(payload: dict[str, Any]) -> dict[str, Any]:
    """Insert one ORF runtime event via service-role REST."""
    row = _request_json(
        "POST",
        "runtime_events",
        body=dict(payload or {}),
        prefer_representation=True,
    )
    if not row:
        raise RuntimeError("Supabase insert_runtime_event returned empty response")
    return row[0]


def upsert_worker_heartbeat(payload: dict[str, Any]) -> dict[str, Any]:
    """Upsert one ORF worker heartbeat keyed by worker_id."""
    row = _request_json(
        "POST",
        "worker_heartbeats",
        query={"on_conflict": "worker_id"},
        body=dict(payload or {}),
        prefer_representation=True,
        prefer_resolution="resolution=merge-duplicates",
    )
    if not row:
        raise RuntimeError("Supabase upsert_worker_heartbeat returned empty response")
    return row[0]


def upsert_device_heartbeat(payload: dict[str, Any]) -> dict[str, Any]:
    """Upsert one ORF device heartbeat keyed by device_id."""
    row = _request_json(
        "POST",
        "device_heartbeats",
        query={"on_conflict": "device_id"},
        body=dict(payload or {}),
        prefer_representation=True,
        prefer_resolution="resolution=merge-duplicates",
    )
    if not row:
        raise RuntimeError("Supabase upsert_device_heartbeat returned empty response")
    return row[0]


def list_phone_devices_for_heartbeat() -> list[dict[str, Any]]:
    """Return the small phone_devices projection needed by local heartbeat publishers."""
    rows = _request_json(
        "GET",
        "phone_devices",
        query={
            "select": "id,name,device_name,adb_serial,host_machine,status,metadata",
            "order": "created_at.desc",
            "limit": "500",
        },
    )
    return rows or []


def _parse_rpc_account_incident_row(row: Any) -> dict[str, Any]:
    if isinstance(row, dict):
        return row
    if isinstance(row, list) and row:
        first = row[0]
        if isinstance(first, dict):
            return first
    raise RuntimeError("Supabase upsert_account_incident returned empty response")


def upsert_account_incident(payload: dict[str, Any]) -> dict[str, Any]:
    """Invoke ORF-3B-1 upsert_account_incident RPC (service role). Exceptions propagate."""
    body = dict(payload or {})
    rpc_params: dict[str, Any] = {
        "p_incident_type": body.get("incident_type") or body.get("p_incident_type"),
        "p_dedupe_key": body.get("dedupe_key") or body.get("p_dedupe_key"),
        "p_severity": body.get("severity", body.get("p_severity", "warning")),
        "p_status": body.get("status", body.get("p_status", "open")),
        "p_client_id": body.get("client_id", body.get("p_client_id")),
        "p_account_id": body.get("account_id", body.get("p_account_id")),
        "p_account_username": body.get("account_username", body.get("p_account_username")),
        "p_run_id": body.get("run_id", body.get("p_run_id")),
        "p_assignment_id": body.get("assignment_id", body.get("p_assignment_id")),
        "p_device_id": body.get("device_id", body.get("p_device_id")),
        "p_clone_id": body.get("clone_id", body.get("p_clone_id")),
        "p_source_event_id": body.get("source_event_id", body.get("p_source_event_id")),
        "p_source": body.get("source", body.get("p_source")),
        "p_reason": body.get("reason", body.get("p_reason")),
        "p_failure_reason": body.get("failure_reason", body.get("p_failure_reason")),
        "p_action_required": body.get("action_required", body.get("p_action_required")),
        "p_safe_client_message": body.get(
            "safe_client_message",
            body.get("p_safe_client_message"),
        ),
        "p_assistant_message": body.get("assistant_message", body.get("p_assistant_message")),
        "p_admin_message": body.get("admin_message", body.get("p_admin_message")),
        "p_metadata": body.get("metadata", body.get("p_metadata", {})),
    }
    row = call_rpc("upsert_account_incident", rpc_params)
    return _parse_rpc_account_incident_row(row)


def apply_instagram_action_restriction(payload: dict[str, Any]) -> dict[str, Any]:
    """Atomically create/enrich the restriction incident and apply its account hold."""
    body = dict(payload or {})
    row = _call_rpc(
        "apply_instagram_action_restriction_v1",
        {
            "p_account_id": body.get("account_id"),
            "p_account_username": body.get("account_username"),
            "p_run_id": body.get("run_id"),
            "p_request_id": body.get("request_id"),
            "p_stable_reason": body.get("stable_reason") or "instagram_action_rate_limit",
            "p_metadata_safe": body.get("metadata_safe") or {},
        },
        timeout_seconds=4.0,
        max_retries=0,
    )
    if isinstance(row, list):
        row = row[0] if row else {}
    if not isinstance(row, dict) or not row:
        raise RuntimeError("apply_instagram_action_restriction_v1 returned empty response")
    return row


def load_instagram_restriction_hold(account_id: str, incident_id: str | None = None) -> dict[str, Any] | None:
    query: dict[str, str] = {
        "select": "id,account_id,incident_id,status,stable_reason,previous_admin_lifecycle_status,verification_required_at,verified_cleared_at,metadata_safe",
        "account_id": f"eq.{str(account_id or '').strip()}",
        "status": "in.(active,verification_required)",
        "order": "created_at.desc",
        "limit": "1",
    }
    if incident_id:
        query["incident_id"] = f"eq.{str(incident_id).strip()}"
    rows = _request_json("GET", "instagram_account_restriction_holds", query=query) or []
    return dict(rows[0]) if rows and isinstance(rows[0], dict) else None


def release_instagram_action_restriction_hold(
    *, account_id: str, incident_id: str, run_id: str | None
) -> dict[str, Any]:
    row = call_rpc(
        "release_instagram_action_restriction_hold_v1",
        {
            "p_account_id": account_id,
            "p_incident_id": incident_id,
            "p_preflight_run_id": run_id,
        },
    )
    if isinstance(row, list):
        row = row[0] if row else {}
    if not isinstance(row, dict) or not row:
        raise RuntimeError("release_instagram_action_restriction_hold_v1 returned empty response")
    return row


def load_account_incidents_to_notify(
    *,
    statuses: list[str] | tuple[str, ...] | None = None,
    min_severity: str = "warning",
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Load active account incidents for the ORF-4 notification dispatcher."""
    allowed_statuses = {"open", "acknowledged"}
    severity_order = {"info": 0, "warning": 1, "error": 2, "critical": 3}
    selected_statuses = [
        str(status or "").strip().lower()
        for status in (statuses or ("open", "acknowledged"))
        if str(status or "").strip().lower() in allowed_statuses
    ]
    if not selected_statuses:
        selected_statuses = ["open", "acknowledged"]
    min_rank = severity_order.get(str(min_severity or "warning").strip().lower(), 1)
    selected_severities = [
        severity
        for severity, rank in severity_order.items()
        if rank >= min_rank
    ]
    safe_limit = max(1, int(limit or 20))
    # Final severity ordering is handled by the dispatcher; this query avoids
    # resolved/ignored rows and keeps the DB read bounded.
    rows = _request_json(
        "GET",
        "account_incidents",
        query={
            "select": (
                "id,created_at,updated_at,first_seen_at,last_seen_at,status,severity,"
                "incident_type,dedupe_key,occurrence_count,client_id,account_id,"
                "account_username,run_id,assignment_id,device_id,clone_id,source,"
                "reason,failure_reason,action_required,safe_client_message,"
                "assistant_message,admin_message,metadata"
            ),
            "status": f"in.({','.join(selected_statuses)})",
            "severity": f"in.({','.join(selected_severities)})",
            "order": "last_seen_at.desc",
            "limit": str(safe_limit),
        },
    ) or []
    return [dict(row) for row in rows if isinstance(row, dict)]


def _postgrest_in_values(values: list[str]) -> str:
    quoted = []
    for value in values:
        cleaned = str(value or "").strip()
        if not cleaned:
            continue
        quoted.append('"' + cleaned.replace("\\", "\\\\").replace('"', '\\"') + '"')
    return "in.(" + ",".join(quoted) + ")"


def load_existing_incident_notifications_by_delivery_keys(
    delivery_keys: list[str],
) -> dict[str, dict[str, Any]]:
    """Return existing delivery rows keyed by delivery_key."""
    keys = []
    for key in delivery_keys:
        cleaned = str(key or "").strip()
        if cleaned and cleaned not in keys:
            keys.append(cleaned)
    if not keys:
        return {}
    rows = _request_json(
        "GET",
        "account_incident_notifications",
        query={
            "select": "*",
            "delivery_key": _postgrest_in_values(keys),
        },
    ) or []
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        if isinstance(row, dict):
            delivery_key = str(row.get("delivery_key") or "").strip()
            if delivery_key:
                out[delivery_key] = dict(row)
    return out


def create_account_incident_notification(payload: dict[str, Any]) -> dict[str, Any]:
    """Insert one ORF-4 account incident notification audit row."""
    row = _request_json(
        "POST",
        "account_incident_notifications",
        body=dict(payload or {}),
        prefer_representation=True,
    )
    if not row:
        raise RuntimeError("Supabase create_account_incident_notification returned empty response")
    return row[0]


def update_account_incident_notification(
    notification_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Update one ORF-4 account incident notification audit row."""
    nid = str(notification_id or "").strip()
    if not nid:
        raise ValueError("notification_id is required")
    row = _request_json(
        "PATCH",
        "account_incident_notifications",
        query={"id": f"eq.{nid}"},
        body=dict(payload or {}),
        prefer_representation=True,
    )
    if not row:
        raise RuntimeError("Supabase update_account_incident_notification returned empty response")
    return row[0]


def load_incident_notification_channel_settings(
    channels: list[str] | tuple[str, ...] | None = None,
) -> dict[str, dict[str, Any]]:
    """Load canonical incident notification channel settings (service-role only)."""
    selected = [
        str(channel or "").strip().lower()
        for channel in (channels or ("slack", "discord"))
        if str(channel or "").strip().lower() in {"slack", "discord"}
    ]
    if not selected:
        selected = ["slack", "discord"]
    rows = _request_json(
        "GET",
        "incident_notification_channel_settings",
        query={
            "select": "channel,enabled,configured,webhook_ciphertext,updated_at,metadata",
            "channel": f"in.({','.join(selected)})",
        },
    )
    out: dict[str, dict[str, Any]] = {}
    for row in rows or []:
        channel = str(row.get("channel") or "").strip().lower()
        if channel:
            out[channel] = dict(row)
    return out


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
    session_counters = (
        performance_summary.get("session_counters")
        if isinstance(performance_summary, dict)
        else None
    )
    body: dict[str, Any] = {
        "status": status,
        "totals": totals,
        "performance_summary": performance_summary,
        "total_targets": int(totals.get("total", 0)),
        "updated_at": now,
    }
    if isinstance(session_counters, dict):
        if session_counters.get("follows") is not None:
            body["total_follow"] = max(0, int(session_counters.get("follows") or 0))
        if session_counters.get("likes") is not None:
            body["total_like"] = max(0, int(session_counters.get("likes") or 0))
    if status == "completed":
        body["finished_at"] = now
        body["completed_at"] = now
    elif status in {"failed", "stopped", "canceled"}:
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


def _safe_uuid_text(value: Any) -> str:
    text = str(value or "").strip()
    if re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", text):
        return text
    return ""


def _interaction_type_from_event_type(event_type: str | None) -> str:
    event = str(event_type or "").strip().lower()
    if "unfollow" in event:
        return "unfollow"
    if "followback" in event:
        return "followback"
    if "follow" in event:
        return "follow"
    if "post_like" in event or "like" in event:
        return "like"
    if "dm" in event:
        return "dm"
    if "story" in event:
        return "story_view"
    if "profile_visit" in event:
        return "profile_visit"
    return event[:80] or "unknown"


def _safe_interaction_evidence_summary(
    *,
    action_type: str,
    username: str,
    source_profile: str,
    run_id: str | None = None,
) -> str:
    parts = [
        f"{str(action_type or 'interaction').replace('_', ' ').title()}",
        f"for @{_canonical_interaction_username(username)}",
    ]
    src = _canonical_source_profile(source_profile)
    if src:
        parts.append(f"via CT @{src}")
    if run_id and str(run_id).strip():
        parts.append("during recorded run")
    return " ".join(parts)[:500]


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
        body.setdefault("source_target_username", source_profile_canonical)
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
    if "metadata_safe" in body and isinstance(body["metadata_safe"], dict):
        prev_meta: dict[str, Any] = {}
        if row and isinstance(row.get("metadata_safe"), dict):
            prev_meta = dict(row["metadata_safe"])
        prev_meta.update(body["metadata_safe"])
        body["metadata_safe"] = prev_meta
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
    target_id: str | None = None,
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
    safe_target_id = _safe_uuid_text(target_id)
    if target_id:
        payload_delta["target_id"] = str(target_id)
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
        evidence_summary = _safe_interaction_evidence_summary(
            action_type="follow",
            username=username,
            source_profile=source_profile,
            run_id=run_id,
        )
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
            "source_target_username": _canonical_source_profile(source_profile) or None,
            "evidence_source": "worker_follow_outcome",
            "evidence_confidence": "high" if safe_target_id else "medium",
            "evidence_summary": evidence_summary,
            "metadata_safe": {
                "source": "worker_follow_outcome",
                "skipped_tap": bool(skipped_tap),
            },
        }
        if safe_target_id:
            patch["source_target_id"] = safe_target_id
            patch["ct_id"] = safe_target_id
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
        evidence_summary = _safe_interaction_evidence_summary(
            action_type="follow_failed",
            username=username,
            source_profile=source_profile,
            run_id=run_id,
        )
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
            "source_target_username": _canonical_source_profile(source_profile) or None,
            "evidence_source": "worker_follow_outcome",
            "evidence_confidence": "high" if safe_target_id else "medium",
            "evidence_summary": evidence_summary,
            "metadata_safe": {
                "source": "worker_follow_outcome",
                "failure_reason": str(failure_reason or "")[:200] or None,
            },
        }
        if safe_target_id:
            patch["source_target_id"] = safe_target_id
            patch["ct_id"] = safe_target_id
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
            target_id=target_id,
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
    target_id: str | None = None,
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
    interaction_type = _interaction_type_from_event_type(event_type)
    safe_target_id = _safe_uuid_text(target_id)
    body: dict[str, Any] = {
        "username": u,
        "event_type": str(event_type or "")[:200],
        "event_status": str(event_status or "success")[:80],
        "interaction_type": interaction_type,
        "interaction_status": str(event_status or "success")[:80],
        "event_at": now,
        "created_at": now,
        "payload": dict(payload) if isinstance(payload, dict) else {},
        "evidence_source": "worker_interaction_event",
        "evidence_confidence": "high" if safe_target_id and sp else ("medium" if sp else "unknown"),
        "evidence_summary": _safe_interaction_evidence_summary(
            action_type=interaction_type,
            username=username,
            source_profile=source_profile,
            run_id=run_id,
        ),
        "metadata_safe": {"source": "worker_interaction_event"},
    }
    if str(account_id or "").strip():
        body["account_id"] = str(account_id).strip()
    if run_id and str(run_id).strip():
        body["run_id"] = str(run_id).strip()
    if session_id and str(session_id).strip():
        body["session_id"] = str(session_id).strip()
    if safe_target_id:
        body["target_id"] = safe_target_id
        body["source_target_id"] = safe_target_id
        body["ct_id"] = safe_target_id
    if sp:
        body["source_profile"] = sp
        body["source_target_username"] = sp
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


def persist_follow_60s_stage_v1(
    *,
    account_id: str,
    run_id: str,
    request_id: str,
    action_id: str,
    username: str,
    source_profile: str,
    stage: str,
    stage_idempotency_key: str,
    event_at: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist one irreversible canary stage through the idempotent RPC."""
    out = call_rpc(
        "persist_follow_60s_stage_v1",
        {
            "p_account_id": str(account_id),
            "p_run_id": str(run_id),
            "p_request_id": str(request_id),
            "p_action_id": str(action_id),
            "p_username": _canonical_interaction_username(username),
            "p_source_profile": str(source_profile or ""),
            "p_stage": str(stage),
            "p_stage_idempotency_key": str(stage_idempotency_key),
            "p_event_at": str(event_at),
            "p_payload": dict(payload or {}),
        },
    )
    return dict(out or {}) if isinstance(out, dict) else {"ok": False, "raw": out}


def get_follow_60s_canary_control_v1(account_id: str) -> dict[str, Any]:
    out = call_rpc("get_follow_60s_canary_control_v1", {"p_account_id": str(account_id)})
    return dict(out or {}) if isinstance(out, dict) else {}


def mark_follow_60s_canary_barrier_v1(
    *, account_id: str, run_id: str, request_id: str, canonical_follow_count: int
) -> dict[str, Any]:
    out = call_rpc(
        "mark_follow_60s_canary_barrier_v1",
        {
            "p_account_id": str(account_id),
            "p_run_id": str(run_id),
            "p_request_id": str(request_id),
            "p_canonical_follow_count": int(canonical_follow_count),
        },
    )
    return dict(out or {}) if isinstance(out, dict) else {"ok": False, "raw": out}


def mark_follow_60s_canary_evaluation_hold_v1(
    *, account_id: str, run_id: str, request_id: str, metadata_safe: dict[str, Any] | None = None
) -> dict[str, Any]:
    out = call_rpc(
        "mark_follow_60s_canary_evaluation_hold_v1",
        {
            "p_account_id": str(account_id),
            "p_run_id": str(run_id),
            "p_request_id": str(request_id),
            "p_metadata_safe": dict(metadata_safe or {}),
        },
    )
    return dict(out or {}) if isinstance(out, dict) else {"ok": False, "raw": out}


def _call_rpc(
    function_name: str,
    params: dict[str, Any] | None,
    *,
    timeout_seconds: float,
    max_retries: int | None,
) -> Any:
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
        raw = _request_urlopen(
            req,
            op=f"RPC {fn}",
            timeout=max(1.0, float(timeout_seconds)),
            max_retries=max_retries,
        )
        if not raw:
            return None
        return json.loads(raw.decode("utf-8"))
    except SupabaseRestError:
        raise
    except Exception as e:
        raise SupabaseRestError(
            _classify_request_error(e),
            method="POST",
            path=f"rpc/{fn}",
            detail=str(e)[:200],
        ) from e


def call_rpc(function_name: str, params: dict[str, Any] | None = None) -> Any:
    """Invoke a Postgres RPC via PostgREST (service role)."""
    return _call_rpc(
        function_name,
        params,
        timeout_seconds=max(30.0, _rest_timeout_seconds()),
        max_retries=None,
    )


def call_rpc_shadow(
    function_name: str,
    params: dict[str, Any] | None = None,
    *,
    timeout_seconds: float = 3.0,
) -> Any:
    """Invoke a non-authoritative shadow RPC once with a short fail-open bound."""
    return _call_rpc(
        function_name,
        params,
        timeout_seconds=max(1.0, min(5.0, float(timeout_seconds))),
        max_retries=0,
    )


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


def get_account_dm_counter_today(account_id: str) -> dict[str, Any] | None:
    """Return the current Johannesburg business-day DM counter row."""
    aid = str(account_id or "").strip()
    if not aid:
        return None
    today, _start, _end = sast_business_day_window()
    rows = _request_json(
        "GET",
        "ig_account_dm_counters",
        query={
            "select": "*",
            "account_id": f"eq.{aid}",
            "counter_date": f"eq.{today}",
            "limit": "1",
        },
    )
    if rows and isinstance(rows, list):
        return rows[0]
    return None


def count_successful_unfollows_today(account_id: str) -> int:
    """Count persisted verified Unfollows in the current SAST business day."""
    aid = str(account_id or "").strip()
    if not aid:
        return 0
    _business_date, start, end = sast_business_day_window()
    rows = _request_json(
        "GET",
        "ig_interacted_users",
        query={
            "select": "id,unfollowed_at",
            "account_id": f"eq.{aid}",
            "unfollow_result": "eq.success",
            "unfollowed_at": f"gte.{start.isoformat()}",
            "limit": "10000",
        },
    )
    if not isinstance(rows, list):
        return 0
    total = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw = str(row.get("unfollowed_at") or "").strip()
        if not raw:
            continue
        try:
            ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if start <= ts.astimezone(timezone.utc) < end:
            total += 1
    return total


def count_successful_follows_today(account_id: str) -> int:
    """Count persisted verified follows for the current SAST business day."""
    aid = str(account_id or "").strip()
    if not aid:
        return 0
    _business_date, start, _end = sast_business_day_window()
    rows = _request_json(
        "GET",
        "ig_interaction_events",
        query={
            "select": "id,event_at",
            "account_id": f"eq.{aid}",
            "interaction_type": "eq.follow",
            "interaction_status": "eq.success",
            # The canonical deferred-persistence RPC emits
            # follow_verified_persisted_v1.  It represents the same physical,
            # verified Follow as the live worker event and must consume the
            # daily allowance as well.
            "event_type": "in.(follow_verified,follow_verified_persisted_v1)",
            "run_id": "not.is.null",
            "event_at": f"gte.{start.isoformat()}",
            "limit": "10000",
        },
    )
    return len(rows or []) if isinstance(rows, list) else 0


def get_account_package_summary(account_id: str) -> dict[str, Any] | None:
    """Load account_package_summary projection row (no insert)."""
    aid = str(account_id or "").strip()
    if not aid:
        return None
    rows = _request_json(
        "GET",
        "account_package_summary",
        query={"select": "*", "account_id": f"eq.{aid}", "limit": "1"},
    )
    if rows and isinstance(rows, list):
        return rows[0]
    return None


def get_account_commercial_policy_revision(account_id: str) -> dict[str, Any] | None:
    """Latest per-account commercial policy revision (plan change / package writer)."""
    aid = str(account_id or "").strip()
    if not aid:
        return None
    rows = _request_json(
        "GET",
        "account_commercial_policy_revisions",
        query={
            "select": "account_id,client_id,package_code,entitlement_id,revision_token,created_at",
            "account_id": f"eq.{aid}",
            "order": "created_at.desc",
            "limit": "1",
        },
    )
    if rows and isinstance(rows, list):
        row = rows[0]
        if isinstance(row, dict):
            row = dict(row)
            row.setdefault(
                "package_starts_at",
                row.get("created_at"),
            )
            return row
    package_row = _request_json(
        "GET",
        "account_commercial_packages",
        query={
            "select": "account_id,package_code,updated_at,starts_at",
            "account_id": f"eq.{aid}",
            "status": "eq.active",
            "ends_at": "is.null",
            "order": "starts_at.desc",
            "limit": "1",
        },
    )
    if package_row and isinstance(package_row, list) and package_row[0]:
        row = package_row[0]
        code = str(row.get("package_code") or "").strip()
        updated = str(row.get("updated_at") or row.get("starts_at") or "").strip()
        if code:
            return {
                "account_id": aid,
                "package_code": code,
                "package_starts_at": str(row.get("starts_at") or row.get("updated_at") or "").strip() or None,
                "revision_token": f"package:{code}:{updated}" if updated else f"package:{code}",
            }
    return None


def get_active_client_instagram_account_ownership_rows(account_id: str) -> list[dict[str, Any]]:
    """Read at most two active canonical ownership links for ambiguity checks."""
    aid = str(account_id or "").strip()
    if not aid:
        return []
    rows = _request_json(
        "GET",
        "client_instagram_accounts",
        query={
            "select": "account_id,client_id,active,updated_at",
            "account_id": f"eq.{aid}",
            "active": "eq.true",
            "limit": "2",
        },
        request_timeout=3.0,
        max_retries=0,
    )
    return [dict(row) for row in rows] if isinstance(rows, list) else []


def get_follow_runtime_cap_inputs(account_id: str) -> dict[str, Any]:
    """Return safe Follow cap inputs from Supabase projections and counters."""
    aid = str(account_id or "").strip()
    if not aid:
        return {}
    settings = None
    rows = _request_json(
        "GET",
        "ig_account_settings",
        query={
            "select": "account_id,follow_limit,max_follow_per_run,max_actions_per_day",
            "account_id": f"eq.{aid}",
            "limit": "1",
        },
    )
    if rows and isinstance(rows, list):
        settings = rows[0]
    summary = get_account_package_summary(aid) or {}
    package_caps = summary.get("package_caps") if isinstance(summary.get("package_caps"), dict) else {}
    package_defaults = summary.get("package_defaults") if isinstance(summary.get("package_defaults"), dict) else {}
    preview = summary.get("effective_caps_preview") if isinstance(summary.get("effective_caps_preview"), dict) else {}
    manual_session = (settings or {}).get("follow_limit")
    legacy_session = (settings or {}).get("max_follow_per_run")
    manual_day = (settings or {}).get("max_actions_per_day")
    configured_session = manual_session or package_defaults.get("follow_session")
    configured_day = manual_day or package_defaults.get("follow_day")
    package_day = package_caps.get("follow_day")
    package_session = package_caps.get("follow_session")
    warmup_day_cap = preview.get("warmup_follow_day_cap")
    effective_day = preview.get("follow_day")
    follows_done_today = count_successful_follows_today(aid)
    day_candidates = []
    for value in (configured_day, effective_day, package_day):
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = 0
        if parsed > 0:
            day_candidates.append(parsed)
    try:
        day_cap = min(day_candidates) if day_candidates else 0
    except (TypeError, ValueError):
        day_cap = 0
    return {
        "db_follow_per_session_limit": configured_session,
        "db_max_follow_per_run": legacy_session,
        "follow_limit_from_db": manual_session,
        "package_default_follow_session_cap": package_defaults.get("follow_session"),
        "package_default_follow_day_cap": package_defaults.get("follow_day"),
        "max_follow_per_run_from_db": legacy_session,
        "max_actions_per_day_from_db": configured_day,
        "follow_day_remaining_today": max(0, day_cap - follows_done_today),
        "package_follow_day_cap": package_day,
        "package_follow_session_cap": package_session,
        "warmup_follow_day_cap": warmup_day_cap,
        "follows_done_today": follows_done_today,
        "follow_day_cap_resolved": day_cap,
        "follow_cap_source": "min(max_actions_per_day,effective_preview_follow_day,package_follow_day)",
        "warmup_status": summary.get("warmup_status"),
        "warmup_day": summary.get("warmup_day"),
    }


def requeue_stale_outreach_dm_jobs(
    account_id: str,
    *,
    stale_minutes: int,
) -> list[dict[str, Any]]:
    """Requeue stale reserved/running outreach jobs before a controlled V1 run."""
    aid = str(account_id or "").strip()
    minutes = max(1, int(stale_minutes or 1))
    if not aid:
        return []
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
    rows = _request_json(
        "GET",
        "ig_dm_jobs",
        query={
            "select": "id,status,attempts,max_attempts,updated_at,reserved_by,recipient_username",
            "account_id": f"eq.{aid}",
            "dm_type": "eq.outreach",
            "status": "in.(reserved,running)",
            "updated_at": f"lt.{cutoff}",
        },
    )
    if not isinstance(rows, list):
        return []

    requeued: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            attempts = int(row.get("attempts") or 0)
            max_attempts = int(row.get("max_attempts") or 0)
        except (TypeError, ValueError):
            continue
        if attempts >= max_attempts:
            continue
        job_id = str(row.get("id") or "").strip()
        if not job_id:
            continue
        updated_rows = _request_json(
            "PATCH",
            "ig_dm_jobs",
            query={
                "id": f"eq.{job_id}",
                "account_id": f"eq.{aid}",
                "dm_type": "eq.outreach",
                "status": "in.(reserved,running)",
                "updated_at": f"lt.{cutoff}",
            },
            body={
                "status": "pending",
                "reserved_by": None,
                "reserved_at": None,
                "updated_at": _utc_now_iso(),
            },
            prefer_representation=True,
        )
        if updated_rows and isinstance(updated_rows, list):
            updated = updated_rows[0]
            if isinstance(updated, dict):
                requeued.append(updated)
                log(
                    "warning",
                    "outreach_stale_job_requeued",
                    account_id=aid,
                    job_id=job_id,
                    previous_status=row.get("status"),
                    stale_minutes=minutes,
                    recipient_username=row.get("recipient_username"),
                    reserved_by=row.get("reserved_by"),
                )
    return requeued


def parse_utc_iso_timestamp(raw: Any) -> datetime | None:
    """Parse ISO-8601 timestamp to timezone-aware UTC datetime."""
    if not raw:
        return None
    try:
        ts = str(raw).strip().replace("Z", "+00:00")
        # macOS' system Python 3.9 only accepts selected fractional-second
        # widths in datetime.fromisoformat(). Postgres/Supabase legitimately
        # emits variable precision (for example 5 digits), so normalize it to
        # microseconds before parsing instead of classifying a real timestamp
        # as missing.
        fractional = re.fullmatch(
            r"(?P<prefix>.*\.)(?P<fraction>\d+)(?P<offset>[+-]\d{2}:\d{2})",
            ts,
        )
        if fractional:
            digits = (fractional.group("fraction") + "000000")[:6]
            ts = f'{fractional.group("prefix")}{digits}{fractional.group("offset")}'
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


def persist_verified_follow_success_rpc(
    *,
    action_id: str,
    account_id: str,
    run_id: str,
    request_id: str,
    candidate_username: str,
    source_target_id: str | None,
    source_ct_username: str | None,
    followed_at: str,
    follow_state_after: str,
    settings_revision_expected: str,
    verification_method: str,
    metadata_safe: dict[str, Any] | None = None,
) -> dict[str, Any]:
    value = _request_json(
        "POST",
        "rpc/persist_verified_follow_success_v1",
        body={
            "p_action_id": str(action_id),
            "p_account_id": str(account_id),
            "p_run_id": str(run_id),
            "p_request_id": str(request_id),
            "p_candidate_username": str(candidate_username),
            "p_source_target_id": str(source_target_id) if source_target_id else None,
            "p_source_ct_username": str(source_ct_username) if source_ct_username else None,
            "p_followed_at": str(followed_at),
            "p_follow_state_after": str(follow_state_after),
            "p_settings_revision_expected": str(settings_revision_expected),
            "p_verification_method": str(verification_method),
            "p_metadata_safe": dict(metadata_safe or {}),
        },
        prefer_representation=True,
    )
    if isinstance(value, list):
        value = value[0] if value else None
    if not isinstance(value, dict):
        raise SupabaseRestError(
            "supabase_rpc_response_invalid",
            method="POST",
            path="rpc/persist_verified_follow_success_v1",
        )
    return value


def get_follow_persistence_event(action_id: str) -> dict[str, Any] | None:
    rows = _request_json(
        "GET",
        "ig_interaction_events",
        query={
            "select": (
                "id,account_id,run_id,request_id,username,event_type,event_status,"
                "interaction_type,interaction_status,event_at,payload"
            ),
            "id": f"eq.{str(action_id)}",
            "limit": "1",
        },
    )
    return rows[0] if isinstance(rows, list) and rows else None


def get_follow_persistence_canonical_evidence(
    *, action_id: str, account_id: str, username: str
) -> dict[str, Any]:
    """Read the three canonical records used to reconcile a committed Follow.

    This is deliberately a read-only recovery path and is called only after a
    strict RPC response failure or an ambiguous network result.
    """
    return {
        "event": get_follow_persistence_event(str(action_id)),
        "interaction": load_interacted_user(str(account_id), str(username)),
        "unfollow_settings": get_account_unfollow_settings(str(account_id)),
    }


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
        "runtime_cap_mode": "prod_normal",
        "runtime_safety_cap": None,
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
            "runtime_cap_mode": "prod_normal",
            "runtime_safety_cap": None,
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
    after_days: int = 0,
    as_of: datetime | None = None,
    include_metadata: bool = False,
) -> list[dict[str, Any]] | tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Load the complete account-scoped bot-Follow ledger for strict Unfollow modes.

    ``limit`` is the page size, never a total-result cap.  Final eligibility,
    lifecycle, followback, protection-list and per-session slicing are applied
    only after this exhaustive scan in ``unfollow_eligibility_engine``.
    """
    aid = str(account_id or "").strip()
    if not aid:
        empty_metadata = {
            "source_rows_loaded": 0,
            "page_size": 0,
            "pages_loaded": 0,
            "page_requests": 0,
            "pagination_used": False,
            "candidate_scan_exhaustive": True,
            "pagination_strategy": "stable_offset_snapshot_v1",
            "scan_as_of": None,
            "eligibility_cutoff": None,
        }
        return ([], empty_metadata) if include_metadata else []

    page_size = max(1, min(int(limit), 1000))
    snapshot_at = as_of or datetime.now(timezone.utc)
    if snapshot_at.tzinfo is None:
        snapshot_at = snapshot_at.replace(tzinfo=timezone.utc)
    else:
        snapshot_at = snapshot_at.astimezone(timezone.utc)
    cutoff = snapshot_at - timedelta(days=max(0, int(after_days)))

    base_query = {
        "select": "*",
        "account_id": f"eq.{aid}",
        "followed_by_bot": "eq.true",
        # Freeze membership for the duration of the scan.  State fields may be
        # classified by the engine, but a concurrent insert cannot shift offsets.
        "created_at": f"lte.{snapshot_at.isoformat()}",
        "order": "followed_at.asc.nullslast,created_at.asc,id.asc",
    }

    out: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_page_signatures: set[str] = set()
    pages_loaded = 0
    page_requests = 0
    offset = 0
    max_pages = 10_000

    for _page_index in range(max_pages):
        raw_rows = _request_json(
            "GET",
            "ig_interacted_users",
            query={
                **base_query,
                "limit": str(page_size),
                "offset": str(offset),
            },
        )
        page_requests += 1
        if not isinstance(raw_rows, list):
            raise RuntimeError("unfollow_candidate_pagination_response_not_list")
        if any(not isinstance(row, dict) for row in raw_rows):
            raise RuntimeError("unfollow_candidate_pagination_row_not_object")
        rows = list(raw_rows)

        signature_payload = [
            (
                str(row.get("id") or ""),
                str(row.get("username") or ""),
                str(row.get("followed_at") or ""),
            )
            for row in rows
        ]
        page_signature = hashlib.sha256(
            json.dumps(signature_payload, separators=(",", ":"), ensure_ascii=True).encode(
                "utf-8"
            )
        ).hexdigest()
        if rows and page_signature in seen_page_signatures:
            raise RuntimeError("unfollow_candidate_pagination_repeated_page")
        if rows:
            seen_page_signatures.add(page_signature)
            pages_loaded += 1

        added = 0
        for row in rows:
            row_id = str(row.get("id") or "").strip()
            if row_id and row_id in seen_ids:
                raise RuntimeError("unfollow_candidate_pagination_duplicate_row_id")
            if row_id:
                seen_ids.add(row_id)
            out.append(row)
            added += 1

        if rows and len(rows) >= page_size and added == 0:
            raise RuntimeError("unfollow_candidate_pagination_no_progress")
        if len(rows) < page_size:
            break
        offset += len(rows)
    else:
        raise RuntimeError("unfollow_candidate_pagination_max_pages_exceeded")

    metadata = {
        "source_rows_loaded": len(out),
        "page_size": page_size,
        "pages_loaded": pages_loaded,
        "page_requests": page_requests,
        "pagination_used": page_requests > 1,
        "candidate_scan_exhaustive": True,
        "pagination_strategy": "stable_offset_snapshot_v1",
        "scan_as_of": snapshot_at.isoformat(),
        "eligibility_cutoff": cutoff.isoformat(),
    }
    return (out, metadata) if include_metadata else out


def fetch_unfollow_candidate_availability(
    account_id: str,
) -> dict[str, dict[str, Any]]:
    """Load the complete account-scoped not-found ledger for classification."""

    aid = str(account_id or "").strip()
    if not aid:
        return {}
    out: dict[str, dict[str, Any]] = {}
    page_size = 1000
    offset = 0
    snapshot_at = datetime.now(timezone.utc).isoformat()
    seen_page_signatures: set[str] = set()
    for _page_index in range(10_000):
        rows = _request_json(
            "GET",
            "ig_unfollow_candidate_availability",
            query={
                "select": "account_id,normalized_username,status,reason,first_not_found_at,last_checked_at,not_found_attempt_count,first_failure_at,last_failure_at,technical_attempt_count,next_retry_at,terminal_at,source_run_id,business_date_sast,created_at",
                "account_id": f"eq.{aid}",
                "status": "in.(temporary_unavailable,exhausted,username_not_found_confirmed,search_surface_unhealthy)",
                "created_at": f"lte.{snapshot_at}",
                "order": "normalized_username.asc",
                "limit": str(page_size),
                "offset": str(offset),
            },
        )
        if not isinstance(rows, list):
            raise RuntimeError("unfollow_candidate_availability_response_not_list")
        if any(not isinstance(row, dict) for row in rows):
            raise RuntimeError("unfollow_candidate_availability_row_not_object")
        page_signature = hashlib.sha256(
            json.dumps(
                [str(row.get("normalized_username") or "") for row in rows],
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
        ).hexdigest()
        if rows and page_signature in seen_page_signatures:
            raise RuntimeError("unfollow_candidate_availability_repeated_page")
        if rows:
            seen_page_signatures.add(page_signature)
        for row in rows:
            key = _canonical_interaction_username(
                str(row.get("normalized_username") or "")
            )
            if not key:
                raise RuntimeError("unfollow_candidate_availability_username_invalid")
            if key in out:
                raise RuntimeError("unfollow_candidate_availability_duplicate_username")
            out[key] = dict(row)
        if len(rows) < page_size:
            break
        offset += len(rows)
    else:
        raise RuntimeError("unfollow_candidate_availability_max_pages_exceeded")
    return out


def record_unfollow_candidate_not_found(
    account_id: str,
    normalized_username: str,
    *,
    source_run_id: str | None,
    reason: str,
    cooldown_hours: int = 24,
    max_attempts: int = 2,
) -> dict[str, Any]:
    """Atomically advance the durable unavailable/cooldown lifecycle."""

    aid = str(account_id or "").strip()
    username = _canonical_interaction_username(normalized_username)
    if not aid or not username:
        raise ValueError("account_id and normalized_username are required")
    result = _call_rpc(
        "record_unfollow_candidate_not_found_v1",
        {
            "p_account_id": aid,
            "p_normalized_username": username,
            "p_source_run_id": str(source_run_id or "").strip() or None,
            "p_reason": str(reason or "unfollow_candidate_not_found").strip(),
            "p_cooldown_hours": max(1, min(int(cooldown_hours), 168)),
            "p_max_attempts": max(1, min(int(max_attempts), 10)),
        },
        timeout_seconds=5.0,
        max_retries=1,
    )
    if not isinstance(result, dict) or not bool(result.get("ok")):
        raise RuntimeError("record_unfollow_candidate_not_found_failed")
    return dict(result)


def record_unfollow_candidate_availability_v2(
    account_id: str,
    normalized_username: str,
    *,
    source_run_id: str | None,
    classification: str,
    reason: str,
    technical_cooldown_minutes: int = 30,
) -> dict[str, Any]:
    """Persist a terminal exact absence or a retryable technical hold."""

    aid = str(account_id or "").strip()
    username = _canonical_interaction_username(normalized_username)
    classification_value = str(classification or "").strip()
    if not aid or not username:
        raise ValueError("account_id and normalized_username are required")
    if classification_value not in {
        "username_not_found_confirmed",
        "search_surface_unhealthy",
    }:
        raise ValueError("unsupported_unfollow_candidate_availability_classification")
    result = _call_rpc(
        "record_unfollow_candidate_availability_v2",
        {
            "p_account_id": aid,
            "p_normalized_username": username,
            "p_source_run_id": str(source_run_id or "").strip() or None,
            "p_classification": classification_value,
            "p_reason": str(reason or classification_value).strip(),
            "p_technical_cooldown_minutes": max(
                5,
                min(int(technical_cooldown_minutes), 24 * 60),
            ),
        },
        timeout_seconds=5.0,
        max_retries=1,
    )
    if not isinstance(result, dict) or not bool(result.get("ok")):
        raise RuntimeError("record_unfollow_candidate_availability_v2_failed")
    return dict(result)


def record_unfollow_phase_circuit_breaker_v1(
    account_id: str,
    *,
    source_run_id: str | None,
    stable_reason: str,
    technical_failure_count: int,
    usernames: list[str],
    cooldown_minutes: int = 30,
) -> dict[str, Any]:
    """Open a bounded Unfollow-only hold without disabling future Follow."""

    aid = str(account_id or "").strip()
    if not aid:
        raise ValueError("account_id is required")
    bounded_usernames = [
        value
        for value in (
            _canonical_interaction_username(str(item or ""))
            for item in list(usernames or [])[:10]
        )
        if value
    ]
    result = _call_rpc(
        "record_unfollow_phase_circuit_breaker_v1",
        {
            "p_account_id": aid,
            "p_source_run_id": str(source_run_id or "").strip() or None,
            "p_stable_reason": str(stable_reason or "").strip(),
            "p_technical_failure_count": max(1, int(technical_failure_count)),
            "p_usernames": bounded_usernames,
            "p_cooldown_minutes": max(5, min(int(cooldown_minutes), 24 * 60)),
        },
        timeout_seconds=5.0,
        max_retries=1,
    )
    if not isinstance(result, dict) or not bool(result.get("ok")):
        raise RuntimeError("record_unfollow_phase_circuit_breaker_v1_failed")
    return dict(result)


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


def fetch_pending_welcome_jobs_by_usernames(
    account_id: str,
    usernames: list[str],
) -> dict[str, dict[str, Any]]:
    """Batch lookup pending Welcome jobs by normalized recipient username."""
    aid = str(account_id or "").strip()
    keys = []
    seen: set[str] = set()
    for raw in usernames:
        key = _normalize_follower_username(raw)
        if key and key not in seen:
            seen.add(key)
            keys.append(key)
    if not aid or not keys:
        return {}
    rows = _request_json(
        "GET",
        "ig_dm_jobs",
        query={
            "select": "id,status,recipient_username,dm_type,source,created_at",
            "account_id": f"eq.{aid}",
            "dm_type": "eq.welcome",
            "status": "eq.pending",
            "recipient_username": f"in.({','.join(keys)})",
        },
    )
    out: dict[str, dict[str, Any]] = {}
    if not rows or not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        nk = _normalize_follower_username(str(row.get("recipient_username") or ""))
        if nk:
            out[nk] = row
    return out


def mark_followbacks_from_seen_followers(
    account_id: str,
    follower_usernames: list[str],
    source: str = "followers_scan",
) -> dict[str, Any]:
    """Mark existing bot-followed interacted users as following back when seen in Followers."""
    t0 = datetime.now(timezone.utc)
    aid = str(account_id or "").strip()
    src = str(source or "followers_scan").strip() or "followers_scan"
    keys: list[str] = []
    seen: set[str] = set()
    for raw in follower_usernames:
        key = _canonical_interaction_username(str(raw or ""))
        if not key or key in seen:
            continue
        if _invalid_interacted_username_reason(key) is not None:
            continue
        seen.add(key)
        keys.append(key)

    started = datetime.now(timezone.utc)
    log(
        "info",
        "followback_memory_mark_started",
        account_id=aid,
        source=src,
        input_count=len(follower_usernames or []),
        normalized_count=len(keys),
    )

    if not aid or not keys:
        duration_ms = round((datetime.now(timezone.utc) - t0).total_seconds() * 1000.0, 2)
        out = {
            "ok": True,
            "account_id": aid,
            "source": src,
            "input_count": len(follower_usernames or []),
            "normalized_count": len(keys),
            "matched_count": 0,
            "updated_count": 0,
            "skipped_count": len(follower_usernames or []) - len(keys),
            "duration_ms": duration_ms,
        }
        log("info", "followback_memory_mark_completed", **out)
        return out

    now = _utc_now_iso()
    matched_usernames: set[str] = set()
    touched_rows: list[dict[str, Any]] = []
    chunk_size = 100
    try:
        for i in range(0, len(keys), chunk_size):
            chunk = keys[i : i + chunk_size]
            in_clause = ",".join(chunk)
            base_query = {
                "select": "id,username,source_target_id,source_target_username",
                "account_id": f"eq.{aid}",
                "username": f"in.({in_clause})",
                "followed_by_bot": "eq.true",
                "follow_status": "eq.following",
                "unfollowed_at": "is.null",
            }
            first_seen_rows = _request_json_tolerate_unknown_columns(
                "PATCH",
                "ig_interacted_users",
                query={**base_query, "followback_detected_at": "is.null"},
                body={
                    "is_following_back": True,
                    "followback_detected_at": now,
                    "updated_at": now,
                },
                prefer_representation=True,
            )
            if isinstance(first_seen_rows, list):
                for row in first_seen_rows:
                    if isinstance(row, dict):
                        username = _canonical_interaction_username(str(row.get("username") or ""))
                        if username:
                            matched_usernames.add(username)
                        touched_rows.append(row)

            rows = _request_json_tolerate_unknown_columns(
                "PATCH",
                "ig_interacted_users",
                query=base_query,
                body={
                    "is_following_back": True,
                    "updated_at": now,
                },
                prefer_representation=True,
            )
            if isinstance(rows, list):
                for row in rows:
                    if isinstance(row, dict):
                        username = _canonical_interaction_username(str(row.get("username") or ""))
                        if username:
                            matched_usernames.add(username)
                        touched_rows.append(row)
        duration_ms = round((datetime.now(timezone.utc) - started).total_seconds() * 1000.0, 2)
        out = {
            "ok": True,
            "account_id": aid,
            "source": src,
            "input_count": len(follower_usernames or []),
            "normalized_count": len(keys),
            "matched_count": len(matched_usernames),
            "updated_count": len(matched_usernames),
            "skipped_count": len(keys) - len(matched_usernames),
            "duration_ms": duration_ms,
        }
        log("info", "followback_memory_mark_completed", **out)
        if matched_usernames:
            out["target_followbacks_sync"] = sync_touched_target_followbacks_from_mark(aid, touched_rows)
        return out
    except Exception as exc:
        duration_ms = round((datetime.now(timezone.utc) - started).total_seconds() * 1000.0, 2)
        out = {
            "ok": False,
            "account_id": aid,
            "source": src,
            "input_count": len(follower_usernames or []),
            "normalized_count": len(keys),
            "matched_count": len(matched_usernames),
            "updated_count": len(matched_usernames),
            "skipped_count": len(keys) - len(matched_usernames),
            "duration_ms": duration_ms,
            "error": str(exc)[:500],
        }
        log("warning", "followback_memory_mark_failed", **out)
        return out


def _resolve_touch_target_ids_from_interacted_rows(
    account_id: str,
    rows: list[dict[str, Any]],
) -> list[str]:
    aid = str(account_id or "").strip()
    target_ids: set[str] = set()
    username_fallbacks: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        tid = str(row.get("source_target_id") or "").strip()
        if tid:
            target_ids.add(tid)
            continue
        ct_username = _canonical_interaction_username(str(row.get("source_target_username") or ""))
        if ct_username:
            username_fallbacks.add(ct_username)
    if username_fallbacks and aid:
        in_clause = ",".join(sorted(username_fallbacks))
        lookup_rows = _request_json(
            "GET",
            "ig_targets",
            query={
                "select": "id,normalized_username",
                "account_id": f"eq.{aid}",
                "normalized_username": f"in.({in_clause})",
            },
        )
        if isinstance(lookup_rows, list):
            for target in lookup_rows:
                if isinstance(target, dict):
                    lookup_id = str(target.get("id") or "").strip()
                    if lookup_id:
                        target_ids.add(lookup_id)
    return sorted(target_ids)


def sync_target_followbacks_count(
    target_id: str,
    *,
    certify_zero_coverage: bool = False,
) -> dict[str, Any]:
    """Recompute ig_targets.followbacks_count; certifies only when attribution is positive unless explicitly requested."""
    tid = str(target_id or "").strip()
    if not tid:
        return {"ok": False, "reason": "missing_target_id"}
    try:
        row = call_rpc(
            "sync_ig_target_followbacks_count",
            {
                "p_target_id": tid,
                "p_certify_zero_coverage": certify_zero_coverage,
            },
        )
    except Exception as exc:
        log(
            "warning",
            "sync_target_followbacks_count_failed",
            target_id=tid,
            error=str(exc)[:300],
        )
        return {"ok": False, "target_id": tid, "error": str(exc)[:300]}
    if isinstance(row, dict):
        return row
    if isinstance(row, list) and row and isinstance(row[0], dict):
        return row[0]
    return {"ok": False, "target_id": tid, "error": "unexpected_rpc_response"}


def sync_touched_target_followbacks_count(
    account_id: str,
    target_ids: list[str],
    *,
    certify_zero_coverage: bool = False,
) -> dict[str, Any]:
    aid = str(account_id or "").strip()
    unique_ids = sorted({str(tid or "").strip() for tid in target_ids if str(tid or "").strip()})
    results: list[dict[str, Any]] = []
    certified = 0
    for tid in unique_ids:
        out = sync_target_followbacks_count(tid, certify_zero_coverage=certify_zero_coverage)
        results.append(out)
        if out.get("certified") is True:
            certified += 1
    return {
        "ok": True,
        "account_id": aid,
        "touched_targets": len(unique_ids),
        "certified_targets": certified,
        "results": results,
    }


def sync_touched_target_followbacks_from_mark(
    account_id: str,
    touched_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    target_ids = _resolve_touch_target_ids_from_interacted_rows(account_id, touched_rows)
    if not target_ids:
        return {
            "ok": True,
            "account_id": account_id,
            "touched_targets": 0,
            "certified_targets": 0,
            "results": [],
        }
    return sync_touched_target_followbacks_count(account_id, target_ids)


def sync_account_target_followbacks_count(account_id: str) -> dict[str, Any]:
    """Positive-only account sync via RPC; never certifies zero-followback CT rows without coverage proof."""
    aid = str(account_id or "").strip()
    if not aid:
        return {"ok": False, "reason": "missing_account_id"}
    try:
        row = call_rpc("sync_ig_account_target_followbacks", {"p_account_id": aid})
    except Exception as exc:
        log(
            "warning",
            "sync_account_target_followbacks_failed",
            account_id=aid,
            error=str(exc)[:300],
        )
        return {"ok": False, "account_id": aid, "error": str(exc)[:300]}
    if isinstance(row, dict):
        return row
    if isinstance(row, list) and row and isinstance(row[0], dict):
        return row[0]
    return {"ok": False, "account_id": aid, "error": "unexpected_rpc_response"}


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


def get_dm_template_by_id(template_id: str, *, account_id: str | None = None) -> dict[str, Any] | None:
    tid = str(template_id or "").strip()
    if not tid:
        return None
    query = {
        "select": "id,account_id,template_type,active,body,is_default,updated_at",
        "id": f"eq.{tid}",
        "limit": "1",
    }
    if account_id:
        query["account_id"] = f"eq.{str(account_id)}"
    rows = _request_json("GET", "ig_dm_templates", query=query) or []
    return rows[0] if isinstance(rows, list) and rows else None


def get_default_dm_template(account_id: str, dm_type: str) -> dict[str, Any] | None:
    aid = str(account_id or "").strip()
    kind = str(dm_type or "").strip().lower()
    if not aid or kind not in ("welcome", "outreach"):
        return None
    rows = _request_json(
        "GET",
        "ig_dm_templates",
        query={
            "select": "id,account_id,template_type,active,body,is_default,updated_at",
            "account_id": f"eq.{aid}",
            "template_type": f"eq.{kind}",
            "active": "eq.true",
            "is_default": "eq.true",
            "order": "created_at.asc",
            "limit": "1",
        },
    ) or []
    return rows[0] if isinstance(rows, list) and rows else None


def get_account_username(account_id: str) -> str:
    aid = str(account_id or "").strip()
    if not aid:
        return ""
    rows = _request_json(
        "GET",
        "ig_accounts",
        query={"select": "username", "id": f"eq.{aid}", "limit": "1"},
    ) or []
    if isinstance(rows, list) and rows:
        return str(rows[0].get("username") or "").strip()
    return ""


def _resolve_dm_template_for_enqueue(
    account_id: str,
    *,
    dm_type: str,
    template_id: str | None,
) -> dict[str, Any] | None:
    tid = str(template_id or "").strip()
    if tid:
        return get_dm_template_by_id(tid, account_id=account_id)

    settings = get_account_dm_settings(account_id) or {}
    settings_key = "welcome_template_id" if dm_type == "welcome" else "default_outreach_template_id"
    settings_template_id = str(settings.get(settings_key) or "").strip()
    if settings_template_id:
        return get_dm_template_by_id(settings_template_id, account_id=account_id)
    return get_default_dm_template(account_id, dm_type)


def _resolve_and_render_dm_message_for_enqueue(
    account_id: str,
    *,
    recipient_username: str,
    recipient_name: str | None,
    account_username: str | None,
    dm_type: str,
    message_body: str | None,
    template_id: str | None,
) -> tuple[str | None, dict[str, Any] | None]:
    from dm_template_renderer import has_unresolved_template_tokens, render_dm_template

    raw_body = str(message_body or "").strip()
    template: dict[str, Any] | None = None
    if not raw_body:
        template = _resolve_dm_template_for_enqueue(account_id, dm_type=dm_type, template_id=template_id)
        if template:
            if template.get("active") is not True:
                raise RuntimeError("dm_template_render_failed:template_inactive")
            template_type = str(template.get("template_type") or "").strip().lower()
            if template_type and template_type != str(dm_type or "").strip().lower():
                raise RuntimeError("dm_template_render_failed:template_type_mismatch")
        raw_body = str((template or {}).get("body") or "").strip()
    if not raw_body:
        return message_body, template

    if not has_unresolved_template_tokens(raw_body):
        return raw_body, template

    sender_username = str(account_username or "").strip() or get_account_username(account_id)
    result = render_dm_template(
        raw_body,
        {
            "recipient_username": recipient_username,
            "recipient_name": recipient_name,
            "account_username": sender_username,
        },
    )
    if not result.ok:
        raise RuntimeError(f"dm_template_render_failed:{result.reason}")
    return result.rendered_body, template


def _render_dm_message_body_for_enqueue(
    account_id: str,
    *,
    recipient_username: str,
    recipient_name: str | None,
    account_username: str | None,
    dm_type: str,
    message_body: str | None,
    template_id: str | None,
) -> str | None:
    rendered_body, _template = _resolve_and_render_dm_message_for_enqueue(
        account_id,
        recipient_username=recipient_username,
        recipient_name=recipient_name,
        account_username=account_username,
        dm_type=dm_type,
        message_body=message_body,
        template_id=template_id,
    )
    return rendered_body


def enqueue_welcome_dm_job_if_eligible(
    account_id: str,
    follower_username: str,
    *,
    scan_run_id: str | None = None,
    template_id: str | None = None,
    message_body: str | None = None,
    recipient_name: str | None = None,
    account_username: str | None = None,
    priority: int = 10,
) -> dict[str, Any] | None:
    """RPC enqueue_welcome_dm_job_if_eligible (no DM send). Returns job row or None."""
    if account_protection_lists.is_interaction_blocked(follower_username):
        log(
            "info",
            "interaction_blacklist_action_skipped",
            account_id=str(account_id),
            action="welcome_dm_enqueue",
            username=str(follower_username or "") or None,
            reason="interaction_blacklist",
        )
        return None
    if str(message_body or "").strip():
        raise RuntimeError("dm_template_render_failed:welcome_message_body_override_not_allowed")
    rendered_body, resolved_template = _resolve_and_render_dm_message_for_enqueue(
        account_id,
        recipient_username=follower_username,
        recipient_name=recipient_name,
        account_username=account_username,
        dm_type="welcome",
        message_body=None,
        template_id=template_id,
    )
    resolved_template_id = str((resolved_template or {}).get("id") or template_id or "").strip()
    if not resolved_template_id:
        raise RuntimeError("dm_template_render_failed:canonical_welcome_template_missing")
    rendered_body_normalized = str(rendered_body or "").strip()
    if not rendered_body_normalized:
        raise RuntimeError("dm_template_render_failed:canonical_welcome_message_empty")
    row = call_rpc(
        "enqueue_welcome_dm_job_if_eligible",
        {
            "p_account_id": str(account_id),
            "p_follower_username": str(follower_username),
            "p_source_scan_run_id": scan_run_id,
            "p_message_body": rendered_body_normalized,
            "p_template_id": resolved_template_id,
            "p_priority": int(priority),
        },
    )
    job: dict[str, Any] | None = None
    if isinstance(row, dict):
        job = row
    elif isinstance(row, list) and row:
        first = row[0]
        job = first if isinstance(first, dict) else None
    if job is None:
        return None

    returned_template_id = str(job.get("template_id") or "").strip()
    returned_message_body = str(job.get("message_body") or "").strip()
    if (
        returned_template_id != resolved_template_id
        or returned_message_body != rendered_body_normalized
    ):
        log(
            "error",
            "welcome_template_contract_mismatch",
            account_id=str(account_id),
            job_id=str(job.get("id") or ""),
            recipient_username=str(follower_username),
            expected_template_id=resolved_template_id,
            returned_template_id=returned_template_id,
            template_version=str((resolved_template or {}).get("updated_at") or ""),
            template_hash=hashlib.sha256(rendered_body_normalized.encode("utf-8")).hexdigest()[:16],
        )
        raise RuntimeError("welcome_template_contract_mismatch")

    log(
        "info",
        "welcome_template_contract_validated",
        account_id=str(account_id),
        job_id=str(job.get("id") or ""),
        recipient_username=str(follower_username),
        template_id=resolved_template_id,
        template_version=str((resolved_template or {}).get("updated_at") or ""),
        template_hash=hashlib.sha256(rendered_body_normalized.encode("utf-8")).hexdigest()[:16],
    )
    return job


def enqueue_outreach_dm_job(
    account_id: str,
    recipient_username: str,
    *,
    message_body: str | None = None,
    template_id: str | None = None,
    recipient_name: str | None = None,
    account_username: str | None = None,
    source: str = "manual",
    campaign_id: str | None = None,
    priority: int = 0,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """RPC enqueue_outreach_dm_job (no DM send). Returns job row or None."""
    if account_protection_lists.is_interaction_blocked(recipient_username):
        log(
            "info",
            "interaction_blacklist_action_skipped",
            account_id=str(account_id),
            action="outreach_dm_enqueue",
            username=str(recipient_username or "") or None,
            reason="interaction_blacklist",
        )
        return None
    rendered_body = _render_dm_message_body_for_enqueue(
        account_id,
        recipient_username=recipient_username,
        recipient_name=recipient_name,
        account_username=account_username,
        dm_type="outreach",
        message_body=message_body,
        template_id=template_id,
    )
    row = call_rpc(
        "enqueue_outreach_dm_job",
        {
            "p_account_id": str(account_id),
            "p_recipient_username": str(recipient_username),
            "p_message_body": rendered_body,
            "p_template_id": template_id,
            "p_source": str(source or "manual"),
            "p_campaign_id": campaign_id,
            "p_priority": int(priority),
            "p_metadata": metadata or {},
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
