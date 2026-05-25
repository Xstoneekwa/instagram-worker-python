"""Best-effort ORF account incident publishing (ORF-3B-2)."""

from __future__ import annotations

from typing import Any

import config
import supabase_client
from logs import log
from runtime_events import redact_metadata

VALID_SEVERITIES = {"info", "warning", "error", "critical"}
VALID_STATUSES = {"open", "acknowledged", "resolved", "ignored"}


def _incidents_enabled() -> bool:
    return bool(getattr(config, "RUNTIME_INCIDENTS_ENABLED", False))


def _log_local_fallback_enabled() -> bool:
    return bool(getattr(config, "RUNTIME_INCIDENTS_LOG_LOCAL_FALLBACK", True))


def _debug_metadata_enabled() -> bool:
    return bool(getattr(config, "RUNTIME_INCIDENTS_INCLUDE_DEBUG_METADATA", False))


def _warn(event: str, **fields: Any) -> None:
    if _log_local_fallback_enabled():
        safe_fields = {
            key: value
            for key, value in fields.items()
            if key not in {"error", "metadata"}
        }
        if "error" in fields:
            safe_fields["error"] = str(fields["error"])[:500]
        log("warning", event, **safe_fields)


def _normalize_metadata(metadata: dict | None) -> dict:
    raw = dict(metadata or {})
    redacted = redact_metadata(raw, visibility="admin_only")
    if not isinstance(redacted, dict):
        return {}
    if _debug_metadata_enabled():
        return redacted
    return {k: v for k, v in redacted.items() if not str(k).startswith("debug_")}


def publish_account_incident(
    incident_type: str,
    dedupe_key: str,
    *,
    severity: str = "warning",
    status: str = "open",
    client_id: str | None = None,
    account_id: str | None = None,
    account_username: str | None = None,
    run_id: str | None = None,
    assignment_id: str | None = None,
    device_id: str | None = None,
    clone_id: str | None = None,
    source_event_id: str | None = None,
    source: str | None = "worker",
    reason: str | None = None,
    failure_reason: str | None = None,
    action_required: str | None = None,
    safe_client_message: str | None = None,
    assistant_message: str | None = None,
    admin_message: str | None = None,
    metadata: dict | None = None,
) -> dict:
    """Publish one account incident via RPC. Failures never escape to the worker."""
    itype = str(incident_type or "").strip()
    dkey = str(dedupe_key or "").strip()
    sev = str(severity or "warning").strip().lower()
    st = str(status or "open").strip().lower()

    if not _incidents_enabled():
        return {"published": False, "reason": "disabled", "dedupe_key": dkey or None}
    if not itype:
        _warn("runtime_incident_publish_skipped", reason="missing_incident_type")
        return {"published": False, "reason": "missing_incident_type", "dedupe_key": dkey or None}
    if not dkey:
        _warn("runtime_incident_publish_skipped", reason="missing_dedupe_key")
        return {"published": False, "reason": "missing_dedupe_key"}
    if sev not in VALID_SEVERITIES:
        _warn("runtime_incident_publish_skipped", reason="invalid_severity", severity=sev)
        return {"published": False, "reason": "invalid_severity", "dedupe_key": dkey}
    if st not in VALID_STATUSES:
        _warn("runtime_incident_publish_skipped", reason="invalid_status", status=st)
        return {"published": False, "reason": "invalid_status", "dedupe_key": dkey}
    if metadata is not None and not isinstance(metadata, dict):
        _warn("runtime_incident_publish_skipped", reason="invalid_metadata")
        return {"published": False, "reason": "invalid_metadata", "dedupe_key": dkey}

    payload: dict[str, Any] = {
        "incident_type": itype,
        "dedupe_key": dkey,
        "severity": sev,
        "status": st,
        "client_id": client_id or None,
        "account_id": account_id or None,
        "account_username": account_username or None,
        "run_id": run_id or None,
        "assignment_id": assignment_id or None,
        "device_id": device_id or None,
        "clone_id": clone_id or None,
        "source_event_id": source_event_id or None,
        "source": source or "worker",
        "reason": reason or None,
        "failure_reason": failure_reason or None,
        "action_required": action_required or None,
        "safe_client_message": safe_client_message or None,
        "assistant_message": assistant_message or None,
        "admin_message": admin_message or None,
        "metadata": _normalize_metadata(metadata),
    }
    try:
        row = supabase_client.upsert_account_incident(payload)
        return {
            "published": True,
            "reason": "published",
            "incident_id": row.get("id"),
            "dedupe_key": row.get("dedupe_key"),
            "status": row.get("status"),
            "severity": row.get("severity"),
            "occurrence_count": row.get("occurrence_count"),
        }
    except Exception as exc:
        _warn(
            "runtime_incident_publish_failed",
            incident_type=itype,
            dedupe_key=dkey,
            severity=sev,
            error=str(exc),
        )
        return {
            "published": False,
            "reason": "upsert_failed",
            "error": str(exc),
            "dedupe_key": dkey,
        }


def build_identity_mismatch_incident(
    *,
    account_id: str,
    expected_username: str,
    actual_username: str | None,
    run_id: str | None = None,
    run_type: str | None = None,
    verification_method: str | None = None,
    identity_evidence: Any = None,
    rename_disambiguation_status: str | None = None,
    metadata: dict | None = None,
) -> dict[str, Any]:
    """Pure builder for active_instagram_account_mismatch (no DB side effects)."""
    aid = str(account_id or "").strip()
    expected = str(expected_username or "").strip().lstrip("@")
    actual = str(actual_username or "").strip().lstrip("@") or "unknown"
    meta: dict[str, Any] = dict(metadata or {})
    meta.update(
        {
            "expected_username": expected or None,
            "actual_username": actual,
            "run_type": run_type,
            "verification_method": verification_method,
            "identity_evidence": identity_evidence,
            "rename_disambiguation_status": rename_disambiguation_status,
        }
    )
    meta = {k: v for k, v in meta.items() if v is not None}
    expected_label = expected or "unknown"
    return {
        "incident_type": "active_instagram_account_mismatch",
        "dedupe_key": f"account:{aid}:identity:mismatch:{actual}",
        "severity": "critical",
        "status": "open",
        "account_id": aid or None,
        "account_username": expected or None,
        "run_id": run_id,
        "source": "account_identity_guard",
        "reason": "active_instagram_account_mismatch",
        "failure_reason": "active_instagram_account_mismatch",
        "action_required": "Verify logged-in Instagram account.",
        "safe_client_message": "Automation paused for account safety.",
        "assistant_message": (
            "Account identity mismatch detected. Verify active Instagram account."
        ),
        "admin_message": (
            f"Expected {expected_label} but detected {actual} during preflight."
        ),
        "metadata": meta,
    }


def build_assignment_dispatch_incident(
    *,
    incident_type: str,
    account_id: str | None = None,
    assignment_id: str | None = None,
    device_id: str | None = None,
    clone_id: str | None = None,
    run_id: str | None = None,
    reason: str | None = None,
    failure_reason: str | None = None,
    action_required: str | None = None,
    metadata: dict | None = None,
) -> dict[str, Any]:
    """Pure builder for assignment dispatch incidents (no DB side effects)."""
    itype = str(incident_type or "").strip()
    aid = str(account_id or "").strip()
    suffix = assignment_id or device_id or run_id or "unknown"
    severity = "error" if itype == "assignment_dispatch_incompatible" else "warning"
    default_action = (
        "Review account assignment and device compatibility."
        if itype == "assignment_dispatch_incompatible"
        else "Assign a compatible device/clone before continuing."
    )
    return {
        "incident_type": itype,
        "dedupe_key": f"account:{aid}:dispatch:{itype}:{suffix}",
        "severity": severity,
        "status": "open",
        "account_id": aid or None,
        "assignment_id": assignment_id,
        "device_id": device_id,
        "clone_id": clone_id,
        "run_id": run_id,
        "source": "assignment_dispatch_resolver",
        "reason": reason or itype,
        "failure_reason": failure_reason or itype,
        "action_required": action_required or default_action,
        "safe_client_message": "Automation paused until assignment is corrected.",
        "assistant_message": "Assignment dispatch issue detected. Review device assignment.",
        "admin_message": f"Assignment dispatch incident: {itype}.",
        "metadata": dict(metadata or {}),
    }
