"""Login challenge incidents and ephemeral verification code consumption."""

from __future__ import annotations

from typing import Any

from login_dashboard_action_publisher import upsert_login_challenge_dashboard_action
from runtime_incidents import publish_account_incident
from supabase_client import call_rpc


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


def sync_login_challenge_dashboard_action(
    *,
    account_id: str,
    dashboard_action_type: str | None,
    client_id: str | None = None,
    run_id: str | None = None,
    challenge_type: str | None = None,
    screen_type: str | None = None,
    masked_email_present: bool | None = None,
    human_review_required: bool | None = None,
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
        masked_email_present=masked_email_present,
        human_review_required=human_review_required,
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
