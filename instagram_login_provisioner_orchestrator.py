"""Isolated login provisioning orchestrator skeleton.

Entry 2E-5J assembles the validated login/provisioning building blocks without
hooking the runner, sender, devices, or real business flows. All side effects
remain injectable and disabled by default.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
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
from instagram_login_ui_probe import extract_login_screen_signals_from_hierarchy


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

CredentialsGetter = Callable[[str], Any]
Publisher = Callable[..., dict[str, Any]]
Timer = Callable[[], float]


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
    safe_account_id = str(account_id or "").strip()
    safe_expected_username = str(expected_username or "").strip()
    max_retries = min(MAX_RETRY_ATTEMPTS, max(0, int(max_retry_attempts or 0)))

    signals = dict(initial_signals or {})
    if not signals:
        start = timer()
        signals = _observe_login_signals(d)
        timings["observe_ms"] += _elapsed_ms(start, timer())

    route = route_login_screen(
        expected_username=safe_expected_username,
        suggested_username=str(signals.get("suggested_username") or ""),
        screen_type=str(signals.get("screen_type") or "unknown"),
        account_lifecycle_lookup=lifecycle_lookup,
        clone_reuse_allowed=clone_reuse_allowed,
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
            total_start=total_start,
            timer=timer,
            publisher=publisher,
            publish_enabled=publish_enabled,
        )

    if route.decision in {"continue_expected_account", "use_another_profile_previous_account_stopped"}:
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
                total_start=total_start,
                timer=timer,
                publisher=publisher,
                publish_enabled=publish_enabled,
            )
        signals = dict(action_result.post_action_signals or {})
        if not _signals_confirm_login_form(signals):
            start = timer()
            signals = _observe_login_signals(d)
            timings["observe_ms"] += _elapsed_ms(start, timer())
        route = route_login_screen(
            expected_username=safe_expected_username,
            suggested_username=str(signals.get("suggested_username") or ""),
            screen_type=str(signals.get("screen_type") or "unknown"),
            account_lifecycle_lookup=lifecycle_lookup,
            clone_reuse_allowed=clone_reuse_allowed,
            account_id=safe_account_id,
        )
        actions_taken.append(f"route:{route.decision}")

    if route.decision != "start_login_form_flow":
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
        signals = _observe_login_signals(d)
        timings["observe_ms"] += _elapsed_ms(start, timer())
        if signals.get("screen_type") != "login_form_empty":
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
        total_start=total_start,
        timer=timer,
        publisher=publisher,
        publish_enabled=publish_enabled,
    )


def _observe_login_signals(d: Any) -> dict[str, Any]:
    try:
        hierarchy_xml = d.dump_hierarchy(compressed=False)
    except TypeError:
        hierarchy_xml = d.dump_hierarchy()
    return extract_login_screen_signals_from_hierarchy(str(hierarchy_xml or ""))


def _signals_confirm_login_form(signals: dict[str, Any]) -> bool:
    return (
        signals.get("screen_type") == "login_form_empty"
        and signals.get("has_username_field") is True
        and signals.get("has_password_field") is True
        and signals.get("has_login_button") is True
    )


def _dry_run_result(
    *,
    route: Any,
    signals: dict[str, Any],
    account_id: str,
    expected_username: str,
    actions_taken: list[str],
    timings: dict[str, int],
    warnings: list[str],
    total_start: float,
    timer: Timer,
) -> LoginProvisioningFlowResult:
    screen_type = str(signals.get("screen_type") or "unknown")
    decision = str(getattr(route, "decision", "") or "unknown_no_action")
    would_tap_continue = decision == "continue_expected_account"
    would_tap_use_another_profile = decision == "use_another_profile_previous_account_stopped"
    would_request_credentials = decision == "start_login_form_flow"
    would_block_mismatch = decision == "block_wrong_suggested_account"
    ready_for_password_smoke = screen_type == "login_form_empty" and would_request_credentials
    smoke_ready = ready_for_password_smoke or would_tap_continue or would_tap_use_another_profile
    reason = "dry_run_ready" if smoke_ready else (getattr(route, "reason", "") or "dry_run_not_ready")
    dry_metadata = {
        "dry_run": True,
        "screen_type": screen_type,
        "router_decision": decision,
        "suggested_username": _safe_public_text(signals.get("suggested_username")),
        "expected_username": expected_username,
        "would_tap_continue": would_tap_continue,
        "would_tap_use_another_profile": would_tap_use_another_profile,
        "would_request_credentials": would_request_credentials,
        "would_submit_password": False,
        "would_publish": False,
        "would_block_mismatch": would_block_mismatch,
        "smoke_ready_for_real_login": smoke_ready,
        "ready_for_password_smoke": ready_for_password_smoke,
        "reason": reason,
    }
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
        dashboard_action_type="review_account_mismatch" if would_block_mismatch else None,
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
