"""Safe runtime credential access boundary for future Instagram login.

Entry 2E-5G defines the Python-side contract only. It does not read a real
Vault, does not log or serialize passwords, and keeps the future secret reader
fully injectable for tests and later service-role wiring.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


SUPPORTED_PROVIDERS = {"instagram"}
ACTIVE_CREDENTIAL_STATUSES = {"active"}
REDACTED = "[REDACTED]"

FORBIDDEN_CREDENTIAL_KEYS = {
    "password",
    "secret",
    "secret_ref",
    "raw_secret",
    "vault",
    "token",
    "authorization",
    "service_role",
    "cookie",
    "sessionid",
    "session_cookie",
    "xml",
    "screenshot",
    "adb_serial",
    "device_udid",
}
SECRET_REF_ALLOWED_KEYS = {"secret_ref"}

VAULT_PAYLOAD_METADATA_MARKERS = (
    "account_id",
    "credentials_version",
    "created_at",
    "secret_ref",
    "supabase_vault",
    '"username"',
    '"provider"',
    '"metadata"',
)
INJECTION_PAYLOAD_MARKERS = (
    '"password":',
    "account_id",
    "credentials_version",
    "created_at",
    "secret_ref",
    "supabase_vault",
    '"username":',
    '"provider":',
    '"metadata":',
)

CredentialsLookup = Callable[[str, str], Optional[dict[str, Any]]]
SecretReader = Callable[[str], Any]


@dataclass(frozen=True)
class VaultPasswordParseResult:
    ok: bool
    password: str = ""
    failure_reason: str = ""
    vault_secret_is_json: bool = False
    vault_secret_has_password_key: bool = False
    vault_secret_contains_metadata_keys: bool = False
    extracted_password_valid: bool = False
    secret_value_safe_for_injection: bool = False


class SecretValue:
    """In-memory password wrapper with explicit reveal for the future executor."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = str(value)

    def __repr__(self) -> str:
        return REDACTED

    def __str__(self) -> str:
        return REDACTED

    def reveal_for_login_executor(self) -> str:
        return self._value


def parse_vault_secret_for_login(raw_secret: str) -> VaultPasswordParseResult:
    """Extract a password without changing its exact character sequence."""

    if not isinstance(raw_secret, str):
        return VaultPasswordParseResult(
            ok=False,
            failure_reason="vault_secret_password_invalid",
        )
    text = raw_secret
    contains_metadata = _contains_vault_metadata_markers(text)
    if not text:
        return VaultPasswordParseResult(
            ok=False,
            failure_reason="vault_secret_empty",
            vault_secret_contains_metadata_keys=contains_metadata,
        )

    if _looks_like_json_object(text):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return VaultPasswordParseResult(
                ok=False,
                failure_reason="vault_secret_password_invalid",
                vault_secret_is_json=True,
                vault_secret_contains_metadata_keys=contains_metadata,
            )
        if not isinstance(payload, dict):
            return VaultPasswordParseResult(
                ok=False,
                failure_reason="vault_secret_password_invalid",
                vault_secret_is_json=True,
                vault_secret_contains_metadata_keys=contains_metadata,
            )
        has_password_key = "password" in payload
        password_value = payload.get("password")
        if password_value is None:
            return VaultPasswordParseResult(
                ok=False,
                failure_reason="vault_secret_payload_missing_password",
                vault_secret_is_json=True,
                vault_secret_has_password_key=False,
                vault_secret_contains_metadata_keys=contains_metadata,
            )
        if not isinstance(password_value, str) or password_value == "":
            return VaultPasswordParseResult(
                ok=False,
                failure_reason="vault_secret_password_invalid",
                vault_secret_is_json=True,
                vault_secret_has_password_key=has_password_key,
                vault_secret_contains_metadata_keys=contains_metadata,
            )
        extracted = password_value
        if revealed_value_blocked_for_injection(extracted):
            return VaultPasswordParseResult(
                ok=False,
                failure_reason="vault_secret_password_invalid",
                vault_secret_is_json=True,
                vault_secret_has_password_key=True,
                vault_secret_contains_metadata_keys=contains_metadata,
            )
        return VaultPasswordParseResult(
            ok=True,
            password=extracted,
            vault_secret_is_json=True,
            vault_secret_has_password_key=True,
            vault_secret_contains_metadata_keys=contains_metadata,
            extracted_password_valid=True,
            secret_value_safe_for_injection=True,
        )

    if revealed_value_blocked_for_injection(text):
        return VaultPasswordParseResult(
            ok=False,
            failure_reason="vault_secret_password_invalid",
            vault_secret_contains_metadata_keys=contains_metadata,
        )

    return VaultPasswordParseResult(
        ok=True,
        password=text,
        vault_secret_is_json=False,
        vault_secret_has_password_key=False,
        vault_secret_contains_metadata_keys=contains_metadata,
        extracted_password_valid=True,
        secret_value_safe_for_injection=True,
    )


def vault_secret_shape_audit(raw_secret: str) -> dict[str, bool]:
    """Return safe shape flags for Vault secrets without exposing secret values."""

    parsed = parse_vault_secret_for_login(raw_secret)
    return {
        "vault_secret_is_json": parsed.vault_secret_is_json,
        "vault_secret_has_password_key": parsed.vault_secret_has_password_key,
        "vault_secret_contains_metadata_keys": parsed.vault_secret_contains_metadata_keys,
        "extracted_password_valid": parsed.extracted_password_valid,
        "secret_value_safe_for_injection": parsed.secret_value_safe_for_injection,
        "parse_ok": parsed.ok,
    }


def revealed_value_blocked_for_injection(value: str) -> bool:
    text = str(value or "")
    if not text:
        return True
    lowered = text.lower()
    if "{" in text and "}" in text:
        return True
    return any(marker in lowered for marker in INJECTION_PAYLOAD_MARKERS)


@dataclass(frozen=True)
class InstagramLoginCredentialsResult:
    ok: bool
    account_id: str
    provider: str
    username: str | None = None
    password: SecretValue | None = None
    credentials_version: int | None = None
    credentials_status: str | None = None
    reauth_required: bool | None = None
    reason: str = ""
    failure_reason: str | None = None
    safe_metadata: dict[str, Any] = field(default_factory=dict)


def get_instagram_credentials_for_login(
    *,
    account_id: str,
    provider: str = "instagram",
    credentials_lookup: CredentialsLookup | None = None,
    secret_reader: SecretReader | None = None,
) -> InstagramLoginCredentialsResult:
    safe_account_id = str(account_id or "").strip()
    safe_provider = str(provider or "").strip().lower()

    if not _is_valid_uuid(safe_account_id):
        return _failure(
            account_id=safe_account_id,
            provider=safe_provider,
            reason="invalid_account_id",
            failure_reason="invalid_account_id",
        )

    if safe_provider not in SUPPORTED_PROVIDERS:
        return _failure(
            account_id=safe_account_id,
            provider=safe_provider,
            reason="unsupported_provider",
            failure_reason="unsupported_provider",
        )

    if credentials_lookup is None:
        return _failure(
            account_id=safe_account_id,
            provider=safe_provider,
            reason="credentials_lookup_missing",
            failure_reason="credentials_lookup_missing",
        )

    row = credentials_lookup(safe_account_id, safe_provider)
    if not row:
        return _failure(
            account_id=safe_account_id,
            provider=safe_provider,
            reason="credentials_not_found",
            failure_reason="credentials_not_found",
        )

    if contains_forbidden_secret_keys(row, allowed_keys=SECRET_REF_ALLOWED_KEYS):
        return _failure_from_row(
            row,
            account_id=safe_account_id,
            provider=safe_provider,
            reason="credentials_payload_forbidden",
            failure_reason="credentials_payload_forbidden",
        )

    row_provider = str(row.get("provider") or "").strip().lower()
    if row_provider != safe_provider:
        return _failure_from_row(
            row,
            account_id=safe_account_id,
            provider=safe_provider,
            reason="credentials_provider_mismatch",
            failure_reason="credentials_provider_mismatch",
        )

    username = _extract_username(row)
    if not username:
        return _failure_from_row(
            row,
            account_id=safe_account_id,
            provider=safe_provider,
            reason="credentials_username_missing",
            failure_reason="credentials_username_missing",
        )

    status = str(row.get("status") or "").strip().lower()
    if status not in ACTIVE_CREDENTIAL_STATUSES:
        return _failure_from_row(
            row,
            account_id=safe_account_id,
            provider=safe_provider,
            reason="credentials_not_active",
            failure_reason="credentials_not_active",
        )

    secret_ref = str(row.get("secret_ref") or "").strip()
    if not secret_ref:
        return _failure_from_row(
            row,
            account_id=safe_account_id,
            provider=safe_provider,
            reason="credentials_secret_ref_missing",
            failure_reason="credentials_secret_ref_missing",
        )

    if secret_reader is None:
        return _failure_from_row(
            row,
            account_id=safe_account_id,
            provider=safe_provider,
            reason="secret_reader_missing",
            failure_reason="secret_reader_missing",
        )

    try:
        raw_password = secret_reader(secret_ref)
    except Exception:
        return _failure_from_row(
            row,
            account_id=safe_account_id,
            provider=safe_provider,
            reason="secret_reader_failed",
            failure_reason="secret_reader_failed",
        )

    if isinstance(raw_password, SecretValue):
        raw_secret_text = raw_password.reveal_for_login_executor()
    else:
        raw_secret_text = str(raw_password or "")

    parsed_secret = parse_vault_secret_for_login(raw_secret_text)
    if not parsed_secret.ok:
        return _failure_from_row(
            row,
            account_id=safe_account_id,
            provider=safe_provider,
            reason=parsed_secret.failure_reason,
            failure_reason=parsed_secret.failure_reason,
        )

    password_value = SecretValue(parsed_secret.password)

    return InstagramLoginCredentialsResult(
        ok=True,
        account_id=safe_account_id,
        provider=safe_provider,
        username=username,
        password=password_value,
        credentials_version=_safe_int(row.get("credentials_version")),
        credentials_status=status,
        reauth_required=_safe_bool_or_none(row.get("reauth_required")),
        reason="credentials_loaded_for_login",
        failure_reason=None,
        safe_metadata=_safe_metadata(
            account_id=safe_account_id,
            provider=safe_provider,
            row=row,
            reason="credentials_loaded_for_login",
            failure_reason=None,
            parsed_secret=parsed_secret,
        ),
    )


def redact_credentials_payload(payload: Any) -> Any:
    if isinstance(payload, dict):
        redacted: dict[str, Any] = {}
        for key, value in payload.items():
            key_text = str(key)
            if _normalized_key(key_text) in FORBIDDEN_CREDENTIAL_KEYS:
                continue
            redacted[key_text] = redact_credentials_payload(value)
        return redacted
    if isinstance(payload, list):
        return [redact_credentials_payload(item) for item in payload]
    if isinstance(payload, SecretValue):
        return REDACTED
    if isinstance(payload, str):
        return _redact_sensitive_string(payload)
    return payload


def credential_result_safe_dict(result: InstagramLoginCredentialsResult) -> dict[str, Any]:
    safe = {
        "ok": bool(result.ok),
        "account_id": result.account_id,
        "provider": result.provider,
        "username": result.username,
        "credentials_version": result.credentials_version,
        "credentials_status": result.credentials_status,
        "reauth_required": result.reauth_required,
        "reason": result.reason,
        "failure_reason": result.failure_reason,
        "safe_metadata": redact_credentials_payload(result.safe_metadata),
    }
    return redact_credentials_payload(safe)


def contains_forbidden_secret_keys(payload: Any, *, allowed_keys: set[str] | None = None) -> bool:
    allowed = {_normalized_key(key) for key in (allowed_keys or set())}
    if isinstance(payload, dict):
        for key, value in payload.items():
            normalized = _normalized_key(str(key))
            if normalized in FORBIDDEN_CREDENTIAL_KEYS and normalized not in allowed:
                return True
            if contains_forbidden_secret_keys(value, allowed_keys=allowed):
                return True
    elif isinstance(payload, list):
        return any(contains_forbidden_secret_keys(item, allowed_keys=allowed) for item in payload)
    return False


def _failure(
    *,
    account_id: str,
    provider: str,
    reason: str,
    failure_reason: str,
    row: dict[str, Any] | None = None,
) -> InstagramLoginCredentialsResult:
    return InstagramLoginCredentialsResult(
        ok=False,
        account_id=account_id,
        provider=provider,
        reason=reason,
        failure_reason=failure_reason,
        credentials_version=_safe_int((row or {}).get("credentials_version")),
        credentials_status=str((row or {}).get("status") or "") or None,
        reauth_required=_safe_bool_or_none((row or {}).get("reauth_required")),
        safe_metadata=_safe_metadata(
            account_id=account_id,
            provider=provider,
            row=row or {},
            reason=reason,
            failure_reason=failure_reason,
        ),
    )


def _failure_from_row(
    row: dict[str, Any],
    *,
    account_id: str,
    provider: str,
    reason: str,
    failure_reason: str,
) -> InstagramLoginCredentialsResult:
    return _failure(
        account_id=account_id,
        provider=provider,
        reason=reason,
        failure_reason=failure_reason,
        row=row,
    )


def _safe_metadata(
    *,
    account_id: str,
    provider: str,
    row: dict[str, Any],
    reason: str,
    failure_reason: str | None,
    parsed_secret: VaultPasswordParseResult | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "source": "instagram_credentials_runtime_access",
        "account_id": account_id,
        "provider": provider,
        "credentials_version": _safe_int(row.get("credentials_version")),
        "credentials_status": str(row.get("status") or "") or None,
        "reauth_required": _safe_bool_or_none(row.get("reauth_required")),
        "secret_provider": str(row.get("secret_provider") or "supabase_vault"),
        "reason": reason,
        "failure_reason": failure_reason,
    }
    if parsed_secret is not None:
        metadata.update(
            {
                "injectable_password_only": bool(parsed_secret.ok and parsed_secret.extracted_password_valid),
                "secret_value_safe_for_injection": bool(parsed_secret.secret_value_safe_for_injection),
                "guard_would_block_revealed_value": bool(
                    parsed_secret.extracted_password_valid
                    and not parsed_secret.secret_value_safe_for_injection
                ),
            }
        )
    return redact_credentials_payload(metadata)


def _extract_username(row: dict[str, Any]) -> str:
    username = str(row.get("username") or row.get("username_at_submission") or "").strip()
    return username


def _is_valid_uuid(value: str) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except (TypeError, ValueError):
        return False


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_bool_or_none(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)


def _normalized_key(key: str) -> str:
    return str(key or "").strip().lower()


def _looks_like_json_object(value: str) -> bool:
    text = str(value or "").strip()
    return text.startswith("{") and text.endswith("}")


def _contains_vault_metadata_markers(value: str) -> bool:
    lowered = str(value or "").lower()
    return any(marker in lowered for marker in VAULT_PAYLOAD_METADATA_MARKERS)


def _redact_sensitive_string(value: str) -> str:
    lowered = value.lower()
    if "supabase_vault://" in lowered:
        return REDACTED
    if any(token in lowered for token in ("service_role", "authorization:", "bearer ")):
        return REDACTED
    return value
