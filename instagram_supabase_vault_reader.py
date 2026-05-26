"""Supabase Vault secret reader boundary for future Instagram login.

Entry 2E-5H adds a safe reader helper but does not wire real login, devices, or
password entry. The default transport is fail-closed until a service-role Vault
read RPC is explicitly supplied.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from instagram_credentials_runtime_access import (
    REDACTED,
    SecretValue,
    redact_credentials_payload,
)


SUPABASE_VAULT_PROVIDER = "supabase_vault"
SUPABASE_VAULT_PREFIX = "supabase_vault://"
DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_VAULT_READ_RPC = "read_instagram_credentials_vault_secret"

VaultRpcCaller = Callable[[str, dict[str, Any]], Any]


@dataclass(frozen=True)
class ParsedVaultSecretRef:
    ok: bool
    provider: str
    secret_id: str
    reason: str
    safe_ref_label: str = f"{SUPABASE_VAULT_PREFIX}{REDACTED}"


@dataclass(frozen=True)
class SupabaseVaultReadError(Exception):
    failure_reason: str
    reason: str
    provider: str = SUPABASE_VAULT_PROVIDER
    safe_ref_label: str = f"{SUPABASE_VAULT_PREFIX}{REDACTED}"
    duration_ms: int = 0
    safe_metadata: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.failure_reason

    def __repr__(self) -> str:
        return self.failure_reason

    def safe_dict(self) -> dict[str, Any]:
        return redact_credentials_payload(
            {
                "ok": False,
                "provider": self.provider,
                "safe_ref_label": self.safe_ref_label,
                "reason": self.reason,
                "failure_reason": self.failure_reason,
                "duration_ms": self.duration_ms,
                "safe_metadata": self.safe_metadata,
            }
        )


class SupabaseVaultClient:
    """Minimal service-role Vault read adapter.

    `rpc_caller` must be supplied explicitly in 2E-5H because the repository has
    a Vault write RPC, but no committed read/decrypt RPC yet.
    """

    def __init__(
        self,
        *,
        rpc_caller: VaultRpcCaller | None = None,
        rpc_function_name: str = DEFAULT_VAULT_READ_RPC,
    ) -> None:
        self.rpc_caller = rpc_caller
        self.rpc_function_name = str(rpc_function_name or DEFAULT_VAULT_READ_RPC).strip()

    @classmethod
    def from_supabase_client(
        cls,
        *,
        rpc_function_name: str = DEFAULT_VAULT_READ_RPC,
    ) -> "SupabaseVaultClient":
        from supabase_client import call_rpc

        return cls(rpc_caller=call_rpc, rpc_function_name=rpc_function_name)

    def read_secret(self, secret_id: str) -> str:
        safe_secret_id = str(secret_id or "").strip()
        if not _is_uuid(safe_secret_id):
            raise _safe_error("vault_secret_id_invalid")
        if self.rpc_caller is None:
            raise _safe_error("vault_transport_not_configured")

        result = self.rpc_caller(
            self.rpc_function_name,
            {"p_secret_id": safe_secret_id},
        )
        secret = _extract_secret_string(result)
        if secret is None:
            raise _safe_error("vault_secret_not_string")
        return secret


def parse_supabase_vault_secret_ref(secret_ref: str) -> ParsedVaultSecretRef:
    raw = str(secret_ref or "").strip()
    if not raw:
        return ParsedVaultSecretRef(
            ok=False,
            provider="",
            secret_id="",
            reason="invalid_secret_ref",
        )
    if not raw.startswith(SUPABASE_VAULT_PREFIX):
        provider = raw.split("://", 1)[0].strip() if "://" in raw else ""
        return ParsedVaultSecretRef(
            ok=False,
            provider=provider,
            secret_id="",
            reason="unsupported_secret_ref_provider",
        )
    secret_id = raw[len(SUPABASE_VAULT_PREFIX) :].strip()
    if not _is_uuid(secret_id):
        return ParsedVaultSecretRef(
            ok=False,
            provider=SUPABASE_VAULT_PROVIDER,
            secret_id="",
            reason="vault_secret_id_invalid",
        )
    return ParsedVaultSecretRef(
        ok=True,
        provider=SUPABASE_VAULT_PROVIDER,
        secret_id=secret_id,
        reason="valid_secret_ref",
    )


def read_supabase_vault_secret(
    secret_ref: str,
    *,
    vault_client: SupabaseVaultClient | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> SecretValue:
    started = time.perf_counter()
    parsed = parse_supabase_vault_secret_ref(secret_ref)
    if not parsed.ok:
        raise _safe_error(parsed.reason, duration_ms=_elapsed_ms(started))

    client = vault_client or SupabaseVaultClient()
    timeout = _safe_timeout(timeout_seconds)
    try:
        secret = _read_with_timeout_guard(client, parsed.secret_id, timeout)
    except SupabaseVaultReadError as exc:
        raise _with_duration(exc, _elapsed_ms(started)) from None
    except TimeoutError:
        raise _safe_error("vault_timeout", duration_ms=_elapsed_ms(started)) from None
    except Exception:
        raise _safe_error("vault_read_failed", duration_ms=_elapsed_ms(started)) from None

    if not isinstance(secret, str):
        raise _safe_error("vault_secret_not_string", duration_ms=_elapsed_ms(started))
    if not secret:
        raise _safe_error("vault_secret_empty", duration_ms=_elapsed_ms(started))
    return SecretValue(secret)


def build_supabase_vault_secret_reader(
    *,
    vault_client: SupabaseVaultClient | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> Callable[[str], SecretValue]:
    def _reader(secret_ref: str) -> SecretValue:
        return read_supabase_vault_secret(
            secret_ref,
            vault_client=vault_client,
            timeout_seconds=timeout_seconds,
        )

    return _reader


def _read_with_timeout_guard(
    vault_client: SupabaseVaultClient,
    secret_id: str,
    timeout_seconds: float,
) -> str:
    started = time.perf_counter()
    secret = vault_client.read_secret(secret_id)
    if (time.perf_counter() - started) > timeout_seconds:
        raise _safe_error("vault_timeout")
    return secret


def _extract_secret_string(result: Any) -> str | None:
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        for key in ("secret", "decrypted_secret", "secret_value", "value"):
            value = result.get(key)
            if isinstance(value, str):
                return value
        return None
    if isinstance(result, list) and len(result) == 1:
        return _extract_secret_string(result[0])
    return None


def _safe_timeout(value: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = DEFAULT_TIMEOUT_SECONDS
    return max(0.1, min(parsed, 30.0))


def _elapsed_ms(started: float) -> int:
    return max(0, int(round((time.perf_counter() - started) * 1000)))


def _with_duration(error: SupabaseVaultReadError, duration_ms: int) -> SupabaseVaultReadError:
    return SupabaseVaultReadError(
        failure_reason=error.failure_reason,
        reason=error.reason,
        provider=error.provider,
        safe_ref_label=error.safe_ref_label,
        duration_ms=duration_ms,
        safe_metadata=error.safe_metadata,
    )


def _safe_error(reason: str, *, duration_ms: int = 0) -> SupabaseVaultReadError:
    failure_reason = str(reason or "vault_read_failed").strip() or "vault_read_failed"
    return SupabaseVaultReadError(
        failure_reason=failure_reason,
        reason=failure_reason,
        duration_ms=duration_ms,
        safe_metadata={
            "source": "instagram_supabase_vault_reader",
            "provider": SUPABASE_VAULT_PROVIDER,
            "safe_ref_label": f"{SUPABASE_VAULT_PREFIX}{REDACTED}",
            "failure_reason": failure_reason,
        },
    )


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except (TypeError, ValueError):
        return False
