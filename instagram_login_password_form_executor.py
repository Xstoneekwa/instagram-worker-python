"""Controlled Instagram login form credential executor.

Entry 2E-5I fills username/password only after the caller has prevalidated the
screen as `login_form_empty` or a controlled password-only continuation screen.
It has no runner hook, no Supabase write, no status publish, no unbounded retry,
and never stores or logs the password.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from instagram_credentials_runtime_access import SecretValue, redact_credentials_payload
from instagram_login_status_classifier import clean_login_probe_metadata
from instagram_login_ui_probe import probe_login_ui_from_hierarchy


ACTION_LOGIN_FORM_SUBMIT = "login_form_submit"
NO_ACTION = "no_action"
MAX_POST_SUBMIT_WAIT_MS = 3000

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
        _focus_clear_and_set_text(targets["password"], revealed_password)
        password_entered = True
        timings["password_input_ms"] = _elapsed_ms(start, timer())
    except Exception:
        timings["total_ms"] = _elapsed_ms(total_start, timer())
        return _result(
            ok=False,
            executed=False,
            action=ACTION_LOGIN_FORM_SUBMIT,
            reason="input_failed",
            failure_reason="input_failed",
            username_entered=username_entered,
            password_entered=password_entered,
            timings=timings,
            warnings=warnings,
            expected_username=username,
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
            probe = probe_login_ui_from_hierarchy(hierarchy_xml, stage="login_password_form_executor")
            post_submit_outcome = str(probe.outcome.value)
            post_submit_screen_type = post_submit_outcome
            post_submit_probe_reason = str(probe.reason or "post_submit_observed")
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

    password = _find_unique_target(
        d,
        (
            {"text": "Password"},
            {"description": "Password"},
        ),
        missing_reason="password_field_not_found",
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
            return {"target": None, "failure_reason": "ambiguous_login_form"}
        if count == 1:
            matches.append(selector)
    if len(matches) > 1:
        return {"target": None, "failure_reason": "ambiguous_login_form"}
    if not matches:
        return {"target": None, "failure_reason": missing_reason}
    return {"target": matches[0], "failure_reason": ""}


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
