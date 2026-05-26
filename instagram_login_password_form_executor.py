"""Controlled Instagram login form credential executor.

Entry 2E-5I fills username/password only after the caller has prevalidated the
screen as `login_form_empty` or a controlled password-only continuation screen.
It has no runner hook, no Supabase write, no status publish, no unbounded retry,
and never stores or logs the password.
"""

from __future__ import annotations

import base64
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import config
from device import get_current_ime, is_fast_ime_available, set_ime
from instagram_credentials_runtime_access import SecretValue, redact_credentials_payload
from instagram_login_status_classifier import clean_login_probe_metadata
from instagram_login_ui_probe import extract_login_screen_signals_from_hierarchy, probe_login_ui_from_hierarchy


ACTION_LOGIN_FORM_SUBMIT = "login_form_submit"
NO_ACTION = "no_action"
MAX_POST_SUBMIT_WAIT_MS = 3000
MAX_PASSWORD_REQUIRED_RETRY = 1

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
    overlay_recovery_allowed = _overlay_recovery_allowed(prevalidated_signals)
    password_required_retry_count = 0
    password_required_dialog_detected = False
    password_required_retry_attempted = False
    password_refill_attempted = False
    second_submit_executed = False
    password_input_method_used = ""
    password_field_focused_before_input: bool | None = None
    input_call_reported_success = False
    password_field_non_empty_confirmed = "unknown"
    password_input_failure_reason = ""

    start = timer()
    targets = _resolve_login_form_targets(d, password_only_mode=password_only_mode)
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

    try:
        revealed_password = password.reveal_for_login_executor()
    except Exception:
        return _failure(
            "password_secret_invalid",
            timings=timings,
            warnings=warnings,
            total_start=total_start,
            timer=timer,
            expected_username=username,
        )
    if not isinstance(revealed_password, str):
        return _failure(
            "password_secret_invalid",
            timings=timings,
            warnings=warnings,
            total_start=total_start,
            timer=timer,
            expected_username=username,
        )
    if not revealed_password:
        return _failure(
            "password_secret_missing",
            timings=timings,
            warnings=warnings,
            total_start=total_start,
            timer=timer,
            expected_username=username,
        )

    username_entered = False
    password_entered = False
    submit_tapped = False

    try:
        start = timer()
        if not password_only_mode:
            _focus_clear_and_set_text(targets["username"], username)
            username_entered = True
        timings["username_input_ms"] = _elapsed_ms(start, timer())

        start = timer()
        input_result = _input_password_robust(d, targets["password"], revealed_password, warnings)
        password_input_method_used = input_result["input_method_used"]
        password_field_focused_before_input = input_result["password_field_focused_before_input"]
        input_call_reported_success = input_result["input_call_reported_success"]
        password_field_non_empty_confirmed = input_result["password_field_non_empty_confirmed"]
        password_input_failure_reason = input_result["reason"]
        password_entered = bool(input_call_reported_success)
        timings["password_input_ms"] = _elapsed_ms(start, timer())
        if not input_call_reported_success:
            raise RuntimeError("password_input_failed")
    except Exception:
        timings["total_ms"] = _elapsed_ms(total_start, timer())
        return _result(
            ok=False,
            executed=False,
            action=ACTION_LOGIN_FORM_SUBMIT,
            reason=password_input_failure_reason or "input_failed",
            failure_reason=password_input_failure_reason or "input_failed",
            username_entered=username_entered,
            password_entered=password_entered,
            timings=timings,
            warnings=warnings,
            expected_username=username,
            input_method_used=password_input_method_used,
            password_field_focused_before_input=password_field_focused_before_input,
            input_action_reported_success=input_call_reported_success,
            password_field_non_empty_confirmed=password_field_non_empty_confirmed,
        )

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
            password_field_focused_before_input=password_field_focused_before_input,
            input_action_reported_success=input_call_reported_success,
            password_field_non_empty_confirmed=password_field_non_empty_confirmed,
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
            )

    timings["post_submit_wait_ms"] = wait_ms
    if wait_ms > 0:
        sleeper(wait_ms / 1000.0)

    post_submit_screen_type = "unknown"
    post_submit_probe_reason = "post_submit_dump_skipped"
    post_submit_outcome = "unknown"
    failure_reason: str | None = None

    if dump_after_submit:
        try:
            start = timer()
            hierarchy_xml = _dump_hierarchy_once(d)
            timings["post_submit_dump_ms"] = _elapsed_ms(start, timer())
            observed = _classify_post_submit_hierarchy(hierarchy_xml)
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
                            start = timer()
                            hierarchy_xml = _dump_hierarchy_once(d)
                            timings["post_submit_dump_ms"] += _elapsed_ms(start, timer())
                            observed = _classify_post_submit_hierarchy(hierarchy_xml)
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
        password_field_focused_before_input=password_field_focused_before_input,
        input_action_reported_success=input_call_reported_success,
        password_field_non_empty_confirmed=password_field_non_empty_confirmed,
        password_required_dialog_detected=password_required_dialog_detected,
        password_required_retry_attempted=password_required_retry_attempted,
        password_required_retry_count=password_required_retry_count,
        password_refill_attempted=password_refill_attempted,
        second_submit_executed=second_submit_executed,
    )


def _prevalidated_signal_failure(signals: dict | None) -> str:
    if not isinstance(signals, dict) or signals.get("screen_type") not in {"login_form_empty", "continue_password_only"}:
        return "login_form_not_validated"
    if signals.get("ambiguous_login_form") is True or signals.get("ambiguous") is True:
        return "ambiguous_login_form"
    if signals.get("screen_type") == "login_form_empty" and signals.get("has_username_field") is not True:
        return "username_field_not_found"
    if signals.get("screen_type") == "continue_password_only" and not signals.get("suggested_username"):
        return "expected_username_missing"
    if signals.get("has_password_field") is not True:
        return "password_field_not_found"
    if signals.get("has_login_button") is not True:
        return "login_button_not_found"
    return ""


def _resolve_login_form_targets(d: Any, *, password_only_mode: bool = False) -> dict[str, Any]:
    username = _find_unique_target(
        d,
        (
            {"text": "Username, email or mobile number"},
            {"description": "Username, email or mobile number"},
            {"text": "Username"},
            {"description": "Username"},
        ),
        missing_reason="username_field_not_found",
    )
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
    return _find_unique_target(
        d,
        (
            {"text": "Password"},
            {"description": "Password"},
        ),
        missing_reason="password_field_not_found",
    )


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


def _focus_clear_and_set_text(target: Any, value: str) -> None:
    _click_target(target)
    clear_text = getattr(target, "clear_text", None)
    if callable(clear_text):
        clear_text()
    else:
        clear = getattr(target, "clear", None)
        if callable(clear):
            clear()
    set_text = getattr(target, "set_text", None)
    if not callable(set_text):
        raise RuntimeError("set_text_unavailable")
    set_text(value)


def _click_target(target: Any) -> None:
    click = getattr(target, "click", None)
    if not callable(click):
        raise RuntimeError("target_click_unavailable")
    click()


def _input_password_robust(d: Any, target: Any, value: str, warnings: list[str]) -> dict[str, Any]:
    focused_before = _focus_password_target(d, target, warnings)
    _clear_target_text(target)
    time.sleep(0.1)
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
            return _password_input_result(
                method_tag or "fast_ime",
                focused_before,
                True,
                _password_field_non_empty_state(target),
                "",
            )
        warnings.append("fast_ime_password_input_failed")
        if not switch_ok:
            warnings.append("fast_ime_switch_failed")

    set_text = getattr(target, "set_text", None)
    if callable(set_text):
        try:
            set_text(value)
            return _password_input_result(
                "set_text",
                focused_before,
                True,
                _password_field_non_empty_state(target),
                "",
            )
        except Exception:
            warnings.append("set_text_password_input_failed")
    return _password_input_result("", focused_before, False, "unknown", "password_input_failed")


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
) -> dict[str, Any]:
    return {
        "input_method_used": method,
        "password_field_focused_before_input": focused_before,
        "input_call_reported_success": call_success,
        "password_field_non_empty_confirmed": non_empty_state,
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
        }
    return {
        "outcome": str(probe.outcome.value),
        "screen_type": str(probe.outcome.value),
        "reason": str(probe.reason or "post_submit_observed"),
        "password_required_dialog_present": False,
    }


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
        "post_submit_dump_ms": 0,
        "total_ms": 0,
    }


def _clamp_ms(value: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = 0
    return min(maximum, max(0, parsed))


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
) -> LoginPasswordExecutionResult:
    safe_metadata = clean_login_probe_metadata(
        redact_credentials_payload(
            {
                "source": "login_password_form_executor",
                "action": action,
                "reason": reason,
                "failure_reason": failure_reason,
                "expected_username": expected_username,
                "post_submit_outcome": post_submit_outcome,
                "password_only_mode": password_only_mode,
                "input_method_used": input_method_used,
                "password_field_focused_before_input": password_field_focused_before_input,
                "input_action_reported_success": input_action_reported_success,
                "password_field_non_empty_confirmed": password_field_non_empty_confirmed,
                "password_required_dialog_detected": password_required_dialog_detected,
                "password_required_retry_attempted": password_required_retry_attempted,
                "password_required_retry_count": password_required_retry_count,
                "password_refill_attempted": password_refill_attempted,
                "second_submit_executed": second_submit_executed,
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
