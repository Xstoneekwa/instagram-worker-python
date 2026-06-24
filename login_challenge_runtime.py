"""Login challenge incidents and ephemeral verification code consumption."""

from __future__ import annotations

from typing import Any

from login_dashboard_action_publisher import upsert_login_challenge_dashboard_action
from logs import log
from runtime_incidents import publish_account_incident
from supabase_client import _request_json, call_rpc

FORBIDDEN_ACTION_METADATA_KEYS = {
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


def publish_login_challenge_pending_incident(
    *,
    account_id: str,
    expected_username: str,
    run_id: str | None,
    challenge_type: str | None,
    screen_type: str | None,
    reason: str,
    dashboard_action_type: str | None,
    masked_email_present: bool | None = None,
) -> dict[str, Any]:
    incident_type = (
        "email_verification_code_required"
        if dashboard_action_type == "enter_email_verification_code"
        else "login_challenge_pending"
    )
    dedupe_key = f"account:{account_id}:login_challenge:{incident_type}"
    return publish_account_incident(
        incident_type=incident_type,
        dedupe_key=dedupe_key,
        severity="warning",
        status="open",
        account_id=account_id,
        account_username=expected_username,
        run_id=run_id,
        source="login_provisioner",
        reason=reason,
        action_required=dashboard_action_type,
        safe_client_message=(
            "Instagram demande un code email pour continuer la connexion."
            if dashboard_action_type == "enter_email_verification_code"
            else "Instagram affiche une vérification qui nécessite une revue humaine."
        ),
        admin_message=(
            "Email verification challenge detected after password submit."
            if dashboard_action_type == "enter_email_verification_code"
            else "Unsupported post-submit login challenge detected."
        ),
        metadata={
            "stage": "post_submit",
            "challenge_type": challenge_type,
            "screen_type": screen_type,
            "dashboard_action_type": dashboard_action_type,
            "masked_email_present": masked_email_present,
            "human_review_required": dashboard_action_type == "review_login_challenge",
        },
    )


def publish_login_package_mismatch_incident(
    *,
    account_id: str,
    expected_username: str,
    expected_package_name: str,
    actual_foreground_package: str | None,
    run_id: str | None = None,
    run_type: str | None = None,
    device_id: str | None = None,
    expected_app_instance_id: str | None = None,
    adb_serial_masked: str | None = None,
    reason: str = "expected_package_mismatch",
) -> dict[str, Any]:
    """Publish a safe blocking incident for wrong Instagram app/clone use."""

    aid = str(account_id or "").strip()
    expected_pkg = str(expected_package_name or "").strip()
    actual_pkg = str(actual_foreground_package or "").strip() or "unknown"
    dedupe_key = f"account:{aid}:login_package_mismatch:{expected_pkg}:{actual_pkg}"
    return publish_account_incident(
        incident_type="login_package_mismatch",
        dedupe_key=dedupe_key,
        severity="critical",
        status="open",
        account_id=aid,
        account_username=str(expected_username or "").strip() or None,
        run_id=run_id,
        device_id=device_id,
        source="login_provisioner",
        reason=reason,
        failure_reason=reason,
        action_required="Review device assignment and Instagram clone before retry.",
        safe_client_message="Automation paused for account safety.",
        assistant_message="Wrong app/clone detected for this account. Review device assignment before retry.",
        admin_message=(
            "Login package mismatch detected. "
            f"Expected package {expected_pkg or 'unknown'}, actual foreground package {actual_pkg}."
        ),
        metadata={
            "account_id": aid,
            "expected_username": str(expected_username or "").strip(),
            "expected_package_name": expected_pkg,
            "actual_foreground_package": actual_pkg,
            "expected_app_instance_id": str(expected_app_instance_id or "").strip() or None,
            "device_id": str(device_id or "").strip() or None,
            "adb_serial_masked": str(adb_serial_masked or "").strip() or None,
            "run_id": str(run_id or "").strip() or None,
            "run_type": str(run_type or "").strip() or None,
            "reason": reason,
            "blocking_campaign": True,
            "requires_operator_action": True,
            "requires_client_action": False,
        },
    )


def sync_login_package_mismatch_dashboard_action(
    *,
    account_id: str,
    expected_package_name: str,
    actual_foreground_package: str | None,
    run_id: str | None = None,
    expected_app_instance_id: str | None = None,
    reason: str = "expected_package_mismatch",
) -> dict[str, Any]:
    return upsert_login_challenge_dashboard_action(
        account_id=account_id,
        action_type="review_login_package_mismatch",
        run_id=run_id,
        challenge_type=None,
        screen_type=None,
        stage="pre_input_package_guard",
        human_review_required=True,
        metadata={
            "expected_package_name": expected_package_name,
            "actual_foreground_package": actual_foreground_package or "unknown",
            "expected_app_instance_id": expected_app_instance_id,
            "reason": reason,
            "blocking_campaign": True,
            "requires_operator_action": True,
            "requires_client_action": False,
        },
    )


def dispatch_login_package_mismatch_notifications() -> dict[str, Any]:
    """Best-effort incident notification dispatch honoring global toggles."""

    try:
        from incident_notifications import dispatch_account_incident_notifications

        return dispatch_account_incident_notifications(min_severity="critical", max_per_run=5)
    except Exception as exc:
        log(
            "warning",
            "login_package_mismatch_notification_dispatch_failed",
            error_type=type(exc).__name__,
        )
        return {"dispatched": False, "reason": "dispatch_failed"}


def sync_login_challenge_dashboard_action(
    *,
    account_id: str,
    dashboard_action_type: str | None,
    client_id: str | None = None,
    run_id: str | None = None,
    challenge_type: str | None = None,
    screen_type: str | None = None,
    stage: str = "post_submit",
    masked_email_present: bool | None = None,
    human_review_required: bool | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not dashboard_action_type:
        return {"published": False, "reason": "no_action_type"}
    return upsert_login_challenge_dashboard_action(
        account_id=account_id,
        action_type=dashboard_action_type,
        client_id=client_id,
        run_id=run_id,
        challenge_type=challenge_type,
        screen_type=screen_type,
        stage=stage,
        masked_email_present=masked_email_present,
        human_review_required=human_review_required,
        metadata=metadata,
    )


def consume_verification_code_for_worker(
    *,
    action_id: str | None = None,
    account_id: str | None = None,
    run_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Consume one ephemeral verification code. Callers must never log the response."""

    params: dict[str, Any] = {"p_metadata": metadata or {"source": "login_provisioner_resume"}}
    if action_id:
        params["p_action_id"] = action_id
    if account_id:
        params["p_account_id"] = account_id
    if run_id:
        params["p_run_id"] = run_id
    result = call_rpc("consume_account_verification_code_for_worker", params)
    return dict(result or {})


def _clean_action_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in dict(metadata or {}).items():
        if str(key).strip().lower() in FORBIDDEN_ACTION_METADATA_KEYS:
            continue
        clean[str(key)] = value
    return clean


def _patch_dashboard_action(
    *,
    action_id: str,
    account_id: str,
    body: dict[str, Any],
) -> dict[str, Any] | None:
    aid = str(action_id or "").strip()
    acct = str(account_id or "").strip()
    if not aid or not acct:
        return None
    rows = _request_json(
        "PATCH",
        "account_dashboard_actions",
        query={
            "id": f"eq.{aid}",
            "account_id": f"eq.{acct}",
            "action_type": "eq.enter_email_verification_code",
        },
        body=body,
        prefer_representation=True,
    ) or []
    if isinstance(rows, list) and rows:
        row = rows[0]
        return dict(row) if isinstance(row, dict) else None
    return None


def _merge_dashboard_action_metadata(
    *,
    action_id: str,
    account_id: str,
    metadata: dict[str, Any],
) -> dict[str, Any] | None:
    rows = _request_json(
        "GET",
        "account_dashboard_actions",
        query={
            "select": "id,metadata",
            "id": f"eq.{action_id}",
            "account_id": f"eq.{account_id}",
            "limit": "1",
        },
    ) or []
    existing = dict(rows[0].get("metadata") or {}) if rows else {}
    merged = {**existing, **_clean_action_metadata(metadata)}
    return _patch_dashboard_action(
        action_id=action_id,
        account_id=account_id,
        body={"metadata": merged},
    )


def mark_verification_action_resume_queued(
    *,
    action_id: str,
    account_id: str,
    run_request_id: str,
    submission_id: str | None = None,
) -> dict[str, Any]:
    return _merge_dashboard_action_metadata(
        action_id=action_id,
        account_id=account_id,
        metadata={
            "resume_request_id": run_request_id,
            "resume_status": "queued",
            "resume_submission_id": submission_id,
            "source": "login_email_code_resume",
        },
    ) or {"updated": False, "reason": "dashboard_action_patch_failed"}


def mark_verification_action_resume_running(
    *,
    action_id: str,
    account_id: str,
    run_request_id: str,
) -> dict[str, Any]:
    return _merge_dashboard_action_metadata(
        action_id=action_id,
        account_id=account_id,
        metadata={
            "resume_request_id": run_request_id,
            "resume_status": "running",
            "source": "login_email_code_resume",
        },
    ) or {"updated": False, "reason": "dashboard_action_patch_failed"}


def reopen_verification_action_pending(
    *,
    action_id: str,
    account_id: str,
    reason: str,
    run_id: str | None = None,
) -> dict[str, Any]:
    rows = _request_json(
        "GET",
        "account_dashboard_actions",
        query={
            "select": "metadata",
            "id": f"eq.{action_id}",
            "account_id": f"eq.{account_id}",
            "limit": "1",
        },
    ) or []
    existing = dict(rows[0].get("metadata") or {}) if rows else {}
    merged = {
        **existing,
        **_clean_action_metadata(
            {
                "resume_status": "needs_new_code",
                "resume_failure_reason": reason,
                "run_id": run_id,
                "source": "login_email_code_resume",
            }
        ),
    }
    patched = _patch_dashboard_action(
        action_id=action_id,
        account_id=account_id,
        body={"status": "pending", "metadata": merged},
    )
    if patched:
        return {"updated": True, "status": "pending", "reason": reason}
    log(
        "warning",
        "verification_action_reopen_pending_failed",
        account_id=account_id,
        action_id=action_id,
        reason=reason,
    )
    return {"updated": False, "reason": "dashboard_action_patch_failed"}


def resolve_verification_action_after_login(
    *,
    action_id: str,
    account_id: str,
    run_id: str | None = None,
) -> dict[str, Any]:
    try:
        row = call_rpc(
            "transition_account_dashboard_action",
            {
                "p_action_id": action_id,
                "p_new_status": "resolved",
                "p_actor_type": "system",
                "p_reason": "email_code_resume_connected",
                "p_metadata": _clean_action_metadata(
                    {
                        "resume_status": "completed",
                        "run_id": run_id,
                        "source": "login_email_code_resume",
                    }
                ),
            },
        )
        return {"updated": True, "status": "resolved", "action_id": (row or {}).get("id")}
    except Exception as exc:
        log(
            "warning",
            "verification_action_resolve_failed",
            account_id=account_id,
            action_id=action_id,
            error_type=type(exc).__name__,
        )
        return {"updated": False, "reason": "transition_failed"}


def sync_verification_action_after_email_code_resume(
    *,
    action_id: str | None,
    account_id: str,
    run_id: str | None,
    ok: bool,
    final_outcome: str,
    failure_reason: str | None,
    screen_type: str | None = None,
) -> dict[str, Any]:
    aid = str(action_id or "").strip()
    acct = str(account_id or "").strip()
    if not aid or not acct:
        return {"updated": False, "reason": "missing_action_or_account"}

    outcome = str(final_outcome or "").strip().lower()
    reason = str(failure_reason or "").strip().lower()
    connected_outcomes = {
        "connected",
        "active_account_home",
        "connected_home",
    }

    if ok and outcome in connected_outcomes:
        return resolve_verification_action_after_login(action_id=aid, account_id=acct, run_id=run_id)

    if reason == "verification_code_still_required" or (
        not ok and outcome == "verification_pending" and reason.startswith("verification_code")
    ):
        return reopen_verification_action_pending(
            action_id=aid,
            account_id=acct,
            reason=reason or "verification_code_still_required",
            run_id=run_id,
        )

    if reason in {"resume_email_code_screen_not_active", "code_missing"}:
        return _merge_dashboard_action_metadata(
            action_id=aid,
            account_id=acct,
            metadata={
                "resume_status": "preflight_failed",
                "resume_failure_reason": reason,
                "resume_preflight_screen_type": screen_type or "",
                "run_id": run_id,
            },
        ) or {"updated": False, "reason": "dashboard_action_patch_failed"}

    return _merge_dashboard_action_metadata(
        action_id=aid,
        account_id=acct,
        metadata={
            "resume_status": "failed",
            "resume_failure_reason": reason or outcome or "unknown",
            "run_id": run_id,
        },
    ) or {"updated": False, "reason": "dashboard_action_patch_failed"}
