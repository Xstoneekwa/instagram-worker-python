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
    get_perf_snapshot,
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
_LAST_DM_SENDER_NAV_TIMINGS: dict[str, float] = {}

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


def _reset_dm_sender_nav_timings() -> None:
    global _LAST_DM_SENDER_NAV_TIMINGS
    _LAST_DM_SENDER_NAV_TIMINGS = {
        "navigation_ms": 0.0,
        "search_ms": 0.0,
        "thread_open_ms": 0.0,
        "parent_search_ready_fast_path_attempted": False,
        "parent_search_ready_fast_path_used": False,
        "parent_search_ready_fast_path_reject_reason": "",
        "search_surface_age_ms": None,
        "sender_prepare_reused_search_surface": False,
        "sender_prepare_lightweight_verify_ms": 0.0,
        "sender_prepare_full_open_search_ms": 0.0,
        "fast_path_total_verify_ms": 0.0,
        "fast_path_foreground_check_ms": 0.0,
        "fast_path_no_dm_thread_check_ms": 0.0,
        "fast_path_search_surface_check_ms": 0.0,
        "fast_path_edittext_check_ms": 0.0,
        "fast_path_direct_edittext_probe_ms": 0.0,
        "fast_path_waits_count": 0,
        "fast_path_timeout_reason": "",
        "fast_path_mode": "",
        "fast_path_parent_proof_used": False,
        "typing_precheck_edittext_reused": False,
    }


def _set_dm_sender_nav_timings(
    *,
    navigation_ms: float = 0.0,
    search_ms: float = 0.0,
    thread_open_ms: float = 0.0,
    parent_search_ready_fast_path_attempted: bool = False,
    parent_search_ready_fast_path_used: bool = False,
    parent_search_ready_fast_path_reject_reason: str = "",
    search_surface_age_ms: float | None = None,
    sender_prepare_reused_search_surface: bool = False,
    sender_prepare_lightweight_verify_ms: float = 0.0,
    sender_prepare_full_open_search_ms: float = 0.0,
    fast_path_total_verify_ms: float = 0.0,
    fast_path_foreground_check_ms: float = 0.0,
    fast_path_no_dm_thread_check_ms: float = 0.0,
    fast_path_search_surface_check_ms: float = 0.0,
    fast_path_edittext_check_ms: float = 0.0,
    fast_path_direct_edittext_probe_ms: float = 0.0,
    fast_path_waits_count: int = 0,
    fast_path_timeout_reason: str = "",
    fast_path_mode: str = "",
    fast_path_parent_proof_used: bool = False,
    typing_precheck_edittext_reused: bool = False,
) -> None:
    global _LAST_DM_SENDER_NAV_TIMINGS
    _LAST_DM_SENDER_NAV_TIMINGS = {
        "navigation_ms": round(max(0.0, float(navigation_ms or 0.0)), 2),
        "search_ms": round(max(0.0, float(search_ms or 0.0)), 2),
        "thread_open_ms": round(max(0.0, float(thread_open_ms or 0.0)), 2),
        "parent_search_ready_fast_path_attempted": bool(parent_search_ready_fast_path_attempted),
        "parent_search_ready_fast_path_used": bool(parent_search_ready_fast_path_used),
        "parent_search_ready_fast_path_reject_reason": str(
            parent_search_ready_fast_path_reject_reason or ""
        ),
        "search_surface_age_ms": (
            round(max(0.0, float(search_surface_age_ms)), 2)
            if search_surface_age_ms is not None
            else None
        ),
        "sender_prepare_reused_search_surface": bool(sender_prepare_reused_search_surface),
        "sender_prepare_lightweight_verify_ms": round(
            max(0.0, float(sender_prepare_lightweight_verify_ms or 0.0)), 2
        ),
        "sender_prepare_full_open_search_ms": round(
            max(0.0, float(sender_prepare_full_open_search_ms or 0.0)), 2
        ),
        "fast_path_total_verify_ms": round(
            max(0.0, float(fast_path_total_verify_ms or 0.0)), 2
        ),
        "fast_path_foreground_check_ms": round(
            max(0.0, float(fast_path_foreground_check_ms or 0.0)), 2
        ),
        "fast_path_no_dm_thread_check_ms": round(
            max(0.0, float(fast_path_no_dm_thread_check_ms or 0.0)), 2
        ),
        "fast_path_search_surface_check_ms": round(
            max(0.0, float(fast_path_search_surface_check_ms or 0.0)), 2
        ),
        "fast_path_edittext_check_ms": round(
            max(0.0, float(fast_path_edittext_check_ms or 0.0)), 2
        ),
        "fast_path_direct_edittext_probe_ms": round(
            max(0.0, float(fast_path_direct_edittext_probe_ms or 0.0)), 2
        ),
        "fast_path_waits_count": max(0, int(fast_path_waits_count or 0)),
        "fast_path_timeout_reason": str(fast_path_timeout_reason or ""),
        "fast_path_mode": str(fast_path_mode or ""),
        "fast_path_parent_proof_used": bool(fast_path_parent_proof_used),
        "typing_precheck_edittext_reused": bool(typing_precheck_edittext_reused),
    }


def _get_dm_sender_nav_timings() -> dict[str, float]:
    return dict(_LAST_DM_SENDER_NAV_TIMINGS)


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


def _outreach_trust_parent_search_ready_enabled() -> bool:
    return str(os.getenv("OUTREACH_TRUST_PARENT_SEARCH_READY", "")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _parent_search_ready_max_age_ms() -> float:
    raw = str(os.getenv("OUTREACH_PARENT_SEARCH_READY_MAX_AGE_MS", "20000") or "20000")
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 20000.0


def _parent_search_ready_fast_verify_max_ms() -> float:
    raw = str(
        os.getenv("OUTREACH_PARENT_SEARCH_READY_FAST_VERIFY_MAX_MS", "1500") or "1500"
    )
    try:
        return max(250.0, float(raw))
    except ValueError:
        return 1500.0


def _parent_search_ready_fast_path_mode() -> str:
    raw = str(os.getenv("OUTREACH_PARENT_SEARCH_READY_FAST_PATH_MODE", "v3") or "v3")
    mode = raw.strip().lower()
    return mode if mode in {"v3"} else "v3"


def _parent_search_ready_age_ms(parent_search_ready: dict[str, Any]) -> float | None:
    raw = parent_search_ready.get("verified_at_monotonic")
    try:
        return max(0.0, (time.perf_counter() - float(raw)) * 1000.0)
    except (TypeError, ValueError):
        return None


def _bounded_wait_timeout_s(deadline: float, cap_s: float) -> float:
    remaining_s = max(0.0, deadline - time.perf_counter())
    return max(0.0, min(cap_s, remaining_s))


def _fast_path_no_dm_thread_visible_bounded(
    d: u2.Device,
    *,
    pkg: str,
    deadline: float,
) -> tuple[bool, str]:
    if time.perf_counter() >= deadline:
        return False, "fast_verify_timeout"
    try:
        cur = d.app_current()
        if (cur or {}).get("package", "") != pkg:
            return False, "instagram_not_foreground"
    except Exception:
        return False, "foreground_check_failed"
    if time.perf_counter() >= deadline:
        return False, "fast_verify_timeout"
    try:
        _w, h = d.window_size()
    except Exception:
        return False, "window_size_failed"
    if time.perf_counter() >= deadline:
        return False, "fast_verify_timeout"
    try:
        composer = d(resourceId=f"{pkg}:id/row_thread_composer_edittext")
        timeout_s = _bounded_wait_timeout_s(deadline, 0.08)
        if timeout_s <= 0.0:
            return False, "fast_verify_timeout"
        if composer.exists(timeout=timeout_s):
            try:
                b = (composer.info or {}).get("bounds") or {}
                if int(b.get("top", 0)) > h * 0.25:
                    return False, "dm_thread_visible"
            except Exception:
                return False, "dm_thread_visible"
    except Exception:
        return False, "dm_thread_probe_failed"
    return True, "ok"


def _fast_path_followers_list_visible_bounded(
    d: u2.Device,
    *,
    deadline: float,
) -> tuple[bool, str]:
    if time.perf_counter() >= deadline:
        return False, "fast_verify_timeout"
    try:
        tab = d(resourceIdMatches=r".*:id/unified_follow_list_tab_layout$")
        rows = d(resourceIdMatches=r".*:id/follow_list_username$")
        tab_timeout_s = _bounded_wait_timeout_s(deadline, 0.04)
        if tab_timeout_s <= 0.0:
            return False, "fast_verify_timeout"
        tab_visible = tab.exists(timeout=tab_timeout_s)
        row_timeout_s = _bounded_wait_timeout_s(deadline, 0.04)
        if row_timeout_s <= 0.0:
            return False, "fast_verify_timeout"
        rows_visible = rows.exists(timeout=row_timeout_s)
        return bool(tab_visible and rows_visible), "ok"
    except Exception:
        return False, "followers_list_probe_failed"


def _fast_path_search_surface_bounded(
    d: u2.Device,
    *,
    pkg: str,
    deadline: float,
) -> tuple[bool, str, Any | None]:
    if time.perf_counter() >= deadline:
        return False, "fast_verify_timeout", None
    try:
        cur = d.app_current()
        if (cur or {}).get("package", "") != pkg:
            return False, "instagram_not_foreground", None
    except Exception:
        return False, "foreground_check_failed", None
    if _check_dm_sender_permission_blocker(d, context="parent_search_ready_fast_path"):
        return False, "permission_blocker", None
    if time.perf_counter() >= deadline:
        return False, "fast_verify_timeout", None
    followers_visible, followers_reason = _fast_path_followers_list_visible_bounded(
        d, deadline=deadline
    )
    if followers_reason == "fast_verify_timeout":
        return False, followers_reason, None
    if followers_visible:
        return False, "followers_list_local_search_surface", None
    if time.perf_counter() >= deadline:
        return False, "fast_verify_timeout", None
    try:
        ed = d(className="android.widget.EditText")
        timeout_s = _bounded_wait_timeout_s(deadline, 0.20)
        if timeout_s <= 0.0:
            return False, "fast_verify_timeout", None
        if not ed.wait(timeout=timeout_s):
            return False, "search_edittext_not_ready", None
        try:
            b = (ed.info or {}).get("bounds") or {}
            _w, h = d.window_size()
            if int(b.get("bottom", 0)) > int(h * 0.38):
                return False, "search_edittext_not_top_band", None
        except Exception:
            pass
        return True, "ok", ed
    except Exception:
        return False, "search_surface_probe_failed", None


def _fast_path_direct_search_edittext_probe_v3(
    d: u2.Device,
    *,
    pkg: str,
    deadline: float,
) -> tuple[bool, str, Any | None]:
    """Parent-proof guard: only confirm a concrete top-band Search EditText."""
    if time.perf_counter() >= deadline:
        return False, "fast_verify_timeout", None

    def _validate_edittext(candidate: Any, source: str) -> tuple[bool, str, Any | None]:
        if time.perf_counter() >= deadline:
            return False, "fast_verify_timeout", None
        try:
            info = candidate.info or {}
        except Exception:
            return False, f"{source}_info_unavailable", None
        class_name = str(info.get("className") or info.get("class") or "")
        if class_name and "EditText" not in class_name:
            return False, f"{source}_not_edittext", None
        if info.get("enabled") is False:
            return False, f"{source}_disabled", None
        bounds = info.get("bounds") or {}
        try:
            _w, h = d.window_size()
            bottom = int(bounds.get("bottom", 0))
            top = int(bounds.get("top", 0))
            if bottom <= 0 or bottom > int(h * 0.38):
                return False, f"{source}_not_top_band", None
            if top < 0:
                return False, f"{source}_invalid_bounds", None
        except Exception:
            return False, f"{source}_bounds_unavailable", None
        return True, "ok", candidate

    exact_rids = (
        f"{pkg}:id/action_bar_search_edit_text",
        "com.instagram.android:id/action_bar_search_edit_text",
        f"{pkg}:id/row_search_edit_text",
        "com.instagram.android:id/row_search_edit_text",
    )
    for rid in exact_rids:
        if time.perf_counter() >= deadline:
            return False, "fast_verify_timeout", None
        try:
            candidate = d(resourceId=rid)
            timeout_s = _bounded_wait_timeout_s(deadline, 0.08)
            if timeout_s <= 0.0:
                return False, "fast_verify_timeout", None
            if not candidate.wait(timeout=timeout_s):
                continue
            ok, reason, ed = _validate_edittext(candidate, "rid")
            if ok:
                return True, "ok", ed
            return False, reason, None
        except Exception:
            continue

    if time.perf_counter() >= deadline:
        return False, "fast_verify_timeout", None
    try:
        candidate = d(className="android.widget.EditText")
        timeout_s = _bounded_wait_timeout_s(deadline, 0.12)
        if timeout_s <= 0.0:
            return False, "fast_verify_timeout", None
        if not candidate.wait(timeout=timeout_s):
            return False, "search_edittext_not_ready", None
        return _validate_edittext(candidate, "class")
    except Exception:
        return False, "direct_edittext_probe_failed", None


def _try_parent_search_ready_fast_path(
    d: u2.Device,
    *,
    pkg: str,
    username: str,
    account_id: str,
    account_username: str,
    run_id: str | None,
    dm_type: str,
    parent_search_ready: dict[str, Any] | None,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    signal = dict(parent_search_ready or {})
    age_ms = _parent_search_ready_age_ms(signal) if signal else None
    max_verify_ms = _parent_search_ready_fast_verify_max_ms()
    deadline = t0 + (max_verify_ms / 1000.0)
    fast_path_mode = _parent_search_ready_fast_path_mode()
    out: dict[str, Any] = {
        "attempted": True,
        "used": False,
        "reject_reason": "",
        "search_surface_age_ms": age_ms,
        "lightweight_verify_ms": 0.0,
        "fast_path_total_verify_ms": 0.0,
        "fast_path_foreground_check_ms": 0.0,
        "fast_path_no_dm_thread_check_ms": 0.0,
        "fast_path_search_surface_check_ms": 0.0,
        "fast_path_edittext_check_ms": 0.0,
        "fast_path_direct_edittext_probe_ms": 0.0,
        "fast_path_waits_count": 0,
        "fast_path_timeout_reason": "",
        "fast_path_mode": fast_path_mode,
        "fast_path_parent_proof_used": False,
        "typing_precheck_edittext_reused": False,
    }

    def _reject(reason: str) -> dict[str, Any]:
        reason_s = str(reason or "rejected")
        out["reject_reason"] = reason_s
        if reason_s == "fast_verify_timeout":
            out["fast_path_timeout_reason"] = reason_s
        elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        out["lightweight_verify_ms"] = elapsed_ms
        out["fast_path_total_verify_ms"] = elapsed_ms
        log(
            "info",
            "dm_sender_parent_search_ready_fast_path_rejected",
            username=username,
            dm_type=dm_type,
            fast_path_reject_reason=out["reject_reason"],
            parent_search_ready_age_ms=(
                round(float(age_ms), 2) if age_ms is not None else None
            ),
            sender_prepare_lightweight_verify_ms=out["lightweight_verify_ms"],
            fast_path_total_verify_ms=out["fast_path_total_verify_ms"],
            fast_path_foreground_check_ms=out["fast_path_foreground_check_ms"],
            fast_path_no_dm_thread_check_ms=out["fast_path_no_dm_thread_check_ms"],
            fast_path_search_surface_check_ms=out["fast_path_search_surface_check_ms"],
            fast_path_edittext_check_ms=out["fast_path_edittext_check_ms"],
            fast_path_direct_edittext_probe_ms=out["fast_path_direct_edittext_probe_ms"],
            fast_path_waits_count=out["fast_path_waits_count"],
            fast_path_timeout_reason=out["fast_path_timeout_reason"] or None,
            fast_path_mode=out["fast_path_mode"],
            fast_path_parent_proof_used=out["fast_path_parent_proof_used"],
        )
        return out

    def _timeout_reject_if_needed() -> dict[str, Any] | None:
        if time.perf_counter() >= deadline:
            return _reject("fast_verify_timeout")
        return None

    log(
        "info",
        "dm_sender_parent_search_ready_fast_path_attempted",
        username=username,
        dm_type=dm_type,
        flag_enabled=_outreach_trust_parent_search_ready_enabled(),
        parent_search_ready_verified=bool(signal.get("verified")),
        parent_search_ready_context=str(signal.get("context") or "") or None,
        parent_search_ready_age_ms=round(float(age_ms), 2) if age_ms is not None else None,
        parent_search_ready_max_age_ms=round(_parent_search_ready_max_age_ms(), 2),
        parent_search_ready_verified_at_source=str(signal.get("verified_at_source") or "")
        or None,
        fast_path_verify_budget_ms=round(max_verify_ms, 2),
        fast_path_mode=fast_path_mode,
    )

    if str(dm_type or "").strip().lower() != "outreach":
        return _reject("dm_type_not_outreach")
    if not _outreach_trust_parent_search_ready_enabled():
        return _reject("flag_disabled")
    if not signal:
        return _reject("missing_signal")
    if not bool(signal.get("verified")):
        return _reject("signal_not_verified")
    if str(signal.get("context") or "") != "unfollow_outreach_pipeline":
        return _reject("context_mismatch")
    if str(signal.get("account_id") or "").strip() != str(account_id or "").strip():
        return _reject("account_id_mismatch")
    signal_run_id = str(signal.get("run_id") or "").strip()
    current_run_id = str(run_id or "").strip()
    if signal_run_id and current_run_id and signal_run_id != current_run_id:
        return _reject("run_id_mismatch")
    if age_ms is None:
        return _reject("missing_verified_at")
    if age_ms > _parent_search_ready_max_age_ms():
        return _reject("signal_too_old")

    timeout_reject = _timeout_reject_if_needed()
    if timeout_reject is not None:
        return timeout_reject
    t_phase = time.perf_counter()
    foreground_ok = verify_app_foreground(d, pkg)
    out["fast_path_foreground_check_ms"] = round(
        (time.perf_counter() - t_phase) * 1000.0, 2
    )
    if not foreground_ok:
        return _reject("instagram_not_foreground")
    timeout_reject = _timeout_reject_if_needed()
    if timeout_reject is not None:
        return timeout_reject

    t_phase = time.perf_counter()
    no_dm_thread_ok, no_dm_thread_reason = _fast_path_no_dm_thread_visible_bounded(
        d, pkg=pkg, deadline=deadline
    )
    out["fast_path_waits_count"] += 1
    out["fast_path_no_dm_thread_check_ms"] = round(
        (time.perf_counter() - t_phase) * 1000.0, 2
    )
    if not no_dm_thread_ok:
        return _reject(no_dm_thread_reason or "dm_thread_check_failed")
    timeout_reject = _timeout_reject_if_needed()
    if timeout_reject is not None:
        return timeout_reject

    t_phase = time.perf_counter()
    ok_surface, why, ed = _fast_path_direct_search_edittext_probe_v3(
        d, pkg=pkg, deadline=deadline
    )
    out["fast_path_waits_count"] += 1
    direct_edittext_ms = round((time.perf_counter() - t_phase) * 1000.0, 2)
    out["fast_path_search_surface_check_ms"] = 0.0
    out["fast_path_edittext_check_ms"] = direct_edittext_ms
    out["fast_path_direct_edittext_probe_ms"] = direct_edittext_ms
    if not ok_surface:
        return _reject(why or "search_surface_not_verified")
    if ed is None:
        return _reject("search_edittext_not_ready")
    timeout_reject = _timeout_reject_if_needed()
    if timeout_reject is not None:
        return timeout_reject

    out["used"] = True
    out["typing_precheck_edittext_reused"] = True
    out["fast_path_parent_proof_used"] = True
    elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
    out["lightweight_verify_ms"] = elapsed_ms
    out["fast_path_total_verify_ms"] = elapsed_ms
    log(
        "info",
        "dm_sender_parent_search_ready_fast_path_used",
        username=username,
        dm_type=dm_type,
        parent_search_ready_age_ms=round(float(age_ms), 2),
        sender_prepare_reused_search_surface=True,
        sender_prepare_lightweight_verify_ms=out["lightweight_verify_ms"],
        fast_path_total_verify_ms=out["fast_path_total_verify_ms"],
        fast_path_foreground_check_ms=out["fast_path_foreground_check_ms"],
        fast_path_no_dm_thread_check_ms=out["fast_path_no_dm_thread_check_ms"],
        fast_path_search_surface_check_ms=out["fast_path_search_surface_check_ms"],
        fast_path_edittext_check_ms=out["fast_path_edittext_check_ms"],
        fast_path_direct_edittext_probe_ms=out["fast_path_direct_edittext_probe_ms"],
        fast_path_waits_count=out["fast_path_waits_count"],
        fast_path_mode=out["fast_path_mode"],
        fast_path_parent_proof_used=True,
        typing_precheck_edittext_reused=True,
    )
    return out


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


def _dm_sender_composer_visible_quick(d: u2.Device) -> bool:
    try:
        return _dm_find_focus_composer(d) is not None
    except Exception:
        return False


def _dm_sender_profile_back_to_search_fast_path(
    d: u2.Device,
    *,
    pkg: str,
    account_username: str,
    context: str,
    last_recipient_username: str = "",
) -> bool:
    """Use Instagram's top-left profile back button to restore the trusted Search surface."""
    src = str(account_username or "").strip()
    log(
        "info",
        "dm_sender_post_job_profile_back_to_search_started",
        context=context,
        account_username=src or None,
        last_recipient_username=last_recipient_username or None,
    )
    tapped, tap_method = tap_instagram_action_bar_back_button(d, pkg)
    if not tapped:
        log(
            "warning",
            "dm_sender_post_job_profile_back_to_search_failed",
            context=context,
            reason="action_bar_back_not_found",
            account_username=src or None,
        )
        return False
    log(
        "info",
        "dm_sender_post_job_profile_action_bar_back_tapped",
        context=context,
        tap_method=tap_method,
        account_username=src or None,
    )
    deadline = time.monotonic() + float(getattr(config, "BACK_TO_SEARCH_MAX_WAIT_S", 3.0))
    while time.monotonic() < deadline:
        if is_lightweight_search_screen(d, pkg):
            verified, why = _verify_dm_sender_global_search_surface(
                d, pkg=pkg, account_username=src, full_followers_check=False
            )
            if verified:
                _mark_dm_sender_global_search_ready(src, context=context)
                log(
                    "info",
                    "dm_sender_post_job_profile_back_to_search_ok",
                    context=context,
                    account_username=src or None,
                    verify_reason=why,
                )
                return True
            log(
                "warning",
                "dm_sender_post_job_profile_back_to_search_failed",
                context=context,
                reason=why,
                account_username=src or None,
            )
            return False
        time.sleep(0.08)
    log(
        "warning",
        "dm_sender_post_job_profile_back_to_search_failed",
        context=context,
        reason="search_timeout",
        account_username=src or None,
    )
    return False


def prepare_dm_sender_global_search_surface(
    d: u2.Device,
    *,
    account_username: str,
    context: str,
    last_recipient_username: str = "",
    prefer_back_stack_to_search: bool = False,
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
        if (
            last_recipient_username
            and is_dm_thread_screen(d, pkg)
            and not prefer_back_stack_to_search
        ):
            return_to_profile_from_dm(d, last_recipient_username, pkg)
    except Exception as e:
        log(
            "warning",
            "dm_sender_prepare_exit_dm_failed",
            context=context,
            error=str(e)[:200],
        )

    quick_pre = is_followers_list_surface_quick(d, source_profile_username=src)
    det_pre: dict[str, Any] = {}
    if quick_pre:
        log(
            "info",
            "dm_sender_post_job_followers_probe_full_check",
            context=context,
            phase="pre_exit",
            reason="quick_probe_true",
            account_username=src or None,
        )
        det_pre, _ = detect_followers_list_screen_fresh(d, source_profile_username=src)
    else:
        log(
            "info",
            "dm_sender_post_job_followers_probe_quick_false",
            context=context,
            phase="pre_exit",
            account_username=src or None,
        )
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

    if not (context == "dm_sender_post_job" and prefer_back_stack_to_search):
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

    quick_post_exit = is_followers_list_surface_quick(d, source_profile_username=src)
    det_post_exit: dict[str, Any] = {}
    if quick_post_exit:
        log(
            "info",
            "dm_sender_post_job_followers_probe_full_check",
            context=context,
            phase="post_exit",
            reason="quick_probe_true",
            account_username=src or None,
        )
        det_post_exit, _ = detect_followers_list_screen_fresh(
            d, source_profile_username=src
        )
    else:
        log(
            "info",
            "dm_sender_post_job_followers_probe_quick_false",
            context=context,
            phase="post_exit",
            account_username=src or None,
        )
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

    if context == "dm_sender_post_job" and prefer_back_stack_to_search:
        log(
            "info",
            "dm_sender_post_job_back_stack_fast_path_started",
            context=context,
            account_username=src or None,
            last_recipient_username=last_recipient_username or None,
        )
        profile_ok = False
        try:
            if last_recipient_username:
                if is_lightweight_search_screen(d, pkg):
                    verified, why = _verify_dm_sender_global_search_surface(
                        d, pkg=pkg, account_username=src, full_followers_check=False
                    )
                    if verified:
                        _mark_dm_sender_global_search_ready(src, context=context)
                        log(
                            "info",
                            "dm_sender_post_job_back_stack_search_ok",
                            context=context,
                            account_username=src or None,
                            verify_reason=why,
                            phase="already_search",
                        )
                        log(
                            "info",
                            "dm_sender_post_job_surface_prepare_done",
                            context=context,
                            account_username=src or None,
                            method="back_stack_fast_path",
                        )
                        return True
                log(
                    "info",
                    "dm_sender_post_job_first_back_to_restore_profile_started",
                    context=context,
                    account_username=src or None,
                    last_recipient_username=last_recipient_username,
                    dm_thread_visible=bool(is_dm_thread_screen(d, pkg)),
                    composer_visible=bool(_dm_sender_composer_visible_quick(d)),
                )
                tapped, tap_method = tap_instagram_action_bar_back_button(d, pkg)
                if tapped:
                    log(
                        "info",
                        "dm_sender_post_job_dm_action_bar_back_tapped",
                        context=context,
                        tap_method=tap_method,
                        last_recipient_username=last_recipient_username,
                    )
                    time.sleep(0.2)
                else:
                    try:
                        d.press("back")
                    except Exception:
                        pass
                    time.sleep(0.2)
                if is_lightweight_search_screen(d, pkg):
                    verified, why = _verify_dm_sender_global_search_surface(
                        d, pkg=pkg, account_username=src, full_followers_check=False
                    )
                    if verified:
                        _mark_dm_sender_global_search_ready(src, context=context)
                        log(
                            "info",
                            "dm_sender_post_job_back_stack_search_ok",
                            context=context,
                            account_username=src or None,
                            verify_reason=why,
                            phase="profile_already_restored",
                        )
                        log(
                            "info",
                            "dm_sender_post_job_surface_prepare_done",
                            context=context,
                            account_username=src or None,
                            method="back_stack_fast_path",
                        )
                        return True
                profile_ok = bool(verify_profile(d, last_recipient_username))
        except Exception as e:
            log(
                "warning",
                "dm_sender_post_job_back_stack_fast_path_failed",
                context=context,
                phase="return_to_profile",
                error=str(e)[:200],
            )
            profile_ok = False

        if profile_ok:
            log(
                "info",
                "dm_sender_post_job_back_stack_profile_ok",
                context=context,
                account_username=src or None,
                last_recipient_username=last_recipient_username or None,
            )
            try:
                log(
                    "info",
                    "dm_sender_post_job_profile_hardware_back_to_search_started",
                    context=context,
                    account_username=src or None,
                    last_recipient_username=last_recipient_username or None,
                )
                search_ok = bool(return_to_search_from_profile(d, pkg))
                if search_ok:
                    _mark_dm_sender_global_search_ready(src, context=context)
                log(
                    "info" if search_ok else "warning",
                    "dm_sender_post_job_profile_hardware_back_to_search_result",
                    context=context,
                    ok=bool(search_ok),
                )
                if search_ok:
                    log(
                        "info",
                        "dm_sender_post_job_back_stack_search_ok",
                        context=context,
                        account_username=src or None,
                        verify_reason="ok",
                    )
                    log(
                        "info",
                        "dm_sender_post_job_surface_prepare_done",
                        context=context,
                        account_username=src or None,
                        method="back_stack_fast_path",
                    )
                    return True
                log(
                    "warning",
                    "dm_sender_post_job_back_stack_fast_path_failed",
                    context=context,
                    phase="return_to_search",
                    reason="profile_back_to_search_failed",
                )
            except Exception as e:
                log(
                    "warning",
                    "dm_sender_post_job_back_stack_fast_path_failed",
                    context=context,
                    phase="return_to_search",
                    error=str(e)[:200],
                )
        else:
            log(
                "warning",
                "dm_sender_post_job_back_stack_fast_path_failed",
                context=context,
                phase="return_to_profile",
                reason="profile_not_verified",
                last_recipient_username=last_recipient_username or None,
            )

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
    allow_percent_fallback: bool = True,
    skip_post_open_verify_for_outreach: bool = False,
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
        allow_percent_fallback=allow_percent_fallback,
        block_if_dm_thread=True,
        caller_context=context,
    ):
        if (
            skip_post_open_verify_for_outreach
            and context == "dm_sender_navigate"
            and not allow_percent_fallback
        ):
            prepare_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            log(
                "info",
                "dm_sender_open_search_post_verify_skip_outreach",
                username=username,
                context=context,
                prepare_ms=prepare_ms,
                reason="open_search_strict_verified",
            )
            _mark_dm_sender_global_search_ready(src, context=context)
            log(
                "info",
                "dm_sender_global_search_ready_from_open_search_strict",
                username=username,
                context=context,
                prepare_ms=prepare_ms,
            )
            log(
                "info",
                "dm_sender_global_search_surface_verified",
                username=username,
                context=context,
                prepare_ms=prepare_ms,
                verify_reason="open_search_strict_verified",
                post_open_verify_skipped=True,
            )
            return True
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
    account_id: str = "",
    run_id: str | None = None,
    account_username: str = "",
    dm_type: str = "",
    previous_username: str | None = None,
    parent_search_ready: dict[str, Any] | None = None,
) -> tuple[str, bool]:
    """
    Search → profile → DM thread. Returns (thread_state, navigation_ok).
    """
    uname = str(username or "").strip()
    src = str(account_username or "").strip()
    prev_uname = str(previous_username or "").strip()
    dm_type_norm = str(dm_type or "").strip().lower()
    t_nav = time.perf_counter()
    _reset_dm_sender_nav_timings()
    log("info", "dm_sender_navigation_started", username=uname)

    if _check_dm_sender_permission_blocker(d, username=uname, context="navigation"):
        return "unknown", False

    if is_dm_thread_screen(d, pkg):
        if not return_to_profile_from_dm(d, uname, pkg):
            log("warning", "dm_sender_stuck_in_dm_thread", username=uname)
            return "unknown", False

    t_before_search = time.perf_counter()
    trusted_search_reuse = False
    parent_fast_path = {
        "attempted": False,
        "used": False,
        "reject_reason": "",
        "search_surface_age_ms": None,
        "lightweight_verify_ms": 0.0,
    }
    full_open_search_ms = 0.0
    if _dm_sender_trust_global_search_ready(src):
        log(
            "info",
            "dm_sender_global_search_reuse_trusted",
            username=uname,
            context="dm_sender_navigate",
        )
        trusted_search_reuse = True
        search_ok = True
    else:
        if dm_type_norm == "outreach":
            parent_fast_path = _try_parent_search_ready_fast_path(
                d,
                pkg=pkg,
                username=uname,
                account_id=account_id,
                account_username=src,
                run_id=run_id,
                dm_type=dm_type_norm,
                parent_search_ready=parent_search_ready,
            )
            search_ok = bool(parent_fast_path.get("used"))
            trusted_search_reuse = bool(search_ok)
        else:
            search_ok = False
        if not search_ok:
            t_full_open = time.perf_counter()
            search_ok = _open_search_with_recovery(
                d,
                pkg=pkg,
                username=uname,
                context="dm_sender_navigate",
                account_username=src,
                skip_if_recently_verified=True,
                allow_percent_fallback=(dm_type_norm != "outreach"),
                skip_post_open_verify_for_outreach=(dm_type_norm == "outreach"),
            )
            full_open_search_ms = (time.perf_counter() - t_full_open) * 1000.0
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
    previous_for_type = (
        prev_uname
        if dm_type_norm == "outreach" and prev_uname and trusted_search_reuse
        else None
    )
    if previous_for_type:
        log(
            "info",
            "dm_sender_previous_username_reused",
            previous_username=previous_for_type,
            current_username=uname,
            dm_type=dm_type_norm,
        )
    if not type_search(
        d,
        uname,
        previous_username=previous_for_type,
        outreach_trusted_search=(dm_type_norm == "outreach" and search_ok),
        outreach_trusted_edittext_verified=bool(
            dm_type_norm == "outreach"
            and search_ok
            and parent_fast_path.get("typing_precheck_edittext_reused")
        ),
    ):
        log("error", "dm_sender_type_search_failed", username=uname)
        return "unknown", False
    type_perf = get_perf_snapshot()
    log(
        "info",
        "dm_sender_username_typed",
        username=uname,
        username_type_ms=round((time.perf_counter() - t_type) * 1000.0, 2),
        navigation_to_username_typed_total_ms=round(
            (time.perf_counter() - t_nav) * 1000.0, 2
        ),
        sender_prepare_to_open_search_ms=sender_prepare_to_open_search_ms,
        typing_precheck_edittext_reused=bool(
            type_perf.get("typing_precheck_edittext_reused")
        ),
        typing_precheck_ms=round(float(type_perf.get("typing_precheck_ms") or 0.0), 2),
        typing_set_text_ms=round(float(type_perf.get("typing_set_text_ms") or 0.0), 2),
        typing_get_text_confirm_ms=round(
            float(type_perf.get("typing_get_text_confirm_ms") or 0.0), 2
        ),
    )

    if bool(getattr(config, "FAST_SKIP_ACCOUNTS_TAB", True)) and bool(
        getattr(config, "FAST_PATH_MODE", False)
    ):
        set_search_ui_mode("mixed_results")
    else:
        accounts_tab_clicked = open_accounts_tab(d)
        set_search_ui_mode("accounts_tab" if accounts_tab_clicked else "mixed_results")

    t_tap = time.perf_counter()
    if not tap_account_result(
        d,
        uname,
        nav_timing_origin=t_type,
        outreach_search_context=(dm_type_norm == "outreach"),
    ):
        log("error", "dm_sender_tap_account_failed", username=uname)
        return "unknown", False
    tap_segment_ms = round((time.perf_counter() - t_tap) * 1000.0, 2)
    search_total_ms = round((time.perf_counter() - t_before_search) * 1000.0, 2)

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
    t_thread_open = time.perf_counter()
    thread_state = open_dm_thread_from_profile(
        d,
        uname,
        outreach_mode=(dm_type_norm == "outreach"),
    )
    thread_open_ms = round((time.perf_counter() - t_thread_open) * 1000.0, 2)
    log(
        "info",
        "dm_sender_dm_thread_opened",
        username=uname,
        thread_state=thread_state,
        thread_open_ms=thread_open_ms,
    )

    snap = get_last_dm_thread_classify_snapshot()
    if (
        dm_type_norm == "outreach"
        and thread_state == "existing_thread"
        and bool(snap)
    ):
        log(
            "info",
            "dm_sender_skip_composer_probe_existing_thread",
            username=uname,
            dm_type=dm_type_norm,
            classify_snapshot=True,
        )
    elif thread_state not in ("dm_not_available", "unknown"):
        ok_comp, comp_reason = verify_dm_composer_safe(d, pkg)
        log(
            "info",
            "dm_sender_composer_probe",
            username=uname,
            composer_ok=bool(ok_comp),
            composer_reason=comp_reason,
        )

    _set_dm_sender_nav_timings(
        navigation_ms=(time.perf_counter() - t_nav) * 1000.0,
        search_ms=search_total_ms,
        thread_open_ms=thread_open_ms,
        parent_search_ready_fast_path_attempted=bool(parent_fast_path.get("attempted")),
        parent_search_ready_fast_path_used=bool(parent_fast_path.get("used")),
        parent_search_ready_fast_path_reject_reason=str(
            parent_fast_path.get("reject_reason") or ""
        ),
        search_surface_age_ms=parent_fast_path.get("search_surface_age_ms"),
        sender_prepare_reused_search_surface=bool(parent_fast_path.get("used")),
        sender_prepare_lightweight_verify_ms=float(
            parent_fast_path.get("lightweight_verify_ms") or 0.0
        ),
        sender_prepare_full_open_search_ms=full_open_search_ms,
        fast_path_total_verify_ms=float(
            parent_fast_path.get("fast_path_total_verify_ms") or 0.0
        ),
        fast_path_foreground_check_ms=float(
            parent_fast_path.get("fast_path_foreground_check_ms") or 0.0
        ),
        fast_path_no_dm_thread_check_ms=float(
            parent_fast_path.get("fast_path_no_dm_thread_check_ms") or 0.0
        ),
        fast_path_search_surface_check_ms=float(
            parent_fast_path.get("fast_path_search_surface_check_ms") or 0.0
        ),
        fast_path_edittext_check_ms=float(
            parent_fast_path.get("fast_path_edittext_check_ms") or 0.0
        ),
        fast_path_direct_edittext_probe_ms=float(
            parent_fast_path.get("fast_path_direct_edittext_probe_ms") or 0.0
        ),
        fast_path_waits_count=int(parent_fast_path.get("fast_path_waits_count") or 0),
        fast_path_timeout_reason=str(parent_fast_path.get("fast_path_timeout_reason") or ""),
        fast_path_mode=str(parent_fast_path.get("fast_path_mode") or ""),
        fast_path_parent_proof_used=bool(
            parent_fast_path.get("fast_path_parent_proof_used")
        ),
        typing_precheck_edittext_reused=bool(
            type_perf.get("typing_precheck_edittext_reused")
        ),
    )
    return thread_state, thread_state not in ("unknown",)


def _safe_teardown_navigation(
    d: u2.Device,
    username: str,
    *,
    pkg: str,
    account_username: str = "",
    prefer_back_stack_to_search: bool = False,
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
        prefer_back_stack_to_search=prefer_back_stack_to_search,
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
    run_id: str | None = None,
    previous_username: str | None = None,
    restore_search_after_job: bool = True,
    parent_search_ready: dict[str, Any] | None = None,
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
    nav_timings: dict[str, float] = {}
    post_job_ms = 0.0

    try:
        thread_state, nav_ok = _navigate_to_recipient_dm_thread(
            d,
            recipient,
            pkg=pkg,
            account_id=account_id,
            run_id=run_id,
            account_username=account_username,
            dm_type=dm_type,
            previous_username=previous_username,
            parent_search_ready=parent_search_ready,
        )
        nav_timings = _get_dm_sender_nav_timings()
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
        t_post_job = time.perf_counter()
        if dm_type == "outreach" and not bool(restore_search_after_job):
            log(
                "info",
                "dm_sender_post_job_restore_skipped_final_outreach_job",
                job_id=job_id,
                recipient_username=recipient,
                dm_type=dm_type,
            )
        else:
            _safe_teardown_navigation(
                d,
                recipient,
                pkg=pkg,
                account_username=account_username,
                prefer_back_stack_to_search=(dm_type == "outreach"),
            )
        post_job_ms = round((time.perf_counter() - t_post_job) * 1000.0, 2)

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
        "navigation_ms": float(nav_timings.get("navigation_ms") or 0.0),
        "search_ms": float(nav_timings.get("search_ms") or 0.0),
        "thread_open_ms": float(nav_timings.get("thread_open_ms") or 0.0),
        "post_job_ms": post_job_ms,
        "parent_search_ready_fast_path_attempted": bool(
            nav_timings.get("parent_search_ready_fast_path_attempted")
        ),
        "parent_search_ready_fast_path_used": bool(
            nav_timings.get("parent_search_ready_fast_path_used")
        ),
        "parent_search_ready_fast_path_reject_reason": str(
            nav_timings.get("parent_search_ready_fast_path_reject_reason") or ""
        ),
        "search_surface_age_ms": nav_timings.get("search_surface_age_ms"),
        "sender_prepare_reused_search_surface": bool(
            nav_timings.get("sender_prepare_reused_search_surface")
        ),
        "sender_prepare_lightweight_verify_ms": float(
            nav_timings.get("sender_prepare_lightweight_verify_ms") or 0.0
        ),
        "sender_prepare_full_open_search_ms": float(
            nav_timings.get("sender_prepare_full_open_search_ms") or 0.0
        ),
        "fast_path_total_verify_ms": float(
            nav_timings.get("fast_path_total_verify_ms") or 0.0
        ),
        "fast_path_foreground_check_ms": float(
            nav_timings.get("fast_path_foreground_check_ms") or 0.0
        ),
        "fast_path_no_dm_thread_check_ms": float(
            nav_timings.get("fast_path_no_dm_thread_check_ms") or 0.0
        ),
        "fast_path_search_surface_check_ms": float(
            nav_timings.get("fast_path_search_surface_check_ms") or 0.0
        ),
        "fast_path_edittext_check_ms": float(
            nav_timings.get("fast_path_edittext_check_ms") or 0.0
        ),
        "fast_path_direct_edittext_probe_ms": float(
            nav_timings.get("fast_path_direct_edittext_probe_ms") or 0.0
        ),
        "fast_path_waits_count": int(nav_timings.get("fast_path_waits_count") or 0),
        "fast_path_timeout_reason": str(
            nav_timings.get("fast_path_timeout_reason") or ""
        ),
        "fast_path_mode": str(nav_timings.get("fast_path_mode") or ""),
        "fast_path_parent_proof_used": bool(
            nav_timings.get("fast_path_parent_proof_used")
        ),
        "typing_precheck_edittext_reused": bool(
            nav_timings.get("typing_precheck_edittext_reused")
        ),
    }


def run_dm_sender_send(
    d: u2.Device,
    *,
    account_id: str,
    account_username: str = "",
    run_id: str | None = None,
    max_jobs: int | None = None,
    dm_type: str | None = None,
    parent_search_ready: dict[str, Any] | None = None,
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
    parent_verified_at = None
    parent_signal_age_at_sender_start_ms = None
    if parent_search_ready:
        try:
            parent_verified_at = float(parent_search_ready.get("verified_at_monotonic"))
            parent_signal_age_at_sender_start_ms = round(
                (time.perf_counter() - parent_verified_at) * 1000.0, 2
            )
        except (TypeError, ValueError):
            parent_verified_at = None

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
        "total_navigation_ms": 0.0,
        "total_post_job_ms": 0.0,
        "total_search_ms": 0.0,
        "total_thread_open_ms": 0.0,
        "parent_search_ready_fast_path_used": False,
        "parent_search_ready_fast_path_reject_reason": "",
        "search_surface_age_ms": None,
        "sender_prepare_reused_search_surface": False,
        "sender_prepare_lightweight_verify_ms": 0.0,
        "sender_prepare_full_open_search_ms": 0.0,
        "fast_path_total_verify_ms": 0.0,
        "fast_path_mode": "",
        "fast_path_parent_proof_used": False,
        "typing_precheck_edittext_reused": False,
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
        parent_search_ready_verified=bool((parent_search_ready or {}).get("verified")),
        parent_search_ready_verified_at_source=str(
            (parent_search_ready or {}).get("verified_at_source") or ""
        )
        or None,
        parent_signal_age_at_sender_start_ms=parent_signal_age_at_sender_start_ms,
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
    last_recipient_username = ""
    for job_index in range(max_jobs):
        t_claim = time.perf_counter()
        job = _claim_job_for_run(
            aid, reserved_by, dm_type=dm_type_resolved, only_job_id=only_job_id
        )
        claim_ms = round((time.perf_counter() - t_claim) * 1000.0, 2)
        parent_signal_age_after_claim_ms = None
        if parent_verified_at is not None:
            parent_signal_age_after_claim_ms = round(
                (time.perf_counter() - parent_verified_at) * 1000.0, 2
            )
        log(
            "info",
            "dm_sender_job_claim_timing",
            account_id=aid,
            run_id=run_id,
            dm_type=dm_type_resolved,
            job_index=job_index,
            job_claim_before_sender_ms=claim_ms,
            parent_signal_age_at_sender_attempt_ms=parent_signal_age_after_claim_ms,
            claimed=bool(job),
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
            run_id=run_id,
            previous_username=(
                last_recipient_username
                if dm_type_resolved == "outreach"
                else None
            ),
            restore_search_after_job=not (
                dm_type_resolved == "outreach" and job_index >= max_jobs - 1
            ),
            parent_search_ready=parent_search_ready,
        )
        summary["total_navigation_ms"] = round(
            float(summary.get("total_navigation_ms") or 0.0)
            + float(last_result.get("navigation_ms") or 0.0),
            2,
        )
        summary["total_post_job_ms"] = round(
            float(summary.get("total_post_job_ms") or 0.0)
            + float(last_result.get("post_job_ms") or 0.0),
            2,
        )
        summary["total_search_ms"] = round(
            float(summary.get("total_search_ms") or 0.0)
            + float(last_result.get("search_ms") or 0.0),
            2,
        )
        summary["total_thread_open_ms"] = round(
            float(summary.get("total_thread_open_ms") or 0.0)
            + float(last_result.get("thread_open_ms") or 0.0),
            2,
        )
        if bool(last_result.get("parent_search_ready_fast_path_used")):
            summary["parent_search_ready_fast_path_used"] = True
        reject_reason = str(last_result.get("parent_search_ready_fast_path_reject_reason") or "")
        if reject_reason:
            summary["parent_search_ready_fast_path_reject_reason"] = reject_reason
        if last_result.get("search_surface_age_ms") is not None:
            summary["search_surface_age_ms"] = last_result.get("search_surface_age_ms")
        if bool(last_result.get("sender_prepare_reused_search_surface")):
            summary["sender_prepare_reused_search_surface"] = True
        summary["sender_prepare_lightweight_verify_ms"] = round(
            float(summary.get("sender_prepare_lightweight_verify_ms") or 0.0)
            + float(last_result.get("sender_prepare_lightweight_verify_ms") or 0.0),
            2,
        )
        summary["sender_prepare_full_open_search_ms"] = round(
            float(summary.get("sender_prepare_full_open_search_ms") or 0.0)
            + float(last_result.get("sender_prepare_full_open_search_ms") or 0.0),
            2,
        )
        summary["fast_path_total_verify_ms"] = round(
            float(summary.get("fast_path_total_verify_ms") or 0.0)
            + float(last_result.get("fast_path_total_verify_ms") or 0.0),
            2,
        )
        if str(last_result.get("fast_path_mode") or ""):
            summary["fast_path_mode"] = str(last_result.get("fast_path_mode") or "")
        if bool(last_result.get("fast_path_parent_proof_used")):
            summary["fast_path_parent_proof_used"] = True
        if bool(last_result.get("typing_precheck_edittext_reused")):
            summary["typing_precheck_edittext_reused"] = True
        last_recipient_username = recipient
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
