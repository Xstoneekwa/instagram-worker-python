"""
V4.2-B — Welcome scan producer: own followers list → anchor logic → enqueue pending jobs only.

No DM UI, no thread open, no send.
"""

from __future__ import annotations

import os
import re
import time
import uuid
import xml.etree.ElementTree as ET
from typing import Any, Literal

import uiautomator2 as u2

import config
import supabase_client
from instagram_navigation import (
    detect_followers_list_screen,
    followers_clear_detect_hierarchy_cache,
    followers_refresh_detect_hierarchy_cache,
    followers_session_list_committed_open_for,
    followers_session_merge_det_for_committed_visual_surface,
    harvest_visible_followers_rows,
    scroll_followers_list_forward,
)
from logs import log
from own_profile_navigation import (
    open_own_followers_list_from_own_profile,
    open_own_profile_from_bottom_nav,
    verify_own_profile,
)

DiscoveryPhase = Literal["pre_anchor", "post_anchor"]

_LAST_WELCOME_SCAN_SUMMARY: dict[str, Any] = {}


def get_last_welcome_scan_summary() -> dict[str, Any]:
    """Last welcome_scan_run_summary payload (for session orchestrator)."""
    return dict(_LAST_WELCOME_SCAN_SUMMARY)


def _norm_username(raw: str) -> str:
    return str(raw or "").strip().lstrip("@").lower()


def _as_nonnegative_int(value: Any, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return max(0, int(default))


def _explicit_welcome_send_hard_cap() -> tuple[bool, int]:
    if "WELCOME_SESSION_SEND_MAX_JOBS" not in os.environ:
        return False, 0
    return True, _as_nonnegative_int(
        getattr(config, "WELCOME_SESSION_SEND_MAX_JOBS", os.environ.get("WELCOME_SESSION_SEND_MAX_JOBS")),
        0,
    )


def _emit_run_summary(**kwargs: Any) -> None:
    global _LAST_WELCOME_SCAN_SUMMARY
    _LAST_WELCOME_SCAN_SUMMARY = dict(kwargs)
    log("info", "welcome_scan_run_summary", **kwargs)


def _followers_suggestions_boundary(
    hierarchy_xml: str,
    *,
    previously_valid_followers_rows: bool,
) -> dict[str, Any]:
    signals = {
        "selected_followers_tab": False,
        "see_all_suggestions": False,
        "suggestion_follow_rows": 0,
        "dismiss_controls": 0,
        "loading_indicator": False,
    }
    try:
        root = ET.fromstring(str(hierarchy_xml or ""))
    except ET.ParseError:
        return {"is_boundary": False, **signals}

    for node in root.iter():
        text = str(node.attrib.get("text") or "").strip()
        content_desc = str(node.attrib.get("content-desc") or "").strip()
        resource_id = str(node.attrib.get("resource-id") or "").lower()
        class_name = str(node.attrib.get("class") or "").lower()
        normalized = " ".join((text or content_desc).lower().split())
        if node.attrib.get("selected") == "true" and re.fullmatch(r"\d+\s+followers", normalized):
            signals["selected_followers_tab"] = True
        if normalized == "see all suggestions":
            signals["see_all_suggestions"] = True
        if text.lower() in {"follow", "follow back"}:
            signals["suggestion_follow_rows"] += 1
        if (
            text in {"x", "X", "×"}
            or normalized in {"remove", "dismiss", "close"}
            or any(token in resource_id for token in ("dismiss", "remove", "close"))
        ):
            signals["dismiss_controls"] += 1
        if class_name.endswith("progressbar") or any(token in resource_id for token in ("progress", "loading", "spinner")):
            signals["loading_indicator"] = True

    suggestions_rows = (
        signals["suggestion_follow_rows"] > 0
        and signals["dismiss_controls"] > 0
    )
    explicit_boundary = signals["see_all_suggestions"] or suggestions_rows
    transient_boundary = previously_valid_followers_rows and signals["loading_indicator"]
    return {
        "is_boundary": bool(signals["selected_followers_tab"] and (explicit_boundary or transient_boundary)),
        **signals,
    }


def run_welcome_scan_producer(
    d: u2.Device,
    *,
    account_id: str,
    account_username: str,
    run_id: str | None = None,
) -> int:
    """
    Welcome producer scan (enqueue ig_dm_jobs welcome pending only).
    Returns process exit code: 0 success, 1 failed/skipped.
    """
    t0 = time.perf_counter()
    aid = str(account_id or "").strip()
    uname = str(account_username or "").strip()
    scan_run_id = (run_id or "").strip() or str(uuid.uuid4())

    max_scrolls = int(getattr(config, "WELCOME_SCAN_MAX_SCROLLS_V1", 5) or 5)
    max_seconds = float(getattr(config, "WELCOME_SCAN_MAX_SECONDS_V1", 90) or 90)
    known_stop_k = int(getattr(config, "WELCOME_SCAN_KNOWN_CONSECUTIVE_STOP_V1", 5) or 5)
    stagnation_max = int(getattr(config, "WELCOME_SCAN_STAGNATION_NO_NEW_ROWS_MAX", 2) or 2)
    post_scroll_settle_s = float(getattr(config, "WELCOME_SCAN_POST_SCROLL_SETTLE_S", 0.65) or 0.65)

    settings: dict[str, Any] = {}
    welcome_enabled = False
    baseline_completed = False
    welcome_template_id: str | None = None
    session_sent_cap = 10
    session_candidate_attempt_cap = 10
    configured_candidate_attempt_cap = 10
    db_welcome_per_session_limit = 10
    db_welcome_per_day_limit = 10
    db_total_dm_per_day_limit = 10
    welcome_sent_today = 0
    total_dm_sent_today = 0
    welcome_day_remaining_today = 10
    total_dm_day_remaining_today = 10
    welcome_send_hard_cap_present = False
    welcome_send_hard_cap = 0

    screens_scanned = 0
    scrolls_done = 0
    usernames_seen_total = 0
    known_count = 0
    unknown_pre_anchor_count = 0
    unknown_post_anchor_gap_count = 0
    jobs_enqueued_count = 0
    jobs_not_enqueued_count = 0
    stop_reason: str | None = None
    failure_reason: str | None = None

    phase: DiscoveryPhase = "pre_anchor"
    anchor_found = False
    first_anchor_username: str | None = None
    consecutive_known = 0
    stagnation_no_new_rows = 0
    runtime_seen: set[str] = set()
    new_follower_usernames_detected: list[str] = []
    new_follower_usernames_enqueued: list[str] = []
    new_follower_visible_rows_enqueued: list[dict[str, Any]] = []
    new_follower_job_ids_enqueued: list[dict[str, Any]] = []
    enqueue_blocked_global = False
    enqueue_block_reason: str | None = None
    suggestions_boundary_detected = False

    def _elapsed_ms() -> float:
        return (time.perf_counter() - t0) * 1000.0

    def _finish(status: str, code: int, reason: str | None = None) -> int:
        nonlocal failure_reason
        if reason:
            failure_reason = reason
        _emit_run_summary(
            account_id=aid,
            run_id=scan_run_id,
            status=status,
            welcome_enabled=welcome_enabled,
            baseline_completed=baseline_completed,
            db_welcome_per_session_limit=db_welcome_per_session_limit,
            db_welcome_per_day_limit=db_welcome_per_day_limit,
            db_total_dm_per_day_limit=db_total_dm_per_day_limit,
            welcome_sent_today=welcome_sent_today,
            total_dm_sent_today=total_dm_sent_today,
            welcome_day_remaining_today=welcome_day_remaining_today,
            total_dm_day_remaining_today=total_dm_day_remaining_today,
            effective_welcome_scan_cap=session_candidate_attempt_cap,
            effective_welcome_sent_cap=session_sent_cap,
            welcome_send_hard_cap_present=welcome_send_hard_cap_present,
            welcome_send_hard_cap=welcome_send_hard_cap if welcome_send_hard_cap_present else None,
            candidate_attempt_cap=session_candidate_attempt_cap,
            configured_candidate_attempt_cap=configured_candidate_attempt_cap,
            screens_scanned=screens_scanned,
            scrolls_done=scrolls_done,
            usernames_seen_total=usernames_seen_total,
            known_count=known_count,
            unknown_pre_anchor_count=unknown_pre_anchor_count,
            unknown_post_anchor_gap_count=unknown_post_anchor_gap_count,
            new_follower_usernames_detected=list(new_follower_usernames_detected),
            new_follower_usernames_enqueued=list(new_follower_usernames_enqueued),
            new_follower_visible_rows_enqueued=list(new_follower_visible_rows_enqueued),
            new_follower_job_ids_enqueued=list(new_follower_job_ids_enqueued),
            scan_final_screen_index=int(scrolls_done),
            jobs_enqueued_count=jobs_enqueued_count,
            jobs_not_enqueued_count=jobs_not_enqueued_count,
            first_anchor_username=first_anchor_username,
            consecutive_known_stop_count=consecutive_known if stop_reason == "consecutive_known_stop" else consecutive_known,
            stop_reason=stop_reason,
            failure_reason=failure_reason,
            followers_suggestions_boundary_detected=suggestions_boundary_detected,
            followers_suggestions_boundary_backtrack_attempted=False,
            total_ms=round(_elapsed_ms(), 2),
        )
        return code

    if not aid:
        log("error", "welcome_scan_aborted", reason="missing_account_id")
        return _finish("failed", 1, "missing_account_id")

    log(
        "info",
        "welcome_scan_run_started",
        account_id=aid,
        account_username=uname,
        run_id=scan_run_id,
    )

    try:
        settings = supabase_client.ensure_account_dm_settings(aid) or {}
    except Exception as e:
        log("error", "welcome_scan_aborted", reason="ensure_dm_settings_failed", error=str(e))
        return _finish("failed", 1, "ensure_dm_settings_failed")

    welcome_enabled = bool(settings.get("welcome_enabled"))
    baseline_completed = bool(settings.get("welcome_baseline_completed_at"))
    welcome_template_id = (
        str(settings.get("welcome_template_id") or "").strip() or None
    )
    db_welcome_per_session_limit = _as_nonnegative_int(
        settings.get("welcome_per_session_limit"),
        10,
    )
    db_welcome_per_day_limit = _as_nonnegative_int(
        settings.get("welcome_per_day_limit"),
        db_welcome_per_session_limit,
    )
    db_total_dm_per_day_limit = _as_nonnegative_int(
        settings.get("total_dm_per_day_limit"),
        db_welcome_per_day_limit,
    )
    try:
        counter = supabase_client.get_account_dm_counter_today(aid) or {}
    except Exception as e:
        counter = {}
        log("error", "welcome_scan_counter_load_failed", account_id=aid, error=str(e))
    welcome_sent_today = _as_nonnegative_int(counter.get("welcome_sent_count"), 0)
    total_dm_sent_today = _as_nonnegative_int(counter.get("total_dm_sent_count"), 0)
    welcome_day_remaining_today = max(0, db_welcome_per_day_limit - welcome_sent_today)
    total_dm_day_remaining_today = max(0, db_total_dm_per_day_limit - total_dm_sent_today)
    session_sent_cap = min(
        db_welcome_per_session_limit,
        welcome_day_remaining_today,
        total_dm_day_remaining_today,
    )
    welcome_send_hard_cap_present, welcome_send_hard_cap = _explicit_welcome_send_hard_cap()
    if welcome_send_hard_cap_present:
        session_sent_cap = min(session_sent_cap, welcome_send_hard_cap)
    configured_candidate_attempt_cap = _as_nonnegative_int(
        getattr(config, "WELCOME_SCAN_CANDIDATE_ATTEMPT_CAP", 3),
        3,
    )
    if configured_candidate_attempt_cap <= 0:
        configured_candidate_attempt_cap = session_sent_cap
    session_candidate_attempt_cap = max(
        session_sent_cap,
        configured_candidate_attempt_cap,
    )
    if welcome_send_hard_cap_present:
        session_candidate_attempt_cap = min(
            session_candidate_attempt_cap,
            welcome_send_hard_cap,
        )
    log(
        "info",
        "welcome_scan_effective_limits_resolved",
        account_id=aid,
        run_id=scan_run_id,
        db_welcome_per_session_limit=db_welcome_per_session_limit,
        db_welcome_per_day_limit=db_welcome_per_day_limit,
        db_total_dm_per_day_limit=db_total_dm_per_day_limit,
        welcome_sent_today=welcome_sent_today,
        total_dm_sent_today=total_dm_sent_today,
        welcome_day_remaining_today=welcome_day_remaining_today,
        total_dm_day_remaining_today=total_dm_day_remaining_today,
        effective_welcome_scan_cap=session_candidate_attempt_cap,
        effective_welcome_sent_cap=session_sent_cap,
        welcome_send_hard_cap_present=welcome_send_hard_cap_present,
        welcome_send_hard_cap=welcome_send_hard_cap if welcome_send_hard_cap_present else None,
        source="min(db_session,db_day_remaining,total_dm_day_remaining,env_hard_cap)"
        if welcome_send_hard_cap_present
        else "min(db_session,db_day_remaining,total_dm_day_remaining)",
    )
    log(
        "info",
        "welcome_scan_candidate_attempt_cap_resolved",
        account_id=aid,
        run_id=scan_run_id,
        sent_cap=session_sent_cap,
        candidate_attempt_cap=session_candidate_attempt_cap,
        configured_candidate_attempt_cap=configured_candidate_attempt_cap,
        welcome_send_hard_cap_present=welcome_send_hard_cap_present,
        welcome_send_hard_cap=welcome_send_hard_cap if welcome_send_hard_cap_present else None,
        source="min(max(sent_cap,config.WELCOME_SCAN_CANDIDATE_ATTEMPT_CAP),env_hard_cap)"
        if welcome_send_hard_cap_present
        else "max(sent_cap,config.WELCOME_SCAN_CANDIDATE_ATTEMPT_CAP)",
    )

    if not welcome_enabled:
        log("info", "welcome_scan_skipped", account_id=aid, reason="welcome_disabled")
        return _finish("skipped", 0, "welcome_disabled")

    if not baseline_completed:
        log("info", "welcome_scan_skipped", account_id=aid, reason="baseline_not_completed")
        return _finish("skipped", 0, "baseline_not_completed")

    if session_sent_cap <= 0:
        log("info", "welcome_scan_skipped", account_id=aid, reason="welcome_day_limit_reached")
        return _finish("skipped", 0, "welcome_day_limit_reached")

    if not open_own_profile_from_bottom_nav(d):
        log("error", "welcome_scan_aborted", reason="own_profile_open_failed")
        return _finish("failed", 1, "own_profile_open_failed")

    ok_profile, _prof_meta = verify_own_profile(d, uname)
    if not ok_profile:
        log("error", "welcome_scan_aborted", reason="own_profile_verify_failed")
        return _finish("failed", 1, "own_profile_verify_failed")

    ok_followers, fol_meta = open_own_followers_list_from_own_profile(
        d,
        uname,
        pkg=str(getattr(config, "INSTAGRAM_PACKAGE", "") or ""),
        followers_open_wait_s=float(
            getattr(config, "WELCOME_SCAN_FOLLOWERS_OPEN_WAIT_S", 0.6) or 0.0
        ),
    )
    if not ok_followers:
        log("error", "welcome_scan_aborted", reason="own_followers_open_failed")
        return _finish("failed", 1, "own_followers_open_failed")

    fol_meta = fol_meta if isinstance(fol_meta, dict) else {}
    det = fol_meta.get("det")
    if not (isinstance(det, dict) and bool(det.get("is_followers_list"))):
        det = detect_followers_list_screen(d, source_profile_username=uname)
        if followers_session_list_committed_open_for(uname):
            det = followers_session_merge_det_for_committed_visual_surface(
                det,
                session_vf_detail_for_loop=None,
                open_list_meta=fol_meta.get("open_meta")
                if isinstance(fol_meta.get("open_meta"), dict)
                else None,
            )
    if not bool(det.get("is_followers_list")):
        log("error", "welcome_scan_aborted", reason="followers_surface_not_verified")
        return _finish("failed", 1, "followers_surface_not_verified")

    def _should_stop_scan() -> bool:
        if stop_reason is not None:
            return True
        if _elapsed_ms() >= max_seconds * 1000.0:
            return True
        if scrolls_done >= max_scrolls:
            return True
        if stagnation_no_new_rows >= stagnation_max:
            return True
        if phase == "post_anchor" and consecutive_known >= known_stop_k:
            return True
        if jobs_enqueued_count >= session_candidate_attempt_cap:
            return True
        return False

    def _process_screen(screen_index: int) -> None:
        nonlocal screens_scanned, usernames_seen_total, stagnation_no_new_rows
        nonlocal phase, anchor_found, first_anchor_username, consecutive_known
        nonlocal known_count, unknown_pre_anchor_count, unknown_post_anchor_gap_count
        nonlocal jobs_enqueued_count, jobs_not_enqueued_count, stop_reason
        nonlocal enqueue_blocked_global, enqueue_block_reason

        if _should_stop_scan():
            return

        row_models, meta = harvest_visible_followers_rows(
            d,
            source_profile_username=uname,
            runtime_seen=runtime_seen,
            force_fresh_hierarchy=(screen_index > 0),
            screen_index=screen_index,
        )
        if (
            screen_index == 0
            and str(meta.get("hierarchy_source") or "") == "cached"
            and row_models
        ):
            log(
                "info",
                "welcome_scan_harvest_reused_followers_entry_snapshot",
                account_id=aid,
                run_id=scan_run_id,
                screen_index=screen_index,
                rows_count=len(row_models),
                extraction_methods=list(meta.get("extraction_methods") or []),
            )
        batch = [str(r.get("username") or "").strip() for r in row_models if r.get("username")]
        row_by_key = {_norm_username(str(r.get("username") or "")): r for r in row_models}
        screens_scanned += 1

        ordered_handles: list[str] = []
        new_seen_this_screen = 0
        for raw in batch:
            key = _norm_username(raw)
            if not key:
                continue
            if key not in runtime_seen:
                runtime_seen.add(key)
                new_seen_this_screen += 1
            ordered_handles.append(str(raw).strip().lstrip("@"))

        if new_seen_this_screen <= 0:
            stagnation_no_new_rows += 1
        else:
            stagnation_no_new_rows = 0

        usernames_seen_total = len(runtime_seen)

        if ordered_handles:
            try:
                supabase_client.mark_followbacks_from_seen_followers(
                    aid,
                    ordered_handles,
                    source="welcome_followers_scan",
                )
            except Exception as e:
                log(
                    "warning",
                    "followback_memory_mark_failed",
                    account_id=aid,
                    source="welcome_followers_scan",
                    input_count=len(ordered_handles),
                    normalized_count=0,
                    updated_count=0,
                    matched_count=0,
                    duration_ms=0.0,
                    error=str(e)[:500],
                )

        known_map = supabase_client.fetch_followers_by_usernames(aid, ordered_handles)
        pending_job_map = supabase_client.fetch_pending_welcome_jobs_by_usernames(
            aid,
            ordered_handles,
        )

        log(
            "info",
            "welcome_scan_screen_harvested",
            account_id=aid,
            screen_index=screen_index,
            visible_rows_count=int(meta.get("visible_rows_count") or len(batch)),
            handles_on_screen=len(ordered_handles),
            known_on_screen=len(known_map),
            phase=phase,
            extraction_methods=list(meta.get("extraction_methods") or []),
        )

        for handle in ordered_handles:
            if _should_stop_scan():
                break
            key = _norm_username(handle)
            if not key:
                continue

            row = known_map.get(key)
            if row is not None:
                known_count += 1
                baseline_anchor = bool(row.get("baseline_existing")) or (
                    str(row.get("welcome_dm_status") or "") == "not_eligible_baseline"
                )
                if not baseline_anchor and phase == "pre_anchor":
                    pending_job = pending_job_map.get(key)
                    if (
                        not pending_job
                        and str(row.get("welcome_dm_status") or "") == "pending"
                        and jobs_enqueued_count
                        < (
                            session_sent_cap
                            if welcome_send_hard_cap_present
                            else session_candidate_attempt_cap
                        )
                    ):
                        pending_job = supabase_client.enqueue_welcome_dm_job_if_eligible(
                            aid,
                            handle,
                            scan_run_id=scan_run_id,
                            template_id=welcome_template_id,
                            message_body=None,
                            account_username=uname,
                        )
                    if (
                        pending_job
                        and jobs_enqueued_count
                        < (
                            session_sent_cap
                            if welcome_send_hard_cap_present
                            else session_candidate_attempt_cap
                        )
                    ):
                        jobs_enqueued_count += 1
                        jid = str(pending_job.get("id") or "")
                        new_follower_usernames_detected.append(str(handle).strip())
                        new_follower_usernames_enqueued.append(str(handle).strip())
                        row_snap = row_by_key.get(key)
                        tap_bounds = dict(
                            (row_snap or {}).get("tap_bounds")
                            or (row_snap or {}).get("bounds")
                            or {}
                        )
                        username_bounds = dict((row_snap or {}).get("username_bounds") or {})
                        new_follower_visible_rows_enqueued.append(
                            {
                                "username": str((row_snap or {}).get("username") or handle),
                                "row_index": (row_snap or {}).get("row_index"),
                                "username_bounds": username_bounds,
                                "tap_bounds": tap_bounds,
                                "extraction_source": str((row_snap or {}).get("extraction_source") or ""),
                                "screen_index": int((row_snap or {}).get("screen_index") or screen_index),
                                "hierarchy_source": str(
                                    (row_snap or {}).get("hierarchy_source")
                                    or meta.get("hierarchy_source")
                                    or ""
                                ),
                            }
                        )
                        new_follower_job_ids_enqueued.append(
                            {
                                "job_id": jid,
                                "username": str(handle).strip(),
                                "screen_index": int((row_snap or {}).get("screen_index") or screen_index),
                                "row_index": (row_snap or {}).get("row_index"),
                                "tap_bounds": tap_bounds or None,
                                "username_bounds": username_bounds or None,
                                "bounds": tap_bounds or username_bounds or None,
                            }
                        )
                        log(
                            "info",
                            "welcome_scan_existing_pending_job_reused",
                            account_id=aid,
                            username=handle,
                            job_id=jid,
                            screen_index=screen_index,
                            welcome_dm_status=str(row.get("welcome_dm_status") or ""),
                        )
                        continue
                    log(
                        "info",
                        "welcome_scan_known_nonbaseline_pre_anchor_ignored",
                        account_id=aid,
                        username=str(row.get("follower_username") or handle),
                        screen_index=screen_index,
                        welcome_dm_status=str(row.get("welcome_dm_status") or ""),
                        skip_reason=str(row.get("skip_reason") or ""),
                    )
                    continue
                if not anchor_found:
                    anchor_found = True
                    first_anchor_username = str(row.get("follower_username") or handle)
                    phase = "post_anchor"
                    log(
                        "info",
                        "welcome_scan_anchor_found",
                        account_id=aid,
                        username=first_anchor_username,
                        screen_index=screen_index,
                        welcome_dm_status=str(row.get("welcome_dm_status") or ""),
                        baseline_existing=bool(row.get("baseline_existing")),
                    )
                if phase == "post_anchor":
                    consecutive_known += 1
                    if consecutive_known >= known_stop_k:
                        stop_reason = "consecutive_known_stop"
                        break
                continue

            if phase == "pre_anchor":
                unknown_pre_anchor_count += 1
                new_follower_usernames_detected.append(str(handle).strip())
                log(
                    "info",
                    "welcome_scan_new_follower_pre_anchor_detected",
                    account_id=aid,
                    username=handle,
                    screen_index=screen_index,
                    discovery_phase=phase,
                )
                if jobs_enqueued_count >= session_candidate_attempt_cap:
                    if stop_reason is None:
                        stop_reason = "candidate_attempt_cap_reached"
                    log(
                        "info",
                        "welcome_scan_candidate_attempt_cap_reached",
                        account_id=aid,
                        sent_cap=session_sent_cap,
                        candidate_attempt_cap=session_candidate_attempt_cap,
                        jobs_enqueued_count=jobs_enqueued_count,
                    )
                    break
                if welcome_send_hard_cap_present and jobs_enqueued_count >= session_sent_cap:
                    if stop_reason is None:
                        stop_reason = "sent_cap_reached"
                    log(
                        "info",
                        "welcome_scan_sent_cap_reached",
                        account_id=aid,
                        sent_cap=session_sent_cap,
                        candidate_attempt_cap=session_candidate_attempt_cap,
                        jobs_enqueued_count=jobs_enqueued_count,
                    )
                    break
                if enqueue_blocked_global:
                    jobs_not_enqueued_count += 1
                    log(
                        "info",
                        "welcome_scan_welcome_job_not_enqueued",
                        account_id=aid,
                        username=handle,
                        reason=enqueue_block_reason or "enqueue_blocked_global",
                    )
                    continue
                try:
                    job = supabase_client.enqueue_welcome_dm_job_if_eligible(
                        aid,
                        handle,
                        scan_run_id=scan_run_id,
                        template_id=welcome_template_id,
                        message_body=None,
                        account_username=uname,
                    )
                except RuntimeError as e:
                    err = str(e)
                    if "dm_template_render_failed" in err.lower():
                        enqueue_blocked_global = True
                        enqueue_block_reason = "welcome_template_render_failed"
                        jobs_not_enqueued_count += 1
                        log(
                            "error",
                            "welcome_scan_enqueue_failed_template_render",
                            account_id=aid,
                            reason=err[:160],
                        )
                        continue
                    if "no welcome message_body or template" in err.lower():
                        enqueue_blocked_global = True
                        enqueue_block_reason = "no_welcome_template"
                        jobs_not_enqueued_count += 1
                        log(
                            "error",
                            "welcome_scan_enqueue_failed_no_template",
                            account_id=aid,
                            error=err[:300],
                        )
                        continue
                    jobs_not_enqueued_count += 1
                    log(
                        "warning",
                        "welcome_scan_enqueue_failed",
                        account_id=aid,
                        username=handle,
                        error=err[:300],
                    )
                    continue

                if job and job.get("id"):
                    jobs_enqueued_count += 1
                    jid = str(job.get("id"))
                    new_follower_usernames_enqueued.append(str(handle).strip())
                    row_snap = row_by_key.get(key)
                    row_bounds: dict[str, Any] = {}
                    username_bounds: dict[str, Any] = {}
                    tap_bounds: dict[str, Any] = {}
                    if row_snap:
                        username_bounds = dict(row_snap.get("username_bounds") or {})
                        tap_bounds = dict(
                            row_snap.get("tap_bounds")
                            or row_snap.get("bounds")
                            or {}
                        )
                        row_bounds = dict(row_snap.get("bounds") or tap_bounds)
                        new_follower_visible_rows_enqueued.append(
                            {
                                "username": str(row_snap.get("username") or handle),
                                "row_index": row_snap.get("row_index"),
                                "username_bounds": username_bounds,
                                "tap_bounds": tap_bounds,
                                "extraction_source": str(
                                    row_snap.get("extraction_source") or ""
                                ),
                                "screen_index": int(row_snap.get("screen_index") or screen_index),
                                "hierarchy_source": str(
                                    row_snap.get("hierarchy_source")
                                    or meta.get("hierarchy_source")
                                    or ""
                                ),
                            }
                        )
                    new_follower_job_ids_enqueued.append(
                        {
                            "job_id": jid,
                            "username": str(handle).strip(),
                            "screen_index": int(
                                row_snap.get("screen_index") if row_snap else screen_index
                            ),
                            "row_index": row_snap.get("row_index") if row_snap else None,
                            "tap_bounds": tap_bounds or None,
                            "username_bounds": username_bounds or None,
                            "bounds": row_bounds or None,
                        }
                    )
                    log(
                        "info",
                        "welcome_scan_welcome_job_enqueued",
                        account_id=aid,
                        username=handle,
                        job_id=jid,
                        screen_index=screen_index,
                        discovery_phase=phase,
                        job_status=str(job.get("status") or ""),
                    )
                else:
                    jobs_not_enqueued_count += 1
                    log(
                        "info",
                        "welcome_scan_welcome_job_not_enqueued",
                        account_id=aid,
                        username=handle,
                        screen_index=screen_index,
                        discovery_phase=phase,
                        reason="rpc_returned_null",
                    )
                continue

            unknown_post_anchor_gap_count += 1
            log(
                "info",
                "welcome_scan_post_anchor_gap_detected",
                account_id=aid,
                username=handle,
                screen_index=screen_index,
                discovery_phase=phase,
            )
            try:
                gap_row = supabase_client.persist_welcome_scan_anchor_gap(
                    aid,
                    handle,
                    scan_run_id=scan_run_id,
                )
                log(
                    "info",
                    "welcome_scan_post_anchor_gap_persisted",
                    account_id=aid,
                    username=handle,
                    follower_id=str((gap_row or {}).get("id") or ""),
                    welcome_dm_status=str((gap_row or {}).get("welcome_dm_status") or ""),
                    skip_reason=str((gap_row or {}).get("skip_reason") or ""),
                )
            except Exception as e:
                log(
                    "warning",
                    "welcome_scan_post_anchor_gap_persist_failed",
                    account_id=aid,
                    username=handle,
                    error=str(e)[:300],
                )

    _process_screen(0)

    while not _should_stop_scan():
        if _elapsed_ms() >= max_seconds * 1000.0:
            stop_reason = "max_seconds_reached"
            break
        if stagnation_no_new_rows >= stagnation_max:
            stop_reason = "stagnation_no_new_rows"
            break
        if scrolls_done >= max_scrolls:
            stop_reason = "max_scrolls_reached"
            break

        if not scroll_followers_list_forward(
            d,
            source_profile_username=uname,
            bypass_post_tap_capture_gate=True,
            bypass_scroll_xml_guards=True,
        ):
            stop_reason = "scroll_failed"
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
            boundary = _followers_suggestions_boundary(
                hier,
                previously_valid_followers_rows=bool(runtime_seen),
            )
            if bool(boundary.get("is_boundary")):
                suggestions_boundary_detected = True
                log(
                    "info",
                    "followers_suggestions_boundary",
                    account_id=aid,
                    run_id=scan_run_id,
                    screen_index=scrolls_done,
                    jobs_enqueued_count=jobs_enqueued_count,
                    **{key: value for key, value in boundary.items() if key != "is_boundary"},
                )
                stop_reason = "followers_suggestions_boundary"
                break

            stop_reason = "followers_surface_lost"
            break

        _process_screen(scrolls_done)

    if stop_reason is None:
        if jobs_enqueued_count >= session_candidate_attempt_cap:
            stop_reason = "candidate_attempt_cap_reached"
        elif phase == "post_anchor" and consecutive_known >= known_stop_k:
            stop_reason = "consecutive_known_stop"
        elif scrolls_done >= max_scrolls:
            stop_reason = "max_scrolls_reached"
        elif _elapsed_ms() >= max_seconds * 1000.0:
            stop_reason = "max_seconds_reached"
        elif stagnation_no_new_rows >= stagnation_max:
            stop_reason = "stagnation_no_new_rows"
        else:
            stop_reason = "scan_complete"

    log(
        "info",
        "welcome_scan_stop",
        account_id=aid,
        stop_reason=stop_reason,
        scrolls_done=scrolls_done,
        screens_scanned=screens_scanned,
        phase=phase,
        anchor_found=anchor_found,
        consecutive_known=consecutive_known,
        jobs_enqueued_count=jobs_enqueued_count,
        elapsed_ms=round(_elapsed_ms(), 2),
    )

    if enqueue_blocked_global and jobs_enqueued_count == 0:
        return _finish("failed", 1, enqueue_block_reason or "no_welcome_template")
    return _finish("success", 0)
