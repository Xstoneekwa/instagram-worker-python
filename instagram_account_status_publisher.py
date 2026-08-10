"""Isolated publisher for Instagram account status updates.

Entry 2E-4A only provides a helper around the internal
instagram-account-status Edge Function. It is not wired into the runtime yet.
"""

from __future__ import annotations

import json
import os
import socket
import uuid
from datetime import datetime, timezone
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

CANONICAL_LOGIN_INVALIDATION_REASONS = {
    "explicit_logout": "explicit_logout",
    "logout_confirmed": "explicit_logout",
    "account_identity_mismatch": "identity_mismatch",
    "active_instagram_account_mismatch": "identity_mismatch",
    "identity_mismatch": "identity_mismatch",
    "auth_session_invalidated": "auth_session_invalidated",
    "session_expired": "instagram_login_screen_confirmed",
    "login_screen_signal": "instagram_login_screen_confirmed",
    "login_screen_detected": "instagram_login_screen_confirmed",
    "instagram_login_screen_confirmed": "instagram_login_screen_confirmed",
    "credentials_invalid": "credential_invalidation",
    "credential_invalidation": "credential_invalidation",
    "account_disabled": "account_disabled",
    "two_factor_required": "security_challenge_requires_login",
    "checkpoint_required": "security_challenge_requires_login",
    "verification_code_required": "security_challenge_requires_login",
    "verification_pending": "security_challenge_requires_login",
    "unsupported_post_submit_challenge": "security_challenge_requires_login",
    "security_challenge_requires_login": "security_challenge_requires_login",
    "other_explicit_canonical_invalidation": "other_explicit_canonical_invalidation",
}


class InstagramAccountStatusPublishError(RuntimeError):
    """Raised only when fail-open is disabled."""


def _enabled() -> bool:
    return bool(getattr(config, "INSTAGRAM_ACCOUNT_STATUS_PUBLISH_ENABLED", False)) or str(
        os.getenv("LOGIN_PROVISIONER_PUBLISH_ENABLED") or ""
    ).strip().lower() in {"1", "true", "yes", "on", "enabled"}


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


def _service_role_rpc_configured() -> bool:
    return bool(
        str(os.getenv("LOGIN_PROVISIONER_PUBLISH_ENABLED") or "").strip().lower()
        in {"1", "true", "yes", "on", "enabled"}
        and (os.getenv("SUPABASE_URL") or "").strip()
        and (os.getenv("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    )


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
    if "source_timestamp" not in clean or not str(clean.get("source_timestamp") or "").strip():
        clean["source_timestamp"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return clean


def _canonical_login_invalidation_reason(
    *,
    login_status: str | None,
    reason: str | None,
    reauth_reason: str | None,
    metadata: dict | None,
) -> str | None:
    normalized_login_status = str(login_status or "").strip().lower()
    if not normalized_login_status or normalized_login_status == "connected":
        return None
    safe_metadata = metadata if isinstance(metadata, dict) else {}
    for candidate in (
        safe_metadata.get("canonical_login_invalidation_reason"),
        reason,
        reauth_reason,
    ):
        mapped = CANONICAL_LOGIN_INVALIDATION_REASONS.get(str(candidate or "").strip().lower())
        if mapped:
            return mapped
    if str(reason or "").strip().lower() == "login_failed" and str(reauth_reason or "").strip().lower() == "login_failed":
        return "credential_invalidation"
    return None


def _rpc_error_reason(exc: Exception) -> str:
    text = str(exc or "").lower()
    mapping = {
        "client_instagram_account_not_found": "account_not_found",
        "invalid_status": "invalid_status",
        "check constraint": "invalid_status",
        "metadata contains a forbidden key": "forbidden_metadata",
        "metadata must be a json object": "metadata_must_be_object",
        "reason too long": "reason_too_long",
        "supabase_auth_401": "status_update_failed",
        "supabase_auth_403": "status_update_failed",
        "supabase_rest_timeout": "timeout",
        "supabase_network_timeout": "network_error",
        "supabase_dns_failed": "network_error",
        "supabase_tls_failed": "network_error",
    }
    for marker, reason in mapping.items():
        if marker in text:
            return reason
    return "status_update_failed"


def _publish_via_service_role_rpc(
    *,
    account_id: str,
    login_status: str | None,
    provisioning_status: str | None,
    onboarding_status: str | None,
    reauth_required: bool | None,
    reauth_reason: str | None,
    reason: str | None,
    external_request_id: str | None,
    metadata: dict | None,
    canonical_invalidation_reason: str | None = None,
) -> dict:
    try:
        from supabase_client import call_rpc

        clean_metadata = _clean_metadata(metadata)
        source = str((clean_metadata.get("source") or "")).strip().lower()
        actor_type = "provisioner" if "provisioner" in source else "worker" if source == "worker" else "internal"
        if canonical_invalidation_reason:
            body = call_rpc(
                "invalidate_client_instagram_login_v1",
                {
                    "p_account_id": account_id,
                    "p_invalidation_reason": canonical_invalidation_reason,
                    "p_source_timestamp": clean_metadata["source_timestamp"],
                    "p_login_status": login_status,
                    "p_provisioning_status": provisioning_status,
                    "p_onboarding_status": onboarding_status,
                    "p_reauth_required": reauth_required,
                    "p_reauth_reason": reauth_reason,
                    "p_actor_type": actor_type,
                    "p_external_request_id": external_request_id,
                    "p_metadata": clean_metadata,
                },
            )
        else:
            body = call_rpc(
                "update_client_instagram_account_status",
                {
                    "p_account_id": account_id,
                    "p_login_status": login_status,
                    "p_provisioning_status": provisioning_status,
                    "p_onboarding_status": onboarding_status,
                    "p_reauth_required": reauth_required,
                    "p_reauth_reason": reauth_reason,
                    "p_actor_type": actor_type,
                    "p_reason": reason,
                    "p_external_request_id": external_request_id,
                    "p_metadata": clean_metadata,
                },
            )
        return {
            "published": True,
            "status_code": 200,
            "response": body or {},
            "transport": "service_role_rpc",
        }
    except Exception as exc:
        return _safe_fail(_rpc_error_reason(exc), error_message=type(exc).__name__)


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

    clean_metadata = _clean_metadata(metadata)
    canonical_invalidation_reason = _canonical_login_invalidation_reason(
        login_status=login_status,
        reason=reason,
        reauth_reason=reauth_reason,
        metadata=clean_metadata,
    )
    if canonical_invalidation_reason:
        clean_metadata["canonical_login_invalidation_reason"] = canonical_invalidation_reason
        if not _service_role_rpc_configured():
            return _safe_fail("canonical_invalidation_rpc_unavailable")
        result = _publish_via_service_role_rpc(
            account_id=aid,
            login_status=login_status,
            provisioning_status=provisioning_status,
            onboarding_status=onboarding_status,
            reauth_required=reauth_required,
            reauth_reason=reauth_reason,
            reason=reason,
            external_request_id=external_request_id,
            metadata=clean_metadata,
            canonical_invalidation_reason=canonical_invalidation_reason,
        )
        _log_publish_result(
            account_id=aid,
            login_status=login_status,
            provisioning_status=provisioning_status,
            onboarding_status=onboarding_status,
            published=bool(result.get("published")),
            reason="published" if result.get("published") else str(result.get("reason") or "status_update_failed"),
            status_code=result.get("status_code") if isinstance(result.get("status_code"), int) else None,
            external_request_id=external_request_id,
        )
        return result

    url = _api_url()
    token = _token()
    if not url or not token:
        if not _service_role_rpc_configured():
            return _safe_fail("url_missing" if not url else "token_missing")
        result = _publish_via_service_role_rpc(
            account_id=aid,
            login_status=login_status,
            provisioning_status=provisioning_status,
            onboarding_status=onboarding_status,
            reauth_required=reauth_required,
            reauth_reason=reauth_reason,
            reason=reason,
            external_request_id=external_request_id,
            metadata=clean_metadata,
        )
        _log_publish_result(
            account_id=aid,
            login_status=login_status,
            provisioning_status=provisioning_status,
            onboarding_status=onboarding_status,
            published=bool(result.get("published")),
            reason="published" if result.get("published") else str(result.get("reason") or "publisher_not_configured"),
            status_code=result.get("status_code") if isinstance(result.get("status_code"), int) else None,
            external_request_id=external_request_id,
        )
        if result.get("published"):
            return result
        return result

    body: dict[str, Any] = {
        "action": "update_status",
        "account_id": aid,
        "metadata": clean_metadata,
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
    try:
        req = request.Request(
            url=url,
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            data=data,
        )
    except (TypeError, ValueError):
        return _safe_fail("request_build_failed")

    try:
        with request.urlopen(req, timeout=_timeout_seconds()) as resp:
            raw = resp.read()
            try:
                parsed = json.loads(raw.decode("utf-8")) if raw else {}
            except json.JSONDecodeError:
                result = _safe_fail("response_not_json")
                _log_publish_result(
                    account_id=aid,
                    login_status=login_status,
                    provisioning_status=provisioning_status,
                    onboarding_status=onboarding_status,
                    published=False,
                    reason="response_not_json",
                    status_code=int(getattr(resp, "status", 200)),
                    external_request_id=external_request_id,
                )
                return result
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
