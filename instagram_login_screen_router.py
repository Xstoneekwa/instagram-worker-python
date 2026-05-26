"""Pure decision layer for Instagram pre-login screens.

Entry 2E-5E prepares the next login/provisioning step without executing it.
The router never taps, types credentials, reads Vault, calls Supabase, or
publishes HTTP. It only returns a safe, testable decision.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from instagram_login_status_classifier import clean_login_probe_metadata

CONTINUE_AS_CANDIDATE = "continue_as_candidate"
LOGIN_FORM_EMPTY = "login_form_empty"
UNKNOWN_SCREEN = "unknown"

STOPPED_LIFECYCLE_STATUSES = {"canceled", "archived", "stopped"}
KNOWN_LIFECYCLE_STATUSES = {"active", "paused", "canceled", "onboarding", "archived", "stopped", "unknown"}

LifecycleLookup = Callable[[str], dict[str, Any]]


@dataclass(frozen=True)
class LoginScreenRouteDecision:
    ok: bool
    screen_type: str
    decision: str
    suggested_username: str = ""
    expected_username: str = ""
    normalized_suggested_username: str = ""
    normalized_expected_username: str = ""
    next_action: str = ""
    reason: str = ""
    should_escalate: bool = False
    should_tap_continue: bool = False
    should_tap_use_another_profile: bool = False
    should_start_login_form_flow: bool = False
    publish_login_status: str | None = None
    provisioning_status: str | None = None
    onboarding_status: str | None = None
    dashboard_action_type: str | None = None
    audit_reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def normalize_instagram_username(username: str | None) -> str:
    return str(username or "").strip().lstrip("@").lower()


def route_login_screen(
    *,
    expected_username: str,
    suggested_username: str | None = None,
    screen_type: str,
    account_lifecycle_lookup: LifecycleLookup | None = None,
    clone_reuse_allowed: bool = False,
    account_id: str | None = None,
    clone_id: str | None = None,
) -> LoginScreenRouteDecision:
    normalized_expected = normalize_instagram_username(expected_username)
    normalized_suggested = normalize_instagram_username(suggested_username)
    safe_screen_type = str(screen_type or UNKNOWN_SCREEN).strip() or UNKNOWN_SCREEN

    if safe_screen_type == LOGIN_FORM_EMPTY:
        return _decision(
            ok=True,
            screen_type=safe_screen_type,
            decision="start_login_form_flow",
            expected_username=expected_username,
            suggested_username=suggested_username or "",
            normalized_expected_username=normalized_expected,
            normalized_suggested_username=normalized_suggested,
            next_action="secure_credentials_required_later",
            reason="login_form_empty",
            should_start_login_form_flow=True,
            clone_reuse_allowed=clone_reuse_allowed,
        )

    if safe_screen_type != CONTINUE_AS_CANDIDATE:
        return _decision(
            ok=False,
            screen_type=UNKNOWN_SCREEN,
            decision="unknown_no_action",
            expected_username=expected_username,
            suggested_username=suggested_username or "",
            normalized_expected_username=normalized_expected,
            normalized_suggested_username=normalized_suggested,
            reason="unknown_login_screen",
            clone_reuse_allowed=clone_reuse_allowed,
        )

    if normalized_suggested and normalized_suggested == normalized_expected:
        return _decision(
            ok=True,
            screen_type=safe_screen_type,
            decision="continue_expected_account",
            expected_username=expected_username,
            suggested_username=suggested_username or "",
            normalized_expected_username=normalized_expected,
            normalized_suggested_username=normalized_suggested,
            next_action="continue_then_secure_password_step_later",
            reason="suggested_username_matches_expected",
            should_tap_continue=True,
            clone_reuse_allowed=clone_reuse_allowed,
        )

    lifecycle_status, lookup_error = _lookup_lifecycle_status(
        normalized_suggested,
        account_lifecycle_lookup,
    )
    lifecycle_is_stopped = lifecycle_status in STOPPED_LIFECYCLE_STATUSES

    if lifecycle_is_stopped and clone_reuse_allowed:
        return _decision(
            ok=True,
            screen_type=safe_screen_type,
            decision="use_another_profile_previous_account_stopped",
            expected_username=expected_username,
            suggested_username=suggested_username or "",
            normalized_expected_username=normalized_expected,
            normalized_suggested_username=normalized_suggested,
            next_action="use_another_profile_then_login_form",
            reason="previous_account_canceled_clone_reusable",
            should_tap_use_another_profile=True,
            audit_reason="previous_account_stopped_override",
            clone_reuse_allowed=clone_reuse_allowed,
            lifecycle_status=lifecycle_status,
        )

    reason = (
        "lifecycle_lookup_failed_wrong_suggested_account_requires_admin_review"
        if lookup_error
        else "wrong_suggested_account_requires_admin_review"
    )
    return _decision(
        ok=False,
        screen_type=safe_screen_type,
        decision="block_wrong_suggested_account",
        expected_username=expected_username,
        suggested_username=suggested_username or "",
        normalized_expected_username=normalized_expected,
        normalized_suggested_username=normalized_suggested,
        reason=reason,
        should_escalate=True,
        publish_login_status="mismatch",
        provisioning_status="blocked",
        onboarding_status="support_required",
        dashboard_action_type="review_account_mismatch",
        clone_reuse_allowed=clone_reuse_allowed,
        lifecycle_status=lifecycle_status,
    )


def _lookup_lifecycle_status(
    normalized_suggested_username: str,
    account_lifecycle_lookup: LifecycleLookup | None,
) -> tuple[str, bool]:
    if not normalized_suggested_username or account_lifecycle_lookup is None:
        return "unknown", False
    try:
        raw = account_lifecycle_lookup(normalized_suggested_username) or {}
    except Exception:
        return "unknown", True

    status = str(raw.get("lifecycle_status") or "unknown").strip().lower()
    if status not in KNOWN_LIFECYCLE_STATUSES:
        status = "unknown"
    return status, False


def _decision(
    *,
    ok: bool,
    screen_type: str,
    decision: str,
    expected_username: str,
    suggested_username: str,
    normalized_expected_username: str,
    normalized_suggested_username: str,
    reason: str,
    clone_reuse_allowed: bool,
    next_action: str = "",
    should_escalate: bool = False,
    should_tap_continue: bool = False,
    should_tap_use_another_profile: bool = False,
    should_start_login_form_flow: bool = False,
    publish_login_status: str | None = None,
    provisioning_status: str | None = None,
    onboarding_status: str | None = None,
    dashboard_action_type: str | None = None,
    audit_reason: str | None = None,
    lifecycle_status: str = "unknown",
) -> LoginScreenRouteDecision:
    metadata = clean_login_probe_metadata(
        {
            "source": "login_screen_router",
            "screen_type": screen_type,
            "decision": decision,
            "reason": reason,
            "suggested_username": normalized_suggested_username,
            "expected_username": normalized_expected_username,
            "lifecycle_status": lifecycle_status,
            "clone_reuse_allowed": bool(clone_reuse_allowed),
            "audit_reason": audit_reason or "",
        }
    )
    return LoginScreenRouteDecision(
        ok=ok,
        screen_type=screen_type,
        decision=decision,
        suggested_username=suggested_username,
        expected_username=expected_username,
        normalized_suggested_username=normalized_suggested_username,
        normalized_expected_username=normalized_expected_username,
        next_action=next_action,
        reason=reason,
        should_escalate=should_escalate,
        should_tap_continue=should_tap_continue,
        should_tap_use_another_profile=should_tap_use_another_profile,
        should_start_login_form_flow=should_start_login_form_flow,
        publish_login_status=publish_login_status,
        provisioning_status=provisioning_status,
        onboarding_status=onboarding_status,
        dashboard_action_type=dashboard_action_type,
        audit_reason=audit_reason,
        metadata=metadata,
    )
