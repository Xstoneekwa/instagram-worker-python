"""Resume Instagram login from the email verification code challenge screen."""

from __future__ import annotations

import config
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from device import (
    adb_available,
    ensure_adb_keyboard_ready,
    get_device_serial,
    is_fast_ime_available,
    run_adb_keyboard_b64_input_detailed as run_adb_keyboard_b64_input,
)
from instagram_credentials_runtime_access import SecretValue, redact_credentials_payload
from instagram_login_status_classifier import LoginProbeOutcome, clean_login_probe_metadata
from instagram_login_ui_probe import extract_login_screen_signals_from_hierarchy, probe_login_ui_from_hierarchy

ACTION_EMAIL_CODE_SUBMIT = "email_code_submit"
DEFAULT_POST_SUBMIT_OBSERVATIONS = 10
DEFAULT_POST_SUBMIT_INTERVAL_MS = 1000
DEFAULT_INITIAL_WAIT_MS = 750
POST_SAVE_LOGIN_DISMISS_OBSERVATIONS = 4
POST_SAVE_LOGIN_DISMISS_INTERVAL_MS = 1000
CODE_CONFIRM_SETTLE_MS = 200
_EMAIL_CODE_INVALID_PATTERNS = (
    "incorrect code",
    "invalid code",
    "code you entered is incorrect",
    "code isn't correct",
    "code is not correct",
    "that code didn't work",
    "code expired",
    "expired code",
)

Timer = Callable[[], float]
Sleeper = Callable[[float], None]


@dataclass
class EmailCodeResumeResult:
    ok: bool
    executed: bool
    action: str
    reason: str
    failure_reason: str | None = None
    post_submit_outcome: str | None = None
    post_submit_probe_reason: str | None = None
    post_submit_screen_type: str | None = None
    code_entered: bool = False
    continue_tapped: bool = False
    timings: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    safe_metadata: dict[str, Any] = field(default_factory=dict)


def execute_email_code_challenge_resume(
    d: Any,
    *,
    verification_code: SecretValue,
    expected_username: str | None = None,
    post_submit_wait_ms: int = 0,
    post_submit_observation_interval_ms: int = DEFAULT_POST_SUBMIT_INTERVAL_MS,
    max_post_submit_observations: int = DEFAULT_POST_SUBMIT_OBSERVATIONS,
    timer: Timer | None = None,
    sleeper: Sleeper | None = None,
) -> EmailCodeResumeResult:
    timer = timer or time.perf_counter
    sleeper = sleeper or time.sleep
    total_start = timer()
    timings: dict[str, int] = {"total_ms": 0}
    warnings: list[str] = []

    screen = _observe_current_screen(d, warnings=warnings)
    if not screen.get("email_code_challenge_present"):
        if _password_screen_ready_after_code(d):
            observed = _observe_current_screen(d, warnings=warnings)
            screen_type = str(observed.get("screen_type") or "continue_password_only")
            return _result(
                ok=False,
                executed=True,
                reason="post_code_password_required",
                failure_reason=None,
                post_submit_outcome="post_code_password_required",
                post_submit_probe_reason="post_code_password_required",
                post_submit_screen_type=screen_type,
                timings=_finish_timings(timings, total_start, timer),
                warnings=[*warnings, "email_code_resume_password_screen_ready"],
                safe_metadata=clean_login_probe_metadata(
                    redact_credentials_payload(
                        {
                            "stage": "email_code_resume",
                            "screen_type": screen_type,
                            "post_code_password_required": True,
                            "email_code_challenge_present": False,
                        }
                    )
                ),
            )
        return _result(
            ok=False,
            executed=False,
            reason="email_code_challenge_screen_required",
            failure_reason="email_code_challenge_screen_required",
            timings=_finish_timings(timings, total_start, timer),
            warnings=warnings,
            safe_metadata={"screen_type": screen.get("screen_type"), "stage": "email_code_resume"},
        )

    code_target = _find_code_input_target(d)
    if code_target.get("failure_reason"):
        return _result(
            ok=False,
            executed=False,
            reason=str(code_target["failure_reason"]),
            failure_reason=str(code_target["failure_reason"]),
            timings=_finish_timings(timings, total_start, timer),
            warnings=warnings,
            safe_metadata={"screen_type": "email_code_challenge", "stage": "email_code_resume"},
        )

    try:
        revealed_code = verification_code.reveal_for_login_executor()
    except Exception:
        return _result(
            ok=False,
            executed=False,
            reason="verification_code_secret_invalid",
            failure_reason="verification_code_secret_invalid",
            timings=_finish_timings(timings, total_start, timer),
            warnings=warnings,
            safe_metadata={"screen_type": "email_code_challenge", "stage": "email_code_resume"},
        )

    if not isinstance(revealed_code, str) or not revealed_code.strip():
        return _result(
            ok=False,
            executed=False,
            reason="verification_code_missing",
            failure_reason="verification_code_missing",
            timings=_finish_timings(timings, total_start, timer),
            warnings=warnings,
            safe_metadata={"screen_type": "email_code_challenge", "stage": "email_code_resume"},
        )

    input_start = timer()
    input_result = _input_code_robust(d, code_target["target"], revealed_code.strip(), warnings, sleeper=sleeper)
    code_entered = bool(input_result.get("confirmed"))
    if not code_entered:
        return _result(
            ok=False,
            executed=False,
            reason=str(input_result.get("reason") or "verification_code_input_failed"),
            failure_reason=str(input_result.get("reason") or "verification_code_input_failed"),
            timings=_finish_timings(timings, total_start, timer),
            warnings=warnings,
            safe_metadata={
                "screen_type": "email_code_challenge",
                "stage": "email_code_resume",
                "code_input_method": input_result.get("method"),
                "code_input_confirmed": False,
                "code_input_confirm_method": input_result.get("confirm_method"),
            },
        )
    timings["code_input_ms"] = _elapsed_ms(input_start, timer)

    skip_continue = bool(input_result.get("skip_continue"))
    continue_tapped = False
    if skip_continue:
        warnings.append("email_code_continue_skipped_password_screen_ready")
        observed = _observe_current_screen(d, warnings=warnings)
        screen_type = str(observed.get("screen_type") or "continue_password_only")
        return _result(
            ok=False,
            executed=True,
            reason="post_code_password_required",
            failure_reason=None,
            code_entered=code_entered,
            continue_tapped=False,
            post_submit_outcome="post_code_password_required",
            post_submit_probe_reason="post_code_password_required",
            post_submit_screen_type=screen_type,
            timings=_finish_timings(timings, total_start, timer),
            warnings=warnings,
            safe_metadata=clean_login_probe_metadata(
                redact_credentials_payload(
                    {
                        "stage": "email_code_resume",
                        "screen_type": screen_type,
                        "post_submit_outcome": "post_code_password_required",
                        "post_submit_probe_reason": "post_code_password_required",
                        "post_submit_screen_type": screen_type,
                        "code_entered": code_entered,
                        "code_input_method": input_result.get("method"),
                        "code_input_confirmed": code_entered,
                        "code_input_confirm_method": input_result.get("confirm_method"),
                        "continue_tapped": False,
                        "post_code_password_required": True,
                    }
                )
            ),
        )

    continue_target = _find_continue_target(d)
    if continue_target.get("failure_reason"):
        if _password_screen_ready_after_code(d):
            warnings.append("email_code_continue_missing_password_screen_ready")
            observed = _observe_current_screen(d, warnings=warnings)
            screen_type = str(observed.get("screen_type") or "continue_password_only")
            return _result(
                ok=False,
                executed=True,
                reason="post_code_password_required",
                failure_reason=None,
                code_entered=code_entered,
                continue_tapped=False,
                post_submit_outcome="post_code_password_required",
                post_submit_probe_reason="post_code_password_required",
                post_submit_screen_type=screen_type,
                timings=_finish_timings(timings, total_start, timer),
                warnings=warnings,
                safe_metadata=clean_login_probe_metadata(
                    redact_credentials_payload(
                        {
                            "stage": "email_code_resume",
                            "screen_type": screen_type,
                            "post_code_password_required": True,
                            "code_entered": code_entered,
                            "continue_tapped": False,
                        }
                    )
                ),
            )
        return _result(
            ok=False,
            executed=True,
            reason=str(continue_target["failure_reason"]),
            failure_reason=str(continue_target["failure_reason"]),
            code_entered=code_entered,
            timings=_finish_timings(timings, total_start, timer),
            warnings=warnings,
            safe_metadata={"screen_type": "email_code_challenge", "stage": "email_code_resume"},
        )

    submit_start = timer()
    try:
        _click_target(continue_target["target"])
        continue_tapped = True
    except Exception:
        return _result(
            ok=False,
            executed=True,
            reason="verification_code_continue_failed",
            failure_reason="verification_code_continue_failed",
            code_entered=code_entered,
            timings=_finish_timings(timings, total_start, timer),
            warnings=warnings,
            safe_metadata={"screen_type": "email_code_challenge", "stage": "email_code_resume"},
        )
    timings["continue_tap_ms"] = _elapsed_ms(submit_start, timer)

    observed = _observe_post_submit_settled(
        d,
        expected_username=expected_username,
        timings=timings,
        warnings=warnings,
        timer=timer,
        sleeper=sleeper,
        initial_wait_ms=DEFAULT_INITIAL_WAIT_MS if post_submit_wait_ms <= 0 else post_submit_wait_ms,
        interval_ms=post_submit_observation_interval_ms,
        max_observations=max_post_submit_observations,
    )
    outcome = str(observed.get("outcome") or "unknown")
    ok = outcome == LoginProbeOutcome.CONNECTED.value
    reason = str(observed.get("reason") or outcome)
    if outcome == "verification_pending":
        if observed.get("save_login_info_prompt_detected") and not observed.get("save_login_info_not_now_tapped"):
            outcome = "post_login_finalizing"
            reason = "post_login_finalizing"
            ok = False
        elif str(observed.get("screen_type") or "") == "email_code_challenge":
            outcome = "post_submit_finalization_pending"
            reason = "post_submit_finalization_pending"
            ok = False
        else:
            reason = "verification_code_still_required"
    elif outcome == LoginProbeOutcome.LOGIN_FAILED.value:
        reason = "verification_code_invalid"
    elif outcome == LoginProbeOutcome.LOGGED_OUT.value:
        reason = "logged_out_after_verification_code"
    elif outcome == "unknown":
        reason = "unknown_after_verification_code"

    return _result(
        ok=ok,
        executed=True,
        reason=reason,
        failure_reason=None if ok else reason,
        code_entered=code_entered,
        continue_tapped=continue_tapped,
        post_submit_outcome=outcome,
        post_submit_probe_reason=reason,
        post_submit_screen_type=str(observed.get("screen_type") or observed.get("screen_label") or "unknown"),
        timings=_finish_timings(timings, total_start, timer),
        warnings=warnings,
        safe_metadata=clean_login_probe_metadata(
            redact_credentials_payload(
                {
                    "stage": "email_code_resume",
                    "screen_type": "email_code_challenge",
                    "post_submit_outcome": outcome,
                    "post_submit_probe_reason": reason,
                    "post_submit_screen_type": observed.get("screen_type"),
                    "post_submit_observation_count": observed.get("observation_count"),
                    "post_submit_screens": observed.get("screens"),
                    "final_terminal_screen": observed.get("screen_label"),
                    "save_login_info_prompt_detected": observed.get("save_login_info_prompt_detected"),
                    "save_login_info_not_now_tapped": observed.get("save_login_info_not_now_tapped"),
                    "save_login_info_dismiss_attempt_count": observed.get("save_login_info_dismiss_attempt_count"),
                    "code_entered": code_entered,
                    "code_input_method": input_result.get("method"),
                    "code_input_confirmed": code_entered,
                    "code_input_confirm_method": input_result.get("confirm_method"),
                    "continue_tapped": continue_tapped,
                }
            )
        ),
    )


def _observe_current_screen(d: Any, *, warnings: list[str]) -> dict[str, Any]:
    try:
        try:
            hierarchy_xml = d.dump_hierarchy(compressed=False)
        except TypeError:
            hierarchy_xml = d.dump_hierarchy()
    except Exception as exc:
        warnings.append("email_code_resume_dump_failed")
        return {"screen_type": "unknown", "email_code_challenge_present": False, "error": type(exc).__name__}
    return extract_login_screen_signals_from_hierarchy(str(hierarchy_xml or ""))


def _normalize_hierarchy_text(hierarchy_xml: str) -> str:
    return re.sub(r"\s+", " ", str(hierarchy_xml or "")).lower()


def _hierarchy_has_invalid_email_code_signal(hierarchy_xml: str) -> bool:
    text = _normalize_hierarchy_text(hierarchy_xml)
    return any(pattern in text for pattern in _EMAIL_CODE_INVALID_PATTERNS)


def _classify_email_code_post_submit_hierarchy(
    hierarchy_xml: str,
    *,
    expected_username: str | None = None,
) -> dict[str, Any]:
    from instagram_login_password_form_executor import _classify_post_submit_hierarchy

    classified = _classify_post_submit_hierarchy(hierarchy_xml)
    if classified.get("screen_type") == "email_code_challenge":
        if _hierarchy_has_invalid_email_code_signal(hierarchy_xml):
            return {
                **classified,
                "outcome": LoginProbeOutcome.LOGIN_FAILED.value,
                "reason": "verification_code_invalid",
                "terminal": True,
                "screen_label": "email_code_challenge_invalid",
            }
        if classified.get("terminal"):
            return {
                **classified,
                "outcome": "unknown",
                "reason": "email_code_challenge_transient_after_submit",
                "terminal": False,
                "screen_label": "email_code_challenge_stale",
            }
    if classified.get("save_login_info_prompt_present") is True and expected_username:
        identity = extract_login_screen_signals_from_hierarchy(
            hierarchy_xml,
            expected_username=expected_username,
        )
        classified = {
            **classified,
            "expected_username_present": identity.get("expected_username_present"),
            "expected_username_match_count": identity.get("expected_username_match_count"),
        }
    return classified


def _observe_post_submit_settled(
    d: Any,
    *,
    expected_username: str | None = None,
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
    save_login_info_prompt_detected = False
    save_login_info_not_now_tapped = False
    save_login_info_dismiss_attempt_count = 0
    last_observed: dict[str, Any] = {
        "outcome": "unknown",
        "screen_type": "unknown",
        "reason": "unknown_after_verification_code",
        "screen_label": "unknown",
        "terminal": False,
    }
    observations = max(1, int(max_observations or 1))
    for index in range(observations):
        delay_ms = int(initial_wait_ms if index == 0 and initial_wait_ms > 0 else interval_ms)
        if delay_ms > 0:
            sleeper(delay_ms / 1000.0)
            wait_total_ms += delay_ms
        try:
            try:
                hierarchy_xml = d.dump_hierarchy(compressed=False)
            except TypeError:
                hierarchy_xml = d.dump_hierarchy()
        except Exception:
            warnings.append("post_submit_dump_failed")
            continue
        observed = _classify_email_code_post_submit_hierarchy(
            str(hierarchy_xml or ""),
            expected_username=expected_username,
        )
        screen_label = str(observed.get("screen_label") or observed.get("screen_type") or "unknown")
        screens.append(screen_label)
        last_observed = {**observed, "screen_label": screen_label}
        if observed.get("save_login_info_prompt_present") is True:
            save_login_info_prompt_detected = True
            warnings.append("instagram_save_login_info_prompt_detected")
            if expected_username and observed.get("expected_username_present") is False:
                last_observed = {
                    **last_observed,
                    "outcome": "save_login_info_prompt_blocking",
                    "screen_type": "save_login_info_prompt",
                    "reason": "save_login_info_identity_not_confirmed",
                    "terminal": True,
                }
                warnings.append("save_login_info_identity_not_confirmed")
                break
            if save_login_info_dismiss_attempt_count >= 2:
                last_observed = {
                    **last_observed,
                    "outcome": "save_login_info_prompt_blocking",
                    "screen_type": "save_login_info_prompt",
                    "reason": "save_login_info_prompt_not_dismissed_after_2_attempts",
                    "terminal": True,
                }
                warnings.append("save_login_info_prompt_blocking")
                break
            save_login_info_dismiss_attempt_count += 1
            if not _dismiss_save_login_info_prompt_once(d, warnings):
                last_observed = {
                    **last_observed,
                    "outcome": "save_login_info_prompt_blocking",
                    "screen_type": "save_login_info_prompt",
                    "reason": "save_login_info_prompt_dismiss_failed",
                    "terminal": True,
                }
                warnings.append("save_login_info_prompt_dismiss_failed")
                break
            save_login_info_not_now_tapped = True
            warnings.append("instagram_save_login_info_prompt_not_now")
            continue
        if observed.get("terminal"):
            break

    if (
        save_login_info_not_now_tapped
        and str(last_observed.get("outcome") or "") != LoginProbeOutcome.CONNECTED.value
    ):
        from instagram_login_password_form_executor import _observe_post_dismiss_final_settled

        timings.setdefault("post_submit_dump_ms", 0)
        final_observed = _observe_post_dismiss_final_settled(
            d,
            timings=timings,
            timer=timer,
            sleeper=sleeper,
            interval_ms=POST_SAVE_LOGIN_DISMISS_INTERVAL_MS,
            max_observations=POST_SAVE_LOGIN_DISMISS_OBSERVATIONS,
        )
        final_screens = list(final_observed.get("screens") or [])
        if final_screens:
            screens.extend(final_screens)
            wait_total_ms += int(final_observed.get("wait_total_ms") or 0)
        final_last = dict(final_observed.get("observed") or {})
        if final_last:
            screen_label = str(
                final_last.get("screen_label") or final_last.get("screen_type") or "unknown"
            )
            last_observed = {**final_last, "screen_label": screen_label}
            if str(last_observed.get("outcome") or "") == LoginProbeOutcome.CONNECTED.value:
                last_observed["terminal"] = True
    elif (
        str(last_observed.get("outcome") or "") == "verification_pending"
        and str(last_observed.get("screen_type") or "") == "email_code_challenge"
        and (save_login_info_prompt_detected or save_login_info_not_now_tapped or "save_login_info_prompt" in screens)
    ):
        last_observed = {
            **last_observed,
            "outcome": "post_login_finalizing",
            "reason": "post_login_finalizing",
            "terminal": False,
        }

    timings["post_submit_wait_total_ms"] = wait_total_ms
    timings["post_submit_observation_count"] = len(screens)
    return {
        **last_observed,
        "screens": screens,
        "observation_count": len(screens),
        "save_login_info_prompt_detected": save_login_info_prompt_detected,
        "save_login_info_not_now_tapped": save_login_info_not_now_tapped,
        "save_login_info_dismiss_attempt_count": save_login_info_dismiss_attempt_count,
    }


def _find_code_input_target(d: Any) -> dict[str, Any]:
    selectors = (
        {"text": "Enter code"},
        {"description": "Enter code"},
        {"className": "android.widget.EditText"},
    )
    return _find_unique_target(d, selectors, missing_reason="verification_code_field_not_found")


def _find_continue_target(d: Any) -> dict[str, Any]:
    selectors = (
        {"text": "Continue"},
        {"description": "Continue"},
    )
    return _find_unique_target(d, selectors, missing_reason="verification_code_continue_not_found")


def _find_unique_target(d: Any, selectors: tuple[dict[str, str], ...], *, missing_reason: str) -> dict[str, Any]:
    for selector in selectors:
        try:
            obj = d(**selector)
        except Exception:
            continue
        try:
            if obj.exists(timeout=0):
                return {"target": obj, "failure_reason": None}
        except Exception:
            continue
    return {"target": None, "failure_reason": missing_reason}


def _input_code_robust(d: Any, target: Any, value: str, warnings: list[str], *, sleeper: Sleeper) -> dict[str, Any]:
    target.click()
    sleeper(0.15)
    clear = getattr(target, "clear_text", None)
    if callable(clear):
        clear()
        sleeper(0.1)

    warnings.append("verification_code_input_method_attempted:set_text")
    set_text = getattr(target, "set_text", None)
    if callable(set_text):
        try:
            set_text(value)
            sleeper(CODE_CONFIRM_SETTLE_MS / 1000.0)
            if _code_input_confirmed(d):
                return {
                    "confirmed": True,
                    "method": "set_text",
                    "confirm_method": "hierarchy_code_field_non_empty",
                    "reason": "",
                }
            warnings.append("verification_code_set_text_not_confirmed")
            if _password_screen_ready_after_code(d):
                return {
                    "confirmed": True,
                    "method": "set_text",
                    "confirm_method": "post_code_password_screen_detected",
                    "reason": "",
                    "skip_continue": True,
                }
        except Exception:
            warnings.append("verification_code_set_text_failed")
    else:
        warnings.append("verification_code_set_text_unavailable")

    target.click()
    sleeper(0.1)
    clear = getattr(target, "clear_text", None)
    if callable(clear):
        clear()
        sleeper(0.1)

    warnings.append("verification_code_input_fallback_adb_keyboard_b64_attempted")
    serial = _direct_device_serial(d)
    fast_ime_id = str(getattr(config, "FAST_IME", "") or "").strip()
    if not adb_available():
        warnings.append("verification_code_adb_not_available")
        if _password_screen_ready_after_code(d):
            return {
                "confirmed": True,
                "method": "set_text",
                "confirm_method": "post_code_password_screen_detected",
                "reason": "",
                "skip_continue": True,
            }
        return {
            "confirmed": False,
            "method": "set_text",
            "confirm_method": "adb_not_available",
            "reason": "adb_not_available",
        }
    ready_state = ensure_adb_keyboard_ready(serial, fast_ime_id=fast_ime_id)
    if not (serial and fast_ime_id and ready_state.get("reason") == "adb_keyboard_ready"):
        unavailable_reason = str(ready_state.get("reason") or "adb_keyboard_unavailable")
        warnings.append(f"verification_code_{unavailable_reason}")
        if _password_screen_ready_after_code(d):
            return {
                "confirmed": True,
                "method": "set_text",
                "confirm_method": "post_code_password_screen_detected",
                "reason": "",
                "skip_continue": True,
            }
        return {
            "confirmed": False,
            "method": "set_text",
            "confirm_method": unavailable_reason,
            "reason": "adb_keyboard_unavailable",
        }
    try:
        adb_result = _adb_input_result_dict(
            run_adb_keyboard_b64_input(
                serial,
                value,
                fast_ime_id=fast_ime_id,
            )
        )
    except Exception:
        adb_result = {
            "command_ok": False,
            "method": "adb_keyboard_b64",
            "switch_ok": False,
            "broadcast_ok": False,
            "reason": "adb_keyboard_exception",
        }
    command_ok = bool(adb_result.get("command_ok"))
    switch_ok = bool(adb_result.get("switch_ok"))
    broadcast_ok = bool(adb_result.get("broadcast_ok"))
    method = str(adb_result.get("method") or "adb_keyboard_b64")
    if not (command_ok and switch_ok and broadcast_ok):
        failure_reason = str(adb_result.get("reason") or "adb_keyboard_b64_failed")
        warnings.append(f"verification_code_{failure_reason}")
        if _password_screen_ready_after_code(d):
            return {
                "confirmed": True,
                "method": method,
                "confirm_method": "post_code_password_screen_detected",
                "reason": "",
                "skip_continue": True,
            }
        return {
            "confirmed": False,
            "method": method,
            "confirm_method": failure_reason,
            "reason": failure_reason,
        }
    sleeper(CODE_CONFIRM_SETTLE_MS / 1000.0)
    if _code_input_confirmed(d):
        warnings.append("verification_code_input_confirmed_after_fallback")
        return {
            "confirmed": True,
            "method": method or "adb_keyboard_b64",
            "confirm_method": "hierarchy_code_field_non_empty",
            "reason": "",
        }
    warnings.append("verification_code_adb_keyboard_b64_not_confirmed")
    if _password_screen_ready_after_code(d):
        return {
            "confirmed": True,
            "method": method or "adb_keyboard_b64",
            "confirm_method": "post_code_password_screen_detected",
            "reason": "",
            "skip_continue": True,
        }
    return {
        "confirmed": False,
        "method": method or "adb_keyboard_b64",
        "confirm_method": "hierarchy_code_field_empty",
        "reason": "verification_code_input_empty",
    }


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


def _password_screen_ready_after_code(d: Any) -> bool:
    try:
        try:
            hierarchy_xml = d.dump_hierarchy(compressed=False)
        except TypeError:
            hierarchy_xml = d.dump_hierarchy()
    except Exception:
        return False
    signals = extract_login_screen_signals_from_hierarchy(str(hierarchy_xml or ""))
    screen_type = str(signals.get("screen_type") or "")
    if screen_type in {"continue_password_only", "login_form_empty", "login_form_prefilled_username"}:
        return bool(signals.get("has_password_field")) and bool(signals.get("has_login_button"))
    return False


def _code_input_confirmed(d: Any) -> bool:
    try:
        try:
            hierarchy_xml = d.dump_hierarchy(compressed=False)
        except TypeError:
            hierarchy_xml = d.dump_hierarchy()
    except Exception:
        return False
    signals = extract_login_screen_signals_from_hierarchy(str(hierarchy_xml or ""))
    if signals.get("email_code_challenge_present") is not True:
        return True
    return _hierarchy_has_filled_code_field(str(hierarchy_xml or ""))


def _hierarchy_has_filled_code_field(hierarchy_xml: str) -> bool:
    text = str(hierarchy_xml or "")
    placeholders = ("enter code", "code", "security code", "confirmation code")
    for node in re.findall(r"<node\b[^>]*>", text, flags=re.I):
        if not re.search(r"""class\s*=\s*['"]android\.widget\.EditText['"]""", node, flags=re.I):
            continue
        for attr in ("text", "content-desc"):
            match = re.search(rf"""{attr}\s*=\s*(['"])(.*?)\1""", node, flags=re.I)
            if not match:
                continue
            value = match.group(2).strip().lower()
            if value and all(placeholder not in value for placeholder in placeholders):
                return True
    return False


def _direct_device_serial(d: Any) -> str:
    serial = get_device_serial(d)
    return str(serial or getattr(config, "DEVICE_SERIAL", "") or "")


def _click_target(target: Any) -> None:
    target.click()


def _dismiss_save_login_info_prompt_once(d: Any, warnings: list[str]) -> bool:
    selectors = (
        {"text": "Not now"},
        {"description": "Not now"},
        {"text": "Pas maintenant"},
        {"description": "Pas maintenant"},
    )
    for selector in selectors:
        try:
            obj = d(**selector)
        except Exception:
            continue
        try:
            if obj.exists(timeout=0):
                obj.click()
                return True
        except Exception:
            continue
    warnings.append("not_now_button_not_found")
    return False


def _elapsed_ms(start: float, timer: Timer) -> int:
    return max(0, int((timer() - start) * 1000))


def _finish_timings(timings: dict[str, int], total_start: float, timer: Timer) -> dict[str, int]:
    timings["total_ms"] = _elapsed_ms(total_start, timer)
    return dict(timings)


def _result(
    *,
    ok: bool,
    executed: bool,
    reason: str,
    failure_reason: str | None = None,
    code_entered: bool = False,
    continue_tapped: bool = False,
    post_submit_outcome: str | None = None,
    post_submit_probe_reason: str | None = None,
    post_submit_screen_type: str | None = None,
    timings: dict[str, int],
    warnings: list[str],
    safe_metadata: dict[str, Any],
) -> EmailCodeResumeResult:
    return EmailCodeResumeResult(
        ok=ok,
        executed=executed,
        action=ACTION_EMAIL_CODE_SUBMIT,
        reason=reason,
        failure_reason=failure_reason,
        code_entered=code_entered,
        continue_tapped=continue_tapped,
        post_submit_outcome=post_submit_outcome,
        post_submit_probe_reason=post_submit_probe_reason,
        post_submit_screen_type=post_submit_screen_type,
        timings=timings,
        warnings=warnings,
        safe_metadata=safe_metadata,
    )
