"""
V4.1 — Welcome baseline dry-run: own profile → own followers list → harvest → Supabase.

No DM send, no ig_dm_jobs, no enqueue_welcome_dm_job_if_eligible.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import uiautomator2 as u2

import config
import supabase_client
from instagram_navigation import (
    detect_followers_list_screen,
    harvest_visible_followers_usernames,
    scroll_followers_list_forward,
)
from logs import log
from own_profile_navigation import (
    open_own_followers_list_from_own_profile,
    open_own_profile_from_bottom_nav,
    verify_own_profile,
)


def _dedupe_usernames(usernames: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in usernames:
        u = str(raw or "").strip().lstrip("@").lower()
        if not u or u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out


def _persist_baseline_followers(
    account_id: str,
    usernames: list[str],
    *,
    scan_run_id: str | None,
) -> dict[str, int]:
    persisted = 0
    failed = 0
    for username in usernames:
        try:
            row = supabase_client.upsert_account_follower_seen_baseline(
                account_id,
                username,
                scan_run_id=scan_run_id,
            )
            if row:
                persisted += 1
                log(
                    "info",
                    "welcome_baseline_follower_persisted",
                    account_id=account_id,
                    username=username,
                    baseline_existing=bool(row.get("baseline_existing")),
                    welcome_dm_status=str(row.get("welcome_dm_status") or ""),
                )
            else:
                failed += 1
        except Exception as e:
            failed += 1
            log(
                "warning",
                "welcome_baseline_follower_persist_failed",
                account_id=account_id,
                username=username,
                error=str(e),
            )
    log(
        "info",
        "welcome_baseline_followers_persisted",
        account_id=account_id,
        persisted_count=persisted,
        failed_count=failed,
        total_usernames=len(usernames),
    )
    return {
        "persisted_count": persisted,
        "failed_count": failed,
    }


def _is_partial_visible_only_baseline(*, visible_only: bool, max_scrolls: int) -> bool:
    """V4.1 visible surface only — must not set welcome_baseline_completed_at."""
    return bool(visible_only) or int(max_scrolls) <= 0


def _emit_run_summary(
    *,
    account_id: str,
    run_id: str,
    status: str,
    own_profile_open_ok: bool,
    followers_list_open_ok: bool,
    usernames_count: int,
    persisted_count: int,
    baseline_completed: bool,
    total_ms: float,
    scan_scope: str,
    failure_reason: str | None = None,
    baseline_completion_skipped_reason: str | None = None,
) -> None:
    log(
        "info",
        "welcome_baseline_run_summary",
        account_id=account_id,
        run_id=run_id or None,
        status=status,
        own_profile_open_ok=own_profile_open_ok,
        followers_list_open_ok=followers_list_open_ok,
        usernames_count=usernames_count,
        persisted_count=persisted_count,
        baseline_completed=baseline_completed,
        scan_scope=scan_scope,
        total_ms=round(total_ms, 2),
        failure_reason=failure_reason,
        baseline_completion_skipped_reason=baseline_completion_skipped_reason,
    )


def run_welcome_baseline_scan(
    d: u2.Device,
    *,
    account_id: str,
    account_username: str,
    run_id: str | None = None,
) -> int:
    """
    Execute V4.1 welcome baseline (visible followers harvest + Supabase persist only).
    Returns process exit code: 0 success, 1 failed.
    """
    t0 = time.perf_counter()
    aid = str(account_id or "").strip()
    uname = str(account_username or "").strip()
    scan_run_id = (run_id or "").strip() or str(uuid.uuid4())
    visible_only = bool(getattr(config, "WELCOME_BASELINE_INITIAL_VISIBLE_ONLY", True))
    max_scrolls = int(getattr(config, "WELCOME_BASELINE_MAX_SCROLLS_V1", 0) or 0)
    if visible_only:
        max_scrolls = min(max_scrolls, 0)
    partial_visible_scan = _is_partial_visible_only_baseline(
        visible_only=visible_only,
        max_scrolls=max_scrolls,
    )
    if partial_visible_scan:
        scan_scope = "visible_only_partial"
    else:
        scan_scope = f"limited_scrolls_{max_scrolls}"

    own_profile_open_ok = False
    followers_list_open_ok = False
    usernames_count = 0
    persisted_count = 0
    baseline_completed = False
    failure_reason: str | None = None
    baseline_completion_skipped_reason: str | None = None

    def _finish(
        status: str,
        code: int,
        reason: str | None = None,
        *,
        skipped_reason: str | None = None,
    ) -> int:
        nonlocal failure_reason, baseline_completion_skipped_reason
        if reason:
            failure_reason = reason
        if skipped_reason:
            baseline_completion_skipped_reason = skipped_reason
        _emit_run_summary(
            account_id=aid,
            run_id=scan_run_id,
            status=status,
            own_profile_open_ok=own_profile_open_ok,
            followers_list_open_ok=followers_list_open_ok,
            usernames_count=usernames_count,
            persisted_count=persisted_count,
            baseline_completed=baseline_completed,
            total_ms=(time.perf_counter() - t0) * 1000.0,
            scan_scope=scan_scope,
            failure_reason=failure_reason,
            baseline_completion_skipped_reason=baseline_completion_skipped_reason,
        )
        return code

    if not aid:
        log("error", "welcome_baseline_aborted", reason="missing_account_id")
        return _finish("failed", 1, "missing_account_id")

    try:
        supabase_client.ensure_account_dm_settings(aid)
    except Exception as e:
        log("error", "welcome_baseline_aborted", reason="ensure_dm_settings_failed", error=str(e))
        return _finish("failed", 1, "ensure_dm_settings_failed")

    if not open_own_profile_from_bottom_nav(d):
        log("error", "welcome_baseline_aborted", reason="own_profile_open_failed")
        return _finish("failed", 1, "own_profile_open_failed")
    own_profile_open_ok = True

    ok_profile, _prof_meta = verify_own_profile(d, uname)
    if not ok_profile:
        log("error", "welcome_baseline_aborted", reason="own_profile_verify_failed")
        return _finish("failed", 1, "own_profile_verify_failed")

    ok_followers, _fol_meta = open_own_followers_list_from_own_profile(
        d,
        uname,
        pkg=str(getattr(config, "INSTAGRAM_PACKAGE", "") or ""),
    )
    if not ok_followers:
        log("error", "welcome_baseline_aborted", reason="own_followers_open_failed")
        return _finish("failed", 1, "own_followers_open_failed")
    followers_list_open_ok = True

    det = detect_followers_list_screen(d, source_profile_username=uname)
    if not bool(det.get("is_followers_list")):
        log("error", "welcome_baseline_aborted", reason="followers_surface_not_verified")
        return _finish("failed", 1, "followers_surface_not_verified")

    harvested: list[str] = []
    runtime_seen: set[str] = set()
    harvest_meta: dict[str, Any] = {}

    def _harvest_pass() -> None:
        nonlocal harvest_meta
        batch, meta = harvest_visible_followers_usernames(
            d,
            source_profile_username=uname,
            runtime_seen=runtime_seen,
        )
        harvested.extend(batch)
        harvest_meta = meta

    _harvest_pass()
    scrolls_done = 0
    while scrolls_done < max_scrolls:
        if not scroll_followers_list_forward(
            d,
            source_profile_username=uname,
            bypass_post_tap_capture_gate=True,
            bypass_scroll_xml_guards=True,
        ):
            break
        scrolls_done += 1
        _harvest_pass()

    usernames = _dedupe_usernames(harvested)
    usernames_count = len(usernames)
    log(
        "info",
        "welcome_baseline_visible_followers_harvested",
        account_id=aid,
        visible_rows_count=int(harvest_meta.get("visible_rows_count") or 0),
        usernames_count=usernames_count,
        sample_usernames=usernames[:12],
        extraction_methods=list(harvest_meta.get("extraction_methods") or []),
        scrolls_done=scrolls_done,
        scan_scope=scan_scope,
    )

    if not usernames:
        log("error", "welcome_baseline_aborted", reason="no_usernames_harvested")
        return _finish("partial", 1, "no_usernames_harvested")

    persist_stats = _persist_baseline_followers(aid, usernames, scan_run_id=scan_run_id)
    persisted_count = int(persist_stats.get("persisted_count") or 0)
    failed_count = int(persist_stats.get("failed_count") or 0)

    if persisted_count <= 0 or failed_count > 0:
        log(
            "error",
            "welcome_baseline_aborted",
            reason="persist_incomplete",
            persisted_count=persisted_count,
            failed_count=failed_count,
        )
        return _finish("partial", 1, "persist_incomplete")

    if partial_visible_scan:
        log(
            "info",
            "welcome_baseline_completion_skipped",
            account_id=aid,
            reason="partial_visible_only_scan",
            followers_seen_count=usernames_count,
            followers_persisted_count=persisted_count,
            scan_scope=scan_scope,
            run_id=scan_run_id,
        )
        return _finish(
            "partial_success",
            0,
            skipped_reason="partial_visible_only_scan",
        )

    try:
        supabase_client.mark_welcome_baseline_completed(aid, scan_run_id=scan_run_id)
        baseline_completed = True
    except Exception as e:
        log("error", "welcome_baseline_aborted", reason="mark_baseline_completed_failed", error=str(e))
        return _finish("partial", 1, "mark_baseline_completed_failed")

    log(
        "info",
        "welcome_baseline_completed",
        account_id=aid,
        followers_seen_count=usernames_count,
        followers_persisted_count=persisted_count,
        scan_scope=scan_scope,
        run_id=scan_run_id,
    )
    return _finish("success", 0)
