"""
V4.5 — Welcome DM list-native sender: Followers row → profile → Message → send → action-bar backs.

Search-based dm_sender_send remains for Outreach / external prospects.
"""

from __future__ import annotations

import time
import traceback
from typing import Any

import uiautomator2 as u2

import config
import supabase_client
from dm_sender_engine import (
    _check_dm_sender_permission_blocker,
    _claim_job_for_run,
    _complete_job_failed_retry,
    _complete_job_skipped,
    _dm_sender_session_should_abort,
    _evaluate_welcome_sendability,
    _perform_real_welcome_dm_send,
    _reset_dm_sender_session_abort,
    _resolve_dm_sender_only_job_id,
    _resolve_dm_sender_real_send_enabled,
    _resolve_reserved_by,
)
from instagram_navigation import (
    detect_followers_list_screen_fresh,
    followers_refresh_detect_hierarchy_cache,
    harvest_visible_followers_rows,
    get_last_dm_thread_classify_snapshot,
    is_dm_thread_screen,
    get_last_dm_thread_attempted,
    open_dm_thread_from_profile,
    reset_dm_thread_probe_state,
    return_welcome_list_from_dm_to_followers,
    scroll_followers_list_to_find_row,
    tap_followers_list_username_row,
    verify_dm_composer_safe,
    verify_profile,
)
from logs import log

_LAST_WELCOME_LIST_SENDER_SUMMARY: dict[str, Any] = {}


def get_last_welcome_list_sender_summary() -> dict[str, Any]:
    return dict(_LAST_WELCOME_LIST_SENDER_SUMMARY)


def _norm_username(raw: str) -> str:
    return str(raw or "").strip().lstrip("@").lower()


def _order_jobs_by_scan(
    scan_order: list[str],
    jobs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rank = {_norm_username(u): i for i, u in enumerate(scan_order)}
    return sorted(
        jobs,
        key=lambda j: rank.get(
            _norm_username(str(j.get("recipient_username") or "")), 9999
        ),
    )


def _scan_row_anchors_by_username(scan_summary: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in scan_summary.get("new_follower_visible_rows_enqueued") or []:
        if not isinstance(row, dict):
            continue
        key = _norm_username(str(row.get("username") or ""))
        if key:
            out[key] = dict(row)
    return out


def _row_from_scan_anchor(anchor: dict[str, Any]) -> dict[str, Any] | None:
    bounds = dict(anchor.get("tap_bounds") or anchor.get("username_bounds") or {})
    if not bounds:
        return None
    try:
        _ = int(bounds["left"])
        _ = int(bounds["top"])
        _ = int(bounds["right"])
        _ = int(bounds["bottom"])
    except (KeyError, TypeError, ValueError):
        return None
    username = str(anchor.get("username") or "").strip()
    if not username:
        return None
    return {
        "username": username,
        "bounds": dict(anchor.get("username_bounds") or bounds),
        "username_bounds": dict(anchor.get("username_bounds") or bounds),
        "tap_bounds": dict(bounds),
        "row_index": anchor.get("row_index"),
        "extraction_source": str(anchor.get("extraction_source") or "scan_anchor"),
        "screen_index": int(anchor.get("screen_index") or 0),
        "hierarchy_source": str(anchor.get("hierarchy_source") or "scan_handoff"),
    }


def _resolve_followers_row(
    d: u2.Device,
    username: str,
    *,
    account_username: str,
    scan_anchors: dict[str, dict[str, Any]],
    screen_index: int = 0,
) -> tuple[dict[str, Any] | None, int, str, dict[str, Any]]:
    """
    Path 1 scan anchor → path 2 fresh visible harvest → path 3 bounded scroll.
    Returns (row, scroll_count, lookup_path, debug_meta).
    """
    uname = str(username or "").strip()
    src = str(account_username or "").strip()
    key = _norm_username(uname)
    debug: dict[str, Any] = {
        "username": uname,
        "scan_anchor_present": key in scan_anchors,
    }

    rows, harvest_meta = harvest_visible_followers_rows(
        d,
        source_profile_username=src,
        runtime_seen=set(),
        force_fresh_hierarchy=True,
        screen_index=screen_index,
    )
    visible_usernames = [str(r.get("username") or "") for r in rows if r.get("username")]
    visible_keys = {_norm_username(u) for u in visible_usernames}
    debug["visible_usernames"] = visible_usernames
    debug["hierarchy_source"] = harvest_meta.get("hierarchy_source")
    debug["screen_index"] = screen_index

    log(
        "info",
        "welcome_list_sender_visible_rows_snapshot",
        username=uname,
        target_username=uname,
        visible_usernames=visible_usernames,
        visible_count=len(visible_usernames),
        source=str(harvest_meta.get("hierarchy_source") or ""),
        screen_index=screen_index,
        extraction_methods=list(harvest_meta.get("extraction_methods") or []),
        scan_anchor_present=bool(debug["scan_anchor_present"]),
    )

    anchor = scan_anchors.get(key)
    if anchor is not None:
        row = _row_from_scan_anchor(anchor)
        if row is not None:
            log(
                "info",
                "welcome_list_sender_scan_row_anchor_reused",
                username=uname,
                row_index=row.get("row_index"),
                username_bounds=row.get("username_bounds"),
                tap_bounds=row.get("tap_bounds"),
                extraction_source=row.get("extraction_source"),
                screen_index=row.get("screen_index"),
            )
            return row, 0, "scan_anchor", debug

    for row in rows:
        if _norm_username(str(row.get("username") or "")) == key:
            log(
                "info",
                "welcome_list_sender_row_lookup_fresh_match",
                username=uname,
                row_index=row.get("row_index"),
                extraction_source=row.get("extraction_source"),
                hierarchy_source=row.get("hierarchy_source"),
            )
            return dict(row), 0, "fresh_visible", debug

    log(
        "info",
        "welcome_list_sender_row_lookup_fresh_miss",
        username=uname,
        visible_usernames=visible_usernames,
        target_in_visible_list=key in visible_keys,
        scan_anchor_present=bool(debug["scan_anchor_present"]),
    )

    if key in visible_keys:
        debug["lookup_path_used"] = "fresh_miss_despite_visible_username"
        return None, 0, "fresh_miss_despite_visible", debug

    log(
        "info",
        "welcome_list_sender_scroll_find_started",
        username=uname,
        reason="target_absent_from_visible_snapshot",
        initial_visible_usernames=visible_usernames,
        scan_anchor_present=bool(debug["scan_anchor_present"]),
    )
    debug["visible_usernames_before_scroll"] = list(visible_usernames)
    row, scrolls, visible_after = scroll_followers_list_to_find_row(
        d,
        uname,
        source_profile_username=src,
        screen_index=screen_index,
    )
    debug["visible_usernames_after_last_scroll"] = visible_after
    if row is not None:
        log(
            "info",
            "welcome_list_sender_row_lookup_fresh_match",
            username=uname,
            row_index=row.get("row_index"),
            extraction_source=row.get("extraction_source"),
            lookup_path="after_scroll",
            scrolls=scrolls,
        )
        return dict(row), scrolls, "scroll_find", debug

    debug["lookup_path_used"] = "scroll_exhausted"
    return None, scrolls, "not_found", debug


def _resolve_pending_recipients(
    scan_summary: dict[str, Any],
    *,
    max_jobs: int,
) -> list[str]:
    enqueued = [
        str(u).strip()
        for u in (scan_summary.get("new_follower_usernames_enqueued") or [])
        if str(u).strip()
    ]
    detected = [
        str(u).strip()
        for u in (scan_summary.get("new_follower_usernames_detected") or [])
        if str(u).strip()
    ]
    ordered: list[str] = []
    seen: set[str] = set()
    for src in (enqueued, detected):
        for u in src:
            key = _norm_username(u)
            if not key or key in seen:
                continue
            seen.add(key)
            ordered.append(u)
            if len(ordered) >= max_jobs:
                return ordered
    return ordered[:max_jobs]


def _verify_followers_surface(
    d: u2.Device,
    *,
    account_username: str,
    context: str,
) -> tuple[bool, dict[str, Any]]:
    det, obs = detect_followers_list_screen_fresh(
        d, source_profile_username=account_username
    )
    ok = bool(det.get("is_followers_list"))
    if not ok:
        log(
            "error",
            "welcome_list_sender_followers_surface_missing",
            context=context,
            account_username=account_username,
            action_bar_title=det.get("action_bar_title"),
            signals=det.get("signals"),
        )
    return ok, obs


def _navigate_followers_row_to_dm(
    d: u2.Device,
    username: str,
    *,
    pkg: str,
    account_username: str,
    scan_anchors: dict[str, dict[str, Any]],
) -> tuple[str, bool, dict[str, Any]]:
    """Followers list row tap → profile → DM thread."""
    uname = str(username or "").strip()
    nav_meta: dict[str, Any] = {"username": uname}
    reset_dm_thread_probe_state()

    row, scrolls, lookup_path, lookup_debug = _resolve_followers_row(
        d,
        uname,
        account_username=account_username,
        scan_anchors=scan_anchors,
    )
    nav_meta["followers_scrolls_to_find"] = scrolls
    nav_meta["lookup_path_used"] = lookup_path
    nav_meta.update(lookup_debug)

    if row is None:
        log(
            "error",
            "welcome_list_sender_row_not_found",
            username=uname,
            scrolls_attempted=scrolls,
            visible_usernames_before_scroll=lookup_debug.get(
                "visible_usernames_before_scroll"
            ),
            visible_usernames_after_last_scroll=lookup_debug.get(
                "visible_usernames_after_last_scroll"
            ),
            lookup_path_used=lookup_path,
            scan_anchor_present=bool(lookup_debug.get("scan_anchor_present")),
            visible_usernames=lookup_debug.get("visible_usernames"),
        )
        return "unknown", False, nav_meta

    bounds = dict(row.get("bounds") or {})
    log(
        "info",
        "welcome_list_sender_row_found",
        username=uname,
        bounds=bounds,
        row_index=row.get("row_index"),
        extraction_source=row.get("extraction_source"),
        followers_scrolls_to_find=scrolls,
    )

    log(
        "info",
        "welcome_list_sender_row_tap_started",
        username=uname,
        bounds=bounds,
        row_index=row.get("row_index"),
        extraction_source=row.get("extraction_source"),
    )
    tapped, tap_x, tap_y = tap_followers_list_username_row(d, row, username=uname)
    if not tapped:
        return "unknown", False, nav_meta

    log(
        "info",
        "welcome_list_sender_row_tapped",
        username=uname,
        tap_x=tap_x,
        tap_y=tap_y,
        bounds=bounds,
        row_index=row.get("row_index"),
        extraction_source=row.get("extraction_source"),
    )

    time.sleep(float(getattr(config, "PROFILE_POST_TAP_STABILIZE_S", 0.12) or 0.12))
    if not verify_profile(d, uname):
        log("error", "welcome_list_sender_profile_verify_failed", username=uname)
        return "unknown", False, nav_meta

    log("info", "welcome_list_sender_profile_opened", username=uname)

    thread_state = open_dm_thread_from_profile(
        d, uname, welcome_list_native=True
    )
    log(
        "info",
        "welcome_list_sender_dm_thread_opened",
        username=uname,
        thread_state=thread_state,
    )

    if thread_state not in ("dm_not_available", "unknown"):
        ok_comp, comp_reason = verify_dm_composer_safe(d, pkg)
        log(
            "info",
            "welcome_list_sender_composer_probe",
            username=uname,
            composer_ok=bool(ok_comp),
            composer_reason=comp_reason,
        )

    nav_ok = thread_state not in ("unknown",)
    return thread_state, nav_ok, nav_meta


def _restore_followers_after_job(
    d: u2.Device,
    username: str,
    *,
    pkg: str,
    account_username: str,
) -> bool:
    """Ensure we end on followers list (from DM, profile, or already on list)."""
    src = str(account_username or "").strip()
    if is_dm_thread_screen(d, pkg):
        fin = return_welcome_list_from_dm_to_followers(
            d,
            username,
            pkg,
            source_profile_username=src,
        )
        return bool(fin.get("followers_surface_ok"))

    from instagram_navigation import tap_instagram_action_bar_back_button

    settle_s = float(
        getattr(config, "WELCOME_LIST_SENDER_BACK_SETTLE_S", 0.45) or 0.45
    )
    for step in range(2):
        det, _ = detect_followers_list_screen_fresh(d, source_profile_username=src)
        if bool(det.get("is_followers_list")):
            if step > 0:
                log(
                    "info",
                    "welcome_list_sender_followers_surface_restored",
                    username=username,
                    via=f"action_bar_back_step_{step}",
                )
            return True
        tapped, _ = tap_instagram_action_bar_back_button(d, pkg)
        if tapped and settle_s > 0:
            time.sleep(settle_s)
    return False


def execute_welcome_list_job(
    d: u2.Device,
    job: dict[str, Any],
    *,
    settings: dict[str, Any],
    account_id: str,
    account_username: str,
    scan_anchors: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    job_id = str(job.get("id") or "")
    recipient = str(job.get("recipient_username") or "").strip()
    dm_type = str(job.get("dm_type") or "")
    message_body = str(job.get("message_body") or "")
    pkg = str(getattr(config, "INSTAGRAM_PACKAGE", "") or "com.instagram.android")

    running = supabase_client.mark_dm_job_running(job_id)
    if not running:
        log("error", "welcome_list_sender_mark_running_failed", job_id=job_id)
        _complete_job_failed_retry(
            job, last_error="mark_dm_job_running_failed", thread_state=None
        )
        return {
            "job_id": job_id,
            "recipient_username": recipient,
            "outcome": "failed_retry",
            "final_job_status": "pending",
            "thread_state": None,
        }

    thread_state = "unknown"
    outcome = "failed_retry"
    final_status = "pending"
    updated_job: dict[str, Any] | None = None
    list_nav_ms = 0.0
    dm_send_ms = 0.0

    try:
        if _check_dm_sender_permission_blocker(
            d, username=recipient, context="welcome_list_navigation"
        ):
            updated_job, outcome = _complete_job_failed_retry(
                job,
                last_error="unexpected_permission_dialog",
                thread_state=None,
            )
            final_status = str((updated_job or {}).get("status") or "pending")
            return {
                "job_id": job_id,
                "recipient_username": recipient,
                "outcome": outcome,
                "final_job_status": final_status,
                "thread_state": None,
            }

        t_nav = time.perf_counter()
        thread_state, nav_ok, _nav_meta = _navigate_followers_row_to_dm(
            d,
            recipient,
            pkg=pkg,
            account_username=account_username,
            scan_anchors=scan_anchors,
        )
        list_nav_ms = (time.perf_counter() - t_nav) * 1000.0
        snap = get_last_dm_thread_classify_snapshot()
        log(
            "info",
            "welcome_list_sender_thread_state_evaluated",
            job_id=job_id,
            username=recipient,
            thread_state=thread_state,
            navigation_ok=bool(nav_ok),
            classify_snapshot=bool(snap),
            list_navigation_ms=round(list_nav_ms, 2),
        )

        if not nav_ok:
            if thread_state == "unknown" and get_last_dm_thread_attempted():
                fail_reason = "thread_state_unknown_after_message"
            else:
                fail_reason = "list_navigation_failed"
            updated_job, outcome = _complete_job_failed_retry(
                job,
                last_error=fail_reason,
                thread_state=thread_state,
            )
            final_status = str((updated_job or {}).get("status") or "pending")
            log(
                "info",
                "welcome_list_sender_job_failed_retry_scheduled",
                job_id=job_id,
                recipient_username=recipient,
                reason=fail_reason,
            )
        else:
            sendable, skip_candidate = _evaluate_welcome_sendability(
                thread_state, settings
            )
            if dm_type != "welcome":
                sendable = thread_state == "empty_new_thread"
                skip_candidate = None if sendable else thread_state

            if thread_state in ("restricted_account", "dm_not_available"):
                reason = (
                    "dm_not_available"
                    if thread_state == "dm_not_available"
                    else "restricted_account"
                )
                updated_job, outcome = _complete_job_skipped(
                    job, skip_reason=reason, thread_state=thread_state
                )
                final_status = str((updated_job or {}).get("status") or "skipped")
                log(
                    "info",
                    "welcome_list_sender_job_completed_skipped",
                    job_id=job_id,
                    recipient_username=recipient,
                    skip_reason=reason,
                    thread_state=thread_state,
                )
            elif not sendable:
                skip_reason = str(skip_candidate or thread_state or "not_sendable")
                updated_job, outcome = _complete_job_skipped(
                    job, skip_reason=skip_reason, thread_state=thread_state
                )
                final_status = str((updated_job or {}).get("status") or "skipped")
                log(
                    "info",
                    "welcome_list_sender_job_completed_skipped",
                    job_id=job_id,
                    recipient_username=recipient,
                    skip_reason=skip_reason,
                    thread_state=thread_state,
                )
            elif thread_state in ("unknown", "composer_visible_uncertain"):
                updated_job, outcome = _complete_job_failed_retry(
                    job,
                    last_error=f"thread_state_{thread_state}",
                    thread_state=thread_state,
                )
                final_status = str((updated_job or {}).get("status") or "pending")
                log(
                    "info",
                    "welcome_list_sender_job_failed_retry_scheduled",
                    job_id=job_id,
                    recipient_username=recipient,
                    reason=f"thread_state_{thread_state}",
                )
            else:
                t_send = time.perf_counter()
                sent_ok, send_out, fail_reason = _perform_real_welcome_dm_send(
                    d,
                    username=recipient,
                    message_body=message_body,
                    thread_state=thread_state,
                    pkg=pkg,
                    post_send_nav="welcome_list",
                    source_profile_username=account_username,
                )
                dm_send_ms = (time.perf_counter() - t_send) * 1000.0
                if sent_ok and fail_reason in (None, "post_finalize_partial"):
                    updated_job = supabase_client.complete_dm_job(
                        job_id,
                        "sent",
                        metadata_patch={
                            "thread_state": thread_state,
                            "send_method": "instagram_send_ui",
                            "message_len": len(message_body),
                            "welcome_list_native": True,
                            "post_finalize_partial": fail_reason == "post_finalize_partial",
                        },
                    )
                    outcome = "sent"
                    final_status = str((updated_job or {}).get("status") or "sent")
                    log(
                        "info",
                        "welcome_list_sender_job_completed_sent",
                        job_id=job_id,
                        recipient_username=recipient,
                        thread_state=thread_state,
                        final_job_status=final_status,
                        dm_send_ms=round(dm_send_ms, 2),
                    )
                else:
                    updated_job, outcome = _complete_job_failed_retry(
                        job,
                        last_error=str(fail_reason or "real_send_failed"),
                        thread_state=thread_state,
                    )
                    final_status = str((updated_job or {}).get("status") or "pending")
                    log(
                        "info",
                        "welcome_list_sender_job_failed_retry_scheduled",
                        job_id=job_id,
                        recipient_username=recipient,
                        reason=str(fail_reason or "real_send_failed"),
                    )
    finally:
        if not _restore_followers_after_job(
            d, recipient, pkg=pkg, account_username=account_username
        ):
            _check_dm_sender_permission_blocker(
                d, username=recipient, context="welcome_list_teardown"
            )

    return {
        "job_id": job_id,
        "recipient_username": recipient,
        "outcome": outcome,
        "final_job_status": final_status,
        "thread_state": thread_state,
        "list_navigation_ms": round(list_nav_ms, 2),
        "dm_send_ms": round(dm_send_ms, 2),
        "job": updated_job,
    }


def run_welcome_list_sender(
    d: u2.Device,
    *,
    account_id: str,
    account_username: str,
    run_id: str | None = None,
    max_jobs: int | None = None,
    scan_summary: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    """
    List-native Welcome DM sender (stays on / returns to Followers list).
    Returns (exit_code, summary) compatible with welcome session orchestrator.
    """
    global _LAST_WELCOME_LIST_SENDER_SUMMARY
    t0 = time.perf_counter()
    aid = str(account_id or "").strip()
    acct_user = str(account_username or "").strip()
    pkg = str(getattr(config, "INSTAGRAM_PACKAGE", "") or "com.instagram.android")
    if max_jobs is None:
        max_jobs = int(getattr(config, "WELCOME_SESSION_SEND_MAX_JOBS", 3) or 3)
    max_jobs = max(0, int(max_jobs))

    scan = dict(scan_summary or {})
    scan_order = _resolve_pending_recipients(scan, max_jobs=max_jobs)

    summary: dict[str, Any] = {
        "account_id": aid,
        "run_id": run_id,
        "max_jobs": max_jobs,
        "sender_mode": "welcome_list_native",
        "jobs_claimed_count": 0,
        "jobs_sent_count": 0,
        "jobs_skipped_count": 0,
        "jobs_failed_count": 0,
        "processed_recipients": [],
        "sent_recipients": [],
        "skipped_recipients": [],
        "failed_recipients": [],
        "recipients_targeted": list(scan_order),
        "recipients_sent": [],
        "recipients_skipped": [],
        "recipients_failed": [],
        "list_navigation_total_ms": 0.0,
        "dm_send_total_ms": 0.0,
        "sender_status": "not_started",
    }

    real_enabled, real_source = _resolve_dm_sender_real_send_enabled()
    if not real_enabled:
        summary["sender_status"] = "blocked_disabled"
        summary["real_send_source"] = real_source
        summary["total_ms"] = round((time.perf_counter() - t0) * 1000.0, 2)
        _LAST_WELCOME_LIST_SENDER_SUMMARY = dict(summary)
        log("info", "welcome_list_sender_summary", **summary)
        return 1, summary

    _reset_dm_sender_session_abort()
    reserved_by = _resolve_reserved_by(d)
    only_job_id, _filter_source = _resolve_dm_sender_only_job_id()

    log(
        "info",
        "welcome_list_sender_started",
        account_id=aid,
        account_username=acct_user,
        run_id=run_id,
        max_jobs=max_jobs,
    )
    log(
        "info",
        "welcome_list_sender_pending_recipients_resolved",
        account_id=aid,
        recipients_targeted=scan_order,
        scan_enqueued_count=len(scan.get("new_follower_usernames_enqueued") or []),
        scan_detected_count=len(scan.get("new_follower_usernames_detected") or []),
    )

    followers_ok, _obs = _verify_followers_surface(
        d, account_username=acct_user, context="welcome_list_sender_start"
    )
    if followers_ok:
        followers_refresh_detect_hierarchy_cache(d, screen_index=0)

    scan_anchors = _scan_row_anchors_by_username(scan)

    if not followers_ok:
        summary["sender_status"] = "failed"
        summary["failure_reason"] = "followers_surface_missing_at_start"
        summary["total_ms"] = round((time.perf_counter() - t0) * 1000.0, 2)
        _LAST_WELCOME_LIST_SENDER_SUMMARY = dict(summary)
        log("info", "welcome_list_sender_summary", **summary)
        log("info", "welcome_list_sender_completed", account_id=aid, sender_status="failed")
        return 1, summary

    try:
        settings = supabase_client.get_account_dm_settings(aid) or {}
    except Exception as e:
        log("error", "welcome_list_sender_settings_load_failed", error=str(e))
        settings = {}

    loop_exit_reason: str | None = None
    for _ in range(max_jobs):
        if _dm_sender_session_should_abort():
            loop_exit_reason = "permission_dialog_abort"
            log(
                "error",
                "dm_sender_session_aborted_permission_dialog",
                account_id=aid,
                run_id=run_id,
                context="welcome_list_claim",
            )
            break

        job = _claim_job_for_run(
            aid, reserved_by, dm_type="welcome", only_job_id=only_job_id
        )
        if not job or not str(job.get("id") or "").strip():
            loop_exit_reason = "no_pending_job"
            log(
                "info",
                "welcome_list_sender_no_pending_job",
                account_id=aid,
                run_id=run_id,
                exit_reason=loop_exit_reason,
            )
            break

        if _dm_sender_session_should_abort():
            loop_exit_reason = "permission_dialog_abort"
            log(
                "error",
                "dm_sender_session_aborted_permission_dialog",
                account_id=aid,
                run_id=run_id,
            )
            break

        followers_ok, _ = _verify_followers_surface(
            d,
            account_username=acct_user,
            context="welcome_list_pre_job",
        )
        if not followers_ok:
            loop_exit_reason = "followers_surface_lost"
            summary["failure_reason"] = "followers_surface_lost"
            break

        recipient = str(job.get("recipient_username") or "").strip()
        summary["jobs_claimed_count"] += 1
        summary["processed_recipients"].append(recipient)

        try:
            result = execute_welcome_list_job(
                d,
                job,
                settings=settings,
                account_id=aid,
                account_username=acct_user,
                scan_anchors=scan_anchors,
            )
        except Exception as e:
            log(
                "error",
                "welcome_list_sender_job_exception",
                job_id=str(job.get("id") or ""),
                recipient_username=recipient,
                phase="execute_welcome_list_job",
                error=str(e),
                traceback=traceback.format_exc(),
            )
            raise
        summary["list_navigation_total_ms"] = float(
            summary["list_navigation_total_ms"]
        ) + float(result.get("list_navigation_ms") or 0.0)
        summary["dm_send_total_ms"] = float(summary["dm_send_total_ms"]) + float(
            result.get("dm_send_ms") or 0.0
        )

        outcome = str(result.get("outcome") or "")
        if outcome == "sent":
            summary["jobs_sent_count"] += 1
            summary["sent_recipients"].append(recipient)
            summary["recipients_sent"].append(recipient)
        elif outcome == "skipped":
            summary["jobs_skipped_count"] += 1
            summary["skipped_recipients"].append(recipient)
            summary["recipients_skipped"].append(recipient)
        else:
            summary["jobs_failed_count"] += 1
            summary["failed_recipients"].append(recipient)
            summary["recipients_failed"].append(recipient)

        if _dm_sender_session_should_abort():
            loop_exit_reason = "permission_dialog_abort"
            log(
                "error",
                "dm_sender_session_aborted_permission_dialog",
                account_id=aid,
                run_id=run_id,
                last_recipient=recipient,
            )
            break

        followers_ok, _ = _verify_followers_surface(
            d,
            account_username=acct_user,
            context="welcome_list_post_job",
        )
        if not followers_ok:
            loop_exit_reason = "followers_surface_lost_after_job"
            summary["failure_reason"] = "followers_surface_lost_after_job"
            break

    if loop_exit_reason is None:
        loop_exit_reason = "max_jobs_reached"
    summary["loop_exit_reason"] = loop_exit_reason
    log(
        "info",
        "welcome_list_sender_loop_finished",
        account_id=aid,
        run_id=run_id,
        exit_reason=loop_exit_reason,
        max_jobs=max_jobs,
        jobs_claimed_count=int(summary["jobs_claimed_count"]),
    )

    total_ms = (time.perf_counter() - t0) * 1000.0
    claimed = int(summary["jobs_claimed_count"])
    failed = int(summary["jobs_failed_count"])
    sent = int(summary["jobs_sent_count"])

    if claimed == 0:
        sender_status = "no_jobs"
        exit_code = 0
    elif failed > 0 and sent == 0:
        sender_status = "failed"
        exit_code = 1
    elif failed > 0 or int(summary["jobs_skipped_count"]) > 0:
        sender_status = "partial_success"
        exit_code = 0
    else:
        sender_status = "success"
        exit_code = 0

    if str(summary.get("failure_reason") or "").startswith("followers_surface"):
        sender_status = "failed" if sent == 0 else "partial_success"
        exit_code = 0 if sent > 0 else 1

    summary["sender_status"] = sender_status
    summary["total_ms"] = round(total_ms, 2)
    summary["list_navigation_total_ms"] = round(
        float(summary["list_navigation_total_ms"]), 2
    )
    summary["dm_send_total_ms"] = round(float(summary["dm_send_total_ms"]), 2)

    _LAST_WELCOME_LIST_SENDER_SUMMARY = dict(summary)
    log("info", "welcome_list_sender_summary", **summary)
    log(
        "info",
        "welcome_list_sender_completed",
        account_id=aid,
        run_id=run_id,
        sender_status=sender_status,
    )
    return exit_code, summary
