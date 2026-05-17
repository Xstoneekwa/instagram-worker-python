"""Orchestrate one safe Instagram navigation run (PoC)."""

from __future__ import annotations

import argparse
import os
import random
import re
import time
import uuid
from pathlib import Path
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

import social_memory
from followers_exploration import (
    FollowersExplorationV1,
    exploration_v1_enabled,
    followers_progressive_max_passes,
    followers_scroll_soft_max_per_session,
)
from followers_injection_evidence import (
    invalidate_followers_injection_evidence,
    note_followers_injection_capture,
    set_committed_light_revalidate_ok,
    should_skip_committed_loop_top_detect,
    should_skip_post_return_injection_capture,
)
from visual_follow_history import (
    VISUAL_FOLLOW_HISTORY_CONTINUE,
    mark_visual_follow_target_processed,
    visual_follow_history_skip_reason_if_any,
)

import config
import supabase_client
from device import (
    app_start,
    check_instagram_version_lock,
    connect_device,
    disable_android_animations,
    force_stop,
    health_check,
    lock_instagram_update_system,
    press_home,
    screenshot,
)
from navigation_engine import NavigationEngineState, observe_instagram_state
from visual_row_mapping import (
    visual_map_followers_rows_from_screenshot,
    visual_row_mapping_to_follower_engine_candidates,
)
from instagram_navigation import (
    cleanup_dm_after_send_button_missing,
    clear_dm_draft,
    detect_unsupported_start_surface,
    dismiss_android_permission_dialog,
    finalize_after_real_send,
    finalize_dm_draft_before_back,
    get_perf_snapshot,
    get_last_dm_thread_classify_snapshot,
    get_last_dm_send_result,
    get_last_dm_thread_attempted,
    get_last_dm_thread_state,
    get_type_search_failure_reason,
    instagram_warm_session_eligible,
    invalidate_search_surface_cache,
    is_dm_thread_screen,
    is_lightweight_search_screen,
    open_dm_thread_from_profile,
    open_accounts_tab,
    open_search,
    reset_dm_send_run_state,
    reset_perf_counters,
    reset_dm_thread_probe_state,
    return_to_profile_from_dm,
    reset_to_search_for_next_target,
    return_to_search_from_profile,
    send_dm_safe,
    set_perf_metric,
    set_wait_event_callback,
    set_search_ui_mode,
    tap_account_result,
    type_dm_draft_only,
    type_search,
    verify_dm_composer_safe,
    verify_dm_draft_text,
    verify_app_foreground,
    verify_profile,
    perform_follow_safe,
    visual_follow_post_action_reconcile,
    open_followers_list_from_profile,
    detect_followers_list_screen,
    detect_followers_list_screen_visual_fallback,
    followers_surface_quick_revalidate,
    followers_session_reset_list_committed_open,
    followers_session_list_committed_open_for,
    followers_session_committed_meta,
    followers_session_merge_det_for_committed_visual_surface,
    followers_session_clear_list_committed_open,
    followers_session_mark_list_committed_open,
    followers_committed_surface_light_revalidate,
    _followers_committed_rendered_strong_visual_ok,
    _followers_set_last_open_detection_method,
    FOLLOWERS_ENGINE_XML_STALE_EXIT_REASONS,
    FOLLOWERS_ENGINE_VISUAL_OPEN_METHODS,
    FOLLOWERS_RENDERED_STRONG_COMMITTED_SOURCES,
    followers_bypass_xml_stale_recovery_if_visual_surface_strong,
    followers_allow_visual_exploratory_scroll_once,
    followers_clear_exploratory_scroll_permit_unused,
    followers_engine_clear_stop_reason,
    get_followers_engine_stop_reason,
    iter_followers_candidates,
    open_follower_profile_from_list,
    return_to_followers_list,
    run_visual_candidate_post_follow_phase,
    scroll_followers_list_forward,
    followers_refresh_hierarchy_for_candidates,
    followers_force_hierarchy_refresh,
    visual_extract_followers_candidates_from_screenshot,
    open_visual_follower_candidate_from_screenshot,
    visual_like_open_post,
    visual_verify_post_liked,
    visual_open_recent_post_from_profile,
    visual_return_to_profile_from_post,
    visual_detect_follow_post_actions,
    visual_follow_profile_dry_run,
    visual_mute_after_follow_dry_run,
    visual_open_following_options_after_follow,
    visual_open_mute_sheet_from_following_options,
    visual_toggle_mute_posts_and_stories,
    visual_capture_profile_context,
    visual_target_profile_lock_clear,
    visual_target_profile_lock_verify,
    visual_detect_private_profile,
    visual_extract_profile_metrics,
    visual_flow_final_return_to_ct_followers_list,
    verify_followers_list_surface_is_ct_account,
    reset_instagram_to_canonical_state,
    ensure_global_search_surface,
    visual_profile_metrics_pass_filter,
    visual_profile_stats_posts_count,
    read_current_profile_username_for_follow_gate,
    reacquire_target_profile_for_follow,
    visual_candidate_follow_pre_follow_screen_guard,
    _followers_current_pkg_activity,
    _follow_ui_state_snapshot,
    _visual_follow_request_pending_state,
    runner_invalidate_visual_followers_session_after_safe_stop,
)
from logs import get_run_log_file_path, init_run_file_logging, log

# Real DM sends per worker process (pairs with SEND_DM_MAX_PER_RUN).
_RUNTIME_REAL_DM_SENT_COUNT: int = 0
# Successful follows this process (pairs with FOLLOW_MAX_PER_RUN).
_RUNTIME_FOLLOW_COUNT: int = 0
_RUNTIME_FOLLOWED_USERNAMES: set[str] = set()
# Follower rows already selected in followers-list engine (same run).
_RUNTIME_SEEN_FOLLOWER_USERNAMES: set[str] = set()
# Opens that resolved username on-profile but did not complete follow (anti-loop).
_RUNTIME_FOLLOWERS_POST_RESOLVE_STREAK: dict[str, int] = {}
# CT followers-list engine: usernames for which perform_follow_safe has returned this process.
_RUNTIME_CT_LIST_FOLLOW_TAP_ATTEMPTED_USERNAMES: set[str] = set()
# perform_follow_safe returned follow_started but no follow_tap_sent (per fkey, this run).
_CT_FOLLOW_NO_TAP_AFTER_START_COUNT: dict[str, int] = {}
# Candidates that returned failed_no_follow_tap this run — never reopen without a successful tap.
_RUNTIME_FAILED_NO_FOLLOW_TAP_FKEY: set[str] = set()
# Monotonic clock: deadline tracking for one CT-list candidate (survives profile reopens until tap or clear).
_CT_CANDIDATE_ATTEMPT_STARTED_MONO: float | None = None
_CT_CANDIDATE_ATTEMPT_FKEY: str | None = None
_RUNTIME_INTERACTED_USERNAMES: set[str] = set()
_RUNTIME_UNFOLLOWED_USERNAMES: set[str] = set()
_RUNTIME_SKIPPED_USERNAMES: set[str] = set()
_VISUAL_FOLLOWERS_OPEN_COUNT_THIS_SESSION: int = 0
_SESSION_SOCIAL_ID: str = ""
_SESSION_COUNTERS: dict[str, int] = {
    "follows": 0,
    "unfollows": 0,
    "likes": 0,
    "pm": 0,
    "interactions": 0,
    "successful_interactions": 0,
}


def _seconds_since_dm_sent_row(row: dict) -> float | None:
    """Wall-clock seconds since last send timestamp on target row, if any."""
    for key in ("dm_sent_at", "processed_at", "updated_at"):
        raw = row.get(key)
        if not raw:
            continue
        try:
            ts = str(raw).replace("Z", "+00:00")
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - dt).total_seconds()
        except Exception:
            continue
    return None


def _seconds_since_follow_row(row: dict) -> float | None:
    """Hours since last follow timestamp on target row, if any."""
    for key in ("followed_at", "updated_at", "processed_at"):
        raw = row.get(key)
        if not raw:
            continue
        try:
            ts = str(raw).replace("Z", "+00:00")
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0
        except Exception:
            continue
    return None


def _phase(name: str, phase_start: float) -> float:
    elapsed_ms = (time.perf_counter() - phase_start) * 1000
    log("info", "phase_timing", phase=name, elapsed_ms=round(elapsed_ms, 2))
    return time.perf_counter()


def _log_phase_budget(phase: str, elapsed_ms: float, expected_budget_ms: float) -> None:
    if elapsed_ms > expected_budget_ms:
        log(
            "warning",
            "phase_budget_exceeded",
            phase=phase,
            elapsed_ms=round(elapsed_ms, 2),
            expected_budget_ms=round(expected_budget_ms, 2),
        )
        supabase_client.log_performance_event(
            action_type="phase_budget_exceeded",
            status="warning",
            target_username=None,
            payload={
                "phase": phase,
                "elapsed_ms": round(elapsed_ms, 2),
                "expected_budget_ms": round(expected_budget_ms, 2),
            },
        )


def _emit_performance_summary(
    *,
    t0: float,
    warm_session_used: bool,
    force_stop_used: bool,
    exit_code: int,
    target_username: str,
    follows_completed_count: int | None = None,
    list_progressive_exploration_exhausted: bool | None = None,
    exploration_passes_used: int | None = None,
    exploration_max_passes: int | None = None,
    followers_session_outcome: str | None = None,
) -> None:
    total_ms = (time.perf_counter() - t0) * 1000
    snap = get_perf_snapshot()
    _perf_followers_extra: dict[str, Any] = {}
    if follows_completed_count is not None:
        _perf_followers_extra["follows_completed_count"] = int(follows_completed_count)
    if list_progressive_exploration_exhausted is not None:
        _perf_followers_extra["list_progressive_exploration_exhausted"] = bool(
            list_progressive_exploration_exhausted
        )
    if exploration_passes_used is not None:
        _perf_followers_extra["exploration_passes_used"] = int(exploration_passes_used)
    if exploration_max_passes is not None:
        _perf_followers_extra["exploration_max_passes"] = int(exploration_max_passes)
    if followers_session_outcome is not None and str(followers_session_outcome).strip():
        _perf_followers_extra["followers_session_outcome"] = str(followers_session_outcome).strip()
    log(
        "info",
        "performance_summary",
        total_ms=round(total_ms, 2),
        target_username=target_username,
        typing_command_ms=round(float(snap.get("typing_command_ms", 0.0)), 2),
        type_search_total_ms=round(float(snap.get("type_search_total_ms", 0.0)), 2),
        search_edittext_wait_ms=round(float(snap.get("search_edittext_wait_ms", 0.0)), 2),
        search_clear_ms=round(float(snap.get("search_clear_ms", 0.0)), 2),
        search_focus_ms=round(float(snap.get("search_focus_ms", 0.0)), 2),
        search_focus_attempts=round(float(snap.get("search_focus_attempts", 0.0)), 2),
        search_focus_retry_count=round(float(snap.get("search_focus_retry_count", 0.0)), 2),
        search_focus_strategy_used=round(float(snap.get("search_focus_strategy_used", 0.0)), 2),
        type_search_pipeline_start_ms=round(float(snap.get("type_search_pipeline_start_ms", 0.0)), 2),
        type_search_focus_phase_ms=round(float(snap.get("type_search_focus_phase_ms", 0.0)), 2),
        type_search_pre_fastime_ms=round(float(snap.get("type_search_pre_fastime_ms", 0.0)), 2),
        type_search_ime_available_probe_ms=round(
            float(snap.get("type_search_ime_available_probe_ms", 0.0)), 2
        ),
        type_search_fastime_phase_ms=round(float(snap.get("type_search_fastime_phase_ms", 0.0)), 2),
        type_search_result_wait_ms=round(float(snap.get("type_search_result_wait_ms", 0.0)), 2),
        type_search_finalize_ms=round(float(snap.get("type_search_finalize_ms", 0.0)), 2),
        type_search_wall_clock_guard_ms=round(
            float(snap.get("type_search_wall_clock_guard_ms", 0.0)), 2
        ),
        search_fastime_broadcast_ms=round(float(snap.get("search_fastime_broadcast_ms", 0.0)), 2),
        search_post_type_settle_ms=round(float(snap.get("search_post_type_settle_ms", 0.0)), 2),
        row_detect_ms=round(float(snap.get("row_detect_ms", 0.0)), 2),
        row_tap_command_ms=round(float(snap.get("row_tap_command_ms", 0.0)), 2),
        post_tap_settle_ms=round(float(snap.get("post_tap_settle_ms", 0.0)), 2),
        profile_transition_wait_ms=round(float(snap.get("profile_transition_wait_ms", 0.0)), 2),
        profile_verify_ms=round(float(snap.get("profile_verify_ms", 0.0)), 2),
        dm_open_click_ms=round(float(snap.get("dm_open_click_ms", 0.0)), 2),
        dm_thread_detect_ms=round(float(snap.get("dm_thread_detect_ms", 0.0)), 2),
        dm_post_open_settle_ms=round(float(snap.get("dm_post_open_settle_ms", 0.0)), 2),
        dm_draft_typing_ms=round(float(snap.get("dm_draft_typing_ms", 0.0)), 2),
        dm_draft_verify_ms=round(float(snap.get("dm_draft_verify_ms", 0.0)), 2),
        dm_draft_clear_ms=round(float(snap.get("dm_draft_clear_ms", 0.0)), 2),
        finalize_before_back_ms=round(float(snap.get("finalize_before_back_ms", 0.0)), 2),
        keyboard_hide_ms=round(float(snap.get("keyboard_hide_ms", 0.0)), 2),
        back_press_ms=round(float(snap.get("back_press_ms", 0.0)), 2),
        profile_detect_wait_ms=round(float(snap.get("profile_detect_wait_ms", 0.0)), 2),
        total_dm_to_profile_phase_ms=round(float(snap.get("total_dm_to_profile_phase_ms", 0.0)), 2),
        profile_to_search_fast_path_total_ms=round(
            float(snap.get("profile_to_search_fast_path_total_ms", 0.0)), 2
        ),
        first_action_delay_ms=round(float(snap.get("first_action_delay_ms", 0.0)), 2),
        draft_to_back_profile_ms=round(float(snap.get("draft_to_back_profile_ms", 0.0)), 2),
        profile_to_search_ms=round(float(snap.get("profile_to_search_ms", 0.0)), 2),
        next_username_ready_ms=round(float(snap.get("next_username_ready_ms", 0.0)), 2),
        post_send_detect_ms=round(float(snap.get("post_send_detect_ms", 0.0)), 2),
        post_send_back_to_profile_ms=round(
            float(snap.get("post_send_back_to_profile_ms", 0.0)), 2
        ),
        post_send_profile_to_search_ms=round(
            float(snap.get("post_send_profile_to_search_ms", 0.0)), 2
        ),
        post_send_finalize_total_ms=round(
            float(snap.get("post_send_finalize_total_ms", 0.0)), 2
        ),
        post_send_cleanup_reason=snap.get("post_send_cleanup_reason"),
        search_click_ms=round(float(snap.get("search_click_ms", 0.0)), 2),
        search_field_ready_ms=round(float(snap.get("search_field_ready_ms", 0.0)), 2),
        search_surface_reused=bool(snap.get("search_surface_reused", False)),
        search_back_to_search_ms=round(float(snap.get("search_back_to_search_ms", 0.0)), 2),
        search_open_skipped_ms=round(float(snap.get("search_open_skipped_ms", 0.0)), 2),
        typing_confirm_ms=round(float(snap.get("typing_confirm_ms", 0.0)), 2),
        warm_session_used=warm_session_used,
        force_stop_used=force_stop_used,
        xml_fetches=int(snap.get("xml_fetches", 0)),
        recovery_used=bool(snap.get("recovery_used", False)),
        exit_code=exit_code,
        log_file_path=get_run_log_file_path(),
        **_perf_followers_extra,
    )


def _parse_targets(args: argparse.Namespace) -> list[str]:
    raw = (getattr(args, "multi", None) or "").strip()
    if raw:
        return [u.strip() for u in raw.split(",") if u.strip()]
    return [config.TARGET_USERNAME]


def _is_supabase_mode(args: argparse.Namespace) -> bool:
    return bool((getattr(args, "account_id", None) or "").strip() or (getattr(args, "username", None) or "").strip())


def _recoverable_target_exit(code: int) -> bool:
    codes = getattr(config, "FAST_RECOVERABLE_TARGET_EXIT_CODES", ()) or ()
    return int(code) in codes


def _exit_reason_from_code(code: int) -> str:
    mapping = {
        0: "ok",
        2: "health_check_failed",
        3: "instagram_not_foreground",
        4: "open_search_failed",
        5: "type_search_failed",
        7: "tap_account_failed",
        8: "profile_verify_failed",
        9: "search_field_not_cleared",
        73: "search_surface_wrong_app_launcher",
        11: "dm_thread_unknown",
        12: "dm_back_to_profile_failed",
        13: "profile_back_to_search_failed",
        14: "stuck_in_dm_thread",
        15: "dm_composer_not_safe",
        16: "dm_draft_typing_failed",
        17: "dm_draft_verify_failed",
        18: "dm_draft_clear_failed",
        19: "dm_send_precheck_failed",
        20: "dm_sent_failed",
        21: "sent_success_navigation_partial",
        31: "follow_success_full",
        32: "follow_success_navigation_partial",
        33: "follow_button_missing",
        34: "follow_verify_failed",
        35: "follow_blocked_duplicate",
        36: "follow_blocked_cooldown",
        40: "followers_list_engine_open_failed",
        43: "instagram_strict_update_lock_unsafe",
        42: "followers_list_engine_return_failed",
        44: "followers_engine_xml_stale_abort",
        45: "visual_followers_candidates_detected_dry_run",
        46: "visual_follower_profile_opened_dry_run",
        47: "visual_post_like_flow_dry_run_complete",
        48: "visual_follow_mute_flow_dry_run_complete",
        49: "visual_post_like_real_verified",
        50: "visual_post_like_real_unverified",
        51: "visual_post_already_liked",
        52: "visual_profile_no_posts_skip",
        37: "follow_blocked_social_memory",
        53: "session_strict_follow_quota_incomplete",
        54: "session_strict_interactions_quota_incomplete",
        55: "visual_follow_real_verified",
        56: "visual_follow_real_unverified",
        57: "visual_follow_already_following",
        58: "visual_profile_context_mismatch_abort",
        59: "visual_mute_real_verified",
        60: "visual_mute_real_unverified",
        61: "visual_mute_real_failed",
        62: "visual_private_profile_skipped",
        63: "visual_private_follow_requested",
        64: "visual_flow_canonical_reset_required",
        65: "visual_mute_tap_sent_unverified",
        66: "visual_followers_no_more_candidates_after_scrolls",
        67: "visual_followers_stalled_visible_follow_buttons_no_candidate",
        71: "follow_started_without_tap_watchdog",
        72: "failed_no_follow_tap",
        74: "follow_review_popup_unhandled",
        99: "follow_review_popup_unhandled_safe_stop",
    }
    return mapping.get(code, f"exit_code_{code}")


def _safe_supabase_call(fn_name: str, *args, **kwargs):
    fn = getattr(supabase_client, fn_name)
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        log("warning", "supabase_call_failed", fn=fn_name, error=str(e))
        return None


def _reset_session_counters() -> None:
    global _SESSION_COUNTERS
    _SESSION_COUNTERS = {
        "follows": 0,
        "unfollows": 0,
        "likes": 0,
        "pm": 0,
        "interactions": 0,
        "successful_interactions": 0,
    }


def _session_follow_quota_exceeded() -> bool:
    caps = [
        int(getattr(config, "SESSION_TOTAL_FOLLOWS_CAP", 0) or 0),
        int(getattr(config, "SESSION_FOLLOW_LIMIT", 0) or 0),
    ]
    pos = [c for c in caps if c > 0]
    if not pos:
        return False
    return _SESSION_COUNTERS["follows"] >= min(pos)


def _session_total_interactions_cap_exceeded() -> bool:
    lim = int(getattr(config, "SESSION_TOTAL_INTERACTIONS_LIMIT", 0) or 0)
    if lim <= 0:
        return False
    return _SESSION_COUNTERS["interactions"] >= lim


def _session_successful_interactions_cap_exceeded() -> bool:
    lim = int(getattr(config, "SESSION_TOTAL_SUCCESSFUL_INTERACTIONS_LIMIT", 0) or 0)
    if lim <= 0:
        return False
    return _SESSION_COUNTERS["successful_interactions"] >= lim


def _session_strict_completion_exit_code() -> int | None:
    if not getattr(config, "SESSION_STRICT_PHASE_COMPLETION", False):
        return None
    caps = [
        int(getattr(config, "SESSION_TOTAL_FOLLOWS_CAP", 0) or 0),
        int(getattr(config, "SESSION_FOLLOW_LIMIT", 0) or 0),
    ]
    pos = [c for c in caps if c > 0]
    if pos and _SESSION_COUNTERS["follows"] < min(pos):
        log(
            "warning",
            "session_strict_incomplete",
            reason="follow_quota_unmet",
            follows=_SESSION_COUNTERS["follows"],
            required=min(pos),
        )
        return 53
    til = int(getattr(config, "SESSION_TOTAL_INTERACTIONS_LIMIT", 0) or 0)
    if til > 0 and _SESSION_COUNTERS["interactions"] < til:
        log(
            "warning",
            "session_strict_incomplete",
            reason="interactions_quota_unmet",
            interactions=_SESSION_COUNTERS["interactions"],
            required=til,
        )
        return 54
    return None


def _social_memory_load_and_evaluate(
    *,
    target_username: str,
    source_profile: str,
    account_id: str,
    run_id: str,
    supabase_mode: bool,
) -> social_memory.FollowEligibility:
    db_row = None
    if supabase_mode and account_id and getattr(config, "SOCIAL_MEMORY_ENABLED", True):
        db_row = _safe_supabase_call(
            "load_interacted_user",
            account_id,
            target_username,
            source_profile or "",
        )
        log(
            "info",
            "social_memory_loaded",
            target_username=target_username,
            source_profile=source_profile or "",
            has_row=bool(db_row),
        )
    return social_memory.evaluate_follow_eligibility(
        target_username=target_username,
        source_profile=source_profile or "",
        db_row=db_row,
        runtime_followed=_RUNTIME_FOLLOWED_USERNAMES,
        runtime_unfollowed=_RUNTIME_UNFOLLOWED_USERNAMES,
        runtime_interacted=_RUNTIME_INTERACTED_USERNAMES,
        runtime_skipped=_RUNTIME_SKIPPED_USERNAMES,
        config=config,
    )


def _persist_social_memory_follow_block(
    *,
    elig: social_memory.FollowEligibility,
    account_id: str,
    run_id: str,
    supabase_mode: bool,
    target_username: str,
    source_profile: str,
) -> None:
    if not (supabase_mode and account_id and getattr(config, "SOCIAL_MEMORY_ENABLED", True)):
        return
    _safe_supabase_call(
        "record_interaction_skip_memory",
        account_id,
        target_username,
        source_profile or "",
        skip_reason=elig.reason,
        run_id=run_id or None,
        session_id=_SESSION_SOCIAL_ID or None,
        lifecycle_state=elig.interaction_state,
    )
    log(
        "info",
        "social_memory_updated",
        target_username=target_username,
        source_profile=source_profile or "",
        kind="skip_block",
        reason=elig.reason,
    )


def _coerce_optional_run_id(run_id: Any) -> str | None:
    if run_id is None:
        return None
    s = str(run_id).strip()
    return s or None


def _followers_visual_load_interacted_db_row(
    *,
    account_id: str,
    target_username: str,
    source_profile: str,
    supabase_mode: bool,
) -> dict[str, Any] | None:
    if not (
        supabase_mode
        and str(account_id or "").strip()
        and getattr(config, "SOCIAL_MEMORY_ENABLED", True)
    ):
        return None
    out = _safe_supabase_call(
        "load_interacted_user",
        str(account_id).strip(),
        target_username,
        source_profile or "",
    )
    return out if isinstance(out, dict) else None


def _visual_candidate_already_connected_soft_followers_after_back(
    det: dict[str, Any],
    *,
    source_profile_username: str,
) -> bool:
    """
    Narrow post-skip accept when strict ``is_followers_list`` stays false briefly after Back
    (stale hierarchy / sparse first paint) while the surface already looks non-profile list.
    """
    if bool(det.get("is_followers_list")):
        return True
    chrome = bool(
        det.get("recycler_present")
        or det.get("scrollable_present")
        or det.get("listview_present")
    )
    tabs_gone = bool(det.get("profile_tabs_absent"))
    guess = str(det.get("current_screen_guess") or "").strip().lower()
    stacked = int(det.get("stacked_central_textview_run") or 0)
    cnt = int(det.get("candidate_username_count") or 0)
    sample = det.get("visible_usernames_sample")
    samp_n = len(sample) if isinstance(sample, list) else 0
    title_match = bool(det.get("title_match"))
    ab = _norm_ig_handle(str(det.get("action_bar_title") or ""))
    src = _norm_ig_handle(source_profile_username or "")
    hdrs = det.get("visible_header_texts")
    hdr_followers_kw = False
    if isinstance(hdrs, list):
        hl = " ".join(str(h).strip().lower() for h in hdrs[:12] if h)
        if any(k in hl for k in ("follower", "abonnés", "abonne", "followers")):
            hdr_followers_kw = True
    if chrome and tabs_gone and title_match:
        return True
    if (
        chrome
        and tabs_gone
        and hdr_followers_kw
        and (cnt >= 1 or samp_n >= 1 or stacked >= 4)
        and guess not in ("likely_profile",)
    ):
        return True
    if (
        chrome
        and tabs_gone
        and src
        and ab == src
        and (cnt >= 1 or samp_n >= 1 or stacked >= 4 or title_match)
    ):
        return True
    return False


def _already_connected_xml_still_follower_profile_band(
    det: dict[str, Any],
    *,
    source_profile_username: str,
    follower_username_hint: str,
) -> bool:
    """
    Rough guard: follower profile chrome often retains the follower handle as the toolbar title.

    Avoid accepting a follower-list screenshot heuristic while XML still anchors on the follower
    profile chrome (distinct from ``source_profile_username``).
    """
    src = _norm_ig_handle(source_profile_username or "")
    fld = _norm_ig_handle(str(follower_username_hint or "").strip())
    if not fld:
        return False
    ab = _norm_ig_handle(str(det.get("action_bar_title") or ""))
    return bool(ab == fld and (not src or fld != src))


def _already_connected_visual_followers_list_accept_relaxed(
    vf: dict[str, Any],
    *,
    xml_follower_profile_band_hint: bool,
) -> tuple[bool, str]:
    """Narrow gated relax for stale-XML follower list paints (post single Back path only).

    Used exclusively from ``_visual_candidate_already_connected_try_direct_back_to_followers_list``.
    """
    if not isinstance(vf, dict):
        return False, "visual_payload_invalid"
    err = str(vf.get("visual_error") or "").strip()
    if err:
        return False, f"visual_error:{err}"
    tabs_abs = bool(vf.get("visual_profile_tabs_absent"))

    fcb = int(vf.get("visual_follow_button_count") or 0)
    rows = int(vf.get("visual_user_rows_detected") or 0)
    search_ok = bool(vf.get("visual_search_area_detected"))
    title_h = bool(vf.get("visual_followers_title_hint"))
    conf = float(vf.get("visual_confidence") or 0.0)

    min_conf_soft = float(
        getattr(config, "ALREADY_CONNECTED_BACK_VISUAL_MIN_CONFIDENCE", 0.28)
        or 0.28
    )
    if conf < min_conf_soft:
        return False, f"visual_conf_below_soft_threshold:{conf:.3f}"

    if not tabs_abs:
        min_rows_tabs_present = max(
            1,
            int(
                getattr(
                    config,
                    "ALREADY_CONNECTED_BACK_VISUAL_TABS_PRESENT_MIN_ROWS",
                    15,
                )
                or 15
            ),
        )
        # Bottom-tab heuristic noisy on real follower lists; gated by strong sheet signals +
        # no XML follower-profile toolbar hint (caller).
        if (
            not xml_follower_profile_band_hint
            and search_ok
            and title_h
            and rows >= min_rows_tabs_present
            and fcb >= 1
        ):
            return True, (
                "visual_tabs_present_rows_title_search_single_follow_cta_accepted"
            )

        if fcb >= 3 and search_ok:
            return True, "dense_follow_strip_search_tab_heuristic_noisy"
        if fcb >= 2 and search_ok and rows >= 1:
            return True, "multi_follow_cta_rows_search_tab_heuristic_noisy"
        return False, "visual_tabs_present_relaxed_blocked"

    if fcb >= 1 and search_ok:
        return True, "sparse_list_follow_cta_with_search_strip"

    if fcb >= 2 and (search_ok or rows >= 1):
        return True, "multi_follow_cta_with_row_or_search"

    if rows >= 2 and (search_ok or fcb >= 1):
        return True, "multi_user_rows_with_search_or_cta"

    if title_h and fcb >= 1 and (search_ok or rows >= 1):
        return True, "title_hint_follow_cta_and_rows_or_search"

    return False, "visual_signals_insufficient"


def _visual_candidate_already_connected_try_direct_back_to_followers_list(
    d: Any,
    *,
    source_profile_username: str,
    follower_username_hint: str = "",
) -> tuple[bool, str, int, dict[str, Any] | None]:
    """
    Prefer one or more plain Back hops from an opened follower profile back onto the CT
    followers list, without invoking ``open_followers_list_from_profile`` (entry_v2 reopen).
    Caps physical Back presses (default config → 3) to **two** — extra probes use wait/refresh only,
    because premature 2ᵉ/3ᵉ Back often leaves CT followers for an intermediate surface.
    """
    retries_raw = getattr(config, "FOLLOWERS_LIST_RETURN_MAX_RETRIES", 2)
    retries_cfg = max(0, int(retries_raw or 0))
    max_physical_backs = max(1, min(retries_cfg + 1, 2))
    last_attempt_idx = -1
    try:
        log(
            "info",
            "visual_candidate_already_connected_direct_back_started",
            source_profile_username=source_profile_username,
            max_physical_back_attempts=max_physical_backs,
            retries_config_followers_return=retries_cfg,
            note_capped=max_physical_backs < (retries_cfg + 1),
        )
    except Exception:
        pass

    probe_labels = ("primary_sleep", "hierarchy_refresh_reprobe")

    def _emit_direct_back_attempt_from_det(
        det: dict[str, Any],
        *,
        followers_ok: bool,
        sub_label: str,
        vc_used: bool,
        vc_confirmed: bool,
    ) -> None:
        try:
            log(
                "info",
                "visual_candidate_already_connected_direct_back_attempt_result",
                source_profile_username=source_profile_username,
                back_attempt_index=attempt,
                sub_probe=sub_label,
                followers_list_detected=followers_ok,
                detector_is_followers_list=bool(
                    isinstance(det, dict) and bool(det.get("is_followers_list"))
                ),
                detector_soft_followers_accept=bool(
                    isinstance(det, dict)
                    and not bool(det.get("is_followers_list"))
                    and _visual_candidate_already_connected_soft_followers_after_back(
                        det,
                        source_profile_username=source_profile_username,
                    )
                ),
                after_back_action_bar_title=str((det.get("action_bar_title") if isinstance(det, dict) else "") or "")[
                    :240
                ],
                after_back_screen_guess=str(
                    (det.get("current_screen_guess") if isinstance(det, dict) else "") or ""
                ),
                strict_list_open=bool(isinstance(det, dict) and det.get("strict_list_open")),
                relaxed_list_open=bool(isinstance(det, dict) and det.get("relaxed_list_open")),
                title_match=bool(isinstance(det, dict) and det.get("title_match")),
                recycler_present=bool(isinstance(det, dict) and det.get("recycler_present")),
                scrollable_present=bool(isinstance(det, dict) and det.get("scrollable_present")),
                candidate_username_count=int(
                    (det.get("candidate_username_count") or 0)
                    if isinstance(det, dict)
                    else 0
                ),
                stacked_central_textview_run=int(
                    (det.get("stacked_central_textview_run") or 0)
                    if isinstance(det, dict)
                    else 0
                ),
                profile_tabs_absent=bool(isinstance(det, dict) and bool(det.get("profile_tabs_absent"))),
                visual_check_used=vc_used,
                visual_check_confirmed=vc_confirmed,
            )
        except Exception:
            pass

    for attempt in range(max_physical_backs):
        last_attempt_idx = attempt
        try:
            d.press("back")
        except Exception:
            pass

        last_xml_det: dict[str, Any] = {}
        for sub in range(2):
            if sub == 0:
                time.sleep(0.78 if attempt == 0 else 0.48)
            else:
                try:
                    followers_refresh_hierarchy_for_candidates(d)
                except Exception:
                    pass
                time.sleep(0.38)

            det = {}
            try:
                det = detect_followers_list_screen(
                    d, source_profile_username=source_profile_username
                )
            except Exception:
                det = {}

            last_xml_det = dict(det) if isinstance(det, dict) else {}

            followers_ok = isinstance(det, dict) and (
                bool(det.get("is_followers_list"))
                or _visual_candidate_already_connected_soft_followers_after_back(
                    det,
                    source_profile_username=source_profile_username,
                )
            )
            _emit_direct_back_attempt_from_det(
                det,
                followers_ok=followers_ok,
                sub_label=probe_labels[sub],
                vc_used=False,
                vc_confirmed=False,
            )

            if followers_ok:
                try:
                    log(
                        "info",
                        "visual_candidate_already_connected_direct_back_list_confirmed",
                        source_profile_username=source_profile_username,
                        physical_back_attempt=attempt,
                        sub_probe=probe_labels[sub],
                        used_soft_accept=bool(
                            isinstance(det, dict)
                            and (not bool(det.get("is_followers_list")))
                            and _visual_candidate_already_connected_soft_followers_after_back(
                                det,
                                source_profile_username=source_profile_username,
                            )
                        ),
                    )
                except Exception:
                    pass
                return True, "direct_back_to_followers_list", attempt, None

        if attempt == 0:
            try:
                log(
                    "info",
                    "visual_candidate_already_connected_direct_back_visual_check_started",
                    source_profile_username=source_profile_username,
                    follower_username_hint=str(follower_username_hint or "")[:128],
                    back_attempt_index=attempt,
                    screenshot_path="",
                )
            except Exception:
                pass

            time.sleep(0.22)
            vf: dict[str, Any] = {}
            try:
                vf = detect_followers_list_screen_visual_fallback(
                    d,
                    source_profile_username=source_profile_username,
                    screenshot_path=None,
                )
            except Exception as e:
                vf = {
                    "visual_match": False,
                    "visual_confidence": 0.0,
                    "visual_follow_button_count": 0,
                    "visual_search_area_detected": False,
                    "visual_user_rows_detected": 0,
                    "visual_profile_tabs_absent": False,
                    "visual_followers_title_hint": False,
                    "visual_error": f"exception:{type(e).__name__}",
                    "screenshot_path_used": "",
                }

            vpath = str(vf.get("screenshot_path_used") or "")
            last_xml = dict(last_xml_det) if isinstance(last_xml_det, dict) else {}

            xml_band = _already_connected_xml_still_follower_profile_band(
                last_xml,
                source_profile_username=source_profile_username,
                follower_username_hint=follower_username_hint,
            )
            strict_match = bool(vf.get("visual_match"))
            relaxed_ok, rel_reason = _already_connected_visual_followers_list_accept_relaxed(
                vf,
                xml_follower_profile_band_hint=xml_band,
            )
            vf_err = str(vf.get("visual_error") or "").strip()

            if xml_band:
                ok_vis = False
                acc_reason = "xml_action_bar_still_follower_profile_band"
            elif vf_err:
                ok_vis = False
                acc_reason = f"visual_fallback_failed:{vf_err}"
            elif strict_match:
                ok_vis = True
                acc_reason = "engine_visual_match"
            elif relaxed_ok:
                ok_vis = True
                acc_reason = rel_reason
            else:
                ok_vis = False
                acc_reason = rel_reason

            vconf = float(vf.get("visual_confidence") or 0.0)
            v_uc = int(vf.get("visual_user_rows_detected") or 0)
            v_fc = int(vf.get("visual_follow_button_count") or 0)

            evt = (
                "visual_candidate_already_connected_direct_back_visual_list_confirmed"
                if ok_vis
                else "visual_candidate_already_connected_direct_back_visual_list_rejected"
            )
            try:
                log(
                    "info",
                    evt,
                    source_profile_username=source_profile_username,
                    follower_username_hint=str(follower_username_hint or "")[:128],
                    back_attempt_index=attempt,
                    screenshot_path=vpath[:1024],
                    visual_user_rows_detected=v_uc,
                    visual_follow_button_count=v_fc,
                    visual_search_area_detected=bool(vf.get("visual_search_area_detected")),
                    visual_followers_title_hint=bool(vf.get("visual_followers_title_hint")),
                    visual_profile_tabs_absent=bool(vf.get("visual_profile_tabs_absent")),
                    visual_match=strict_match,
                    visual_relaxed_candidate=relaxed_ok,
                    visual_confidence=vconf,
                    acceptance_reason=acc_reason,
                    xml_follower_profile_band_hint=xml_band,
                )
            except Exception:
                pass

            _emit_direct_back_attempt_from_det(
                last_xml,
                followers_ok=ok_vis,
                sub_label="visual_screenshot_fallback",
                vc_used=True,
                vc_confirmed=ok_vis,
            )

            if ok_vis:
                try:
                    log(
                        "info",
                        "visual_candidate_already_connected_direct_back_list_confirmed",
                        source_profile_username=source_profile_username,
                        physical_back_attempt=attempt,
                        sub_probe="visual_screenshot_fallback",
                        used_soft_accept=False,
                    )
                except Exception:
                    pass
                _vf_out = dict(vf)
                _vf_out["open_detection_method"] = str(
                    _vf_out.get("open_detection_method") or "visual_fallback"
                )
                return (
                    True,
                    "direct_back_visual_followers_list_confirmed",
                    attempt,
                    _vf_out,
                )

    try:
        log(
            "info",
            "visual_candidate_already_connected_direct_back_not_confirmed",
            source_profile_username=source_profile_username,
            physical_backs_used=max_physical_backs,
            last_physical_back_attempt=last_attempt_idx,
        )
    except Exception:
        pass
    return False, "direct_back_not_confirmed", last_attempt_idx, None


def _followers_visual_emit_already_connected_skip_event(
    *,
    account_id: str,
    run_id: Any,
    supabase_mode: bool,
    follower_username: str,
    source_profile_username: str,
    skip_reason: str,
    visual_candidate_id: str | None,
    memory_detail: dict[str, Any] | None,
) -> None:
    if not (supabase_mode and str(account_id or "").strip() and follower_username):
        return
    _safe_supabase_call(
        "record_interaction_event",
        str(account_id).strip(),
        follower_username,
        source_profile_username or "",
        run_id=_coerce_optional_run_id(run_id),
        session_id=_SESSION_SOCIAL_ID or None,
        event_type="followers_visual_already_connected_skip",
        event_status="info",
        event_reason=skip_reason[:500],
        payload={
            "visual_candidate_id": str(visual_candidate_id or ""),
            "memory_detail": dict(memory_detail) if memory_detail else {},
        },
    )


def _visual_candidate_already_connected_fast_return(
    d: Any,
    *,
    source_profile_username: str,
    pkg: str,
    follower_username: str,
    visual_candidate_id: str | None,
    skip_reason: str,
    memory_detail: dict[str, Any] | None,
    follow_header_state: str | None,
    account_id: str,
    run_id: Any,
    supabase_mode: bool,
    visual_loop_state: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    log(
        "info",
        "visual_candidate_already_connected_fast_return_started",
        source_profile_username=source_profile_username,
        follower_username=follower_username,
        visual_candidate_id=str(visual_candidate_id or ""),
        skip_reason=skip_reason,
        memory_status=str((memory_detail or {}).get("memory_status") or ""),
        following_status=str((memory_detail or {}).get("following_status") or ""),
        follow_header_state=str(follow_header_state or ""),
    )

    dir_vf_snap: dict[str, Any] | None = None
    dir_ok, dir_how, _dir_attempt, dir_vf_snap = (
        _visual_candidate_already_connected_try_direct_back_to_followers_list(
            d,
            source_profile_username=source_profile_username,
            follower_username_hint=str(follower_username or ""),
        )
    )
    vf_snap_eff: dict[str, Any] | None = dir_vf_snap
    if dir_ok:
        ok, how = True, dir_how
    else:
        try:
            log(
                "info",
                "visual_candidate_already_connected_fast_return_fallback_started",
                source_profile_username=source_profile_username,
                direct_back_outcome=str(dir_how or ""),
                fallback="return_to_followers_list_full",
            )
        except Exception:
            pass
        vf_snap_eff = None
        ok, how = return_to_followers_list(d, source_profile_username, pkg)
    if ok:
        _how_s = str(how or "").strip()
        if _how_s == "direct_back_visual_followers_list_confirmed":
            _committed_src = "already_connected_direct_back_visual_confirmed"
        elif _how_s.startswith("direct_back"):
            _committed_src = "already_connected_direct_back_other"
        else:
            _committed_src = "already_connected_fallback_return_success"
        try:
            followers_session_mark_list_committed_open(
                source_profile_username,
                committed_source=_committed_src[:120],
            )
        except Exception:
            pass
        if (
            vf_snap_eff
            and isinstance(visual_loop_state, dict)
            and _how_s == "direct_back_visual_followers_list_confirmed"
        ):
            try:
                visual_loop_state["session_vf_detail"] = dict(vf_snap_eff)
            except Exception:
                pass
        if isinstance(visual_loop_state, dict):
            try:
                visual_loop_state["post_return_picker_refresh_pending"] = True
                visual_loop_state["post_return_picker_refresh_meta"] = {
                    "visual_candidate_id": str(visual_candidate_id or ""),
                    "follower_username": str(follower_username or ""),
                    "return_method": str(how or ""),
                    "phase": "already_connected_fast_return",
                }
                log(
                    "info",
                    "followers_post_return_picker_refresh_armed",
                    source_profile_username=source_profile_username,
                    visual_candidate_id=str(visual_candidate_id or ""),
                    follower_username=str(follower_username or ""),
                    return_method=str(how or ""),
                    phase="already_connected_fast_return",
                )
            except Exception:
                pass
        try:
            log(
                "info",
                "followers_session_rearmed_after_already_connected_fast_return",
                source_profile_username=source_profile_username,
                follower_username=str(follower_username or ""),
                return_method=str(how or ""),
                committed_source=_committed_src[:120],
                session_vf_promoted=bool(vf_snap_eff)
                and _how_s == "direct_back_visual_followers_list_confirmed",
            )
        except Exception:
            pass

        log(
            "info",
            "visual_candidate_already_connected_fast_return_success",
            source_profile_username=source_profile_username,
            follower_username=follower_username,
            visual_candidate_id=str(visual_candidate_id or ""),
            return_how=str(how or ""),
            skip_reason=skip_reason,
        )
    else:
        log(
            "warning",
            "visual_candidate_already_connected_fast_return_failed",
            source_profile_username=source_profile_username,
            follower_username=follower_username,
            visual_candidate_id=str(visual_candidate_id or ""),
            return_how=str(how or ""),
            skip_reason=skip_reason,
        )
    if ok:
        _followers_visual_emit_already_connected_skip_event(
            account_id=account_id,
            run_id=run_id,
            supabase_mode=supabase_mode,
            follower_username=follower_username,
            source_profile_username=source_profile_username,
            skip_reason=skip_reason,
            visual_candidate_id=visual_candidate_id,
            memory_detail=memory_detail,
        )
    return ok, str(how or "")


def _ct_list_raw_follow_invite_visible(d: Any) -> bool:
    """UiAutomator probe when _follow_ui_state_snapshot misses (e.g. sparse headers)."""
    try:
        if d(text="Follow").exists(timeout=0.07):
            return True
        if d(text="Suivre").exists(timeout=0.05):
            return True
        if d(textContains="Follow").exists(timeout=0.05):
            return True
        if d(description="Follow").exists(timeout=0.04):
            return True
    except Exception:
        return False
    return False


def _ct_list_bypass_runtime_duplicate_social_memory(
    d: Any,
    *,
    elig: social_memory.FollowEligibility,
    fkey: str,
    followers_resolved_continue: bool,
    resolve_streak: int,
    follow_header_snap: str,
    follow_invite_visible: bool,
    enable_real_follow: bool,
    enable_visual_follow: bool,
) -> bool:
    """
    Allow one real perform_follow_safe on CT list–opened profiles when the only block is
    runtime_interacted from an earlier navigation that never sent a follow tap.
    """
    if elig.allowed or not getattr(config, "SOCIAL_MEMORY_ENABLED", True):
        return False
    if elig.reason != "runtime_already_interacted":
        return False
    if fkey in _RUNTIME_CT_LIST_FOLLOW_TAP_ATTEMPTED_USERNAMES:
        return False
    if not (enable_real_follow or enable_visual_follow):
        return False
    _raw = _ct_list_raw_follow_invite_visible(d)
    if not (follow_invite_visible or follow_header_snap == "follow" or _raw):
        return False
    return bool(followers_resolved_continue or resolve_streak >= 2)


def _flush_follow_action_logs_to_supabase(
    *,
    events: list[tuple[str, dict]],
    run_id: str,
    account_id: str,
    target_username: str,
    supabase_mode: bool,
) -> None:
    if not (supabase_mode and run_id and account_id):
        return
    for ev, pl in events or []:
        base = {"target_username": target_username, "account_id": account_id, "run_id": run_id}
        merged = {**base, **pl}
        _safe_supabase_call(
            "insert_action_log",
            run_id=run_id,
            account_id=account_id,
            target_username=target_username,
            action_type=ev,
            status="info",
            message=ev,
            payload=merged,
        )


def _cleanup_session_apps(d) -> None:
    if d is None or not bool(getattr(config, "CLOSE_APPS_AFTER_RUN", True)):
        return
    pkg = config.INSTAGRAM_PACKAGE
    force_stop_ok = False
    home_pressed = False
    try:
        force_stop(d, pkg)
        force_stop_ok = True
    except Exception as e:
        log("warning", "session_cleanup_force_stop_failed", error=str(e), package=pkg)
    if bool(getattr(config, "HOME_AFTER_RUN", True)):
        try:
            d.press("home")
            home_pressed = True
        except Exception as e:
            log("warning", "session_cleanup_home_failed", error=str(e))
    log(
        "info",
        "session_cleanup_apps_closed",
        package=pkg,
        force_stop_ok=force_stop_ok,
        home_pressed=home_pressed,
    )


def _return_with_cleanup(d, code: int) -> int:
    set_wait_event_callback(None)
    _cleanup_session_apps(d)
    return code


def _open_search_with_recovery(d, *, pkg: str, username: str, context: str) -> bool:
    if open_search(d):
        return True
    unsupported_reason = detect_unsupported_start_surface(d) or "open_search_no_edittext"
    log(
        "warning",
        "unsupported_surface_recovery",
        reason=unsupported_reason,
        username=username,
        context=context,
    )
    if unsupported_reason == "android_permission_dialog":
        denied = dismiss_android_permission_dialog(d)
        log(
            "info",
            "permission_recovery_restart",
            username=username,
            context=context,
            denied=denied,
        )
    invalidate_search_surface_cache(unsupported_reason)
    force_stop(d, pkg)
    app_start(d, pkg)
    ws = time.perf_counter()
    log("info", "wait_started", wait_reason="recovery_app_start_wait")
    time.sleep(config.APP_START_WAIT_S)
    log(
        "info",
        "wait_finished",
        wait_reason="recovery_app_start_wait",
        wait_duration_ms=round((time.perf_counter() - ws) * 1000, 2),
    )
    fg_ok = verify_app_foreground(d, pkg)
    log(
        "info",
        "recovery_force_restart_done",
        reason=unsupported_reason,
        username=username,
        context=context,
        foreground_ok=fg_ok,
    )
    if not fg_ok:
        if unsupported_reason == "android_permission_dialog":
            log(
                "error",
                "permission_recovery_failed",
                username=username,
                context=context,
                reason="instagram_not_foreground_after_restart",
            )
        return False
    ok_retry = open_search(d)
    if not ok_retry and unsupported_reason == "android_permission_dialog":
        log(
            "error",
            "permission_recovery_failed",
            username=username,
            context=context,
            reason="open_search_failed_after_permission_recovery",
        )
    return ok_retry


def _update_run_status_safe(
    *,
    run_id: str,
    status: str,
    totals: dict,
    performance_summary: dict,
) -> None:
    _safe_supabase_call(
        "update_run_status",
        run_id=run_id,
        status=status,
        totals=totals,
        performance_summary=performance_summary,
    )
    log(
        "info",
        "run_status_updated",
        run_id=run_id,
        status=status,
        totals=totals,
    )


def _update_target_status_safe(
    *,
    target_id: str,
    status: str,
    last_error: str | None,
) -> None:
    _safe_supabase_call(
        "update_target_status",
        target_id=target_id,
        status=status,
        last_error=last_error,
        attempted=True,
    )
    if status == "failed":
        _safe_supabase_call("increment_target_retry_count", target_id=target_id)
    log(
        "info",
        "target_status_updated",
        target_id=target_id,
        status=status,
        last_error=last_error,
    )


def _insert_dm_send_safe_log(
    *,
    run_id: str,
    account_id: str,
    username: str,
    action_type: str,
    status: str,
    message: str,
    payload: dict,
) -> None:
    try:
        supabase_client.insert_action_log(
            run_id=run_id,
            account_id=account_id,
            target_username=username,
            action_type=action_type,
            status=status,
            message=message,
            payload=payload,
        )
    except Exception as e:
        log("warning", "dm_send_supabase_log_failed", action_type=action_type, error=str(e))


def _emit_dm_send_supabase_logs(
    *,
    run_id: str,
    account_id: str,
    username: str,
    send_out: dict,
) -> None:
    snap = get_last_dm_thread_classify_snapshot()
    base_payload = {
        "target_username": send_out.get("target_username") or username,
        "thread_state": send_out.get("thread_state"),
        "enable_real_send": send_out.get("enable_real_send"),
        "reason": send_out.get("reason"),
        "message_len": send_out.get("message_len"),
        "blocked_event": send_out.get("blocked_event"),
        "failure_event": send_out.get("failure_event"),
        "sent": send_out.get("sent"),
        "dm_thread_classify": snap,
    }
    for k in (
        "composer_text_len_before_send",
        "draft_matches_before_send",
        "send_button_candidate_count",
        "send_button_debug_candidates_count",
        "coordinate_fallback_used",
        "debug_screenshot_path",
        "debug_xml_path",
        "debug_screenshot_error",
        "debug_xml_error",
    ):
        if k in send_out:
            base_payload[k] = send_out[k]
    ui_dump = send_out.get("dm_send_bottom_ui_dump")
    if isinstance(ui_dump, dict):
        base_payload["bottom_ui_dump_node_count"] = send_out.get(
            "bottom_ui_dump_node_count", ui_dump.get("bottom_ui_dump_node_count")
        )
        base_payload["bottom_ui_dump_bottom_fraction"] = send_out.get(
            "bottom_ui_dump_bottom_fraction", ui_dump.get("bottom_ui_dump_bottom_fraction")
        )
        base_payload["bottom_ui_dump_max_nodes"] = send_out.get(
            "bottom_ui_dump_max_nodes", ui_dump.get("bottom_ui_dump_max_nodes")
        )
        for k in ("bottom_ui_dump_nodes_total_before_cap", "bottom_ui_dump_nodes_truncated"):
            if k in send_out:
                base_payload[k] = send_out[k]
            elif k in ui_dump:
                base_payload[k] = ui_dump[k]
        if "dm_send_bottom_ui_dump" in send_out:
            base_payload["dm_send_bottom_ui_dump"] = send_out["dm_send_bottom_ui_dump"]
    if send_out.get("precheck_ok"):
        _insert_dm_send_safe_log(
            run_id=run_id,
            account_id=account_id,
            username=username,
            action_type="send_dm_precheck",
            status="success",
            message="send_dm_precheck ok",
            payload=base_payload,
        )
    if send_out.get("blocked_event"):
        _insert_dm_send_safe_log(
            run_id=run_id,
            account_id=account_id,
            username=username,
            action_type="send_dm_blocked",
            status="blocked",
            message=str(send_out.get("blocked_event") or "blocked"),
            payload=base_payload,
        )
    elif send_out.get("sent"):
        _insert_dm_send_safe_log(
            run_id=run_id,
            account_id=account_id,
            username=username,
            action_type="send_dm_sent",
            status="success",
            message="dm sent",
            payload=base_payload,
        )
    elif send_out.get("failure_event") == "dm_sent_failed":
        _insert_dm_send_safe_log(
            run_id=run_id,
            account_id=account_id,
            username=username,
            action_type="send_dm_blocked",
            status="failed",
            message="send_failed",
            payload=base_payload,
        )
    elif not send_out.get("precheck_ok"):
        _insert_dm_send_safe_log(
            run_id=run_id,
            account_id=account_id,
            username=username,
            action_type="send_dm_blocked",
            status="failed",
            message="precheck_failed",
            payload=base_payload,
        )

    if isinstance(ui_dump, dict) and "nodes" in ui_dump:
        dump_payload = dict(ui_dump)
        dump_payload.update(
            {
                "reason": send_out.get("reason") or "send_button_missing",
                "debug_screenshot_path": send_out.get("debug_screenshot_path"),
                "debug_xml_path": send_out.get("debug_xml_path"),
            }
        )
        _insert_dm_send_safe_log(
            run_id=run_id,
            account_id=account_id,
            username=username,
            action_type="dm_send_bottom_ui_dump",
            status="debug",
            message="bottom ui dump for send gate",
            payload=dump_payload,
        )


def _dm_send_exit_code(send_out: dict) -> int | None:
    if send_out.get("sent"):
        return None
    if send_out.get("failure_event") == "dm_sent_failed":
        return 20
    if send_out.get("precheck_ok") and send_out.get("blocked_event"):
        return None
    if send_out.get("blocked_event"):
        return None
    if not send_out.get("precheck_ok"):
        return 19
    return None


def _insert_dm_log_safe(
    *,
    run_id: str,
    account_id: str,
    username: str,
    dm_state: str,
    exit_code: int,
    warm_session_used: bool,
    target_perf: dict,
) -> None:
    if exit_code == 21:
        returned_to_profile = bool(int(target_perf.get("post_finalize_back_profile_ok", 0)))
        returned_to_search = bool(int(target_perf.get("post_finalize_back_to_search_ok", 0)))
        navigation_flow_status = "post_send_partial"
    else:
        returned_to_profile = exit_code in (0, 13)
        returned_to_search = exit_code == 0
        if exit_code == 12:
            navigation_flow_status = "dm_back_failed"
        elif exit_code == 13:
            navigation_flow_status = "search_back_failed"
        else:
            navigation_flow_status = "complete"

    dm_status = "success"
    if exit_code in (12, 13):
        dm_status = "failed"
    if exit_code == 21:
        dm_status = "success"
    if dm_state == "dm_not_available":
        dm_status = "unavailable"
    elif dm_state in ("restricted_account", "unknown"):
        dm_status = "blocked" if dm_state == "restricted_account" else "failed"
    payload = {
        "target_username": username,
        "thread_state": dm_state,
        "profile_verified": True,
        "dm_opened": True,
        "dm_thread_state_detected": dm_state != "unknown",
        "returned_to_profile": returned_to_profile,
        "returned_to_search": returned_to_search,
        "dm_back_to_profile_ms": round(float(target_perf.get("dm_back_to_profile_ms", 0.0)), 2),
        "profile_back_to_search_ms": round(float(target_perf.get("search_back_to_search_ms", 0.0)), 2),
        "navigation_flow_status": navigation_flow_status,
        "navigation_exit_code": None if navigation_flow_status == "complete" else exit_code,
        "dm_open_click_ms": round(float(target_perf.get("dm_open_click_ms", 0.0)), 2),
        "dm_thread_detect_ms": round(float(target_perf.get("dm_thread_detect_ms", 0.0)), 2),
        "warm_session_used": warm_session_used,
        "search_surface_reused": bool(target_perf.get("search_surface_reused", False)),
        "dm_thread_classify": get_last_dm_thread_classify_snapshot(),
        "post_send_detect_ms": round(float(target_perf.get("post_send_detect_ms", 0.0)), 2),
        "post_send_finalize_total_ms": round(
            float(target_perf.get("post_send_finalize_total_ms", 0.0)), 2
        ),
        "post_send_cleanup_reason": target_perf.get("post_send_cleanup_reason"),
        "business_navigation_status": ("partial" if exit_code == 21 else "full"),
    }
    try:
        supabase_client.insert_action_log(
            run_id=run_id,
            account_id=account_id,
            target_username=username,
            action_type="open_dm",
            status=dm_status,
            message="DM thread state classified (safe mode, no send)",
            payload=payload,
        )
        log(
            "info",
            "dm_log_inserted",
            run_id=run_id,
            target_username=username,
            status=dm_status,
            thread_state=dm_state,
        )
    except Exception as e:
        log(
            "warning",
            "dm_log_insert_failed",
            run_id=run_id,
            target_username=username,
            status=dm_status,
            thread_state=dm_state,
            error=str(e),
        )


def _build_target_perf_compact_payload(
    *,
    username: str,
    target_perf: dict,
    exit_code: int,
    total_ms: float,
    navigation_strategy: str,
) -> dict:
    open_search_ms = float(target_perf.get("search_click_ms", 0.0)) + float(
        target_perf.get("search_field_ready_ms", 0.0)
    )
    ts_total = float(target_perf.get("type_search_total_ms", 0.0))
    type_search_ms = (
        ts_total
        if ts_total > 0.0
        else float(target_perf.get("typing_command_ms", 0.0))
        + float(target_perf.get("typing_confirm_ms", 0.0))
    )
    tap_account_ms = (
        float(target_perf.get("row_detect_ms", 0.0))
        + float(target_perf.get("row_tap_command_ms", 0.0))
        + float(target_perf.get("post_tap_settle_ms", 0.0))
    )
    open_dm_thread_ms = float(target_perf.get("dm_open_click_ms", 0.0)) + float(
        target_perf.get("dm_thread_detect_ms", 0.0)
    )
    dm_draft_total_ms = (
        float(target_perf.get("dm_draft_typing_ms", 0.0))
        + float(target_perf.get("dm_draft_verify_ms", 0.0))
        + float(target_perf.get("dm_draft_clear_ms", 0.0))
    )
    return {
        "username": username,
        "total_ms": round(float(total_ms), 2),
        "first_action_delay_ms": round(float(target_perf.get("first_action_delay_ms", 0.0)), 2),
        "open_search_ms": round(open_search_ms, 2),
        "type_search_ms": round(type_search_ms, 2),
        "search_edittext_wait_ms": round(float(target_perf.get("search_edittext_wait_ms", 0.0)), 2),
        "search_placeholder_check_ms": round(
            float(target_perf.get("search_placeholder_check_ms", 0.0)), 2
        ),
        "search_clear_decision_ms": round(float(target_perf.get("search_clear_decision_ms", 0.0)), 2),
        "search_clear_ms": round(float(target_perf.get("search_clear_ms", 0.0)), 2),
        "search_refetch_ms": round(float(target_perf.get("search_refetch_ms", 0.0)), 2),
        "search_pre_focus_validation_ms": round(
            float(target_perf.get("search_pre_focus_validation_ms", 0.0)), 2
        ),
        "type_search_pre_focus_accounted_ms": round(
            float(target_perf.get("search_edittext_wait_ms", 0.0))
            + float(target_perf.get("search_placeholder_check_ms", 0.0))
            + float(target_perf.get("search_clear_ms", 0.0))
            + float(target_perf.get("search_refetch_ms", 0.0))
            + float(target_perf.get("search_pre_focus_validation_ms", 0.0)),
            2,
        ),
        "search_focus_ms": round(float(target_perf.get("search_focus_ms", 0.0)), 2),
        "search_focus_attempts": round(float(target_perf.get("search_focus_attempts", 0.0)), 2),
        "search_focus_retry_count": round(float(target_perf.get("search_focus_retry_count", 0.0)), 2),
        "search_focus_strategy_used": round(float(target_perf.get("search_focus_strategy_used", 0.0)), 2),
        "type_search_pipeline_start_ms": round(float(target_perf.get("type_search_pipeline_start_ms", 0.0)), 2),
        "type_search_focus_phase_ms": round(float(target_perf.get("type_search_focus_phase_ms", 0.0)), 2),
        "type_search_pre_fastime_ms": round(float(target_perf.get("type_search_pre_fastime_ms", 0.0)), 2),
        "type_search_ime_available_probe_ms": round(
            float(target_perf.get("type_search_ime_available_probe_ms", 0.0)), 2
        ),
        "type_search_fastime_phase_ms": round(float(target_perf.get("type_search_fastime_phase_ms", 0.0)), 2),
        "type_search_result_wait_ms": round(float(target_perf.get("type_search_result_wait_ms", 0.0)), 2),
        "type_search_finalize_ms": round(float(target_perf.get("type_search_finalize_ms", 0.0)), 2),
        "type_search_wall_clock_guard_ms": round(
            float(target_perf.get("type_search_wall_clock_guard_ms", 0.0)), 2
        ),
        "tap_account_ms": round(tap_account_ms, 2),
        "open_dm_thread_ms": round(open_dm_thread_ms, 2),
        "dm_draft_total_ms": round(dm_draft_total_ms, 2),
        "dm_back_to_profile_ms": round(float(target_perf.get("dm_back_to_profile_ms", 0.0)), 2),
        "profile_to_search_ms": round(float(target_perf.get("profile_to_search_ms", 0.0)), 2),
        "reset_to_search_ms": round(float(target_perf.get("reset_to_search_ms", 0.0)), 2),
        "next_username_ready_ms": round(float(target_perf.get("next_username_ready_ms", 0.0)), 2),
        "navigation_strategy": navigation_strategy,
        "exit_code": int(exit_code),
    }


def _run_one_target(
    d,
    username: str,
    *,
    target_index: int,
    warm_session_used: bool,
    force_stop_used: bool,
    previous_username: str | None,
    use_fast_reset_between_targets: bool = False,
    first_action_delay_ms: float = 0.0,
    next_username_ready_ms: float = 0.0,
    target_row_raw: dict | None = None,
    account_id: str = "",
    run_id: str = "",
    supabase_mode: bool = False,
) -> int:
    """Navigate search → profile for one username. Assumes IG already foreground when target_index==0."""
    pkg = config.INSTAGRAM_PACKAGE
    target_id = str((target_row_raw or {}).get("id") or "").strip()
    t0 = time.perf_counter()
    t = t0
    phase_ms: dict[str, float] = {}

    def _mark_phase_ms(name: str, prev_t: float) -> float:
        now = time.perf_counter()
        phase_ms[name] = (now - prev_t) * 1000
        return now

    def _emit_target_perf_compact(exit_code: int) -> None:
        snap = get_perf_snapshot()
        open_search_ms = float(snap.get("search_click_ms", 0.0)) + float(
            snap.get("search_field_ready_ms", 0.0)
        )
        ts_total = float(snap.get("type_search_total_ms", 0.0))
        type_search_ms = (
            ts_total
            if ts_total > 0.0
            else float(snap.get("typing_command_ms", 0.0)) + float(snap.get("typing_confirm_ms", 0.0))
        )
        tap_account_ms = (
            float(snap.get("row_detect_ms", 0.0))
            + float(snap.get("row_tap_command_ms", 0.0))
            + float(snap.get("post_tap_settle_ms", 0.0))
        )
        log(
            "info",
            "target_perf_compact",
            username=username,
            total_ms=round((time.perf_counter() - t0) * 1000, 2),
            first_action_delay_ms=round(float(snap.get("first_action_delay_ms", 0.0)), 2),
            open_search_ms=round(open_search_ms, 2),
            type_search_ms=round(type_search_ms, 2),
            search_edittext_wait_ms=round(float(snap.get("search_edittext_wait_ms", 0.0)), 2),
            search_clear_ms=round(float(snap.get("search_clear_ms", 0.0)), 2),
            search_focus_ms=round(float(snap.get("search_focus_ms", 0.0)), 2),
            search_focus_attempts=round(float(snap.get("search_focus_attempts", 0.0)), 2),
            search_focus_retry_count=round(float(snap.get("search_focus_retry_count", 0.0)), 2),
            search_focus_strategy_used=round(float(snap.get("search_focus_strategy_used", 0.0)), 2),
            type_search_pipeline_start_ms=round(float(snap.get("type_search_pipeline_start_ms", 0.0)), 2),
            search_placeholder_check_ms=round(float(snap.get("search_placeholder_check_ms", 0.0)), 2),
            search_clear_decision_ms=round(float(snap.get("search_clear_decision_ms", 0.0)), 2),
            search_refetch_ms=round(float(snap.get("search_refetch_ms", 0.0)), 2),
            search_pre_focus_validation_ms=round(
                float(snap.get("search_pre_focus_validation_ms", 0.0)), 2
            ),
            type_search_pre_focus_accounted_ms=round(
                float(snap.get("search_edittext_wait_ms", 0.0))
                + float(snap.get("search_placeholder_check_ms", 0.0))
                + float(snap.get("search_clear_ms", 0.0))
                + float(snap.get("search_refetch_ms", 0.0))
                + float(snap.get("search_pre_focus_validation_ms", 0.0)),
                2,
            ),
            type_search_focus_phase_ms=round(float(snap.get("type_search_focus_phase_ms", 0.0)), 2),
            type_search_pre_fastime_ms=round(float(snap.get("type_search_pre_fastime_ms", 0.0)), 2),
            type_search_ime_available_probe_ms=round(
                float(snap.get("type_search_ime_available_probe_ms", 0.0)), 2
            ),
            type_search_fastime_phase_ms=round(float(snap.get("type_search_fastime_phase_ms", 0.0)), 2),
            type_search_result_wait_ms=round(float(snap.get("type_search_result_wait_ms", 0.0)), 2),
            type_search_finalize_ms=round(float(snap.get("type_search_finalize_ms", 0.0)), 2),
            type_search_wall_clock_guard_ms=round(
                float(snap.get("type_search_wall_clock_guard_ms", 0.0)), 2
            ),
            search_fastime_broadcast_ms=round(float(snap.get("search_fastime_broadcast_ms", 0.0)), 2),
            search_post_type_settle_ms=round(float(snap.get("search_post_type_settle_ms", 0.0)), 2),
            tap_account_ms=round(tap_account_ms, 2),
            open_dm_thread_ms=round(float(snap.get("dm_open_click_ms", 0.0)) + float(snap.get("dm_thread_detect_ms", 0.0)), 2),
            dm_draft_total_ms=round(
                float(snap.get("dm_draft_typing_ms", 0.0))
                + float(snap.get("dm_draft_verify_ms", 0.0))
                + float(snap.get("dm_draft_clear_ms", 0.0)),
                2,
            ),
            dm_back_to_profile_ms=round(float(snap.get("dm_back_to_profile_ms", 0.0)), 2),
            profile_to_search_ms=round(float(snap.get("profile_to_search_ms", 0.0)), 2),
            next_username_ready_ms=round(float(snap.get("next_username_ready_ms", 0.0)), 2),
            exit_code=exit_code,
        )

    def _insert_dm_draft_log(action_type: str, status: str, message: str, payload: dict) -> None:
        if not (supabase_mode and run_id and account_id):
            return
        _safe_supabase_call(
            "insert_action_log",
            run_id=run_id,
            account_id=account_id,
            target_username=username,
            action_type=action_type,
            status=status,
            message=message,
            payload=payload,
        )

    def _flush_follow_events(events: list[tuple[str, dict]]) -> None:
        _flush_follow_action_logs_to_supabase(
            events=events,
            run_id=run_id,
            account_id=account_id,
            target_username=username,
            supabase_mode=supabase_mode,
        )

    def _dm_composer_actual_text_len() -> int:
        try:
            cur = d(resourceIdMatches=r".*:id/row_thread_composer_edittext.*")
            if cur.exists(timeout=0.05):
                t = cur.get_text() or ""
                return len(str(t))
        except Exception:
            pass
        try:
            for ed in d(className="android.widget.EditText").all():
                b = (ed.info or {}).get("bounds") or {}
                if int(b.get("top", 0)) > int(d.window_size()[1] * 0.35):
                    t = ed.get_text() or ""
                    return len(str(t))
        except Exception:
            pass
        return 0
    set_perf_metric("first_action_delay_ms", first_action_delay_ms)
    set_perf_metric("next_username_ready_ms", next_username_ready_ms)
    if target_index == 0:
        log("info", "first_action_delay_ms", value=round(first_action_delay_ms, 2))
    if is_dm_thread_screen(d, pkg):
        if not return_to_profile_from_dm(d, username, pkg):
            log("error", "run_aborted", reason="stuck_in_dm_thread", username=username)
            exit_code = 14
            _emit_performance_summary(
                t0=t0,
                warm_session_used=warm_session_used,
                force_stop_used=force_stop_used,
                exit_code=exit_code,
                target_username=username,
            )
            return exit_code
        t = _phase("dm_back_to_profile_recovery", t)
        if not return_to_search_from_profile(d, pkg):
            log("error", "run_aborted", reason="stuck_in_dm_thread", username=username)
            exit_code = 14
            _emit_performance_summary(
                t0=t0,
                warm_session_used=warm_session_used,
                force_stop_used=force_stop_used,
                exit_code=exit_code,
                target_username=username,
            )
            return exit_code
        t = _phase("profile_back_to_search_recovery", t)

    if target_index > 0 and not use_fast_reset_between_targets:
        fast_mode = bool(getattr(config, "FAST_PATH_MODE", False))
        quick_on_search = False
        t_already = time.perf_counter()
        if bool(getattr(config, "FAST_DISABLE_ALREADY_ON_SEARCH_LONG_WAIT", True)) and fast_mode:
            t_quick = time.monotonic() + 0.5
            while time.monotonic() < t_quick:
                if is_lightweight_search_screen(d, pkg):
                    quick_on_search = True
                    break
                time.sleep(0.05)
        else:
            quick_on_search = is_lightweight_search_screen(d, pkg)

        if quick_on_search:
            log("info", "next_username_fast_path", mode="already_on_search_quick")
            elapsed = (time.perf_counter() - t_already) * 1000
            log("info", "phase_timing", phase="already_on_search", elapsed_ms=round(elapsed, 2))
            _log_phase_budget("already_on_search", elapsed, 2000.0)
            t = time.perf_counter()
        elif bool(getattr(config, "FAST_DIRECT_OPEN_SEARCH_ON_NEXT_TARGET", True)) and fast_mode:
            log("info", "next_username_fast_path", mode="direct_open_search")
            if not _open_search_with_recovery(
                d, pkg=pkg, username=username, context="next_target_fast_direct_open_search"
            ):
                log("error", "run_aborted", reason="open_search_failed_after_back", username=username)
                exit_code = 4
                _emit_performance_summary(
                    t0=t0,
                    warm_session_used=warm_session_used,
                    force_stop_used=force_stop_used,
                    exit_code=exit_code,
                    target_username=username,
                )
                return exit_code
            t = _phase("open_search", t)
        elif return_to_search_from_profile(d, pkg):
            t = _phase("return_to_search", t)
        else:
            t = _phase("return_to_search_failed", t)
            if not _open_search_with_recovery(
                d, pkg=pkg, username=username, context="after_back_from_profile"
            ):
                log("error", "run_aborted", reason="open_search_failed_after_back", username=username)
                exit_code = 4
                _emit_performance_summary(
                    t0=t0,
                    warm_session_used=warm_session_used,
                    force_stop_used=force_stop_used,
                    exit_code=exit_code,
                    target_username=username,
                )
                _emit_target_perf_compact(exit_code)
                return exit_code
            t = _phase("open_search", t)
            t = _mark_phase_ms("open_search", t)
    else:
        if not _open_search_with_recovery(d, pkg=pkg, username=username, context="first_target"):
            log("error", "run_aborted", reason="open_search_failed", username=username)
            exit_code = 4
            _emit_performance_summary(
                t0=t0,
                warm_session_used=warm_session_used,
                force_stop_used=force_stop_used,
                exit_code=exit_code,
                target_username=username,
            )
            _emit_target_perf_compact(exit_code)
            return exit_code
        t = _phase("open_search", t)
        t = _mark_phase_ms("open_search", t)

    if not type_search(d, username, previous_username=previous_username):
        reason = get_type_search_failure_reason() or "type_search_failed"
        if reason == "search_surface_wrong_app_launcher":
            exit_code = 73
        elif reason == "search_field_not_cleared":
            exit_code = 9
        else:
            exit_code = 5
        log("error", "run_aborted", reason=reason, username=username, exit_code=exit_code)
        _emit_performance_summary(
            t0=t0,
            warm_session_used=warm_session_used,
            force_stop_used=force_stop_used,
            exit_code=exit_code,
            target_username=username,
        )
        _emit_target_perf_compact(exit_code)
        return exit_code
    t = _phase("type_search", t)
    t = _mark_phase_ms("type_search", t)

    if bool(getattr(config, "FAST_SKIP_ACCOUNTS_TAB", True)) and bool(
        getattr(config, "FAST_PATH_MODE", False)
    ):
        accounts_tab_clicked = False
        log("info", "accounts_tab_skipped_fast_path")
    else:
        accounts_tab_clicked = open_accounts_tab(d)
    ui_mode = "accounts_tab" if accounts_tab_clicked else "mixed_results"
    set_search_ui_mode(ui_mode)
    log("info", "search_ui_mode", mode=ui_mode)
    t = _phase("open_accounts_tab", t)

    if not tap_account_result(d, username):
        log("error", "run_aborted", reason="tap_account_failed", username=username)
        exit_code = 7
        _emit_performance_summary(
            t0=t0,
            warm_session_used=warm_session_used,
            force_stop_used=force_stop_used,
            exit_code=exit_code,
            target_username=username,
        )
        _emit_target_perf_compact(exit_code)
        return exit_code
    t = _phase("tap_account", t)
    t = _mark_phase_ms("tap_account", t)

    if not verify_profile(d, username):
        log("error", "run_aborted", reason="profile_verify_failed", username=username)
        exit_code = 8
        _emit_performance_summary(
            t0=t0,
            warm_session_used=warm_session_used,
            force_stop_used=force_stop_used,
            exit_code=exit_code,
            target_username=username,
        )
        _emit_target_perf_compact(exit_code)
        return exit_code
    t = _phase("verify_profile", t)

    if bool(getattr(config, "ENABLE_REAL_FOLLOW", False)):
        global _RUNTIME_FOLLOW_COUNT, _RUNTIME_FOLLOWED_USERNAMES, _RUNTIME_INTERACTED_USERNAMES
        global _RUNTIME_SKIPPED_USERNAMES
        ukey = (username or "").strip().lower()
        # Queue targets: persist social memory without blogger source_profile (empty key).
        social_src = ""

        def _follow_fail_emit(
            exit_c: int, action_type: str, payload_extra: dict | None = None
        ) -> int:
            pl = {
                "target_username": username,
                "account_id": account_id,
                "run_id": run_id,
                "follow_state_before": None,
                "follow_state_after": None,
                "verify_attempts": 0,
                "navigation_state": "profile_after_verify",
                "timings": {},
            }
            if payload_extra:
                pl.update(payload_extra)
            if supabase_mode and run_id and account_id:
                _safe_supabase_call(
                    "insert_action_log",
                    run_id=run_id,
                    account_id=account_id,
                    target_username=username,
                    action_type=action_type,
                    status="blocked",
                    message=action_type,
                    payload=pl,
                )
            _emit_performance_summary(
                t0=t0,
                warm_session_used=warm_session_used,
                force_stop_used=force_stop_used,
                exit_code=exit_c,
                target_username=username,
            )
            _emit_target_perf_compact(exit_c)
            return exit_c

        if _session_follow_quota_exceeded():
            log(
                "warning",
                "social_memory_follow_blocked",
                username=username,
                reason="session_follow_quota",
            )
            return _follow_fail_emit(
                37,
                "social_memory_follow_blocked",
                {"reason": "session_follow_quota", "follows": _SESSION_COUNTERS["follows"]},
            )

        if _session_total_interactions_cap_exceeded():
            log(
                "warning",
                "social_memory_follow_blocked",
                username=username,
                reason="session_total_interactions_cap",
            )
            return _follow_fail_emit(
                37,
                "social_memory_follow_blocked",
                {"reason": "session_total_interactions_cap"},
            )

        if _session_successful_interactions_cap_exceeded():
            log(
                "warning",
                "social_memory_follow_blocked",
                username=username,
                reason="session_successful_interactions_cap",
            )
            return _follow_fail_emit(
                37,
                "social_memory_follow_blocked",
                {"reason": "session_successful_interactions_cap"},
            )

        elig = _social_memory_load_and_evaluate(
            target_username=username,
            source_profile=social_src,
            account_id=account_id,
            run_id=run_id,
            supabase_mode=supabase_mode,
        )
        if not elig.allowed:
            log(
                "warning",
                elig.log_event,
                username=username,
                reason=elig.reason,
                interaction_state=elig.interaction_state,
            )
            log(
                "info",
                "social_memory_interaction_state",
                username=username,
                state=elig.interaction_state,
                reason=elig.reason,
            )
            _persist_social_memory_follow_block(
                elig=elig,
                account_id=account_id,
                run_id=run_id,
                supabase_mode=supabase_mode,
                target_username=username,
                source_profile=social_src,
            )
            if supabase_mode and run_id and account_id:
                _safe_supabase_call(
                    "insert_action_log",
                    run_id=run_id,
                    account_id=account_id,
                    target_username=username,
                    action_type="social_memory_follow_blocked",
                    status="blocked",
                    message=elig.reason,
                    payload={
                        "target_username": username,
                        "source_profile": social_src,
                        "reason": elig.reason,
                        "interaction_state": elig.interaction_state,
                    },
                )
            _RUNTIME_INTERACTED_USERNAMES.add(ukey)
            _RUNTIME_SKIPPED_USERNAMES.add(ukey)
            return _follow_fail_emit(
                37,
                "social_memory_follow_blocked",
                {"reason": elig.reason, "interaction_state": elig.interaction_state},
            )

        if ukey in _RUNTIME_FOLLOWED_USERNAMES:
            log(
                "warning",
                "follow_blocked_runtime_duplicate",
                username=username,
                reason="same_username_in_run",
            )
            return _follow_fail_emit(
                35,
                "follow_blocked_runtime_duplicate",
                {"reason": "same_username_in_run"},
            )

        if _RUNTIME_FOLLOW_COUNT >= int(getattr(config, "FOLLOW_MAX_PER_RUN", 5)):
            log(
                "warning",
                "follow_blocked_runtime_duplicate",
                username=username,
                reason="follow_max_per_run",
            )
            return _follow_fail_emit(
                35,
                "follow_blocked_runtime_duplicate",
                {
                    "reason": "follow_max_per_run",
                    "follow_max_per_run": int(getattr(config, "FOLLOW_MAX_PER_RUN", 5)),
                },
            )

        if supabase_mode and target_id:
            row_f = _safe_supabase_call("load_target_by_id", target_id) or {}
            if bool(row_f.get("followed")):
                log("warning", "follow_blocked_db_duplicate", username=username, target_id=target_id)
                return _follow_fail_emit(
                    35,
                    "follow_blocked_db_duplicate",
                    {"target_id": target_id},
                )
            cool_h = float(getattr(config, "FOLLOW_COOLDOWN_HOURS", 0) or 0)
            if cool_h > 0 and row_f.get("followed_at"):
                elapsed_h = _seconds_since_follow_row(row_f)
                if elapsed_h is not None and elapsed_h < cool_h:
                    log(
                        "warning",
                        "follow_blocked_cooldown",
                        username=username,
                        hours_since=float(elapsed_h),
                        cooldown_hours=cool_h,
                    )
                    return _follow_fail_emit(
                        36,
                        "follow_blocked_cooldown",
                        {
                            "cooldown_hours": cool_h,
                            "hours_since_follow": float(elapsed_h),
                            "note": "followed_at_recent_without_followed_flag",
                        },
                    )

        follow_out = perform_follow_safe(d, username, pkg)
        _flush_follow_events(list(follow_out.get("events") or []))

        if not follow_out.get("ok"):
            fc = int(follow_out.get("failure_code") or 33)
            _SESSION_COUNTERS["interactions"] += 1
            _RUNTIME_INTERACTED_USERNAMES.add(ukey)
            if supabase_mode and account_id:
                _safe_supabase_call(
                    "record_follow_interaction_outcome",
                    account_id,
                    username,
                    social_src,
                    run_id=run_id or None,
                    session_id=_SESSION_SOCIAL_ID or None,
                    follow_ok=False,
                    skipped_tap=False,
                    follow_state_after=str(follow_out.get("follow_state_after") or ""),
                    follow_status=None,
                    failure_code=fc,
                    failure_reason=_exit_reason_from_code(fc),
                )
                log(
                    "info",
                    "social_memory_updated",
                    target_username=username,
                    kind="follow_failed",
                    failure_code=fc,
                )
            if supabase_mode and target_id:
                _safe_supabase_call(
                    "patch_target_follow_fields",
                    target_id,
                    follow_status="failed",
                    last_follow_error=_exit_reason_from_code(fc),
                    last_follow_run_id=run_id or None,
                )
            _emit_performance_summary(
                t0=t0,
                warm_session_used=warm_session_used,
                force_stop_used=force_stop_used,
                exit_code=fc,
                target_username=username,
            )
            _emit_target_perf_compact(fc)
            return fc

        fs_after = str(follow_out.get("follow_state_after") or "")
        if bool(follow_out.get("skipped_tap")):
            follow_status = "already_following"
        elif fs_after == "requested":
            follow_status = "requested"
        else:
            follow_status = "following"

        if not bool(follow_out.get("skipped_tap")):
            _RUNTIME_FOLLOW_COUNT += 1
        _RUNTIME_FOLLOWED_USERNAMES.add(ukey)
        _RUNTIME_INTERACTED_USERNAMES.add(ukey)
        _SESSION_COUNTERS["follows"] += 1
        _SESSION_COUNTERS["interactions"] += 1
        _SESSION_COUNTERS["successful_interactions"] += 1

        if supabase_mode and account_id:
            mem_ok = _safe_supabase_call(
                "record_follow_interaction_outcome",
                account_id,
                username,
                social_src,
                run_id=run_id or None,
                session_id=_SESSION_SOCIAL_ID or None,
                follow_ok=True,
                skipped_tap=bool(follow_out.get("skipped_tap")),
                follow_state_after=str(follow_out.get("follow_state_after") or ""),
                follow_status=follow_status,
                failure_code=None,
                failure_reason=None,
            )
            log(
                "info",
                "social_memory_updated",
                target_username=username,
                kind="follow_success",
                memory_ok=(mem_ok or {}).get("ok"),
            )

        if supabase_mode and target_id:
            biz = _safe_supabase_call(
                "mark_target_follow_business",
                target_id,
                run_id=run_id or None,
                follow_status=follow_status,
                follow_method=(
                    "ui_already_following"
                    if bool(follow_out.get("skipped_tap"))
                    else "ui_tap"
                ),
                last_follow_error=None,
            )
            if run_id and account_id:
                _safe_supabase_call(
                    "insert_action_log",
                    run_id=run_id,
                    account_id=account_id,
                    target_username=username,
                    action_type="follow_business_mark",
                    status="success" if (biz or {}).get("ok") else "failed",
                    message="follow business persist",
                    payload={
                        "target_username": username,
                        "account_id": account_id,
                        "run_id": run_id,
                        "follow_status": follow_status,
                        "mark_applied": (biz or {}).get("applied"),
                        "mark_error": (biz or {}).get("error"),
                        "verify_attempts": int(follow_out.get("verify_attempts") or 0),
                        "navigation_state": "profile",
                    },
                )

        if not bool(getattr(config, "FOLLOW_THEN_DM", True)):
            log("info", "follow_return_profile_ok", username=username, mode="follow_only")
            if supabase_mode and run_id and account_id:
                _safe_supabase_call(
                    "insert_action_log",
                    run_id=run_id,
                    account_id=account_id,
                    target_username=username,
                    action_type="follow_return_profile_ok",
                    status="info",
                    message="profile stable before return to search",
                    payload={
                        "target_username": username,
                        "account_id": account_id,
                        "run_id": run_id,
                        "navigation_state": "profile",
                        "follow_state_after": fs_after,
                    },
                )
            search_ok = False
            if use_fast_reset_between_targets:
                search_ok = bool(reset_to_search_for_next_target(d, pkg))
            else:
                search_ok = bool(return_to_search_from_profile(d, pkg))
            partial = False
            if search_ok:
                invalidate_search_surface_cache("follow_complete")
                log("info", "follow_return_search_ok", username=username)
                if supabase_mode and run_id and account_id:
                    _safe_supabase_call(
                        "insert_action_log",
                        run_id=run_id,
                        account_id=account_id,
                        target_username=username,
                        action_type="follow_return_search_ok",
                        status="success",
                        message="back to search after follow",
                        payload={
                            "target_username": username,
                            "account_id": account_id,
                            "run_id": run_id,
                            "navigation_state": "search",
                        },
                    )
                nav_code = 31
            else:
                if open_search(d):
                    partial = True
                    invalidate_search_surface_cache("follow_return_partial")
                    log(
                        "warning",
                        "follow_navigation_partial",
                        username=username,
                        note="open_search_recovered",
                    )
                    if supabase_mode and run_id and account_id:
                        _safe_supabase_call(
                            "insert_action_log",
                            run_id=run_id,
                            account_id=account_id,
                            target_username=username,
                            action_type="follow_navigation_partial",
                            status="warning",
                            message="partial recovery to search",
                            payload={
                                "target_username": username,
                                "account_id": account_id,
                                "run_id": run_id,
                                "navigation_state": "search_partial",
                            },
                        )
                    nav_code = 32
                else:
                    log("error", "follow_navigation_failed", username=username)
                    if supabase_mode and run_id and account_id:
                        _safe_supabase_call(
                            "insert_action_log",
                            run_id=run_id,
                            account_id=account_id,
                            target_username=username,
                            action_type="follow_navigation_failed",
                            status="failed",
                            message="could not return to search after follow",
                            payload={
                                "target_username": username,
                                "account_id": account_id,
                                "run_id": run_id,
                                "navigation_state": "unknown",
                            },
                        )
                    nav_code = 32
            _emit_performance_summary(
                t0=t0,
                warm_session_used=warm_session_used,
                force_stop_used=force_stop_used,
                exit_code=nav_code,
                target_username=username,
            )
            _emit_target_perf_compact(nav_code)
            return nav_code

        log("info", "follow_return_profile_ok", username=username, mode="follow_then_dm")
        if supabase_mode and run_id and account_id:
            _safe_supabase_call(
                "insert_action_log",
                run_id=run_id,
                account_id=account_id,
                target_username=username,
                action_type="follow_return_profile_ok",
                status="info",
                message="remain on profile for DM",
                payload={
                    "target_username": username,
                    "account_id": account_id,
                    "run_id": run_id,
                    "navigation_state": "profile_continue_dm",
                    "follow_state_after": fs_after,
                },
            )

    dm_state = open_dm_thread_from_profile(d, username)
    t = _phase("open_dm_thread", t)
    t = _mark_phase_ms("open_dm_thread", t)
    if dm_state == "composer_visible_uncertain":
        log("info", "dm_thread_state_allowed_for_draft_only", username=username, thread_state=dm_state)
    if dm_state == "unknown":
        log("error", "run_aborted", reason="dm_thread_unknown", username=username)
        exit_code = 11
        _emit_performance_summary(
            t0=t0,
            warm_session_used=warm_session_used,
            force_stop_used=force_stop_used,
            exit_code=exit_code,
            target_username=username,
        )
        _emit_target_perf_compact(exit_code)
        return exit_code

    if bool(getattr(config, "DM_DRAFT_TYPING_ENABLED", True)):
        ok_comp, comp_signal = verify_dm_composer_safe(d, pkg)
        if not ok_comp:
            _insert_dm_draft_log(
                action_type="dm_draft_blocked_composer_not_visible",
                status="failed",
                message="composer not visible/safe",
                payload={
                    "reason": str(comp_signal or "dm_composer_not_safe"),
                    "thread_state": dm_state,
                    "composer_visible": False,
                    "expected_text_len": len(str(getattr(config, "SAFE_DRAFT_MESSAGE", "") or "")),
                    "actual_text_len": _dm_composer_actual_text_len(),
                    "draft_verify_attempts": 0,
                    "fastime_used": False,
                    "set_text_used": False,
                },
            )
            log(
                "error",
                "run_aborted",
                reason="dm_composer_not_safe",
                username=username,
                composer_reason=comp_signal,
            )
            exit_code = 15
            _emit_performance_summary(
                t0=t0,
                warm_session_used=warm_session_used,
                force_stop_used=force_stop_used,
                exit_code=exit_code,
                target_username=username,
            )
            _emit_target_perf_compact(exit_code)
            return exit_code

        draft_text = str(getattr(config, "SAFE_DRAFT_MESSAGE", "") or "")
        ok_type, type_info = type_dm_draft_only(d, draft_text, pkg)
        type_info_s = str(type_info or "")
        info = type_info if isinstance(type_info, dict) else {}
        method = str(info.get("method") or "")
        fastime_used = bool(info.get("fastime_used")) or method == "fast_ime"
        set_text_used = bool(info.get("set_text_used")) or method == "set_text"

        def _draft_payload(
            *,
            verify_will_continue: bool | None = None,
            actual_len_override: int | None = None,
            prefix_override: str | None = None,
        ) -> dict:
            expected_len = int(info.get("expected_text_len") or len(draft_text))
            actual_len = (
                int(actual_len_override)
                if actual_len_override is not None
                else int(info.get("actual_text_len") or _dm_composer_actual_text_len())
            )
            prefix = (
                str(prefix_override)
                if prefix_override is not None
                else str(info.get("text_prefix_preview") or "")[:20]
            )
            out = {
                "expected_text_len": expected_len,
                "actual_text_len": actual_len,
                "fastime_used": bool(fastime_used),
                "set_text_used": bool(set_text_used),
                "thread_state": dm_state,
                "composer_visible": bool(info.get("composer_visible", True)),
                "text_prefix_preview": prefix[:20],
            }
            if verify_will_continue is not None:
                out["verify_will_continue"] = bool(verify_will_continue)
            return out

        if fastime_used:
            _insert_dm_draft_log(
                action_type="dm_draft_text_after_fastime",
                status="success" if ok_type else "failed",
                message="text observed after fastime",
                payload=_draft_payload(
                    verify_will_continue=bool(info.get("verify_will_continue", False)),
                    actual_len_override=int(info.get("actual_text_len_after_fastime") or 0),
                    prefix_override=str(info.get("text_prefix_after_fastime") or "")[:20],
                ),
            )
        if bool(info.get("fastime_partial_text")):
            _insert_dm_draft_log(
                action_type="dm_draft_fastime_partial_text",
                status="warning",
                message="fastime partial text detected",
                payload=_draft_payload(
                    verify_will_continue=bool(info.get("fallback_set_text_started", False))
                ),
            )
        if bool(info.get("fastime_truncated")):
            _insert_dm_draft_log(
                action_type="dm_draft_fastime_truncated",
                status="warning",
                message="fastime text truncated",
                payload=_draft_payload(
                    verify_will_continue=bool(info.get("fallback_set_text_started", False))
                ),
            )
        if bool(info.get("fallback_set_text_started")):
            _insert_dm_draft_log(
                action_type="dm_draft_fallback_set_text_started",
                status="started",
                message="fallback set_text started",
                payload=_draft_payload(
                    verify_will_continue=bool(info.get("fallback_set_text_ok", False))
                ),
            )
        if bool(info.get("fallback_set_text_ok")):
            _insert_dm_draft_log(
                action_type="dm_draft_fallback_set_text_ok",
                status="success",
                message="fallback set_text ok",
                payload=_draft_payload(
                    verify_will_continue=True,
                    actual_len_override=int(info.get("actual_text_len_after_set_text") or 0),
                    prefix_override=str(info.get("text_prefix_after_set_text") or "")[:20],
                ),
            )
            _insert_dm_draft_log(
                action_type="dm_draft_text_after_set_text",
                status="success",
                message="text observed after set_text",
                payload=_draft_payload(
                    verify_will_continue=True,
                    actual_len_override=int(info.get("actual_text_len_after_set_text") or 0),
                    prefix_override=str(info.get("text_prefix_after_set_text") or "")[:20],
                ),
            )
        if bool(info.get("fallback_set_text_failed")):
            _insert_dm_draft_log(
                action_type="dm_draft_fallback_set_text_failed",
                status="failed",
                message="fallback set_text failed",
                payload=_draft_payload(verify_will_continue=False),
            )

        if (not ok_type) and ("fast_ime" in type_info_s.lower()) and not fastime_used:
            _insert_dm_draft_log(
                action_type="dm_draft_fastime_ui_not_visible",
                status="failed",
                message="fastime path failed",
                payload={
                    **_draft_payload(verify_will_continue=False),
                    "reason": type_info_s or "fast_ime_failed",
                },
            )
        if not ok_type:
            log(
                "error",
                "run_aborted",
                reason="dm_draft_typing_failed",
                username=username,
                detail=type_info,
            )
            exit_code = 16
            _emit_performance_summary(
                t0=t0,
                warm_session_used=warm_session_used,
                force_stop_used=force_stop_used,
                exit_code=exit_code,
                target_username=username,
            )
            return exit_code
        _insert_dm_draft_log(
            action_type="dm_draft_typed",
            status="success",
            message="draft typed",
            payload={
                "thread_state": dm_state,
                "composer_visible": True,
                "expected_text_len": len(draft_text),
                "actual_text_len": _dm_composer_actual_text_len(),
                "draft_verify_attempts": 0,
                "fastime_used": bool(fastime_used),
                "set_text_used": bool(set_text_used),
            },
        )

        if bool(getattr(config, "DM_VERIFY_TYPED_TEXT", True)):
            draft_verify_attempts = 1
            verify_ok = verify_dm_draft_text(d, draft_text)
            _insert_dm_draft_log(
                action_type="dm_draft_verify_ui_match",
                status="success" if verify_ok else "failed",
                message="draft verify ui match",
                payload={
                    "thread_state": dm_state,
                    "composer_visible": True,
                    "expected_text_len": len(draft_text),
                    "actual_text_len": _dm_composer_actual_text_len(),
                    "draft_verify_attempts": draft_verify_attempts,
                    "fastime_used": bool(fastime_used),
                    "set_text_used": bool(set_text_used),
                },
            )
            if verify_ok:
                _insert_dm_draft_log(
                    action_type="dm_draft_verify_ok",
                    status="success",
                    message="draft verify ok",
                    payload={
                        "thread_state": dm_state,
                        "composer_visible": True,
                        "expected_text_len": len(draft_text),
                        "actual_text_len": _dm_composer_actual_text_len(),
                        "draft_verify_attempts": draft_verify_attempts,
                        "fastime_used": bool(fastime_used),
                        "set_text_used": bool(set_text_used),
                    },
                )
            if not verify_ok:
                actual_len = _dm_composer_actual_text_len()
                _insert_dm_draft_log(
                    action_type="dm_draft_verify_failed",
                    status="failed",
                    message="draft verify failed",
                    payload={
                        "reason": "dm_draft_verify_failed",
                        "thread_state": dm_state,
                        "composer_visible": True,
                        "expected_text_len": len(draft_text),
                        "actual_text_len": actual_len,
                        "draft_verify_attempts": draft_verify_attempts,
                        "fastime_used": bool(fastime_used),
                        "set_text_used": bool(set_text_used),
                    },
                )
                _insert_dm_draft_log(
                    action_type="dm_draft_blocked_text_mismatch",
                    status="failed",
                    message="draft text mismatch",
                    payload={
                        "reason": "dm_draft_verify_failed",
                        "thread_state": dm_state,
                        "composer_visible": True,
                        "expected_text_len": len(draft_text),
                        "actual_text_len": actual_len,
                        "draft_verify_attempts": draft_verify_attempts,
                        "fastime_used": bool(fastime_used),
                        "set_text_used": bool(set_text_used),
                    },
                )
                _insert_dm_draft_log(
                    action_type="dm_draft_exit_17_reason",
                    status="failed",
                    message="exit 17 draft verify failed",
                    payload={
                        "reason": "dm_draft_verify_failed",
                        "thread_state": dm_state,
                        "composer_visible": bool(
                            (get_last_dm_thread_classify_snapshot() or {}).get(
                                "composer_visible", True
                            )
                        ),
                        "expected_text_len": len(draft_text),
                        "actual_text_len": actual_len,
                        "draft_verify_attempts": draft_verify_attempts,
                        "fastime_used": bool(fastime_used),
                        "set_text_used": bool(set_text_used),
                    },
                )
            if not verify_ok:
                log("error", "run_aborted", reason="dm_draft_verify_failed", username=username)
                exit_code = 17
                _emit_performance_summary(
                    t0=t0,
                    warm_session_used=warm_session_used,
                    force_stop_used=force_stop_used,
                    exit_code=exit_code,
                    target_username=username,
                )
                _emit_target_perf_compact(exit_code)
                return exit_code

        global _RUNTIME_REAL_DM_SENT_COUNT
        send_blocked: str | None = None
        cooldown_remain: float | None = None
        previous_dm_sent = False
        if bool(getattr(config, "ENABLE_REAL_DM_SEND", False)):
            if _RUNTIME_REAL_DM_SENT_COUNT >= int(getattr(config, "SEND_DM_MAX_PER_RUN", 1)):
                send_blocked = "dm_send_blocked_already_sent_runtime"
            elif supabase_mode and target_id:
                row_guard = _safe_supabase_call("load_target_by_id", target_id) or {}
                previous_dm_sent = bool(row_guard.get("dm_sent"))
                if previous_dm_sent and bool(
                    getattr(config, "SEND_DM_SKIP_IF_PREVIOUS_DM_SENT", True)
                ):
                    send_blocked = "dm_send_blocked_previous_dm"
                else:
                    cool = float(getattr(config, "SEND_DM_COOLDOWN_SECONDS", 0) or 0)
                    if cool > 0 and previous_dm_sent:
                        elapsed = _seconds_since_dm_sent_row(row_guard)
                        if elapsed is not None and elapsed < cool:
                            send_blocked = "dm_send_blocked_cooldown"
                            cooldown_remain = round(cool - elapsed, 2)

        if send_blocked:
            block_payload: dict = {
                "target_username": username,
                "run_id": run_id or None,
                "thread_state": dm_state,
                "send_method": None,
                "message_len": len(draft_text),
                "dm_sent": False,
                "dm_sent_at": None,
                "cooldown_seconds": float(getattr(config, "SEND_DM_COOLDOWN_SECONDS", 0) or 0),
                "previous_dm_sent": previous_dm_sent,
                "business_status": "send_blocked",
                "navigation_status_post_send": None,
            }
            if send_blocked == "dm_send_blocked_cooldown":
                block_payload["cooldown_remain_s"] = cooldown_remain
            if supabase_mode and run_id and account_id:
                _safe_supabase_call(
                    "insert_action_log",
                    run_id=run_id,
                    account_id=account_id,
                    target_username=username,
                    action_type=send_blocked,
                    status="blocked",
                    message=send_blocked,
                    payload=block_payload,
                )
            send_out = {
                "target_username": username,
                "thread_state": dm_state,
                "enable_real_send": bool(getattr(config, "ENABLE_REAL_DM_SEND", False)),
                "message_len": len(draft_text),
                "precheck_ok": True,
                "sent": False,
                "reason": None,
                "blocked_event": send_blocked,
                "failure_event": None,
            }
        else:
            send_out = send_dm_safe(
                d, username, draft_text, dm_state, target_row=target_row_raw
            )
        if supabase_mode and run_id and account_id:
            _emit_dm_send_supabase_logs(
                run_id=run_id,
                account_id=account_id,
                username=username,
                send_out=send_out,
            )
        if send_out.get("reason") == "send_button_missing":
            cleanup_dm_after_send_button_missing(d)
        send_exit = _dm_send_exit_code(send_out)
        if send_exit is not None:
            log(
                "error",
                "run_aborted",
                reason=_exit_reason_from_code(send_exit),
                username=username,
                dm_send=send_out,
                exit_code=send_exit,
            )
            if send_exit == 19 and bool(getattr(config, "ENABLE_REAL_DM_SEND", False)):
                try:
                    return_to_profile_from_dm(d, username, pkg)
                except Exception:
                    pass
            _emit_performance_summary(
                t0=t0,
                warm_session_used=warm_session_used,
                force_stop_used=force_stop_used,
                exit_code=send_exit,
                target_username=username,
            )
            _emit_target_perf_compact(send_exit)
            return send_exit

        real_sent = bool(send_out.get("sent"))
        if real_sent:
            _RUNTIME_REAL_DM_SENT_COUNT += 1
            send_method = "instagram_send_ui"
            if send_out.get("coordinate_fallback_used"):
                send_method = "coordinate_fallback"
            elif send_out.get("send_button_position_fallback"):
                send_method = "position_fallback_tap"
            log(
                "info",
                "dm_send_sent_confirmed",
                username=username,
                thread_state=dm_state,
                send_method=send_method,
                message_len=len(draft_text),
            )
            if supabase_mode and run_id and account_id and target_id:
                _safe_supabase_call(
                    "insert_action_log",
                    run_id=run_id,
                    account_id=account_id,
                    target_username=username,
                    action_type="dm_send_business_mark_started",
                    status="started",
                    message="persist dm_sent business fields",
                    payload={
                        "target_username": username,
                        "run_id": run_id,
                        "thread_state": dm_state,
                        "send_method": send_method,
                        "message_len": len(draft_text),
                        "business_status": "mark_started",
                        "navigation_status_post_send": None,
                    },
                )
                biz = _safe_supabase_call(
                    "mark_target_dm_sent_business",
                    target_id,
                    run_id=run_id,
                    message_preview=draft_text[:500],
                    thread_state_at_send=dm_state,
                    send_method=send_method,
                    navigation_status_post_send="full",
                )
                _safe_supabase_call(
                    "insert_action_log",
                    run_id=run_id,
                    account_id=account_id,
                    target_username=username,
                    action_type="dm_send_business_mark_success"
                    if (biz or {}).get("ok")
                    else "dm_send_business_mark_failed",
                    status="success" if (biz or {}).get("ok") else "failed",
                    message="business mark result",
                    payload={
                        "target_username": username,
                        "run_id": run_id,
                        "thread_state": dm_state,
                        "send_method": send_method,
                        "message_len": len(draft_text),
                        "dm_sent": True,
                        "business_status": "dm_sent_persisted",
                        "mark_applied": (biz or {}).get("applied"),
                        "mark_error": (biz or {}).get("error"),
                    },
                )
            fin = finalize_after_real_send(
                d,
                username,
                pkg,
                use_fast_reset_between_targets=use_fast_reset_between_targets,
                pre_send_composer_text_len=int(
                    send_out.get("composer_text_len_before_send") or 0
                ),
            )
            nav_ok = bool(fin.get("back_to_profile_ok")) and bool(fin.get("back_to_search_ok"))
            set_perf_metric(
                "post_finalize_back_profile_ok", 1 if fin.get("back_to_profile_ok") else 0
            )
            set_perf_metric(
                "post_finalize_back_to_search_ok", 1 if fin.get("back_to_search_ok") else 0
            )
            if supabase_mode and target_id:
                _safe_supabase_call(
                    "update_target_post_send_navigation",
                    target_id,
                    navigation_status_post_send="full" if nav_ok else "partial",
                )
            if supabase_mode and run_id and account_id:
                _safe_supabase_call(
                    "insert_action_log",
                    run_id=run_id,
                    account_id=account_id,
                    target_username=username,
                    action_type="dm_send_post_finalize_summary",
                    status="success" if nav_ok else "warning",
                    message="post-send finalize",
                    payload={
                        "target_username": username,
                        "run_id": run_id,
                        "thread_state": dm_state,
                        "send_method": send_method,
                        "business_status": "sent_success_full"
                        if nav_ok
                        else "sent_success_navigation_partial",
                        "navigation_status_post_send": "full" if nav_ok else "partial",
                        "post_send_signal_ok": fin.get("post_send_signal_ok"),
                        "post_send_cleanup_reason": fin.get("post_send_cleanup_reason"),
                        "back_to_profile_ok": fin.get("back_to_profile_ok"),
                        "back_to_search_ok": fin.get("back_to_search_ok"),
                    },
                )
            exit_code = 0 if nav_ok else 21
            _emit_performance_summary(
                t0=t0,
                warm_session_used=warm_session_used,
                force_stop_used=force_stop_used,
                exit_code=exit_code,
                target_username=username,
            )
            _emit_target_perf_compact(exit_code)
            return exit_code

        if bool(getattr(config, "DM_CLEAR_DRAFT_AFTER_TEST", True)):
            if not clear_dm_draft(d):
                log("error", "run_aborted", reason="dm_draft_clear_failed", username=username)
                exit_code = 18
                _emit_performance_summary(
                    t0=t0,
                    warm_session_used=warm_session_used,
                    force_stop_used=force_stop_used,
                    exit_code=exit_code,
                    target_username=username,
                )
                _emit_target_perf_compact(exit_code)
                return exit_code
            if not finalize_dm_draft_before_back(d):
                log("error", "run_aborted", reason="dm_draft_clear_failed", username=username)
                exit_code = 18
                _emit_performance_summary(
                    t0=t0,
                    warm_session_used=warm_session_used,
                    force_stop_used=force_stop_used,
                    exit_code=exit_code,
                    target_username=username,
                )
                return exit_code

    dm_to_profile_phase_start = time.perf_counter()
    if not return_to_profile_from_dm(d, username, pkg):
        log("error", "run_aborted", reason="dm_back_to_profile_failed", username=username)
        exit_code = 12
        _emit_performance_summary(
            t0=t0,
            warm_session_used=warm_session_used,
            force_stop_used=force_stop_used,
            exit_code=exit_code,
            target_username=username,
        )
        _emit_target_perf_compact(exit_code)
        return exit_code
    snap_dm = get_perf_snapshot()
    draft_clear_ms = float(snap_dm.get("dm_draft_clear_ms", 0.0))
    finalize_before_back_ms = float(snap_dm.get("finalize_before_back_ms", 0.0))
    keyboard_hide_ms = float(snap_dm.get("keyboard_hide_ms", 0.0))
    back_press_ms = float(snap_dm.get("back_press_ms", 0.0))
    profile_detect_wait_ms = float(snap_dm.get("profile_detect_wait_ms", 0.0))
    total_dm_to_profile_phase_ms = (time.perf_counter() - dm_to_profile_phase_start) * 1000
    set_perf_metric("draft_to_back_profile_ms", total_dm_to_profile_phase_ms)
    set_perf_metric("total_dm_to_profile_phase_ms", total_dm_to_profile_phase_ms)
    log(
        "info",
        "dm_to_profile_phase_timing",
        draft_clear_ms=round(draft_clear_ms, 2),
        finalize_before_back_ms=round(finalize_before_back_ms, 2),
        keyboard_hide_ms=round(keyboard_hide_ms, 2),
        back_press_ms=round(back_press_ms, 2),
        profile_detect_wait_ms=round(profile_detect_wait_ms, 2),
        total_dm_to_profile_phase_ms=round(total_dm_to_profile_phase_ms, 2),
    )
    _log_phase_budget("dm_to_profile", total_dm_to_profile_phase_ms, 3000.0)
    t = _phase("dm_back_to_profile", dm_to_profile_phase_start)

    t_profile_search = time.perf_counter()
    if use_fast_reset_between_targets:
        navigation_strategy = "reset_between_targets"
        ok_reset = reset_to_search_for_next_target(d, pkg)
        if not ok_reset:
            log("error", "run_aborted", reason="profile_back_to_search_failed", username=username)
            exit_code = 13
            _emit_performance_summary(
                t0=t0,
                warm_session_used=warm_session_used,
                force_stop_used=force_stop_used,
                exit_code=exit_code,
                target_username=username,
            )
            _emit_target_perf_compact(exit_code)
            return exit_code
    else:
        navigation_strategy = "profile_back_to_search"
        if not return_to_search_from_profile(d, pkg):
            if open_search(d):
                log("info", "search_back_to_search_recovered_by_open_search", username=username)
            else:
                log("error", "run_aborted", reason="profile_back_to_search_failed", username=username)
                exit_code = 13
                _emit_performance_summary(
                    t0=t0,
                    warm_session_used=warm_session_used,
                    force_stop_used=force_stop_used,
                    exit_code=exit_code,
                    target_username=username,
                )
                _emit_target_perf_compact(exit_code)
                return exit_code
    profile_to_search_total_ms = (time.perf_counter() - t_profile_search) * 1000
    if navigation_strategy == "reset_between_targets":
        set_perf_metric("profile_to_search_ms", 0.0)
        set_perf_metric("reset_to_search_ms", profile_to_search_total_ms)
    else:
        set_perf_metric("profile_to_search_ms", profile_to_search_total_ms)
    set_perf_metric("profile_to_search_fast_path_total_ms", profile_to_search_total_ms)
    _log_phase_budget("profile_to_search", profile_to_search_total_ms, 3000.0)
    snap_gain = get_perf_snapshot()
    log(
        "info",
        "performance_gain_snapshot",
        profile_to_search_before_ms=6234.0,
        profile_to_search_after_ms=round(profile_to_search_total_ms, 2),
        dm_thread_detect_before_ms=4000.0,
        dm_thread_detect_after_ms=round(float(snap_gain.get("dm_thread_detect_ms", 0.0)), 2),
    )
    t = _phase("profile_back_to_search", t)

    log(
        "info",
        "run_success_stop",
        total_ms=round((time.perf_counter() - t0) * 1000, 2),
        target=username,
        dm_thread_state=dm_state,
        message="DM thread open/check complete; safe run complete (no typing/sending).",
    )
    _emit_performance_summary(
        t0=t0,
        warm_session_used=warm_session_used,
        force_stop_used=force_stop_used,
        exit_code=0,
        target_username=username,
    )
    _emit_target_perf_compact(0)
    return 0


def _norm_ig_handle(u: str) -> str:
    return (u or "").strip().lstrip("@").lower()


_IG_PUBLIC_HANDLE_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")


def _is_plausible_public_ig_username(raw: str) -> bool:
    """
    True for a likely public Instagram handle from list/hint/profile read.
    Rejects internal visual row placeholders (vfp_*, vf_row_*) and social-memory sentinels.
    """
    s = str(raw or "").strip().lstrip("@")
    if not s:
        return False
    if s.startswith("__visual"):
        return False
    if s.startswith("vfp_"):
        return False
    if s.startswith("vf_row_"):
        return False
    return bool(_IG_PUBLIC_HANDLE_RE.fullmatch(s))


def _ct_clear_candidate_attempt_timer() -> None:
    global _CT_CANDIDATE_ATTEMPT_STARTED_MONO, _CT_CANDIDATE_ATTEMPT_FKEY
    _CT_CANDIDATE_ATTEMPT_STARTED_MONO = None
    _CT_CANDIDATE_ATTEMPT_FKEY = None


def _ct_start_candidate_attempt_timer(fkey: str) -> None:
    """
    Start (or preserve) the attempt window from first username resolution for this fkey.
    Reopens of the same profile without a follow tap keep the original deadline.
    """
    global _CT_CANDIDATE_ATTEMPT_STARTED_MONO, _CT_CANDIDATE_ATTEMPT_FKEY
    fk = _norm_ig_handle(fkey)
    if not fk:
        return
    if (
        _CT_CANDIDATE_ATTEMPT_FKEY == fk
        and _CT_CANDIDATE_ATTEMPT_STARTED_MONO is not None
        and fk not in _RUNTIME_CT_LIST_FOLLOW_TAP_ATTEMPTED_USERNAMES
    ):
        return
    _CT_CANDIDATE_ATTEMPT_STARTED_MONO = time.monotonic()
    _CT_CANDIDATE_ATTEMPT_FKEY = fk


def _ct_candidate_follow_attempt_timed_out(fkey: str, *, timeout_s: float) -> bool:
    global _CT_CANDIDATE_ATTEMPT_STARTED_MONO, _CT_CANDIDATE_ATTEMPT_FKEY
    fk = _norm_ig_handle(fkey)
    if not fk or _CT_CANDIDATE_ATTEMPT_STARTED_MONO is None or _CT_CANDIDATE_ATTEMPT_FKEY != fk:
        return False
    if fk in _RUNTIME_CT_LIST_FOLLOW_TAP_ATTEMPTED_USERNAMES:
        return False
    if fk in _RUNTIME_FOLLOWED_USERNAMES:
        return False
    return (time.monotonic() - _CT_CANDIDATE_ATTEMPT_STARTED_MONO) >= float(timeout_s)


_POST_FOLLOW_RETURN_CT_SAFE_STOP_FAILURE_REASONS: frozenset[str] = frozenset(
    {
        "post_follow_return_ct_fast_abort_after_foreign_profile",
        "post_follow_return_ct_compact_abort_no_list_confirmed",
        "post_follow_return_ct_aborted_to_prevent_drift",
        "post_follow_return_ct_round_budget_exceeded",
        "post_follow_return_ct_compact_contract_violation",
    }
)


def _ct_return_followers_list_or_canonical_reset(
    d: Any,
    *,
    source_profile_username: str,
    pkg: str,
    account_id: str | None,
    reason: str,
) -> None:
    ok_r, how = return_to_followers_list(d, source_profile_username, pkg)
    if ok_r:
        log(
            "info",
            "visual_followers_timeout_returned_to_ct_list",
            reason=reason,
            how=how,
            source_profile_username=source_profile_username,
        )
        return
    log(
        "warning",
        "visual_followers_timeout_list_return_failed_canonical_reset",
        reason=reason,
        source_profile_username=source_profile_username,
    )
    reset_instagram_to_canonical_state(
        d,
        reason=f"ct_followers_{reason}",
        source_profile_username=source_profile_username,
        source_account_context=str(account_id or ""),
    )


def _ct_follow_out_had_tap_sent(follow_out: dict) -> bool:
    for e in follow_out.get("events") or []:
        if isinstance(e, (list, tuple)) and e and e[0] in (
            "follow_tap_sent",
            "follow_action_exact_follow_tap_sent",
        ):
            return True
    return False


def _ct_follow_out_visual_already_connected(
    follow_out: dict[str, Any],
) -> tuple[bool, str]:
    """
    True when the profile was already in a connected state and no new follow tap should
    trigger post-follow / mute / success accounting.
    """
    if bool(follow_out.get("skipped_tap")):
        return True, "skipped_tap"
    if bool(follow_out.get("already_following")):
        return True, "already_following"
    fs_b = str(follow_out.get("follow_state_before") or "").strip().lower()
    if fs_b in ("following", "requested"):
        return True, "follow_state_before_connected"
    fs_a = str(follow_out.get("follow_state_after") or "").strip().lower()
    if fs_a == "following" and not _ct_follow_out_had_tap_sent(follow_out):
        return True, "following_after_no_follow_tap_event"
    return False, ""


def _ct_follow_out_follow_started_no_tap(follow_out: dict) -> bool:
    if bool(follow_out.get("skipped_tap")):
        return False
    evs = list(follow_out.get("events") or [])
    started = any(e[0] == "follow_started" for e in evs)
    tapped = any(e[0] == "follow_tap_sent" for e in evs)
    return started and not tapped


def _ct_follow_out_is_failed_no_follow_tap(follow_out: dict) -> bool:
    if str(follow_out.get("failure_reason") or "") == "failed_no_follow_tap":
        return True
    return int(follow_out.get("failure_code") or 0) == 72


def _open_meta_visual_fallback_screenshot_path(meta: dict) -> str | None:
    """Path to the screenshot used for visual_fallback open detection, if present."""
    for key in ("last_poll_snapshot", "after_tap_screen_snapshot"):
        snap = meta.get(key) or {}
        vf = snap.get("visual_fallback_detail") or {}
        p = vf.get("screenshot_path_used") or vf.get("screenshot_path")
        if p:
            return str(p)
    return None


def _followers_try_refresh_injection_screenshot_after_scroll(
    d,
    open_list_meta: dict,
    *,
    source_profile_username: str,
    scroll_used: int,
    reason: str,
) -> None:
    """
    Capture the current followers list after a successful forward scroll so the next loop
    iteration's visual injection path does not reuse a pre-scroll bitmap.
    """
    out = (
        Path(__file__).resolve().parent
        / "logs"
        / "screenshots"
        / "followers_after_scroll_latest.png"
    )
    capture_ok = False
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    try:
        screenshot(d, str(out))
        capture_ok = bool(out.is_file())
    except Exception as _cap_exc:
        try:
            log(
                "warning",
                "followers_visual_injection_screenshot_refresh_failed",
                source_profile_username=source_profile_username,
                screenshot_path=str(out),
                scroll_used=int(scroll_used),
                reason=str(reason or ""),
                error=str(_cap_exc),
            )
        except Exception:
            pass
    if capture_ok:
        note_followers_injection_capture(
            open_list_meta,
            screenshot_path=str(out),
            capture_reason="post_scroll",
            source_profile_username=source_profile_username,
            scroll_used=int(scroll_used),
        )
    if capture_ok:
        try:
            from followers_inter_candidate_perf import (
                inter_candidate_segment_b_note_refresh_after_scroll,
            )

            inter_candidate_segment_b_note_refresh_after_scroll()
        except Exception:
            pass
    try:
        log(
            "info",
            "followers_visual_injection_screenshot_refreshed_after_scroll",
            source_profile_username=source_profile_username,
            screenshot_path=str(out),
            scroll_used=int(scroll_used),
            reason=str(reason or ""),
            capture_ok=bool(capture_ok),
        )
    except Exception:
        pass


def _followers_try_post_return_picker_injection_refresh(
    d,
    open_list_meta: dict,
    *,
    visual_loop_state: dict[str, Any],
    source_profile_username: str,
) -> None:
    """
    One-shot: after compact post-follow return, replace ``latest_followers_injection_screenshot_path``
    with a fresh capture taken shortly before the next candidate picker (distinct from the
    return-confirmation ``followers_visual_fallback_*.png`` frame).
    """
    if not bool(visual_loop_state.get("post_return_picker_refresh_pending")):
        return
    meta_arm = visual_loop_state.get("post_return_picker_refresh_meta")
    if not isinstance(meta_arm, dict):
        meta_arm = {}
    prior = ""
    if isinstance(open_list_meta, dict):
        prior = str(open_list_meta.get("latest_followers_injection_screenshot_path") or "").strip()
    _skip_cap, _skip_meta = should_skip_post_return_injection_capture(
        open_list_meta,
        visual_loop_state,
        source_profile_username=source_profile_username,
    )
    if _skip_cap:
        visual_loop_state["post_return_picker_refresh_pending"] = False
        visual_loop_state.pop("post_return_picker_refresh_meta", None)
        try:
            from followers_inter_candidate_perf import (
                inter_candidate_segment_b_note_evidence_source,
                inter_candidate_segment_b_pre_picker_injection_capture_end,
            )

            inter_candidate_segment_b_pre_picker_injection_capture_end()
            inter_candidate_segment_b_note_evidence_source(source="post_return_injection")
        except Exception:
            pass
        try:
            log(
                "info",
                "followers_post_return_injection_capture_skipped_evidence_fresh",
                source_profile_username=source_profile_username,
                evidence_age_ms=_skip_meta.get("evidence_age_ms"),
                evidence_capture_reason=str(
                    _skip_meta.get("evidence_capture_reason") or ""
                )[:80],
                screenshot_path=str(_skip_meta.get("screenshot_path") or "")[:400],
                reason="promoted_return_evidence_fresh",
            )
        except Exception:
            pass
        return
    try:
        from followers_inter_candidate_perf import (
            inter_candidate_segment_b_pre_picker_injection_capture_start,
        )

        inter_candidate_segment_b_pre_picker_injection_capture_start()
    except Exception:
        pass
    settle_raw = float(
        getattr(config, "FOLLOWERS_POST_RETURN_PICKER_INJECTION_SETTLE_S", 0.75) or 0.75
    )
    settle_s = max(0.6, min(1.0, settle_raw))
    try:
        from followers_inter_candidate_perf import inter_candidate_on_picker_refresh_settle

        inter_candidate_on_picker_refresh_settle(settle_s=settle_s)
    except Exception:
        pass
    try:
        time.sleep(settle_s)
    except Exception:
        pass
    out = (
        Path(__file__).resolve().parent
        / "logs"
        / "screenshots"
        / "followers_after_post_return_latest.png"
    )
    capture_ok = False
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    try:
        screenshot(d, str(out))
        capture_ok = bool(out.is_file())
    except Exception as _cap_exc:
        try:
            log(
                "warning",
                "followers_post_return_picker_injection_screenshot_refresh_failed",
                source_profile_username=source_profile_username,
                screenshot_path=str(out),
                prior_promoted_return_screenshot_path=prior[:400] if prior else "",
                refresh_reason="post_follow_return_before_next_picker",
                settle_s=round(settle_s, 3),
                error=str(_cap_exc),
            )
        except Exception:
            pass
    if capture_ok:
        note_followers_injection_capture(
            open_list_meta,
            screenshot_path=str(out),
            capture_reason="post_return",
            source_profile_username=source_profile_username,
            scroll_used=0,
            visual_loop_state=visual_loop_state,
        )
    try:
        from followers_inter_candidate_perf import (
            inter_candidate_segment_b_pre_picker_injection_capture_end,
        )

        inter_candidate_segment_b_pre_picker_injection_capture_end()
    except Exception:
        pass
    if capture_ok:
        try:
            from followers_inter_candidate_perf import (
                inter_candidate_segment_b_note_evidence_source,
            )

            inter_candidate_segment_b_note_evidence_source(
                source="post_return_injection"
            )
        except Exception:
            pass
    try:
        log(
            "info",
            "followers_post_return_picker_injection_screenshot_refreshed",
            source_profile_username=source_profile_username,
            screenshot_path=str(out),
            prior_promoted_return_screenshot_path=prior[:400] if prior else "",
            refresh_reason="post_follow_return_before_next_picker",
            settle_s=round(settle_s, 3),
            capture_ok=bool(capture_ok),
            visual_candidate_id=str(meta_arm.get("visual_candidate_id") or "")[:120],
            follower_username=str(meta_arm.get("follower_username") or "")[:120],
            return_method=str(meta_arm.get("return_method") or "")[:120],
        )
    except Exception:
        pass
    visual_loop_state["post_return_picker_refresh_pending"] = False
    visual_loop_state.pop("post_return_picker_refresh_meta", None)


def _followers_injection_screenshot_path(
    open_list_meta: dict,
    session_vf: dict | None,
) -> str | None:
    """
    Resolve a followers-list screenshot for visual row mapping without fresh XML.
    Order: latest post-scroll capture (open_list_meta) → session visual_fallback_detail
    → open meta snapshots → tap_diag capture → canonical after-tap file.
    """
    latest = open_list_meta.get("latest_followers_injection_screenshot_path")
    if isinstance(latest, str):
        latest_s = latest.strip()
        if latest_s and os.path.isfile(latest_s):
            return latest_s
    if isinstance(session_vf, dict):
        for k in ("screenshot_path_used", "screenshot_path"):
            p = session_vf.get(k)
            if p and os.path.isfile(str(p)):
                return str(p)
    p2 = _open_meta_visual_fallback_screenshot_path(open_list_meta)
    if p2 and os.path.isfile(str(p2)):
        return str(p2)
    td = open_list_meta.get("tap_diag")
    if isinstance(td, dict):
        p3 = td.get("followers_after_tap_immediate_screenshot_path")
        if p3 and os.path.isfile(str(p3)):
            return str(p3)
    p4 = Path(__file__).resolve().parent / "logs" / "screenshots" / "followers_after_tap_immediate.png"
    if p4.is_file():
        return str(p4)
    return None


def _followers_row_mapping_skip_reasons_only_tap_y_outside_safe_vertical_band(sk: object) -> bool:
    """
    True when row-mapping skips are exclusively ``tap_y_outside_safe_vertical_band`` counts
    (no mixed skip reasons). Used for a narrow XML-stale defer toward exploratory scroll.
    """
    if not isinstance(sk, dict) or not sk:
        return False
    if set(sk.keys()) != {"tap_y_outside_safe_vertical_band"}:
        return False
    return int(sk.get("tap_y_outside_safe_vertical_band") or 0) >= 1


def _followers_det_skip_redetect_after_visual_bypass(
    cached_det: dict[str, Any],
    session_vf: dict[str, Any] | None,
    open_detection_method: str,
) -> dict[str, Any]:
    """
    Reuse last full detect_* payload but refresh visual_fallback_detail from session
    so one iteration can skip a redundant stale XML poll.
    """
    out = dict(cached_det)
    odm = str(open_detection_method or "xml")
    if odm not in FOLLOWERS_ENGINE_VISUAL_OPEN_METHODS and isinstance(session_vf, dict) and bool(
        session_vf.get("visual_match")
    ):
        odm = "visual_fallback"
    out["open_detection_method"] = odm
    if isinstance(session_vf, dict) and session_vf:
        out["visual_fallback_detail"] = dict(session_vf)
    return out


def _open_meta_visual_fallback_list_was_open(meta: dict) -> bool:
    """True when open success payload shows followers list validated via visual_fallback."""
    if str(meta.get("open_detection_method") or "") not in FOLLOWERS_ENGINE_VISUAL_OPEN_METHODS:
        return False
    for key in ("last_poll_snapshot", "after_tap_screen_snapshot"):
        snap = meta.get(key) or {}
        if bool(snap.get("is_followers_list")):
            return True
        vf = snap.get("visual_fallback_detail") or {}
        if bool(vf.get("visual_match")):
            return True
    return False


def _try_visual_followers_picker_dry_run(
    d,
    *,
    open_list_meta: dict,
    source_profile_username: str,
    source_account_context: str | None,
    run_id: str | None,
    t0: float,
    warm_session_used: bool,
    force_stop_used: bool,
    eng_log: Callable[..., None],
    stop_reason: str | None = None,
    candidates: list | None = None,
    iteration: int | None = None,
    scroll_used: int = 0,
    runtime_seen: set | None = None,
) -> int | None:
    """
    Visual screenshot candidate extraction + dry-run exit 45 (or 46 after one profile-open tap).
    Returns VISUAL_FOLLOW_HISTORY_CONTINUE when a private-filter skip should resume the followers loop.
    Returns None if picker disabled, not dry-run, not visual_fallback open proof, or no screenshot.
    """
    global _VISUAL_FOLLOWERS_OPEN_COUNT_THIS_SESSION
    history_recorded_follow_target = False
    history_recorded_target_username = ""
    if not bool(getattr(config, "ENABLE_VISUAL_FOLLOWERS_CANDIDATE_PICKER", False)):
        return None
    if not bool(getattr(config, "VISUAL_FOLLOWERS_PICKER_DRY_RUN", True)):
        return None
    if str(open_list_meta.get("open_detection_method") or "") not in FOLLOWERS_ENGINE_VISUAL_OPEN_METHODS:
        return None
    if not _open_meta_visual_fallback_list_was_open(open_list_meta):
        return None

    shot = _open_meta_visual_fallback_screenshot_path(open_list_meta)
    if not shot:
        log(
            "warning",
            "visual_followers_picker_skipped_no_screenshot",
            source_profile_username=source_profile_username,
            source_account_context=source_account_context,
            stop_reason=stop_reason,
            iteration=iteration,
            scroll_used=scroll_used,
            open_detection_method=open_list_meta.get("open_detection_method"),
        )
        return None

    log(
        "info",
        "visual_followers_picker_restart_requested",
        source_profile_username=source_profile_username,
        stop_reason=stop_reason,
        iteration=iteration,
        scroll_used=scroll_used,
    )

    _action_acct = str(
        getattr(config, "VISUAL_FOLLOWERS_ACTION_ACCOUNT_USERNAME", "") or ""
    ).strip()
    vpick = visual_extract_followers_candidates_from_screenshot(
        d,
        screenshot_path=shot,
        source_profile_username=source_profile_username,
        runtime_seen=runtime_seen,
        action_account_username=_action_acct if _action_acct else None,
        phase="visual_followers_picker_restart",
    )
    try:
        log(
            "info",
            "followers_candidate_picker_returned",
            candidate_count=int(vpick.get("candidate_count") or 0),
            picker_scroll_rounds=int(vpick.get("picker_scroll_rounds") or 0),
            source_profile_username=source_profile_username,
            screenshot_path=str(shot),
            picker_error=str(vpick.get("picker_error") or ""),
        )
        if int(vpick.get("candidate_count") or 0) == 0:
            try:
                from followers_inter_candidate_perf import inter_candidate_on_picker_empty

                inter_candidate_on_picker_empty()
            except Exception:
                pass
    except Exception:
        pass
    cands = list(vpick.get("candidates") or [])
    min_conf = float(getattr(config, "FOLLOWERS_VISUAL_FALLBACK_MIN_CONFIDENCE", 0.65))
    open_enabled = bool(getattr(config, "ENABLE_VISUAL_FOLLOWERS_CANDIDATE_OPEN", False))
    open_dry = bool(getattr(config, "VISUAL_FOLLOWERS_OPEN_DRY_RUN", True))
    max_open = int(getattr(config, "VISUAL_FOLLOWERS_OPEN_MAX_PER_RUN", 1) or 1)

    if (
        open_enabled
        and open_dry
        and cands
        and max_open > _VISUAL_FOLLOWERS_OPEN_COUNT_THIS_SESSION
    ):
        ordered = sorted(
            cands,
            key=lambda x: int((x.get("approx_row_bounds") or {}).get("top") or 10**6),
        )
        best = None
        selection_reason = ""
        for c in ordered:
            cf = float(c.get("confidence") or 0)
            if cf < min_conf:
                rb = dict(c.get("approx_row_bounds") or {})
                log(
                    "info",
                    "visual_followers_candidate_skipped",
                    source_profile_username=source_profile_username,
                    visual_candidate_id=c.get("visual_candidate_id"),
                    row_index=c.get("row_index"),
                    span_index=c.get("span_index"),
                    top=rb.get("top"),
                    bottom=rb.get("bottom"),
                    confidence=c.get("confidence"),
                    skip_reason="confidence_below_min_open_threshold",
                    min_confidence=min_conf,
                )
                continue
            hist_hint = str(c.get("resolved_username_hint") or "").strip().lstrip("@")
            hist_skip = visual_follow_history_skip_reason_if_any(
                source_profile_username=source_profile_username,
                target_username_hint=hist_hint,
                source_account_context=str(source_account_context or ""),
            )
            if hist_skip:
                rbh = dict(c.get("approx_row_bounds") or {})
                log(
                    "info",
                    "visual_followers_candidate_skipped",
                    source_profile_username=source_profile_username,
                    visual_candidate_id=c.get("visual_candidate_id"),
                    row_index=c.get("row_index"),
                    span_index=c.get("span_index"),
                    top=rbh.get("top"),
                    bottom=rbh.get("bottom"),
                    confidence=c.get("confidence"),
                    resolved_username_hint=hist_hint,
                    skip_reason=hist_skip,
                )
                continue
            best = c
            selection_reason = (
                "first_valid_after_source_account_exclusion"
                if _action_acct
                else "first_eligible_row_top_to_bottom"
            )
            break
        if best:
            _VISUAL_FOLLOWERS_OPEN_COUNT_THIS_SESSION += 1
            rb_sel = dict(best.get("approx_row_bounds") or {})
            log(
                "info",
                "visual_follower_candidate_selected_for_open",
                source_profile_username=source_profile_username,
                visual_candidate_id=best.get("visual_candidate_id"),
                row_index=best.get("row_index"),
                span_index=best.get("span_index"),
                top=rb_sel.get("top"),
                bottom=rb_sel.get("bottom"),
                confidence=best.get("confidence"),
                selection_reason=selection_reason,
                resolved_username_hint=best.get("resolved_username_hint") or "",
            )
            visual_target_profile_lock_clear()
            open_out = open_visual_follower_candidate_from_screenshot(
                d,
                best,
                source_profile_username=source_profile_username,
            )
            log(
                "info",
                "visual_follower_open_dry_run_complete",
                source_profile_username=source_profile_username,
                profile_detected=open_out.get("profile_detected"),
                failure_reason=open_out.get("failure_reason"),
                tap_x=open_out.get("tap_x"),
                tap_y=open_out.get("tap_y"),
                visual_candidate_id=open_out.get("visual_candidate_id"),
                current_package=open_out.get("current_package"),
                current_activity=open_out.get("current_activity"),
            )
            eng_log(
                "visual_follower_open_dry_run_complete",
                "success" if open_out.get("profile_detected") else "failed",
                "visual_follower_profile_opened_dry_run",
                {
                    "profile_detected": open_out.get("profile_detected"),
                    "failure_reason": open_out.get("failure_reason"),
                    "tap_x": open_out.get("tap_x"),
                    "tap_y": open_out.get("tap_y"),
                    "visual_candidate_id": open_out.get("visual_candidate_id"),
                },
            )
            priv_detect_runner: dict = {}
            skip_post_private = False
            profile_expected_ctx = None
            zero_posts_hard_skip = False
            if open_out.get("profile_detected"):
                priv_detect_runner = visual_detect_private_profile(
                    d, source_profile_username=source_profile_username
                )
                skip_post_private = bool(
                    priv_detect_runner.get("private_profile_detected")
                )
                if (
                    bool(getattr(config, "ENABLE_PRIVATE_ACCOUNT_FILTER", False))
                    and not bool(getattr(config, "FOLLOW_PRIVATE_ACCOUNTS", False))
                    and skip_post_private
                ):
                    log(
                        "info",
                        "visual_private_profile_skipped",
                        source_profile_username=source_profile_username,
                        detection_method=priv_detect_runner.get("detection_method"),
                        confidence=round(
                            float(priv_detect_runner.get("confidence") or 0.0),
                            4,
                        ),
                        current_activity=priv_detect_runner.get("current_activity"),
                        current_package=priv_detect_runner.get("current_package"),
                        reason="private_account_filter_follow_private_disabled",
                    )
                    tgt_sk = str(best.get("resolved_username_hint") or "").strip().lstrip(
                        "@"
                    )
                    if not tgt_sk:
                        cap_priv = visual_capture_profile_context(
                            d, source_profile_username=source_profile_username
                        )
                        tgt_sk = str(
                            cap_priv.get("header_username_detected") or ""
                        ).strip().lstrip("@")
                    mark_visual_follow_target_processed(
                        source_profile_username=source_profile_username,
                        target_username=tgt_sk,
                        visual_candidate_id=str(best.get("visual_candidate_id") or ""),
                        status="skipped_private",
                        follow_verified=False,
                        follow_request_pending=False,
                        run_id=str(run_id or ""),
                        source_account_context=str(source_account_context or ""),
                        metadata={
                            "skip_reason": "private_account_filter_follow_private_disabled",
                        },
                    )
                    visual_target_profile_lock_clear()
                    pkg_priv = config.INSTAGRAM_PACKAGE
                    ok_priv_back, how_priv = return_to_followers_list(
                        d, source_profile_username, pkg_priv
                    )
                    log(
                        "info",
                        "visual_private_profile_skipped_continue",
                        source_profile_username=source_profile_username,
                        target_username=tgt_sk,
                        return_ok=ok_priv_back,
                        how=how_priv,
                        reason="private_account_filter_follow_private_disabled",
                    )
                    _VISUAL_FOLLOWERS_OPEN_COUNT_THIS_SESSION = max(
                        0, _VISUAL_FOLLOWERS_OPEN_COUNT_THIS_SESSION - 1
                    )
                    if not ok_priv_back:
                        _emit_performance_summary(
                            t0=t0,
                            warm_session_used=warm_session_used,
                            force_stop_used=force_stop_used,
                            exit_code=42,
                            target_username=source_profile_username,
                        )
                        return 42
                    return VISUAL_FOLLOW_HISTORY_CONTINUE
                if bool(getattr(config, "ENABLE_VISUAL_PROFILE_CONTEXT_LOCK", False)):
                    profile_expected_ctx = visual_capture_profile_context(
                        d, source_profile_username=source_profile_username
                    )

                gate_un = str(
                    (profile_expected_ctx or {}).get("header_username_detected") or ""
                ).strip().lstrip("@")
                if not gate_un:
                    gate_un = str(
                        best.get("resolved_username_hint") or ""
                    ).strip().lstrip("@")
                if not gate_un:
                    gate_un = str(
                        read_current_profile_username_for_follow_gate(d) or ""
                    ).strip().lstrip("@")
                if gate_un:
                    best = dict(best)
                    best["resolved_username_hint"] = gate_un
                    if profile_expected_ctx is not None:
                        profile_expected_ctx = {
                            **dict(profile_expected_ctx),
                            "header_username_detected": gate_un,
                        }
                    else:
                        profile_expected_ctx = {"header_username_detected": gate_un}
                    log(
                        "info",
                        "visual_followers_username_resolved_on_profile",
                        follower_username=gate_un,
                        source_profile_username=source_profile_username,
                        visual_candidate_id=best.get("visual_candidate_id"),
                        phase="picker_dry_run",
                    )

                if bool(getattr(config, "ENABLE_VISUAL_POST_LIKE_BEFORE_FOLLOW", False)):
                    zp_ct = visual_profile_stats_posts_count(d)
                    if zp_ct is not None and int(zp_ct) == 0:
                        zero_posts_hard_skip = True
                        meta_zp = _followers_current_pkg_activity(d)
                        log(
                            "info",
                            "visual_profile_zero_posts_hard_skip",
                            posts_count=0,
                            skip_post_open=True,
                            skip_like=True,
                            skip_mute=True,
                            source_profile_username=source_profile_username,
                            current_activity=meta_zp.get("current_activity"),
                            current_package=meta_zp.get("current_package"),
                        )

            if open_out.get("profile_detected"):
                if bool(getattr(config, "ENABLE_VISUAL_PROFILE_METRICS_FILTER", False)):
                    metrics_pf = visual_extract_profile_metrics(
                        d, source_profile_username=source_profile_username
                    )
                    pass_m, reason_m = visual_profile_metrics_pass_filter(metrics_pf)
                    if not pass_m:
                        log(
                            "warning",
                            "visual_profile_metrics_filter_skipped",
                            filter_reason=reason_m,
                            posts_count=metrics_pf.get("posts_count"),
                            followers_count=metrics_pf.get("followers_count"),
                            following_count=metrics_pf.get("following_count"),
                            username_norm=metrics_pf.get("username_norm"),
                            source_profile_username=source_profile_username,
                            current_activity=metrics_pf.get("current_activity"),
                            current_package=metrics_pf.get("current_package"),
                        )
                        tgt_m = str(
                            (profile_expected_ctx or {}).get("header_username_detected")
                            or ""
                        ).strip().lstrip("@")
                        if not tgt_m:
                            tgt_m = str(
                                best.get("resolved_username_hint") or ""
                            ).strip().lstrip("@")
                        if not tgt_m:
                            cap_m = visual_capture_profile_context(
                                d, source_profile_username=source_profile_username
                            )
                            tgt_m = str(
                                cap_m.get("header_username_detected") or ""
                            ).strip().lstrip("@")
                        if tgt_m:
                            mark_visual_follow_target_processed(
                                source_profile_username=source_profile_username,
                                target_username=tgt_m,
                                visual_candidate_id=str(
                                    best.get("visual_candidate_id") or ""
                                ),
                                status="skipped_metrics_filter",
                                follow_verified=False,
                                follow_request_pending=False,
                                run_id=str(run_id or ""),
                                source_account_context=str(source_account_context or ""),
                                metadata={
                                    "filter_reason": reason_m,
                                    "posts_count": metrics_pf.get("posts_count"),
                                    "followers_count": metrics_pf.get("followers_count"),
                                    "following_count": metrics_pf.get("following_count"),
                                },
                            )
                        visual_target_profile_lock_clear()
                        pkg_m = config.INSTAGRAM_PACKAGE
                        ok_m_back, how_m = return_to_followers_list(
                            d, source_profile_username, pkg_m
                        )
                        log(
                            "info",
                            "visual_profile_metrics_skipped_return_followers",
                            source_profile_username=source_profile_username,
                            target_username=tgt_m,
                            return_ok=ok_m_back,
                            how=how_m,
                            filter_reason=reason_m,
                        )
                        _VISUAL_FOLLOWERS_OPEN_COUNT_THIS_SESSION = max(
                            0, _VISUAL_FOLLOWERS_OPEN_COUNT_THIS_SESSION - 1
                        )
                        if not ok_m_back:
                            _emit_performance_summary(
                                t0=t0,
                                warm_session_used=warm_session_used,
                                force_stop_used=force_stop_used,
                                exit_code=42,
                                target_username=source_profile_username,
                            )
                            return 42
                        return VISUAL_FOLLOW_HISTORY_CONTINUE

            if open_out.get("profile_detected"):
                post_like_before = bool(
                    getattr(config, "ENABLE_VISUAL_POST_LIKE_BEFORE_FOLLOW", False)
                ) and bool(getattr(config, "ENABLE_VISUAL_POST_LIKE_FLOW", False))
                _post_like_hard_lock_mismatch = False
                post_follow_context_blocked = False
                post_like_failed = False
                exit_code = 48
                fr_out = None
                if not post_like_before:
                    log(
                        "info",
                        "visual_post_like_flow_bypassed_for_stability",
                        source_profile_username=source_profile_username,
                        ENABLE_VISUAL_POST_LIKE_BEFORE_FOLLOW=bool(
                            getattr(
                                config,
                                "ENABLE_VISUAL_POST_LIKE_BEFORE_FOLLOW",
                                False,
                            )
                        ),
                        ENABLE_VISUAL_POST_LIKE_FLOW=bool(
                            getattr(config, "ENABLE_VISUAL_POST_LIKE_FLOW", False)
                        ),
                    )
                    post_out = {
                        "ok": True,
                        "post_detected": False,
                        "private_profile": False,
                        "no_posts_profile": False,
                        "failure_reason": None,
                        "source_profile_username": source_profile_username,
                    }
                    like_out = {
                        "ok": True,
                        "skipped": True,
                        "stability_bypass": True,
                    }
                    back_out = {
                        "ok": True,
                        "skipped": True,
                        "profile_detected": True,
                    }

                def _expected_target_username_from_ctx() -> str:
                    t = str(
                        (profile_expected_ctx or {}).get("header_username_detected")
                        or ""
                    ).strip().lstrip("@")
                    if t:
                        return t
                    return str(best.get("resolved_username_hint") or "").strip().lstrip(
                        "@"
                    )

                def _follower_candidate_payload() -> dict:
                    tx = open_out.get("tap_x")
                    ty = open_out.get("tap_y")
                    un = _expected_target_username_from_ctx()
                    rc: list[int]
                    if tx is not None and ty is not None:
                        rc = [int(tx), int(ty)]
                    else:
                        rb = dict(best.get("approx_row_bounds") or {})
                        try:
                            w, _h = d.window_size()
                            mx = int(w) // 2
                        except Exception:
                            mx = 360
                        top = int(rb.get("top") or 0)
                        bot = int(rb.get("bottom") or 0)
                        rc = [mx, (top + bot) // 2]
                    return {"username": un, "row_center": rc}

                def _follow_surface_ok_for_dry_run(
                    expected: str,
                ) -> tuple[bool, str, str]:
                    src_n = _norm_ig_handle(source_profile_username)
                    exp_n = _norm_ig_handle(expected)
                    cur_n = _norm_ig_handle(
                        read_current_profile_username_for_follow_gate(d)
                    )
                    if cur_n == src_n:
                        return False, cur_n, "on_source_ct_profile_not_target"
                    if not exp_n:
                        if cur_n:
                            return True, cur_n, "ok_expected_from_profile_gate_only"
                        return False, cur_n, "missing_expected_target_username"
                    if cur_n != exp_n:
                        return False, cur_n, "current_username_mismatch_expected"
                    return True, cur_n, "ok"

                def _fr_out_follow_blocked_stub(
                    reason: str,
                ) -> dict:
                    return {
                        "ok": False,
                        "follow_button_detected": False,
                        "follow_verified": False,
                        "real_follow_tap_sent": False,
                        "skip_already_following": False,
                        "profile_context_mismatch": True,
                        "target_profile_lock_mismatch": False,
                        "failure_reason": reason,
                        "detected_button_text": "",
                        "follow_button_visible_after_verify": False,
                        "follow_request_pending": False,
                        "follow_request_pending_method": "",
                        "tap_x": None,
                        "tap_y": None,
                        "follow_button_bounds": None,
                        "dry_run": True,
                    }

                if not post_like_before:
                    log(
                        "info",
                        "visual_follow_stability_flow_started",
                        source_profile_username=source_profile_username,
                    )

                if post_like_before:
                    post_follow_context_blocked = False
                    if skip_post_private:
                        meta_priv = _followers_current_pkg_activity(d)
                        log(
                            "info",
                            "visual_post_like_skipped_private_profile",
                            source_profile_username=source_profile_username,
                            current_activity=meta_priv.get("current_activity"),
                            current_package=meta_priv.get("current_package"),
                            private_detection_method=priv_detect_runner.get(
                                "detection_method"
                            ),
                            confidence=round(
                                float(priv_detect_runner.get("confidence") or 0.0),
                                4,
                            ),
                        )
                        post_out = {
                            "ok": True,
                            "post_detected": False,
                            "private_profile": True,
                            "no_posts_profile": False,
                            "failure_reason": None,
                            "source_profile_username": source_profile_username,
                        }
                        like_out = {
                            "ok": True,
                            "skipped": True,
                            "private_profile": True,
                        }
                        back_out = {
                            "ok": True,
                            "skipped": True,
                            "profile_detected": True,
                        }
                    elif zero_posts_hard_skip:
                        post_out = {
                            "ok": True,
                            "post_detected": False,
                            "private_profile": False,
                            "no_posts_profile": True,
                            "no_posts_detection_method": "stats_strip_posts_zero_hard_skip",
                            "no_posts_confidence": 1.0,
                            "failure_reason": None,
                            "source_profile_username": source_profile_username,
                        }
                    else:
                        post_out = visual_open_recent_post_from_profile(
                            d, source_profile_username=source_profile_username
                        )
                    post_follow_context_blocked = False
                _pr_fail = str(post_out.get("failure_reason") or "")
                if (
                    not bool(post_out.get("post_detected"))
                    and _pr_fail == "post_viewer_not_detected"
                ):
                    meta_rs = _followers_current_pkg_activity(d)
                    tap_sent = (
                        post_out.get("tap_x") is not None
                        and post_out.get("tap_y") is not None
                    )

                    def _post_open_fail_log_kwargs(
                        *,
                        meta_pkg: dict,
                        tv: dict | None,
                        phase: str | None,
                    ) -> dict:
                        if tv is None:
                            tlk = None
                        elif isinstance(tv, dict) and tv.get("skipped"):
                            tlk = True
                        elif isinstance(tv, dict):
                            tlk = bool(tv.get("ok"))
                        else:
                            tlk = None
                        return {
                            "phase": phase,
                            "tap_x": post_out.get("tap_x"),
                            "tap_y": post_out.get("tap_y"),
                            "source_profile_username": source_profile_username,
                            "current_activity": meta_pkg.get("current_activity"),
                            "current_package": meta_pkg.get("current_package"),
                            "target_lock_ok": tlk,
                            "username_norm": "",
                        }

                    log(
                        "info",
                        "visual_recent_post_open_failed_recovery_started",
                        failure_reason=_pr_fail,
                        post_detected=bool(post_out.get("post_detected")),
                        **_post_open_fail_log_kwargs(
                            meta_pkg=meta_rs,
                            tv=None,
                            phase=(
                                "back_before_target_lock_verify"
                                if tap_sent
                                else "target_lock_verify_only_no_tap"
                            ),
                        ),
                    )

                    if tap_sent:
                        log(
                            "info",
                            "visual_recent_post_open_failed_back_sent",
                            **_post_open_fail_log_kwargs(
                                meta_pkg=meta_rs,
                                tv=None,
                                phase="before_navigate_back_from_wrong_surface",
                            ),
                        )
                        try:
                            d.press("back")
                        except Exception:
                            pass
                        time.sleep(1.2)
                        tv_post = visual_target_profile_lock_verify(
                            d,
                            source_profile_username=source_profile_username,
                            action=(
                                "after_recent_post_open_failed_after_back"
                            ),
                        )
                        meta_rb = _followers_current_pkg_activity(d)
                        if not tv_post.get("ok"):
                            post_follow_context_blocked = True
                            log(
                                "info",
                                "visual_recent_post_open_failed_recovery_failed",
                                **_post_open_fail_log_kwargs(
                                    meta_pkg=meta_rb,
                                    tv=tv_post,
                                    phase="after_back_target_lock_verify",
                                ),
                            )
                            log(
                                "info",
                                "visual_follow_blocked_after_post_open_failed_context_mismatch",
                                **_post_open_fail_log_kwargs(
                                    meta_pkg=meta_rb,
                                    tv=tv_post,
                                    phase="after_back_target_lock_verify",
                                ),
                            )
                        else:
                            log(
                                "info",
                                "visual_recent_post_open_failed_recovery_success",
                                **_post_open_fail_log_kwargs(
                                    meta_pkg=meta_rb,
                                    tv=tv_post,
                                    phase="after_back_target_lock_verify",
                                ),
                            )
                    else:
                        tv_pre = visual_target_profile_lock_verify(
                            d,
                            source_profile_username=source_profile_username,
                            action=(
                                "after_recent_post_open_failed_before_follow"
                            ),
                        )
                        if not tv_pre.get("ok"):
                            post_follow_context_blocked = True
                            log(
                                "info",
                                "visual_recent_post_open_failed_recovery_failed",
                                **_post_open_fail_log_kwargs(
                                    meta_pkg=meta_rs,
                                    tv=tv_pre,
                                    phase="initial_target_lock_verify",
                                ),
                            )
                            log(
                                "info",
                                "visual_follow_blocked_after_post_open_failed_context_mismatch",
                                **_post_open_fail_log_kwargs(
                                    meta_pkg=meta_rs,
                                    tv=tv_pre,
                                    phase="initial_target_lock_verify",
                                ),
                            )
                        else:
                            log(
                                "info",
                                "visual_recent_post_open_failed_recovery_success",
                                **_post_open_fail_log_kwargs(
                                    meta_pkg=meta_rs,
                                    tv=tv_pre,
                                    phase="initial_target_lock_only",
                                ),
                            )

                like_out: dict = {"ok": False, "skipped": True}
                back_out: dict = {"ok": True, "skipped": True}
                if not post_out.get("ok"):
                    if _pr_fail == "post_viewer_not_detected":
                        like_out = {
                            "ok": False,
                            "skipped": True,
                            "failure_reason": "post_viewer_not_detected",
                        }
                elif post_out.get("no_posts_profile"):
                    meta_np = _followers_current_pkg_activity(d)
                    log(
                        "info",
                        "visual_post_like_skipped_no_posts_profile",
                        source_profile_username=source_profile_username,
                        current_activity=meta_np.get("current_activity"),
                        current_package=meta_np.get("current_package"),
                        detection_method=post_out.get("no_posts_detection_method"),
                        confidence=round(
                            float(post_out.get("no_posts_confidence") or 0.0),
                            4,
                        ),
                    )
                    like_out = {
                        "ok": True,
                        "skipped": True,
                        "no_posts_profile": True,
                    }
                    back_out = {
                        "ok": True,
                        "skipped": True,
                        "profile_detected": True,
                    }
                elif post_out.get("post_detected"):
                    like_out = visual_like_open_post(
                        d,
                        source_profile_username=source_profile_username,
                        expected_profile_context=profile_expected_ctx,
                        post_opened_via_profile_grid=bool(
                            post_out.get("post_detected")
                        ),
                    )
                    if (
                        bool(getattr(config, "ENABLE_REAL_VISUAL_POST_LIKE", False))
                        and like_out.get("ok")
                        and like_out.get("real_tap_sent")
                    ):
                        if bool(
                            getattr(config, "VISUAL_POST_LIKE_VERIFY_AFTER_TAP", True)
                        ):
                            ver = visual_verify_post_liked(
                                d,
                                source_profile_username=source_profile_username,
                                tap_x=like_out.get("tap_x"),
                                tap_y=like_out.get("tap_y"),
                                detect_confidence=like_out.get("confidence"),
                                post_tap_screenshot_path=like_out.get(
                                    "post_tap_screenshot_path"
                                ),
                                like_button_state_before=like_out.get(
                                    "like_button_state_before"
                                ),
                                pre_tap_like_button_bounds=like_out.get(
                                    "like_button_bounds"
                                ),
                            )
                            like_out["liked_verified"] = bool(
                                ver.get("liked_verified")
                            )
                            like_out["verification_method"] = ver.get(
                                "verification_method"
                            )
                            like_out["verify_confidence"] = float(
                                ver.get("confidence") or 0.0
                            )
                        else:
                            like_out["liked_verified"] = True
                            like_out["verification_method"] = "verify_disabled"
                            like_out["verify_confidence"] = 1.0
                        meta_rc = _followers_current_pkg_activity(d)
                        log(
                            "info",
                            "visual_post_like_real_complete",
                            tap_x=like_out.get("tap_x"),
                            tap_y=like_out.get("tap_y"),
                            verification_method=like_out.get("verification_method"),
                            confidence=round(
                                float(like_out.get("verify_confidence") or 0.0),
                                4,
                            ),
                            current_activity=meta_rc.get("current_activity"),
                            current_package=meta_rc.get("current_package"),
                            source_profile_username=source_profile_username,
                        )
                    back_out = visual_return_to_profile_from_post(
                        d, source_profile_username=source_profile_username
                    )
                elif post_out.get("private_profile"):
                    like_out = {
                        "ok": True,
                        "skipped": True,
                        "private_profile": True,
                    }
                    back_out = {
                        "ok": True,
                        "skipped": True,
                        "profile_detected": True,
                    }
                else:
                    like_out = {
                        "ok": False,
                        "skipped": True,
                        "failure_reason": "post_viewer_not_detected",
                    }
                real_visual_like = bool(
                    getattr(config, "ENABLE_REAL_VISUAL_POST_LIKE", False)
                )
                log(
                    "info",
                    "visual_post_like_flow_runner_complete",
                    source_profile_username=source_profile_username,
                    post_open_ok=post_out.get("ok"),
                    post_detected=post_out.get("post_detected"),
                    no_posts_profile=post_out.get("no_posts_profile"),
                    like_dry_run_ok=like_out.get("ok"),
                    return_profile_ok=back_out.get("ok"),
                    real_visual_like=real_visual_like,
                    already_liked=like_out.get("already_liked"),
                    liked_verified=like_out.get("liked_verified"),
                )
                if real_visual_like:
                    eng_log(
                        "visual_post_like_flow_real",
                        "success"
                        if bool(
                            post_out.get("ok")
                            and back_out.get("ok")
                            and (
                                post_out.get("no_posts_profile")
                                or post_out.get("private_profile")
                                or (
                                    post_out.get("post_detected")
                                    and (
                                        like_out.get("already_liked")
                                        or (
                                            like_out.get("ok")
                                            and like_out.get("liked_verified")
                                        )
                                    )
                                )
                            )
                        )
                        else "info",
                        "visual_post_like_flow_real_complete",
                        {
                            "post_detected": post_out.get("post_detected"),
                            "no_posts_profile": post_out.get("no_posts_profile"),
                            "private_profile": post_out.get("private_profile"),
                            "already_liked": like_out.get("already_liked"),
                            "liked_verified": like_out.get("liked_verified"),
                            "profile_after_back": back_out.get("profile_detected"),
                        },
                    )
                else:
                    eng_log(
                        "visual_post_like_flow_dry_run",
                        "success"
                        if bool(
                            post_out.get("ok")
                            and back_out.get("ok")
                            and (
                                post_out.get("no_posts_profile")
                                or post_out.get("private_profile")
                                or (
                                    post_out.get("post_detected")
                                    and like_out.get("ok")
                                )
                            )
                        )
                        else "info",
                        "visual_post_like_flow_dry_run_complete",
                        {
                            "post_detected": post_out.get("post_detected"),
                            "no_posts_profile": post_out.get("no_posts_profile"),
                            "private_profile": post_out.get("private_profile"),
                            "like_dry_run": True,
                            "profile_after_back": back_out.get("profile_detected"),
                        },
                    )
                fr_out = None
                exit_code = 47
                if post_out.get("no_posts_profile"):
                    exit_code = 52
                elif real_visual_like:
                    if like_out.get("already_liked"):
                        exit_code = 51
                    elif like_out.get("real_tap_sent") and like_out.get("ok"):
                        exit_code = (
                            49 if like_out.get("liked_verified") else 50
                        )
                _post_like_hard_lock_mismatch = bool(
                    post_out.get("target_profile_lock_mismatch")
                    or like_out.get("target_profile_lock_mismatch")
                    or like_out.get("profile_context_mismatch")
                )
                if _post_like_hard_lock_mismatch:
                    exit_code = 58
                elif post_follow_context_blocked:
                    log(
                        "info",
                        "visual_post_like_failed_continue_to_follow",
                        source_profile_username=source_profile_username,
                        post_detected=bool(post_out.get("post_detected")),
                        post_ok=bool(post_out.get("ok")),
                        like_ok=bool(like_out.get("ok")),
                        failure_reason=str(post_out.get("failure_reason") or ""),
                    )
                    log(
                        "info",
                        "visual_follow_attempt_allowed_after_post_like_failure",
                        source_profile_username=source_profile_username,
                    )

                posts_count_gate = visual_profile_stats_posts_count(d)
                like_ok_effective = bool(
                    like_out.get("already_liked")
                    or (
                        like_out.get("ok")
                        and (
                            not real_visual_like
                            or bool(like_out.get("liked_verified"))
                        )
                    )
                )
                post_like_failed = bool(
                    not skip_post_private
                    and not bool(post_out.get("no_posts_profile"))
                    and not bool(post_out.get("private_profile"))
                    and (
                        (posts_count_gate is not None and int(posts_count_gate) > 0)
                        or bool(post_out.get("post_detected"))
                    )
                    and (
                        post_follow_context_blocked
                        or not bool(post_out.get("ok"))
                        or (
                            bool(post_out.get("post_detected"))
                            and not like_ok_effective
                        )
                    )
                )

                if bool(getattr(config, "ENABLE_VISUAL_FOLLOW_MUTE_FLOW", False)) and (
                    not _post_like_hard_lock_mismatch
                ):
                    if (
                        post_like_failed
                        and posts_count_gate is not None
                        and int(posts_count_gate) > 0
                    ):
                        log(
                            "info",
                            "visual_post_like_failed_target_reacquire_before_follow",
                            source_profile_username=source_profile_username,
                            posts_count=int(posts_count_gate),
                            post_detected=bool(post_out.get("post_detected")),
                            like_ok_effective=like_ok_effective,
                        )
                        _cand_pre = _follower_candidate_payload()
                        _racq_pre = reacquire_target_profile_for_follow(
                            d,
                            target_username=_expected_target_username_from_ctx(),
                            source_profile_username=source_profile_username,
                            source_account_context=str(source_account_context or ""),
                            follower_candidate=_cand_pre
                            if str(_cand_pre.get("username") or "").strip()
                            else None,
                        )
                        if _racq_pre.get("ok"):
                            log(
                                "info",
                                "visual_follow_attempt_allowed_after_target_reacquire",
                                source_profile_username=source_profile_username,
                                reacquire_method=str(
                                    _racq_pre.get("method") or ""
                                ),
                            )
                            if bool(
                                getattr(
                                    config, "ENABLE_VISUAL_PROFILE_CONTEXT_LOCK", False
                                )
                            ):
                                profile_expected_ctx = visual_capture_profile_context(
                                    d,
                                    source_profile_username=source_profile_username,
                                )

                    log(
                        "info",
                        "visual_follow_runtime_config",
                        ENABLE_REAL_VISUAL_FOLLOW=bool(
                            getattr(config, "ENABLE_REAL_VISUAL_FOLLOW", False)
                        ),
                        VISUAL_FOLLOW_MUTE_DRY_RUN=bool(
                            getattr(config, "VISUAL_FOLLOW_MUTE_DRY_RUN", True)
                        ),
                        dry_effective=(
                            (not bool(getattr(config, "ENABLE_REAL_VISUAL_FOLLOW", False)))
                            and bool(getattr(config, "VISUAL_FOLLOW_MUTE_DRY_RUN", True))
                        ),
                        source_profile_username=source_profile_username,
                    )
                    _exp_tgt = _expected_target_username_from_ctx()
                    _ok_gate, _cur_u, _reason_gate = _follow_surface_ok_for_dry_run(
                        _exp_tgt
                    )
                    if not _ok_gate:
                        log(
                            "info",
                            "visual_follow_blocked_not_on_target_profile",
                            expected_target_username=_exp_tgt,
                            current_username=_cur_u,
                            source_profile_username=source_profile_username,
                            reason=_reason_gate,
                        )
                        _cand_g = _follower_candidate_payload()
                        _racq_g = reacquire_target_profile_for_follow(
                            d,
                            target_username=_exp_tgt,
                            source_profile_username=source_profile_username,
                            source_account_context=str(source_account_context or ""),
                            follower_candidate=_cand_g
                            if str(_cand_g.get("username") or "").strip()
                            else None,
                        )
                        if _racq_g.get("ok"):
                            if bool(
                                getattr(
                                    config, "ENABLE_VISUAL_PROFILE_CONTEXT_LOCK", False
                                )
                            ):
                                profile_expected_ctx = visual_capture_profile_context(
                                    d,
                                    source_profile_username=source_profile_username,
                                )
                            _exp_tgt = _expected_target_username_from_ctx()
                            _ok_gate, _cur_u, _reason_gate = (
                                _follow_surface_ok_for_dry_run(_exp_tgt)
                            )
                        if not _ok_gate:
                            log(
                                "info",
                                "visual_follow_blocked_not_on_target_profile",
                                expected_target_username=_exp_tgt,
                                current_username=_cur_u,
                                source_profile_username=source_profile_username,
                                reason=_reason_gate,
                                phase="after_reacquire",
                            )
                            fr_out = _fr_out_follow_blocked_stub(
                                "visual_follow_blocked_not_on_target_profile_after_reacquire"
                            )
                        else:
                            fr_out = visual_follow_profile_dry_run(
                                d,
                                source_profile_username=source_profile_username,
                                expected_profile_context=profile_expected_ctx,
                            )
                    else:
                        fr_out = visual_follow_profile_dry_run(
                            d,
                            source_profile_username=source_profile_username,
                            expected_profile_context=profile_expected_ctx,
                        )
                    real_mute_on = bool(
                        getattr(config, "ENABLE_REAL_VISUAL_MUTE_AFTER_FOLLOW", False)
                    )
                    real_follow_ok = bool(
                        getattr(config, "ENABLE_REAL_VISUAL_FOLLOW", False)
                        and fr_out.get("follow_verified")
                        and fr_out.get("real_follow_tap_sent")
                    )
                    mute_skip_private_rq = bool(
                        bool(getattr(config, "FOLLOW_PRIVATE_ACCOUNTS", False))
                        and bool(fr_out.get("follow_request_pending"))
                        and bool(fr_out.get("follow_verified"))
                    )
                    real_mute_eligible = bool(
                        real_follow_ok and not mute_skip_private_rq
                    )
                    mute_real = None

                    if (
                        not post_like_before
                        and fr_out is not None
                        and fr_out.get("follow_verified")
                    ):
                        log(
                            "info",
                            "visual_follow_stability_follow_verified",
                            source_profile_username=source_profile_username,
                            username_norm=_expected_target_username_from_ctx(),
                            follow_request_pending=bool(
                                fr_out.get("follow_request_pending")
                            ),
                        )

                    if zero_posts_hard_skip:
                        log(
                            "info",
                            "visual_follow_zero_posts_continue_mute_flow",
                            source_profile_username=source_profile_username,
                            follow_verified=bool(fr_out.get("follow_verified")),
                            real_follow_ok=real_follow_ok,
                            real_mute_on=real_mute_on,
                            mute_skip_private_rq=mute_skip_private_rq,
                            skip_post_open=True,
                            skip_like=True,
                        )

                    if fr_out.get("profile_context_mismatch") or fr_out.get(
                        "target_profile_lock_mismatch"
                    ):
                        act_out = {"popup_detected": False, "skipped": True}
                        mute_out = {"ok": False, "skipped": True}
                    elif bool(getattr(config, "ENABLE_REAL_VISUAL_FOLLOW", False)) and not bool(
                        fr_out.get("follow_verified")
                    ):
                        log(
                            "info",
                            "visual_follow_mute_skipped_follow_unverified",
                            source_profile_username=source_profile_username,
                            follow_verified=False,
                            real_follow_tap_sent=bool(
                                fr_out.get("real_follow_tap_sent")
                            ),
                            skip_already_following=bool(
                                fr_out.get("skip_already_following")
                            ),
                            failure_reason=str(fr_out.get("failure_reason") or ""),
                        )
                        act_out = {"popup_detected": False, "skipped": True}
                        mute_out = {
                            "ok": False,
                            "skipped": True,
                            "skipped_reason": "follow_not_verified",
                        }
                    elif mute_skip_private_rq and real_follow_ok:
                        log(
                            "info",
                            "visual_follow_mute_skipped_private_follow_request_only",
                            source_profile_username=source_profile_username,
                            follow_verified=bool(fr_out.get("follow_verified")),
                            follow_request_pending=bool(
                                fr_out.get("follow_request_pending")
                            ),
                            follow_request_pending_method=str(
                                fr_out.get("follow_request_pending_method") or ""
                            ),
                        )
                        log(
                            "info",
                            "private_follow_request_skip_mute_expected",
                            source_profile_username=source_profile_username,
                            visual_private_profile_detected=bool(
                                priv_detect_runner.get("private_profile_detected")
                            ),
                            follow_verified=bool(fr_out.get("follow_verified")),
                            follow_request_pending=bool(
                                fr_out.get("follow_request_pending")
                            ),
                        )
                        act_out = {"popup_detected": False, "skipped": True}
                        mute_out = {
                            "ok": False,
                            "skipped": True,
                            "skipped_reason": "private_follow_request_pending",
                        }
                    elif real_mute_on and real_mute_eligible:
                        if not post_like_before:
                            log(
                                "info",
                                "visual_follow_stability_mute_started",
                                source_profile_username=source_profile_username,
                            )
                        act_out = visual_detect_follow_post_actions(
                            d, source_profile_username=source_profile_username
                        )
                        mute_out = {
                            "ok": True,
                            "skipped": True,
                            "real_mute_flow": True,
                            "dry_run": False,
                        }
                        fo_m = visual_open_following_options_after_follow(
                            d,
                            source_profile_username=source_profile_username,
                        )
                        if not fo_m.get("ok"):
                            mute_real = {"following_options": fo_m, "mute_sheet": None, "toggles": None}
                            mute_out = {
                                **mute_out,
                                "ok": False,
                                "failure_reason": fo_m.get("failure_reason"),
                                "real_mute_following_options": fo_m,
                            }
                        else:
                            ms_m = visual_open_mute_sheet_from_following_options(
                                d,
                                source_profile_username=source_profile_username,
                            )
                            if not ms_m.get("ok"):
                                mute_real = {
                                    "following_options": fo_m,
                                    "mute_sheet": ms_m,
                                    "toggles": None,
                                }
                                mute_out = {
                                    **mute_out,
                                    "ok": False,
                                    "failure_reason": ms_m.get("failure_reason"),
                                    "real_mute_mute_sheet": ms_m,
                                }
                            else:
                                tg_m = visual_toggle_mute_posts_and_stories(
                                    d,
                                    source_profile_username=source_profile_username,
                                )
                                mute_real = {
                                    "following_options": fo_m,
                                    "mute_sheet": ms_m,
                                    "toggles": tg_m,
                                }
                                mute_out = {
                                    **mute_out,
                                    "ok": bool(tg_m.get("ok")),
                                    "failure_reason": tg_m.get("failure_reason"),
                                    "real_mute_toggles": tg_m,
                                    "mute_posts_verified": tg_m.get("posts_verified"),
                                    "mute_stories_verified": tg_m.get(
                                        "stories_verified"
                                    ),
                                }
                    else:
                        act_out = visual_detect_follow_post_actions(
                            d, source_profile_username=source_profile_username
                        )
                        mute_out = visual_mute_after_follow_dry_run(
                            d,
                            source_profile_username=source_profile_username,
                        )
                    log(
                        "info",
                        "visual_follow_mute_flow_runner_complete",
                        source_profile_username=source_profile_username,
                        follow_detect_ok=fr_out.get("follow_button_detected"),
                        follow_dry_run_ok=fr_out.get("ok"),
                        follow_actions_popup=act_out.get("popup_detected"),
                        mute_dry_run_ok=mute_out.get("ok"),
                    )
                    eng_log(
                        "visual_follow_mute_flow_dry_run",
                        "info",
                        "visual_follow_mute_flow_dry_run_complete",
                        {
                            "follow_detect_ok": fr_out.get("follow_button_detected"),
                            "follow_dry_run": True,
                            "mute_dry_run": True,
                            "popup_detected": act_out.get("popup_detected"),
                        },
                    )
                    if mute_out.get("target_profile_lock_mismatch"):
                        exit_code = 58
                    if bool(getattr(config, "ENABLE_REAL_VISUAL_FOLLOW", False)):
                        if fr_out.get("profile_context_mismatch") or fr_out.get(
                            "target_profile_lock_mismatch"
                        ):
                            exit_code = 58
                        elif fr_out.get("skip_already_following"):
                            exit_code = 57
                        elif fr_out.get("real_follow_tap_sent"):
                            _fp = bool(
                                getattr(config, "FOLLOW_PRIVATE_ACCOUNTS", False)
                            )
                            if (
                                _fp
                                and fr_out.get("follow_verified")
                                and fr_out.get("follow_request_pending")
                            ):
                                exit_code = 63
                            else:
                                exit_code = (
                                    55 if fr_out.get("follow_verified") else 56
                                )
                    if exit_code in (55, 57, 63):
                        tgt_rec = str(
                            (profile_expected_ctx or {}).get(
                                "header_username_detected"
                            )
                            or ""
                        ).strip().lstrip("@")
                        if not tgt_rec:
                            tgt_rec = str(
                                (best or {}).get("resolved_username_hint") or ""
                            ).strip().lstrip("@")
                        meta_hist = _followers_current_pkg_activity(d)
                        if not bool(fr_out.get("follow_verified")):
                            log(
                                "info",
                                "visual_follow_history_not_recorded_follow_unverified",
                                detected_button_text=str(
                                    fr_out.get("detected_button_text") or ""
                                ),
                                follow_verified=False,
                                follow_button_visible_after_verify=bool(
                                    fr_out.get("follow_button_visible_after_verify")
                                ),
                                current_activity=meta_hist.get("current_activity"),
                                current_package=meta_hist.get("current_package"),
                                source_profile_username=source_profile_username,
                                exit_code=exit_code,
                                failure_reason=str(fr_out.get("failure_reason") or ""),
                            )
                        else:
                            _hist_meta: dict = {"exit_code": exit_code}
                            if exit_code == 63:
                                _hist_meta["visual_private_profile_detected"] = bool(
                                    priv_detect_runner.get("private_profile_detected")
                                )
                            mark_visual_follow_target_processed(
                                source_profile_username=source_profile_username,
                                target_username=tgt_rec,
                                visual_candidate_id=str(
                                    (best or {}).get("visual_candidate_id") or ""
                                ),
                                status={
                                    55: "followed",
                                    57: "already_following",
                                    63: "follow_requested",
                                }[exit_code],
                                follow_verified=bool(fr_out.get("follow_verified")),
                                follow_request_pending=bool(
                                    fr_out.get("follow_request_pending")
                                ),
                                run_id=str(run_id or ""),
                                source_account_context=str(source_account_context or ""),
                                metadata=_hist_meta,
                            )
                            history_recorded_follow_target = True
                            history_recorded_target_username = tgt_rec
                            if exit_code == 63 and bool(
                                priv_detect_runner.get("private_profile_detected")
                            ) and bool(fr_out.get("follow_request_pending")):
                                log(
                                    "info",
                                    "private_follow_request_handled_success",
                                    source_profile_username=source_profile_username,
                                    target_username=tgt_rec,
                                    visual_private_profile_detected=True,
                                    follow_request_pending=True,
                                    exit_code=exit_code,
                                )
                    if (
                        real_mute_on
                        and real_mute_eligible
                        and mute_real is not None
                        and not bool(fr_out.get("follow_request_pending"))
                    ):
                        fo_r = mute_real.get("following_options") or {}
                        ms_r = mute_real.get("mute_sheet")
                        tg_r = mute_real.get("toggles")
                        if (
                            fo_r.get("target_profile_lock_mismatch")
                            or (
                                isinstance(ms_r, dict)
                                and ms_r.get("target_profile_lock_mismatch")
                            )
                            or (
                                isinstance(tg_r, dict)
                                and tg_r.get("target_profile_lock_mismatch")
                            )
                        ):
                            exit_code = 58
                        elif isinstance(tg_r, dict):
                            pst = bool(tg_r.get("posts_verify_strict"))
                            sst = bool(tg_r.get("stories_verify_strict"))
                            vt = bool(tg_r.get("verify_trusted"))
                            mtu = bool(tg_r.get("mute_tap_sent_unverified"))
                            tg_ok = bool(tg_r.get("ok"))
                            if tg_ok and vt:
                                exit_code = 59
                            elif tg_ok and (pst or sst) and not vt:
                                exit_code = 60
                            elif tg_ok and mtu:
                                exit_code = 65
                            else:
                                exit_code = 61
                    if exit_code not in (
                        49,
                        50,
                        51,
                        52,
                        55,
                        56,
                        57,
                        58,
                        59,
                        60,
                        61,
                        62,
                        63,
                        65,
                    ):
                        exit_code = 48
                pkg_fin = config.INSTAGRAM_PACKAGE
                _picker_fin_cand = str(
                    (best or {}).get("resolved_username_hint") or ""
                ).strip().lstrip("@")
                if not _picker_fin_cand:
                    _picker_fin_cand = str(
                        (profile_expected_ctx or {}).get("header_username_detected")
                        or ""
                    ).strip().lstrip("@")
                if not post_like_before:
                    log(
                        "info",
                        "visual_follow_stability_return_ct_followers_started",
                        source_profile_username=source_profile_username,
                        candidate_username=_picker_fin_cand,
                    )
                fin_ret = visual_flow_final_return_to_ct_followers_list(
                    d,
                    source_profile_username=source_profile_username,
                    pkg=pkg_fin,
                    candidate_username=_picker_fin_cand or None,
                    source_account_context=str(source_account_context or "") or None,
                )
                ok_fin = bool(fin_ret.get("final_return_ok"))
                how_fin = str(fin_ret.get("how") or "")
                if (
                    ok_fin
                    and not fin_ret.get("restart_required")
                    and not verify_followers_list_surface_is_ct_account(
                        d,
                        source_profile_username=source_profile_username,
                        follower_candidate_username=_picker_fin_cand or None,
                    )
                ):
                    log(
                        "info",
                        "visual_followers_picker_abort_wrong_source_list",
                        phase="post_final_return_surface_check",
                        source_profile_username=source_profile_username,
                        follower_candidate_username=_picker_fin_cand,
                    )
                    rr_wr = reset_instagram_to_canonical_state(
                        d,
                        reason="picker_wrong_followers_list_header",
                        source_profile_username=source_profile_username,
                        source_account_context=str(source_account_context or "") or None,
                    )
                    if rr_wr.get("ok") and _reenter_ct_followers_list_after_canonical_reset(
                        d,
                        source_profile_username=source_profile_username,
                        account_id=str(source_account_context or ""),
                        context="picker_wrong_followers_list_recover",
                    ):
                        visual_target_profile_lock_clear()
                        return VISUAL_FOLLOW_HISTORY_CONTINUE
                    ok_fin = False
                    how_fin = "wrong_followers_list_after_return"
                if (
                    not post_like_before
                    and ok_fin
                    and not fin_ret.get("restart_required")
                    and verify_followers_list_surface_is_ct_account(
                        d,
                        source_profile_username=source_profile_username,
                        follower_candidate_username=_picker_fin_cand or None,
                    )
                ):
                    log(
                        "info",
                        "visual_follow_stability_return_ct_followers_success",
                        source_profile_username=source_profile_username,
                        final_return_how=how_fin,
                        candidate_username=_picker_fin_cand,
                    )
                if fin_ret.get("restart_required"):
                    reset_ok = bool(fin_ret.get("reset_ok"))
                    if reset_ok and _reenter_ct_followers_list_after_canonical_reset(
                        d,
                        source_profile_username=source_profile_username,
                        account_id=str(source_account_context or ""),
                        context="visual_flow_after_final_return_reset",
                    ):
                        log(
                            "info",
                            "visual_flow_canonical_reset_recovered",
                            source_profile_username=source_profile_username,
                            final_return_how=how_fin,
                            reset_performed=bool(fin_ret.get("reset_performed")),
                            business_status="recovered_reset",
                            history_target_recorded=bool(
                                history_recorded_follow_target
                            ),
                        )
                        log(
                            "info",
                            "visual_flow_restart_from_canonical_requested",
                            source_profile_username=source_profile_username,
                            resume="followers_engine_same_session",
                        )
                        log(
                            "info",
                            "visual_followers_reentry_success_continue",
                            source_profile_username=source_profile_username,
                        )
                        if history_recorded_follow_target:
                            log(
                                "info",
                                "visual_follow_history_preserved_after_reset",
                                source_profile_username=source_profile_username,
                                target_username=history_recorded_target_username,
                            )
                            log(
                                "info",
                                "visual_followers_next_run_should_skip_recorded_target",
                                source_profile_username=source_profile_username,
                                target_username=history_recorded_target_username,
                                skip_reason="already_processed_follow_target",
                            )
                        log(
                            "info",
                            "visual_follow_history_state_debug",
                            history_recorded_follow_target=history_recorded_follow_target,
                            history_recorded_target_username=history_recorded_target_username,
                            exit_code=exit_code,
                            branch="canonical_reset_recovered_return_continue",
                        )
                        visual_target_profile_lock_clear()
                        return VISUAL_FOLLOW_HISTORY_CONTINUE
                    log(
                        "info",
                        "visual_flow_aborted_after_canonical_reset",
                        source_profile_username=source_profile_username,
                        final_return_how=how_fin,
                        reset_performed=bool(fin_ret.get("reset_performed")),
                        reset_ok=reset_ok,
                        business_status="needs_restart"
                        if reset_ok
                        else "failed",
                    )
                    _preserve_follow_exit = bool(
                        isinstance(fr_out, dict)
                        and bool(fr_out.get("follow_verified"))
                        and bool(fr_out.get("real_follow_tap_sent"))
                    )
                    if _preserve_follow_exit:
                        log(
                            "info",
                            "visual_flow_final_return_reset_failed_preserve_follow_success",
                            source_profile_username=source_profile_username,
                            exit_code=exit_code,
                            follow_verified=bool(fr_out.get("follow_verified")),
                            verification_method=str(
                                fr_out.get("verification_method") or ""
                            ),
                        )
                    else:
                        exit_code = 64
                if zero_posts_hard_skip:
                    meta_zdone = _followers_current_pkg_activity(d)
                    _hist_ok = bool(history_recorded_follow_target) or bool(
                        exit_code in (55, 57, 63)
                        and isinstance(fr_out, dict)
                        and bool(fr_out.get("follow_verified"))
                    )
                    log(
                        "info",
                        "visual_follow_zero_posts_profile_complete",
                        posts_count=0,
                        skip_post_open=True,
                        skip_like=True,
                        skip_mute=False,
                        mute_flow_allowed=True,
                        exit_code=exit_code,
                        follow_verified=(
                            bool(fr_out.get("follow_verified"))
                            if isinstance(fr_out, dict)
                            else False
                        ),
                        history_recorded=_hist_ok,
                        final_return_ok=bool(ok_fin),
                        final_return_how=str(how_fin or ""),
                        source_profile_username=source_profile_username,
                        current_activity=meta_zdone.get("current_activity"),
                        current_package=meta_zdone.get("current_package"),
                    )
                log(
                    "info",
                    "visual_follow_history_state_debug",
                    history_recorded_follow_target=history_recorded_follow_target,
                    history_recorded_target_username=history_recorded_target_username,
                    exit_code=exit_code,
                    branch="picker_dry_run_complete_before_emit",
                )
                if not post_like_before:
                    log(
                        "info",
                        "visual_follow_stability_next_candidate",
                        source_profile_username=source_profile_username,
                        exit_code=exit_code,
                        final_return_ok=bool(ok_fin),
                        history_recorded_follow_target=history_recorded_follow_target,
                    )
                visual_target_profile_lock_clear()
                _emit_performance_summary(
                    t0=t0,
                    warm_session_used=warm_session_used,
                    force_stop_used=force_stop_used,
                    exit_code=exit_code,
                    target_username=source_profile_username,
                )
                if (
                    exit_code in (55, 57, 59, 60, 63)
                    and ok_fin
                    and not fin_ret.get("restart_required")
                ):
                    if exit_code == 63:
                        log(
                            "info",
                            "private_follow_request_continue_next_candidate",
                            source_profile_username=source_profile_username,
                            exit_code=exit_code,
                            visual_private_profile_detected=bool(
                                priv_detect_runner.get("private_profile_detected")
                            ),
                            follow_request_pending=bool(
                                fr_out.get("follow_request_pending")
                            ),
                        )
                    return VISUAL_FOLLOW_HISTORY_CONTINUE
                return exit_code
            if open_out.get("profile_detected"):
                visual_target_profile_lock_clear()
            _emit_performance_summary(
                t0=t0,
                warm_session_used=warm_session_used,
                force_stop_used=force_stop_used,
                exit_code=46,
                target_username=source_profile_username,
            )
            return 46

    log(
        "info",
        "visual_followers_picker_dry_run_complete",
        screenshot_path=vpick.get("screenshot_path"),
        source_profile_username=source_profile_username,
        source_account_context=source_account_context,
        candidate_count=vpick.get("candidate_count"),
        sample_candidates=vpick.get("sample_candidates"),
        mean_confidence=vpick.get("mean_confidence"),
        dry_run=True,
        visual_only=True,
        picker_error=vpick.get("picker_error"),
        stop_reason=stop_reason,
        xml_candidate_row_count=len(candidates) if candidates is not None else None,
        iteration=iteration,
        scroll_used=scroll_used,
    )
    eng_log(
        "visual_followers_picker_dry_run_complete",
        "success" if int(vpick.get("candidate_count") or 0) > 0 else "info",
        "visual_followers_candidates_detected_dry_run",
        {
            "candidate_count": vpick.get("candidate_count"),
            "mean_confidence": vpick.get("mean_confidence"),
            "screenshot_path": vpick.get("screenshot_path"),
            "picker_error": vpick.get("picker_error"),
            "stop_reason": stop_reason,
            "iteration": iteration,
            "scroll_used": scroll_used,
        },
    )
    _emit_performance_summary(
        t0=t0,
        warm_session_used=warm_session_used,
        force_stop_used=force_stop_used,
        exit_code=45,
        target_username=source_profile_username,
    )
    return 45


def _reenter_ct_followers_list_after_canonical_reset(
    d,
    *,
    source_profile_username: str,
    account_id: str,
    context: str,
) -> bool:
    """
    After instagram_canonical_reset_success, rebuild CT profile + followers list surface
    so the followers engine can continue in the same process.
    """
    pkg = config.INSTAGRAM_PACKAGE
    gsurf = ensure_global_search_surface(
        d,
        intended_username=source_profile_username,
        source_profile_username=source_profile_username,
        source_account_context=account_id,
    )
    if not gsurf.get("ok"):
        log(
            "warning",
            "canonical_reset_reenter_failed",
            phase="global_search_surface",
            context=context,
            source_profile_username=source_profile_username,
        )
        return False
    if not _open_search_with_recovery(
        d,
        pkg=pkg,
        username=source_profile_username,
        context=context,
    ):
        log(
            "warning",
            "canonical_reset_reenter_failed",
            phase="open_search",
            context=context,
            source_profile_username=source_profile_username,
        )
        return False
    if not type_search(d, source_profile_username, previous_username=None):
        log(
            "warning",
            "canonical_reset_reenter_failed",
            phase="type_search",
            context=context,
            source_profile_username=source_profile_username,
        )
        return False
    if bool(getattr(config, "FAST_SKIP_ACCOUNTS_TAB", True)) and bool(
        getattr(config, "FAST_PATH_MODE", False)
    ):
        accounts_tab_clicked = False
    else:
        accounts_tab_clicked = open_accounts_tab(d)
    ui_mode = "accounts_tab" if accounts_tab_clicked else "mixed_results"
    set_search_ui_mode(ui_mode)
    if not tap_account_result(d, source_profile_username):
        log(
            "warning",
            "canonical_reset_reenter_failed",
            phase="tap_account",
            context=context,
            source_profile_username=source_profile_username,
        )
        return False
    if not verify_profile(d, source_profile_username):
        log(
            "warning",
            "canonical_reset_reenter_failed",
            phase="verify_profile",
            context=context,
            source_profile_username=source_profile_username,
        )
        return False
    _open_ok, _meta = open_followers_list_from_profile(
        d, source_profile_username, pkg, profile_verified=True
    )
    if not _open_ok:
        log(
            "warning",
            "canonical_reset_reenter_failed",
            phase="open_followers_list",
            context=context,
            source_profile_username=source_profile_username,
        )
        return False
    log(
        "info",
        "canonical_reset_reenter_success",
        context=context,
        source_profile_username=source_profile_username,
    )
    return True


def _run_followers_list_engine_session(
    d,
    *,
    source_profile_username: str,
    account_id: str,
    run_id: str,
    supabase_mode: bool,
    warm_session_used: bool,
    force_stop_used: bool,
) -> int:
    """
    Source profile → source followers list → follower profile → FOLLOW SAFE V1 → return to list.
    Does not run the standard search/DM per-queue target loop.
    """
    global _RUNTIME_FOLLOW_COUNT, _RUNTIME_FOLLOWED_USERNAMES, _RUNTIME_INTERACTED_USERNAMES
    global _RUNTIME_SKIPPED_USERNAMES
    global _VISUAL_FOLLOWERS_OPEN_COUNT_THIS_SESSION
    _VISUAL_FOLLOWERS_OPEN_COUNT_THIS_SESSION = 0
    pkg = config.INSTAGRAM_PACKAGE
    src_key = _norm_ig_handle(source_profile_username)
    log(
        "info",
        "visual_followers_ct_source_loaded",
        source_profile_username=source_profile_username,
        target_username=str(getattr(config, "TARGET_USERNAME", "") or ""),
        account_id=str(account_id or ""),
    )

    def _eng_log(action_type: str, status: str, message: str, payload: dict) -> None:
        if not (supabase_mode and run_id and account_id):
            return
        base = {
            "source_profile_username": source_profile_username,
            "source_account_context": account_id,
        }
        _safe_supabase_call(
            "insert_action_log",
            run_id=run_id,
            account_id=account_id,
            target_username=source_profile_username,
            action_type=action_type,
            status=status,
            message=message,
            payload={**base, **payload},
        )

    t0 = time.perf_counter()
    gsurf = ensure_global_search_surface(
        d,
        intended_username=source_profile_username,
        source_profile_username=source_profile_username,
        source_account_context=account_id,
    )
    if not gsurf.get("ok"):
        _eng_log(
            "followers_engine_aborted",
            "failed",
            "global_search_surface_failed",
            {"reason": gsurf.get("reason")},
        )
        _emit_performance_summary(
            t0=t0,
            warm_session_used=warm_session_used,
            force_stop_used=force_stop_used,
            exit_code=64,
            target_username=source_profile_username,
        )
        return 64
    if not _open_search_with_recovery(
        d, pkg=pkg, username=source_profile_username, context="followers_engine_start"
    ):
        _eng_log("followers_engine_aborted", "failed", "open_search_failed", {})
        return 4
    if not type_search(d, source_profile_username, previous_username=None):
        reason = get_type_search_failure_reason() or "type_search_failed"
        _eng_log("followers_engine_aborted", "failed", reason, {})
        if reason == "search_surface_wrong_app_launcher":
            return 73
        if reason == "search_field_not_cleared":
            return 9
        return 5

    if bool(getattr(config, "FAST_SKIP_ACCOUNTS_TAB", True)) and bool(
        getattr(config, "FAST_PATH_MODE", False)
    ):
        accounts_tab_clicked = False
    else:
        accounts_tab_clicked = open_accounts_tab(d)
    ui_mode = "accounts_tab" if accounts_tab_clicked else "mixed_results"
    set_search_ui_mode(ui_mode)

    if not tap_account_result(d, source_profile_username):
        _eng_log("followers_engine_aborted", "failed", "tap_account_failed", {})
        return 7
    if not verify_profile(d, source_profile_username):
        _eng_log("followers_engine_aborted", "failed", "profile_verify_failed", {})
        return 8

    _eng_log("followers_list_open_started", "started", "open_from_source_profile", {})
    followers_session_reset_list_committed_open()
    followers_list_ready = False
    _open_ok, open_list_meta = open_followers_list_from_profile(
        d, source_profile_username, pkg, profile_verified=True
    )
    if not _open_ok:
        fb_payload = {
            **open_list_meta,
            "source_profile_username": source_profile_username,
            "source_account_context": account_id,
        }
        if open_list_meta.get("followers_stat_text_dump"):
            _eng_log(
                "followers_stat_text_dump",
                "info",
                "profile_stats_strip",
                {
                    "text_view_count": len(open_list_meta.get("followers_stat_text_dump") or []),
                    "profile_stats_visible": open_list_meta.get("profile_stats_visible"),
                    "followers_stat_text_dump": (open_list_meta.get("followers_stat_text_dump") or [])[
                        :80
                    ],
                },
            )
        if open_list_meta.get("followers_stat_coordinate_retry"):
            _eng_log(
                "followers_stat_coordinate_retry",
                "info",
                "coordinate_fallback_used",
                {
                    "tap_x": open_list_meta.get("tap_x"),
                    "tap_y": open_list_meta.get("tap_y"),
                    "tap_method": open_list_meta.get("tap_method"),
                },
            )
        _eng_log(
            "followers_list_open_debug",
            "failed",
            "followers_list_not_opened",
            fb_payload,
        )
        _eng_log(
            "followers_list_open_failed",
            "failed",
            "followers_list_not_opened",
            fb_payload,
        )
        return 40
    followers_list_ready = True
    navigation_loop_state: dict[str, Any] = {"last_state": None}

    succ_payload = {
        **open_list_meta,
        "source_profile_username": source_profile_username,
        "source_account_context": account_id,
    }
    _eng_log(
        "followers_list_open_success",
        "success",
        "followers_list_open_success",
        succ_payload,
    )
    if open_list_meta.get("followers_stat_text_dump"):
        _eng_log(
            "followers_stat_text_dump",
            "info",
            "profile_stats_strip",
            {
                "text_view_count": len(open_list_meta.get("followers_stat_text_dump") or []),
                "profile_stats_visible": open_list_meta.get("profile_stats_visible"),
                "followers_stat_text_dump": (open_list_meta.get("followers_stat_text_dump") or [])[
                    :80
                ],
            },
        )
    if open_list_meta.get("followers_stat_coordinate_retry"):
        _eng_log(
            "followers_stat_coordinate_retry",
            "info",
            "coordinate_fallback_used",
            {
                "tap_x": open_list_meta.get("tap_x"),
                "tap_y": open_list_meta.get("tap_y"),
                "tap_method": open_list_meta.get("tap_method"),
            },
        )

    max_iter = int(getattr(config, "FOLLOWERS_LIST_MAX_ITERATIONS_PER_RUN", 35))
    _follow_max_per_run = int(getattr(config, "FOLLOW_MAX_PER_RUN", 5))
    _followers_iter_attr = getattr(config, "FOLLOWERS_LIST_MAX_ITERATIONS_PER_RUN", None)
    log(
        "info",
        "followers_engine_goal_resolved",
        follows_goal_effective=max_iter,
        follows_goal_source=(
            "config.FOLLOWERS_LIST_MAX_ITERATIONS_PER_RUN"
            if _followers_iter_attr is not None
            else "getattr_default_35"
        ),
        followers_list_max_iterations_per_run=max_iter,
        follow_max_per_run=_follow_max_per_run,
        follow_max_per_run_applies_to="standard_search_dm_target_loop_only",
        supabase_mode=bool(supabase_mode),
        account_id=str(account_id or ""),
        run_id=str(run_id or ""),
        config_fallback_followers_list_max_iterations=35,
        config_fallback_follow_max_per_run=5,
    )
    max_scroll = int(followers_scroll_soft_max_per_session())
    scroll_used = 0
    processed = 0
    follows_completed_count = 0
    followers_engine_loop_iteration = 0
    prev_candidate_row_count: int | None = None
    sparse_follow_scrolls = 0
    visible_follow_buttons_stall_scrolls = 0
    max_visible_buttons_micro_scrolls = int(
        getattr(config, "FOLLOWERS_MAX_VISIBLE_BUTTON_MICRO_SCROLLS", 2) or 2
    )

    session_vf_detail_for_loop: dict | None = None
    for _snap_key in ("last_poll_snapshot", "after_tap_screen_snapshot"):
        _snap = open_list_meta.get(_snap_key) or {}
        _vf = _snap.get("visual_fallback_detail") or {}
        if isinstance(_vf, dict) and bool(_vf.get("visual_match")):
            session_vf_detail_for_loop = _vf
            break
    open_detection_method = str(open_list_meta.get("open_detection_method") or "xml")
    if (
        open_detection_method not in FOLLOWERS_ENGINE_VISUAL_OPEN_METHODS
        and isinstance(session_vf_detail_for_loop, dict)
        and bool(session_vf_detail_for_loop.get("visual_match"))
    ):
        open_detection_method = "visual_fallback"
    visual_loop_state: dict[str, Any] = {
        "session_vf_detail": session_vf_detail_for_loop,
        "grace_remaining": max(
            0,
            int(getattr(config, "FOLLOWERS_VISUAL_XML_STALE_GRACE_ITERATIONS", 3) or 3),
        ),
        "post_return_picker_refresh_pending": False,
        "list_progressive_exploration_passes_used": 0,
        "list_progressive_exploration_active": False,
        "list_progressive_exploration_last_empty_reason": "",
        "list_progressive_exploration_last_scroll_profile": "",
        "list_progressive_exploration_exhausted": False,
        "pending_recent_verified_follow_visual_candidate_skip": None,
        "pending_recent_already_connected_visual_candidate_skip": None,
        "blocked_already_connected_visual_candidate_ids": {},
    }
    _expl_v1 = FollowersExplorationV1(
        visual_loop_state,
        source_profile_username=source_profile_username,
    )

    def _followers_clear_pending_visual_candidate_skip() -> None:
        visual_loop_state["pending_recent_verified_follow_visual_candidate_skip"] = None

    def _followers_clear_pending_already_connected_visual_skip(
        *,
        clear_reason: str = "",
    ) -> None:
        _prev = visual_loop_state.get("pending_recent_already_connected_visual_candidate_skip")
        if not isinstance(_prev, dict):
            return
        visual_loop_state["pending_recent_already_connected_visual_candidate_skip"] = None
        try:
            log(
                "info",
                "followers_recent_already_connected_visual_candidate_skip_cleared",
                source_profile_username=str(_prev.get("source_profile_username") or "")[:120],
                visual_candidate_id=str(_prev.get("visual_candidate_id") or "")[:120],
                follower_username=str(_prev.get("follower_username") or "")[:120],
                skip_reason=str(_prev.get("skip_reason") or "")[:200],
                clear_reason=str(clear_reason or "")[:160],
            )
        except Exception:
            pass

    def _followers_clear_pending_if_pick_other_visual_id(p: dict | None) -> None:
        if not isinstance(p, dict):
            return
        _pv = str(p.get("visual_candidate_id") or "").strip()
        _pend = visual_loop_state.get("pending_recent_verified_follow_visual_candidate_skip")
        if isinstance(_pend, dict) and str(_pend.get("source_profile_username") or "") == str(
            source_profile_username or ""
        ):
            _armed = str(_pend.get("visual_candidate_id") or "").strip()
            if _pv and _armed and _pv != _armed:
                _followers_clear_pending_visual_candidate_skip()
        _pend_ac = visual_loop_state.get("pending_recent_already_connected_visual_candidate_skip")
        if isinstance(_pend_ac, dict) and str(_pend_ac.get("source_profile_username") or "") == str(
            source_profile_username or ""
        ):
            _armed_ac = str(_pend_ac.get("visual_candidate_id") or "").strip()
            if _pv and _armed_ac and _pv != _armed_ac:
                _followers_clear_pending_already_connected_visual_skip(
                    clear_reason="picked_other_visual_candidate_id",
                )

    def _ac_blocked_visual_ids() -> set[str]:
        bag = visual_loop_state.setdefault("blocked_already_connected_visual_candidate_ids", {})
        if not isinstance(bag, dict):
            bag = {}
            visual_loop_state["blocked_already_connected_visual_candidate_ids"] = bag
        sk = str(source_profile_username or "").strip()
        st = bag.get(sk)
        if not isinstance(st, set):
            st = set()
            bag[sk] = st
        return st

    def _ac_register_blocked_visual_after_postopen_skip(
        *,
        visual_candidate_id: str,
        follower_username: str,
        skip_reason: str,
    ) -> None:
        v = str(visual_candidate_id or "").strip()
        if not v:
            return
        s = _ac_blocked_visual_ids()
        s.add(v)
        try:
            log(
                "info",
                "followers_already_connected_visual_candidate_blocked_until_scroll",
                source_profile_username=source_profile_username,
                visual_candidate_id=v,
                follower_username=str(follower_username or "")[:120],
                skip_reason=str(skip_reason or "")[:220],
                blocked_count=len(s),
            )
        except Exception:
            pass

    def _followers_clear_ac_visual_blocks_after_scroll(*, clear_reason: str) -> None:
        bag = visual_loop_state.get("blocked_already_connected_visual_candidate_ids")
        if not isinstance(bag, dict):
            return
        sk = str(source_profile_username or "").strip()
        st = bag.pop(sk, None)
        n = len(st) if isinstance(st, set) else 0
        if n > 0:
            try:
                log(
                    "info",
                    "followers_already_connected_visual_candidate_blocks_cleared_after_scroll",
                    source_profile_username=source_profile_username,
                    clear_reason=str(clear_reason or "")[:180],
                    cleared_count=int(n),
                )
            except Exception:
                pass

    def _trace_visual_candidate_post_follower_open(
        *,
        pick_ctx: dict[str, Any],
        follower_un_so_far: str,
        det_ctx: dict[str, Any] | None,
        open_det_method: str,
    ) -> dict[str, Any]:
        """
        Trace + observe Instagram state after opening a visual-mapped follower profile.
        Does not change follow/mute behaviour; logs only (+ returns nav_obs for callers).
        """
        if not pick_ctx.get("visual_candidate_id"):
            return {}
        log(
            "info",
            "visual_candidate_profile_state_entered",
            source_profile_username=source_profile_username,
            visual_candidate_id=pick_ctx.get("visual_candidate_id"),
            follower_username_hint=str(follower_un_so_far or ""),
            resolved_username_hint=str(pick_ctx.get("resolved_username_hint") or ""),
            open_detection_method=open_det_method,
            open_list_meta_open_detection_method=str(
                open_list_meta.get("open_detection_method") or ""
            ),
        )
        log(
            "info",
            "visual_candidate_profile_analysis_started",
            source_profile_username=source_profile_username,
            visual_candidate_id=pick_ctx.get("visual_candidate_id"),
        )
        log(
            "info",
            "visual_candidate_profile_settle_before_observe",
            source_profile_username=source_profile_username,
            visual_candidate_id=pick_ctx.get("visual_candidate_id"),
            settle_s=round(0.8 + random.random() * 0.4, 3),
        )
        try:
            from followers_inter_candidate_perf import (
                inter_candidate_on_profile_settle_before_observe,
            )

            inter_candidate_on_profile_settle_before_observe()
        except Exception:
            pass
        time.sleep(0.8 + random.random() * 0.4)
        try:
            from followers_inter_candidate_perf import (
                inter_candidate_on_profile_observe_after_settle,
            )

            inter_candidate_on_profile_observe_after_settle()
        except Exception:
            pass
        log(
            "info",
            "visual_candidate_profile_observe_context_isolated",
            source_profile_username=source_profile_username,
            visual_candidate_id=pick_ctx.get("visual_candidate_id"),
            disable_followers_visual_fallback=True,
            omit_session_visual_fallback_detail=True,
            last_known_state_for_observe=NavigationEngineState.CANDIDATE_PROFILE.value,
        )
        log(
            "info",
            "visual_candidate_profile_fresh_observe_started",
            source_profile_username=source_profile_username,
            visual_candidate_id=pick_ctx.get("visual_candidate_id"),
        )
        nav_obs_local: dict[str, Any] = {}
        try:
            nav_ctx_trace: dict[str, Any] = {
                "phase": "candidate_profile_analysis",
                "visual_candidate_id": pick_ctx.get("visual_candidate_id"),
                "source_profile_username": source_profile_username,
                "disable_followers_visual_fallback": True,
                "expected_state": "CANDIDATE_PROFILE",
                "det": det_ctx if isinstance(det_ctx, dict) else None,
                "open_detection_method": open_det_method,
            }
            nav_obs_local = observe_instagram_state(
                d,
                expected_package=pkg,
                last_known_state=NavigationEngineState.CANDIDATE_PROFILE.value,
                context=nav_ctx_trace,
            )
            navigation_loop_state["last_state"] = str(nav_obs_local.get("state") or "")
            _sig = nav_obs_local.get("signals") or {}
            log(
                "info",
                "visual_candidate_profile_analysis_result",
                source_profile_username=source_profile_username,
                visual_candidate_id=pick_ctx.get("visual_candidate_id"),
                navigation_state=nav_obs_local.get("state"),
                navigation_confidence=nav_obs_local.get("confidence"),
                navigation_reason=nav_obs_local.get("reason"),
                xml_guess=nav_obs_local.get("xml_guess"),
                visual_guess=nav_obs_local.get("visual_guess"),
                signal_visual_match=_sig.get("visual_match"),
                signal_is_followers_list=_sig.get("is_followers_list"),
            )
        except Exception as exc:
            log(
                "error",
                "visual_candidate_profile_flow_failed",
                failure_reason="visual_candidate_state_unknown",
                error=str(exc),
                source_profile_username=source_profile_username,
                visual_candidate_id=pick_ctx.get("visual_candidate_id"),
            )
            return {}

        if str(nav_obs_local.get("state") or "") == NavigationEngineState.UNKNOWN.value and float(
            nav_obs_local.get("confidence") or 0.0
        ) < 0.35:
            log(
                "info",
                "visual_candidate_navigation_state_uncertain",
                failure_reason="visual_candidate_state_unknown",
                source_profile_username=source_profile_username,
                visual_candidate_id=pick_ctx.get("visual_candidate_id"),
                navigation_state=nav_obs_local.get("state"),
                navigation_confidence=nav_obs_local.get("confidence"),
                navigation_reason=nav_obs_local.get("reason"),
            )

        _private_profile_detected = False
        try:
            _priv = visual_detect_private_profile(
                d, source_profile_username=source_profile_username
            )
            _private_profile_detected = bool(_priv.get("private_profile_detected"))
            if _private_profile_detected:
                log(
                    "info",
                    "visual_candidate_private_profile_detected",
                    source_profile_username=source_profile_username,
                    visual_candidate_id=pick_ctx.get("visual_candidate_id"),
                    private_confidence=_priv.get("confidence"),
                    detection_method=_priv.get("detection_method"),
                )
        except Exception as exc:
            log(
                "warning",
                "visual_candidate_profile_flow_failed",
                failure_reason="visual_candidate_profile_analysis_failed",
                subphase="private_detect",
                error=str(exc),
                visual_candidate_id=pick_ctx.get("visual_candidate_id"),
            )

        _hdr = "unknown"
        try:
            _hdr = _follow_ui_state_snapshot(d)
            log(
                "info",
                "visual_candidate_follow_state_detected",
                source_profile_username=source_profile_username,
                visual_candidate_id=pick_ctx.get("visual_candidate_id"),
                follow_header_state=_hdr,
            )
        except Exception as exc:
            log(
                "warning",
                "visual_candidate_profile_flow_failed",
                failure_reason="visual_candidate_profile_analysis_failed",
                subphase="follow_header_snapshot",
                error=str(exc),
                visual_candidate_id=pick_ctx.get("visual_candidate_id"),
            )

        _enable_real_d = bool(getattr(config, "ENABLE_REAL_FOLLOW", False))
        _enable_vf_d = bool(getattr(config, "ENABLE_REAL_VISUAL_FOLLOW", False))
        _exec_d = _enable_real_d or (
            _followers_resolved_continue_to_follow and _enable_vf_d
        )
        _follow_priv_accounts = bool(getattr(config, "FOLLOW_PRIVATE_ACCOUNTS", False))
        _dry_mute = bool(getattr(config, "VISUAL_FOLLOW_MUTE_DRY_RUN", True))
        _dry_follow_open = bool(getattr(config, "VISUAL_FOLLOWERS_OPEN_DRY_RUN", True))
        _mute_flow_on = bool(getattr(config, "ENABLE_VISUAL_FOLLOW_MUTE_FLOW", False))
        _real_mute_after = bool(getattr(config, "ENABLE_REAL_VISUAL_MUTE_AFTER_FOLLOW", False))
        _nav_st = str(nav_obs_local.get("state") or "")
        _nav_cf = float(nav_obs_local.get("confidence") or 0.0)
        _profile_like_nav = _nav_st in (
            NavigationEngineState.PROFILE.value,
            NavigationEngineState.CANDIDATE_PROFILE.value,
            NavigationEngineState.PRIVATE_PROFILE.value,
        )
        _should_follow = bool(
            _exec_d
            and followers_list_ready
            and _hdr == "follow"
            and _profile_like_nav
            and (not _private_profile_detected or _follow_priv_accounts)
        )
        _should_mute = bool(_mute_flow_on and _real_mute_after and _should_follow)
        _decision_parts: list[str] = []
        if not _exec_d:
            _decision_parts.append("follow_execution_disabled")
        elif not followers_list_ready:
            _decision_parts.append("followers_list_not_ready")
        elif _hdr != "follow":
            _decision_parts.append(f"follow_header_not_invite:{_hdr}")
        elif not _profile_like_nav:
            _decision_parts.append(f"navigation_not_profile_like:{_nav_st}")
        elif _private_profile_detected and not _follow_priv_accounts:
            _decision_parts.append("private_profile_follow_private_accounts_disabled")
        else:
            _decision_parts.append("proceed_to_downstream_follow_gate")
        _decision_reason = "+".join(_decision_parts)
        log(
            "info",
            "visual_candidate_next_action_decision",
            source_profile_username=source_profile_username,
            visual_candidate_id=pick_ctx.get("visual_candidate_id"),
            navigation_state=_nav_st,
            navigation_confidence=_nav_cf,
            follow_header_state=_hdr,
            should_follow=_should_follow,
            should_mute=_should_mute,
            follow_private_accounts=_follow_priv_accounts,
            dry_run_follow_mute=_dry_mute,
            dry_run_followers_open=_dry_follow_open,
            reason=_decision_reason,
            follower_username_hint=str(follower_un_so_far or ""),
        )
        if _hdr == "follow" and not _should_follow:
            log(
                "info",
                "visual_candidate_follow_skipped",
                source_profile_username=source_profile_username,
                visual_candidate_id=pick_ctx.get("visual_candidate_id"),
                reason=_decision_reason,
                follow_header_state=_hdr,
                navigation_state=_nav_st,
            )

        try:
            _pend_rq, _pend_m = _visual_follow_request_pending_state(d)
            if _pend_rq:
                log(
                    "info",
                    "visual_candidate_pending_request_detected",
                    source_profile_username=source_profile_username,
                    visual_candidate_id=pick_ctx.get("visual_candidate_id"),
                    pending_method=_pend_m,
                )
        except Exception as exc:
            log(
                "warning",
                "visual_candidate_profile_flow_failed",
                failure_reason="visual_candidate_profile_analysis_failed",
                subphase="pending_request_detect",
                error=str(exc),
                visual_candidate_id=pick_ctx.get("visual_candidate_id"),
            )

        return nav_obs_local

    xml_stale_bypass_meta: dict[str, Any] = {}
    skip_followers_xml_detect_next_iter = False
    det_xml_last_for_bypass: dict[str, Any] | None = None

    def _collect_follower_candidates() -> list:
        return iter_followers_candidates(
            d,
            source_profile_username=source_profile_username,
            runtime_seen=_RUNTIME_SEEN_FOLLOWER_USERNAMES,
            source_account_context=account_id or None,
        )

    def _followers_xml_stale_engine_stop(
        *,
        stop_reason: str | None,
        det: dict,
        loop_iteration: int,
        xml_candidates: list | None = None,
    ) -> int | None:
        xml_stale_bypass_meta.clear()
        bypass_ok, bypass_reason = followers_bypass_xml_stale_recovery_if_visual_surface_strong(
            d,
            source_profile_username=source_profile_username,
            det=det,
            phase="xml_stale_engine_stop",
            stop_reason=stop_reason,
            loop_iteration=loop_iteration,
            session_visual_fallback_detail=visual_loop_state.get("session_vf_detail"),
            visual_xml_stale_grace_remaining=int(visual_loop_state.get("grace_remaining") or 0),
        )
        if bypass_ok:
            if bypass_reason == "session_visual_fallback_grace":
                visual_loop_state["grace_remaining"] = max(
                    0, int(visual_loop_state.get("grace_remaining") or 0) - 1
                )
            followers_engine_clear_stop_reason()
            xml_stale_bypass_meta["bypassed"] = True
            xml_stale_bypass_meta["bypass_reason"] = bypass_reason
            log(
                "info",
                "followers_visual_mode_continue",
                phase="xml_stale_engine_stop",
                bypass_reason=bypass_reason,
                stop_reason=stop_reason,
                loop_iteration=loop_iteration,
                source_profile_username=source_profile_username,
            )
            return None

        nav_ctx: dict[str, Any] = {
            "source_profile_username": source_profile_username,
            "det": det,
            "open_detection_method": open_detection_method,
            "session_visual_fallback_detail": visual_loop_state.get("session_vf_detail"),
            "stop_reason": stop_reason,
        }
        nav_obs = observe_instagram_state(
            d,
            expected_package=pkg,
            last_known_state=navigation_loop_state.get("last_state"),
            context=nav_ctx,
        )
        navigation_loop_state["last_state"] = str(nav_obs.get("state") or "")
        log(
            "info",
            "navigation_state_observed",
            state=nav_obs.get("state"),
            confidence=nav_obs.get("confidence"),
            reason=nav_obs.get("reason"),
            xml_guess=nav_obs.get("xml_guess"),
            visual_guess=nav_obs.get("visual_guess"),
            foreground_package=nav_obs.get("foreground_package"),
            stop_reason=stop_reason,
            loop_iteration=loop_iteration,
            source_profile_username=source_profile_username,
        )
        _nav_sig = nav_obs.get("signals") or {}
        if (
            str(nav_obs.get("state") or "") == NavigationEngineState.FOLLOWERS_LIST.value
            and float(nav_obs.get("confidence") or 0.0) >= 0.65
        ):
            if _nav_sig.get("visual_overrides_xml_profile_guess"):
                log(
                    "info",
                    "navigation_state_visual_override",
                    source_profile_username=source_profile_username,
                    stop_reason=stop_reason,
                    loop_iteration=loop_iteration,
                    xml_guess=nav_obs.get("xml_guess"),
                    confidence=nav_obs.get("confidence"),
                )
            log(
                "info",
                "navigation_state_xml_stale_bypassed",
                source_profile_username=source_profile_username,
                stop_reason=stop_reason,
                loop_iteration=loop_iteration,
                navigation_confidence=nav_obs.get("confidence"),
            )
            followers_engine_clear_stop_reason()
            return None

        pic = _try_visual_followers_picker_dry_run(
            d,
            open_list_meta=open_list_meta,
            source_profile_username=source_profile_username,
            source_account_context=account_id,
            run_id=run_id,
            t0=t0,
            warm_session_used=warm_session_used,
            force_stop_used=force_stop_used,
            eng_log=_eng_log,
            stop_reason=stop_reason,
            candidates=xml_candidates,
            iteration=loop_iteration,
            scroll_used=scroll_used,
            runtime_seen=_RUNTIME_SEEN_FOLLOWER_USERNAMES,
        )
        if pic == VISUAL_FOLLOW_HISTORY_CONTINUE:
            log(
                "info",
                "visual_followers_picker_restarted_after_reentry",
                source_profile_username=source_profile_username,
                stop_reason=stop_reason,
                loop_iteration=loop_iteration,
            )
            log(
                "info",
                "visual_followers_picker_restarted_after_continue",
                source_profile_username=source_profile_username,
                stop_reason=stop_reason,
                loop_iteration=loop_iteration,
            )
            return VISUAL_FOLLOW_HISTORY_CONTINUE
        if pic is not None:
            return pic
        sr = str(stop_reason or "unknown")
        payload = {
            "source_profile_username": source_profile_username,
            "open_detection_method": open_detection_method,
            "stop_reason": sr,
            "candidate_username_count": det.get("candidate_username_count"),
            "visible_usernames_sample": det.get("visible_usernames_sample"),
            "scroll_used": scroll_used,
            "iteration": loop_iteration,
            "current_activity": det.get("current_activity"),
            "current_package": det.get("current_package"),
        }
        log("error", "followers_engine_stopped_xml_stale", **payload)
        _eng_log(
            "followers_engine_stopped_xml_stale",
            "failed",
            sr,
            payload,
        )
        _emit_performance_summary(
            t0=t0,
            warm_session_used=warm_session_used,
            force_stop_used=force_stop_used,
            exit_code=44,
            target_username=source_profile_username,
        )
        return 44

    while processed < max_iter:
        followers_engine_loop_iteration += 1
        followers_clear_exploratory_scroll_permit_unused()
        exploratory_scroll_permit_armed_this_iter = False
        exploratory_scroll_profile_this_iter = "default"
        exploratory_scroll_reposition_meta: dict[str, Any] | None = None
        stop_r_loop = get_followers_engine_stop_reason()

        try:
            from followers_inter_candidate_perf import inter_candidate_on_loop_iteration

            inter_candidate_on_loop_iteration()
        except Exception:
            pass
        log(
            "info",
            "followers_loop_iteration",
            iteration=processed,
            source_profile_username=source_profile_username,
            scroll_used=scroll_used,
            open_detection_method=open_detection_method,
            stop_reason=stop_r_loop,
            prev_candidate_row_count=prev_candidate_row_count,
        )
        _eng_log(
            "followers_loop_iteration",
            "info",
            "followers_loop_iteration",
            {
                "iteration": processed,
                "scroll_used": scroll_used,
                "open_detection_method": open_detection_method,
                "stop_reason": stop_r_loop,
                "prev_candidate_row_count": prev_candidate_row_count,
            },
        )

        followers_xml_detect_skipped_this_iter = False
        _sess_vf_sync = visual_loop_state.get("session_vf_detail")
        if isinstance(_sess_vf_sync, dict) and _sess_vf_sync:
            session_vf_detail_for_loop = dict(_sess_vf_sync)
        _cm_loop_early = followers_session_committed_meta()
        _ca_at_loop_early = float(_cm_loop_early.get("followers_list_committed_at") or 0)
        _committed_age_loop_early = (
            round((time.perf_counter() - _ca_at_loop_early) * 1000.0, 2)
            if _ca_at_loop_early > 0
            else -1.0
        )
        _list_committed_loop_early = followers_session_list_committed_open_for(
            source_profile_username
        )
        try:
            from followers_inter_candidate_perf import (
                inter_candidate_segment_b_pre_picker_loop_detect_start,
            )

            inter_candidate_segment_b_pre_picker_loop_detect_start()
        except Exception:
            pass
        _skip_loop_detect, _skip_loop_detect_meta = should_skip_committed_loop_top_detect(
            open_list_meta,
            visual_loop_state,
            source_profile_username=source_profile_username,
            committed_age_ms=_committed_age_loop_early,
            list_committed_open=bool(_list_committed_loop_early),
        )
        if _skip_loop_detect:
            det = _followers_det_skip_redetect_after_visual_bypass(
                det_xml_last_for_bypass if isinstance(det_xml_last_for_bypass, dict) else {},
                session_vf_detail_for_loop,
                open_detection_method,
            )
            det_xml_last_for_bypass = det
            followers_xml_detect_skipped_this_iter = True
            try:
                log(
                    "info",
                    "followers_committed_loop_detect_skipped_evidence_fresh",
                    source_profile_username=source_profile_username,
                    loop_iteration=followers_engine_loop_iteration,
                    iteration=processed,
                    evidence_age_ms=_skip_loop_detect_meta.get("evidence_age_ms"),
                    committed_age_ms=_skip_loop_detect_meta.get("committed_age_ms"),
                    evidence_capture_reason=str(
                        _skip_loop_detect_meta.get("evidence_capture_reason") or ""
                    )[:80],
                    screenshot_path=str(_skip_loop_detect_meta.get("screenshot_path") or "")[
                        :400
                    ],
                    reason="committed_surface_evidence_fresh",
                )
            except Exception:
                pass
        elif (
            skip_followers_xml_detect_next_iter
            and det_xml_last_for_bypass is not None
            and isinstance(session_vf_detail_for_loop, dict)
            and bool(session_vf_detail_for_loop.get("visual_match"))
        ):
            skip_followers_xml_detect_next_iter = False
            det = _followers_det_skip_redetect_after_visual_bypass(
                det_xml_last_for_bypass,
                session_vf_detail_for_loop,
                open_detection_method,
            )
            followers_xml_detect_skipped_this_iter = True
            log(
                "info",
                "followers_xml_detect_skipped_after_visual_bypass",
                source_profile_username=source_profile_username,
                loop_iteration=followers_engine_loop_iteration,
                iteration=processed,
            )
        else:
            det = detect_followers_list_screen(d, source_profile_username=source_profile_username)
            det_xml_last_for_bypass = det
        try:
            from followers_inter_candidate_perf import (
                inter_candidate_segment_b_pre_picker_loop_detect_end,
            )

            inter_candidate_segment_b_pre_picker_loop_detect_end()
        except Exception:
            pass

        _det_odm = det.get("open_detection_method")
        if _det_odm:
            _new_odm = str(_det_odm)
            if _new_odm in FOLLOWERS_ENGINE_VISUAL_OPEN_METHODS:
                open_detection_method = _new_odm
            elif open_detection_method not in FOLLOWERS_ENGINE_VISUAL_OPEN_METHODS:
                open_detection_method = _new_odm

        _visual_followers_surface = (
            open_detection_method in FOLLOWERS_ENGINE_VISUAL_OPEN_METHODS
            or (
                isinstance(session_vf_detail_for_loop, dict)
                and bool(session_vf_detail_for_loop.get("visual_match"))
            )
        )

        try:
            _loop_pkg_meta = _followers_current_pkg_activity(d)
            _loop_pkg = str(_loop_pkg_meta.get("current_package") or "")
        except Exception:
            _loop_pkg = ""
        if _loop_pkg and _loop_pkg != pkg:
            log(
                "warning",
                "runner_wrong_surface_after_safe_stop",
                reason="foreground_package_mismatch",
                current_package=_loop_pkg,
                expected_package=pkg,
                source_profile_username=source_profile_username,
                iteration=processed,
            )
            invalidate_followers_injection_evidence(
                open_list_meta,
                visual_loop_state,
                reason="foreground_package_mismatch",
                source_profile_username=source_profile_username,
            )
            runner_invalidate_visual_followers_session_after_safe_stop(
                d,
                source_profile_username=source_profile_username,
            )
            _emit_performance_summary(
                t0=t0,
                warm_session_used=warm_session_used,
                force_stop_used=force_stop_used,
                exit_code=96,
                target_username=source_profile_username,
            )
            return 96
        if not verify_app_foreground(d, pkg):
            log(
                "warning",
                "runner_wrong_surface_after_safe_stop",
                reason="verify_app_foreground_false",
                source_profile_username=source_profile_username,
                iteration=processed,
                expected_package=pkg,
            )
            invalidate_followers_injection_evidence(
                open_list_meta,
                visual_loop_state,
                reason="verify_app_foreground_false",
                source_profile_username=source_profile_username,
            )
            runner_invalidate_visual_followers_session_after_safe_stop(
                d,
                source_profile_username=source_profile_username,
            )
            _emit_performance_summary(
                t0=t0,
                warm_session_used=warm_session_used,
                force_stop_used=force_stop_used,
                exit_code=96,
                target_username=source_profile_username,
            )
            return 96
        try:
            nav_obs_fg = observe_instagram_state(
                d,
                expected_package=pkg,
                last_known_state=str(
                    navigation_loop_state.get("last_state")
                    or NavigationEngineState.FOLLOWERS_LIST.value
                ),
                context={
                    "phase": "followers_loop_iter_guard",
                    "det": det if isinstance(det, dict) else {},
                },
            )
            navigation_loop_state["last_state"] = str(nav_obs_fg.get("state") or "")
            _st_nav = str(nav_obs_fg.get("state") or "")
            if _st_nav == NavigationEngineState.LAUNCHER_WRONG_SURFACE.value:
                log(
                    "warning",
                    "runner_wrong_surface_after_safe_stop",
                    reason="launcher_wrong_surface",
                    navigation_state=_st_nav,
                    source_profile_username=source_profile_username,
                    iteration=processed,
                    foreground_package=str(nav_obs_fg.get("foreground_package") or ""),
                )
                invalidate_followers_injection_evidence(
                    open_list_meta,
                    visual_loop_state,
                    reason="launcher_wrong_surface",
                    source_profile_username=source_profile_username,
                )
                runner_invalidate_visual_followers_session_after_safe_stop(
                    d,
                    source_profile_username=source_profile_username,
                )
                _emit_performance_summary(
                    t0=t0,
                    warm_session_used=warm_session_used,
                    force_stop_used=force_stop_used,
                    exit_code=96,
                    target_username=source_profile_username,
                )
                return 96
            _scr = str(
                nav_obs_fg.get("screen_class") or nav_obs_fg.get("visual_guess") or ""
            ).strip().lower()
            if _scr == "other" and _st_nav != NavigationEngineState.FOLLOWERS_LIST.value:
                log(
                    "warning",
                    "runner_wrong_surface_after_safe_stop",
                    reason="screen_class_other",
                    screen_class=_scr,
                    navigation_state=_st_nav,
                    source_profile_username=source_profile_username,
                    iteration=processed,
                )
                invalidate_followers_injection_evidence(
                    open_list_meta,
                    visual_loop_state,
                    reason="screen_class_other",
                    source_profile_username=source_profile_username,
                )
                runner_invalidate_visual_followers_session_after_safe_stop(
                    d,
                    source_profile_username=source_profile_username,
                )
                _emit_performance_summary(
                    t0=t0,
                    warm_session_used=warm_session_used,
                    force_stop_used=force_stop_used,
                    exit_code=96,
                    target_username=source_profile_username,
                )
                return 96
        except Exception as e_nav:
            log(
                "warning",
                "followers_loop_surface_guard_observe_failed",
                error=str(e_nav),
                source_profile_username=source_profile_username,
            )

        bypassed_xml_stale_this_iter = False

        if (
            _visual_followers_surface
            and stop_r_loop in FOLLOWERS_ENGINE_XML_STALE_EXIT_REASONS
        ):
            _stale_pic = _followers_xml_stale_engine_stop(
                stop_reason=stop_r_loop,
                det=det,
                loop_iteration=followers_engine_loop_iteration,
            )
            if xml_stale_bypass_meta.get("bypassed"):
                skip_followers_xml_detect_next_iter = True
                log(
                    "info",
                    "followers_candidate_collection_reentered_after_visual_bypass",
                    phase="xml_stale_before_candidates",
                    bypass_reason=xml_stale_bypass_meta.get("bypass_reason"),
                    loop_iteration=followers_engine_loop_iteration,
                    source_profile_username=source_profile_username,
                    open_detection_method=open_detection_method,
                )
            if _stale_pic == VISUAL_FOLLOW_HISTORY_CONTINUE:
                log(
                    "info",
                    "followers_candidate_collection_reentered_after_visual_bypass",
                    phase="xml_stale_before_candidates_visual_continue_fallthrough",
                    source_profile_username=source_profile_username,
                    loop_iteration=followers_engine_loop_iteration,
                    iteration=processed,
                )
                log(
                    "info",
                    "visual_followers_loop_continue_after_reentry",
                    source_profile_username=source_profile_username,
                    phase="xml_stale_before_candidates",
                    iteration=processed,
                )
                log(
                    "info",
                    "visual_followers_loop_continued_after_reentry",
                    source_profile_username=source_profile_username,
                    phase="xml_stale_before_candidates",
                    iteration=processed,
                )
                bypassed_xml_stale_this_iter = True
            elif _stale_pic is not None:
                return _stale_pic
            else:
                bypassed_xml_stale_this_iter = True

        if bool(visual_loop_state.get("post_return_picker_refresh_pending")):
            _followers_try_post_return_picker_injection_refresh(
                d,
                open_list_meta,
                visual_loop_state=visual_loop_state,
                source_profile_username=source_profile_username,
            )

        if not det.get("is_followers_list"):
            if followers_session_list_committed_open_for(source_profile_username):
                _cm = followers_session_committed_meta()
                _ca_at = float(_cm.get("followers_list_committed_at") or 0)
                _committed_age_ms = (
                    round((time.perf_counter() - _ca_at) * 1000.0, 2) if _ca_at > 0 else 0.0
                )
                log(
                    "info",
                    "followers_engine_reuse_committed_followers_surface",
                    source_profile_username=source_profile_username,
                    committed_source=str(_cm.get("followers_list_committed_source") or ""),
                    det_is_followers_list=bool(det.get("is_followers_list")),
                    current_screen_guess=str(det.get("current_screen_guess") or ""),
                    committed_age_ms=_committed_age_ms,
                )
                det_merged = followers_session_merge_det_for_committed_visual_surface(
                    det,
                    session_vf_detail_for_loop=session_vf_detail_for_loop,
                    open_list_meta=open_list_meta,
                )
                try:
                    from followers_inter_candidate_perf import (
                        inter_candidate_segment_b_pre_picker_committed_revalidate_start,
                    )

                    inter_candidate_segment_b_pre_picker_committed_revalidate_start()
                except Exception:
                    pass
                _light_st, _light_meta = followers_committed_surface_light_revalidate(
                    d,
                    source_profile_username=source_profile_username,
                    expected_package=pkg,
                    det_merged=det_merged,
                    committed_age_ms=_committed_age_ms,
                    open_list_meta=open_list_meta,
                    session_vf_detail_for_loop=session_vf_detail_for_loop,
                )
                _light_meta_dict = _light_meta if isinstance(_light_meta, dict) else {}
                _ps_latest_inj = ""
                if isinstance(open_list_meta, dict):
                    _ps_latest_inj = str(
                        open_list_meta.get("latest_followers_injection_screenshot_path") or ""
                    ).strip()
                if _light_st == "light_ok":
                    try:
                        log(
                            "info",
                            "committed_followers_surface_revalidated_light",
                            source_profile_username=source_profile_username,
                            committed_source=str(_cm.get("followers_list_committed_source") or ""),
                            committed_age_ms=_committed_age_ms,
                            light_meta=dict(_light_meta) if isinstance(_light_meta, dict) else {},
                        )
                    except Exception:
                        pass
                    det = dict(det_merged)
                    if not bool(det.get("is_followers_list")):
                        det["is_followers_list"] = True
                    _sigs = list(det.get("signals") or [])
                    if "committed_followers_surface_revalidated_light" not in _sigs:
                        _sigs.append("committed_followers_surface_revalidated_light")
                    det["signals"] = _sigs
                    open_detection_method = str(
                        det.get("open_detection_method") or open_detection_method or ""
                    )
                    if open_detection_method.strip():
                        _followers_set_last_open_detection_method(open_detection_method)
                    _vfd_sync = det.get("visual_fallback_detail")
                    if isinstance(_vfd_sync, dict) and _vfd_sync:
                        session_vf_detail_for_loop = dict(_vfd_sync)
                        visual_loop_state["session_vf_detail"] = session_vf_detail_for_loop
                    set_committed_light_revalidate_ok(visual_loop_state, ok=True)
                elif _light_st == "wrong_surface":
                    invalidate_followers_injection_evidence(
                        open_list_meta,
                        visual_loop_state,
                        reason="committed_light_wrong_surface",
                        source_profile_username=source_profile_username,
                    )
                    set_committed_light_revalidate_ok(visual_loop_state, ok=False)
                    log(
                        "warning",
                        "followers_engine_committed_surface_lost",
                        source_profile_username=source_profile_username,
                        revalidate_ok=False,
                        revalidate_meta=dict(_light_meta) if isinstance(_light_meta, dict) else {},
                        loss_reason="committed_light_wrong_surface",
                        **followers_session_committed_meta(),
                    )
                    _eng_log(
                        "followers_engine_committed_surface_lost",
                        "failed",
                        str(
                            (_light_meta or {}).get("reason")
                            if isinstance(_light_meta, dict)
                            else "wrong_surface"
                        ),
                        {
                            "source_profile_username": source_profile_username,
                            "revalidate_meta": _light_meta,
                        },
                    )
                    followers_session_clear_list_committed_open(source_profile_username)
                    return 42
                elif (
                    _light_st == "light_inconclusive"
                    and str(_light_meta_dict.get("reason") or "")
                    == "vision_rejected_on_committed_shot"
                    and str(_cm.get("followers_list_committed_source") or "")
                    in FOLLOWERS_RENDERED_STRONG_COMMITTED_SOURCES
                    and str(_cm.get("followers_list_committed_for") or "").strip()
                    == str(source_profile_username or "").strip()
                    and followers_session_list_committed_open_for(source_profile_username)
                    and bool(_ps_latest_inj and os.path.isfile(_ps_latest_inj))
                ):
                    try:
                        log(
                            "info",
                            "followers_committed_surface_post_scroll_vision_rejection_tolerated",
                            source_profile_username=source_profile_username,
                            light_status=str(_light_st or ""),
                            light_reason=str(_light_meta_dict.get("reason") or ""),
                            screenshot_path=str(_light_meta_dict.get("screenshot_path") or "")[:400],
                            followers_list_committed_source=str(
                                _cm.get("followers_list_committed_source") or ""
                            ),
                            latest_followers_injection_screenshot_path=_ps_latest_inj,
                            quick_revalidate_skipped=True,
                            continue_basis="committed_rendered_strong_post_scroll",
                        )
                    except Exception:
                        pass
                    try:
                        log(
                            "info",
                            "committed_followers_surface_revalidated_light",
                            source_profile_username=source_profile_username,
                            committed_source=str(_cm.get("followers_list_committed_source") or ""),
                            committed_age_ms=_committed_age_ms,
                            light_meta=dict(_light_meta_dict),
                        )
                    except Exception:
                        pass
                    det = dict(det_merged)
                    if not bool(det.get("is_followers_list")):
                        det["is_followers_list"] = True
                    _sigs = list(det.get("signals") or [])
                    if "committed_followers_surface_revalidated_light" not in _sigs:
                        _sigs.append("committed_followers_surface_revalidated_light")
                    det["signals"] = _sigs
                    open_detection_method = str(
                        det.get("open_detection_method") or open_detection_method or ""
                    )
                    if open_detection_method.strip():
                        _followers_set_last_open_detection_method(open_detection_method)
                    _vfd_sync = det.get("visual_fallback_detail")
                    if isinstance(_vfd_sync, dict) and _vfd_sync:
                        session_vf_detail_for_loop = dict(_vfd_sync)
                        visual_loop_state["session_vf_detail"] = session_vf_detail_for_loop
                    set_committed_light_revalidate_ok(visual_loop_state, ok=True)
                else:
                    rv_ok, rv_meta = followers_surface_quick_revalidate(
                        d,
                        source_profile_username=source_profile_username,
                        max_seconds=4.75,
                    )
                    if rv_ok:
                        try:
                            det = detect_followers_list_screen(
                                d, source_profile_username=source_profile_username
                            )
                        except Exception:
                            det = det_merged
                        det = followers_session_merge_det_for_committed_visual_surface(
                            det,
                            session_vf_detail_for_loop=session_vf_detail_for_loop,
                            open_list_meta=open_list_meta,
                        )
                        if not bool(det.get("is_followers_list")):
                            det["is_followers_list"] = True
                            _sigs = list(det.get("signals") or [])
                            if "committed_followers_surface_revalidated" not in _sigs:
                                _sigs.append("committed_followers_surface_revalidated")
                            det["signals"] = _sigs
                    else:
                        _rv_reason = str((rv_meta or {}).get("reason") or "")
                        _rv_stage = str((rv_meta or {}).get("stage") or "")
                        _detect_ms = None
                        if isinstance(rv_meta, dict):
                            _ems = rv_meta.get("elapsed_ms_stage") or {}
                            if isinstance(_ems, dict) and _ems.get("detect_ms") is not None:
                                _detect_ms = float(_ems.get("detect_ms"))
                        if _rv_reason == "time_budget_exceeded":
                            try:
                                log(
                                    "info",
                                    "followers_engine_committed_surface_revalidate_budget_exceeded",
                                    source_profile_username=source_profile_username,
                                    committed_source=str(_cm.get("followers_list_committed_source") or ""),
                                    detect_ms=_detect_ms,
                                    max_seconds=4.75,
                                    committed_age_ms=_committed_age_ms,
                                    revalidate_stage=_rv_stage,
                                    revalidate_status="revalidate_inconclusive_budget_exceeded",
                                    fallback_action="continue_with_committed_visual_merge",
                                    light_path_status=_light_st,
                                    light_path_meta=dict(_light_meta)
                                    if isinstance(_light_meta, dict)
                                    else {},
                                )
                            except Exception:
                                pass
                            try:
                                log(
                                    "info",
                                    "followers_engine_reuse_committed_surface_with_recent_visual_commit",
                                    source_profile_username=source_profile_username,
                                    committed_source=str(_cm.get("followers_list_committed_source") or ""),
                                    committed_age_ms=_committed_age_ms,
                                    revalidate_status="revalidate_inconclusive_budget_exceeded",
                                )
                            except Exception:
                                pass
                            det = dict(det_merged)
                            if not bool(det.get("is_followers_list")):
                                det["is_followers_list"] = True
                            _sigs = list(det.get("signals") or [])
                            for _tag in (
                                "committed_visual_surface_merge",
                                "committed_followers_budget_inconclusive_continue",
                            ):
                                if _tag not in _sigs:
                                    _sigs.append(_tag)
                            det["signals"] = _sigs
                            set_committed_light_revalidate_ok(visual_loop_state, ok=True)
                        else:
                            invalidate_followers_injection_evidence(
                                open_list_meta,
                                visual_loop_state,
                                reason="committed_surface_revalidate_failed",
                                source_profile_username=source_profile_username,
                            )
                            set_committed_light_revalidate_ok(visual_loop_state, ok=False)
                            log(
                                "warning",
                                "followers_engine_committed_surface_lost",
                                source_profile_username=source_profile_username,
                                revalidate_ok=False,
                                revalidate_meta=dict(rv_meta) if isinstance(rv_meta, dict) else {},
                                **followers_session_committed_meta(),
                            )
                            _eng_log(
                                "followers_engine_committed_surface_lost",
                                "failed",
                                _rv_reason or "revalidate_failed",
                                {
                                    "source_profile_username": source_profile_username,
                                    "revalidate_meta": rv_meta,
                                },
                            )
                            followers_session_clear_list_committed_open(source_profile_username)
                            return 42
                try:
                    from followers_inter_candidate_perf import (
                        inter_candidate_segment_b_pre_picker_committed_revalidate_end,
                    )

                    inter_candidate_segment_b_pre_picker_committed_revalidate_end()
                except Exception:
                    pass
            elif _visual_followers_surface and not bypassed_xml_stale_this_iter:
                _stale_pic = _followers_xml_stale_engine_stop(
                    stop_reason=stop_r_loop or "visual_fallback_xml_not_followers_list",
                    det=det,
                    loop_iteration=followers_engine_loop_iteration,
                )
                if xml_stale_bypass_meta.get("bypassed"):
                    skip_followers_xml_detect_next_iter = True
                    log(
                        "info",
                        "followers_candidate_collection_reentered_after_visual_bypass",
                        phase="xml_stale_not_followers_list",
                        bypass_reason=xml_stale_bypass_meta.get("bypass_reason"),
                        loop_iteration=followers_engine_loop_iteration,
                        source_profile_username=source_profile_username,
                        open_detection_method=open_detection_method,
                    )
                if _stale_pic == VISUAL_FOLLOW_HISTORY_CONTINUE:
                    log(
                        "info",
                        "followers_candidate_collection_reentered_after_visual_bypass",
                        phase="xml_stale_not_followers_list_visual_continue_fallthrough",
                        source_profile_username=source_profile_username,
                        loop_iteration=followers_engine_loop_iteration,
                        iteration=processed,
                    )
                    log(
                        "info",
                        "visual_followers_loop_continue_after_reentry",
                        source_profile_username=source_profile_username,
                        phase="xml_stale_not_followers_list",
                        iteration=processed,
                    )
                    log(
                        "info",
                        "visual_followers_loop_continued_after_reentry",
                        source_profile_username=source_profile_username,
                        phase="xml_stale_not_followers_list",
                        iteration=processed,
                    )
                    bypassed_xml_stale_this_iter = True
                elif _stale_pic is not None:
                    return _stale_pic
                else:
                    bypassed_xml_stale_this_iter = True
            if not bypassed_xml_stale_this_iter and not followers_session_list_committed_open_for(
                source_profile_username
            ):
                ok_rec, how = return_to_followers_list(d, source_profile_username, pkg)
                if not ok_rec:
                    _eng_log(
                        "followers_list_engine_return_failed",
                        "failed",
                        "lost_surface_recovery_failed",
                        {"how": how},
                    )
                    return 42
                if how == "reopen_from_source_profile":
                    _eng_log(
                        "followers_list_reopen_fallback",
                        "warning",
                        "reopened_from_source_profile",
                        {"source_profile_username": source_profile_username},
                    )
                _eng_log("followers_list_recovered", "success", "surface_restored", {"method": how})
                continue

        log(
            "info",
            "followers_candidate_collection_started",
            source_profile_username=source_profile_username,
            loop_iteration=followers_engine_loop_iteration,
            iteration=processed,
            open_detection_method=open_detection_method,
            followers_xml_detect_skipped=bool(followers_xml_detect_skipped_this_iter),
        )
        try:
            candidates = _collect_follower_candidates()
        except Exception as _collect_exc:
            log(
                "error",
                "followers_collect_candidates_failed",
                loop_iteration=followers_engine_loop_iteration,
                source_profile_username=source_profile_username,
                error=str(_collect_exc),
            )
            raise
        log(
            "info",
            "followers_after_collect_candidates_reached",
            loop_iteration=followers_engine_loop_iteration,
            open_detection_method=open_detection_method,
            candidates_len=len(candidates) if isinstance(candidates, list) else -1,
            source_profile_username=source_profile_username,
        )
        _odm_open_meta_gate = str(open_list_meta.get("open_detection_method") or "")
        if str(open_detection_method) == "visual_fallback" or _odm_open_meta_gate == "visual_fallback":
            _svf_early = (
                session_vf_detail_for_loop if isinstance(session_vf_detail_for_loop, dict) else {}
            )
            _early_shot = str(
                _svf_early.get("screenshot_path_used") or _svf_early.get("screenshot_path") or ""
            )
            _early_shot_ex = bool(_early_shot) and os.path.exists(_early_shot)
            _early_cc = (
                int(det.get("candidate_username_count", 0) or 0) if isinstance(det, dict) else 0
            )
            log(
                "info",
                "followers_visual_row_mapping_gate_checked",
                loop_iteration=followers_engine_loop_iteration,
                gate_phase="immediate_after_collect_visual_fallback",
                len_candidates=len(candidates) if isinstance(candidates, list) else -1,
                candidate_username_count=_early_cc,
                visual_followers_surface=(
                    str(open_detection_method) == "visual_fallback"
                    or _odm_open_meta_gate == "visual_fallback"
                ),
                vf_rows_ok=False,
                visual_match=bool(_svf_early.get("visual_match")),
                visual_confidence=_svf_early.get("visual_confidence"),
                visual_user_rows_detected=_svf_early.get("visual_user_rows_detected"),
                visual_follow_button_count=_svf_early.get("visual_follow_button_count"),
                screenshot_path=_early_shot,
                screenshot_path_exists=_early_shot_ex,
                open_detection_method=open_detection_method,
                open_list_meta_open_detection_method=_odm_open_meta_gate,
                enabled_visual_picker=bool(
                    getattr(config, "ENABLE_VISUAL_FOLLOWERS_CANDIDATE_PICKER", False)
                ),
                enabled_row_mapping=bool(
                    getattr(config, "ENABLE_VISUAL_FOLLOWERS_ROW_MAPPING", True)
                ),
                gate_passed=False,
                gate_block_reason="pending_full_eval",
                source_profile_username=source_profile_username,
            )
        vf_evidence: dict | None = None
        _committed_src_gate_meta = str(
            (followers_session_committed_meta() or {}).get("followers_list_committed_source") or ""
        )
        if isinstance(det, dict):
            _vfd_loop = det.get("visual_fallback_detail")
            if isinstance(_vfd_loop, dict):
                if bool(_vfd_loop.get("visual_match")):
                    vf_evidence = _vfd_loop
                elif (
                    _committed_src_gate_meta in FOLLOWERS_RENDERED_STRONG_COMMITTED_SOURCES
                    and _followers_committed_rendered_strong_visual_ok(_vfd_loop)
                ):
                    vf_evidence = _vfd_loop
        if vf_evidence is None and isinstance(session_vf_detail_for_loop, dict):
            vf_evidence = session_vf_detail_for_loop

        _min_vf_conf = float(getattr(config, "FOLLOWERS_VISUAL_FALLBACK_MIN_CONFIDENCE", 0.65))
        _vf_standard_ok = (
            isinstance(vf_evidence, dict)
            and bool(vf_evidence.get("visual_match"))
            and float(vf_evidence.get("visual_confidence") or 0.0) >= _min_vf_conf
            and (
                int(vf_evidence.get("visual_user_rows_detected") or 0) >= 2
                or int(vf_evidence.get("visual_follow_button_count") or 0) >= 1
            )
        )
        _vf_rendered_strong_committed_ok = (
            _committed_src_gate_meta in FOLLOWERS_RENDERED_STRONG_COMMITTED_SOURCES
            and isinstance(vf_evidence, dict)
            and _followers_committed_rendered_strong_visual_ok(vf_evidence)
        )
        _vf_rows_ok = bool(_vf_standard_ok or _vf_rendered_strong_committed_ok)

        _gate_shot = _followers_injection_screenshot_path(
            open_list_meta, session_vf_detail_for_loop
        )
        _xml_cc_gate = int(det.get("candidate_username_count") or 0)
        _vf_g = vf_evidence if isinstance(vf_evidence, dict) else {}
        _gate_shot_str = str(_gate_shot) if _gate_shot else ""
        _screenshot_path_exists = bool(_gate_shot_str) and os.path.isfile(_gate_shot_str)
        enabled_visual_picker_gate = bool(
            getattr(config, "ENABLE_VISUAL_FOLLOWERS_CANDIDATE_PICKER", False)
        )
        enabled_row_mapping_gate = bool(
            getattr(config, "ENABLE_VISUAL_FOLLOWERS_ROW_MAPPING", True)
        )

        _log_visual_row_mapping_gate = (
            str(open_detection_method) == "visual_fallback"
            or _odm_open_meta_gate == "visual_fallback"
            or open_detection_method in FOLLOWERS_ENGINE_VISUAL_OPEN_METHODS
            or bool(_visual_followers_surface)
        )
        _gate_surface_ok = (
            bool(_visual_followers_surface)
            or str(open_detection_method) == "visual_fallback"
            or _odm_open_meta_gate == "visual_fallback"
        )

        gate_passed = False
        gate_block_reason = "unknown"
        if _log_visual_row_mapping_gate:
            if len(candidates) > 0:
                gate_block_reason = "candidates_not_empty"
            elif not _gate_surface_ok:
                gate_block_reason = "not_visual_followers_surface"
            elif not _vf_rows_ok:
                gate_block_reason = "vf_rows_not_ok"
            elif _xml_cc_gate != 0:
                gate_block_reason = "candidate_username_count_not_zero"
            elif not _gate_shot_str:
                gate_block_reason = "missing_screenshot_path"
            elif not _screenshot_path_exists:
                gate_block_reason = "screenshot_file_missing"
            elif not (enabled_visual_picker_gate or enabled_row_mapping_gate):
                gate_block_reason = "visual_picker_disabled"
            else:
                gate_passed = True
                gate_block_reason = (
                    "committed_rendered_strong_visual_ok"
                    if _vf_rendered_strong_committed_ok and not _vf_standard_ok
                    else "ok"
                )
            log(
                "info",
                "followers_visual_row_mapping_gate_checked",
                loop_iteration=followers_engine_loop_iteration,
                gate_phase="full_eval",
                len_candidates=len(candidates),
                candidate_username_count=_xml_cc_gate,
                visual_followers_surface=bool(_gate_surface_ok),
                vf_rows_ok=bool(_vf_rows_ok),
                visual_match=bool(_vf_g.get("visual_match")),
                visual_confidence=_vf_g.get("visual_confidence"),
                visual_user_rows_detected=_vf_g.get("visual_user_rows_detected"),
                visual_follow_button_count=_vf_g.get("visual_follow_button_count"),
                screenshot_path=_gate_shot_str,
                screenshot_path_exists=_screenshot_path_exists,
                open_detection_method=open_detection_method,
                enabled_visual_picker=enabled_visual_picker_gate,
                enabled_row_mapping=enabled_row_mapping_gate,
                gate_passed=gate_passed,
                gate_block_reason=gate_block_reason,
                committed_rendered_strong_visual_ok=bool(_vf_rendered_strong_committed_ok),
                source_profile_username=source_profile_username,
            )
            try:
                from followers_inter_candidate_perf import (
                    inter_candidate_segment_b_note_gate,
                )

                inter_candidate_segment_b_note_gate(
                    gate_passed=bool(gate_passed),
                    gate_block_reason=str(gate_block_reason or ""),
                    visual_follow_button_count=int(_vf_g.get("visual_follow_button_count") or 0),
                    visual_user_rows_detected=int(
                        _vf_g.get("visual_user_rows_detected") or 0
                    ),
                )
            except Exception:
                pass
            if (
                gate_passed
                and _vf_rendered_strong_committed_ok
                and not _vf_standard_ok
            ):
                log(
                    "info",
                    "followers_visual_row_mapping_gate_rendered_strong_committed_allowed",
                    loop_iteration=followers_engine_loop_iteration,
                    gate_passed=True,
                    gate_reason="committed_rendered_strong_visual_ok",
                    open_detection_method=open_detection_method,
                    source_profile_username=source_profile_username,
                )

        _vp_inj_for_defer: dict | None = None
        _row_mapping_diag: dict[str, Any] = {}
        if gate_passed:
            _shot_inj = _gate_shot_str
            if _shot_inj:
                _inj: list = []
                if bool(getattr(config, "ENABLE_VISUAL_FOLLOWERS_CANDIDATE_PICKER", False)):
                    _acct_inj = str(
                        getattr(config, "VISUAL_FOLLOWERS_ACTION_ACCOUNT_USERNAME", "") or ""
                    ).strip()
                    try:
                        from followers_inter_candidate_perf import (
                            inter_candidate_segment_b_picker_phase_start,
                        )

                        inter_candidate_segment_b_picker_phase_start()
                    except Exception:
                        pass
                    _vp_inj = visual_extract_followers_candidates_from_screenshot(
                        d,
                        screenshot_path=_shot_inj,
                        source_profile_username=source_profile_username,
                        runtime_seen=_RUNTIME_SEEN_FOLLOWER_USERNAMES,
                        action_account_username=_acct_inj if _acct_inj else None,
                        phase="followers_engine_inject",
                    )
                    try:
                        log(
                            "info",
                            "followers_candidate_picker_returned",
                            candidate_count=int(_vp_inj.get("candidate_count") or 0),
                            picker_scroll_rounds=int(_vp_inj.get("picker_scroll_rounds") or 0),
                            source_profile_username=source_profile_username,
                            screenshot_path=str(_shot_inj),
                            picker_error=str(_vp_inj.get("picker_error") or ""),
                        )
                        if int(_vp_inj.get("candidate_count") or 0) == 0:
                            try:
                                from followers_inter_candidate_perf import (
                                    inter_candidate_on_picker_empty,
                                )

                                inter_candidate_on_picker_empty()
                            except Exception:
                                pass
                    except Exception:
                        pass
                    _inj = list(_vp_inj.get("candidates") or [])
                    if isinstance(_vp_inj, dict):
                        _vp_inj_for_defer = dict(_vp_inj)
                    try:
                        from followers_inter_candidate_perf import (
                            inter_candidate_segment_b_note_picker_result,
                        )

                        inter_candidate_segment_b_note_picker_result(_vp_inj)
                    except Exception:
                        pass
                if not _inj:
                    _max_map = int(
                        getattr(config, "VISUAL_FOLLOWERS_MAX_CANDIDATES_PER_SCREEN", 5) or 5
                    )
                    try:
                        from followers_inter_candidate_perf import (
                            inter_candidate_segment_b_mapping_phase_start,
                        )

                        inter_candidate_segment_b_mapping_phase_start()
                    except Exception:
                        pass
                    _mapped, _row_mapping_diag = visual_map_followers_rows_from_screenshot(
                        _shot_inj,
                        max_candidates=_max_map,
                        min_confidence=_min_vf_conf,
                    )
                    _inj = visual_row_mapping_to_follower_engine_candidates(
                        _mapped,
                        source_profile_username=source_profile_username,
                    )
                    try:
                        from followers_inter_candidate_perf import (
                            inter_candidate_segment_b_note_mapping_result,
                        )

                        inter_candidate_segment_b_note_mapping_result(_row_mapping_diag)
                    except Exception:
                        pass
                if _inj:
                    candidates = _inj
                    followers_engine_clear_stop_reason()
                    _src_inj = (
                        "visual_row_mapping"
                        if any(
                            str(c.get("selection_method") or "") == "visual_row_mapping"
                            for c in _inj
                        )
                        else "visual_extract_followers_candidates_from_screenshot"
                    )
                    log(
                        "info",
                        "followers_visual_mode_continue",
                        phase="injected_visual_candidates_from_open_shot",
                        candidate_count=len(_inj),
                        screenshot_path=_shot_inj,
                        source_profile_username=source_profile_username,
                        injection_source=_src_inj,
                    )
                    try:
                        from followers_inter_candidate_perf import (
                            inter_candidate_segment_b_note_candidates_injected,
                        )

                        inter_candidate_segment_b_note_candidates_injected(count=len(_inj))
                    except Exception:
                        pass
        if len(candidates) > 0:
            visible_follow_buttons_stall_scrolls = 0
            visual_loop_state["list_progressive_exploration_passes_used"] = 0
            visual_loop_state["list_progressive_exploration_active"] = False
            visual_loop_state["list_progressive_exploration_exhausted"] = False
            visual_loop_state["list_progressive_exploration_last_empty_reason"] = ""
            visual_loop_state["list_progressive_exploration_last_scroll_profile"] = ""
        prev_candidate_row_count = len(candidates)
        stop_r_after_candidates = get_followers_engine_stop_reason()
        _empty_reason_pick = ""
        _picker_err_pick = ""
        if isinstance(_vp_inj_for_defer, dict):
            _empty_reason_pick = str(
                _vp_inj_for_defer.get("empty_reason")
                or _vp_inj_for_defer.get("reason")
                or ""
            )
            _picker_err_pick = str(_vp_inj_for_defer.get("picker_error") or "")
        _defer_xml_stale_pick_legit_empty_xml_open = (
            str(stop_r_after_candidates or "") == "visual_open_xml_empty"
            and bool(_vf_rendered_strong_committed_ok)
            and bool(getattr(config, "ENABLE_VISUAL_FOLLOWERS_CANDIDATE_PICKER", False))
            and _picker_err_pick == ""
            and _empty_reason_pick in ("no_blue_follow_spans", "no_rows_after_filter")
        )
        _defer_xml_stale_pick_legit_unsafe_low_follow_only = (
            str(stop_r_after_candidates or "") == "visual_open_xml_empty"
            and bool(_vf_rendered_strong_committed_ok)
            and bool(getattr(config, "ENABLE_VISUAL_FOLLOWERS_CANDIDATE_PICKER", False))
            and _picker_err_pick in ("", "vision_validation_rejected")
            and int(_row_mapping_diag.get("cta_allowed_count") or 0) > 0
            and int(_row_mapping_diag.get("mapped_count") or 0) == 0
            and str(_row_mapping_diag.get("row_mapping_empty_reason") or "")
            == "no_tap_safe_visual_candidate_after_cta_allowed"
            and _followers_row_mapping_skip_reasons_only_tap_y_outside_safe_vertical_band(
                _row_mapping_diag.get("row_mapping_skip_reasons")
            )
        )
        # Defer XML-stale abort toward scroll when the list is structurally committed
        # (rendered-strong) even if open_detection_method stayed "xml" and session_vf
        # has no visual_match (zero-CTA path). Do not widen global _visual_followers_surface.
        _defer_visual_followers_surface_ok = bool(_visual_followers_surface) or bool(
            _vf_rendered_strong_committed_ok
        )
        _fbc_defer = 0
        if isinstance(det, dict):
            _fbc_defer = int(det.get("follow_buttons_visual_count") or 0)
        if _fbc_defer <= 0 and isinstance(session_vf_detail_for_loop, dict):
            _fbc_defer = int(session_vf_detail_for_loop.get("visual_follow_button_count") or 0)
        _vision_rejected_pick = _picker_err_pick == "vision_validation_rejected" or (
            _empty_reason_pick == "vision_validation_rejected"
        )
        _defer_xml_stale_pick_vision_rejected_visible_follow = (
            bool(_defer_visual_followers_surface_ok)
            and bool(gate_passed)
            and len(candidates) == 0
            and _vision_rejected_pick
            and _fbc_defer >= 1
            and bool(getattr(config, "ENABLE_VISUAL_FOLLOWERS_CANDIDATE_PICKER", False))
        )
        _prog_max_passes_cfg = int(followers_progressive_max_passes())
        _prog_passes_used_snap = int(
            visual_loop_state.get("list_progressive_exploration_passes_used") or 0
        )
        _defer_pick_soft_progressive = (
            bool(visual_loop_state.get("list_progressive_exploration_active"))
            and _prog_passes_used_snap >= 1
            and _prog_passes_used_snap < _prog_max_passes_cfg
            and len(candidates) == 0
            and _picker_err_pick == ""
            and _empty_reason_pick in ("no_blue_follow_spans", "no_rows_after_filter")
            and gate_passed
            and bool(_vf_rendered_strong_committed_ok)
            and bool(_defer_visual_followers_surface_ok)
            and bool(getattr(config, "ENABLE_VISUAL_FOLLOWERS_CANDIDATE_PICKER", False))
        )
        _defer_xml_stale_for_visual_scroll = (
            bool(_defer_visual_followers_surface_ok)
            and bool(gate_passed)
            and len(candidates) == 0
            and (
                str(stop_r_after_candidates or "")
                not in FOLLOWERS_ENGINE_XML_STALE_EXIT_REASONS
                or _defer_xml_stale_pick_legit_empty_xml_open
                or _defer_xml_stale_pick_legit_unsafe_low_follow_only
                or _defer_xml_stale_pick_vision_rejected_visible_follow
            )
        )
        _defer_xml_stale_pick_committed_strong_no_tap_safe = (
            bool(_defer_xml_stale_for_visual_scroll)
            and bool(_vf_rendered_strong_committed_ok)
            and str(gate_block_reason or "") == "committed_rendered_strong_visual_ok"
            and bool(getattr(config, "ENABLE_VISUAL_FOLLOWERS_CANDIDATE_PICKER", False))
            and not _defer_xml_stale_pick_legit_empty_xml_open
            and not _defer_xml_stale_pick_legit_unsafe_low_follow_only
            and not _defer_xml_stale_pick_vision_rejected_visible_follow
        )
        if _defer_xml_stale_for_visual_scroll and (
            _defer_xml_stale_pick_legit_empty_xml_open
            or _defer_xml_stale_pick_legit_unsafe_low_follow_only
            or _defer_xml_stale_pick_vision_rejected_visible_follow
            or _defer_xml_stale_pick_committed_strong_no_tap_safe
        ):
            if _defer_xml_stale_pick_legit_empty_xml_open:
                try:
                    log(
                        "info",
                        "followers_visual_zero_follow_spans_deferred_to_scroll",
                        loop_iteration=followers_engine_loop_iteration,
                        source_profile_username=source_profile_username,
                        stop_r_after_candidates=str(stop_r_after_candidates or ""),
                        candidate_count=int(
                            (_vp_inj_for_defer or {}).get("candidate_count") or 0
                        ),
                        picker_error=_picker_err_pick,
                        empty_reason=_empty_reason_pick,
                        gate_passed=bool(gate_passed),
                        visual_followers_surface=bool(_visual_followers_surface),
                        vf_rendered_strong_committed_ok=bool(_vf_rendered_strong_committed_ok),
                        defer_visual_followers_surface_ok=bool(
                            _defer_visual_followers_surface_ok
                        ),
                        open_detection_method=str(open_detection_method or ""),
                    )
                except Exception:
                    pass
                try:
                    from followers_inter_candidate_perf import (
                        inter_candidate_segment_b_note_scroll_deferred_from_pick,
                    )

                    inter_candidate_segment_b_note_scroll_deferred_from_pick(
                        empty_reason=_empty_reason_pick,
                        picker_error=_picker_err_pick,
                        defer_kind="zero_follow_spans",
                    )
                except Exception:
                    pass
                if followers_allow_visual_exploratory_scroll_once(
                    reason="zero_follow_spans_defer",
                    source_profile_username=source_profile_username,
                ):
                    exploratory_scroll_permit_armed_this_iter = True
                    exploratory_scroll_profile_this_iter = "zero_follow_spans_soft"
                    exploratory_scroll_reposition_meta = None
            elif _defer_xml_stale_pick_legit_unsafe_low_follow_only:
                try:
                    log(
                        "info",
                        "followers_visual_unsafe_low_follow_cta_deferred_to_scroll",
                        loop_iteration=followers_engine_loop_iteration,
                        source_profile_username=source_profile_username,
                        stop_r_after_candidates=str(stop_r_after_candidates or ""),
                        picker_error=_picker_err_pick,
                        cta_allowed_count=int(_row_mapping_diag.get("cta_allowed_count") or 0),
                        mapped_count=int(_row_mapping_diag.get("mapped_count") or 0),
                        row_mapping_skip_reasons=dict(
                            _row_mapping_diag.get("row_mapping_skip_reasons") or {}
                        ),
                        reason="follow_cta_allowed_but_tap_y_outside_safe_band",
                    )
                except Exception:
                    pass
                try:
                    from followers_inter_candidate_perf import (
                        inter_candidate_segment_b_note_scroll_deferred_from_pick,
                    )

                    inter_candidate_segment_b_note_scroll_deferred_from_pick(
                        empty_reason=_empty_reason_pick,
                        picker_error=_picker_err_pick,
                        defer_kind="unsafe_low_follow",
                    )
                except Exception:
                    pass
                if followers_allow_visual_exploratory_scroll_once(
                    reason="unsafe_low_follow_cta_defer",
                    source_profile_username=source_profile_username,
                ):
                    exploratory_scroll_permit_armed_this_iter = True
                    exploratory_scroll_profile_this_iter = "micro_reposition"
                    exploratory_scroll_reposition_meta = None
                    try:
                        _ty_o = _row_mapping_diag.get("reposition_low_cta_tap_y_o")
                        _oh_o = _row_mapping_diag.get("reposition_orig_h_o")
                        _ysb_o = _row_mapping_diag.get("reposition_y_safe_bottom_o")
                        if _ty_o is not None and _oh_o is not None:
                            exploratory_scroll_reposition_meta = {
                                "tap_y_ref_o": int(_ty_o),
                                "orig_h_o": int(_oh_o),
                                "y_safe_bottom_o": int(_ysb_o)
                                if _ysb_o is not None
                                else int(int(_oh_o) * 0.86),
                            }
                    except (TypeError, ValueError):
                        exploratory_scroll_reposition_meta = None
            elif _defer_xml_stale_pick_vision_rejected_visible_follow:
                try:
                    log(
                        "info",
                        "followers_visual_vision_rejected_deferred_to_scroll",
                        loop_iteration=followers_engine_loop_iteration,
                        source_profile_username=source_profile_username,
                        picker_error=_picker_err_pick,
                        empty_reason=_empty_reason_pick,
                        follow_button_count=int(_fbc_defer),
                        gate_passed=bool(gate_passed),
                        open_detection_method=str(open_detection_method or ""),
                    )
                except Exception:
                    pass
                try:
                    from followers_inter_candidate_perf import (
                        inter_candidate_segment_b_note_scroll_deferred_from_pick,
                    )

                    inter_candidate_segment_b_note_scroll_deferred_from_pick(
                        empty_reason=_empty_reason_pick,
                        picker_error=_picker_err_pick,
                        defer_kind="vision_rejected",
                    )
                except Exception:
                    pass
                if followers_allow_visual_exploratory_scroll_once(
                    reason="vision_rejected_visible_follow_defer",
                    source_profile_username=source_profile_username,
                ):
                    exploratory_scroll_permit_armed_this_iter = True
                    exploratory_scroll_profile_this_iter = "zero_follow_spans_soft"
                    exploratory_scroll_reposition_meta = None
                    try:
                        log(
                            "info",
                            "followers_visual_empty_mapping_scroll_permit_armed",
                            source_profile_username=source_profile_username,
                            loop_iteration=followers_engine_loop_iteration,
                            defer_reason="vision_validation_rejected",
                            follow_button_count=int(_fbc_defer),
                            scroll_profile=exploratory_scroll_profile_this_iter,
                        )
                    except Exception:
                        pass
            elif _defer_xml_stale_pick_committed_strong_no_tap_safe:
                try:
                    log(
                        "info",
                        "followers_visual_no_tap_safe_candidate_deferred_to_scroll",
                        loop_iteration=followers_engine_loop_iteration,
                        source_profile_username=source_profile_username,
                        gate_block_reason=str(gate_block_reason or ""),
                        empty_reason=_empty_reason_pick,
                        picker_error=_picker_err_pick,
                        row_mapping_empty_reason=str(
                            _row_mapping_diag.get("row_mapping_empty_reason") or ""
                        ),
                        follow_button_count=int(_fbc_defer),
                        vf_rendered_strong_committed_ok=bool(
                            _vf_rendered_strong_committed_ok
                        ),
                    )
                except Exception:
                    pass
                try:
                    from followers_inter_candidate_perf import (
                        inter_candidate_segment_b_note_scroll_deferred_from_pick,
                    )

                    inter_candidate_segment_b_note_scroll_deferred_from_pick(
                        empty_reason=_empty_reason_pick,
                        picker_error=_picker_err_pick,
                        defer_kind="no_tap_safe_committed",
                    )
                except Exception:
                    pass
                if followers_allow_visual_exploratory_scroll_once(
                    reason="no_tap_safe_candidate_committed_rendered_strong",
                    source_profile_username=source_profile_username,
                ):
                    exploratory_scroll_permit_armed_this_iter = True
                    exploratory_scroll_profile_this_iter = "zero_follow_spans_soft"
                    exploratory_scroll_reposition_meta = None
                    try:
                        log(
                            "info",
                            "followers_visual_empty_mapping_scroll_permit_armed",
                            source_profile_username=source_profile_username,
                            loop_iteration=followers_engine_loop_iteration,
                            defer_reason="no_tap_safe_candidate_committed_rendered_strong",
                            follow_button_count=int(_fbc_defer),
                            scroll_profile=exploratory_scroll_profile_this_iter,
                        )
                    except Exception:
                        pass
        elif _defer_xml_stale_for_visual_scroll and _defer_pick_soft_progressive:
            try:
                log(
                    "info",
                    "followers_visual_progressive_soft_exploration_pass_armed",
                    loop_iteration=followers_engine_loop_iteration,
                    source_profile_username=source_profile_username,
                    exploration_passes_used=int(_prog_passes_used_snap),
                    exploration_max_passes=int(_prog_max_passes_cfg),
                    empty_reason=_empty_reason_pick,
                    gate_passed=bool(gate_passed),
                    vf_rendered_strong_committed_ok=bool(_vf_rendered_strong_committed_ok),
                    defer_visual_followers_surface_ok=bool(_defer_visual_followers_surface_ok),
                )
            except Exception:
                pass
            if followers_allow_visual_exploratory_scroll_once(
                reason="zero_follow_spans_defer",
                source_profile_username=source_profile_username,
            ):
                exploratory_scroll_permit_armed_this_iter = True
                exploratory_scroll_profile_this_iter = "zero_follow_spans_soft"
                exploratory_scroll_reposition_meta = None
        if (
            bool(visual_loop_state.get("list_progressive_exploration_active"))
            and int(visual_loop_state.get("list_progressive_exploration_passes_used") or 0)
            >= _prog_max_passes_cfg
            and len(candidates) == 0
            and _picker_err_pick == ""
            and _empty_reason_pick in ("no_blue_follow_spans", "no_rows_after_filter")
            and gate_passed
            and bool(_vf_rendered_strong_committed_ok)
            and bool(_defer_visual_followers_surface_ok)
            and bool(getattr(config, "ENABLE_VISUAL_FOLLOWERS_CANDIDATE_PICKER", False))
            and not bool(visual_loop_state.get("list_progressive_exploration_exhausted"))
        ):
            visual_loop_state["list_progressive_exploration_exhausted"] = True
            try:
                log(
                    "info",
                    "followers_list_soft_exploration_exhausted",
                    source_profile_username=source_profile_username,
                    exploration_passes_used=int(
                        visual_loop_state.get("list_progressive_exploration_passes_used") or 0
                    ),
                    max_passes=int(_prog_max_passes_cfg),
                    last_empty_reason=str(
                        visual_loop_state.get("list_progressive_exploration_last_empty_reason")
                        or _empty_reason_pick
                        or ""
                    )[:200],
                    scroll_used=int(scroll_used),
                    session_outcome="no_followable_candidates_after_bounded_exploration",
                    stop_reason=str(get_followers_engine_stop_reason() or ""),
                )
            except Exception:
                pass
        if _defer_xml_stale_for_visual_scroll:
            try:
                log(
                    "info",
                    "followers_visual_empty_mapping_deferred_to_scroll",
                    loop_iteration=followers_engine_loop_iteration,
                    len_candidates=0,
                    visual_followers_surface=True,
                    open_detection_method=open_detection_method,
                    reason="no_tap_safe_visual_candidate",
                    gate_block_reason=str(gate_block_reason or ""),
                    source_profile_username=source_profile_username,
                )
            except Exception:
                pass
        if _visual_followers_surface and (
            (
                stop_r_after_candidates in FOLLOWERS_ENGINE_XML_STALE_EXIT_REASONS
                and not _defer_xml_stale_for_visual_scroll
            )
            or (len(candidates) == 0 and not _defer_xml_stale_for_visual_scroll)
        ):
            stale_reason = stop_r_after_candidates
            if stale_reason not in FOLLOWERS_ENGINE_XML_STALE_EXIT_REASONS:
                stale_reason = "visual_fallback_no_xml_candidates"
            _stale_pic = _followers_xml_stale_engine_stop(
                stop_reason=stale_reason,
                det=det,
                loop_iteration=followers_engine_loop_iteration,
                xml_candidates=candidates,
            )
            if xml_stale_bypass_meta.get("bypassed"):
                skip_followers_xml_detect_next_iter = True
                log(
                    "info",
                    "followers_candidate_collection_reentered_after_visual_bypass",
                    phase="xml_stale_after_candidates",
                    bypass_reason=xml_stale_bypass_meta.get("bypass_reason"),
                    loop_iteration=followers_engine_loop_iteration,
                    source_profile_username=source_profile_username,
                    open_detection_method=open_detection_method,
                )
            if _stale_pic == VISUAL_FOLLOW_HISTORY_CONTINUE:
                log(
                    "info",
                    "visual_followers_loop_continue_after_reentry",
                    source_profile_username=source_profile_username,
                    phase="xml_stale_after_candidates",
                    iteration=processed,
                )
                continue
            if _stale_pic is not None:
                return _stale_pic

        def _first_eligible_follower_pick(cand_list: list) -> dict | None:
            visual_loop_state["_ac_pending_had_skip_hit"] = False
            _expl_v1.begin_visible_window(cand_list)
            for c in cand_list:
                ckey = _norm_ig_handle(str(c.get("username") or ""))
                if ckey == src_key:
                    log(
                        "info",
                        "followers_candidate_skipped_source_profile",
                        username=c.get("username"),
                        source_profile_username=source_profile_username,
                    )
                    _expl_v1.note_visible_skip("source_profile")
                    continue
                if c.get("already_seen_runtime"):
                    log(
                        "info",
                        "followers_candidate_skipped_runtime_seen",
                        username=c.get("username"),
                        source_profile_username=source_profile_username,
                    )
                    _eng_log(
                        "followers_candidate_skipped_runtime_seen",
                        "info",
                        "skipped_runtime_seen",
                        {"follower_username": c.get("username")},
                    )
                    _expl_v1.note_visible_skip("runtime_seen")
                    continue
                _runtime_hit = ""
                _runtime_kh = ""
                for _fld in ("username", "resolved_username_hint"):
                    _kh = _norm_ig_handle(str(c.get(_fld) or ""))
                    if not _kh:
                        continue
                    if _kh in _RUNTIME_FOLLOWED_USERNAMES:
                        _runtime_hit = "followed"
                        _runtime_kh = _kh
                        break
                    if _kh in _RUNTIME_SEEN_FOLLOWER_USERNAMES:
                        _runtime_hit = "seen"
                        _runtime_kh = _kh
                        break
                if _runtime_hit:
                    try:
                        log(
                            "info",
                            "followers_candidate_skipped_recently_followed_runtime",
                            source_profile_username=source_profile_username,
                            visual_candidate_id=str(c.get("visual_candidate_id") or ""),
                            candidate_username=str(
                                c.get("username") or c.get("resolved_username_hint") or ""
                            ),
                            normalized_candidate_key=_runtime_kh,
                            runtime_set_hit=_runtime_hit,
                            skip_reason="recently_followed_in_current_session",
                        )
                    except Exception:
                        pass
                    _expl_v1.note_visible_skip("recently_followed_runtime")
                    continue
                _cv_ac_blk = str(c.get("visual_candidate_id") or "").strip()
                if _cv_ac_blk and _cv_ac_blk in _ac_blocked_visual_ids():
                    try:
                        log(
                            "info",
                            "followers_candidate_skipped_blocked_already_connected_visual_candidate",
                            source_profile_username=source_profile_username,
                            visual_candidate_id=_cv_ac_blk,
                            follower_username=str(
                                c.get("username") or c.get("resolved_username_hint") or ""
                            )[:120],
                            memory_scope="until_list_scroll_removes_row_from_mapping",
                        )
                    except Exception:
                        pass
                    _expl_v1.note_visible_skip("blocked_visual")
                    continue
                _pend_ac = visual_loop_state.get(
                    "pending_recent_already_connected_visual_candidate_skip"
                )
                if isinstance(_pend_ac, dict) and str(
                    _pend_ac.get("source_profile_username") or ""
                ) == str(source_profile_username or ""):
                    _arm_ac = str(_pend_ac.get("visual_candidate_id") or "").strip()
                    _c_id_ac = str(c.get("visual_candidate_id") or "").strip()
                    if _arm_ac and _c_id_ac == _arm_ac:
                        visual_loop_state["_ac_pending_had_skip_hit"] = True
                        try:
                            log(
                                "info",
                                "followers_candidate_skipped_recent_already_connected_visual_candidate",
                                source_profile_username=source_profile_username,
                                visual_candidate_id=_c_id_ac,
                                follower_username=str(
                                    _pend_ac.get("follower_username") or c.get("username") or ""
                                )[:120],
                                skip_reason=str(_pend_ac.get("skip_reason") or "")[:200],
                                memory_scope="until_other_visual_pick_or_list_scroll",
                            )
                        except Exception:
                            pass
                        _expl_v1.note_visible_skip("recent_already_connected")
                        continue
                _pend_vc = visual_loop_state.get(
                    "pending_recent_verified_follow_visual_candidate_skip"
                )
                if isinstance(_pend_vc, dict) and str(
                    _pend_vc.get("source_profile_username") or ""
                ) == str(source_profile_username or ""):
                    _arm_id = str(_pend_vc.get("visual_candidate_id") or "").strip()
                    _c_id = str(c.get("visual_candidate_id") or "").strip()
                    if _arm_id and _c_id == _arm_id:
                        try:
                            log(
                                "info",
                                "followers_candidate_skipped_recent_verified_follow_visual_candidate",
                                source_profile_username=source_profile_username,
                                visual_candidate_id=_c_id,
                                skip_reason="same_visual_candidate_just_followed_without_username",
                                memory_scope="one_shot_post_verified_follow",
                            )
                        except Exception:
                            pass
                        _followers_clear_pending_visual_candidate_skip()
                        _expl_v1.note_visible_skip("verified_follow_one_shot")
                        continue
                _expl_v1.note_actionable_pick()
                return c
            vids_blk = [
                str(c.get("visual_candidate_id") or "").strip()
                for c in cand_list
                if str(c.get("visual_candidate_id") or "").strip()
            ]
            if vids_blk:
                _bid_all = _ac_blocked_visual_ids()
                if all(v in _bid_all for v in vids_blk):
                    visual_loop_state["_ac_all_blocked_need_scroll"] = True
            return None

        def _followers_note_ac_pending_scroll_forced_if_needed(
            pick_ctx: dict | None, cand_len: int
        ) -> bool:
            if pick_ctx is not None or cand_len == 0:
                visual_loop_state.pop("_ac_pending_had_skip_hit", None)
                visual_loop_state.pop("_ac_all_blocked_need_scroll", None)
                return False
            _all_blk = bool(visual_loop_state.pop("_ac_all_blocked_need_scroll", False))
            _pending_hit = bool(visual_loop_state.pop("_ac_pending_had_skip_hit", False))
            if not _pending_hit and not _all_blk:
                return False
            _psk = visual_loop_state.get("pending_recent_already_connected_visual_candidate_skip")
            _psk = _psk if isinstance(_psk, dict) else {}
            _reason = (
                "no_eligible_pick_after_pending_already_connected_row_skip"
                if _pending_hit
                else "all_visible_candidates_blocked_already_connected_until_scroll"
            )
            try:
                log(
                    "info",
                    "followers_recent_already_connected_visual_candidate_scroll_forced",
                    source_profile_username=source_profile_username,
                    visual_candidate_id=str(_psk.get("visual_candidate_id") or "")[:120],
                    follower_username=str(_psk.get("follower_username") or "")[:120],
                    candidates_len=cand_len,
                    reason=_reason,
                    trigger_pending_same_row=bool(_pending_hit),
                    trigger_all_visible_blocked=bool(_all_blk),
                )
            except Exception:
                pass
            return True

        _ac_scroll_forced = False
        _expl_v1.apply_post_scroll_candidate_window(
            candidates if isinstance(candidates, list) else [],
            scroll_used=int(scroll_used),
        )
        pick = _first_eligible_follower_pick(candidates)
        _followers_clear_pending_if_pick_other_visual_id(pick)
        _ac_scroll_forced = (
            _ac_scroll_forced
            or _followers_note_ac_pending_scroll_forced_if_needed(pick, len(candidates))
        )
        det_sparse_loop: dict | None = None

        if pick is None and len(candidates) == 0:
            _skip_sparse_detect_no_blue_defer = (
                bool(exploratory_scroll_permit_armed_this_iter)
                and str(_empty_reason_pick or "") == "no_blue_follow_spans"
                and str(_picker_err_pick or "") == ""
                and not bool(_vision_rejected_pick)
                and int(_row_mapping_diag.get("mapped_count") or 0) == 0
                and int(_row_mapping_diag.get("cta_allowed_count") or 0) == 0
                and int(_fbc_defer) == 0
                and bool(_vf_rendered_strong_committed_ok)
                and bool(gate_passed)
                and bool(_defer_visual_followers_surface_ok)
            )
            if _skip_sparse_detect_no_blue_defer:
                det_sparse_loop = dict(det) if isinstance(det, dict) else {}
                det_sparse_loop["is_followers_list"] = True
                det_sparse_loop["follow_buttons_visual_count"] = 0
                _sparse_skip_sigs = list(det_sparse_loop.get("signals") or [])
                if "sparse_detect_skipped_before_scroll" not in _sparse_skip_sigs:
                    _sparse_skip_sigs.append("sparse_detect_skipped_before_scroll")
                det_sparse_loop["signals"] = _sparse_skip_sigs
                visible_follow_buttons_stall_scrolls = 0
                try:
                    log(
                        "info",
                        "followers_visual_empty_frame_sparse_detect_skipped_before_scroll",
                        source_profile_username=source_profile_username,
                        picker_empty_reason=str(_empty_reason_pick or "")[:160],
                        visual_follow_button_count=int(_fbc_defer),
                        mapped_count=int(_row_mapping_diag.get("mapped_count") or 0),
                        tap_safe_candidate_count=int(_row_mapping_diag.get("mapped_count") or 0),
                        cta_allowed_count=int(_row_mapping_diag.get("cta_allowed_count") or 0),
                        reason="no_blue_follow_spans_defer_already_armed",
                        exploratory_scroll_profile=str(
                            exploratory_scroll_profile_this_iter or ""
                        )[:80],
                        vf_rendered_strong_committed_ok=bool(_vf_rendered_strong_committed_ok),
                    )
                except Exception:
                    pass
            else:
                det_sparse_loop = detect_followers_list_screen(
                    d, source_profile_username=source_profile_username
                )
            if not _skip_sparse_detect_no_blue_defer:
                fbc_pre = int(det_sparse_loop.get("follow_buttons_visual_count") or 0)
                if fbc_pre == 0:
                    visible_follow_buttons_stall_scrolls = 0
                elif bool(det_sparse_loop.get("is_followers_list")) and fbc_pre > 0:
                    log(
                        "info",
                        "visual_followers_scroll_blocked_visible_follow_buttons",
                        source_profile_username=source_profile_username,
                        follow_button_count=fbc_pre,
                        note="hierarchy_refresh_then_re_resolve_before_any_scroll",
                    )
                    log(
                        "info",
                        "visual_followers_candidate_resolution_retry_before_scroll",
                        source_profile_username=source_profile_username,
                        follow_button_count=fbc_pre,
                    )
                    followers_force_hierarchy_refresh(
                        d, source_profile_username=source_profile_username
                    )
                    time.sleep(0.45)
                    candidates = _collect_follower_candidates()
                    pick = _first_eligible_follower_pick(candidates)
                    _followers_clear_pending_if_pick_other_visual_id(pick)
                    _ac_scroll_forced = (
                        _ac_scroll_forced
                        or _followers_note_ac_pending_scroll_forced_if_needed(
                            pick, len(candidates)
                        )
                    )
                    if pick is not None:
                        visible_follow_buttons_stall_scrolls = 0

        if _ac_scroll_forced and pick is None:
            try:
                log(
                    "info",
                    "followers_recent_already_connected_visual_candidate_forced_scroll_execution_started",
                    source_profile_username=source_profile_username,
                    scroll_used_before=int(scroll_used),
                    max_scroll=int(max_scroll),
                    candidates_len=len(candidates) if isinstance(candidates, list) else -1,
                )
            except Exception:
                pass
            _forced_scroll_stop, _forced_scroll_stop_reason = _expl_v1.should_stop_scrolling(
                scroll_used=int(scroll_used),
                max_scroll_soft=int(max_scroll),
                follows_completed=int(follows_completed_count),
                max_iter=int(max_iter),
                list_progressive_exhausted=bool(
                    visual_loop_state.get("list_progressive_exploration_exhausted")
                ),
                session_elapsed_s=float(time.perf_counter() - t0),
                exploration_passes_used=int(_prog_passes_used_snap),
                exploration_max_passes=int(_prog_max_passes_cfg),
            )
            if _forced_scroll_stop:
                try:
                    log(
                        "warning",
                        "followers_recent_already_connected_visual_candidate_forced_scroll_blocked",
                        source_profile_username=source_profile_username,
                        scroll_used=int(scroll_used),
                        max_scroll=int(max_scroll),
                        stop_reason=str(_forced_scroll_stop_reason or "")[:160],
                    )
                except Exception:
                    pass
                _expl_v1.log_stop_reason(
                    str(_forced_scroll_stop_reason or "forced_scroll_blocked"),
                    scroll_used=int(scroll_used),
                    phase="forced_already_connected",
                )
            else:
                _forced_scroll_profile = _expl_v1.choose_scroll_profile(
                    base_profile="default",
                    exploratory_armed=False,
                    forced_already_connected=True,
                )
                _scroll_diag: dict[str, Any] = {}
                _sc_ac = scroll_followers_list_forward(
                    d,
                    apply_exploratory_xml_override=False,
                    scroll_profile=_forced_scroll_profile,
                    source_profile_username=source_profile_username,
                    scroll_reposition_meta=None,
                    bypass_post_tap_capture_gate=True,
                    bypass_scroll_xml_guards=True,
                    scroll_diag_out=_scroll_diag,
                )
                if _sc_ac:
                    scroll_used += 1
                    _expl_v1.mark_scroll_completed_pending_check()
                    try:
                        log(
                            "info",
                            "followers_recent_already_connected_visual_candidate_forced_scroll_execution_success",
                            source_profile_username=source_profile_username,
                            scroll_used_after=int(scroll_used),
                            scroll_profile=str(_forced_scroll_profile or ""),
                        )
                    except Exception:
                        pass
                    _eng_log(
                        "followers_list_scroll",
                        "info",
                        "scroll_already_connected_forced",
                        {
                            "scroll_index": scroll_used,
                            "direction": "forward",
                            "scroll_profile": _forced_scroll_profile,
                        },
                    )
                    _followers_try_refresh_injection_screenshot_after_scroll(
                        d,
                        open_list_meta,
                        source_profile_username=source_profile_username,
                        scroll_used=scroll_used,
                        reason="followers_scroll_success_already_connected_forced",
                    )
                    _followers_clear_pending_visual_candidate_skip()
                    _followers_clear_pending_already_connected_visual_skip(
                        clear_reason="followers_scroll_success_already_connected_forced",
                    )
                    _followers_clear_ac_visual_blocks_after_scroll(
                        clear_reason="followers_scroll_success_already_connected_forced",
                    )
                    continue
                try:
                    _cm_ac = followers_session_committed_meta()
                    _vf_ac = (
                        visual_loop_state.get("session_vf_detail")
                        if isinstance(visual_loop_state.get("session_vf_detail"), dict)
                        else {}
                    )
                    log(
                        "warning",
                        "followers_recent_already_connected_visual_candidate_forced_scroll_execution_failed",
                        source_profile_username=source_profile_username,
                        scroll_used=int(scroll_used),
                        failure_reason=str(_scroll_diag.get("failure_reason") or "scroll_returned_false"),
                        scroll_helper_succeeded=bool(_scroll_diag.get("scroll_helper_succeeded")),
                        whether_physical_swipe_attempted=bool(
                            _scroll_diag.get("whether_physical_swipe_attempted")
                        ),
                        bypass_post_tap_capture_gate=bool(
                            _scroll_diag.get("bypass_post_tap_capture_gate")
                        ),
                        bypass_scroll_xml_guards=bool(_scroll_diag.get("bypass_scroll_xml_guards")),
                        committed_source=str(
                            (_cm_ac or {}).get("followers_list_committed_source") or ""
                        )[:120],
                        det_is_followers_list=bool(
                            isinstance(det, dict) and bool(det.get("is_followers_list"))
                        ),
                        session_vf_available=bool(_vf_ac),
                        visual_user_rows_detected=int(_vf_ac.get("visual_user_rows_detected") or 0)
                        if _vf_ac
                        else 0,
                        visual_follow_button_count=int(_vf_ac.get("visual_follow_button_count") or 0)
                        if _vf_ac
                        else 0,
                        visual_search_area_detected=bool(_vf_ac.get("visual_search_area_detected"))
                        if _vf_ac
                        else False,
                        visual_followers_title_hint=bool(_vf_ac.get("visual_followers_title_hint"))
                        if _vf_ac
                        else False,
                    )
                except Exception:
                    pass

        if pick is None:
            if len(candidates) == 0:
                det_sparse = (
                    det_sparse_loop
                    if det_sparse_loop is not None
                    else detect_followers_list_screen(
                        d, source_profile_username=source_profile_username
                    )
                )
                fbc_sparse = int(det_sparse.get("follow_buttons_visual_count") or 0)
                if fbc_sparse == 0:
                    visible_follow_buttons_stall_scrolls = 0
                if bool(det_sparse.get("is_followers_list")) and fbc_sparse > 0:
                    if (
                        visible_follow_buttons_stall_scrolls
                        >= max_visible_buttons_micro_scrolls
                    ):
                        log(
                            "error",
                            "visual_followers_picker_stalled_visible_buttons_no_candidate",
                            source_profile_username=source_profile_username,
                            follow_button_count=fbc_sparse,
                            stall_scrolls=visible_follow_buttons_stall_scrolls,
                            sparse_follow_scrolls=sparse_follow_scrolls,
                            max_micro_scrolls=max_visible_buttons_micro_scrolls,
                        )
                        _eng_log(
                            "followers_engine_stalled_visible_follows",
                            "failed",
                            "no_resolved_candidate_after_scrolls",
                            {
                                "follow_button_count": fbc_sparse,
                                "stall_scrolls": visible_follow_buttons_stall_scrolls,
                            },
                        )
                        _emit_performance_summary(
                            t0=t0,
                            warm_session_used=warm_session_used,
                            force_stop_used=force_stop_used,
                            exit_code=67,
                            target_username=source_profile_username,
                        )
                        return 67
                    log(
                        "info",
                        "visual_followers_sparse_list_scroll",
                        scroll_index=sparse_follow_scrolls,
                        reason="no_candidate_after_retry_micro_scroll_only",
                        follow_button_count=fbc_sparse,
                        source_profile_username=source_profile_username,
                        scroll_kind="micro_swipe",
                    )
                    _sparse_micro_profile = _expl_v1.choose_scroll_profile(
                        base_profile="default",
                        exploratory_armed=False,
                    )
                    if scroll_followers_list_forward(
                        d,
                        scroll_profile=_sparse_micro_profile,
                        source_profile_username=source_profile_username,
                    ):
                        sparse_follow_scrolls += 1
                        visible_follow_buttons_stall_scrolls += 1
                        scroll_used += 1
                        _expl_v1.mark_scroll_completed_pending_check()
                        _eng_log(
                            "followers_list_scroll",
                            "info",
                            "scroll_sparse_xml_micro",
                            {
                                "scroll_index": scroll_used,
                                "sparse_index": sparse_follow_scrolls,
                                "direction": "forward",
                                "scroll_kind": "micro_swipe",
                            },
                        )
                        _followers_try_refresh_injection_screenshot_after_scroll(
                            d,
                            open_list_meta,
                            source_profile_username=source_profile_username,
                            scroll_used=scroll_used,
                            reason="followers_scroll_success_sparse_micro",
                        )
                        _followers_clear_pending_visual_candidate_skip()
                        _followers_clear_pending_already_connected_visual_skip(
                            clear_reason="followers_scroll_success_sparse_micro",
                        )
                        _followers_clear_ac_visual_blocks_after_scroll(
                            clear_reason="followers_scroll_success_sparse_micro",
                        )
                    continue
                if bool(det_sparse.get("is_followers_list")) and fbc_sparse == 0:
                    _sparse_legacy_cap = 3
                    _sparse_cap = (
                        int(
                            getattr(
                                config,
                                "FOLLOWERS_EXPLORATION_V1_SPARSE_ZERO_BUTTON_SCROLL_MAX",
                                8,
                            )
                            or 8
                        )
                        if exploration_v1_enabled()
                        else _sparse_legacy_cap
                    )
                    _sparse_should_exit = False
                    _sparse_exit_reason = ""
                    if exploration_v1_enabled():
                        _sparse_should_exit, _sparse_exit_reason = _expl_v1.should_stop_scrolling(
                            scroll_used=int(scroll_used),
                            max_scroll_soft=int(max_scroll),
                            follows_completed=int(follows_completed_count),
                            max_iter=int(max_iter),
                            list_progressive_exhausted=bool(
                                visual_loop_state.get("list_progressive_exploration_exhausted")
                            ),
                            session_elapsed_s=float(time.perf_counter() - t0),
                            exploration_passes_used=int(_prog_passes_used_snap),
                            exploration_max_passes=int(_prog_max_passes_cfg),
                        )
                        if (
                            not _sparse_should_exit
                            and sparse_follow_scrolls >= _sparse_cap
                            and int(_expl_v1.state.get("no_new_visual_progress_count") or 0)
                            >= 2
                        ):
                            _sparse_should_exit = True
                            _sparse_exit_reason = "sparse_zero_buttons_stagnation"
                    elif sparse_follow_scrolls >= _sparse_legacy_cap:
                        _sparse_should_exit = True
                        _sparse_exit_reason = "sparse_scroll_cap_legacy"
                    if _sparse_should_exit:
                        _expl_v1.log_end_reached_confirmed(
                            sparse_scrolls=int(sparse_follow_scrolls),
                            follow_button_count=int(fbc_sparse),
                            stop_reason=str(_sparse_exit_reason or "")[:160],
                        )
                        log(
                            "info",
                            "visual_followers_no_more_candidates_after_scrolls",
                            source_profile_username=source_profile_username,
                            sparse_scrolls=sparse_follow_scrolls,
                            follow_button_count=fbc_sparse,
                            exploration_stop_reason=str(_sparse_exit_reason or "")[:160],
                        )
                        _eng_log(
                            "followers_engine_sparse_exhausted",
                            "failed",
                            "no_candidates_after_sparse_scrolls",
                            {
                                "sparse_scrolls": sparse_follow_scrolls,
                                "follow_button_count": fbc_sparse,
                                "exploration_stop_reason": _sparse_exit_reason,
                            },
                        )
                        _expl_v1.log_stop_reason(
                            str(_sparse_exit_reason or "sparse_exhausted"),
                            scroll_used=int(scroll_used),
                            sparse_scrolls=int(sparse_follow_scrolls),
                        )
                        _emit_performance_summary(
                            t0=t0,
                            warm_session_used=warm_session_used,
                            force_stop_used=force_stop_used,
                            exit_code=66,
                            target_username=source_profile_username,
                        )
                        return 66
            _main_scroll_stop, _main_scroll_stop_reason = _expl_v1.should_stop_scrolling(
                scroll_used=int(scroll_used),
                max_scroll_soft=int(max_scroll),
                follows_completed=int(follows_completed_count),
                max_iter=int(max_iter),
                list_progressive_exhausted=bool(
                    visual_loop_state.get("list_progressive_exploration_exhausted")
                ),
                session_elapsed_s=float(time.perf_counter() - t0),
                exploration_passes_used=int(_prog_passes_used_snap),
                exploration_max_passes=int(_prog_max_passes_cfg),
            )
            if _main_scroll_stop:
                _expl_v1.log_stop_reason(
                    str(_main_scroll_stop_reason or "exploration_stop"),
                    scroll_used=int(scroll_used),
                    phase="main_scroll_loop",
                )
                log(
                    "info",
                    "followers_engine_scroll_stop",
                    source_profile_username=source_profile_username,
                    scroll_used=scroll_used,
                    stop_reason=str(_main_scroll_stop_reason or "")[:160],
                    exploration_v1=bool(exploration_v1_enabled()),
                )
                break
            if exploratory_scroll_permit_armed_this_iter:
                try:
                    from followers_inter_candidate_perf import (
                        inter_candidate_segment_b_scroll_phase_start,
                    )

                    inter_candidate_segment_b_scroll_phase_start()
                except Exception:
                    pass
                try:
                    log(
                        "info",
                        "followers_visual_empty_mapping_scroll_attempted",
                        source_profile_username=source_profile_username,
                        loop_iteration=followers_engine_loop_iteration,
                        scroll_profile=exploratory_scroll_profile_this_iter,
                        scroll_used_before=int(scroll_used),
                    )
                except Exception:
                    pass
            _main_scroll_profile = _expl_v1.choose_scroll_profile(
                base_profile=(
                    exploratory_scroll_profile_this_iter
                    if exploratory_scroll_permit_armed_this_iter
                    else "default"
                ),
                exploratory_armed=bool(exploratory_scroll_permit_armed_this_iter),
            )
            if not scroll_followers_list_forward(
                d,
                apply_exploratory_xml_override=exploratory_scroll_permit_armed_this_iter,
                scroll_profile=_main_scroll_profile,
                source_profile_username=source_profile_username,
                scroll_reposition_meta=(
                    exploratory_scroll_reposition_meta
                    if exploratory_scroll_permit_armed_this_iter
                    and exploratory_scroll_profile_this_iter == "micro_reposition"
                    else None
                ),
            ):
                _expl_v1.log_stop_reason(
                    "scroll_failed",
                    scroll_used=int(scroll_used),
                    phase="main_scroll_loop",
                )
                break
            else:
                _expl_profile_completed = ""
                if exploratory_scroll_permit_armed_this_iter:
                    _expl_profile_completed = str(
                        exploratory_scroll_profile_this_iter or ""
                    ).strip()
                scroll_used += 1
                _expl_v1.mark_scroll_completed_pending_check()
                if exploratory_scroll_permit_armed_this_iter:
                    try:
                        log(
                            "info",
                            "followers_visual_empty_mapping_scroll_used",
                            source_profile_username=source_profile_username,
                            loop_iteration=followers_engine_loop_iteration,
                            scroll_profile=exploratory_scroll_profile_this_iter,
                            scroll_index=int(scroll_used),
                            main_scroll_profile=str(_main_scroll_profile or ""),
                        )
                        try:
                            from followers_inter_candidate_perf import (
                                inter_candidate_on_exploratory_scroll_used,
                                inter_candidate_segment_b_note_exploratory_scroll_used,
                            )

                            inter_candidate_on_exploratory_scroll_used()
                            inter_candidate_segment_b_note_exploratory_scroll_used(
                                scroll_profile=exploratory_scroll_profile_this_iter,
                            )
                        except Exception:
                            pass
                    except Exception:
                        pass
                _eng_log(
                    "followers_list_scroll",
                    "info",
                    "scroll",
                    {"scroll_index": scroll_used, "direction": "forward"},
                )
                if exploratory_scroll_permit_armed_this_iter:
                    _prev_stop_exploratory = get_followers_engine_stop_reason()
                    followers_engine_clear_stop_reason()
                    try:
                        log(
                            "info",
                            "followers_visual_exploratory_scroll_success_stale_stop_cleared",
                            source_profile_username=source_profile_username,
                            previous_stop_reason=str(_prev_stop_exploratory or ""),
                            scroll_used=int(scroll_used),
                        )
                    except Exception:
                        pass
                    exploratory_scroll_permit_armed_this_iter = False
                    exploratory_scroll_profile_this_iter = "default"
                    exploratory_scroll_reposition_meta = None
                if _expl_profile_completed == "zero_follow_spans_soft":
                    visual_loop_state["list_progressive_exploration_passes_used"] = int(
                        visual_loop_state.get("list_progressive_exploration_passes_used") or 0
                    ) + 1
                    visual_loop_state["list_progressive_exploration_active"] = True
                    visual_loop_state["list_progressive_exploration_last_empty_reason"] = str(
                        _empty_reason_pick or ""
                    )[:160]
                    visual_loop_state["list_progressive_exploration_last_scroll_profile"] = (
                        "zero_follow_spans_soft"
                    )
                _followers_try_refresh_injection_screenshot_after_scroll(
                    d,
                    open_list_meta,
                    source_profile_username=source_profile_username,
                    scroll_used=scroll_used,
                    reason="followers_scroll_success",
                )
                _followers_clear_pending_visual_candidate_skip()
                _followers_clear_pending_already_connected_visual_skip(
                    clear_reason="followers_scroll_success_main_loop",
                )
                _followers_clear_ac_visual_blocks_after_scroll(
                    clear_reason="followers_scroll_success_main_loop",
                )
                continue

        try:
            from followers_inter_candidate_perf import inter_candidate_on_candidate_selected

            inter_candidate_on_candidate_selected(
                follower_username=str(pick.get("username") or ""),
                resolved_username_hint=str(pick.get("resolved_username_hint") or ""),
            )
        except Exception:
            pass
        log(
            "info",
            "followers_candidate_selected",
            follower_username=pick.get("username"),
            source_profile_username=source_profile_username,
        )
        _eng_log(
            "followers_candidate_selected",
            "info",
            "selected",
            {
                "follower_username": pick.get("username"),
                "row_center": pick.get("row_center"),
            },
        )
        if pick.get("visual_candidate_id"):
            log(
                "info",
                "followers_visual_candidate_selected",
                source_profile_username=source_profile_username,
                visual_candidate_id=pick.get("visual_candidate_id"),
                row_index=pick.get("row_index"),
                span_index=pick.get("span_index"),
                confidence=pick.get("confidence"),
                resolved_username_hint=pick.get("resolved_username_hint"),
                follower_username=pick.get("username"),
            )

        sparse_follow_scrolls = 0

        _vcid_for_pick = str(pick.get("visual_candidate_id") or "").strip()
        _prev_pick_username = str(pick.get("username") or "").strip()
        _hint_pick = str(pick.get("resolved_username_hint") or "").strip().lstrip("@")
        _from_pick = (
            _prev_pick_username
            if _is_plausible_public_ig_username(_prev_pick_username)
            else ""
        )
        _from_hint = _hint_pick if _is_plausible_public_ig_username(_hint_pick) else ""
        _resolved_prefollow = _from_pick or _from_hint

        pending_username = bool(pick.get("username_pending_profile_read")) or (
            bool(_vcid_for_pick) and not _resolved_prefollow
        )
        _followers_resolved_continue_to_follow = False
        _ct_follow_resolve_streak = 0

        if not pending_username:
            fkey_pre = _norm_ig_handle(
                str(_resolved_prefollow or pick.get("username") or "")
            )
            if fkey_pre:
                _RUNTIME_SEEN_FOLLOWER_USERNAMES.add(fkey_pre)
            if fkey_pre == src_key:
                ok_r, _ = return_to_followers_list(d, source_profile_username, pkg)
                if not ok_r:
                    return 42
                continue

        if (
            _vcid_for_pick
            and _resolved_prefollow
            and _is_plausible_public_ig_username(str(_resolved_prefollow))
            and str(account_id or "").strip()
        ):
            _db_row_b1 = _followers_visual_load_interacted_db_row(
                account_id=str(account_id),
                target_username=str(_resolved_prefollow),
                source_profile=source_profile_username,
                supabase_mode=supabase_mode,
            )
            _b1_ok, _b1_reason, _b1_detail = (
                social_memory.db_row_persistent_active_follow_connection(_db_row_b1)
            )
            if _b1_ok:
                log(
                    "info",
                    "followers_candidate_preopen_skipped_persistent_already_connected",
                    source_profile_username=source_profile_username,
                    follower_username=str(_resolved_prefollow),
                    visual_candidate_id=_vcid_for_pick,
                    skip_reason=_b1_reason,
                    memory_status=str((_b1_detail or {}).get("memory_status") or ""),
                    following_status=str((_b1_detail or {}).get("following_status") or ""),
                    interaction_lifecycle_state=str(
                        (_b1_detail or {}).get("interaction_lifecycle_state") or ""
                    ),
                )
                _followers_visual_emit_already_connected_skip_event(
                    account_id=str(account_id),
                    run_id=run_id,
                    supabase_mode=supabase_mode,
                    follower_username=str(_resolved_prefollow),
                    source_profile_username=source_profile_username,
                    skip_reason=f"persistent_preopen_already_connected:{_b1_reason}",
                    visual_candidate_id=_vcid_for_pick,
                    memory_detail=_b1_detail,
                )
                _fk_skip = _norm_ig_handle(str(_resolved_prefollow))
                if _fk_skip:
                    _RUNTIME_SEEN_FOLLOWER_USERNAMES.add(_fk_skip)
                    _RUNTIME_FOLLOWED_USERNAMES.add(_fk_skip)
                continue

        invalidate_followers_injection_evidence(
            open_list_meta,
            visual_loop_state,
            reason="opening_follower_profile",
            source_profile_username=source_profile_username,
        )
        set_committed_light_revalidate_ok(visual_loop_state, ok=False)

        if not open_follower_profile_from_list(d, pick, source_profile_username, pkg):
            _eng_log(
                "follower_profile_open_failed",
                "failed",
                "open_follower_failed",
                {
                    "follower_username": pick.get("username"),
                    "username_pending_profile_read": pending_username,
                },
            )
            ok_r, _ = return_to_followers_list(d, source_profile_username, pkg)
            if not ok_r:
                return 42
            continue

        followers_session_clear_list_committed_open(source_profile_username)

        follower_un = (
            _resolved_prefollow
            if _resolved_prefollow
            else str(pick.get("username") or "").strip().lstrip("@")
        )
        if not pending_username and _resolved_prefollow:
            pick["username"] = _resolved_prefollow

        if pending_username:
            try:
                log(
                    "info",
                    "visual_followers_profile_username_resolution_attempted",
                    source_profile_username=source_profile_username,
                    visual_candidate_id=str(pick.get("visual_candidate_id") or ""),
                    previous_pick_username=_prev_pick_username,
                    resolved_username_hint=_hint_pick,
                    pending_flag=bool(pick.get("username_pending_profile_read")),
                    resolution_reason="visual_candidate_needs_action_bar_read",
                )
            except Exception:
                pass
            follower_un = str(
                read_current_profile_username_for_follow_gate(d) or ""
            ).strip().lstrip("@")
            if follower_un:
                pick["username"] = follower_un
                pick["username_pending_profile_read"] = False
                pick["username_resolution_source"] = "profile_action_bar_post_open"
                try:
                    log(
                        "info",
                        "visual_followers_profile_username_resolved",
                        source_profile_username=source_profile_username,
                        visual_candidate_id=str(pick.get("visual_candidate_id") or ""),
                        previous_pick_username=_prev_pick_username,
                        resolved_username=follower_un,
                        resolution_source="profile_action_bar_post_open",
                    )
                except Exception:
                    pass
            else:
                try:
                    log(
                        "info",
                        "visual_followers_profile_username_unresolved",
                        source_profile_username=source_profile_username,
                        visual_candidate_id=str(pick.get("visual_candidate_id") or ""),
                        previous_pick_username=_prev_pick_username,
                        resolved_username_hint=_hint_pick,
                        resolution_reason="empty_after_action_bar_read",
                    )
                except Exception:
                    pass
            if not follower_un:
                log(
                    "warning",
                    "visual_followers_username_read_on_profile_empty",
                    source_profile_username=source_profile_username,
                )
                log(
                    "info",
                    "visual_followers_resolved_username_skip_reason",
                    reason="empty_username_after_profile_read",
                    source_profile_username=source_profile_username,
                )
                ok_r, _ = return_to_followers_list(d, source_profile_username, pkg)
                if not ok_r:
                    return 42
                continue
            fkey = _norm_ig_handle(follower_un)
            if not fkey:
                log(
                    "info",
                    "visual_followers_resolved_username_skip_reason",
                    reason="normalize_empty_after_profile_read",
                    source_profile_username=source_profile_username,
                )
                ok_r, _ = return_to_followers_list(d, source_profile_username, pkg)
                if not ok_r:
                    return 42
                continue
            if fkey == src_key:
                log(
                    "info",
                    "followers_candidate_skipped_source_profile",
                    username=follower_un,
                    source_profile_username=source_profile_username,
                    phase="after_profile_username_read",
                )
                log(
                    "info",
                    "visual_followers_resolved_username_skip_reason",
                    reason="source_profile_self",
                    follower_username=follower_un,
                    source_profile_username=source_profile_username,
                )
                ok_r, _ = return_to_followers_list(d, source_profile_username, pkg)
                if not ok_r:
                    return 42
                continue
            if (
                pending_username
                and not _resolved_prefollow
                and _vcid_for_pick
                and str(account_id or "").strip()
            ):
                _db_row_b2 = _followers_visual_load_interacted_db_row(
                    account_id=str(account_id),
                    target_username=follower_un,
                    source_profile=source_profile_username,
                    supabase_mode=supabase_mode,
                )
                _b2_ok, _b2_reason, _b2_detail = (
                    social_memory.db_row_persistent_active_follow_connection(_db_row_b2)
                )
                if _b2_ok:
                    log(
                        "info",
                        "followers_candidate_postopen_skipped_persistent_already_connected",
                        source_profile_username=source_profile_username,
                        follower_username=follower_un,
                        visual_candidate_id=_vcid_for_pick,
                        skip_reason=_b2_reason,
                        memory_status=str((_b2_detail or {}).get("memory_status") or ""),
                        following_status=str((_b2_detail or {}).get("following_status") or ""),
                        interaction_lifecycle_state=str(
                            (_b2_detail or {}).get("interaction_lifecycle_state") or ""
                        ),
                    )
                    try:
                        visual_loop_state["pending_recent_already_connected_visual_candidate_skip"] = {
                            "source_profile_username": str(source_profile_username or ""),
                            "visual_candidate_id": str(_vcid_for_pick or "").strip(),
                            "follower_username": str(follower_un or "").strip(),
                            "skip_reason": f"persistent_postopen_already_connected:{_b2_reason}",
                            "armed_loop_iteration": int(followers_engine_loop_iteration),
                            "armed_at_monotonic": float(time.monotonic()),
                        }
                        log(
                            "info",
                            "followers_recent_already_connected_visual_candidate_skip_armed",
                            source_profile_username=source_profile_username,
                            visual_candidate_id=str(_vcid_for_pick or ""),
                            follower_username=str(follower_un or ""),
                            skip_reason=f"persistent_postopen_already_connected:{_b2_reason}",
                            armed_loop_iteration=int(followers_engine_loop_iteration),
                        )
                    except Exception:
                        pass
                    try:
                        _ac_register_blocked_visual_after_postopen_skip(
                            visual_candidate_id=str(_vcid_for_pick or ""),
                            follower_username=str(follower_un or ""),
                            skip_reason=f"persistent_postopen_already_connected:{_b2_reason}",
                        )
                    except Exception:
                        pass
                    _RUNTIME_SEEN_FOLLOWER_USERNAMES.add(fkey)
                    _RUNTIME_FOLLOWED_USERNAMES.add(fkey)
                    _ok_b2, _how_b2 = _visual_candidate_already_connected_fast_return(
                        d,
                        source_profile_username=source_profile_username,
                        pkg=pkg,
                        follower_username=follower_un,
                        visual_candidate_id=_vcid_for_pick,
                        skip_reason=f"persistent_postopen_already_connected:{_b2_reason}",
                        memory_detail=_b2_detail,
                        follow_header_state=None,
                        account_id=str(account_id),
                        run_id=run_id,
                        supabase_mode=supabase_mode,
                        visual_loop_state=visual_loop_state,
                    )
                    if not _ok_b2:
                        return 42
                    continue
            _ct_start_candidate_attempt_timer(fkey)
            streak = _RUNTIME_FOLLOWERS_POST_RESOLVE_STREAK.get(fkey, 0) + 1
            _RUNTIME_FOLLOWERS_POST_RESOLVE_STREAK[fkey] = streak
            _ct_follow_resolve_streak = streak
            _max_reopen = int(
                getattr(config, "CT_FOLLOW_MAX_SAME_PROFILE_REOPEN_WITHOUT_TAP", 2) or 2
            )
            if (
                fkey not in _RUNTIME_CT_LIST_FOLLOW_TAP_ATTEMPTED_USERNAMES
                and streak > 1 + _max_reopen
            ):
                log(
                    "info",
                    "visual_followers_candidate_abandoned_after_reopen_limit",
                    follower_username=follower_un,
                    fkey=fkey,
                    source_profile_username=source_profile_username,
                    resolve_streak=streak,
                    max_same_profile_reopen=_max_reopen,
                )
                _RUNTIME_SEEN_FOLLOWER_USERNAMES.add(fkey)
                _ct_clear_candidate_attempt_timer()
                _RUNTIME_FOLLOWERS_POST_RESOLVE_STREAK.pop(fkey, None)
                _ct_return_followers_list_or_canonical_reset(
                    d,
                    source_profile_username=source_profile_username,
                    pkg=pkg,
                    account_id=account_id or None,
                    reason="abandoned_after_reopen_limit",
                )
                continue
            if fkey in _RUNTIME_SEEN_FOLLOWER_USERNAMES:
                if streak < 2:
                    log(
                        "info",
                        "followers_candidate_skipped_runtime_seen",
                        username=follower_un,
                        source_profile_username=source_profile_username,
                        phase="after_profile_username_read",
                    )
                    log(
                        "info",
                        "visual_followers_resolved_username_skip_reason",
                        reason="runtime_seen_before_follow",
                        follower_username=follower_un,
                        source_profile_username=source_profile_username,
                    )
                    _eng_log(
                        "followers_candidate_skipped_runtime_seen",
                        "info",
                        "skipped_runtime_seen",
                        {"follower_username": follower_un},
                    )
                    ok_r, _ = return_to_followers_list(d, source_profile_username, pkg)
                    if not ok_r:
                        return 42
                    continue
                log(
                    "warning",
                    "visual_followers_same_profile_reopened_without_follow",
                    follower_username=follower_un,
                    source_profile_username=source_profile_username,
                    resolve_streak=streak,
                )
                log(
                    "info",
                    "visual_followers_force_follow_after_reopen_loop",
                    follower_username=follower_un,
                    source_profile_username=source_profile_username,
                    resolve_streak=streak,
                )
            if account_id:
                hist_skip = visual_follow_history_skip_reason_if_any(
                    source_profile_username=source_profile_username,
                    target_username_hint=follower_un,
                    source_account_context=str(account_id),
                )
                if hist_skip:
                    log(
                        "info",
                        "visual_followers_fallback_candidate_skipped",
                        reason=str(hist_skip),
                        username=follower_un,
                        phase="after_profile_username_read",
                    )
                    log(
                        "info",
                        "visual_followers_resolved_username_skip_reason",
                        reason=str(hist_skip),
                        follower_username=follower_un,
                        source_profile_username=source_profile_username,
                    )
                    _RUNTIME_SEEN_FOLLOWER_USERNAMES.add(fkey)
                    _ct_clear_candidate_attempt_timer()
                    ok_r, _ = return_to_followers_list(d, source_profile_username, pkg)
                    if not ok_r:
                        return 42
                    continue
            _prof_ok_after_read = verify_profile(d, follower_un)
            if not _prof_ok_after_read and pick.get("visual_candidate_id"):
                _nav_fb = observe_instagram_state(
                    d,
                    expected_package=pkg,
                    last_known_state=NavigationEngineState.CANDIDATE_PROFILE.value,
                    context={
                        "phase": "candidate_profile_analysis",
                        "visual_candidate_id": pick.get("visual_candidate_id"),
                        "source_profile_username": source_profile_username,
                        "disable_followers_visual_fallback": True,
                        "expected_state": "CANDIDATE_PROFILE",
                        "det": det if isinstance(det, dict) else None,
                    },
                )
                navigation_loop_state["last_state"] = str(_nav_fb.get("state") or "")
                _st_fb = str(_nav_fb.get("state") or "")
                _cf_fb = float(_nav_fb.get("confidence") or 0.0)
                _prof_like = _st_fb in (
                    NavigationEngineState.PROFILE.value,
                    NavigationEngineState.PRIVATE_PROFILE.value,
                    NavigationEngineState.CANDIDATE_PROFILE.value,
                )
                if _prof_like and _cf_fb >= 0.45:
                    log(
                        "info",
                        "visual_candidate_verify_profile_username_relaxed",
                        source_profile_username=source_profile_username,
                        visual_candidate_id=pick.get("visual_candidate_id"),
                        follower_username_read=follower_un,
                        navigation_state=_st_fb,
                        navigation_confidence=_cf_fb,
                        navigation_reason=_nav_fb.get("reason"),
                    )
                    _prof_ok_after_read = verify_profile(d, "")
            if not _prof_ok_after_read:
                log(
                    "error",
                    "follower_profile_verify_after_username_read_failed",
                    follower_username=follower_un,
                    source_profile_username=source_profile_username,
                )
                if pick.get("visual_candidate_id"):
                    log(
                        "error",
                        "visual_candidate_profile_flow_failed",
                        failure_reason="visual_candidate_profile_analysis_failed",
                        follower_username=follower_un,
                        source_profile_username=source_profile_username,
                        visual_candidate_id=pick.get("visual_candidate_id"),
                    )
                log(
                    "info",
                    "visual_followers_resolved_username_skip_reason",
                    reason="verify_profile_failed_after_username_read",
                    follower_username=follower_un,
                    source_profile_username=source_profile_username,
                )
                ok_r, _ = return_to_followers_list(d, source_profile_username, pkg)
                if not ok_r:
                    if pick.get("visual_candidate_id"):
                        log(
                            "error",
                            "visual_candidate_profile_flow_failed",
                            failure_reason="visual_candidate_return_ct_failed",
                            phase="after_verify_profile_failed",
                            source_profile_username=source_profile_username,
                        )
                    return 42
                continue
            log(
                "info",
                "visual_followers_resolved_username_continue_to_follow",
                follower_username=follower_un,
                source_profile_username=source_profile_username,
                resolve_streak=streak,
            )
            _followers_resolved_continue_to_follow = True
            log(
                "info",
                "visual_followers_before_follow_block_checkpoint",
                phase="immediately_after_resolved_continue",
                follower_username=follower_un,
                source_profile_username=source_profile_username,
                fkey=fkey,
                resolve_streak=streak,
                followers_list_ready=followers_list_ready,
                ENABLE_REAL_FOLLOW=bool(getattr(config, "ENABLE_REAL_FOLLOW", False)),
                ENABLE_REAL_VISUAL_FOLLOW=bool(
                    getattr(config, "ENABLE_REAL_VISUAL_FOLLOW", False)
                ),
            )
        else:
            fkey = _norm_ig_handle(follower_un)

        if fkey and fkey in _RUNTIME_FAILED_NO_FOLLOW_TAP_FKEY:
            log(
                "info",
                "visual_followers_candidate_skipped_failed_no_follow_tap",
                follower_username=follower_un,
                fkey=fkey,
                source_profile_username=source_profile_username,
            )
            ok_sk, _ = return_to_followers_list(d, source_profile_username, pkg)
            if not ok_sk:
                return 42
            continue

        _eng_log(
            "follower_profile_open_success",
            "success",
            "follower_profile_open",
            {
                "follower_username": follower_un,
                "username_pending_profile_read": pending_username,
            },
        )
        _trace_visual_candidate_post_follower_open(
            pick_ctx=pick,
            follower_un_so_far=follower_un,
            det_ctx=det if isinstance(det, dict) else None,
            open_det_method=open_detection_method,
        )

        _vcid_sm = str(pick.get("visual_candidate_id") or "").strip()
        _sm_target_username = str(follower_un or "").strip()
        if _vcid_sm and not _sm_target_username:
            _sm_target_username = str(pick.get("resolved_username_hint") or "").strip()
        if _vcid_sm and not _sm_target_username:
            _sm_target_username = f"__visual_candidate:{_vcid_sm}"
        if _vcid_sm and not fkey:
            fkey = _norm_ig_handle(_sm_target_username)

        _enable_real_follow = bool(getattr(config, "ENABLE_REAL_FOLLOW", False))
        _enable_visual_follow = bool(getattr(config, "ENABLE_REAL_VISUAL_FOLLOW", False))
        _ct_follow_execution_enabled = _enable_real_follow or (
            _followers_resolved_continue_to_follow and _enable_visual_follow
        )
        if not _followers_resolved_continue_to_follow:
            log(
                "info",
                "visual_followers_before_follow_block_checkpoint",
                phase="non_pending_list_candidate",
                follower_username=follower_un,
                source_profile_username=source_profile_username,
                fkey=_norm_ig_handle(follower_un),
                followers_list_ready=followers_list_ready,
                ENABLE_REAL_FOLLOW=_enable_real_follow,
                ENABLE_REAL_VISUAL_FOLLOW=_enable_visual_follow,
                ct_follow_execution_enabled=_ct_follow_execution_enabled,
            )
        if (
            _followers_resolved_continue_to_follow
            and not _enable_real_follow
            and _enable_visual_follow
            and _ct_follow_execution_enabled
        ):
            log(
                "info",
                "visual_followers_ct_list_follow_gate_visual_fallback",
                follower_username=follower_un,
                source_profile_username=source_profile_username,
                message="ENABLE_REAL_FOLLOW_false_using_ENABLE_REAL_VISUAL_FOLLOW_for_ct_list",
            )

        follow_out: dict[str, Any] | None = None
        if _ct_follow_execution_enabled:
            if not followers_list_ready:
                log(
                    "error",
                    "follow_blocked_followers_list_not_opened",
                    follower_username=follower_un,
                    source_profile_username=source_profile_username,
                )
                _eng_log(
                    "follow_blocked",
                    "failed",
                    "followers_list_not_ready",
                    {"follower_username": follower_un},
                )
                ok_nf, _ = return_to_followers_list(d, source_profile_username, pkg)
                if not ok_nf:
                    if _vcid_sm:
                        log(
                            "error",
                            "visual_candidate_profile_flow_failed",
                            failure_reason="visual_candidate_return_ct_failed",
                            phase="follow_blocked_followers_list_not_opened",
                            source_profile_username=source_profile_username,
                            visual_candidate_id=pick.get("visual_candidate_id"),
                        )
                    return 42
                continue
            if (
                _session_follow_quota_exceeded()
                or _session_total_interactions_cap_exceeded()
                or _session_successful_interactions_cap_exceeded()
            ):
                log(
                    "warning",
                    "social_memory_follow_blocked",
                    follower_username=follower_un,
                    source_profile_username=source_profile_username,
                    reason="session_quota",
                )
                _eng_log(
                    "social_memory_follow_blocked",
                    "blocked",
                    "session_quota",
                    {"follower_username": follower_un, "reason": "session_quota"},
                )
                _RUNTIME_INTERACTED_USERNAMES.add(fkey)
                _RUNTIME_SKIPPED_USERNAMES.add(fkey)
                _RUNTIME_SEEN_FOLLOWER_USERNAMES.add(fkey)
                ok_sq, _ = return_to_followers_list(d, source_profile_username, pkg)
                if not ok_sq:
                    if _vcid_sm:
                        log(
                            "error",
                            "visual_candidate_profile_flow_failed",
                            failure_reason="visual_candidate_return_ct_failed",
                            phase="session_quota_follow_blocked",
                            source_profile_username=source_profile_username,
                            visual_candidate_id=pick.get("visual_candidate_id"),
                        )
                    return 42
                continue

            try:
                _follow_hdr_snap = _follow_ui_state_snapshot(d)
            except Exception:
                _follow_hdr_snap = "unknown"
            if _vcid_sm and _follow_hdr_snap != "follow":
                log(
                    "info",
                    "visual_candidate_follow_skipped",
                    source_profile_username=source_profile_username,
                    visual_candidate_id=pick.get("visual_candidate_id"),
                    reason=f"follow_header_not_invite:{_follow_hdr_snap}",
                    follow_header_state=_follow_hdr_snap,
                )
            if _vcid_sm and _follow_hdr_snap in ("following", "requested"):
                log(
                    "info",
                    "visual_candidate_already_connected_skip",
                    source_profile_username=source_profile_username,
                    follower_username=follower_un,
                    visual_candidate_id=pick.get("visual_candidate_id"),
                    skip_reason="candidate_already_connected_skip",
                    follow_header_state=_follow_hdr_snap,
                    memory_status="ui_header_only",
                    following_status=str(_follow_hdr_snap),
                )
                _fk_snap = _norm_ig_handle(str(follower_un or ""))
                if _fk_snap:
                    _RUNTIME_SEEN_FOLLOWER_USERNAMES.add(_fk_snap)
                    _RUNTIME_FOLLOWED_USERNAMES.add(_fk_snap)
                _hdr_mem = {
                    "memory_status": "ui_header",
                    "following_status": str(_follow_hdr_snap),
                }
                _ok_snap, _how_snap = _visual_candidate_already_connected_fast_return(
                    d,
                    source_profile_username=source_profile_username,
                    pkg=pkg,
                    follower_username=str(follower_un or ""),
                    visual_candidate_id=str(pick.get("visual_candidate_id") or ""),
                    skip_reason=(
                        "candidate_already_connected_skip:"
                        f"follow_header_not_invite:{_follow_hdr_snap}"
                    ),
                    memory_detail=_hdr_mem,
                    follow_header_state=str(_follow_hdr_snap),
                    account_id=str(account_id),
                    run_id=run_id,
                    supabase_mode=supabase_mode,
                    visual_loop_state=visual_loop_state,
                )
                if not _ok_snap:
                    return 42
                _ct_clear_candidate_attempt_timer()
                if _fk_snap:
                    _RUNTIME_FOLLOWERS_POST_RESOLVE_STREAK.pop(_fk_snap, None)
                continue
            _follow_invite_visible = _follow_hdr_snap == "follow" or _ct_list_raw_follow_invite_visible(
                d
            )
            try:
                _posts_cnt = visual_profile_stats_posts_count(d)
            except Exception:
                _posts_cnt = None
            if (
                _posts_cnt is not None
                and int(_posts_cnt) == 0
                and _follow_invite_visible
            ):
                log(
                    "info",
                    "visual_no_posts_yet_follow_allowed",
                    follower_username=follower_un,
                    source_profile_username=source_profile_username,
                    posts_count=0,
                    follow_header_state=_follow_hdr_snap,
                )

            log(
                "info",
                "visual_followers_before_social_memory_check",
                follower_username=follower_un,
                fkey=fkey,
                resolve_streak=_ct_follow_resolve_streak,
                follow_button_visible=_follow_invite_visible,
                follow_header_state=_follow_hdr_snap,
                posts_count=_posts_cnt,
                ENABLE_REAL_FOLLOW=_enable_real_follow,
                ENABLE_REAL_VISUAL_FOLLOW=_enable_visual_follow,
                ct_follow_tap_attempted_yet=fkey
                in _RUNTIME_CT_LIST_FOLLOW_TAP_ATTEMPTED_USERNAMES,
            )
            try:
                from followers_inter_candidate_perf import (
                    inter_candidate_on_social_memory_check_started,
                )

                inter_candidate_on_social_memory_check_started()
            except Exception:
                pass

            elig = _social_memory_load_and_evaluate(
                target_username=_sm_target_username if _vcid_sm else follower_un,
                source_profile=source_profile_username,
                account_id=account_id,
                run_id=run_id,
                supabase_mode=supabase_mode,
            )
            try:
                from followers_inter_candidate_perf import inter_candidate_on_social_memory_loaded

                inter_candidate_on_social_memory_loaded()
            except Exception:
                pass
            _sm_bypass_dup = _ct_list_bypass_runtime_duplicate_social_memory(
                d,
                elig=elig,
                fkey=fkey,
                followers_resolved_continue=_followers_resolved_continue_to_follow,
                resolve_streak=_ct_follow_resolve_streak,
                follow_header_snap=_follow_hdr_snap,
                follow_invite_visible=_follow_invite_visible,
                enable_real_follow=_enable_real_follow,
                enable_visual_follow=_enable_visual_follow,
            )
            if _sm_bypass_dup:
                log(
                    "info",
                    "visual_followers_social_memory_duplicate_ignored_before_follow",
                    follower_username=follower_un,
                    fkey=fkey,
                    social_memory_reason=elig.reason,
                    resolve_streak=_ct_follow_resolve_streak,
                    follow_header_state=_follow_hdr_snap,
                    source_profile_username=source_profile_username,
                )

            if not elig.allowed and not _sm_bypass_dup:
                if (
                    elig.reason == "runtime_already_interacted"
                    and fkey in _RUNTIME_CT_LIST_FOLLOW_TAP_ATTEMPTED_USERNAMES
                ):
                    log(
                        "info",
                        "visual_followers_social_memory_duplicate_blocked_after_follow_attempt",
                        follower_username=follower_un,
                        fkey=fkey,
                        source_profile_username=source_profile_username,
                    )
                if (
                    elig.reason == "runtime_already_interacted"
                    and _ct_follow_resolve_streak >= 4
                    and _followers_resolved_continue_to_follow
                ):
                    log(
                        "error",
                        "visual_followers_exit_antiloop_social_memory",
                        follower_username=follower_un,
                        fkey=fkey,
                        resolve_streak=_ct_follow_resolve_streak,
                        follow_header_state=_follow_hdr_snap,
                        follow_button_visible=_follow_invite_visible,
                        source_profile_username=source_profile_username,
                    )
                    if _vcid_sm:
                        log(
                            "error",
                            "visual_candidate_profile_flow_failed",
                            failure_reason="unknown_after_follow_state",
                            source_profile_username=source_profile_username,
                            visual_candidate_id=pick.get("visual_candidate_id"),
                            social_memory_reason=elig.reason,
                        )
                    _emit_performance_summary(
                        t0=t0,
                        warm_session_used=warm_session_used,
                        force_stop_used=force_stop_used,
                        exit_code=74,
                        target_username=source_profile_username,
                    )
                    return 74
                log(
                    "warning",
                    elig.log_event,
                    follower_username=follower_un,
                    source_profile_username=source_profile_username,
                    reason=elig.reason,
                )
                log(
                    "info",
                    "social_memory_interaction_state",
                    username=follower_un,
                    state=elig.interaction_state,
                    reason=elig.reason,
                )
                _persist_social_memory_follow_block(
                    elig=elig,
                    account_id=account_id,
                    run_id=run_id,
                    supabase_mode=supabase_mode,
                    target_username=_sm_target_username if _vcid_sm else follower_un,
                    source_profile=source_profile_username,
                )
                _eng_log(
                    "social_memory_skip",
                    "info",
                    elig.reason,
                    {
                        "follower_username": follower_un,
                        "interaction_state": elig.interaction_state,
                    },
                )
                _RUNTIME_INTERACTED_USERNAMES.add(fkey)
                _RUNTIME_SKIPPED_USERNAMES.add(fkey)
                _RUNTIME_SEEN_FOLLOWER_USERNAMES.add(fkey)
                ok_el, _ = return_to_followers_list(d, source_profile_username, pkg)
                if not ok_el:
                    if _vcid_sm:
                        log(
                            "error",
                            "visual_candidate_profile_flow_failed",
                            failure_reason="visual_candidate_return_ct_failed",
                            phase="social_memory_skip_return_list",
                            source_profile_username=source_profile_username,
                            visual_candidate_id=pick.get("visual_candidate_id"),
                            social_memory_reason=elig.reason,
                        )
                    return 42
                if _vcid_sm:
                    log(
                        "info",
                        "visual_candidate_follow_skipped",
                        source_profile_username=source_profile_username,
                        visual_candidate_id=pick.get("visual_candidate_id"),
                        reason=f"social_memory_blocked:{elig.reason}",
                    )
                continue

            _attempt_timeout_s = float(
                getattr(config, "CT_FOLLOW_CANDIDATE_ATTEMPT_TIMEOUT_S", 90) or 90
            )
            _attempt_timeout_s = max(60.0, min(120.0, _attempt_timeout_s))
            if _ct_candidate_follow_attempt_timed_out(
                fkey, timeout_s=_attempt_timeout_s
            ):
                _t0_attempt = float(_CT_CANDIDATE_ATTEMPT_STARTED_MONO or time.monotonic())
                _elapsed = time.monotonic() - _t0_attempt
                log(
                    "error",
                    "visual_followers_candidate_attempt_timeout",
                    follower_username=follower_un,
                    fkey=fkey,
                    source_profile_username=source_profile_username,
                    elapsed_s=round(_elapsed, 2),
                    timeout_s=_attempt_timeout_s,
                    resolve_streak=_ct_follow_resolve_streak,
                )
                _RUNTIME_SEEN_FOLLOWER_USERNAMES.add(fkey)
                _ct_clear_candidate_attempt_timer()
                _RUNTIME_FOLLOWERS_POST_RESOLVE_STREAK.pop(fkey, None)
                _ct_return_followers_list_or_canonical_reset(
                    d,
                    source_profile_username=source_profile_username,
                    pkg=pkg,
                    account_id=account_id or None,
                    reason="attempt_timeout",
                )
                continue
            if (
                _ct_follow_resolve_streak >= 2
                and fkey not in _RUNTIME_CT_LIST_FOLLOW_TAP_ATTEMPTED_USERNAMES
                and _CT_FOLLOW_NO_TAP_AFTER_START_COUNT.get(fkey, 0) >= 1
            ):
                log(
                    "warning",
                    "visual_followers_candidate_abandoned_resolve_streak_without_tap",
                    follower_username=follower_un,
                    fkey=fkey,
                    source_profile_username=source_profile_username,
                    resolve_streak=_ct_follow_resolve_streak,
                    no_tap_after_start_count=_CT_FOLLOW_NO_TAP_AFTER_START_COUNT.get(
                        fkey, 0
                    ),
                )
                _RUNTIME_SEEN_FOLLOWER_USERNAMES.add(fkey)
                _CT_FOLLOW_NO_TAP_AFTER_START_COUNT.pop(fkey, None)
                _ct_clear_candidate_attempt_timer()
                _RUNTIME_FOLLOWERS_POST_RESOLVE_STREAK.pop(fkey, None)
                _ct_return_followers_list_or_canonical_reset(
                    d,
                    source_profile_username=source_profile_username,
                    pkg=pkg,
                    account_id=account_id or None,
                    reason="resolve_streak_no_follow_tap",
                )
                continue
            _profile_follow_already_open = bool(_vcid_sm)
            if _profile_follow_already_open and str(follower_un or "").strip():
                log(
                    "info",
                    "visual_candidate_profile_already_open_resolved_username",
                    source_profile_username=source_profile_username,
                    visual_candidate_id=pick.get("visual_candidate_id"),
                    follower_username_resolved=str(follower_un).strip(),
                    profile_already_open=True,
                    note="visual_candidate_context_kept_for_follow_engine_v2",
                )
            if _profile_follow_already_open:
                log(
                    "info",
                    "visual_candidate_current_screen_guard_started",
                    source_profile_username=source_profile_username,
                    visual_candidate_id=pick.get("visual_candidate_id"),
                    follower_username=follower_un,
                )
                try:
                    from followers_inter_candidate_perf import (
                        inter_candidate_on_screen_guard_started,
                    )

                    inter_candidate_on_screen_guard_started()
                except Exception:
                    pass
                _g_pre = visual_candidate_follow_pre_follow_screen_guard(
                    d,
                    source_profile_username=source_profile_username,
                    pkg=pkg,
                    pick=pick,
                )
                _guard_ok = bool(_g_pre.get("ok"))
                _g_final = _g_pre
                _recovered_after_reopen = False
                if not _guard_ok:
                    log(
                        "warning",
                        "visual_candidate_profile_lost_before_follow",
                        visual_candidate_id=pick.get("visual_candidate_id"),
                        source_profile_username=source_profile_username,
                        action_bar_title=_g_pre.get("action_bar_title"),
                        navigation_state=_g_pre.get("navigation_state"),
                        navigation_confidence=_g_pre.get("navigation_confidence"),
                        navigation_reason=_g_pre.get("navigation_reason"),
                        follow_header_state=_g_pre.get("follow_header_state"),
                        followers_list_xml_hint=_g_pre.get("followers_list_xml_hint"),
                        reason=str(_g_pre.get("reason") or ""),
                    )
                    log(
                        "info",
                        "visual_candidate_reopen_from_followers_started",
                        visual_candidate_id=pick.get("visual_candidate_id"),
                        source_profile_username=source_profile_username,
                        prior_guard_reason=str(_g_pre.get("reason") or ""),
                    )
                    _ok_ret_list, _ret_list_meth = return_to_followers_list(
                        d, source_profile_username, pkg
                    )
                    if not _ok_ret_list:
                        log(
                            "error",
                            "visual_candidate_reopen_from_followers_failed",
                            visual_candidate_id=pick.get("visual_candidate_id"),
                            source_profile_username=source_profile_username,
                            reason=f"return_to_followers_list_failed:{_ret_list_meth}",
                        )
                        if _vcid_sm:
                            log(
                                "error",
                                "visual_candidate_profile_flow_failed",
                                failure_reason="visual_candidate_return_ct_failed",
                                phase="screen_guard_recovery_return_list",
                                source_profile_username=source_profile_username,
                                visual_candidate_id=pick.get("visual_candidate_id"),
                            )
                        return 42
                    if not open_follower_profile_from_list(
                        d, pick, source_profile_username, pkg
                    ):
                        log(
                            "error",
                            "visual_candidate_reopen_from_followers_failed",
                            visual_candidate_id=pick.get("visual_candidate_id"),
                            source_profile_username=source_profile_username,
                            reason="open_follower_profile_from_list_failed_after_list",
                        )
                        log(
                            "info",
                            "visual_candidate_follow_skipped",
                            source_profile_username=source_profile_username,
                            visual_candidate_id=pick.get("visual_candidate_id"),
                            reason="visual_candidate_screen_guard_recovery_exhausted",
                        )
                        _RUNTIME_SEEN_FOLLOWER_USERNAMES.add(fkey)
                        _RUNTIME_SKIPPED_USERNAMES.add(fkey)
                        _ct_clear_candidate_attempt_timer()
                        _RUNTIME_FOLLOWERS_POST_RESOLVE_STREAK.pop(fkey, None)
                        continue
                    _g_post = visual_candidate_follow_pre_follow_screen_guard(
                        d,
                        source_profile_username=source_profile_username,
                        pkg=pkg,
                        pick=pick,
                    )
                    if not _g_post.get("ok"):
                        log(
                            "error",
                            "visual_candidate_reopen_from_followers_failed",
                            visual_candidate_id=pick.get("visual_candidate_id"),
                            source_profile_username=source_profile_username,
                            reason=f"guard_failed_after_reopen:{_g_post.get('reason')}",
                            action_bar_title=_g_post.get("action_bar_title"),
                            navigation_state=_g_post.get("navigation_state"),
                        )
                        log(
                            "info",
                            "visual_candidate_follow_skipped",
                            source_profile_username=source_profile_username,
                            visual_candidate_id=pick.get("visual_candidate_id"),
                            reason="visual_candidate_screen_guard_recovery_exhausted",
                        )
                        _RUNTIME_SEEN_FOLLOWER_USERNAMES.add(fkey)
                        _RUNTIME_SKIPPED_USERNAMES.add(fkey)
                        _ct_clear_candidate_attempt_timer()
                        _RUNTIME_FOLLOWERS_POST_RESOLVE_STREAK.pop(fkey, None)
                        continue
                    log(
                        "info",
                        "visual_candidate_reopen_from_followers_success",
                        visual_candidate_id=pick.get("visual_candidate_id"),
                        source_profile_username=source_profile_username,
                    )
                    _g_final = _g_post
                    _recovered_after_reopen = True
                log(
                    "info",
                    "visual_candidate_current_screen_guard_passed",
                    source_profile_username=source_profile_username,
                    visual_candidate_id=pick.get("visual_candidate_id"),
                    action_bar_title=_g_final.get("action_bar_title"),
                    navigation_state=_g_final.get("navigation_state"),
                    recovered_after_reopen=_recovered_after_reopen,
                )
                try:
                    from followers_inter_candidate_perf import (
                        inter_candidate_on_screen_guard_passed,
                    )

                    inter_candidate_on_screen_guard_passed()
                except Exception:
                    pass
            log(
                "info",
                "visual_followers_perform_follow_safe_invocation",
                follower_username=follower_un,
                source_profile_username=source_profile_username,
                profile_already_open=_profile_follow_already_open,
                visual_candidate_id=pick.get("visual_candidate_id") if _vcid_sm else None,
                gate_real_follow=_enable_real_follow,
                gate_visual_follow_fallback=bool(
                    _followers_resolved_continue_to_follow
                    and not _enable_real_follow
                    and _enable_visual_follow
                ),
                social_memory_duplicate_bypassed=_sm_bypass_dup,
                attempt_elapsed_s=(
                    round(time.monotonic() - _CT_CANDIDATE_ATTEMPT_STARTED_MONO, 2)
                    if _CT_CANDIDATE_ATTEMPT_STARTED_MONO is not None
                    else None
                ),
            )
            _ff_s = float(
                getattr(config, "CT_FOLLOW_NO_TAP_FAIL_FAST_S", 5.0) or 5.0
            )
            log(
                "info",
                "visual_followers_follow_started_no_tap_watchdog_started",
                follower_username=follower_un,
                fkey=fkey,
                source_profile_username=source_profile_username,
                fail_fast_s=round(_ff_s, 3),
            )
            if _vcid_sm and not str(follower_un or "").strip():
                log(
                    "info",
                    "visual_candidate_follow_using_visual_id_for_logs",
                    visual_candidate_id=pick.get("visual_candidate_id"),
                    social_memory_target_username=_sm_target_username,
                    note="perform_follow_safe_profile_already_open_when_username_unresolved",
                )
            follow_out = perform_follow_safe(
                d,
                follower_un,
                pkg,
                profile_already_open=_profile_follow_already_open,
                visual_candidate_id=pick.get("visual_candidate_id") if _vcid_sm else None,
                source_profile_username=source_profile_username,
            )
            _ct_clear_candidate_attempt_timer()
            _flush_follow_action_logs_to_supabase(
                events=list(follow_out.get("events") or []),
                run_id=run_id,
                account_id=account_id,
                target_username=_sm_target_username if _vcid_sm else follower_un,
                supabase_mode=supabase_mode,
            )

            if (
                int(follow_out.get("failure_code") or 0) == 74
                or str(follow_out.get("failure_reason") or "") == "review_popup_unhandled"
            ):
                log(
                    "error",
                    "follow_review_popup_unhandled_safe_stop_started",
                    follower_username=follower_un,
                    fkey=fkey,
                    source_profile_username=source_profile_username,
                    visual_candidate_id=str(pick.get("visual_candidate_id") or ""),
                    failure_code=int(follow_out.get("failure_code") or 74),
                )
                try:
                    if hasattr(d, "app_stop"):
                        d.app_stop(pkg)
                except Exception:
                    pass
                try:
                    force_stop(d, pkg)
                except Exception as e_fs:
                    log(
                        "warning",
                        "follow_review_popup_unhandled_safe_stop_force_stop_failed",
                        error=str(e_fs),
                        package=pkg,
                    )
                if bool(getattr(config, "HOME_AFTER_RUN", True)):
                    try:
                        press_home(d)
                        time.sleep(0.2)
                    except Exception as e_h:
                        log(
                            "warning",
                            "follow_review_popup_unhandled_safe_stop_home_failed",
                            error=str(e_h),
                        )
                log(
                    "info",
                    "follow_review_popup_unhandled_safe_stop_done",
                    follower_username=follower_un,
                    source_profile_username=source_profile_username,
                    visual_candidate_id=str(pick.get("visual_candidate_id") or ""),
                )
                force_stop_used = True
                runner_invalidate_visual_followers_session_after_safe_stop(
                    d,
                    source_profile_username=source_profile_username,
                )
                _VISUAL_FOLLOWERS_OPEN_COUNT_THIS_SESSION = 0
                _eng_log(
                    "followers_engine_session_terminated_safe_stop",
                    "warning",
                    "follow_review_popup_unhandled",
                    {
                        "iterations": processed,
                        "visual_candidate_id": pick.get("visual_candidate_id"),
                        "follower_username": follower_un,
                    },
                )
                _emit_performance_summary(
                    t0=t0,
                    warm_session_used=warm_session_used,
                    force_stop_used=True,
                    exit_code=99,
                    target_username=source_profile_username,
                )
                return 99

            if _ct_follow_out_is_failed_no_follow_tap(follow_out):
                log(
                    "error",
                    "visual_followers_failed_no_follow_tap_abandon_candidate",
                    follower_username=follower_un,
                    fkey=fkey,
                    source_profile_username=source_profile_username,
                    failure_code=int(follow_out.get("failure_code") or 72),
                )
                _RUNTIME_FAILED_NO_FOLLOW_TAP_FKEY.add(fkey)
                _RUNTIME_SEEN_FOLLOWER_USERNAMES.add(fkey)
                _CT_FOLLOW_NO_TAP_AFTER_START_COUNT.pop(fkey, None)
                _RUNTIME_FOLLOWERS_POST_RESOLVE_STREAK.pop(fkey, None)
                ok_ab, _ = return_to_followers_list(d, source_profile_username, pkg)
                if not ok_ab:
                    if _vcid_sm:
                        log(
                            "error",
                            "visual_candidate_profile_flow_failed",
                            failure_reason="visual_candidate_return_ct_failed",
                            phase="after_no_follow_tap_failure",
                            source_profile_username=source_profile_username,
                            visual_candidate_id=pick.get("visual_candidate_id"),
                        )
                    return 42
                log(
                    "info",
                    "visual_followers_next_candidate_after_no_tap_failure",
                    follower_username=follower_un,
                    fkey=fkey,
                    source_profile_username=source_profile_username,
                )
                continue

            _had_tap = _ct_follow_out_had_tap_sent(follow_out)
            _skipped = bool(follow_out.get("skipped_tap"))
            if _had_tap or _skipped:
                _RUNTIME_CT_LIST_FOLLOW_TAP_ATTEMPTED_USERNAMES.add(fkey)
                _CT_FOLLOW_NO_TAP_AFTER_START_COUNT.pop(fkey, None)
            elif _ct_follow_out_follow_started_no_tap(follow_out):
                _CT_FOLLOW_NO_TAP_AFTER_START_COUNT[fkey] = (
                    _CT_FOLLOW_NO_TAP_AFTER_START_COUNT.get(fkey, 0) + 1
                )
                if _CT_FOLLOW_NO_TAP_AFTER_START_COUNT[fkey] >= 2:
                    log(
                        "warning",
                        "visual_followers_candidate_abandoned_follow_no_tap_retries",
                        follower_username=follower_un,
                        fkey=fkey,
                        source_profile_username=source_profile_username,
                        attempts_without_tap=_CT_FOLLOW_NO_TAP_AFTER_START_COUNT[fkey],
                        failure_code=int(follow_out.get("failure_code") or 0),
                    )
                    _RUNTIME_SEEN_FOLLOWER_USERNAMES.add(fkey)
                    _CT_FOLLOW_NO_TAP_AFTER_START_COUNT.pop(fkey, None)
                    _RUNTIME_FOLLOWERS_POST_RESOLVE_STREAK.pop(fkey, None)
                    _ct_return_followers_list_or_canonical_reset(
                        d,
                        source_profile_username=source_profile_username,
                        pkg=pkg,
                        account_id=account_id or None,
                        reason="follow_no_tap_retries",
                    )
                    continue

            if not follow_out.get("ok"):
                if _vcid_sm and _profile_follow_already_open:
                    reco_r = visual_follow_post_action_reconcile(
                        d,
                        follow_out=dict(follow_out),
                        profile_already_open=True,
                        visual_candidate_id=str(pick.get("visual_candidate_id") or ""),
                    )
                    if reco_r.get("inferred_follow_success"):
                        follow_out["ok"] = True
                        follow_out["failure_code"] = None
                        follow_out["failure_reason"] = None
                        follow_out["reconciled_inferred_success"] = True
                        follow_out["reconciled_reason"] = str(reco_r.get("reason") or "")
                        follow_out.pop("visual_follow_failure_reason", None)
                        st_r = str(reco_r.get("current_follow_state") or "")
                        if st_r in ("following", "requested"):
                            follow_out["follow_state_after"] = st_r
                        elif not str(follow_out.get("follow_state_after") or "").strip():
                            follow_out["follow_state_after"] = st_r
                        if (
                            not _ct_follow_out_had_tap_sent(follow_out)
                            and str(follow_out.get("follow_state_after") or "")
                            in ("following", "requested")
                        ):
                            follow_out["skipped_tap"] = True
                        log(
                            "info",
                            "visual_follow_runner_follow_out_reconciled_to_success",
                            visual_candidate_id=pick.get("visual_candidate_id"),
                            source_profile_username=source_profile_username,
                            follower_username=follower_un,
                            **reco_r,
                        )
                if not follow_out.get("ok"):
                    fc = int(follow_out.get("failure_code") or 33)
                    if _vcid_sm:
                        log(
                            "error",
                            "visual_candidate_profile_flow_failed",
                            failure_reason="visual_candidate_follow_action_failed",
                            failure_code=fc,
                            source_profile_username=source_profile_username,
                            visual_candidate_id=pick.get("visual_candidate_id"),
                            follower_username=follower_un,
                        )
                    _SESSION_COUNTERS["interactions"] += 1
                    _RUNTIME_INTERACTED_USERNAMES.add(fkey)
                    _RUNTIME_SEEN_FOLLOWER_USERNAMES.add(fkey)
                    if supabase_mode and account_id:
                        _vfp_un = str(follower_un or "").strip()
                        if _vfp_un:
                            _safe_supabase_call(
                                "record_follow_interaction_outcome",
                                account_id,
                                follower_un,
                                source_profile_username,
                                run_id=run_id or None,
                                session_id=_SESSION_SOCIAL_ID or None,
                                follow_ok=False,
                                skipped_tap=False,
                                follow_state_after=str(follow_out.get("follow_state_after") or ""),
                                follow_status=None,
                                failure_code=fc,
                                failure_reason=_exit_reason_from_code(fc),
                            )
                            log(
                                "info",
                                "social_memory_updated",
                                target_username=follower_un,
                                kind="follow_failed",
                            )
                        else:
                            log(
                                "info",
                                "followers_follow_persistence_skipped_unresolved_username",
                                source_profile_username=source_profile_username,
                                visual_candidate_id=str(pick.get("visual_candidate_id") or ""),
                                follow_state_after=str(follow_out.get("follow_state_after") or ""),
                                follow_ok=False,
                                reason="follow_failed_without_resolved_username",
                            )
                    ok_f, _ = return_to_followers_list(d, source_profile_username, pkg)
                    if not ok_f:
                        if _vcid_sm:
                            log(
                                "error",
                                "visual_candidate_profile_flow_failed",
                                failure_reason="visual_candidate_return_ct_failed",
                                phase="after_follow_action_failed_return_list",
                                source_profile_username=source_profile_username,
                                visual_candidate_id=pick.get("visual_candidate_id"),
                            )
                        return 42
                    continue

            fs_af = str(follow_out.get("follow_state_after") or "")
            if bool(follow_out.get("skipped_tap")):
                f_st = "already_following"
            elif fs_af == "requested":
                f_st = "requested"
            else:
                f_st = "following"

            if (
                bool(follow_out.get("ok"))
                and fs_af in ("following", "requested")
                and not str(follower_un or "").strip()
            ):
                try:
                    _urg_pf = str(
                        read_current_profile_username_for_follow_gate(d) or ""
                    ).strip().lstrip("@")
                except Exception:
                    _urg_pf = ""
                if _urg_pf and _is_plausible_public_ig_username(_urg_pf):
                    follower_un = _urg_pf
                    pick["username"] = _urg_pf
                    pick["username_pending_profile_read"] = False
                    pick["username_resolution_source"] = (
                        "profile_action_bar_post_follow_recovery"
                    )
                    fkey = _norm_ig_handle(follower_un)
                    try:
                        log(
                            "info",
                            "followers_follow_persistence_username_recovered_post_follow",
                            source_profile_username=source_profile_username,
                            visual_candidate_id=str(pick.get("visual_candidate_id") or ""),
                            resolved_username=_urg_pf,
                            follow_state_after=fs_af,
                        )
                    except Exception:
                        pass

            if (
                bool(follow_out.get("ok"))
                and fkey
                and str(follower_un or "").strip()
                and fs_af in ("following", "requested")
            ):
                _RUNTIME_FOLLOWED_USERNAMES.add(fkey)
                _RUNTIME_SEEN_FOLLOWER_USERNAMES.add(fkey)
                try:
                    log(
                        "info",
                        "followers_runtime_followed_username_recorded",
                        source_profile_username=source_profile_username,
                        follower_username=str(follower_un or "").strip(),
                        normalized_username=fkey,
                        visual_candidate_id=str(pick.get("visual_candidate_id") or ""),
                        follow_state_after=fs_af,
                    )
                except Exception:
                    pass

            _vcid_arm = str(pick.get("visual_candidate_id") or "").strip()
            if (
                bool(follow_out.get("ok"))
                and fs_af in ("following", "requested")
                and not str(follower_un or "").strip()
                and _vcid_arm
            ):
                visual_loop_state["pending_recent_verified_follow_visual_candidate_skip"] = {
                    "source_profile_username": str(source_profile_username or ""),
                    "visual_candidate_id": _vcid_arm,
                    "armed_loop_iteration": int(followers_engine_loop_iteration),
                }
                try:
                    log(
                        "info",
                        "followers_recent_verified_follow_visual_candidate_skip_armed",
                        source_profile_username=source_profile_username,
                        visual_candidate_id=_vcid_arm,
                        follower_username=str(follower_un or ""),
                        follow_state_after=fs_af,
                        reason="verified_follow_without_resolved_username",
                    )
                except Exception:
                    pass

            _vc_skip_conn, _vc_skip_reason = _ct_follow_out_visual_already_connected(
                follow_out
            )
            if _vc_skip_conn and bool(follow_out.get("ok")):
                _fs_b = str(follow_out.get("follow_state_before") or "")
                log(
                    "info",
                    "visual_candidate_already_connected_detected",
                    visual_candidate_id=pick.get("visual_candidate_id"),
                    follower_username=follower_un,
                    follow_state_before=_fs_b,
                    follow_state_after=fs_af,
                    skipped_tap=bool(follow_out.get("skipped_tap")),
                    source_profile_username=source_profile_username,
                    skip_reason=_vc_skip_reason,
                )
                log(
                    "info",
                    "visual_candidate_already_connected_skipped",
                    visual_candidate_id=pick.get("visual_candidate_id"),
                    follower_username=follower_un,
                    follow_state_before=_fs_b,
                    follow_state_after=fs_af,
                    skipped_tap=bool(follow_out.get("skipped_tap")),
                    source_profile_username=source_profile_username,
                    skip_reason=_vc_skip_reason,
                )
                log(
                    "info",
                    "visual_candidate_already_connected_no_post_follow_flow",
                    visual_candidate_id=pick.get("visual_candidate_id"),
                    follower_username=follower_un,
                    follow_state_before=_fs_b,
                    follow_state_after=fs_af,
                    skipped_tap=bool(follow_out.get("skipped_tap")),
                    source_profile_username=source_profile_username,
                    skip_reason=_vc_skip_reason,
                )
                if supabase_mode and account_id:
                    _vfp_un_ac = str(follower_un or "").strip()
                    if _vfp_un_ac:
                        mem = _safe_supabase_call(
                            "record_follow_interaction_outcome",
                            account_id,
                            follower_un,
                            source_profile_username,
                            run_id=run_id or None,
                            session_id=_SESSION_SOCIAL_ID or None,
                            follow_ok=False,
                            skipped_tap=True,
                            follow_state_after=fs_af,
                            follow_status="already_following",
                            failure_code=None,
                            failure_reason=f"already_connected:{_vc_skip_reason}",
                        )
                        log(
                            "info",
                            "social_memory_updated",
                            target_username=follower_un,
                            kind="follow_skipped_already_connected",
                            memory_ok=(mem or {}).get("ok"),
                        )
                    else:
                        log(
                            "info",
                            "followers_follow_persistence_skipped_unresolved_username",
                            source_profile_username=source_profile_username,
                            visual_candidate_id=str(pick.get("visual_candidate_id") or ""),
                            follow_state_after=fs_af,
                            follow_ok=False,
                            reason="already_connected_without_resolved_username",
                        )
                if fkey:
                    _RUNTIME_SEEN_FOLLOWER_USERNAMES.add(fkey)
                    _RUNTIME_FOLLOWED_USERNAMES.add(fkey)
                    _RUNTIME_SKIPPED_USERNAMES.add(fkey)
                    _RUNTIME_INTERACTED_USERNAMES.add(fkey)
                _SESSION_COUNTERS["interactions"] += 1
                _RUNTIME_FOLLOWERS_POST_RESOLVE_STREAK.pop(fkey, None)
                _ok_surf, _surf_meta = followers_surface_quick_revalidate(
                    d,
                    source_profile_username=source_profile_username,
                )
                if _ok_surf:
                    log(
                        "info",
                        "visual_candidate_already_connected_continue_on_current_followers_surface",
                        visual_candidate_id=pick.get("visual_candidate_id"),
                        follower_username=follower_un,
                        source_profile_username=source_profile_username,
                        skip_reason=_vc_skip_reason,
                        quality_reason=str((_surf_meta or {}).get("quality_reason") or ""),
                        elapsed_ms_total=round(
                            float((_surf_meta or {}).get("elapsed_ms_total") or 0.0), 2
                        ),
                    )
                    try:
                        log(
                            "info",
                            "visual_candidate_duplicate_already_following_recovered_continue",
                            source_profile_username=source_profile_username,
                            visual_candidate_id=str(pick.get("visual_candidate_id") or ""),
                            follower_username=str(follower_un or ""),
                            follow_state=str(fs_af or ""),
                            return_method="followers_surface_quick_revalidate_on_list",
                            session_outcome="duplicate_reopen_skipped_continue",
                        )
                    except Exception:
                        pass
                    continue
                try:
                    d.press("back")
                except Exception as e_bk:
                    log(
                        "warning",
                        "visual_candidate_already_connected_safe_back_failed",
                        error=str(e_bk),
                        follower_username=follower_un,
                        source_profile_username=source_profile_username,
                    )
                time.sleep(0.18)
                _ok_after, _surf_after = followers_surface_quick_revalidate(
                    d,
                    source_profile_username=source_profile_username,
                )
                if _ok_after:
                    log(
                        "info",
                        "visual_candidate_already_connected_safe_back_success",
                        visual_candidate_id=pick.get("visual_candidate_id"),
                        follower_username=follower_un,
                        source_profile_username=source_profile_username,
                        skip_reason=_vc_skip_reason,
                        quality_reason=str((_surf_after or {}).get("quality_reason") or ""),
                        elapsed_ms_total=round(
                            float((_surf_after or {}).get("elapsed_ms_total") or 0.0), 2
                        ),
                    )
                    try:
                        log(
                            "info",
                            "visual_candidate_duplicate_already_following_recovered_continue",
                            source_profile_username=source_profile_username,
                            visual_candidate_id=str(pick.get("visual_candidate_id") or ""),
                            follower_username=str(follower_un or ""),
                            follow_state=str(fs_af or ""),
                            return_method="back_press_then_surface_revalidate",
                            session_outcome="duplicate_reopen_skipped_continue",
                        )
                    except Exception:
                        pass
                    continue
                _ok_ret_dup, _how_dup = return_to_followers_list(
                    d, source_profile_username, pkg
                )
                if _ok_ret_dup:
                    _ok_nav_dup, _surf_nav_dup = followers_surface_quick_revalidate(
                        d,
                        source_profile_username=source_profile_username,
                    )
                    if _ok_nav_dup:
                        if fkey:
                            _RUNTIME_FOLLOWED_USERNAMES.add(fkey)
                            _RUNTIME_SEEN_FOLLOWER_USERNAMES.add(fkey)
                        try:
                            log(
                                "info",
                                "visual_candidate_duplicate_already_following_recovered_continue",
                                source_profile_username=source_profile_username,
                                visual_candidate_id=str(pick.get("visual_candidate_id") or ""),
                                follower_username=str(follower_un or ""),
                                follow_state=str(fs_af or ""),
                                return_method=str(_how_dup or ""),
                                session_outcome="duplicate_reopen_skipped_continue",
                            )
                        except Exception:
                            pass
                        continue
                log(
                    "info",
                    "visual_candidate_already_connected_safe_stop_started",
                    visual_candidate_id=pick.get("visual_candidate_id"),
                    follower_username=follower_un,
                    source_profile_username=source_profile_username,
                    skip_reason=_vc_skip_reason,
                    session_outcome="already_connected_safe_skipped",
                    surface_revalidate_reason=str((_surf_meta or {}).get("reason") or ""),
                    surface_revalidate_after_back=str((_surf_after or {}).get("reason") or ""),
                )
                try:
                    if hasattr(d, "app_stop"):
                        d.app_stop(pkg)
                except Exception:
                    pass
                try:
                    force_stop(d, pkg)
                except Exception as e_fs:
                    log(
                        "warning",
                        "visual_candidate_already_connected_safe_stop_force_stop_failed",
                        error=str(e_fs),
                        package=pkg,
                    )
                if bool(getattr(config, "HOME_AFTER_RUN", True)):
                    try:
                        press_home(d)
                        time.sleep(0.2)
                    except Exception as e_h:
                        log(
                            "warning",
                            "visual_candidate_already_connected_safe_stop_home_failed",
                            error=str(e_h),
                        )
                log(
                    "info",
                    "visual_candidate_already_connected_safe_stop_done",
                    visual_candidate_id=pick.get("visual_candidate_id"),
                    follower_username=follower_un,
                    source_profile_username=source_profile_username,
                    skip_reason=_vc_skip_reason,
                    session_outcome="already_connected_safe_skipped",
                )
                force_stop_used = True
                _followers_clear_pending_visual_candidate_skip()
                _followers_clear_pending_already_connected_visual_skip(
                    clear_reason="already_connected_safe_stop",
                )
                runner_invalidate_visual_followers_session_after_safe_stop(
                    d,
                    source_profile_username=source_profile_username,
                )
                _VISUAL_FOLLOWERS_OPEN_COUNT_THIS_SESSION = 0
                _eng_log(
                    "followers_engine_session_terminated_safe_stop",
                    "warning",
                    "already_connected_safe_skipped",
                    {
                        "iterations": processed,
                        "visual_candidate_id": pick.get("visual_candidate_id"),
                        "follower_username": follower_un,
                        "surface_revalidate": _surf_meta,
                        "surface_revalidate_after_back": _surf_after,
                    },
                )
                _emit_performance_summary(
                    t0=t0,
                    warm_session_used=warm_session_used,
                    force_stop_used=True,
                    exit_code=98,
                    target_username=source_profile_username,
                )
                return 98

            if not bool(follow_out.get("skipped_tap")):
                _RUNTIME_FOLLOW_COUNT += 1
            _RUNTIME_FOLLOWED_USERNAMES.add(fkey)
            _RUNTIME_INTERACTED_USERNAMES.add(fkey)
            _SESSION_COUNTERS["follows"] += 1
            follows_completed_count += 1
            _SESSION_COUNTERS["interactions"] += 1
            _SESSION_COUNTERS["successful_interactions"] += 1

            if supabase_mode and account_id:
                _vfp_un_ok = str(follower_un or "").strip()
                if _vfp_un_ok:
                    mem = _safe_supabase_call(
                        "record_follow_interaction_outcome",
                        account_id,
                        follower_un,
                        source_profile_username,
                        run_id=run_id or None,
                        session_id=_SESSION_SOCIAL_ID or None,
                        follow_ok=True,
                        skipped_tap=bool(follow_out.get("skipped_tap")),
                        follow_state_after=fs_af,
                        follow_status=f_st,
                        failure_code=None,
                        failure_reason=None,
                    )
                    log(
                        "info",
                        "social_memory_updated",
                        target_username=follower_un,
                        kind="follow_success",
                        memory_ok=(mem or {}).get("ok"),
                    )
                    _eng_log(
                        "social_memory_updated",
                        "success",
                        "follow_persisted",
                        {"follower_username": follower_un, "follow_status": f_st},
                    )
                else:
                    log(
                        "info",
                        "followers_follow_persistence_skipped_unresolved_username",
                        source_profile_username=source_profile_username,
                        visual_candidate_id=str(pick.get("visual_candidate_id") or ""),
                        follow_state_after=fs_af,
                        follow_ok=True,
                        reason="verified_follow_without_resolved_username",
                    )
        else:
            if _vcid_sm and _followers_resolved_continue_to_follow:
                log(
                    "info",
                    "visual_candidate_follow_skipped",
                    source_profile_username=source_profile_username,
                    visual_candidate_id=pick.get("visual_candidate_id"),
                    reason="visual_candidate_follow_not_triggered_ct_follow_execution_disabled",
                    ENABLE_REAL_FOLLOW=_enable_real_follow,
                    ENABLE_REAL_VISUAL_FOLLOW=_enable_visual_follow,
                    followers_list_ready=followers_list_ready,
                )
            if _followers_resolved_continue_to_follow:
                log(
                    "error",
                    "visual_followers_resolved_username_follow_not_executed",
                    reason="ct_follow_execution_disabled_after_resolved_continue",
                    follower_username=follower_un,
                    source_profile_username=source_profile_username,
                    ENABLE_REAL_FOLLOW=_enable_real_follow,
                    ENABLE_REAL_VISUAL_FOLLOW=_enable_visual_follow,
                    followers_list_ready=followers_list_ready,
                )
                _eng_log(
                    "visual_followers_follow_blocked",
                    "failed",
                    "both_follow_flags_false_after_continue_to_follow",
                    {
                        "follower_username": follower_un,
                        "ENABLE_REAL_FOLLOW": _enable_real_follow,
                        "ENABLE_REAL_VISUAL_FOLLOW": _enable_visual_follow,
                    },
                )
                if pick.get("visual_candidate_id"):
                    log(
                        "error",
                        "visual_candidate_profile_flow_failed",
                        failure_reason="visual_candidate_follow_not_triggered",
                        source_profile_username=source_profile_username,
                        visual_candidate_id=pick.get("visual_candidate_id"),
                        follower_username=follower_un,
                        ENABLE_REAL_FOLLOW=_enable_real_follow,
                        ENABLE_REAL_VISUAL_FOLLOW=_enable_visual_follow,
                    )
            else:
                log(
                    "info",
                    "followers_follow_skipped",
                    reason="ENABLE_REAL_FOLLOW_false",
                    follower_username=pick.get("username"),
                    source_profile_username=source_profile_username,
                )
                if _vcid_sm:
                    log(
                        "info",
                        "visual_candidate_follow_skipped",
                        source_profile_username=source_profile_username,
                        visual_candidate_id=pick.get("visual_candidate_id"),
                        reason="non_pending_list_candidate_follow_disabled",
                    )

        _pf_follow_ok = bool(
            follow_out is not None
            and bool(follow_out.get("ok"))
            and _ct_follow_execution_enabled
        )
        if pick.get("visual_candidate_id"):
            _pf = run_visual_candidate_post_follow_phase(
                d,
                pkg=pkg,
                source_profile_username=source_profile_username,
                visual_candidate_id=str(pick.get("visual_candidate_id") or ""),
                follower_username=str(follower_un or ""),
                follow_success_verified=_pf_follow_ok,
                follow_state_after=str((follow_out or {}).get("follow_state_after") or ""),
                skipped_tap=bool((follow_out or {}).get("skipped_tap")),
                det=det if isinstance(det, dict) else None,
                session_likes_used=int(_SESSION_COUNTERS.get("likes") or 0),
            )
            if supabase_mode and account_id and str(follower_un or "").strip():
                _mute_pf = _pf.get("mute") if isinstance(_pf.get("mute"), dict) else {}
                if (
                    bool(_mute_pf.get("mute_started"))
                    and bool(_mute_pf.get("ok"))
                    and bool(_mute_pf.get("mute_engine_v2"))
                    and str(_mute_pf.get("mute_v2_outcome") or "")
                    in ("success", "partial_success")
                ):
                    _safe_supabase_call(
                        "record_mute_interaction_success",
                        account_id,
                        follower_un,
                        source_profile_username,
                        run_id=run_id or None,
                        session_id=_SESSION_SOCIAL_ID or None,
                        muted_posts=bool(_mute_pf.get("posts_verified")),
                        muted_stories=bool(_mute_pf.get("stories_verified")),
                        mute_partial=bool(_mute_pf.get("mute_v2_partial")),
                        visual_candidate_id=str(pick.get("visual_candidate_id") or ""),
                        timings_ms=_mute_pf.get("timings_ms")
                        if isinstance(_mute_pf.get("timings_ms"), dict)
                        else {},
                    )
                _likes_pf = _pf.get("likes") if isinstance(_pf.get("likes"), dict) else {}
                _likes_phase = str(_likes_pf.get("phase_outcome") or "")
                _liked_n = int(_likes_pf.get("liked_count") or 0)
                if _likes_phase in ("success", "partial_success") and _liked_n > 0:
                    _SESSION_COUNTERS["likes"] = int(_SESSION_COUNTERS.get("likes") or 0) + _liked_n
                    _safe_supabase_call(
                        "record_post_like_interaction_success",
                        account_id,
                        follower_un,
                        source_profile_username,
                        run_id=run_id or None,
                        session_id=_SESSION_SOCIAL_ID or None,
                        liked_count=_liked_n,
                        target_count=int(_likes_pf.get("target_count") or 0),
                        attempted_count=int(_likes_pf.get("attempted_count") or 0),
                        skipped_already_liked_count=int(
                            _likes_pf.get("skipped_already_liked_count") or 0
                        ),
                        phase_outcome=_likes_phase,
                        post_like_mode=str(_likes_pf.get("post_like_mode") or ""),
                        visual_candidate_id=str(pick.get("visual_candidate_id") or ""),
                        timings_ms=_likes_pf.get("timings_ms")
                        if isinstance(_likes_pf.get("timings_ms"), dict)
                        else {},
                        per_post=_likes_pf.get("per_post")
                        if isinstance(_likes_pf.get("per_post"), list)
                        else None,
                    )
                    log(
                        "info",
                        "post_likes_persisted",
                        target_username=follower_un,
                        source_profile_username=source_profile_username,
                        liked_count=_liked_n,
                        phase_outcome=_likes_phase,
                        visual_candidate_id=str(pick.get("visual_candidate_id") or ""),
                    )
            ok_back = bool(_pf.get("return_ok"))
            how = str(_pf.get("return_how") or "")
            _pf_fail = str(_pf.get("return_failure_reason") or "")
            if not ok_back:
                _eng_log(
                    "followers_list_engine_return_failed",
                    "failed",
                    "return_after_follow_failed",
                    {"how": how, "post_follow_failure_reason": _pf_fail},
                )
                if _pf_follow_ok:
                    _partial_note = "follow_verified_but_return_ct_failed"
                    if _pf_fail == "post_follow_return_ct_aborted_to_prevent_drift":
                        _partial_note = (
                            "follow_verified_but_return_ct_aborted_to_prevent_drift"
                        )
                    elif _pf_fail == "post_follow_return_ct_round_budget_exceeded":
                        _partial_note = (
                            "follow_verified_but_return_ct_round_budget_exceeded"
                        )
                    elif _pf_fail == "post_follow_return_ct_fast_abort_after_foreign_profile":
                        _partial_note = (
                            "follow_verified_but_return_ct_fast_abort_foreign_profile"
                        )
                    elif _pf_fail == "post_follow_return_ct_compact_abort_no_list_confirmed":
                        _partial_note = "follow_verified_but_compact_return_aborted"
                    elif _pf_fail == "post_follow_return_ct_compact_contract_violation":
                        _partial_note = (
                            "follow_verified_but_compact_return_contract_violation"
                        )
                    _partial_log: dict[str, Any] = {
                        "source_profile_username": source_profile_username,
                        "visual_candidate_id": pick.get("visual_candidate_id"),
                        "follower_username": follower_un,
                        "note": _partial_note,
                        "post_follow_failure_reason": _pf_fail,
                    }
                    if _pf_fail in _POST_FOLLOW_RETURN_CT_SAFE_STOP_FAILURE_REASONS:
                        _partial_log["session_outcome"] = "partial_safe_stopped"
                    log(
                        "warning",
                        "visual_candidate_post_follow_session_partial",
                        **_partial_log,
                    )
                    if _pf_fail in _POST_FOLLOW_RETURN_CT_SAFE_STOP_FAILURE_REASONS:
                        _mute_pf = (
                            _pf.get("mute") if isinstance(_pf.get("mute"), dict) else {}
                        )
                        log(
                            "warning",
                            "visual_candidate_post_follow_no_recovery_after_compact_abort",
                            source_profile_username=source_profile_username,
                            visual_candidate_id=pick.get("visual_candidate_id"),
                            follower_username=follower_un,
                            post_follow_failure_reason=_pf_fail,
                            session_outcome="partial_safe_stopped",
                        )
                        log(
                            "info",
                            "visual_candidate_post_follow_safe_stop_started",
                            source_profile_username=source_profile_username,
                            visual_candidate_id=pick.get("visual_candidate_id"),
                            follower_username=follower_un,
                            package=pkg,
                            post_follow_failure_reason=_pf_fail,
                            session_outcome="partial_safe_stopped",
                        )
                        try:
                            force_stop(d, pkg)
                        except Exception as e_fs:
                            log(
                                "warning",
                                "visual_candidate_post_follow_safe_stop_force_stop_failed",
                                error=str(e_fs),
                                package=pkg,
                            )
                        try:
                            if hasattr(d, "app_stop"):
                                d.app_stop(pkg)
                        except Exception:
                            pass
                        if bool(getattr(config, "HOME_AFTER_RUN", True)):
                            try:
                                press_home(d)
                                time.sleep(0.2)
                            except Exception as e_h:
                                log(
                                    "warning",
                                    "visual_candidate_post_follow_safe_stop_home_failed",
                                    error=str(e_h),
                                )
                        log(
                            "info",
                            "visual_candidate_post_follow_safe_stop_done",
                            source_profile_username=source_profile_username,
                            visual_candidate_id=pick.get("visual_candidate_id"),
                            follower_username=follower_un,
                            session_outcome="partial_safe_stopped",
                            follow_success_verified=True,
                            return_ct_failed=True,
                            post_follow_failure_reason=_pf_fail,
                            mute_ok=bool(_mute_pf.get("ok")),
                            mute_skipped=bool(_mute_pf.get("skipped")),
                            mute_skipped_reason=_mute_pf.get("skipped_reason"),
                            mute_failure_reason=_mute_pf.get("failure_reason"),
                        )
                        log(
                            "warning",
                            "visual_candidate_post_follow_safe_stop_terminating_run",
                            source_profile_username=source_profile_username,
                            visual_candidate_id=pick.get("visual_candidate_id"),
                            follower_username=follower_un,
                            post_follow_failure_reason=_pf_fail,
                            session_outcome="partial_safe_stopped",
                        )
                        force_stop_used = True
                        _followers_clear_pending_visual_candidate_skip()
                        _followers_clear_pending_already_connected_visual_skip(
                            clear_reason="post_follow_safe_stop",
                        )
                        runner_invalidate_visual_followers_session_after_safe_stop(
                            d,
                            source_profile_username=source_profile_username,
                        )
                        _VISUAL_FOLLOWERS_OPEN_COUNT_THIS_SESSION = 0
                        fk_session = _norm_ig_handle(str(follower_un or ""))
                        if fk_session:
                            _RUNTIME_SEEN_FOLLOWER_USERNAMES.add(fk_session)
                            _RUNTIME_FOLLOWERS_POST_RESOLVE_STREAK.pop(fk_session, None)
                        _eng_log(
                            "followers_engine_session_terminated_safe_stop",
                            "warning",
                            "partial_safe_stopped",
                            {
                                "post_follow_failure_reason": _pf_fail,
                                "iterations": processed,
                                "visual_candidate_id": pick.get("visual_candidate_id"),
                            },
                        )
                        _emit_performance_summary(
                            t0=t0,
                            warm_session_used=warm_session_used,
                            force_stop_used=True,
                            exit_code=0,
                            target_username=source_profile_username,
                        )
                        return 97
                    else:
                        _ct_return_followers_list_or_canonical_reset(
                            d,
                            source_profile_username=source_profile_username,
                            pkg=pkg,
                            account_id=account_id or None,
                            reason="post_follow_return_after_verified_follow",
                        )
                    fk_session = _norm_ig_handle(str(follower_un or ""))
                    if fk_session:
                        _RUNTIME_SEEN_FOLLOWER_USERNAMES.add(fk_session)
                        _RUNTIME_FOLLOWERS_POST_RESOLVE_STREAK.pop(fk_session, None)
                    processed += 1
                    continue
                log(
                    "error",
                    "visual_candidate_profile_flow_failed",
                    failure_reason="visual_candidate_return_ct_failed",
                    how=how,
                    post_follow_failure_reason=_pf_fail,
                    source_profile_username=source_profile_username,
                    visual_candidate_id=pick.get("visual_candidate_id"),
                )
                return 42
        else:
            ok_back, how = return_to_followers_list(d, source_profile_username, pkg)
            if not ok_back:
                _eng_log(
                    "followers_list_engine_return_failed",
                    "failed",
                    "return_after_follow_failed",
                    {"how": how},
                )
                return 42
        if how == "reopen_from_source_profile":
            _eng_log(
                "followers_list_reopen_fallback",
                "warning",
                "reopened_from_source_profile",
                {"source_profile_username": source_profile_username},
            )
        _eng_log("followers_list_recovered", "success", "return_after_follower", {"method": how})
        if pick.get("visual_candidate_id"):
            log(
                "info",
                "visual_candidate_profile_return_ct_success",
                source_profile_username=source_profile_username,
                visual_candidate_id=pick.get("visual_candidate_id"),
                follower_username=follower_un,
                return_method=str(how or ""),
            )
            if str(how or "") in (
                "compact_foreign_profile_back_visual_followers_list_confirmed",
                "compact_foreign_profile_fallback_then_list",
            ):
                followers_session_mark_list_committed_open(
                    source_profile_username,
                    committed_source="post_follow_compact_visual_return",
                )
                log(
                    "info",
                    "followers_session_rearmed_after_post_follow_visual_return",
                    source_profile_username=source_profile_username,
                    visual_candidate_id=pick.get("visual_candidate_id"),
                    follower_username=follower_un,
                    return_method=str(how or ""),
                    committed_source="post_follow_compact_visual_return",
                )
                _promo_shot = str(_pf.get("return_list_screenshot_path") or "").strip()
                _promo_vf = (
                    _pf.get("return_visual_fallback_detail")
                    if isinstance(_pf.get("return_visual_fallback_detail"), dict)
                    else None
                )
                _vf_promoted = False
                if _promo_shot and os.path.isfile(_promo_shot):
                    note_followers_injection_capture(
                        open_list_meta,
                        screenshot_path=_promo_shot,
                        capture_reason="post_return_promoted",
                        source_profile_username=source_profile_username,
                        scroll_used=0,
                        visual_loop_state=visual_loop_state,
                    )
                    if _promo_vf:
                        session_vf_detail_for_loop = dict(_promo_vf)
                        visual_loop_state["session_vf_detail"] = session_vf_detail_for_loop
                        _vf_promoted = True
                    try:
                        log(
                            "info",
                            "followers_post_return_visual_evidence_promoted_for_next_loop",
                            source_profile_username=source_profile_username,
                            visual_candidate_id=pick.get("visual_candidate_id"),
                            follower_username=follower_un,
                            return_method=str(how or ""),
                            screenshot_path=_promo_shot,
                            promoted_to="latest_followers_injection_screenshot_path",
                            session_vf_detail_promoted=bool(_vf_promoted),
                        )
                    except Exception:
                        pass
                visual_loop_state["post_return_picker_refresh_pending"] = True
                visual_loop_state["post_return_picker_refresh_meta"] = {
                    "visual_candidate_id": str(pick.get("visual_candidate_id") or ""),
                    "follower_username": str(follower_un or ""),
                    "return_method": str(how or ""),
                }
                try:
                    log(
                        "info",
                        "followers_post_return_picker_refresh_armed",
                        source_profile_username=source_profile_username,
                        visual_candidate_id=str(pick.get("visual_candidate_id") or ""),
                        follower_username=str(follower_un or ""),
                        return_method=str(how or ""),
                    )
                except Exception:
                    pass

        fk_session = _norm_ig_handle(str(follower_un or ""))
        if fk_session:
            _RUNTIME_SEEN_FOLLOWER_USERNAMES.add(fk_session)
            _RUNTIME_FOLLOWERS_POST_RESOLVE_STREAK.pop(fk_session, None)

        processed += 1

    stop_final = get_followers_engine_stop_reason()
    if stop_final in FOLLOWERS_ENGINE_XML_STALE_EXIT_REASONS:
        det_final = detect_followers_list_screen(
            d, source_profile_username=source_profile_username
        )
        pic_final = _followers_xml_stale_engine_stop(
            stop_reason=stop_final,
            det=det_final,
            loop_iteration=followers_engine_loop_iteration,
        )
        if pic_final == VISUAL_FOLLOW_HISTORY_CONTINUE:
            log(
                "info",
                "visual_follow_history_continue_after_followers_loop_end",
                source_profile_username=source_profile_username,
                stop_reason=stop_final,
            )
            log(
                "info",
                "visual_followers_loop_continued_after_reentry",
                source_profile_username=source_profile_username,
                phase="followers_loop_end_stale",
                stop_reason=stop_final,
            )
            return pic_final
        if pic_final is not None:
            return pic_final

    _followers_clear_pending_visual_candidate_skip()
    _followers_clear_pending_already_connected_visual_skip(
        clear_reason="followers_engine_loop_end",
    )
    _followers_clear_ac_visual_blocks_after_scroll(
        clear_reason="followers_engine_loop_end",
    )
    _prog_max_end = int(followers_progressive_max_passes())
    _expl_used_end = int(visual_loop_state.get("list_progressive_exploration_passes_used") or 0)
    _expl_exhausted_end = bool(visual_loop_state.get("list_progressive_exploration_exhausted"))
    if follows_completed_count > 0:
        _followers_sess_outcome = "follows_completed"
    elif _expl_exhausted_end:
        _followers_sess_outcome = "no_followable_candidates_bounded_exploration"
    else:
        _followers_sess_outcome = "no_follows_attempted"

    _emit_performance_summary(
        t0=t0,
        warm_session_used=warm_session_used,
        force_stop_used=force_stop_used,
        exit_code=0,
        target_username=source_profile_username,
        follows_completed_count=follows_completed_count,
        list_progressive_exploration_exhausted=_expl_exhausted_end,
        exploration_passes_used=_expl_used_end,
        exploration_max_passes=_prog_max_end,
        followers_session_outcome=_followers_sess_outcome,
    )
    log(
        "info",
        "followers_engine_session_complete",
        source_profile_username=source_profile_username,
        iterations=processed,
        total_ms=round((time.perf_counter() - t0) * 1000, 2),
        follows_completed_count=follows_completed_count,
        list_progressive_exploration_exhausted=_expl_exhausted_end,
        exploration_passes_used=_expl_used_end,
        exploration_max_passes=_prog_max_end,
        session_outcome=_followers_sess_outcome,
    )
    _eng_log(
        "followers_engine_session_complete",
        "success",
        "complete",
        {
            "iterations": processed,
            "total_ms": round((time.perf_counter() - t0) * 1000, 2),
            "follows_completed_count": follows_completed_count,
            "list_progressive_exploration_exhausted": _expl_exhausted_end,
            "exploration_passes_used": _expl_used_end,
            "exploration_max_passes": _prog_max_end,
            "session_outcome": _followers_sess_outcome,
        },
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Instagram safe navigation worker")
    parser.add_argument(
        "--multi",
        type=str,
        default="",
        help="Comma-separated usernames (warm session, reuse search when possible)",
    )
    parser.add_argument("--account-id", type=str, default="", help="Supabase ig_accounts.id")
    parser.add_argument("--username", type=str, default="", help="Supabase ig_accounts.username")
    parser.add_argument("--limit", type=int, default=25, help="Supabase pending target fetch limit")
    args = parser.parse_args()
    supabase_mode = _is_supabase_mode(args)

    account_id = ""
    run_id = ""
    db_targets: list[dict] = []
    if supabase_mode:
        account = _safe_supabase_call(
            "load_account",
            account_id=(args.account_id or "").strip() or None,
            username=(args.username or "").strip() or None,
        )
        if not account:
            log("error", "run_aborted", reason="supabase_account_not_found")
            return 10
        account_id = str(account.get("id") or "").strip()
        if not account_id:
            log("error", "run_aborted", reason="supabase_account_missing_id")
            return 10
        db_targets = _safe_supabase_call(
            "load_pending_targets",
            account_id=account_id,
            limit=max(1, int(args.limit)),
        ) or []
        targets = [str(t.get("target_username") or "").strip() for t in db_targets if str(t.get("target_username") or "").strip()]
        followers_engine_has_explicit_source = bool(
            bool(getattr(config, "ENABLE_FOLLOWERS_LIST_ENGINE", False))
            and (getattr(config, "FOLLOWERS_SOURCE_USERNAME", "") or "").strip()
        )
        if not targets:
            if followers_engine_has_explicit_source:
                log(
                    "info",
                    "run_followers_engine_no_pending_targets_ok",
                    account_id=account_id,
                    followers_source_username=(getattr(config, "FOLLOWERS_SOURCE_USERNAME", "") or "").strip(),
                )
            else:
                log("info", "run_no_pending_targets", account_id=account_id)
                return 0
        run = _safe_supabase_call("create_run", account_id=account_id) or {}
        run_id = str(run.get("id") or "").strip()
        supabase_client.set_log_context(account_id, run_id)
        supabase_client.log_performance_event(
            action_type="performance_test",
            status="debug",
            target_username=None,
            payload={"ping": "ok"},
        )
    else:
        supabase_client.set_log_context(None, None)
        targets = _parse_targets(args)
        if not targets:
            log("error", "run_aborted", reason="no_targets_after_parse")
            return 1

    def _on_wait_event(wait_reason: str, wait_duration_ms: float) -> None:
        if wait_duration_ms <= 1000.0:
            return
        supabase_client.log_performance_event(
            action_type="long_wait_detected",
            status="warning",
            target_username=None,
            payload={
                "wait_reason": wait_reason,
                "wait_duration_ms": round(wait_duration_ms, 2),
            },
        )

    set_wait_event_callback(_on_wait_event)

    file_run_id = (run_id or "").strip() or str(uuid.uuid4())
    log_file_path = init_run_file_logging(file_run_id)
    if log_file_path:
        log(
            "info",
            "run_log_file_created",
            run_id=file_run_id,
            log_file_path=log_file_path,
        )
    else:
        log(
            "warning",
            "run_log_file_init_failed",
            run_id=file_run_id,
            message="could_not_open_runs_log_file",
        )

    log(
        "info",
        "run_started",
        targets=targets,
        target_count=len(targets),
        package=config.INSTAGRAM_PACKAGE,
        multi_mode=len(targets) > 1,
        supabase_mode=supabase_mode,
        account_id=account_id or None,
        run_id=run_id or None,
    )
    reset_dm_send_run_state()
    global _RUNTIME_REAL_DM_SENT_COUNT, _RUNTIME_FOLLOW_COUNT, _RUNTIME_FOLLOWED_USERNAMES
    global _RUNTIME_SEEN_FOLLOWER_USERNAMES, _RUNTIME_INTERACTED_USERNAMES, _RUNTIME_UNFOLLOWED_USERNAMES
    global _RUNTIME_SKIPPED_USERNAMES, _SESSION_SOCIAL_ID
    _RUNTIME_REAL_DM_SENT_COUNT = 0
    _RUNTIME_FOLLOW_COUNT = 0
    _RUNTIME_FOLLOWED_USERNAMES = set()
    _RUNTIME_SEEN_FOLLOWER_USERNAMES = set()
    _RUNTIME_INTERACTED_USERNAMES = set()
    _RUNTIME_UNFOLLOWED_USERNAMES = set()
    _RUNTIME_SKIPPED_USERNAMES = set()
    _SESSION_SOCIAL_ID = str(uuid.uuid4())
    _reset_session_counters()
    t_session = time.perf_counter()
    t = t_session
    warm_session_used = False
    force_stop_used = False

    d = connect_device(config.DEVICE_SERIAL)
    t = _phase("connect_device", t)

    disable_android_animations(config.DEVICE_SERIAL)
    t = _phase("disable_android_animations", t)

    if not health_check(d):
        log("error", "run_aborted", reason="health_check_failed")
        reset_perf_counters()
        _emit_performance_summary(
            t0=t_session,
            warm_session_used=warm_session_used,
            force_stop_used=force_stop_used,
            exit_code=2,
            target_username=targets[0] if targets else config.TARGET_USERNAME,
        )
        if supabase_mode and run_id:
            _update_run_status_safe(
                run_id=run_id,
                status="failed",
                totals={"total": len(targets), "success": 0, "failed": len(targets)},
                performance_summary={"reason": "health_check_failed"},
            )
        return _return_with_cleanup(d, 2)
    t = _phase("health_check", t)

    _lock_check: dict = {}
    if bool(getattr(config, "LOCK_INSTAGRAM_AUTO_UPDATE", False)):
        try:
            lock_instagram_update_system(d, getattr(config, "INSTAGRAM_PACKAGE", None))
        except Exception as e:
            log("warning", "instagram_auto_update_lock_runner_wrap_failed", error=str(e))
        t = _phase("instagram_auto_update_lock", t)
    if bool(getattr(config, "LOCK_INSTAGRAM_AUTO_UPDATE", False)) or bool(
        getattr(config, "ENABLE_STRICT_UPDATE_LOCK", False)
    ):
        try:
            _lock_check = check_instagram_version_lock(d)
        except Exception as e:
            log("warning", "instagram_version_lock_check_failed", error=str(e))
            _lock_check = {"unsafe_for_automation": False, "error": str(e)}
        t = _phase("instagram_version_lock_check", t)
        if bool(getattr(config, "ENABLE_STRICT_UPDATE_LOCK", False)) and bool(
            _lock_check.get("unsafe_for_automation")
        ):
            log(
                "critical",
                "instagram_strict_lock_run_aborted",
                automation_unsafe_reason=_lock_check.get("automation_unsafe_reason"),
                instagram_suspended=_lock_check.get("instagram_suspended"),
                unsafe_for_automation=_lock_check.get("unsafe_for_automation"),
            )
            log("error", "run_aborted", reason="instagram_strict_update_lock_unsafe")
            reset_perf_counters()
            _emit_performance_summary(
                t0=t_session,
                warm_session_used=warm_session_used,
                force_stop_used=force_stop_used,
                exit_code=43,
                target_username=targets[0] if targets else config.TARGET_USERNAME,
            )
            if supabase_mode and run_id:
                _update_run_status_safe(
                    run_id=run_id,
                    status="failed",
                    totals={"total": len(targets), "success": 0, "failed": len(targets)},
                    performance_summary={
                        "reason": "instagram_strict_update_lock_unsafe",
                        "lock_check": _lock_check,
                    },
                )
            return _return_with_cleanup(d, 43)

    t_warm_decision = time.perf_counter()
    warm_ok, warm_reason = instagram_warm_session_eligible(d, config.INSTAGRAM_PACKAGE)
    warm_session_decision_ms = (time.perf_counter() - t_warm_decision) * 1000
    log("info", "warm_session_decision_ms", value=round(warm_session_decision_ms, 2))
    warm_session_used = warm_ok
    if warm_ok:
        log("info", "instagram_warm_session_reused", reason=warm_reason)
        if float(config.WARM_SESSION_MICRO_WAIT_S) > 0.2:
            ws = time.perf_counter()
            log("info", "wait_started", wait_reason="warm_session_micro_wait")
            time.sleep(config.WARM_SESSION_MICRO_WAIT_S)
            log(
                "info",
                "wait_finished",
                wait_reason="warm_session_micro_wait",
                wait_duration_ms=round((time.perf_counter() - ws) * 1000, 2),
            )
        else:
            time.sleep(config.WARM_SESSION_MICRO_WAIT_S)
        t = _phase("warm_session_skip_force_stop", t)
    else:
        log("warning", "warm_session_rejected", reason=warm_reason)
        if warm_reason == "android_permission_dialog":
            log("warning", "unsupported_start_surface_detected", reason=warm_reason)
            denied = dismiss_android_permission_dialog(d)
            log(
                "info",
                "permission_recovery_restart",
                reason=warm_reason,
                denied=denied,
                stage="run_start",
            )
        invalidate_search_surface_cache(warm_reason or "cold_start_or_unhealthy")
        force_stop_used = True
        force_stop(d, config.INSTAGRAM_PACKAGE)
        t = _phase("force_stop", t)

        app_start(d, config.INSTAGRAM_PACKAGE)
        ws = time.perf_counter()
        log("info", "wait_started", wait_reason="app_start_wait")
        time.sleep(config.APP_START_WAIT_S)
        log(
            "info",
            "wait_finished",
            wait_reason="app_start_wait",
            wait_duration_ms=round((time.perf_counter() - ws) * 1000, 2),
        )
        t = _phase("app_start", t)
    startup_wait_ms = (time.perf_counter() - t_session) * 1000
    log("info", "startup_wait_ms", value=round(startup_wait_ms, 2))

    if not verify_app_foreground(d, config.INSTAGRAM_PACKAGE):
        if not warm_ok and warm_reason == "android_permission_dialog":
            log(
                "error",
                "permission_recovery_failed",
                reason="instagram_not_foreground_after_permission_restart",
            )
        log("error", "run_aborted", reason="instagram_not_foreground")
        reset_perf_counters()
        _emit_performance_summary(
            t0=t_session,
            warm_session_used=warm_session_used,
            force_stop_used=force_stop_used,
            exit_code=3,
            target_username=targets[0] if targets else config.TARGET_USERNAME,
        )
        if supabase_mode and run_id:
            _update_run_status_safe(
                run_id=run_id,
                status="failed",
                totals={"total": len(targets), "success": 0, "failed": len(targets)},
                performance_summary={"reason": "instagram_not_foreground"},
            )
        return _return_with_cleanup(d, 3)
    t = _phase("verify_app_running", t)

    if bool(getattr(config, "ENABLE_FOLLOWERS_LIST_ENGINE", False)):
        source_profile_username = (getattr(config, "FOLLOWERS_SOURCE_USERNAME", "") or "").strip()
        if not source_profile_username:
            source_profile_username = (targets[0] if targets else "").strip()
        if not source_profile_username:
            log("error", "run_aborted", reason="followers_engine_missing_source_profile")
            return _return_with_cleanup(d, 1)
        try:
            _cur_pkg = d.app_current()
            _fg_startup = str((_cur_pkg or {}).get("package") or "")
        except Exception:
            _fg_startup = ""
        log(
            "info",
            "followers_engine_runtime_config",
            followers_engine_enabled=True,
            followers_source_username=source_profile_username,
            follow_private_accounts=bool(getattr(config, "FOLLOW_PRIVATE_ACCOUNTS", False)),
            visual_followers_open_dry_run=bool(
                getattr(config, "VISUAL_FOLLOWERS_OPEN_DRY_RUN", True)
            ),
            visual_follow_mute_dry_run=bool(
                getattr(config, "VISUAL_FOLLOW_MUTE_DRY_RUN", True)
            ),
            instagram_foreground_package=_fg_startup,
            instagram_expected_package=str(getattr(config, "INSTAGRAM_PACKAGE", "") or ""),
            validation_reminder=(
                "ensure_source_followers_list_has_private_account_not_yet_followed_for_follow_request_pending"
            ),
        )
        eng_code = _run_followers_list_engine_session(
            d,
            source_profile_username=source_profile_username,
            account_id=account_id,
            run_id=run_id,
            supabase_mode=supabase_mode,
            warm_session_used=warm_session_used,
            force_stop_used=force_stop_used,
        )
        if supabase_mode and run_id:
            if eng_code == 97:
                _update_run_status_safe(
                    run_id=run_id,
                    status="completed",
                    totals={"total": 1, "success": 1, "failed": 0},
                    performance_summary={
                        "followers_list_engine": True,
                        "exit_code": 97,
                        "outcome": "partial_safe_stopped",
                        "source_profile_username": source_profile_username,
                    },
                )
            elif eng_code == 96:
                _update_run_status_safe(
                    run_id=run_id,
                    status="failed",
                    totals={"total": 1, "success": 0, "failed": 1},
                    performance_summary={
                        "followers_list_engine": True,
                        "exit_code": 96,
                        "outcome": "wrong_surface_abort",
                        "source_profile_username": source_profile_username,
                    },
                )
            elif eng_code == 98:
                _update_run_status_safe(
                    run_id=run_id,
                    status="completed",
                    totals={"total": 1, "success": 0, "failed": 0},
                    performance_summary={
                        "followers_list_engine": True,
                        "exit_code": 98,
                        "outcome": "already_connected_safe_skipped",
                        "source_profile_username": source_profile_username,
                    },
                )
            else:
                _update_run_status_safe(
                    run_id=run_id,
                    status="completed" if eng_code == 0 else "failed",
                    totals={
                        "total": 1,
                        "success": 1 if eng_code == 0 else 0,
                        "failed": 0 if eng_code == 0 else 1,
                    },
                    performance_summary={
                        "followers_list_engine": True,
                        "exit_code": eng_code,
                        "source_profile_username": source_profile_username,
                    },
                )
        return _return_with_cleanup(d, 0 if eng_code in (97, 98) else eng_code)

    prev_username: str | None = None
    prev_target_end_ts: float | None = None
    successes = 0
    failures = 0
    send_blocked_tested_count = 0
    send_blocked_existing_thread_count = 0
    send_blocked_config_disabled_count = 0
    run_target_summaries: list[dict] = []

    def _build_run_perf_summary() -> dict:
        total_count = len(run_target_summaries)
        slowest_username = ""
        slowest_ms = 0.0
        for item in run_target_summaries:
            compact = item.get("compact") or {}
            ms = float(compact.get("total_ms", 0.0))
            if ms >= slowest_ms:
                slowest_ms = ms
                slowest_username = str(item.get("username") or "")
        avg_target_ms = (sum(float((i.get("compact") or {}).get("total_ms", 0.0)) for i in run_target_summaries) / total_count) if total_count else 0.0
        return {
            "targets": run_target_summaries,
            "total_session_ms": round((time.perf_counter() - t_session) * 1000, 2),
            "target_count": total_count,
            "success_count": int(successes),
            "failed_count": int(failures),
            "avg_target_ms": round(avg_target_ms, 2),
            "slowest_target_username": slowest_username or None,
            "slowest_target_ms": round(slowest_ms, 2),
            "warm_session_used": warm_session_used,
            "force_stop_used": force_stop_used,
            "fast_path_mode": bool(getattr(config, "FAST_PATH_MODE", False)),
            "send_blocked_tested_count": int(send_blocked_tested_count),
            "send_blocked_existing_thread_count": int(send_blocked_existing_thread_count),
            "send_blocked_config_disabled_count": int(send_blocked_config_disabled_count),
        }
    for idx, username in enumerate(targets):
        iter_start = time.perf_counter()
        reset_perf_counters()
        reset_dm_thread_probe_state()
        reset_dm_send_run_state()
        log(
            "info",
            "multi_target_iteration",
            index=idx,
            username=username,
            total=len(targets),
        )
        db_target = db_targets[idx] if supabase_mode and idx < len(db_targets) else None
        target_id = str((db_target or {}).get("id") or "").strip()
        target_row_raw: dict | None = None
        if db_target and isinstance(db_target.get("raw"), dict):
            target_row_raw = db_target["raw"]
        if supabase_mode and run_id:
            _safe_supabase_call(
                "insert_action_log",
                run_id=run_id,
                account_id=account_id,
                target_username=username,
                action_type="profile_open_test",
                status="started",
                message="Starting safe profile open check",
                payload={"target_index": idx},
            )
        code = _run_one_target(
            d,
            username,
            target_index=idx,
            warm_session_used=warm_session_used,
            force_stop_used=force_stop_used,
            previous_username=prev_username,
            use_fast_reset_between_targets=bool(getattr(config, "FAST_RESET_BETWEEN_TARGETS", False)),
            first_action_delay_ms=((iter_start - t_session) * 1000 if idx == 0 else 0.0),
            next_username_ready_ms=(
                (iter_start - prev_target_end_ts) * 1000 if prev_target_end_ts is not None else 0.0
            ),
            target_row_raw=target_row_raw,
            account_id=account_id,
            run_id=run_id,
            supabase_mode=supabase_mode,
        )
        dm_attempted = get_last_dm_thread_attempted()
        dm_state = get_last_dm_thread_state() if dm_attempted else "unknown"
        target_perf = get_perf_snapshot()
        iter_elapsed_ms = (time.perf_counter() - iter_start) * 1000
        navigation_strategy = (
            "reset_between_targets"
            if bool(getattr(config, "FAST_RESET_BETWEEN_TARGETS", False))
            else "profile_back_to_search"
        )
        compact = _build_target_perf_compact_payload(
            username=username,
            target_perf=target_perf,
            exit_code=code,
            total_ms=iter_elapsed_ms,
            navigation_strategy=navigation_strategy,
        )
        supabase_client.log_performance_event(
            action_type="target_perf_compact",
            status="success" if code in (0, 21, 31, 32) else "failed",
            target_username=username,
            payload={k: v for k, v in compact.items() if k != "username"},
        )
        run_target_summaries.append(
            {
                "username": username,
                "exit_code": code,
                "reason": _exit_reason_from_code(code),
                "dm_thread_state": dm_state,
                "dm_attempted": dm_attempted,
                "compact": compact,
                "performance": target_perf,
            }
        )
        if supabase_mode and run_id and dm_attempted:
            _insert_dm_log_safe(
                run_id=run_id,
                account_id=account_id,
                username=username,
                dm_state=dm_state,
                exit_code=code,
                warm_session_used=warm_session_used,
                target_perf=target_perf,
            )
        if code not in (0, 21, 31, 32):
            failures += 1
            if supabase_mode and run_id:
                _safe_supabase_call(
                    "insert_action_log",
                    run_id=run_id,
                    account_id=account_id,
                    target_username=username,
                    action_type="profile_open_test",
                    status="failed",
                    message="Safe profile open check failed",
                    payload={"exit_code": code, "reason": _exit_reason_from_code(code), "performance": target_perf},
                )
            more_targets = idx < len(targets) - 1
            recoverable_here = (
                more_targets
                and _recoverable_target_exit(code)
                and bool(getattr(config, "FAST_RESET_BETWEEN_TARGETS", False))
            )
            if recoverable_here and code == 11 and supabase_mode and target_id:
                _update_target_status_safe(
                    target_id=target_id,
                    status="send_blocked_tested",
                    last_error="dm_thread_unknown",
                )
                log(
                    "info",
                    "target_marked_send_blocked_tested",
                    target_id=target_id,
                    username=username,
                    reason="dm_thread_unknown_recoverable_continue",
                )
                send_blocked_tested_count += 1
            elif supabase_mode and target_id:
                _update_target_status_safe(
                    target_id=target_id,
                    status="failed",
                    last_error=_exit_reason_from_code(code),
                )
            if recoverable_here:
                log(
                    "info",
                    "reset_after_partial_failure",
                    failed_username=username,
                    exit_code=code,
                    reason=_exit_reason_from_code(code),
                )
                if supabase_mode and run_id:
                    supabase_client.log_performance_event(
                        action_type="reset_after_partial_failure",
                        status="warning",
                        target_username=username,
                        payload={
                            "exit_code": code,
                            "reason": _exit_reason_from_code(code),
                            "next_index": idx + 1,
                        },
                    )
                if not reset_to_search_for_next_target(d, config.INSTAGRAM_PACKAGE):
                    log("error", "run_aborted", reason="reset_after_partial_failure_failed", username=username)
                    if supabase_mode and run_id:
                        perf_summary = _build_run_perf_summary()
                        perf_summary["reason"] = "reset_after_partial_failure_failed"
                        _update_run_status_safe(
                            run_id=run_id,
                            status="failed",
                            totals={"total": len(targets), "success": successes, "failed": failures},
                            performance_summary=perf_summary,
                        )
                    return _return_with_cleanup(d, 13)
                prev_username = None
                prev_target_end_ts = time.perf_counter()
                continue

            if supabase_mode and run_id:
                perf_summary = _build_run_perf_summary()
                perf_summary["reason"] = _exit_reason_from_code(code)
                _update_run_status_safe(
                    run_id=run_id,
                    status="failed",
                    totals={"total": len(targets), "success": successes, "failed": failures},
                    performance_summary=perf_summary,
                )
            return _return_with_cleanup(d, code)
        successes += 1
        if supabase_mode and run_id:
            _safe_supabase_call(
                "insert_action_log",
                run_id=run_id,
                account_id=account_id,
                target_username=username,
                action_type="profile_open_test",
                status="success",
                message="Safe profile open check complete",
                payload={
                    "exit_code": code,
                    "performance": target_perf,
                    "navigation_partial": code in (21, 32),
                },
            )
        if supabase_mode and target_id:
            send_res = get_last_dm_send_result()
            if send_res.get("sent"):
                # Business row already updated in _run_one_target (dm_sent + finalize).
                log(
                    "info",
                    "target_status_updated",
                    target_id=target_id,
                    status="completed"
                    if code != 21
                    else "sent_navigation_partial",
                    last_error=None if code != 21 else "sent_navigation_partial",
                    note="dm_sent_business_persisted_in_target_run",
                )
            elif (
                send_res.get("precheck_ok")
                and send_res.get("blocked_event") == "dm_send_blocked_config_disabled"
                and bool(getattr(config, "DM_DRAFT_TYPING_ENABLED", True))
                and bool(getattr(config, "DM_CLEAR_DRAFT_AFTER_TEST", True))
            ):
                _update_target_status_safe(
                    target_id=target_id,
                    status="send_blocked_tested",
                    last_error=None,
                )
                log(
                    "info",
                    "target_marked_send_blocked_tested",
                    target_id=target_id,
                    username=username,
                    reason="send_dm_blocked_config_disabled",
                )
                send_blocked_tested_count += 1
                send_blocked_config_disabled_count += 1
            elif (
                send_res.get("blocked_event") == "dm_send_blocked_existing_thread"
                and bool(getattr(config, "DM_DRAFT_TYPING_ENABLED", True))
                and bool(getattr(config, "DM_CLEAR_DRAFT_AFTER_TEST", True))
            ):
                _update_target_status_safe(
                    target_id=target_id,
                    status="send_blocked_tested",
                    last_error=None,
                )
                log(
                    "info",
                    "target_marked_send_blocked_tested",
                    target_id=target_id,
                    username=username,
                    reason="send_dm_blocked_existing_thread",
                )
                send_blocked_tested_count += 1
                send_blocked_existing_thread_count += 1
            else:
                _update_target_status_safe(
                    target_id=target_id,
                    status="success",
                    last_error=None,
                )
        prev_username = username
        prev_target_end_ts = time.perf_counter()

    log(
        "info",
        "run_all_targets_complete",
        count=len(targets),
        total_session_ms=round((time.perf_counter() - t_session) * 1000, 2),
        message="Instagram left warm on device; no home press.",
    )
    if supabase_mode and run_id:
        perf_summary = _build_run_perf_summary()
        perf_summary["had_failures"] = failures > 0
        perf_summary["session_counters"] = dict(_SESSION_COUNTERS)
        strict_code = _session_strict_completion_exit_code()
        if strict_code is not None and failures == 0:
            perf_summary["strict_session_exit_code"] = strict_code
            _update_run_status_safe(
                run_id=run_id,
                status="failed",
                totals={"total": len(targets), "success": successes, "failed": failures},
                performance_summary=perf_summary,
            )
            return _return_with_cleanup(d, strict_code)
        _update_run_status_safe(
            run_id=run_id,
            status="completed",
            totals={"total": len(targets), "success": successes, "failed": failures},
            performance_summary=perf_summary,
        )
    else:
        strict_code = _session_strict_completion_exit_code()
        if strict_code is not None and failures == 0:
            return _return_with_cleanup(d, strict_code)
    return _return_with_cleanup(d, 1 if failures > 0 else 0)


if __name__ == "__main__":
    raise SystemExit(main())
