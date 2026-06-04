"""Supabase REST helpers for Live View host agent (service_role only)."""

from __future__ import annotations

import json
import os
from typing import Any
from urllib import error, parse, request

from live_view_host_agent_core import ACTIVE_SESSION_STATUSES, frame_storage_object_path

ACTIVE_IG_RUN_STATUSES = (
    "running",
    "queued",
    "pending",
    "in_progress",
    "active",
    "starting",
)
ACTIVE_REQUEST_STATUSES = ("queued", "claimed", "starting", "running")


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


def _headers(*, content_type: str = "application/json", prefer: str | None = None) -> dict[str, str]:
    key = _service_key()
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": content_type,
    }
    if prefer:
        headers["Prefer"] = prefer
    return headers


def _request(
    method: str,
    path: str,
    *,
    query: dict[str, str] | None = None,
    body: bytes | dict[str, Any] | list[dict[str, Any]] | None = None,
    prefer: str | None = None,
    content_type: str = "application/json",
    timeout_s: float = 20.0,
) -> Any:
    base = _base_url()
    params = f"?{parse.urlencode(query or {})}" if query else ""
    url = f"{base}{path}{params}"
    data: bytes | None = None
    if isinstance(body, (dict, list)):
        data = json.dumps(body, separators=(",", ":")).encode("utf-8")
    elif isinstance(body, bytes):
        data = body
    req = request.Request(
        url=url,
        method=method,
        headers=_headers(content_type=content_type, prefer=prefer),
        data=data,
    )
    try:
        with request.urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read()
            if not raw:
                return None
            if content_type == "application/json" or (resp.headers.get("Content-Type") or "").startswith("application/json"):
                return json.loads(raw.decode("utf-8"))
            return raw
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Supabase {method} {path} failed: {exc.code} {detail}") from exc


def select_table(table: str, query: dict[str, str]) -> list[dict[str, Any]]:
    payload = _request("GET", f"/rest/v1/{table}", query=query)
    if payload is None:
        return []
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    return []


def update_table(table: str, query: dict[str, str], body: dict[str, Any]) -> list[dict[str, Any]]:
    payload = _request(
        "PATCH",
        f"/rest/v1/{table}",
        query=query,
        body=body,
        prefer="return=representation",
    )
    if payload is None:
        return []
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    return []


def insert_table(table: str, body: dict[str, Any]) -> list[dict[str, Any]]:
    payload = _request(
        "POST",
        f"/rest/v1/{table}",
        body=body,
        prefer="return=representation",
    )
    if payload is None:
        return []
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    return []


def fetch_session(session_id: str) -> dict[str, Any] | None:
    rows = select_table("live_view_sessions", {
        "select": "*",
        "id": f"eq.{session_id}",
        "limit": "1",
    })
    return rows[0] if rows else None


def list_pending_sessions(host_id: str) -> list[dict[str, Any]]:
    return select_table("live_view_sessions", {
        "select": "*",
        "status": "eq.pending",
        "host_id": f"eq.{host_id}",
        "order": "created_at.asc",
        "limit": "20",
    })


def list_active_sessions_for_device(device_id: str, *, exclude_session_id: str | None = None) -> list[dict[str, Any]]:
    rows = select_table("live_view_sessions", {
        "select": "id,status,device_id,app_instance_id,account_id",
        "device_id": f"eq.{device_id}",
        "status": f"in.({','.join(sorted(ACTIVE_SESSION_STATUSES))})",
        "order": "created_at.desc",
        "limit": "10",
    })
    if exclude_session_id:
        return [row for row in rows if str(row.get("id")) != exclude_session_id]
    return rows


def claim_pending_session(
    session_id: str,
    *,
    metadata_safe: dict[str, Any],
    started_at: str,
) -> dict[str, Any] | None:
    rows = update_table(
        "live_view_sessions",
        {
            "id": f"eq.{session_id}",
            "status": "eq.pending",
        },
        {
            "status": "starting",
            "started_at": started_at,
            "updated_at": started_at,
            "stream_transport": "screenshot_polling",
            "metadata_safe": metadata_safe,
        },
    )
    return rows[0] if rows else None


def update_session(session_id: str, body: dict[str, Any]) -> dict[str, Any] | None:
    rows = update_table("live_view_sessions", {"id": f"eq.{session_id}"}, body)
    return rows[0] if rows else None


def insert_audit_event(event: dict[str, Any]) -> None:
    insert_table("live_view_audit_events", event)


def fetch_device(device_id: str) -> dict[str, Any] | None:
    rows = select_table("phone_devices", {
        "select": "id,name,device_name,status,host_machine,adb_serial",
        "id": f"eq.{device_id}",
        "limit": "1",
    })
    return rows[0] if rows else None


def fetch_app_instance(app_instance_id: str) -> dict[str, Any] | None:
    rows = select_table("phone_app_instances", {
        "select": "id,device_id,instance_index,visible_label,package_name,status,is_launchable",
        "id": f"eq.{app_instance_id}",
        "limit": "1",
    })
    return rows[0] if rows else None


def fetch_account(account_id: str) -> dict[str, Any] | None:
    rows = select_table("ig_accounts", {
        "select": "id,username,status",
        "id": f"eq.{account_id}",
        "limit": "1",
    })
    return rows[0] if rows else None


def fetch_device_heartbeat(device_id: str) -> dict[str, Any] | None:
    rows = select_table("device_heartbeats", {
        "select": "device_id,status,last_seen_at",
        "device_id": f"eq.{device_id}",
        "limit": "1",
    })
    return rows[0] if rows else None


def account_has_active_run(account_id: str) -> bool:
    ig_runs = select_table("ig_runs", {
        "select": "id",
        "account_id": f"eq.{account_id}",
        "status": f"in.({','.join(ACTIVE_IG_RUN_STATUSES)})",
        "limit": "1",
    })
    if ig_runs:
        return True
    requests = select_table("account_run_requests", {
        "select": "id",
        "account_id": f"eq.{account_id}",
        "status": f"in.({','.join(ACTIVE_REQUEST_STATUSES)})",
        "limit": "1",
    })
    return bool(requests)


def upload_frame_png(session_id: str, png_bytes: bytes, *, bucket: str) -> None:
    object_path = frame_storage_object_path(session_id)
    encoded = parse.quote(object_path, safe="/")
    key = _service_key()
    url = f"{_base_url()}/storage/v1/object/{bucket}/{encoded}"
    req = request.Request(
        url=url,
        method="POST",
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "image/png",
            "x-upsert": "true",
        },
        data=png_bytes,
    )
    try:
        with request.urlopen(req, timeout=30.0) as resp:
            resp.read()
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Supabase storage upload failed: {exc.code} {detail}") from exc


def download_frame_png(session_id: str, *, bucket: str) -> bytes | None:
    object_path = frame_storage_object_path(session_id)
    encoded = parse.quote(object_path, safe="/")
    try:
        payload = _request(
            "GET",
            f"/storage/v1/object/{bucket}/{encoded}",
            content_type="application/octet-stream",
            timeout_s=20.0,
        )
    except RuntimeError:
        return None
    if isinstance(payload, bytes):
        return payload
    return None


def delete_frame_png(session_id: str, *, bucket: str) -> None:
    object_path = frame_storage_object_path(session_id)
    key = _service_key()
    url = f"{_base_url()}/storage/v1/object/{bucket}"
    body = json.dumps({"prefixes": [object_path]}, separators=(",", ":")).encode("utf-8")
    req = request.Request(
        url=url,
        method="DELETE",
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        data=body,
    )
    try:
        with request.urlopen(req, timeout=20.0) as resp:
            resp.read()
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Supabase storage delete failed: {exc.code} {detail}") from exc
