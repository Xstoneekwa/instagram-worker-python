"""Isolated publisher for Instagram account status updates.

Entry 2E-4A only provides a helper around the internal
instagram-account-status Edge Function. It is not wired into the runtime yet.
"""

from __future__ import annotations

import json
import socket
import uuid
from typing import Any
from urllib import error, request

import config
from logs import log

SAFE_REQUEST_ID_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-")
FORBIDDEN_METADATA_KEYS = {
    "password",
    "secret",
    "secret_ref",
    "raw_secret",
    "token",
    "cookie",
    "webhook",
    "webhook_url",
    "vault",
    "service_role",
    "authorization",
    "bearer",
    "adb_serial",
    "device_udid",
    "xml",
    "screenshot",
    "session_cookie",
}


class InstagramAccountStatusPublishError(RuntimeError):
    """Raised only when fail-open is disabled."""


def _enabled() -> bool:
    return bool(getattr(config, "INSTAGRAM_ACCOUNT_STATUS_PUBLISH_ENABLED", False))


def _fail_open() -> bool:
    return bool(getattr(config, "INSTAGRAM_ACCOUNT_STATUS_FAIL_OPEN", True))


def _api_url() -> str:
    return str(getattr(config, "INSTAGRAM_ACCOUNT_STATUS_API_URL", "") or "").strip()


def _token() -> str:
    return str(getattr(config, "INSTAGRAM_ACCOUNT_STATUS_INTERNAL_API_TOKEN", "") or "").strip()


def _timeout_seconds() -> float:
    try:
        return max(0.1, float(getattr(config, "INSTAGRAM_ACCOUNT_STATUS_TIMEOUT_SECONDS", 10.0)))
    except (TypeError, ValueError):
        return 10.0


def _safe_fail(reason: str, *, status_code: int | None = None, error_message: str | None = None) -> dict:
    out: dict[str, Any] = {"published": False, "reason": reason}
    if status_code is not None:
        out["status_code"] = int(status_code)
    if error_message:
        out["error"] = str(error_message)[:500]
    if _fail_open():
        return out
    raise InstagramAccountStatusPublishError(json.dumps(out, sort_keys=True))


def _valid_uuid(value: str) -> bool:
    try:
        uuid.UUID(str(value or "").strip())
        return True
    except (TypeError, ValueError):
        return False


def _safe_request_id(value: str) -> bool:
    raw = str(value or "").strip()
    return bool(raw) and len(raw) <= 120 and all(ch in SAFE_REQUEST_ID_CHARS for ch in raw)


def _metadata_forbidden_path(value: Any, prefix: str = "metadata") -> str | None:
    if not isinstance(value, dict):
        return None
    for key, item in value.items():
        key_s = str(key)
        key_l = key_s.strip().lower()
        path = f"{prefix}.{key_s}"
        if key_l in FORBIDDEN_METADATA_KEYS:
            return path
        if isinstance(item, dict):
            nested = _metadata_forbidden_path(item, path)
            if nested:
                return nested
    return None


def _clean_metadata(metadata: dict | None) -> dict:
    clean = dict(metadata or {})
    if "source" not in clean or not str(clean.get("source") or "").strip():
        clean["source"] = "python_status_publisher"
    return clean


def _log_publish_result(
    *,
    account_id: str,
    login_status: str | None,
    provisioning_status: str | None,
    onboarding_status: str | None,
    published: bool,
    reason: str,
    status_code: int | None = None,
    external_request_id: str | None = None,
) -> None:
    log(
        "info" if published else "warning",
        "instagram_account_status_publish_result",
        account_id=account_id,
        login_status=login_status,
        provisioning_status=provisioning_status,
        onboarding_status=onboarding_status,
        published=published,
        reason=reason,
        status_code=status_code,
        external_request_id=external_request_id,
    )


def publish_instagram_account_status(
    account_id: str,
    login_status: str | None = None,
    provisioning_status: str | None = None,
    onboarding_status: str | None = None,
    reauth_required: bool | None = None,
    reauth_reason: str | None = None,
    reason: str | None = None,
    external_request_id: str | None = None,
    metadata: dict | None = None,
) -> dict:
    """Publish account status to the internal Edge Function when enabled."""

    aid = str(account_id or "").strip()
    if not _enabled():
        return {"published": False, "reason": "disabled"}

    if not aid or not _valid_uuid(aid):
        return _safe_fail("account_id_invalid")

    if not any(
        value is not None and str(value).strip() != ""
        for value in (login_status, provisioning_status, onboarding_status)
    ) and reauth_required is None:
        return _safe_fail("no_status_fields")

    if reason is not None and len(str(reason).strip()) > 500:
        return _safe_fail("reason_too_long")

    if external_request_id is not None and not _safe_request_id(str(external_request_id)):
        return _safe_fail("external_request_id_invalid")

    if metadata is not None and not isinstance(metadata, dict):
        return _safe_fail("metadata_must_be_object")

    forbidden = _metadata_forbidden_path(metadata or {})
    if forbidden:
        return _safe_fail("forbidden_metadata", error_message=forbidden)

    url = _api_url()
    token = _token()
    if not url or not token:
        return _safe_fail("not_configured")

    body: dict[str, Any] = {
        "action": "update_status",
        "account_id": aid,
        "metadata": _clean_metadata(metadata),
    }
    for key, value in (
        ("login_status", login_status),
        ("provisioning_status", provisioning_status),
        ("onboarding_status", onboarding_status),
        ("reauth_required", reauth_required),
        ("reauth_reason", reauth_reason),
        ("reason", reason),
        ("external_request_id", external_request_id),
    ):
        if value is not None:
            body[key] = value

    data = json.dumps(body, separators=(",", ":")).encode("utf-8")
    req = request.Request(
        url=url,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        data=data,
    )

    try:
        with request.urlopen(req, timeout=_timeout_seconds()) as resp:
            raw = resp.read()
            parsed = json.loads(raw.decode("utf-8")) if raw else {}
            out = {
                "published": True,
                "status_code": int(getattr(resp, "status", 200)),
                "response": parsed,
            }
            _log_publish_result(
                account_id=aid,
                login_status=login_status,
                provisioning_status=provisioning_status,
                onboarding_status=onboarding_status,
                published=True,
                reason="published",
                status_code=out["status_code"],
                external_request_id=external_request_id,
            )
            return out
    except error.HTTPError as exc:
        status_code = int(getattr(exc, "code", 0) or 0)
        result = _safe_fail("http_error", status_code=status_code)
        _log_publish_result(
            account_id=aid,
            login_status=login_status,
            provisioning_status=provisioning_status,
            onboarding_status=onboarding_status,
            published=False,
            reason="http_error",
            status_code=status_code,
            external_request_id=external_request_id,
        )
        return result
    except (TimeoutError, socket.timeout) as exc:
        result = _safe_fail("timeout", error_message=str(exc))
        _log_publish_result(
            account_id=aid,
            login_status=login_status,
            provisioning_status=provisioning_status,
            onboarding_status=onboarding_status,
            published=False,
            reason="timeout",
            external_request_id=external_request_id,
        )
        return result
    except error.URLError as exc:
        reason_out = "timeout" if isinstance(getattr(exc, "reason", None), socket.timeout) else "network_error"
        result = _safe_fail(reason_out, error_message=str(exc.reason) if hasattr(exc, "reason") else str(exc))
        _log_publish_result(
            account_id=aid,
            login_status=login_status,
            provisioning_status=provisioning_status,
            onboarding_status=onboarding_status,
            published=False,
            reason=reason_out,
            external_request_id=external_request_id,
        )
        return result
