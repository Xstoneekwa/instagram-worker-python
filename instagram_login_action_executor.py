"""Controlled executor for safe Instagram pre-login screen actions.

Entry 2E-5F applies an already-computed login screen router decision. It can
tap only the explicit Continue / Use another profile targets, then observes the
screen once. It never types credentials, reads Vault, publishes HTTP, or decides
business overrides.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from instagram_login_status_classifier import FORBIDDEN_METADATA_KEYS, clean_login_probe_metadata
from instagram_login_ui_probe import extract_login_screen_signals_from_hierarchy

ACTION_CONTINUE = "tap_continue"
ACTION_USE_ANOTHER_PROFILE = "tap_use_another_profile"
NO_ACTION = "no_action"

ALLOWED_DECISION_ACTIONS = {
    "continue_expected_account": ("Continue", ACTION_CONTINUE),
    "use_another_profile_previous_account_stopped": ("Use another profile", ACTION_USE_ANOTHER_PROFILE),
}
NO_ACTION_DECISIONS = {
    "block_wrong_suggested_account",
    "start_login_form_flow",
    "unknown_no_action",
}
MAX_POST_ACTION_WAIT_MS = 1500

Timer = Callable[[], float]
Sleeper = Callable[[float], None]


@dataclass(frozen=True)
class LoginActionExecutionResult:
    ok: bool
    executed: bool
    action: str
    decision: str
    reason: str
    failure_reason: str = ""
    post_action_screen_type: str = "unknown"
    post_action_probe_reason: str = ""
    post_action_signals: dict[str, Any] = field(default_factory=dict)
    timings: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


def execute_login_screen_decision(
    d: Any,
    decision: Any,
    *,
    post_action_wait_ms: int = 500,
    dump_after_action: bool = True,
    tap_timeout_ms: int = 1500,
    timer: Timer | None = None,
    sleeper: Sleeper | None = None,
) -> LoginActionExecutionResult:
    """Execute one controlled pre-login action and observe the result.

    The caller owns the business decision. This executor only applies supported
    router decisions and does at most one tap, with no retry by default.
    """

    timer = timer or time.perf_counter
    sleeper = sleeper or time.sleep
    decision_value = _decision_value(decision)
    timings = _empty_timings()
    warnings: list[str] = []
    total_start = timer()
    wait_ms = _clamp_ms(post_action_wait_ms, MAX_POST_ACTION_WAIT_MS)
    tap_timeout = _clamp_ms(tap_timeout_ms, MAX_POST_ACTION_WAIT_MS)

    if wait_ms != post_action_wait_ms:
        warnings.append("post_action_wait_ms_clamped")
    if tap_timeout != tap_timeout_ms:
        warnings.append("tap_timeout_ms_clamped")

    if decision_value in NO_ACTION_DECISIONS:
        timings["total_ms"] = _elapsed_ms(total_start, timer())
        return _result(
            ok=True,
            executed=False,
            action=NO_ACTION,
            decision=decision_value,
            reason="decision_requires_no_action",
            timings=timings,
            warnings=warnings,
        )

    if decision_value not in ALLOWED_DECISION_ACTIONS:
        timings["total_ms"] = _elapsed_ms(total_start, timer())
        return _result(
            ok=False,
            executed=False,
            action=NO_ACTION,
            decision=decision_value,
            reason="unsupported_decision",
            failure_reason="unsupported_decision",
            timings=timings,
            warnings=warnings,
        )

    target_text, action = ALLOWED_DECISION_ACTIONS[decision_value]
    start = timer()
    selector_result = _find_exact_accessibility_target(d, target_text)
    timings["target_lookup_ms"] = _elapsed_ms(start, timer())

    if selector_result["failure_reason"]:
        timings["total_ms"] = _elapsed_ms(total_start, timer())
        return _result(
            ok=False,
            executed=False,
            action=action,
            decision=decision_value,
            reason=selector_result["failure_reason"],
            failure_reason=selector_result["failure_reason"],
            timings=timings,
            warnings=warnings,
        )

    target = selector_result["target"]
    try:
        start = timer()
        _click_target(target, tap_timeout_ms=tap_timeout)
        timings["tap_ms"] = _elapsed_ms(start, timer())
    except Exception:
        timings["tap_ms"] = _elapsed_ms(start, timer())
        timings["total_ms"] = _elapsed_ms(total_start, timer())
        return _result(
            ok=False,
            executed=False,
            action=action,
            decision=decision_value,
            reason="tap_failed",
            failure_reason="tap_failed",
            timings=timings,
            warnings=warnings,
        )

    timings["post_action_wait_ms"] = wait_ms
    if wait_ms > 0:
        sleeper(wait_ms / 1000.0)

    post_action_signals: dict[str, Any] = {}
    post_action_screen_type = "unknown"
    post_action_probe_reason = "post_action_dump_skipped"
    failure_reason = ""

    if dump_after_action:
        try:
            start = timer()
            hierarchy_xml = _dump_hierarchy_once(d)
            timings["post_action_dump_ms"] = _elapsed_ms(start, timer())
            post_action_signals = _safe_login_screen_signals(hierarchy_xml)
            post_action_screen_type = str(post_action_signals.get("screen_type") or "unknown")
            post_action_probe_reason = "post_action_observed"
        except Exception:
            timings["post_action_dump_ms"] = _elapsed_ms(start, timer())
            post_action_probe_reason = "post_action_dump_failed"
            failure_reason = "post_action_dump_failed"

    if action == ACTION_USE_ANOTHER_PROFILE and post_action_screen_type == "unknown":
        warnings.append("post_action_screen_unknown")

    timings["total_ms"] = _elapsed_ms(total_start, timer())
    return _result(
        ok=not failure_reason,
        executed=True,
        action=action,
        decision=decision_value,
        reason="action_executed",
        failure_reason=failure_reason,
        post_action_screen_type=post_action_screen_type,
        post_action_probe_reason=post_action_probe_reason,
        post_action_signals=post_action_signals,
        timings=timings,
        warnings=warnings,
    )


def _decision_value(decision: Any) -> str:
    return str(getattr(decision, "decision", decision) or "").strip()


def _find_exact_accessibility_target(d: Any, target_text: str) -> dict[str, Any]:
    candidates: list[Any] = []
    for selector_kwargs in ({"text": target_text}, {"description": target_text}):
        try:
            selector = d(**selector_kwargs)
        except Exception:
            continue
        count = _selector_count(selector)
        if count > 1:
            return {"target": None, "failure_reason": "ambiguous_target_button"}
        if count == 1:
            candidates.append(selector)

    if len(candidates) > 1:
        return {"target": None, "failure_reason": "ambiguous_target_button"}
    if not candidates:
        return {"target": None, "failure_reason": "target_button_not_found"}
    return {"target": candidates[0], "failure_reason": ""}


def _selector_count(selector: Any) -> int:
    count_attr = getattr(selector, "count", None)
    if callable(count_attr):
        try:
            return max(0, int(count_attr))
        except TypeError:
            try:
                return max(0, int(count_attr()))
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


def _click_target(target: Any, *, tap_timeout_ms: int) -> None:
    click = getattr(target, "click", None)
    if not callable(click):
        raise RuntimeError("target_click_unavailable")
    try:
        click(timeout=tap_timeout_ms / 1000.0)
    except TypeError:
        click()


def _dump_hierarchy_once(d: Any) -> str:
    try:
        return str(d.dump_hierarchy(compressed=False) or "")
    except TypeError:
        return str(d.dump_hierarchy() or "")


def _safe_login_screen_signals(hierarchy_xml: str | None) -> dict[str, Any]:
    signals = extract_login_screen_signals_from_hierarchy(hierarchy_xml)
    # The executor exposes the resulting screen shape, not credential-field
    # details or raw hierarchy content.
    signals.pop("has_password_field", None)
    signals["suggested_username"] = _safe_signal_text(signals.get("suggested_username"))
    return clean_login_probe_metadata(dict(signals))


def _safe_signal_text(value: Any) -> str:
    text = str(value or "").strip()
    lowered = text.lower()
    if not text:
        return ""
    if any(forbidden in lowered for forbidden in FORBIDDEN_METADATA_KEYS):
        return ""
    return text


def _empty_timings() -> dict[str, int]:
    return {
        "target_lookup_ms": 0,
        "tap_ms": 0,
        "post_action_wait_ms": 0,
        "post_action_dump_ms": 0,
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


def _result(
    *,
    ok: bool,
    executed: bool,
    action: str,
    decision: str,
    reason: str,
    failure_reason: str = "",
    post_action_screen_type: str = "unknown",
    post_action_probe_reason: str = "",
    post_action_signals: dict[str, Any] | None = None,
    timings: dict[str, int] | None = None,
    warnings: list[str] | None = None,
) -> LoginActionExecutionResult:
    metadata = clean_login_probe_metadata(
        {
            "source": "login_action_executor",
            "decision": decision,
            "action": action,
            "reason": reason,
            "failure_reason": failure_reason,
            "post_action_screen_type": post_action_screen_type,
        }
    )
    return LoginActionExecutionResult(
        ok=ok,
        executed=executed,
        action=action,
        decision=decision,
        reason=reason,
        failure_reason=failure_reason,
        post_action_screen_type=post_action_screen_type,
        post_action_probe_reason=post_action_probe_reason,
        post_action_signals=post_action_signals or {},
        timings=timings or _empty_timings(),
        warnings=list(warnings or []),
        metadata=metadata,
    )
