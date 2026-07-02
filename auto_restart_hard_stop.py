"""Auto Restart hard-stop: incident, dashboard action, scheduler exclusion helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import supabase_client
from auto_restart_runtime import AUTO_RESTART_TICK_SOURCE, is_hard_stop_reason, normalize_hard_stop_reason
from logs import log
from runtime_events import redact_metadata
from runtime_incidents import publish_account_incident

HUMAN_REVIEW_ACTION_TYPE = "review_auto_restart_hard_stop"

_REASON_TO_INCIDENT = {
    "challenge_blocked": {
        "incident_type": "instagram_challenge",
        "severity": "critical",
        "action_required": "Review Instagram challenge/checkpoint before resuming automation.",
    },
    "restriction_blocked": {
        "incident_type": "instagram_restriction",
        "severity": "critical",
        "action_required": "Review Instagram restriction/action block before resuming automation.",
    },
    "account_mismatch_blocked": {
        "incident_type": "active_instagram_account_mismatch",
        "severity": "critical",
        "action_required": "Verify logged-in Instagram account matches assignment.",
    },
    "ui_state_unknown_blocked": {
        "incident_type": "ui_state_unknown",
        "severity": "error",
        "action_required": "Human must classify UI state before automation resumes.",
    },
    "quota_inconsistency_blocked": {
        "incident_type": "auto_restart_quota_inconsistency",
        "severity": "error",
        "action_required": "Verify quota counters and resume plan before scheduling.",
    },
    "resume_plan_invalid": {
        "incident_type": "auto_restart_resume_plan_invalid",
        "severity": "error",
        "action_required": "Repair resume plan metadata before scheduling.",
    },
    "unknown_required_field": {
        "incident_type": "auto_restart_resume_plan_invalid",
        "severity": "error",
        "action_required": "Complete required resume plan fields before scheduling.",
    },
    "runtime_unknown_blocked": {
        "incident_type": "runtime_unknown_blocked",
        "severity": "critical",
        "action_required": "Investigate unclassified runtime failure before resuming.",
    },
}

_FORBIDDEN_METADATA_KEYS = {
    "password",
    "secret",
    "token",
    "xml",
    "screenshot",
    "webhook",
    "credentials",
    "adb_serial",
    "device_udid",
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _redact_evidence(metadata: dict[str, Any] | None) -> dict[str, Any]:
    raw = redact_metadata(dict(metadata or {}), visibility="admin_only")
    if not isinstance(raw, dict):
        return {}
    return {
        key: value
        for key, value in raw.items()
        if str(key).strip().lower() not in _FORBIDDEN_METADATA_KEYS
    }


def _incident_spec(reason: str) -> dict[str, str]:
    normalized = normalize_hard_stop_reason(reason)
    return dict(
        _REASON_TO_INCIDENT.get(
            normalized,
            {
                "incident_type": "runtime_unknown_blocked",
                "severity": "critical",
                "action_required": "Investigate automation hard stop before resuming.",
            },
        )
    )


def _incident_dedupe_key(*, account_id: str, reason: str) -> str:
    normalized = normalize_hard_stop_reason(reason)
    return f"account:{account_id}:auto_restart_hard_stop:{normalized}"


def _dashboard_dedupe_key(*, account_id: str, reason: str) -> str:
    normalized = normalize_hard_stop_reason(reason)
    return f"account:{account_id}:dashboard_action:{HUMAN_REVIEW_ACTION_TYPE}:{normalized}"


def cancel_pending_auto_restart_requests(
    *,
    account_id: str,
    worker_id: str | None = None,
    reason: str,
    exclude_request_id: str | None = None,
) -> list[str]:
    rows = supabase_client._request_json(
        "GET",
        "account_run_requests",
        query={
            "select": "id,status,metadata_safe",
            "account_id": f"eq.{account_id}",
            "status": "in.(queued,claimed,starting,running)",
            "limit": "50",
        },
    ) or []
    canceled: list[str] = []
    for row in rows:
        request_id = str(row.get("id") or "").strip()
        if not request_id or request_id == exclude_request_id:
            continue
        meta = dict(row.get("metadata_safe") or {})
        if not meta.get("auto_restart") or str(meta.get("source") or "") != AUTO_RESTART_TICK_SOURCE:
            continue
        try:
            from account_run_control import cancel_account_run_request

            cancel_account_run_request(
                request_id=request_id,
                account_id=account_id,
                actor_id=worker_id,
                reason=reason,
            )
            canceled.append(request_id)
        except Exception as exc:
            log(
                "warning",
                "auto_restart_hard_stop_cancel_failed",
                account_id=account_id,
                request_id=request_id,
                error_type=type(exc).__name__,
            )
    return canceled


def upsert_hard_stop_dashboard_action(
    *,
    account_id: str,
    client_id: str | None,
    incident_id: str | None,
    reason: str,
    request_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = normalize_hard_stop_reason(reason)
    dedupe_key = _dashboard_dedupe_key(account_id=account_id, reason=normalized)
    safe_metadata = _redact_evidence(
        {
            **(metadata or {}),
            "request_id": request_id,
            "hard_stop_reason": normalized,
            "source": "auto_restart_hard_stop",
        }
    )
    title_by_reason = {
        "challenge_blocked": "Instagram challenge — automation paused",
        "restriction_blocked": "Instagram restriction — automation paused",
        "account_mismatch_blocked": "Account identity mismatch — review required",
        "ui_state_unknown_blocked": "UI state unknown — human review required",
        "quota_inconsistency_blocked": "Quota inconsistency — scheduling blocked",
        "unknown_required_field": "Resume plan incomplete — scheduling blocked",
        "resume_plan_invalid": "Resume plan invalid — scheduling blocked",
        "runtime_unknown_blocked": "Runtime hard stop — human review required",
    }
    title = title_by_reason.get(normalized, "Auto Restart hard stop — human review required")
    payload = {
        "account_id": account_id,
        "client_id": client_id,
        "incident_id": incident_id,
        "action_type": HUMAN_REVIEW_ACTION_TYPE,
        "status": "pending",
        "severity": "critical" if normalized.endswith("_blocked") else "error",
        "audience": "admin",
        "requires_client_action": False,
        "blocking_campaign": True,
        "title": title,
        "safe_client_message": "Automation paused pending operator review.",
        "assistant_message": "Auto Restart hard stop recorded. Acknowledge and resolve after verification.",
        "admin_message": f"Hard stop reason: {normalized}",
        "action_label": "Review hard stop",
        "action_deep_link": f"/instagram-dashboard/credentials-actions?account_id={account_id}",
        "dedupe_key": dedupe_key,
        "metadata": safe_metadata,
    }
    existing = supabase_client._request_json(
        "GET",
        "account_dashboard_actions",
        query={
            "select": "id,status",
            "dedupe_key": f"eq.{dedupe_key}",
            "status": "in.(pending,acknowledged,pending_verification)",
            "limit": "1",
        },
    ) or []
    if existing:
        action_id = str(existing[0].get("id") or "").strip()
        supabase_client._request_json(
            "PATCH",
            "account_dashboard_actions",
            query={"id": f"eq.{action_id}"},
            body={
                "updated_at": _utc_now_iso(),
                "metadata": safe_metadata,
                "incident_id": incident_id,
            },
        )
        return {"published": True, "reason": "updated", "dashboard_action_id": action_id}
    inserted = supabase_client._request_json(
        "POST",
        "account_dashboard_actions",
        body=payload,
    )
    action_id = None
    if isinstance(inserted, list) and inserted:
        action_id = inserted[0].get("id")
    elif isinstance(inserted, dict):
        action_id = inserted.get("id")
    return {"published": True, "reason": "inserted", "dashboard_action_id": action_id}


def _dispatcher_host_from_worker_id(worker_id: str | None) -> str | None:
    normalized = str(worker_id or "").strip().lower()
    if not normalized.startswith("run-dispatcher:"):
        return None
    host = normalized.split(":", 1)[1].strip()
    return host or None


def execute_auto_restart_hard_stop(
    *,
    account_id: str,
    reason: str,
    request_id: str | None = None,
    run_id: str | None = None,
    device_id: str | None = None,
    app_instance_id: str | None = None,
    execution_worker_id: str | None = None,
    trigger_source: str | None = None,
    client_id: str | None = None,
    account_username: str | None = None,
    evidence: dict[str, Any] | None = None,
    cancel_pending: bool = True,
    worker_id: str | None = None,
) -> dict[str, Any]:
    normalized = normalize_hard_stop_reason(reason)
    if not is_hard_stop_reason(normalized):
        return {"executed": False, "reason": "not_hard_stop", "normalized_reason": normalized}

    spec = _incident_spec(normalized)
    dedupe_key = _incident_dedupe_key(account_id=account_id, reason=normalized)
    safe_evidence = _redact_evidence(
        {
            **(evidence or {}),
            "request_id": request_id,
            "run_id": run_id,
            "device_id": device_id,
            "app_instance_id": app_instance_id,
            "execution_worker_id": execution_worker_id,
            "worker_id": worker_id or execution_worker_id,
            "host_machine": _dispatcher_host_from_worker_id(worker_id or execution_worker_id),
            "trigger_source": trigger_source,
            "observed_at": _utc_now_iso(),
            "required_human_action": spec.get("action_required"),
        }
    )
    incident_result = publish_account_incident(
        incident_type=spec["incident_type"],
        dedupe_key=dedupe_key,
        severity=spec["severity"],
        status="open",
        account_id=account_id,
        account_username=account_username,
        run_id=run_id,
        device_id=device_id,
        clone_id=app_instance_id,
        source="auto_restart_hard_stop",
        reason=normalized,
        failure_reason=normalized,
        action_required=spec["action_required"],
        safe_client_message="Automation paused for account safety.",
        assistant_message=f"Auto Restart hard stop: {normalized}. Human review required.",
        admin_message=f"Auto Restart hard stop ({normalized}) for account {account_id}.",
        metadata=safe_evidence,
    )
    incident_id = incident_result.get("incident_id")
    dashboard_result = upsert_hard_stop_dashboard_action(
        account_id=account_id,
        client_id=client_id,
        incident_id=incident_id,
        reason=normalized,
        request_id=request_id,
        metadata=safe_evidence,
    )
    canceled_request_ids: list[str] = []
    if cancel_pending:
        canceled_request_ids = cancel_pending_auto_restart_requests(
            account_id=account_id,
            worker_id=worker_id or execution_worker_id,
            reason=normalized,
            exclude_request_id=request_id,
        )
    notification_result: dict[str, Any] = {"dispatched": False, "reason": "skipped"}
    try:
        import config
        from incident_notifications import dispatch_account_incident_notifications

        if bool(getattr(config, "INCIDENT_NOTIFICATIONS_ENABLED", False)) and incident_id:
            notification_result = dispatch_account_incident_notifications(
                incident_ids=[str(incident_id)],
                channels=getattr(config, "INCIDENT_NOTIFICATION_CHANNELS", None),
            )
    except Exception as exc:
        notification_result = {
            "dispatched": False,
            "reason": "notification_failed_fail_open",
            "error_type": type(exc).__name__,
        }
        log(
            "warning",
            "auto_restart_hard_stop_notification_failed",
            account_id=account_id,
            request_id=request_id,
            hard_stop_reason=normalized,
            error_type=type(exc).__name__,
        )

    log(
        "warning",
        "auto_restart_hard_stop_executed",
        account_id=account_id,
        request_id=request_id,
        hard_stop_reason=normalized,
        incident_id=incident_id,
        dashboard_action_id=dashboard_result.get("dashboard_action_id"),
        canceled_request_count=len(canceled_request_ids),
    )
    return {
        "executed": True,
        "reason": normalized,
        "incident": incident_result,
        "dashboard_action": dashboard_result,
        "canceled_request_ids": canceled_request_ids,
        "notification": notification_result,
        "incident_key": dedupe_key,
    }
