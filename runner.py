"""Orchestrate one safe Instagram navigation run (PoC)."""

from __future__ import annotations

import argparse
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timezone

import social_memory

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
    open_followers_list_from_profile,
    detect_followers_list_screen,
    FOLLOWERS_ENGINE_XML_STALE_EXIT_REASONS,
    get_followers_engine_stop_reason,
    iter_followers_candidates,
    open_follower_profile_from_list,
    return_to_followers_list,
    scroll_followers_list_forward,
    visual_extract_followers_candidates_from_screenshot,
    open_visual_follower_candidate_from_screenshot,
    visual_like_open_post,
    visual_open_recent_post_from_profile,
    visual_return_to_profile_from_post,
    visual_detect_follow_button_on_profile,
    visual_detect_follow_post_actions,
    visual_follow_profile_dry_run,
    visual_mute_after_follow_dry_run,
)
from logs import log

# Real DM sends per worker process (pairs with SEND_DM_MAX_PER_RUN).
_RUNTIME_REAL_DM_SENT_COUNT: int = 0
# Successful follows this process (pairs with FOLLOW_MAX_PER_RUN).
_RUNTIME_FOLLOW_COUNT: int = 0
_RUNTIME_FOLLOWED_USERNAMES: set[str] = set()
# Follower rows already selected in followers-list engine (same run).
_RUNTIME_SEEN_FOLLOWER_USERNAMES: set[str] = set()
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
) -> None:
    total_ms = (time.perf_counter() - t0) * 1000
    snap = get_perf_snapshot()
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
        37: "follow_blocked_social_memory",
        51: "session_strict_follow_quota_incomplete",
        52: "session_strict_interactions_quota_incomplete",
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
        return 51
    til = int(getattr(config, "SESSION_TOTAL_INTERACTIONS_LIMIT", 0) or 0)
    if til > 0 and _SESSION_COUNTERS["interactions"] < til:
        log(
            "warning",
            "session_strict_incomplete",
            reason="interactions_quota_unmet",
            interactions=_SESSION_COUNTERS["interactions"],
            required=til,
        )
        return 52
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
        exit_code = 9 if reason == "search_field_not_cleared" else 5
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


def _open_meta_visual_fallback_screenshot_path(meta: dict) -> str | None:
    """Path to the screenshot used for visual_fallback open detection, if present."""
    for key in ("last_poll_snapshot", "after_tap_screen_snapshot"):
        snap = meta.get(key) or {}
        vf = snap.get("visual_fallback_detail") or {}
        p = vf.get("screenshot_path_used") or vf.get("screenshot_path")
        if p:
            return str(p)
    return None


def _open_meta_visual_fallback_list_was_open(meta: dict) -> bool:
    """True when open success payload shows followers list validated via visual_fallback."""
    if str(meta.get("open_detection_method") or "") != "visual_fallback":
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
    Returns None if picker disabled, not dry-run, not visual_fallback open proof, or no screenshot.
    """
    global _VISUAL_FOLLOWERS_OPEN_COUNT_THIS_SESSION
    if not bool(getattr(config, "ENABLE_VISUAL_FOLLOWERS_CANDIDATE_PICKER", False)):
        return None
    if not bool(getattr(config, "VISUAL_FOLLOWERS_PICKER_DRY_RUN", True)):
        return None
    if str(open_list_meta.get("open_detection_method") or "") != "visual_fallback":
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

    vpick = visual_extract_followers_candidates_from_screenshot(
        d,
        screenshot_path=shot,
        source_profile_username=source_profile_username,
        runtime_seen=runtime_seen,
    )
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
        ranked = sorted(
            (c for c in cands if float(c.get("confidence") or 0) >= min_conf),
            key=lambda x: float(x.get("confidence") or 0),
            reverse=True,
        )
        if ranked:
            best = ranked[0]
            _VISUAL_FOLLOWERS_OPEN_COUNT_THIS_SESSION += 1
            log(
                "info",
                "visual_follower_candidate_selected_for_open",
                source_profile_username=source_profile_username,
                visual_candidate_id=best.get("visual_candidate_id"),
                row_index=best.get("row_index"),
                confidence=best.get("confidence"),
                selection="highest_confidence",
            )
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
            if open_out.get("profile_detected") and bool(
                getattr(config, "ENABLE_VISUAL_POST_LIKE_FLOW", False)
            ):
                post_out = visual_open_recent_post_from_profile(
                    d, source_profile_username=source_profile_username
                )
                like_out: dict = {"ok": False, "skipped": True}
                back_out: dict = {"ok": True, "skipped": True}
                if not post_out.get("ok"):
                    pass
                elif post_out.get("post_detected"):
                    like_out = visual_like_open_post(
                        d, source_profile_username=source_profile_username
                    )
                    back_out = visual_return_to_profile_from_post(
                        d, source_profile_username=source_profile_username
                    )
                else:
                    like_out = {
                        "ok": False,
                        "skipped": True,
                        "failure_reason": "post_viewer_not_detected",
                    }
                log(
                    "info",
                    "visual_post_like_flow_runner_complete",
                    source_profile_username=source_profile_username,
                    post_open_ok=post_out.get("ok"),
                    post_detected=post_out.get("post_detected"),
                    like_dry_run_ok=like_out.get("ok"),
                    return_profile_ok=back_out.get("ok"),
                )
                eng_log(
                    "visual_post_like_flow_dry_run",
                    "success"
                    if bool(
                        post_out.get("ok")
                        and post_out.get("post_detected")
                        and like_out.get("ok")
                        and back_out.get("ok")
                    )
                    else "info",
                    "visual_post_like_flow_dry_run_complete",
                    {
                        "post_detected": post_out.get("post_detected"),
                        "like_dry_run": True,
                        "profile_after_back": back_out.get("profile_detected"),
                    },
                )
                exit_code = 47
                if bool(getattr(config, "ENABLE_VISUAL_FOLLOW_MUTE_FLOW", False)):
                    det_fb = visual_detect_follow_button_on_profile(
                        d, source_profile_username=source_profile_username
                    )
                    fr_out = visual_follow_profile_dry_run(
                        d,
                        source_profile_username=source_profile_username,
                        follow_detection=det_fb,
                    )
                    act_out = visual_detect_follow_post_actions(
                        d, source_profile_username=source_profile_username
                    )
                    mute_out = visual_mute_after_follow_dry_run(
                        d,
                        source_profile_username=source_profile_username,
                        actions_out=act_out,
                    )
                    log(
                        "info",
                        "visual_follow_mute_flow_runner_complete",
                        source_profile_username=source_profile_username,
                        follow_detect_ok=det_fb.get("ok"),
                        follow_dry_run_ok=fr_out.get("ok"),
                        follow_actions_popup=act_out.get("popup_detected"),
                        mute_dry_run_ok=mute_out.get("ok"),
                    )
                    eng_log(
                        "visual_follow_mute_flow_dry_run",
                        "info",
                        "visual_follow_mute_flow_dry_run_complete",
                        {
                            "follow_detect_ok": det_fb.get("ok"),
                            "follow_dry_run": True,
                            "mute_dry_run": True,
                            "popup_detected": act_out.get("popup_detected"),
                        },
                    )
                    exit_code = 48
                _emit_performance_summary(
                    t0=t0,
                    warm_session_used=warm_session_used,
                    force_stop_used=force_stop_used,
                    exit_code=exit_code,
                    target_username=source_profile_username,
                )
                return exit_code
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
    if not _open_search_with_recovery(
        d, pkg=pkg, username=source_profile_username, context="followers_engine_start"
    ):
        _eng_log("followers_engine_aborted", "failed", "open_search_failed", {})
        return 4
    if not type_search(d, source_profile_username, previous_username=None):
        reason = get_type_search_failure_reason() or "type_search_failed"
        _eng_log("followers_engine_aborted", "failed", reason, {})
        return 9 if reason == "search_field_not_cleared" else 5

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
    max_scroll = int(getattr(config, "FOLLOWERS_LIST_SCROLL_MAX_PER_SESSION", 25))
    scroll_used = 0
    processed = 0
    open_detection_method = str(open_list_meta.get("open_detection_method") or "xml")
    followers_engine_loop_iteration = 0
    prev_candidate_row_count: int | None = None

    def _collect_follower_candidates() -> list:
        return iter_followers_candidates(
            d,
            source_profile_username=source_profile_username,
            runtime_seen=_RUNTIME_SEEN_FOLLOWER_USERNAMES,
        )

    def _followers_xml_stale_engine_stop(
        *,
        stop_reason: str | None,
        det: dict,
        loop_iteration: int,
        xml_candidates: list | None = None,
    ) -> int:
        pic = _try_visual_followers_picker_dry_run(
            d,
            open_list_meta=open_list_meta,
            source_profile_username=source_profile_username,
            source_account_context=account_id,
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
        stop_r_loop = get_followers_engine_stop_reason()

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

        det = detect_followers_list_screen(d, source_profile_username=source_profile_username)

        if (
            open_detection_method == "visual_fallback"
            and stop_r_loop in FOLLOWERS_ENGINE_XML_STALE_EXIT_REASONS
        ):
            return _followers_xml_stale_engine_stop(
                stop_reason=stop_r_loop,
                det=det,
                loop_iteration=followers_engine_loop_iteration,
            )

        if not det.get("is_followers_list"):
            if open_detection_method == "visual_fallback":
                return _followers_xml_stale_engine_stop(
                    stop_reason=stop_r_loop or "visual_fallback_xml_not_followers_list",
                    det=det,
                    loop_iteration=followers_engine_loop_iteration,
                )
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

        candidates = _collect_follower_candidates()
        prev_candidate_row_count = len(candidates)
        stop_r_after_candidates = get_followers_engine_stop_reason()
        if open_detection_method == "visual_fallback" and (
            stop_r_after_candidates in FOLLOWERS_ENGINE_XML_STALE_EXIT_REASONS
            or len(candidates) == 0
        ):
            stale_reason = stop_r_after_candidates
            if stale_reason not in FOLLOWERS_ENGINE_XML_STALE_EXIT_REASONS:
                stale_reason = "visual_fallback_no_xml_candidates"
            return _followers_xml_stale_engine_stop(
                stop_reason=stale_reason,
                det=det,
                loop_iteration=followers_engine_loop_iteration,
                xml_candidates=candidates,
            )

        pick = None
        for c in candidates:
            ckey = _norm_ig_handle(str(c.get("username") or ""))
            if ckey == src_key:
                log(
                    "info",
                    "followers_candidate_skipped_source_profile",
                    username=c.get("username"),
                    source_profile_username=source_profile_username,
                )
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
                continue
            pick = c
            break

        if pick is None:
            if scroll_used >= max_scroll:
                log(
                    "info",
                    "followers_engine_scroll_cap",
                    source_profile_username=source_profile_username,
                    scroll_used=scroll_used,
                )
                break
            elif not scroll_followers_list_forward(d):
                break
            else:
                scroll_used += 1
                _eng_log(
                    "followers_list_scroll",
                    "info",
                    "scroll",
                    {"scroll_index": scroll_used, "direction": "forward"},
                )
                continue

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

        fkey = _norm_ig_handle(str(pick.get("username") or ""))
        _RUNTIME_SEEN_FOLLOWER_USERNAMES.add(fkey)

        if fkey == src_key:
            ok_r, _ = return_to_followers_list(d, source_profile_username, pkg)
            if not ok_r:
                return 42
            continue

        if not open_follower_profile_from_list(d, pick, source_profile_username, pkg):
            _eng_log(
                "follower_profile_open_failed",
                "failed",
                "open_follower_failed",
                {"follower_username": pick.get("username")},
            )
            ok_r, _ = return_to_followers_list(d, source_profile_username, pkg)
            if not ok_r:
                return 42
            continue

        _eng_log(
            "follower_profile_open_success",
            "success",
            "follower_profile_open",
            {"follower_username": pick.get("username")},
        )

        follower_un = str(pick.get("username") or "")

        if bool(getattr(config, "ENABLE_REAL_FOLLOW", False)):
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
                ok_sq, _ = return_to_followers_list(d, source_profile_username, pkg)
                if not ok_sq:
                    return 42
                continue

            elig = _social_memory_load_and_evaluate(
                target_username=follower_un,
                source_profile=source_profile_username,
                account_id=account_id,
                run_id=run_id,
                supabase_mode=supabase_mode,
            )
            if not elig.allowed:
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
                    target_username=follower_un,
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
                ok_el, _ = return_to_followers_list(d, source_profile_username, pkg)
                if not ok_el:
                    return 42
                continue

            follow_out = perform_follow_safe(d, follower_un, pkg)
            _flush_follow_action_logs_to_supabase(
                events=list(follow_out.get("events") or []),
                run_id=run_id,
                account_id=account_id,
                target_username=follower_un,
                supabase_mode=supabase_mode,
            )

            if not follow_out.get("ok"):
                fc = int(follow_out.get("failure_code") or 33)
                _SESSION_COUNTERS["interactions"] += 1
                _RUNTIME_INTERACTED_USERNAMES.add(fkey)
                if supabase_mode and account_id:
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
                ok_f, _ = return_to_followers_list(d, source_profile_username, pkg)
                if not ok_f:
                    return 42
                continue

            fs_af = str(follow_out.get("follow_state_after") or "")
            if bool(follow_out.get("skipped_tap")):
                f_st = "already_following"
            elif fs_af == "requested":
                f_st = "requested"
            else:
                f_st = "following"

            if not bool(follow_out.get("skipped_tap")):
                _RUNTIME_FOLLOW_COUNT += 1
            _RUNTIME_FOLLOWED_USERNAMES.add(fkey)
            _RUNTIME_INTERACTED_USERNAMES.add(fkey)
            _SESSION_COUNTERS["follows"] += 1
            _SESSION_COUNTERS["interactions"] += 1
            _SESSION_COUNTERS["successful_interactions"] += 1

            if supabase_mode and account_id:
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
                "followers_follow_skipped",
                reason="ENABLE_REAL_FOLLOW_false",
                follower_username=pick.get("username"),
                source_profile_username=source_profile_username,
            )

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

        processed += 1

    stop_final = get_followers_engine_stop_reason()
    if stop_final in FOLLOWERS_ENGINE_XML_STALE_EXIT_REASONS:
        det_final = detect_followers_list_screen(
            d, source_profile_username=source_profile_username
        )
        return _followers_xml_stale_engine_stop(
            stop_reason=stop_final,
            det=det_final,
            loop_iteration=followers_engine_loop_iteration,
        )

    _emit_performance_summary(
        t0=t0,
        warm_session_used=warm_session_used,
        force_stop_used=force_stop_used,
        exit_code=0,
        target_username=source_profile_username,
    )
    log(
        "info",
        "followers_engine_session_complete",
        source_profile_username=source_profile_username,
        iterations=processed,
        total_ms=round((time.perf_counter() - t0) * 1000, 2),
    )
    _eng_log(
        "followers_engine_session_complete",
        "success",
        "complete",
        {
            "iterations": processed,
            "total_ms": round((time.perf_counter() - t0) * 1000, 2),
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
        if not targets:
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
        return _return_with_cleanup(d, eng_code)

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
