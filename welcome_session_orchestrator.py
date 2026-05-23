"""
V4.5 — Welcome session: scan producer → list-native Welcome DM sender (single run).

Search-based dm_sender_send is preserved for Outreach / external prospects.
"""

from __future__ import annotations

import time
from typing import Any

import uiautomator2 as u2

import config
import supabase_client
from dm_sender_engine import _resolve_dm_sender_real_send_enabled
from logs import log
from welcome_list_sender import run_welcome_list_sender
from welcome_scan_producer import get_last_welcome_scan_summary, run_welcome_scan_producer


def _session_status(
    scan_code: int,
    sender_code: int,
    *,
    sender_blocked: bool,
    sender_status: str | None = None,
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


def run_welcome_session_send(
    d: u2.Device,
    *,
    account_id: str,
    account_username: str,
    run_id: str | None = None,
) -> int:
    """
    Phase A: welcome scan (enqueue pending jobs, stay on Followers list).
    Phase B: list-native Welcome DM send (row tap → profile → Message → send).
    """
    t0 = time.perf_counter()
    aid = str(account_id or "").strip()
    uname = str(account_username or "").strip()
    env_max_jobs = max(0, int(getattr(config, "WELCOME_SESSION_SEND_MAX_JOBS", 3) or 3))
    try:
        settings = supabase_client.get_account_dm_settings(aid) or {}
    except Exception as exc:
        settings = {}
        log("error", "welcome_session_settings_load_failed", account_id=aid, error=str(exc))
    raw_db_max_jobs = settings.get("welcome_per_session_limit") if settings else None
    db_max_jobs = (
        max(0, int(raw_db_max_jobs))
        if raw_db_max_jobs is not None and str(raw_db_max_jobs).strip() != ""
        else env_max_jobs
    )
    max_jobs = min(db_max_jobs, env_max_jobs)
    log(
        "info",
        "welcome_effective_limits_resolved",
        account_id=aid,
        run_id=run_id,
        db_welcome_per_session_limit=db_max_jobs,
        env_welcome_send_max_jobs=env_max_jobs,
        effective_welcome_send_max=max_jobs,
        source="min(db,env_hard_cap)",
    )

    log(
        "info",
        "welcome_session_send_dispatch",
        account_id=aid,
        account_username=uname,
        run_id=run_id,
        max_jobs=max_jobs,
        sender_mode="welcome_list_native",
    )
    log(
        "info",
        "welcome_session_send_started",
        account_id=aid,
        account_username=uname,
        run_id=run_id,
    )

    real_enabled, real_source = _resolve_dm_sender_real_send_enabled()
    if not real_enabled:
        log(
            "error",
            "dm_sender_real_send_blocked_disabled",
            account_id=aid,
            run_id=run_id,
            real_send_source=real_source,
        )
        log(
            "info",
            "welcome_session_send_summary",
            account_id=aid,
            run_id=run_id,
            session_status="failed",
            total_ms=round((time.perf_counter() - t0) * 1000.0, 2),
            real_send_enabled=False,
            real_send_source=real_source,
        )
        return 1

    # --- Phase A: scan (ends on own Followers list) ---
    log("info", "welcome_session_scan_phase_started", account_id=aid, run_id=run_id)
    scan_t0 = time.perf_counter()
    scan_code = run_welcome_scan_producer(
        d,
        account_id=aid,
        account_username=uname,
        run_id=run_id,
    )
    scan_ms = (time.perf_counter() - scan_t0) * 1000.0
    scan_summary = get_last_welcome_scan_summary()
    log(
        "info",
        "welcome_session_scan_phase_completed",
        account_id=aid,
        run_id=run_id,
        scan_status=scan_summary.get("status"),
        scan_exit_code=scan_code,
        scan_total_ms=round(scan_ms, 2),
        scan_jobs_enqueued_count=scan_summary.get("jobs_enqueued_count"),
        scan_unknown_pre_anchor_count=scan_summary.get("unknown_pre_anchor_count"),
        scan_new_follower_usernames_enqueued=scan_summary.get(
            "new_follower_usernames_enqueued"
        ),
        scan_job_ids_enqueued_count=len(
            scan_summary.get("new_follower_job_ids_enqueued") or []
        ),
        scan_final_screen_index=scan_summary.get("scan_final_screen_index"),
        scan_stop_reason=scan_summary.get("stop_reason"),
    )

    if scan_code != 0:
        log(
            "info",
            "welcome_session_send_summary",
            account_id=aid,
            run_id=run_id,
            session_status="failed",
            total_ms=round((time.perf_counter() - t0) * 1000.0, 2),
            scan_status=scan_summary.get("status"),
            failure_reason="scan_phase_failed",
        )
        return 1

    # --- Phase B: list-native sender (no global Search surface prepare) ---
    log(
        "info",
        "welcome_session_sender_phase_started",
        account_id=aid,
        run_id=run_id,
        max_jobs=max_jobs,
        sender_mode="welcome_list_native",
    )
    sender_t0 = time.perf_counter()
    sender_code, sender_summary = run_welcome_list_sender(
        d,
        account_id=aid,
        account_username=uname,
        run_id=run_id,
        max_jobs=max_jobs,
        scan_summary=scan_summary,
    )
    sender_ms = (time.perf_counter() - sender_t0) * 1000.0
    sender_blocked = str(sender_summary.get("sender_status") or "") == "blocked_disabled"
    log(
        "info",
        "welcome_session_sender_phase_completed",
        account_id=aid,
        run_id=run_id,
        sender_status=sender_summary.get("sender_status"),
        sender_exit_code=sender_code,
        sender_total_ms=round(sender_ms, 2),
        sender_mode=sender_summary.get("sender_mode"),
        sender_jobs_claimed_count=sender_summary.get("jobs_claimed_count"),
        sender_jobs_sent_count=sender_summary.get("jobs_sent_count"),
        sender_jobs_skipped_count=sender_summary.get("jobs_skipped_count"),
        sender_jobs_failed_count=sender_summary.get("jobs_failed_count"),
        list_navigation_total_ms=sender_summary.get("list_navigation_total_ms"),
        dm_send_total_ms=sender_summary.get("dm_send_total_ms"),
    )

    total_ms = (time.perf_counter() - t0) * 1000.0
    session_status = _session_status(
        scan_code,
        sender_code,
        sender_blocked=sender_blocked,
        sender_status=str(sender_summary.get("sender_status") or ""),
    )
    exit_code = 0 if session_status == "success" else 1

    log(
        "info",
        "welcome_session_send_summary",
        account_id=aid,
        run_id=run_id,
        session_status=session_status,
        total_ms=round(total_ms, 2),
        scan_status=scan_summary.get("status"),
        scan_total_ms=round(scan_ms, 2),
        scan_unknown_pre_anchor_count=scan_summary.get("unknown_pre_anchor_count"),
        scan_jobs_enqueued_count=scan_summary.get("jobs_enqueued_count"),
        scan_stop_reason=scan_summary.get("stop_reason"),
        sender_status=sender_summary.get("sender_status"),
        sender_mode=sender_summary.get("sender_mode"),
        sender_total_ms=round(sender_ms, 2),
        sender_jobs_claimed_count=sender_summary.get("jobs_claimed_count"),
        sender_jobs_sent_count=sender_summary.get("jobs_sent_count"),
        sender_jobs_skipped_count=sender_summary.get("jobs_skipped_count"),
        sender_jobs_failed_count=sender_summary.get("jobs_failed_count"),
        sender_processed_recipients=sender_summary.get("processed_recipients"),
        sender_sent_recipients=sender_summary.get("sent_recipients"),
        sender_skipped_recipients=sender_summary.get("skipped_recipients"),
        recipients_planned=sender_summary.get("recipients_planned"),
        selection_strategy=sender_summary.get("selection_strategy"),
        session_claim_mode=sender_summary.get("session_claim_mode"),
        list_navigation_total_ms=sender_summary.get("list_navigation_total_ms"),
        dm_send_total_ms=sender_summary.get("dm_send_total_ms"),
        scan_exit_code=scan_code,
        sender_exit_code=sender_code,
    )
    return exit_code


def dispatch_welcome_session_send(
    d: u2.Device,
    *,
    account_id: str,
    account_username: str,
    run_id: str | None = None,
) -> int:
    return run_welcome_session_send(
        d,
        account_id=account_id,
        account_username=account_username,
        run_id=run_id,
    )
