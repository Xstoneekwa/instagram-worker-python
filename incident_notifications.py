"""ORF-4 account incident notification dispatcher."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest

import config
import incident_notification_channel_config as channel_config
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
DISPATCHER_VERSION = "orf-4d"
DRY_RUN_DISPATCHER_VERSION = "orf-4b"
WEBHOOK_USER_AGENT = "PhoneFarmIncidentNotifier/1.0 (+https://localhost)"
ACTION_CTA_LABEL = "Open Incidents/Actions"
CANONICAL_INCIDENTS_BASE_URL = "https://www.boostmybusinesses.com"


def _notifications_enabled() -> bool:
    return bool(getattr(config, "INCIDENT_NOTIFICATIONS_ENABLED", False))


def _fail_open_enabled() -> bool:
    return bool(getattr(config, "INCIDENT_NOTIFICATIONS_FAIL_OPEN", True))


def _dry_run_enabled() -> bool:
    return bool(getattr(config, "INCIDENT_NOTIFICATIONS_DRY_RUN", True))


def _http_timeout_seconds() -> int:
    try:
        return max(1, int(getattr(config, "INCIDENT_NOTIFICATIONS_HTTP_TIMEOUT_SECONDS", 10)))
    except (TypeError, ValueError):
        return 10


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _warn(event: str, **fields: Any) -> None:
    safe_fields = {
        key: value
        for key, value in fields.items()
        if key not in {"payload", "metadata"}
    }
    if "error" in safe_fields:
        safe_fields["error"] = _truncate_redact(safe_fields["error"])
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


def _channel_toggle_enabled(channel: str) -> bool:
    if bool(getattr(config, "INCIDENT_NOTIFICATIONS_CANONICAL_SETTINGS", True)):
        return channel_config.channel_enabled_for_dispatch(channel)
    normalized = str(channel or "").strip().lower()
    if normalized == "slack":
        return bool(getattr(config, "INCIDENT_NOTIFICATIONS_SLACK_ENABLED", True))
    if normalized == "discord":
        return bool(getattr(config, "INCIDENT_NOTIFICATIONS_DISCORD_ENABLED", True))
    return False


def resolve_dispatch_channels(
    value: str | list[str] | tuple[str, ...] | None = None,
) -> tuple[list[str], list[str]]:
    """Return (allow_list_channels, enabled_channels) after per-channel toggles."""
    if value is None:
        raw = getattr(config, "INCIDENT_NOTIFICATIONS_CHANNELS", "slack")
    else:
        raw = value
    allowed = parse_notification_channels(raw)
    enabled = [channel for channel in allowed if _channel_toggle_enabled(channel)]
    return allowed, enabled


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


def _safe_count(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


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


def _truncate_redact(value: Any, max_len: int = 500) -> str:
    text = "" if value is None else str(value)
    lowered = text.lower()
    if any(
        marker in lowered
        for marker in (
            "hooks.slack.com",
            "discord.com/api/webhooks",
            "discordapp.com/api/webhooks",
            "webhook_url",
            "service_role",
            "authorization: bearer",
            "access_token",
            "refresh_token",
            "cookie",
            "device_udid",
            "<node",
            "<?xml",
        )
    ):
        text = "[redacted]"
    return text[:max_len]


def _sanitize_error(value: Any) -> str:
    text = _truncate_redact(value)
    if text == "[redacted]":
        return "webhook_request_failed"
    return text or "webhook_request_failed"


def _incident_dashboard_url(incident: dict) -> str | None:
    """Secure internal link to the Admin incidents view (never a webhook)."""
    base = str(
        getattr(config, "INCIDENT_NOTIFICATIONS_DASHBOARD_BASE_URL", "")
        or CANONICAL_INCIDENTS_BASE_URL
    ).strip().rstrip("/")
    incident_id = str(incident.get("id") or "").strip()
    suffix = f"?incident_id={incident_id}" if incident_id else ""
    return f"{base}/instagram-dashboard/incidents{suffix}"


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
    metadata = incident.get("metadata") if isinstance(incident.get("metadata"), dict) else {}

    if str(metadata.get("domain") or "").strip().lower() == "auto_login":
        phase = _safe_text(metadata.get("phase"), max_len=80) or "unknown"
        reason_code = _safe_text(
            metadata.get("reason_code") or incident.get("failure_reason") or incident.get("reason"),
            max_len=160,
        ) or "unclassified_auto_login_failure"
        operator_label = _safe_text(metadata.get("operator_label"), max_len=160) or "Auto Login failed"
        retryable = metadata.get("retryable")
        retry_label = "yes" if retryable is True else "no" if retryable is False else "after correction"
        request_id = str(metadata.get("request_id") or metadata.get("run_request_id") or "").strip()
        device_id = str(incident.get("device_id") or metadata.get("device_id") or "").strip()
        app_instance_id = str(metadata.get("app_instance_id") or "").strip()
        title = f"[AUTO LOGIN {severity.upper()}] {operator_label}"
        message_parts = [
            title,
            f"Account: {account_username or 'unknown'}",
            f"Phase: {phase}",
            f"Reason: {reason_code}",
        ]
        if admin_message:
            message_parts.append(f"Summary: {admin_message}")
        if action_required:
            message_parts.append(f"Action: {action_required}")
        message_parts.append(f"Retry: {retry_label}")
        if request_id:
            message_parts.append(f"Request: {_short_id(request_id) or request_id}")
        if run_id:
            message_parts.append(f"Run: {_short_id(run_id) or run_id}")
        if device_id or app_instance_id:
            message_parts.append(
                "Device/instance: "
                f"{_short_id(device_id) or 'unknown'} / {_short_id(app_instance_id) or 'unknown'}"
            )
        if last_seen_at:
            message_parts.append(f"Date: {last_seen_at}")
        dashboard_url = _incident_dashboard_url(incident)
        payload = {
            "title": title,
            "text": "\n".join(message_parts),
            "severity": severity,
            "incident_type": incident_type,
            "domain": "auto_login",
            "phase": phase,
            "reason_code": reason_code,
            "account_username": account_username,
            "request_id": request_id or None,
            "run_id": run_id,
            "device_id_short": _short_id(device_id),
            "app_instance_id_short": _short_id(app_instance_id),
            "retryable": retryable,
            "dashboard_url": dashboard_url,
        }
        return _redact_payload({k: v for k, v in payload.items() if v is not None})

    title = f"[{severity.upper()}] {incident_type}"
    message_parts = [
        title,
        f"Account: {account_username or 'unknown'} ({_short_id(incident.get('account_id')) or 'no-account-id'})",
        f"Status: {status} | Occurrences: {occurrence_count}",
    ]
    phase_summary = (
        metadata.get("phase_summary")
        if isinstance(metadata.get("phase_summary"), dict)
        else {}
    )
    if phase_summary:
        requested = phase_summary.get("requested_phases")
        requested = requested if isinstance(requested, dict) else {}
        requested_label = ",".join(
            phase
            for phase in ("welcome", "follow", "unfollow")
            if requested.get(phase) is True
        ) or "unknown"
        auto_restart_label = (
            "allowed"
            if phase_summary.get("auto_restart_allowed") is True
            else "blocked"
            if phase_summary.get("auto_restart_blocked") is True
            else "unknown"
        )
        message_parts.extend(
            [
                (
                    "Phase: "
                    f"{_safe_text(phase_summary.get('blocked_phase'), max_len=80) or 'unknown'}"
                    f" | Requested: {requested_label}"
                ),
                (
                    "Follow: target "
                    f"{_safe_count(phase_summary.get('follow_target'))}"
                    f" | remaining {_safe_count(phase_summary.get('follow_remaining'))}"
                ),
                (
                    "Unfollow: actionable "
                    f"{_safe_count(phase_summary.get('unfollow_actionable_remaining'))}"
                    f" | hold {_safe_count(phase_summary.get('unfollow_candidates_on_hold'))}"
                    f" | terminal {_safe_count(phase_summary.get('unfollow_terminally_unavailable'))}"
                ),
                (
                    "Reason: "
                    f"{_safe_text(phase_summary.get('stable_reason'), max_len=160) or 'unknown'}"
                    f" ({_safe_text(phase_summary.get('reason_code'), max_len=160) or 'unclassified'})"
                ),
                f"Auto Restart: {auto_restart_label}",
                (
                    "Next: "
                    f"{_safe_text(phase_summary.get('suggested_next_action'), max_len=160) or 'operator_review'}"
                ),
            ]
        )
        next_retry_at = _safe_text(phase_summary.get("next_retry_at"), max_len=80)
        if next_retry_at:
            message_parts.append(f"Next retry: {next_retry_at}")
    if last_seen_at:
        message_parts.append(f"Last seen: {last_seen_at}")
    if action_required:
        message_parts.append(f"Action: {action_required}")
    if run_id:
        message_parts.append(f"Run: {_short_id(run_id) or run_id}")
    dashboard_url = _incident_dashboard_url(incident)
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
        "phase_summary": phase_summary or None,
        "dashboard_url": dashboard_url,
    }
    return _redact_payload({k: v for k, v in payload.items() if v is not None})


def build_notification_channel_payload(channel: str, payload: dict) -> dict:
    safe = _redact_payload(dict(payload or {}))
    text = str(safe.get("text") or safe.get("title") or "Incident notification")
    dashboard_url = str(safe.get("dashboard_url") or "").strip()
    normalized = str(channel or "").strip().lower()
    if dashboard_url:
        if normalized == "slack":
            text = f"{text}\n<{dashboard_url}|{ACTION_CTA_LABEL}>"
        elif normalized == "discord":
            text = f"{text}\n[{ACTION_CTA_LABEL}]({dashboard_url})"
    if normalized == "slack":
        return {"text": text}
    if normalized == "discord":
        return {"content": text}
    raise ValueError("invalid_notification_channel")


def build_slack_payload(payload: dict) -> dict:
    return build_notification_channel_payload("slack", payload)


def build_discord_payload(payload: dict) -> dict:
    return build_notification_channel_payload("discord", payload)


def dispatch_operator_review_action_notification(
    *,
    event: str,
    action_id: str,
    incident_id: str,
    account_id: str,
    account_username: str,
    reason: str,
    final_status: str,
    operator_id: str = "system",
) -> dict[str, Any]:
    """Send one idempotent operator-review notification per configured channel."""
    normalized_event = str(event or "").strip().lower()
    if normalized_event not in {"created", "resolved"}:
        return {"sent_count": 0, "failed_count": 0, "reason": "invalid_event"}
    if not _notifications_enabled() or _dry_run_enabled():
        return {"sent_count": 0, "failed_count": 0, "reason": "disabled_or_dry_run"}

    title = "Operator review required" if normalized_event == "created" else "Operator review resolved"
    payload = {
        "title": title,
        "text": "\n".join(
            [
                title,
                f"Account: @{account_username or 'unknown'} ({_short_id(account_id) or 'unknown'})",
                f"Reason: {reason or 'operator_review_required'}",
                f"State: {final_status}",
                f"Operator: {operator_id or 'system'}",
                f"Action: {_short_id(action_id) or 'unknown'}",
            ]
        ),
        "dashboard_url": _incident_dashboard_url({"id": incident_id}),
        "cta_label": ACTION_CTA_LABEL,
        "action_id": action_id,
    }
    allowed_channels, selected_channels = resolve_dispatch_channels()
    summary = {"sent_count": 0, "failed_count": 0, "skipped_count": 0, "reason": "real_send"}
    for channel in allowed_channels:
        if channel not in selected_channels:
            summary["skipped_count"] += 1
            continue
        delivery_key = f"{channel}:{incident_id}:operator_review_{normalized_event}:{action_id}"
        existing = supabase_client.load_existing_incident_notifications_by_delivery_keys([delivery_key]).get(delivery_key)
        if str((existing or {}).get("status") or "").strip().lower() == "sent":
            summary["skipped_count"] += 1
            continue
        row = {
            "incident_id": incident_id,
            "channel": channel,
            "status": "pending",
            "target": "redacted",
            "delivery_key": delivery_key,
            "attempt_count": 1,
            "last_attempt_at": _utc_now_iso(),
            "payload": _audit_payload(channel, payload),
            "metadata": {
                "dispatcher": DISPATCHER_NAME,
                "notification_type": f"operator_review_{normalized_event}",
                "redacted": True,
            },
        }
        created = existing or supabase_client.create_account_incident_notification(row)
        result = send_notification_webhook(channel, payload)
        notification_id = str(created.get("id") or "").strip()
        if result.get("ok"):
            update = {
                "status": "sent",
                "delivered_at": _utc_now_iso(),
                "response_status": result.get("response_status"),
                "last_error": None,
            }
            summary["sent_count"] += 1
        else:
            update = _failure_update(result)
            summary["failed_count"] += 1
        if notification_id:
            supabase_client.update_account_incident_notification(notification_id, update)
    return summary


def build_python_retries_exhausted_notification(
    *,
    account_username: str,
    run_id: str | None,
    business_session_id: str,
    phase: str,
    root_failure_code: str,
    failure_signature: str,
    quota_remaining: dict[str, Any] | None,
    cleanup_completed: bool,
) -> dict[str, Any]:
    """Build the exact redacted informational message from the locked policy."""
    title = "[INFO] Python runtime retries exhausted"
    quota = dict(quota_remaining or {})
    quota_label = ", ".join(
        f"{key}={value}"
        for key, value in sorted(quota.items())
        if key in {"follow", "unfollow", "welcome", "outreach", "total"}
        and isinstance(value, (int, float))
    ) or "unknown"
    lines = [
        title,
        f"Le run de {str(account_username or 'unknown').strip()} a rencontré un bug Python interne.",
        "Deux relances automatiques ont été effectuées sans succès.",
        "Aucun challenge, aucune restriction et aucun risque Instagram n’ont été détectés.",
        "Aucune action sur le compte Instagram, aucun patch manuel du compte et aucune review opérateur ne sont nécessaires.",
        "La campagne n’est pas bloquée pour une raison de sécurité.",
        "L’événement est transmis à l’équipe technique à titre informatif.",
        f"Run: {_short_id(run_id) or 'unknown'}",
        f"Business session: {_short_id(business_session_id) or 'unknown'}",
        f"Phase: {str(phase or 'unknown')[:40]}",
        f"Root failure code: {str(root_failure_code or 'unknown')[:80]}",
        f"Failure signature: {str(failure_signature or 'unknown')[:120]}",
        "Relances effectuées: 2/2",
        f"Quota restant: {quota_label}",
        f"Cleanup: {'completed' if cleanup_completed else 'uncertain'}",
    ]
    return {
        "title": title,
        "text": "\n".join(lines),
        "severity": "warning",
        "notification_type": "python_runtime_retries_exhausted",
    }


def _load_retry_proof(
    *, business_session_id: str, failure_signature: str
) -> set[int]:
    rows = supabase_client._request_json(
        "GET",
        "auto_restart_decisions",
        query={
            "select": "business_session_id,decision,metadata_safe",
            "business_session_id": f"eq.{business_session_id}",
            "decision": "eq.enqueued",
            "limit": "20",
        },
    ) or []
    indexes: set[int] = set()
    for row in rows:
        metadata = row.get("metadata_safe") if isinstance(row, dict) else None
        if not isinstance(metadata, dict):
            continue
        if str(metadata.get("failure_signature") or "") != failure_signature:
            continue
        try:
            retry_index = int(metadata.get("retry_index"))
        except (TypeError, ValueError):
            continue
        if retry_index in {1, 2}:
            indexes.add(retry_index)
    return indexes


def dispatch_python_retries_exhausted_notification(
    *,
    account_id: str,
    account_username: str,
    run_id: str | None,
    request_id: str,
    business_session_id: str,
    phase: str,
    root_failure_code: str,
    failure_signature: str,
    quota_remaining: dict[str, Any] | None,
    cleanup_completed: bool,
) -> dict[str, Any]:
    """Send one deduplicated technical notification, without an incident row."""
    if not _notifications_enabled() or _dry_run_enabled():
        return {"sent_count": 0, "reason": "disabled_or_dry_run"}
    try:
        if _load_retry_proof(
            business_session_id=business_session_id,
            failure_signature=failure_signature,
        ) != {1, 2}:
            return {"sent_count": 0, "reason": "retry_proof_incomplete"}
    except Exception as exc:
        return {
            "sent_count": 0,
            "reason": "retry_proof_unavailable",
            "error": _truncate_redact(exc),
        }

    payload = build_python_retries_exhausted_notification(
        account_username=account_username,
        run_id=run_id,
        business_session_id=business_session_id,
        phase=phase,
        root_failure_code=root_failure_code,
        failure_signature=failure_signature,
        quota_remaining=quota_remaining,
        cleanup_completed=cleanup_completed,
    )
    allowed_channels, selected_channels = resolve_dispatch_channels()
    summary = {"sent_count": 0, "failed_count": 0, "skipped_count": 0, "reason": "real_send"}
    for channel in allowed_channels:
        if channel not in selected_channels:
            summary["skipped_count"] += 1
            continue
        delivery_key = (
            f"technical:{channel}:{business_session_id}:"
            f"{failure_signature}:retries_exhausted"
        )
        existing = supabase_client._request_json(
            "GET",
            "auto_restart_decisions",
            query={"select": "id", "idempotency_key": f"eq.{delivery_key}", "limit": "1"},
        ) or []
        if existing:
            summary["skipped_count"] += 1
            continue
        rows = supabase_client._request_json(
            "POST",
            "auto_restart_decisions",
            query={"on_conflict": "idempotency_key"},
            body={
                "request_id": request_id,
                "idempotency_key": delivery_key,
                "actor": "system",
                "account_id": account_id,
                "business_session_id": business_session_id,
                "prior_run_id": run_id,
                "action": "technical_notification",
                "decision": "notification_pending",
                "reason": "recoverable_python_bug_retries_exhausted",
                "mode": "production",
                "restart_count_window": 2,
                "metadata_safe": {
                    "channel": channel,
                    "severity": "warning",
                    "failure_signature": failure_signature,
                    "root_failure_code": root_failure_code,
                    "retry_index": 2,
                    "retries_completed": "2/2",
                },
            },
            prefer_representation=True,
            prefer_resolution="resolution=ignore-duplicates",
        ) or []
        # The unique idempotency key is the send claim. A concurrent
        # dispatcher that loses the insert race gets no represented row and
        # must not call the webhook.
        if not rows:
            summary["skipped_count"] += 1
            continue
        decision_id = str(rows[0].get("id") or "") if rows else ""
        result = send_notification_webhook(channel, payload)
        delivered = bool(result.get("ok"))
        if delivered:
            summary["sent_count"] += 1
        else:
            summary["failed_count"] += 1
        if decision_id:
            supabase_client._request_json(
                "PATCH",
                "auto_restart_decisions",
                query={"id": f"eq.{decision_id}"},
                body={
                    "decision": "notification_sent" if delivered else "notification_failed",
                    "metadata_safe": {
                        "channel": channel,
                        "severity": "warning",
                        "failure_signature": failure_signature,
                        "root_failure_code": root_failure_code,
                        "retry_index": 2,
                        "retries_completed": "2/2",
                        "delivery_status": "sent" if delivered else "failed",
                        "delivery_reason": None
                        if delivered
                        else _truncate_redact(result.get("reason")),
                    },
                },
                prefer_representation=False,
            )
    return summary


def _post_json_webhook(url: str, body: dict, timeout: int) -> dict:
    raw_url = str(url or "").strip()
    if not raw_url:
        return {"ok": False, "reason": "config_missing_webhook"}
    data = json.dumps(_redact_payload(body or {}), separators=(",", ":")).encode("utf-8")
    req = urlrequest.Request(
        raw_url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent": WEBHOOK_USER_AGENT,
        },
    )
    try:
        with urlrequest.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            status = int(getattr(resp, "status", 200) or 200)
            preview = _truncate_redact(raw.decode("utf-8", errors="replace"))
            return {
                "ok": 200 <= status < 300,
                "response_status": status,
                "response_body_preview": preview,
            }
    except urlerror.HTTPError as exc:
        raw = exc.read()
        return {
            "ok": False,
            "reason": f"http_status_{int(exc.code)}",
            "response_status": int(exc.code),
            "response_body_preview": _truncate_redact(raw.decode("utf-8", errors="replace")),
            "last_error": f"http_status_{int(exc.code)}",
        }
    except Exception as exc:
        return {
            "ok": False,
            "reason": "webhook_request_failed",
            "last_error": _sanitize_error(exc),
        }


def send_slack_webhook(payload: dict, webhook_url: str) -> dict:
    if not str(webhook_url or "").strip():
        return {"ok": False, "reason": "config_missing_webhook"}
    return _post_json_webhook(
        webhook_url,
        build_slack_payload(payload),
        _http_timeout_seconds(),
    )


def send_discord_webhook(payload: dict, webhook_url: str) -> dict:
    if not str(webhook_url or "").strip():
        return {"ok": False, "reason": "config_missing_webhook"}
    return _post_json_webhook(
        webhook_url,
        build_discord_payload(payload),
        _http_timeout_seconds(),
    )


def send_notification_webhook(channel: str, channel_payload: dict) -> dict:
    normalized = str(channel or "").strip().lower()
    effective = channel_config.resolve_effective_channel_config(normalized)
    if not effective.get("send_allowed"):
        return {
            "ok": False,
            "reason": effective.get("reason") or "channel_not_configured",
        }
    webhook_url = str(effective.get("webhook_url") or "").strip()
    if normalized == "slack":
        return send_slack_webhook(channel_payload, webhook_url)
    if normalized == "discord":
        return send_discord_webhook(channel_payload, webhook_url)
    return {"ok": False, "reason": "invalid_channel"}


def _webhook_configured(channel: str) -> bool:
    return channel_config.webhook_configured_for_dispatch(channel)


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
        "attempted_count": 0,
        "sent_count": 0,
        "failed_count": 0,
        "retried_count": 0,
        "retry_exhausted_count": 0,
        "skipped_duplicate_count": 0,
        "skipped_disabled_count": 0,
        "skipped_channel_disabled_count": 0,
        "selected_channels": [],
        "enabled_channels": [],
        "errors_count": 0,
        "real_send_enabled": False,
        "dry_run": _dry_run_enabled() if dry_run is None else dry_run,
    }


def _audit_payload(channel: str, base_payload: dict) -> dict:
    channel_payload = (
        build_slack_payload(base_payload)
        if channel == "slack"
        else build_discord_payload(base_payload)
    )
    return _redact_payload(
        {
            "channel": channel,
            "message": base_payload,
            "channel_payload": channel_payload,
        }
    )


def _base_notification_row(
    *,
    incident: dict,
    channel: str,
    delivery_key: str,
    payload: dict,
    dry_run: bool,
    status: str,
    target: str,
    attempt_count: int,
    metadata_reason: str,
) -> dict:
    return {
        "incident_id": incident.get("id"),
        "channel": channel,
        "status": status,
        "target": target,
        "delivery_key": delivery_key,
        "attempt_count": attempt_count,
        "payload": payload,
        "metadata": {
            "dry_run": dry_run,
            "reason": metadata_reason,
            "dispatcher": DISPATCHER_NAME,
            "dispatcher_version": DRY_RUN_DISPATCHER_VERSION if dry_run else DISPATCHER_VERSION,
        },
    }


def _failure_update(send_result: dict) -> dict:
    return {
        "status": "failed",
        "delivered_at": None,
        "response_status": send_result.get("response_status"),
        "response_body_preview": _truncate_redact(send_result.get("response_body_preview")),
        "last_error": _truncate_redact(
            send_result.get("last_error")
            or send_result.get("reason")
            or "webhook_request_failed"
        ),
    }


def _max_attempts() -> int:
    try:
        return max(1, int(getattr(config, "INCIDENT_NOTIFICATIONS_MAX_ATTEMPTS", 3)))
    except (TypeError, ValueError):
        return 3


def _retry_eligible(existing_row: dict, *, dry_run: bool) -> bool:
    """Failed/stuck-pending rows are retried with a bounded attempt budget."""
    if dry_run:
        return False
    status = str(existing_row.get("status") or "").strip().lower()
    if status not in {"failed", "pending"}:
        return False
    try:
        attempts = int(existing_row.get("attempt_count") or 0)
    except (TypeError, ValueError):
        attempts = 0
    return attempts < _max_attempts()


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
    allowed_channels, selected_channels = resolve_dispatch_channels(channels)
    threshold = str(
        min_severity
        if min_severity is not None
        else getattr(config, "INCIDENT_NOTIFICATIONS_MIN_SEVERITY", "warning")
    ).strip().lower()
    limit = max(1, int(max_per_run or getattr(config, "INCIDENT_NOTIFICATIONS_MAX_PER_RUN", 20)))
    summary = _empty_summary(dry_run=dry_run)
    summary["selected_channels"] = list(allowed_channels)
    summary["enabled_channels"] = list(selected_channels)
    summary["reason"] = "dry_run" if dry_run else "real_send"
    summary["real_send_enabled"] = not dry_run
    if not selected_channels:
        summary["reason"] = "channels_disabled"
        summary["dispatched"] = True
        return summary

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
            for channel in allowed_channels:
                if channel not in selected_channels:
                    summary["skipped_channel_disabled_count"] += 1
                    continue
                delivery_key = build_delivery_key(channel, str(incident.get("id")))
                existing_row = existing.get(delivery_key)
                if existing_row is not None:
                    if not _retry_eligible(existing_row, dry_run=dry_run):
                        existing_status = str(existing_row.get("status") or "").strip().lower()
                        if existing_status in {"failed", "pending"} and not dry_run:
                            summary["retry_exhausted_count"] += 1
                        else:
                            summary["skipped_duplicate_count"] += 1
                        continue
                    # Bounded retry: re-attempt this channel delivery in place.
                    notification_id = str(existing_row.get("id") or "").strip()
                    try:
                        previous_attempts = int(existing_row.get("attempt_count") or 0)
                    except (TypeError, ValueError):
                        previous_attempts = 0
                    summary["attempted_count"] += 1
                    summary["retried_count"] += 1
                    send_result = send_notification_webhook(channel, base_payload)
                    if send_result.get("ok"):
                        update = {
                            "status": "sent",
                            "delivered_at": _utc_now_iso(),
                            "response_status": send_result.get("response_status"),
                            "response_body_preview": _truncate_redact(
                                send_result.get("response_body_preview")
                            ),
                            "last_error": None,
                        }
                        summary["sent_count"] += 1
                    else:
                        update = _failure_update(send_result)
                        summary["failed_count"] += 1
                    update["attempt_count"] = previous_attempts + 1
                    update["last_attempt_at"] = _utc_now_iso()
                    if notification_id:
                        supabase_client.update_account_incident_notification(
                            notification_id,
                            update,
                        )
                    existing[delivery_key] = {**existing_row, **update}
                    continue
                audit_payload = _audit_payload(channel, base_payload)
                if dry_run:
                    row = _base_notification_row(
                        incident=incident,
                        channel=channel,
                        status="skipped",
                        target="dry-run",
                        delivery_key=delivery_key,
                        attempt_count=0,
                        payload=audit_payload,
                        dry_run=True,
                        metadata_reason="dry_run_no_webhook_sent",
                    )
                    supabase_client.create_account_incident_notification(row)
                    existing[delivery_key] = row
                    summary["created_count"] += 1
                    continue

                summary["attempted_count"] += 1
                if not _webhook_configured(channel):
                    effective = channel_config.resolve_effective_channel_config(channel)
                    failure_reason = str(
                        effective.get("reason") or "channel_not_configured"
                    )
                    failed_row = _base_notification_row(
                        incident=incident,
                        channel=channel,
                        status="failed",
                        target=channel,
                        delivery_key=delivery_key,
                        attempt_count=1,
                        payload=audit_payload,
                        dry_run=False,
                        metadata_reason=failure_reason,
                    )
                    failed_row.update(
                        {
                            "last_attempt_at": _utc_now_iso(),
                            "last_error": failure_reason,
                        }
                    )
                    supabase_client.create_account_incident_notification(failed_row)
                    existing[delivery_key] = failed_row
                    summary["created_count"] += 1
                    summary["failed_count"] += 1
                    continue

                pending_row = _base_notification_row(
                    incident=incident,
                    channel=channel,
                    status="pending",
                    target=channel,
                    delivery_key=delivery_key,
                    attempt_count=1,
                    payload=audit_payload,
                    dry_run=False,
                    metadata_reason="webhook_send_attempted",
                )
                pending_row["last_attempt_at"] = _utc_now_iso()
                created = supabase_client.create_account_incident_notification(pending_row)
                summary["created_count"] += 1
                notification_id = str(created.get("id") or "").strip()
                send_result = send_notification_webhook(channel, base_payload)
                if send_result.get("ok"):
                    update = {
                        "status": "sent",
                        "delivered_at": _utc_now_iso(),
                        "response_status": send_result.get("response_status"),
                        "response_body_preview": _truncate_redact(
                            send_result.get("response_body_preview")
                        ),
                        "last_error": None,
                    }
                    summary["sent_count"] += 1
                else:
                    update = _failure_update(send_result)
                    summary["failed_count"] += 1
                if notification_id:
                    supabase_client.update_account_incident_notification(notification_id, update)
                existing[delivery_key] = {**pending_row, **update}
        summary["dispatched"] = True
        return summary
    except Exception as exc:
        summary["errors_count"] += 1
        summary["reason"] = "dispatch_failed"
        summary["error"] = _sanitize_error(exc)
        _warn("incident_notification_dispatch_failed", error=str(exc))
        if _fail_open_enabled():
            return summary
        raise
