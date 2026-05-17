"""
V4.1 — Welcome baseline: own profile → own followers list → harvest → Supabase.

No DM send, no ig_dm_jobs, no enqueue_welcome_dm_job_if_eligible.
"""

from __future__ import annotations

import re
import time
import uuid
from typing import Any

import uiautomator2 as u2

import config
import supabase_client
from instagram_navigation import (
    detect_followers_list_screen,
    followers_clear_detect_hierarchy_cache,
    followers_refresh_detect_hierarchy_cache,
    harvest_visible_followers_usernames,
    scroll_followers_list_forward,
)
from logs import log
from own_profile_navigation import (
    open_own_followers_list_from_own_profile,
    open_own_profile_from_bottom_nav,
    verify_own_profile,
)

_TAB_TOTAL_FOLLOWERS_RE = re.compile(r"^(\d+)\s+followers\b", re.IGNORECASE)
_TAB_TOTAL_FOLLOWERS_FR_RE = re.compile(r"^(\d+)\s+abonn", re.IGNORECASE)


def _norm_username(raw: str) -> str:
    return str(raw or "").strip().lstrip("@").lower()


def _dedupe_usernames(usernames: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in usernames:
        u = _norm_username(raw)
        if not u or u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out


def _parse_total_followers_count_from_det(det: dict[str, Any]) -> int | None:
    """Parse '20 followers' (or localized) from own-unified selected tab header."""
    candidates: list[str] = []
    for raw in list(det.get("visible_header_texts") or [])[:12]:
        t = str(raw or "").strip()
        if t:
            candidates.append(t)
    for pat in (_TAB_TOTAL_FOLLOWERS_RE, _TAB_TOTAL_FOLLOWERS_FR_RE):
        for t in candidates:
            m = pat.match(t)
            if m:
                try:
                    return int(m.group(1))
                except (TypeError, ValueError):
                    continue
    for t in candidates:
        low = t.lower()
        if "follower" in low or "abonn" in low:
            m = re.match(r"^(\d+)", t)
            if m:
                try:
                    return int(m.group(1))
                except (TypeError, ValueError):
                    continue
    return None


def _persist_baseline_followers(
    account_id: str,
    usernames: list[str],
    *,
    scan_run_id: str | None,
) -> dict[str, Any]:
    persisted = 0
    failed = 0
    succeeded_usernames: list[str] = []
    for username in usernames:
        try:
            row = supabase_client.upsert_account_follower_seen_baseline(
                account_id,
                username,
                scan_run_id=scan_run_id,
            )
            if row:
                persisted += 1
                succeeded_usernames.append(username)
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
    if usernames:
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
        "succeeded_usernames": succeeded_usernames,
    }


def _is_partial_visible_only_baseline(*, window_mode: bool, visible_only: bool, max_scrolls: int) -> bool:
    """Visible-only lab mode — must not set welcome_baseline_completed_at."""
    if window_mode:
        return False
    return bool(visible_only) or int(max_scrolls) <= 0


def _evaluate_baseline_completion(
    *,
    followers_list_open_ok: bool,
    window_mode: bool,
    partial_visible_scan: bool,
    unique_persisted_count: int,
    failed_persist_count: int,
    elapsed_ms: float,
    max_seconds: float,
    min_rows: int,
    scrolls_done: int,
    initial_rows_count: int,
    total_followers_count: int | None,
) -> tuple[bool, str | None, str | None]:
    """
    Returns (baseline_completed, completion_mode, skip_reason).
    """
    if not followers_list_open_ok:
        return False, None, "followers_list_not_open"
    if failed_persist_count > 0:
        return False, None, "persist_failures"
    if unique_persisted_count <= 0:
        return False, None, "no_usernames_persisted"
    if partial_visible_scan:
        return False, None, "partial_visible_only_scan"

    if (
        total_followers_count is not None
        and total_followers_count > 0
        and unique_persisted_count >= total_followers_count
    ):
        return True, "full_small_account_coverage", None

    if not window_mode:
        return False, None, "not_window_mode"

    if elapsed_ms > float(max_seconds) * 1000.0:
        return False, None, "max_seconds_exceeded"

    if unique_persisted_count < int(min_rows):
        return False, None, "min_rows_not_met"

    depth_ok = int(scrolls_done) >= 1 or int(initial_rows_count) >= int(min_rows)
    if not depth_ok:
        return False, None, "insufficient_scan_depth"

    return True, "bounded_window_ready", None


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
    completion_mode: str | None = None,
    stop_reason: str | None = None,
    total_followers_count_if_known: int | None = None,
    screens_scanned: int = 0,
    scrolls_done: int = 0,
    duplicate_runtime_count: int = 0,
    failed_count: int = 0,
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
        unique_usernames_count=usernames_count,
        persisted_count=persisted_count,
        baseline_completed=baseline_completed,
        scan_scope=scan_scope,
        total_ms=round(total_ms, 2),
        failure_reason=failure_reason,
        baseline_completion_skipped_reason=baseline_completion_skipped_reason,
        completion_mode=completion_mode,
        stop_reason=stop_reason,
        total_followers_count_if_known=total_followers_count_if_known,
        screens_scanned=screens_scanned,
        scrolls_done=scrolls_done,
        duplicate_runtime_count=duplicate_runtime_count,
        failed_count=failed_count,
    )


def run_welcome_baseline_scan(
    d: u2.Device,
    *,
    account_id: str,
    account_username: str,
    run_id: str | None = None,
) -> int:
    """
    Execute welcome baseline (followers harvest + Supabase persist only).
    V4.1-E window mode: bounded multi-scroll + completion criteria.
    Returns process exit code: 0 success, 1 failed.
    """
    t0 = time.perf_counter()
    aid = str(account_id or "").strip()
    uname = str(account_username or "").strip()
    scan_run_id = (run_id or "").strip() or str(uuid.uuid4())

    window_mode = bool(getattr(config, "WELCOME_BASELINE_WINDOW_MODE_ENABLED", False))
    visible_only = bool(getattr(config, "WELCOME_BASELINE_INITIAL_VISIBLE_ONLY", False))
    max_scrolls = int(getattr(config, "WELCOME_BASELINE_MAX_SCROLLS_V1", 0) or 0)
    min_rows = int(getattr(config, "WELCOME_BASELINE_MIN_ROWS_V1", 30) or 30)
    max_seconds = float(getattr(config, "WELCOME_BASELINE_MAX_SECONDS_V1", 120) or 120)
    stagnation_max = int(
        getattr(config, "WELCOME_BASELINE_STAGNATION_NO_NEW_ROWS_MAX", 2) or 2
    )
    post_scroll_settle_s = float(
        getattr(config, "WELCOME_BASELINE_POST_SCROLL_SETTLE_S", 0.65) or 0.65
    )

    if window_mode:
        visible_only = False
        if max_scrolls <= 0:
            max_scrolls = 8
    elif visible_only:
        max_scrolls = 0

    partial_visible_scan = _is_partial_visible_only_baseline(
        window_mode=window_mode,
        visible_only=visible_only,
        max_scrolls=max_scrolls,
    )
    if partial_visible_scan:
        scan_scope = "visible_only_partial"
    elif window_mode:
        scan_scope = "baseline_window"
    else:
        scan_scope = f"limited_scrolls_{max_scrolls}"

    own_profile_open_ok = False
    followers_list_open_ok = False
    usernames_count = 0
    persisted_count = 0
    failed_count = 0
    baseline_completed = False
    completion_mode: str | None = None
    stop_reason: str | None = None
    failure_reason: str | None = None
    baseline_completion_skipped_reason: str | None = None
    total_followers_count: int | None = None
    screens_scanned = 0
    scrolls_done = 0
    duplicate_runtime_count = 0
    initial_rows_count = 0

    runtime_seen: set[str] = set()
    unique_persisted: set[str] = set()
    stagnation_no_new_rows = 0

    def _elapsed_ms() -> float:
        return (time.perf_counter() - t0) * 1000.0

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
            total_ms=_elapsed_ms(),
            scan_scope=scan_scope,
            failure_reason=failure_reason,
            baseline_completion_skipped_reason=baseline_completion_skipped_reason,
            completion_mode=completion_mode,
            stop_reason=stop_reason,
            total_followers_count_if_known=total_followers_count,
            screens_scanned=screens_scanned,
            scrolls_done=scrolls_done,
            duplicate_runtime_count=duplicate_runtime_count,
            failed_count=failed_count,
        )
        return code

    def _harvest_and_persist_screen(screen_index: int) -> int:
        """Harvest current screen, persist new handles only. Returns count newly persisted."""
        nonlocal harvest_meta, initial_rows_count, duplicate_runtime_count
        nonlocal screens_scanned, stagnation_no_new_rows

        batch, meta = harvest_visible_followers_usernames(
            d,
            source_profile_username=uname,
            runtime_seen=runtime_seen,
        )
        harvest_meta = meta
        screens_scanned += 1
        screen_rows = int(meta.get("visible_rows_count") or len(batch))
        if screen_index == 0:
            initial_rows_count = screen_rows

        new_handles: list[str] = []
        dup_this_screen = 0
        for raw in batch:
            key = _norm_username(raw)
            if not key:
                continue
            if key in runtime_seen:
                dup_this_screen += 1
                continue
            runtime_seen.add(key)
            new_handles.append(str(raw).strip().lstrip("@"))

        duplicate_runtime_count += dup_this_screen

        to_persist = [u for u in new_handles if _norm_username(u) not in unique_persisted]
        new_persisted = 0
        if to_persist:
            stats = _persist_baseline_followers(aid, to_persist, scan_run_id=scan_run_id)
            new_persisted = int(stats.get("persisted_count") or 0)
            failed_batch = int(stats.get("failed_count") or 0)
            nonlocal failed_count
            failed_count += failed_batch
            for u in list(stats.get("succeeded_usernames") or []):
                key = _norm_username(u)
                if key:
                    unique_persisted.add(key)

        if new_persisted <= 0:
            stagnation_no_new_rows += 1
        else:
            stagnation_no_new_rows = 0

        log(
            "info",
            "welcome_baseline_window_screen_harvested",
            account_id=aid,
            screen_index=screen_index,
            visible_rows_count=screen_rows,
            batch_count=len(batch),
            new_handles_count=len(new_handles),
            new_persisted_count=new_persisted,
            unique_persisted_count=len(unique_persisted),
            duplicate_runtime_screen=dup_this_screen,
            extraction_methods=list(meta.get("extraction_methods") or []),
            stagnation_no_new_rows=stagnation_no_new_rows,
        )
        return new_persisted

    harvest_meta: dict[str, Any] = {}

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

    total_followers_count = _parse_total_followers_count_from_det(det)

    # --- Screen 0 (post-open cache XML is fresh from entry V2) ---
    _harvest_and_persist_screen(0)

    if (
        total_followers_count is not None
        and len(unique_persisted) >= total_followers_count
    ):
        stop_reason = "full_small_account_coverage_after_screen_0"

    # --- Bounded scroll window ---
    while (
        window_mode
        and scrolls_done < max_scrolls
        and stop_reason is None
    ):
        if _elapsed_ms() >= max_seconds * 1000.0:
            stop_reason = "max_seconds_reached"
            break
        if stagnation_no_new_rows >= stagnation_max:
            stop_reason = "stagnation_no_new_rows"
            break
        if (
            total_followers_count is not None
            and len(unique_persisted) >= total_followers_count
        ):
            stop_reason = "full_small_account_coverage"
            break

        log(
            "info",
            "welcome_baseline_window_scroll_started",
            account_id=aid,
            scroll_index=scrolls_done + 1,
            max_scrolls=max_scrolls,
            unique_persisted_count=len(unique_persisted),
        )

        if not scroll_followers_list_forward(
            d,
            source_profile_username=uname,
            bypass_post_tap_capture_gate=True,
            bypass_scroll_xml_guards=True,
        ):
            stop_reason = "scroll_failed"
            log(
                "info",
                "welcome_baseline_window_stop",
                account_id=aid,
                stop_reason=stop_reason,
                scrolls_done=scrolls_done,
            )
            break

        scrolls_done += 1
        followers_clear_detect_hierarchy_cache()
        if post_scroll_settle_s > 0:
            time.sleep(min(post_scroll_settle_s, 2.0))

        hier = followers_refresh_detect_hierarchy_cache(d, screen_index=scrolls_done)
        det_scroll = detect_followers_list_screen(
            d,
            source_profile_username=uname,
            hierarchy_xml=hier or None,
        )
        if not bool(det_scroll.get("is_followers_list")):
            stop_reason = "followers_surface_lost"
            log(
                "info",
                "welcome_baseline_window_stop",
                account_id=aid,
                stop_reason=stop_reason,
                scrolls_done=scrolls_done,
            )
            break

        tab_total = _parse_total_followers_count_from_det(det_scroll)
        if tab_total is not None:
            total_followers_count = tab_total

        log(
            "info",
            "welcome_baseline_window_scroll_done",
            account_id=aid,
            scroll_index=scrolls_done,
            hierarchy_xml_len=len(hier or ""),
        )

        _harvest_and_persist_screen(scrolls_done)

        if (
            total_followers_count is not None
            and len(unique_persisted) >= total_followers_count
        ):
            stop_reason = "full_small_account_coverage"
            break

    if window_mode and stop_reason is None and scrolls_done >= max_scrolls:
        stop_reason = "max_scrolls_reached"

    if window_mode and stop_reason:
        log(
            "info",
            "welcome_baseline_window_stop",
            account_id=aid,
            stop_reason=stop_reason,
            scrolls_done=scrolls_done,
            screens_scanned=screens_scanned,
            unique_persisted_count=len(unique_persisted),
            total_followers_count_if_known=total_followers_count,
            elapsed_ms=round(_elapsed_ms(), 2),
        )

    usernames_count = len(unique_persisted)
    persisted_count = usernames_count

    log(
        "info",
        "welcome_baseline_visible_followers_harvested",
        account_id=aid,
        visible_rows_count=int(harvest_meta.get("visible_rows_count") or 0),
        usernames_count=usernames_count,
        sample_usernames=sorted(unique_persisted)[:12],
        extraction_methods=list(harvest_meta.get("extraction_methods") or []),
        scrolls_done=scrolls_done,
        scan_scope=scan_scope,
        screens_scanned=screens_scanned,
        duplicate_runtime_count=duplicate_runtime_count,
        total_followers_count_if_known=total_followers_count,
        stop_reason=stop_reason,
    )

    if usernames_count <= 0:
        log("error", "welcome_baseline_aborted", reason="no_usernames_harvested")
        return _finish("partial", 1, "no_usernames_harvested")

    completed, comp_mode, skip_reason = _evaluate_baseline_completion(
        followers_list_open_ok=followers_list_open_ok,
        window_mode=window_mode,
        partial_visible_scan=partial_visible_scan,
        unique_persisted_count=usernames_count,
        failed_persist_count=failed_count,
        elapsed_ms=_elapsed_ms(),
        max_seconds=max_seconds,
        min_rows=min_rows,
        scrolls_done=scrolls_done,
        initial_rows_count=initial_rows_count,
        total_followers_count=total_followers_count,
    )

    if completed and comp_mode:
        try:
            supabase_client.mark_welcome_baseline_completed(aid, scan_run_id=scan_run_id)
            baseline_completed = True
            completion_mode = comp_mode
        except Exception as e:
            log(
                "error",
                "welcome_baseline_aborted",
                reason="mark_baseline_completed_failed",
                error=str(e),
            )
            return _finish("partial", 1, "mark_baseline_completed_failed")
        log(
            "info",
            "welcome_baseline_completed",
            account_id=aid,
            followers_seen_count=usernames_count,
            followers_persisted_count=persisted_count,
            scan_scope=scan_scope,
            run_id=scan_run_id,
            completion_mode=completion_mode,
            total_followers_count_if_known=total_followers_count,
            scrolls_done=scrolls_done,
            screens_scanned=screens_scanned,
        )
        return _finish("success", 0)

    baseline_completion_skipped_reason = skip_reason or "completion_criteria_not_met"
    log(
        "info",
        "welcome_baseline_completion_skipped",
        account_id=aid,
        reason=baseline_completion_skipped_reason,
        followers_seen_count=usernames_count,
        followers_persisted_count=persisted_count,
        scan_scope=scan_scope,
        run_id=scan_run_id,
        total_followers_count_if_known=total_followers_count,
        scrolls_done=scrolls_done,
        min_rows_required=min_rows,
        elapsed_ms=round(_elapsed_ms(), 2),
    )
    return _finish(
        "partial_success",
        0,
        skipped_reason=baseline_completion_skipped_reason,
    )
