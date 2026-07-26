"""
V4.3-B+ / V4.4 — DM Sender Engine: dry-run (classify + release) and real Welcome send.

Dry-run: no type_dm_draft_only / send_dm_safe.
Real send: reuses instagram_navigation draft + send + post-send finalize.
"""

from __future__ import annotations

import os
import re
import time
import xml.etree.ElementTree as ET
from typing import Any, Callable

import uiautomator2 as u2

import config
import account_protection_lists
import supabase_client
from device import app_start, force_stop, get_device_serial
from dm_real_send_flags import (
    resolve_outreach_dm_real_send_enabled,
    resolve_welcome_dm_real_send_enabled,
)
from instagram_navigation import (
    _dm_find_focus_composer,
    _wait_search_edittext,
    clear_dm_draft,
    dm_thread_shows_outgoing_message,
    read_dm_composer_text,
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
    verify_welcome_dm_thread_recipient_exact,
    _welcome_dm_thread_recipient_identity_from_hierarchy,
)
from logs import log

_DM_SENDER_GLOBAL_SEARCH_READY: dict[str, Any] = {}
_DM_SENDER_SESSION_ABORT_PERMISSION: bool = False
_LAST_DM_SENDER_NAV_TIMINGS: dict[str, float] = {}
_LAST_DM_SENDER_POST_JOB_RESTORE: dict[str, Any] = {}

# This proof only bridges one fresh thread observation to the immediately
# following composer focus. It is never reusable across navigation or jobs.
WELCOME_COMPOSER_EVIDENCE_TTL_S = 1.5
WELCOME_COMPOSER_FAST_PATH_RUNTIME_ENABLED = False

_TRUSTED_GLOBAL_SEARCH_CONTEXTS = frozenset(
    {
        "welcome_session_scan_to_sender",
        "dm_sender_post_job",
    }
)

_DM_COMPOSER_PLACEHOLDER_TEXTS = frozenset(
    " ".join(value.strip().lower().replace("\u2026", "...").split())
    for value in (
        "Message",
        "Message...",
        "Message…",
        "Send message",
        "Write a message",
        "Envoyer un message",
        "Écrire un message",
        "Ecrire un message",
    )
)


def _is_dm_composer_placeholder_text(value: str | None) -> bool:
    """Instagram can expose composer hint text via get_text(); do not treat it as draft."""
    raw = str(value or "").strip()
    if not raw:
        return False
    normalized = " ".join(raw.lower().replace("\u2026", "...").split())
    return normalized in _DM_COMPOSER_PLACEHOLDER_TEXTS


def _normalize_welcome_recipient(value: str | None) -> str:
    return str(value or "").strip().lstrip("@").lower()


def _composer_bounds_key(value: Any) -> tuple[int, int, int, int] | None:
    if isinstance(value, dict):
        try:
            return tuple(int(value.get(key) or 0) for key in ("left", "top", "right", "bottom"))
        except Exception:
            return None
    numbers = [int(part) for part in re.findall(r"-?\d+", str(value or ""))]
    return tuple(numbers[:4]) if len(numbers) >= 4 else None


def _fresh_welcome_composer_evidence(
    d: u2.Device,
    *,
    pkg: str,
    account_id: str,
    run_id: str,
    job_id: str,
    expected_username: str,
    navigation_generation: str,
) -> tuple[dict[str, Any] | None, str, str]:
    """Capture one exact, empty Welcome composer from a fresh hierarchy."""
    required = {
        "account_id": account_id,
        "run_id": run_id,
        "job_id": job_id,
        "expected_username": expected_username,
        "navigation_generation": navigation_generation,
    }
    if any(not str(value or "").strip() for value in required.values()):
        return None, "missing_correlated_identity", ""

    try:
        current_pkg = str((d.app_current() or {}).get("package") or "")
    except Exception:
        current_pkg = ""
    if current_pkg != str(pkg or ""):
        return None, "foreground_package_mismatch", ""

    try:
        try:
            hierarchy_xml = str(d.dump_hierarchy(compressed=False) or "")
        except TypeError:
            hierarchy_xml = str(d.dump_hierarchy() or "")
        root = ET.fromstring(hierarchy_xml)
    except Exception:
        return None, "fresh_snapshot_unavailable", ""

    normalized_xml = " ".join(hierarchy_xml.lower().split())
    if "review account info carefully" in normalized_xml:
        return None, "account_review_popup", ""

    identity_ok, identity_reason, observed_username = (
        _welcome_dm_thread_recipient_identity_from_hierarchy(
            hierarchy_xml, expected_username
        )
    )
    composer_nodes: list[ET.Element] = []
    exact_resource_id = f"{pkg}:id/row_thread_composer_edittext"
    for node in root.iter():
        resource_id = str(node.attrib.get("resource-id") or "")
        if resource_id in {
            "com.instagram.android:id/row_thread_composer_edittext",
            exact_resource_id,
        }:
            composer_nodes.append(node)

    if not identity_ok:
        return None, identity_reason, observed_username
    if len(composer_nodes) != 1:
        return None, "composer_exact_selector_ambiguous", observed_username

    composer_node = composer_nodes[0]
    composer_text = str(composer_node.attrib.get("text") or "").strip()
    if composer_text and not _is_dm_composer_placeholder_text(composer_text):
        return None, "composer_not_empty", observed_username

    evidence = {
        **required,
        "expected_username": str(expected_username or "").strip(),
        "observed_username": observed_username,
        "resource_id": str(composer_node.attrib.get("resource-id") or ""),
        "bounds": str(composer_node.attrib.get("bounds") or ""),
        "composer_text": composer_text,
        "created_monotonic": time.perf_counter(),
    }
    log(
        "info",
        "welcome_composer_evidence_created",
        job_id=job_id,
        expected_username=str(expected_username or "").strip(),
        snapshot_age_ms=0.0,
        navigation_generation=navigation_generation,
    )
    return evidence, "ok", observed_username


def _resolve_welcome_composer_from_evidence(
    d: u2.Device,
    evidence: dict[str, Any],
    *,
    account_id: str,
    run_id: str,
    job_id: str,
    expected_username: str,
    navigation_generation: str,
) -> tuple[Any | None, str, float, str]:
    """Validate short-lived evidence and return only its exact selector."""
    created_at = float(evidence.get("created_monotonic") or 0.0)
    evidence_age_ms = max(0.0, (time.perf_counter() - created_at) * 1000.0)
    observed_username = str(evidence.get("observed_username") or "")
    expected = {
        "account_id": account_id,
        "run_id": run_id,
        "job_id": job_id,
        "expected_username": expected_username,
    }
    for field, value in expected.items():
        if str(evidence.get(field) or "") != str(value or ""):
            return None, f"{field}_mismatch", evidence_age_ms, observed_username
    if _normalize_welcome_recipient(observed_username) != _normalize_welcome_recipient(
        expected_username
    ):
        return None, "observed_username_mismatch", evidence_age_ms, observed_username
    if str(evidence.get("navigation_generation") or "") != str(
        navigation_generation or ""
    ):
        return None, "navigation_generation_changed", evidence_age_ms, observed_username
    if evidence_age_ms > WELCOME_COMPOSER_EVIDENCE_TTL_S * 1000.0:
        return None, "snapshot_stale", evidence_age_ms, observed_username

    resource_id = str(evidence.get("resource_id") or "")
    if resource_id != "com.instagram.android:id/row_thread_composer_edittext":
        return None, "composer_selector_not_exact", evidence_age_ms, observed_username
    try:
        composer = d(resourceId=resource_id)
        if not composer.exists(timeout=0.08):
            return None, "composer_exact_selector_absent", evidence_age_ms, observed_username
        info = composer.info or {}
        bounds = info.get("bounds") or ""
        if _composer_bounds_key(bounds) != _composer_bounds_key(evidence.get("bounds")):
            return None, "composer_bounds_changed", evidence_age_ms, observed_username
        current_text = str(composer.get_text() or "").strip()
        if current_text and not _is_dm_composer_placeholder_text(current_text):
            return None, "composer_not_empty", evidence_age_ms, observed_username
    except Exception:
        return None, "composer_exact_selector_error", evidence_age_ms, observed_username
    return composer, "ok", evidence_age_ms, observed_username


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
        "navigation_to_username_typed_total_ms": 0.0,
        "sender_prepare_to_open_search_ms": 0.0,
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
        "post_job_clear_previous_username_ms": 0.0,
    }


def _set_dm_sender_nav_timings(
    *,
    navigation_ms: float = 0.0,
    navigation_to_username_typed_total_ms: float = 0.0,
    sender_prepare_to_open_search_ms: float = 0.0,
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
    post_job_clear_previous_username_ms: float = 0.0,
) -> None:
    global _LAST_DM_SENDER_NAV_TIMINGS
    _LAST_DM_SENDER_NAV_TIMINGS = {
        "navigation_ms": round(max(0.0, float(navigation_ms or 0.0)), 2),
        "navigation_to_username_typed_total_ms": round(
            max(0.0, float(navigation_to_username_typed_total_ms or 0.0)), 2
        ),
        "sender_prepare_to_open_search_ms": round(
            max(0.0, float(sender_prepare_to_open_search_ms or 0.0)), 2
        ),
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
        "post_job_clear_previous_username_ms": round(
            max(0.0, float(post_job_clear_previous_username_ms or 0.0)), 2
        ),
    }


def _get_dm_sender_nav_timings() -> dict[str, float]:
    return dict(_LAST_DM_SENDER_NAV_TIMINGS)


def _reset_dm_sender_post_job_restore() -> None:
    global _LAST_DM_SENDER_POST_JOB_RESTORE
    _LAST_DM_SENDER_POST_JOB_RESTORE = {
        "post_job_restore_mode": "",
        "post_job_restore_final_mode": "",
        "post_job_restore_final_reason": "",
        "post_job_back_to_previous_search_attempted": False,
        "post_job_previous_search_results_detected": False,
        "post_job_previous_search_username_present": False,
        "post_job_reuse_previous_search_surface_ms": 0.0,
        "post_job_fallback_open_search_reason": "",
        "post_job_restore_attempts_count": 0,
        "post_job_restore_used_fresh_open_search": False,
        "post_job_restore_used_back_stack": False,
        "post_job_restore_success_after_retry": False,
    }


def _set_dm_sender_post_job_restore(**values: Any) -> None:
    global _LAST_DM_SENDER_POST_JOB_RESTORE
    if not _LAST_DM_SENDER_POST_JOB_RESTORE:
        _reset_dm_sender_post_job_restore()
    _LAST_DM_SENDER_POST_JOB_RESTORE.update(values)


def _get_dm_sender_post_job_restore() -> dict[str, Any]:
    return dict(_LAST_DM_SENDER_POST_JOB_RESTORE)


def _record_dm_sender_post_job_restore_attempt(
    *,
    attempt: str,
    ok: bool,
    reason: str = "",
    final_mode: str = "",
    used_back_stack: bool = False,
    used_fresh_open_search: bool = False,
    success_after_retry: bool = False,
    previous_username_present: bool | None = None,
    reuse_ms: float | None = None,
) -> None:
    state = _get_dm_sender_post_job_restore()
    attempts_count = int(state.get("post_job_restore_attempts_count") or 0) + 1
    updates: dict[str, Any] = {
        "post_job_restore_attempts_count": attempts_count,
        "post_job_restore_used_back_stack": bool(
            state.get("post_job_restore_used_back_stack") or used_back_stack
        ),
        "post_job_restore_used_fresh_open_search": bool(
            state.get("post_job_restore_used_fresh_open_search") or used_fresh_open_search
        ),
        "post_job_restore_success_after_retry": bool(
            state.get("post_job_restore_success_after_retry") or success_after_retry
        ),
    }
    if final_mode:
        updates["post_job_restore_mode"] = final_mode
        updates["post_job_restore_final_mode"] = final_mode
        updates["post_job_restore_final_reason"] = reason
        if final_mode != "fresh_open_search_used":
            updates["post_job_fallback_open_search_reason"] = ""
    if final_mode in (
        "previous_search_results_reused",
        "previous_search_results_reused_after_retry",
    ):
        updates["post_job_previous_search_results_detected"] = True
    if previous_username_present is not None:
        updates["post_job_previous_search_username_present"] = bool(previous_username_present)
    if reuse_ms is not None:
        updates["post_job_reuse_previous_search_surface_ms"] = round(
            max(0.0, float(reuse_ms or 0.0)), 2
        )
    if final_mode == "fresh_open_search_used":
        updates["post_job_fallback_open_search_reason"] = reason
    elif not ok and reason:
        updates["post_job_restore_final_reason"] = reason
    _set_dm_sender_post_job_restore(**updates)
    log(
        "info" if ok else "warning",
        "post_job_restore_attempt_result",
        post_job_restore_attempt=str(attempt or ""),
        ok=bool(ok),
        reason=str(reason or "") or None,
        post_job_restore_final_mode=str(final_mode or "") or None,
        post_job_restore_attempts_count=attempts_count,
        post_job_restore_used_fresh_open_search=bool(updates["post_job_restore_used_fresh_open_search"]),
        post_job_restore_used_back_stack=bool(updates["post_job_restore_used_back_stack"]),
        post_job_restore_success_after_retry=bool(updates["post_job_restore_success_after_retry"]),
    )


def _normalize_dm_sender_handle(value: str) -> str:
    return str(value or "").strip().lstrip("@").lower()


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

    skip_followers_probe_for_outreach_restore = (
        context == "dm_sender_post_job" and prefer_back_stack_to_search
    )
    quick_pre = False
    det_pre: dict[str, Any] = {}
    if skip_followers_probe_for_outreach_restore:
        log(
            "info",
            "dm_sender_post_job_followers_probe_skipped_outreach_restore",
            context=context,
            phase="pre_exit",
            account_username=src or None,
            prefer_back_stack_to_search=True,
            reason="outreach_search_restore_no_followers_surface_expected",
        )
    else:
        quick_pre = is_followers_list_surface_quick(d, source_profile_username=src)
    if not skip_followers_probe_for_outreach_restore and quick_pre:
        log(
            "info",
            "dm_sender_post_job_followers_probe_full_check",
            context=context,
            phase="pre_exit",
            reason="quick_probe_true",
            account_username=src or None,
        )
        det_pre, _ = detect_followers_list_screen_fresh(d, source_profile_username=src)
    elif not skip_followers_probe_for_outreach_restore:
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

    quick_post_exit = False
    det_post_exit: dict[str, Any] = {}
    if skip_followers_probe_for_outreach_restore:
        log(
            "info",
            "dm_sender_post_job_followers_probe_skipped_outreach_restore",
            context=context,
            phase="post_exit",
            account_username=src or None,
            prefer_back_stack_to_search=True,
            reason="outreach_search_restore_no_followers_surface_expected",
        )
    else:
        quick_post_exit = is_followers_list_surface_quick(d, source_profile_username=src)
    if not skip_followers_probe_for_outreach_restore and quick_post_exit:
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
    elif not skip_followers_probe_for_outreach_restore:
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
        t_restore = time.perf_counter()
        _set_dm_sender_post_job_restore(
            post_job_back_to_previous_search_attempted=True,
            post_job_restore_used_back_stack=True,
        )
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
                        _set_dm_sender_post_job_restore(
                            post_job_previous_search_results_detected=True,
                            post_job_reuse_previous_search_surface_ms=round(
                                (time.perf_counter() - t_restore) * 1000.0, 2
                            ),
                        )
                        _record_dm_sender_post_job_restore_attempt(
                            attempt="already_on_search",
                            ok=True,
                            reason="already_search_verified",
                            final_mode="previous_search_results_reused",
                            used_back_stack=True,
                            reuse_ms=(time.perf_counter() - t_restore) * 1000.0,
                        )
                        return True
                start_dm_thread_visible = bool(is_dm_thread_screen(d, pkg))
                start_composer_visible = bool(_dm_sender_composer_visible_quick(d))
                start_screen = (
                    "thread"
                    if start_dm_thread_visible or start_composer_visible
                    else "profile"
                    if verify_profile(d, last_recipient_username)
                    else "unknown"
                )
                log(
                    "info",
                    "dm_sender_post_job_restore_start_screen",
                    context=context,
                    account_username=src or None,
                    last_recipient_username=last_recipient_username or None,
                    post_job_restore_start_screen=start_screen,
                    dm_thread_visible=start_dm_thread_visible,
                    composer_visible=start_composer_visible,
                )
                if start_screen == "thread":
                    fast_t0 = time.perf_counter()
                    log(
                        "info",
                        "dm_sender_post_job_restore_fast_path_started",
                        context=context,
                        account_username=src or None,
                        last_recipient_username=last_recipient_username or None,
                        post_job_restore_start_screen=start_screen,
                    )
                    t_thread_profile = time.perf_counter()
                    tapped, tap_method = tap_instagram_action_bar_back_button(d, pkg)
                    if tapped:
                        log(
                            "info",
                            "dm_sender_post_job_restore_fast_thread_back_tapped",
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
                    thread_to_profile_ms = round(
                        (time.perf_counter() - t_thread_profile) * 1000.0, 2
                    )
                    if is_lightweight_search_screen(d, pkg):
                        verified, why = _verify_dm_sender_global_search_surface(
                            d, pkg=pkg, account_username=src, full_followers_check=False
                        )
                        if verified:
                            reuse_ms = round((time.perf_counter() - t_restore) * 1000.0, 2)
                            _mark_dm_sender_global_search_ready(src, context=context)
                            _record_dm_sender_post_job_restore_attempt(
                                attempt="thread_back_direct_to_search",
                                ok=True,
                                reason="thread_back_direct_to_search_ok",
                                final_mode="previous_search_results_reused",
                                used_back_stack=True,
                                previous_username_present=False,
                                reuse_ms=reuse_ms,
                            )
                            log(
                                "info",
                                "dm_sender_post_job_restore_fast_path_done",
                                context=context,
                                post_job_restore_fast_path_used=True,
                                post_job_restore_thread_to_profile_ms=thread_to_profile_ms,
                                post_job_restore_profile_to_search_ms=0.0,
                                post_job_restore_verify_search_ms=0.0,
                                post_job_restore_timeout_saved_estimate_ms=round(
                                    float(getattr(config, "BACK_TO_SEARCH_MAX_WAIT_S", 2.5))
                                    * 1000.0,
                                    2,
                                ),
                            )
                            log(
                                "info",
                                "dm_sender_post_job_surface_prepare_done",
                                context=context,
                                account_username=src or None,
                                method="thread_back_direct_to_search_fast_path",
                            )
                            return True
                    profile_ready = bool(verify_profile(d, last_recipient_username))
                    if profile_ready:
                        t_profile_search = time.perf_counter()
                        search_ok = bool(return_to_search_from_profile(d, pkg))
                        profile_to_search_ms = round(
                            (time.perf_counter() - t_profile_search) * 1000.0, 2
                        )
                        if search_ok:
                            reuse_ms = round((time.perf_counter() - t_restore) * 1000.0, 2)
                            _mark_dm_sender_global_search_ready(src, context=context)
                            _record_dm_sender_post_job_restore_attempt(
                                attempt="thread_to_profile_to_search",
                                ok=True,
                                reason="thread_to_profile_to_search_ok",
                                final_mode="previous_search_results_reused",
                                used_back_stack=True,
                                previous_username_present=False,
                                reuse_ms=reuse_ms,
                            )
                            log(
                                "info",
                                "dm_sender_post_job_restore_fast_path_done",
                                context=context,
                                post_job_restore_fast_path_used=True,
                                post_job_restore_thread_to_profile_ms=thread_to_profile_ms,
                                post_job_restore_profile_to_search_ms=profile_to_search_ms,
                                post_job_restore_verify_search_ms=profile_to_search_ms,
                                post_job_restore_timeout_saved_estimate_ms=round(
                                    float(getattr(config, "BACK_TO_SEARCH_MAX_WAIT_S", 2.5))
                                    * 1000.0,
                                    2,
                                ),
                            )
                            log(
                                "info",
                                "dm_sender_post_job_surface_prepare_done",
                                context=context,
                                account_username=src or None,
                                method="thread_to_profile_to_search_fast_path",
                            )
                            return True
                        _record_dm_sender_post_job_restore_attempt(
                            attempt="thread_to_profile_to_search",
                            ok=False,
                            reason="thread_to_profile_to_search_failed",
                            used_back_stack=True,
                        )
                        log(
                            "warning",
                            "dm_sender_post_job_restore_fast_path_failed",
                            context=context,
                            reason="thread_to_profile_to_search_failed",
                            post_job_restore_thread_to_profile_ms=thread_to_profile_ms,
                            post_job_restore_profile_to_search_ms=profile_to_search_ms,
                        )
                    else:
                        _record_dm_sender_post_job_restore_attempt(
                            attempt="thread_to_profile",
                            ok=False,
                            reason="profile_not_verified_after_thread_back",
                            used_back_stack=True,
                        )
                        log(
                            "warning",
                            "dm_sender_post_job_restore_fast_path_failed",
                            context=context,
                            reason="profile_not_verified_after_thread_back",
                            post_job_restore_thread_to_profile_ms=thread_to_profile_ms,
                            post_job_restore_elapsed_ms=round(
                                (time.perf_counter() - fast_t0) * 1000.0, 2
                            ),
                        )
                if verify_profile(d, last_recipient_username):
                    log(
                        "info",
                        "dm_sender_post_job_profile_hardware_back_to_search_started",
                        context=context,
                        account_username=src or None,
                        last_recipient_username=last_recipient_username or None,
                        phase="profile_already_restored",
                    )
                    search_ok = bool(return_to_search_from_profile(d, pkg))
                    if search_ok:
                        _mark_dm_sender_global_search_ready(src, context=context)
                        previous_present = False
                        try:
                            ed_prev = _wait_search_edittext(d)
                            cur_txt = ed_prev.get_text() if ed_prev is not None else ""
                            previous_present = _normalize_dm_sender_handle(
                                str(last_recipient_username or "")
                            ) in _normalize_dm_sender_handle(str(cur_txt or ""))
                        except Exception:
                            previous_present = False
                        reuse_ms = round((time.perf_counter() - t_restore) * 1000.0, 2)
                        _set_dm_sender_post_job_restore(
                            post_job_previous_search_results_detected=True,
                            post_job_previous_search_username_present=previous_present,
                            post_job_reuse_previous_search_surface_ms=reuse_ms,
                        )
                        _record_dm_sender_post_job_restore_attempt(
                            attempt="profile_hardware_back_to_search",
                            ok=True,
                            reason="profile_hardware_back_to_search_ok",
                            final_mode="previous_search_results_reused",
                            used_back_stack=True,
                            previous_username_present=previous_present,
                            reuse_ms=reuse_ms,
                        )
                        log(
                            "info",
                            "dm_sender_post_job_previous_search_results_reused",
                            context=context,
                            account_username=src or None,
                            last_recipient_username=last_recipient_username or None,
                            post_job_previous_search_username_present=previous_present,
                            post_job_reuse_previous_search_surface_ms=reuse_ms,
                        )
                        log(
                            "info",
                            "dm_sender_post_job_surface_prepare_done",
                            context=context,
                            account_username=src or None,
                            method="previous_search_results_hardware_back",
                        )
                        return True
                    _record_dm_sender_post_job_restore_attempt(
                        attempt="profile_hardware_back_to_search",
                        ok=False,
                        reason="profile_hardware_back_to_search_failed",
                        used_back_stack=True,
                    )
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
                        _set_dm_sender_post_job_restore(
                            post_job_previous_search_results_detected=True,
                            post_job_reuse_previous_search_surface_ms=round(
                                (time.perf_counter() - t_restore) * 1000.0, 2
                            ),
                        )
                        _record_dm_sender_post_job_restore_attempt(
                            attempt="action_bar_back_to_search",
                            ok=True,
                            reason="search_detected_after_action_bar_back",
                            final_mode="previous_search_results_reused_after_retry",
                            used_back_stack=True,
                            success_after_retry=True,
                            reuse_ms=(time.perf_counter() - t_restore) * 1000.0,
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
                    _record_dm_sender_post_job_restore_attempt(
                        attempt="profile_hardware_back_to_search_retry",
                        ok=True,
                        reason="profile_hardware_back_to_search_ok_after_retry",
                        final_mode="previous_search_results_reused_after_retry",
                        used_back_stack=True,
                        success_after_retry=True,
                        reuse_ms=(time.perf_counter() - t_restore) * 1000.0,
                    )
                    return True
                _record_dm_sender_post_job_restore_attempt(
                    attempt="profile_hardware_back_to_search_retry",
                    ok=False,
                    reason="profile_back_to_search_failed",
                    used_back_stack=True,
                )
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
    _set_dm_sender_post_job_restore(
        post_job_fallback_open_search_reason="previous_search_results_unavailable",
    )
    _record_dm_sender_post_job_restore_attempt(
        attempt="previous_search_results_unavailable",
        ok=False,
        reason="previous_search_results_unavailable",
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
        _record_dm_sender_post_job_restore_attempt(
            attempt="fresh_open_search",
            ok=False,
            reason="open_search_failed",
            final_mode="restore_failed",
            used_fresh_open_search=True,
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
        _record_dm_sender_post_job_restore_attempt(
            attempt="fresh_open_search_verify",
            ok=False,
            reason=why,
            final_mode="restore_failed",
            used_fresh_open_search=True,
        )
        return False

    _mark_dm_sender_global_search_ready(src, context=context)
    search_perf = get_perf_snapshot()
    reused_by_open_search_helper = bool(search_perf.get("search_surface_reused"))
    final_mode = (
        "previous_search_results_reused_after_retry"
        if reused_by_open_search_helper
        else "fresh_open_search_used"
    )
    final_reason = (
        "open_search_helper_reused_search_surface_after_retry"
        if reused_by_open_search_helper
        else "fresh_open_search_verified"
    )
    _record_dm_sender_post_job_restore_attempt(
        attempt="fresh_open_search",
        ok=True,
        reason=final_reason,
        final_mode=final_mode,
        used_fresh_open_search=not reused_by_open_search_helper,
        used_back_stack=bool(reused_by_open_search_helper),
        success_after_retry=bool(reused_by_open_search_helper),
    )
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
    navigation_to_username_typed_total_ms = round((time.perf_counter() - t_nav) * 1000.0, 2)
    log(
        "info",
        "dm_sender_username_typed",
        username=uname,
        username_type_ms=round((time.perf_counter() - t_type) * 1000.0, 2),
        navigation_to_username_typed_total_ms=navigation_to_username_typed_total_ms,
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
        navigation_to_username_typed_total_ms=navigation_to_username_typed_total_ms,
        sender_prepare_to_open_search_ms=sender_prepare_to_open_search_ms,
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
        post_job_clear_previous_username_ms=float(
            type_perf.get("post_job_clear_previous_username_ms") or 0.0
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
    _reset_dm_sender_post_job_restore()
    if prefer_back_stack_to_search:
        _set_dm_sender_post_job_restore(post_job_restore_used_back_stack=True)
    if _check_dm_sender_permission_blocker(
        d, username=username, context="post_job_teardown"
    ):
        _record_dm_sender_post_job_restore_attempt(
            attempt="permission_blocker",
            ok=False,
            reason="permission_blocker",
            final_mode="restore_failed",
        )
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


def prepare_dm_sender_jobs(
    d: u2.Device,
    *,
    account_id: str,
    dm_type: str,
    max_jobs: int,
) -> dict[str, Any]:
    """Claim jobs without touching UI so callers can prepare before Search-ready."""
    t0 = time.perf_counter()
    aid = str(account_id or "").strip()
    dm_type_resolved = str(dm_type or "").strip()
    limit = max(0, int(max_jobs or 0))
    reserved_by = _resolve_reserved_by(d)
    only_job_id, filter_source = _resolve_dm_sender_only_job_id()
    jobs: list[dict[str, Any]] = []
    log(
        "info",
        "dm_sender_prepare_jobs_started",
        account_id=aid,
        dm_type=dm_type_resolved,
        max_jobs=limit,
        only_job_id=only_job_id or None,
        filter_source=filter_source,
        reserved_by=reserved_by,
    )
    for job_index in range(limit):
        t_claim = time.perf_counter()
        job = _claim_job_for_run(
            aid,
            reserved_by,
            dm_type=dm_type_resolved,
            only_job_id=only_job_id,
        )
        claim_ms = round((time.perf_counter() - t_claim) * 1000.0, 2)
        log(
            "info",
            "dm_sender_prepare_job_claim_attempt",
            account_id=aid,
            dm_type=dm_type_resolved,
            job_index=job_index,
            claimed=bool(job),
            job_claim_before_sender_ms=claim_ms,
            job_id=str((job or {}).get("id") or "") or None,
            recipient_username=str((job or {}).get("recipient_username") or "") or None,
        )
        if not job:
            break
        jobs.append(job)
        if only_job_id:
            break
    prepare_ms = round((time.perf_counter() - t0) * 1000.0, 2)
    log(
        "info",
        "dm_sender_prepare_jobs_completed",
        account_id=aid,
        dm_type=dm_type_resolved,
        prepared_jobs_count=len(jobs),
        prepared_job_ids=[str(job.get("id") or "") for job in jobs],
        prepare_ms=prepare_ms,
    )
    return {
        "jobs": jobs,
        "prepared_jobs_count": len(jobs),
        "reserved_by": reserved_by,
        "only_job_id": only_job_id or "",
        "filter_source": filter_source,
        "prepare_ms": prepare_ms,
    }


def release_prepared_dm_jobs(
    jobs: list[dict[str, Any]],
    *,
    reason: str,
) -> dict[str, Any]:
    released = 0
    failed = 0
    job_ids: list[str] = []
    for job in list(jobs or []):
        job_id = str((job or {}).get("id") or "").strip()
        if not job_id:
            continue
        job_ids.append(job_id)
        try:
            row = supabase_client.release_dm_job_after_dry_run(
                job_id,
                thread_state=str(reason or "prepared_not_sent"),
                sendable=False,
                skip_reason_candidate=str(reason or "prepared_not_sent"),
                metadata_patch={"prepared_job_released": True, "release_reason": reason},
            )
            if row:
                released += 1
            else:
                failed += 1
        except Exception as exc:
            failed += 1
            log(
                "warning",
                "dm_sender_prepared_job_release_failed",
                job_id=job_id,
                reason=reason,
                error=str(exc)[:300],
            )
    log(
        "info",
        "dm_sender_prepared_jobs_released",
        reason=reason,
        jobs_released_or_requeued_on_search_ready_failure=released,
        release_failed_count=failed,
        job_ids=job_ids,
    )
    return {
        "released_count": released,
        "failed_count": failed,
        "job_ids": job_ids,
    }


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


def resolve_dm_sender_real_send_enabled_for_type(dm_type: str | None) -> tuple[bool, str]:
    """Resolve real-send for a DM domain without cross-enabling products."""
    dm_type_norm = str(dm_type or "").strip().lower()
    if dm_type_norm in {"welcome", "dm_welcome", "dm_welcome_session_send", "welcome_session"}:
        return resolve_welcome_dm_real_send_enabled()
    if dm_type_norm in {"outreach", "outreach_session"}:
        return resolve_outreach_dm_real_send_enabled()

    enabled, source = _resolve_dm_sender_real_send_enabled()
    log(
        "warning",
        "dm_sender_legacy_real_send_resolved",
        dm_type=dm_type_norm or None,
        enabled=enabled,
        source=f"legacy:{source}",
    )
    return enabled, f"legacy:{source}"


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


def _complete_job_send_unverified_quarantine(
    job: dict[str, Any],
    *,
    last_error: str,
    thread_state: str | None = None,
    metadata_patch: dict[str, Any] | None = None,
    increment_attempt: bool = True,
) -> tuple[dict[str, Any] | None, str]:
    """Terminal quarantine: an unverified tap must never become retryable."""
    job_id = str(job.get("id") or "")
    patch = dict(metadata_patch or {})
    patch.update(
        {
            "send_verification_status": "unverified",
            "manual_reconciliation_required": True,
            "automatic_retry_blocked": True,
        }
    )
    if thread_state:
        patch["thread_state"] = thread_state
    row = supabase_client.complete_dm_job(
        job_id,
        "failed",
        last_error=str(last_error),
        increment_attempt=bool(increment_attempt),
        retry_delay_seconds=None,
        metadata_patch=patch,
    )
    log(
        "warning",
        "dm_sender_send_unverified_quarantined",
        job_id=job_id,
        last_error=last_error,
        recipient_username=job.get("recipient_username"),
        thread_state=thread_state,
        automatic_retry_blocked=True,
    )
    return row, "send_unverified_quarantined"


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


def _thread_snapshot_supports_composer_fast_path(
    snap: dict[str, Any],
    *,
    dm_type: str,
    thread_state: str,
) -> tuple[bool, str]:
    dm_type_norm = str(dm_type or "").strip().lower()
    if dm_type_norm != "outreach":
        return False, "dm_type_not_outreach"
    if str(thread_state or "").strip() != "empty_new_thread":
        return False, "thread_state_not_empty_new_thread"
    if not isinstance(snap, dict) or not bool(snap):
        return False, "thread_snapshot_missing"
    if not bool(snap.get("composer_visible")):
        return False, "composer_not_visible_in_thread_snapshot"
    composer_signal = str(snap.get("composer_signal") or "")
    if "resource_id_exact_composer_pkg" not in composer_signal:
        return False, "composer_signal_not_exact_resource_id"
    return True, "ok"


def _resolve_dm_text_composer(
    d: u2.Device,
    *,
    pkg: str,
    username: str,
    caller: str,
    dm_type: str = "",
    thread_state: str = "",
    thread_snapshot: dict[str, Any] | None = None,
    welcome_composer_evidence: dict[str, Any] | None = None,
    account_id: str = "",
    run_id: str = "",
    job_id: str = "",
    navigation_generation: str = "",
    fast_path_state: dict[str, Any] | None = None,
) -> tuple[Any | None, str | None]:
    started_at = time.perf_counter()
    state = fast_path_state if isinstance(fast_path_state, dict) else {}
    if _check_dm_sender_permission_blocker(d, username=username, context=caller):
        return None, "unexpected_permission_dialog"
    if str(dm_type or "").strip().lower() == "welcome" and isinstance(
        welcome_composer_evidence, dict
    ):
        composer, evidence_reason, evidence_age_ms, observed_username = (
            _resolve_welcome_composer_from_evidence(
                d,
                welcome_composer_evidence,
                account_id=account_id,
                run_id=run_id,
                job_id=job_id,
                expected_username=username,
                navigation_generation=navigation_generation,
            )
        )
        if composer is not None:
            try:
                composer.click()
            except Exception:
                composer = None
                evidence_reason = "composer_focus_failed"
        if composer is not None:
            state.update(
                {
                    "used": True,
                    "evidence_age_ms": round(evidence_age_ms, 2),
                    "composer": composer,
                }
            )
            return composer, None
        log(
            "info",
            "welcome_composer_fast_path_invalidated",
            reason=evidence_reason,
            evidence_age_ms=round(evidence_age_ms, 2),
            expected_username=username,
            observed_username=observed_username or None,
        )
        state.update({"used": False, "invalidation_reason": evidence_reason})

    snap = thread_snapshot if isinstance(thread_snapshot, dict) else {}
    fast_path_ok, fast_path_reason = _thread_snapshot_supports_composer_fast_path(
        snap,
        dm_type=dm_type,
        thread_state=thread_state,
    )
    if fast_path_ok:
        log(
            "info",
            "dm_sender_composer_resolve_fast_path_from_thread_snapshot_used",
            username=username,
            caller=caller,
            thread_state=thread_state,
            composer_signal=str(snap.get("composer_signal") or ""),
            dm_type=str(dm_type or ""),
        )
    else:
        if str(dm_type or "").strip().lower() == "outreach":
            log(
                "info",
                "dm_sender_composer_resolve_fast_path_rejected",
                username=username,
                caller=caller,
                thread_state=thread_state,
                dm_type=str(dm_type or ""),
                reason=fast_path_reason,
            )
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
    if str(dm_type or "").strip().lower() == "welcome":
        log(
            "info",
            "welcome_composer_full_path_completed",
            reason=str(state.get("invalidation_reason") or "evidence_unavailable"),
            elapsed_ms=round((time.perf_counter() - started_at) * 1000.0, 2),
        )
    return ed2, None


def _finalize_after_confirmed_outreach_send(
    d: u2.Device,
    username: str,
    pkg: str,
    *,
    post_send_signal_reason: str,
) -> dict[str, Any]:
    t_total = time.perf_counter()
    log("info", "dm_send_post_finalize_started", username=username)
    log(
        "info",
        "dm_send_post_finalize_fast_path_used",
        username=username,
        reason="send_already_confirmed",
        post_send_signal_reason=post_send_signal_reason,
        dm_type="outreach",
    )
    t_prof = time.perf_counter()
    ok_profile = bool(return_to_profile_from_dm(d, username, pkg))
    back_prof_ms = (time.perf_counter() - t_prof) * 1000.0
    if ok_profile:
        log(
            "info",
            "dm_send_post_back_to_profile_ok",
            username=username,
            ms=round(back_prof_ms, 2),
        )
    else:
        log(
            "warning",
            "dm_send_post_back_to_profile_failed",
            username=username,
            ms=round(back_prof_ms, 2),
        )
    total_ms = (time.perf_counter() - t_total) * 1000.0
    cleanup = "profile_only_search_deferred" if ok_profile else "profile_failed_search_deferred"
    log(
        "info",
        "dm_send_post_finalize_done",
        username=username,
        post_send_cleanup_reason=cleanup,
        post_send_finalize_total_ms=round(total_ms, 2),
        post_send_fast_finalize_used=True,
    )
    return {
        "post_send_signal_ok": True,
        "post_send_signal_reason": post_send_signal_reason,
        "back_to_profile_ok": ok_profile,
        "back_to_search_ok": False,
        "post_send_cleanup_reason": cleanup,
        "post_send_fast_finalize_used": True,
        "post_send_finalize_total_ms": round(total_ms, 2),
    }


def _perform_real_welcome_dm_send(
    d: u2.Device,
    *,
    username: str,
    message_body: str,
    thread_state: str,
    pkg: str,
    post_send_nav: str = "search",
    source_profile_username: str = "",
    dm_type: str = "welcome",
    thread_snapshot: dict[str, Any] | None = None,
    account_id: str = "",
    run_id: str = "",
    job_id: str = "",
    navigation_generation: str = "",
    state_transition: Callable[[str, str], None] | None = None,
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

    from dm_template_renderer import has_unresolved_template_tokens

    if has_unresolved_template_tokens(draft_text):
        log(
            "error",
            "dm_sender_unresolved_template_token_blocked",
            username=uname,
            thread_state=thread_state,
            message_len=len(draft_text),
        )
        return False, {}, "unresolved_template_token"

    welcome_mode = str(dm_type or "").strip().lower() == "welcome"
    welcome_composer_evidence: dict[str, Any] | None = None
    if welcome_mode:
        identity_ok, identity_reason, observed_username = (
            verify_welcome_dm_thread_recipient_exact(d, uname, pkg)
        )
        if not identity_ok:
            log(
                "error",
                "dm_sender_welcome_thread_identity_blocked_before_typing",
                username=uname,
                reason=identity_reason,
                observed_thread_username=observed_username or None,
            )
            return False, {}, identity_reason
        if WELCOME_COMPOSER_FAST_PATH_RUNTIME_ENABLED:
            welcome_composer_evidence, evidence_reason, evidence_observed = (
                _fresh_welcome_composer_evidence(
                    d,
                    pkg=pkg,
                    account_id=account_id,
                    run_id=run_id,
                    job_id=job_id,
                    expected_username=uname,
                    navigation_generation=navigation_generation,
                )
            )
            if welcome_composer_evidence is None:
                log(
                    "info",
                    "welcome_composer_fast_path_invalidated",
                    reason=evidence_reason,
                    evidence_age_ms=0.0,
                    expected_username=uname,
                    observed_username=evidence_observed or None,
                )
        else:
            log(
                "info",
                "WELCOME_COMPOSER_FAST_PATH_DISABLED_PENDING_PHYSICAL_STABILITY",
                expected_username=uname,
                account_id=account_id or None,
                run_id=run_id or None,
                job_id=job_id or None,
            )

    if dm_thread_shows_outgoing_message(d, draft_text):
        log(
            "info",
            "dm_sender_existing_sent_message_detected",
            username=uname,
            message_len=len(draft_text),
        )
        log(
            "info",
            "dm_sender_duplicate_send_prevented",
            username=uname,
            thread_state=thread_state,
        )
        return True, {"sent": True, "duplicate_prevented": True}, None

    flags = _dm_message_typing_flags(draft_text)
    strategy = _select_dm_typing_strategy(flags)
    log(
        "info",
        "dm_sender_typing_strategy_selected",
        username=uname,
        strategy=strategy,
        **flags,
    )

    composer_fast_path_state: dict[str, Any] = {}
    _ed, focus_err = _resolve_dm_text_composer(
        d,
        pkg=pkg,
        username=uname,
        caller="real_send",
        dm_type=dm_type,
        thread_state=thread_state,
        thread_snapshot=thread_snapshot,
        welcome_composer_evidence=welcome_composer_evidence,
        account_id=account_id,
        run_id=run_id,
        job_id=job_id,
        navigation_generation=navigation_generation,
        fast_path_state=composer_fast_path_state,
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

    if bool(composer_fast_path_state.get("used")):
        ok_comp, comp_signal = True, "fresh_welcome_composer_evidence"
    else:
        ok_comp, comp_signal = verify_dm_composer_safe(d, pkg)
    if not ok_comp:
        log(
            "error",
            "dm_sender_real_send_blocked_composer",
            username=uname,
            composer_reason=comp_signal,
        )
        return False, {}, "composer_not_safe"
    if state_transition is not None:
        state_transition("composer_exact", str(comp_signal or "full_composer_probe"))

    if not bool(getattr(config, "DM_DRAFT_TYPING_ENABLED", True)):
        return False, {}, "draft_typing_disabled"

    existing_draft = read_dm_composer_text(d)
    ok_type = False
    type_info: Any = {}
    reused_draft = False
    existing_draft_is_placeholder = _is_dm_composer_placeholder_text(existing_draft)
    if existing_draft_is_placeholder:
        log(
            "info",
            "dm_sender_existing_draft_ignored_placeholder",
            username=uname,
            draft_len=len(existing_draft),
            thread_state=thread_state,
        )
    elif existing_draft.strip() == draft_text.strip():
        log(
            "info",
            "dm_sender_existing_draft_detected",
            username=uname,
            draft_len=len(existing_draft),
        )
        log(
            "info",
            "dm_sender_existing_draft_reused",
            username=uname,
            thread_state=thread_state,
        )
        ok_type = True
        type_info = {"method": "existing_draft_reused"}
        reused_draft = True
    elif existing_draft.strip():
        log(
            "info",
            "dm_sender_existing_draft_detected",
            username=uname,
            draft_len=len(existing_draft),
            draft_mismatch=True,
        )
        log(
            "info",
            "dm_sender_existing_draft_cleared",
            username=uname,
            thread_state=thread_state,
        )
        clear_dm_draft(d)

    if not reused_draft:
        force_method = "set_text" if strategy == "set_text" else None
        ok_type, type_info = type_dm_draft_only(
            d,
            draft_text,
            pkg,
            force_method=force_method,
            composer=_ed if bool(composer_fast_path_state.get("used")) else None,
        )
        log(
            "info",
            "dm_sender_real_send_draft_typed",
            username=uname,
            draft_ok=bool(ok_type),
            strategy=strategy,
            type_info=str(type_info)[:200]
            if not isinstance(type_info, dict)
            else type_info.get("method"),
        )
        if not ok_type:
            fail_reason = "draft_typing_failed"
            if isinstance(type_info, dict):
                fail_reason = str(
                    type_info.get("reason") or type_info.get("method") or fail_reason
                )
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
                    d,
                    draft_text,
                    pkg,
                    force_method="set_text",
                    composer=_ed if bool(composer_fast_path_state.get("used")) else None,
                )
            if not ok_type:
                return False, {"type_info": type_info}, "draft_typing_failed"

    log(
        "info",
        "dm_sender_typing_completed",
        username=uname,
        strategy=strategy,
        reused_existing_draft=reused_draft,
        **flags,
    )
    composer_after_type = read_dm_composer_text(d)
    log(
        "info",
        "dm_sender_draft_text_after_typing",
        username=uname,
        draft_len=len(composer_after_type),
        draft_matches_expected=composer_after_type.strip() == draft_text.strip(),
    )

    if bool(getattr(config, "DM_VERIFY_TYPED_TEXT", True)):
        if not verify_dm_draft_text(d, draft_text):
            return False, {}, "draft_verify_failed"
    if state_transition is not None:
        state_transition("draft_exact", "draft_readback_exact")

    if welcome_mode:
        identity_ok, identity_reason, observed_username = (
            verify_welcome_dm_thread_recipient_exact(d, uname, pkg)
        )
        if not identity_ok:
            log(
                "error",
                "dm_sender_welcome_thread_identity_blocked_before_send",
                username=uname,
                reason=identity_reason,
                observed_thread_username=observed_username or None,
            )
            return False, {}, identity_reason
        if bool(composer_fast_path_state.get("used")):
            log(
                "info",
                "welcome_composer_fast_path_used",
                evidence_age_ms=composer_fast_path_state.get("evidence_age_ms"),
                exhaustive_focus_probe_skipped=True,
                header_revalidated_before_send=True,
                draft_revalidated_before_send=True,
            )

    prev_enable = bool(getattr(config, "ENABLE_REAL_DM_SEND", False))
    try:
        config.ENABLE_REAL_DM_SEND = True
        send_out = send_dm_safe(
            d,
            uname,
            draft_text,
            thread_state,
            target_row=None,
            job_id=job_id,
        )
    finally:
        config.ENABLE_REAL_DM_SEND = prev_enable

    if bool(send_out.get("send_tapped")) and state_transition is not None:
        state_transition("send_tapped", "exact_send_selector_tapped")

    if bool(send_out.get("duplicate_prevented")):
        return True, send_out, None

    if bool(send_out.get("sent")):
        if state_transition is not None:
            state_transition("outbound_verified", "strong_outbound_bubble_proof")
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
        if post_send_nav == "sender_owned":
            return True, send_out, None
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
                send_already_confirmed=True,
                job_id=job_id,
            )
            send_out["post_finalize"] = fin
            if not bool(fin.get("followers_surface_ok")):
                return True, send_out, "post_finalize_partial"
            return True, send_out, None

        post_send_signal_reason = str(send_out.get("post_send_signal_reason") or "")
        use_outreach_fast_finalize = (
            str(dm_type or "").strip().lower() == "outreach"
            and thread_state == "empty_new_thread"
            and post_send_signal_reason in {"composer_text_shortened", "composer_empty"}
        )
        if use_outreach_fast_finalize:
            fin = _finalize_after_confirmed_outreach_send(
                d,
                uname,
                pkg,
                post_send_signal_reason=post_send_signal_reason,
            )
        else:
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
    skip_post_job_restore: bool = False,
    parent_search_ready: dict[str, Any] | None = None,
    job_index: int = 0,
    jobs_total: int = 0,
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
    navigation_finished = False
    send_confirmed = False
    job_terminal_handled = False
    post_send_fast_finalize_used = False

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
        navigation_finished = True
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
            job_terminal_handled = True
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
                job_terminal_handled = True
            elif not sendable:
                skip_reason = str(skip_candidate or thread_state or "not_sendable")
                updated_job, outcome = _complete_job_skipped(
                    job, skip_reason=skip_reason, thread_state=thread_state
                )
                final_status = str((updated_job or {}).get("status") or "skipped")
                job_terminal_handled = True
            elif thread_state in ("unknown", "composer_visible_uncertain"):
                updated_job, outcome = _complete_job_failed_retry(
                    job,
                    last_error=f"thread_state_{thread_state}",
                    thread_state=thread_state,
                )
                final_status = str((updated_job or {}).get("status") or "pending")
                job_terminal_handled = True
            else:
                navigation_generation = (
                    f"{str(run_id or 'no-run')}:{job_id}:{time.monotonic_ns()}"
                )
                sent_ok, send_out, fail_reason = _perform_real_welcome_dm_send(
                    d,
                    username=recipient,
                    message_body=message_body,
                    thread_state=thread_state,
                    pkg=pkg,
                    dm_type=dm_type,
                    thread_snapshot=snap if isinstance(snap, dict) else None,
                    account_id=account_id,
                    run_id=str(run_id or ""),
                    job_id=job_id,
                    navigation_generation=navigation_generation,
                )
                post_send_fast_finalize_used = bool(
                    (send_out.get("post_finalize") or {}).get("post_send_fast_finalize_used")
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
                    send_confirmed = True
                    job_terminal_handled = True
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
                    job_terminal_handled = True
    finally:
        t_post_job = time.perf_counter()
        post_job_restore = _get_dm_sender_post_job_restore()
        if not navigation_finished:
            log(
                "warning",
                "dm_sender_post_job_restore_skipped_navigation_incomplete",
                job_id=job_id,
                recipient_username=recipient,
                dm_type=dm_type,
                job_index=job_index,
                jobs_total=jobs_total,
                send_confirmed_before_restore=send_confirmed,
                job_terminal_handled=job_terminal_handled,
                job_status_before_post_job_restore=final_status,
            )
        elif skip_post_job_restore:
            _reset_dm_sender_post_job_restore()
            _set_dm_sender_post_job_restore(
                post_job_restore_mode="skipped_send_one",
                post_job_restore_final_mode="skipped_send_one",
                post_job_restore_final_reason="single_prepared_job",
            )
            log(
                "info",
                "dm_sender_post_job_restore_skipped_send_one",
                job_id=job_id,
                recipient_username=recipient,
                dm_type=dm_type,
                jobs_total=jobs_total,
            )
            post_job_restore = _get_dm_sender_post_job_restore()
        elif dm_type == "outreach" and not bool(restore_search_after_job):
            _reset_dm_sender_post_job_restore()
            _set_dm_sender_post_job_restore(
                post_job_restore_mode="skipped_final_job",
                post_job_restore_final_mode="skipped_final_job",
                post_job_restore_final_reason="final_job_no_restore_required",
            )
            log(
                "info",
                "dm_sender_post_job_restore_skipped_final_outreach_job",
                job_id=job_id,
                recipient_username=recipient,
                dm_type=dm_type,
            )
            post_job_restore = _get_dm_sender_post_job_restore()
        else:
            log(
                "info",
                "dm_sender_before_post_job_restore",
                job_id=job_id,
                recipient_username=recipient,
                dm_type=dm_type,
                job_index=job_index,
                jobs_total=jobs_total,
                send_confirmed_before_restore=send_confirmed,
                job_terminal_handled=job_terminal_handled,
                job_status_before_post_job_restore=final_status,
                outcome=outcome,
            )
            _safe_teardown_navigation(
                d,
                recipient,
                pkg=pkg,
                account_username=account_username,
                prefer_back_stack_to_search=(dm_type == "outreach"),
            )
            post_job_restore = _get_dm_sender_post_job_restore()
        post_job_ms = round((time.perf_counter() - t_post_job) * 1000.0, 2)
        if navigation_finished:
            if not str(post_job_restore.get("post_job_restore_final_mode") or ""):
                _set_dm_sender_post_job_restore(
                    post_job_restore_mode="restore_failed",
                    post_job_restore_final_mode="restore_failed",
                    post_job_restore_final_reason="restore_surface_prepare_failed",
                )
                post_job_restore = _get_dm_sender_post_job_restore()
            log(
                "info",
                "post_job_restore_final_mode",
                job_id=job_id,
                recipient_username=recipient,
                dm_type=dm_type,
                job_index=job_index,
                jobs_total=jobs_total,
                post_job_restore_final_mode=str(
                    post_job_restore.get("post_job_restore_final_mode") or ""
                ),
                post_job_restore_final_reason=str(
                    post_job_restore.get("post_job_restore_final_reason") or ""
                )
                or None,
                post_job_restore_attempts_count=int(
                    post_job_restore.get("post_job_restore_attempts_count") or 0
                ),
                post_job_restore_used_fresh_open_search=bool(
                    post_job_restore.get("post_job_restore_used_fresh_open_search")
                ),
                post_job_restore_used_back_stack=bool(
                    post_job_restore.get("post_job_restore_used_back_stack")
                ),
                post_job_restore_success_after_retry=bool(
                    post_job_restore.get("post_job_restore_success_after_retry")
                ),
            )
            log(
                "info",
                "dm_sender_post_job_restore_summary",
                job_id=job_id,
                recipient_username=recipient,
                dm_type=dm_type,
                job_index=job_index,
                jobs_total=jobs_total,
                post_job_restore_mode=str(post_job_restore.get("post_job_restore_mode") or ""),
                post_job_restore_final_mode=str(
                    post_job_restore.get("post_job_restore_final_mode") or ""
                ),
                post_job_restore_final_reason=str(
                    post_job_restore.get("post_job_restore_final_reason") or ""
                )
                or None,
                post_job_restore_attempts_count=int(
                    post_job_restore.get("post_job_restore_attempts_count") or 0
                ),
                post_job_restore_used_fresh_open_search=bool(
                    post_job_restore.get("post_job_restore_used_fresh_open_search")
                ),
                post_job_restore_used_back_stack=bool(
                    post_job_restore.get("post_job_restore_used_back_stack")
                ),
                post_job_restore_success_after_retry=bool(
                    post_job_restore.get("post_job_restore_success_after_retry")
                ),
                post_job_back_to_previous_search_attempted=bool(
                    post_job_restore.get("post_job_back_to_previous_search_attempted")
                ),
                post_job_previous_search_results_detected=bool(
                    post_job_restore.get("post_job_previous_search_results_detected")
                ),
                post_job_previous_search_username_present=bool(
                    post_job_restore.get("post_job_previous_search_username_present")
                ),
                post_job_reuse_previous_search_surface_ms=float(
                    post_job_restore.get("post_job_reuse_previous_search_surface_ms") or 0.0
                ),
                post_job_fallback_open_search_reason=str(
                    post_job_restore.get("post_job_fallback_open_search_reason") or ""
                )
                or None,
                post_job_ms=post_job_ms,
                send_confirmed_before_restore=send_confirmed,
                job_terminal_handled=job_terminal_handled,
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
        "navigation_ms": float(nav_timings.get("navigation_ms") or 0.0),
        "navigation_to_username_typed_total_ms": float(
            nav_timings.get("navigation_to_username_typed_total_ms") or 0.0
        ),
        "sender_prepare_to_open_search_ms": float(
            nav_timings.get("sender_prepare_to_open_search_ms") or 0.0
        ),
        "search_ms": float(nav_timings.get("search_ms") or 0.0),
        "thread_open_ms": float(nav_timings.get("thread_open_ms") or 0.0),
        "post_job_ms": post_job_ms,
        "post_job_restore": post_job_restore,
        "post_job_restore_mode": str(post_job_restore.get("post_job_restore_mode") or ""),
        "post_job_restore_final_mode": str(
            post_job_restore.get("post_job_restore_final_mode") or ""
        ),
        "post_job_restore_final_reason": str(
            post_job_restore.get("post_job_restore_final_reason") or ""
        ),
        "post_job_restore_attempts_count": int(
            post_job_restore.get("post_job_restore_attempts_count") or 0
        ),
        "post_job_restore_used_fresh_open_search": bool(
            post_job_restore.get("post_job_restore_used_fresh_open_search")
        ),
        "post_job_restore_used_back_stack": bool(
            post_job_restore.get("post_job_restore_used_back_stack")
        ),
        "post_job_restore_success_after_retry": bool(
            post_job_restore.get("post_job_restore_success_after_retry")
        ),
        "post_job_previous_search_results_detected": bool(
            post_job_restore.get("post_job_previous_search_results_detected")
        ),
        "post_job_fallback_open_search_reason": str(
            post_job_restore.get("post_job_fallback_open_search_reason") or ""
        ),
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
        "post_send_fast_finalize_used": bool(post_send_fast_finalize_used),
        "post_job_clear_previous_username_ms": float(
            nav_timings.get("post_job_clear_previous_username_ms") or 0.0
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
    prepared_jobs: list[dict[str, Any]] | None = None,
    settings_override: dict[str, Any] | None = None,
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
    prepared_job_list = list(prepared_jobs or [])
    using_prepared_jobs = prepared_jobs is not None
    if max_jobs is None:
        max_jobs = (
            len(prepared_job_list)
            if using_prepared_jobs
            else int(getattr(config, "WELCOME_SESSION_SEND_MAX_JOBS", 3) or 3)
        )
    max_jobs = max(0, int(max_jobs))
    if using_prepared_jobs:
        max_jobs = min(max_jobs, len(prepared_job_list))

    real_enabled, real_source = resolve_dm_sender_real_send_enabled_for_type(dm_type_resolved)
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
        "using_prepared_jobs": bool(using_prepared_jobs),
        "prepared_jobs_count": len(prepared_job_list),
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
        "search_ready_to_first_username_typed_ms": 0.0,
        "first_job_sender_prepare_ms": 0.0,
        "avg_inter_job_ms": 0.0,
        "inter_job_total_ms_values": [],
        "previous_search_reuse_count": 0,
        "previous_search_reuse_fail_count": 0,
        "fallback_open_search_between_jobs_count": 0,
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
        "post_send_fast_finalize_used": False,
    }

    log(
        "info",
        "dm_sender_send_started",
        account_id=aid,
        run_id=run_id,
        dm_type=dm_type_resolved,
        max_jobs=max_jobs,
        using_prepared_jobs=bool(using_prepared_jobs),
        prepared_jobs_count=len(prepared_job_list),
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

    if settings_override is not None:
        settings = dict(settings_override or {})
    else:
        try:
            settings = supabase_client.get_account_dm_settings(aid) or {}
        except Exception as e:
            log("error", "dm_sender_settings_load_failed", error=str(e))
            settings = {}

    last_result: dict[str, Any] = {}
    last_recipient_username = ""
    previous_restore_mode = ""
    for job_index in range(max_jobs):
        if using_prepared_jobs:
            job = prepared_job_list[job_index] if job_index < len(prepared_job_list) else None
            claim_ms = 0.0
        else:
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
            using_prepared_jobs=bool(using_prepared_jobs),
            job_id=str((job or {}).get("id") or "") or None,
        )
        if not job:
            log("info", "dm_sender_no_pending_job", account_id=aid, dm_type=dm_type_resolved)
            break

        summary["jobs_claimed_count"] += 1
        recipient = str(job.get("recipient_username") or "").strip()
        summary["processed_recipients"].append(recipient)

        if account_protection_lists.is_interaction_blocked(recipient):
            _complete_job_skipped(
                job,
                skip_reason="interaction_blacklist",
                thread_state="not_opened_protection_list",
            )
            summary["jobs_skipped_count"] += 1
            summary["skipped_recipients"].append(recipient)
            log(
                "info",
                "interaction_blacklist_action_skipped",
                account_id=aid,
                run_id=run_id,
                job_id=str(job.get("id") or ""),
                action=f"{dm_type_resolved}_dm_send",
                username=recipient or None,
                reason="interaction_blacklist",
            )
            continue

        skip_post_job_restore = bool(using_prepared_jobs and max_jobs == 1)
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
            skip_post_job_restore=skip_post_job_restore,
            parent_search_ready=parent_search_ready,
            job_index=job_index,
            jobs_total=max_jobs,
        )
        if job_index > 0:
            inter_job_ms = float(
                last_result.get("navigation_to_username_typed_total_ms") or 0.0
            )
            summary["inter_job_total_ms_values"].append(inter_job_ms)
            vals = list(summary.get("inter_job_total_ms_values") or [])
            summary["avg_inter_job_ms"] = round(
                sum(float(v or 0.0) for v in vals) / max(1, len(vals)),
                2,
            )
            log(
                "info",
                "dm_sender_inter_job_timing",
                job_index=job_index,
                jobs_total=max_jobs,
                inter_job_total_ms=inter_job_ms,
                next_username_ready_ms=inter_job_ms,
                post_job_restore_mode=previous_restore_mode or None,
            )
        restore_mode = str(
            last_result.get("post_job_restore_final_mode")
            or last_result.get("post_job_restore_mode")
            or ""
        )
        if restore_mode in (
            "previous_search_results_reused",
            "previous_search_results_reused_after_retry",
        ):
            summary["previous_search_reuse_count"] += 1
        elif restore_mode == "fresh_open_search_used":
            summary["previous_search_reuse_fail_count"] += 1
            summary["fallback_open_search_between_jobs_count"] += 1
        elif restore_mode == "restore_failed":
            summary["previous_search_reuse_fail_count"] += 1
        previous_restore_mode = restore_mode
        summary["total_navigation_ms"] = round(
            float(summary.get("total_navigation_ms") or 0.0)
            + float(last_result.get("navigation_ms") or 0.0),
            2,
        )
        if job_index == 0:
            summary["search_ready_to_first_username_typed_ms"] = float(
                last_result.get("navigation_to_username_typed_total_ms") or 0.0
            )
            summary["first_job_sender_prepare_ms"] = float(
                last_result.get("sender_prepare_to_open_search_ms") or 0.0
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
        if bool(last_result.get("post_send_fast_finalize_used")):
            summary["post_send_fast_finalize_used"] = True
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
