"""Persistent Auto Restart device locks backed by Supabase RPCs."""

from __future__ import annotations

import os
from typing import Any

import supabase_client
from logs import log


def _load_device_lock(device_id: str) -> dict[str, Any] | None:
    rows = supabase_client._request_json(
        "GET",
        "auto_restart_device_locks",
        query={
            "select": "device_id,worker_id,request_id,lease_expires_at",
            "device_id": f"eq.{device_id}",
            "limit": "1",
        },
        request_timeout=5.0,
        max_retries=0,
    ) or []
    return dict(rows[0]) if rows else None


def _lease_seconds() -> int:
    raw = os.environ.get("AUTO_RESTART_DEVICE_LOCK_SECONDS", "900")
    try:
        return max(60, min(3600, int(str(raw).strip())))
    except ValueError:
        return 900


def acquire_device_lock(
    *,
    device_id: str,
    worker_id: str,
    account_id: str,
    app_instance_id: str | None,
    reason: str = "auto_restart",
) -> dict[str, Any]:
    payload = supabase_client.call_rpc(
        "auto_restart_acquire_device_lock",
        {
            "p_device_id": device_id,
            "p_worker_id": worker_id,
            "p_account_id": account_id,
            "p_app_instance_id": app_instance_id,
            "p_lease_seconds": _lease_seconds(),
            "p_reason": reason,
        },
    )
    return payload if isinstance(payload, dict) else {"ok": False, "acquired": False, "reason": "device_lock_failed"}


def bind_device_lock_to_request(
    *,
    device_id: str,
    worker_id: str,
    request_id: str,
) -> dict[str, Any]:
    payload = supabase_client.call_rpc(
        "auto_restart_bind_device_lock_to_request",
        {
            "p_device_id": device_id,
            "p_worker_id": worker_id,
            "p_request_id": request_id,
            "p_lease_seconds": _lease_seconds(),
        },
    )
    return payload if isinstance(payload, dict) else {"ok": False, "bound": False, "reason": "bind_failed"}


def renew_device_lock(
    *,
    device_id: str,
    worker_id: str,
    request_id: str,
) -> dict[str, Any]:
    params = {
            "p_device_id": device_id,
            "p_worker_id": worker_id,
            "p_request_id": request_id,
            "p_lease_seconds": _lease_seconds(),
        }
    try:
        payload = supabase_client.call_rpc_once(
            "auto_restart_renew_device_lock", params, timeout_seconds=5.0
        )
    except Exception:
        canonical = _load_device_lock(device_id)
        if (
            canonical
            and str(canonical.get("worker_id") or "") == worker_id
            and str(canonical.get("request_id") or "") == request_id
        ):
            return {"ok": True, "renewed": True, "reconciled": True}
        raise
    return payload if isinstance(payload, dict) else {"ok": False, "renewed": False, "reason": "renew_failed"}


def transfer_device_lock(
    *,
    device_id: str,
    request_id: str,
    new_worker_id: str,
) -> dict[str, Any]:
    payload = supabase_client.call_rpc(
        "auto_restart_transfer_device_lock",
        {
            "p_device_id": device_id,
            "p_request_id": request_id,
            "p_new_worker_id": new_worker_id,
            "p_lease_seconds": _lease_seconds(),
        },
    )
    return payload if isinstance(payload, dict) else {"ok": False, "transferred": False, "reason": "transfer_failed"}


def release_device_lock(
    *,
    device_id: str,
    worker_id: str,
    request_id: str | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "p_device_id": device_id,
        "p_worker_id": worker_id,
    }
    if request_id:
        params["p_request_id"] = request_id
    try:
        payload = supabase_client.call_rpc_once(
            "auto_restart_release_device_lock", params, timeout_seconds=5.0
        )
    except Exception:
        canonical = _load_device_lock(device_id)
        if canonical is None or (
            request_id
            and str(canonical.get("request_id") or "") != str(request_id)
        ):
            payload = {"ok": True, "released": True, "reconciled": True}
        else:
            raise
    result = payload if isinstance(payload, dict) else {"ok": True, "released": False}
    log(
        "info",
        "auto_restart_device_lock_released",
        device_id=device_id,
        worker_id=worker_id,
        request_id=request_id,
        released=bool(result.get("released")),
    )
    return result


def release_device_lock_for_request(
    *,
    request_id: str,
    worker_id: str,
    device_id: str | None = None,
) -> dict[str, Any]:
    """Release a tick-bound lock by request_id when device_id is not in dispatch context."""
    rid = str(request_id or "").strip()
    wid = str(worker_id or "").strip()
    if not rid or not wid:
        return {"ok": False, "released": False, "reason": "missing_identifiers"}
    did = str(device_id or "").strip()
    if not did:
        rows = supabase_client._request_json(
            "GET",
            "auto_restart_device_locks",
            query={
                "select": "device_id,worker_id",
                "request_id": f"eq.{rid}",
                "limit": "1",
            },
        ) or []
        if rows:
            did = str(rows[0].get("device_id") or "").strip()
            wid = str(rows[0].get("worker_id") or wid).strip()
    if not did:
        return {"ok": False, "released": False, "reason": "device_lock_missing"}
    return release_device_lock(device_id=did, worker_id=wid, request_id=rid)


def reconcile_stale_device_ui_leases(*, grace_seconds: int = 0) -> dict[str, Any]:
    payload = supabase_client.call_rpc(
        "reconcile_stale_device_ui_leases",
        {"p_grace_seconds": grace_seconds},
    )
    result = payload if isinstance(payload, dict) else {"ok": False, "reconciled": 0}
    if int(result.get("reconciled") or 0) > 0:
        log(
            "info",
            "device_ui_lease_stale_reconciled",
            reconciled=result.get("reconciled"),
            skipped_active=result.get("skipped_active"),
        )
    return result
