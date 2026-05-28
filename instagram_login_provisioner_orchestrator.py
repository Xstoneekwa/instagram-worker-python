"""Isolated login provisioning orchestrator skeleton.

Entry 2E-5J assembles the validated login/provisioning building blocks without
hooking the runner, sender, devices, or real business flows. All side effects
remain injectable and disabled by default.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field, replace
from types import SimpleNamespace
from typing import Any, Callable, Optional

from instagram_credentials_runtime_access import (
    InstagramLoginCredentialsResult,
    SecretValue,
    credential_result_safe_dict,
    redact_credentials_payload,
)
from instagram_login_action_executor import execute_login_screen_decision
from instagram_login_password_form_executor import execute_login_form_credentials
from instagram_login_screen_router import normalize_instagram_username, route_login_screen
from instagram_login_status_classifier import (
    LoginProbeOutcome,
    classify_login_probe_outcome,
    clean_login_probe_metadata,
    normalize_login_probe_outcome,
)
from instagram_login_ui_probe import detect_login_probe_outcome_from_hierarchy, extract_login_screen_signals_from_hierarchy


TRANSIENT_RETRY_FAILURES = {
    "username_field_not_found",
    "password_field_not_found",
    "login_button_not_found",
    "ambiguous_login_form",
    "post_submit_dump_failed",
    "input_failed",
    "submit_failed",
}
CREDENTIALS_MISSING_REASONS = {
    "credentials_missing",
    "credentials_not_found",
    "credentials_lookup_missing",
    "metadata_not_found",
    "no_active_credentials",
}
CREDENTIALS_MISSING_ERROR_CODES = CREDENTIALS_MISSING_REASONS
NO_RETRY_FAILURES = {
    "login_form_not_validated",
    "expected_username_missing",
    "password_secret_missing",
    "password_secret_invalid",
    "vault_secret_payload_missing_password",
    "vault_secret_password_invalid",
    "blocked_secret_payload_shape",
    "credentials_missing",
    "credentials_invalid",
    "login_failed",
    "needs_2fa",
    "checkpoint",
    "mismatch",
    "wrong_account",
    "block_wrong_suggested_account",
    "save_password_prompt_blocking",
    "username_prefilled_not_editable",
    "username_input_failed",
}
MAX_RETRY_ATTEMPTS = 1
POST_CONTINUE_REOBSERVE_WAIT_MS = 1500
PROFILE_MENU_REOBSERVE_WAIT_MS = 1500
PROFILE_REFRESH_WAIT_MS = 500
DEFAULT_INSTAGRAM_PACKAGE_NAME = "com.instagram.android"
DEFAULT_POST_APP_START_WAIT_MS = 1500
MAX_POST_APP_START_WAIT_MS = 3000
DEFAULT_STARTUP_OBSERVATIONS = 4
DEFAULT_STARTUP_INTERVAL_MS = 1000
MAX_STARTUP_OBSERVATIONS = 6
MAX_STARTUP_INTERVAL_MS = 1500
MAX_LOGOUT_SETTINGS_SCROLLS = 5
LOGOUT_SETTINGS_SCROLL_WAIT_MS = 500
LOGOUT_BUTTON_LABELS = (
    "log out",
    "logout",
    "se déconnecter",
    "se deconnecter",
    "déconnexion",
    "deconnexion",
)

CredentialsGetter = Callable[[str], Any]
PreviousAccountLifecycleLookup = Callable[[str, dict[str, Any]], dict[str, Any]]
Publisher = Callable[..., dict[str, Any]]
Timer = Callable[[], float]
Sleeper = Callable[[float], None]

REUSABLE_PREVIOUS_ACCOUNT_LIFECYCLES = {"canceled", "stopped", "archived"}
LOGOUT_FALLBACK_LIFECYCLE_SOURCES = {"operator_smoke_override", "lifecycle_lookup_safe"}
CONNECTED_HOME_IDENTITY_SCREENS = frozenset(
    {
        "active_account_home",
        "active_account_profile",
        "connected_home",
        "connected_profile",
    }
)
POST_LOGOUT_KNOWN_SCREENS = {
    "login_form_empty",
    "login_form_prefilled_username",
    "continue_as_candidate",
    "account_picker",
    "continue_password_only",
    "connected",
}
POST_LOGOUT_SETTLING_OBSERVATIONS = 6
POST_LOGOUT_SETTLING_INTERVAL_MS = DEFAULT_STARTUP_INTERVAL_MS
PARENT_APP_START_METADATA_KEYS = (
    "app_start_attempted",
    "app_start_ok",
    "observe_current_screen_only",
    "package_name",
    "post_start_wait_ms",
    "screen_after_app_start",
    "screen_after_app_start_initial",
    "screen_after_app_start_final",
    "startup_observation_count",
    "startup_wait_total_ms",
    "startup_screens",
    "startup_final_screen_type",
    "startup_settling_used",
)
POST_ADD_EXISTING_RESUME_SCREENS = frozenset(
    {
        "login_form_empty",
        "login_form_prefilled_username",
        "continue_as_candidate",
        "account_picker",
        "continue_password_only",
        "checkpoint",
        "needs_2fa",
        "login_failed",
    }
)
ROUTING_SCREEN_TYPES = {
    "continue_as_candidate",
    "account_picker",
    "login_form_empty",
    "login_form_prefilled_username",
    "continue_password_only",
    "active_account_profile",
}
DEFAULT_USE_ANOTHER_PROFILE_INTERVAL_MS = 1000


@dataclass(frozen=True)
class LoginProvisioningFlowResult:
    ok: bool
    completed: bool
    final_outcome: str
    final_login_status: str | None
    final_provisioning_status: str | None
    final_onboarding_status: str | None
    reason: str
    failure_reason: str | None = None
    retry_attempted: bool = False
    retry_count: int = 0
    actions_taken: list[str] = field(default_factory=list)
    dashboard_action_type: str | None = None
    should_publish_status: bool = False
    publish_payload: dict[str, Any] | None = None
    published: bool = False
    publish_reason: str | None = None
    timings: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    safe_metadata: dict[str, Any] = field(default_factory=dict)


def run_login_provisioning_flow(
    d: Any,
    *,
    account_id: str,
    expected_username: str,
    credentials_getter: CredentialsGetter,
    lifecycle_lookup: Callable[[str], dict[str, Any]] | None = None,
    previous_account_lifecycle_lookup: PreviousAccountLifecycleLookup | None = None,
    clone_reuse_allowed: bool = False,
    publisher: Publisher | None = None,
    publish_enabled: bool = False,
    max_retry_attempts: int = MAX_RETRY_ATTEMPTS,
    initial_signals: dict | None = None,
    dry_run: bool = False,
    start_app_before_probe: bool = True,
    observe_current_screen_only: bool = False,
    package_name: str = DEFAULT_INSTAGRAM_PACKAGE_NAME,
    post_start_wait_ms: int = DEFAULT_POST_APP_START_WAIT_MS,
    post_submit_timeout_ms: Optional[int] = None,
    operator_smoke_active_account_username: str | None = None,
    operator_smoke_allow_logout_fallback: bool = False,
    timer: Timer | None = None,
    sleeper: Sleeper | None = None,
) -> LoginProvisioningFlowResult:
    """Run one isolated provisioning decision flow.

    Real/default provisioning starts the targeted Instagram package before
    probing. Unit tests and manual diagnostics may opt into
    observe_current_screen_only=True to preserve pure current-screen observation.
    The only credential UI actions are delegated to already validated executors,
    and publication remains disabled unless the caller explicitly injects a
    publisher and enables it.
    """

    timer = timer or time.perf_counter
    sleeper = sleeper or time.sleep
    total_start = timer()
    timings = _empty_timings()
    warnings: list[str] = []
    actions_taken: list[str] = []
    post_continue_metadata: dict[str, Any] = {}
    safe_account_id = str(account_id or "").strip()
    safe_expected_username = str(expected_username or "").strip()
    safe_operator_smoke_active_username = _normalize_identity_username(operator_smoke_active_account_username)
    max_retries = min(MAX_RETRY_ATTEMPTS, max(0, int(max_retry_attempts or 0)))
    safe_package_name = _safe_package_name(package_name)
    bounded_post_start_wait_ms = _clamp_post_start_wait_ms(post_start_wait_ms)
    app_start_attempted = bool(start_app_before_probe) and not bool(observe_current_screen_only)
    screen_preparation_metadata = {
        "expected_username": safe_expected_username,
        "observe_current_screen_only": bool(observe_current_screen_only),
        "app_start_attempted": app_start_attempted,
        "app_start_ok": None,
        "package_name": safe_package_name,
        "post_start_wait_ms": bounded_post_start_wait_ms if app_start_attempted else 0,
        "screen_after_app_start": "",
        "screen_after_app_start_initial": "",
        "screen_after_app_start_final": "",
        "startup_observation_count": 0,
        "startup_wait_total_ms": 0,
        "startup_screens": [],
        "startup_final_screen_type": "",
        "startup_settling_used": False,
        "would_submit_password": False,
    }

    if app_start_attempted:
        try:
            start = timer()
            d.app_start(safe_package_name)
            timings["app_start_ms"] += _elapsed_ms(start, timer())
            screen_preparation_metadata["app_start_ok"] = True
        except Exception:
            timings["app_start_ms"] += _elapsed_ms(start, timer())
            screen_preparation_metadata["app_start_ok"] = False
            return _finalize(
                ok=False,
                completed=False,
                final_outcome="unknown",
                reason="app_start_failed",
                failure_reason="app_start_failed",
                final_login_status="logged_out",
                final_provisioning_status="login_pending",
                final_onboarding_status="credentials_required",
                should_publish_status=False,
                account_id=safe_account_id,
                expected_username=safe_expected_username,
                actions_taken=actions_taken,
                timings=timings,
                warnings=warnings,
                extra_metadata=screen_preparation_metadata,
                total_start=total_start,
                timer=timer,
                publisher=publisher,
                publish_enabled=publish_enabled,
            )
        timings["post_start_wait_ms"] = bounded_post_start_wait_ms
        if bounded_post_start_wait_ms > 0:
            sleeper(bounded_post_start_wait_ms / 1000.0)

    signals = dict(initial_signals or {})
    if not signals and app_start_attempted:
        startup_observation = _observe_startup_screen_settled(
            d,
            expected_username=safe_expected_username,
            timings=timings,
            timer=timer,
            sleeper=sleeper,
            interval_ms=DEFAULT_STARTUP_INTERVAL_MS,
            max_observations=DEFAULT_STARTUP_OBSERVATIONS,
        )
        signals = dict(startup_observation.get("signals") or {})
        screen_preparation_metadata.update(
            {
                "screen_after_app_start": startup_observation["final_screen_type"],
                "screen_after_app_start_initial": startup_observation["initial_screen_type"],
                "screen_after_app_start_final": startup_observation["final_screen_type"],
                "startup_observation_count": startup_observation["observation_count"],
                "startup_wait_total_ms": startup_observation["wait_total_ms"],
                "startup_screens": startup_observation["screens"],
                "startup_final_screen_type": startup_observation["final_screen_type"],
                "startup_settling_used": bool(startup_observation["observation_count"] > 1),
            }
        )
    elif not signals:
        start = timer()
        signals = _observe_login_signals(d, expected_username=safe_expected_username)
        timings["observe_ms"] += _elapsed_ms(start, timer())
    if app_start_attempted:
        if not screen_preparation_metadata["screen_after_app_start"]:
            screen_type = _screen_after_app_start(signals)
            screen_preparation_metadata["screen_after_app_start"] = screen_type
            screen_preparation_metadata["screen_after_app_start_initial"] = screen_type
            screen_preparation_metadata["screen_after_app_start_final"] = screen_type
            screen_preparation_metadata["startup_observation_count"] = 1
            screen_preparation_metadata["startup_screens"] = [screen_type]
            screen_preparation_metadata["startup_final_screen_type"] = screen_type
        post_app_start_outcome = _post_action_outcome_from_signals(signals)
        if (
            post_app_start_outcome == LoginProbeOutcome.CONNECTED.value
            and not _defer_connected_no_password_early_exit(
                signals,
                screen_preparation_metadata,
                expected_username=safe_expected_username,
            )
        ):
            classification = classify_login_probe_outcome(LoginProbeOutcome.CONNECTED.value)
            return _finalize(
                ok=True,
                completed=True,
                final_outcome=LoginProbeOutcome.CONNECTED.value,
                reason="connected_no_password_needed",
                failure_reason=None,
                final_login_status=classification.login_status,
                final_provisioning_status=classification.provisioning_status,
                final_onboarding_status=classification.onboarding_status,
                should_publish_status=False,
                account_id=safe_account_id,
                expected_username=safe_expected_username,
                actions_taken=actions_taken,
                timings=timings,
                warnings=warnings,
                extra_metadata={
                    **screen_preparation_metadata,
                    "password_required": False,
                    "ready_for_password_submit": False,
                },
                total_start=total_start,
                timer=timer,
                publisher=publisher,
                publish_enabled=publish_enabled,
            )
        if str(signals.get("screen_type") or "unknown") == "unknown":
            return _finalize(
                ok=False,
                completed=False,
                final_outcome="unknown",
                reason="screen_preparation_failed_after_startup_settling",
                failure_reason="screen_preparation_failed_after_startup_settling",
                final_login_status="logged_out",
                final_provisioning_status="login_pending",
                final_onboarding_status="credentials_required",
                should_publish_status=False,
                account_id=safe_account_id,
                expected_username=safe_expected_username,
                actions_taken=actions_taken,
                timings=timings,
                warnings=warnings,
                extra_metadata=screen_preparation_metadata,
                total_start=total_start,
                timer=timer,
                publisher=publisher,
                publish_enabled=publish_enabled,
            )
    elif (
        _post_action_outcome_from_signals(signals) == LoginProbeOutcome.CONNECTED.value
        and not _defer_connected_no_password_early_exit(
            signals,
            screen_preparation_metadata,
            expected_username=safe_expected_username,
        )
    ):
        classification = classify_login_probe_outcome(LoginProbeOutcome.CONNECTED.value)
        return _finalize(
            ok=True,
            completed=True,
            final_outcome=LoginProbeOutcome.CONNECTED.value,
            reason="connected_no_password_needed",
            failure_reason=None,
            final_login_status=classification.login_status,
            final_provisioning_status=classification.provisioning_status,
            final_onboarding_status=classification.onboarding_status,
            should_publish_status=False,
            account_id=safe_account_id,
            expected_username=safe_expected_username,
            actions_taken=actions_taken,
            timings=timings,
            warnings=warnings,
            extra_metadata={
                **screen_preparation_metadata,
                "password_required": False,
                "ready_for_password_submit": False,
            },
            total_start=total_start,
            timer=timer,
            publisher=publisher,
            publish_enabled=publish_enabled,
        )

    old_logged_in_metadata: dict[str, Any] = dict(screen_preparation_metadata)
    if signals.get("screen_type") in {"active_account_home", "active_account_profile"}:
        if dry_run and signals.get("screen_type") == "active_account_home":
            timings["total_ms"] = _elapsed_ms(total_start, timer())
            return LoginProvisioningFlowResult(
                ok=True,
                completed=False,
                final_outcome="dry_run",
                final_login_status=None,
                final_provisioning_status=None,
                final_onboarding_status=None,
                reason="dry_run_active_home_needs_profile_identification",
                failure_reason=None,
                retry_attempted=False,
                retry_count=0,
                actions_taken=[*actions_taken, "route:open_profile_from_home"],
                dashboard_action_type=None,
                should_publish_status=False,
                publish_payload=None,
                published=False,
                publish_reason="disabled",
                timings=dict(timings),
                warnings=list(warnings),
                safe_metadata=clean_login_probe_metadata(
                    redact_credentials_payload(
                        {
                            "dry_run": True,
                            "screen_type": "active_account_home",
                            "router_decision": "open_profile_from_home",
                            "would_tap_profile_bottom_nav": True,
                            "would_submit_password": False,
                            "would_publish": False,
                            "logout_attempted": False,
                        }
                    )
                ),
            )
        if signals.get("screen_type") == "active_account_home":
            start = timer()
            action_result = execute_login_screen_decision(
                d,
                SimpleNamespace(decision="open_profile_from_home"),
                post_action_wait_ms=500,
            )
            timings["action_ms"] += _elapsed_ms(start, timer())
            actions_taken.append(action_result.action)
            if not action_result.ok:
                return _finalize(
                    ok=False,
                    completed=False,
                    final_outcome="action_failed",
                    reason=action_result.failure_reason or action_result.reason,
                    failure_reason=action_result.failure_reason or action_result.reason,
                    account_id=safe_account_id,
                    expected_username=safe_expected_username,
                    actions_taken=actions_taken,
                    timings=timings,
                    warnings=[*warnings, *action_result.warnings],
                    extra_metadata=old_logged_in_metadata,
                    total_start=total_start,
                    timer=timer,
                    publisher=publisher,
                    publish_enabled=publish_enabled,
                )
            start = timer()
            signals = _observe_login_signals(d, expected_username=safe_expected_username)
            timings["observe_ms"] += _elapsed_ms(start, timer())
            old_logged_in_metadata["profile_opened"] = signals.get("screen_type") == "active_account_profile"
            if signals.get("screen_type") == "active_account_home":
                operator_username = _safe_public_text(safe_operator_smoke_active_username)
                if operator_username:
                    signals = {
                        **signals,
                        "screen_type": "active_account_profile",
                        "actual_logged_in_username": operator_username,
                    }
                    old_logged_in_metadata["active_account_identity_source"] = "operator_smoke_override"
                else:
                    return _finalize(
                        ok=False,
                        completed=True,
                        final_outcome="unknown",
                        reason="identity_unknown_on_connected_home",
                        failure_reason="identity_unknown_on_connected_home",
                        final_login_status="logged_out",
                        final_provisioning_status="login_pending",
                        final_onboarding_status="credentials_required",
                        should_publish_status=False,
                        account_id=safe_account_id,
                        expected_username=safe_expected_username,
                        actions_taken=actions_taken,
                        timings=timings,
                        warnings=warnings,
                        extra_metadata={
                            **old_logged_in_metadata,
                            "screen_after_app_start_final": screen_preparation_metadata.get(
                                "screen_after_app_start_final"
                            )
                            or "active_account_home",
                            "password_required": False,
                            "ready_for_password_submit": False,
                            "would_submit_password": False,
                        },
                        total_start=total_start,
                        timer=timer,
                        publisher=publisher,
                        publish_enabled=publish_enabled,
                    )

        if signals.get("screen_type") == "active_account_profile":
            actual_username = _safe_public_text(signals.get("actual_logged_in_username"))
            if not actual_username and safe_operator_smoke_active_username:
                actual_username = safe_operator_smoke_active_username
                signals = {**signals, "actual_logged_in_username": actual_username}
                old_logged_in_metadata["active_account_identity_source"] = "operator_smoke_override"
            if not actual_username:
                return _finalize(
                    ok=False,
                    completed=True,
                    final_outcome="unknown",
                    reason="identity_unknown_on_connected_home",
                    failure_reason="identity_unknown_on_connected_home",
                    final_login_status="logged_out",
                    final_provisioning_status="login_pending",
                    final_onboarding_status="credentials_required",
                    should_publish_status=False,
                    account_id=safe_account_id,
                    expected_username=safe_expected_username,
                    actions_taken=actions_taken,
                    timings=timings,
                    warnings=warnings,
                    extra_metadata={
                        **old_logged_in_metadata,
                        "profile_opened": True,
                        "password_required": False,
                        "ready_for_password_submit": False,
                        "would_submit_password": False,
                    },
                    total_start=total_start,
                    timer=timer,
                    publisher=publisher,
                    publish_enabled=publish_enabled,
                )
            old_logged_in_metadata = {
                **old_logged_in_metadata,
                "actual_logged_in_username": actual_username,
                "active_account_username": actual_username,
                "account_mismatch_detected": bool(
                    actual_username
                    and actual_username.strip().lstrip("@").lower()
                    != safe_expected_username.strip().lstrip("@").lower()
                ),
                "profile_opened": True,
                "profile_username": actual_username,
                "profile_menu_initially_missing": bool(signals.get("profile_menu_missing_transient")),
                "profile_refresh_attempted": False,
                "old_logged_in_recovery_attempted": False,
                "logout_attempted": False,
                "would_submit_password": False,
            }
            if actual_username and actual_username.strip().lstrip("@").lower() == safe_expected_username.strip().lstrip("@").lower():
                classification = classify_login_probe_outcome(LoginProbeOutcome.CONNECTED.value)
                return _finalize(
                    ok=True,
                    completed=True,
                    final_outcome=LoginProbeOutcome.CONNECTED.value,
                    reason="active_profile_matches_expected",
                    failure_reason=None,
                    final_login_status=classification.login_status,
                    final_provisioning_status=classification.provisioning_status,
                    final_onboarding_status=classification.onboarding_status,
                    should_publish_status=False,
                    account_id=safe_account_id,
                    expected_username=safe_expected_username,
                    actions_taken=actions_taken,
                    timings=timings,
                    warnings=warnings,
                    extra_metadata={
                        **old_logged_in_metadata,
                        "password_required": False,
                        "ready_for_password_submit": False,
                    },
                    total_start=total_start,
                    timer=timer,
                    publisher=publisher,
                    publish_enabled=publish_enabled,
                )

            previous_account_lifecycle = _resolve_previous_account_lifecycle(
                suggested_username=actual_username,
                screen_type=signals.get("screen_type"),
                account_id=safe_account_id,
                expected_username=safe_expected_username,
                previous_account_lifecycle_lookup=previous_account_lifecycle_lookup,
                legacy_lifecycle_lookup=lifecycle_lookup,
                legacy_clone_reuse_allowed=clone_reuse_allowed,
            )
            recovery_route = route_login_screen(
                expected_username=safe_expected_username,
                suggested_username=actual_username,
                screen_type="active_account_profile",
                account_lifecycle_lookup=_router_lifecycle_lookup(previous_account_lifecycle),
                clone_reuse_allowed=bool(previous_account_lifecycle.get("clone_reuse_allowed")),
                account_id=safe_account_id,
            )
            actions_taken.append(f"route:{recovery_route.decision}")
            if dry_run:
                return _dry_run_result(
                    route=recovery_route,
                    signals={**signals, "suggested_username": actual_username},
                    account_id=safe_account_id,
                    expected_username=safe_expected_username,
                    actions_taken=actions_taken,
                    timings=timings,
                    warnings=warnings,
                    previous_account_lifecycle=previous_account_lifecycle,
                    total_start=total_start,
                    timer=timer,
                )
            old_logged_in_metadata = {
                **old_logged_in_metadata,
                **_flow_metadata(previous_account_lifecycle),
                "active_account_lifecycle_source": _safe_public_text(previous_account_lifecycle.get("source")),
                "active_account_lifecycle_status": _safe_public_text(previous_account_lifecycle.get("lifecycle_status")),
                "clone_reuse_allowed": bool(previous_account_lifecycle.get("clone_reuse_allowed")),
                "lifecycle_gate_result": recovery_route.decision,
                "old_logged_in_recovery_allowed": recovery_route.decision == "recover_old_logged_in_account",
            }
            if recovery_route.decision != "recover_old_logged_in_account":
                return _finalize(
                    ok=False,
                    completed=True,
                    final_outcome="mismatch",
                    reason=recovery_route.reason or "block_wrong_active_account",
                    failure_reason="mismatch",
                    final_login_status="mismatch",
                    final_provisioning_status="blocked",
                    final_onboarding_status="support_required",
                    dashboard_action_type=recovery_route.dashboard_action_type or "review_logged_in_account_mismatch",
                    should_publish_status=False,
                    account_id=safe_account_id,
                    expected_username=safe_expected_username,
                    actions_taken=actions_taken,
                    timings=timings,
                    warnings=warnings,
                    extra_metadata=old_logged_in_metadata,
                    total_start=total_start,
                    timer=timer,
                    publisher=publisher,
                    publish_enabled=publish_enabled,
                )

            logout_fallback_allowed, logout_fallback_reason = _logout_fallback_gate(
                actual_username=actual_username,
                expected_username=safe_expected_username,
                previous_account_lifecycle=previous_account_lifecycle,
                explicitly_allowed=bool(operator_smoke_allow_logout_fallback),
            )
            old_logged_in_metadata.update(
                {
                    "logout_fallback_allowed": logout_fallback_allowed,
                    "logout_fallback_reason": logout_fallback_reason,
                    "add_existing_attempted": False,
                    "add_existing_failed_reason": "",
                }
            )
            if logout_fallback_allowed:
                logout_result = run_old_account_logout_fallback_flow(
                    d,
                    account_id=safe_account_id,
                    expected_username=safe_expected_username,
                    previous_account_lifecycle_lookup=previous_account_lifecycle_lookup,
                    publisher=publisher,
                    publish_enabled=False,
                    initial_signals=signals,
                    timer=timer,
                    sleeper=sleeper,
                )
                post_logout_signals = dict(
                    logout_result.safe_metadata.get("post_logout_final_signals") or {}
                )
                if not post_logout_signals:
                    post_logout_settled = _observe_post_logout_settled(
                        d,
                        expected_username=safe_expected_username,
                        timings=timings,
                        timer=timer,
                        sleeper=sleeper,
                    )
                    post_logout_signals = dict(post_logout_settled.get("signals") or {})
                merged_logout_metadata = {
                    **old_logged_in_metadata,
                    **dict(logout_result.safe_metadata or {}),
                    "recovery_path": "logout_fallback",
                    "logout_fallback_allowed": True,
                    "logout_fallback_reason": logout_fallback_reason,
                    "add_existing_attempted": False,
                    "add_existing_failed_reason": "operator_smoke_logout_fallback_requested",
                    "post_logout_final_suggested_username": _safe_public_text(
                        post_logout_signals.get("suggested_username")
                        or logout_result.safe_metadata.get("post_logout_final_suggested_username")
                    ),
                }
                if not logout_result.ok:
                    return replace(
                        logout_result,
                        actions_taken=[*actions_taken, *logout_result.actions_taken],
                        timings=_merge_timings(timings, logout_result.timings),
                        safe_metadata=clean_login_probe_metadata(redact_credentials_payload(merged_logout_metadata)),
                    )
                resume_result = run_login_provisioning_flow(
                    d,
                    account_id=safe_account_id,
                    expected_username=safe_expected_username,
                    credentials_getter=credentials_getter,
                    lifecycle_lookup=lifecycle_lookup,
                    previous_account_lifecycle_lookup=previous_account_lifecycle_lookup,
                    clone_reuse_allowed=clone_reuse_allowed,
                    publisher=publisher,
                    publish_enabled=publish_enabled,
                    max_retry_attempts=max_retries,
                    initial_signals=post_logout_signals,
                    dry_run=dry_run,
                    start_app_before_probe=False,
                    observe_current_screen_only=True,
                    package_name=safe_package_name,
                    post_start_wait_ms=0,
                    post_submit_timeout_ms=post_submit_timeout_ms,
                    operator_smoke_active_account_username=None,
                    operator_smoke_allow_logout_fallback=False,
                    timer=timer,
                    sleeper=sleeper,
                )
                return replace(
                    resume_result,
                    actions_taken=[*actions_taken, *logout_result.actions_taken, *resume_result.actions_taken],
                    timings=_merge_timings(_merge_timings(timings, logout_result.timings), resume_result.timings),
                    safe_metadata=clean_login_probe_metadata(
                        redact_credentials_payload(
                            _merge_logout_resume_metadata(
                                merged_logout_metadata,
                                dict(resume_result.safe_metadata or {}),
                                logout_fallback_reason=logout_fallback_reason,
                            )
                        )
                    ),
                )

            old_logged_in_metadata["old_logged_in_recovery_attempted"] = True
            old_logged_in_metadata["recovery_path"] = "add_existing_account"
            old_logged_in_metadata["add_existing_attempted"] = True
            old_logged_in_metadata["add_account_sheet_opened"] = False
            old_logged_in_metadata["log_into_existing_account_tapped"] = False
            add_existing_prefix_steps = (
                (
                    SimpleNamespace(decision="open_account_switcher", target_username=actual_username),
                    "account_switcher_sheet",
                ),
                (SimpleNamespace(decision="tap_add_instagram_account"), ""),
            )
            for step_decision, expected_screen in add_existing_prefix_steps:
                start = timer()
                action_result = execute_login_screen_decision(
                    d,
                    step_decision,
                    post_action_wait_ms=500,
                )
                timings["action_ms"] += _elapsed_ms(start, timer())
                actions_taken.append(action_result.action)
                if not action_result.ok:
                    return _finalize(
                        ok=False,
                        completed=False,
                        final_outcome="action_failed",
                        reason=action_result.failure_reason or action_result.reason,
                        failure_reason=action_result.failure_reason or action_result.reason,
                        account_id=safe_account_id,
                        expected_username=safe_expected_username,
                        actions_taken=actions_taken,
                        timings=timings,
                        warnings=[*warnings, *action_result.warnings],
                        extra_metadata=old_logged_in_metadata,
                        total_start=total_start,
                        timer=timer,
                        publisher=publisher,
                        publish_enabled=publish_enabled,
                    )
                post_action_signals = dict(action_result.post_action_signals or {})
                if step_decision.decision == "tap_add_instagram_account":
                    old_logged_in_metadata["add_instagram_account_tapped"] = True
                    if _post_add_existing_screen_is_routable(post_action_signals):
                        signals = post_action_signals
                    continue
                start = timer()
                signals = _observe_login_signals(d, expected_username=safe_expected_username)
                timings["observe_ms"] += _elapsed_ms(start, timer())
                if step_decision.decision == "open_account_switcher":
                    old_logged_in_metadata["account_switcher_opened"] = signals.get("screen_type") == "account_switcher_sheet"
                    if expected_screen and signals.get("screen_type") != expected_screen:
                        return _finalize(
                            ok=False,
                            completed=False,
                            final_outcome="unknown",
                            reason=f"{expected_screen}_not_validated",
                            failure_reason=f"{expected_screen}_not_validated",
                            account_id=safe_account_id,
                            expected_username=safe_expected_username,
                            actions_taken=actions_taken,
                            timings=timings,
                            warnings=warnings,
                            extra_metadata=old_logged_in_metadata,
                            total_start=total_start,
                            timer=timer,
                            publisher=publisher,
                            publish_enabled=publish_enabled,
                        )

            if not _post_add_existing_screen_is_routable(signals):
                post_add_settled = _observe_post_add_existing_settled(
                    d,
                    expected_username=safe_expected_username,
                    timings=timings,
                    timer=timer,
                    sleeper=sleeper,
                )
                signals = dict(post_add_settled.get("signals") or {})
                _merge_post_add_existing_settled_metadata(old_logged_in_metadata, post_add_settled)
                timings["post_add_existing_wait_total_ms"] = int(post_add_settled.get("wait_total_ms") or 0)
            else:
                post_add_settled = _post_add_existing_settled_from_signals(signals)
                _merge_post_add_existing_settled_metadata(old_logged_in_metadata, post_add_settled)

            if str(signals.get("screen_type") or "") == "add_account_sheet":
                old_logged_in_metadata["add_account_sheet_opened"] = True
                start = timer()
                log_into_result = execute_login_screen_decision(
                    d,
                    SimpleNamespace(decision="tap_log_into_existing_account"),
                    post_action_wait_ms=500,
                )
                timings["action_ms"] += _elapsed_ms(start, timer())
                actions_taken.append(log_into_result.action)
                if not log_into_result.ok:
                    return _finalize(
                        ok=False,
                        completed=False,
                        final_outcome="action_failed",
                        reason=log_into_result.failure_reason or log_into_result.reason,
                        failure_reason=log_into_result.failure_reason or log_into_result.reason,
                        account_id=safe_account_id,
                        expected_username=safe_expected_username,
                        actions_taken=actions_taken,
                        timings=timings,
                        warnings=[*warnings, *log_into_result.warnings],
                        extra_metadata=old_logged_in_metadata,
                        total_start=total_start,
                        timer=timer,
                        publisher=publisher,
                        publish_enabled=publish_enabled,
                    )
                old_logged_in_metadata["log_into_existing_account_tapped"] = True
                post_log_into_settled = _observe_post_add_existing_settled(
                    d,
                    expected_username=safe_expected_username,
                    timings=timings,
                    timer=timer,
                    sleeper=sleeper,
                )
                signals = dict(post_log_into_settled.get("signals") or {})
                _merge_post_add_existing_settled_metadata(
                    old_logged_in_metadata,
                    post_log_into_settled,
                    append=True,
                )
                timings["post_add_existing_wait_total_ms"] = int(
                    timings.get("post_add_existing_wait_total_ms") or 0
                ) + int(post_log_into_settled.get("wait_total_ms") or 0)

            if not _post_add_existing_screen_is_routable(signals):
                sleeper(POST_CONTINUE_REOBSERVE_WAIT_MS / 1000.0)
                start = timer()
                signals = _observe_login_signals(d, expected_username=safe_expected_username)
                timings["observe_ms"] += _elapsed_ms(start, timer())
                final_screen = _preparation_screen_label(signals)
                old_logged_in_metadata["post_add_existing_observation_count"] = int(
                    old_logged_in_metadata.get("post_add_existing_observation_count") or 0
                ) + 1
                old_logged_in_metadata.setdefault("post_add_existing_screens", []).append(final_screen)
                old_logged_in_metadata["screen_after_add_existing_final"] = final_screen

            if not _post_add_existing_screen_is_routable(signals):
                return _finalize(
                    ok=False,
                    completed=False,
                    final_outcome="unknown",
                    reason="post_add_existing_unknown",
                    failure_reason="post_add_existing_unknown",
                    account_id=safe_account_id,
                    expected_username=safe_expected_username,
                    actions_taken=actions_taken,
                    timings=timings,
                    warnings=warnings,
                    extra_metadata=old_logged_in_metadata,
                    total_start=total_start,
                    timer=timer,
                    publisher=publisher,
                    publish_enabled=publish_enabled,
                )

    routing_signals = _routing_signals(
        signals,
        screen_preparation_metadata,
        previous_account_lifecycle=None,
    )
    previous_account_lifecycle = _resolve_previous_account_lifecycle(
        suggested_username=routing_signals.get("suggested_username"),
        screen_type=routing_signals.get("screen_type"),
        account_id=safe_account_id,
        expected_username=safe_expected_username,
        previous_account_lifecycle_lookup=previous_account_lifecycle_lookup,
        legacy_lifecycle_lookup=lifecycle_lookup,
        legacy_clone_reuse_allowed=clone_reuse_allowed,
    )
    routing_signals = _routing_signals(
        signals,
        screen_preparation_metadata,
        previous_account_lifecycle=previous_account_lifecycle,
    )
    route = _route_provisioning_screen(
        expected_username=safe_expected_username,
        routing_signals=routing_signals,
        previous_account_lifecycle=previous_account_lifecycle,
        account_id=safe_account_id,
    )
    actions_taken.append(f"route:{route.decision}")
    route_metadata = {
        "router_decision": route.decision,
        "routing_screen_type": routing_signals.get("screen_type"),
        "screen_type": routing_signals.get("screen_type"),
        "suggested_username": _safe_public_text(routing_signals.get("suggested_username")),
    }
    if routing_signals.get("screen_type") == "account_picker":
        route_metadata.update(
            {
                "available_usernames": [
                    _safe_public_text(username) for username in list(routing_signals.get("available_usernames") or [])
                ],
                "expected_username_present": bool(routing_signals.get("expected_username_present")),
                "account_picker_selection_executed": False,
            }
        )
    if routing_signals.get("screen_type") == "continue_password_only":
        displayed_username = _safe_public_text(routing_signals.get("suggested_username"))
        route_metadata.update(
            {
                "displayed_username": displayed_username,
                "password_only_username": displayed_username,
                "username_match": bool(
                    displayed_username
                    and displayed_username.strip().lstrip("@").lower() == safe_expected_username.strip().lstrip("@").lower()
                ),
            }
        )
    if route.decision == "select_expected_account_from_picker":
        route_metadata["selected_account_username"] = _safe_public_text(
            getattr(route, "target_username", "") or safe_expected_username
        )
    old_logged_in_metadata.update(route_metadata)

    if dry_run:
        return _dry_run_result(
            route=route,
            signals=signals,
            account_id=safe_account_id,
            expected_username=safe_expected_username,
            actions_taken=actions_taken,
            timings=timings,
            warnings=warnings,
            previous_account_lifecycle=previous_account_lifecycle,
            total_start=total_start,
            timer=timer,
        )

    if route.decision == "block_wrong_suggested_account":
        return _finalize(
            ok=False,
            completed=True,
            final_outcome="mismatch",
            reason=route.reason or "wrong_suggested_account_requires_admin_review",
            failure_reason="mismatch",
            final_login_status="mismatch",
            final_provisioning_status="blocked",
            final_onboarding_status="support_required",
            dashboard_action_type="review_account_mismatch",
            should_publish_status=True,
            account_id=safe_account_id,
            expected_username=safe_expected_username,
            actions_taken=actions_taken,
            timings=timings,
            warnings=warnings,
            extra_metadata={**_flow_metadata(previous_account_lifecycle), **old_logged_in_metadata},
            total_start=total_start,
            timer=timer,
            publisher=publisher,
            publish_enabled=publish_enabled,
        )

    if route.decision == "expected_account_not_listed":
        return _finalize(
            ok=False,
            completed=True,
            final_outcome="mismatch",
            reason=route.reason or "expected_account_not_listed",
            failure_reason="mismatch",
            final_login_status="mismatch",
            final_provisioning_status="blocked",
            final_onboarding_status="support_required",
            dashboard_action_type=route.dashboard_action_type or "review_account_picker_missing_expected",
            should_publish_status=False,
            account_id=safe_account_id,
            expected_username=safe_expected_username,
            actions_taken=actions_taken,
            timings=timings,
            warnings=warnings,
            extra_metadata={**_flow_metadata(previous_account_lifecycle), **old_logged_in_metadata, **route_metadata},
            total_start=total_start,
            timer=timer,
            publisher=publisher,
            publish_enabled=publish_enabled,
        )

    if route.decision == "unknown_no_action":
        return _finalize(
            ok=False,
            completed=False,
            final_outcome="unknown",
            reason="unknown_login_screen",
            failure_reason="unknown_login_screen",
            account_id=safe_account_id,
            expected_username=safe_expected_username,
            actions_taken=actions_taken,
            timings=timings,
            warnings=warnings,
            extra_metadata={
                **_flow_metadata(previous_account_lifecycle),
                **old_logged_in_metadata,
                **route_metadata,
            },
            total_start=total_start,
            timer=timer,
            publisher=publisher,
            publish_enabled=publish_enabled,
        )

    if route.decision in {
        "continue_expected_account",
        "select_expected_account_from_picker",
        "use_another_profile_previous_account_stopped",
    }:
        start = timer()
        action_result = execute_login_screen_decision(d, route, post_action_wait_ms=0)
        timings["action_ms"] += _elapsed_ms(start, timer())
        actions_taken.append(action_result.action)
        if action_result.action == "tap_expected_account":
            old_logged_in_metadata.update(
                {
                    "selected_account_username": _safe_public_text(getattr(route, "target_username", "") or safe_expected_username),
                    "account_picker_selection_executed": bool(action_result.executed),
                    **{
                        key: action_result.metadata[key]
                        for key in (
                            "account_picker_target_resolution_method",
                            "account_picker_target_row_count",
                            "account_picker_target_node_count",
                            "account_picker_action_result",
                            "account_picker_visible_usernames_count",
                            "account_picker_selected_row_index_if_known",
                        )
                        if key in action_result.metadata
                    },
                }
            )
        if not action_result.ok:
            return _finalize(
                ok=False,
                completed=False,
                final_outcome="action_failed",
                reason=action_result.failure_reason or action_result.reason,
                failure_reason=action_result.failure_reason or action_result.reason,
                account_id=safe_account_id,
                expected_username=safe_expected_username,
                actions_taken=actions_taken,
                timings=timings,
                warnings=[*warnings, *action_result.warnings],
                extra_metadata={**_flow_metadata(previous_account_lifecycle), **old_logged_in_metadata},
                total_start=total_start,
                timer=timer,
                publisher=publisher,
                publish_enabled=publish_enabled,
            )
        signals = dict(action_result.post_action_signals or {})
        if _signals_confirm_login_form(signals) and action_result.action == "tap_use_another_profile":
            final_screen = _preparation_screen_label(signals)
            post_continue_metadata.update(
                {
                    "post_use_another_profile_observation_count": 1,
                    "post_use_another_profile_screens": [final_screen],
                    "post_use_another_profile_wait_total_ms": 0,
                    "screen_after_use_another_profile_final": final_screen,
                }
            )
        if not _signals_confirm_login_form(signals):
            metadata_prefix = _post_action_metadata_prefix(action_result.action)
            should_settle = bool(metadata_prefix) and (
                action_result.action == "tap_use_another_profile"
                or _should_reobserve_post_action_transition(action_result.action, signals)
            )
            if should_settle:
                initial_screen = (
                    "transition_loading"
                    if _signals_show_loading_transition(signals)
                    else "transition_unknown"
                )
                warnings.append(f"{metadata_prefix}_reobserve_after_transition")
                interval_ms = (
                    DEFAULT_USE_ANOTHER_PROFILE_INTERVAL_MS
                    if action_result.action == "tap_use_another_profile"
                    else DEFAULT_STARTUP_INTERVAL_MS
                )
                settled = _observe_preparation_screen_settled(
                    d,
                    expected_username=safe_expected_username,
                    timings=timings,
                    timer=timer,
                    sleeper=sleeper,
                    initial_screen=initial_screen,
                    interval_ms=interval_ms,
                    max_observations=DEFAULT_STARTUP_OBSERVATIONS,
                )
                signals = dict(settled.get("signals") or {})
                if action_result.action == "tap_use_another_profile":
                    post_continue_metadata = {
                        "post_use_another_profile_initial_screen": initial_screen,
                        "post_use_another_profile_reobserve": True,
                        "post_use_another_profile_observation_count": settled["observation_count"],
                        "post_use_another_profile_screens": settled["screens"],
                        "post_use_another_profile_wait_total_ms": settled["wait_total_ms"],
                        "screen_after_use_another_profile_final": settled["final_screen_type"],
                    }
                else:
                    post_continue_metadata = {
                        f"{metadata_prefix}_initial_screen": initial_screen,
                        f"{metadata_prefix}_reobserve": True,
                        f"{metadata_prefix}_reobserve_count": settled["observation_count"],
                        f"{metadata_prefix}_screens": settled["screens"],
                        f"{metadata_prefix}_wait_total_ms": settled["wait_total_ms"],
                        f"{metadata_prefix}_final_screen_type": settled["final_screen_type"],
                    }
                    if action_result.action == "tap_expected_account":
                        post_continue_metadata.update(
                            {
                                "post_account_picker_observation_count": settled["observation_count"],
                                "post_account_picker_screens": settled["screens"],
                                "screen_after_account_picker_final": settled["final_screen_type"],
                            }
                        )
                timings["post_continue_reobserve_wait_ms"] = settled["wait_total_ms"]
            else:
                start = timer()
                signals = _observe_login_signals(d, expected_username=safe_expected_username)
                timings["observe_ms"] += _elapsed_ms(start, timer())
                metadata_prefix = _post_action_metadata_prefix(action_result.action)
                if metadata_prefix:
                    final_key = (
                        "screen_after_use_another_profile_final"
                        if action_result.action == "tap_use_another_profile"
                        else f"{metadata_prefix}_final_screen_type"
                    )
                    post_continue_metadata[final_key] = _preparation_screen_label(signals)
                    if action_result.action == "tap_expected_account":
                        final_screen = _preparation_screen_label(signals)
                        post_continue_metadata.update(
                            {
                                "post_account_picker_observation_count": 1,
                                "post_account_picker_screens": [final_screen],
                                "screen_after_account_picker_final": final_screen,
                            }
                        )
        if action_result.action == "tap_expected_account" and "screen_after_account_picker_final" not in post_continue_metadata:
            final_screen = _preparation_screen_label(signals)
            post_continue_metadata.update(
                {
                    "post_account_picker_observation_count": 1,
                    "post_account_picker_screens": [final_screen],
                    "screen_after_account_picker_final": final_screen,
                }
            )
        post_action_lifecycle = _resolve_previous_account_lifecycle(
            suggested_username=signals.get("suggested_username"),
            screen_type=signals.get("screen_type"),
            account_id=safe_account_id,
            expected_username=safe_expected_username,
            previous_account_lifecycle_lookup=previous_account_lifecycle_lookup,
            legacy_lifecycle_lookup=lifecycle_lookup,
            legacy_clone_reuse_allowed=clone_reuse_allowed,
        )
        if _previous_account_lifecycle_has_gate_metadata(post_action_lifecycle):
            previous_account_lifecycle = post_action_lifecycle
        post_routing_signals = _routing_signals(
            signals,
            old_logged_in_metadata,
            previous_account_lifecycle=previous_account_lifecycle,
        )
        route = _route_provisioning_screen(
            expected_username=safe_expected_username,
            routing_signals=post_routing_signals,
            previous_account_lifecycle=previous_account_lifecycle,
            account_id=safe_account_id,
        )
        actions_taken.append(f"route:{route.decision}")
        route_metadata = {
            "router_decision": route.decision,
            "routing_screen_type": post_routing_signals.get("screen_type"),
            "screen_type": post_routing_signals.get("screen_type"),
            "suggested_username": _safe_public_text(post_routing_signals.get("suggested_username")),
        }
        old_logged_in_metadata.update(route_metadata)

    if route.decision == "username_prefilled_not_editable":
        return _finalize(
            ok=False,
            completed=True,
            final_outcome="username_prefilled_not_editable",
            reason="username_prefilled_not_editable",
            failure_reason="username_prefilled_not_editable",
            final_login_status="logged_out",
            final_provisioning_status="login_pending",
            final_onboarding_status="credentials_required",
            should_publish_status=False,
            account_id=safe_account_id,
            expected_username=safe_expected_username,
            actions_taken=actions_taken,
            timings=timings,
            warnings=warnings,
            extra_metadata={
                **_flow_metadata(previous_account_lifecycle),
                **old_logged_in_metadata,
                **post_continue_metadata,
                **_pre_submit_observation_metadata(signals),
            },
            total_start=total_start,
            timer=timer,
            publisher=publisher,
            publish_enabled=publish_enabled,
        )

    if not _route_starts_login_form_flow(route.decision) and not _signals_confirm_login_form(signals):
        post_action_outcome = _post_action_outcome_from_signals(signals)
        if post_action_outcome:
            classification = classify_login_probe_outcome(post_action_outcome)
            return _finalize(
                ok=post_action_outcome == LoginProbeOutcome.CONNECTED.value,
                completed=post_action_outcome
                in {
                    LoginProbeOutcome.CONNECTED.value,
                    LoginProbeOutcome.NEEDS_2FA.value,
                    LoginProbeOutcome.CHECKPOINT.value,
                    LoginProbeOutcome.LOGIN_FAILED.value,
                },
                final_outcome=post_action_outcome,
                reason=f"post_action_{classification.reason}",
                failure_reason=None if post_action_outcome == LoginProbeOutcome.CONNECTED.value else post_action_outcome,
                final_login_status=classification.login_status,
                final_provisioning_status=classification.provisioning_status,
                final_onboarding_status=classification.onboarding_status,
                dashboard_action_type=_dashboard_action_for_outcome(post_action_outcome),
                should_publish_status=False,
                account_id=safe_account_id,
                expected_username=safe_expected_username,
                actions_taken=actions_taken,
                timings=timings,
                warnings=warnings,
                extra_metadata={
                    **_flow_metadata(previous_account_lifecycle),
                    **old_logged_in_metadata,
                    **post_continue_metadata,
                    "post_action_status_candidate": post_action_outcome,
                    "password_required": False,
                    "ready_for_password_smoke": False,
                    "would_submit_password": False,
                },
                total_start=total_start,
                timer=timer,
                publisher=publisher,
                publish_enabled=publish_enabled,
            )
        return _finalize(
            ok=False,
            completed=False,
            final_outcome="unknown",
            reason=route.reason or "login_form_not_validated",
            failure_reason=route.reason or "login_form_not_validated",
            account_id=safe_account_id,
            expected_username=safe_expected_username,
            actions_taken=actions_taken,
            timings=timings,
            warnings=warnings,
            extra_metadata={**_flow_metadata(previous_account_lifecycle), **old_logged_in_metadata, **post_continue_metadata},
            total_start=total_start,
            timer=timer,
            publisher=publisher,
            publish_enabled=publish_enabled,
        )

    credentials = _load_credentials(
        credentials_getter,
        safe_account_id,
        expected_username=safe_expected_username,
    )
    if not credentials["ok"]:
        return _credentials_failure_result(
            credentials,
            account_id=safe_account_id,
            expected_username=safe_expected_username,
            actions_taken=actions_taken,
            timings=timings,
            warnings=warnings,
            extra_metadata={
                **_flow_metadata(previous_account_lifecycle),
                **old_logged_in_metadata,
                **post_continue_metadata,
                **_pre_submit_observation_metadata(signals),
            },
            total_start=total_start,
            timer=timer,
            publisher=publisher,
            publish_enabled=publish_enabled,
        )

    retry_count = 0
    retry_attempted = False
    password_result = _execute_password_form(
        d,
        expected_username=safe_expected_username,
        password=credentials["password"],
        signals=signals,
        post_submit_timeout_ms=post_submit_timeout_ms,
        timer=timer,
    )
    actions_taken.append("login_form_submit")

    while _should_retry_password_result(password_result, retry_count, max_retries):
        retry_attempted = True
        retry_count += 1
        actions_taken.append("retry_reobserve_login_form")
        start = timer()
        signals = _observe_login_signals(d, expected_username=safe_expected_username)
        timings["observe_ms"] += _elapsed_ms(start, timer())
        if not _signals_confirm_login_form(signals):
            warnings.append("retry_aborted_login_form_not_validated")
            break
        password_result = _execute_password_form(
            d,
            expected_username=safe_expected_username,
            password=credentials["password"],
            signals=signals,
            post_submit_timeout_ms=post_submit_timeout_ms,
            timer=timer,
        )
        actions_taken.append("login_form_submit_retry")

    outcome = _password_result_outcome(password_result)
    password_result_metadata = {"password_result": _safe_password_result_metadata(password_result)}
    if str(getattr(password_result, "failure_reason", "") or "") == "blocked_secret_payload_shape":
        return _finalize(
            ok=False,
            completed=True,
            final_outcome="secret_payload_not_password",
            reason="blocked_secret_payload_shape",
            failure_reason="blocked_secret_payload_shape",
            final_login_status="logged_out",
            final_provisioning_status="login_pending",
            final_onboarding_status="credentials_submitted",
            should_publish_status=False,
            account_id=safe_account_id,
            expected_username=safe_expected_username,
            actions_taken=actions_taken,
            timings=_merge_timings(timings, password_result.timings),
            warnings=[*warnings, *password_result.warnings],
            extra_metadata={
                **_flow_metadata(previous_account_lifecycle),
                **old_logged_in_metadata,
                **post_continue_metadata,
                **_pre_submit_observation_metadata(signals),
                **password_result_metadata,
                "password_submit_result": "blocked_secret_payload_shape",
            },
            total_start=total_start,
            timer=timer,
            publisher=publisher,
            publish_enabled=publish_enabled,
        )
    if outcome in {"password_input_missing_or_not_accepted", "password_input_failed"}:
        return _finalize(
            ok=False,
            completed=True,
            final_outcome=outcome,
            reason=getattr(password_result, "post_submit_probe_reason", None) or outcome,
            failure_reason=outcome,
            final_login_status="logged_out",
            final_provisioning_status="login_pending",
            final_onboarding_status="credentials_submitted",
            retry_attempted=retry_attempted,
            retry_count=retry_count,
            dashboard_action_type="retry_provisioning",
            should_publish_status=False,
            account_id=safe_account_id,
            expected_username=safe_expected_username,
            actions_taken=actions_taken,
            timings=_merge_timings(timings, password_result.timings),
            warnings=[*warnings, *password_result.warnings],
            extra_metadata={
                **_flow_metadata(previous_account_lifecycle),
                **old_logged_in_metadata,
                **post_continue_metadata,
                **_pre_submit_observation_metadata(signals),
                **password_result_metadata,
            },
            total_start=total_start,
            timer=timer,
            publisher=publisher,
            publish_enabled=publish_enabled,
        )

    if outcome in {"username_input_failed", "username_prefilled_not_editable"}:
        return _finalize(
            ok=False,
            completed=True,
            final_outcome=outcome,
            reason=getattr(password_result, "failure_reason", None) or outcome,
            failure_reason=outcome,
            final_login_status="logged_out",
            final_provisioning_status="login_pending",
            final_onboarding_status="credentials_required",
            retry_attempted=retry_attempted,
            retry_count=retry_count,
            should_publish_status=False,
            account_id=safe_account_id,
            expected_username=safe_expected_username,
            actions_taken=actions_taken,
            timings=_merge_timings(timings, password_result.timings),
            warnings=[*warnings, *password_result.warnings],
            extra_metadata={
                **_flow_metadata(previous_account_lifecycle),
                **old_logged_in_metadata,
                **post_continue_metadata,
                **_pre_submit_observation_metadata(signals),
                **password_result_metadata,
            },
            total_start=total_start,
            timer=timer,
            publisher=publisher,
            publish_enabled=publish_enabled,
        )

    if password_result.failure_reason and outcome == "unknown":
        return _finalize(
            ok=False,
            completed=False,
            final_outcome=outcome,
            reason=password_result.failure_reason,
            failure_reason=password_result.failure_reason,
            final_login_status="logged_out",
            final_provisioning_status="login_pending",
            final_onboarding_status="credentials_submitted",
            retry_attempted=retry_attempted,
            retry_count=retry_count,
            dashboard_action_type=_dashboard_action_for_failure(password_result.failure_reason),
            should_publish_status=False,
            account_id=safe_account_id,
            expected_username=safe_expected_username,
            actions_taken=actions_taken,
            timings=_merge_timings(timings, password_result.timings),
            warnings=[*warnings, *password_result.warnings],
            extra_metadata={
                **_flow_metadata(previous_account_lifecycle),
                **old_logged_in_metadata,
                **post_continue_metadata,
                **_pre_submit_observation_metadata(signals),
                **password_result_metadata,
            },
            total_start=total_start,
            timer=timer,
            publisher=publisher,
            publish_enabled=publish_enabled,
        )

    classification = classify_login_probe_outcome(outcome)
    dashboard_action_type = _dashboard_action_for_outcome(outcome)
    final_reason = _final_reason_for_password_outcome(outcome, password_result, classification.reason)
    return _finalize(
        ok=outcome == LoginProbeOutcome.CONNECTED.value,
        completed=outcome in {
            LoginProbeOutcome.CONNECTED.value,
            LoginProbeOutcome.NEEDS_2FA.value,
            LoginProbeOutcome.CHECKPOINT.value,
            LoginProbeOutcome.LOGIN_FAILED.value,
        },
        final_outcome=outcome,
        reason=final_reason,
        failure_reason=None if outcome == LoginProbeOutcome.CONNECTED.value else outcome,
        final_login_status=classification.login_status,
        final_provisioning_status=classification.provisioning_status,
        final_onboarding_status=classification.onboarding_status,
        retry_attempted=retry_attempted,
        retry_count=retry_count,
        dashboard_action_type=dashboard_action_type,
        should_publish_status=classification.should_publish,
        account_id=safe_account_id,
        expected_username=safe_expected_username,
        actions_taken=actions_taken,
        timings=_merge_timings(timings, password_result.timings),
        warnings=[*warnings, *password_result.warnings],
        extra_metadata={
            **_flow_metadata(previous_account_lifecycle),
            **old_logged_in_metadata,
            **post_continue_metadata,
            **_pre_submit_observation_metadata(signals),
            **password_result_metadata,
        },
        total_start=total_start,
        timer=timer,
        publisher=publisher,
        publish_enabled=publish_enabled,
    )


def run_old_account_logout_fallback_flow(
    d: Any,
    *,
    account_id: str,
    expected_username: str,
    previous_account_lifecycle_lookup: PreviousAccountLifecycleLookup | None = None,
    publisher: Publisher | None = None,
    publish_enabled: bool = False,
    initial_signals: dict | None = None,
    timer: Timer | None = None,
    sleeper: Callable[[float], None] | None = None,
) -> LoginProvisioningFlowResult:
    """Explicit no-password fallback for logging out a reusable old account.

    This is intentionally separate from the normal provisioning flow: callers
    must opt in after deciding that the Cas F path is unavailable.
    """

    timer = timer or time.perf_counter
    sleeper = sleeper or time.sleep
    total_start = timer()
    timings = _empty_timings()
    warnings: list[str] = []
    actions_taken: list[str] = []
    safe_account_id = str(account_id or "").strip()
    safe_expected_username = str(expected_username or "").strip()
    metadata: dict[str, Any] = {
        "recovery_path": "logout_fallback",
        "logout_fallback_allowed": False,
        "logout_fallback_reason": "",
        "add_existing_attempted": False,
        "add_existing_failed_reason": "",
        "logout_fallback_attempted": False,
        "logout_attempted": False,
        "logout_scroll_attempted": False,
        "logout_scroll_attempt_count": 0,
        "logout_settings_scroll_attempted": False,
        "logout_settings_scroll_count": 0,
        "logout_button_visible_before_scroll": False,
        "logout_button_visible_after_scroll": False,
        "logout_button_target_text": "",
        "logout_button_target_method": "",
        "logout_not_visible_reason": "",
        "settings_reobserve_after_menu": False,
        "save_login_prompt_handled": False,
        "save_login_info_prompt_detected": False,
        "save_login_info_not_now_tapped": False,
        "logout_confirmation_handled": False,
        "logout_confirmation_detected": False,
        "logout_confirmation_tapped": False,
        "profile_opened": False,
        "profile_username": "",
        "profile_menu_opened": False,
        "settings_opened": False,
        "logout_button_tapped": False,
        "post_logout_observation_count": 0,
        "post_logout_screens": [],
        "screen_after_logout_final": "",
        "would_submit_password": False,
        "would_publish": False,
    }

    signals = dict(initial_signals or {})
    if not signals:
        start = timer()
        signals = _observe_login_signals(d, expected_username=safe_expected_username)
        timings["observe_ms"] += _elapsed_ms(start, timer())

    if signals.get("screen_type") == "active_account_home":
        action_result = _execute_logout_step(
            d,
            SimpleNamespace(decision="open_profile_from_home"),
            timings=timings,
            actions_taken=actions_taken,
        )
        if not action_result.ok:
            return _logout_fallback_finalize(
                ok=False,
                final_outcome="action_failed",
                reason=action_result.failure_reason or action_result.reason,
                failure_reason=action_result.failure_reason or action_result.reason,
                account_id=safe_account_id,
                expected_username=safe_expected_username,
                actions_taken=actions_taken,
                timings=timings,
                warnings=[*warnings, *action_result.warnings],
                metadata=metadata,
                total_start=total_start,
                timer=timer,
                publisher=publisher,
                publish_enabled=publish_enabled,
            )
        start = timer()
        signals = _observe_login_signals(d, expected_username=safe_expected_username)
        timings["observe_ms"] += _elapsed_ms(start, timer())
        metadata["profile_opened"] = signals.get("screen_type") == "active_account_profile"

    if signals.get("screen_type") != "active_account_profile":
        return _logout_fallback_finalize(
            ok=False,
            final_outcome="unknown",
            reason="active_profile_not_confirmed",
            failure_reason="active_profile_not_confirmed",
            account_id=safe_account_id,
            expected_username=safe_expected_username,
            actions_taken=actions_taken,
            timings=timings,
            warnings=warnings,
            metadata=metadata,
            total_start=total_start,
            timer=timer,
            publisher=publisher,
            publish_enabled=publish_enabled,
        )

    actual_username = _safe_public_text(signals.get("actual_logged_in_username"))
    metadata["actual_logged_in_username"] = actual_username
    metadata["active_account_username"] = actual_username
    metadata["profile_username"] = actual_username
    metadata["profile_opened"] = True
    metadata["account_mismatch_detected"] = bool(
        actual_username
        and actual_username.strip().lstrip("@").lower() != safe_expected_username.strip().lstrip("@").lower()
    )
    if not actual_username:
        return _logout_fallback_finalize(
            ok=False,
            final_outcome="mismatch",
            reason="active_username_missing",
            failure_reason="active_username_missing",
            account_id=safe_account_id,
            expected_username=safe_expected_username,
            actions_taken=actions_taken,
            timings=timings,
            warnings=warnings,
            metadata=metadata,
            dashboard_action_type="review_logged_in_account_mismatch",
            total_start=total_start,
            timer=timer,
            publisher=publisher,
            publish_enabled=publish_enabled,
        )

    normalized_actual = actual_username.strip().lstrip("@").lower()
    normalized_expected = safe_expected_username.strip().lstrip("@").lower()
    if normalized_actual == normalized_expected:
        return _logout_fallback_finalize(
            ok=False,
            final_outcome="connected",
            reason="no_logout_expected_username",
            failure_reason="no_logout_expected_username",
            account_id=safe_account_id,
            expected_username=safe_expected_username,
            actions_taken=actions_taken,
            timings=timings,
            warnings=warnings,
            metadata=metadata,
            total_start=total_start,
            timer=timer,
            publisher=publisher,
            publish_enabled=publish_enabled,
        )

    previous_account_lifecycle = _resolve_previous_account_lifecycle(
        suggested_username=actual_username,
        screen_type="active_account_profile",
        account_id=safe_account_id,
        expected_username=safe_expected_username,
        previous_account_lifecycle_lookup=previous_account_lifecycle_lookup,
        legacy_lifecycle_lookup=None,
        legacy_clone_reuse_allowed=False,
    )
    metadata.update(_flow_metadata(previous_account_lifecycle))
    metadata["active_account_lifecycle_source"] = _safe_public_text(previous_account_lifecycle.get("source"))
    metadata["active_account_lifecycle_status"] = _safe_public_text(
        previous_account_lifecycle.get("lifecycle_status")
    )
    metadata["clone_reuse_allowed"] = bool(previous_account_lifecycle.get("clone_reuse_allowed"))
    lifecycle_ok = (
        previous_account_lifecycle.get("lifecycle_status") in REUSABLE_PREVIOUS_ACCOUNT_LIFECYCLES
        and bool(previous_account_lifecycle.get("clone_reuse_allowed"))
        and str(previous_account_lifecycle.get("source") or "").strip() in LOGOUT_FALLBACK_LIFECYCLE_SOURCES
    )
    metadata["lifecycle_gate_result"] = "allow_logout_fallback" if lifecycle_ok else "block_wrong_active_account"
    metadata["logout_fallback_allowed"] = bool(lifecycle_ok)
    metadata["logout_fallback_reason"] = metadata["lifecycle_gate_result"]
    if not lifecycle_ok:
        return _logout_fallback_finalize(
            ok=False,
            final_outcome="mismatch",
            reason="wrong_active_account_requires_admin_review",
            failure_reason="mismatch",
            account_id=safe_account_id,
            expected_username=safe_expected_username,
            actions_taken=actions_taken,
            timings=timings,
            warnings=warnings,
            metadata=metadata,
            dashboard_action_type="review_logged_in_account_mismatch",
            total_start=total_start,
            timer=timer,
            publisher=publisher,
            publish_enabled=publish_enabled,
        )

    signals, menu_ok, menu_metadata = _stabilize_profile_menu(
        d,
        signals=signals,
        actual_username=actual_username,
        expected_username=safe_expected_username,
        timings=timings,
        actions_taken=actions_taken,
        sleeper=sleeper,
    )
    metadata.update(menu_metadata)
    if not menu_ok:
        return _logout_fallback_finalize(
            ok=False,
            final_outcome="unknown",
            reason=metadata.get("profile_menu_failure_reason") or "profile_menu_not_found",
            failure_reason=metadata.get("profile_menu_failure_reason") or "profile_menu_not_found",
            account_id=safe_account_id,
            expected_username=safe_expected_username,
            actions_taken=actions_taken,
            timings=timings,
            warnings=warnings,
            metadata=metadata,
            total_start=total_start,
            timer=timer,
            publisher=publisher,
            publish_enabled=publish_enabled,
        )

    metadata["logout_fallback_attempted"] = True
    for decision in (SimpleNamespace(decision="open_profile_menu"),):
        action_result = _execute_logout_step(
            d,
            decision,
            timings=timings,
            actions_taken=actions_taken,
        )
        if not action_result.ok:
            return _logout_fallback_finalize(
                ok=False,
                final_outcome="action_failed",
                reason=action_result.failure_reason or action_result.reason,
                failure_reason=action_result.failure_reason or action_result.reason,
                account_id=safe_account_id,
                expected_username=safe_expected_username,
                actions_taken=actions_taken,
                timings=timings,
                warnings=[*warnings, *action_result.warnings],
                metadata=metadata,
                total_start=total_start,
                timer=timer,
                publisher=publisher,
                publish_enabled=publish_enabled,
            )
        start = timer()
        signals = _observe_login_signals(d, expected_username=safe_expected_username)
        timings["observe_ms"] += _elapsed_ms(start, timer())
        metadata["profile_menu_opened"] = signals.get("screen_type") in {"profile_menu_sheet", "settings_and_activity"}
    if signals.get("screen_type") not in {"profile_menu_sheet", "settings_and_activity"}:
        metadata["settings_reobserve_after_menu"] = True
        sleeper(POST_CONTINUE_REOBSERVE_WAIT_MS / 1000.0)
        start = timer()
        signals = _observe_login_signals(d, expected_username=safe_expected_username)
        timings["observe_ms"] += _elapsed_ms(start, timer())
        metadata["profile_menu_opened"] = signals.get("screen_type") in {"profile_menu_sheet", "settings_and_activity"}
    if signals.get("screen_type") == "profile_menu_sheet":
        action_result = _execute_logout_step(
            d,
            SimpleNamespace(decision="tap_settings_and_activity"),
            timings=timings,
            actions_taken=actions_taken,
        )
        if not action_result.ok:
            return _logout_fallback_finalize(
                ok=False,
                final_outcome="action_failed",
                reason=action_result.failure_reason or action_result.reason,
                failure_reason=action_result.failure_reason or action_result.reason,
                account_id=safe_account_id,
                expected_username=safe_expected_username,
                actions_taken=actions_taken,
                timings=timings,
                warnings=[*warnings, *action_result.warnings],
                metadata=metadata,
                total_start=total_start,
                timer=timer,
                publisher=publisher,
                publish_enabled=publish_enabled,
            )
        start = timer()
        signals = _observe_login_signals(d, expected_username=safe_expected_username)
        timings["observe_ms"] += _elapsed_ms(start, timer())
        metadata["settings_opened"] = signals.get("screen_type") == "settings_and_activity"

    if signals.get("screen_type") != "settings_and_activity":
        return _logout_fallback_finalize(
            ok=False,
            final_outcome="unknown",
            reason="settings_and_activity_not_validated",
            failure_reason="settings_and_activity_not_validated",
            account_id=safe_account_id,
            expected_username=safe_expected_username,
            actions_taken=actions_taken,
            timings=timings,
            warnings=warnings,
            metadata=metadata,
            total_start=total_start,
            timer=timer,
            publisher=publisher,
            publish_enabled=publish_enabled,
        )
    metadata["settings_opened"] = True

    logout_target = _logout_button_target_from_signals(signals)
    metadata["logout_button_visible_before_scroll"] = bool(logout_target.get("visible"))
    _update_logout_target_metadata(metadata, logout_target)
    if logout_target.get("ambiguous"):
        metadata["logout_not_visible_reason"] = "logout_ambiguous"
        return _logout_fallback_finalize(
            ok=False,
            final_outcome="unknown",
            reason="logout_ambiguous",
            failure_reason="logout_ambiguous",
            account_id=safe_account_id,
            expected_username=safe_expected_username,
            actions_taken=actions_taken,
            timings=timings,
            warnings=warnings,
            metadata=metadata,
            total_start=total_start,
            timer=timer,
            publisher=publisher,
            publish_enabled=publish_enabled,
        )

    if not logout_target.get("visible"):
        signals, logout_target = _settle_settings_logout_button(
            d,
            expected_username=safe_expected_username,
            signals=signals,
            metadata=metadata,
            timings=timings,
            timer=timer,
            sleeper=sleeper,
        )
        _update_logout_target_metadata(metadata, logout_target)
        if logout_target.get("ambiguous"):
            metadata["logout_not_visible_reason"] = "logout_ambiguous"
            return _logout_fallback_finalize(
                ok=False,
                final_outcome="unknown",
                reason="logout_ambiguous",
                failure_reason="logout_ambiguous",
                account_id=safe_account_id,
                expected_username=safe_expected_username,
                actions_taken=actions_taken,
                timings=timings,
                warnings=warnings,
                metadata=metadata,
                total_start=total_start,
                timer=timer,
                publisher=publisher,
                publish_enabled=publish_enabled,
            )
        if signals.get("screen_type") != "settings_and_activity" or not logout_target.get("visible"):
            metadata["logout_not_visible_reason"] = "logout_not_visible_after_scrolls"
            return _logout_fallback_finalize(
                ok=False,
                final_outcome="unknown",
                reason="logout_not_visible_after_scrolls",
                failure_reason="logout_not_visible_after_scrolls",
                account_id=safe_account_id,
                expected_username=safe_expected_username,
                actions_taken=actions_taken,
                timings=timings,
                warnings=warnings,
                metadata=metadata,
                total_start=total_start,
                timer=timer,
                publisher=publisher,
                publish_enabled=publish_enabled,
            )

    action_result = _execute_logout_step(
        d,
        SimpleNamespace(decision="tap_logout"),
        timings=timings,
        actions_taken=actions_taken,
    )
    if not action_result.ok:
        return _logout_fallback_finalize(
            ok=False,
            final_outcome="action_failed",
            reason=action_result.failure_reason or action_result.reason,
            failure_reason=action_result.failure_reason or action_result.reason,
            account_id=safe_account_id,
            expected_username=safe_expected_username,
            actions_taken=actions_taken,
            timings=timings,
            warnings=[*warnings, *action_result.warnings],
            metadata=metadata,
            total_start=total_start,
            timer=timer,
            publisher=publisher,
            publish_enabled=publish_enabled,
        )
    metadata["logout_attempted"] = True
    metadata["logout_button_tapped"] = True
    start = timer()
    signals = _observe_login_signals(d, expected_username=safe_expected_username)
    timings["observe_ms"] += _elapsed_ms(start, timer())
    _append_post_logout_observation(metadata, signals)

    if signals.get("screen_type") == "save_login_info_prompt":
        metadata["save_login_info_prompt_detected"] = True
        action_result = _execute_logout_step(
            d,
            SimpleNamespace(decision="tap_not_now"),
            timings=timings,
            actions_taken=actions_taken,
        )
        if not action_result.ok:
            return _logout_fallback_finalize(
                ok=False,
                final_outcome="action_failed",
                reason=action_result.failure_reason or action_result.reason,
                failure_reason=action_result.failure_reason or action_result.reason,
                account_id=safe_account_id,
                expected_username=safe_expected_username,
                actions_taken=actions_taken,
                timings=timings,
                warnings=[*warnings, *action_result.warnings],
                metadata=metadata,
                total_start=total_start,
                timer=timer,
                publisher=publisher,
                publish_enabled=publish_enabled,
            )
        metadata["save_login_prompt_handled"] = True
        metadata["save_login_info_not_now_tapped"] = True
        start = timer()
        signals = _observe_login_signals(d, expected_username=safe_expected_username)
        timings["observe_ms"] += _elapsed_ms(start, timer())
        _append_post_logout_observation(metadata, signals)

    if signals.get("screen_type") == "logout_confirmation_prompt":
        metadata["logout_confirmation_detected"] = True
        action_result = _execute_logout_step(
            d,
            SimpleNamespace(decision="tap_confirm_logout"),
            timings=timings,
            actions_taken=actions_taken,
        )
        if not action_result.ok:
            return _logout_fallback_finalize(
                ok=False,
                final_outcome="action_failed",
                reason=action_result.failure_reason or action_result.reason,
                failure_reason=action_result.failure_reason or action_result.reason,
                account_id=safe_account_id,
                expected_username=safe_expected_username,
                actions_taken=actions_taken,
                timings=timings,
                warnings=[*warnings, *action_result.warnings],
                metadata=metadata,
                total_start=total_start,
                timer=timer,
                publisher=publisher,
                publish_enabled=publish_enabled,
            )
        metadata["logout_confirmation_handled"] = True
        metadata["logout_confirmation_tapped"] = True
        start = timer()
        signals = _observe_login_signals(d, expected_username=safe_expected_username)
        timings["observe_ms"] += _elapsed_ms(start, timer())
        _append_post_logout_observation(metadata, signals)

    post_logout_settled = _observe_post_logout_settled(
        d,
        expected_username=safe_expected_username,
        timings=timings,
        timer=timer,
        sleeper=sleeper,
    )
    signals = dict(post_logout_settled.get("signals") or {})
    timings["post_logout_wait_total_ms"] = int(post_logout_settled.get("wait_total_ms") or 0)
    metadata["post_logout_wait_total_ms"] = int(post_logout_settled.get("wait_total_ms") or 0)
    for screen in list(post_logout_settled.get("screens") or []):
        _append_post_logout_observation(metadata, {"screen_type": screen})
    final_screen_type = _post_logout_screen_type(signals)
    metadata["post_logout_final_signals"] = dict(signals)
    metadata["final_screen_type"] = final_screen_type
    metadata["screen_after_logout_final"] = final_screen_type
    suggested_username = _safe_public_text(signals.get("suggested_username"))
    if suggested_username:
        metadata["post_logout_final_suggested_username"] = suggested_username
    metadata["post_logout_known_screen"] = final_screen_type in POST_LOGOUT_KNOWN_SCREENS
    if final_screen_type not in POST_LOGOUT_KNOWN_SCREENS:
        return _logout_fallback_finalize(
            ok=False,
            final_outcome="unknown",
            reason="post_logout_unknown_screen",
            failure_reason="post_logout_unknown_screen",
            account_id=safe_account_id,
            expected_username=safe_expected_username,
            actions_taken=actions_taken,
            timings=timings,
            warnings=warnings,
            metadata=metadata,
            total_start=total_start,
            timer=timer,
            publisher=publisher,
            publish_enabled=publish_enabled,
        )

    return _logout_fallback_finalize(
        ok=True,
        final_outcome=final_screen_type,
        reason="post_logout_known_screen",
        failure_reason=None,
        account_id=safe_account_id,
        expected_username=safe_expected_username,
        actions_taken=actions_taken,
        timings=timings,
        warnings=warnings,
        metadata=metadata,
        total_start=total_start,
        timer=timer,
        publisher=publisher,
        publish_enabled=publish_enabled,
    )


def _execute_logout_step(
    d: Any,
    decision: Any,
    *,
    timings: dict[str, int],
    actions_taken: list[str],
) -> Any:
    start = time.perf_counter()
    action_result = execute_login_screen_decision(d, decision, post_action_wait_ms=500)
    timings["action_ms"] += _elapsed_ms(start, time.perf_counter())
    actions_taken.append(action_result.action)
    return action_result


def _stabilize_profile_menu(
    d: Any,
    *,
    signals: dict[str, Any],
    actual_username: str,
    expected_username: str,
    timings: dict[str, int],
    actions_taken: list[str],
    sleeper: Callable[[float], None],
) -> tuple[dict[str, Any], bool, dict[str, Any]]:
    metadata: dict[str, Any] = {
        "profile_menu_initially_missing": not bool(signals.get("profile_menu_ready")),
        "profile_menu_wait_reobserve": False,
        "profile_menu_home_profile_refresh_attempted": False,
        "profile_menu_final_found": bool(signals.get("profile_menu_ready")),
        "profile_menu_failure_reason": "",
    }
    if signals.get("profile_menu_ready"):
        return signals, True, metadata

    metadata["profile_menu_wait_reobserve"] = True
    sleeper(PROFILE_MENU_REOBSERVE_WAIT_MS / 1000.0)
    start = time.perf_counter()
    signals = _observe_login_signals(d, expected_username=expected_username)
    timings["observe_ms"] += _elapsed_ms(start, time.perf_counter())
    if not _same_active_profile(signals, actual_username):
        metadata["profile_menu_failure_reason"] = "username_changed"
        return signals, False, metadata
    if signals.get("profile_menu_ready"):
        metadata["profile_menu_final_found"] = True
        return signals, True, metadata

    metadata["profile_menu_home_profile_refresh_attempted"] = True
    home_result = _execute_logout_step(
        d,
        SimpleNamespace(decision="open_home_from_profile"),
        timings=timings,
        actions_taken=actions_taken,
    )
    if not home_result.ok:
        metadata["profile_menu_failure_reason"] = home_result.failure_reason or home_result.reason
        return signals, False, metadata
    sleeper(PROFILE_REFRESH_WAIT_MS / 1000.0)
    profile_result = _execute_logout_step(
        d,
        SimpleNamespace(decision="open_profile_from_home"),
        timings=timings,
        actions_taken=actions_taken,
    )
    if not profile_result.ok:
        metadata["profile_menu_failure_reason"] = profile_result.failure_reason or profile_result.reason
        return signals, False, metadata
    sleeper(PROFILE_REFRESH_WAIT_MS / 1000.0)
    start = time.perf_counter()
    signals = _observe_login_signals(d, expected_username=expected_username)
    timings["observe_ms"] += _elapsed_ms(start, time.perf_counter())
    if not _same_active_profile(signals, actual_username):
        metadata["profile_menu_failure_reason"] = "username_changed"
        return signals, False, metadata
    if not signals.get("profile_menu_ready"):
        metadata["profile_menu_failure_reason"] = "profile_menu_not_found"
        return signals, False, metadata
    metadata["profile_menu_final_found"] = True
    return signals, True, metadata


def _same_active_profile(signals: dict[str, Any], actual_username: str) -> bool:
    return (
        signals.get("screen_type") == "active_account_profile"
        and _safe_public_text(signals.get("actual_logged_in_username")).strip().lstrip("@").lower()
        == str(actual_username or "").strip().lstrip("@").lower()
    )


def _classify_post_logout_unknown(signals: dict[str, Any]) -> str | None:
    if signals.get("has_continue_button") and signals.get("has_use_another_profile"):
        return "continue_as_candidate"
    preparation_screen = _preparation_screen_label(signals)
    if preparation_screen in POST_LOGOUT_KNOWN_SCREENS:
        return preparation_screen
    suggested_username = str(signals.get("suggested_username") or "").strip()
    if (
        suggested_username
        and not signals.get("has_password_field")
        and not signals.get("has_login_button")
        and not signals.get("active_account_profile")
        and not signals.get("active_account_home")
    ):
        return "continue_as_candidate"
    return None


def _post_logout_screen_type(signals: dict[str, Any]) -> str:
    screen_type = str(signals.get("screen_type") or "unknown")
    if screen_type in {
        "login_form_empty",
        "login_form_prefilled_username",
        "continue_as_candidate",
        "account_picker",
        "continue_password_only",
        "save_login_info_prompt",
        "logout_confirmation_prompt",
    }:
        return screen_type
    if screen_type == "unknown":
        classified = _classify_post_logout_unknown(signals)
        if classified:
            return classified
    if str(signals.get("login_probe_outcome") or "") == LoginProbeOutcome.CONNECTED.value:
        return "connected"
    return screen_type


def _post_logout_screen_is_exploitable(signals: dict[str, Any]) -> bool:
    screen_type = str(signals.get("screen_type") or "unknown")
    if screen_type in POST_LOGOUT_KNOWN_SCREENS:
        return True
    if screen_type in {"save_login_info_prompt", "logout_confirmation_prompt"}:
        return False
    if screen_type != "unknown":
        return False
    if _classify_post_logout_unknown(signals):
        return True
    return bool(str(signals.get("suggested_username") or "").strip())


def _observe_post_logout_settled(
    d: Any,
    *,
    expected_username: str,
    timings: dict[str, int],
    timer: Timer,
    sleeper: Sleeper,
    max_observations: int = POST_LOGOUT_SETTLING_OBSERVATIONS,
    interval_ms: int = POST_LOGOUT_SETTLING_INTERVAL_MS,
) -> dict[str, Any]:
    observations = _clamp_count(max_observations, MAX_STARTUP_OBSERVATIONS)
    interval = _clamp_ms(interval_ms, MAX_STARTUP_INTERVAL_MS)
    screens: list[str] = []
    wait_total_ms = 0
    last_signals: dict[str, Any] = {}

    for index in range(observations):
        if index > 0 and interval > 0:
            sleeper(interval / 1000.0)
            wait_total_ms += interval
        start = timer()
        last_signals = _observe_login_signals(d, expected_username=expected_username)
        timings["observe_ms"] += _elapsed_ms(start, timer())
        screen_type = _post_logout_screen_type(last_signals)
        screens.append(screen_type)
        if _post_logout_screen_is_exploitable(last_signals):
            break

    final_screen_type = screens[-1] if screens else "unknown"
    timings["post_logout_settling_observation_count"] = len(screens)
    return {
        "signals": last_signals,
        "observation_count": len(screens),
        "wait_total_ms": wait_total_ms,
        "screens": screens,
        "final_screen_type": final_screen_type,
    }


def _append_post_logout_observation(metadata: dict[str, Any], signals: dict[str, Any]) -> None:
    screen = _post_logout_screen_type(signals)
    metadata["post_logout_observation_count"] = int(metadata.get("post_logout_observation_count") or 0) + 1
    metadata.setdefault("post_logout_screens", []).append(screen)
    metadata["screen_after_logout_final"] = screen
    suggested_username = _safe_public_text(signals.get("suggested_username"))
    if suggested_username:
        metadata["post_logout_final_suggested_username"] = suggested_username


def _merge_logout_resume_metadata(
    parent_metadata: dict[str, Any],
    resume_metadata: dict[str, Any],
    *,
    logout_fallback_reason: str,
) -> dict[str, Any]:
    merged = {
        **parent_metadata,
        **resume_metadata,
        "recovery_path": "logout_fallback",
        "logout_fallback_allowed": True,
        "logout_fallback_reason": logout_fallback_reason,
        "post_logout_resume_observe_only": True,
    }
    for key in PARENT_APP_START_METADATA_KEYS:
        if key in parent_metadata:
            merged[key] = parent_metadata[key]
    return merged


def _scroll_settings_to_logout_once(d: Any) -> bool:
    try:
        selector = d(scrollable=True)
    except Exception:
        selector = None
    if selector is not None:
        scroll = getattr(selector, "scroll", None)
        forward = getattr(scroll, "forward", None)
        if callable(forward):
            try:
                return bool(forward(steps=30))
            except TypeError:
                try:
                    return bool(forward())
                except Exception:
                    pass
            except Exception:
                pass
        to = getattr(scroll, "to", None)
        if callable(to):
            for label in LOGOUT_BUTTON_LABELS:
                try:
                    if bool(to(text=label)):
                        return True
                except Exception:
                    continue
        fling = getattr(selector, "fling", None)
        forward_fling = getattr(fling, "forward", None)
        if callable(forward_fling):
            try:
                return bool(forward_fling())
            except Exception:
                pass
        to_end = getattr(fling, "toEnd", None)
        if callable(to_end):
            try:
                return bool(to_end(max_swipes=1))
            except TypeError:
                try:
                    return bool(to_end())
                except Exception:
                    pass
            except Exception:
                pass
    swipe_ext = getattr(d, "swipe_ext", None)
    if callable(swipe_ext):
        try:
            swipe_ext("up", scale=0.75)
            return True
        except Exception:
            pass
    return False


def _settle_settings_logout_button(
    d: Any,
    *,
    expected_username: str,
    signals: dict[str, Any],
    metadata: dict[str, Any],
    timings: dict[str, int],
    timer: Timer,
    sleeper: Sleeper,
) -> tuple[dict[str, Any], dict[str, Any]]:
    latest_signals = dict(signals)
    latest_target = _logout_button_target_from_signals(latest_signals)
    metadata["logout_scroll_attempted"] = True
    metadata["logout_settings_scroll_attempted"] = True
    for _ in range(MAX_LOGOUT_SETTINGS_SCROLLS):
        metadata["logout_scroll_attempt_count"] = int(metadata.get("logout_scroll_attempt_count") or 0) + 1
        metadata["logout_settings_scroll_count"] = int(metadata.get("logout_settings_scroll_count") or 0) + 1
        scrolled = _scroll_settings_to_logout_once(d)
        if LOGOUT_SETTINGS_SCROLL_WAIT_MS > 0:
            sleeper(LOGOUT_SETTINGS_SCROLL_WAIT_MS / 1000.0)
        start = timer()
        latest_signals = _observe_login_signals(d, expected_username=expected_username)
        timings["observe_ms"] += _elapsed_ms(start, timer())
        latest_target = _logout_button_target_from_signals(latest_signals)
        metadata["logout_button_visible_after_scroll"] = bool(latest_target.get("visible"))
        if latest_target.get("visible") or latest_target.get("ambiguous"):
            break
        if latest_signals.get("screen_type") != "settings_and_activity":
            metadata["logout_not_visible_reason"] = "settings_screen_lost_after_scroll"
            break
        if not scrolled:
            metadata["logout_not_visible_reason"] = "settings_scroll_unavailable"
    if latest_target.get("visible"):
        metadata["logout_not_visible_reason"] = ""
    elif not metadata.get("logout_not_visible_reason"):
        metadata["logout_not_visible_reason"] = "logout_not_visible_after_scrolls"
    return latest_signals, latest_target


def _logout_button_target_from_signals(signals: dict[str, Any]) -> dict[str, Any]:
    count = int(signals.get("logout_button_candidate_count") or 0)
    visible = bool(signals.get("has_log_out_button")) and count > 0
    return {
        "visible": visible,
        "ambiguous": count > 1,
        "text": _safe_public_text(signals.get("logout_button_target_text")),
        "method": _safe_public_text(signals.get("logout_button_target_method")),
        "candidate_count": count,
    }


def _update_logout_target_metadata(metadata: dict[str, Any], target: dict[str, Any]) -> None:
    if target.get("visible"):
        metadata["logout_button_visible_after_scroll"] = True
    text = _safe_public_text(target.get("text"))
    method = _safe_public_text(target.get("method"))
    if text:
        metadata["logout_button_target_text"] = text
    if method:
        metadata["logout_button_target_method"] = method


def _logout_fallback_finalize(
    *,
    ok: bool,
    final_outcome: str,
    reason: str,
    failure_reason: str | None,
    account_id: str,
    expected_username: str,
    actions_taken: list[str],
    timings: dict[str, int],
    warnings: list[str],
    metadata: dict[str, Any],
    total_start: float,
    timer: Timer,
    publisher: Publisher | None,
    publish_enabled: bool,
    dashboard_action_type: str | None = None,
) -> LoginProvisioningFlowResult:
    return _finalize(
        ok=ok,
        completed=ok,
        final_outcome=final_outcome,
        reason=reason,
        failure_reason=failure_reason,
        account_id=account_id,
        expected_username=expected_username,
        actions_taken=actions_taken,
        timings=timings,
        warnings=warnings,
        extra_metadata={
            **metadata,
            "password_required": False,
            "ready_for_password_submit": False,
            "ready_for_credentials_flow": False,
            "would_submit_password": False,
            "would_publish": False,
        },
        dashboard_action_type=dashboard_action_type,
        should_publish_status=False,
        total_start=total_start,
        timer=timer,
        publisher=publisher,
        publish_enabled=publish_enabled,
    )


def _observe_login_signals(d: Any, *, expected_username: str | None = None) -> dict[str, Any]:
    try:
        hierarchy_xml = d.dump_hierarchy(compressed=False)
    except TypeError:
        hierarchy_xml = d.dump_hierarchy()
    hierarchy_text = str(hierarchy_xml or "")
    signals = extract_login_screen_signals_from_hierarchy(hierarchy_text, expected_username=expected_username)
    signals.update(_extract_logout_button_signal_metadata(hierarchy_text))
    try:
        signals["login_probe_outcome"] = str(detect_login_probe_outcome_from_hierarchy(hierarchy_text).value)
    except Exception:
        signals["login_probe_outcome"] = "unknown"
    return signals


def _extract_logout_button_signal_metadata(hierarchy_text: str) -> dict[str, Any]:
    candidates = _collect_logout_button_candidates(hierarchy_text)
    if not candidates:
        return {
            "logout_button_candidate_count": 0,
            "logout_button_target_text": "",
            "logout_button_target_method": "",
        }
    if len(candidates) > 1:
        return {
            "logout_button_candidate_count": len(candidates),
            "logout_button_target_text": "",
            "logout_button_target_method": "ambiguous_hierarchy",
        }
    candidate = candidates[0]
    return {
        "logout_button_candidate_count": 1,
        "logout_button_target_text": candidate["label"],
        "logout_button_target_method": candidate["method"],
    }


def _collect_logout_button_candidates(hierarchy_text: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for raw_attrs in re.findall(r"<node\b([^>]*)/?>", str(hierarchy_text or "")):
        attrs = dict(re.findall(r'([\w-]+)="([^"]*)"', raw_attrs))
        if str(attrs.get("visible-to-user", attrs.get("visible", "true"))).lower() == "false":
            continue
        if str(attrs.get("enabled", "true")).lower() == "false":
            continue
        label = _safe_public_text(attrs.get("text") or attrs.get("content-desc") or attrs.get("contentDescription"))
        if not _is_logout_button_label(label):
            continue
        bounds = str(attrs.get("bounds") or "")
        if not bounds:
            continue
        method = "hierarchy_clickable_text" if str(attrs.get("clickable", "false")).lower() == "true" else "hierarchy_text"
        key = (label.lower(), bounds)
        if key in seen:
            continue
        seen.add(key)
        candidates.append({"label": label, "method": method, "bounds": bounds})
    return candidates


def _is_logout_button_label(label: str) -> bool:
    normalized = re.sub(r"\s+", " ", str(label or "").strip()).lower()
    if not normalized:
        return False
    if normalized in LOGOUT_BUTTON_LABELS:
        return True
    if normalized.startswith("log out of ") and "your account" not in normalized and "?" not in normalized:
        return True
    if normalized.startswith("se déconnecter de ") or normalized.startswith("se deconnecter de "):
        return "?" not in normalized
    return False


def _observe_startup_screen_settled(
    d: Any,
    *,
    expected_username: str,
    timings: dict[str, int],
    timer: Timer,
    sleeper: Sleeper,
    interval_ms: int,
    max_observations: int,
) -> dict[str, Any]:
    observations = _clamp_count(max_observations, MAX_STARTUP_OBSERVATIONS)
    interval = _clamp_ms(interval_ms, MAX_STARTUP_INTERVAL_MS)
    screens: list[str] = []
    wait_total_ms = 0
    last_signals: dict[str, Any] = {}

    for index in range(observations):
        if index > 0 and interval > 0:
            sleeper(interval / 1000.0)
            wait_total_ms += interval
        start = timer()
        last_signals = _observe_login_signals(d, expected_username=expected_username)
        timings["observe_ms"] += _elapsed_ms(start, timer())
        screen_type = _screen_after_app_start(last_signals)
        screens.append(screen_type)
        if _startup_screen_is_exploitable(last_signals):
            break

    final_screen_type = screens[-1] if screens else "unknown"
    timings["startup_observation_count"] += len(screens)
    timings["startup_wait_total_ms"] += wait_total_ms
    return {
        "signals": last_signals,
        "observation_count": len(screens),
        "wait_total_ms": wait_total_ms,
        "screens": screens,
        "initial_screen_type": screens[0] if screens else "unknown",
        "final_screen_type": final_screen_type,
    }


def _post_add_existing_settled_from_signals(signals: dict[str, Any]) -> dict[str, Any]:
    final_screen = _preparation_screen_label(signals)
    return {
        "signals": dict(signals),
        "observation_count": 1,
        "wait_total_ms": 0,
        "screens": ["transition_unknown", final_screen],
        "final_screen_type": final_screen,
    }


def _observe_post_add_existing_settled(
    d: Any,
    *,
    expected_username: str,
    timings: dict[str, int],
    timer: Timer,
    sleeper: Sleeper,
) -> dict[str, Any]:
    return _observe_preparation_screen_settled(
        d,
        expected_username=expected_username,
        timings=timings,
        timer=timer,
        sleeper=sleeper,
        initial_screen="transition_unknown",
        interval_ms=DEFAULT_STARTUP_INTERVAL_MS,
        max_observations=DEFAULT_STARTUP_OBSERVATIONS,
    )


def _merge_post_add_existing_settled_metadata(
    metadata: dict[str, Any],
    settled: dict[str, Any],
    *,
    append: bool = False,
) -> None:
    screens = [str(screen) for screen in list(settled.get("screens") or [])]
    observation_screens = screens[1:] if screens and screens[0] == "transition_unknown" else screens
    signals = dict(settled.get("signals") or {})
    final_screen = _preparation_screen_label(signals)
    if append:
        merged_screens = list(metadata.get("post_add_existing_screens") or [])
        merged_screens.extend(observation_screens)
        metadata["post_add_existing_screens"] = merged_screens
        metadata["post_add_existing_observation_count"] = int(
            metadata.get("post_add_existing_observation_count") or 0
        ) + int(settled.get("observation_count") or 0)
    else:
        metadata["post_add_existing_screens"] = observation_screens
        metadata["post_add_existing_observation_count"] = int(settled.get("observation_count") or 0)
    metadata["screen_after_add_existing_final"] = final_screen
    metadata["add_account_sheet_opened"] = bool(metadata.get("add_account_sheet_opened")) or (
        "add_account_sheet" in screens
        or str(signals.get("screen_type") or "") == "add_account_sheet"
    )


def _post_add_existing_screen_is_routable(signals: dict[str, Any]) -> bool:
    screen_type = str(signals.get("screen_type") or "unknown").strip() or "unknown"
    if screen_type in POST_ADD_EXISTING_RESUME_SCREENS:
        return True
    outcome = str(signals.get("login_probe_outcome") or "").strip()
    return outcome in {
        LoginProbeOutcome.NEEDS_2FA.value,
        LoginProbeOutcome.CHECKPOINT.value,
        LoginProbeOutcome.LOGIN_FAILED.value,
    }


def _observe_preparation_screen_settled(
    d: Any,
    *,
    expected_username: str,
    timings: dict[str, int],
    timer: Timer,
    sleeper: Sleeper,
    initial_screen: str,
    interval_ms: int,
    max_observations: int,
) -> dict[str, Any]:
    observations = _clamp_count(max_observations, MAX_STARTUP_OBSERVATIONS)
    interval = _clamp_ms(interval_ms, MAX_STARTUP_INTERVAL_MS)
    screens: list[str] = [str(initial_screen or "transition_unknown")]
    wait_total_ms = 0
    last_signals: dict[str, Any] = {}

    for _index in range(observations):
        if interval > 0:
            sleeper(interval / 1000.0)
            wait_total_ms += interval
        start = timer()
        last_signals = _observe_login_signals(d, expected_username=expected_username)
        timings["observe_ms"] += _elapsed_ms(start, timer())
        screen_type = _preparation_screen_label(last_signals)
        screens.append(screen_type)
        if _startup_screen_is_exploitable(last_signals):
            break

    final_screen_type = screens[-1] if screens else "unknown"
    return {
        "signals": last_signals,
        "observation_count": max(0, len(screens) - 1),
        "wait_total_ms": wait_total_ms,
        "screens": screens,
        "final_screen_type": final_screen_type,
    }


def _normalize_identity_username(value: Any) -> str:
    return _safe_public_text(value).strip().lstrip("@").lower()


def _effective_connected_home_screen(
    signals: dict[str, Any],
    screen_preparation_metadata: dict[str, Any],
) -> str:
    screen_type = _safe_screen_type_value(str(signals.get("screen_type") or "unknown"))
    if screen_type in CONNECTED_HOME_IDENTITY_SCREENS:
        return screen_type
    for key in ("screen_after_app_start_final", "startup_final_screen_type", "screen_after_app_start"):
        startup_final = _safe_screen_type_value(str(screen_preparation_metadata.get(key) or ""))
        if startup_final in CONNECTED_HOME_IDENTITY_SCREENS:
            return startup_final
    return ""


def _connected_home_identity_proven(signals: dict[str, Any], *, expected_username: str) -> bool:
    actual_username = _normalize_identity_username(signals.get("actual_logged_in_username"))
    expected = _normalize_identity_username(expected_username)
    return bool(actual_username) and bool(expected) and actual_username == expected


def _defer_connected_no_password_early_exit(
    signals: dict[str, Any],
    screen_preparation_metadata: dict[str, Any],
    *,
    expected_username: str,
) -> bool:
    if not _effective_connected_home_screen(signals, screen_preparation_metadata):
        return False
    return not _connected_home_identity_proven(signals, expected_username=expected_username)


def _startup_screen_is_exploitable(signals: dict[str, Any]) -> bool:
    screen_type = str(signals.get("screen_type") or "unknown")
    if screen_type in {
        "add_account_sheet",
        "continue_as_candidate",
        "account_picker",
        "login_form_empty",
        "login_form_prefilled_username",
        "continue_password_only",
        "active_account_home",
        "active_account_profile",
        "connected_home",
        "connected_profile",
        "password_required_dialog",
        "google_password_manager_save_prompt",
        "save_login_info_prompt",
    }:
        return True
    outcome = str(signals.get("login_probe_outcome") or "unknown")
    return outcome in {
        LoginProbeOutcome.CONNECTED.value,
        LoginProbeOutcome.NEEDS_2FA.value,
        LoginProbeOutcome.CHECKPOINT.value,
        LoginProbeOutcome.LOGIN_FAILED.value,
    }


def _safe_package_name(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return DEFAULT_INSTAGRAM_PACKAGE_NAME
    if not re.fullmatch(r"[A-Za-z0-9_.]+", text):
        return DEFAULT_INSTAGRAM_PACKAGE_NAME
    return text


def _clamp_post_start_wait_ms(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = DEFAULT_POST_APP_START_WAIT_MS
    return min(MAX_POST_APP_START_WAIT_MS, max(0, parsed))


def _clamp_ms(value: Any, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = 0
    return min(maximum, max(0, parsed))


def _clamp_count(value: Any, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = 1
    return min(maximum, max(1, parsed))


def _routing_signals(
    signals: dict[str, Any],
    screen_preparation_metadata: dict[str, Any],
    *,
    previous_account_lifecycle: dict[str, Any] | None = None,
) -> dict[str, Any]:
    routing = dict(signals)
    routing["screen_type"] = _routing_screen_type(
        signals,
        screen_preparation_metadata,
        previous_account_lifecycle=previous_account_lifecycle,
    )
    suggested_username = _safe_public_text(signals.get("suggested_username"))
    if not suggested_username and previous_account_lifecycle:
        suggested_username = _safe_public_text(previous_account_lifecycle.get("username"))
    if suggested_username:
        routing["suggested_username"] = suggested_username
    return routing


def _routing_screen_type(
    signals: dict[str, Any],
    screen_preparation_metadata: dict[str, Any],
    *,
    previous_account_lifecycle: dict[str, Any] | None = None,
) -> str:
    screen_type = str(signals.get("screen_type") or "unknown").strip() or "unknown"
    if screen_type in ROUTING_SCREEN_TYPES:
        return _safe_screen_type_value(screen_type)

    for key in ("startup_final_screen_type", "screen_after_app_start_final", "screen_after_app_start"):
        startup_final = str(screen_preparation_metadata.get(key) or "").strip()
        if startup_final in ROUTING_SCREEN_TYPES:
            return _safe_screen_type_value(startup_final)

    if (
        signals.get("has_continue_button")
        and signals.get("has_use_another_profile")
        and signals.get("suggested_username")
    ):
        return "continue_as_candidate"

    if _cas_a_reuse_route_allowed(
        expected_username=str(screen_preparation_metadata.get("expected_username") or ""),
        suggested_username=str(signals.get("suggested_username") or ""),
        previous_account_lifecycle=previous_account_lifecycle or {},
    ):
        return "continue_as_candidate"

    return _safe_screen_type_value(screen_type)


def _cas_a_reuse_route_allowed(
    *,
    expected_username: str,
    suggested_username: str,
    previous_account_lifecycle: dict[str, Any],
) -> bool:
    normalized_expected = normalize_instagram_username(expected_username)
    normalized_suggested = normalize_instagram_username(suggested_username)
    if not normalized_suggested or normalized_suggested == normalized_expected:
        return False
    lifecycle_username = normalize_instagram_username(previous_account_lifecycle.get("username"))
    if lifecycle_username and lifecycle_username != normalized_suggested:
        return False
    lifecycle_status = str(previous_account_lifecycle.get("lifecycle_status") or "unknown").strip().lower()
    if lifecycle_status not in REUSABLE_PREVIOUS_ACCOUNT_LIFECYCLES:
        return False
    return bool(previous_account_lifecycle.get("clone_reuse_allowed"))


def _route_provisioning_screen(
    *,
    expected_username: str,
    routing_signals: dict[str, Any],
    previous_account_lifecycle: dict[str, Any],
    account_id: str,
) -> Any:
    screen_type = str(routing_signals.get("screen_type") or "unknown")
    suggested_username = str(routing_signals.get("suggested_username") or "")
    clone_reuse_allowed = bool(previous_account_lifecycle.get("clone_reuse_allowed"))
    route = route_login_screen(
        expected_username=expected_username,
        suggested_username=suggested_username,
        screen_type=screen_type,
        available_usernames=list(routing_signals.get("available_usernames") or []),
        account_lifecycle_lookup=_router_lifecycle_lookup(previous_account_lifecycle),
        clone_reuse_allowed=clone_reuse_allowed,
        account_id=account_id,
    )
    if route.decision != "unknown_no_action":
        return route
    if not _cas_a_reuse_route_allowed(
        expected_username=expected_username,
        suggested_username=suggested_username,
        previous_account_lifecycle=previous_account_lifecycle,
    ):
        return route
    return route_login_screen(
        expected_username=expected_username,
        suggested_username=suggested_username,
        screen_type="continue_as_candidate",
        available_usernames=list(routing_signals.get("available_usernames") or []),
        account_lifecycle_lookup=_router_lifecycle_lookup(previous_account_lifecycle),
        clone_reuse_allowed=clone_reuse_allowed,
        account_id=account_id,
    )


def _logout_fallback_gate(
    *,
    actual_username: str,
    expected_username: str,
    previous_account_lifecycle: dict[str, Any],
    explicitly_allowed: bool,
) -> tuple[bool, str]:
    if not explicitly_allowed:
        return False, "logout_fallback_not_explicitly_allowed"
    normalized_actual = _normalize_identity_username(actual_username)
    normalized_expected = _normalize_identity_username(expected_username)
    if not normalized_actual:
        return False, "active_username_missing"
    if normalized_actual == normalized_expected:
        return False, "active_username_matches_expected"
    lifecycle_status = str(previous_account_lifecycle.get("lifecycle_status") or "unknown").strip().lower()
    if lifecycle_status not in REUSABLE_PREVIOUS_ACCOUNT_LIFECYCLES:
        return False, "active_account_lifecycle_not_reusable"
    if not bool(previous_account_lifecycle.get("clone_reuse_allowed")):
        return False, "clone_reuse_not_allowed"
    source = str(previous_account_lifecycle.get("source") or "").strip()
    if source not in LOGOUT_FALLBACK_LIFECYCLE_SOURCES:
        return False, "lifecycle_source_not_safe_for_logout"
    return True, "operator_smoke_logout_fallback_allowed"


def _preparation_screen_label(signals: dict[str, Any]) -> str:
    screen_type = str(signals.get("screen_type") or "unknown").strip() or "unknown"
    if screen_type != "unknown":
        return _safe_screen_type_value(screen_type)
    return _screen_after_app_start(signals)


def _post_action_metadata_prefix(action: str) -> str:
    if action == "tap_continue":
        return "post_continue"
    if action == "tap_expected_account":
        return "post_account_picker"
    if action == "tap_use_another_profile":
        return "post_use_another_profile"
    return ""


def _screen_after_app_start(signals: dict[str, Any]) -> str:
    screen_type = str(signals.get("screen_type") or "unknown").strip() or "unknown"
    if screen_type != "unknown":
        return _safe_screen_type_value(screen_type)
    outcome = str(signals.get("login_probe_outcome") or "unknown").strip() or "unknown"
    if outcome in {
        LoginProbeOutcome.CONNECTED.value,
        LoginProbeOutcome.NEEDS_2FA.value,
        LoginProbeOutcome.CHECKPOINT.value,
        LoginProbeOutcome.LOGIN_FAILED.value,
        LoginProbeOutcome.LOGGED_OUT.value,
    }:
        return outcome
    return "unknown"


def _signals_confirm_login_form(signals: dict[str, Any]) -> bool:
    if signals.get("screen_type") == "login_form_empty":
        return signals.get("has_username_field") is True and signals.get("has_login_button") is True
    if signals.get("screen_type") == "login_form_prefilled_username":
        return (
            signals.get("username_prefilled_present") is True
            and signals.get("username_field_editable_present") is True
            and signals.get("has_password_field") is True
            and signals.get("has_login_button") is True
        )
    if signals.get("screen_type") == "continue_password_only":
        return (
            bool(signals.get("suggested_username"))
            and signals.get("has_password_field") is True
            and signals.get("has_login_button") is True
        )
    return False


def _route_starts_login_form_flow(decision: Any) -> bool:
    return str(decision or "") in {
        "start_login_form_flow",
        "start_login_form_flow_prefilled_expected",
        "start_login_form_flow_replace_username",
    }


def _signals_show_loading_transition(signals: dict[str, Any]) -> bool:
    return str(signals.get("screen_type") or "unknown") == "unknown" and signals.get("transition_loading") is True


def _should_reobserve_post_action_transition(action: str, signals: dict[str, Any]) -> bool:
    if action == "tap_use_another_profile":
        return not _signals_confirm_login_form(signals)
    screen_type = str(signals.get("screen_type") or "unknown")
    if screen_type != "unknown":
        return False
    if action == "tap_continue":
        return _signals_show_loading_transition(signals)
    if action == "tap_expected_account":
        return True
    return False


def _pre_submit_observation_metadata(signals: dict[str, Any]) -> dict[str, Any]:
    screen_type = str(signals.get("screen_type") or "unknown")
    return {
        "screen_type": _safe_screen_type_value(screen_type),
        "prefilled_username": _safe_public_text(signals.get("prefilled_username")),
        "displayed_username": _safe_public_text(signals.get("suggested_username")) if screen_type == "continue_password_only" else "",
        "password_only_username": _safe_public_text(signals.get("suggested_username")) if screen_type == "continue_password_only" else "",
        "username_prefilled_present": bool(signals.get("username_prefilled_present")),
        "username_field_present": bool(signals.get("username_field_present") or signals.get("has_username_field")),
        "username_field_editable_present": bool(
            signals.get("username_field_editable_present") or signals.get("username_editable_present")
        ),
        "password_field_present": bool(signals.get("password_field_present") or signals.get("has_password_field")),
        "login_button_present": bool(signals.get("login_button_present") or signals.get("has_login_button")),
        "password_required": screen_type in {
            "login_form_empty",
            "login_form_prefilled_username",
            "continue_password_only",
        },
        "ready_for_password_submit": bool(signals.get("ready_for_password_submit")),
        "ready_for_credentials_flow": bool(signals.get("ready_for_credentials_flow")),
        "would_submit_password": False,
    }


def _post_action_outcome_from_signals(signals: dict[str, Any]) -> str:
    outcome = str(signals.get("login_probe_outcome") or "unknown").strip()
    if outcome in {
        LoginProbeOutcome.CONNECTED.value,
        LoginProbeOutcome.NEEDS_2FA.value,
        LoginProbeOutcome.CHECKPOINT.value,
        LoginProbeOutcome.LOGIN_FAILED.value,
    }:
        return outcome
    return ""


def _resolve_previous_account_lifecycle(
    *,
    suggested_username: Any,
    screen_type: Any,
    account_id: str,
    expected_username: str,
    previous_account_lifecycle_lookup: PreviousAccountLifecycleLookup | None,
    legacy_lifecycle_lookup: Callable[[str], dict[str, Any]] | None,
    legacy_clone_reuse_allowed: bool,
) -> dict[str, Any]:
    username = _safe_public_text(suggested_username)
    normalized_username = username.strip().lstrip("@").lower()
    metadata = {
        "username": normalized_username,
        "lifecycle_status": "unknown",
        "clone_reuse_allowed": False,
        "source": "",
        "reason": "",
        "lookup_failed": False,
    }
    allowed_screen_types = {"continue_as_candidate", "active_account_profile"}
    if normalized_username and str(screen_type or "") == "active_account_home":
        allowed_screen_types = {*allowed_screen_types, "active_account_home"}
    if not normalized_username or str(screen_type or "") not in allowed_screen_types:
        return metadata

    context = clean_login_probe_metadata(
        redact_credentials_payload(
            {
                "account_id": account_id,
                "expected_username": expected_username,
                "screen_type": _safe_public_text(screen_type),
            }
        )
    )
    try:
        if previous_account_lifecycle_lookup is not None:
            raw = previous_account_lifecycle_lookup(normalized_username, context) or {}
        elif legacy_lifecycle_lookup is not None:
            raw = legacy_lifecycle_lookup(normalized_username) or {}
        else:
            raw = {}
    except Exception:
        return {**metadata, "lookup_failed": True, "reason": "lifecycle_lookup_failed"}

    lifecycle_status = str(raw.get("lifecycle_status") or "unknown").strip().lower()
    if lifecycle_status not in {"active", "paused", "canceled", "onboarding", "archived", "stopped", "unknown"}:
        lifecycle_status = "unknown"
    clone_reuse_from_lookup = raw.get("clone_reuse_allowed")
    clone_reuse = bool(clone_reuse_from_lookup) if "clone_reuse_allowed" in raw else bool(legacy_clone_reuse_allowed)
    return {
        "username": normalized_username,
        "lifecycle_status": lifecycle_status,
        "clone_reuse_allowed": clone_reuse,
        "source": _safe_public_text(raw.get("source")),
        "reason": _safe_public_text(raw.get("reason")),
        "lookup_failed": False,
    }


def _previous_account_lifecycle_has_gate_metadata(previous_account_lifecycle: dict[str, Any]) -> bool:
    if not previous_account_lifecycle.get("username"):
        return False
    lifecycle_status = str(previous_account_lifecycle.get("lifecycle_status") or "unknown").strip().lower()
    return (
        lifecycle_status in REUSABLE_PREVIOUS_ACCOUNT_LIFECYCLES
        or bool(previous_account_lifecycle.get("clone_reuse_allowed"))
        or bool(previous_account_lifecycle.get("source"))
        or bool(previous_account_lifecycle.get("lookup_failed"))
    )


def _router_lifecycle_lookup(previous_account_lifecycle: dict[str, Any]) -> Callable[[str], dict[str, Any]] | None:
    if not previous_account_lifecycle.get("username"):
        return None
    if previous_account_lifecycle.get("lookup_failed"):
        def _failed_lookup(_username: str) -> dict[str, Any]:
            raise RuntimeError("lifecycle_lookup_failed")

        return _failed_lookup

    def _lookup(_username: str) -> dict[str, Any]:
        return {"lifecycle_status": previous_account_lifecycle.get("lifecycle_status") or "unknown"}

    return _lookup


def _flow_metadata(previous_account_lifecycle: dict[str, Any]) -> dict[str, Any]:
    if not previous_account_lifecycle.get("username"):
        return {}
    return {
        "previous_account_lifecycle": {
            "username": _safe_public_text(previous_account_lifecycle.get("username")),
            "lifecycle_status": _safe_public_text(previous_account_lifecycle.get("lifecycle_status")),
            "clone_reuse_allowed": bool(previous_account_lifecycle.get("clone_reuse_allowed")),
            "source": _safe_public_text(previous_account_lifecycle.get("source")),
            "reason": _safe_public_text(previous_account_lifecycle.get("reason")),
        }
    }


def _dry_run_result(
    *,
    route: Any,
    signals: dict[str, Any],
    account_id: str,
    expected_username: str,
    actions_taken: list[str],
    timings: dict[str, int],
    warnings: list[str],
    previous_account_lifecycle: dict[str, Any],
    total_start: float,
    timer: Timer,
) -> LoginProvisioningFlowResult:
    screen_type = str(signals.get("screen_type") or "unknown")
    decision = str(getattr(route, "decision", "") or "unknown_no_action")
    would_tap_continue = decision == "continue_expected_account"
    would_tap_expected_account = decision == "select_expected_account_from_picker"
    would_tap_use_another_profile = decision == "use_another_profile_previous_account_stopped"
    would_recover_old_logged_in_account = decision == "recover_old_logged_in_account"
    would_request_credentials = _route_starts_login_form_flow(decision)
    would_block_mismatch = decision in {"block_wrong_suggested_account", "block_wrong_active_account"}
    ready_for_password_smoke = (
        screen_type in {"login_form_empty", "login_form_prefilled_username", "continue_password_only"}
        and would_request_credentials
    )
    password_required = (
        screen_type in {"login_form_empty", "login_form_prefilled_username", "continue_password_only"}
        and would_request_credentials
    )
    ready_for_credentials_flow = screen_type in {"login_form_empty", "login_form_prefilled_username"} and would_request_credentials
    smoke_ready = (
        ready_for_password_smoke
        or would_tap_continue
        or would_tap_expected_account
        or would_tap_use_another_profile
        or would_recover_old_logged_in_account
    )
    reason = "dry_run_ready" if smoke_ready else (getattr(route, "reason", "") or "dry_run_not_ready")
    dry_metadata = {
        "dry_run": True,
        "screen_type": screen_type,
        "router_decision": decision,
        "suggested_username": _safe_public_text(signals.get("suggested_username")),
        "expected_username": expected_username,
        "would_tap_continue": would_tap_continue,
        "would_tap_expected_account": would_tap_expected_account,
        "would_tap_use_another_profile": would_tap_use_another_profile,
        "would_recover_old_logged_in_account": would_recover_old_logged_in_account,
        "actual_logged_in_username": _safe_public_text(signals.get("actual_logged_in_username")),
        "would_request_credentials": would_request_credentials,
        "would_submit_password": False,
        "would_publish": False,
        "would_block_mismatch": would_block_mismatch,
        "available_usernames": [
            _safe_public_text(username) for username in list(signals.get("available_usernames") or [])
        ],
        "expected_username_present": signals.get("expected_username_present"),
        "smoke_ready_for_real_login": smoke_ready,
        "ready_for_password_smoke": ready_for_password_smoke,
        "password_required": password_required,
        "ready_for_credentials_flow": ready_for_credentials_flow,
        "overlay_present": bool(signals.get("overlay_present")),
        "overlay_type": str(signals.get("overlay_type") or ""),
        "overlay_blocking_business": bool(signals.get("overlay_blocking_business")),
        "reason": reason,
    }
    dry_metadata.update(_flow_metadata(previous_account_lifecycle))
    timings["total_ms"] = _elapsed_ms(total_start, timer())
    safe_metadata = clean_login_probe_metadata(redact_credentials_payload(dry_metadata))
    return LoginProvisioningFlowResult(
        ok=smoke_ready,
        completed=False,
        final_outcome="dry_run",
        final_login_status=None,
        final_provisioning_status=None,
        final_onboarding_status=None,
        reason=reason,
        failure_reason=None if smoke_ready else reason,
        retry_attempted=False,
        retry_count=0,
        actions_taken=list(actions_taken),
        dashboard_action_type=(
            "review_logged_in_account_mismatch"
            if decision == "block_wrong_active_account"
            else "review_account_mismatch"
            if would_block_mismatch
            else None
        ),
        should_publish_status=False,
        publish_payload=None,
        published=False,
        publish_reason="disabled",
        timings=dict(timings),
        warnings=list(warnings),
        safe_metadata=safe_metadata,
    )


def _safe_public_text(value: Any) -> str:
    text = str(value or "").strip()
    lowered = text.lower()
    if any(
        token in lowered
        for token in (
            "password",
            "secret",
            "secret_ref",
            "vault",
            "token",
            "authorization",
            "bearer",
            "cookie",
            "session",
            "xml",
            "screenshot",
            "emulator-",
            "adb_serial",
            "device_udid",
        )
    ):
        return ""
    return text


def _safe_screen_type_value(value: Any) -> str:
    text = str(value or "").strip()
    if text in {
        "continue_as_candidate",
        "continue_password_only",
        "login_form_empty",
        "login_form_prefilled_username",
        "unknown",
    }:
        return text
    return _safe_public_text(text)


def _load_credentials(
    credentials_getter: CredentialsGetter,
    account_id: str,
    *,
    expected_username: str = "",
) -> dict[str, Any]:
    diagnostic = _empty_credentials_diagnostic(expected_username=expected_username)
    diagnostic["credentials_stage"] = "credentials_getter"
    try:
        raw = credentials_getter(account_id)
    except Exception as exc:
        error_code = _map_credentials_exception(exc)
        diagnostic.update(
            {
                "credentials_error_code": error_code,
                "credentials_invalid_reason": error_code,
                "credentials_stage": "credentials_getter",
            }
        )
        return {
            "ok": False,
            "reason": "credentials_invalid",
            **diagnostic,
        }

    diagnostic.update(_safe_credentials_diagnostic(raw, expected_username=expected_username))

    if raw is None:
        diagnostic.update(
            {
                "credentials_error_code": "credentials_not_found",
                "credentials_invalid_reason": "credentials_not_found",
                "credentials_stage": "credentials_lookup",
            }
        )
        return {"ok": False, "reason": "credentials_not_found", **diagnostic}

    username = _extract_attr(raw, "username")
    password = _extract_attr(raw, "password")
    ok_attr = _extract_attr(raw, "ok", default=None)

    if ok_attr is None and not isinstance(raw, InstagramLoginCredentialsResult):
        if not username:
            diagnostic.update(
                {
                    "credentials_error_code": "credentials_username_missing",
                    "credentials_invalid_reason": "credentials_username_missing",
                    "credentials_stage": "runtime_access",
                }
            )
            return {"ok": False, "reason": "credentials_username_missing", **diagnostic}
        if not isinstance(password, SecretValue):
            diagnostic.update(
                {
                    "credentials_error_code": "password_secret_invalid",
                    "credentials_invalid_reason": "password_secret_invalid",
                    "credentials_stage": "runtime_access",
                }
            )
            return {"ok": False, "reason": "password_secret_invalid", **diagnostic}
        diagnostic["secret_loaded"] = True
        return {"ok": True, "username": str(username), "password": password, **diagnostic}

    ok = bool(ok_attr) if ok_attr is not None else False

    if not ok:
        error_code = str(
            _extract_attr(raw, "failure_reason")
            or _extract_attr(raw, "reason")
            or "credentials_not_found"
        )
        diagnostic.update(
            {
                "credentials_error_code": error_code,
                "credentials_invalid_reason": error_code,
                "credentials_stage": "runtime_access",
            }
        )
        return {"ok": False, "reason": error_code, **diagnostic}

    if not username:
        diagnostic.update(
            {
                "credentials_error_code": "credentials_username_missing",
                "credentials_invalid_reason": "credentials_username_missing",
                "credentials_stage": "runtime_access",
            }
        )
        return {"ok": False, "reason": "credentials_username_missing", **diagnostic}

    if not isinstance(password, SecretValue):
        diagnostic.update(
            {
                "credentials_error_code": "password_secret_invalid",
                "credentials_invalid_reason": "password_secret_invalid",
                "credentials_stage": "runtime_access",
            }
        )
        return {"ok": False, "reason": "password_secret_invalid", **diagnostic}

    diagnostic["secret_loaded"] = True
    return {"ok": True, "username": str(username), "password": password, **diagnostic}


def _empty_credentials_diagnostic(*, expected_username: str = "") -> dict[str, Any]:
    return {
        "credentials_error_code": "",
        "credentials_invalid_reason": "",
        "credentials_stage": "",
        "credential_metadata_found": False,
        "credentials_status": None,
        "credentials_version": None,
        "secret_provider": "",
        "username_matches_expected": None,
        "secret_loaded": False,
        "injectable_password_only": None,
        "secret_value_safe_for_injection": None,
        "guard_would_block_revealed_value": None,
        "expected_username": str(expected_username or "").strip(),
    }


def _safe_credentials_diagnostic(raw: Any, *, expected_username: str = "") -> dict[str, Any]:
    diagnostic = _empty_credentials_diagnostic(expected_username=expected_username)
    if isinstance(raw, InstagramLoginCredentialsResult):
        safe = credential_result_safe_dict(raw)
    elif isinstance(raw, dict):
        safe = redact_credentials_payload(dict(raw))
    else:
        return diagnostic

    meta = dict(safe.get("safe_metadata") or {})
    username = str(safe.get("username") or meta.get("username") or "").strip()
    normalized_expected = str(expected_username or "").strip().lstrip("@").lower()
    normalized_username = username.strip().lstrip("@").lower()
    diagnostic.update(
        {
            "credential_metadata_found": bool(meta or safe.get("credentials_status") or safe.get("credentials_version")),
            "credentials_status": safe.get("credentials_status"),
            "credentials_version": safe.get("credentials_version"),
            "secret_provider": str(meta.get("secret_provider") or safe.get("secret_provider") or "supabase_vault"),
            "username_matches_expected": (
                normalized_username == normalized_expected if normalized_expected and normalized_username else None
            ),
            "secret_loaded": bool(safe.get("ok")),
        }
    )
    for key in (
        "injectable_password_only",
        "secret_value_safe_for_injection",
        "guard_would_block_revealed_value",
    ):
        if key in meta:
            diagnostic[key] = meta.get(key)
    return diagnostic


def _map_credentials_exception(exc: Exception) -> str:
    message = str(exc or "").strip().lower()
    if "supabase_url is not set" in message:
        return "supabase_env_missing"
    if "supabase_service_role_key is not set" in message:
        return "supabase_service_role_missing"
    if message.startswith("supabase "):
        return "supabase_request_failed"
    return "credentials_getter_exception"


def _extract_attr(value: Any, name: str, *, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _credentials_failure_result(
    credentials: dict[str, Any],
    *,
    account_id: str,
    expected_username: str,
    actions_taken: list[str],
    timings: dict[str, int],
    warnings: list[str],
    extra_metadata: dict[str, Any] | None,
    total_start: float,
    timer: Timer,
    publisher: Publisher | None,
    publish_enabled: bool,
) -> LoginProvisioningFlowResult:
    reason = str(credentials.get("reason") or "credentials_missing")
    error_code = str(credentials.get("credentials_error_code") or reason)
    invalid_reason = str(credentials.get("credentials_invalid_reason") or error_code or reason)
    credentials_stage = str(credentials.get("credentials_stage") or "")
    is_missing = reason in CREDENTIALS_MISSING_REASONS or error_code in CREDENTIALS_MISSING_ERROR_CODES
    dashboard_action_type = (
        "update_instagram_password"
        if reason in {"credentials_invalid", "password_secret_invalid", "password_secret_missing"}
        or error_code in {"password_secret_invalid", "vault_secret_password_invalid", "vault_read_failed", "secret_reader_failed"}
        else "submit_instagram_credentials"
    )
    credentials_metadata = {
        key: credentials.get(key)
        for key in (
            "credentials_error_code",
            "credentials_invalid_reason",
            "credentials_stage",
            "credential_metadata_found",
            "credentials_status",
            "credentials_version",
            "secret_provider",
            "username_matches_expected",
            "secret_loaded",
            "injectable_password_only",
            "secret_value_safe_for_injection",
            "guard_would_block_revealed_value",
        )
        if key in credentials
    }
    return _finalize(
        ok=False,
        completed=False,
        final_outcome="credentials_missing" if is_missing else "credentials_invalid",
        reason=reason,
        failure_reason=reason,
        final_login_status="logged_out",
        final_provisioning_status="login_pending",
        final_onboarding_status="credentials_required",
        dashboard_action_type=dashboard_action_type,
        should_publish_status=True,
        account_id=account_id,
        expected_username=expected_username,
        actions_taken=actions_taken,
        timings=timings,
        warnings=warnings,
        extra_metadata={
            **(extra_metadata or {}),
            **credentials_metadata,
            "credentials_error_code": error_code,
            "credentials_invalid_reason": invalid_reason,
            "credentials_stage": credentials_stage,
        },
        total_start=total_start,
        timer=timer,
        publisher=publisher,
        publish_enabled=publish_enabled,
    )


def _execute_password_form(
    d: Any,
    *,
    expected_username: str,
    password: SecretValue,
    signals: dict[str, Any],
    post_submit_timeout_ms: Optional[int],
    timer: Timer,
) -> Any:
    start = timer()
    result = execute_login_form_credentials(
        d,
        expected_username=expected_username,
        password=password,
        prevalidated_signals=signals,
        post_submit_wait_ms=0,
        post_submit_timeout_ms=post_submit_timeout_ms,
    )
    result.timings["orchestrator_password_executor_ms"] = _elapsed_ms(start, timer())
    return result


def _safe_password_result_metadata(result: Any) -> dict[str, Any]:
    safe = {
        "executed": bool(getattr(result, "executed", False)),
        "submit_tapped": bool(getattr(result, "submit_tapped", False)),
        "reason": str(getattr(result, "reason", "") or ""),
        "failure_reason": str(getattr(result, "failure_reason", "") or ""),
        "post_submit_outcome": str(getattr(result, "post_submit_outcome", "") or ""),
        "post_submit_screen_type": str(getattr(result, "post_submit_screen_type", "") or ""),
    }
    metadata = getattr(result, "safe_metadata", None)
    if isinstance(metadata, dict):
        for key in (
            "password_only_mode",
            "input_method_used",
            "password_field_target_kind",
            "password_input_method",
            "password_input_result",
            "password_confirm_method",
            "password_field_focused_before_input",
            "input_action_reported_success",
            "password_field_non_empty_confirmed",
            "username_replaced",
            "username_input_confirmed",
            "username_input_result",
            "username_input_ms",
            "username_placeholder_ignored",
            "username_field_focused_before_input",
            "username_clear_method",
            "username_input_method",
            "password_required_dialog_detected",
            "password_required_retry_attempted",
            "password_required_retry_count",
            "password_refill_attempted",
            "second_submit_executed",
            "password_submit_result",
            "post_submit_observation_count",
            "post_submit_wait_total_ms",
            "post_submit_timeout_ms",
            "post_submit_interval_ms",
            "post_submit_loading_timeout",
            "post_submit_screens",
            "final_terminal_screen",
            "save_password_prompt_detected",
            "save_password_prompt_dismissed",
            "save_password_prompt_dismiss_attempt_count",
            "dismiss_method",
            "post_dismiss_screen_type",
        ):
            if key in metadata:
                safe[key] = metadata.get(key)
    return redact_credentials_payload(safe)


def _should_retry_password_result(result: Any, retry_count: int, max_retries: int) -> bool:
    if retry_count >= max_retries:
        return False
    failure = str(getattr(result, "failure_reason", "") or "")
    probe_reason = str(getattr(result, "post_submit_probe_reason", "") or "")
    if probe_reason in {"post_submit_unknown_after_settling", "session_expired_after_settling"}:
        return False
    outcome = _password_result_outcome(result)
    if failure in NO_RETRY_FAILURES or outcome in {
        "login_failed",
        "needs_2fa",
        "checkpoint",
        "connected",
        "login_submit_still_loading",
        "save_password_prompt_blocking",
    }:
        return False
    if failure in TRANSIENT_RETRY_FAILURES:
        return True
    return outcome == "unknown" and bool(getattr(result, "executed", False))


def _password_result_outcome(result: Any) -> str:
    failure = str(getattr(result, "failure_reason", "") or "")
    if failure == "blocked_secret_payload_shape":
        return "secret_payload_not_password"
    if failure in {"username_input_failed", "username_prefilled_not_editable"}:
        return failure
    raw = str(getattr(result, "post_submit_outcome", "") or "unknown")
    if raw in {
        "password_input_missing_or_not_accepted",
        "password_input_failed",
        "username_input_failed",
        "username_prefilled_not_editable",
        "save_password_prompt_blocking",
        "login_submit_still_loading",
    }:
        return raw
    normalized = normalize_login_probe_outcome(raw)
    return str(normalized.value)


def _dashboard_action_for_outcome(outcome: str) -> str | None:
    return {
        "needs_2fa": "complete_two_factor",
        "checkpoint": "resolve_checkpoint",
        "login_failed": "update_instagram_password",
    }.get(outcome)


def _final_reason_for_password_outcome(outcome: str, password_result: Any, classification_reason: str) -> str:
    probe_reason = str(getattr(password_result, "post_submit_probe_reason", "") or "")
    if outcome == "logged_out" and probe_reason == "session_expired_after_settling":
        return "session_expired_after_settling"
    if outcome == "unknown" and probe_reason == "post_submit_unknown_after_settling":
        return "post_submit_unknown_after_settling"
    if outcome == "save_password_prompt_blocking":
        return "save_password_prompt_blocking"
    if outcome == "login_submit_still_loading":
        return "post_submit_loading_timeout"
    if outcome == "unknown":
        return "unknown_post_submit_outcome"
    return str(classification_reason or "")


def _dashboard_action_for_failure(failure_reason: str | None) -> str | None:
    if failure_reason in {"credentials_missing", "credentials_not_found"}:
        return "submit_instagram_credentials"
    if failure_reason in {
        "credentials_invalid",
        "password_secret_missing",
        "password_secret_invalid",
        "vault_secret_payload_missing_password",
        "vault_secret_password_invalid",
        "blocked_secret_payload_shape",
    }:
        return "update_instagram_password"
    if failure_reason in TRANSIENT_RETRY_FAILURES:
        return "retry_provisioning"
    return None


def _finalize(
    *,
    ok: bool,
    completed: bool,
    final_outcome: str,
    reason: str,
    account_id: str,
    expected_username: str,
    actions_taken: list[str],
    timings: dict[str, int],
    warnings: list[str],
    extra_metadata: dict[str, Any] | None = None,
    total_start: float,
    timer: Timer,
    publisher: Publisher | None,
    publish_enabled: bool,
    failure_reason: str | None = None,
    final_login_status: str | None = None,
    final_provisioning_status: str | None = None,
    final_onboarding_status: str | None = None,
    retry_attempted: bool = False,
    retry_count: int = 0,
    dashboard_action_type: str | None = None,
    should_publish_status: bool = False,
) -> LoginProvisioningFlowResult:
    timings["total_ms"] = _elapsed_ms(total_start, timer())
    publish_payload = _publish_payload(
        account_id=account_id,
        final_login_status=final_login_status,
        final_provisioning_status=final_provisioning_status,
        final_onboarding_status=final_onboarding_status,
        reason=reason,
        final_outcome=final_outcome,
        retry_count=retry_count,
        dashboard_action_type=dashboard_action_type,
    )
    published = False
    publish_reason = "disabled"
    if publish_enabled and should_publish_status and publisher is not None:
        try:
            publish_result = publisher(**publish_payload)
            published = bool((publish_result or {}).get("published", True))
            publish_reason = str((publish_result or {}).get("reason") or "published")
        except Exception:
            published = False
            publish_reason = "publisher_exception"
    elif publish_enabled and should_publish_status:
        publish_reason = "publisher_missing"
    elif not should_publish_status:
        publish_reason = "not_publishable"

    safe_metadata = clean_login_probe_metadata(
        redact_credentials_payload(
            {
                "source": "login_provisioner_orchestrator",
                "account_id": account_id,
                "expected_username": expected_username,
                "final_outcome": final_outcome,
                "reason": reason,
                "failure_reason": failure_reason,
                "retry_count": retry_count,
                "dashboard_action_type": dashboard_action_type,
                "actions_taken": actions_taken,
                **(extra_metadata or {}),
            }
        )
    )
    return LoginProvisioningFlowResult(
        ok=ok,
        completed=completed,
        final_outcome=final_outcome,
        final_login_status=final_login_status,
        final_provisioning_status=final_provisioning_status,
        final_onboarding_status=final_onboarding_status,
        reason=reason,
        failure_reason=failure_reason,
        retry_attempted=retry_attempted,
        retry_count=retry_count,
        actions_taken=list(actions_taken),
        dashboard_action_type=dashboard_action_type,
        should_publish_status=should_publish_status,
        publish_payload=publish_payload if should_publish_status else None,
        published=published,
        publish_reason=publish_reason,
        timings=dict(timings),
        warnings=list(warnings),
        safe_metadata=safe_metadata,
    )


def _publish_payload(
    *,
    account_id: str,
    final_login_status: str | None,
    final_provisioning_status: str | None,
    final_onboarding_status: str | None,
    reason: str,
    final_outcome: str,
    retry_count: int,
    dashboard_action_type: str | None,
) -> dict[str, Any]:
    return clean_login_probe_metadata(
        redact_credentials_payload(
            {
                "account_id": account_id,
                "login_status": final_login_status,
                "provisioning_status": final_provisioning_status,
                "onboarding_status": final_onboarding_status,
                "reason": reason,
                "metadata": {
                    "source": "login_provisioner_orchestrator",
                    "final_outcome": final_outcome,
                    "retry_count": retry_count,
                    "dashboard_action_type": dashboard_action_type,
                },
            }
        )
    )


def _merge_timings(base: dict[str, int], extra: dict[str, Any] | None) -> dict[str, int]:
    merged = dict(base)
    for key, value in (extra or {}).items():
        try:
            merged[str(key)] = int(value)
        except (TypeError, ValueError):
            continue
    return merged


def _empty_timings() -> dict[str, int]:
    return {
        "app_start_ms": 0,
        "post_start_wait_ms": 0,
        "startup_wait_total_ms": 0,
        "startup_observation_count": 0,
        "observe_ms": 0,
        "action_ms": 0,
        "total_ms": 0,
    }


def _elapsed_ms(start: float, end: float) -> int:
    return max(0, int(round((end - start) * 1000)))
