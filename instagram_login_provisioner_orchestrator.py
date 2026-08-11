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
from login_challenge_provenance import (
    ChallengeProvenanceLoader,
    default_challenge_provenance_loader,
    evaluate_pre_input_email_challenge,
    evaluate_pre_input_verification_challenge,
    PROVENANCE_KIND_ACTIVE_RUN,
)
from login_orphan_recovery_state import ORPHAN_RECOVERY_EVENT_DETECTED, record_orphan_recovery_event
from instagram_login_action_executor import execute_login_screen_decision
from instagram_login_email_code_executor import execute_email_code_challenge_resume
from instagram_login_password_form_executor import (
    advance_login_username_step,
    execute_login_form_credentials,
)
from instagram_login_screen_router import (
    JOIN_INSTAGRAM_PROVISIONING_NEXT_ACTION,
    normalize_instagram_username,
    route_login_screen,
)
from instagram_login_status_classifier import (
    LoginProbeOutcome,
    classify_login_probe_outcome,
    clean_login_probe_metadata,
    normalize_login_probe_outcome,
)
from instagram_login_ui_probe import detect_login_probe_outcome_from_hierarchy, extract_login_screen_signals_from_hierarchy
from login_challenge_runtime import (
    consume_verification_code_for_worker,
    dispatch_login_package_mismatch_notifications,
    publish_login_challenge_pending_incident,
    publish_login_package_mismatch_incident,
    sync_login_challenge_dashboard_action,
    sync_login_package_mismatch_dashboard_action,
    sync_verification_action_after_email_code_resume,
)


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
    "save_login_info_prompt_blocking",
    "username_prefilled_not_editable",
    "username_input_failed",
}
MAX_RETRY_ATTEMPTS = 1
CENTRAL_ORCHESTRATOR_VERSION = "entry2e5p19-central-v1"
POST_CONTINUE_REOBSERVE_WAIT_MS = 1500
POST_LOGIN_LOCATION_DISMISS_WAIT_MS = 300
PROFILE_MENU_REOBSERVE_WAIT_MS = 1500
PROFILE_REFRESH_WAIT_MS = 500
DEFAULT_INSTAGRAM_PACKAGE_NAME = "com.instagram.android"
TRANSIENT_FOREGROUND_PACKAGES = frozenset(
    {
        "com.android.credentialmanager",
        "com.google.android.gms",
        "com.samsung.android.samsungpassautofill",
    }
)
TRANSIENT_FOREGROUND_RECOVERY_ATTEMPTS = 2
TRANSIENT_FOREGROUND_RECOVERY_WAIT_MS = 500
DEFAULT_POST_APP_START_WAIT_MS = 1500
MAX_POST_APP_START_WAIT_MS = 3000
DEFAULT_STARTUP_OBSERVATIONS = 4
DEFAULT_STARTUP_INTERVAL_MS = 1000
MAX_STARTUP_OBSERVATIONS = 6
MAX_STARTUP_INTERVAL_MS = 1500
MAX_APP_START_RETRY_COUNT = 1
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
ConnectedIdentityVerifier = Callable[..., Any]

REUSABLE_PREVIOUS_ACCOUNT_LIFECYCLES = {"canceled", "stopped", "archived"}
STALE_SESSION_LIFECYCLE_SOURCE = "stale_replacement_safety_check"
LOGOUT_FALLBACK_LIFECYCLE_SOURCES = {"operator_smoke_override", "lifecycle_lookup_safe", STALE_SESSION_LIFECYCLE_SOURCE}
STALE_ADD_EXISTING_FALLBACK_RECOVERABLE_REASONS = {
    "account_switcher_sheet_not_validated",
    "add_account_sheet_not_validated",
    "post_add_existing_unknown",
    "open_account_switcher_failed",
    "tap_add_instagram_account_failed",
    "tap_log_into_existing_account_failed",
}
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
    "login_form_username_step",
    "continue_as_candidate",
    "account_picker",
    "continue_password_only",
    "connected",
}
POST_EMAIL_CODE_PASSWORD_SCREENS = frozenset(
    {
        "continue_password_only",
        "login_form_empty",
        "login_form_prefilled_username",
        "login_form_username_step",
    }
)
POST_LOGOUT_SETTLING_OBSERVATIONS = 6
POST_LOGOUT_SETTLING_INTERVAL_MS = DEFAULT_STARTUP_INTERVAL_MS
PARENT_APP_START_METADATA_KEYS = (
    "central_orchestrator_used",
    "central_orchestrator_version",
    "selected_route",
    "selected_route_reason",
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
    "app_start_retry_attempted",
    "app_start_retry_count",
    "app_start_retry_reason",
    "app_start_retry_result",
    "startup_after_retry_screens",
)
POST_ADD_EXISTING_RESUME_SCREENS = frozenset(
    {
        "login_form_empty",
        "login_form_prefilled_username",
        "login_form_username_step",
        "continue_as_candidate",
        "account_picker",
        "continue_password_only",
        "join_instagram_landing",
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
    "login_form_username_step",
    "continue_password_only",
    "join_instagram_landing",
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
    run_id: str | None = None,
    run_type: str | None = "login_provisioning",
    device_serial: str | None = None,
    device_id: str | None = None,
    expected_app_instance_id: str | None = None,
    assignment_id: str | None = None,
    credentials_version: int | None = None,
    assignment_updated_at: str | None = None,
    challenge_provenance_loader: ChallengeProvenanceLoader | None = None,
    connected_identity_verifier: ConnectedIdentityVerifier | None = None,
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
    safe_run_id = str(run_id or "").strip() or None
    safe_run_type = str(run_type or "login_provisioning").strip() or "login_provisioning"
    safe_device_id = str(device_id or "").strip() or None
    safe_expected_app_instance_id = str(expected_app_instance_id or "").strip() or None
    safe_assignment_id = str(assignment_id or "").strip() or None
    safe_assignment_updated_at = str(assignment_updated_at or "").strip() or None
    provenance_loader = challenge_provenance_loader or default_challenge_provenance_loader
    identity_verifier = connected_identity_verifier or _default_connected_identity_verifier
    safe_adb_serial_masked = _mask_adb_serial(device_serial)
    safe_operator_smoke_active_username = _normalize_identity_username(operator_smoke_active_account_username)
    max_retries = min(MAX_RETRY_ATTEMPTS, max(0, int(max_retry_attempts or 0)))
    safe_package_name = _safe_package_name(package_name)
    bounded_post_start_wait_ms = _clamp_post_start_wait_ms(post_start_wait_ms)
    app_start_attempted = bool(start_app_before_probe) and not bool(observe_current_screen_only)
    screen_preparation_metadata = {
        "central_orchestrator_used": True,
        "central_orchestrator_version": CENTRAL_ORCHESTRATOR_VERSION,
        "selected_route": "",
        "selected_route_reason": "",
        "expected_username": safe_expected_username,
        "run_id": safe_run_id,
        "run_type": safe_run_type,
        "expected_package_name": safe_package_name,
        "expected_app_instance_id": safe_expected_app_instance_id,
        "assignment_id": safe_assignment_id,
        "credentials_version": credentials_version,
        "device_id": safe_device_id,
        **({"adb_serial_masked": safe_adb_serial_masked} if safe_adb_serial_masked else {}),
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
        "app_start_retry_attempted": False,
        "app_start_retry_count": 0,
        "app_start_retry_reason": "",
        "app_start_retry_result": "",
        "startup_after_retry_screens": [],
        "would_submit_password": False,
    }

    def _connected_identity_gate(
        extra_metadata: dict[str, Any],
        *,
        failure_timings: dict[str, Any] | None = None,
        failure_warnings: list[str] | None = None,
    ) -> tuple[dict[str, Any], LoginProvisioningFlowResult | None]:
        identity_result = _verify_connected_identity_before_ready(
            d,
            verifier=identity_verifier,
            account_id=safe_account_id,
            expected_username=safe_expected_username,
            run_id=safe_run_id,
            run_type=safe_run_type,
        )
        identity_metadata = _connected_identity_safe_metadata(identity_result)
        merged_metadata = {**extra_metadata, **identity_metadata}
        if bool(identity_metadata.get("expected_identity_verified")):
            actions_taken.append("verify_connected_account_identity")
            return merged_metadata, None

        identity_failure_reason = str(
            identity_metadata.get("identity_verification_failure_reason")
            or "expected_instagram_identity_not_verified"
        )
        exact_mismatch = identity_failure_reason == "active_instagram_account_mismatch"
        failure = _finalize(
            ok=False,
            completed=True,
            final_outcome="identity_verification_failed",
            reason=identity_failure_reason,
            failure_reason=identity_failure_reason,
            final_login_status="mismatch" if exact_mismatch else "logged_out",
            final_provisioning_status="blocked" if exact_mismatch else "login_pending",
            final_onboarding_status="blocked" if exact_mismatch else "credentials_submitted",
            dashboard_action_type=(
                "review_logged_in_account_mismatch" if exact_mismatch else "retry_provisioning"
            ),
            should_publish_status=False,
            account_id=safe_account_id,
            expected_username=safe_expected_username,
            actions_taken=[*actions_taken, "verify_connected_account_identity"],
            timings=failure_timings or timings,
            warnings=[*(failure_warnings or warnings), "connected_identity_not_verified_safe"],
            extra_metadata=merged_metadata,
            total_start=total_start,
            timer=timer,
            publisher=publisher,
            publish_enabled=publish_enabled,
        )
        return merged_metadata, failure

    def _dismiss_initial_location_prompt_before_connected_exit(
        current_signals: dict[str, Any],
    ) -> tuple[dict[str, Any], LoginProvisioningFlowResult | None]:
        screen_type = str(current_signals.get("screen_type") or "")
        prompt_present = bool(
            current_signals.get("post_login_location_services_prompt")
            or current_signals.get("connected_post_login_location_services_prompt")
            or screen_type == "connected_post_login_location_services_prompt"
        )
        if not prompt_present:
            return current_signals, None

        screen_preparation_metadata.update(
            {
                "post_login_location_services_prompt_detected": True,
                "post_login_location_services_prompt_dismiss_attempt_count": 1,
                "post_login_location_services_prompt_dismiss_method": "back",
            }
        )
        press = getattr(d, "press", None)
        try:
            if not callable(press):
                raise RuntimeError("device_back_unavailable")
            press("back")
            actions_taken.append("dismiss_post_login_location_services_prompt_back")
            warnings.append("post_login_location_services_prompt_dismiss_back")
        except Exception:
            screen_preparation_metadata["post_login_location_services_prompt_dismissed"] = False
            failure = _finalize(
                ok=False,
                completed=False,
                final_outcome="blocked",
                reason="post_login_location_services_prompt_dismiss_failed",
                failure_reason="post_login_location_services_prompt_dismiss_failed",
                final_login_status="connected",
                final_provisioning_status="login_pending",
                final_onboarding_status="credentials_submitted",
                should_publish_status=False,
                account_id=safe_account_id,
                expected_username=safe_expected_username,
                actions_taken=actions_taken,
                timings=timings,
                warnings=[*warnings, "post_login_location_services_prompt_back_failed"],
                extra_metadata=screen_preparation_metadata,
                total_start=total_start,
                timer=timer,
                publisher=publisher,
                publish_enabled=publish_enabled,
            )
            return current_signals, failure

        sleeper(POST_LOGIN_LOCATION_DISMISS_WAIT_MS / 1000.0)
        refreshed_signals = _observe_login_signals(d, expected_username=safe_expected_username)
        refreshed_screen_type = _screen_after_app_start(refreshed_signals)
        prompt_still_present = bool(
            refreshed_signals.get("post_login_location_services_prompt")
            or refreshed_signals.get("connected_post_login_location_services_prompt")
            or refreshed_screen_type == "connected_post_login_location_services_prompt"
        )
        connected_after_dismiss = (
            _post_action_outcome_from_signals(refreshed_signals) == LoginProbeOutcome.CONNECTED.value
        )
        dismissed = bool(connected_after_dismiss and not prompt_still_present)
        screen_preparation_metadata.update(
            {
                "post_login_location_services_prompt_dismissed": dismissed,
                "post_login_location_services_prompt_post_screen": refreshed_screen_type,
            }
        )
        if dismissed:
            return refreshed_signals, None

        failure = _finalize(
            ok=False,
            completed=False,
            final_outcome="blocked",
            reason="post_login_location_services_prompt_not_dismissed",
            failure_reason="post_login_location_services_prompt_not_dismissed",
            final_login_status="connected",
            final_provisioning_status="login_pending",
            final_onboarding_status="credentials_submitted",
            should_publish_status=False,
            account_id=safe_account_id,
            expected_username=safe_expected_username,
            actions_taken=actions_taken,
            timings=timings,
            warnings=[*warnings, "post_login_location_services_prompt_not_dismissed"],
            extra_metadata=screen_preparation_metadata,
            total_start=total_start,
            timer=timer,
            publisher=publisher,
            publish_enabled=publish_enabled,
        )
        return refreshed_signals, failure

    if app_start_attempted:
        app_start_result = _attempt_app_start(
            d,
            package_name=safe_package_name,
            timings=timings,
            timer=timer,
        )
        screen_preparation_metadata["app_start_ok"] = app_start_result["ok"]
        if not app_start_result["ok"]:
            retry_result = _retry_app_start_once(
                d,
                package_name=safe_package_name,
                timings=timings,
                timer=timer,
                reason="app_start_failed",
            )
            screen_preparation_metadata.update(retry_result["metadata"])
            screen_preparation_metadata["app_start_ok"] = retry_result["ok"]
        if not screen_preparation_metadata["app_start_ok"]:
            return _finalize(
                ok=False,
                completed=False,
                final_outcome="unknown",
                reason="app_start_failed_after_retry",
                failure_reason="app_start_failed_after_retry",
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
        guard = _check_expected_foreground_package(d, expected_package_name=safe_package_name)
        screen_preparation_metadata.update(guard)
        if guard.get("package_guard_mismatch"):
            return _finalize_package_mismatch(
                account_id=safe_account_id,
                expected_username=safe_expected_username,
                expected_package_name=safe_package_name,
                actual_foreground_package=str(guard.get("actual_foreground_package") or ""),
                run_id=safe_run_id,
                run_type=safe_run_type,
                device_id=safe_device_id,
                expected_app_instance_id=safe_expected_app_instance_id,
                adb_serial_masked=safe_adb_serial_masked,
                actions_taken=actions_taken,
                timings=timings,
                warnings=warnings,
                extra_metadata=screen_preparation_metadata,
                total_start=total_start,
                timer=timer,
                publisher=publisher,
                publish_enabled=publish_enabled,
            )

    signals = dict(initial_signals or {})
    if not signals and app_start_attempted:
        try:
            startup_observation = _observe_startup_screen_settled(
                d,
                expected_username=safe_expected_username,
                timings=timings,
                timer=timer,
                sleeper=sleeper,
                interval_ms=DEFAULT_STARTUP_INTERVAL_MS,
                max_observations=DEFAULT_STARTUP_OBSERVATIONS,
            )
        except Exception:
            startup_observation = {
                "signals": {},
                "observation_count": 0,
                "wait_total_ms": 0,
                "screens": ["unknown"],
                "initial_screen_type": "unknown",
                "final_screen_type": "unknown",
            }
            screen_preparation_metadata["app_start_retry_reason"] = "startup_observe_failed"
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
        if _startup_retry_needed(startup_observation):
            retry_result = _retry_app_start_once(
                d,
                package_name=safe_package_name,
                timings=timings,
                timer=timer,
                reason=str(screen_preparation_metadata.get("app_start_retry_reason") or "startup_unknown_or_loading"),
            )
            screen_preparation_metadata.update(retry_result["metadata"])
            screen_preparation_metadata["app_start_ok"] = retry_result["ok"]
            if not retry_result["ok"]:
                return _finalize(
                    ok=False,
                    completed=False,
                    final_outcome="unknown",
                    reason="app_start_failed_after_retry",
                    failure_reason="app_start_failed_after_retry",
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
            try:
                retry_startup_observation = _observe_startup_screen_settled(
                    d,
                    expected_username=safe_expected_username,
                    timings=timings,
                    timer=timer,
                    sleeper=sleeper,
                    interval_ms=DEFAULT_STARTUP_INTERVAL_MS,
                    max_observations=DEFAULT_STARTUP_OBSERVATIONS,
                )
            except Exception:
                retry_startup_observation = {
                    "signals": {},
                    "observation_count": 0,
                    "wait_total_ms": 0,
                    "screens": ["unknown"],
                    "initial_screen_type": "unknown",
                    "final_screen_type": "unknown",
                }
            signals = dict(retry_startup_observation.get("signals") or {})
            screen_preparation_metadata.update(
                {
                    "screen_after_app_start": retry_startup_observation["final_screen_type"],
                    "screen_after_app_start_final": retry_startup_observation["final_screen_type"],
                    "startup_observation_count": retry_startup_observation["observation_count"],
                    "startup_wait_total_ms": retry_startup_observation["wait_total_ms"],
                    "startup_screens": retry_startup_observation["screens"],
                    "startup_final_screen_type": retry_startup_observation["final_screen_type"],
                    "startup_settling_used": bool(retry_startup_observation["observation_count"] > 1),
                    "startup_after_retry_screens": retry_startup_observation["screens"],
                    "app_start_retry_result": (
                        "routable_after_retry"
                        if not _startup_retry_needed(retry_startup_observation)
                        else "startup_unknown_after_retry"
                    ),
                }
            )
            if _startup_retry_needed(retry_startup_observation):
                return _finalize(
                    ok=False,
                    completed=False,
                    final_outcome="unknown",
                    reason="startup_unknown_after_retry",
                    failure_reason="startup_unknown_after_retry",
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
    elif not signals:
        start = timer()
        signals = _observe_login_signals(d, expected_username=safe_expected_username)
        timings["observe_ms"] += _elapsed_ms(start, timer())
    signals, initial_location_prompt_failure = _dismiss_initial_location_prompt_before_connected_exit(signals)
    if initial_location_prompt_failure is not None:
        return initial_location_prompt_failure

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
            identity_metadata, identity_failure = _connected_identity_gate(
                {
                    **screen_preparation_metadata,
                    "selected_route": "already_connected_expected",
                    "selected_route_reason": "connected_probe_identity_confirmed",
                    "password_required": False,
                    "ready_for_password_submit": False,
                }
            )
            if identity_failure is not None:
                return identity_failure
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
                extra_metadata=identity_metadata,
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
        identity_metadata, identity_failure = _connected_identity_gate(
            {
                **screen_preparation_metadata,
                "selected_route": "already_connected_expected",
                "selected_route_reason": "connected_probe_identity_confirmed",
                "password_required": False,
                "ready_for_password_submit": False,
            }
        )
        if identity_failure is not None:
            return identity_failure
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
            extra_metadata=identity_metadata,
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
                            "selected_route": "identity_unknown_on_connected_home",
                            "selected_route_reason": "active_home_profile_identity_missing",
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
                        "selected_route": "identity_unknown_on_connected_home",
                        "selected_route_reason": "active_profile_identity_missing",
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
                identity_metadata, identity_failure = _connected_identity_gate(
                    {
                        **old_logged_in_metadata,
                        "selected_route": "already_connected_expected",
                        "selected_route_reason": "active_profile_matches_expected",
                        "password_required": False,
                        "ready_for_password_submit": False,
                    }
                )
                if identity_failure is not None:
                    return identity_failure
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
                    extra_metadata=identity_metadata,
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
                "selected_route": _central_selected_route(
                    recovery_route.decision,
                    {"screen_type": "active_account_profile"},
                ),
                "selected_route_reason": recovery_route.reason
                or _central_selected_route(recovery_route.decision, {"screen_type": "active_account_profile"}),
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
                    final_onboarding_status="blocked",
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
            stale_replacement_allowed = _previous_account_allows_stale_replacement(previous_account_lifecycle)
            stale_selected_route = "add_existing_account" if stale_replacement_allowed else "logout_fallback"
            stale_replacement_metadata = _stale_session_replacement_metadata(
                previous_account_lifecycle,
                actual_username=actual_username,
                expected_username=safe_expected_username,
                allowed=stale_replacement_allowed,
                selected_route=stale_selected_route,
                controlled_logout_status="not_started",
                target_login_status="in_progress" if stale_replacement_allowed else "not_started",
                identity_verification_status="in_progress" if stale_replacement_allowed else "not_started",
            )
            old_logged_in_metadata.update(
                {
                    **stale_replacement_metadata,
                    "logout_fallback_allowed": logout_fallback_allowed and not stale_replacement_allowed,
                    "logout_fallback_reason": "" if stale_replacement_allowed else logout_fallback_reason,
                    "add_existing_attempted": False,
                    "add_existing_failed_reason": "",
                    **({"primary_replacement_status": "started"} if stale_replacement_allowed else {}),
                    "selected_route": "add_existing_account" if stale_replacement_allowed else (
                        "logout_fallback" if logout_fallback_allowed else "add_existing_account"
                    ),
                    "selected_route_reason": (
                        "stale_session_replacement_try_add_existing_first"
                        if stale_replacement_allowed
                        else (
                            "operator_smoke_logout_fallback_allowed"
                            if logout_fallback_allowed
                            else "old_active_account_reusable_add_existing_default"
                        )
                    ),
                }
            )
            logout_fallback_initial_signals = dict(signals)

            def _run_logout_fallback_after_old_account(
                parent_metadata: dict[str, Any],
                *,
                reason: str,
            ) -> LoginProvisioningFlowResult:
                logout_result = run_old_account_logout_fallback_flow(
                    d,
                    account_id=safe_account_id,
                    expected_username=safe_expected_username,
                    previous_account_lifecycle_lookup=previous_account_lifecycle_lookup,
                    publisher=publisher,
                    publish_enabled=False,
                    initial_signals=logout_fallback_initial_signals,
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
                    **parent_metadata,
                    **dict(logout_result.safe_metadata or {}),
                    "recovery_path": "logout_fallback",
                    "logout_fallback_allowed": True,
                    "logout_fallback_reason": reason,
                    "add_existing_attempted": False,
                    "fallback_replacement_status": "started",
                    "post_logout_final_suggested_username": _safe_public_text(
                        post_logout_signals.get("suggested_username")
                        or logout_result.safe_metadata.get("post_logout_final_suggested_username")
                    ),
                }
                if not logout_result.ok:
                    merged_logout_metadata["controlled_logout_status"] = "failed"
                    if stale_replacement_metadata:
                        merged_logout_metadata["replacement_safety_status"] = "allowed"
                        merged_logout_metadata["target_login_status"] = "not_started"
                        merged_logout_metadata["identity_verification_status"] = "not_started"
                        merged_logout_metadata["fallback_replacement_status"] = "failed"
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
                    run_id=safe_run_id,
                    run_type=safe_run_type,
                    device_serial=device_serial,
                    device_id=safe_device_id,
                    expected_app_instance_id=safe_expected_app_instance_id,
                    connected_identity_verifier=identity_verifier,
                    timer=timer,
                    sleeper=sleeper,
                )
                resume_metadata = dict(resume_result.safe_metadata or {})
                if stale_replacement_metadata:
                    resume_connected = resume_result.final_outcome == LoginProbeOutcome.CONNECTED.value
                    resume_metadata.update(
                        {
                            **_stale_session_replacement_metadata(
                                previous_account_lifecycle,
                                actual_username=actual_username,
                                expected_username=safe_expected_username,
                                allowed=True,
                                selected_route="logout_fallback",
                                controlled_logout_status="completed",
                                target_login_status="connected" if resume_connected else "failed",
                                identity_verification_status="verified" if resume_connected else "failed",
                            ),
                            "controlled_logout_status": "completed",
                            "target_login_status": "connected" if resume_connected else "failed",
                            "identity_verification_status": "verified" if resume_connected else "failed",
                            "replacement_safety_status": "allowed",
                            "fallback_replacement_status": "completed" if resume_connected else "failed",
                        }
                    )
                return replace(
                    resume_result,
                    actions_taken=[*actions_taken, *logout_result.actions_taken, *resume_result.actions_taken],
                    timings=_merge_timings(_merge_timings(timings, logout_result.timings), resume_result.timings),
                    safe_metadata=clean_login_probe_metadata(
                        redact_credentials_payload(
                            _merge_logout_resume_metadata(
                                merged_logout_metadata,
                                resume_metadata,
                                logout_fallback_reason=reason,
                            )
                        )
                    ),
                )

            if logout_fallback_allowed and not stale_replacement_allowed:
                return _run_logout_fallback_after_old_account(
                    {
                        **old_logged_in_metadata,
                        "add_existing_failed_reason": "operator_smoke_logout_fallback_requested",
                    },
                    reason=logout_fallback_reason,
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
                guard = _check_expected_foreground_package(d, expected_package_name=safe_package_name)
                old_logged_in_metadata.update(guard)
                if guard.get("package_guard_mismatch"):
                    return _finalize_package_mismatch(
                        account_id=safe_account_id,
                        expected_username=safe_expected_username,
                        expected_package_name=safe_package_name,
                        actual_foreground_package=str(guard.get("actual_foreground_package") or ""),
                        run_id=safe_run_id,
                        run_type=safe_run_type,
                        device_id=safe_device_id,
                        expected_app_instance_id=safe_expected_app_instance_id,
                        adb_serial_masked=safe_adb_serial_masked,
                        actions_taken=actions_taken,
                        timings=timings,
                        warnings=warnings,
                        extra_metadata={**_flow_metadata(previous_account_lifecycle), **old_logged_in_metadata},
                        total_start=total_start,
                        timer=timer,
                        publisher=publisher,
                        publish_enabled=publish_enabled,
                    )
                start = timer()
                action_result = execute_login_screen_decision(
                    d,
                    step_decision,
                    post_action_wait_ms=500,
                )
                timings["action_ms"] += _elapsed_ms(start, timer())
                actions_taken.append(action_result.action)
                if not action_result.ok:
                    raw_failure_reason = action_result.failure_reason or action_result.reason
                    failure_reason = (
                        "open_account_switcher_failed"
                        if step_decision.decision == "open_account_switcher"
                        else (
                            "tap_add_instagram_account_failed"
                            if step_decision.decision == "tap_add_instagram_account"
                            else raw_failure_reason
                        )
                    )
                    if _stale_add_existing_failure_is_recoverable(failure_reason, previous_account_lifecycle):
                        return _run_logout_fallback_after_old_account(
                            {
                                **old_logged_in_metadata,
                                "primary_replacement_status": "failed_recoverable",
                                "primary_replacement_failure_reason": _safe_public_text(failure_reason),
                                "add_existing_failed_reason": _safe_public_text(failure_reason),
                                "fallback_replacement_status": "started",
                            },
                            reason="stale_add_existing_failed_recoverable",
                        )
                    return _finalize(
                        ok=False,
                        completed=False,
                        final_outcome="action_failed",
                        reason=failure_reason,
                        failure_reason=failure_reason,
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
                        failure_reason = f"{expected_screen}_not_validated"
                        if _stale_add_existing_failure_is_recoverable(failure_reason, previous_account_lifecycle):
                            return _run_logout_fallback_after_old_account(
                                {
                                    **old_logged_in_metadata,
                                    "primary_replacement_status": "failed_recoverable",
                                    "primary_replacement_failure_reason": failure_reason,
                                    "add_existing_failed_reason": failure_reason,
                                    "fallback_replacement_status": "started",
                                },
                                reason="stale_add_existing_failed_recoverable",
                            )
                        return _finalize(
                            ok=False,
                            completed=False,
                            final_outcome="unknown",
                            reason=failure_reason,
                            failure_reason=failure_reason,
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
                guard = _check_expected_foreground_package(d, expected_package_name=safe_package_name)
                old_logged_in_metadata.update(guard)
                if guard.get("package_guard_mismatch"):
                    return _finalize_package_mismatch(
                        account_id=safe_account_id,
                        expected_username=safe_expected_username,
                        expected_package_name=safe_package_name,
                        actual_foreground_package=str(guard.get("actual_foreground_package") or ""),
                        run_id=safe_run_id,
                        run_type=safe_run_type,
                        device_id=safe_device_id,
                        expected_app_instance_id=safe_expected_app_instance_id,
                        adb_serial_masked=safe_adb_serial_masked,
                        actions_taken=actions_taken,
                        timings=timings,
                        warnings=warnings,
                        extra_metadata={**_flow_metadata(previous_account_lifecycle), **old_logged_in_metadata},
                        total_start=total_start,
                        timer=timer,
                        publisher=publisher,
                        publish_enabled=publish_enabled,
                    )
                start = timer()
                log_into_result = execute_login_screen_decision(
                    d,
                    SimpleNamespace(decision="tap_log_into_existing_account"),
                    post_action_wait_ms=500,
                )
                timings["action_ms"] += _elapsed_ms(start, timer())
                actions_taken.append(log_into_result.action)
                if not log_into_result.ok:
                    failure_reason = "tap_log_into_existing_account_failed"
                    if _stale_add_existing_failure_is_recoverable(failure_reason, previous_account_lifecycle):
                        return _run_logout_fallback_after_old_account(
                            {
                                **old_logged_in_metadata,
                                "primary_replacement_status": "failed_recoverable",
                                "primary_replacement_failure_reason": _safe_public_text(failure_reason),
                                "add_existing_failed_reason": _safe_public_text(failure_reason),
                                "fallback_replacement_status": "started",
                            },
                            reason="stale_add_existing_failed_recoverable",
                        )
                    return _finalize(
                        ok=False,
                        completed=False,
                        final_outcome="action_failed",
                        reason=failure_reason,
                        failure_reason=failure_reason,
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
                failure_reason = "post_add_existing_unknown"
                if _stale_add_existing_failure_is_recoverable(failure_reason, previous_account_lifecycle):
                    return _run_logout_fallback_after_old_account(
                        {
                            **old_logged_in_metadata,
                            "primary_replacement_status": "failed_recoverable",
                            "primary_replacement_failure_reason": failure_reason,
                            "add_existing_failed_reason": failure_reason,
                            "fallback_replacement_status": "started",
                        },
                        reason="stale_add_existing_failed_recoverable",
                    )
                return _finalize(
                    ok=False,
                    completed=False,
                    final_outcome="unknown",
                    reason=failure_reason,
                    failure_reason=failure_reason,
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
        suggested_username=routing_signals.get("suggested_username") or routing_signals.get("prefilled_username"),
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
        "selected_route": _central_selected_route(route.decision, routing_signals),
        "selected_route_reason": route.reason or _central_selected_route(route.decision, routing_signals),
        "routing_screen_type": routing_signals.get("screen_type"),
        "screen_type": routing_signals.get("screen_type"),
        "suggested_username": _safe_public_text(
            routing_signals.get("suggested_username") or routing_signals.get("prefilled_username")
        ),
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
    if routing_signals.get("screen_type") == "join_instagram_landing":
        route_metadata.update(
            {
                "join_instagram_landing_detected": True,
                "join_instagram_progress_event": "join_instagram_landing_detected",
                "has_already_have_profile_button": bool(routing_signals.get("has_already_have_profile_button")),
                "has_get_started_button": bool(routing_signals.get("has_get_started_button")),
                "required_navigation_action": JOIN_INSTAGRAM_PROVISIONING_NEXT_ACTION,
            }
        )
    if routing_signals.get("screen_type") == "continue_as_candidate":
        suggested = _safe_public_text(routing_signals.get("suggested_username"))
        route_metadata.update(
            {
                "suggested_account_screen_detected": True,
                "suggested_account_mismatch": bool(
                    suggested
                    and suggested.strip().lstrip("@").lower()
                    != safe_expected_username.strip().lstrip("@").lower()
                ),
                "safe_alternate_navigation_available": bool(
                    routing_signals.get("has_use_another_profile")
                    or routing_signals.get("has_use_another_profile_button")
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
            final_onboarding_status="blocked",
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
            final_onboarding_status="blocked",
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
        post_action_outcome = _post_action_outcome_from_signals(routing_signals)
        if post_action_outcome:
            if (
                post_action_outcome == LoginProbeOutcome.VERIFICATION_PENDING.value
                and (
                    routing_signals.get("verification_code_challenge_present") is True
                    or str(routing_signals.get("screen_type") or "") in {
                        "email_code_challenge",
                        "sms_code_challenge",
                        "whatsapp_code_challenge",
                        "authenticator_app_code_challenge",
                    }
                )
            ):
                historical_action = provenance_loader(safe_account_id)
                provenance = evaluate_pre_input_verification_challenge(
                    routing_signals=routing_signals,
                    package_guard_mismatch=bool(screen_preparation_metadata.get("package_guard_mismatch")),
                    account_id=safe_account_id,
                    run_id=safe_run_id,
                    expected_app_instance_id=safe_expected_app_instance_id,
                    assignment_id=safe_assignment_id,
                    credentials_version=credentials_version,
                    assignment_updated_at=safe_assignment_updated_at,
                    historical_action=historical_action,
                )
                old_logged_in_metadata["challenge_provenance_accepted"] = provenance.accepted
                old_logged_in_metadata["challenge_provenance_reason"] = provenance.reason
                old_logged_in_metadata["challenge_provenance_proof_kind"] = provenance.proof_kind
                if not provenance.accepted:
                    try:
                        record_orphan_recovery_event(
                            account_id=safe_account_id,
                            event_type=ORPHAN_RECOVERY_EVENT_DETECTED,
                            run_id=safe_run_id or "",
                            status="detected",
                            message=provenance.reason,
                            metadata={
                                "failure_reason": provenance.reason,
                                "screen_type": str(routing_signals.get("screen_type") or ""),
                            },
                        )
                    except Exception:
                        pass
                    return _finalize(
                        ok=False,
                        completed=True,
                        final_outcome="blocked",
                        reason="orphan_challenge_provenance_weak",
                        failure_reason=provenance.reason,
                        final_login_status="blocked",
                        final_provisioning_status="blocked",
                        final_onboarding_status="blocked",
                        should_publish_status=False,
                        account_id=safe_account_id,
                        expected_username=safe_expected_username,
                        actions_taken=actions_taken,
                        timings=timings,
                        warnings=warnings,
                        extra_metadata={
                            **_flow_metadata(previous_account_lifecycle),
                            **old_logged_in_metadata,
                            **route_metadata,
                            "selected_route": "orphan_verification_challenge_blocked",
                            "selected_route_reason": provenance.reason,
                        },
                        total_start=total_start,
                        timer=timer,
                        publisher=publisher,
                        publish_enabled=publish_enabled,
                    )
                old_logged_in_metadata["selected_route"] = "orphan_verification_challenge_resume"
                old_logged_in_metadata["selected_route_reason"] = provenance.reason
            classification = classify_login_probe_outcome(post_action_outcome)
            post_action_metadata = {
                **_flow_metadata(previous_account_lifecycle),
                **old_logged_in_metadata,
                **route_metadata,
                "post_action_status_candidate": post_action_outcome,
                "password_required": False,
                "ready_for_password_smoke": False,
                "would_submit_password": False,
            }
            if post_action_outcome == LoginProbeOutcome.CONNECTED.value:
                post_action_metadata, identity_failure = _connected_identity_gate(post_action_metadata)
                if identity_failure is not None:
                    return identity_failure
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
                dashboard_action_type=_dashboard_action_for_outcome(
                    post_action_outcome,
                    challenge_type=str(routing_signals.get("challenge_type") or ""),
                    post_submit_screen_type=str(routing_signals.get("screen_type") or ""),
                ),
                should_publish_status=False,
                account_id=safe_account_id,
                expected_username=safe_expected_username,
                actions_taken=actions_taken,
                timings=timings,
                warnings=warnings,
                extra_metadata=post_action_metadata,
                total_start=total_start,
                timer=timer,
                publisher=publisher,
                publish_enabled=publish_enabled,
            )
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
        "open_existing_profile_from_join_landing",
    }:
        guard = _check_expected_foreground_package(d, expected_package_name=safe_package_name)
        old_logged_in_metadata.update(guard)
        if guard.get("package_guard_mismatch"):
            return _finalize_package_mismatch(
                account_id=safe_account_id,
                expected_username=safe_expected_username,
                expected_package_name=safe_package_name,
                actual_foreground_package=str(guard.get("actual_foreground_package") or ""),
                run_id=safe_run_id,
                run_type=safe_run_type,
                device_id=safe_device_id,
                expected_app_instance_id=safe_expected_app_instance_id,
                adb_serial_masked=safe_adb_serial_masked,
                actions_taken=actions_taken,
                timings=timings,
                warnings=warnings,
                extra_metadata={**_flow_metadata(previous_account_lifecycle), **old_logged_in_metadata},
                total_start=total_start,
                timer=timer,
                publisher=publisher,
                publish_enabled=publish_enabled,
            )
        start = timer()
        action_result = execute_login_screen_decision(d, route, post_action_wait_ms=0)
        timings["action_ms"] += _elapsed_ms(start, timer())
        actions_taken.append(action_result.action)
        if action_result.action == "tap_use_another_profile" and action_result.executed:
            old_logged_in_metadata["use_another_profile_selected"] = True
        if action_result.action == "tap_already_have_profile":
            old_logged_in_metadata.update(
                {
                    "join_instagram_landing_detected": True,
                    "join_instagram_progress_event": "join_instagram_landing_detected",
                    "already_have_profile_tap_sent": bool(action_result.executed),
                    "join_instagram_existing_profile_path_used": bool(action_result.executed),
                }
            )
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
        if _signals_confirm_login_form(signals) and action_result.action in {
            "tap_use_another_profile",
            "tap_already_have_profile",
        }:
            final_screen = _preparation_screen_label(signals)
            if action_result.action == "tap_already_have_profile":
                post_continue_metadata.update(
                    {
                        "post_join_instagram_landing_observation_count": 1,
                        "post_join_instagram_landing_screens": [final_screen],
                        "post_join_instagram_landing_wait_total_ms": 0,
                        "screen_after_join_instagram_landing_final": final_screen,
                        "login_form_after_join_landing_detected": True,
                        "join_instagram_progress_event_after": "login_form_after_join_landing_detected",
                    }
                )
            else:
                post_continue_metadata.update(
                    {
                        "post_use_another_profile_observation_count": 1,
                        "post_use_another_profile_screens": [final_screen],
                        "post_use_another_profile_wait_total_ms": 0,
                        "screen_after_use_another_profile_final": final_screen,
                        "login_surface_reached": True,
                    }
                )
        if not _signals_confirm_login_form(signals):
            metadata_prefix = _post_action_metadata_prefix(action_result.action)
            should_settle = bool(metadata_prefix) and (
                action_result.action in {"tap_use_another_profile", "tap_already_have_profile"}
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
                elif action_result.action == "tap_already_have_profile":
                    post_continue_metadata = {
                        "post_join_instagram_landing_initial_screen": initial_screen,
                        "post_join_instagram_landing_reobserve": True,
                        "post_join_instagram_landing_reobserve_count": settled["observation_count"],
                        "post_join_instagram_landing_screens": settled["screens"],
                        "post_join_instagram_landing_wait_total_ms": settled["wait_total_ms"],
                        "screen_after_join_instagram_landing_final": settled["final_screen_type"],
                        "login_form_after_join_landing_detected": _signals_confirm_login_form(signals),
                        "join_instagram_progress_event_after": "login_form_after_join_landing_detected"
                        if _signals_confirm_login_form(signals)
                        else "",
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
                        else "screen_after_join_instagram_landing_final"
                        if action_result.action == "tap_already_have_profile"
                        else f"{metadata_prefix}_final_screen_type"
                    )
                    post_continue_metadata[final_key] = _preparation_screen_label(signals)
                    if action_result.action == "tap_already_have_profile":
                        post_continue_metadata.update(
                            {
                                "login_form_after_join_landing_detected": _signals_confirm_login_form(signals),
                                "join_instagram_progress_event_after": "login_form_after_join_landing_detected"
                                if _signals_confirm_login_form(signals)
                                else "",
                            }
                        )
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
            suggested_username=signals.get("suggested_username") or signals.get("prefilled_username"),
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
            "selected_route": old_logged_in_metadata.get("selected_route")
            or _central_selected_route(route.decision, post_routing_signals),
            "selected_route_reason": old_logged_in_metadata.get("selected_route_reason")
            or route.reason
            or _central_selected_route(route.decision, post_routing_signals),
            "routing_screen_type": post_routing_signals.get("screen_type"),
            "screen_type": post_routing_signals.get("screen_type"),
            "suggested_username": _safe_public_text(
                post_routing_signals.get("suggested_username") or post_routing_signals.get("prefilled_username")
            ),
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
            post_action_metadata = {
                **_flow_metadata(previous_account_lifecycle),
                **old_logged_in_metadata,
                **post_continue_metadata,
                "post_action_status_candidate": post_action_outcome,
                "password_required": False,
                "ready_for_password_smoke": False,
                "would_submit_password": False,
            }
            if post_action_outcome == LoginProbeOutcome.CONNECTED.value:
                post_action_metadata, identity_failure = _connected_identity_gate(post_action_metadata)
                if identity_failure is not None:
                    return identity_failure
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
                dashboard_action_type=_dashboard_action_for_outcome(
                    post_action_outcome,
                    challenge_type=str(signals.get("challenge_type") or ""),
                    post_submit_screen_type=str(signals.get("screen_type") or ""),
                ),
                should_publish_status=False,
                account_id=safe_account_id,
                expected_username=safe_expected_username,
                actions_taken=actions_taken,
                timings=timings,
                warnings=warnings,
                extra_metadata=post_action_metadata,
                total_start=total_start,
                timer=timer,
                publisher=publisher,
                publish_enabled=publish_enabled,
            )
        if str(signals.get("screen_type") or "") == "join_instagram_landing":
            return _finalize(
                ok=False,
                completed=True,
                final_outcome="blocked",
                reason="join_instagram_landing_unresolved",
                failure_reason="continue_to_existing_profile_login_required",
                final_login_status="logged_out",
                final_provisioning_status="blocked",
                final_onboarding_status="credentials_required",
                should_publish_status=False,
                account_id=safe_account_id,
                expected_username=safe_expected_username,
                actions_taken=actions_taken,
                timings=timings,
                warnings=[*warnings, "join_instagram_landing_blocked_before_credentials"],
                extra_metadata={
                    **_flow_metadata(previous_account_lifecycle),
                    **old_logged_in_metadata,
                    **post_continue_metadata,
                    "required_navigation_action": JOIN_INSTAGRAM_PROVISIONING_NEXT_ACTION,
                    "join_instagram_landing_detected": True,
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

    if str(signals.get("screen_type") or "") == "login_form_username_step":
        guard = _guard_foreground_package_for_login_input(
            d,
            expected_package_name=safe_package_name,
            timer=timer,
            sleeper=sleeper,
        )
        old_logged_in_metadata.update(guard)
        if guard.get("package_guard_mismatch"):
            return _finalize_package_mismatch(
                account_id=safe_account_id,
                expected_username=safe_expected_username,
                expected_package_name=safe_package_name,
                actual_foreground_package=str(guard.get("actual_foreground_package") or ""),
                run_id=safe_run_id,
                run_type=safe_run_type,
                device_id=safe_device_id,
                expected_app_instance_id=safe_expected_app_instance_id,
                adb_serial_masked=safe_adb_serial_masked,
                actions_taken=actions_taken,
                timings=timings,
                warnings=warnings,
                extra_metadata={**old_logged_in_metadata, **post_continue_metadata},
                total_start=total_start,
                timer=timer,
                publisher=publisher,
                publish_enabled=publish_enabled,
            )
        username_step = advance_login_username_step(
            d,
            expected_username=safe_expected_username,
            prevalidated_signals=signals,
            sleeper=sleeper,
        )
        actions_taken.append("login_username_step_submit")
        old_logged_in_metadata["login_username_step"] = dict(username_step.safe_metadata)
        if not username_step.ok:
            return _finalize(
                ok=False,
                completed=False,
                final_outcome="username_step_failed",
                reason=username_step.failure_reason or username_step.reason,
                failure_reason=username_step.failure_reason or username_step.reason,
                final_login_status="logged_out",
                final_provisioning_status="login_pending",
                final_onboarding_status="credentials_required",
                should_publish_status=False,
                account_id=safe_account_id,
                expected_username=safe_expected_username,
                actions_taken=actions_taken,
                timings=timings,
                warnings=warnings,
                extra_metadata={**old_logged_in_metadata, **post_continue_metadata},
                total_start=total_start,
                timer=timer,
                publisher=publisher,
                publish_enabled=publish_enabled,
            )
        signals = dict(username_step.post_action_signals or {})
        post_username_outcome = _post_action_outcome_from_signals(signals)
        if not _signals_confirm_login_form(signals):
            if post_username_outcome:
                classification = classify_login_probe_outcome(post_username_outcome)
                return _finalize(
                    ok=post_username_outcome == LoginProbeOutcome.CONNECTED.value,
                    completed=post_username_outcome in {
                        LoginProbeOutcome.CONNECTED.value,
                        LoginProbeOutcome.NEEDS_2FA.value,
                        LoginProbeOutcome.CHECKPOINT.value,
                        LoginProbeOutcome.VERIFICATION_PENDING.value,
                        LoginProbeOutcome.LOGIN_FAILED.value,
                    },
                    final_outcome=post_username_outcome,
                    reason=f"username_step_{classification.reason}",
                    failure_reason=None if post_username_outcome == LoginProbeOutcome.CONNECTED.value else post_username_outcome,
                    final_login_status=classification.login_status,
                    final_provisioning_status=classification.provisioning_status,
                    final_onboarding_status=classification.onboarding_status,
                    dashboard_action_type=_dashboard_action_for_outcome(
                        post_username_outcome,
                        challenge_type=str(signals.get("challenge_type") or ""),
                        post_submit_screen_type=str(signals.get("screen_type") or ""),
                    ),
                    should_publish_status=classification.should_publish,
                    account_id=safe_account_id,
                    expected_username=safe_expected_username,
                    actions_taken=actions_taken,
                    timings=timings,
                    warnings=warnings,
                    extra_metadata={**old_logged_in_metadata, **post_continue_metadata},
                    total_start=total_start,
                    timer=timer,
                    publisher=publisher,
                    publish_enabled=publish_enabled,
                )
            return _finalize(
                ok=False,
                completed=False,
                final_outcome="unknown",
                reason="username_step_transition_not_reached",
                failure_reason="username_step_transition_not_reached",
                account_id=safe_account_id,
                expected_username=safe_expected_username,
                actions_taken=actions_taken,
                timings=timings,
                warnings=warnings,
                extra_metadata={**old_logged_in_metadata, **post_continue_metadata},
                total_start=total_start,
                timer=timer,
                publisher=publisher,
                publish_enabled=publish_enabled,
            )

    if str(signals.get("screen_type") or "") == "login_form_empty":
        actions_taken.append("login_form_empty_detected")
        old_logged_in_metadata["login_form_empty_detected"] = True

    guard = _guard_foreground_package_for_login_input(
        d,
        expected_package_name=safe_package_name,
        timer=timer,
        sleeper=sleeper,
    )
    old_logged_in_metadata.update(guard)
    if guard.get("package_guard_mismatch"):
        return _finalize_package_mismatch(
            account_id=safe_account_id,
            expected_username=safe_expected_username,
            expected_package_name=safe_package_name,
            actual_foreground_package=str(guard.get("actual_foreground_package") or ""),
            run_id=safe_run_id,
            run_type=safe_run_type,
            device_id=safe_device_id,
            expected_app_instance_id=safe_expected_app_instance_id,
            adb_serial_masked=safe_adb_serial_masked,
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

    actions_taken.append("credential_runtime_read_started")
    credentials = _load_credentials(
        credentials_getter,
        safe_account_id,
        expected_username=safe_expected_username,
    )
    if not credentials["ok"]:
        actions_taken.append("credential_runtime_read_failed")
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

    actions_taken.append("credential_runtime_read_ok")

    retry_count = 0
    retry_attempted = False
    guard = _guard_foreground_package_for_login_input(
        d,
        expected_package_name=safe_package_name,
        timer=timer,
        sleeper=sleeper,
    )
    old_logged_in_metadata.update(guard)
    if guard.get("package_guard_mismatch"):
        return _finalize_package_mismatch(
            account_id=safe_account_id,
            expected_username=safe_expected_username,
            expected_package_name=safe_package_name,
            actual_foreground_package=str(guard.get("actual_foreground_package") or ""),
            run_id=safe_run_id,
            run_type=safe_run_type,
            device_id=safe_device_id,
            expected_app_instance_id=safe_expected_app_instance_id,
            adb_serial_masked=safe_adb_serial_masked,
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
        guard = _guard_foreground_package_for_login_input(
            d,
            expected_package_name=safe_package_name,
            timer=timer,
            sleeper=sleeper,
        )
        old_logged_in_metadata.update(guard)
        if guard.get("package_guard_mismatch"):
            return _finalize_package_mismatch(
                account_id=safe_account_id,
                expected_username=safe_expected_username,
                expected_package_name=safe_package_name,
                actual_foreground_package=str(guard.get("actual_foreground_package") or ""),
                run_id=safe_run_id,
                run_type=safe_run_type,
                device_id=safe_device_id,
                expected_app_instance_id=safe_expected_app_instance_id,
                adb_serial_masked=safe_adb_serial_masked,
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
    password_meta = password_result_metadata["password_result"]
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

    classification = classify_login_probe_outcome(
        outcome,
        metadata={
            **password_meta,
            "screen_type": password_meta.get("post_submit_screen_type"),
        },
    )
    final_metadata = {
        **_flow_metadata(previous_account_lifecycle),
        **old_logged_in_metadata,
        **post_continue_metadata,
        **_pre_submit_observation_metadata(signals),
        **password_result_metadata,
    }
    if outcome == LoginProbeOutcome.CONNECTED.value:
        final_metadata, identity_failure = _connected_identity_gate(
            final_metadata,
            failure_timings=_merge_timings(timings, password_result.timings),
            failure_warnings=[*warnings, *password_result.warnings],
        )
        if identity_failure is not None:
            return replace(
                identity_failure,
                retry_attempted=retry_attempted,
                retry_count=retry_count,
            )
    dashboard_action_type = _dashboard_action_for_outcome(
        outcome,
        challenge_type=str(password_meta.get("challenge_type") or ""),
        post_submit_screen_type=str(password_meta.get("post_submit_screen_type") or ""),
    )
    final_reason = _final_reason_for_password_outcome(outcome, password_result, classification.reason)
    return _finalize(
        ok=outcome == LoginProbeOutcome.CONNECTED.value,
        completed=outcome in {
            LoginProbeOutcome.CONNECTED.value,
            LoginProbeOutcome.NEEDS_2FA.value,
            LoginProbeOutcome.CHECKPOINT.value,
            LoginProbeOutcome.VERIFICATION_PENDING.value,
            LoginProbeOutcome.UNSUPPORTED_POST_SUBMIT_CHALLENGE.value,
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
        extra_metadata=final_metadata,
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
    suggested_username = _safe_public_text(
        signals.get("suggested_username") or signals.get("prefilled_username")
    )
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
        "login_form_username_step",
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


def _central_selected_route(decision: str, signals: dict[str, Any]) -> str:
    safe_decision = str(decision or "")
    screen_type = str(signals.get("screen_type") or "")
    if safe_decision == "continue_expected_account":
        return "continue_as_expected"
    if safe_decision == "use_another_profile_previous_account_stopped":
        return "use_another_profile"
    if safe_decision == "select_expected_account_from_picker":
        return "account_picker"
    if safe_decision == "open_existing_profile_from_join_landing":
        return "join_instagram_existing_profile"
    if safe_decision == "start_login_form_flow_prefilled_expected":
        return "login_form_prefilled_expected"
    if safe_decision == "start_login_form_flow_replace_username":
        return "replace_prefilled_username"
    if safe_decision == "start_login_username_step_flow":
        return "login_username_step"
    if safe_decision == "start_login_form_flow":
        if screen_type == "continue_password_only":
            return "continue_password_only"
        if screen_type == "login_form_empty":
            return "login_form_empty"
        return "login_form"
    if safe_decision == "connected_expected_account":
        return "already_connected_expected"
    if safe_decision == "recover_old_logged_in_account":
        return "add_existing_account"
    if safe_decision == "expected_account_not_listed":
        return "expected_account_not_listed"
    if safe_decision == "block_wrong_active_account":
        return "requires_review"
    if safe_decision == "block_wrong_suggested_account":
        if screen_type == "login_form_prefilled_username":
            return "username_prefilled_mismatch_requires_review"
        if screen_type == "continue_password_only":
            return "continue_password_only_username_mismatch"
        return "suggested_account_mismatch_requires_review"
    if safe_decision == "username_prefilled_not_editable":
        return "username_prefilled_mismatch_requires_review"
    return safe_decision or "unknown"


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
        if screen_type == "unknown" and _signals_show_loading_transition(last_signals):
            screen_type = "loading"
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


def _attempt_app_start(
    d: Any,
    *,
    package_name: str,
    timings: dict[str, int],
    timer: Timer,
) -> dict[str, Any]:
    start = timer()
    try:
        d.app_start(package_name)
        timings["app_start_ms"] += _elapsed_ms(start, timer())
        return {"ok": True}
    except Exception:
        timings["app_start_ms"] += _elapsed_ms(start, timer())
        return {"ok": False}


def _foreground_package_name(d: Any) -> str:
    try:
        app_current = getattr(d, "app_current", None)
        if callable(app_current):
            current = app_current() or {}
            if isinstance(current, dict):
                return str(current.get("package") or current.get("packageName") or "").strip()
    except Exception:
        return ""
    return ""


def _check_expected_foreground_package(d: Any, *, expected_package_name: str) -> dict[str, Any]:
    expected = _safe_package_name(expected_package_name)
    actual = _foreground_package_name(d)
    checked = bool(expected and actual)
    mismatch = bool(checked and actual != expected)
    return {
        "package_guard_checked": checked,
        "expected_package_name": expected,
        "actual_foreground_package": actual,
        "package_guard_mismatch": mismatch,
        "package_guard_reason": "expected_package_mismatch" if mismatch else "",
    }


def _recover_transient_foreground_package(
    d: Any,
    *,
    expected_package_name: str,
    timer: Timer,
    sleeper: Sleeper,
    max_attempts: int = TRANSIENT_FOREGROUND_RECOVERY_ATTEMPTS,
) -> dict[str, Any]:
    metadata = {
        "transient_foreground_recovery_attempted": False,
        "transient_foreground_recovery_count": 0,
        "transient_foreground_packages_seen": [],
        "transient_foreground_recovery_succeeded": False,
    }
    guard = _check_expected_foreground_package(d, expected_package_name=expected_package_name)
    if not guard.get("package_guard_mismatch"):
        return {**guard, **metadata}

    actual = str(guard.get("actual_foreground_package") or "").strip()
    if actual not in TRANSIENT_FOREGROUND_PACKAGES:
        return {**guard, **metadata}

    press = getattr(d, "press", None)
    for attempt in range(max_attempts):
        metadata["transient_foreground_recovery_attempted"] = True
        metadata["transient_foreground_recovery_count"] = attempt + 1
        seen = [str(item) for item in list(metadata["transient_foreground_packages_seen"] or [])]
        if actual and actual not in seen:
            seen.append(actual)
        metadata["transient_foreground_packages_seen"] = seen
        if callable(press):
            try:
                press("back")
            except Exception:
                pass
        if TRANSIENT_FOREGROUND_RECOVERY_WAIT_MS > 0:
            sleeper(TRANSIENT_FOREGROUND_RECOVERY_WAIT_MS / 1000.0)
        guard = _check_expected_foreground_package(d, expected_package_name=expected_package_name)
        if not guard.get("package_guard_mismatch"):
            metadata["transient_foreground_recovery_succeeded"] = True
            return {**guard, **metadata}
        actual = str(guard.get("actual_foreground_package") or "").strip()
        if actual not in TRANSIENT_FOREGROUND_PACKAGES:
            return {**guard, **metadata}

    metadata["transient_foreground_recovery_succeeded"] = not guard.get("package_guard_mismatch")
    return {**guard, **metadata}


def _guard_foreground_package_for_login_input(
    d: Any,
    *,
    expected_package_name: str,
    timer: Timer,
    sleeper: Sleeper,
) -> dict[str, Any]:
    return _recover_transient_foreground_package(
        d,
        expected_package_name=expected_package_name,
        timer=timer,
        sleeper=sleeper,
    )


def _retry_app_start_once(
    d: Any,
    *,
    package_name: str,
    timings: dict[str, int],
    timer: Timer,
    reason: str,
) -> dict[str, Any]:
    metadata = {
        "app_start_retry_attempted": True,
        "app_start_retry_count": 1,
        "app_start_retry_reason": reason,
        "app_start_retry_result": "",
    }
    retry = _attempt_app_start(d, package_name=package_name, timings=timings, timer=timer)
    metadata["app_start_retry_result"] = "started_after_retry" if retry["ok"] else "app_start_failed_after_retry"
    return {"ok": bool(retry["ok"]), "metadata": metadata}


def _startup_retry_needed(startup_observation: dict[str, Any]) -> bool:
    final_screen = str(startup_observation.get("final_screen_type") or "unknown")
    if final_screen in {"unknown", "loading"}:
        return True
    signals = dict(startup_observation.get("signals") or {})
    return not _startup_screen_is_exploitable(signals)


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
        "join_instagram_landing",
        "email_code_challenge",
        "sms_code_challenge",
        "whatsapp_code_challenge",
        "authenticator_app_code_challenge",
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
        LoginProbeOutcome.VERIFICATION_PENDING.value,
        LoginProbeOutcome.LOGIN_FAILED.value,
    }


def _safe_package_name(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return DEFAULT_INSTAGRAM_PACKAGE_NAME
    if not re.fullmatch(r"[A-Za-z0-9_.]+", text):
        return DEFAULT_INSTAGRAM_PACKAGE_NAME
    return text


def _mask_adb_serial(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= 6:
        return text[:1] + "***" + text[-1:]
    return text[:4] + "***" + text[-4:]


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
    suggested_username = str(routing_signals.get("suggested_username") or routing_signals.get("prefilled_username") or "")
    if (
        screen_type == "login_form_prefilled_username"
        and suggested_username.strip().lstrip("@").lower()
        != str(expected_username or "").strip().lstrip("@").lower()
        and not bool(routing_signals.get("username_field_editable_present", True))
    ):
        return SimpleNamespace(
            decision="username_prefilled_not_editable",
            reason="username_prefilled_not_editable",
            dashboard_action_type=None,
        )
    clone_reuse_allowed = bool(previous_account_lifecycle.get("clone_reuse_allowed"))
    route = route_login_screen(
        expected_username=expected_username,
        suggested_username=suggested_username,
        screen_type=screen_type,
        available_usernames=list(routing_signals.get("available_usernames") or []),
        account_lifecycle_lookup=_router_lifecycle_lookup(previous_account_lifecycle),
        clone_reuse_allowed=clone_reuse_allowed,
        has_use_another_profile=bool(
            routing_signals.get("has_use_another_profile")
            or routing_signals.get("has_use_another_profile_button")
        ),
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
        has_use_another_profile=bool(
            routing_signals.get("has_use_another_profile")
            or routing_signals.get("has_use_another_profile_button")
        ),
        account_id=account_id,
    )


def _logout_fallback_gate(
    *,
    actual_username: str,
    expected_username: str,
    previous_account_lifecycle: dict[str, Any],
    explicitly_allowed: bool,
) -> tuple[bool, str]:
    stale_replacement_allowed = _previous_account_allows_stale_replacement(previous_account_lifecycle)
    if not explicitly_allowed and not stale_replacement_allowed:
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
    if stale_replacement_allowed:
        return True, "stale_session_replacement_allowed"
    return True, "operator_smoke_logout_fallback_allowed"


def _previous_account_allows_stale_replacement(previous_account_lifecycle: dict[str, Any]) -> bool:
    return (
        str(previous_account_lifecycle.get("source") or "").strip() == STALE_SESSION_LIFECYCLE_SOURCE
        and bool(previous_account_lifecycle.get("clone_reuse_allowed"))
        and bool(previous_account_lifecycle.get("stale_session_replacement_allowed"))
        and str(previous_account_lifecycle.get("replacement_safety_status") or "").strip().lower() == "allowed"
    )


def _stale_session_replacement_metadata(
    previous_account_lifecycle: dict[str, Any],
    *,
    actual_username: str,
    expected_username: str,
    allowed: bool,
    selected_route: str,
    controlled_logout_status: str = "not_started",
    target_login_status: str = "not_started",
    identity_verification_status: str = "not_started",
) -> dict[str, Any]:
    if not _previous_account_allows_stale_replacement(previous_account_lifecycle):
        return {}
    return {
        "replacement_flow": "previous_account_replacement",
        "selected_route": _safe_public_text(selected_route),
        "replacement_route": _safe_public_text(selected_route),
        "stale_session_replacement_allowed": bool(allowed),
        "replacement_safety_status": "allowed" if allowed else "blocked",
        "connected_account_state": _safe_public_text(
            previous_account_lifecycle.get("connected_account_state")
            or previous_account_lifecycle.get("stale_account_state")
            or previous_account_lifecycle.get("lifecycle_status")
        ),
        "previous_account_state": _safe_public_text(
            previous_account_lifecycle.get("previous_account_state")
            or previous_account_lifecycle.get("stale_account_state")
            or previous_account_lifecycle.get("lifecycle_status")
        ),
        "stale_account_state": _safe_public_text(
            previous_account_lifecycle.get("stale_account_state")
            or previous_account_lifecycle.get("lifecycle_status")
        ),
        "connected_username": _safe_public_text(actual_username),
        "target_username": _safe_public_text(expected_username),
        "controlled_logout_status": _safe_public_text(controlled_logout_status),
        "target_login_status": _safe_public_text(target_login_status),
        "identity_verification_status": _safe_public_text(identity_verification_status),
    }


def _stale_add_existing_failure_is_recoverable(reason: Any, previous_account_lifecycle: dict[str, Any]) -> bool:
    return (
        _previous_account_allows_stale_replacement(previous_account_lifecycle)
        and str(reason or "").strip() in STALE_ADD_EXISTING_FALLBACK_RECOVERABLE_REASONS
    )


def _replacement_progress_metadata_for_final_outcome(
    metadata: dict[str, Any],
    *,
    final_outcome: str,
) -> dict[str, Any]:
    if not bool(metadata.get("stale_session_replacement_allowed")):
        return metadata
    updated = dict(metadata)
    primary_status = str(updated.get("primary_replacement_status") or "").strip()
    if not primary_status or primary_status == "started":
        updated["primary_replacement_status"] = (
            "completed" if final_outcome == LoginProbeOutcome.CONNECTED.value else "failed"
        )
    if str(updated.get("target_login_status") or "") in {"", "not_started", "in_progress"}:
        updated["target_login_status"] = "connected" if final_outcome == LoginProbeOutcome.CONNECTED.value else "failed"
    if str(updated.get("identity_verification_status") or "") in {"", "not_started", "in_progress"}:
        updated["identity_verification_status"] = (
            "verified" if final_outcome == LoginProbeOutcome.CONNECTED.value else "failed"
        )
    if str(updated.get("controlled_logout_status") or "") == "":
        updated["controlled_logout_status"] = "not_started"
    return updated


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
    if action == "tap_already_have_profile":
        return "post_join_instagram_landing"
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
    if signals.get("screen_type") == "login_form_username_step":
        return (
            signals.get("has_username_field") is True
            and signals.get("has_password_field") is not True
            and signals.get("has_login_button") is True
        )
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
        "start_login_username_step_flow",
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
    if action == "tap_already_have_profile":
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
    screen_type = str(signals.get("screen_type") or "").strip()
    if signals.get("verification_code_challenge_present") is True or screen_type in {
        "email_code_challenge",
        "sms_code_challenge",
        "whatsapp_code_challenge",
        "authenticator_app_code_challenge",
    }:
        return LoginProbeOutcome.VERIFICATION_PENDING.value
    outcome = str(signals.get("login_probe_outcome") or "unknown").strip()
    if outcome in {
        LoginProbeOutcome.CONNECTED.value,
        LoginProbeOutcome.NEEDS_2FA.value,
        LoginProbeOutcome.CHECKPOINT.value,
        LoginProbeOutcome.VERIFICATION_PENDING.value,
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
    allowed_screen_types = {"continue_as_candidate", "active_account_profile", "login_form_prefilled_username"}
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
        "stale_session_replacement_allowed": bool(raw.get("stale_session_replacement_allowed")),
        "replacement_safety_status": _safe_public_text(raw.get("replacement_safety_status")),
        "stale_account_state": _safe_public_text(raw.get("stale_account_state")),
        "connected_account_state": _safe_public_text(raw.get("connected_account_state")),
        "previous_account_state": _safe_public_text(raw.get("previous_account_state")),
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
            "stale_session_replacement_allowed": bool(
                previous_account_lifecycle.get("stale_session_replacement_allowed")
            ),
            "replacement_safety_status": _safe_public_text(previous_account_lifecycle.get("replacement_safety_status")),
            "stale_account_state": _safe_public_text(previous_account_lifecycle.get("stale_account_state")),
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
        "login_form_username_step",
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
            "email_code_challenge_detected",
            "verification_code_challenge_detected",
            "challenge_type",
            "verification_channel",
            "verification_code_expired",
            "masked_email_present",
            "post_submit_screens",
            "final_terminal_screen",
            "save_password_prompt_detected",
            "save_password_prompt_dismissed",
            "save_password_prompt_dismiss_attempt_count",
            "dismiss_method",
            "samsung_pass_save_password_prompt_detected",
            "samsung_pass_save_password_prompt_cancelled",
            "instagram_save_login_info_prompt_detected",
            "instagram_save_login_info_prompt_not_now",
            "post_login_location_services_prompt_detected",
            "post_login_location_services_prompt_dismissed",
            "post_login_location_services_prompt_dismiss_method",
            "notifications_prompt_detected",
            "notifications_next_tap_sent",
            "notifications_skip_tap_sent",
            "notifications_skip_after_settings_sent",
            "android_notification_settings_detected",
            "android_back_from_notification_settings_sent",
            "post_dismiss_screen_type",
            "post_dismiss_final_observation_count",
            "post_dismiss_final_screens",
            "post_dismiss_final_wait_total_ms",
            "post_dismiss_final_screen_type",
            "connected_detected_after_save_prompt_dismiss",
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
        "verification_pending",
        "unsupported_post_submit_challenge",
        "connected",
        "login_submit_still_loading",
        "save_password_prompt_blocking",
        "save_login_info_prompt_blocking",
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
        "save_login_info_prompt_blocking",
        "login_submit_still_loading",
    }:
        return raw
    normalized = normalize_login_probe_outcome(raw)
    return str(normalized.value)


def _dashboard_action_for_outcome(
    outcome: str,
    *,
    challenge_type: str = "",
    post_submit_screen_type: str = "",
) -> str | None:
    if outcome == LoginProbeOutcome.VERIFICATION_PENDING.value:
        if challenge_type in {"email", "sms", "whatsapp", "authenticator_app"} or post_submit_screen_type in {
            "email_code_challenge",
            "sms_code_challenge",
            "whatsapp_code_challenge",
            "authenticator_app_code_challenge",
        }:
            return "enter_email_verification_code"
        return None
    if outcome == LoginProbeOutcome.UNSUPPORTED_POST_SUBMIT_CHALLENGE.value:
        return "review_login_challenge"
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
    if outcome == "save_login_info_prompt_blocking":
        return "save_login_info_prompt_blocking"
    if outcome == "login_submit_still_loading":
        return "post_submit_loading_timeout"
    if outcome == "unknown":
        return "unknown_post_submit_outcome"
    return str(classification_reason or "")


def _dashboard_action_for_failure(failure_reason: str | None) -> str | None:
    if failure_reason in {"adb_not_available", "password_input_unavailable"}:
        return "retry_provisioning"
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


def _finalize_package_mismatch(
    *,
    account_id: str,
    expected_username: str,
    expected_package_name: str,
    actual_foreground_package: str,
    actions_taken: list[str],
    timings: dict[str, int],
    warnings: list[str],
    extra_metadata: dict[str, Any] | None,
    total_start: float,
    timer: Timer,
    publisher: Publisher | None,
    publish_enabled: bool,
    run_id: str | None = None,
    run_type: str | None = None,
    device_id: str | None = None,
    expected_app_instance_id: str | None = None,
    adb_serial_masked: str | None = None,
    reason: str = "expected_package_mismatch",
) -> LoginProvisioningFlowResult:
    metadata = {
        **(extra_metadata or {}),
        "final_outcome": "wrong_app_package",
        "reason": reason,
        "failure_reason": reason,
        "package_guard_checked": True,
        "package_guard_mismatch": True,
        "expected_package_name": expected_package_name,
        "actual_foreground_package": actual_foreground_package or "unknown",
        "expected_app_instance_id": expected_app_instance_id,
        "device_id": device_id,
        "adb_serial_masked": adb_serial_masked,
        "run_id": run_id,
        "run_type": run_type,
        "would_submit_password": False,
        "password_input": False,
        "submit_executed": False,
    }
    side_effects = _sync_login_package_mismatch_side_effects(
        account_id=account_id,
        expected_username=expected_username,
        expected_package_name=expected_package_name,
        actual_foreground_package=actual_foreground_package or "unknown",
        run_id=run_id,
        run_type=run_type,
        device_id=device_id,
        expected_app_instance_id=expected_app_instance_id,
        adb_serial_masked=adb_serial_masked,
        reason=reason,
    )
    metadata.update(side_effects)
    if side_effects.get("warnings"):
        warnings = [*warnings, *list(side_effects.get("warnings") or [])]
    return _finalize(
        ok=False,
        completed=False,
        final_outcome="wrong_app_package",
        reason=reason,
        failure_reason=reason,
        final_login_status="logged_out",
        final_provisioning_status="blocked",
        final_onboarding_status="blocked",
        dashboard_action_type="review_login_package_mismatch",
        should_publish_status=False,
        account_id=account_id,
        expected_username=expected_username,
        actions_taken=actions_taken,
        timings=timings,
        warnings=warnings,
        extra_metadata=metadata,
        total_start=total_start,
        timer=timer,
        publisher=publisher,
        publish_enabled=publish_enabled,
    )


def _sync_login_package_mismatch_side_effects(
    *,
    account_id: str,
    expected_username: str,
    expected_package_name: str,
    actual_foreground_package: str,
    run_id: str | None,
    run_type: str | None,
    device_id: str | None,
    expected_app_instance_id: str | None,
    adb_serial_masked: str | None,
    reason: str,
) -> dict[str, Any]:
    warnings: list[str] = []
    try:
        incident = publish_login_package_mismatch_incident(
            account_id=account_id,
            expected_username=expected_username,
            expected_package_name=expected_package_name,
            actual_foreground_package=actual_foreground_package,
            run_id=run_id,
            run_type=run_type,
            device_id=device_id,
            expected_app_instance_id=expected_app_instance_id,
            adb_serial_masked=adb_serial_masked,
            reason=reason,
        )
    except Exception:
        incident = {"published": False, "reason": "incident_publish_failed"}
        warnings.append("login_package_mismatch_incident_failed_safe")
    try:
        dashboard_action = sync_login_package_mismatch_dashboard_action(
            account_id=account_id,
            expected_package_name=expected_package_name,
            actual_foreground_package=actual_foreground_package,
            run_id=run_id,
            expected_app_instance_id=expected_app_instance_id,
            reason=reason,
        )
    except Exception:
        dashboard_action = {"published": False, "reason": "dashboard_action_sync_failed"}
        warnings.append("login_package_mismatch_dashboard_action_failed_safe")
    try:
        notifications = dispatch_login_package_mismatch_notifications()
    except Exception:
        notifications = {"dispatched": False, "reason": "dispatch_failed"}
        warnings.append("login_package_mismatch_notifications_failed_safe")
    return {
        "login_package_mismatch_incident": incident,
        "login_package_mismatch_dashboard_action": dashboard_action,
        "login_package_mismatch_notifications": notifications,
        "warnings": warnings,
    }


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
    extra_metadata = _replacement_progress_metadata_for_final_outcome(
        dict(extra_metadata or {}),
        final_outcome=final_outcome,
    )
    expected_identity = _normalize_identity_username(extra_metadata.get("expected_username"))
    actual_identity = _normalize_identity_username(extra_metadata.get("actual_logged_in_username"))
    if (
        ok
        and completed
        and final_outcome == LoginProbeOutcome.CONNECTED.value
        and expected_identity
        and actual_identity == expected_identity
    ):
        extra_metadata.setdefault("expected_identity_verified", True)
        extra_metadata.setdefault("identity_verification_status", "verified")
    publish_payload = _publish_payload(
        account_id=account_id,
        final_login_status=final_login_status,
        final_provisioning_status=final_provisioning_status,
        final_onboarding_status=final_onboarding_status,
        reason=reason,
        final_outcome=final_outcome,
        retry_count=retry_count,
        dashboard_action_type=dashboard_action_type,
        extra_metadata=extra_metadata,
    )
    published = False
    publish_attempted = False
    publish_result_label = "skipped"
    publish_error_code = ""
    connected_publish_allowed = _connected_status_publishable(
        ok=ok,
        completed=completed,
        final_outcome=final_outcome,
        final_login_status=final_login_status,
        account_id=account_id,
        extra_metadata=extra_metadata,
    )
    publish_allowed = connected_publish_allowed or _verification_status_publishable(
        should_publish_status=should_publish_status,
        final_outcome=final_outcome,
        final_login_status=final_login_status,
        account_id=account_id,
    )
    effective_should_publish = bool(should_publish_status) and publish_allowed
    publish_reason = _publish_skip_reason(
        publish_enabled=publish_enabled,
        should_publish_status=should_publish_status,
        publish_allowed=publish_allowed,
        account_id=account_id,
        final_outcome=final_outcome,
    )
    publish_warnings = list(warnings)
    if publish_enabled and effective_should_publish and publisher is not None:
        try:
            publish_attempted = True
            publish_result = publisher(**publish_payload)
            published = bool((publish_result or {}).get("published", True))
            publish_result_label = "published" if published else "failed"
            publish_error = _publish_result_error_code(publish_result or {})
            publish_reason = "published_connected" if published else publish_error
            if not published:
                publish_error_code = publish_error
                publish_warnings.append("publish_failed_safe")
        except Exception as exc:
            publish_error = _publish_exception_error_code(exc)
            published = False
            publish_attempted = True
            publish_result_label = "failed"
            publish_reason = publish_error
            publish_error_code = publish_error
            publish_warnings.append("publish_failed_safe")
    elif publish_enabled and effective_should_publish:
        publish_result_label = "failed"
        publish_reason = "publisher_missing"
        publish_error_code = "publisher_missing"

    challenge_side_effects = _sync_login_challenge_side_effects(
        account_id=account_id,
        expected_username=expected_username,
        dashboard_action_type=dashboard_action_type,
        final_outcome=final_outcome,
        reason=reason,
        extra_metadata=extra_metadata,
        publish_warnings=publish_warnings,
    )
    if challenge_side_effects.get("warnings"):
        publish_warnings.extend(challenge_side_effects["warnings"])

    follow_source_rotation_provision: dict[str, Any] = {"skipped": True, "db_mutation_performed": False}
    if account_id and str(final_provisioning_status or "") == "ready" and connected_publish_allowed:
        try:
            from follow_source_rotation_settings import maybe_provision_follow_source_rotation_on_ready

            follow_source_rotation_provision = maybe_provision_follow_source_rotation_on_ready(
                account_id=account_id,
                account_username=expected_username,
                final_provisioning_status=final_provisioning_status,
                context="login_provisioning_ready",
            )
        except Exception as exc:
            follow_source_rotation_provision = {
                "ok": False,
                "skipped": False,
                "reason": "follow_source_rotation_provision_failed",
                "error": str(exc)[:200],
                "db_mutation_performed": False,
            }
            publish_warnings.append("follow_source_rotation_provision_failed_safe")

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
                "publish_enabled": bool(publish_enabled),
                "publish_attempted": bool(publish_attempted),
                "publish_result": publish_result_label,
                "publish_error_code": publish_error_code,
                "dashboard_action_sync": challenge_side_effects.get("dashboard_action_sync"),
                "login_challenge_incident": challenge_side_effects.get("login_challenge_incident"),
                "follow_source_rotation_provision": follow_source_rotation_provision,
                **extra_metadata,
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
        should_publish_status=effective_should_publish,
        publish_payload=publish_payload if effective_should_publish else None,
        published=published,
        publish_reason=publish_reason,
        timings=dict(timings),
        warnings=publish_warnings,
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
    extra_metadata: dict[str, Any],
) -> dict[str, Any]:
    final_reauth_required = False if str(final_login_status or "") == "connected" else None
    final_reauth_reason = None
    run_id = str(extra_metadata.get("run_id") or "").strip()
    external_request_id = f"login_provisioner:{run_id}" if run_id else None
    publish_metadata = clean_login_probe_metadata(
        {
            "source": "login_provisioner_orchestrator",
            **_publish_safe_metadata(extra_metadata),
            "final_outcome": final_outcome,
            "retry_count": retry_count,
            "dashboard_action_type": dashboard_action_type,
        }
    )
    return redact_credentials_payload(
        {
            "account_id": account_id,
            "login_status": final_login_status,
            "provisioning_status": final_provisioning_status,
            "onboarding_status": final_onboarding_status,
            "reauth_required": final_reauth_required,
            "reauth_reason": final_reauth_reason,
            "reason": reason,
            "external_request_id": external_request_id,
            "metadata": publish_metadata,
        }
    )


def _publish_result_error_code(publish_result: dict[str, Any]) -> str:
    reason = str((publish_result or {}).get("reason") or "").strip()
    if reason in {"", "published"}:
        return "publish_failed"
    mapping = {
        "url_missing": "publisher_url_missing",
        "token_missing": "publisher_token_missing",
        "not_configured": "publisher_not_configured",
        "http_error": "publisher_http_error",
        "timeout": "publisher_timeout",
        "network_error": "publisher_request_exception",
        "request_build_failed": "publisher_request_exception",
        "response_not_json": "publisher_response_not_json",
        "response_not_ok": "publisher_response_not_ok",
        "account_id_invalid": "publisher_invalid_payload",
        "no_status_fields": "publisher_invalid_payload",
        "reason_too_long": "publisher_invalid_payload",
        "external_request_id_invalid": "publisher_invalid_payload",
        "metadata_must_be_object": "publisher_invalid_payload",
        "forbidden_metadata": "publisher_invalid_payload",
        "rpc_failed": "publisher_rpc_error",
        "status_update_failed": "publisher_rpc_error",
        "invalid_status": "publisher_rpc_error",
        "account_not_found": "publisher_rpc_error",
        "disabled": "publisher_disabled",
        "not_configured": "publisher_not_configured",
    }
    if reason.startswith("publisher_"):
        return reason
    return mapping.get(reason, "publisher_unexpected_exception")


def _publish_exception_error_code(exc: Exception) -> str:
    if isinstance(exc, (TypeError, ValueError)):
        return "publisher_invalid_payload"
    return "publisher_unexpected_exception"


def _connected_status_publishable(
    *,
    ok: bool,
    completed: bool,
    final_outcome: str,
    final_login_status: str | None,
    account_id: str,
    extra_metadata: dict[str, Any],
) -> bool:
    if not str(account_id or "").strip():
        return False
    if not (ok and completed):
        return False
    if str(final_outcome or "") != LoginProbeOutcome.CONNECTED.value:
        return False
    if str(final_login_status or "") != "connected":
        return False
    if extra_metadata.get("expected_identity_verified") is not True:
        return False
    if extra_metadata.get("profile_opened") is not True:
        return False
    expected_username = _normalize_identity_username(extra_metadata.get("expected_username"))
    actual_username = _normalize_identity_username(extra_metadata.get("actual_logged_in_username"))
    if not expected_username or not actual_username or actual_username != expected_username:
        return False
    selected_route = str(extra_metadata.get("selected_route") or "")
    router_decision = str(extra_metadata.get("router_decision") or "")
    safe_routes = {
        "continue_as_expected",
        "use_another_profile",
        "account_picker",
        "login_form_empty",
        "login_form_prefilled_expected",
        "replace_prefilled_username",
        "continue_password_only",
        "already_connected_expected",
        "add_existing_account",
        "logout_fallback",
    }
    return bool(selected_route in safe_routes or router_decision or extra_metadata.get("central_orchestrator_used"))


def _default_connected_identity_verifier(d: Any, **kwargs: Any) -> Any:
    from account_identity_guard import verify_active_instagram_account_matches_expected

    return verify_active_instagram_account_matches_expected(d, **kwargs)


def _verify_connected_identity_before_ready(
    d: Any,
    *,
    verifier: ConnectedIdentityVerifier,
    account_id: str,
    expected_username: str,
    run_id: str | None,
    run_type: str,
) -> Any:
    try:
        return verifier(
            d,
            expected_account_username=expected_username,
            account_id=account_id,
            run_id=run_id,
            run_type=run_type,
            stage="login_provisioning_post_login_identity",
        )
    except Exception as exc:
        return {
            "ok": False,
            "expected_account_username": expected_username,
            "actual_logged_in_username": "",
            "failure_reason": "identity_verification_internal_error",
            "verification_method": "account_identity_guard_exception",
            "meta": {"error_type": type(exc).__name__},
        }


def _connected_identity_safe_metadata(result: Any) -> dict[str, Any]:
    if hasattr(result, "to_dict") and callable(result.to_dict):
        raw = result.to_dict()
    elif isinstance(result, dict):
        raw = dict(result)
    else:
        raw = {
            "ok": bool(getattr(result, "ok", False)),
            "expected_account_username": getattr(result, "expected_account_username", ""),
            "actual_logged_in_username": getattr(result, "actual_logged_in_username", ""),
            "failure_reason": getattr(result, "failure_reason", ""),
            "verification_method": getattr(result, "verification_method", ""),
            "identity_evidence": getattr(result, "identity_evidence", ""),
        }
    expected = _safe_public_text(raw.get("expected_account_username"))
    actual = _safe_public_text(raw.get("actual_logged_in_username"))
    normalized_expected = _normalize_identity_username(expected)
    normalized_actual = _normalize_identity_username(actual)
    raw_meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
    profile_opened = raw.get("profile_opened") is True or raw_meta.get("profile_opened") is True
    verified = (
        bool(raw.get("ok"))
        and bool(normalized_expected)
        and normalized_actual == normalized_expected
        and profile_opened
    )
    failure_reason = _safe_public_text(raw.get("failure_reason"))
    if not verified and not failure_reason:
        if normalized_actual and normalized_actual != normalized_expected:
            failure_reason = "active_instagram_account_mismatch"
        elif not profile_opened:
            failure_reason = "own_profile_not_opened"
        else:
            failure_reason = "expected_instagram_identity_not_verified"
    verification_method = _safe_public_text(raw.get("verification_method"))
    identity_evidence = _safe_public_text(raw.get("identity_evidence"))
    return {
        "expected_identity_verified": verified,
        "identity_verification_status": "verified" if verified else "failed",
        "identity_verification_failure_reason": "" if verified else failure_reason,
        "expected_username": expected,
        "actual_logged_in_username": actual,
        "profile_opened": profile_opened,
        "identity_verification_method": verification_method,
        "identity_evidence": identity_evidence,
    }


def _verification_status_publishable(
    *,
    should_publish_status: bool,
    final_outcome: str,
    final_login_status: str | None,
    account_id: str,
) -> bool:
    if not should_publish_status or not str(account_id or "").strip():
        return False
    outcome = str(final_outcome or "").strip().lower()
    if outcome not in {
        LoginProbeOutcome.VERIFICATION_PENDING.value,
        LoginProbeOutcome.NEEDS_2FA.value,
        LoginProbeOutcome.CHECKPOINT.value,
        LoginProbeOutcome.UNSUPPORTED_POST_SUBMIT_CHALLENGE.value,
    }:
        return False
    return bool(str(final_login_status or "").strip())


def _publish_skip_reason(
    *,
    publish_enabled: bool,
    should_publish_status: bool,
    publish_allowed: bool,
    account_id: str,
    final_outcome: str,
) -> str:
    if not publish_enabled:
        return "disabled"
    if not str(account_id or "").strip():
        return "missing_account_id"
    if not should_publish_status or not publish_allowed:
        if str(final_outcome or "") in {
            "needs_2fa",
            "checkpoint",
            "verification_pending",
            "unsupported_post_submit_challenge",
            "login_failed",
            "password_required_dialog",
        }:
            return "deferred_until_dashboard"
        return "not_publishable"
    return "publisher_missing"


def _publish_safe_metadata(extra_metadata: dict[str, Any]) -> dict[str, Any]:
    allowed_keys = (
        "run_id",
        "central_orchestrator_version",
        "selected_route",
        "final_terminal_screen",
        "screen_type",
        "screen_before_submit",
        "expected_identity_verified",
        "identity_verification_status",
        "identity_verification_failure_reason",
        "expected_username",
        "actual_logged_in_username",
        "profile_opened",
        "identity_verification_method",
        "identity_evidence",
    )
    safe: dict[str, Any] = {}
    for key in allowed_keys:
        value = extra_metadata.get(key)
        if value not in (None, ""):
            safe[key] = value
    if "central_orchestrator_used" in extra_metadata:
        safe["central_orchestrator_used"] = bool(extra_metadata.get("central_orchestrator_used"))
    return safe


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


def _extract_challenge_metadata(extra_metadata: dict[str, Any]) -> dict[str, Any]:
    password_meta = extra_metadata.get("password_result")
    if isinstance(password_meta, dict):
        return password_meta
    return extra_metadata


def _sync_login_challenge_side_effects(
    *,
    account_id: str,
    expected_username: str,
    dashboard_action_type: str | None,
    final_outcome: str,
    reason: str,
    extra_metadata: dict[str, Any],
    publish_warnings: list[str],
) -> dict[str, Any]:
    if dashboard_action_type not in {"enter_email_verification_code", "review_login_challenge"}:
        return {}

    challenge_meta = _extract_challenge_metadata(extra_metadata)
    run_id = str(extra_metadata.get("run_id") or challenge_meta.get("run_id") or "").strip() or None
    warnings: list[str] = []
    dashboard_action_sync: dict[str, Any] | None = None
    login_challenge_incident: dict[str, Any] | None = None

    try:
        dashboard_action_sync = sync_login_challenge_dashboard_action(
            account_id=account_id,
            dashboard_action_type=dashboard_action_type,
            run_id=run_id,
            challenge_type=str(challenge_meta.get("challenge_type") or ""),
            screen_type=str(challenge_meta.get("post_submit_screen_type") or challenge_meta.get("screen_type") or ""),
            masked_email_present=bool(challenge_meta.get("masked_email_present")),
            human_review_required=dashboard_action_type == "review_login_challenge",
            stage="post_submit",
            metadata={
                "provenance_kind": PROVENANCE_KIND_ACTIVE_RUN,
                "expected_app_instance_id": extra_metadata.get("expected_app_instance_id"),
                "assignment_id": extra_metadata.get("assignment_id"),
                "credentials_version": extra_metadata.get("credentials_version"),
                "request_id": extra_metadata.get("request_id"),
            },
        )
    except Exception:
        warnings.append("dashboard_action_sync_failed_safe")
        dashboard_action_sync = {"published": False, "reason": "dashboard_action_sync_failed_safe"}

    try:
        login_challenge_incident = publish_login_challenge_pending_incident(
            account_id=account_id,
            expected_username=expected_username,
            run_id=run_id,
            challenge_type=str(challenge_meta.get("challenge_type") or ""),
            screen_type=str(challenge_meta.get("post_submit_screen_type") or challenge_meta.get("screen_type") or ""),
            reason=reason or final_outcome,
            dashboard_action_type=dashboard_action_type,
            masked_email_present=bool(challenge_meta.get("masked_email_present")),
        )
    except Exception:
        warnings.append("login_challenge_incident_failed_safe")
        login_challenge_incident = {"published": False, "reason": "login_challenge_incident_failed_safe"}

    if dashboard_action_sync and not dashboard_action_sync.get("published"):
        warnings.append("dashboard_action_sync_not_published")
    if login_challenge_incident and not login_challenge_incident.get("published"):
        warnings.append("login_challenge_incident_not_published")

    return {
        "dashboard_action_sync": dashboard_action_sync,
        "login_challenge_incident": login_challenge_incident,
        "warnings": warnings,
    }


def _post_email_code_password_screen_ready(signals: dict[str, Any]) -> bool:
    screen_type = str(signals.get("screen_type") or "")
    if screen_type not in POST_EMAIL_CODE_PASSWORD_SCREENS:
        return False
    return _signals_confirm_login_form(signals)


def _signals_indicate_password_entry_ready(signals: dict[str, Any]) -> bool:
    if _post_email_code_password_screen_ready(signals):
        return True
    if signals.get("verification_code_challenge_present") is True:
        return False
    return bool(signals.get("has_password_field")) and bool(signals.get("has_login_button"))


def _should_chain_password_after_email_code_resume(resume_result: Any, password_signals: dict[str, Any]) -> bool:
    if _signals_indicate_password_entry_ready(password_signals):
        return True
    outcome = str(getattr(resume_result, "post_submit_outcome", "") or getattr(resume_result, "reason", "") or "")
    if outcome == "post_code_password_required":
        return True
    screen_type = str(getattr(resume_result, "post_submit_screen_type", "") or password_signals.get("screen_type") or "")
    if screen_type in POST_EMAIL_CODE_PASSWORD_SCREENS:
        return True
    failure = str(getattr(resume_result, "failure_reason", "") or "")
    reason = str(getattr(resume_result, "reason", "") or "")
    if failure in {
        "email_code_challenge_screen_required",
        "verification_code_challenge_screen_required",
    } or reason in {"post_code_password_required"}:
        return _signals_indicate_password_entry_ready(password_signals)
    if failure.startswith("verification_code") or reason in {"verification_code_input_empty", "adb_not_available"}:
        return _signals_indicate_password_entry_ready(password_signals)
    return False


def _submit_password_after_email_code(
    d: Any,
    *,
    account_id: str,
    expected_username: str,
    credentials_getter: CredentialsGetter,
    signals: dict[str, Any],
    actions_taken: list[str],
    timings: dict[str, int],
    warnings: list[str],
    total_start: float,
    timer: Timer,
    sleeper: Sleeper,
    publisher: Publisher | None,
    publish_enabled: bool,
    package_name: str,
    run_id: str | None,
    run_type: str,
    device_id: str | None,
    expected_app_instance_id: str | None,
    adb_serial_masked: str | None,
    post_submit_timeout_ms: Optional[int],
    max_retry_attempts: int,
    action_id: str | None,
    consume_from_action: bool,
    resume_extra_metadata: dict[str, Any],
    connected_identity_verifier: ConnectedIdentityVerifier,
) -> LoginProvisioningFlowResult:
    safe_account_id = str(account_id or "").strip()
    safe_expected_username = str(expected_username or "").strip()
    safe_package_name = _safe_package_name(package_name)
    max_retries = min(MAX_RETRY_ATTEMPTS, max(0, int(max_retry_attempts or 0)))

    route = route_login_screen(
        expected_username=safe_expected_username,
        suggested_username=str(signals.get("suggested_username") or signals.get("prefilled_username") or ""),
        screen_type=str(signals.get("screen_type") or "unknown"),
        available_usernames=list(signals.get("available_usernames") or []),
        has_use_another_profile=bool(
            signals.get("has_use_another_profile") or signals.get("has_use_another_profile_button")
        ),
        account_id=safe_account_id,
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
            final_onboarding_status="blocked",
            dashboard_action_type="review_account_mismatch",
            should_publish_status=True,
            account_id=safe_account_id,
            expected_username=safe_expected_username,
            actions_taken=actions_taken,
            timings=timings,
            warnings=warnings,
            extra_metadata={
                **resume_extra_metadata,
                **_pre_submit_observation_metadata(signals),
                "selected_route": route.decision,
                "selected_route_reason": route.reason,
            },
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
                **resume_extra_metadata,
                **_pre_submit_observation_metadata(signals),
            },
            total_start=total_start,
            timer=timer,
            publisher=publisher,
            publish_enabled=publish_enabled,
        )

    guard = _guard_foreground_package_for_login_input(
        d,
        expected_package_name=safe_package_name,
        timer=timer,
        sleeper=sleeper,
    )
    if guard.get("package_guard_mismatch"):
        return _finalize_package_mismatch(
            account_id=safe_account_id,
            expected_username=safe_expected_username,
            expected_package_name=safe_package_name,
            actual_foreground_package=str(guard.get("actual_foreground_package") or ""),
            run_id=run_id,
            run_type=run_type,
            device_id=device_id,
            expected_app_instance_id=expected_app_instance_id,
            adb_serial_masked=adb_serial_masked,
            actions_taken=actions_taken,
            timings=timings,
            warnings=warnings,
            extra_metadata={
                **resume_extra_metadata,
                **_pre_submit_observation_metadata(signals),
                **guard,
            },
            total_start=total_start,
            timer=timer,
            publisher=publisher,
            publish_enabled=publish_enabled,
            reason="resume_email_code_wrong_package",
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
    password_meta = password_result_metadata["password_result"]
    identity_metadata: dict[str, Any] = {}
    identity_failure_reason = ""
    if outcome == LoginProbeOutcome.CONNECTED.value:
        identity_result = _verify_connected_identity_before_ready(
            d,
            verifier=connected_identity_verifier,
            account_id=safe_account_id,
            expected_username=safe_expected_username,
            run_id=run_id,
            run_type=run_type,
        )
        identity_metadata = _connected_identity_safe_metadata(identity_result)
        actions_taken.append("verify_connected_account_identity")
        if identity_metadata.get("expected_identity_verified") is not True:
            identity_failure_reason = str(
                identity_metadata.get("identity_verification_failure_reason")
                or "expected_instagram_identity_not_verified"
            )
            outcome = "identity_verification_failed"
            warnings.append("connected_identity_not_verified_safe")
    classification = classify_login_probe_outcome(
        outcome,
        metadata={
            **password_meta,
            "screen_type": password_meta.get("post_submit_screen_type"),
        },
    )
    dashboard_action_type = _dashboard_action_for_outcome(
        outcome,
        challenge_type=str(password_meta.get("challenge_type") or ""),
        post_submit_screen_type=str(password_meta.get("post_submit_screen_type") or ""),
    )
    if identity_failure_reason:
        dashboard_action_type = "enter_email_verification_code"
    final_reason = _final_reason_for_password_outcome(outcome, password_result, classification.reason)
    if identity_failure_reason:
        final_reason = identity_failure_reason

    action_sync: dict[str, Any] | None = None
    if consume_from_action and action_id:
        try:
            action_sync = sync_verification_action_after_email_code_resume(
                action_id=action_id,
                account_id=safe_account_id,
                run_id=run_id,
                ok=outcome == LoginProbeOutcome.CONNECTED.value and not identity_failure_reason,
                final_outcome=outcome,
                failure_reason=(
                    None
                    if outcome == LoginProbeOutcome.CONNECTED.value and not identity_failure_reason
                    else identity_failure_reason or outcome
                ),
                screen_type=str(password_meta.get("post_submit_screen_type") or ""),
            )
        except Exception:
            warnings.append("verification_action_sync_failed_safe")

    return _finalize(
        ok=outcome == LoginProbeOutcome.CONNECTED.value,
        completed=bool(identity_failure_reason)
        or outcome
        in {
            LoginProbeOutcome.CONNECTED.value,
            LoginProbeOutcome.NEEDS_2FA.value,
            LoginProbeOutcome.CHECKPOINT.value,
            LoginProbeOutcome.VERIFICATION_PENDING.value,
            LoginProbeOutcome.UNSUPPORTED_POST_SUBMIT_CHALLENGE.value,
            LoginProbeOutcome.LOGIN_FAILED.value,
        },
        final_outcome=outcome,
        reason=final_reason,
        failure_reason=(
            None
            if outcome == LoginProbeOutcome.CONNECTED.value
            else identity_failure_reason or outcome
        ),
        final_login_status="verification_pending" if identity_failure_reason else classification.login_status,
        final_provisioning_status=(
            "login_verification_pending" if identity_failure_reason else classification.provisioning_status
        ),
        final_onboarding_status="verification_pending" if identity_failure_reason else classification.onboarding_status,
        retry_attempted=retry_attempted,
        retry_count=retry_count,
        dashboard_action_type=dashboard_action_type,
        should_publish_status=classification.should_publish or bool(identity_failure_reason),
        account_id=safe_account_id,
        expected_username=safe_expected_username,
        actions_taken=actions_taken,
        timings=_merge_timings(timings, password_result.timings),
        warnings=[*warnings, *password_result.warnings],
        extra_metadata={
            **resume_extra_metadata,
            **({"verification_action_sync": action_sync} if action_sync else {}),
            **_pre_submit_observation_metadata(signals),
            **password_result_metadata,
            **identity_metadata,
            "post_email_code_password_submit": True,
        },
        total_start=total_start,
        timer=timer,
        publisher=publisher,
        publish_enabled=publish_enabled,
    )


def run_email_code_resume_flow(
    d: Any,
    *,
    account_id: str,
    expected_username: str,
    verification_code: SecretValue,
    credentials_getter: CredentialsGetter | None = None,
    action_id: str | None = None,
    consume_from_action: bool = False,
    run_id: str | None = None,
    publisher: Publisher | None = None,
    publish_enabled: bool = False,
    package_name: str = DEFAULT_INSTAGRAM_PACKAGE_NAME,
    run_type: str | None = "login_email_code_resume",
    device_serial: str | None = None,
    device_id: str | None = None,
    expected_app_instance_id: str | None = None,
    post_submit_timeout_ms: Optional[int] = None,
    max_retry_attempts: int = MAX_RETRY_ATTEMPTS,
    timer: Timer | None = None,
    sleeper: Sleeper | None = None,
    connected_identity_verifier: ConnectedIdentityVerifier | None = None,
) -> LoginProvisioningFlowResult:
    """Resume the canonical verification-code challenge without reloading credentials."""

    timer = timer or time.perf_counter
    sleeper = sleeper or time.sleep
    total_start = timer()
    timings = _empty_timings()
    warnings: list[str] = []
    actions_taken = ["route:email_code_resume"]
    safe_account_id = str(account_id or "").strip()
    safe_expected_username = str(expected_username or "").strip()
    safe_package_name = _safe_package_name(package_name)
    safe_run_type = str(run_type or "login_email_code_resume").strip() or "login_email_code_resume"
    safe_device_id = str(device_id or "").strip() or None
    safe_expected_app_instance_id = str(expected_app_instance_id or "").strip() or None
    safe_adb_serial_masked = _mask_adb_serial(device_serial)
    code_value = verification_code
    identity_verifier = connected_identity_verifier or _default_connected_identity_verifier
    resume_extra_base: dict[str, Any] = {
        "resume_mode": "consume_action" if consume_from_action else "stdin",
        "run_id": run_id,
    }

    guard = _guard_foreground_package_for_login_input(
        d,
        expected_package_name=safe_package_name,
        timer=timer,
        sleeper=sleeper,
    )
    if guard.get("package_guard_mismatch"):
        return _finalize_package_mismatch(
            account_id=safe_account_id,
            expected_username=safe_expected_username,
            expected_package_name=safe_package_name,
            actual_foreground_package=str(guard.get("actual_foreground_package") or ""),
            run_id=run_id,
            run_type=safe_run_type,
            device_id=safe_device_id,
            expected_app_instance_id=safe_expected_app_instance_id,
            adb_serial_masked=safe_adb_serial_masked,
            actions_taken=actions_taken,
            timings=timings,
            warnings=warnings,
            extra_metadata={
                "resume_mode": "consume_action" if consume_from_action else "stdin",
                "run_id": run_id,
                "expected_package_name": safe_package_name,
                "expected_app_instance_id": safe_expected_app_instance_id,
                "device_id": safe_device_id,
                "adb_serial_masked": safe_adb_serial_masked,
                **guard,
            },
            total_start=total_start,
            timer=timer,
            publisher=publisher,
            publish_enabled=publish_enabled,
            reason="resume_email_code_wrong_package",
        )

    observe_start = timer()
    initial_signals = _observe_login_signals(d, expected_username=safe_expected_username)
    timings["observe_ms"] += _elapsed_ms(observe_start, timer())
    password_screen_ready = _signals_indicate_password_entry_ready(initial_signals)

    if consume_from_action:
        consumed = consume_verification_code_for_worker(
            action_id=action_id,
            account_id=safe_account_id,
            run_id=run_id,
        )
        if not consumed.get("ok"):
            consume_reason = str(consumed.get("reason") or "verification_code_not_available")
            action_sync: dict[str, Any] | None = None
            if action_id:
                try:
                    action_sync = sync_verification_action_after_email_code_resume(
                        action_id=action_id,
                        account_id=safe_account_id,
                        run_id=run_id,
                        ok=False,
                        final_outcome="verification_pending",
                        failure_reason=consume_reason,
                    )
                except Exception:
                    warnings.append("verification_action_sync_failed_safe")
            return _finalize(
                ok=False,
                completed=False,
                final_outcome="verification_pending",
                reason=consume_reason,
                failure_reason=consume_reason,
                final_login_status="verification_pending",
                final_provisioning_status="login_verification_pending",
                final_onboarding_status="verification_pending",
                account_id=safe_account_id,
                expected_username=safe_expected_username,
                actions_taken=actions_taken,
                timings=timings,
                warnings=warnings,
                extra_metadata={
                    **resume_extra_base,
                    "verification_action_sync": action_sync,
                },
                total_start=total_start,
                timer=timer,
                publisher=publisher,
                publish_enabled=publish_enabled,
                dashboard_action_type="enter_email_verification_code",
                should_publish_status=False,
            )
        code_value = SecretValue(str(consumed.get("verification_code") or ""))

    if password_screen_ready and credentials_getter is not None:
        warnings.append("email_code_entry_skipped_password_screen_ready")
        actions_taken.append("route:post_email_code_password")
        return _submit_password_after_email_code(
            d,
            account_id=safe_account_id,
            expected_username=safe_expected_username,
            credentials_getter=credentials_getter,
            signals=initial_signals,
            actions_taken=actions_taken,
            timings=timings,
            warnings=warnings,
            total_start=total_start,
            timer=timer,
            sleeper=sleeper,
            publisher=publisher,
            publish_enabled=publish_enabled,
            package_name=safe_package_name,
            run_id=run_id,
            run_type=safe_run_type,
            device_id=safe_device_id,
            expected_app_instance_id=safe_expected_app_instance_id,
            adb_serial_masked=safe_adb_serial_masked,
            post_submit_timeout_ms=post_submit_timeout_ms,
            max_retry_attempts=max_retry_attempts,
            action_id=action_id,
            consume_from_action=consume_from_action,
            resume_extra_metadata={**resume_extra_base, "email_code_entry_skipped": True},
            connected_identity_verifier=identity_verifier,
        )

    resume_result = execute_email_code_challenge_resume(
        d,
        verification_code=code_value,
        post_submit_wait_ms=int(post_submit_timeout_ms or 0),
        timer=timer,
        sleeper=sleeper,
    )
    actions_taken.append("email_code_submit")
    email_code_metadata = {
        "email_code_result": {
            "executed": resume_result.executed,
            "code_entered": resume_result.code_entered,
            "continue_tapped": resume_result.continue_tapped,
            "post_submit_outcome": resume_result.post_submit_outcome,
            "post_submit_screen_type": resume_result.post_submit_screen_type,
        },
    }

    if resume_result.ok:
        identity_result = _verify_connected_identity_before_ready(
            d,
            verifier=identity_verifier,
            account_id=safe_account_id,
            expected_username=safe_expected_username,
            run_id=run_id,
            run_type=safe_run_type,
        )
        identity_metadata = _connected_identity_safe_metadata(identity_result)
        if identity_metadata.get("expected_identity_verified") is not True:
            identity_failure_reason = str(
                identity_metadata.get("identity_verification_failure_reason")
                or "expected_instagram_identity_not_verified"
            )
            action_sync: dict[str, Any] | None = None
            if consume_from_action and action_id:
                try:
                    action_sync = sync_verification_action_after_email_code_resume(
                        action_id=action_id,
                        account_id=safe_account_id,
                        run_id=run_id,
                        ok=False,
                        final_outcome="identity_verification_failed",
                        failure_reason=identity_failure_reason,
                        screen_type=str(resume_result.post_submit_screen_type or ""),
                    )
                except Exception:
                    warnings.append("verification_action_sync_failed_safe")
            return _finalize(
                ok=False,
                completed=True,
                final_outcome="identity_verification_failed",
                reason=identity_failure_reason,
                failure_reason=identity_failure_reason,
                final_login_status="verification_pending",
                final_provisioning_status="login_verification_pending",
                final_onboarding_status="verification_pending",
                account_id=safe_account_id,
                expected_username=safe_expected_username,
                actions_taken=[*actions_taken, "verify_connected_account_identity"],
                timings=_merge_timings(timings, resume_result.timings),
                warnings=[*warnings, *resume_result.warnings, "connected_identity_not_verified_safe"],
                extra_metadata={
                    **resume_extra_base,
                    **email_code_metadata,
                    **identity_metadata,
                    **({"verification_action_sync": action_sync} if action_sync else {}),
                },
                total_start=total_start,
                timer=timer,
                publisher=publisher,
                publish_enabled=publish_enabled,
                dashboard_action_type="enter_email_verification_code",
                should_publish_status=True,
            )
        action_sync: dict[str, Any] | None = None
        if consume_from_action and action_id:
            try:
                action_sync = sync_verification_action_after_email_code_resume(
                    action_id=action_id,
                    account_id=safe_account_id,
                    run_id=run_id,
                    ok=True,
                    final_outcome=str(resume_result.post_submit_outcome or "connected"),
                    failure_reason=None,
                    screen_type=str(resume_result.post_submit_screen_type or ""),
                )
            except Exception:
                warnings.append("verification_action_sync_failed_safe")
        outcome = str(resume_result.post_submit_outcome or "connected")
        classification = classify_login_probe_outcome(
            outcome,
            metadata={
                **(resume_result.safe_metadata or {}),
                "screen_type": resume_result.post_submit_screen_type,
            },
        )
        return _finalize(
            ok=True,
            completed=True,
            final_outcome=outcome,
            reason=resume_result.reason,
            failure_reason=None,
            final_login_status=classification.login_status,
            final_provisioning_status=classification.provisioning_status,
            final_onboarding_status=classification.onboarding_status,
            account_id=safe_account_id,
            expected_username=safe_expected_username,
            actions_taken=actions_taken,
            timings=_merge_timings(timings, resume_result.timings),
            warnings=[*warnings, *resume_result.warnings],
            extra_metadata={
                **resume_extra_base,
                **email_code_metadata,
                **identity_metadata,
                **({"verification_action_sync": action_sync} if action_sync else {}),
            },
            total_start=total_start,
            timer=timer,
            publisher=publisher,
            publish_enabled=publish_enabled,
            should_publish_status=classification.should_publish,
        )

    observe_start = timer()
    password_signals = _observe_login_signals(d, expected_username=safe_expected_username)
    timings["observe_ms"] += _elapsed_ms(observe_start, timer())
    outcome = str(resume_result.post_submit_outcome or resume_result.reason or "unknown")
    if resume_result.failure_reason and not resume_result.post_submit_outcome:
        outcome = "verification_pending" if resume_result.failure_reason.startswith("verification_code") else "unknown"
    should_submit_password = credentials_getter is not None and _should_chain_password_after_email_code_resume(
        resume_result,
        password_signals,
    )
    if should_submit_password:
        actions_taken.append("route:post_email_code_password")
        return _submit_password_after_email_code(
            d,
            account_id=safe_account_id,
            expected_username=safe_expected_username,
            credentials_getter=credentials_getter,
            signals=password_signals,
            actions_taken=actions_taken,
            timings=timings,
            warnings=[*warnings, *resume_result.warnings],
            total_start=total_start,
            timer=timer,
            sleeper=sleeper,
            publisher=publisher,
            publish_enabled=publish_enabled,
            package_name=safe_package_name,
            run_id=run_id,
            run_type=safe_run_type,
            device_id=safe_device_id,
            expected_app_instance_id=safe_expected_app_instance_id,
            adb_serial_masked=safe_adb_serial_masked,
            post_submit_timeout_ms=post_submit_timeout_ms,
            max_retry_attempts=max_retry_attempts,
            action_id=action_id,
            consume_from_action=consume_from_action,
            resume_extra_metadata={**resume_extra_base, **email_code_metadata},
            connected_identity_verifier=identity_verifier,
        )

    classification = classify_login_probe_outcome(
        outcome,
        metadata={
            **(resume_result.safe_metadata or {}),
            "screen_type": resume_result.post_submit_screen_type,
        },
    )
    dashboard_action_type = _dashboard_action_for_outcome(
        outcome,
        challenge_type=str((resume_result.safe_metadata or {}).get("challenge_type") or ""),
        post_submit_screen_type=str(resume_result.post_submit_screen_type or ""),
    )
    if not dashboard_action_type and outcome in {"unknown", "unsupported_post_submit_challenge"}:
        dashboard_action_type = "review_login_challenge"
    action_sync = None
    if consume_from_action and action_id:
        try:
            action_sync = sync_verification_action_after_email_code_resume(
                action_id=action_id,
                account_id=safe_account_id,
                run_id=run_id,
                ok=False,
                final_outcome=outcome,
                failure_reason=resume_result.failure_reason or outcome,
                screen_type=str(resume_result.post_submit_screen_type or password_signals.get("screen_type") or ""),
            )
        except Exception:
            warnings.append("verification_action_sync_failed_safe")
    return _finalize(
        ok=False,
        completed=bool(resume_result.executed),
        final_outcome=outcome,
        reason=resume_result.reason,
        failure_reason=resume_result.failure_reason or outcome,
        final_login_status=classification.login_status,
        final_provisioning_status=classification.provisioning_status,
        final_onboarding_status=classification.onboarding_status,
        account_id=safe_account_id,
        expected_username=safe_expected_username,
        actions_taken=actions_taken,
        timings=_merge_timings(timings, resume_result.timings),
        warnings=[*warnings, *resume_result.warnings],
        extra_metadata={
            **resume_extra_base,
            **email_code_metadata,
            **({"verification_action_sync": action_sync} if action_sync else {}),
            "post_email_code_password_submit": False,
            "post_email_code_screen_type": str(password_signals.get("screen_type") or ""),
        },
        total_start=total_start,
        timer=timer,
        publisher=publisher,
        publish_enabled=publish_enabled,
        dashboard_action_type=dashboard_action_type,
        should_publish_status=classification.should_publish or bool(dashboard_action_type),
    )
