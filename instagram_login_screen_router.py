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
CONTINUE_PASSWORD_ONLY = "continue_password_only"
ACCOUNT_PICKER = "account_picker"
ACTIVE_ACCOUNT_PROFILE = "active_account_profile"
JOIN_INSTAGRAM_LANDING = "join_instagram_landing"
JOIN_INSTAGRAM_PROVISIONING_NEXT_ACTION = "continue_to_existing_profile_login"
LOGIN_FORM_EMPTY = "login_form_empty"
LOGIN_FORM_PREFILLED_USERNAME = "login_form_prefilled_username"
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
    target_username: str = ""
    next_action: str = ""
    reason: str = ""
    should_escalate: bool = False
    should_tap_continue: bool = False
    should_tap_use_another_profile: bool = False
    should_tap_already_have_profile: bool = False
    should_tap_expected_account: bool = False
    should_recover_old_logged_in_account: bool = False
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
    available_usernames: list[str] | tuple[str, ...] | None = None,
    account_lifecycle_lookup: LifecycleLookup | None = None,
    clone_reuse_allowed: bool = False,
    account_id: str | None = None,
    clone_id: str | None = None,
) -> LoginScreenRouteDecision:
    normalized_expected = normalize_instagram_username(expected_username)
    normalized_suggested = normalize_instagram_username(suggested_username)
    normalized_available = [
        normalize_instagram_username(username)
        for username in (available_usernames or [])
        if normalize_instagram_username(username)
    ]
    safe_screen_type = str(screen_type or UNKNOWN_SCREEN).strip() or UNKNOWN_SCREEN

    if safe_screen_type == JOIN_INSTAGRAM_LANDING:
        return _decision(
            ok=True,
            screen_type=safe_screen_type,
            decision="open_existing_profile_from_join_landing",
            expected_username=expected_username,
            suggested_username=suggested_username or "",
            normalized_expected_username=normalized_expected,
            normalized_suggested_username=normalized_suggested,
            next_action=JOIN_INSTAGRAM_PROVISIONING_NEXT_ACTION,
            reason="join_instagram_landing_existing_profile_required",
            should_tap_already_have_profile=True,
            clone_reuse_allowed=clone_reuse_allowed,
        )

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

    if safe_screen_type == LOGIN_FORM_PREFILLED_USERNAME:
        if not normalized_suggested:
            return _decision(
                ok=False,
                screen_type=safe_screen_type,
                decision="username_prefilled_not_editable",
                expected_username=expected_username,
                suggested_username=suggested_username or "",
                normalized_expected_username=normalized_expected,
                normalized_suggested_username=normalized_suggested,
                reason="username_prefilled_not_editable",
                clone_reuse_allowed=clone_reuse_allowed,
            )
        if normalized_suggested == normalized_expected:
            return _decision(
                ok=True,
                screen_type=safe_screen_type,
                decision="start_login_form_flow_prefilled_expected",
                expected_username=expected_username,
                suggested_username=suggested_username or "",
                normalized_expected_username=normalized_expected,
                normalized_suggested_username=normalized_suggested,
                next_action="secure_password_required_later",
                reason="prefilled_username_matches_expected",
                should_start_login_form_flow=True,
                clone_reuse_allowed=clone_reuse_allowed,
            )
        lifecycle_status, lookup_error = _lookup_lifecycle_status(
            normalized_suggested,
            account_lifecycle_lookup,
        )
        if lifecycle_status in STOPPED_LIFECYCLE_STATUSES and clone_reuse_allowed:
            return _decision(
                ok=True,
                screen_type=safe_screen_type,
                decision="start_login_form_flow_replace_username",
                expected_username=expected_username,
                suggested_username=suggested_username or "",
                normalized_expected_username=normalized_expected,
                normalized_suggested_username=normalized_suggested,
                next_action="replace_prefilled_username_then_secure_password",
                reason="prefilled_old_username_reusable_replace",
                should_start_login_form_flow=True,
                clone_reuse_allowed=clone_reuse_allowed,
                lifecycle_status=lifecycle_status,
            )
        reason = (
            "lifecycle_lookup_failed_username_prefilled_mismatch_requires_review"
            if lookup_error
            else "username_prefilled_mismatch_requires_review"
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
            onboarding_status="blocked",
            dashboard_action_type="review_account_mismatch",
            clone_reuse_allowed=clone_reuse_allowed,
            lifecycle_status=lifecycle_status,
        )

    if safe_screen_type == CONTINUE_PASSWORD_ONLY:
        if normalized_suggested and normalized_suggested == normalized_expected:
            return _decision(
                ok=True,
                screen_type=safe_screen_type,
                decision="start_login_form_flow",
                expected_username=expected_username,
                suggested_username=suggested_username or "",
                normalized_expected_username=normalized_expected,
                normalized_suggested_username=normalized_suggested,
                next_action="secure_password_required_later",
                reason="continue_password_only_expected_account",
                should_start_login_form_flow=True,
                clone_reuse_allowed=clone_reuse_allowed,
            )
        return _decision(
            ok=False,
            screen_type=safe_screen_type,
            decision="block_wrong_suggested_account",
            expected_username=expected_username,
            suggested_username=suggested_username or "",
            normalized_expected_username=normalized_expected,
            normalized_suggested_username=normalized_suggested,
            reason="continue_password_only_username_mismatch",
            should_escalate=True,
            publish_login_status="mismatch",
            provisioning_status="blocked",
            onboarding_status="blocked",
            dashboard_action_type="review_account_mismatch",
            clone_reuse_allowed=clone_reuse_allowed,
        )

    if safe_screen_type == ACCOUNT_PICKER:
        if not normalized_expected:
            return _decision(
                ok=False,
                screen_type=safe_screen_type,
                decision="expected_username_missing",
                expected_username=expected_username,
                suggested_username=suggested_username or "",
                normalized_expected_username=normalized_expected,
                normalized_suggested_username=normalized_suggested,
                reason="expected_username_missing",
                clone_reuse_allowed=clone_reuse_allowed,
            )
        match_count = sum(1 for username in normalized_available if username == normalized_expected)
        if match_count == 1:
            return _decision(
                ok=True,
                screen_type=safe_screen_type,
                decision="select_expected_account_from_picker",
                expected_username=expected_username,
                suggested_username=suggested_username or "",
                normalized_expected_username=normalized_expected,
                normalized_suggested_username=normalized_suggested,
                target_username=normalized_expected,
                next_action="tap_expected_account_row_then_observe",
                reason="expected_account_listed",
                should_tap_expected_account=True,
                clone_reuse_allowed=clone_reuse_allowed,
            )
        if match_count > 1:
            return _decision(
                ok=False,
                screen_type=safe_screen_type,
                decision="ambiguous_expected_account_row",
                expected_username=expected_username,
                suggested_username=suggested_username or "",
                normalized_expected_username=normalized_expected,
                normalized_suggested_username=normalized_suggested,
                target_username=normalized_expected,
                reason="ambiguous_expected_account_row",
                clone_reuse_allowed=clone_reuse_allowed,
            )
        return _decision(
            ok=False,
            screen_type=safe_screen_type,
            decision="expected_account_not_listed",
            expected_username=expected_username,
            suggested_username=suggested_username or "",
            normalized_expected_username=normalized_expected,
            normalized_suggested_username=normalized_suggested,
            target_username=normalized_expected,
            reason="expected_account_not_listed",
            dashboard_action_type="review_account_picker_missing_expected",
            clone_reuse_allowed=clone_reuse_allowed,
        )

    if safe_screen_type == ACTIVE_ACCOUNT_PROFILE:
        if normalized_suggested and normalized_suggested == normalized_expected:
            return _decision(
                ok=True,
                screen_type=safe_screen_type,
                decision="connected_expected_account",
                expected_username=expected_username,
                suggested_username=suggested_username or "",
                normalized_expected_username=normalized_expected,
                normalized_suggested_username=normalized_suggested,
                reason="active_profile_matches_expected",
                clone_reuse_allowed=clone_reuse_allowed,
            )
        lifecycle_status, lookup_error = _lookup_lifecycle_status(
            normalized_suggested,
            account_lifecycle_lookup,
        )
        if lifecycle_status in STOPPED_LIFECYCLE_STATUSES and clone_reuse_allowed:
            return _decision(
                ok=True,
                screen_type=safe_screen_type,
                decision="recover_old_logged_in_account",
                expected_username=expected_username,
                suggested_username=suggested_username or "",
                normalized_expected_username=normalized_expected,
                normalized_suggested_username=normalized_suggested,
                target_username=normalized_suggested,
                next_action="open_account_switcher_then_login_existing",
                reason="old_logged_in_account_reusable",
                should_recover_old_logged_in_account=True,
                audit_reason="old_logged_in_account_recovery",
                clone_reuse_allowed=clone_reuse_allowed,
                lifecycle_status=lifecycle_status,
            )
        reason = (
            "lifecycle_lookup_failed_wrong_active_account_requires_admin_review"
            if lookup_error
            else "wrong_active_account_requires_admin_review"
        )
        return _decision(
            ok=False,
            screen_type=safe_screen_type,
            decision="block_wrong_active_account",
            expected_username=expected_username,
            suggested_username=suggested_username or "",
            normalized_expected_username=normalized_expected,
            normalized_suggested_username=normalized_suggested,
            target_username=normalized_suggested,
            reason=reason,
            should_escalate=True,
            publish_login_status="mismatch",
            provisioning_status="blocked",
            onboarding_status="blocked",
            dashboard_action_type="review_logged_in_account_mismatch",
            clone_reuse_allowed=clone_reuse_allowed,
            lifecycle_status=lifecycle_status,
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
        onboarding_status="blocked",
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
    should_tap_already_have_profile: bool = False,
    should_tap_expected_account: bool = False,
    should_recover_old_logged_in_account: bool = False,
    should_start_login_form_flow: bool = False,
    publish_login_status: str | None = None,
    provisioning_status: str | None = None,
    onboarding_status: str | None = None,
    dashboard_action_type: str | None = None,
    audit_reason: str | None = None,
    lifecycle_status: str = "unknown",
    target_username: str = "",
) -> LoginScreenRouteDecision:
    metadata = clean_login_probe_metadata(
        {
            "source": "login_screen_router",
            "screen_type": screen_type,
            "decision": decision,
            "reason": reason,
            "suggested_username": normalized_suggested_username,
            "expected_username": normalized_expected_username,
            "target_username": target_username,
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
        target_username=target_username,
        next_action=next_action,
        reason=reason,
        should_escalate=should_escalate,
        should_tap_continue=should_tap_continue,
        should_tap_use_another_profile=should_tap_use_another_profile,
        should_tap_already_have_profile=should_tap_already_have_profile,
        should_tap_expected_account=should_tap_expected_account,
        should_recover_old_logged_in_account=should_recover_old_logged_in_account,
        should_start_login_form_flow=should_start_login_form_flow,
        publish_login_status=publish_login_status,
        provisioning_status=provisioning_status,
        onboarding_status=onboarding_status,
        dashboard_action_type=dashboard_action_type,
        audit_reason=audit_reason,
        metadata=metadata,
    )
