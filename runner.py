"""Orchestrate one safe Instagram navigation run (PoC)."""

from __future__ import annotations

import argparse
import time

import config
from device import app_start, connect_device, disable_android_animations, force_stop, health_check
from instagram_navigation import (
    get_perf_snapshot,
    get_type_search_failure_reason,
    instagram_warm_session_eligible,
    invalidate_search_surface_cache,
    open_accounts_tab,
    open_search,
    reset_perf_counters,
    return_to_search_from_profile,
    set_search_ui_mode,
    tap_account_result,
    type_search,
    verify_app_foreground,
    verify_profile,
)
from logs import log


def _phase(name: str, phase_start: float) -> float:
    elapsed_ms = (time.perf_counter() - phase_start) * 1000
    log("info", "phase_timing", phase=name, elapsed_ms=round(elapsed_ms, 2))
    return time.perf_counter()


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
        row_detect_ms=round(float(snap.get("row_detect_ms", 0.0)), 2),
        row_tap_command_ms=round(float(snap.get("row_tap_command_ms", 0.0)), 2),
        post_tap_settle_ms=round(float(snap.get("post_tap_settle_ms", 0.0)), 2),
        profile_transition_wait_ms=round(float(snap.get("profile_transition_wait_ms", 0.0)), 2),
        profile_verify_ms=round(float(snap.get("profile_verify_ms", 0.0)), 2),
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


def _run_one_target(
    d,
    username: str,
    *,
    target_index: int,
    warm_session_used: bool,
    force_stop_used: bool,
    previous_username: str | None,
) -> int:
    """Navigate search → profile for one username. Assumes IG already foreground when target_index==0."""
    pkg = config.INSTAGRAM_PACKAGE
    t0 = time.perf_counter()
    t = t0

    if target_index > 0:
        if return_to_search_from_profile(d, pkg):
            t = _phase("return_to_search", t)
        else:
            t = _phase("return_to_search_failed", t)
            if not open_search(d):
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
    else:
        if not open_search(d):
            log("error", "run_aborted", reason="open_search_failed", username=username)
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
        return exit_code
    t = _phase("type_search", t)

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
        return exit_code
    t = _phase("tap_account", t)

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
        return exit_code
    t = _phase("verify_profile", t)

    log(
        "info",
        "run_success_stop",
        total_ms=round((time.perf_counter() - t0) * 1000, 2),
        target=username,
        message="Profile verified; safe run complete (no further actions).",
    )
    _emit_performance_summary(
        t0=t0,
        warm_session_used=warm_session_used,
        force_stop_used=force_stop_used,
        exit_code=0,
        target_username=username,
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
    args = parser.parse_args()
    targets = _parse_targets(args)
    if not targets:
        log("error", "run_aborted", reason="no_targets_after_parse")
        return 1

    log(
        "info",
        "run_started",
        targets=targets,
        target_count=len(targets),
        package=config.INSTAGRAM_PACKAGE,
        multi_mode=len(targets) > 1,
    )
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
        return 2
    t = _phase("health_check", t)

    warm_ok, warm_reason = instagram_warm_session_eligible(d, config.INSTAGRAM_PACKAGE)
    warm_session_used = warm_ok
    if warm_ok:
        log("info", "instagram_warm_session_reused", reason=warm_reason)
        time.sleep(config.WARM_SESSION_MICRO_WAIT_S)
        t = _phase("warm_session_skip_force_stop", t)
    else:
        invalidate_search_surface_cache("cold_start_or_unhealthy")
        force_stop_used = True
        force_stop(d, config.INSTAGRAM_PACKAGE)
        t = _phase("force_stop", t)

        app_start(d, config.INSTAGRAM_PACKAGE)
        time.sleep(config.APP_START_WAIT_S)
        t = _phase("app_start", t)

    if not verify_app_foreground(d, config.INSTAGRAM_PACKAGE):
        log("error", "run_aborted", reason="instagram_not_foreground")
        reset_perf_counters()
        _emit_performance_summary(
            t0=t_session,
            warm_session_used=warm_session_used,
            force_stop_used=force_stop_used,
            exit_code=3,
            target_username=targets[0] if targets else config.TARGET_USERNAME,
        )
        return 3
    t = _phase("verify_app_running", t)

    prev_username: str | None = None
    for idx, username in enumerate(targets):
        reset_perf_counters()
        log(
            "info",
            "multi_target_iteration",
            index=idx,
            username=username,
            total=len(targets),
        )
        code = _run_one_target(
            d,
            username,
            target_index=idx,
            warm_session_used=warm_session_used,
            force_stop_used=force_stop_used,
            previous_username=prev_username,
        )
        if code != 0:
            return code
        prev_username = username

    log(
        "info",
        "run_all_targets_complete",
        count=len(targets),
        total_session_ms=round((time.perf_counter() - t_session) * 1000, 2),
        message="Instagram left warm on device; no home press.",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
