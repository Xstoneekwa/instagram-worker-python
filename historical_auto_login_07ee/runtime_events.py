"""Best-effort ORF runtime event publishing."""

from __future__ import annotations

import hashlib
from typing import Any

import config
import supabase_client
from logs import log

VALID_SEVERITIES = {"debug", "info", "warning", "error", "critical"}
VALID_VISIBILITIES = {"admin_only", "assistant_safe", "client_safe"}
SENSITIVE_KEY_FRAGMENTS = {
    "password",
    "pass",
    "secret",
    "token",
    "authorization",
    "service_role",
    "api_key",
    "access_token",
    "refresh_token",
    "device_udid",
    "credential",
    "cookie",
    "session",
}
OPS_SENSITIVE_KEYS = {
    "adb_serial",
    "device_udid",
    "host_machine",
    "hub_label",
    "hub_port",
}


def _events_enabled() -> bool:
    return bool(getattr(config, "RUNTIME_EVENTS_ENABLED", False))


def _debug_enabled() -> bool:
    return bool(getattr(config, "RUNTIME_EVENTS_INCLUDE_DEBUG", False))


def _log_local_fallback_enabled() -> bool:
    return bool(getattr(config, "RUNTIME_EVENTS_LOG_LOCAL_FALLBACK", True))


def _sensitive_key(key: str) -> bool:
    normalized = str(key or "").strip().lower()
    return any(fragment in normalized for fragment in SENSITIVE_KEY_FRAGMENTS)


def _adb_serial_safe(value: Any) -> dict[str, str] | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    return {
        "adb_serial_suffix": raw[-4:],
        "adb_serial_hash": hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12],
    }


def redact_metadata(value: Any, *, visibility: str = "admin_only") -> Any:
    """Recursively remove secrets and client-visible ops internals."""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            key_s = str(key)
            key_l = key_s.lower()
            if _sensitive_key(key_s):
                continue
            if visibility != "admin_only" and key_l in OPS_SENSITIVE_KEYS:
                if key_l == "adb_serial":
                    safe = _adb_serial_safe(item)
                    if safe:
                        out.update(safe)
                continue
            if key_l == "adb_serial":
                safe = _adb_serial_safe(item)
                if safe:
                    out.update(safe)
                continue
            out[key_s] = redact_metadata(item, visibility=visibility)
        return out
    if isinstance(value, list):
        return [redact_metadata(item, visibility=visibility) for item in value]
    return value


def _warn(event: str, **fields: Any) -> None:
    if _log_local_fallback_enabled():
        log("warning", event, **fields)


def publish_runtime_event(
    event_type: str,
    *,
    severity: str = "info",
    visibility: str = "admin_only",
    account_id: str | None = None,
    run_id: str | None = None,
    assignment_id: str | None = None,
    device_id: str | None = None,
    clone_id: str | None = None,
    job_id: str | None = None,
    dm_type: str | None = None,
    source: str = "worker",
    reason: str | None = None,
    message: str | None = None,
    metadata: dict | None = None,
) -> dict:
    """Publish one ORF event. Telemetry failures never escape to the worker."""
    event = str(event_type or "").strip()
    sev = str(severity or "info").strip().lower()
    vis = str(visibility or "admin_only").strip().lower()

    if not _events_enabled():
        return {"published": False, "reason": "disabled"}
    if sev == "debug" and not _debug_enabled():
        return {"published": False, "reason": "debug_disabled"}
    if not event:
        return {"published": False, "reason": "missing_event_type"}
    if sev not in VALID_SEVERITIES:
        _warn("runtime_event_publish_skipped", reason="invalid_severity", severity=sev)
        return {"published": False, "reason": "invalid_severity"}
    if vis not in VALID_VISIBILITIES:
        _warn("runtime_event_publish_skipped", reason="invalid_visibility", visibility=vis)
        return {"published": False, "reason": "invalid_visibility"}

    body = {
        "event_type": event,
        "severity": sev,
        "visibility": vis,
        "account_id": (account_id or None),
        "run_id": (run_id or None),
        "assignment_id": (assignment_id or None),
        "device_id": (device_id or None),
        "clone_id": (clone_id or None),
        "job_id": (job_id or None),
        "dm_type": (dm_type or None),
        "source": (source or "worker"),
        "reason": (reason or None),
        "message": (message or None),
        "metadata": redact_metadata(metadata or {}, visibility=vis),
    }
    try:
        row = supabase_client.insert_runtime_event(body)
        return {"published": True, "id": row.get("id"), "row": row}
    except Exception as exc:
        _warn(
            "runtime_event_publish_failed",
            event_type=event,
            severity=sev,
            visibility=vis,
            error=str(exc),
        )
        return {"published": False, "reason": "insert_failed", "error": str(exc)}
