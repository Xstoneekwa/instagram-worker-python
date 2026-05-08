"""Orchestrate one safe Instagram navigation run (PoC)."""

from __future__ import annotations

import argparse
import time

import config
import supabase_client
from device import app_start, connect_device, disable_android_animations, force_stop, health_check
from instagram_navigation import (
    cleanup_dm_after_send_button_missing,
    clear_dm_draft,
    detect_unsupported_start_surface,
    dismiss_android_permission_dialog,
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
)
from logs import log


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
    }
    return mapping.get(code, f"exit_code_{code}")


def _safe_supabase_call(fn_name: str, *args, **kwargs):
    fn = getattr(supabase_client, fn_name)
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        log("warning", "supabase_call_failed", fn=fn_name, error=str(e))
        return None


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

        if bool(getattr(config, "DM_VERIFY_TYPED_TEXT", True)):
            if not verify_dm_draft_text(d, draft_text):
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
            status="success" if code == 0 else "failed",
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
        if code != 0:
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
                payload={"exit_code": 0, "performance": target_perf},
            )
        if supabase_mode and target_id:
            send_res = get_last_dm_send_result()
            if send_res.get("sent"):
                _safe_supabase_call(
                    "mark_target_dm_sent_completed",
                    target_id=target_id,
                )
                log(
                    "info",
                    "target_status_updated",
                    target_id=target_id,
                    status="completed",
                    last_error=None,
                    note="dm_sent",
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
        _update_run_status_safe(
            run_id=run_id,
            status="completed",
            totals={"total": len(targets), "success": successes, "failed": failures},
            performance_summary=perf_summary,
        )
    return _return_with_cleanup(d, 1 if failures > 0 else 0)


if __name__ == "__main__":
    raise SystemExit(main())
