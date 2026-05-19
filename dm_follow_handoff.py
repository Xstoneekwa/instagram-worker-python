"""
Welcome DM → Follow transition layer (account_session only).

Prepares device/session state after Welcome list-native DM and before the
followers list engine runs. Does not modify Welcome or Follow internals.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import uiautomator2 as u2

import config
from instagram_navigation import (
    clear_follow_ct_search_context,
    detect_followers_list_screen_fresh,
    followers_clear_detect_hierarchy_cache,
    invalidate_search_surface_cache,
    is_dm_thread_screen,
    reset_dm_send_run_state,
    reset_dm_thread_probe_state,
    return_welcome_list_from_dm_to_followers,
    tap_instagram_action_bar_back_button,
    verify_app_foreground,
)
from logs import log


@dataclass
class HandoffResult:
    ok: bool
    reason: str
    surface_label: str = ""
    prepare_ms: float = 0.0
    dm_thread_recovered: bool = False
    followers_surface_ok: bool = False
    resets_applied: list[str] = field(default_factory=list)


def _last_welcome_dm_recipient(sender_summary: dict[str, Any] | None) -> str:
    if not isinstance(sender_summary, dict):
        return ""
    for key in ("sent_recipients", "processed_recipients", "recipients_sent"):
        items = sender_summary.get(key) or []
        if isinstance(items, list) and items:
            return str(items[-1] or "").strip()
    return ""


def _apply_handoff_state_resets() -> list[str]:
    applied: list[str] = []
    try:
        clear_follow_ct_search_context()
        applied.append("clear_follow_ct_search_context")
    except Exception:
        pass
    try:
        invalidate_search_surface_cache("dm_follow_handoff")
        applied.append("invalidate_search_surface_cache")
    except Exception:
        pass
    try:
        reset_dm_thread_probe_state()
        applied.append("reset_dm_thread_probe_state")
    except Exception:
        pass
    try:
        reset_dm_send_run_state()
        applied.append("reset_dm_send_run_state")
    except Exception:
        pass
    try:
        followers_clear_detect_hierarchy_cache()
        applied.append("followers_clear_detect_hierarchy_cache")
    except Exception:
        pass
    return applied


def _recover_surface_toward_own_followers(
    d: u2.Device,
    *,
    pkg: str,
    account_username: str,
    last_dm_recipient: str,
) -> tuple[bool, bool, str]:
    """
    Return (dm_thread_recovered, followers_surface_ok, surface_label).
    """
    src = str(account_username or "").strip()
    recipient = str(last_dm_recipient or "").strip()

    dm_recovered = False

    if is_dm_thread_screen(d, pkg):
        if recipient:
            fin = return_welcome_list_from_dm_to_followers(
                d,
                recipient,
                pkg,
                source_profile_username=src,
            )
            dm_recovered = True
            followers_ok = bool(fin.get("followers_surface_ok"))
            if followers_ok:
                return dm_recovered, True, "own_followers_list_via_dm_return"
        else:
            log(
                "warning",
                "dm_follow_handoff_dm_thread_no_recipient",
                account_username=src,
                message="Falling back to action-bar backs without recipient username.",
            )

    det, _ = detect_followers_list_screen_fresh(d, source_profile_username=src)
    if bool(det.get("is_followers_list")):
        return False, True, "own_followers_list"

    settle_s = float(
        getattr(config, "DM_FOLLOW_HANDOFF_BACK_SETTLE_S", 0.45) or 0.45
    )
    max_steps = int(getattr(config, "DM_FOLLOW_HANDOFF_MAX_BACK_STEPS", 3) or 3)
    max_steps = max(1, min(5, max_steps))

    for step in range(max_steps):
        if is_dm_thread_screen(d, pkg):
            dm_recovered = True
        det, _ = detect_followers_list_screen_fresh(d, source_profile_username=src)
        if bool(det.get("is_followers_list")):
            log(
                "info",
                "dm_follow_handoff_followers_surface_restored",
                account_username=src,
                via=f"action_bar_back_step_{step}",
            )
            return dm_recovered, True, "own_followers_list_restored"
        tapped, _ = tap_instagram_action_bar_back_button(d, pkg)
        if tapped and settle_s > 0:
            time.sleep(settle_s)

    if is_dm_thread_screen(d, pkg):
        return dm_recovered, False, "dm_thread_stuck"

    det2, _ = detect_followers_list_screen_fresh(d, source_profile_username=src)
    if bool(det2.get("is_followers_list")):
        return dm_recovered, True, "own_followers_list_after_backs"

    return dm_recovered, False, "surface_not_own_followers_list"


def prepare_dm_to_follow_handoff(
    d: u2.Device,
    *,
    account_username: str,
    source_profile_username: str,
    welcome_phase_executed: bool = False,
    sender_summary: dict[str, Any] | None = None,
) -> HandoffResult:
    """
    Reset shared caches/context and ensure IG is usable for Follow engine entry.
    Called from account_session only, immediately before _run_followers_list_engine_session.
    """
    t0 = time.perf_counter()
    acct = str(account_username or "").strip()
    src = str(source_profile_username or "").strip()
    pkg = str(getattr(config, "INSTAGRAM_PACKAGE", "") or "com.instagram.android")
    last_recipient = _last_welcome_dm_recipient(sender_summary)

    log(
        "info",
        "dm_follow_handoff_started",
        account_username=acct,
        source_profile_username=src,
        welcome_phase_executed=bool(welcome_phase_executed),
        last_welcome_dm_recipient=last_recipient or None,
    )

    if not verify_app_foreground(d, pkg):
        prepare_ms = (time.perf_counter() - t0) * 1000.0
        log(
            "error",
            "dm_follow_handoff_failed",
            reason="instagram_not_foreground",
            prepare_ms=round(prepare_ms, 2),
        )
        return HandoffResult(
            ok=False,
            reason="handoff_instagram_not_foreground",
            surface_label="not_foreground",
            prepare_ms=prepare_ms,
        )

    resets = _apply_handoff_state_resets()

    dm_recovered, followers_ok, surface_label = _recover_surface_toward_own_followers(
        d,
        pkg=pkg,
        account_username=acct,
        last_dm_recipient=last_recipient,
    )

    if is_dm_thread_screen(d, pkg):
        prepare_ms = (time.perf_counter() - t0) * 1000.0
        log(
            "error",
            "dm_follow_handoff_failed",
            reason="handoff_dm_thread_stuck",
            surface_label=surface_label,
            dm_thread_recovered=dm_recovered,
            followers_surface_ok=followers_ok,
            prepare_ms=round(prepare_ms, 2),
            resets_applied=resets,
        )
        return HandoffResult(
            ok=False,
            reason="handoff_dm_thread_stuck",
            surface_label=surface_label,
            prepare_ms=prepare_ms,
            dm_thread_recovered=dm_recovered,
            followers_surface_ok=followers_ok,
            resets_applied=resets,
        )

    if welcome_phase_executed and not followers_ok:
        prepare_ms = (time.perf_counter() - t0) * 1000.0
        log(
            "error",
            "dm_follow_handoff_failed",
            reason="handoff_welcome_followers_surface_missing",
            surface_label=surface_label,
            dm_thread_recovered=dm_recovered,
            prepare_ms=round(prepare_ms, 2),
            resets_applied=resets,
        )
        return HandoffResult(
            ok=False,
            reason="handoff_welcome_followers_surface_missing",
            surface_label=surface_label,
            prepare_ms=prepare_ms,
            dm_thread_recovered=dm_recovered,
            followers_surface_ok=False,
            resets_applied=resets,
        )

    prepare_ms = (time.perf_counter() - t0) * 1000.0
    log(
        "info",
        "dm_follow_handoff_prepared",
        account_username=acct,
        source_profile_username=src,
        surface_label=surface_label,
        welcome_phase_executed=bool(welcome_phase_executed),
        dm_thread_recovered=dm_recovered,
        followers_surface_ok=followers_ok,
        prepare_ms=round(prepare_ms, 2),
        resets_applied=resets,
        ready_for_follow=True,
    )
    return HandoffResult(
        ok=True,
        reason="handoff_ready_for_follow_engine",
        surface_label=surface_label,
        prepare_ms=prepare_ms,
        dm_thread_recovered=dm_recovered,
        followers_surface_ok=followers_ok,
        resets_applied=resets,
    )
