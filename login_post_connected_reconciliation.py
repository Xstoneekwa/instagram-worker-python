"""Bounded post-login connected reconciliation for orphaned physical sessions."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from instagram_login_status_classifier import LoginProbeOutcome, classify_login_probe_outcome
from instagram_login_ui_probe import extract_login_screen_signals_from_hierarchy

from instagram_login_password_form_executor import (
    POST_DISMISS_FINAL_INTERVAL_MS,
    POST_DISMISS_FINAL_OBSERVATIONS,
    _classify_post_submit_hierarchy,
    _dismiss_save_login_info_prompt_once,
    _observe_post_dismiss_final_settled,
)

Timer = Callable[[], float]
Sleeper = Callable[[float], None]
Publisher = Callable[..., dict[str, Any]]


@dataclass(frozen=True)
class PostLoginConnectedReconciliationResult:
    ok: bool
    completed: bool
    final_outcome: str
    reason: str
    failure_reason: str = ""
    published: bool = False
    publish_result: str = "skipped"
    screen_type_before: str = ""
    screen_type_after: str = ""
    save_login_info_prompt_detected: bool = False
    save_login_info_not_now_tapped: bool = False
    expected_username_confirmed: bool = False
    actions_taken: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    timings: dict[str, int] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


def _observe_signals(d: Any, *, expected_username: str) -> dict[str, Any]:
    try:
        hierarchy_xml = d.dump_hierarchy(compressed=False)
    except TypeError:
        hierarchy_xml = d.dump_hierarchy()
    return extract_login_screen_signals_from_hierarchy(
        str(hierarchy_xml or ""),
        expected_username=expected_username,
    )


def _connected_like_signals(d: Any, signals: dict[str, Any]) -> bool:
    screen_type = str(signals.get("screen_type") or "").strip()
    if screen_type in {"active_account_home", "active_account_profile", "connected_home", "connected_profile"}:
        return True
    if signals.get("active_account_home") or signals.get("active_account_profile"):
        return True
    try:
        hierarchy_xml = d.dump_hierarchy(compressed=False)
    except TypeError:
        hierarchy_xml = d.dump_hierarchy()
    classified = _classify_post_submit_hierarchy(str(hierarchy_xml or ""))
    return str(classified.get("outcome") or "") == LoginProbeOutcome.CONNECTED.value


def run_post_login_connected_reconciliation(
    d: Any,
    *,
    account_id: str,
    expected_username: str,
    publisher: Optional[Publisher] = None,
    publish_enabled: bool = True,
    run_id: str = "",
    timer: Optional[Timer] = None,
    sleeper: Optional[Sleeper] = None,
) -> PostLoginConnectedReconciliationResult:
    timer = timer or time.perf_counter
    sleeper = sleeper or time.sleep
    total_start = timer()
    actions_taken: list[str] = []
    warnings: list[str] = []
    safe_account_id = str(account_id or "").strip()
    safe_username = str(expected_username or "").strip()

    def _finish(
        *,
        ok: bool,
        completed: bool,
        final_outcome: str,
        reason: str,
        failure_reason: str = "",
        published: bool = False,
        publish_result: str = "skipped",
        screen_type_before: str = "",
        screen_type_after: str = "",
        save_login_info_prompt_detected: bool = False,
        save_login_info_not_now_tapped: bool = False,
        expected_username_confirmed: bool = False,
    ) -> PostLoginConnectedReconciliationResult:
        return PostLoginConnectedReconciliationResult(
            ok=ok,
            completed=completed,
            final_outcome=final_outcome,
            reason=reason,
            failure_reason=failure_reason or reason,
            published=published,
            publish_result=publish_result,
            screen_type_before=screen_type_before,
            screen_type_after=screen_type_after,
            save_login_info_prompt_detected=save_login_info_prompt_detected,
            save_login_info_not_now_tapped=save_login_info_not_now_tapped,
            expected_username_confirmed=expected_username_confirmed,
            actions_taken=actions_taken,
            warnings=warnings,
            timings={"total_ms": int((timer() - total_start) * 1000)},
            metadata={
                "account_id": safe_account_id,
                "expected_username": safe_username,
                "run_id": run_id,
                "source": "post_login_connected_reconciliation",
            },
        )

    if not safe_account_id or not safe_username:
        return _finish(
            ok=False,
            completed=True,
            final_outcome="blocked",
            reason="missing_account_or_username",
            failure_reason="missing_account_or_username",
        )

    signals_before = _observe_signals(d, expected_username=safe_username)
    screen_before = str(signals_before.get("screen_type") or "unknown")
    save_login_detected = bool(signals_before.get("save_login_info_prompt"))
    expected_confirmed = bool(signals_before.get("expected_username_present"))

    if save_login_detected:
        if not expected_confirmed:
            warnings.append("save_login_info_identity_not_confirmed")
            return _finish(
                ok=False,
                completed=True,
                final_outcome="blocked",
                reason="save_login_info_identity_not_confirmed",
                failure_reason="save_login_info_identity_not_confirmed",
                screen_type_before=screen_before,
                save_login_info_prompt_detected=True,
                expected_username_confirmed=False,
            )
        actions_taken.append("save_login_info_not_now")
        if not _dismiss_save_login_info_prompt_once(d, warnings):
            return _finish(
                ok=False,
                completed=True,
                final_outcome="blocked",
                reason="save_login_info_prompt_dismiss_failed",
                failure_reason="save_login_info_prompt_dismiss_failed",
                screen_type_before=screen_before,
                save_login_info_prompt_detected=True,
                expected_username_confirmed=True,
            )
        save_login_not_now = True
        timings: dict[str, int] = {"post_submit_dump_ms": 0}
        final_observed = _observe_post_dismiss_final_settled(
            d,
            timings=timings,
            timer=timer,
            sleeper=sleeper,
            interval_ms=POST_DISMISS_FINAL_INTERVAL_MS,
            max_observations=POST_DISMISS_FINAL_OBSERVATIONS,
        )
        observed = dict(final_observed.get("observed") or {})
        outcome = str(observed.get("outcome") or "unknown")
        screen_after = str(observed.get("screen_type") or observed.get("screen_label") or "unknown")
        if outcome != LoginProbeOutcome.CONNECTED.value:
            return _finish(
                ok=False,
                completed=True,
                final_outcome="blocked",
                reason="connected_not_confirmed_after_save_login_dismiss",
                failure_reason="connected_not_confirmed_after_save_login_dismiss",
                screen_type_before=screen_before,
                screen_type_after=screen_after,
                save_login_info_prompt_detected=True,
                save_login_info_not_now_tapped=save_login_not_now,
                expected_username_confirmed=True,
            )
    else:
        save_login_not_now = False
        if not _connected_like_signals(d, signals_before):
            return _finish(
                ok=False,
                completed=True,
                final_outcome="blocked",
                reason="reconciliation_screen_ambiguous",
                failure_reason="reconciliation_screen_ambiguous",
                screen_type_before=screen_before,
            )
        outcome = LoginProbeOutcome.CONNECTED.value
        screen_after = screen_before

    classification = classify_login_probe_outcome(
        outcome,
        metadata={"screen_type": screen_after, "source": "post_login_connected_reconciliation"},
    )
    published = False
    publish_result = "skipped"
    if publish_enabled and publisher is not None and classification.should_publish:
        try:
            publish_outcome = publisher(
                account_id=safe_account_id,
                login_status=classification.login_status,
                provisioning_status=classification.provisioning_status,
                onboarding_status=classification.onboarding_status,
                reauth_required=classification.reauth_required,
                reauth_reason=classification.reauth_reason,
                reason=classification.reason,
                external_request_id=f"post_login_reconciliation:{run_id}" if run_id else None,
                metadata={
                    **classification.metadata,
                    "final_outcome": outcome,
                    "reconciliation_reason": "post_login_connected_reconciliation",
                    "save_login_info_prompt_detected": save_login_detected,
                    "save_login_info_not_now_tapped": save_login_not_now,
                },
            )
            published = bool((publish_outcome or {}).get("published", True))
            publish_result = "published" if published else str((publish_outcome or {}).get("reason") or "publish_failed")
        except Exception as exc:
            warnings.append("publish_failed_safe")
            publish_result = str(exc)[:120]

    actions_taken.append("publish_connected" if published else "publish_skipped")
    return _finish(
        ok=True,
        completed=True,
        final_outcome=outcome,
        reason=classification.reason or "login_connected",
        published=published,
        publish_result=publish_result,
        screen_type_before=screen_before,
        screen_type_after=screen_after,
        save_login_info_prompt_detected=save_login_detected,
        save_login_info_not_now_tapped=save_login_not_now,
        expected_username_confirmed=expected_confirmed or not save_login_detected,
    )
