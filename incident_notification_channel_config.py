"""Resolve effective incident notification channel config from canonical DB settings."""

from __future__ import annotations

import base64
import hashlib
import os
from typing import Any

import config
import supabase_client

VALID_CHANNELS = {"slack", "discord"}


def _canonical_settings_enabled() -> bool:
    return bool(getattr(config, "INCIDENT_NOTIFICATIONS_CANONICAL_SETTINGS", True))


def _env_legacy_fallback_enabled() -> bool:
    return bool(getattr(config, "INCIDENT_NOTIFICATIONS_ENV_LEGACY_FALLBACK", False))


def _service_role_key() -> str:
    return str(
        getattr(config, "SUPABASE_SERVICE_ROLE_KEY", "")
        or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
        or ""
    ).strip()


def _encryption_key() -> bytes:
    raw = str(
        getattr(config, "INCIDENT_NOTIFICATION_WEBHOOK_ENCRYPTION_KEY", "")
        or os.getenv("INCIDENT_NOTIFICATION_WEBHOOK_ENCRYPTION_KEY", "")
        or ""
    ).strip()
    if not raw:
        raw = hashlib.sha256(
            f"local-incident-notif:{_service_role_key() or 'dev'}".encode("utf-8")
        ).hexdigest()
    return hashlib.sha256(raw.encode("utf-8")).digest()


def decrypt_webhook_ciphertext(ciphertext: str | None) -> str:
    raw = str(ciphertext or "").strip()
    if not raw:
        return ""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:
        raise RuntimeError("webhook_decrypt_unavailable") from exc
    buf = base64.b64decode(raw)
    iv = buf[:12]
    tag = buf[12:28]
    data = buf[28:]
    aesgcm = AESGCM(_encryption_key())
    decrypted = aesgcm.decrypt(iv, data + tag, None)
    return decrypted.decode("utf-8")


def _env_webhook_url(channel: str) -> str:
    normalized = str(channel or "").strip().lower()
    if normalized == "slack":
        return str(getattr(config, "SLACK_WEBHOOK_URL", "") or "").strip()
    if normalized == "discord":
        return str(getattr(config, "DISCORD_WEBHOOK_URL", "") or "").strip()
    return ""


def load_channel_settings_row(channel: str) -> dict[str, Any] | None:
    normalized = str(channel or "").strip().lower()
    if normalized not in VALID_CHANNELS:
        return None
    rows = supabase_client.load_incident_notification_channel_settings([normalized])
    return rows.get(normalized)


def resolve_effective_channel_config(channel: str) -> dict[str, Any]:
    normalized = str(channel or "").strip().lower()
    base = {
        "channel": normalized,
        "row_present": False,
        "enabled": False,
        "configured": False,
        "webhook_url": "",
        "source": "none",
        "send_allowed": False,
        "reason": "channel_not_configured",
    }
    if normalized not in VALID_CHANNELS:
        base["reason"] = "invalid_channel"
        return base

    if not _canonical_settings_enabled():
        webhook_url = _env_webhook_url(normalized)
        base.update(
            {
                "source": "env_legacy_only",
                "enabled": bool(webhook_url),
                "configured": bool(webhook_url),
                "webhook_url": webhook_url,
                "send_allowed": bool(webhook_url),
                "reason": None if webhook_url else "channel_not_configured",
            }
        )
        return base

    row = load_channel_settings_row(normalized)
    if row is not None:
        base["row_present"] = True
        enabled = bool(row.get("enabled"))
        configured = bool(row.get("configured"))
        base["enabled"] = enabled
        base["configured"] = configured
        base["source"] = "canonical"
        if not enabled:
            base["reason"] = "channel_disabled"
            return base
        if not configured or not str(row.get("webhook_ciphertext") or "").strip():
            base["reason"] = "channel_not_configured"
            return base
        try:
            webhook_url = decrypt_webhook_ciphertext(str(row.get("webhook_ciphertext") or ""))
        except Exception:
            webhook_url = ""
        webhook_url = str(webhook_url or "").strip()
        if not webhook_url:
            base["reason"] = "channel_not_configured"
            return base
        base.update(
            {
                "webhook_url": webhook_url,
                "send_allowed": True,
                "reason": None,
            }
        )
        return base

    if _env_legacy_fallback_enabled():
        webhook_url = _env_webhook_url(normalized)
        if webhook_url:
            base.update(
                {
                    "source": "env_legacy_fallback",
                    "enabled": True,
                    "configured": True,
                    "webhook_url": webhook_url,
                    "send_allowed": True,
                    "reason": None,
                }
            )
            return base

    return base


def channel_enabled_for_dispatch(channel: str) -> bool:
    cfg = resolve_effective_channel_config(channel)
    if cfg.get("row_present"):
        return bool(cfg.get("enabled"))
    if _env_legacy_fallback_enabled() or not _canonical_settings_enabled():
        return bool(cfg.get("send_allowed"))
    return False


def webhook_configured_for_dispatch(channel: str) -> bool:
    return bool(resolve_effective_channel_config(channel).get("send_allowed"))
