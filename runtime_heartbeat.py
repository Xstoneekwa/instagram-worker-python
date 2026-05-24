"""Best-effort ORF worker and device heartbeats."""

from __future__ import annotations

import os
import socket
import time
from typing import Any

import config
import supabase_client
from logs import log
from runtime_events import redact_metadata

WORKER_STATUSES = {"starting", "idle", "running", "stopping", "offline", "error", "unknown"}
DEVICE_STATUSES = {"online", "offline", "unauthorized", "busy", "maintenance", "unknown", "error"}

_LAST_WORKER_HEARTBEAT: dict[str, float] = {}
_LAST_DEVICE_HEARTBEAT: dict[str, float] = {}


def _heartbeats_enabled() -> bool:
    return bool(getattr(config, "RUNTIME_HEARTBEATS_ENABLED", False))


def _interval_seconds() -> float:
    try:
        return max(0.0, float(getattr(config, "RUNTIME_HEARTBEAT_INTERVAL_SECONDS", 30)))
    except (TypeError, ValueError):
        return 30.0


def _worker_id() -> str:
    configured = str(os.getenv("WORKER_ID") or "").strip()
    if configured:
        return configured
    return f"{socket.gethostname()}:{os.getpid()}"


def _git_sha() -> str:
    return str(os.getenv("GIT_SHA") or "unknown").strip() or "unknown"


def _skip_throttled(cache: dict[str, float], key: str, *, force: bool) -> bool:
    if force:
        cache[key] = time.monotonic()
        return False
    now = time.monotonic()
    previous = cache.get(key)
    if previous is not None and now - previous < _interval_seconds():
        return True
    cache[key] = now
    return False


def _warn(event: str, **fields: Any) -> None:
    if bool(getattr(config, "RUNTIME_EVENTS_LOG_LOCAL_FALLBACK", True)):
        log("warning", event, **fields)


def heartbeat_worker(
    worker_id: str | None = None,
    status: str = "unknown",
    account_id: str | None = None,
    run_id: str | None = None,
    assignment_id: str | None = None,
    device_id: str | None = None,
    clone_id: str | None = None,
    metadata: dict | None = None,
    *,
    force: bool = False,
) -> dict:
    """Upsert worker presence. Telemetry failures never escape to the worker."""
    if not _heartbeats_enabled():
        return {"published": False, "reason": "disabled"}
    st = str(status or "unknown").strip().lower()
    if st not in WORKER_STATUSES:
        _warn("worker_heartbeat_skipped", reason="invalid_status", status=st)
        return {"published": False, "reason": "invalid_status"}
    wid = str(worker_id or _worker_id()).strip()
    if not wid:
        return {"published": False, "reason": "missing_worker_id"}
    if _skip_throttled(_LAST_WORKER_HEARTBEAT, f"{wid}:{st}", force=force):
        return {"published": False, "reason": "throttled"}

    body = {
        "worker_id": wid,
        "host_machine": socket.gethostname(),
        "process_id": str(os.getpid()),
        "git_sha": _git_sha(),
        "status": st,
        "current_account_id": account_id or None,
        "current_run_id": run_id or None,
        "current_assignment_id": assignment_id or None,
        "current_device_id": device_id or None,
        "current_clone_id": clone_id or None,
        "metadata": redact_metadata(metadata or {}, visibility="admin_only"),
    }
    try:
        row = supabase_client.upsert_worker_heartbeat(body)
        return {"published": True, "worker_id": wid, "row": row}
    except Exception as exc:
        _warn("worker_heartbeat_failed", worker_id=wid, status=st, error=str(exc))
        return {"published": False, "reason": "upsert_failed", "error": str(exc)}


def heartbeat_device(
    device_id: str | None,
    status: str = "unknown",
    adb_serial: str | None = None,
    host_machine: str | None = None,
    account_id: str | None = None,
    assignment_id: str | None = None,
    clone_id: str | None = None,
    metadata: dict | None = None,
    *,
    force: bool = False,
) -> dict:
    """Upsert device presence when a real phone_devices.id is known."""
    if not _heartbeats_enabled():
        return {"published": False, "reason": "disabled"}
    did = str(device_id or "").strip()
    if not did:
        return {"published": False, "reason": "missing_device_id"}
    st = str(status or "unknown").strip().lower()
    if st not in DEVICE_STATUSES:
        _warn("device_heartbeat_skipped", reason="invalid_status", status=st, device_id=did)
        return {"published": False, "reason": "invalid_status"}
    if _skip_throttled(_LAST_DEVICE_HEARTBEAT, f"{did}:{st}", force=force):
        return {"published": False, "reason": "throttled"}

    body = {
        "device_id": did,
        "adb_serial": str(adb_serial or "").strip() or None,
        "host_machine": str(host_machine or socket.gethostname()).strip() or None,
        "status": st,
        "current_account_id": account_id or None,
        "current_assignment_id": assignment_id or None,
        "current_clone_id": clone_id or None,
        "metadata": redact_metadata(metadata or {}, visibility="admin_only"),
    }
    try:
        row = supabase_client.upsert_device_heartbeat(body)
        return {"published": True, "device_id": did, "row": row}
    except Exception as exc:
        _warn("device_heartbeat_failed", device_id=did, status=st, error=str(exc))
        return {"published": False, "reason": "upsert_failed", "error": str(exc)}


def mark_worker_stopping(**kwargs: Any) -> dict:
    return heartbeat_worker(status="stopping", force=True, **kwargs)


def mark_device_status(device_id: str | None, status: str, **kwargs: Any) -> dict:
    return heartbeat_device(device_id, status=status, force=True, **kwargs)
