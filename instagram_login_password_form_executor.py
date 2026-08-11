"""Controlled Instagram login form credential executor.

Entry 2E-5I fills username/password only after the caller has prevalidated the
screen as `login_form_empty` or a controlled password-only continuation screen.
It has no runner hook, no Supabase write, no status publish, no unbounded retry,
and never stores or logs the password.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import config
from device import (
    adb_available,
    ensure_adb_keyboard_ready,
    get_device_serial,
    is_fast_ime_available,
    run_adb_keyboard_b64_input_detailed as run_adb_keyboard_b64_input,
)
from instagram_credentials_runtime_access import (
    SecretValue,
    redact_credentials_payload,
    revealed_value_blocked_for_injection,
)
from instagram_login_status_classifier import LoginProbeOutcome, clean_login_probe_metadata
from instagram_login_ui_probe import extract_login_screen_signals_from_hierarchy, probe_login_ui_from_hierarchy


ACTION_LOGIN_FORM_SUBMIT = "login_form_submit"
ACTION_LOGIN_USERNAME_STEP_SUBMIT = "login_username_step_submit"
NO_ACTION = "no_action"
MAX_POST_SUBMIT_WAIT_MS = 3000
MAX_PASSWORD_REQUIRED_RETRY = 1
DEFAULT_POST_SUBMIT_OBSERVATIONS = 4
DEFAULT_POST_SUBMIT_INTERVAL_MS = 1000
DEFAULT_POST_SUBMIT_TIMEOUT_MS = 8000
MAX_POST_SUBMIT_OBSERVATIONS = 15
MAX_POST_SUBMIT_INTERVAL_MS = 1500
MAX_POST_SUBMIT_TIMEOUT_MS = 15000
MAX_SAVE_PASSWORD_PROMPT_DISMISS_ATTEMPTS = 2
VERIFICATION_CODE_SCREEN_TYPES = {
    "email_code_challenge",
    "sms_code_challenge",
    "whatsapp_code_challenge",
    "authenticator_app_code_challenge",
}
POST_SUBMIT_FINAL_RECHECK_OBSERVATIONS = 3
POST_SUBMIT_FINAL_RECHECK_INTERVAL_MS = 1000
POST_DISMISS_FINAL_OBSERVATIONS = 4
POST_DISMISS_FINAL_INTERVAL_MS = 1000
PASSWORD_CONFIRM_SETTLE_MS = 150
USERNAME_INPUT_FAILURE_REASONS = {
    "username_input_failed",
    "username_clear_failed",
    "username_still_prefilled_after_input",
    "username_field_not_found",
    "username_field_not_focusable",
    "username_prefilled_not_editable",
}
USERNAME_POST_INPUT_SETTLE_MS = 150
_USERNAME_PLACEHOLDER_PHRASES = {
    "username, email or mobile number",
    "phone number, username or email",
    "username",
    "email or mobile number",
    "nom d'utilisateur, e-mail ou numéro de mobile",
    "nom d'utilisateur, e-mail ou numero de mobile",
    "numéro de téléphone, nom d'utilisateur ou e-mail",
    "numero de telephone, nom d'utilisateur ou e-mail",
}

Timer = Callable[[], float]
Sleeper = Callable[[float], None]
ReturnedLoginFormRecovery = Callable[[], dict[str, Any]]


@dataclass(frozen=True)
class LoginPasswordExecutionResult:
    ok: bool
    executed: bool
    action: str
    reason: str
    failure_reason: str | None = None
    username_entered: bool = False
    password_entered: bool = False
    submit_tapped: bool = False
    post_submit_screen_type: str | None = None
    post_submit_probe_reason: str | None = None
    post_submit_outcome: str | None = None
    timings: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    safe_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LoginUsernameStepExecutionResult:
    ok: bool
    executed: bool
    reason: str
    failure_reason: str | None = None
    post_action_signals: dict[str, Any] = field(default_factory=dict)
    observations: tuple[str, ...] = ()
    safe_metadata: dict[str, Any] = field(default_factory=dict)


def advance_login_username_step(
    d: Any,
    *,
    expected_username: str,
    prevalidated_signals: dict | None,
    max_observations: int = 4,
    observation_interval_ms: int = 500,
    sleeper: Sleeper | None = None,
) -> LoginUsernameStepExecutionResult:
    """Advance a proven username-only login step without reading a secret."""
    sleeper = sleeper or time.sleep
    signals = dict(prevalidated_signals or {})
    if (
        signals.get("screen_type") != "login_form_username_step"
        or signals.get("has_username_field") is not True
        or signals.get("has_login_button") is not True
        or signals.get("has_password_field") is True
    ):
        return LoginUsernameStepExecutionResult(
            ok=False,
            executed=False,
            reason="username_step_not_validated",
            failure_reason="username_step_not_validated",
        )

    username = str(expected_username or "").strip()
    if not username:
        return LoginUsernameStepExecutionResult(
            ok=False,
            executed=False,
            reason="expected_username_missing",
            failure_reason="expected_username_missing",
        )

    username_target = _find_username_target(
        d,
        prefilled_username=str(signals.get("prefilled_username") or ""),
    )
    if username_target.get("failure_reason"):
        reason = str(username_target["failure_reason"])
        return LoginUsernameStepExecutionResult(False, False, reason, reason)
    submit_target = _find_unique_target(
        d,
        (
            {"text": "Log in"},
            {"description": "Log in"},
            {"text": "Continue"},
            {"description": "Continue"},
            {"text": "Se connecter"},
            {"description": "Se connecter"},
            {"text": "Continuer"},
            {"description": "Continuer"},
        ),
        missing_reason="login_button_not_found",
    )
    if submit_target.get("failure_reason"):
        reason = str(submit_target["failure_reason"])
        return LoginUsernameStepExecutionResult(False, False, reason, reason)

    warnings: list[str] = []
    username_result = _focus_clear_set_and_confirm_username(
        d,
        username_target["target"],
        username,
        prefilled_username_mode=bool(signals.get("prefilled_username")),
        prefilled_username=str(signals.get("prefilled_username") or ""),
        sleeper=sleeper,
        warnings=warnings,
    )
    if username_result.get("username_input_confirmed") == "false":
        reason = str(username_result.get("username_input_result") or "username_input_failed")
        return LoginUsernameStepExecutionResult(False, False, reason, reason)

    try:
        _click_target(submit_target["target"])
    except Exception:
        return LoginUsernameStepExecutionResult(
            ok=False,
            executed=False,
            reason="username_step_submit_failed",
            failure_reason="username_step_submit_failed",
        )

    observations: list[str] = []
    latest: dict[str, Any] = {}
    bounded_observations = _clamp_count(max_observations, 6)
    bounded_interval_ms = _clamp_ms(observation_interval_ms, 1500)
    for index in range(bounded_observations):
        if bounded_interval_ms > 0:
            sleeper(bounded_interval_ms / 1000.0)
        try:
            latest = extract_login_screen_signals_from_hierarchy(
                _dump_hierarchy_once(d),
                expected_username=username,
            )
        except Exception:
            latest = {"screen_type": "unknown"}
        screen_type = str(latest.get("screen_type") or "unknown")
        observations.append(screen_type)
        if screen_type in {
            "continue_password_only",
            "login_form_empty",
            "login_form_prefilled_username",
            *VERIFICATION_CODE_SCREEN_TYPES,
            "active_account_home",
            "active_account_profile",
        }:
            break

    final_screen = str(latest.get("screen_type") or "unknown")
    ok = final_screen in {
        "continue_password_only",
        "login_form_empty",
        "login_form_prefilled_username",
        *VERIFICATION_CODE_SCREEN_TYPES,
        "active_account_home",
        "active_account_profile",
    }
    reason = "username_step_advanced" if ok else "username_step_transition_not_reached"
    return LoginUsernameStepExecutionResult(
        ok=ok,
        executed=True,
        reason=reason,
        failure_reason=None if ok else reason,
        post_action_signals=latest,
        observations=tuple(observations),
        safe_metadata={
            "source": "login_username_step_executor",
            "username_input_confirmed": username_result.get("username_input_confirmed"),
            "username_input_result": username_result.get("username_input_result"),
            "observation_count": len(observations),
            "observed_screens": list(observations),
            "final_screen_type": final_screen,
            "secret_read": False,
            "credential_input_attempted": False,
        },
    )


def execute_login_form_credentials(
    d: Any,
    *,
    expected_username: str,
    password: SecretValue,
    prevalidated_signals: dict | None = None,
    post_submit_wait_ms: int = 1000,
    dump_after_submit: bool = True,
    max_password_required_retry: int = MAX_PASSWORD_REQUIRED_RETRY,
    max_post_submit_observations: int = DEFAULT_POST_SUBMIT_OBSERVATIONS,
    post_submit_observation_interval_ms: int = DEFAULT_POST_SUBMIT_INTERVAL_MS,
    post_submit_timeout_ms: Optional[int] = None,
    timer: Timer | None = None,
    sleeper: Sleeper | None = None,
) -> LoginPasswordExecutionResult:
    """Fill and submit one prevalidated Instagram login form.

    The password is revealed only after screen/field/button validation. On a
    full login form the username is entered first; on a password-only form the
    visible username has already been selected by the Continue flow.
    """

    timer = timer or time.perf_counter
    sleeper = sleeper or time.sleep
    total_start = timer()
    timings = _empty_timings()
    warnings: list[str] = []
    wait_ms = _clamp_ms(post_submit_wait_ms, MAX_POST_SUBMIT_WAIT_MS)
    if wait_ms != post_submit_wait_ms:
        warnings.append("post_submit_wait_ms_clamped")
    observation_limit = _clamp_count(max_post_submit_observations, MAX_POST_SUBMIT_OBSERVATIONS)
    observation_interval_ms = _clamp_ms(post_submit_observation_interval_ms, MAX_POST_SUBMIT_INTERVAL_MS)
    timeout_ms = _clamp_ms(post_submit_timeout_ms, MAX_POST_SUBMIT_TIMEOUT_MS) if post_submit_timeout_ms is not None else 0
    if timeout_ms > 0:
        observation_limit = _observation_count_for_timeout(timeout_ms, observation_interval_ms)

    username = str(expected_username or "").strip()
    if not username:
        return _failure(
            "expected_username_missing",
            timings=timings,
            warnings=warnings,
            total_start=total_start,
            timer=timer,
            expected_username=username,
        )

    if not isinstance(password, SecretValue):
        return _failure(
            "password_secret_invalid",
            timings=timings,
            warnings=warnings,
            total_start=total_start,
            timer=timer,
            expected_username=username,
        )

    signal_failure = _prevalidated_signal_failure(prevalidated_signals)
    if signal_failure:
        return _failure(
            signal_failure,
            timings=timings,
            warnings=warnings,
            total_start=total_start,
            timer=timer,
            expected_username=username,
        )

    password_only_mode = _is_password_only_mode(prevalidated_signals)
    prefilled_username_mode = _is_prefilled_username_mode(prevalidated_signals)
    overlay_recovery_allowed = _overlay_recovery_allowed(prevalidated_signals)
    password_required_retry_count = 0
    password_required_dialog_detected = False
    password_required_retry_attempted = False
    password_refill_attempted = False
    second_submit_executed = False
    password_input_method_used = ""
    password_field_target_kind = ""
    password_input_method = ""
    password_input_result = ""
    password_confirm_method = ""
    password_field_focused_before_input: bool | None = None
    input_call_reported_success = False
    password_field_non_empty_confirmed = "unknown"
    password_input_failure_reason = ""
    username_replaced = False
    username_input_confirmed = "unknown"
    username_input_result = "not_required" if password_only_mode else ""
    username_field_focused_before_input: bool | None = None
    username_clear_method = ""
    username_input_method = ""
    username_placeholder_ignored = False

    start = timer()
    targets = _resolve_login_form_targets(
        d,
        password_only_mode=password_only_mode,
        prefilled_username=str((prevalidated_signals or {}).get("prefilled_username") or ""),
        password_field_proof=str((prevalidated_signals or {}).get("password_field_proof") or ""),
    )
    timings["target_lookup_ms"] = _elapsed_ms(start, timer())
    if targets["failure_reason"]:
        return _failure(
            targets["failure_reason"],
            timings=timings,
            warnings=warnings,
            total_start=total_start,
            timer=timer,
            expected_username=username,
        )

    username_entered = False
    password_entered = False
    submit_tapped = False

    username_input_started = timer()
    try:
        if not password_only_mode:
            username_result = _focus_clear_set_and_confirm_username(
                d,
                targets["username"],
                username,
                prefilled_username_mode=prefilled_username_mode,
                prefilled_username=str((prevalidated_signals or {}).get("prefilled_username") or ""),
                sleeper=sleeper,
                warnings=warnings,
            )
            username_replaced = bool(username_result["username_replaced"])
            username_input_confirmed = str(username_result["username_input_confirmed"])
            username_input_result = str(username_result["username_input_result"])
            username_field_focused_before_input = username_result.get("username_field_focused_before_input")
            username_clear_method = str(username_result.get("username_clear_method") or "")
            username_input_method = str(username_result.get("username_input_method") or "")
            username_placeholder_ignored = bool(username_result.get("username_placeholder_ignored"))
            username_entered = username_input_result in {"username_input_confirmed", "username_input_assumed"}
            if username_entered:
                warnings.append("username_field_fill_sent")
            if not username_entered:
                raise RuntimeError(username_input_result or "username_input_failed")

        try:
            revealed_password = password.reveal_for_login_executor()
        except Exception:
            raise RuntimeError("password_secret_invalid")
        if not isinstance(revealed_password, str):
            raise RuntimeError("password_secret_invalid")
        if not revealed_password:
            raise RuntimeError("password_secret_missing")
        if revealed_value_blocked_for_injection(revealed_password):
            raise RuntimeError("blocked_secret_payload_shape")

        start = timer()
        input_result = _input_password_robust(d, targets["password"], revealed_password, warnings)
        password_input_method_used = input_result["input_method_used"]
        password_input_method = str(input_result.get("password_input_method") or password_input_method_used)
        password_input_result = str(input_result.get("password_input_result") or "")
        password_field_target_kind = str(input_result.get("password_field_target_kind") or "")
        password_confirm_method = str(input_result.get("password_confirm_method") or "")
        password_field_focused_before_input = input_result["password_field_focused_before_input"]
        input_call_reported_success = input_result["input_call_reported_success"]
        password_field_non_empty_confirmed = input_result["password_field_non_empty_confirmed"]
        password_input_failure_reason = input_result["reason"]
        password_entered = bool(input_call_reported_success)
        if password_entered:
            warnings.append("password_field_fill_sent")
        timings["password_input_ms"] = _elapsed_ms(start, timer())
        if not input_call_reported_success:
            raise RuntimeError("password_input_failed")
    except Exception as exc:
        failure_reason = str(exc) if str(exc) in {
            "input_failed",
            *USERNAME_INPUT_FAILURE_REASONS,
            "password_secret_invalid",
            "password_secret_missing",
            "blocked_secret_payload_shape",
        } else (password_input_failure_reason or username_input_result or "input_failed")
        timings["username_input_ms"] = _elapsed_ms(username_input_started, timer())
        timings["total_ms"] = _elapsed_ms(total_start, timer())
        return _result(
            ok=False,
            executed=False,
            action=ACTION_LOGIN_FORM_SUBMIT,
            reason=failure_reason,
            failure_reason=failure_reason,
            username_entered=username_entered,
            password_entered=password_entered,
            timings=timings,
            warnings=warnings,
            expected_username=username,
            password_only_mode=password_only_mode,
            input_method_used=password_input_method_used,
            password_field_target_kind=password_field_target_kind,
            password_input_method=password_input_method,
            password_input_result=password_input_result,
            password_confirm_method=password_confirm_method,
            password_field_focused_before_input=password_field_focused_before_input,
            input_action_reported_success=input_call_reported_success,
            password_field_non_empty_confirmed=password_field_non_empty_confirmed,
            username_replaced=username_replaced,
            username_input_confirmed=username_input_confirmed,
            username_input_result=username_input_result or failure_reason,
            username_field_focused_before_input=username_field_focused_before_input,
            username_clear_method=username_clear_method,
            username_input_method=username_input_method,
            username_placeholder_ignored=username_placeholder_ignored,
            password_submit_result="blocked_secret_payload_shape" if failure_reason == "blocked_secret_payload_shape" else None,
        )
    finally:
        if not password_only_mode:
            timings["username_input_ms"] = _elapsed_ms(username_input_started, timer())

    if password_field_non_empty_confirmed == "false":
        timings["total_ms"] = _elapsed_ms(total_start, timer())
        return _result(
            ok=False,
            executed=False,
            action=ACTION_LOGIN_FORM_SUBMIT,
            reason="password_input_not_confirmed",
            failure_reason="password_input_not_confirmed",
            username_entered=username_entered,
            password_entered=password_entered,
            timings=timings,
            warnings=warnings,
            expected_username=username,
            password_only_mode=password_only_mode,
            input_method_used=password_input_method_used,
            password_field_target_kind=password_field_target_kind,
            password_input_method=password_input_method,
            password_input_result=password_input_result,
            password_confirm_method=password_confirm_method,
            password_field_focused_before_input=password_field_focused_before_input,
            input_action_reported_success=input_call_reported_success,
            password_field_non_empty_confirmed=password_field_non_empty_confirmed,
            username_replaced=username_replaced,
            username_input_confirmed=username_input_confirmed,
            username_input_result=username_input_result,
        )

    fresh_submit = _resolve_fresh_login_submit_target(
        d,
        expected_username=username,
        password_only_mode=password_only_mode,
        prefilled_username=str((prevalidated_signals or {}).get("prefilled_username") or ""),
        password_field_proof=str((prevalidated_signals or {}).get("password_field_proof") or ""),
        sleeper=sleeper,
        warnings=warnings,
    )
    if fresh_submit["failure_reason"]:
        timings["total_ms"] = _elapsed_ms(total_start, timer())
        return _result(
            ok=False,
            executed=False,
            action=ACTION_LOGIN_FORM_SUBMIT,
            reason=str(fresh_submit["failure_reason"]),
            failure_reason=str(fresh_submit["failure_reason"]),
            username_entered=username_entered,
            password_entered=password_entered,
            submit_tapped=False,
            timings=timings,
            warnings=warnings,
            expected_username=username,
            password_only_mode=password_only_mode,
            input_method_used=password_input_method_used,
            password_field_target_kind=password_field_target_kind,
            password_input_method=password_input_method,
            password_input_result=password_input_result,
            password_confirm_method=password_confirm_method,
            password_field_focused_before_input=password_field_focused_before_input,
            input_action_reported_success=input_call_reported_success,
            password_field_non_empty_confirmed=password_field_non_empty_confirmed,
            username_replaced=username_replaced,
            username_input_confirmed=username_input_confirmed,
            username_input_result=username_input_result,
        )
    targets = {**targets, **fresh_submit["targets"]}

    try:
        start = timer()
        _click_target(targets["login_button"])
        submit_tapped = True
        warnings.append("login_submit_tap_sent")
        timings["submit_tap_ms"] = _elapsed_ms(start, timer())
    except Exception:
        if overlay_recovery_allowed and _safe_overlay_recovery_once(d, targets, warnings):
            try:
                start = timer()
                _click_target(targets["login_button"])
                submit_tapped = True
                timings["submit_tap_ms"] += _elapsed_ms(start, timer())
            except Exception:
                timings["total_ms"] = _elapsed_ms(total_start, timer())
                return _result(
                    ok=False,
                    executed=False,
                    action=ACTION_LOGIN_FORM_SUBMIT,
                    reason="submit_failed",
                    failure_reason="submit_failed",
                    username_entered=username_entered,
                    password_entered=password_entered,
                    submit_tapped=submit_tapped,
                    timings=timings,
                    warnings=warnings,
                    expected_username=username,
                    password_only_mode=password_only_mode,
                    username_replaced=username_replaced,
                    username_input_confirmed=username_input_confirmed,
                    username_input_result=username_input_result,
                )
        else:
            timings["total_ms"] = _elapsed_ms(total_start, timer())
            return _result(
                ok=False,
                executed=False,
                action=ACTION_LOGIN_FORM_SUBMIT,
                reason="submit_failed",
                failure_reason="submit_failed",
                username_entered=username_entered,
                password_entered=password_entered,
                submit_tapped=submit_tapped,
                timings=timings,
                warnings=warnings,
                expected_username=username,
                password_only_mode=password_only_mode,
                username_replaced=username_replaced,
                username_input_confirmed=username_input_confirmed,
                username_input_result=username_input_result,
            )

    timings["post_submit_wait_ms"] = wait_ms

    post_submit_screen_type = "unknown"
    post_submit_probe_reason = "post_submit_dump_skipped"
    post_submit_outcome = "unknown"
    post_submit_observation_count = 0
    post_submit_wait_total_ms = 0
    post_submit_screens: list[str] = []
    final_terminal_screen = ""
    save_password_prompt_detected = False
    save_password_prompt_dismissed = False
    save_password_prompt_dismiss_method = ""
    save_password_prompt_dismiss_attempt_count = 0
    samsung_pass_save_password_prompt_detected = False
    samsung_pass_save_password_prompt_cancelled = False
    instagram_save_login_info_prompt_detected = False
    instagram_save_login_info_prompt_not_now = False
    post_login_location_services_prompt_detected = False
    post_login_location_services_prompt_dismissed = False
    post_login_location_services_prompt_dismiss_method = ""
    notifications_prompt_detected = False
    notifications_next_tap_sent = False
    notifications_skip_tap_sent = False
    notifications_skip_after_settings_sent = False
    android_notification_settings_detected = False
    android_back_from_notification_settings_sent = False
    post_dismiss_screen_type = ""
    post_dismiss_final_observation_count = 0
    post_dismiss_final_screens: list[str] = []
    post_dismiss_final_wait_total_ms = 0
    post_dismiss_final_screen_type = ""
    connected_detected_after_save_prompt_dismiss = False
    post_submit_loading_timeout = False
    post_submit_challenge_type = ""
    post_submit_masked_email_present = False
    email_code_challenge_detected = False
    verification_code_challenge_detected = False
    failure_reason: str | None = None

    if dump_after_submit:
        try:
            warnings.append("post_submit_observe_started")
            def _recover_returned_login_form_once() -> dict[str, Any]:
                nonlocal password_refill_attempted, second_submit_executed
                recovery = _resolve_fresh_login_submit_target(
                    d,
                    expected_username=username,
                    password_only_mode=password_only_mode,
                    prefilled_username=str((prevalidated_signals or {}).get("prefilled_username") or ""),
                    password_field_proof=str((prevalidated_signals or {}).get("password_field_proof") or ""),
                    sleeper=sleeper,
                    warnings=warnings,
                )
                if recovery["failure_reason"]:
                    return recovery
                recovery_targets = dict(recovery["targets"])
                password_state = _password_field_non_empty_state(recovery_targets["password"])
                if password_state == "false":
                    password_refill_attempted = True
                    refill = _input_password_robust(
                        d,
                        recovery_targets["password"],
                        revealed_password,
                        warnings,
                    )
                    if not _password_injection_confirmed(refill):
                        return {"failure_reason": "password_input_not_confirmed"}
                    recovery = _resolve_fresh_login_submit_target(
                        d,
                        expected_username=username,
                        password_only_mode=password_only_mode,
                        prefilled_username=str((prevalidated_signals or {}).get("prefilled_username") or ""),
                        password_field_proof=str((prevalidated_signals or {}).get("password_field_proof") or ""),
                        sleeper=sleeper,
                        warnings=warnings,
                    )
                    if recovery["failure_reason"]:
                        return recovery
                    recovery_targets = dict(recovery["targets"])
                _click_target(recovery_targets["login_button"])
                second_submit_executed = True
                warnings.append("login_submit_returned_form_recovery_tap_sent")
                return {"failure_reason": "", "executed": True}

            observed = _observe_post_submit_settled(
                d,
                timings=timings,
                warnings=warnings,
                timer=timer,
                sleeper=sleeper,
                initial_wait_ms=wait_ms,
                interval_ms=observation_interval_ms,
                max_observations=observation_limit,
                returned_login_form_recovery=_recover_returned_login_form_once,
            )
            post_submit_observation_count = int(observed.get("observation_count") or 0)
            post_submit_wait_total_ms = int(observed.get("wait_total_ms") or 0)
            post_submit_screens = list(observed.get("screens") or [])
            final_terminal_screen = str(observed.get("final_terminal_screen") or "")
            save_password_prompt_detected = bool(observed.get("save_password_prompt_detected"))
            save_password_prompt_dismissed = bool(observed.get("save_password_prompt_dismissed"))
            save_password_prompt_dismiss_method = str(observed.get("dismiss_method") or "")
            save_password_prompt_dismiss_attempt_count = int(
                observed.get("save_password_prompt_dismiss_attempt_count") or 0
            )
            samsung_pass_save_password_prompt_detected = bool(
                observed.get("samsung_pass_save_password_prompt_detected")
            )
            samsung_pass_save_password_prompt_cancelled = bool(
                observed.get("samsung_pass_save_password_prompt_cancelled")
            )
            instagram_save_login_info_prompt_detected = bool(
                observed.get("instagram_save_login_info_prompt_detected")
            )
            instagram_save_login_info_prompt_not_now = bool(observed.get("instagram_save_login_info_prompt_not_now"))
            post_login_location_services_prompt_detected = bool(
                observed.get("post_login_location_services_prompt_detected")
            )
            post_login_location_services_prompt_dismissed = bool(
                observed.get("post_login_location_services_prompt_dismissed")
            )
            post_login_location_services_prompt_dismiss_method = str(
                observed.get("post_login_location_services_prompt_dismiss_method") or ""
            )
            notifications_prompt_detected = bool(observed.get("notifications_prompt_detected"))
            notifications_next_tap_sent = bool(observed.get("notifications_next_tap_sent"))
            notifications_skip_tap_sent = bool(observed.get("notifications_skip_tap_sent"))
            notifications_skip_after_settings_sent = bool(observed.get("notifications_skip_after_settings_sent"))
            android_notification_settings_detected = bool(observed.get("android_notification_settings_detected"))
            android_back_from_notification_settings_sent = bool(
                observed.get("android_back_from_notification_settings_sent")
            )
            post_dismiss_screen_type = str(observed.get("post_dismiss_screen_type") or "")
            post_dismiss_final_observation_count = int(observed.get("post_dismiss_final_observation_count") or 0)
            post_dismiss_final_screens = list(observed.get("post_dismiss_final_screens") or [])
            post_dismiss_final_wait_total_ms = int(observed.get("post_dismiss_final_wait_total_ms") or 0)
            post_dismiss_final_screen_type = str(observed.get("post_dismiss_final_screen_type") or "")
            connected_detected_after_save_prompt_dismiss = bool(
                observed.get("connected_detected_after_save_prompt_dismiss")
            )
            post_submit_loading_timeout = bool(observed.get("post_submit_loading_timeout"))
            email_code_challenge_detected = bool(observed.get("email_code_challenge_detected"))
            verification_code_challenge_detected = bool(
                observed.get("verification_code_challenge_detected")
            )
            post_submit_challenge_type = str(observed.get("challenge_type") or "")
            post_submit_masked_email_present = bool(observed.get("masked_email_present"))
            password_required_dialog_detected = observed["password_required_dialog_present"]
            if password_required_dialog_detected and max(0, int(max_password_required_retry or 0)) > 0:
                password_required_retry_attempted = True
                password_required_retry_count = 1
                warnings.append("password_required_dialog_retry_once")
                if _tap_ok_once(d):
                    try:
                        password_refill_attempted = True
                        start = timer()
                        input_result = _input_password_robust(d, targets["password"], revealed_password, warnings)
                        password_input_method_used = input_result["input_method_used"]
                        password_field_focused_before_input = input_result["password_field_focused_before_input"]
                        input_call_reported_success = input_result["input_call_reported_success"]
                        password_field_non_empty_confirmed = input_result["password_field_non_empty_confirmed"]
                        password_input_failure_reason = input_result["reason"]
                        timings["password_input_ms"] += _elapsed_ms(start, timer())
                        if not input_call_reported_success or password_field_non_empty_confirmed == "false":
                            failure_reason = "password_input_not_confirmed"
                            post_submit_outcome = "password_input_failed"
                            post_submit_screen_type = "password_input_failed"
                            post_submit_probe_reason = password_input_failure_reason or "password_input_not_confirmed"
                        else:
                            start = timer()
                            _click_target(targets["login_button"])
                            second_submit_executed = True
                            submit_tapped = True
                            timings["submit_tap_ms"] += _elapsed_ms(start, timer())
                            observed = _observe_post_submit_settled(
                                d,
                                timings=timings,
                                warnings=warnings,
                                timer=timer,
                                sleeper=sleeper,
                                initial_wait_ms=observation_interval_ms,
                                interval_ms=observation_interval_ms,
                                max_observations=observation_limit,
                            )
                            post_submit_observation_count += int(observed.get("observation_count") or 0)
                            post_submit_wait_total_ms += int(observed.get("wait_total_ms") or 0)
                            post_submit_screens.extend(list(observed.get("screens") or []))
                            final_terminal_screen = str(observed.get("final_terminal_screen") or final_terminal_screen)
                            save_password_prompt_detected = save_password_prompt_detected or bool(
                                observed.get("save_password_prompt_detected")
                            )
                            save_password_prompt_dismissed = save_password_prompt_dismissed or bool(
                                observed.get("save_password_prompt_dismissed")
                            )
                            save_password_prompt_dismiss_method = (
                                str(observed.get("dismiss_method") or "") or save_password_prompt_dismiss_method
                            )
                            save_password_prompt_dismiss_attempt_count += int(
                                observed.get("save_password_prompt_dismiss_attempt_count") or 0
                            )
                            samsung_pass_save_password_prompt_detected = (
                                samsung_pass_save_password_prompt_detected
                                or bool(observed.get("samsung_pass_save_password_prompt_detected"))
                            )
                            samsung_pass_save_password_prompt_cancelled = (
                                samsung_pass_save_password_prompt_cancelled
                                or bool(observed.get("samsung_pass_save_password_prompt_cancelled"))
                            )
                            instagram_save_login_info_prompt_detected = (
                                instagram_save_login_info_prompt_detected
                                or bool(observed.get("instagram_save_login_info_prompt_detected"))
                            )
                            instagram_save_login_info_prompt_not_now = (
                                instagram_save_login_info_prompt_not_now
                                or bool(observed.get("instagram_save_login_info_prompt_not_now"))
                            )
                            post_login_location_services_prompt_detected = (
                                post_login_location_services_prompt_detected
                                or bool(observed.get("post_login_location_services_prompt_detected"))
                            )
                            post_login_location_services_prompt_dismissed = (
                                post_login_location_services_prompt_dismissed
                                or bool(observed.get("post_login_location_services_prompt_dismissed"))
                            )
                            post_login_location_services_prompt_dismiss_method = (
                                str(observed.get("post_login_location_services_prompt_dismiss_method") or "")
                                or post_login_location_services_prompt_dismiss_method
                            )
                            notifications_prompt_detected = notifications_prompt_detected or bool(
                                observed.get("notifications_prompt_detected")
                            )
                            notifications_next_tap_sent = notifications_next_tap_sent or bool(
                                observed.get("notifications_next_tap_sent")
                            )
                            notifications_skip_tap_sent = notifications_skip_tap_sent or bool(
                                observed.get("notifications_skip_tap_sent")
                            )
                            notifications_skip_after_settings_sent = notifications_skip_after_settings_sent or bool(
                                observed.get("notifications_skip_after_settings_sent")
                            )
                            android_notification_settings_detected = android_notification_settings_detected or bool(
                                observed.get("android_notification_settings_detected")
                            )
                            android_back_from_notification_settings_sent = (
                                android_back_from_notification_settings_sent
                                or bool(observed.get("android_back_from_notification_settings_sent"))
                            )
                            post_dismiss_screen_type = (
                                str(observed.get("post_dismiss_screen_type") or "") or post_dismiss_screen_type
                            )
                            post_dismiss_final_observation_count += int(
                                observed.get("post_dismiss_final_observation_count") or 0
                            )
                            post_dismiss_final_screens.extend(list(observed.get("post_dismiss_final_screens") or []))
                            post_dismiss_final_wait_total_ms += int(
                                observed.get("post_dismiss_final_wait_total_ms") or 0
                            )
                            post_dismiss_final_screen_type = (
                                str(observed.get("post_dismiss_final_screen_type") or "")
                                or post_dismiss_final_screen_type
                            )
                            connected_detected_after_save_prompt_dismiss = (
                                connected_detected_after_save_prompt_dismiss
                                or bool(observed.get("connected_detected_after_save_prompt_dismiss"))
                            )
                            post_submit_loading_timeout = post_submit_loading_timeout or bool(
                                observed.get("post_submit_loading_timeout")
                            )
                            email_code_challenge_detected = email_code_challenge_detected or bool(
                                observed.get("email_code_challenge_detected")
                            )
                            verification_code_challenge_detected = (
                                verification_code_challenge_detected
                                or bool(observed.get("verification_code_challenge_detected"))
                            )
                            post_submit_challenge_type = (
                                str(observed.get("challenge_type") or "") or post_submit_challenge_type
                            )
                            post_submit_masked_email_present = post_submit_masked_email_present or bool(
                                observed.get("masked_email_present")
                            )
                            if observed["password_required_dialog_present"]:
                                failure_reason = "password_input_failed"
                                post_submit_outcome = "password_input_failed"
                                post_submit_screen_type = "password_required_dialog"
                                post_submit_probe_reason = "password_required_dialog_reappeared"
                            else:
                                post_submit_outcome = observed["outcome"]
                                post_submit_screen_type = observed["screen_type"]
                                post_submit_probe_reason = observed["reason"]
                    except Exception:
                        failure_reason = "password_input_failed"
                        post_submit_outcome = "password_input_failed"
                        post_submit_screen_type = "password_input_failed"
                        post_submit_probe_reason = "password_required_retry_failed"
                else:
                    failure_reason = "password_input_failed"
                    post_submit_outcome = "password_input_failed"
                    post_submit_screen_type = "password_required_dialog"
                    post_submit_probe_reason = "password_required_ok_not_found"
            elif password_required_dialog_detected:
                failure_reason = "password_input_missing_or_not_accepted"
                post_submit_outcome = "password_input_missing_or_not_accepted"
                post_submit_screen_type = "password_required_dialog"
                post_submit_probe_reason = "password_required_dialog"
            else:
                post_submit_outcome = observed["outcome"]
                post_submit_screen_type = observed["screen_type"]
                post_submit_probe_reason = observed["reason"]
        except Exception:
            timings["post_submit_dump_ms"] = _elapsed_ms(start, timer())
            post_submit_probe_reason = "post_submit_dump_failed"
            post_submit_outcome = "unknown"
            post_submit_screen_type = "unknown"
            failure_reason = "post_submit_dump_failed"

    timings["total_ms"] = _elapsed_ms(total_start, timer())
    return _result(
        ok=failure_reason is None,
        executed=True,
        action=ACTION_LOGIN_FORM_SUBMIT,
        reason="login_form_submitted" if failure_reason is None else failure_reason,
        failure_reason=failure_reason,
        username_entered=username_entered,
        password_entered=password_entered,
        submit_tapped=submit_tapped,
        post_submit_screen_type=post_submit_screen_type,
        post_submit_probe_reason=post_submit_probe_reason,
        post_submit_outcome=post_submit_outcome,
        timings=timings,
        warnings=warnings,
        expected_username=username,
        password_only_mode=password_only_mode,
        input_method_used=password_input_method_used,
        password_field_target_kind=password_field_target_kind,
        password_input_method=password_input_method,
        password_input_result=password_input_result,
        password_confirm_method=password_confirm_method,
        password_field_focused_before_input=password_field_focused_before_input,
        input_action_reported_success=input_call_reported_success,
        password_field_non_empty_confirmed=password_field_non_empty_confirmed,
        password_required_dialog_detected=password_required_dialog_detected,
        password_required_retry_attempted=password_required_retry_attempted,
        password_required_retry_count=password_required_retry_count,
        password_refill_attempted=password_refill_attempted,
        second_submit_executed=second_submit_executed,
        post_submit_observation_count=post_submit_observation_count,
        post_submit_wait_total_ms=post_submit_wait_total_ms,
        post_submit_screens=post_submit_screens,
        final_terminal_screen=final_terminal_screen,
        post_submit_timeout_ms=timeout_ms,
        post_submit_interval_ms=observation_interval_ms,
        post_submit_loading_timeout=post_submit_loading_timeout,
        email_code_challenge_detected=email_code_challenge_detected,
        verification_code_challenge_detected=verification_code_challenge_detected,
        challenge_type=post_submit_challenge_type,
        masked_email_present=post_submit_masked_email_present,
        save_password_prompt_detected=save_password_prompt_detected,
        save_password_prompt_dismissed=save_password_prompt_dismissed,
        save_password_prompt_dismiss_attempt_count=save_password_prompt_dismiss_attempt_count,
        save_password_prompt_dismiss_method=save_password_prompt_dismiss_method,
        samsung_pass_save_password_prompt_detected=samsung_pass_save_password_prompt_detected,
        samsung_pass_save_password_prompt_cancelled=samsung_pass_save_password_prompt_cancelled,
        instagram_save_login_info_prompt_detected=instagram_save_login_info_prompt_detected,
        instagram_save_login_info_prompt_not_now=instagram_save_login_info_prompt_not_now,
        post_login_location_services_prompt_detected=post_login_location_services_prompt_detected,
        post_login_location_services_prompt_dismissed=post_login_location_services_prompt_dismissed,
        post_login_location_services_prompt_dismiss_method=post_login_location_services_prompt_dismiss_method,
        notifications_prompt_detected=notifications_prompt_detected,
        notifications_next_tap_sent=notifications_next_tap_sent,
        notifications_skip_tap_sent=notifications_skip_tap_sent,
        notifications_skip_after_settings_sent=notifications_skip_after_settings_sent,
        android_notification_settings_detected=android_notification_settings_detected,
        android_back_from_notification_settings_sent=android_back_from_notification_settings_sent,
        post_dismiss_screen_type=post_dismiss_screen_type,
        post_dismiss_final_observation_count=post_dismiss_final_observation_count,
        post_dismiss_final_screens=post_dismiss_final_screens,
        post_dismiss_final_wait_total_ms=post_dismiss_final_wait_total_ms,
        post_dismiss_final_screen_type=post_dismiss_final_screen_type,
        connected_detected_after_save_prompt_dismiss=connected_detected_after_save_prompt_dismiss,
        username_replaced=username_replaced,
        username_input_confirmed=username_input_confirmed,
        username_input_result=username_input_result,
        username_field_focused_before_input=username_field_focused_before_input,
        username_clear_method=username_clear_method,
        username_input_method=username_input_method,
        username_placeholder_ignored=username_placeholder_ignored,
    )


def _prevalidated_signal_failure(signals: dict | None) -> str:
    if not isinstance(signals, dict) or signals.get("screen_type") not in {
        "login_form_empty",
        "login_form_prefilled_username",
        "continue_password_only",
    }:
        return "login_form_not_validated"
    if signals.get("ambiguous_login_form") is True or signals.get("ambiguous") is True:
        return "ambiguous_login_form"
    if signals.get("screen_type") == "login_form_empty" and signals.get("has_username_field") is not True:
        return "username_field_not_found"
    if signals.get("screen_type") == "login_form_prefilled_username":
        if signals.get("username_field_editable_present") is not True and signals.get("username_editable_present") is not True:
            return "username_prefilled_not_editable"
        if not signals.get("prefilled_username"):
            return "username_field_not_found"
    if signals.get("screen_type") == "continue_password_only" and not signals.get("suggested_username"):
        return "expected_username_missing"
    if signals.get("has_password_field") is not True:
        return "password_field_not_found"
    if signals.get("has_login_button") is not True:
        return "login_button_not_found"
    return ""


def _resolve_login_form_targets(
    d: Any,
    *,
    password_only_mode: bool = False,
    prefilled_username: str = "",
    password_field_proof: str = "",
) -> dict[str, Any]:
    username = _find_username_target(d, prefilled_username=prefilled_username)
    if username["failure_reason"] and not (password_only_mode and username["failure_reason"] == "username_field_not_found"):
        return {"failure_reason": username["failure_reason"]}

    password = _find_password_target(
        d,
        password_only_mode=password_only_mode,
        password_field_proof=password_field_proof,
    )
    if password["failure_reason"]:
        return {"failure_reason": password["failure_reason"]}

    login_button = _find_unique_target(
        d,
        (
            {"text": "Log in"},
            {"description": "Log in"},
        ),
        missing_reason="login_button_not_found",
    )
    if login_button["failure_reason"]:
        return {"failure_reason": login_button["failure_reason"]}

    return {
        "failure_reason": "",
        "username": username["target"],
        "password": password["target"],
        "login_button": login_button["target"],
    }


def _find_username_target(d: Any, *, prefilled_username: str = "") -> dict[str, Any]:
    safe_prefilled = str(prefilled_username or "").strip()
    if safe_prefilled:
        edit_text = _find_username_edit_text_target(d)
        if edit_text["target"] is not None:
            return edit_text

    if not safe_prefilled:
        edit_text = _find_username_edit_text_target(d)
        if edit_text["target"] is not None:
            return edit_text

    selectors: list[dict[str, str]] = [
        {"text": "Username, email or mobile number"},
        {"description": "Username, email or mobile number"},
        {"text": "Username"},
        {"description": "Username"},
    ]
    if safe_prefilled:
        selectors.extend(
            (
                {"text": safe_prefilled},
                {"description": safe_prefilled},
            )
        )
    text_match = _find_unique_target(
        d,
        tuple(selectors),
        missing_reason="username_field_not_found",
    )
    if text_match["target"] is not None or text_match["failure_reason"] == "ambiguous_login_form":
        return text_match

    return _find_username_edit_text_target(d)


def _find_username_edit_text_target(d: Any) -> dict[str, Any]:
    try:
        selector = d(className="android.widget.EditText")
    except Exception:
        return {"target": None, "failure_reason": "username_field_not_found"}

    candidates: list[Any] = []
    all_method = getattr(selector, "all", None)
    if callable(all_method):
        try:
            candidates = [item for item in all_method() if item is not None]
        except Exception:
            candidates = []

    if not candidates:
        count = _selector_count(selector)
        if count == 1:
            candidates = [selector]
        elif count > 1:
            index_getter = getattr(selector, "__getitem__", None)
            if callable(index_getter):
                try:
                    candidates = [index_getter(0)]
                except Exception:
                    candidates = [selector]

    if not candidates:
        return {"target": None, "failure_reason": "username_field_not_found"}
    if len(candidates) > 2:
        return {"target": None, "failure_reason": "ambiguous_login_form"}
    return {"target": candidates[0], "failure_reason": ""}


def _find_unique_target(
    d: Any,
    selectors: tuple[dict[str, str], ...],
    *,
    missing_reason: str,
) -> dict[str, Any]:
    matches: list[Any] = []
    for selector_kwargs in selectors:
        try:
            selector = d(**selector_kwargs)
        except Exception:
            continue
        count = _selector_count(selector)
        if count > 1:
            if matches:
                continue
            return {"target": None, "failure_reason": "ambiguous_login_form"}
        if count == 1:
            matches.append(selector)
    if not matches:
        return {"target": None, "failure_reason": missing_reason}
    return {"target": matches[0], "failure_reason": ""}


def _find_password_target(
    d: Any,
    *,
    password_only_mode: bool,
    password_field_proof: str = "",
) -> dict[str, Any]:
    hierarchy_proof = str(password_field_proof or "").strip() in {
        "android_password_property",
        "android_input_type",
        "resource_id",
        "localized_accessibility_label",
        "masked_editable_value",
    }
    if password_only_mode:
        edit_text = _find_unique_target(
            d,
            ({"className": "android.widget.EditText"},),
            missing_reason="password_field_not_found",
        )
        if not edit_text["failure_reason"] and (
            _password_target_proof(edit_text["target"]) or hierarchy_proof
        ):
            return edit_text
        if edit_text["failure_reason"] == "ambiguous_login_form":
            return edit_text
    else:
        edit_text = _find_password_edit_text_target(
            d,
            hierarchy_proof=hierarchy_proof,
        )
        if edit_text["target"] is not None:
            return edit_text
    return _find_unique_target(
        d,
        (
            {"text": "Password"},
            {"description": "Password"},
            {"text": "Mot de passe"},
            {"description": "Mot de passe"},
        ),
        missing_reason="password_field_not_found",
    )


def _find_password_edit_text_target(
    d: Any,
    *,
    hierarchy_proof: bool = False,
) -> dict[str, Any]:
    try:
        selector = d(className="android.widget.EditText")
    except Exception:
        return {"target": None, "failure_reason": "password_field_not_found"}

    candidates: list[Any] = []
    all_method = getattr(selector, "all", None)
    if callable(all_method):
        try:
            candidates = [item for item in all_method() if item is not None]
        except Exception:
            candidates = []

    if not candidates:
        count = _selector_count(selector)
        if count == 1:
            candidates = [selector]

    proven = [candidate for candidate in candidates if _password_target_proof(candidate)]
    if len(proven) == 1:
        return {"target": proven[0], "failure_reason": ""}
    if len(proven) > 1:
        return {"target": None, "failure_reason": "ambiguous_login_form"}
    if len(candidates) >= 2 and hierarchy_proof:
        return {"target": candidates[1], "failure_reason": ""}
    if len(candidates) == 1:
        if _password_target_proof(candidates[0]):
            return {"target": candidates[0], "failure_reason": ""}
    count = _selector_count(selector)
    if count >= 2 and hierarchy_proof:
        target = _selector_instance(d, "android.widget.EditText", 1)
        if target is not None:
            return {"target": target, "failure_reason": ""}
    elif count == 1:
        target = _selector_instance(d, "android.widget.EditText", 0)
        if target is not None:
            if _password_target_proof(target):
                return {"target": target, "failure_reason": ""}
    return {"target": None, "failure_reason": "password_field_not_found"}


def _password_target_proof(target: Any) -> str:
    info = _selector_info(target)
    text = _target_public_text(target).strip().lower()
    resource_name = str(info.get("resourceName") or info.get("resource-id") or "").strip().lower()
    description = str(
        info.get("contentDescription")
        or info.get("content-desc")
        or info.get("hint")
        or ""
    ).strip().lower()
    input_type = str(info.get("inputType") or info.get("input-type") or "").strip().lower()
    password_property = info.get("password") is True or str(info.get("password") or "").lower() == "true"
    if password_property:
        return "android_password_property"
    if "password" in input_type:
        return "android_input_type"
    if "password" in resource_name or "passcode" in resource_name:
        return "resource_id"
    if text in {"password", "mot de passe"} or description in {"password", "mot de passe"}:
        return "localized_accessibility_label"
    if _looks_like_masked_password(text):
        return "masked_editable_value"
    return ""


def _selector_instance(d: Any, class_name: str, instance: int) -> Any | None:
    try:
        target = d(className=class_name, instance=instance)
    except Exception:
        return None
    if _selector_count(target) > 0 or _selector_info(target):
        return target
    return None


def _selector_count(selector: Any) -> int:
    count_attr = getattr(selector, "count", None)
    if callable(count_attr):
        try:
            return max(0, int(count_attr()))
        except TypeError:
            try:
                return max(0, int(count_attr))
            except Exception:
                return 0
        except Exception:
            return 0
    if count_attr is not None:
        try:
            return max(0, int(count_attr))
        except Exception:
            return 0
    exists = getattr(selector, "exists", None)
    if callable(exists):
        try:
            return 1 if bool(exists()) else 0
        except Exception:
            return 0
    if exists is not None:
        return 1 if bool(exists) else 0
    return 0


def _focus_clear_set_and_confirm_username(
    d: Any,
    target: Any,
    expected_username: str,
    *,
    prefilled_username_mode: bool,
    prefilled_username: str = "",
    sleeper: Sleeper,
    warnings: list[str],
) -> dict[str, Any]:
    failure_result = "username_input_failed" if prefilled_username_mode else "input_failed"
    before_raw = _read_username_field_value(
        d,
        target,
        prefilled_username=prefilled_username,
        prefer_hierarchy=prefilled_username_mode,
    )
    before_effective = _effective_username_field_text(before_raw)
    before_normalized = _normalize_username(before_effective)
    expected_normalized = _normalize_username(expected_username)
    username_replaced = bool(
        prefilled_username_mode
        and before_normalized
        and before_normalized != expected_normalized
    )
    placeholder_ignored = _is_username_placeholder_text(before_raw)
    if before_normalized == expected_normalized:
        return _username_input_success(
            username_replaced=False,
            confirmed="true",
            result="username_input_confirmed",
            focused_before=_focus_username_target(d, target, warnings),
            clear_method="already_expected",
            input_method="skipped",
            username_placeholder_ignored=placeholder_ignored,
        )

    focused_before = _focus_username_target(d, target, warnings)
    clear_method = ""
    input_method = ""

    if before_effective:
        clear_method = _clear_username_field(target, warnings)
        if clear_method == "clear_failed":
            warnings.append("username_clear_text_failed_trying_set_text")
    else:
        clear_method = "skipped_empty_field"

    sleeper(USERNAME_POST_INPUT_SETTLE_MS / 1000.0)
    after_clear_raw = _read_username_field_value(
        d,
        target,
        prefilled_username=prefilled_username,
        prefer_hierarchy=prefilled_username_mode,
    )
    after_clear_normalized = _normalize_username(_effective_username_field_text(after_clear_raw))
    if after_clear_normalized == expected_normalized:
        return _username_input_success(
            username_replaced=username_replaced,
            confirmed="true",
            result="username_input_confirmed",
            focused_before=focused_before,
            clear_method=clear_method or "clear_only",
            input_method="clear_only",
            username_placeholder_ignored=placeholder_ignored or _is_username_placeholder_text(after_clear_raw),
        )

    input_method = _set_username_field_value(d, target, expected_username, warnings)
    if not input_method:
        input_failure = "username_field_not_focusable" if focused_before is False else "username_clear_failed"
        if not prefilled_username_mode and input_failure == "username_clear_failed":
            input_failure = failure_result
        return _username_input_failure(
            failure_result=input_failure,
            focused_before=focused_before,
            clear_method=clear_method,
            input_method="",
        )

    sleeper(USERNAME_POST_INPUT_SETTLE_MS / 1000.0)
    after_set_direct_raw = _target_public_text(target)
    after_set_direct_normalized = _normalize_username(_effective_username_field_text(after_set_direct_raw))
    if after_set_direct_normalized == expected_normalized:
        return _username_input_success(
            username_replaced=username_replaced,
            confirmed="true",
            result="username_input_confirmed",
            focused_before=focused_before,
            clear_method=clear_method,
            input_method=input_method,
            username_placeholder_ignored=placeholder_ignored or _is_username_placeholder_text(after_set_direct_raw),
        )

    if prefilled_username_mode or _is_username_placeholder_text(after_set_direct_raw):
        after_set_hierarchy = _normalize_username(_username_value_from_hierarchy(d))
        if after_set_hierarchy == expected_normalized:
            return _username_input_success(
                username_replaced=username_replaced,
                confirmed="true",
                result="username_input_confirmed",
                focused_before=focused_before,
                clear_method=clear_method,
                input_method=input_method,
                username_placeholder_ignored=placeholder_ignored or _is_username_placeholder_text(after_set_direct_raw),
            )

    before_is_real_username = _is_valid_instagram_username(before_effective) or (
        prefilled_username_mode and _is_valid_instagram_username(prefilled_username)
    )
    if (
        after_set_direct_raw
        and before_is_real_username
        and after_set_direct_normalized == before_normalized
        and before_normalized != expected_normalized
    ):
        return _username_input_failure(
            failure_result="username_still_prefilled_after_input",
            focused_before=focused_before,
            clear_method=clear_method,
            input_method=input_method,
        )

    if input_method:
        placeholder_sticky = _is_username_placeholder_text(after_set_direct_raw)
        return _username_input_success(
            username_replaced=username_replaced,
            confirmed="unknown",
            result="username_input_assumed",
            focused_before=focused_before,
            clear_method=clear_method,
            input_method=input_method,
            username_placeholder_ignored=placeholder_ignored or placeholder_sticky,
        )

    return _username_input_failure(
        failure_result=failure_result,
        focused_before=focused_before,
        clear_method=clear_method,
        input_method=input_method,
    )


def _username_input_success(
    *,
    username_replaced: bool,
    confirmed: str,
    result: str,
    focused_before: bool | None,
    clear_method: str,
    input_method: str,
    username_placeholder_ignored: bool = False,
) -> dict[str, Any]:
    return {
        "username_replaced": username_replaced,
        "username_input_confirmed": confirmed,
        "username_input_result": result,
        "username_field_focused_before_input": focused_before,
        "username_clear_method": clear_method,
        "username_input_method": input_method,
        "username_placeholder_ignored": username_placeholder_ignored,
    }


def _username_input_failure(
    *,
    failure_result: str,
    focused_before: bool | None,
    clear_method: str,
    input_method: str,
) -> dict[str, Any]:
    return {
        "username_replaced": False,
        "username_input_confirmed": "false",
        "username_input_result": failure_result,
        "username_field_focused_before_input": focused_before,
        "username_clear_method": clear_method,
        "username_input_method": input_method,
    }


def _focus_username_target(d: Any, target: Any, warnings: list[str]) -> bool | None:
    try:
        _click_target(target)
    except Exception:
        warnings.append("username_field_accessibility_focus_failed")
    time.sleep(0.1)
    focused = _target_focused(target)
    if focused is True:
        return True
    if _tap_target_bounds(d, target):
        time.sleep(0.1)
        focused_after_bounds = _target_focused(target)
        if focused_after_bounds is not None:
            return focused_after_bounds
        return True
    return focused


def _clear_username_field(target: Any, warnings: list[str]) -> str:
    if _clear_target_text_checked(target):
        return "clear_text"
    if _set_target_text_checked(target, ""):
        return "set_text_empty"
    warnings.append("username_clear_methods_unavailable")
    return "clear_failed"


def _set_username_field_value(d: Any, target: Any, expected_username: str, warnings: list[str]) -> str:
    if _set_target_text_checked(target, expected_username):
        return "set_text"
    serial = _direct_device_serial(d)
    fast_ime_id = str(getattr(config, "FAST_IME", "") or "").strip()
    if serial and fast_ime_id and is_fast_ime_available(serial):
        try:
            adb_result = _adb_input_result_dict(
                run_adb_keyboard_b64_input(
                    serial,
                    expected_username,
                    fast_ime_id=fast_ime_id,
                )
            )
            command_ok = bool(adb_result.get("command_ok"))
            method_tag = str(adb_result.get("method") or "adb_keyboard_b64")
            broadcast_ok = bool(adb_result.get("broadcast_ok"))
        except Exception:
            command_ok, method_tag, broadcast_ok = False, "", False
        if command_ok and broadcast_ok:
            return method_tag or "adb_keyboard_b64"
        warnings.append("username_adb_keyboard_input_failed")
    return ""


def _set_target_text_checked(target: Any, value: str) -> bool:
    set_text = getattr(target, "set_text", None)
    if not callable(set_text):
        return False
    try:
        set_text(value)
        return True
    except Exception:
        return False


def _read_username_field_value(
    d: Any,
    target: Any,
    *,
    prefilled_username: str = "",
    prefer_hierarchy: bool = False,
) -> str:
    direct = _effective_username_field_text(_target_public_text(target))
    if direct:
        return direct
    if prefer_hierarchy:
        hierarchy_value = _username_value_from_hierarchy(d)
        if hierarchy_value:
            return hierarchy_value
    return str(prefilled_username or "").strip()


def _username_value_from_hierarchy(d: Any) -> str:
    try:
        hierarchy_xml = _dump_hierarchy_once(d)
        signals = extract_login_screen_signals_from_hierarchy(hierarchy_xml)
        return str(signals.get("prefilled_username") or "").strip()
    except Exception:
        return ""


def _focus_clear_and_set_text(d: Any, target: Any, value: str) -> None:
    result = _focus_clear_set_and_confirm_username(
        d,
        target,
        value,
        prefilled_username_mode=False,
        sleeper=time.sleep,
        warnings=[],
    )
    if result["username_input_result"] not in {"username_input_confirmed", "username_input_assumed"}:
        raise RuntimeError("set_text_unavailable")


def _click_target(target: Any) -> None:
    click = getattr(target, "click", None)
    if not callable(click):
        raise RuntimeError("target_click_unavailable")
    click()


def _resolve_fresh_login_submit_target(
    d: Any,
    *,
    expected_username: str,
    password_only_mode: bool,
    prefilled_username: str,
    password_field_proof: str,
    sleeper: Sleeper,
    warnings: list[str],
) -> dict[str, Any]:
    """Re-prove the login surface and resolve new selector handles before submit.

    A sparse hierarchy may omit field/button nodes, so live unique selectors are
    accepted only when the hierarchy does not prove a conflicting destination.
    If a proven login form temporarily loses its CTA (normally because of the
    IME/layout), Back is sent once and every proof is rebuilt afterwards.
    """

    try:
        hierarchy_xml = _dump_login_submit_hierarchy_once(d)
    except Exception:
        return {"failure_reason": "login_form_fresh_observation_failed", "targets": {}}
    signals = extract_login_screen_signals_from_hierarchy(hierarchy_xml)
    if _fresh_submit_conflicting_surface(signals, hierarchy_xml=hierarchy_xml):
        return {"failure_reason": "login_submit_wrong_surface", "targets": {}}

    targets = _resolve_login_form_targets(
        d,
        password_only_mode=password_only_mode,
        prefilled_username=prefilled_username,
        password_field_proof=password_field_proof,
    )
    if targets.get("failure_reason") == "login_button_not_found" and _fresh_submit_login_form_proved(signals):
        press = getattr(d, "press", None)
        if callable(press):
            press("back")
            warnings.append("login_submit_ime_hide_recovery_sent")
            sleeper(0.15)
            try:
                hierarchy_xml = _dump_login_submit_hierarchy_once(d)
            except Exception:
                return {"failure_reason": "login_form_fresh_observation_failed", "targets": {}}
            signals = extract_login_screen_signals_from_hierarchy(hierarchy_xml)
            if _fresh_submit_conflicting_surface(signals, hierarchy_xml=hierarchy_xml):
                return {"failure_reason": "login_submit_wrong_surface", "targets": {}}
            targets = _resolve_login_form_targets(
                d,
                password_only_mode=password_only_mode,
                prefilled_username=prefilled_username,
                password_field_proof=password_field_proof,
            )
    if targets.get("failure_reason"):
        return {"failure_reason": str(targets["failure_reason"]), "targets": {}}

    username_target = targets.get("username")
    if not password_only_mode and username_target is not None:
        actual_username = _normalize_username(
            str(signals.get("prefilled_username") or "")
            or _read_username_field_value(
                d,
                username_target,
                prefilled_username=prefilled_username,
                prefer_hierarchy=False,
            )
        )
        if actual_username and actual_username != _normalize_username(expected_username):
            return {"failure_reason": "login_submit_username_mismatch", "targets": {}}

    button_info = _selector_info(targets["login_button"])
    if button_info.get("enabled") is False:
        sleeper(0.15)
        refreshed = _resolve_login_form_targets(
            d,
            password_only_mode=password_only_mode,
            prefilled_username=prefilled_username,
            password_field_proof=password_field_proof,
        )
        if refreshed.get("failure_reason"):
            return {"failure_reason": str(refreshed["failure_reason"]), "targets": {}}
        targets = refreshed
        button_info = _selector_info(targets["login_button"])
        if button_info.get("enabled") is False:
            return {"failure_reason": "login_button_disabled", "targets": {}}

    warnings.append("login_submit_fresh_surface_proved")
    return {"failure_reason": "", "targets": targets}


def _dump_login_submit_hierarchy_once(d: Any) -> str:
    """Capture the submit boundary through the device adapter when available."""

    provider = getattr(d, "dump_login_submit_hierarchy", None)
    if callable(provider):
        hierarchy = provider()
        if not isinstance(hierarchy, str) or not hierarchy.strip():
            raise RuntimeError("empty login submit hierarchy")
        return hierarchy
    return _dump_hierarchy_once(d)


def _fresh_submit_login_form_proved(signals: dict[str, Any]) -> bool:
    return str(signals.get("screen_type") or "") in {
        "login_form_empty",
        "login_form_prefilled_username",
        "continue_password_only",
    } or (
        signals.get("has_password_field") is True
        and signals.get("has_username_field") is True
    )


def _fresh_submit_conflicting_surface(signals: dict[str, Any], *, hierarchy_xml: str) -> bool:
    explicit_signal = any(
        signals.get(key) is True
        for key in (
            "active_account_home",
            "active_account_profile",
            "verification_code_challenge_present",
            "post_login_location_services_prompt",
            "instagram_turn_on_notifications_prompt",
            "android_instagram_notification_settings",
            "save_login_info_prompt",
        )
    )
    if explicit_signal:
        return True
    probe = probe_login_ui_from_hierarchy(hierarchy_xml, stage="login_submit_fresh_observation")
    return probe.outcome in {
        LoginProbeOutcome.CONNECTED,
        LoginProbeOutcome.NEEDS_2FA,
        LoginProbeOutcome.CHECKPOINT,
        LoginProbeOutcome.VERIFICATION_PENDING,
        LoginProbeOutcome.UNSUPPORTED_POST_SUBMIT_CHALLENGE,
        LoginProbeOutcome.LOGIN_FAILED,
    }


def _input_password_robust(d: Any, target: Any, value: str, warnings: list[str]) -> dict[str, Any]:
    target = _refresh_password_target_if_needed(d, target, warnings)
    target_kind = _password_target_kind(target)
    focused_before = _focus_password_target(d, target, warnings)
    _clear_target_text(target)
    time.sleep(0.1)

    warnings.append("password_input_method_attempted:set_text")
    set_text_result = _attempt_password_set_text_injection(
        d,
        target,
        value,
        focused_before=focused_before,
        target_kind=target_kind,
    )
    if _password_injection_confirmed(set_text_result):
        warnings.extend(set_text_result.get("injection_trace") or [])
        return set_text_result

    if set_text_result.get("password_input_result") == "password_input_failed":
        warnings.extend(set_text_result.get("injection_trace") or [])
        return set_text_result

    if set_text_result.get("password_input_result") == "password_input_empty":
        warnings.append("password_input_set_text_empty")

    _focus_password_target(d, target, warnings)
    _clear_target_text(target)
    time.sleep(0.1)
    warnings.append("password_input_fallback_adb_keyboard_b64_attempted")
    adb_result = _attempt_password_adb_keyboard_injection(
        d,
        target,
        value,
        focused_before=focused_before,
        target_kind=target_kind,
    )
    if _password_injection_confirmed(adb_result):
        warnings.append("password_input_confirmed_after_fallback")
        warnings.extend(adb_result.get("injection_trace") or [])
        return adb_result

    warnings.append("password_input_fallback_failed")
    failure_reason = "password_input_missing_or_not_accepted"
    if adb_result.get("reason"):
        failure_reason = str(adb_result["reason"])
    elif set_text_result.get("reason"):
        failure_reason = str(set_text_result["reason"])
    return _password_input_result(
        str(adb_result.get("password_input_method") or set_text_result.get("password_input_method") or ""),
        focused_before,
        False,
        str(adb_result.get("password_field_non_empty_confirmed") or "false"),
        failure_reason,
        target_kind=target_kind,
        input_result="password_input_empty",
        confirm_method=str(
            adb_result.get("password_confirm_method")
            or set_text_result.get("password_confirm_method")
            or "not_attempted"
        ),
        injection_trace=["password_input_fallback_failed"],
    )


def _refresh_password_target_if_needed(d: Any, target: Any, warnings: list[str]) -> Any:
    if _password_target_kind(target) != "password_placeholder":
        return target
    edit_text = _find_password_edit_text_target(d)
    if edit_text["target"] is not None:
        warnings.append("password_target_refreshed_from_hierarchy_edittext")
        return edit_text["target"]
    if _tap_target_bounds(d, target):
        time.sleep(0.1)
        edit_text = _find_password_edit_text_target(d)
        if edit_text["target"] is not None:
            warnings.append("password_target_refreshed_after_placeholder_tap")
            return edit_text["target"]
    return target


def _attempt_password_set_text_injection(
    d: Any,
    target: Any,
    value: str,
    *,
    focused_before: bool | None,
    target_kind: str,
) -> dict[str, Any]:
    set_text = getattr(target, "set_text", None)
    if not callable(set_text):
        return _password_input_result(
            "set_text",
            focused_before,
            False,
            "false",
            "password_input_failed",
            target_kind=target_kind,
            input_result="password_input_failed",
            confirm_method="set_text_unavailable",
            injection_trace=["password_input_set_text_unavailable"],
        )
    try:
        set_text(value)
        time.sleep(PASSWORD_CONFIRM_SETTLE_MS / 1000.0)
        confirmation = _confirm_password_non_empty_after_input(
            d,
            target,
            method="set_text",
            input_success=True,
            focused_before=focused_before,
            target_kind=target_kind,
        )
        return _password_input_result(
            "set_text",
            focused_before,
            _password_injection_confirmed_from_parts(
                confirmation["password_input_result"],
                confirmation["non_empty_state"],
            ),
            confirmation["non_empty_state"],
            "",
            target_kind=target_kind,
            input_result=confirmation["password_input_result"],
            confirm_method=confirmation["password_confirm_method"],
            injection_trace=[],
        )
    except Exception:
        return _password_input_result(
            "set_text",
            focused_before,
            False,
            "false",
            "password_input_failed",
            target_kind=target_kind,
            input_result="password_input_failed",
            confirm_method="set_text_exception",
            injection_trace=["set_text_password_input_failed"],
        )


def _attempt_password_adb_keyboard_injection(
    d: Any,
    target: Any,
    value: str,
    *,
    focused_before: bool | None,
    target_kind: str,
) -> dict[str, Any]:
    serial = _direct_device_serial(d)
    fast_ime_id = str(getattr(config, "FAST_IME", "") or "").strip()
    if not adb_available():
        return _password_input_result(
            "",
            focused_before,
            False,
            "false",
            "adb_not_available",
            target_kind=target_kind,
            input_result="password_input_empty",
            confirm_method="adb_not_available",
            injection_trace=["adb_not_available"],
        )
    if not serial:
        return _password_input_result(
            "",
            focused_before,
            False,
            "false",
            "adb_serial_missing",
            target_kind=target_kind,
            input_result="password_input_empty",
            confirm_method="adb_serial_missing",
            injection_trace=["adb_serial_missing"],
        )
    ready_state = ensure_adb_keyboard_ready(serial, fast_ime_id=fast_ime_id)
    if not (serial and fast_ime_id and ready_state.get("ok")):
        return _password_input_result(
            "",
            focused_before,
            False,
            "false",
            str(ready_state.get("reason") or "adb_keyboard_unavailable"),
            target_kind=target_kind,
            input_result="password_input_empty",
            confirm_method=str(ready_state.get("reason") or "adb_keyboard_unavailable"),
            injection_trace=[str(ready_state.get("reason") or "adb_keyboard_unavailable")],
        )
    try:
        adb_result = _adb_input_result_dict(
            run_adb_keyboard_b64_input(
                serial,
                value,
                fast_ime_id=fast_ime_id,
            )
        )
        command_ok = bool(adb_result.get("command_ok"))
        method_tag = str(adb_result.get("method") or "adb_keyboard_b64")
        switch_ok = bool(adb_result.get("switch_ok"))
        broadcast_ok = bool(adb_result.get("broadcast_ok"))
        adb_reason = str(adb_result.get("reason") or "")
    except Exception:
        command_ok, method_tag, switch_ok, broadcast_ok, adb_reason = False, "", False, False, "adb_keyboard_exception"
    if not (command_ok and broadcast_ok):
        trace = [adb_reason or "fast_ime_password_input_failed"]
        if not switch_ok:
            trace.append("fast_ime_switch_failed")
        return _password_input_result(
            method_tag or "adb_keyboard_b64",
            focused_before,
            False,
            "false",
            adb_reason or "password_input_missing_or_not_accepted",
            target_kind=target_kind,
            input_result="password_input_empty",
            confirm_method=adb_reason or ("adb_keyboard_broadcast_failed" if command_ok else "adb_keyboard_command_failed"),
            injection_trace=trace,
        )
    time.sleep(PASSWORD_CONFIRM_SETTLE_MS / 1000.0)
    confirmation = _confirm_password_non_empty_after_input(
        d,
        target,
        method=method_tag or "adb_keyboard_b64",
        input_success=True,
        focused_before=focused_before,
        target_kind=target_kind,
    )
    return _password_input_result(
        method_tag or "adb_keyboard_b64",
        focused_before,
        _password_injection_confirmed_from_parts(
            confirmation["password_input_result"],
            confirmation["non_empty_state"],
        ),
        confirmation["non_empty_state"],
        "",
        target_kind=target_kind,
        input_result=confirmation["password_input_result"],
        confirm_method=confirmation["password_confirm_method"],
        injection_trace=[],
    )


def _password_injection_confirmed(result: dict[str, Any]) -> bool:
    return _password_injection_confirmed_from_parts(
        str(result.get("password_input_result") or ""),
        str(result.get("password_field_non_empty_confirmed") or ""),
    )


def _adb_input_result_dict(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        return result
    if isinstance(result, tuple):
        command_ok = bool(result[0]) if len(result) > 0 else False
        method = str(result[1] or "adb_keyboard_b64") if len(result) > 1 else "adb_keyboard_b64"
        switch_ok = bool(result[2]) if len(result) > 2 else False
        broadcast_ok = bool(result[3]) if len(result) > 3 else False
        return {
            "command_ok": command_ok,
            "method": method,
            "switch_ok": switch_ok,
            "broadcast_ok": broadcast_ok,
            "reason": "" if command_ok and switch_ok and broadcast_ok else "adb_keyboard_b64_failed",
        }
    return {
        "command_ok": False,
        "method": "adb_keyboard_b64",
        "switch_ok": False,
        "broadcast_ok": False,
        "reason": "adb_keyboard_b64_failed",
    }


def _password_injection_confirmed_from_parts(input_result: str, non_empty_state: str) -> bool:
    if input_result in {"password_input_confirmed", "password_input_assumed"}:
        return True
    if input_result in {"password_input_empty", "password_input_failed"}:
        return False
    return non_empty_state in {"true", "unknown_but_input_success"}


def _focus_password_target(d: Any, target: Any, warnings: list[str]) -> bool | None:
    try:
        _click_target(target)
    except Exception:
        warnings.append("password_field_accessibility_focus_failed")
    time.sleep(0.1)
    focused = _target_focused(target)
    if focused is True:
        return True
    if _tap_target_bounds(d, target):
        time.sleep(0.1)
        focused_after_bounds = _target_focused(target)
        if focused_after_bounds is not None:
            return focused_after_bounds
        return True
    return focused


def _tap_target_bounds(d: Any, target: Any) -> bool:
    info = _selector_info(target)
    bounds = info.get("bounds") if isinstance(info, dict) else None
    if not isinstance(bounds, dict):
        return False
    try:
        left = int(bounds.get("left"))
        right = int(bounds.get("right"))
        top = int(bounds.get("top"))
        bottom = int(bounds.get("bottom"))
    except Exception:
        return False
    if right <= left or bottom <= top:
        return False
    click = getattr(d, "click", None)
    if not callable(click):
        return False
    try:
        click((left + right) // 2, (top + bottom) // 2)
        return True
    except Exception:
        return False


def _password_target_kind(target: Any) -> str:
    info = _selector_info(target)
    class_name = str(info.get("className") or info.get("class") or "").strip()
    resource_name = str(info.get("resourceName") or info.get("resource-id") or "").strip().lower()
    text = _target_public_text(target).strip().lower()
    proof = _password_target_proof(target)
    if "edittext" in class_name.lower() and proof:
        return f"password_edittext_{proof}"
    if "edittext" in class_name.lower():
        return "edittext"
    if text in {"password", "mot de passe"}:
        return "password_placeholder"
    return "unknown"


def _confirm_password_non_empty_after_input(
    d: Any,
    target: Any,
    *,
    method: str,
    input_success: bool,
    focused_before: bool | None,
    target_kind: str,
) -> dict[str, str]:
    direct_state = _password_field_non_empty_state(target)
    if direct_state == "true":
        return {
            "non_empty_state": "true",
            "password_input_result": "password_input_confirmed",
            "password_confirm_method": "target_accessibility_non_empty",
        }

    if method == "adb_keyboard_b64":
        hierarchy_state = _password_non_empty_state_from_hierarchy(d)
        if hierarchy_state == "true":
            return {
                "non_empty_state": "true",
                "password_input_result": "password_input_confirmed",
                "password_confirm_method": "hierarchy_masked_password",
            }
        if hierarchy_state == "false":
            return {
                "non_empty_state": "false",
                "password_input_result": "password_input_empty",
                "password_confirm_method": "hierarchy_password_empty",
            }

    if direct_state == "false" and not input_success:
        return {
            "non_empty_state": "false",
            "password_input_result": "password_input_empty",
            "password_confirm_method": "target_accessibility_empty",
        }

    if input_success and method == "adb_keyboard_b64" and (
        target_kind.startswith("password_edittext_")
        or target_kind in {"edittext", "password_placeholder", "unknown"}
    ):
        return {
            "non_empty_state": "unknown_but_input_success",
            "password_input_result": "password_input_assumed",
            "password_confirm_method": "adb_keyboard_b64_input_success",
        }

    if input_success and direct_state == "unknown" and focused_before is not False:
        return {
            "non_empty_state": "unknown_but_input_success",
            "password_input_result": "password_input_assumed",
            "password_confirm_method": "input_success_no_empty_signal",
        }

    return {
        "non_empty_state": direct_state,
        "password_input_result": "password_input_empty" if direct_state == "false" else "password_input_unknown",
        "password_confirm_method": "target_accessibility_empty" if direct_state == "false" else "unconfirmed",
    }


def _password_non_empty_state_from_hierarchy(d: Any) -> str:
    try:
        hierarchy_xml = _dump_hierarchy_once(d)
    except Exception:
        return "unknown"
    edit_text_values = _password_candidate_values_from_hierarchy(hierarchy_xml)
    if not edit_text_values:
        return "unknown"
    for value in edit_text_values:
        normalized = str(value or "").strip()
        if _looks_like_masked_password(normalized):
            return "true"
    last_value = str(edit_text_values[-1] or "").strip()
    if not last_value or last_value.lower() in {"password", "mot de passe"}:
        return "false"
    return "unknown"


def _password_candidate_values_from_hierarchy(hierarchy_xml: str) -> list[str]:
    values: list[str] = []
    for match in re.finditer(r"<node\b[^>]*>", str(hierarchy_xml or "")):
        node = match.group(0)
        if "EditText" not in node and 'editable="true"' not in node:
            continue
        text_match = re.search(r'text="([^"]*)"', node)
        value = text_match.group(1).strip() if text_match else ""
        values.append(value)
    return values[-1:] if values else []


def _looks_like_masked_password(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    return bool(re.fullmatch(r"[\u2022\u25cf\u25e6\u2219*]+", text))


def _direct_device_serial(d: Any) -> str:
    serial = get_device_serial(d)
    return str(serial or "").strip()


def _password_input_result(
    method: str,
    focused_before: bool | None,
    call_success: bool,
    non_empty_state: str,
    reason: str,
    *,
    target_kind: str = "",
    input_result: str = "",
    confirm_method: str = "",
    injection_trace: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "input_method_used": method,
        "password_input_method": method,
        "password_input_result": input_result or ("password_input_success" if call_success else "password_input_failed"),
        "password_field_target_kind": target_kind,
        "password_field_focused_before_input": focused_before,
        "input_call_reported_success": call_success,
        "password_field_non_empty_confirmed": non_empty_state,
        "password_confirm_method": confirm_method,
        "reason": reason,
        "injection_trace": list(injection_trace or []),
    }


def _target_focused(target: Any) -> bool | None:
    info = _selector_info(target)
    if not info:
        return None
    focused = info.get("focused")
    return bool(focused) if focused is not None else None


def _clear_target_text(target: Any) -> None:
    clear_text = getattr(target, "clear_text", None)
    if callable(clear_text):
        try:
            clear_text()
            return
        except Exception:
            pass
    clear = getattr(target, "clear", None)
    if callable(clear):
        try:
            clear()
        except Exception:
            pass


def _clear_target_text_checked(target: Any) -> bool:
    clear_text = getattr(target, "clear_text", None)
    if callable(clear_text):
        try:
            clear_text()
            return True
        except Exception:
            return False
    clear = getattr(target, "clear", None)
    if callable(clear):
        try:
            clear()
            return True
        except Exception:
            return False
    return False


def _target_public_text(target: Any) -> str:
    info = _selector_info(target)
    for key in ("text", "contentDescription", "content-desc"):
        value = str(info.get(key) or "").strip()
        if value:
            return value
    return ""


def _normalize_username(value: Any) -> str:
    return str(value or "").strip().lstrip("@").lower()


def _normalize_labelish(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _is_username_placeholder_text(value: Any) -> bool:
    normalized = _normalize_labelish(value)
    if not normalized:
        return False
    if normalized in _USERNAME_PLACEHOLDER_PHRASES:
        return True
    return (
        "username" in normalized
        and (
            "email" in normalized
            or "mobile" in normalized
            or "phone" in normalized
            or "e-mail" in normalized
            or "telephone" in normalized
            or "téléphone" in normalized
        )
    )


def _effective_username_field_text(value: Any) -> str:
    raw = str(value or "").strip()
    if _is_username_placeholder_text(raw):
        return ""
    return raw


def _is_valid_instagram_username(value: Any) -> bool:
    candidate = _normalize_username(value)
    return bool(candidate) and bool(re.fullmatch(r"[a-z0-9._]{1,30}", candidate))


def _password_field_non_empty_state(target: Any) -> str:
    info = _selector_info(target)
    if not info:
        return "unknown"
    for key in ("text", "contentDescription", "content-desc"):
        if key not in info:
            continue
        value = str(info.get(key) or "").strip()
        if value == "":
            return "false"
        if value.lower() in {"password", "mot de passe"}:
            return "false"
        return "true"
    return "unknown"


def _selector_info(target: Any) -> dict[str, Any]:
    try:
        info = getattr(target, "info", None)
        if callable(info):
            info = info()
    except Exception:
        return {}
    return dict(info) if isinstance(info, dict) else {}


def _classify_post_submit_hierarchy(hierarchy_xml: str) -> dict[str, Any]:
    probe = probe_login_ui_from_hierarchy(hierarchy_xml, stage="login_password_form_executor")
    signals = extract_login_screen_signals_from_hierarchy(hierarchy_xml)
    if signals.get("password_required_dialog_present") is True:
        return {
            "outcome": "password_input_missing_or_not_accepted",
            "screen_type": "password_required_dialog",
            "reason": "password_required_dialog",
            "password_required_dialog_present": True,
            "terminal": True,
            "screen_label": "password_required_dialog",
        }
    if signals.get("save_password_prompt_present") is True:
        screen_type = (
            "samsung_pass_save_password_prompt"
            if signals.get("samsung_pass_save_password_prompt_present") is True
            else "google_password_manager_save_prompt"
        )
        reason = (
            "samsung_pass_save_password_prompt"
            if screen_type == "samsung_pass_save_password_prompt"
            else "google_password_manager_save_prompt"
        )
        return {
            "outcome": "unknown",
            "screen_type": screen_type,
            "reason": reason,
            "password_required_dialog_present": False,
            "save_password_prompt_present": True,
            "samsung_pass_save_password_prompt_present": screen_type == "samsung_pass_save_password_prompt",
            "terminal": False,
            "screen_label": screen_type,
        }
    if signals.get("save_login_info_prompt") is True:
        return {
            "outcome": "unknown",
            "screen_type": "save_login_info_prompt",
            "reason": "save_login_info_prompt",
            "password_required_dialog_present": False,
            "save_password_prompt_present": False,
            "save_login_info_prompt_present": True,
            "terminal": False,
            "screen_label": "save_login_info_prompt",
        }
    if signals.get("post_login_location_services_prompt") is True:
        return {
            "outcome": "connected",
            "screen_type": "connected_post_login_location_services_prompt",
            "reason": "connected_post_login_location_services_prompt",
            "password_required_dialog_present": False,
            "save_password_prompt_present": False,
            "post_login_location_services_prompt_present": True,
            "terminal": True,
            "screen_label": "connected_post_login_location_services_prompt",
        }
    if signals.get("android_instagram_notification_settings") is True:
        return {
            "outcome": "connected",
            "screen_type": "android_instagram_notification_settings",
            "reason": "android_instagram_notification_settings",
            "password_required_dialog_present": False,
            "save_password_prompt_present": False,
            "android_notification_settings_present": True,
            "terminal": False,
            "screen_label": "android_instagram_notification_settings",
        }
    if signals.get("instagram_turn_on_notifications_prompt") is True:
        return {
            "outcome": "connected",
            "screen_type": "instagram_turn_on_notifications_prompt",
            "reason": "instagram_turn_on_notifications_prompt",
            "password_required_dialog_present": False,
            "save_password_prompt_present": False,
            "instagram_turn_on_notifications_prompt_present": True,
            "notifications_skip_visible": bool(signals.get("has_notifications_skip_button")),
            "notifications_next_visible": bool(signals.get("has_notifications_next_button")),
            "terminal": False,
            "screen_label": "instagram_turn_on_notifications_prompt",
        }
    if signals.get("verification_code_challenge_present") is True:
        challenge_type = str(signals.get("challenge_type") or "").strip()
        screen_type = str(signals.get("screen_type") or "verification_code_challenge")
        return {
            "outcome": "verification_pending",
            "screen_type": screen_type,
            "reason": "verification_code_required",
            "password_required_dialog_present": False,
            "save_password_prompt_present": False,
            "verification_code_challenge_detected": True,
            "email_code_challenge_detected": challenge_type == "email",
            "challenge_type": challenge_type,
            "verification_channel": challenge_type,
            "masked_email_present": bool(signals.get("masked_email_present"))
            if challenge_type == "email"
            else False,
            "verification_code_expired": bool(signals.get("verification_code_expired")),
            "terminal": True,
            "screen_label": screen_type,
        }
    if probe.outcome == LoginProbeOutcome.UNSUPPORTED_POST_SUBMIT_CHALLENGE:
        return {
            "outcome": "unsupported_post_submit_challenge",
            "screen_type": "unsupported_post_submit_challenge",
            "reason": "unsupported_post_submit_challenge",
            "password_required_dialog_present": False,
            "save_password_prompt_present": False,
            "email_code_challenge_detected": False,
            "challenge_type": "unknown",
            "human_review_required": True,
            "terminal": True,
            "screen_label": "unsupported_post_submit_challenge",
        }
    if signals.get("transition_loading") is True:
        return {
            "outcome": "unknown",
            "screen_type": "loading",
            "reason": "loading_transition",
            "password_required_dialog_present": False,
            "save_password_prompt_present": False,
            "email_code_challenge_detected": False,
            "terminal": False,
            "screen_label": "loading",
        }
    if signals.get("active_account_home") is True:
        return {
            "outcome": "connected",
            "screen_type": "connected_home",
            "reason": "connected_home_signal",
            "password_required_dialog_present": False,
            "save_password_prompt_present": False,
            "terminal": True,
            "screen_label": "connected_home",
        }
    if signals.get("active_account_profile") is True:
        return {
            "outcome": "connected",
            "screen_type": "connected_profile",
            "reason": "connected_profile_signal",
            "password_required_dialog_present": False,
            "save_password_prompt_present": False,
            "terminal": True,
            "screen_label": "connected_profile",
        }
    outcome = str(probe.outcome.value)
    terminal = outcome in {
        "connected",
        "needs_2fa",
        "checkpoint",
        "verification_pending",
        "unsupported_post_submit_challenge",
        "login_failed",
    }
    screen_label = outcome if outcome != "unknown" else str(signals.get("screen_type") or "unknown")
    return {
        "outcome": outcome,
        "screen_type": screen_label,
        "reason": str(probe.reason or "post_submit_observed"),
        "password_required_dialog_present": False,
        "save_password_prompt_present": False,
        "terminal": terminal,
        "screen_label": screen_label,
    }


def _observe_post_submit_settled(
    d: Any,
    *,
    timings: dict[str, int],
    warnings: list[str],
    timer: Timer,
    sleeper: Sleeper,
    initial_wait_ms: int,
    interval_ms: int,
    max_observations: int,
    returned_login_form_recovery: ReturnedLoginFormRecovery | None = None,
) -> dict[str, Any]:
    screens: list[str] = []
    wait_total_ms = 0
    last_observed: dict[str, Any] = {
        "outcome": "unknown",
        "screen_type": "unknown",
        "reason": "post_submit_unknown_after_settling",
        "password_required_dialog_present": False,
        "save_password_prompt_present": False,
        "email_code_challenge_detected": False,
        "challenge_type": "",
        "masked_email_present": False,
        "terminal": False,
        "screen_label": "unknown",
    }
    observations = max(1, int(max_observations or 1))
    save_password_prompt_detected = False
    save_password_prompt_dismissed = False
    save_password_prompt_dismiss_attempt_count = 0
    samsung_pass_save_password_prompt_detected = False
    samsung_pass_save_password_prompt_cancelled = False
    instagram_save_login_info_prompt_detected = False
    instagram_save_login_info_prompt_not_now = False
    post_login_location_services_prompt_detected = False
    post_login_location_services_prompt_dismissed = False
    post_login_location_services_prompt_dismiss_method = ""
    notifications_prompt_detected = False
    notifications_next_tap_sent = False
    notifications_skip_tap_sent = False
    notifications_skip_after_settings_sent = False
    android_notification_settings_detected = False
    android_back_from_notification_settings_sent = False
    dismiss_method = ""
    post_dismiss_screen_type = ""
    post_dismiss_final_observation_count = 0
    post_dismiss_final_screens: list[str] = []
    post_dismiss_final_wait_total_ms = 0
    post_dismiss_final_screen_type = ""
    connected_detected_after_save_prompt_dismiss = False
    for index in range(observations):
        delay_ms = int(initial_wait_ms if index == 0 and initial_wait_ms > 0 else interval_ms)
        if delay_ms > 0:
            sleeper(delay_ms / 1000.0)
            wait_total_ms += delay_ms
        start = timer()
        hierarchy_xml = _dump_hierarchy_once(d)
        timings["post_submit_dump_ms"] += _elapsed_ms(start, timer())
        observed = _classify_post_submit_hierarchy(hierarchy_xml)
        last_observed = observed
        screen_label = str(observed.get("screen_label") or observed.get("screen_type") or "unknown")
        screens.append(screen_label)
        if observed.get("save_password_prompt_present") is True:
            save_password_prompt_detected = True
            is_samsung_pass_prompt = screen_label == "samsung_pass_save_password_prompt"
            if is_samsung_pass_prompt:
                samsung_pass_save_password_prompt_detected = True
                warnings.append("samsung_pass_save_password_prompt_detected")
            if save_password_prompt_dismiss_attempt_count >= MAX_SAVE_PASSWORD_PROMPT_DISMISS_ATTEMPTS:
                last_observed = {
                    **observed,
                    "outcome": "save_password_prompt_blocking",
                    "screen_type": screen_label,
                    "reason": "save_password_prompt_not_dismissed_after_2_attempts",
                    "terminal": True,
                }
                warnings.append("save_password_prompt_blocking")
                break
            dismiss_method = "cancel" if is_samsung_pass_prompt else "back"
            save_password_prompt_dismiss_attempt_count += 1
            if not _dismiss_save_password_prompt_once(d, observed, warnings):
                last_observed = {
                    **observed,
                    "outcome": "save_password_prompt_blocking",
                    "screen_type": screen_label,
                    "reason": "save_password_prompt_dismiss_failed",
                    "terminal": True,
                }
                warnings.append("save_password_prompt_dismiss_failed")
                break
            if is_samsung_pass_prompt:
                samsung_pass_save_password_prompt_cancelled = True
                warnings.append("samsung_pass_save_password_prompt_cancelled")
            continue
        if observed.get("save_login_info_prompt_present") is True:
            instagram_save_login_info_prompt_detected = True
            warnings.append("instagram_save_login_info_prompt_detected")
            if save_password_prompt_dismiss_attempt_count >= MAX_SAVE_PASSWORD_PROMPT_DISMISS_ATTEMPTS:
                last_observed = {
                    **observed,
                    "outcome": "save_login_info_prompt_blocking",
                    "screen_type": "save_login_info_prompt",
                    "reason": "save_login_info_prompt_not_dismissed_after_2_attempts",
                    "terminal": True,
                }
                warnings.append("save_login_info_prompt_blocking")
                break
            dismiss_method = "not_now"
            save_password_prompt_dismiss_attempt_count += 1
            if not _dismiss_save_login_info_prompt_once(d, warnings):
                last_observed = {
                    **observed,
                    "outcome": "save_login_info_prompt_blocking",
                    "screen_type": "save_login_info_prompt",
                    "reason": "save_login_info_prompt_dismiss_failed",
                    "terminal": True,
                }
                warnings.append("save_login_info_prompt_dismiss_failed")
                break
            instagram_save_login_info_prompt_not_now = True
            warnings.append("instagram_save_login_info_prompt_not_now")
            continue
        if observed.get("post_login_location_services_prompt_present") is True:
            post_login_location_services_prompt_detected = True
            warnings.append("post_login_location_services_prompt_detected")
            post_login_location_services_prompt_dismiss_method = "back"
            if _dismiss_post_login_location_services_prompt_once(d, warnings):
                post_login_location_services_prompt_dismissed = True
                warnings.append("post_login_location_services_prompt_dismiss_back")
            else:
                warnings.append("post_login_location_services_prompt_back_unavailable")
            break
        if observed.get("android_notification_settings_present") is True:
            android_notification_settings_detected = True
            warnings.append("android_notification_settings_detected")
            if _android_back_from_notification_settings_once(d, warnings):
                android_back_from_notification_settings_sent = True
                warnings.append("android_back_from_notification_settings_sent")
            else:
                warnings.append("android_back_from_notification_settings_unavailable")
            continue
        if observed.get("instagram_turn_on_notifications_prompt_present") is True:
            notifications_prompt_detected = True
            warnings.append("notifications_prompt_detected")
            if observed.get("notifications_skip_visible") is True:
                if _tap_notifications_skip_once(d, warnings):
                    notifications_skip_tap_sent = True
                    if android_notification_settings_detected:
                        notifications_skip_after_settings_sent = True
                        warnings.append("notifications_skip_after_settings_sent")
                    else:
                        warnings.append("notifications_skip_tap_sent")
                else:
                    warnings.append("notifications_skip_tap_failed")
                continue
            if observed.get("notifications_next_visible") is True:
                if _tap_notifications_next_once(d, warnings):
                    notifications_next_tap_sent = True
                    warnings.append("notifications_next_tap_sent")
                else:
                    warnings.append("notifications_next_tap_failed")
                continue
            warnings.append("notifications_prompt_no_action_target")
            continue
        if observed.get("password_required_dialog_present") is True:
            break
        if bool(observed.get("terminal")):
            break
    if (
        (save_password_prompt_detected or instagram_save_login_info_prompt_detected)
        and not _is_post_submit_dismissible_prompt_label(str(last_observed.get("screen_label") or ""))
    ):
        save_password_prompt_dismissed = str(last_observed.get("outcome") or "") not in {
            "save_password_prompt_blocking",
            "save_login_info_prompt_blocking",
        }
    if save_password_prompt_dismissed and screens:
        for label in reversed(screens):
            if not _is_post_submit_dismissible_prompt_label(label):
                post_dismiss_screen_type = label
                break
    if save_password_prompt_dismissed and post_dismiss_screen_type in {"", "loading", "unknown"}:
        final_observed = _observe_post_dismiss_final_settled(
            d,
            timings=timings,
            timer=timer,
            sleeper=sleeper,
            interval_ms=POST_DISMISS_FINAL_INTERVAL_MS,
            max_observations=POST_DISMISS_FINAL_OBSERVATIONS,
        )
        post_dismiss_final_observation_count = int(final_observed.get("observation_count") or 0)
        post_dismiss_final_screens = list(final_observed.get("screens") or [])
        post_dismiss_final_wait_total_ms = int(final_observed.get("wait_total_ms") or 0)
        post_dismiss_final_screen_type = str(final_observed.get("final_screen_type") or "")
        if post_dismiss_final_screens:
            screens.extend(post_dismiss_final_screens)
            last_observed = dict(final_observed.get("observed") or last_observed)
            outcome = str(last_observed.get("outcome") or "unknown")
            connected_detected_after_save_prompt_dismiss = outcome == "connected"
            post_dismiss_screen_type = post_dismiss_final_screen_type or post_dismiss_screen_type
    outcome = str(last_observed.get("outcome") or "unknown")
    if outcome == "unknown" and any(label in {"loading", "logged_out"} for label in screens):
        final_recheck = _observe_post_submit_final_recheck(
            d,
            timings=timings,
            timer=timer,
            sleeper=sleeper,
        )
        final_recheck_screens = list(final_recheck.get("screens") or [])
        if final_recheck_screens:
            screens.extend(final_recheck_screens)
            wait_total_ms += int(final_recheck.get("wait_total_ms") or 0)
        final_observed = dict(final_recheck.get("observed") or {})
        if final_observed.get("terminal") is True or final_observed.get("verification_code_challenge_detected") is True:
            last_observed = final_observed
            outcome = str(last_observed.get("outcome") or "unknown")
            warnings.append("post_submit_final_recheck_terminal")

    if outcome == "logged_out" and "loading" in screens and returned_login_form_recovery is not None:
        warnings.append("post_submit_returned_login_form_recovery_started")
        recovery = returned_login_form_recovery()
        if recovery.get("executed") is True:
            recovered = _observe_post_submit_settled(
                d,
                timings=timings,
                warnings=warnings,
                timer=timer,
                sleeper=sleeper,
                initial_wait_ms=initial_wait_ms,
                interval_ms=interval_ms,
                max_observations=max_observations,
                returned_login_form_recovery=None,
            )
            recovered_screens = list(recovered.get("screens") or [])
            return {
                **recovered,
                "observation_count": len(screens) + int(recovered.get("observation_count") or 0),
                "wait_total_ms": wait_total_ms + int(recovered.get("wait_total_ms") or 0),
                "screens": [*screens, *recovered_screens],
                "returned_login_form_recovery_attempted": True,
                "returned_login_form_recovery_executed": True,
            }
        last_observed = {
            **last_observed,
            "reason": str(recovery.get("failure_reason") or "login_submit_returned_form_recovery_failed"),
            "terminal": True,
        }
        warnings.append("post_submit_returned_login_form_recovery_failed")
    elif outcome == "logged_out":
        last_observed = {
            **last_observed,
            "reason": "session_expired_after_settling",
            "terminal": True,
        }
        warnings.append("post_submit_logged_out_after_settling")
    elif outcome == "unknown":
        final_loading = bool(
            post_dismiss_final_observation_count
            and post_dismiss_final_screens
            and all(screen == "loading" for screen in post_dismiss_final_screens)
        )
        if final_loading or (not post_dismiss_final_observation_count and screens and all(screen == "loading" for screen in screens)):
            last_observed = {
                **last_observed,
                "outcome": "login_submit_still_loading",
                "screen_type": "loading",
                "screen_label": "loading",
                "reason": "post_submit_loading_timeout",
                "terminal": True,
            }
            warnings.append("post_submit_loading_timeout")
        else:
            reason = (
                "post_submit_unknown_after_final_settling"
                if post_dismiss_final_observation_count
                else "post_submit_unknown_after_settling"
            )
            last_observed = {
                **last_observed,
                "reason": reason,
            }
            warnings.append(reason)
    timings["post_submit_wait_total_ms"] += wait_total_ms
    timings["post_submit_observation_count"] += len(screens)
    return {
        **last_observed,
        "observation_count": len(screens),
        "wait_total_ms": wait_total_ms,
        "screens": screens,
        "final_terminal_screen": screens[-1] if screens else "",
        "post_submit_loading_timeout": str(last_observed.get("reason") or "") == "post_submit_loading_timeout",
        "email_code_challenge_detected": bool(last_observed.get("email_code_challenge_detected")),
        "verification_code_challenge_detected": bool(
            last_observed.get("verification_code_challenge_detected")
        ),
        "challenge_type": str(last_observed.get("challenge_type") or ""),
        "masked_email_present": bool(last_observed.get("masked_email_present")),
        "save_password_prompt_detected": save_password_prompt_detected,
        "save_password_prompt_dismissed": save_password_prompt_dismissed,
        "save_password_prompt_dismiss_attempt_count": save_password_prompt_dismiss_attempt_count,
        "dismiss_method": dismiss_method if (save_password_prompt_detected or instagram_save_login_info_prompt_detected) else "",
        "samsung_pass_save_password_prompt_detected": samsung_pass_save_password_prompt_detected,
        "samsung_pass_save_password_prompt_cancelled": samsung_pass_save_password_prompt_cancelled,
        "instagram_save_login_info_prompt_detected": instagram_save_login_info_prompt_detected,
        "instagram_save_login_info_prompt_not_now": instagram_save_login_info_prompt_not_now,
        "post_login_location_services_prompt_detected": post_login_location_services_prompt_detected,
        "post_login_location_services_prompt_dismissed": post_login_location_services_prompt_dismissed,
        "post_login_location_services_prompt_dismiss_method": post_login_location_services_prompt_dismiss_method,
        "notifications_prompt_detected": notifications_prompt_detected,
        "notifications_next_tap_sent": notifications_next_tap_sent,
        "notifications_skip_tap_sent": notifications_skip_tap_sent,
        "notifications_skip_after_settings_sent": notifications_skip_after_settings_sent,
        "android_notification_settings_detected": android_notification_settings_detected,
        "android_back_from_notification_settings_sent": android_back_from_notification_settings_sent,
        "post_dismiss_screen_type": post_dismiss_screen_type,
        "post_dismiss_final_observation_count": post_dismiss_final_observation_count,
        "post_dismiss_final_screens": post_dismiss_final_screens,
        "post_dismiss_final_wait_total_ms": post_dismiss_final_wait_total_ms,
        "post_dismiss_final_screen_type": post_dismiss_final_screen_type,
        "connected_detected_after_save_prompt_dismiss": connected_detected_after_save_prompt_dismiss,
    }


def _observe_post_submit_final_recheck(
    d: Any,
    *,
    timings: dict[str, int],
    timer: Timer,
    sleeper: Sleeper,
) -> dict[str, Any]:
    screens: list[str] = []
    wait_total_ms = 0
    observed: dict[str, Any] = {
        "outcome": "unknown",
        "screen_type": "unknown",
        "reason": "post_submit_unknown_after_final_recheck",
        "terminal": False,
        "screen_label": "unknown",
    }
    for _index in range(POST_SUBMIT_FINAL_RECHECK_OBSERVATIONS):
        sleeper(POST_SUBMIT_FINAL_RECHECK_INTERVAL_MS / 1000.0)
        wait_total_ms += POST_SUBMIT_FINAL_RECHECK_INTERVAL_MS
        start = timer()
        hierarchy_xml = _dump_hierarchy_once(d)
        timings["post_submit_dump_ms"] += _elapsed_ms(start, timer())
        observed = _classify_post_submit_hierarchy(hierarchy_xml)
        screen_label = str(observed.get("screen_label") or observed.get("screen_type") or "unknown")
        screens.append(screen_label)
        if observed.get("terminal") is True:
            break
    return {
        "observed": observed,
        "screens": screens,
        "wait_total_ms": wait_total_ms,
    }


def _observe_post_dismiss_final_settled(
    d: Any,
    *,
    timings: dict[str, int],
    timer: Timer,
    sleeper: Sleeper,
    interval_ms: int,
    max_observations: int,
) -> dict[str, Any]:
    observations = max(1, min(int(max_observations or 1), MAX_POST_SUBMIT_OBSERVATIONS))
    interval = _clamp_ms(interval_ms, MAX_POST_SUBMIT_INTERVAL_MS)
    screens: list[str] = []
    wait_total_ms = 0
    last_observed: dict[str, Any] = {
        "outcome": "unknown",
        "screen_type": "unknown",
        "screen_label": "unknown",
        "reason": "post_submit_unknown_after_final_settling",
        "terminal": False,
    }
    for _index in range(observations):
        if interval > 0:
            sleeper(interval / 1000.0)
            wait_total_ms += interval
        start = timer()
        hierarchy_xml = _dump_hierarchy_once(d)
        timings["post_submit_dump_ms"] += _elapsed_ms(start, timer())
        last_observed = _classify_post_submit_hierarchy(hierarchy_xml)
        label = str(last_observed.get("screen_label") or last_observed.get("screen_type") or "unknown")
        screens.append(label)
        if last_observed.get("password_required_dialog_present") is True or bool(last_observed.get("terminal")):
            break
    return {
        "observed": last_observed,
        "observation_count": len(screens),
        "screens": screens,
        "wait_total_ms": wait_total_ms,
        "final_screen_type": screens[-1] if screens else "",
    }


def _is_post_submit_dismissible_prompt_label(label: str) -> bool:
    return str(label or "") in {
        "google_password_manager_save_prompt",
        "samsung_pass_save_password_prompt",
        "save_login_info_prompt",
    }


def _dismiss_save_password_prompt_once(d: Any, observed: dict[str, Any], warnings: list[str]) -> bool:
    if str(observed.get("screen_type") or "") == "samsung_pass_save_password_prompt":
        target = _find_unique_target(
            d,
            ({"text": "Cancel"}, {"description": "Cancel"}, {"text": "Annuler"}, {"description": "Annuler"}),
            missing_reason="cancel_button_not_found",
        )
        if target["failure_reason"]:
            warnings.append(str(target["failure_reason"]))
            return False
        try:
            _click_target(target["target"])
            return True
        except Exception:
            warnings.append("samsung_pass_cancel_tap_failed")
            return False

    press = getattr(d, "press", None)
    if not callable(press):
        warnings.append("save_password_prompt_back_unavailable")
        return False
    try:
        press("back")
        warnings.append("save_password_prompt_dismiss_back")
        return True
    except Exception:
        warnings.append("save_password_prompt_back_failed")
        return False


def _dismiss_save_login_info_prompt_once(d: Any, warnings: list[str]) -> bool:
    target = _find_unique_target(
        d,
        ({"text": "Not now"}, {"description": "Not now"}, {"text": "Pas maintenant"}, {"description": "Pas maintenant"}),
        missing_reason="not_now_button_not_found",
    )
    if target["failure_reason"]:
        warnings.append(str(target["failure_reason"]))
        return False
    try:
        _click_target(target["target"])
        return True
    except Exception:
        warnings.append("save_login_info_not_now_tap_failed")
        return False


def _dismiss_post_login_location_services_prompt_once(d: Any, warnings: list[str]) -> bool:
    press = getattr(d, "press", None)
    if not callable(press):
        return False
    try:
        press("back")
        return True
    except Exception:
        warnings.append("post_login_location_services_prompt_back_failed")
        return False


def _tap_notifications_next_once(d: Any, warnings: list[str]) -> bool:
    target = _find_unique_target(
        d,
        ({"text": "Next"}, {"description": "Next"}, {"text": "Suivant"}, {"description": "Suivant"}),
        missing_reason="notifications_next_button_not_found",
    )
    if target["failure_reason"]:
        warnings.append(str(target["failure_reason"]))
        return False
    try:
        _click_target(target["target"])
        return True
    except Exception:
        warnings.append("notifications_next_tap_failed")
        return False


def _tap_notifications_skip_once(d: Any, warnings: list[str]) -> bool:
    target = _find_unique_target(
        d,
        ({"text": "Skip"}, {"description": "Skip"}, {"text": "Ignorer"}, {"description": "Ignorer"}),
        missing_reason="notifications_skip_button_not_found",
    )
    if target["failure_reason"]:
        warnings.append(str(target["failure_reason"]))
        return False
    try:
        _click_target(target["target"])
        return True
    except Exception:
        warnings.append("notifications_skip_tap_failed")
        return False


def _android_back_from_notification_settings_once(d: Any, warnings: list[str]) -> bool:
    press = getattr(d, "press", None)
    if not callable(press):
        warnings.append("android_notification_settings_back_unavailable")
        return False
    try:
        press("back")
        return True
    except Exception:
        warnings.append("android_notification_settings_back_failed")
        return False


def _tap_ok_once(d: Any) -> bool:
    target = _find_unique_target(d, ({"text": "OK"}, {"description": "OK"}), missing_reason="ok_button_not_found")
    if target["failure_reason"]:
        return False
    try:
        _click_target(target["target"])
        return True
    except Exception:
        return False


def _is_password_only_mode(signals: dict | None) -> bool:
    return isinstance(signals, dict) and signals.get("screen_type") == "continue_password_only"


def _is_prefilled_username_mode(signals: dict | None) -> bool:
    return isinstance(signals, dict) and signals.get("screen_type") == "login_form_prefilled_username"


def _overlay_recovery_allowed(signals: dict | None) -> bool:
    return isinstance(signals, dict) and (
        signals.get("overlay_present") is True or signals.get("password_overlay_present") is True
    )


def _safe_overlay_recovery_once(d: Any, targets: dict[str, Any], warnings: list[str]) -> bool:
    warnings.append("overlay_submit_recovery_once")
    press = getattr(d, "press", None)
    if callable(press):
        try:
            press("back")
        except Exception:
            warnings.append("overlay_back_failed")
    try:
        _click_target(targets["password"])
    except Exception:
        warnings.append("overlay_refocus_failed")
        return False
    return True


def _dump_hierarchy_once(d: Any) -> str:
    try:
        return str(d.dump_hierarchy(compressed=False) or "")
    except TypeError:
        return str(d.dump_hierarchy() or "")


def _empty_timings() -> dict[str, int]:
    return {
        "target_lookup_ms": 0,
        "username_input_ms": 0,
        "password_input_ms": 0,
        "submit_tap_ms": 0,
        "post_submit_wait_ms": 0,
        "post_submit_wait_total_ms": 0,
        "post_submit_observation_count": 0,
        "post_submit_dump_ms": 0,
        "total_ms": 0,
    }


def _clamp_ms(value: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = 0
    return min(maximum, max(0, parsed))


def _clamp_count(value: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = 1
    return min(maximum, max(1, parsed))


def _observation_count_for_timeout(timeout_ms: int, interval_ms: int) -> int:
    interval = max(1, int(interval_ms or DEFAULT_POST_SUBMIT_INTERVAL_MS))
    timeout = max(interval, int(timeout_ms or interval))
    count = (timeout + interval - 1) // interval
    return _clamp_count(count, MAX_POST_SUBMIT_OBSERVATIONS)


def _elapsed_ms(start: float, end: float) -> int:
    return max(0, int(round((end - start) * 1000)))


def _failure(
    failure_reason: str,
    *,
    timings: dict[str, int],
    warnings: list[str],
    total_start: float,
    timer: Timer,
    expected_username: str,
    username_entered: bool = False,
    password_entered: bool = False,
    submit_tapped: bool = False,
) -> LoginPasswordExecutionResult:
    timings["total_ms"] = _elapsed_ms(total_start, timer())
    return _result(
        ok=False,
        executed=False,
        action=ACTION_LOGIN_FORM_SUBMIT if username_entered or password_entered or submit_tapped else NO_ACTION,
        reason=failure_reason,
        failure_reason=failure_reason,
        username_entered=username_entered,
        password_entered=password_entered,
        submit_tapped=submit_tapped,
        timings=timings,
        warnings=warnings,
        expected_username=expected_username,
    )


def _result(
    *,
    ok: bool,
    executed: bool,
    action: str,
    reason: str,
    failure_reason: str | None = None,
    username_entered: bool = False,
    password_entered: bool = False,
    submit_tapped: bool = False,
    post_submit_screen_type: str | None = None,
    post_submit_probe_reason: str | None = None,
    post_submit_outcome: str | None = None,
    timings: dict[str, int] | None = None,
    warnings: list[str] | None = None,
    expected_username: str = "",
    password_only_mode: bool = False,
    input_method_used: str = "",
    password_field_focused_before_input: bool | None = None,
    input_action_reported_success: bool = False,
    password_field_non_empty_confirmed: str = "unknown",
    password_required_dialog_detected: bool = False,
    password_required_retry_attempted: bool = False,
    password_required_retry_count: int = 0,
    password_refill_attempted: bool = False,
    second_submit_executed: bool = False,
    password_submit_result: str | None = None,
    post_submit_observation_count: int = 0,
    post_submit_wait_total_ms: int = 0,
    post_submit_screens: list[str] | None = None,
    final_terminal_screen: str = "",
    post_submit_timeout_ms: int = 0,
    post_submit_interval_ms: int = 0,
    post_submit_loading_timeout: bool = False,
    email_code_challenge_detected: bool = False,
    verification_code_challenge_detected: bool = False,
    challenge_type: str = "",
    masked_email_present: bool = False,
    save_password_prompt_detected: bool = False,
    save_password_prompt_dismissed: bool = False,
    save_password_prompt_dismiss_attempt_count: int = 0,
    save_password_prompt_dismiss_method: str = "",
    samsung_pass_save_password_prompt_detected: bool = False,
    samsung_pass_save_password_prompt_cancelled: bool = False,
    instagram_save_login_info_prompt_detected: bool = False,
    instagram_save_login_info_prompt_not_now: bool = False,
    post_dismiss_screen_type: str = "",
    post_dismiss_final_observation_count: int = 0,
    post_dismiss_final_screens: list[str] | None = None,
    post_dismiss_final_wait_total_ms: int = 0,
    post_dismiss_final_screen_type: str = "",
    connected_detected_after_save_prompt_dismiss: bool = False,
    post_login_location_services_prompt_detected: bool = False,
    post_login_location_services_prompt_dismissed: bool = False,
    post_login_location_services_prompt_dismiss_method: str = "",
    notifications_prompt_detected: bool = False,
    notifications_next_tap_sent: bool = False,
    notifications_skip_tap_sent: bool = False,
    notifications_skip_after_settings_sent: bool = False,
    android_notification_settings_detected: bool = False,
    android_back_from_notification_settings_sent: bool = False,
    username_replaced: bool = False,
    username_input_confirmed: str = "unknown",
    username_input_result: str = "",
    username_field_focused_before_input: bool | None = None,
    username_clear_method: str = "",
    username_input_method: str = "",
    username_placeholder_ignored: bool = False,
    password_field_target_kind: str = "",
    password_input_method: str = "",
    password_input_result: str = "",
    password_confirm_method: str = "",
) -> LoginPasswordExecutionResult:
    safe_metadata = clean_login_probe_metadata(
        redact_credentials_payload(
            {
                "source": "login_password_form_executor",
                "action": action,
                "reason": reason,
                "failure_reason": failure_reason,
                "password_submit_result": password_submit_result,
                "expected_username": expected_username,
                "post_submit_outcome": post_submit_outcome,
                "password_only_mode": password_only_mode,
                "input_method_used": input_method_used,
                "password_input_method": password_input_method,
                "password_input_result": password_input_result,
                "password_field_target_kind": password_field_target_kind,
                "password_confirm_method": password_confirm_method,
                "password_field_focused_before_input": password_field_focused_before_input,
                "input_action_reported_success": input_action_reported_success,
                "password_field_non_empty_confirmed": password_field_non_empty_confirmed,
                "password_required_dialog_detected": password_required_dialog_detected,
                "password_required_retry_attempted": password_required_retry_attempted,
                "password_required_retry_count": password_required_retry_count,
                "password_refill_attempted": password_refill_attempted,
                "second_submit_executed": second_submit_executed,
                "post_submit_observation_count": post_submit_observation_count,
                "post_submit_wait_total_ms": post_submit_wait_total_ms,
                "post_submit_screens": list(post_submit_screens or []),
                "final_terminal_screen": final_terminal_screen,
                "post_submit_timeout_ms": post_submit_timeout_ms,
                "post_submit_interval_ms": post_submit_interval_ms,
                "post_submit_loading_timeout": post_submit_loading_timeout,
                "email_code_challenge_detected": email_code_challenge_detected,
                "verification_code_challenge_detected": verification_code_challenge_detected,
                "challenge_type": challenge_type,
                "masked_email_present": masked_email_present,
                "save_password_prompt_detected": save_password_prompt_detected,
                "save_password_prompt_dismissed": save_password_prompt_dismissed,
                "save_password_prompt_dismiss_attempt_count": save_password_prompt_dismiss_attempt_count,
                "dismiss_method": save_password_prompt_dismiss_method,
                "samsung_pass_save_password_prompt_detected": samsung_pass_save_password_prompt_detected,
                "samsung_pass_save_password_prompt_cancelled": samsung_pass_save_password_prompt_cancelled,
                "instagram_save_login_info_prompt_detected": instagram_save_login_info_prompt_detected,
                "instagram_save_login_info_prompt_not_now": instagram_save_login_info_prompt_not_now,
                "post_login_location_services_prompt_detected": post_login_location_services_prompt_detected,
                "post_login_location_services_prompt_dismissed": post_login_location_services_prompt_dismissed,
                "post_login_location_services_prompt_dismiss_method": post_login_location_services_prompt_dismiss_method,
                "notifications_prompt_detected": notifications_prompt_detected,
                "notifications_next_tap_sent": notifications_next_tap_sent,
                "notifications_skip_tap_sent": notifications_skip_tap_sent,
                "notifications_skip_after_settings_sent": notifications_skip_after_settings_sent,
                "android_notification_settings_detected": android_notification_settings_detected,
                "android_back_from_notification_settings_sent": android_back_from_notification_settings_sent,
                "post_dismiss_screen_type": post_dismiss_screen_type,
                "post_dismiss_final_observation_count": post_dismiss_final_observation_count,
                "post_dismiss_final_screens": list(post_dismiss_final_screens or []),
                "post_dismiss_final_wait_total_ms": post_dismiss_final_wait_total_ms,
                "post_dismiss_final_screen_type": post_dismiss_final_screen_type,
                "connected_detected_after_save_prompt_dismiss": connected_detected_after_save_prompt_dismiss,
                "username_replaced": username_replaced,
                "username_input_confirmed": username_input_confirmed,
                "username_input_result": username_input_result,
                "username_input_ms": int((timings or {}).get("username_input_ms") or 0),
                "username_field_focused_before_input": username_field_focused_before_input,
                "username_clear_method": username_clear_method,
                "username_input_method": username_input_method,
                "username_placeholder_ignored": username_placeholder_ignored,
            }
        )
    )
    return LoginPasswordExecutionResult(
        ok=ok,
        executed=executed,
        action=action,
        reason=reason,
        failure_reason=failure_reason,
        username_entered=username_entered,
        password_entered=password_entered,
        submit_tapped=submit_tapped,
        post_submit_screen_type=post_submit_screen_type,
        post_submit_probe_reason=post_submit_probe_reason,
        post_submit_outcome=post_submit_outcome,
        timings=timings or _empty_timings(),
        warnings=list(warnings or []),
        safe_metadata=safe_metadata,
    )
