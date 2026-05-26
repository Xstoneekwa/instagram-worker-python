"""Isolated skeleton for future Instagram login provisioning checks.

Entry 2E-5A does not automate device login, read Vault secrets, or hook into
runner/sender flows. It only wires an abstract login outcome through the pure
classifier and, when explicitly enabled, the injectable status publisher.
"""

from __future__ import annotations

import os
from typing import Any, Callable

from instagram_login_status_classifier import (
    LoginProbeOutcome,
    LoginStatusClassification,
    classify_login_probe_outcome,
    clean_login_probe_metadata,
)
from logs import log

Publisher = Callable[..., dict]

TRUE_ENV_VALUES = {"1", "true", "yes", "y", "on", "enabled"}


def is_login_provisioner_enabled() -> bool:
    value = os.getenv("INSTAGRAM_LOGIN_PROVISIONER_ENABLED", "false")
    return str(value or "").strip().lower() in TRUE_ENV_VALUES


def probe_device_login(*_args: Any, **_kwargs: Any) -> LoginStatusClassification:
    """Placeholder for a later UI probe; no device action is performed in 2E-5A."""
    return classify_login_probe_outcome(LoginProbeOutcome.SKIPPED_NOT_IMPLEMENTED)


def publish_classified_login_status(
    *,
    account_id: str,
    classification: LoginStatusClassification,
    external_request_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    publisher: Publisher | None = None,
    fail_open: bool = True,
) -> dict:
    if not is_login_provisioner_enabled():
        return {"published": False, "reason": "disabled"}

    if not classification.should_publish:
        return {"published": False, "reason": classification.reason}

    safe_metadata = clean_login_probe_metadata({**classification.metadata, **(metadata or {})})
    publish = publisher or _default_publisher

    try:
        return publish(
            account_id=account_id,
            login_status=classification.login_status,
            provisioning_status=classification.provisioning_status,
            onboarding_status=classification.onboarding_status,
            reauth_required=classification.reauth_required,
            reauth_reason=classification.reauth_reason,
            reason=classification.reason,
            external_request_id=external_request_id,
            metadata=safe_metadata,
        )
    except Exception as exc:
        log(
            "warning",
            "instagram_login_provisioner_publish_failed",
            account_id=account_id,
            reason="publisher_exception",
            error_type=type(exc).__name__,
        )
        if fail_open:
            return {"published": False, "reason": "publisher_exception"}
        raise


def run_login_provisioning_check(
    *,
    account_id: str,
    outcome: LoginProbeOutcome | str | None = None,
    external_request_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    publisher: Publisher | None = None,
) -> dict:
    classification = (
        probe_device_login()
        if outcome is None
        else classify_login_probe_outcome(outcome, metadata=metadata)
    )
    publish_result = publish_classified_login_status(
        account_id=account_id,
        classification=classification,
        external_request_id=external_request_id,
        metadata=metadata,
        publisher=publisher,
    )
    return {"classification": classification, "publish_result": publish_result}


def _default_publisher(**kwargs: Any) -> dict:
    from instagram_account_status_publisher import publish_instagram_account_status

    return publish_instagram_account_status(**kwargs)
