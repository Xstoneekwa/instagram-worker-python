"""Resume Instagram login from the email verification code challenge screen."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from instagram_credentials_runtime_access import SecretValue, redact_credentials_payload
from instagram_login_status_classifier import LoginProbeOutcome, clean_login_probe_metadata
from instagram_login_ui_probe import extract_login_screen_signals_from_hierarchy, probe_login_ui_from_hierarchy

ACTION_EMAIL_CODE_SUBMIT = "email_code_submit"
DEFAULT_POST_SUBMIT_OBSERVATIONS = 4
DEFAULT_POST_SUBMIT_INTERVAL_MS = 1000
DEFAULT_INITIAL_WAIT_MS = 750

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
    try:
        _focus_and_set_text(code_target["target"], revealed_code.strip(), d=d, sleeper=sleeper)
        code_entered = True
    except Exception:
        return _result(
            ok=False,
            executed=False,
            reason="verification_code_input_failed",
            failure_reason="verification_code_input_failed",
            timings=_finish_timings(timings, total_start, timer),
            warnings=warnings,
            safe_metadata={"screen_type": "email_code_challenge", "stage": "email_code_resume"},
        )
    timings["code_input_ms"] = _elapsed_ms(input_start, timer)

    continue_target = _find_continue_target(d)
    if continue_target.get("failure_reason"):
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
                    "code_entered": code_entered,
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
    from instagram_login_password_form_executor import _classify_post_submit_hierarchy

    screens: list[str] = []
    wait_total_ms = 0
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
        observed = _classify_post_submit_hierarchy(str(hierarchy_xml or ""))
        screen_label = str(observed.get("screen_label") or observed.get("screen_type") or "unknown")
        screens.append(screen_label)
        last_observed = {**observed, "screen_label": screen_label}
        if observed.get("terminal"):
            break

    timings["post_submit_wait_total_ms"] = wait_total_ms
    timings["post_submit_observation_count"] = len(screens)
    return {
        **last_observed,
        "screens": screens,
        "observation_count": len(screens),
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


def _focus_and_set_text(target: Any, value: str, *, d: Any, sleeper: Sleeper) -> None:
    target.click()
    sleeper(0.15)
    clear = getattr(target, "clear_text", None)
    if callable(clear):
        clear()
    set_text = getattr(target, "set_text", None)
    if not callable(set_text):
        raise RuntimeError("set_text_unavailable")
    set_text(value)
    info = getattr(target, "info", None)
    current = ""
    if isinstance(info, dict):
        current = str(info.get("text") or "")
    if not current.strip():
        raise RuntimeError("verification_code_input_empty")


def _click_target(target: Any) -> None:
    target.click()


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
