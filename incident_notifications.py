"""ORF-4B dry-run account incident notification dispatcher."""

from __future__ import annotations

from typing import Any

import config
import supabase_client
from logs import log
from runtime_events import redact_metadata

VALID_CHANNELS = {"slack", "discord"}
SEVERITY_RANK = {"info": 0, "warning": 1, "error": 2, "critical": 3}
SENSITIVE_PAYLOAD_KEYS = {
    "service_role",
    "token",
    "secret",
    "cookie",
    "webhook",
    "raw_xml",
    "xml",
    "stack_trace",
    "device_udid",
    "adb_serial",
    "credentials",
    "password",
}
DISPATCHER_NAME = "incident_notifications"
DISPATCHER_VERSION = "orf-4b"


def _notifications_enabled() -> bool:
    return bool(getattr(config, "INCIDENT_NOTIFICATIONS_ENABLED", False))


def _fail_open_enabled() -> bool:
    return bool(getattr(config, "INCIDENT_NOTIFICATIONS_FAIL_OPEN", True))


def _dry_run_enabled() -> bool:
    return bool(getattr(config, "INCIDENT_NOTIFICATIONS_DRY_RUN", True))


def _warn(event: str, **fields: Any) -> None:
    safe_fields = {
        key: value
        for key, value in fields.items()
        if key not in {"payload", "metadata"}
    }
    if "error" in safe_fields:
        safe_fields["error"] = str(safe_fields["error"])[:500]
    log("warning", event, **safe_fields)


def parse_notification_channels(value: str | list[str] | tuple[str, ...] | None) -> list[str]:
    if isinstance(value, str):
        raw_items = value.split(",")
    elif isinstance(value, (list, tuple)):
        raw_items = list(value)
    else:
        raw_items = []
    out: list[str] = []
    for item in raw_items:
        channel = str(item or "").strip().lower()
        if channel in VALID_CHANNELS and channel not in out:
            out.append(channel)
    return out or ["slack"]


def severity_rank(severity: str) -> int:
    return SEVERITY_RANK.get(str(severity or "").strip().lower(), -1)


def is_severity_at_least(severity: str, min_severity: str) -> bool:
    return severity_rank(severity) >= severity_rank(min_severity)


def build_delivery_key(channel: str, incident_id: str) -> str:
    return f"{str(channel or '').strip().lower()}:{str(incident_id or '').strip()}:opened"


def _short_id(value: Any, *, length: int = 8) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    return text[:length] + "..."


def _safe_text(value: Any, *, max_len: int = 1000) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    lowered = text.lower()
    if any(marker in lowered for marker in ("service_role", "webhook_url", "authorization: bearer")):
        return "[redacted]"
    return text[:max_len]


def _redact_payload(value: Any) -> Any:
    redacted = redact_metadata(value, visibility="admin_only")
    if isinstance(redacted, dict):
        return {
            str(key): _redact_payload(item)
            for key, item in redacted.items()
            if not any(fragment in str(key).lower() for fragment in SENSITIVE_PAYLOAD_KEYS)
        }
    if isinstance(redacted, list):
        return [_redact_payload(item) for item in redacted]
    if isinstance(redacted, str):
        lowered = redacted.lower()
        if any(fragment in lowered for fragment in ("service_role", "webhook", "token", "secret", "cookie")):
            return "[redacted]"
    return redacted


def build_incident_notification_payload(incident: dict) -> dict:
    severity = str(incident.get("severity") or "warning").strip().lower()
    incident_type = str(incident.get("incident_type") or "unknown_incident").strip()
    status = str(incident.get("status") or "unknown").strip().lower()
    account_username = _safe_text(incident.get("account_username"), max_len=120)
    action_required = _safe_text(incident.get("action_required"))
    assistant_message = _safe_text(incident.get("assistant_message"))
    admin_message = _safe_text(incident.get("admin_message"))
    run_id = str(incident.get("run_id") or "").strip() or None
    last_seen_at = str(incident.get("last_seen_at") or "").strip() or None
    occurrence_count = incident.get("occurrence_count") or 1

    title = f"[{severity.upper()}] {incident_type}"
    message_parts = [
        title,
        f"Account: {account_username or 'unknown'} ({_short_id(incident.get('account_id')) or 'no-account-id'})",
        f"Status: {status} | Occurrences: {occurrence_count}",
    ]
    if last_seen_at:
        message_parts.append(f"Last seen: {last_seen_at}")
    if action_required:
        message_parts.append(f"Action: {action_required}")
    if run_id:
        message_parts.append(f"Run: {_short_id(run_id) or run_id}")
    message_parts.append("Dashboard: DASHBOARD_URL_PLACEHOLDER")

    payload = {
        "title": title,
        "text": "\n".join(message_parts),
        "severity": severity,
        "incident_type": incident_type,
        "account_username": account_username,
        "account_id_short": _short_id(incident.get("account_id")),
        "status": status,
        "occurrence_count": occurrence_count,
        "last_seen_at": last_seen_at,
        "action_required": action_required,
        "assistant_message": assistant_message,
        "admin_message": admin_message,
        "run_id": run_id,
        "dashboard_url": "DASHBOARD_URL_PLACEHOLDER",
    }
    return _redact_payload({k: v for k, v in payload.items() if v is not None})


def build_slack_payload(payload: dict) -> dict:
    safe = _redact_payload(dict(payload or {}))
    return {"text": str(safe.get("text") or safe.get("title") or "Incident notification")}


def build_discord_payload(payload: dict) -> dict:
    safe = _redact_payload(dict(payload or {}))
    return {"content": str(safe.get("text") or safe.get("title") or "Incident notification")}


def _sort_incidents(incidents: list[dict]) -> list[dict]:
    return sorted(
        incidents,
        key=lambda row: (
            severity_rank(str(row.get("severity") or "")),
            str(row.get("last_seen_at") or ""),
        ),
        reverse=True,
    )


def _empty_summary(*, dry_run: bool | None = None) -> dict[str, Any]:
    return {
        "dispatched": False,
        "reason": None,
        "selected_count": 0,
        "created_count": 0,
        "skipped_duplicate_count": 0,
        "skipped_disabled_count": 0,
        "errors_count": 0,
        "dry_run": _dry_run_enabled() if dry_run is None else dry_run,
    }


def dispatch_account_incident_notifications(
    *,
    channels: str | list[str] | tuple[str, ...] | None = None,
    min_severity: str | None = None,
    max_per_run: int | None = None,
) -> dict[str, Any]:
    if not _notifications_enabled():
        summary = _empty_summary()
        summary.update({"reason": "disabled", "skipped_disabled_count": 1})
        return summary

    dry_run = _dry_run_enabled()
    if not dry_run:
        summary = _empty_summary(dry_run=False)
        summary.update({"reason": "real_send_not_implemented"})
        return summary

    selected_channels = parse_notification_channels(
        channels if channels is not None else getattr(config, "INCIDENT_NOTIFICATIONS_CHANNELS", "slack")
    )
    threshold = str(
        min_severity
        if min_severity is not None
        else getattr(config, "INCIDENT_NOTIFICATIONS_MIN_SEVERITY", "warning")
    ).strip().lower()
    limit = max(1, int(max_per_run or getattr(config, "INCIDENT_NOTIFICATIONS_MAX_PER_RUN", 20)))
    summary = _empty_summary(dry_run=True)
    summary["reason"] = "dry_run"

    try:
        raw_incidents = supabase_client.load_account_incidents_to_notify(
            statuses=["open", "acknowledged"],
            min_severity=threshold,
            limit=max(limit * 4, limit),
        )
        incidents = [
            dict(row)
            for row in raw_incidents
            if str(row.get("status") or "").strip().lower() in {"open", "acknowledged"}
            and is_severity_at_least(str(row.get("severity") or ""), threshold)
            and str(row.get("id") or "").strip()
        ]
        incidents = _sort_incidents(incidents)[:limit]
        summary["selected_count"] = len(incidents)

        delivery_keys = [
            build_delivery_key(channel, str(incident.get("id")))
            for incident in incidents
            for channel in selected_channels
        ]
        existing = supabase_client.load_existing_incident_notifications_by_delivery_keys(delivery_keys)

        for incident in incidents:
            base_payload = build_incident_notification_payload(incident)
            for channel in selected_channels:
                delivery_key = build_delivery_key(channel, str(incident.get("id")))
                if delivery_key in existing:
                    summary["skipped_duplicate_count"] += 1
                    continue
                channel_payload = (
                    build_slack_payload(base_payload)
                    if channel == "slack"
                    else build_discord_payload(base_payload)
                )
                audit_payload = {
                    "channel": channel,
                    "message": base_payload,
                    "channel_payload": channel_payload,
                }
                row = {
                    "incident_id": incident.get("id"),
                    "channel": channel,
                    "status": "skipped",
                    "target": "dry-run",
                    "delivery_key": delivery_key,
                    "attempt_count": 0,
                    "payload": _redact_payload(audit_payload),
                    "metadata": {
                        "dry_run": True,
                        "reason": "dry_run_no_webhook_sent",
                        "dispatcher": DISPATCHER_NAME,
                        "dispatcher_version": DISPATCHER_VERSION,
                    },
                }
                supabase_client.create_account_incident_notification(row)
                existing[delivery_key] = row
                summary["created_count"] += 1
        summary["dispatched"] = True
        return summary
    except Exception as exc:
        summary["errors_count"] += 1
        summary["reason"] = "dispatch_failed"
        summary["error"] = str(exc)
        _warn("incident_notification_dispatch_failed", error=str(exc))
        if _fail_open_enabled():
            return summary
        raise
