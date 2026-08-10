"""Pure login probe outcome classifier for future Instagram provisioning.

This module intentionally has no device, HTTP, Vault, or runtime dependency.
It maps an abstract login probe outcome to the status payload expected by the
isolated Instagram account status publisher.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class LoginProbeOutcome(str, Enum):
    CONNECTED = "connected"
    NEEDS_2FA = "needs_2fa"
    CHECKPOINT = "checkpoint"
    VERIFICATION_PENDING = "verification_pending"
    UNSUPPORTED_POST_SUBMIT_CHALLENGE = "unsupported_post_submit_challenge"
    LOGIN_FAILED = "login_failed"
    LOGGED_OUT = "logged_out"
    SKIPPED_NOT_IMPLEMENTED = "skipped_not_implemented"
    UNKNOWN = "unknown"


FORBIDDEN_METADATA_KEYS = {
    "password",
    "secret",
    "secret_ref",
    "raw_secret",
    "token",
    "cookie",
    "vault",
    "webhook",
    "webhook_url",
    "service_role",
    "authorization",
    "bearer",
    "xml",
    "screenshot",
    "adb_serial",
    "device_udid",
    "session_cookie",
}

DEFAULT_METADATA = {
    "source": "provisioner",
    "stage": "login_probe",
    "probe_version": "v1",
}


@dataclass(frozen=True)
class LoginStatusClassification:
    ok: bool
    outcome: LoginProbeOutcome
    login_status: str | None = None
    provisioning_status: str | None = None
    onboarding_status: str | None = None
    reauth_required: bool | None = None
    reauth_reason: str | None = None
    reason: str = ""
    should_publish: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def publish_kwargs(self) -> dict[str, Any]:
        return {
            "login_status": self.login_status,
            "provisioning_status": self.provisioning_status,
            "onboarding_status": self.onboarding_status,
            "reauth_required": self.reauth_required,
            "reauth_reason": self.reauth_reason,
            "reason": self.reason,
            "metadata": dict(self.metadata),
        }


def normalize_login_probe_outcome(outcome: LoginProbeOutcome | str | None) -> LoginProbeOutcome:
    if isinstance(outcome, LoginProbeOutcome):
        return outcome
    value = str(outcome or "").strip().lower()
    try:
        return LoginProbeOutcome(value)
    except ValueError:
        return LoginProbeOutcome.UNKNOWN


def clean_login_probe_metadata(metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    merged: dict[str, Any] = dict(DEFAULT_METADATA)
    if isinstance(metadata, dict):
        merged.update(metadata)
    return _clean_metadata_value(merged)


def classify_login_probe_outcome(
    outcome: LoginProbeOutcome | str | None,
    *,
    metadata: dict[str, Any] | None = None,
    login_failed_reauth_reason: str = "credentials_invalid",
) -> LoginStatusClassification:
    normalized = normalize_login_probe_outcome(outcome)
    safe_metadata = clean_login_probe_metadata(metadata)

    if normalized == LoginProbeOutcome.CONNECTED:
        return LoginStatusClassification(
            ok=True,
            outcome=normalized,
            login_status="connected",
            provisioning_status="ready",
            onboarding_status="ready",
            reauth_required=False,
            reauth_reason=None,
            reason="login_connected",
            should_publish=True,
            metadata=safe_metadata,
        )

    if normalized == LoginProbeOutcome.NEEDS_2FA:
        return LoginStatusClassification(
            ok=False,
            outcome=normalized,
            login_status="needs_2fa",
            provisioning_status="login_verification_pending",
            onboarding_status="verification_pending",
            reauth_required=None,
            reauth_reason=None,
            reason="two_factor_required",
            should_publish=True,
            metadata=safe_metadata,
        )

    if normalized == LoginProbeOutcome.CHECKPOINT:
        return LoginStatusClassification(
            ok=False,
            outcome=normalized,
            login_status="checkpoint",
            provisioning_status="login_verification_pending",
            onboarding_status="verification_pending",
            reauth_required=None,
            reauth_reason=None,
            reason="checkpoint_required",
            should_publish=True,
            metadata=safe_metadata,
        )

    if normalized == LoginProbeOutcome.VERIFICATION_PENDING:
        challenge_type = str(safe_metadata.get("challenge_type") or "").strip().lower()
        screen_type = str(safe_metadata.get("screen_type") or "").strip().lower()
        if challenge_type in {"email", "sms", "whatsapp", "authenticator_app"} or screen_type in {
            "email_code_challenge",
            "sms_code_challenge",
            "whatsapp_code_challenge",
            "authenticator_app_code_challenge",
        }:
            reason = "verification_code_required"
        else:
            reason = str(safe_metadata.get("reason") or "verification_pending").strip() or "verification_pending"
        return LoginStatusClassification(
            ok=False,
            outcome=normalized,
            login_status="verification_pending",
            provisioning_status="login_verification_pending",
            onboarding_status="verification_pending",
            reauth_required=None,
            reauth_reason=None,
            reason=reason,
            should_publish=True,
            metadata=safe_metadata,
        )

    if normalized == LoginProbeOutcome.UNSUPPORTED_POST_SUBMIT_CHALLENGE:
        return LoginStatusClassification(
            ok=False,
            outcome=normalized,
            login_status="verification_pending",
            provisioning_status="login_verification_pending",
            onboarding_status="verification_pending",
            reauth_required=None,
            reauth_reason=None,
            reason="unsupported_post_submit_challenge",
            should_publish=True,
            metadata=safe_metadata,
        )

    if normalized == LoginProbeOutcome.LOGIN_FAILED:
        reauth_reason = str(login_failed_reauth_reason or "").strip()
        if reauth_reason not in {"credentials_invalid", "login_failed"}:
            reauth_reason = "credentials_invalid"
        return LoginStatusClassification(
            ok=False,
            outcome=normalized,
            login_status="failed",
            provisioning_status="failed",
            onboarding_status="blocked",
            reauth_required=True,
            reauth_reason=reauth_reason,
            reason="login_failed",
            should_publish=True,
            metadata=safe_metadata,
        )

    if normalized == LoginProbeOutcome.LOGGED_OUT:
        return LoginStatusClassification(
            ok=False,
            outcome=normalized,
            login_status="logged_out",
            provisioning_status="login_pending",
            onboarding_status="credentials_submitted",
            reauth_required=None,
            reauth_reason=None,
            reason="session_expired",
            should_publish=True,
            metadata=safe_metadata,
        )

    if normalized == LoginProbeOutcome.SKIPPED_NOT_IMPLEMENTED:
        return LoginStatusClassification(
            ok=False,
            outcome=normalized,
            reason="probe_not_implemented",
            should_publish=False,
            metadata=safe_metadata,
        )

    return LoginStatusClassification(
        ok=False,
        outcome=LoginProbeOutcome.UNKNOWN,
        reason="unknown_login_probe_outcome",
        should_publish=False,
        metadata=safe_metadata,
        error="unsupported_login_probe_outcome",
    )


def _clean_metadata_value(value: Any) -> Any:
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for key, nested in value.items():
            key_text = str(key)
            if key_text.strip().lower() in FORBIDDEN_METADATA_KEYS:
                continue
            clean[key_text] = _clean_metadata_value(nested)
        return clean
    if isinstance(value, list):
        return [_clean_metadata_value(item) for item in value]
    return value
