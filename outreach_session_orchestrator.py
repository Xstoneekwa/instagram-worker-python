"""Standalone Outreach DM session orchestrator.

V1 intentionally stays thin: Supabase settings + local hard caps, then the
existing DM sender engine with dm_type='outreach'.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

import uiautomator2 as u2

import config
import supabase_client
from dm_sender_engine import _resolve_dm_sender_real_send_enabled, run_dm_sender_send
from logs import log

_LAST_OUTREACH_SESSION_SUMMARY: dict[str, Any] = {}


def get_last_outreach_session_summary() -> dict[str, Any]:
    return dict(_LAST_OUTREACH_SESSION_SUMMARY)


def _publish_summary(summary: dict[str, Any]) -> None:
    global _LAST_OUTREACH_SESSION_SUMMARY
    _LAST_OUTREACH_SESSION_SUMMARY = dict(summary)
    log("info", "outreach_session_summary", **summary)


def _as_nonnegative_int(value: Any, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return max(0, int(default))


def _is_valid_counter_row(counter: Any) -> bool:
    if not isinstance(counter, dict):
        return False
    if str(counter.get("counter_date") or "") != datetime.now(timezone.utc).date().isoformat():
        return False
    for key in ("outreach_sent_count", "total_dm_sent_count"):
        if key not in counter:
            return False
        try:
            if int(counter.get(key) or 0) < 0:
                return False
        except (TypeError, ValueError):
            return False
    return True


def _resolve_effective_max_jobs(settings: dict[str, Any], counter: dict[str, Any]) -> dict[str, Any]:
    session_limit = _as_nonnegative_int(settings.get("outreach_per_session_limit"), 0)
    day_limit = _as_nonnegative_int(settings.get("outreach_per_day_limit"), 0)
    total_day_limit = _as_nonnegative_int(settings.get("total_dm_per_day_limit"), 0)
    hard_session = _as_nonnegative_int(getattr(config, "OUTREACH_HARD_MAX_PER_SESSION", 5), 5)
    hard_day = _as_nonnegative_int(getattr(config, "OUTREACH_HARD_MAX_PER_DAY", 40), 40)
    outreach_sent_today = _as_nonnegative_int(counter.get("outreach_sent_count"), 0)
    total_sent_today = _as_nonnegative_int(counter.get("total_dm_sent_count"), 0)

    caps = [
        session_limit,
        hard_session,
        max(0, day_limit - outreach_sent_today),
        max(0, total_day_limit - total_sent_today),
        max(0, hard_day - outreach_sent_today),
    ]
    effective = min(caps) if caps else 0
    return {
        "max_jobs_effective": int(effective),
        "outreach_per_session_limit": int(session_limit),
        "outreach_per_day_limit": int(day_limit),
        "total_dm_per_day_limit": int(total_day_limit),
        "outreach_hard_max_per_session": int(hard_session),
        "outreach_hard_max_per_day": int(hard_day),
        "outreach_sent_today": int(outreach_sent_today),
        "total_dm_sent_today": int(total_sent_today),
    }


def run_outreach_session(
    d: u2.Device,
    *,
    account_id: str,
    account_username: str,
    run_id: str | None = None,
    parent_search_ready: dict[str, Any] | None = None,
) -> int:
    t0 = time.perf_counter()
    aid = str(account_id or "").strip()
    uname = str(account_username or "").strip()
    parent_verified_at = None
    parent_signal_age_at_outreach_start_ms = None
    if parent_search_ready:
        try:
            parent_verified_at = float(parent_search_ready.get("verified_at_monotonic"))
            parent_signal_age_at_outreach_start_ms = round(
                (time.perf_counter() - parent_verified_at) * 1000.0, 2
            )
        except (TypeError, ValueError):
            parent_verified_at = None

    log(
        "info",
        "outreach_session_started",
        account_id=aid,
        account_username=uname,
        run_id=run_id,
        dm_type="outreach",
        parent_search_ready_verified=bool((parent_search_ready or {}).get("verified")),
        parent_search_ready_verified_at_source=str(
            (parent_search_ready or {}).get("verified_at_source") or ""
        )
        or None,
        parent_signal_age_at_outreach_start_ms=parent_signal_age_at_outreach_start_ms,
    )

    try:
        settings = supabase_client.get_account_dm_settings(aid) or {}
    except Exception as exc:
        settings = {}
        log("error", "outreach_session_settings_load_failed", account_id=aid, error=str(exc))

    real_enabled, real_source = _resolve_dm_sender_real_send_enabled()
    legacy_real_enabled = bool(getattr(config, "ENABLE_REAL_DM_SEND", False))
    log(
        "info",
        "outreach_real_send_enabled",
        account_id=aid,
        run_id=run_id,
        outreach_real_send_enabled=bool(real_enabled),
        enable_real_dm_send_legacy=legacy_real_enabled,
        dm_sender_real_send_enabled=bool(real_enabled),
        dm_sender_real_send_config=bool(getattr(config, "DM_SENDER_REAL_SEND_ENABLED", False)),
        real_send_source=real_source,
    )
    if legacy_real_enabled and not real_enabled:
        log(
            "warning",
            "outreach_real_send_blocked_by_dm_sender_flag",
            account_id=aid,
            run_id=run_id,
            enable_real_dm_send_legacy=True,
            dm_sender_real_send_enabled=False,
        )
    base_summary: dict[str, Any] = {
        "account_id": aid,
        "account_username": uname,
        "run_id": run_id,
        "dm_type": "outreach",
        "outreach_enabled": bool(settings.get("outreach_enabled")),
        "real_send_enabled": bool(real_enabled),
        "real_send_source": real_source,
        "jobs_claimed": 0,
        "jobs_completed": 0,
        "jobs_failed": 0,
        "jobs_skipped": 0,
        "existing_thread_skips": 0,
        "sendability_failures": 0,
    }

    if not bool(settings.get("outreach_enabled")):
        summary = {
            **base_summary,
            "session_status": "blocked_disabled",
            "exit_code": 1,
            "failure_reason": "outreach_disabled",
            "total_ms": round((time.perf_counter() - t0) * 1000.0, 2),
        }
        _publish_summary(summary)
        return 1

    if not real_enabled:
        summary = {
            **base_summary,
            "session_status": "blocked_disabled",
            "exit_code": 1,
            "failure_reason": "real_send_disabled",
            "total_ms": round((time.perf_counter() - t0) * 1000.0, 2),
        }
        _publish_summary(summary)
        return 1

    try:
        counter = supabase_client.get_account_dm_counter_today(aid)
    except Exception as exc:
        counter = None
        log("error", "outreach_session_counter_load_failed", account_id=aid, error=str(exc))

    if not _is_valid_counter_row(counter):
        log(
            "error",
            "outreach_missing_counters_block",
            account_id=aid,
            run_id=run_id,
            counter_present=bool(counter),
        )
        summary = {
            **base_summary,
            "session_status": "blocked_missing_counters",
            "exit_code": 1,
            "failure_reason": "missing_or_invalid_counters",
            "total_ms": round((time.perf_counter() - t0) * 1000.0, 2),
        }
        _publish_summary(summary)
        return 1

    stale_minutes = _as_nonnegative_int(
        getattr(config, "STALE_OUTREACH_JOB_MINUTES", 30),
        30,
    )
    try:
        stale_requeued = supabase_client.requeue_stale_outreach_dm_jobs(
            aid,
            stale_minutes=stale_minutes,
        )
    except Exception as exc:
        stale_requeued = []
        log("warning", "outreach_stale_job_cleanup_failed", account_id=aid, error=str(exc))

    quota = _resolve_effective_max_jobs(settings, counter)
    if int(quota.get("max_jobs_effective") or 0) <= 0:
        summary = {
            **base_summary,
            **quota,
            "stale_jobs_requeued": len(stale_requeued),
            "session_status": "no_quota",
            "exit_code": 0,
            "total_ms": round((time.perf_counter() - t0) * 1000.0, 2),
        }
        _publish_summary(summary)
        return 0

    t_sender_dispatch = time.perf_counter()
    parent_signal_age_before_sender_ms = None
    if parent_verified_at is not None:
        parent_signal_age_before_sender_ms = round(
            (t_sender_dispatch - parent_verified_at) * 1000.0, 2
        )
    log(
        "info",
        "outreach_dispatch_to_sender_started",
        account_id=aid,
        run_id=run_id,
        outreach_dispatch_to_sender_attempt_ms=round(
            (t_sender_dispatch - t0) * 1000.0, 2
        ),
        parent_signal_age_at_sender_attempt_ms=parent_signal_age_before_sender_ms,
    )
    code, sender_summary = run_dm_sender_send(
        d,
        account_id=aid,
        account_username=uname,
        run_id=run_id,
        max_jobs=int(quota["max_jobs_effective"]),
        dm_type="outreach",
        parent_search_ready=parent_search_ready,
    )

    jobs_claimed = _as_nonnegative_int(sender_summary.get("jobs_claimed_count"), 0)
    jobs_sent = _as_nonnegative_int(sender_summary.get("jobs_sent_count"), 0)
    jobs_failed = _as_nonnegative_int(sender_summary.get("jobs_failed_count"), 0)
    jobs_skipped = _as_nonnegative_int(sender_summary.get("jobs_skipped_count"), 0)
    existing_thread_skips = _as_nonnegative_int(
        sender_summary.get("existing_thread_skips_count"), 0
    )
    sendability_failures = _as_nonnegative_int(
        sender_summary.get("sendability_failures_count"), 0
    )
    session_status = str(sender_summary.get("sender_status") or "")
    if not session_status:
        session_status = "success" if int(code) == 0 else "failed"
    if jobs_sent > 0 and (jobs_failed > 0 or jobs_skipped > 0):
        session_status = "completed_partial"
    elif jobs_sent > 0 and jobs_failed == 0 and jobs_skipped == 0:
        session_status = "completed_clean"

    summary = {
        **base_summary,
        **quota,
        "jobs_claimed": jobs_claimed,
        "jobs_completed": jobs_sent,
        "jobs_failed": jobs_failed,
        "jobs_skipped": jobs_skipped,
        "stale_jobs_requeued": len(stale_requeued),
        "existing_thread_skips": existing_thread_skips,
        "sendability_failures": sendability_failures,
        "session_status": session_status,
        "exit_code": int(code),
        "parent_search_ready_fast_path_used": bool(
            sender_summary.get("parent_search_ready_fast_path_used")
        ),
        "parent_search_ready_fast_path_reject_reason": str(
            sender_summary.get("parent_search_ready_fast_path_reject_reason") or ""
        ),
        "search_surface_age_ms": sender_summary.get("search_surface_age_ms"),
        "sender_prepare_reused_search_surface": bool(
            sender_summary.get("sender_prepare_reused_search_surface")
        ),
        "sender_summary": sender_summary,
        "total_ms": round((time.perf_counter() - t0) * 1000.0, 2),
    }
    _publish_summary(summary)
    return int(code)


def dispatch_outreach_session(
    d: u2.Device,
    *,
    account_id: str,
    account_username: str,
    run_id: str | None = None,
    parent_search_ready: dict[str, Any] | None = None,
) -> int:
    return run_outreach_session(
        d,
        account_id=account_id,
        account_username=account_username,
        run_id=run_id,
        parent_search_ready=parent_search_ready,
    )
