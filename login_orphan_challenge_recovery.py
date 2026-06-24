"""Bounded physical recovery for orphan pre-input email-code challenges."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from instagram_login_ui_probe import extract_login_screen_signals_from_hierarchy
from login_challenge_provenance import evaluate_pre_input_email_challenge
from login_orphan_recovery_state import (
    ORPHAN_RECOVERY_EVENT_BLOCKED,
    ORPHAN_RECOVERY_EVENT_DETECTED,
    ORPHAN_RECOVERY_EVENT_FAILED,
    ORPHAN_RECOVERY_EVENT_RESTORED,
    ORPHAN_RECOVERY_EVENT_STARTED,
    record_orphan_recovery_event,
)

STABLE_LOGIN_SURFACES = frozenset(
    {
        "login_form_empty",
        "login_form_prefilled_username",
        "continue_password_only",
        "logged_out",
        "join_instagram_landing",
    }
)
CONNECTED_LIKE_SURFACES = frozenset({"connected", "continue_as_candidate"})
POST_BACK_WAIT_MS = 1200

Timer = Callable[[], float]
Sleeper = Callable[[float], None]


@dataclass(frozen=True)
class OrphanChallengeRecoveryResult:
    ok: bool
    completed: bool
    final_outcome: str
    reason: str
    failure_reason: str = ""
    recovery_state: str = "unknown"
    screen_type_before: str = ""
    screen_type_after: str = ""
    actions_taken: list[str] = field(default_factory=list)
    timings: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


def is_stable_login_surface(signals: dict[str, Any]) -> bool:
    screen_type = str(signals.get("screen_type") or "").strip()
    if screen_type in STABLE_LOGIN_SURFACES:
        return True
    if signals.get("ready_for_credentials_flow"):
        return True
    if signals.get("ready_for_password_submit"):
        return True
    if signals.get("logged_out_present") or signals.get("has_login_button") and signals.get("has_username_field"):
        return screen_type in {"login_form_empty", "login_form_prefilled_username", "unknown"}
    return False


def is_orphan_email_challenge_screen(
    *,
    signals: dict[str, Any],
    package_guard_mismatch: bool,
    account_id: str,
    expected_app_instance_id: Optional[str] = None,
    assignment_id: Optional[str] = None,
    credentials_version: Optional[int] = None,
    assignment_updated_at: Optional[str] = None,
    historical_action: Optional[dict[str, Any]] = None,
) -> bool:
    verdict = evaluate_pre_input_email_challenge(
        routing_signals=signals,
        package_guard_mismatch=package_guard_mismatch,
        account_id=account_id,
        run_id=None,
        expected_app_instance_id=expected_app_instance_id,
        assignment_id=assignment_id,
        credentials_version=credentials_version,
        assignment_updated_at=assignment_updated_at,
        historical_action=historical_action,
    )
    return not verdict.accepted and str(signals.get("screen_type") or "") == "email_code_challenge"


def _observe_signals(d: Any) -> tuple[dict[str, Any], str]:
    try:
        hierarchy_xml = d.dump_hierarchy(compressed=False)
    except TypeError:
        hierarchy_xml = d.dump_hierarchy()
    signals = extract_login_screen_signals_from_hierarchy(hierarchy_xml)
    return signals, str(hierarchy_xml or "")


def _package_guard_mismatch(d: Any, expected_package: str) -> bool:
    expected = str(expected_package or "").strip()
    if not expected:
        return False
    try:
        current = d.app_current() or {}
    except Exception:
        return False
    package = str(current.get("package") or "").strip()
    return bool(package and package != expected)


def run_orphan_challenge_recovery_flow(
    d: Any,
    *,
    account_id: str,
    expected_username: str,
    expected_package: str,
    expected_app_instance_id: Optional[str] = None,
    assignment_id: Optional[str] = None,
    credentials_version: Optional[int] = None,
    assignment_updated_at: Optional[str] = None,
    run_id: str = "",
    challenge_provenance_loader: Optional[Callable[[str], Optional[dict[str, Any]]]] = None,
    timer: Optional[Timer] = None,
    sleeper: Optional[Sleeper] = None,
) -> OrphanChallengeRecoveryResult:
    timer = timer or time.perf_counter
    sleeper = sleeper or time.sleep
    total_start = timer()
    actions_taken: list[str] = []
    warnings: list[str] = []
    metadata: dict[str, Any] = {
        "expected_username": expected_username,
        "expected_app_instance_id": expected_app_instance_id or "",
        "assignment_id": assignment_id or "",
    }

    def _finish(
        *,
        ok: bool,
        completed: bool,
        final_outcome: str,
        reason: str,
        failure_reason: str = "",
        recovery_state: str,
        screen_type_before: str = "",
        screen_type_after: str = "",
        event_type: Optional[str] = None,
        event_status: str = "recorded",
    ) -> OrphanChallengeRecoveryResult:
        if event_type:
            record_orphan_recovery_event(
                account_id=account_id,
                event_type=event_type,
                run_id=run_id,
                status=event_status,
                message=reason,
                metadata={
                    "failure_reason": failure_reason or reason,
                    "screen_type_before": screen_type_before,
                    "screen_type_after": screen_type_after,
                    "recovery_state": recovery_state,
                },
            )
        elapsed_ms = int((timer() - total_start) * 1000)
        return OrphanChallengeRecoveryResult(
            ok=ok,
            completed=completed,
            final_outcome=final_outcome,
            reason=reason,
            failure_reason=failure_reason or reason,
            recovery_state=recovery_state,
            screen_type_before=screen_type_before,
            screen_type_after=screen_type_after,
            actions_taken=actions_taken,
            timings={"total_ms": elapsed_ms},
            warnings=warnings,
            metadata=metadata,
        )

    signals_before, _ = _observe_signals(d)
    screen_before = str(signals_before.get("screen_type") or "unknown")
    metadata["screen_type_before"] = screen_before
    package_mismatch = _package_guard_mismatch(d, expected_package)
    loader = challenge_provenance_loader or (lambda _aid: None)
    historical_action = loader(account_id)

    if not is_orphan_email_challenge_screen(
        signals=signals_before,
        package_guard_mismatch=package_mismatch,
        account_id=account_id,
        expected_app_instance_id=expected_app_instance_id,
        assignment_id=assignment_id,
        credentials_version=credentials_version,
        assignment_updated_at=assignment_updated_at,
        historical_action=historical_action,
    ):
        if screen_before == "email_code_challenge":
            return _finish(
                ok=False,
                completed=True,
                final_outcome="blocked",
                reason="orphan_recovery_not_confirmed",
                failure_reason="challenge_has_strong_provenance",
                recovery_state="recovery_blocked",
                screen_type_before=screen_before,
                event_type=ORPHAN_RECOVERY_EVENT_BLOCKED,
            )
        return _finish(
            ok=False,
            completed=True,
            final_outcome="blocked",
            reason="orphan_recovery_screen_mismatch",
            failure_reason="not_orphan_email_code_challenge",
            recovery_state="recovery_blocked",
            screen_type_before=screen_before,
            event_type=ORPHAN_RECOVERY_EVENT_BLOCKED,
        )

    record_orphan_recovery_event(
        account_id=account_id,
        event_type=ORPHAN_RECOVERY_EVENT_DETECTED,
        run_id=run_id,
        status="confirmed",
        message="orphan_email_code_challenge_confirmed",
        metadata={"screen_type": screen_before},
    )
    record_orphan_recovery_event(
        account_id=account_id,
        event_type=ORPHAN_RECOVERY_EVENT_STARTED,
        run_id=run_id,
        status="started",
        message="orphan_recovery_started",
        metadata={"screen_type": screen_before},
    )
    actions_taken.append("recovery_in_progress")

    try:
        d.press("back")
        actions_taken.append("controlled_back_once")
    except Exception as exc:
        warnings.append("controlled_back_failed")
        return _finish(
            ok=False,
            completed=True,
            final_outcome="failed",
            reason="controlled_back_failed",
            failure_reason=str(exc)[:160],
            recovery_state="recovery_failed",
            screen_type_before=screen_before,
            event_type=ORPHAN_RECOVERY_EVENT_FAILED,
            event_status="failed",
        )

    sleeper(POST_BACK_WAIT_MS / 1000.0)
    signals_after, _ = _observe_signals(d)
    screen_after = str(signals_after.get("screen_type") or "unknown")
    metadata["screen_type_after"] = screen_after

    if is_stable_login_surface(signals_after):
        return _finish(
            ok=True,
            completed=True,
            final_outcome="restored",
            reason="login_surface_restored",
            recovery_state="login_surface_restored",
            screen_type_before=screen_before,
            screen_type_after=screen_after,
            event_type=ORPHAN_RECOVERY_EVENT_RESTORED,
            event_status="success",
        )

    if screen_after == "email_code_challenge" or signals_after.get("email_code_challenge_present"):
        return _finish(
            ok=False,
            completed=True,
            final_outcome="blocked",
            reason="challenge_persisted_after_back",
            recovery_state="recovery_blocked",
            screen_type_before=screen_before,
            screen_type_after=screen_after,
            event_type=ORPHAN_RECOVERY_EVENT_BLOCKED,
        )

    if screen_after in CONNECTED_LIKE_SURFACES or signals_after.get("connected_present"):
        return _finish(
            ok=False,
            completed=True,
            final_outcome="blocked",
            reason="connected_surface_after_back",
            recovery_state="recovery_blocked",
            screen_type_before=screen_before,
            screen_type_after=screen_after,
            event_type=ORPHAN_RECOVERY_EVENT_BLOCKED,
        )

    return _finish(
        ok=False,
        completed=True,
        final_outcome="blocked",
        reason="ambiguous_surface_after_back",
        recovery_state="recovery_blocked",
        screen_type_before=screen_before,
        screen_type_after=screen_after,
        event_type=ORPHAN_RECOVERY_EVENT_BLOCKED,
    )
