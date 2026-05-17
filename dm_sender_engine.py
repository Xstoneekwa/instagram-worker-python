"""
V4.3-B — DM Sender Engine dry-run: claim ig_dm_jobs → navigate → classify → release/complete.

No type_dm_draft_only, no send_dm_safe, no real send.
"""

from __future__ import annotations

import os
import time
from typing import Any

import uiautomator2 as u2

import config
import supabase_client
from device import app_start, force_stop, get_device_serial
from instagram_navigation import (
    detect_unsupported_start_surface,
    dismiss_android_permission_dialog,
    get_last_dm_thread_classify_snapshot,
    invalidate_search_surface_cache,
    is_dm_thread_screen,
    open_accounts_tab,
    open_dm_thread_from_profile,
    open_search,
    reset_dm_thread_probe_state,
    return_to_profile_from_dm,
    return_to_search_from_profile,
    set_search_ui_mode,
    tap_account_result,
    type_search,
    verify_app_foreground,
    verify_dm_composer_safe,
    verify_profile,
)
from logs import log


def _resolve_reserved_by(d: u2.Device) -> str:
    cfg = str(getattr(config, "DM_SENDER_RESERVED_BY", "") or "").strip()
    if cfg:
        return cfg[:120]
    serial = get_device_serial(d)
    if serial:
        return str(serial)[:120]
    return f"worker-{os.getpid()}"[:120]


def _open_search_with_recovery(
    d: u2.Device,
    *,
    pkg: str,
    username: str,
    context: str,
) -> bool:
    if open_search(d):
        return True
    unsupported_reason = detect_unsupported_start_surface(d) or "open_search_no_edittext"
    log(
        "warning",
        "dm_sender_unsupported_surface_recovery",
        reason=unsupported_reason,
        username=username,
        context=context,
    )
    if unsupported_reason == "android_permission_dialog":
        dismiss_android_permission_dialog(d)
    invalidate_search_surface_cache(unsupported_reason)
    force_stop(d, pkg)
    app_start(d, pkg)
    time.sleep(float(getattr(config, "APP_START_WAIT_S", 3.0) or 3.0))
    if not verify_app_foreground(d, pkg):
        return False
    return bool(open_search(d))


def _evaluate_welcome_sendability(
    thread_state: str,
    settings: dict[str, Any],
) -> tuple[bool, str | None]:
    """
    Decide if Welcome would be sendable after dry-run (metadata only in V4.3-B).
    """
    check_chat = bool(settings.get("check_chat_before_welcome", True))
    skip_existing = bool(settings.get("welcome_skip_if_existing_thread", True))

    if thread_state == "empty_new_thread":
        return True, None

    if thread_state == "existing_thread":
        if check_chat and skip_existing:
            return False, "existing_thread"
        return False, "existing_thread"

    if thread_state == "restricted_account":
        return False, "restricted_account"

    if thread_state == "dm_not_available":
        return False, "dm_not_available"

    return False, "unknown_thread_state"


def _finalize_job_after_dry_run(
    job: dict[str, Any],
    *,
    thread_state: str,
    sendable: bool,
    skip_reason_candidate: str | None,
    settings: dict[str, Any],
) -> tuple[dict[str, Any] | None, str]:
    """
    Returns (updated_job, outcome_key).
    outcome_key: released_pending | skipped | failed_retry
    """
    job_id = str(job.get("id") or "")
    dm_type = str(job.get("dm_type") or "")

    if dm_type == "welcome":
        sendable, skip_reason_candidate = _evaluate_welcome_sendability(
            thread_state, settings
        )

    if thread_state in ("restricted_account",):
        row = supabase_client.complete_dm_job(
            job_id,
            "skipped",
            skip_reason=str(thread_state),
            metadata_patch={"dry_run_terminal": True, "thread_state": thread_state},
        )
        log(
            "info",
            "dm_sender_job_completed_skipped",
            job_id=job_id,
            thread_state=thread_state,
            recipient_username=job.get("recipient_username"),
        )
        return row, "skipped"

    if thread_state in ("dm_not_available",):
        row = supabase_client.complete_dm_job(
            job_id,
            "skipped",
            skip_reason="dm_not_available",
            metadata_patch={"dry_run_terminal": True, "thread_state": thread_state},
        )
        log(
            "info",
            "dm_sender_job_completed_skipped",
            job_id=job_id,
            thread_state=thread_state,
            skip_reason="dm_not_available",
            recipient_username=job.get("recipient_username"),
        )
        return row, "skipped"

    if thread_state in (
        "unknown",
        "composer_visible_uncertain",
    ):
        delay = int(getattr(config, "DM_SENDER_FAILED_RETRY_DELAY_SECONDS", 300) or 300)
        row = supabase_client.complete_dm_job(
            job_id,
            "failed",
            last_error=f"dry_run_thread_state_{thread_state}",
            increment_attempt=True,
            retry_delay_seconds=delay,
            metadata_patch={"dry_run_failed": True, "thread_state": thread_state},
        )
        log(
            "info",
            "dm_sender_job_failed_retry_scheduled",
            job_id=job_id,
            thread_state=thread_state,
            retry_delay_seconds=delay,
            recipient_username=job.get("recipient_username"),
        )
        return row, "failed_retry"

    row = supabase_client.release_dm_job_after_dry_run(
        job_id,
        thread_state=thread_state,
        sendable=bool(sendable),
        skip_reason_candidate=skip_reason_candidate,
        metadata_patch={
            "dm_type": dm_type,
            "recipient_username": str(job.get("recipient_username") or ""),
        },
    )
    log(
        "info",
        "dm_sender_dry_run_released_pending",
        job_id=job_id,
        recipient_username=job.get("recipient_username"),
        thread_state=thread_state,
        sendable=bool(sendable),
        skip_reason_candidate=skip_reason_candidate,
    )
    return row, "released_pending"


def _navigate_to_recipient_dm_thread(
    d: u2.Device,
    username: str,
    *,
    pkg: str,
) -> tuple[str, bool]:
    """
    Search → profile → DM thread. Returns (thread_state, navigation_ok).
    """
    uname = str(username or "").strip()
    log("info", "dm_sender_navigation_started", username=uname)

    if is_dm_thread_screen(d, pkg):
        if not return_to_profile_from_dm(d, uname, pkg):
            log("warning", "dm_sender_stuck_in_dm_thread", username=uname)
            return "unknown", False
        return_to_search_from_profile(d, pkg)

    if not _open_search_with_recovery(d, pkg=pkg, username=uname, context="dm_sender_first"):
        log("error", "dm_sender_open_search_failed", username=uname)
        return "unknown", False

    if not type_search(d, uname, previous_username=None):
        log("error", "dm_sender_type_search_failed", username=uname)
        return "unknown", False

    if bool(getattr(config, "FAST_SKIP_ACCOUNTS_TAB", True)) and bool(
        getattr(config, "FAST_PATH_MODE", False)
    ):
        set_search_ui_mode("mixed_results")
    else:
        accounts_tab_clicked = open_accounts_tab(d)
        set_search_ui_mode("accounts_tab" if accounts_tab_clicked else "mixed_results")

    if not tap_account_result(d, uname):
        log("error", "dm_sender_tap_account_failed", username=uname)
        return "unknown", False

    if not verify_profile(d, uname):
        log("error", "dm_sender_profile_verify_failed", username=uname)
        return "unknown", False

    log("info", "dm_sender_profile_opened", username=uname)

    reset_dm_thread_probe_state()
    thread_state = open_dm_thread_from_profile(d, uname)
    log(
        "info",
        "dm_sender_dm_thread_opened",
        username=uname,
        thread_state=thread_state,
    )

    if thread_state not in ("dm_not_available", "unknown"):
        ok_comp, comp_reason = verify_dm_composer_safe(d, pkg)
        log(
            "info",
            "dm_sender_composer_probe",
            username=uname,
            composer_ok=bool(ok_comp),
            composer_reason=comp_reason,
        )

    return thread_state, thread_state not in ("unknown",)


def _safe_teardown_navigation(
    d: u2.Device,
    username: str,
    *,
    pkg: str,
) -> None:
    try:
        if is_dm_thread_screen(d, pkg):
            return_to_profile_from_dm(d, username, pkg)
        return_to_search_from_profile(d, pkg)
    except Exception as e:
        log("warning", "dm_sender_teardown_navigation_failed", error=str(e)[:200])


def _resolve_dm_sender_only_job_id() -> tuple[str, str]:
    """Resolve job filter: shell env wins over config.DM_SENDER_ONLY_JOB_ID."""
    env_raw = os.environ.get("DM_SENDER_ONLY_JOB_ID")
    if env_raw is not None:
        return str(env_raw).strip(), "env"
    cfg = str(getattr(config, "DM_SENDER_ONLY_JOB_ID", "") or "").strip()
    if cfg:
        return cfg, "config"
    return "", "config_empty"


def _claim_job_for_run(
    account_id: str,
    reserved_by: str,
    *,
    dm_type: str,
    only_job_id: str = "",
) -> dict[str, Any] | None:
    only_id = str(only_job_id or "").strip()
    if only_id:
        job = supabase_client.claim_dm_job_by_id(account_id, only_id, reserved_by)
        if job:
            log(
                "info",
                "dm_sender_job_claimed",
                job_id=only_id,
                claim_mode="claim_by_id",
                recipient_username=job.get("recipient_username"),
            )
        return job
    job = supabase_client.claim_next_dm_job(
        account_id,
        reserved_by,
        dm_type=dm_type or None,
    )
    if job:
        log(
            "info",
            "dm_sender_job_claimed",
            job_id=str(job.get("id") or ""),
            claim_mode="claim_next",
            recipient_username=job.get("recipient_username"),
            priority=job.get("priority"),
        )
    return job


def execute_dm_job_dry_run(
    d: u2.Device,
    job: dict[str, Any],
    *,
    settings: dict[str, Any],
    account_id: str,
) -> dict[str, Any]:
    """Run UI dry-run for one job; finalize via release or complete."""
    job_id = str(job.get("id") or "")
    recipient = str(job.get("recipient_username") or "").strip()
    dm_type = str(job.get("dm_type") or "")
    pkg = str(getattr(config, "INSTAGRAM_PACKAGE", "") or "com.instagram.android")

    running = supabase_client.mark_dm_job_running(job_id)
    if not running:
        log("error", "dm_sender_mark_running_failed", job_id=job_id)
        delay = int(getattr(config, "DM_SENDER_FAILED_RETRY_DELAY_SECONDS", 300) or 300)
        supabase_client.complete_dm_job(
            job_id,
            "failed",
            last_error="mark_dm_job_running_failed",
            increment_attempt=True,
            retry_delay_seconds=delay,
        )
        return {
            "job_id": job_id,
            "recipient_username": recipient,
            "outcome": "failed_retry",
            "final_job_status": "pending",
            "thread_state": None,
            "sendable": False,
        }

    log(
        "info",
        "dm_sender_job_marked_running",
        job_id=job_id,
        recipient_username=recipient,
        dm_type=dm_type,
    )

    thread_state = "unknown"
    sendable = False
    skip_candidate: str | None = None
    outcome = "failed_retry"
    final_status = "pending"
    updated_job: dict[str, Any] | None = None

    try:
        thread_state, nav_ok = _navigate_to_recipient_dm_thread(
            d, recipient, pkg=pkg
        )
        snap = get_last_dm_thread_classify_snapshot()
        log(
            "info",
            "dm_sender_thread_state_evaluated",
            job_id=job_id,
            username=recipient,
            thread_state=thread_state,
            navigation_ok=bool(nav_ok),
            classify_snapshot=bool(snap),
        )

        if not nav_ok:
            delay = int(getattr(config, "DM_SENDER_FAILED_RETRY_DELAY_SECONDS", 300) or 300)
            updated_job = supabase_client.complete_dm_job(
                job_id,
                "failed",
                last_error="navigation_failed",
                increment_attempt=True,
                retry_delay_seconds=delay,
                metadata_patch={"thread_state": thread_state},
            )
            outcome = "failed_retry"
            final_status = str((updated_job or {}).get("status") or "pending")
            log(
                "info",
                "dm_sender_job_failed_retry_scheduled",
                job_id=job_id,
                reason="navigation_failed",
            )
        else:
            sendable, skip_candidate = _evaluate_welcome_sendability(
                thread_state, settings
            )
            if dm_type != "welcome":
                sendable = thread_state == "empty_new_thread"
                skip_candidate = None if sendable else thread_state

            updated_job, outcome = _finalize_job_after_dry_run(
                job,
                thread_state=thread_state,
                sendable=sendable,
                skip_reason_candidate=skip_candidate,
                settings=settings,
            )
            final_status = str((updated_job or {}).get("status") or "pending")
    finally:
        _safe_teardown_navigation(d, recipient, pkg=pkg)

    return {
        "job_id": job_id,
        "recipient_username": recipient,
        "dm_type": dm_type,
        "thread_state": thread_state,
        "sendable": bool(sendable),
        "skip_reason_candidate": skip_candidate,
        "outcome": outcome,
        "final_job_status": final_status,
        "job": updated_job,
    }


def run_dm_sender_dry_run(
    d: u2.Device,
    *,
    account_id: str,
    run_id: str | None = None,
) -> int:
    """
    Claim and dry-run up to DM_SENDER_DRY_RUN_MAX_JOBS_PER_RUN jobs.
    Returns process exit code (0 ok, 1 error/partial).
    """
    t0 = time.perf_counter()
    aid = str(account_id or "").strip()
    dm_type = str(getattr(config, "DM_SENDER_DEFAULT_DM_TYPE", "welcome") or "welcome")
    max_jobs = max(0, int(getattr(config, "DM_SENDER_DRY_RUN_MAX_JOBS_PER_RUN", 1) or 1))
    reserved_by = _resolve_reserved_by(d)
    only_job_id, filter_source = _resolve_dm_sender_only_job_id()
    log(
        "info",
        "dm_sender_job_filter_resolved",
        only_job_id=only_job_id or None,
        filter_source=filter_source,
    )

    jobs_claimed = 0
    jobs_released = 0
    jobs_skipped = 0
    jobs_failed = 0
    last_result: dict[str, Any] = {}

    log(
        "info",
        "dm_sender_dry_run_started",
        account_id=aid,
        run_id=run_id,
        dm_type=dm_type,
        max_jobs=max_jobs,
        reserved_by=reserved_by,
    )

    try:
        settings = supabase_client.get_account_dm_settings(aid) or {}
    except Exception as e:
        log("error", "dm_sender_settings_load_failed", error=str(e))
        settings = {}

    for _ in range(max_jobs):
        job = _claim_job_for_run(
            aid, reserved_by, dm_type=dm_type, only_job_id=only_job_id
        )
        if not job:
            log("info", "dm_sender_no_pending_job", account_id=aid, dm_type=dm_type)
            break

        jobs_claimed += 1
        last_result = execute_dm_job_dry_run(
            d,
            job,
            settings=settings,
            account_id=aid,
        )
        outcome = str(last_result.get("outcome") or "")
        if outcome == "released_pending":
            jobs_released += 1
        elif outcome == "skipped":
            jobs_skipped += 1
        elif outcome == "failed_retry":
            jobs_failed += 1

    total_ms = (time.perf_counter() - t0) * 1000.0
    log(
        "info",
        "dm_sender_dry_run_summary",
        account_id=aid,
        run_id=run_id,
        jobs_claimed_count=jobs_claimed,
        jobs_released_pending_count=jobs_released,
        jobs_skipped_count=jobs_skipped,
        jobs_failed_count=jobs_failed,
        recipient_username=last_result.get("recipient_username"),
        dm_type=last_result.get("dm_type") or dm_type,
        thread_state=last_result.get("thread_state"),
        sendable=last_result.get("sendable"),
        final_job_status=last_result.get("final_job_status"),
        total_ms=round(total_ms, 2),
    )

    if jobs_claimed == 0:
        return 0
    if jobs_failed > 0 and jobs_released == 0 and jobs_skipped == 0:
        return 1
    return 0


def dispatch_dm_sender_dry_run(
    d: u2.Device,
    *,
    account_id: str,
    run_id: str | None = None,
) -> int:
    log(
        "info",
        "dm_sender_dry_run_dispatch",
        account_id=account_id,
        run_id=run_id,
    )
    return run_dm_sender_dry_run(d, account_id=account_id, run_id=run_id)
