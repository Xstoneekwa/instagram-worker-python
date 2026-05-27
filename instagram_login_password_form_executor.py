"""Controlled Instagram login form credential executor.

Entry 2E-5I fills username/password only after the caller has prevalidated the
screen as `login_form_empty` or a controlled password-only continuation screen.
It has no runner hook, no Supabase write, no status publish, no unbounded retry,
and never stores or logs the password.
"""

from __future__ import annotations

import base64
import re
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import config
from device import get_current_ime, is_fast_ime_available, set_ime
from instagram_credentials_runtime_access import (
    SecretValue,
    redact_credentials_payload,
    revealed_value_blocked_for_injection,
)
from instagram_login_status_classifier import clean_login_probe_metadata
from instagram_login_ui_probe import extract_login_screen_signals_from_hierarchy, probe_login_ui_from_hierarchy


ACTION_LOGIN_FORM_SUBMIT = "login_form_submit"
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

Timer = Callable[[], float]
Sleeper = Callable[[float], None]


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

    start = timer()
    targets = _resolve_login_form_targets(
        d,
        password_only_mode=password_only_mode,
        prefilled_username=str((prevalidated_signals or {}).get("prefilled_username") or ""),
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
            username_entered = username_input_result in {"username_input_confirmed", "username_input_assumed"}
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

    try:
        start = timer()
        _click_target(targets["login_button"])
        submit_tapped = True
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
    post_dismiss_screen_type = ""
    post_submit_loading_timeout = False
    failure_reason: str | None = None

    if dump_after_submit:
        try:
            observed = _observe_post_submit_settled(
                d,
                timings=timings,
                warnings=warnings,
                timer=timer,
                sleeper=sleeper,
                initial_wait_ms=wait_ms,
                interval_ms=observation_interval_ms,
                max_observations=observation_limit,
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
            post_dismiss_screen_type = str(observed.get("post_dismiss_screen_type") or "")
            post_submit_loading_timeout = bool(observed.get("post_submit_loading_timeout"))
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
                            post_dismiss_screen_type = (
                                str(observed.get("post_dismiss_screen_type") or "") or post_dismiss_screen_type
                            )
                            post_submit_loading_timeout = post_submit_loading_timeout or bool(
                                observed.get("post_submit_loading_timeout")
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
        save_password_prompt_detected=save_password_prompt_detected,
        save_password_prompt_dismissed=save_password_prompt_dismissed,
        save_password_prompt_dismiss_attempt_count=save_password_prompt_dismiss_attempt_count,
        save_password_prompt_dismiss_method=save_password_prompt_dismiss_method,
        post_dismiss_screen_type=post_dismiss_screen_type,
        username_replaced=username_replaced,
        username_input_confirmed=username_input_confirmed,
        username_input_result=username_input_result,
        username_field_focused_before_input=username_field_focused_before_input,
        username_clear_method=username_clear_method,
        username_input_method=username_input_method,
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
) -> dict[str, Any]:
    username = _find_username_target(d, prefilled_username=prefilled_username)
    if username["failure_reason"] and not (password_only_mode and username["failure_reason"] == "username_field_not_found"):
        return {"failure_reason": username["failure_reason"]}

    password = _find_password_target(d, password_only_mode=password_only_mode)
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


def _find_password_target(d: Any, *, password_only_mode: bool) -> dict[str, Any]:
    if password_only_mode:
        edit_text = _find_unique_target(
            d,
            ({"className": "android.widget.EditText"},),
            missing_reason="password_field_not_found",
        )
        if not edit_text["failure_reason"]:
            return edit_text
        if edit_text["failure_reason"] == "ambiguous_login_form":
            return edit_text
    else:
        edit_text = _find_password_edit_text_target(d)
        if edit_text["target"] is not None:
            return edit_text
    return _find_unique_target(
        d,
        (
            {"text": "Password"},
            {"description": "Password"},
        ),
        missing_reason="password_field_not_found",
    )


def _find_password_edit_text_target(d: Any) -> dict[str, Any]:
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

    if len(candidates) >= 2:
        return {"target": candidates[1], "failure_reason": ""}
    if len(candidates) == 1:
        info = _selector_info(candidates[0])
        text = _target_public_text(candidates[0]).strip().lower()
        resource_name = str(info.get("resourceName") or info.get("resource-id") or "").strip().lower()
        if "password" in resource_name or text in {"password", "mot de passe"} or _looks_like_masked_password(text):
            return {"target": candidates[0], "failure_reason": ""}
    return {"target": None, "failure_reason": "password_field_not_found"}


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
    before = _read_username_field_value(
        d,
        target,
        prefilled_username=prefilled_username,
        prefer_hierarchy=prefilled_username_mode,
    )
    before_normalized = _normalize_username(before)
    expected_normalized = _normalize_username(expected_username)
    if before_normalized == expected_normalized:
        return _username_input_success(
            username_replaced=False,
            confirmed="true",
            result="username_input_confirmed",
            focused_before=_focus_username_target(d, target, warnings),
            clear_method="already_expected",
            input_method="skipped",
        )

    focused_before = _focus_username_target(d, target, warnings)
    clear_method = ""
    input_method = ""

    clear_method = _clear_username_field(target, warnings)
    if clear_method == "clear_failed":
        warnings.append("username_clear_text_failed_trying_set_text")

    sleeper(USERNAME_POST_INPUT_SETTLE_MS / 1000.0)
    after_clear = _read_username_field_value(
        d,
        target,
        prefilled_username=prefilled_username,
        prefer_hierarchy=prefilled_username_mode,
    )
    after_clear_normalized = _normalize_username(after_clear)
    if after_clear_normalized == expected_normalized:
        return _username_input_success(
            username_replaced=before_normalized != expected_normalized,
            confirmed="true",
            result="username_input_confirmed",
            focused_before=focused_before,
            clear_method=clear_method or "clear_only",
            input_method="clear_only",
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
    after_set_direct = _target_public_text(target)
    after_set_direct_normalized = _normalize_username(after_set_direct)
    if after_set_direct_normalized == expected_normalized:
        return _username_input_success(
            username_replaced=before_normalized != expected_normalized,
            confirmed="true",
            result="username_input_confirmed",
            focused_before=focused_before,
            clear_method=clear_method,
            input_method=input_method,
        )

    if prefilled_username_mode:
        after_set_hierarchy = _normalize_username(_username_value_from_hierarchy(d))
        if after_set_hierarchy == expected_normalized:
            return _username_input_success(
                username_replaced=before_normalized != expected_normalized,
                confirmed="true",
                result="username_input_confirmed",
                focused_before=focused_before,
                clear_method=clear_method,
                input_method=input_method,
            )

    if (
        after_set_direct
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
        return _username_input_success(
            username_replaced=before_normalized != expected_normalized or prefilled_username_mode,
            confirmed="unknown",
            result="username_input_assumed",
            focused_before=focused_before,
            clear_method=clear_method,
            input_method=input_method,
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
) -> dict[str, Any]:
    return {
        "username_replaced": username_replaced,
        "username_input_confirmed": confirmed,
        "username_input_result": result,
        "username_field_focused_before_input": focused_before,
        "username_clear_method": clear_method,
        "username_input_method": input_method,
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
            command_ok, method_tag, _switch_ok, broadcast_ok = _run_adb_keyboard_b64_input(
                serial,
                expected_username,
                fast_ime_id=fast_ime_id,
            )
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
    direct = _target_public_text(target)
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


def _input_password_robust(d: Any, target: Any, value: str, warnings: list[str]) -> dict[str, Any]:
    focused_before = _focus_password_target(d, target, warnings)
    _clear_target_text(target)
    time.sleep(0.1)
    target_kind = _password_target_kind(target)
    serial = _direct_device_serial(d)
    fast_ime_id = str(getattr(config, "FAST_IME", "") or "").strip()
    if serial and fast_ime_id and is_fast_ime_available(serial):
        try:
            command_ok, method_tag, switch_ok, broadcast_ok = _run_adb_keyboard_b64_input(
                serial, value, fast_ime_id=fast_ime_id
            )
        except Exception:
            command_ok, method_tag, switch_ok, broadcast_ok = False, "", False, False
        if command_ok and broadcast_ok:
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
                method_tag or "fast_ime",
                focused_before,
                True,
                confirmation["non_empty_state"],
                "",
                target_kind=target_kind,
                input_result=confirmation["password_input_result"],
                confirm_method=confirmation["password_confirm_method"],
            )
        warnings.append("fast_ime_password_input_failed")
        if not switch_ok:
            warnings.append("fast_ime_switch_failed")

    set_text = getattr(target, "set_text", None)
    if callable(set_text):
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
                True,
                confirmation["non_empty_state"],
                "",
                target_kind=target_kind,
                input_result=confirmation["password_input_result"],
                confirm_method=confirmation["password_confirm_method"],
            )
        except Exception:
            warnings.append("set_text_password_input_failed")
    return _password_input_result(
        "",
        focused_before,
        False,
        "unknown",
        "password_input_failed",
        target_kind=target_kind,
        input_result="password_input_failed",
        confirm_method="not_attempted",
    )


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
    if "edittext" in class_name.lower() and "password" in resource_name:
        return "password_edittext_resource"
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

    if input_success and method == "adb_keyboard_b64" and target_kind in {
        "password_edittext_resource",
        "edittext",
        "password_placeholder",
        "unknown",
    }:
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
    serial = getattr(d, "serial", None)
    return str(serial).strip() if serial else ""


def _run_adb_keyboard_b64_input(
    serial: str,
    value: str,
    *,
    fast_ime_id: str,
) -> tuple[bool, str, bool, bool]:
    fast_ime_id = str(fast_ime_id or "").strip()
    if not serial or not fast_ime_id:
        return False, "", False, False
    current_ime = get_current_ime(serial).strip()
    switch_ok = current_ime == fast_ime_id or set_ime(serial, fast_ime_id)
    if not switch_ok:
        return False, "", False, False
    encoded = base64.b64encode(str(value or "").encode("utf-8")).decode("ascii")
    # Send via stdin so the secret-derived payload is not exposed in host argv.
    shell_line = f"am broadcast -a ADB_INPUT_B64 --es msg {shlex.quote(encoded)}\n"
    proc = subprocess.run(
        ["adb", "-s", serial, "shell"],
        input=shell_line,
        text=True,
        capture_output=True,
        timeout=10,
    )
    return proc.returncode == 0, "adb_keyboard_b64", True, proc.returncode == 0


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
        return {
            "outcome": "unknown",
            "screen_type": "google_password_manager_save_prompt",
            "reason": "google_password_manager_save_prompt",
            "password_required_dialog_present": False,
            "save_password_prompt_present": True,
            "terminal": False,
            "screen_label": "google_password_manager_save_prompt",
        }
    if signals.get("transition_loading") is True:
        return {
            "outcome": "unknown",
            "screen_type": "loading",
            "reason": "loading_transition",
            "password_required_dialog_present": False,
            "save_password_prompt_present": False,
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
    terminal = outcome in {"connected", "needs_2fa", "checkpoint", "login_failed"}
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
) -> dict[str, Any]:
    screens: list[str] = []
    wait_total_ms = 0
    last_observed: dict[str, Any] = {
        "outcome": "unknown",
        "screen_type": "unknown",
        "reason": "post_submit_unknown_after_settling",
        "password_required_dialog_present": False,
        "save_password_prompt_present": False,
        "terminal": False,
        "screen_label": "unknown",
    }
    observations = max(1, int(max_observations or 1))
    save_password_prompt_detected = False
    save_password_prompt_dismissed = False
    save_password_prompt_dismiss_attempt_count = 0
    dismiss_method = ""
    post_dismiss_screen_type = ""
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
        screens.append(str(observed.get("screen_label") or observed.get("screen_type") or "unknown"))
        if observed.get("save_password_prompt_present") is True:
            save_password_prompt_detected = True
            if save_password_prompt_dismiss_attempt_count >= MAX_SAVE_PASSWORD_PROMPT_DISMISS_ATTEMPTS:
                last_observed = {
                    **observed,
                    "outcome": "save_password_prompt_blocking",
                    "screen_type": "google_password_manager_save_prompt",
                    "reason": "save_password_prompt_not_dismissed_after_2_attempts",
                    "terminal": True,
                }
                warnings.append("save_password_prompt_blocking")
                break
            dismiss_method = "back"
            save_password_prompt_dismiss_attempt_count += 1
            if not _dismiss_save_password_prompt_once(d, warnings):
                last_observed = {
                    **observed,
                    "outcome": "save_password_prompt_blocking",
                    "screen_type": "google_password_manager_save_prompt",
                    "reason": "save_password_prompt_dismiss_failed",
                    "terminal": True,
                }
                warnings.append("save_password_prompt_dismiss_failed")
                break
            continue
        if observed.get("password_required_dialog_present") is True:
            break
        if bool(observed.get("terminal")):
            break
    outcome = str(last_observed.get("outcome") or "unknown")
    if outcome == "logged_out":
        last_observed = {
            **last_observed,
            "reason": "session_expired_after_settling",
            "terminal": True,
        }
        warnings.append("post_submit_logged_out_after_settling")
    elif outcome == "unknown":
        if screens and all(screen == "loading" for screen in screens):
            last_observed = {
                **last_observed,
                "outcome": "login_submit_still_loading",
                "screen_type": "loading",
                "reason": "post_submit_loading_timeout",
                "terminal": True,
            }
            warnings.append("post_submit_loading_timeout")
        else:
            last_observed = {
                **last_observed,
                "reason": "post_submit_unknown_after_settling",
            }
            warnings.append("post_submit_unknown_after_settling")
    if save_password_prompt_detected and str(last_observed.get("screen_label") or "") != "google_password_manager_save_prompt":
        save_password_prompt_dismissed = str(last_observed.get("outcome") or "") != "save_password_prompt_blocking"
    if save_password_prompt_dismissed and screens:
        for label in reversed(screens):
            if label != "google_password_manager_save_prompt":
                post_dismiss_screen_type = label
                break
    timings["post_submit_wait_total_ms"] += wait_total_ms
    timings["post_submit_observation_count"] += len(screens)
    return {
        **last_observed,
        "observation_count": len(screens),
        "wait_total_ms": wait_total_ms,
        "screens": screens,
        "final_terminal_screen": screens[-1] if screens else "",
        "post_submit_loading_timeout": str(last_observed.get("reason") or "") == "post_submit_loading_timeout",
        "save_password_prompt_detected": save_password_prompt_detected,
        "save_password_prompt_dismissed": save_password_prompt_dismissed,
        "save_password_prompt_dismiss_attempt_count": save_password_prompt_dismiss_attempt_count,
        "dismiss_method": dismiss_method if save_password_prompt_detected else "",
        "post_dismiss_screen_type": post_dismiss_screen_type,
    }


def _dismiss_save_password_prompt_once(d: Any, warnings: list[str]) -> bool:
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
    save_password_prompt_detected: bool = False,
    save_password_prompt_dismissed: bool = False,
    save_password_prompt_dismiss_attempt_count: int = 0,
    save_password_prompt_dismiss_method: str = "",
    post_dismiss_screen_type: str = "",
    username_replaced: bool = False,
    username_input_confirmed: str = "unknown",
    username_input_result: str = "",
    username_field_focused_before_input: bool | None = None,
    username_clear_method: str = "",
    username_input_method: str = "",
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
                "save_password_prompt_detected": save_password_prompt_detected,
                "save_password_prompt_dismissed": save_password_prompt_dismissed,
                "save_password_prompt_dismiss_attempt_count": save_password_prompt_dismiss_attempt_count,
                "dismiss_method": save_password_prompt_dismiss_method,
                "post_dismiss_screen_type": post_dismiss_screen_type,
                "username_replaced": username_replaced,
                "username_input_confirmed": username_input_confirmed,
                "username_input_result": username_input_result,
                "username_field_focused_before_input": username_field_focused_before_input,
                "username_clear_method": username_clear_method,
                "username_input_method": username_input_method,
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
