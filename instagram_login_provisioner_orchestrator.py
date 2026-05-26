"""Isolated login provisioning orchestrator skeleton.

Entry 2E-5J assembles the validated login/provisioning building blocks without
hooking the runner, sender, devices, or real business flows. All side effects
remain injectable and disabled by default.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Callable

from instagram_credentials_runtime_access import SecretValue, redact_credentials_payload
from instagram_login_action_executor import execute_login_screen_decision
from instagram_login_password_form_executor import execute_login_form_credentials
from instagram_login_screen_router import route_login_screen
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
NO_RETRY_FAILURES = {
    "login_form_not_validated",
    "expected_username_missing",
    "password_secret_missing",
    "password_secret_invalid",
    "credentials_missing",
    "credentials_invalid",
    "login_failed",
    "needs_2fa",
    "checkpoint",
    "mismatch",
    "wrong_account",
    "block_wrong_suggested_account",
}
MAX_RETRY_ATTEMPTS = 1
POST_CONTINUE_REOBSERVE_WAIT_MS = 1500
PROFILE_MENU_REOBSERVE_WAIT_MS = 1500
PROFILE_REFRESH_WAIT_MS = 500

CredentialsGetter = Callable[[str], Any]
PreviousAccountLifecycleLookup = Callable[[str, dict[str, Any]], dict[str, Any]]
Publisher = Callable[..., dict[str, Any]]
Timer = Callable[[], float]

REUSABLE_PREVIOUS_ACCOUNT_LIFECYCLES = {"canceled", "stopped", "archived"}
POST_LOGOUT_KNOWN_SCREENS = {
    "login_form_empty",
    "continue_as_candidate",
    "account_picker",
    "continue_password_only",
    "connected",
}


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
    timer: Timer | None = None,
) -> LoginProvisioningFlowResult:
    """Run one isolated provisioning decision flow.

    No device app lifecycle is managed here. The only UI actions are delegated to
    already validated executors, and publication remains disabled unless the
    caller explicitly injects a publisher and enables it.
    """

    timer = timer or time.perf_counter
    total_start = timer()
    timings = _empty_timings()
    warnings: list[str] = []
    actions_taken: list[str] = []
    post_continue_metadata: dict[str, Any] = {}
    safe_account_id = str(account_id or "").strip()
    safe_expected_username = str(expected_username or "").strip()
    max_retries = min(MAX_RETRY_ATTEMPTS, max(0, int(max_retry_attempts or 0)))

    signals = dict(initial_signals or {})
    if not signals:
        start = timer()
        signals = _observe_login_signals(d, expected_username=safe_expected_username)
        timings["observe_ms"] += _elapsed_ms(start, timer())

    old_logged_in_metadata: dict[str, Any] = {}
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

        if signals.get("screen_type") == "active_account_profile":
            actual_username = _safe_public_text(signals.get("actual_logged_in_username"))
            old_logged_in_metadata = {
                "actual_logged_in_username": actual_username,
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

            old_logged_in_metadata["old_logged_in_recovery_attempted"] = True
            recovery_steps = (
                SimpleNamespace(decision="open_account_switcher", target_username=actual_username),
                SimpleNamespace(decision="tap_add_instagram_account"),
                SimpleNamespace(decision="tap_log_into_existing_account"),
            )
            expected_step_screens = ("account_switcher_sheet", "add_account_sheet", "")
            for index, step_decision in enumerate(recovery_steps):
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
                start = timer()
                signals = _observe_login_signals(d, expected_username=safe_expected_username)
                timings["observe_ms"] += _elapsed_ms(start, timer())
                expected_screen = expected_step_screens[index]
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

            if signals.get("screen_type") == "unknown":
                old_logged_in_metadata.update(
                    {
                        "post_old_logged_in_recovery_initial_screen": "transition_unknown",
                        "post_old_logged_in_recovery_reobserve": True,
                        "post_old_logged_in_recovery_reobserve_count": 1,
                    }
                )
                time.sleep(POST_CONTINUE_REOBSERVE_WAIT_MS / 1000.0)
                start = timer()
                signals = _observe_login_signals(d, expected_username=safe_expected_username)
                timings["observe_ms"] += _elapsed_ms(start, timer())
                old_logged_in_metadata["post_old_logged_in_recovery_final_screen_type"] = _safe_screen_type_value(
                    signals.get("screen_type") or "unknown"
                )

    previous_account_lifecycle = _resolve_previous_account_lifecycle(
        suggested_username=signals.get("suggested_username"),
        screen_type=signals.get("screen_type"),
        account_id=safe_account_id,
        expected_username=safe_expected_username,
        previous_account_lifecycle_lookup=previous_account_lifecycle_lookup,
        legacy_lifecycle_lookup=lifecycle_lookup,
        legacy_clone_reuse_allowed=clone_reuse_allowed,
    )
    route = route_login_screen(
        expected_username=safe_expected_username,
        suggested_username=str(signals.get("suggested_username") or ""),
        screen_type=str(signals.get("screen_type") or "unknown"),
        available_usernames=list(signals.get("available_usernames") or []),
        account_lifecycle_lookup=_router_lifecycle_lookup(previous_account_lifecycle),
        clone_reuse_allowed=bool(previous_account_lifecycle.get("clone_reuse_allowed")),
        account_id=safe_account_id,
    )
    actions_taken.append(f"route:{route.decision}")

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
            extra_metadata={**_flow_metadata(previous_account_lifecycle), **old_logged_in_metadata},
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
        if not _signals_confirm_login_form(signals):
            if _should_reobserve_post_action_transition(action_result.action, signals):
                metadata_prefix = "post_continue" if action_result.action == "tap_continue" else "post_account_picker"
                initial_screen = (
                    "transition_loading"
                    if _signals_show_loading_transition(signals)
                    else "transition_unknown"
                )
                post_continue_metadata = {
                    f"{metadata_prefix}_initial_screen": initial_screen,
                    f"{metadata_prefix}_reobserve": True,
                    f"{metadata_prefix}_reobserve_count": 1,
                }
                timings["post_continue_reobserve_wait_ms"] = POST_CONTINUE_REOBSERVE_WAIT_MS
                time.sleep(POST_CONTINUE_REOBSERVE_WAIT_MS / 1000.0)
                warnings.append(f"{metadata_prefix}_reobserve_after_loading")
            start = timer()
            signals = _observe_login_signals(d, expected_username=safe_expected_username)
            timings["observe_ms"] += _elapsed_ms(start, timer())
            if post_continue_metadata:
                metadata_prefix = "post_continue" if action_result.action == "tap_continue" else "post_account_picker"
                post_continue_metadata[f"{metadata_prefix}_final_screen_type"] = _safe_screen_type_value(
                    signals.get("screen_type") or "unknown"
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
        if post_action_lifecycle.get("username"):
            previous_account_lifecycle = post_action_lifecycle
        route = route_login_screen(
            expected_username=safe_expected_username,
            suggested_username=str(signals.get("suggested_username") or ""),
            screen_type=str(signals.get("screen_type") or "unknown"),
            available_usernames=list(signals.get("available_usernames") or []),
            account_lifecycle_lookup=_router_lifecycle_lookup(post_action_lifecycle),
            clone_reuse_allowed=bool(post_action_lifecycle.get("clone_reuse_allowed")),
            account_id=safe_account_id,
        )
        actions_taken.append(f"route:{route.decision}")

    if route.decision != "start_login_form_flow" and not _signals_confirm_login_form(signals):
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

    credentials = _load_credentials(credentials_getter, safe_account_id)
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
            timer=timer,
        )
        actions_taken.append("login_form_submit_retry")

    outcome = _password_result_outcome(password_result)
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
            extra_metadata={**_flow_metadata(previous_account_lifecycle), **old_logged_in_metadata, **post_continue_metadata},
            total_start=total_start,
            timer=timer,
            publisher=publisher,
            publish_enabled=publish_enabled,
        )

    classification = classify_login_probe_outcome(outcome)
    dashboard_action_type = _dashboard_action_for_outcome(outcome)
    return _finalize(
        ok=outcome == LoginProbeOutcome.CONNECTED.value,
        completed=outcome in {
            LoginProbeOutcome.CONNECTED.value,
            LoginProbeOutcome.NEEDS_2FA.value,
            LoginProbeOutcome.CHECKPOINT.value,
            LoginProbeOutcome.LOGIN_FAILED.value,
        },
        final_outcome=outcome,
        reason=classification.reason if outcome != "unknown" else "unknown_post_submit_outcome",
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
        extra_metadata={**_flow_metadata(previous_account_lifecycle), **old_logged_in_metadata, **post_continue_metadata},
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
        "logout_fallback_attempted": False,
        "logout_attempted": False,
        "logout_scroll_attempted": False,
        "logout_scroll_attempt_count": 0,
        "settings_reobserve_after_menu": False,
        "save_login_prompt_handled": False,
        "logout_confirmation_handled": False,
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
    lifecycle_ok = (
        previous_account_lifecycle.get("lifecycle_status") in REUSABLE_PREVIOUS_ACCOUNT_LIFECYCLES
        and bool(previous_account_lifecycle.get("clone_reuse_allowed"))
    )
    metadata["lifecycle_gate_result"] = "allow_logout_fallback" if lifecycle_ok else "block_wrong_active_account"
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
    if signals.get("screen_type") not in {"profile_menu_sheet", "settings_and_activity"}:
        metadata["settings_reobserve_after_menu"] = True
        sleeper(POST_CONTINUE_REOBSERVE_WAIT_MS / 1000.0)
        start = timer()
        signals = _observe_login_signals(d, expected_username=safe_expected_username)
        timings["observe_ms"] += _elapsed_ms(start, timer())
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

    if not signals.get("has_log_out_button"):
        metadata["logout_scroll_attempted"] = True
        for _attempt in range(3):
            metadata["logout_scroll_attempt_count"] = int(metadata["logout_scroll_attempt_count"]) + 1
            if not _scroll_settings_to_logout_once(d):
                break
            start = timer()
            signals = _observe_login_signals(d, expected_username=safe_expected_username)
            timings["observe_ms"] += _elapsed_ms(start, timer())
            if signals.get("screen_type") == "settings_and_activity" and signals.get("has_log_out_button"):
                break
        if signals.get("screen_type") != "settings_and_activity" or not signals.get("has_log_out_button"):
            return _logout_fallback_finalize(
                ok=False,
                final_outcome="unknown",
                reason="logout_not_visible",
                failure_reason="logout_not_visible",
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
    start = timer()
    signals = _observe_login_signals(d, expected_username=safe_expected_username)
    timings["observe_ms"] += _elapsed_ms(start, timer())

    if signals.get("screen_type") == "save_login_info_prompt":
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
        start = timer()
        signals = _observe_login_signals(d, expected_username=safe_expected_username)
        timings["observe_ms"] += _elapsed_ms(start, timer())

    if signals.get("screen_type") == "logout_confirmation_prompt":
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
        start = timer()
        signals = _observe_login_signals(d, expected_username=safe_expected_username)
        timings["observe_ms"] += _elapsed_ms(start, timer())

    if signals.get("screen_type") == "unknown":
        metadata["post_logout_reobserve"] = True
        metadata["post_logout_reobserve_count"] = 1
        sleeper(POST_CONTINUE_REOBSERVE_WAIT_MS / 1000.0)
        start = timer()
        signals = _observe_login_signals(d, expected_username=safe_expected_username)
        timings["observe_ms"] += _elapsed_ms(start, timer())

    final_screen_type = _post_logout_screen_type(signals)
    metadata["final_screen_type"] = final_screen_type
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


def _post_logout_screen_type(signals: dict[str, Any]) -> str:
    screen_type = str(signals.get("screen_type") or "unknown")
    if screen_type in {"login_form_empty", "continue_as_candidate", "account_picker", "continue_password_only"}:
        return screen_type
    if str(signals.get("login_probe_outcome") or "") == LoginProbeOutcome.CONNECTED.value:
        return "connected"
    return screen_type


def _scroll_settings_to_logout_once(d: Any) -> bool:
    try:
        selector = d(scrollable=True)
        scroll = getattr(selector, "scroll", None)
        to = getattr(scroll, "to", None)
        if callable(to):
            if bool(to(text="Log out")):
                return True
        fling = getattr(selector, "fling", None)
        to_end = getattr(fling, "toEnd", None)
        if callable(to_end):
            return bool(to_end(max_swipes=5))
    except Exception:
        return False
    return False


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
    try:
        signals["login_probe_outcome"] = str(detect_login_probe_outcome_from_hierarchy(hierarchy_text).value)
    except Exception:
        signals["login_probe_outcome"] = "unknown"
    return signals


def _signals_confirm_login_form(signals: dict[str, Any]) -> bool:
    if signals.get("screen_type") == "login_form_empty":
        return signals.get("has_username_field") is True and signals.get("has_login_button") is True
    if signals.get("screen_type") == "continue_password_only":
        return (
            bool(signals.get("suggested_username"))
            and signals.get("has_password_field") is True
            and signals.get("has_login_button") is True
        )
    return False


def _signals_show_loading_transition(signals: dict[str, Any]) -> bool:
    return str(signals.get("screen_type") or "unknown") == "unknown" and signals.get("transition_loading") is True


def _should_reobserve_post_action_transition(action: str, signals: dict[str, Any]) -> bool:
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
        "password_required": screen_type in {"login_form_empty", "continue_password_only"},
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
    if not normalized_username or str(screen_type or "") not in {"continue_as_candidate", "active_account_profile"}:
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
    would_request_credentials = decision == "start_login_form_flow"
    would_block_mismatch = decision in {"block_wrong_suggested_account", "block_wrong_active_account"}
    ready_for_password_smoke = screen_type in {"login_form_empty", "continue_password_only"} and would_request_credentials
    password_required = screen_type in {"login_form_empty", "continue_password_only"} and would_request_credentials
    ready_for_credentials_flow = screen_type == "login_form_empty" and would_request_credentials
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
        "unknown",
    }:
        return text
    return _safe_public_text(text)


def _load_credentials(credentials_getter: CredentialsGetter, account_id: str) -> dict[str, Any]:
    try:
        raw = credentials_getter(account_id)
    except Exception:
        return {"ok": False, "reason": "credentials_invalid"}
    username = _extract_attr(raw, "username")
    password = _extract_attr(raw, "password")
    ok = bool(_extract_attr(raw, "ok", default=True))
    if not raw or not ok:
        return {"ok": False, "reason": "credentials_missing"}
    if not username or not isinstance(password, SecretValue):
        return {"ok": False, "reason": "credentials_invalid"}
    return {"ok": True, "username": str(username), "password": password}


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
    dashboard_action_type = (
        "update_instagram_password"
        if reason in {"credentials_invalid", "password_secret_invalid", "password_secret_missing"}
        else "submit_instagram_credentials"
    )
    return _finalize(
        ok=False,
        completed=False,
        final_outcome="credentials_missing" if reason == "credentials_missing" else "credentials_invalid",
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
        extra_metadata=extra_metadata,
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
    timer: Timer,
) -> Any:
    start = timer()
    result = execute_login_form_credentials(
        d,
        expected_username=expected_username,
        password=password,
        prevalidated_signals=signals,
        post_submit_wait_ms=0,
    )
    result.timings["orchestrator_password_executor_ms"] = _elapsed_ms(start, timer())
    return result


def _should_retry_password_result(result: Any, retry_count: int, max_retries: int) -> bool:
    if retry_count >= max_retries:
        return False
    failure = str(getattr(result, "failure_reason", "") or "")
    outcome = _password_result_outcome(result)
    if failure in NO_RETRY_FAILURES or outcome in {"login_failed", "needs_2fa", "checkpoint", "connected"}:
        return False
    if failure in TRANSIENT_RETRY_FAILURES:
        return True
    return outcome == "unknown" and bool(getattr(result, "executed", False))


def _password_result_outcome(result: Any) -> str:
    raw = str(getattr(result, "post_submit_outcome", "") or "unknown")
    normalized = normalize_login_probe_outcome(raw)
    return str(normalized.value)


def _dashboard_action_for_outcome(outcome: str) -> str | None:
    return {
        "needs_2fa": "complete_two_factor",
        "checkpoint": "resolve_checkpoint",
        "login_failed": "update_instagram_password",
    }.get(outcome)


def _dashboard_action_for_failure(failure_reason: str | None) -> str | None:
    if failure_reason in {"credentials_missing", "credentials_not_found"}:
        return "submit_instagram_credentials"
    if failure_reason in {"credentials_invalid", "password_secret_missing", "password_secret_invalid"}:
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
    return {"observe_ms": 0, "action_ms": 0, "total_ms": 0}


def _elapsed_ms(start: float, end: float) -> int:
    return max(0, int(round((end - start) * 1000)))
