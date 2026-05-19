"""
V4.7 — Account session: optional Welcome DM block → Followers list engine (Follow/Likes).

Run type: account_session (wired from runner.py).
dm_welcome_session_send remains a standalone diagnostic run type.
"""

from __future__ import annotations

import time
from typing import Any, Callable

import uiautomator2 as u2

import supabase_client
from dm_follow_handoff import HandoffResult, prepare_dm_to_follow_handoff
from dm_sender_engine import _resolve_dm_sender_real_send_enabled
from logs import log
from welcome_list_sender import get_last_welcome_list_sender_summary
from welcome_scan_producer import get_last_welcome_scan_summary
from welcome_session_orchestrator import dispatch_welcome_session_send

FollowEngineRunner = Callable[..., int]


def _welcome_session_status_label(
    scan_code: int,
    sender_code: int,
    *,
    sender_blocked: bool,
    sender_status: str,
) -> str:
    if sender_blocked:
        return "failed"
    ss = str(sender_status or "").strip()
    if ss == "partial_success":
        return "partial_success"
    if ss in ("failed", "blocked_disabled"):
        return "failed"
    if ss == "success" and scan_code == 0:
        return "success"
    if scan_code != 0 and sender_code != 0:
        return "failed"
    if scan_code != 0 or sender_code != 0:
        return "partial_success"
    return "success"


def _should_run_follow_after_welcome(
    *,
    welcome_enabled: bool,
    real_send_enabled: bool,
    welcome_phase_executed: bool,
    welcome_exit_code: int,
    scan_summary: dict[str, Any],
    sender_summary: dict[str, Any],
    welcome_session_status: str,
) -> tuple[bool, str]:
    if not welcome_enabled:
        return True, "welcome_disabled_bypass"

    if not real_send_enabled:
        return False, "welcome_real_send_disabled"

    if not welcome_phase_executed:
        return False, "welcome_sender_failed"

    scan_status = str(scan_summary.get("status") or "")
    if scan_status == "failed":
        return False, "welcome_scan_failed"

    failure_reason = str(sender_summary.get("failure_reason") or "")
    if failure_reason.startswith("followers_surface"):
        return False, "welcome_surface_unstable"

    jobs_failed = int(sender_summary.get("jobs_failed_count") or 0)
    if jobs_failed > 0:
        return False, "welcome_sender_failed_jobs"

    sender_status = str(sender_summary.get("sender_status") or "")
    if sender_status == "failed":
        return False, "welcome_sender_failed"

    if welcome_session_status == "success":
        return True, "welcome_closed_success"

    if welcome_session_status == "partial_success" and jobs_failed == 0:
        return True, "welcome_closed_with_expected_skips"

    if welcome_session_status == "failed":
        return False, "welcome_sender_failed"

    return False, "welcome_sender_failed"


def _account_session_status(
    *,
    transition_reason: str,
    follow_phase_executed: bool,
    follow_exit_code: int | None,
    welcome_blocked_follow: bool,
) -> str:
    if transition_reason == "welcome_real_send_disabled":
        return "failed"
    if welcome_blocked_follow:
        return "failed"
    if not follow_phase_executed:
        return "failed"
    if follow_exit_code is None:
        return "failed"
    if follow_exit_code in (0, 97, 98):
        return "success"
    return "failed"


def run_account_session(
    d: u2.Device,
    *,
    account_id: str,
    account_username: str,
    run_id: str | None,
    source_profile_username: str,
    run_followers_list_engine_session: FollowEngineRunner,
    supabase_mode: bool,
    warm_session_used: bool,
    force_stop_used: bool,
) -> int:
    t0 = time.perf_counter()
    aid = str(account_id or "").strip()
    uname = str(account_username or "").strip()
    src = str(source_profile_username or "").strip()

    log(
        "info",
        "account_session_started",
        account_id=aid,
        account_username=uname,
        run_id=run_id,
        followers_source_username=src or None,
    )

    if not src:
        log("error", "account_session_aborted", reason="missing_followers_source_username")
        log(
            "info",
            "account_session_summary",
            account_id=aid,
            account_username=uname,
            run_id=run_id,
            total_ms=round((time.perf_counter() - t0) * 1000.0, 2),
            session_status="failed",
            transition_reason="missing_followers_source_username",
            welcome_enabled=False,
            welcome_phase_executed=False,
            follow_phase_executed=False,
            follow_phase_skipped_reason="missing_followers_source_username",
        )
        return 1

    settings: dict[str, Any] = {}
    try:
        settings = supabase_client.get_account_dm_settings(aid) or {}
    except Exception as e:
        log("error", "account_session_settings_load_failed", error=str(e))

    welcome_enabled = bool(settings.get("welcome_enabled"))
    real_send_enabled, real_send_source = _resolve_dm_sender_real_send_enabled()

    welcome_phase_executed = False
    welcome_bypass_reason: str | None = None
    welcome_exit_code = 0
    welcome_session_status = "skipped"
    welcome_t0 = welcome_t1 = 0.0
    scan_summary: dict[str, Any] = {}
    sender_summary: dict[str, Any] = {}

    if not welcome_enabled:
        welcome_bypass_reason = "welcome_disabled"
        log(
            "info",
            "account_session_welcome_bypassed",
            account_id=aid,
            run_id=run_id,
            reason=welcome_bypass_reason,
        )
    elif not real_send_enabled:
        welcome_bypass_reason = "welcome_real_send_disabled"
        welcome_session_status = "failed"
        log(
            "error",
            "account_session_welcome_blocked",
            account_id=aid,
            run_id=run_id,
            reason=welcome_bypass_reason,
            real_send_source=real_send_source,
        )
    else:
        welcome_phase_executed = True
        log(
            "info",
            "account_session_welcome_phase_started",
            account_id=aid,
            account_username=uname,
            run_id=run_id,
        )
        welcome_t0 = time.perf_counter()
        welcome_exit_code = dispatch_welcome_session_send(
            d,
            account_id=aid,
            account_username=uname,
            run_id=run_id,
        )
        welcome_t1 = time.perf_counter()
        scan_summary = get_last_welcome_scan_summary()
        sender_summary = get_last_welcome_list_sender_summary()
        scan_code = 0 if str(scan_summary.get("status") or "") == "success" else 1
        sender_blocked = str(sender_summary.get("sender_status") or "") == "blocked_disabled"
        welcome_session_status = _welcome_session_status_label(
            scan_code,
            welcome_exit_code,
            sender_blocked=sender_blocked,
            sender_status=str(sender_summary.get("sender_status") or ""),
        )
        log(
            "info",
            "account_session_welcome_phase_completed",
            account_id=aid,
            run_id=run_id,
            welcome_exit_code=welcome_exit_code,
            welcome_session_status=welcome_session_status,
            welcome_scan_status=scan_summary.get("status"),
            welcome_sender_status=sender_summary.get("sender_status"),
            welcome_sender_jobs_sent_count=sender_summary.get("jobs_sent_count"),
            welcome_sender_jobs_failed_count=sender_summary.get("jobs_failed_count"),
            welcome_total_ms=round((welcome_t1 - welcome_t0) * 1000.0, 2),
        )

    follow_phase_executed = False
    follow_phase_skipped_reason: str | None = None
    follow_exit_code: int | None = None
    follow_t0 = follow_t1 = 0.0
    handoff_result: HandoffResult | None = None

    run_follow, transition_reason = _should_run_follow_after_welcome(
        welcome_enabled=welcome_enabled,
        real_send_enabled=real_send_enabled,
        welcome_phase_executed=welcome_phase_executed,
        welcome_exit_code=welcome_exit_code,
        scan_summary=scan_summary,
        sender_summary=sender_summary,
        welcome_session_status=welcome_session_status,
    )

    welcome_blocked_follow = not run_follow and welcome_enabled

    if not run_follow:
        follow_phase_skipped_reason = transition_reason
        log(
            "info",
            "account_session_follow_phase_skipped",
            account_id=aid,
            run_id=run_id,
            reason=transition_reason,
        )
    else:
        follow_ready = True
        if welcome_phase_executed:
            handoff_result = prepare_dm_to_follow_handoff(
                d,
                account_username=uname,
                source_profile_username=src,
                welcome_phase_executed=True,
                sender_summary=sender_summary,
            )
            log(
                "info",
                "account_session_handoff_completed",
                account_id=aid,
                run_id=run_id,
                handoff_ok=handoff_result.ok,
                handoff_reason=handoff_result.reason,
                handoff_surface_label=handoff_result.surface_label,
                handoff_prepare_ms=round(handoff_result.prepare_ms, 2),
                handoff_dm_thread_recovered=handoff_result.dm_thread_recovered,
                handoff_followers_surface_ok=handoff_result.followers_surface_ok,
                handoff_resets_applied=handoff_result.resets_applied,
            )
            if not handoff_result.ok:
                follow_ready = False
                follow_phase_skipped_reason = handoff_result.reason
                log(
                    "info",
                    "account_session_follow_phase_skipped",
                    account_id=aid,
                    run_id=run_id,
                    reason=handoff_result.reason,
                    prior_transition_reason=transition_reason,
                )
        if follow_ready:
            log(
                "info",
                "account_session_follow_phase_started",
                account_id=aid,
                run_id=run_id,
                followers_source_username=src,
                transition_reason=transition_reason,
                handoff_applied=bool(welcome_phase_executed),
                handoff_reason=(
                    handoff_result.reason if handoff_result is not None else None
                ),
            )
            follow_t0 = time.perf_counter()
            follow_exit_code = int(
                run_followers_list_engine_session(
                    d,
                    source_profile_username=src,
                    account_id=aid,
                    run_id=str(run_id or ""),
                    supabase_mode=supabase_mode,
                    warm_session_used=warm_session_used,
                    force_stop_used=force_stop_used,
                )
            )
            follow_t1 = time.perf_counter()
            follow_phase_executed = True
            log(
                "info",
                "account_session_follow_phase_completed",
                account_id=aid,
                run_id=run_id,
                follow_engine_exit_code=follow_exit_code,
                follow_total_ms=round((follow_t1 - follow_t0) * 1000.0, 2),
            )

    session_status = _account_session_status(
        transition_reason=transition_reason,
        follow_phase_executed=follow_phase_executed,
        follow_exit_code=follow_exit_code,
        welcome_blocked_follow=welcome_blocked_follow,
    )
    exit_code = 0 if session_status == "success" else 1
    total_ms = (time.perf_counter() - t0) * 1000.0

    log(
        "info",
        "account_session_summary",
        account_id=aid,
        account_username=uname,
        run_id=run_id,
        total_ms=round(total_ms, 2),
        session_status=session_status,
        transition_reason=transition_reason,
        welcome_enabled=welcome_enabled,
        welcome_phase_executed=welcome_phase_executed,
        welcome_bypass_reason=welcome_bypass_reason,
        welcome_session_status=welcome_session_status,
        welcome_exit_code=welcome_exit_code,
        welcome_scan_status=scan_summary.get("status"),
        welcome_scan_jobs_enqueued_count=scan_summary.get("jobs_enqueued_count"),
        welcome_scan_stop_reason=scan_summary.get("stop_reason"),
        welcome_sender_status=sender_summary.get("sender_status"),
        welcome_sender_exit_code=welcome_exit_code if welcome_phase_executed else None,
        welcome_sender_jobs_claimed_count=sender_summary.get("jobs_claimed_count"),
        welcome_sender_jobs_sent_count=sender_summary.get("jobs_sent_count"),
        welcome_sender_jobs_skipped_count=sender_summary.get("jobs_skipped_count"),
        welcome_sender_jobs_failed_count=sender_summary.get("jobs_failed_count"),
        welcome_sender_loop_exit_reason=sender_summary.get("loop_exit_reason"),
        welcome_total_ms=round((welcome_t1 - welcome_t0) * 1000.0, 2) if welcome_phase_executed else 0.0,
        welcome_scan_total_ms=scan_summary.get("total_ms"),
        welcome_sender_total_ms=sender_summary.get("total_ms"),
        follow_phase_executed=follow_phase_executed,
        follow_phase_skipped_reason=follow_phase_skipped_reason,
        followers_source_username=src,
        follow_engine_exit_code=follow_exit_code,
        follow_total_ms=round((follow_t1 - follow_t0) * 1000.0, 2) if follow_phase_executed else 0.0,
        handoff_ok=handoff_result.ok if handoff_result is not None else None,
        handoff_reason=handoff_result.reason if handoff_result is not None else None,
        handoff_surface_label=handoff_result.surface_label if handoff_result is not None else None,
        handoff_prepare_ms=(
            round(handoff_result.prepare_ms, 2) if handoff_result is not None else None
        ),
        real_send_enabled=real_send_enabled,
        real_send_source=real_send_source,
    )
    return exit_code


def dispatch_account_session(
    d: u2.Device,
    *,
    account_id: str,
    account_username: str,
    run_id: str | None,
    source_profile_username: str,
    run_followers_list_engine_session: FollowEngineRunner,
    supabase_mode: bool,
    warm_session_used: bool,
    force_stop_used: bool,
) -> int:
    return run_account_session(
        d,
        account_id=account_id,
        account_username=account_username,
        run_id=run_id,
        source_profile_username=source_profile_username,
        run_followers_list_engine_session=run_followers_list_engine_session,
        supabase_mode=supabase_mode,
        warm_session_used=warm_session_used,
        force_stop_used=force_stop_used,
    )
