"""Fail-open publisher for login challenge dashboard actions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import config
from logs import log
from supabase_client import call_rpc

LOGIN_CHALLENGE_ACTIONS = {
    "enter_email_verification_code": {
        "title": "Code de vérification Instagram requis",
        "safe_client_message": "Instagram demande un code de vérification pour continuer la connexion.",
        "action_label": "Saisir le code",
        "audience": "client",
        "requires_client_action": True,
        "severity": "warning",
    },
    "review_login_challenge": {
        "title": "Vérification Instagram à examiner",
        "safe_client_message": "Instagram affiche une étape de vérification non automatisée. Une revue humaine est requise.",
        "action_label": "Examiner la vérification",
        "audience": "admin",
        "requires_client_action": False,
        "severity": "warning",
    },
    "review_login_package_mismatch": {
        "title": "Mauvaise app Instagram détectée",
        "safe_client_message": "Wrong app/clone detected for this account. Review device assignment before retry.",
        "action_label": "Revoir l'assignation du device",
        "audience": "admin",
        "requires_client_action": False,
        "severity": "critical",
    },
}

FORBIDDEN_METADATA_KEYS = {
    "password",
    "secret",
    "secret_ref",
    "raw_secret",
    "token",
    "verification_code",
    "cookie",
    "vault",
    "webhook_url",
    "service_role",
    "authorization",
    "bearer",
    "xml",
    "screenshot",
    "adb_serial",
    "device_udid",
}

EMAIL_CODE_ACTION_TTL_MINUTES = 10
SUPPORTED_VERIFICATION_CHANNELS = frozenset(
    {"email", "sms", "whatsapp", "authenticator_app"}
)


def _verification_channel_label(channel: str | None) -> str:
    return {
        "email": "Email",
        "sms": "SMS",
        "whatsapp": "WhatsApp",
        "authenticator_app": "Authenticator app",
    }.get(str(channel or "").strip().lower(), "Unknown")


def _enabled() -> bool:
    return bool(getattr(config, "LOGIN_CHALLENGE_DASHBOARD_ACTION_ENABLED", True))


def _fail_open() -> bool:
    return bool(getattr(config, "LOGIN_CHALLENGE_DASHBOARD_ACTION_FAIL_OPEN", True))


def _clean_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    clean: dict[str, Any] = {"source": "login_dashboard_action_publisher"}
    for key, value in dict(metadata or {}).items():
        if str(key).strip().lower() in FORBIDDEN_METADATA_KEYS:
            continue
        clean[str(key)] = value
    return clean


def upsert_login_challenge_dashboard_action(
    *,
    account_id: str,
    action_type: str,
    client_id: str | None = None,
    run_id: str | None = None,
    challenge_type: str | None = None,
    screen_type: str | None = None,
    stage: str = "post_submit",
    masked_email_present: bool | None = None,
    human_review_required: bool | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Upsert a login-challenge dashboard action via service-role RPC."""

    aid = str(account_id or "").strip()
    atype = str(action_type or "").strip()
    if not _enabled():
        return {"published": False, "reason": "disabled", "action_type": atype or None}
    if not aid or atype not in LOGIN_CHALLENGE_ACTIONS:
        return {"published": False, "reason": "invalid_payload", "action_type": atype or None}

    spec = dict(LOGIN_CHALLENGE_ACTIONS[atype])
    channel = str(challenge_type or "").strip().lower()
    if not channel and str(screen_type or "").strip() == "email_code_challenge":
        channel = "email"
    if not channel and atype == "enter_email_verification_code":
        channel = "email"
    if atype == "enter_email_verification_code":
        if channel not in SUPPORTED_VERIFICATION_CHANNELS:
            return {"published": False, "reason": "verification_channel_invalid", "action_type": atype}
        channel_label = _verification_channel_label(channel)
        spec.update(
            {
                "title": f"Code de vérification Instagram requis · {channel_label}",
                "safe_client_message": (
                    "Instagram demande un code de vérification pour continuer la connexion. "
                    f"Canal : {channel_label}."
                ),
                "action_label": "Saisir le code",
            }
        )
    action_expires_at = (
        (datetime.now(timezone.utc) + timedelta(minutes=EMAIL_CODE_ACTION_TTL_MINUTES)).isoformat()
        if atype == "enter_email_verification_code"
        else None
    )
    safe_metadata = _clean_metadata(
        {
            **(metadata or {}),
            "run_id": run_id,
            "challenge_type": channel or challenge_type,
            "verification_channel": channel or challenge_type,
            "screen_type": screen_type,
            "stage": stage,
            "masked_email_present": masked_email_present,
            "human_review_required": human_review_required,
            "action_expires_at": action_expires_at,
            "ttl_minutes": EMAIL_CODE_ACTION_TTL_MINUTES if action_expires_at else None,
        }
    )
    dedupe_key = f"account:{aid}:dashboard_action:{atype}"
    deep_link = f"/instagram-dashboard/credentials-actions?account_id={aid}"

    try:
        row = call_rpc(
            "upsert_login_challenge_dashboard_action",
            {
                "p_account_id": aid,
                "p_client_id": client_id,
                "p_action_type": atype,
                "p_status": "pending",
                "p_severity": spec["severity"],
                "p_audience": spec["audience"],
                "p_requires_client_action": spec["requires_client_action"],
                "p_blocking_campaign": True,
                "p_title": spec["title"],
                "p_safe_client_message": spec["safe_client_message"],
                "p_action_label": spec["action_label"],
                "p_action_deep_link": deep_link,
                "p_dedupe_key": dedupe_key,
                "p_metadata": safe_metadata,
            },
        )
        action_id = (row or {}).get("id") if isinstance(row, dict) else None
        return {
            "published": True,
            "reason": "upserted",
            "action_type": atype,
            "dashboard_action_id": action_id,
        }
    except Exception as exc:
        reason = "dashboard_action_upsert_failed"
        fallback = _fallback_upsert_login_challenge_dashboard_action(
            account_id=aid,
            action_type=atype,
            spec=spec,
            safe_metadata=safe_metadata,
            dedupe_key=dedupe_key,
            deep_link=deep_link,
            client_id=client_id,
        )
        if fallback.get("published"):
            return fallback
        if not _fail_open():
            raise
        log(
            "warning",
            "login_dashboard_action_publish_failed",
            account_id=aid,
            action_type=atype,
            reason=reason,
            error_type=type(exc).__name__,
        )
        return {"published": False, "reason": reason, "action_type": atype}


def _fallback_upsert_login_challenge_dashboard_action(
    *,
    account_id: str,
    action_type: str,
    spec: dict[str, Any],
    safe_metadata: dict[str, Any],
    dedupe_key: str,
    deep_link: str,
    client_id: str | None,
) -> dict[str, Any]:
    """Direct safe upsert when the RPC path is slow or unavailable."""

    from supabase_client import _request_json

    existing_rows = _request_json(
        "GET",
        "account_dashboard_actions",
        query={
            "select": "id,status",
            "account_id": f"eq.{account_id}",
            "dedupe_key": f"eq.{dedupe_key}",
            "status": "in.(pending,acknowledged,pending_verification,code_submitted)",
            "order": "created_at.desc",
            "limit": "1",
        },
    ) or []
    body = {
        "account_id": account_id,
        "client_id": client_id,
        "action_type": action_type,
        "status": "pending",
        "severity": spec["severity"],
        "audience": spec["audience"],
        "requires_client_action": spec["requires_client_action"],
        "blocking_campaign": True,
        "title": spec["title"],
        "safe_client_message": spec["safe_client_message"],
        "action_label": spec["action_label"],
        "action_deep_link": deep_link,
        "dedupe_key": dedupe_key,
        "metadata": safe_metadata,
    }
    try:
        if existing_rows:
            action_id = str(existing_rows[0].get("id") or "").strip()
            if not action_id:
                return {"published": False, "reason": "fallback_missing_action_id"}
            rows = _request_json(
                "PATCH",
                "account_dashboard_actions",
                query={"id": f"eq.{action_id}"},
                body={
                    "status": "pending",
                    "metadata": safe_metadata,
                },
                prefer_representation=True,
            ) or []
            patched_id = str((rows[0] or {}).get("id") or action_id) if rows else action_id
            return {
                "published": True,
                "reason": "fallback_patched",
                "action_type": action_type,
                "dashboard_action_id": patched_id,
            }
        rows = _request_json(
            "POST",
            "account_dashboard_actions",
            body=body,
            prefer_representation=True,
        ) or []
        action_id = str((rows[0] or {}).get("id") or "") if rows else ""
        if not action_id:
            return {"published": False, "reason": "fallback_insert_failed"}
        return {
            "published": True,
            "reason": "fallback_inserted",
            "action_type": action_type,
            "dashboard_action_id": action_id,
        }
    except Exception:
        return {"published": False, "reason": "fallback_upsert_failed"}
