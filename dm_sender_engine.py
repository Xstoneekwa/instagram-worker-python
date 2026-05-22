"""
V4.3-B+ / V4.4 — DM Sender Engine: dry-run (classify + release) and real Welcome send.

Dry-run: no type_dm_draft_only / send_dm_safe.
Real send: reuses instagram_navigation draft + send + post-send finalize.
"""

from __future__ import annotations

import os
import time
from typing import Any, Callable

import uiautomator2 as u2

import config
import supabase_client
from device import app_start, force_stop, get_device_serial
from instagram_navigation import (
    _dm_find_focus_composer,
    _wait_search_edittext,
    cleanup_dm_after_send_button_missing,
    detect_unexpected_android_media_permission_dialog,
    detect_unsupported_start_surface,
    dismiss_android_permission_dialog,
    finalize_after_real_send,
    get_last_dm_thread_classify_snapshot,
    invalidate_search_surface_cache,
    is_dm_thread_screen,
    detect_followers_list_screen_fresh,
    is_followers_list_surface_quick,
    is_lightweight_search_screen,
    observe_followers_list_surface_fresh,
    tap_instagram_action_bar_back_button,
    open_accounts_tab,
    open_dm_thread_from_profile,
    open_search,
    reset_dm_thread_probe_state,
    return_to_profile_from_dm,
    return_to_search_from_profile,
    send_dm_safe,
    set_search_ui_mode,
    tap_account_result,
    type_dm_draft_only,
    type_search,
    verify_app_foreground,
    verify_dm_composer_safe,
    verify_dm_draft_text,
    verify_profile,
)
from logs import log

_DM_SENDER_GLOBAL_SEARCH_READY: dict[str, Any] = {}
_DM_SENDER_SESSION_ABORT_PERMISSION: bool = False

_TRUSTED_GLOBAL_SEARCH_CONTEXTS = frozenset(
    {
        "welcome_session_scan_to_sender",
        "dm_sender_post_job",
    }
)


def _resolve_reserved_by(d: u2.Device) -> str:
    cfg = str(getattr(config, "DM_SENDER_RESERVED_BY", "") or "").strip()
    if cfg:
        return cfg[:120]
    serial = get_device_serial(d)
    if serial:
        return str(serial)[:120]
    return f"worker-{os.getpid()}"[:120]


def _mark_dm_sender_global_search_ready(
    account_username: str,
    *,
    context: str,
) -> None:
    global _DM_SENDER_GLOBAL_SEARCH_READY
    _DM_SENDER_GLOBAL_SEARCH_READY = {
        "account_username": str(account_username or "").strip(),
        "at": time.perf_counter(),
        "context": str(context or ""),
    }


def _reset_dm_sender_session_abort() -> None:
    global _DM_SENDER_SESSION_ABORT_PERMISSION
    _DM_SENDER_SESSION_ABORT_PERMISSION = False


def _dm_sender_session_should_abort() -> bool:
    return bool(_DM_SENDER_SESSION_ABORT_PERMISSION)


def _dm_sender_trust_global_search_ready(account_username: str) -> bool:
    """Skip heavy re-verify when scan→sender or post-job prepare just marked search ready."""
    st = _DM_SENDER_GLOBAL_SEARCH_READY
    if not st:
        return False
    src = str(account_username or "").strip()
    if str(st.get("account_username") or "") != src:
        return False
    if str(st.get("context") or "") not in _TRUSTED_GLOBAL_SEARCH_CONTEXTS:
        return False
    max_age = float(
        getattr(config, "DM_SENDER_GLOBAL_SEARCH_TRUST_MAX_AGE_S", 120.0) or 120.0
    )
    return (time.perf_counter() - float(st.get("at") or 0.0)) < max_age


def _dm_sender_global_search_recently_verified(
    account_username: str,
    *,
    ttl_s: float | None = None,
) -> bool:
    st = _DM_SENDER_GLOBAL_SEARCH_READY
    if not st:
        return False
    if str(st.get("account_username") or "") != str(account_username or "").strip():
        return False
    ttl = float(
        ttl_s
        if ttl_s is not None
        else getattr(config, "DM_SENDER_GLOBAL_SEARCH_READY_TTL_S", 120.0) or 120.0
    )
    return (time.perf_counter() - float(st.get("at") or 0.0)) < ttl


def _check_dm_sender_permission_blocker(
    d: u2.Device,
    *,
    username: str = "",
    context: str = "",
) -> bool:
    """True if run must safe-fail (permission dialog foreground)."""
    hit, why = detect_unexpected_android_media_permission_dialog(d)
    if not hit:
        return False
    global _DM_SENDER_SESSION_ABORT_PERMISSION
    _DM_SENDER_SESSION_ABORT_PERMISSION = True
    log(
        "error",
        "dm_sender_unexpected_permission_dialog_detected",
        username=username or None,
        context=context,
        reason=why,
    )
    return True


def _verify_dm_sender_global_search_surface(
    d: u2.Device,
    *,
    pkg: str,
    account_username: str,
    full_followers_check: bool = False,
) -> tuple[bool, str]:
    """True when bottom-nav global Search is ready (not Followers-list local search)."""
    src = str(account_username or "").strip()
    if _check_dm_sender_permission_blocker(d, context="global_search_verify"):
        return False, "unexpected_permission_dialog"
    if full_followers_check:
        det_fl, _ = detect_followers_list_screen_fresh(d, source_profile_username=src)
        if bool(det_fl.get("is_followers_list")):
            return False, "followers_list_local_search_surface"
    elif is_followers_list_surface_quick(d, source_profile_username=src):
        return False, "followers_list_local_search_surface"
    if not is_lightweight_search_screen(d, pkg):
        return False, "not_global_search_screen"
    return True, "ok"


def _dm_sender_open_search(
    d: u2.Device,
    *,
    pkg: str,
    account_username: str,
    context: str,
    allow_percent_fallback: bool = True,
    block_if_dm_thread: bool = True,
    caller_context: str = "",
) -> bool:
    if _check_dm_sender_permission_blocker(d, context=context):
        return False
    return bool(
        open_search(
            d,
            source_profile_username=account_username,
            allow_percent_fallback=allow_percent_fallback,
            block_if_dm_thread=block_if_dm_thread,
            caller_context=caller_context or context,
        )
    )


def _log_followers_exit_observed(
    event: str,
    *,
    context: str,
    obs: dict[str, Any],
    back_step: int | None = None,
    tap_method: str | None = None,
) -> None:
    payload: dict[str, Any] = {
        "context": context,
        "followers_detected_fresh": bool(obs.get("followers_detected_fresh")),
        "current_package": obs.get("current_package"),
        "current_activity": obs.get("current_activity"),
        "action_bar_title": obs.get("action_bar_title"),
        "signals": obs.get("signals"),
        "own_unified_followers_list_detected": obs.get(
            "own_unified_followers_list_detected"
        ),
        "open_detection_method": obs.get("open_detection_method"),
        "relaxed_list_open": obs.get("relaxed_list_open"),
        "strict_list_open": obs.get("strict_list_open"),
        "followers_detect_hierarchy_source": obs.get("followers_detect_hierarchy_source"),
        "followers_detect_stale_cache_present": obs.get(
            "followers_detect_stale_cache_present"
        ),
        "screenshot_path": obs.get("screenshot_path"),
        "xml_path": obs.get("xml_path"),
        "fresh_hierarchy_len": obs.get("fresh_hierarchy_len"),
    }
    if back_step is not None:
        payload["back_step"] = int(back_step)
    if tap_method:
        payload["tap_method"] = tap_method
    log("info", event, **payload)


def _followers_still_detected_fresh(
    d: u2.Device,
    *,
    account_username: str,
    context: str,
    phase: str,
    back_step: int | None = None,
) -> bool:
    stem = f"dm_sender_followers_exit_post_{phase}"
    if back_step is not None:
        stem = f"{stem}_{int(back_step)}"
    obs = observe_followers_list_surface_fresh(
        d,
        source_profile_username=account_username,
        artifact_stem=stem,
    )
    event = (
        "dm_sender_followers_surface_exit_post_action_bar_observed"
        if phase == "action_bar"
        else "dm_sender_followers_surface_exit_post_hardware_back_observed"
    )
    _log_followers_exit_observed(
        event,
        context=context,
        obs=obs,
        back_step=back_step,
    )
    return bool(obs.get("followers_detected_fresh"))


def _exit_followers_list_surface_for_sender(
    d: u2.Device,
    *,
    account_username: str,
    context: str,
) -> bool:
    """Surface-aware exit from own/other Followers list before global Search."""
    pkg = str(getattr(config, "INSTAGRAM_PACKAGE", "") or "com.instagram.android")
    src = str(account_username or "").strip()

    det_start, _ = detect_followers_list_screen_fresh(d, source_profile_username=src)
    if not bool(det_start.get("is_followers_list")):
        return True

    log(
        "info",
        "dm_sender_followers_surface_exit_started",
        context=context,
        account_username=src or None,
        followers_detect_hierarchy_source=det_start.get(
            "followers_detect_hierarchy_source"
        ),
    )

    settle = float(getattr(config, "DM_SENDER_FOLLOWERS_EXIT_SETTLE_S", 0.45) or 0.45)
    tapped, tap_method = tap_instagram_action_bar_back_button(d, pkg)
    if tapped:
        log(
            "info",
            "dm_sender_followers_surface_exit_action_bar_back_tapped",
            context=context,
            tap_method=tap_method,
        )
        time.sleep(settle)
        if not _followers_still_detected_fresh(
            d, account_username=src, context=context, phase="action_bar"
        ):
            log(
                "info",
                "dm_sender_followers_surface_exit_action_bar_back_success",
                context=context,
                tap_method=tap_method,
            )
            return True
        log(
            "warning",
            "dm_sender_followers_surface_exit_action_bar_back_failed",
            context=context,
            tap_method=tap_method,
            reason="still_on_followers_list_fresh",
        )
    else:
        log(
            "warning",
            "dm_sender_followers_surface_exit_action_bar_back_failed",
            context=context,
            reason="action_bar_back_not_found",
        )

    log(
        "info",
        "dm_sender_followers_surface_exit_hardware_back_fallback_started",
        context=context,
    )
    hw_max = int(getattr(config, "DM_SENDER_FOLLOWERS_EXIT_HARDWARE_BACK_MAX", 3) or 3)
    for step in range(max(0, hw_max)):
        try:
            d.press("back")
        except Exception:
            pass
        time.sleep(settle)
        if not _followers_still_detected_fresh(
            d,
            account_username=src,
            context=context,
            phase="hardware_back",
            back_step=step + 1,
        ):
            log(
                "info",
                "dm_sender_followers_surface_exit_hardware_back_fallback_success",
                context=context,
                back_step=step + 1,
            )
            return True

    _followers_still_detected_fresh(
        d,
        account_username=src,
        context=context,
        phase="hardware_back",
        back_step=hw_max,
    )
    log(
        "error",
        "dm_sender_followers_surface_exit_hardware_back_fallback_failed",
        context=context,
        back_steps=hw_max,
    )
    return False


def prepare_dm_sender_global_search_surface(
    d: u2.Device,
    *,
    account_username: str,
    context: str,
    last_recipient_username: str = "",
) -> bool:
    """
    Leave Followers list / DM thread / profile and open verified global Instagram Search.
    Used between Welcome scan→sender and between consecutive sender jobs.
    """
    pkg = str(getattr(config, "INSTAGRAM_PACKAGE", "") or "com.instagram.android")
    src = str(account_username or "").strip()
    log(
        "info",
        "dm_sender_post_job_surface_prepare_started",
        context=context,
        account_username=src or None,
        last_recipient_username=last_recipient_username or None,
    )

    invalidate_search_surface_cache(f"dm_sender_prepare:{context}")

    try:
        if last_recipient_username and is_dm_thread_screen(d, pkg):
            return_to_profile_from_dm(d, last_recipient_username, pkg)
    except Exception as e:
        log(
            "warning",
            "dm_sender_prepare_exit_dm_failed",
            context=context,
            error=str(e)[:200],
        )

    det_pre, _ = detect_followers_list_screen_fresh(d, source_profile_username=src)
    if bool(det_pre.get("is_followers_list")):
        if not _exit_followers_list_surface_for_sender(
            d, account_username=src, context=context
        ):
            log(
                "error",
                "dm_sender_post_job_surface_prepare_failed",
                context=context,
                reason="still_on_followers_list_fresh",
                account_username=src or None,
            )
            return False

    dm_back_max = int(getattr(config, "DM_SENDER_SURFACE_PREPARE_DM_BACK_MAX", 3) or 3)
    for _ in range(dm_back_max):
        if is_dm_thread_screen(d, pkg):
            try:
                d.press("back")
            except Exception:
                pass
            time.sleep(0.2)
            continue
        break

    det_post_exit, _ = detect_followers_list_screen_fresh(d, source_profile_username=src)
    if bool(det_post_exit.get("is_followers_list")):
        log(
            "error",
            "dm_sender_post_job_surface_prepare_failed",
            context=context,
            reason="still_on_followers_list_fresh",
            account_username=src or None,
            action_bar_title=det_post_exit.get("action_bar_title"),
            signals=det_post_exit.get("signals"),
        )
        return False

    if _check_dm_sender_permission_blocker(d, context=context):
        log(
            "error",
            "dm_sender_post_job_surface_prepare_failed",
            context=context,
            reason="unexpected_permission_dialog",
            account_username=src or None,
        )
        return False

    from_dm = bool(last_recipient_username) and is_dm_thread_screen(d, pkg)
    if from_dm:
        log(
            "info",
            "dm_sender_post_job_exit_dm_before_search",
            context=context,
            recipient_username=last_recipient_username,
        )
        if not return_to_profile_from_dm(d, last_recipient_username, pkg):
            log(
                "error",
                "dm_sender_post_job_surface_prepare_failed",
                context=context,
                reason="dm_thread_exit_failed",
            )
            return False
        time.sleep(0.2)
        if is_dm_thread_screen(d, pkg):
            log(
                "error",
                "dm_sender_open_search_blocked_from_dm_thread",
                context=context,
                recipient_username=last_recipient_username,
            )
            log(
                "error",
                "dm_sender_post_job_surface_prepare_failed",
                context=context,
                reason="stuck_in_dm_thread",
            )
            return False

    allow_pct = not from_dm
    disable_post_job_percent_fallback = bool(
        getattr(config, "DM_SENDER_DISABLE_POST_JOB_PERCENT_FALLBACK", True)
    )
    if context == "dm_sender_post_job" and disable_post_job_percent_fallback:
        allow_pct = False
    log(
        "info",
        "dm_sender_post_job_search_guard_context",
        context=context,
        account_username=src or None,
        last_recipient_username=last_recipient_username or None,
        from_dm=bool(from_dm),
        post_job_percent_fallback_disabled=bool(
            context == "dm_sender_post_job" and disable_post_job_percent_fallback
        ),
        allow_percent_fallback=bool(allow_pct),
    )
    if not _dm_sender_open_search(
        d,
        pkg=pkg,
        account_username=src,
        context=context,
        allow_percent_fallback=allow_pct,
        block_if_dm_thread=True,
        caller_context=context,
    ):
        log(
            "error",
            "dm_sender_post_job_surface_prepare_failed",
            context=context,
            reason="open_search_failed",
            account_username=src or None,
        )
        return False

    verified, why = _verify_dm_sender_global_search_surface(
        d, pkg=pkg, account_username=src, full_followers_check=False
    )
    if not verified:
        log(
            "error",
            "dm_sender_post_job_surface_prepare_failed",
            context=context,
            reason=why,
            account_username=src or None,
        )
        return False

    _mark_dm_sender_global_search_ready(src, context=context)
    log(
        "info",
        "dm_sender_post_job_global_search_verified",
        context=context,
        account_username=src or None,
        verify_reason=why,
    )
    log(
        "info",
        "dm_sender_post_job_surface_prepare_done",
        context=context,
        account_username=src or None,
    )
    return True


def welcome_session_prepare_sender_surface(
    d: u2.Device,
    *,
    account_username: str,
) -> bool:
    """Scan phase ends on own Followers list — reset before DM sender claims jobs."""
    return prepare_dm_sender_global_search_surface(
        d,
        account_username=account_username,
        context="welcome_session_scan_to_sender",
    )


def _open_search_with_recovery(
    d: u2.Device,
    *,
    pkg: str,
    username: str,
    context: str,
    account_username: str = "",
    skip_if_recently_verified: bool = True,
) -> bool:
    src = str(account_username or "").strip()
    t0 = time.perf_counter()
    log(
        "info",
        "dm_sender_global_search_prepare_started",
        username=username,
        context=context,
        skip_if_recently_verified=bool(skip_if_recently_verified),
    )
    if _check_dm_sender_permission_blocker(d, username=username, context=context):
        return False

    if skip_if_recently_verified and _dm_sender_global_search_recently_verified(src):
        if _dm_sender_trust_global_search_ready(src):
            t_ed = time.perf_counter()
            ed = _wait_search_edittext(d)
            wait_ed_ms = round((time.perf_counter() - t_ed) * 1000.0, 2)
            log(
                "info",
                "dm_sender_global_search_prepare_skipped_recently_verified",
                username=username,
                context=context,
                verify_reason="trusted_mark_no_reverify",
                prepare_ms=round((time.perf_counter() - t0) * 1000.0, 2),
                wait_edittext_ms=wait_ed_ms,
                search_field_ready=bool(ed is not None),
                trust_skip_verify=True,
            )
            return bool(ed is not None)

        t_verify = time.perf_counter()
        ok_light, why = _verify_dm_sender_global_search_surface(
            d, pkg=pkg, account_username=src, full_followers_check=False
        )
        verify_ms = round((time.perf_counter() - t_verify) * 1000.0, 2)
        if ok_light:
            t_ed = time.perf_counter()
            ed = _wait_search_edittext(d)
            wait_ed_ms = round((time.perf_counter() - t_ed) * 1000.0, 2)
            log(
                "info",
                "dm_sender_global_search_prepare_skipped_recently_verified",
                username=username,
                context=context,
                verify_reason=why,
                prepare_ms=round((time.perf_counter() - t0) * 1000.0, 2),
                verify_ms=verify_ms,
                wait_edittext_ms=wait_ed_ms,
                search_field_ready=bool(ed is not None),
                trust_skip_verify=False,
            )
            return True

    invalidate_search_surface_cache(f"dm_sender_open_search:{context}")
    if _dm_sender_open_search(
        d,
        pkg=pkg,
        account_username=src,
        context=context,
        allow_percent_fallback=True,
        block_if_dm_thread=True,
        caller_context=context,
    ):
        ok, _why = _verify_dm_sender_global_search_surface(
            d, pkg=pkg, account_username=src, full_followers_check=False
        )
        if ok:
            _mark_dm_sender_global_search_ready(src, context=context)
            log(
                "info",
                "dm_sender_global_search_surface_verified",
                username=username,
                context=context,
                prepare_ms=round((time.perf_counter() - t0) * 1000.0, 2),
            )
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
    if not _dm_sender_open_search(
        d,
        pkg=pkg,
        account_username=src,
        context=f"{context}_recovery",
        allow_percent_fallback=False,
        block_if_dm_thread=True,
        caller_context=f"{context}_recovery",
    ):
        return False
    ok, _why = _verify_dm_sender_global_search_surface(
        d, pkg=pkg, account_username=src, full_followers_check=False
    )
    if ok:
        _mark_dm_sender_global_search_ready(src, context=context)
    return bool(ok)


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


def _evaluate_outreach_sendability(
    thread_state: str,
    settings: dict[str, Any],
) -> tuple[bool, str | None]:
    """Outreach-specific DM gate; keeps cold outreach separate from Welcome rules."""
    skip_existing = bool(settings.get("outreach_skip_if_existing_thread", True))

    if thread_state == "empty_new_thread":
        return True, None

    if thread_state == "existing_thread":
        if skip_existing:
            return False, "existing_thread"
        return True, None

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
    elif dm_type == "outreach":
        sendable, skip_reason_candidate = _evaluate_outreach_sendability(
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
    account_username: str = "",
) -> tuple[str, bool]:
    """
    Search → profile → DM thread. Returns (thread_state, navigation_ok).
    """
    uname = str(username or "").strip()
    src = str(account_username or "").strip()
    t_nav = time.perf_counter()
    log("info", "dm_sender_navigation_started", username=uname)

    if _check_dm_sender_permission_blocker(d, username=uname, context="navigation"):
        return "unknown", False

    if is_dm_thread_screen(d, pkg):
        if not return_to_profile_from_dm(d, uname, pkg):
            log("warning", "dm_sender_stuck_in_dm_thread", username=uname)
            return "unknown", False

    t_before_search = time.perf_counter()
    if _dm_sender_trust_global_search_ready(src):
        log(
            "info",
            "dm_sender_global_search_reuse_trusted",
            username=uname,
            context="dm_sender_navigate",
        )
        search_ok = True
    else:
        search_ok = _open_search_with_recovery(
            d,
            pkg=pkg,
            username=uname,
            context="dm_sender_navigate",
            account_username=src,
            skip_if_recently_verified=True,
        )
    if not search_ok:
        log("error", "dm_sender_open_search_failed", username=uname)
        return "unknown", False
    sender_prepare_to_open_search_ms = round(
        (time.perf_counter() - t_before_search) * 1000.0, 2
    )

    t_field = time.perf_counter()
    ed = _wait_search_edittext(d)
    log(
        "info",
        "dm_sender_search_field_ready",
        username=uname,
        search_field_ready=bool(ed is not None),
        open_search_to_field_ready_ms=round((time.perf_counter() - t_field) * 1000.0, 2),
    )

    t_type = time.perf_counter()
    log("info", "dm_sender_username_typing_started", username=uname)
    if not type_search(d, uname, previous_username=None):
        log("error", "dm_sender_type_search_failed", username=uname)
        return "unknown", False
    log(
        "info",
        "dm_sender_username_typed",
        username=uname,
        username_type_ms=round((time.perf_counter() - t_type) * 1000.0, 2),
        navigation_to_username_typed_total_ms=round(
            (time.perf_counter() - t_nav) * 1000.0, 2
        ),
        sender_prepare_to_open_search_ms=sender_prepare_to_open_search_ms,
    )

    if bool(getattr(config, "FAST_SKIP_ACCOUNTS_TAB", True)) and bool(
        getattr(config, "FAST_PATH_MODE", False)
    ):
        set_search_ui_mode("mixed_results")
    else:
        accounts_tab_clicked = open_accounts_tab(d)
        set_search_ui_mode("accounts_tab" if accounts_tab_clicked else "mixed_results")

    t_tap = time.perf_counter()
    if not tap_account_result(d, uname, nav_timing_origin=t_type):
        log("error", "dm_sender_tap_account_failed", username=uname)
        return "unknown", False
    tap_segment_ms = round((time.perf_counter() - t_tap) * 1000.0, 2)

    t_prof = time.perf_counter()
    if not verify_profile(d, uname):
        log("error", "dm_sender_profile_verify_failed", username=uname)
        return "unknown", False
    profile_verify_ms = round((time.perf_counter() - t_prof) * 1000.0, 2)

    log(
        "info",
        "dm_sender_profile_opened",
        username=uname,
        tap_segment_ms=tap_segment_ms,
        profile_verify_ms=profile_verify_ms,
        tap_to_profile_open_ms=tap_segment_ms + profile_verify_ms,
    )

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
    account_username: str = "",
) -> None:
    """Exit DM thread safely, then restore verified global Search (no percent-fallback from DM)."""
    if _check_dm_sender_permission_blocker(
        d, username=username, context="post_job_teardown"
    ):
        return
    prepare_dm_sender_global_search_surface(
        d,
        account_username=account_username,
        context="dm_sender_post_job",
        last_recipient_username=username,
    )


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
        if not supabase_client.is_valid_dm_job_row(job):
            return None
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
    if not supabase_client.is_valid_dm_job_row(job):
        return None
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
    account_username: str = "",
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
            d, recipient, pkg=pkg, account_username=account_username
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
            if dm_type == "outreach":
                sendable, skip_candidate = _evaluate_outreach_sendability(
                    thread_state, settings
                )
            elif dm_type != "welcome":
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
        _safe_teardown_navigation(
            d, recipient, pkg=pkg, account_username=account_username
        )

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


def _truthy_env(raw: str | None) -> bool:
    if raw is None:
        return False
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _resolve_dm_sender_real_send_enabled() -> tuple[bool, str]:
    env_raw = os.environ.get("DM_SENDER_REAL_SEND_ENABLED")
    if env_raw is not None:
        return _truthy_env(env_raw), "env"
    return bool(getattr(config, "DM_SENDER_REAL_SEND_ENABLED", False)), "config"


def _complete_job_skipped(
    job: dict[str, Any],
    *,
    skip_reason: str,
    thread_state: str,
) -> tuple[dict[str, Any] | None, str]:
    job_id = str(job.get("id") or "")
    row = supabase_client.complete_dm_job(
        job_id,
        "skipped",
        skip_reason=str(skip_reason),
        metadata_patch={
            "thread_state": thread_state,
            "recipient_username": str(job.get("recipient_username") or ""),
        },
    )
    log(
        "info",
        "dm_sender_job_completed_skipped",
        job_id=job_id,
        skip_reason=skip_reason,
        thread_state=thread_state,
        recipient_username=job.get("recipient_username"),
    )
    return row, "skipped"


def _complete_job_failed_retry(
    job: dict[str, Any],
    *,
    last_error: str,
    thread_state: str | None = None,
    metadata_patch: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, str]:
    job_id = str(job.get("id") or "")
    delay = int(getattr(config, "DM_SENDER_FAILED_RETRY_DELAY_SECONDS", 300) or 300)
    patch = dict(metadata_patch or {})
    if thread_state:
        patch["thread_state"] = thread_state
    row = supabase_client.complete_dm_job(
        job_id,
        "failed",
        last_error=str(last_error),
        increment_attempt=True,
        retry_delay_seconds=delay,
        metadata_patch=patch,
    )
    log(
        "info",
        "dm_sender_job_failed_retry_scheduled",
        job_id=job_id,
        last_error=last_error,
        retry_delay_seconds=delay,
        recipient_username=job.get("recipient_username"),
        thread_state=thread_state,
    )
    return row, "failed_retry"


def _dm_message_typing_flags(text: str) -> dict[str, Any]:
    raw = str(text or "")
    return {
        "message_len": len(raw),
        "contains_spaces": " " in raw,
        "contains_newlines": "\n" in raw or "\r" in raw,
        "contains_non_ascii": any(ord(c) > 127 for c in raw),
    }


def _select_dm_typing_strategy(flags: dict[str, Any]) -> str:
    if flags.get("contains_newlines"):
        return "set_text"
    if flags.get("contains_spaces") and int(flags.get("message_len") or 0) > 12:
        return "set_text"
    if flags.get("contains_non_ascii"):
        return "set_text"
    return "fast_ime"


def _dm_audit_non_text_action_candidates(d: u2.Device, *, caller: str) -> None:
    probes: list[tuple[str, Callable[[], Any]]] = (
        ("photo", lambda: d(descriptionContains="Photo")),
        ("gallery", lambda: d(descriptionContains="Gallery")),
        ("image", lambda: d(descriptionContains="Image")),
        ("media", lambda: d(descriptionContains="Media")),
        ("attachment", lambda: d(descriptionContains="Attach")),
        ("camera", lambda: d(descriptionContains="Camera")),
        ("microphone", lambda: d(descriptionContains="Microphone")),
        ("voice", lambda: d(descriptionContains="Voice")),
    )
    for label, factory in probes:
        try:
            o = factory()
            if not o.exists(timeout=0.04):
                continue
            info = o.info or {}
            log(
                "info",
                "dm_sender_dm_non_text_action_candidate_rejected",
                caller=caller,
                label=label,
                resource_id=str(info.get("resourceName") or "")[:120],
                content_desc=str(
                    info.get("contentDescription") or info.get("description") or ""
                )[:120],
                text=str(info.get("text") or "")[:80],
                bounds=info.get("bounds"),
            )
            if label in ("photo", "gallery", "image", "media", "attachment", "camera"):
                log(
                    "info",
                    "dm_sender_photo_gallery_action_rejected",
                    caller=caller,
                    label=label,
                    resource_id=str(info.get("resourceName") or "")[:120],
                    content_desc=str(
                        info.get("contentDescription") or info.get("description") or ""
                    )[:120],
                )
        except Exception:
            continue


def _resolve_dm_text_composer(
    d: u2.Device,
    *,
    pkg: str,
    username: str,
    caller: str,
) -> tuple[Any | None, str | None]:
    if _check_dm_sender_permission_blocker(d, username=username, context=caller):
        return None, "unexpected_permission_dialog"
    _dm_audit_non_text_action_candidates(d, caller=caller)
    ed = _dm_find_focus_composer(d)
    if ed is None:
        log(
            "error",
            "dm_sender_text_composer_not_resolved",
            username=username,
            caller=caller,
        )
        return None, "draft_composer_not_resolved"
    bounds = (ed.info or {}).get("bounds") if hasattr(ed, "info") else None
    log(
        "info",
        "dm_sender_text_composer_resolved",
        username=username,
        caller=caller,
        bounds=bounds,
    )
    log(
        "info",
        "dm_sender_text_composer_focus_started",
        username=username,
        caller=caller,
    )
    try:
        ed.click()
        time.sleep(0.05)
    except Exception as e:
        log(
            "error",
            "dm_sender_text_composer_focus_failed",
            username=username,
            caller=caller,
            error=str(e)[:200],
        )
        return None, "composer_focus_failed"
    ed2 = _dm_find_focus_composer(d) or ed
    log(
        "info",
        "dm_sender_text_composer_focus_verified",
        username=username,
        caller=caller,
    )
    return ed2, None


def _perform_real_welcome_dm_send(
    d: u2.Device,
    *,
    username: str,
    message_body: str,
    thread_state: str,
    pkg: str,
    post_send_nav: str = "search",
    source_profile_username: str = "",
) -> tuple[bool, dict[str, Any], str | None]:
    """
    Type job.message_body, verify draft, tap Send, post-send finalize.
    Returns (sent_ok, send_out, failure_reason).
    """
    uname = str(username or "").strip()
    draft_text = str(message_body or "")
    log(
        "info",
        "dm_sender_real_send_attempt_started",
        username=uname,
        thread_state=thread_state,
        message_len=len(draft_text),
    )

    if not draft_text.strip():
        return False, {}, "empty_message_body"

    flags = _dm_message_typing_flags(draft_text)
    strategy = _select_dm_typing_strategy(flags)
    log(
        "info",
        "dm_sender_typing_strategy_selected",
        username=uname,
        strategy=strategy,
        **flags,
    )

    _ed, focus_err = _resolve_dm_text_composer(
        d, pkg=pkg, username=uname, caller="real_send"
    )
    if focus_err:
        log(
            "error",
            "dm_sender_typing_strategy_failed",
            username=uname,
            strategy=strategy,
            failure_reason=focus_err,
            **flags,
        )
        return False, {}, focus_err

    ok_comp, comp_signal = verify_dm_composer_safe(d, pkg)
    if not ok_comp:
        log(
            "error",
            "dm_sender_real_send_blocked_composer",
            username=uname,
            composer_reason=comp_signal,
        )
        return False, {}, "composer_not_safe"

    if not bool(getattr(config, "DM_DRAFT_TYPING_ENABLED", True)):
        return False, {}, "draft_typing_disabled"

    force_method = "set_text" if strategy == "set_text" else None
    ok_type, type_info = type_dm_draft_only(
        d, draft_text, pkg, force_method=force_method
    )
    log(
        "info",
        "dm_sender_real_send_draft_typed",
        username=uname,
        draft_ok=bool(ok_type),
        strategy=strategy,
        type_info=str(type_info)[:200] if not isinstance(type_info, dict) else type_info.get("method"),
    )
    if not ok_type:
        fail_reason = "draft_typing_failed"
        if isinstance(type_info, dict):
            fail_reason = str(type_info.get("reason") or type_info.get("method") or fail_reason)
        elif isinstance(type_info, str):
            fail_reason = type_info
        log(
            "error",
            "dm_sender_typing_strategy_failed",
            username=uname,
            strategy=strategy,
            failure_reason=fail_reason,
            **flags,
        )
        if strategy == "fast_ime":
            log(
                "info",
                "dm_sender_typing_fallback_used",
                username=uname,
                from_strategy="fast_ime",
                to_strategy="set_text",
            )
            ok_type, type_info = type_dm_draft_only(
                d, draft_text, pkg, force_method="set_text"
            )
        if not ok_type:
            return False, {"type_info": type_info}, "draft_typing_failed"
    log(
        "info",
        "dm_sender_typing_completed",
        username=uname,
        strategy=strategy,
        **flags,
    )

    if bool(getattr(config, "DM_VERIFY_TYPED_TEXT", True)):
        if not verify_dm_draft_text(d, draft_text):
            return False, {}, "draft_verify_failed"

    prev_enable = bool(getattr(config, "ENABLE_REAL_DM_SEND", False))
    try:
        config.ENABLE_REAL_DM_SEND = True
        send_out = send_dm_safe(
            d,
            uname,
            draft_text,
            thread_state,
            target_row=None,
        )
    finally:
        config.ENABLE_REAL_DM_SEND = prev_enable

    if send_out.get("reason") == "send_button_missing":
        cleanup_dm_after_send_button_missing(d, pkg)

    if bool(send_out.get("sent")):
        log(
            "info",
            "dm_sender_real_send_button_tapped",
            username=uname,
            thread_state=thread_state,
        )
        log(
            "info",
            "dm_sender_real_send_verified",
            username=uname,
            thread_state=thread_state,
            message_len=len(draft_text),
        )
        if post_send_nav == "welcome_list":
            from instagram_navigation import return_welcome_list_from_dm_to_followers

            fin = return_welcome_list_from_dm_to_followers(
                d,
                uname,
                pkg,
                source_profile_username=source_profile_username,
                pre_send_composer_text_len=int(
                    send_out.get("composer_text_len_before_send") or 0
                ),
            )
            send_out["post_finalize"] = fin
            if not bool(fin.get("followers_surface_ok")):
                return True, send_out, "post_finalize_partial"
            return True, send_out, None

        fin = finalize_after_real_send(
            d,
            uname,
            pkg,
            use_fast_reset_between_targets=False,
            pre_send_composer_text_len=int(
                send_out.get("composer_text_len_before_send") or 0
            ),
            restore_global_search=False,
        )
        send_out["post_finalize"] = fin
        nav_ok = bool(fin.get("back_to_profile_ok"))
        if not nav_ok:
            return True, send_out, "post_finalize_partial"
        return True, send_out, None

    blocked = send_out.get("blocked_event")
    reason = send_out.get("reason") or blocked or "send_not_sent"
    log(
        "warning",
        "dm_sender_real_send_not_sent",
        username=uname,
        thread_state=thread_state,
        blocked_event=blocked,
        reason=reason,
    )
    return False, send_out, str(reason)


def execute_dm_job_real_send(
    d: u2.Device,
    job: dict[str, Any],
    *,
    settings: dict[str, Any],
    account_id: str,
    account_username: str = "",
) -> dict[str, Any]:
    """Claimed job → navigate → send or skip/fail terminal complete."""
    _ = account_id
    job_id = str(job.get("id") or "")
    recipient = str(job.get("recipient_username") or "").strip()
    dm_type = str(job.get("dm_type") or "")
    message_body = str(job.get("message_body") or "")
    pkg = str(getattr(config, "INSTAGRAM_PACKAGE", "") or "com.instagram.android")

    running = supabase_client.mark_dm_job_running(job_id)
    if not running:
        log("error", "dm_sender_mark_running_failed", job_id=job_id)
        _complete_job_failed_retry(
            job, last_error="mark_dm_job_running_failed", thread_state=None
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
            d, recipient, pkg=pkg, account_username=account_username
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
            updated_job, outcome = _complete_job_failed_retry(
                job,
                last_error="navigation_failed",
                thread_state=thread_state,
            )
            final_status = str((updated_job or {}).get("status") or "pending")
        else:
            sendable, skip_candidate = _evaluate_welcome_sendability(
                thread_state, settings
            )
            if dm_type == "outreach":
                sendable, skip_candidate = _evaluate_outreach_sendability(
                    thread_state, settings
                )
            elif dm_type != "welcome":
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
            elif not sendable:
                skip_reason = str(skip_candidate or thread_state or "not_sendable")
                updated_job, outcome = _complete_job_skipped(
                    job, skip_reason=skip_reason, thread_state=thread_state
                )
                final_status = str((updated_job or {}).get("status") or "skipped")
            elif thread_state in ("unknown", "composer_visible_uncertain"):
                updated_job, outcome = _complete_job_failed_retry(
                    job,
                    last_error=f"thread_state_{thread_state}",
                    thread_state=thread_state,
                )
                final_status = str((updated_job or {}).get("status") or "pending")
            else:
                sent_ok, send_out, fail_reason = _perform_real_welcome_dm_send(
                    d,
                    username=recipient,
                    message_body=message_body,
                    thread_state=thread_state,
                    pkg=pkg,
                )
                if sent_ok and fail_reason in (None, "post_finalize_partial"):
                    send_method = "instagram_send_ui"
                    if send_out.get("coordinate_fallback_used"):
                        send_method = "coordinate_fallback"
                    updated_job = supabase_client.complete_dm_job(
                        job_id,
                        "sent",
                        metadata_patch={
                            "thread_state": thread_state,
                            "send_method": send_method,
                            "message_len": len(message_body),
                            "post_finalize_partial": fail_reason == "post_finalize_partial",
                        },
                    )
                    outcome = "sent"
                    final_status = str((updated_job or {}).get("status") or "sent")
                    log(
                        "info",
                        "dm_sender_job_completed_sent",
                        job_id=job_id,
                        recipient_username=recipient,
                        thread_state=thread_state,
                        send_method=send_method,
                        final_job_status=final_status,
                    )
                else:
                    updated_job, outcome = _complete_job_failed_retry(
                        job,
                        last_error=str(fail_reason or "real_send_failed"),
                        thread_state=thread_state,
                        metadata_patch={"send_out": {k: send_out.get(k) for k in (
                            "sent",
                            "reason",
                            "blocked_event",
                            "failure_event",
                            "precheck_ok",
                        )}},
                    )
                    final_status = str((updated_job or {}).get("status") or "pending")
    finally:
        _safe_teardown_navigation(
            d, recipient, pkg=pkg, account_username=account_username
        )

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


def run_dm_sender_send(
    d: u2.Device,
    *,
    account_id: str,
    account_username: str = "",
    run_id: str | None = None,
    max_jobs: int | None = None,
    dm_type: str | None = None,
) -> tuple[int, dict[str, Any]]:
    """
    Real Welcome DM send: claim → navigate → type job.message_body → send → complete.
    Returns (exit_code, summary_dict).
    """
    t0 = time.perf_counter()
    aid = str(account_id or "").strip()
    acct_user = str(account_username or "").strip()
    dm_type_resolved = str(
        dm_type or getattr(config, "DM_SENDER_DEFAULT_DM_TYPE", "welcome") or "welcome"
    )
    if max_jobs is None:
        max_jobs = int(getattr(config, "WELCOME_SESSION_SEND_MAX_JOBS", 3) or 3)
    max_jobs = max(0, int(max_jobs))

    real_enabled, real_source = _resolve_dm_sender_real_send_enabled()
    reserved_by = _resolve_reserved_by(d)
    only_job_id, filter_source = _resolve_dm_sender_only_job_id()

    summary: dict[str, Any] = {
        "account_id": aid,
        "run_id": run_id,
        "dm_type": dm_type_resolved,
        "max_jobs": max_jobs,
        "real_send_enabled": real_enabled,
        "real_send_source": real_source,
        "filter_source": filter_source,
        "only_job_id": only_job_id or None,
        "jobs_claimed_count": 0,
        "jobs_sent_count": 0,
        "jobs_skipped_count": 0,
        "jobs_failed_count": 0,
        "existing_thread_skips_count": 0,
        "sendability_failures_count": 0,
        "processed_recipients": [],
        "sent_recipients": [],
        "skipped_recipients": [],
        "failed_recipients": [],
        "sender_status": "not_started",
    }

    log(
        "info",
        "dm_sender_send_started",
        account_id=aid,
        run_id=run_id,
        dm_type=dm_type_resolved,
        max_jobs=max_jobs,
        reserved_by=reserved_by,
        real_send_enabled=real_enabled,
        real_send_source=real_source,
    )
    log(
        "info",
        "dm_sender_job_filter_resolved",
        only_job_id=only_job_id or None,
        filter_source=filter_source,
    )

    if not real_enabled:
        log(
            "error",
            "dm_sender_real_send_blocked_disabled",
            account_id=aid,
            run_id=run_id,
            real_send_source=real_source,
        )
        summary["sender_status"] = "blocked_disabled"
        summary["total_ms"] = round((time.perf_counter() - t0) * 1000.0, 2)
        return 1, summary

    try:
        settings = supabase_client.get_account_dm_settings(aid) or {}
    except Exception as e:
        log("error", "dm_sender_settings_load_failed", error=str(e))
        settings = {}

    last_result: dict[str, Any] = {}
    for _ in range(max_jobs):
        job = _claim_job_for_run(
            aid, reserved_by, dm_type=dm_type_resolved, only_job_id=only_job_id
        )
        if not job:
            log("info", "dm_sender_no_pending_job", account_id=aid, dm_type=dm_type_resolved)
            break

        summary["jobs_claimed_count"] += 1
        recipient = str(job.get("recipient_username") or "").strip()
        summary["processed_recipients"].append(recipient)

        last_result = execute_dm_job_real_send(
            d,
            job,
            settings=settings,
            account_id=aid,
            account_username=acct_user,
        )
        outcome = str(last_result.get("outcome") or "")
        if outcome == "sent":
            summary["jobs_sent_count"] += 1
            summary["sent_recipients"].append(recipient)
        elif outcome == "skipped":
            summary["jobs_skipped_count"] += 1
            summary["skipped_recipients"].append(recipient)
            if str(last_result.get("thread_state") or "") == "existing_thread":
                summary["existing_thread_skips_count"] += 1
            if not bool(last_result.get("sendable")):
                summary["sendability_failures_count"] += 1
        elif outcome == "failed_retry":
            summary["jobs_failed_count"] += 1
            summary["failed_recipients"].append(recipient)

        if _dm_sender_session_should_abort():
            log(
                "error",
                "dm_sender_session_aborted_permission_dialog",
                account_id=aid,
                run_id=run_id,
                last_recipient=recipient,
            )
            break

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

    summary["sender_status"] = sender_status
    summary["total_ms"] = round(total_ms, 2)
    summary["last_recipient_username"] = last_result.get("recipient_username")
    summary["last_thread_state"] = last_result.get("thread_state")
    summary["last_outcome"] = last_result.get("outcome")

    log("info", "dm_sender_send_summary", **summary)
    return exit_code, summary


def dispatch_dm_sender_send(
    d: u2.Device,
    *,
    account_id: str,
    run_id: str | None = None,
    max_jobs: int | None = None,
) -> int:
    log(
        "info",
        "dm_sender_send_dispatch",
        account_id=account_id,
        run_id=run_id,
        max_jobs=max_jobs,
    )
    code, _ = run_dm_sender_send(
        d, account_id=account_id, run_id=run_id, max_jobs=max_jobs
    )
    return code
