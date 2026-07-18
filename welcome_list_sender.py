"""
V4.5 — Welcome DM list-native sender: Followers row → profile → Message → send → action-bar backs.

Search-based dm_sender_send remains for Outreach / external prospects.
"""

from __future__ import annotations

import hashlib
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
    _complete_job_send_unverified_quarantine,
    _complete_job_skipped,
    _dm_sender_session_should_abort,
    _evaluate_welcome_sendability,
    _perform_real_welcome_dm_send,
    _reset_dm_sender_session_abort,
    _resolve_dm_sender_only_job_id,
    _resolve_reserved_by,
    resolve_welcome_dm_real_send_enabled,
)
from instagram_navigation import (
    _capture_welcome_dm_forensics_artifacts,
    detect_followers_list_screen_fresh,
    followers_clear_detect_hierarchy_cache,
    followers_refresh_detect_hierarchy_cache,
    followers_session_clear_list_committed_open,
    followers_suggestions_boundary_from_cached_hierarchy,
    harvest_visible_followers_rows,
    get_last_dm_thread_classify_snapshot,
    is_dm_thread_screen,
    get_last_dm_thread_attempted,
    open_dm_thread_from_profile,
    reset_dm_thread_probe_state,
    return_welcome_list_from_dm_to_followers,
    scroll_followers_list_backward,
    scroll_followers_list_forward,
    tap_followers_list_username_row,
    verify_profile,
    verify_welcome_profile_username_exact,
)
from logs import log

_LAST_WELCOME_LIST_SENDER_SUMMARY: dict[str, Any] = {}


def get_last_welcome_list_sender_summary() -> dict[str, Any]:
    return dict(_LAST_WELCOME_LIST_SENDER_SUMMARY)


def _norm_username(raw: str) -> str:
    return str(raw or "").strip().lstrip("@").lower()


_SEND_UNVERIFIED_REASONS = frozenset(
    {
        "send_unverified",
        "send_without_strong_outbound_proof",
        "welcome_outbound_unverified",
    }
)

_WELCOME_ROW_SNAPSHOT_TTL_MS = 750.0
_WELCOME_REAL_FOLLOWER_CTA_CLASSES = frozenset(
    {"message", "follow_back", "following", "requested", "contact"}
)

_WELCOME_STRUCTURED_FAILURES = {
    "profile_username_mismatch": "welcome_profile_identity_mismatch",
    "thread_recipient_identity_mismatch": "welcome_thread_identity_mismatch",
    "draft_verify_failed": "welcome_draft_verification_failed",
    "draft_typing_failed": "welcome_draft_verification_failed",
    "send_unverified": "welcome_outbound_unverified",
    "send_without_strong_outbound_proof": "welcome_outbound_unverified",
    "post_finalize_partial": "welcome_followers_restore_failed",
}


def _welcome_structured_failure_reason(reason: str | None) -> str:
    raw = str(reason or "welcome_unknown_failure").strip()
    return _WELCOME_STRUCTURED_FAILURES.get(raw, raw)

_WELCOME_STATE_TRANSITIONS = {
    "initial": {"followers_stable"},
    "followers_stable": {"planned_row_freshly_resolved", "next_job_ready"},
    "planned_row_freshly_resolved": {"target_profile_exact"},
    "target_profile_exact": {"dm_thread_exact"},
    "dm_thread_exact": {"composer_exact"},
    "composer_exact": {"draft_exact"},
    "draft_exact": {"send_tapped"},
    "send_tapped": {"outbound_verified"},
    "outbound_verified": {"thread_exit"},
    "thread_exit": {"followers_restored"},
    "followers_restored": {"next_job_ready"},
    "next_job_ready": {"planned_row_freshly_resolved", "followers_stable"},
}


class _WelcomeStateMachine:
    """Nominal Welcome contract; failures are recorded without inventing transitions."""

    def __init__(self, *, account_id: str, run_id: str | None) -> None:
        self.account_id = str(account_id or "")
        self.run_id = str(run_id or "")
        self.current = "initial"
        self.history: list[dict[str, Any]] = []

    def transition(
        self,
        state: str,
        *,
        proof: str,
        owner: str,
        job_id: str = "",
        username: str = "",
    ) -> None:
        target = str(state or "")
        allowed = _WELCOME_STATE_TRANSITIONS.get(self.current, set())
        if target not in allowed:
            raise RuntimeError(
                f"invalid_welcome_state_transition:{self.current}->{target}"
            )
        previous = self.current
        self.current = target
        entry = {
            "from": previous,
            "state": target,
            "proof": str(proof or ""),
            "owner": str(owner or ""),
            "job_id": str(job_id or ""),
            "username": str(username or ""),
        }
        self.history.append(entry)
        log(
            "info",
            "welcome_state_transition",
            account_id=self.account_id,
            run_id=self.run_id or None,
            **entry,
        )

    def fail(
        self,
        reason: str,
        *,
        owner: str,
        job_id: str = "",
        username: str = "",
    ) -> None:
        log(
            "error",
            "welcome_state_failed",
            account_id=self.account_id,
            run_id=self.run_id or None,
            state=self.current,
            reason=str(reason or "welcome_state_unknown_failure"),
            owner=str(owner or ""),
            job_id=str(job_id or ""),
            username=str(username or ""),
        )

    def reset_after_clean_skip(
        self,
        *,
        proof: str,
        job_id: str = "",
        username: str = "",
    ) -> None:
        previous = self.current
        self.current = "followers_stable"
        entry = {
            "from": previous,
            "state": "followers_stable",
            "proof": str(proof or "clean_skip_followers_restored"),
            "owner": "welcome_sender_job_loop",
            "job_id": str(job_id or ""),
            "username": str(username or ""),
        }
        self.history.append(entry)
        log(
            "info",
            "welcome_state_clean_skip_reset",
            account_id=self.account_id,
            run_id=self.run_id or None,
            **entry,
        )


def _job_requires_send_unverified_quarantine(job: dict[str, Any]) -> bool:
    if str(job.get("last_error") or "") in _SEND_UNVERIFIED_REASONS:
        return True
    metadata = job.get("metadata") or {}
    return bool(
        isinstance(metadata, dict)
        and str(metadata.get("send_verification_status") or "") == "unverified"
    )


def _capture_welcome_failure_before_cleanup(
    d: u2.Device,
    *,
    account_username: str,
    expected_username: str,
    job_id: str,
    navigation_state: str,
) -> dict[str, Any]:
    """Capture the final Android state before runner cleanup can force-stop Instagram."""
    artifacts = _capture_welcome_dm_forensics_artifacts(
        d,
        expected_username or account_username,
        artifact_suffix=f"failure_before_cleanup_{job_id or 'no_job'}",
    )
    current: dict[str, Any] = {}
    try:
        current = dict(d.app_current() or {})
    except Exception as exc:
        current = {"error": str(exc)[:200]}
    out = {
        **artifacts,
        "account_username": account_username,
        "expected_username": expected_username or None,
        "last_job_id": job_id or None,
        "last_navigation_state": navigation_state,
        "current_package": current.get("package"),
        "current_activity": current.get("activity"),
    }
    log("error", "welcome_failure_forensics_captured_before_cleanup", **out)
    return out


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
    job_ids = {
        _norm_username(str(entry.get("username") or "")): str(entry.get("job_id") or "")
        for entry in scan_summary.get("new_follower_job_ids_enqueued") or []
        if isinstance(entry, dict)
    }
    for row in scan_summary.get("new_follower_visible_rows_enqueued") or []:
        if not isinstance(row, dict):
            continue
        key = _norm_username(str(row.get("username") or ""))
        if key:
            anchor = dict(row)
            anchor["job_id"] = job_ids.get(key) or None
            anchor["scan_generation"] = str(scan_summary.get("run_id") or "")
            anchor["captured_at_monotonic"] = scan_summary.get(
                "scan_completed_at_monotonic"
            )
            out[key] = anchor
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


def _anchor_age_ms(anchor: dict[str, Any] | None) -> float | None:
    if not anchor:
        return None
    try:
        captured = float(anchor.get("captured_at_monotonic"))
    except (TypeError, ValueError):
        return None
    return max(0.0, round((time.perf_counter() - captured) * 1000.0, 2))


def _welcome_row_bounds_valid(row: dict[str, Any]) -> bool:
    bounds = dict(row.get("tap_bounds") or row.get("bounds") or {})
    try:
        left = int(bounds["left"])
        top = int(bounds["top"])
        right = int(bounds["right"])
        bottom = int(bounds["bottom"])
    except (KeyError, TypeError, ValueError):
        return False
    return right > left and bottom > top and left >= 0 and top >= 0


def _welcome_row_is_real_follower(row: dict[str, Any]) -> bool:
    return str(row.get("row_cta_xml_class") or "").strip().lower() in (
        _WELCOME_REAL_FOLLOWER_CTA_CLASSES
    )


def _welcome_rows_viewport_fingerprint(rows: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for row in rows:
        username = _norm_username(str(row.get("username") or ""))
        bounds = dict(row.get("username_bounds") or row.get("bounds") or {})
        cta = str(row.get("row_cta_xml_class") or "").strip().lower()
        parts.append(f"{username}:{cta}:{bounds}")
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


def _mark_fresh_welcome_row(
    row: dict[str, Any],
    *,
    job_id: str,
    scan_generation: str,
    navigation_generation: str,
    viewport_fingerprint: str,
    snapshot_captured_at: float,
) -> dict[str, Any]:
    current = dict(row)
    current["welcome_job_id"] = str(job_id or "")
    current["welcome_scan_generation"] = str(scan_generation or "")
    current["welcome_navigation_generation"] = str(navigation_generation or "")
    current["welcome_viewport_fingerprint"] = str(viewport_fingerprint or "")
    current["welcome_snapshot_captured_at"] = float(snapshot_captured_at)
    current["welcome_fresh_identity_resolved"] = True
    return current


def _authorize_welcome_row_tap(
    row: dict[str, Any] | None,
    *,
    expected_username: str,
    job_id: str,
    scan_generation: str,
    navigation_generation: str,
) -> tuple[bool, str, float, str]:
    observed_username = str((row or {}).get("username") or "").strip()
    expected_key = _norm_username(expected_username)
    observed_key = _norm_username(observed_username)
    try:
        captured_at = float((row or {}).get("welcome_snapshot_captured_at"))
        snapshot_age_ms = max(
            0.0, round((time.perf_counter() - captured_at) * 1000.0, 2)
        )
    except (TypeError, ValueError):
        snapshot_age_ms = float("inf")

    reason = "authorized"
    if not row or not bool(row.get("welcome_fresh_identity_resolved")):
        reason = "fresh_identity_missing"
    elif not expected_key or observed_key != expected_key:
        reason = "username_mismatch"
    elif str(row.get("welcome_job_id") or "") != str(job_id or ""):
        reason = "job_id_mismatch"
    elif str(row.get("welcome_scan_generation") or "") != str(
        scan_generation or ""
    ):
        reason = "scan_generation_changed"
    elif str(row.get("welcome_navigation_generation") or "") != str(
        navigation_generation or ""
    ):
        reason = "navigation_generation_changed"
    elif snapshot_age_ms > _WELCOME_ROW_SNAPSHOT_TTL_MS:
        reason = "snapshot_ttl_expired"
    elif not _welcome_row_is_real_follower(row):
        reason = "suggestions_or_non_follower_row"
    elif not _welcome_row_bounds_valid(row):
        reason = "current_bounds_invalid"

    return reason == "authorized", reason, snapshot_age_ms, observed_username


def _resolve_followers_row(
    d: u2.Device,
    username: str,
    *,
    account_username: str,
    scan_anchors: dict[str, dict[str, Any]],
    screen_index: int = 0,
    allow_scan_anchor_fast_path: bool = False,
    job_id: str = "",
    scan_generation: str = "",
    navigation_generation: str = "",
    anchor_invalidation_reason: str = "fresh_identity_required_before_tap",
    target_screen_index: int | None = None,
    current_screen_index: int | None = None,
) -> tuple[dict[str, Any] | None, int, str, dict[str, Any]]:
    """
    Scan anchors retain identity/order only. Every tappable row is freshly resolved.
    Returns (row, scroll_count, lookup_path, debug_meta).
    """
    uname = str(username or "").strip()
    src = str(account_username or "").strip()
    key = _norm_username(uname)
    debug: dict[str, Any] = {
        "username": uname,
        "scan_anchor_present": key in scan_anchors,
        "scan_anchor_fast_path_allowed": bool(allow_scan_anchor_fast_path),
        "scan_anchor_coordinates_reused": False,
        "job_id": str(job_id or ""),
        "scan_generation": str(scan_generation or ""),
        "navigation_generation": str(navigation_generation or ""),
    }

    anchor = scan_anchors.get(key)
    if anchor is not None:
        log(
            "info",
            "welcome_row_anchor_invalidated",
            job_id=str(job_id or anchor.get("job_id") or ""),
            expected_username=uname,
            reason=str(anchor_invalidation_reason or "fresh_identity_required_before_tap"),
            anchor_age_ms=_anchor_age_ms(anchor),
            previous_navigation_generation=str(
                anchor.get("navigation_generation")
                or anchor.get("scan_generation")
                or ""
            ),
        )

    resolve_started = time.perf_counter()
    surface_ok, _surface_xml = _verify_followers_surface(
        d,
        account_username=src,
        context="welcome_row_fresh_resolve",
    )
    if not surface_ok:
        debug["lookup_path_used"] = "followers_surface_not_stable"
        log(
            "warning",
            "welcome_row_tap_blocked",
            reason="followers_surface_not_stable",
            expected_username=uname,
            observed_username=None,
            job_id=str(job_id or ""),
        )
        return None, 0, "followers_surface_not_stable", debug

    snapshot_captured_at = time.perf_counter()
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
    viewport_fingerprint = _welcome_rows_viewport_fingerprint(rows)
    debug["viewport_fingerprint"] = viewport_fingerprint

    log(
        "info",
        "welcome_row_fresh_resolve_started",
        job_id=str(job_id or ""),
        expected_username=uname,
        viewport_fingerprint=viewport_fingerprint,
    )

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

    for row in rows:
        if (
            _norm_username(str(row.get("username") or "")) == key
            and _welcome_row_is_real_follower(row)
            and _welcome_row_bounds_valid(row)
        ):
            current = _mark_fresh_welcome_row(
                row,
                job_id=job_id,
                scan_generation=scan_generation,
                navigation_generation=navigation_generation,
                viewport_fingerprint=viewport_fingerprint,
                snapshot_captured_at=snapshot_captured_at,
            )
            elapsed_ms = round((time.perf_counter() - resolve_started) * 1000.0, 2)
            log(
                "info",
                "welcome_list_sender_row_lookup_fresh_match",
                username=uname,
                row_index=row.get("row_index"),
                extraction_source=row.get("extraction_source"),
                hierarchy_source=row.get("hierarchy_source"),
            )
            log(
                "info",
                "welcome_row_fresh_resolve_completed",
                job_id=str(job_id or ""),
                expected_username=uname,
                username_match=True,
                current_bounds=current.get("tap_bounds") or current.get("bounds"),
                elapsed_ms=elapsed_ms,
                snapshot_age_ms=round(
                    (time.perf_counter() - snapshot_captured_at) * 1000.0, 2
                ),
                viewport_fingerprint=viewport_fingerprint,
            )
            debug["resolved_screen_index"] = int(screen_index)
            debug["lookup_path_used"] = "fresh_visible"
            return current, 0, "fresh_visible", debug

    boundary = followers_suggestions_boundary_from_cached_hierarchy(
        previously_valid_followers_rows=True
    )
    log(
        "info",
        "welcome_list_sender_row_lookup_fresh_miss",
        username=uname,
        visible_usernames=visible_usernames,
        target_in_visible_list=key in visible_keys,
        scan_anchor_present=bool(debug["scan_anchor_present"]),
        suggestions_boundary=bool(boundary.get("is_boundary")),
    )

    if bool(boundary.get("is_boundary")) and key in visible_keys:
        debug["lookup_path_used"] = "welcome_suggestions_boundary_reached"
        log(
            "warning",
            "welcome_row_tap_blocked",
            reason="suggestions_boundary_target_not_real_follower",
            expected_username=uname,
            observed_username=uname,
            job_id=str(job_id or ""),
        )
        return None, 0, "welcome_suggestions_boundary_reached", debug

    if key in visible_keys:
        debug["lookup_path_used"] = "fresh_miss_despite_visible_username"
        return None, 0, "fresh_miss_despite_visible", debug

    current_idx = int(
        screen_index if current_screen_index is None else current_screen_index
    )
    target_idx = (
        None if target_screen_index is None else max(0, int(target_screen_index))
    )
    at_suggestions_boundary = bool(boundary.get("is_boundary"))
    direction = (
        "backward"
        if at_suggestions_boundary or (target_idx is not None and target_idx < current_idx)
        else "forward"
    )
    log(
        "info",
        "welcome_viewport_reposition_started",
        username=uname,
        reason="target_absent_from_visible_snapshot",
        direction=direction,
        target_screen_index=target_idx,
        current_screen_index=current_idx,
        initial_visible_usernames=visible_usernames,
        scan_anchor_present=bool(debug["scan_anchor_present"]),
        suggestions_boundary=at_suggestions_boundary,
    )
    debug["visible_usernames_before_scroll"] = list(visible_usernames)
    configured_max = max(
        0, int(getattr(config, "WELCOME_LIST_SENDER_MAX_SCROLL_FIND", 3) or 3)
    )
    estimated_distance = (
        abs(current_idx - target_idx) if target_idx is not None else configured_max
    )
    max_scrolls = min(configured_max, max(1, estimated_distance + 1))
    debug["reposition_direction"] = direction
    debug["reposition_max_attempts"] = max_scrolls
    settle_s = float(
        getattr(config, "WELCOME_LIST_SENDER_SCROLL_SETTLE_S", 0.45) or 0.45
    )
    last_visible = list(visible_usernames)
    for scrolls in range(1, max_scrolls + 1):
        if direction == "backward":
            moved = scroll_followers_list_backward(
                d,
                source_profile_username=src,
                scroll_steps=1,
                scroll_profile="compact",
            )
            step_screen_index = max(0, current_idx - scrolls)
        else:
            moved = scroll_followers_list_forward(
                d,
                source_profile_username=src,
                bypass_post_tap_capture_gate=True,
                bypass_scroll_xml_guards=True,
            )
            step_screen_index = current_idx + scrolls
        if not moved:
            break
        followers_clear_detect_hierarchy_cache()
        if settle_s > 0:
            time.sleep(min(settle_s, 1.5))
        step_captured_at = time.perf_counter()
        step_rows, step_meta = harvest_visible_followers_rows(
            d,
            source_profile_username=src,
            runtime_seen=set(),
            force_fresh_hierarchy=True,
            screen_index=step_screen_index,
        )
        last_visible = [
            str(candidate.get("username") or "")
            for candidate in step_rows
            if candidate.get("username")
        ]
        step_fingerprint = _welcome_rows_viewport_fingerprint(step_rows)
        step_boundary = followers_suggestions_boundary_from_cached_hierarchy(
            previously_valid_followers_rows=True
        )
        log(
            "info",
            "welcome_viewport_reposition_step",
            username=uname,
            direction=direction,
            attempt=scrolls,
            screen_index=step_screen_index,
            visible_usernames=list(last_visible),
            viewport_fingerprint=step_fingerprint,
            suggestions_boundary=bool(step_boundary.get("is_boundary")),
        )
        for candidate in step_rows:
            if (
                _norm_username(str(candidate.get("username") or "")) == key
                and _welcome_row_is_real_follower(candidate)
                and _welcome_row_bounds_valid(candidate)
            ):
                current = _mark_fresh_welcome_row(
                    candidate,
                    job_id=job_id,
                    scan_generation=scan_generation,
                    navigation_generation=(
                        f"{navigation_generation}:{direction}:{scrolls}"
                    ),
                    viewport_fingerprint=step_fingerprint,
                    snapshot_captured_at=step_captured_at,
                )
                debug["resolved_navigation_generation"] = current.get(
                    "welcome_navigation_generation"
                )
                debug["visible_usernames_after_last_scroll"] = list(last_visible)
                debug["resolved_screen_index"] = step_screen_index
                debug["lookup_path_used"] = f"bounded_{direction}_find"
                log(
                    "info",
                    "welcome_row_fresh_resolve_completed",
                    job_id=str(job_id or ""),
                    expected_username=uname,
                    username_match=True,
                    current_bounds=current.get("tap_bounds") or current.get("bounds"),
                    elapsed_ms=round(
                        (time.perf_counter() - resolve_started) * 1000.0, 2
                    ),
                    snapshot_age_ms=round(
                        (time.perf_counter() - step_captured_at) * 1000.0, 2
                    ),
                    viewport_fingerprint=step_fingerprint,
                )
                log(
                    "info",
                    "welcome_viewport_reposition_completed",
                    username=uname,
                    direction=direction,
                    attempts_used=scrolls,
                    recovered=True,
                    final_screen_index=step_screen_index,
                )
                return current, scrolls, f"bounded_{direction}_find", debug
        if direction == "forward" and bool(step_boundary.get("is_boundary")):
            debug["lookup_path_used"] = "welcome_suggestions_boundary_reached"
            debug["visible_usernames_after_last_scroll"] = list(last_visible)
            log(
                "warning",
                "welcome_row_tap_blocked",
                reason="welcome_suggestions_boundary_reached",
                expected_username=uname,
                observed_username=None,
                job_id=str(job_id or ""),
            )
            return None, scrolls, "welcome_suggestions_boundary_reached", debug

    debug["visible_usernames_after_last_scroll"] = list(last_visible)
    debug["lookup_path_used"] = "welcome_planned_row_not_found"
    log(
        "warning",
        "welcome_viewport_reposition_completed",
        username=uname,
        direction=direction,
        attempts_used=max_scrolls,
        recovered=False,
        final_screen_index=max(0, current_idx - max_scrolls)
        if direction == "backward"
        else current_idx + max_scrolls,
    )
    log(
        "info",
        "welcome_row_fresh_resolve_completed",
        job_id=str(job_id or ""),
        expected_username=uname,
        username_match=False,
        current_bounds=None,
        elapsed_ms=round((time.perf_counter() - resolve_started) * 1000.0, 2),
        snapshot_age_ms=None,
        viewport_fingerprint=debug.get("viewport_fingerprint"),
    )
    return None, max_scrolls, "welcome_planned_row_not_found", debug


def _session_scan_jobs_from_summary(scan_summary: dict[str, Any]) -> list[dict[str, Any]]:
    """Job entries enqueued during the current scan, in discovery order."""
    out: list[dict[str, Any]] = []
    for entry in scan_summary.get("new_follower_job_ids_enqueued") or []:
        if not isinstance(entry, dict):
            continue
        jid = str(entry.get("job_id") or "").strip()
        username = str(entry.get("username") or "").strip()
        if not jid or not username:
            continue
        out.append(dict(entry))
    return out


def _sender_start_visible_usernames(
    d: u2.Device,
    *,
    account_username: str,
    screen_index: int,
) -> tuple[list[str], dict[str, Any]]:
    rows, meta = harvest_visible_followers_rows(
        d,
        source_profile_username=account_username,
        runtime_seen=set(),
        force_fresh_hierarchy=True,
        screen_index=int(screen_index),
    )
    visible = [str(r.get("username") or "") for r in rows if r.get("username")]
    return visible, meta


def _restore_followers_list_to_scan_start_zone(
    d: u2.Device,
    *,
    account_username: str,
    pkg: str,
    scan_final_screen_index: int = 0,
) -> tuple[bool, str]:
    """
    Return the followers list to the scan-start viewport.

    Prefer in-place backward scroll while the committed scan surface is still open.
    Only fall back to profile back + reopen after clearing the committed-open guard.
    """
    from instagram_navigation import tap_instagram_action_bar_back_button
    from own_profile_navigation import open_own_followers_list_from_own_profile

    settle_s = float(
        getattr(config, "WELCOME_LIST_SENDER_BACK_SETTLE_S", 0.45) or 0.45
    )
    scroll_steps = max(0, int(scan_final_screen_index))
    det, _ = detect_followers_list_screen_fresh(
        d, source_profile_username=account_username
    )
    on_followers = bool(det.get("is_followers_list"))

    if on_followers and scroll_steps == 0:
        followers_refresh_detect_hierarchy_cache(d, screen_index=0)
        return True, "already_on_followers_scan_start"

    if on_followers and scroll_steps > 0:
        scrolled = scroll_followers_list_backward(
            d,
            source_profile_username=account_username,
            scroll_steps=scroll_steps,
        )
        followers_clear_detect_hierarchy_cache()
        followers_refresh_detect_hierarchy_cache(d, screen_index=0)
        det_after, _ = detect_followers_list_screen_fresh(
            d, source_profile_username=account_username
        )
        if scrolled and bool(det_after.get("is_followers_list")):
            log(
                "info",
                "welcome_list_sender_reposition_scroll_restore_ok",
                account_username=account_username,
                scroll_steps=scroll_steps,
                open_detection_method=det_after.get("open_detection_method"),
            )
            return True, "followers_scroll_backward_to_scan_start"
        log(
            "warning",
            "welcome_list_sender_reposition_scroll_restore_failed",
            account_username=account_username,
            scroll_steps=scroll_steps,
            scrolled=scrolled,
            is_followers_list=bool(det_after.get("is_followers_list")),
            action_bar_title=det_after.get("action_bar_title"),
        )

    followers_session_clear_list_committed_open(account_username)
    det_now, _ = detect_followers_list_screen_fresh(
        d, source_profile_username=account_username
    )
    if bool(det_now.get("is_followers_list")):
        tapped, _method = tap_instagram_action_bar_back_button(d, pkg)
        if tapped and settle_s > 0:
            time.sleep(settle_s)
    ok_open, open_meta = open_own_followers_list_from_own_profile(
        d, account_username, pkg=pkg
    )
    if not ok_open:
        reason = str((open_meta or {}).get("failure_reason") or "reopen_failed")
        return False, reason
    followers_clear_detect_hierarchy_cache()
    followers_refresh_detect_hierarchy_cache(d, screen_index=0)
    return True, "profile_back_reopen_followers"


def _anchor_available_for_username(
    username: str,
    scan_anchors: dict[str, dict[str, Any]],
) -> bool:
    key = _norm_username(username)
    if key not in scan_anchors:
        return False
    return _row_from_scan_anchor(scan_anchors[key]) is not None


def _order_scan_jobs_viewport_first(
    candidates: list[dict[str, Any]],
    *,
    visible_keys: set[str],
) -> list[dict[str, Any]]:
    """Visible rows first, then higher screen_index (closer to end-of-scan viewport)."""

    def _sort_key(entry: dict[str, Any]) -> tuple[int, int, int]:
        ukey = _norm_username(str(entry.get("username") or ""))
        visible_rank = 0 if ukey in visible_keys else 1
        screen_idx = int(entry.get("screen_index") or 0)
        row_idx = entry.get("row_index")
        row_rank = int(row_idx) if row_idx is not None else 9999
        return (visible_rank, -screen_idx, row_rank)

    return sorted(candidates, key=_sort_key)


def _build_planned_session_jobs(
    selected: list[dict[str, Any]],
    *,
    selection_strategy: str,
    scan_anchors: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    planned: list[dict[str, Any]] = []
    for idx, entry in enumerate(selected):
        username = str(entry.get("username") or "").strip()
        planned.append(
            {
                "job_id": str(entry.get("job_id") or "").strip(),
                "username": username,
                "screen_index": int(entry.get("screen_index") or 0),
                "row_index": entry.get("row_index"),
                "anchor_available": _anchor_available_for_username(
                    username, scan_anchors
                ),
                "selection_reason": selection_strategy,
                "planned_index": idx,
            }
        )
    return planned


def _planned_jobs_need_restore_scan_start(
    planned: list[dict[str, Any]],
    *,
    visible_keys: set[str],
    scan_final_screen_index: int,
) -> bool:
    if scan_final_screen_index <= 0 or not planned:
        return False
    for job in planned:
        ukey = _norm_username(str(job.get("username") or ""))
        if ukey in visible_keys:
            continue
        if int(job.get("screen_index") or 0) < scan_final_screen_index:
            return True
    return False


def _resolve_session_sender_plan(
    d: u2.Device,
    scan: dict[str, Any],
    *,
    account_username: str,
    pkg: str,
    max_jobs: int,
    scan_anchors: dict[str, dict[str, Any]],
    attempt_cap: int | None = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """
    Build the plan without moving the Followers viewport.

    Freshly visible planned jobs always win. Navigation for an off-screen job
    belongs exclusively to the row resolver when that job is processed.
    Returns (planned_jobs, selection_strategy, position_meta).
    """
    candidates = _session_scan_jobs_from_summary(scan)
    scan_final_screen_index = int(scan.get("scan_final_screen_index") or 0)
    position_meta: dict[str, Any] = {
        "scan_final_screen_index": scan_final_screen_index,
        "scan_jobs_total": len(candidates),
    }

    visible, harvest_meta = _sender_start_visible_usernames(
        d,
        account_username=account_username,
        screen_index=scan_final_screen_index,
    )
    visible_keys = {_norm_username(u) for u in visible}
    position_meta["sender_start_visible_usernames"] = list(visible)
    position_meta["sender_start_surface"] = str(
        harvest_meta.get("hierarchy_source") or ""
    )

    plan_cap = max_jobs if attempt_cap is None else max(0, int(attempt_cap))
    scan_order_slice = candidates[:plan_cap]
    visible_planned = [
        entry
        for entry in scan_order_slice
        if _norm_username(str(entry.get("username") or "")) in visible_keys
    ]
    if visible_planned:
        selection_strategy = "visible_planned_job_first"
        selected = _order_scan_jobs_viewport_first(
            scan_order_slice, visible_keys=visible_keys
        )
    else:
        selection_strategy = "scan_order_bounded_reposition"
        selected = scan_order_slice

    position_meta["restore_needed"] = False
    position_meta["reposition_success"] = None
    position_meta["visible_planned_usernames"] = [
        str(entry.get("username") or "") for entry in visible_planned
    ]
    log(
        "info",
        "welcome_list_sender_non_destructive_plan_selected",
        account_username=account_username,
        planned_usernames=[str(e.get("username") or "") for e in selected],
        visible_usernames=list(visible),
        visible_planned_usernames=position_meta["visible_planned_usernames"],
        selection_strategy=selection_strategy,
        viewport_moved=False,
    )

    planned = _build_planned_session_jobs(
        selected,
        selection_strategy=selection_strategy,
        scan_anchors=scan_anchors,
    )
    position_meta["selection_strategy"] = selection_strategy
    return planned, selection_strategy, position_meta


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
            current_screen_guess=det.get("current_screen_guess"),
            open_detection_method=det.get("open_detection_method"),
            signals=det.get("signals"),
        )
    return ok, obs


def _recovered_followers_snapshot_strong(
    open_meta: dict[str, Any],
    *,
    account_username: str,
    pkg: str,
) -> tuple[bool, str, dict[str, Any]]:
    canonical_open_meta = open_meta
    nested_open_meta = open_meta.get("open_meta")
    if isinstance(nested_open_meta, dict):
        merged_det = open_meta.get("det") or {}
        merged_signals = list(merged_det.get("signals") or []) if isinstance(merged_det, dict) else []
        if not bool(merged_det.get("is_followers_list")):
            return False, "committed_merge_not_followers_list", {}
        if "committed_visual_surface_merge" not in merged_signals:
            return False, "committed_visual_surface_merge_missing", {}
        canonical_open_meta = nested_open_meta

    snapshot = canonical_open_meta.get("after_tap_screen_snapshot") or {}
    if not isinstance(snapshot, dict) or not bool(snapshot.get("is_followers_list")):
        return False, "snapshot_not_followers_list", {}
    if str(snapshot.get("open_detection_method") or "") != "visual_fallback":
        return False, "snapshot_not_visual_fallback", snapshot
    if str(canonical_open_meta.get("open_detection_method") or "") != "visual_fallback":
        return False, "canonical_open_not_visual_fallback", snapshot
    source = str(canonical_open_meta.get("source_profile_username") or "").strip().lower()
    if source != str(account_username or "").strip().lower():
        return False, "canonical_open_account_mismatch", snapshot

    visual = snapshot.get("visual_fallback_detail") or {}
    if not isinstance(visual, dict) or not bool(visual.get("visual_match")):
        return False, "snapshot_visual_match_missing", snapshot
    min_confidence = float(
        getattr(config, "FOLLOWERS_VISUAL_FALLBACK_MIN_CONFIDENCE", 0.65) or 0.65
    )
    min_follow_buttons = int(
        getattr(config, "FOLLOWERS_VISUAL_MIN_FOLLOW_BUTTONS", 3) or 3
    )
    confidence = float(visual.get("visual_confidence") or 0.0)
    rows = int(visual.get("visual_user_rows_detected") or 0)
    follow_buttons = int(visual.get("visual_follow_button_count") or 0)
    search_strip = bool(visual.get("visual_search_area_detected"))
    if confidence < min_confidence:
        return False, "snapshot_visual_confidence_weak", snapshot
    if follow_buttons < min_follow_buttons:
        return False, "snapshot_follow_buttons_weak", snapshot
    if rows < 2:
        return False, "snapshot_rows_weak", snapshot
    if not search_strip:
        return False, "snapshot_search_strip_missing", snapshot

    snapshot_pkg = str(snapshot.get("current_package") or "").strip()
    expected_pkg = str(pkg or "").strip()
    if snapshot_pkg and expected_pkg and snapshot_pkg != expected_pkg:
        return False, "snapshot_package_mismatch", snapshot
    return True, "strong_visual_snapshot", snapshot


def _fresh_followers_surface_contradiction(
    fresh_det: dict[str, Any],
    fresh_xml: str,
    *,
    recovered_snapshot: dict[str, Any],
    pkg: str,
) -> str:
    fresh_pkg = str(fresh_det.get("current_package") or "").strip()
    expected_pkg = str(pkg or "").strip()
    if fresh_pkg and expected_pkg and fresh_pkg != expected_pkg:
        return "foreground_package_changed"
    prior_activity = str(recovered_snapshot.get("current_activity") or "").strip()
    fresh_activity = str(fresh_det.get("current_activity") or "").strip()
    if prior_activity and fresh_activity and prior_activity != fresh_activity:
        return "foreground_activity_changed"
    if bool(fresh_det.get("navigation_since_snapshot")):
        return "navigation_since_snapshot"

    recognized_surface = str(
        fresh_det.get("recognized_surface") or fresh_det.get("surface") or ""
    ).strip().lower()
    if recognized_surface in {
        "login",
        "challenge",
        "checkpoint",
        "dm_thread",
        "profile",
        "home",
        "search",
        "post",
        "story",
    }:
        return f"recognized_surface:{recognized_surface}"

    xml = str(fresh_xml or "").lower()
    marker_groups = (
        (
            "login_or_challenge",
            (
                "challenge_required",
                "challenge_webview",
                "checkpoint",
                "login_password",
                "login_username",
            ),
        ),
        ("dm_thread", ("row_thread_composer", "direct_thread", "message_composer")),
        ("home", ("feed_timeline", "feed_view_pager")),
        ("search", ("explore_grid", "search_tab_selected")),
        ("story", ("reel_viewer", "story_viewer")),
    )
    for reason, markers in marker_groups:
        if any(marker in xml for marker in markers):
            return f"fresh_xml:{reason}"

    guess = str(fresh_det.get("current_screen_guess") or "").strip().lower()
    profile_header = "profile_header" in xml
    profile_structure = any(
        marker in xml
        for marker in ("profile_grid", "profile_tab", "profile_header_followers")
    )
    if guess == "profile_header_rid" and profile_header and profile_structure:
        return "fresh_xml:profile_confirmed"
    return ""


def _ensure_sender_entry_followers_surface(
    d: u2.Device,
    *,
    account_username: str,
    pkg: str,
) -> tuple[bool, dict[str, Any]]:
    from own_profile_navigation import open_own_followers_list_from_own_profile

    log(
        "info",
        "welcome_sender_entry_surface_check_started",
        account_username=account_username,
    )
    ok, obs = _verify_followers_surface(
        d, account_username=account_username, context="welcome_list_sender_start"
    )
    if ok:
        return True, {
            "recovered": False,
            "surface_decision": "fresh_detection_confirmed",
            "obs": obs,
        }

    log(
        "info",
        "welcome_sender_entry_surface_recovery_attempted",
        account_username=account_username,
    )
    followers_session_clear_list_committed_open(account_username)
    opened, open_meta = open_own_followers_list_from_own_profile(
        d, account_username, pkg=pkg
    )
    failure_obs = obs
    if opened:
        followers_clear_detect_hierarchy_cache()
        det, obs2 = detect_followers_list_screen_fresh(
            d, source_profile_username=account_username
        )
        failure_obs = obs2
        if bool(det.get("is_followers_list")):
            log(
                "info",
                "welcome_sender_entry_surface_recovered",
                account_username=account_username,
                open_detection_method=det.get("open_detection_method"),
                surface_decision="fresh_detection_confirmed",
            )
            return True, {
                "recovered": True,
                "surface_decision": "fresh_detection_confirmed",
                "open_meta": open_meta,
                "obs": obs2,
            }
        strong, snapshot_reason, snapshot = _recovered_followers_snapshot_strong(
            open_meta,
            account_username=account_username,
            pkg=pkg,
        )
        contradiction = _fresh_followers_surface_contradiction(
            det,
            obs2,
            recovered_snapshot=snapshot,
            pkg=pkg,
        )
        if strong and not contradiction:
            visual = snapshot.get("visual_fallback_detail") or {}
            log(
                "info",
                "welcome_sender_entry_surface_recovered",
                account_username=account_username,
                open_detection_method="visual_fallback",
                surface_decision="recovered_snapshot_preserved",
                visual_confidence=visual.get("visual_confidence"),
                visual_user_rows_detected=visual.get("visual_user_rows_detected"),
                visual_follow_button_count=visual.get("visual_follow_button_count"),
            )
            return True, {
                "recovered": True,
                "surface_decision": "recovered_snapshot_preserved",
                "open_meta": open_meta,
                "obs": obs2,
            }
        log(
            "warning",
            "welcome_sender_entry_recovered_snapshot_rejected",
            account_username=account_username,
            surface_decision="recovered_snapshot_rejected",
            snapshot_reason=snapshot_reason,
            contradiction=contradiction or None,
            current_screen_guess=det.get("current_screen_guess"),
        )

    log(
        "error",
        "welcome_sender_entry_surface_recovery_failed",
        account_username=account_username,
        opened=bool(opened),
        failure_reason=str((open_meta or {}).get("failure_reason") or ""),
        surface_decision="recovered_snapshot_rejected",
    )
    return False, {
        "recovered": False,
        "surface_decision": "recovered_snapshot_rejected",
        "open_meta": open_meta,
        "obs": failure_obs,
    }


def _welcome_send_has_strong_outbound_proof(
    send_out: dict[str, Any],
    fail_reason: str | None,
) -> bool:
    if fail_reason not in (None, "post_finalize_partial"):
        return False
    if not bool(send_out.get("sent")):
        return False
    if bool(send_out.get("duplicate_prevented")):
        return False
    return True


def _skip_current_scan_session_jobs(
    scan_summary: dict[str, Any],
    *,
    run_id: str | None,
    reason: str,
    last_error: str,
) -> int:
    """Terminalize scan-enqueued Welcome jobs when sender exits before any send."""
    skipped = 0
    metadata_patch = {
        "cleanup_run_id": str(run_id or ""),
        "cleanup_reason": reason,
    }
    for entry in _session_scan_jobs_from_summary(scan_summary):
        job_id = str(entry.get("job_id") or "").strip()
        if not job_id:
            continue
        try:
            supabase_client.complete_dm_job(
                job_id,
                "skipped",
                skip_reason=reason,
                last_error=last_error,
                metadata_patch=metadata_patch,
            )
            skipped += 1
        except Exception as exc:
            log(
                "error",
                "welcome_list_sender_scan_job_cleanup_failed",
                job_id=job_id,
                reason=reason,
                error=str(exc),
            )
    if skipped:
        log(
            "info",
            "welcome_list_sender_scan_jobs_skipped_after_sender_failure",
            skipped_count=skipped,
            reason=reason,
            run_id=run_id,
        )
    return skipped


def _navigate_followers_row_to_dm(
    d: u2.Device,
    username: str,
    *,
    pkg: str,
    account_username: str,
    scan_anchors: dict[str, dict[str, Any]],
    planned_job_context: dict[str, Any] | None = None,
    state_machine: _WelcomeStateMachine | None = None,
) -> tuple[str, bool, dict[str, Any]]:
    """Followers list row tap → profile → DM thread."""
    uname = str(username or "").strip()
    nav_meta: dict[str, Any] = {"username": uname}
    plan_ctx = dict(planned_job_context or {})
    reset_dm_thread_probe_state()

    row, scrolls, lookup_path, lookup_debug = _resolve_followers_row(
        d,
        uname,
        account_username=account_username,
        scan_anchors=scan_anchors,
        allow_scan_anchor_fast_path=bool(
            plan_ctx.get("current_scan_session")
            and plan_ctx.get("followers_surface_fresh")
        ),
        job_id=str(plan_ctx.get("job_id") or ""),
        scan_generation=str(plan_ctx.get("scan_generation") or ""),
        navigation_generation=str(plan_ctx.get("navigation_generation") or ""),
        anchor_invalidation_reason=str(
            plan_ctx.get("anchor_invalidation_reason")
            or "fresh_identity_required_before_tap"
        ),
        target_screen_index=plan_ctx.get("target_screen_index"),
        current_screen_index=plan_ctx.get("current_screen_index"),
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

    resolved_navigation_generation = str(
        lookup_debug.get("resolved_navigation_generation")
        or row.get("welcome_navigation_generation")
        or plan_ctx.get("navigation_generation")
        or ""
    )
    tap_allowed, tap_reason, snapshot_age_ms, observed_username = (
        _authorize_welcome_row_tap(
            row,
            expected_username=uname,
            job_id=str(plan_ctx.get("job_id") or ""),
            scan_generation=str(plan_ctx.get("scan_generation") or ""),
            navigation_generation=resolved_navigation_generation,
        )
    )
    if not tap_allowed:
        log(
            "warning",
            "welcome_row_tap_blocked",
            reason=tap_reason,
            expected_username=uname,
            observed_username=observed_username or None,
            job_id=str(plan_ctx.get("job_id") or ""),
            snapshot_age_ms=None
            if snapshot_age_ms == float("inf")
            else snapshot_age_ms,
        )
        return f"row_tap_blocked_{tap_reason}", False, nav_meta

    log(
        "info",
        "welcome_row_tap_authorized",
        job_id=str(plan_ctx.get("job_id") or ""),
        expected_username=uname,
        current_bounds=row.get("tap_bounds") or row.get("bounds"),
        scan_generation=str(plan_ctx.get("scan_generation") or ""),
        navigation_generation=resolved_navigation_generation,
        viewport_fingerprint=row.get("welcome_viewport_fingerprint"),
        snapshot_age_ms=snapshot_age_ms,
    )
    if state_machine is not None:
        if state_machine.current == "initial":
            state_machine.transition(
                "followers_stable",
                proof="fresh_followers_surface_and_hierarchy",
                owner="welcome_sender_viewport",
                job_id=str(plan_ctx.get("job_id") or ""),
                username=uname,
            )
        elif state_machine.current == "followers_restored":
            state_machine.transition(
                "next_job_ready",
                proof="next_planned_row_freshly_visible",
                owner="welcome_sender_viewport",
                job_id=str(plan_ctx.get("job_id") or ""),
                username=uname,
            )
        state_machine.transition(
            "planned_row_freshly_resolved",
            proof="fresh_hierarchy_exact_username_current_bounds",
            owner="welcome_sender_viewport",
            job_id=str(plan_ctx.get("job_id") or ""),
            username=uname,
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

    identity_ok, identity_reason, observed_username = (
        verify_welcome_profile_username_exact(d, uname, pkg)
    )
    nav_meta["profile_identity_reason"] = identity_reason
    nav_meta["observed_profile_username"] = observed_username
    if not identity_ok:
        log(
            "error",
            "welcome_list_sender_profile_identity_rejected",
            username=uname,
            reason=identity_reason,
            observed_profile_username=observed_username or None,
        )
        return identity_reason, False, nav_meta

    log("info", "welcome_list_sender_profile_opened", username=uname)
    if state_machine is not None:
        state_machine.transition(
            "target_profile_exact",
            proof=str(identity_reason or "exact_profile_username"),
            owner="welcome_sender_navigation",
            job_id=str(plan_ctx.get("job_id") or ""),
            username=uname,
        )

    thread_state = open_dm_thread_from_profile(
        d, uname, welcome_list_native=True
    )
    log(
        "info",
        "welcome_list_sender_dm_thread_opened",
        username=uname,
        thread_state=thread_state,
    )

    nav_ok = thread_state not in (
        "unknown",
        "foreground_package_mismatch",
        "profile_username_mismatch",
        "thread_recipient_identity_mismatch",
        "account_review_popup",
    )
    if (
        nav_ok
        and thread_state not in ("dm_not_available", "restricted_account")
        and state_machine is not None
    ):
        state_machine.transition(
            "dm_thread_exact",
            proof=f"exact_thread_header:{thread_state}",
            owner="welcome_sender_navigation",
            job_id=str(plan_ctx.get("job_id") or ""),
            username=uname,
        )
    return thread_state, nav_ok, nav_meta


def _restore_followers_after_job(
    d: u2.Device,
    username: str,
    *,
    pkg: str,
    account_username: str,
    job_id: str = "",
    state_machine: _WelcomeStateMachine | None = None,
) -> bool:
    """Ensure we end on followers list (from DM, profile, or already on list)."""
    src = str(account_username or "").strip()
    from own_profile_navigation import (
        open_own_followers_list_from_own_profile,
        open_own_profile_from_bottom_nav,
    )

    def _confirm_followers(stage: str) -> bool:
        det, _ = detect_followers_list_screen_fresh(d, source_profile_username=src)
        ok = bool(det.get("is_followers_list"))
        log(
            "info" if ok else "warning",
            "welcome_post_job_return_surface_check",
            username=username,
            source_profile_username=src or None,
            stage=stage,
            followers_surface_ok=ok,
            action_bar_title=det.get("action_bar_title"),
            current_screen_guess=det.get("current_screen_guess"),
            open_detection_method=det.get("open_detection_method"),
        )
        return ok

    log(
        "info",
        "welcome_post_job_return_started",
        username=username,
        source_profile_username=src or None,
    )
    if _confirm_followers("initial"):
        if state_machine is not None and state_machine.current == "outbound_verified":
            state_machine.transition(
                "thread_exit",
                proof="direct_followers_after_outbound",
                owner="welcome_sender_post_dm_return",
                job_id=job_id,
                username=username,
            )
            state_machine.transition(
                "followers_restored",
                proof="fresh_followers_detection",
                owner="welcome_sender_post_dm_return",
                job_id=job_id,
                username=username,
            )
        log(
            "info",
            "welcome_post_job_return_succeeded",
            username=username,
            source_profile_username=src or None,
            method="already_on_followers",
        )
        return True

    if is_dm_thread_screen(d, pkg):
        log(
            "info",
            "welcome_post_job_thread_to_profile",
            username=username,
            source_profile_username=src or None,
        )
        fin = return_welcome_list_from_dm_to_followers(
            d,
            username,
            pkg,
            source_profile_username=src,
            job_id=job_id,
        )
        log(
            "info" if bool(fin.get("followers_surface_ok")) else "warning",
            "welcome_return_from_thread_completed",
            username=username,
            source_profile_username=src or None,
            followers_surface_ok=bool(fin.get("followers_surface_ok")),
            profile_identity_reason=fin.get("profile_identity_reason"),
        )
        if bool(fin.get("followers_surface_ok")) and _confirm_followers("thread_return"):
            if state_machine is not None and state_machine.current == "outbound_verified":
                state_machine.transition(
                    "thread_exit",
                    proof="canonical_thread_exit",
                    owner="welcome_sender_post_dm_return",
                    job_id=job_id,
                    username=username,
                )
                state_machine.transition(
                    "followers_restored",
                    proof="fresh_followers_detection",
                    owner="welcome_sender_post_dm_return",
                    job_id=job_id,
                    username=username,
                )
            log(
                "info",
                "welcome_post_job_return_succeeded",
                username=username,
                source_profile_username=src or None,
                method="thread_to_profile_to_followers",
            )
            return True
        log(
            "error",
            "welcome_post_job_return_failed",
            username=username,
            source_profile_username=src or None,
            failure_reason="canonical_thread_return_failed",
        )
        return False

    recipient_profile_ok, _, _ = verify_welcome_profile_username_exact(
        d, username, pkg
    )
    own_profile_ok, _, _ = verify_welcome_profile_username_exact(d, src, pkg)
    if recipient_profile_ok:
        from instagram_navigation import tap_instagram_action_bar_back_button

        settle_s = float(
            getattr(config, "WELCOME_LIST_SENDER_BACK_SETTLE_S", 0.45) or 0.45
        )
        tapped, _ = tap_instagram_action_bar_back_button(d, pkg)
        if tapped and settle_s > 0:
            time.sleep(settle_s)
        if tapped and _confirm_followers("recipient_profile_single_back"):
            if state_machine is not None and state_machine.current == "outbound_verified":
                state_machine.transition(
                    "thread_exit",
                    proof="recipient_profile_already_restored",
                    owner="welcome_sender_post_dm_return",
                    job_id=job_id,
                    username=username,
                )
                state_machine.transition(
                    "followers_restored",
                    proof="fresh_followers_detection",
                    owner="welcome_sender_post_dm_return",
                    job_id=job_id,
                    username=username,
                )
            log(
                "info",
                "welcome_post_job_return_succeeded",
                username=username,
                source_profile_username=src or None,
                method="recipient_profile_single_back",
            )
            return True

    log(
        "info",
        "welcome_post_job_profile_to_followers",
        username=username,
        source_profile_username=src or None,
        method="own_profile_canonical_reopen",
    )
    followers_session_clear_list_committed_open(src)
    profile_ok = own_profile_ok or open_own_profile_from_bottom_nav(d)
    opened = False
    open_meta: dict[str, Any] = {}
    if profile_ok:
        opened, open_meta = open_own_followers_list_from_own_profile(d, src, pkg=pkg)
    if opened and _confirm_followers("own_profile_reopen"):
        followers_clear_detect_hierarchy_cache()
        followers_refresh_detect_hierarchy_cache(d, screen_index=0)
        if state_machine is not None and state_machine.current == "outbound_verified":
            state_machine.transition(
                "thread_exit",
                proof="bounded_canonical_reopen",
                owner="welcome_sender_post_dm_return",
                job_id=job_id,
                username=username,
            )
            state_machine.transition(
                "followers_restored",
                proof="fresh_followers_detection",
                owner="welcome_sender_post_dm_return",
                job_id=job_id,
                username=username,
            )
        log(
            "info",
            "welcome_post_job_return_succeeded",
            username=username,
            source_profile_username=src or None,
            method="own_profile_to_followers",
        )
        return True

    log(
        "error",
        "welcome_post_job_return_failed",
        username=username,
        source_profile_username=src or None,
        own_profile_opened=bool(profile_ok),
        followers_opened=bool(opened),
        failure_reason=str((open_meta or {}).get("failure_reason") or "followers_recovery_failed"),
    )
    return False


def execute_welcome_list_job(
    d: u2.Device,
    job: dict[str, Any],
    *,
    settings: dict[str, Any],
    account_id: str,
    account_username: str,
    scan_anchors: dict[str, dict[str, Any]],
    planned_job_context: dict[str, Any] | None = None,
    state_machine: _WelcomeStateMachine | None = None,
) -> dict[str, Any]:
    job_id = str(job.get("id") or "")
    recipient = str(job.get("recipient_username") or "").strip()
    dm_type = str(job.get("dm_type") or "")
    message_body = str(job.get("message_body") or "")
    pkg = str(getattr(config, "INSTAGRAM_PACKAGE", "") or "com.instagram.android")
    machine = state_machine or _WelcomeStateMachine(
        account_id=account_id,
        run_id=str((planned_job_context or {}).get("run_id") or ""),
    )
    state_history_start = len(machine.history)

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
    fail_reason: str | None = None
    followers_surface_restored = False
    return_attempted = False
    navigation_meta: dict[str, Any] = {}

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
        thread_state, nav_ok, navigation_meta = _navigate_followers_row_to_dm(
            d,
            recipient,
            pkg=pkg,
            account_username=account_username,
            scan_anchors=scan_anchors,
            planned_job_context=planned_job_context,
            state_machine=machine,
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
            if thread_state.startswith("row_tap_blocked_"):
                fail_reason = thread_state
            elif thread_state == "unknown" and get_last_dm_thread_attempted():
                fail_reason = "thread_state_unknown_after_message"
            elif thread_state in (
                "foreground_package_mismatch",
                "profile_username_mismatch",
                "thread_recipient_identity_mismatch",
                "account_review_popup",
            ):
                fail_reason = thread_state
            else:
                fail_reason = "list_navigation_failed"
            plan_ctx = dict(planned_job_context or {})
            lookup_path = str(navigation_meta.get("lookup_path_used") or "")
            row_failure_reason = {
                "welcome_planned_row_not_found": "welcome_planned_row_not_found",
                "fresh_miss_despite_visible": "welcome_planned_row_identity_unverified",
                "followers_surface_not_stable": "followers_surface_not_stable_before_welcome_row_tap",
                "welcome_suggestions_boundary_reached": "welcome_suggestions_boundary_reached",
            }.get(lookup_path)
            row_missing = row_failure_reason is not None
            if plan_ctx.get("current_scan_session") and row_missing:
                fail_reason = str(row_failure_reason)
                log(
                    "error",
                    "welcome_list_sender_current_scan_row_not_found",
                    job_id=job_id,
                    recipient_username=recipient,
                    planned_index=int(plan_ctx.get("planned_index") or 0),
                    selection_strategy=str(plan_ctx.get("selection_strategy") or ""),
                    reposition_applied=bool(plan_ctx.get("reposition_applied")),
                    possible_unfollow_or_surface_shift=True,
                    lookup_path_used=lookup_path,
                    scrolls_attempted=navigation_meta.get("followers_scrolls_to_find"),
                    scan_anchor_present=bool(navigation_meta.get("scan_anchor_present")),
                )
            fail_reason = _welcome_structured_failure_reason(fail_reason)
            machine.fail(
                fail_reason,
                owner="welcome_sender_navigation",
                job_id=job_id,
                username=recipient,
            )
            if fail_reason in _SEND_UNVERIFIED_REASONS:
                updated_job, outcome = _complete_job_send_unverified_quarantine(
                    job,
                    last_error=fail_reason,
                    thread_state=thread_state,
                )
            else:
                updated_job, outcome = _complete_job_failed_retry(
                    job,
                    last_error=fail_reason,
                    thread_state=thread_state,
                )
            final_status = str((updated_job or {}).get("status") or "pending")
            log(
                "warning",
                "welcome_list_sender_job_navigation_failed",
                job_id=job_id,
                recipient_username=recipient,
                reason=fail_reason,
                outcome=outcome,
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
                    post_send_nav="sender_owned",
                    source_profile_username=account_username,
                    account_id=account_id,
                    run_id=str((planned_job_context or {}).get("run_id") or ""),
                    job_id=job_id,
                    navigation_generation=str(
                        navigation_meta.get("resolved_navigation_generation") or ""
                    ),
                    state_transition=lambda state, proof: machine.transition(
                        state,
                        proof=proof,
                        owner="welcome_sender_send",
                        job_id=job_id,
                        username=recipient,
                    ),
                )
                dm_send_ms = (time.perf_counter() - t_send) * 1000.0
                strong_send_proof = _welcome_send_has_strong_outbound_proof(
                    send_out, fail_reason
                )
                if sent_ok and strong_send_proof:
                    return_attempted = True
                    followers_surface_restored = _restore_followers_after_job(
                        d,
                        recipient,
                        pkg=pkg,
                        account_username=account_username,
                        job_id=job_id,
                        state_machine=machine,
                    )
                    if not followers_surface_restored:
                        fail_reason = "welcome_followers_restore_failed"
                        machine.fail(
                            fail_reason,
                            owner="welcome_sender_post_dm_return",
                            job_id=job_id,
                            username=recipient,
                        )
                    updated_job = supabase_client.complete_dm_job(
                        job_id,
                        "sent",
                        metadata_patch={
                            "thread_state": thread_state,
                            "send_method": "instagram_send_ui",
                            "message_len": len(message_body),
                            "welcome_list_native": True,
                            "post_finalize_partial": not followers_surface_restored,
                            "send_verification_status": "verified",
                            "outbound_bubble_evidence_found": True,
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
                    unverified_reason = _welcome_structured_failure_reason(
                        fail_reason or "send_unverified"
                    )
                    if sent_ok and not strong_send_proof:
                        unverified_reason = "welcome_outbound_unverified"
                    fail_reason = unverified_reason
                    updated_job, outcome = _complete_job_send_unverified_quarantine(
                        job,
                        last_error=unverified_reason,
                        thread_state=thread_state,
                    )
                    final_status = str((updated_job or {}).get("status") or "failed")
                    log(
                        "warning",
                        "welcome_list_sender_job_send_unverified_quarantined",
                        job_id=job_id,
                        recipient_username=recipient,
                        reason=unverified_reason,
                        final_job_status=final_status,
                    )
    finally:
        should_restore = machine.current not in {"initial", "followers_stable"}
        if followers_surface_restored:
            log(
                "info",
                "welcome_list_sender_post_job_restore_already_owned",
                job_id=job_id,
                recipient_username=recipient,
                reason="sender_return_completed_once",
            )
        elif should_restore and not return_attempted:
            return_attempted = True
            followers_surface_restored = _restore_followers_after_job(
                d,
                recipient,
                pkg=pkg,
                account_username=account_username,
                job_id=job_id,
                state_machine=machine,
            )
        if should_restore and not followers_surface_restored:
            if not fail_reason:
                fail_reason = "welcome_followers_restore_failed"
            machine.fail(
                str(fail_reason),
                owner="welcome_sender_post_dm_return",
                job_id=job_id,
                username=recipient,
            )
            _capture_welcome_failure_before_cleanup(
                d,
                account_username=account_username,
                expected_username=recipient,
                job_id=job_id,
                navigation_state=str(fail_reason or thread_state or "unknown"),
            )
            _check_dm_sender_permission_blocker(
                d, username=recipient, context="welcome_list_teardown"
            )
        elif (
            outcome == "skipped"
            and followers_surface_restored
            and machine.current not in {"initial", "followers_stable"}
        ):
            machine.reset_after_clean_skip(
                proof="non_dmable_job_skipped_with_fresh_followers",
                job_id=job_id,
                username=recipient,
            )

    return {
        "job_id": job_id,
        "recipient_username": recipient,
        "outcome": outcome,
        "final_job_status": final_status,
        "thread_state": thread_state,
        "post_finalize_partial": fail_reason == "welcome_followers_restore_failed",
        "followers_surface_restored": bool(followers_surface_restored),
        "failure_reason": fail_reason,
        "navigation_meta": navigation_meta,
        "welcome_state": machine.current,
        "welcome_state_history": list(machine.history[state_history_start:]),
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
    current_scan_session_mode = scan_summary is not None
    session_scan_jobs = _session_scan_jobs_from_summary(scan)
    scan_attempt_cap = int(
        scan.get("candidate_attempt_cap")
        or scan.get("effective_welcome_scan_cap")
        or max_jobs
    )
    sender_attempt_cap = max(max_jobs, scan_attempt_cap) if current_scan_session_mode else max_jobs

    summary: dict[str, Any] = {
        "account_id": aid,
        "run_id": run_id,
        "max_jobs": max_jobs,
        "sent_cap": max_jobs,
        "attempt_cap": sender_attempt_cap,
        "sender_mode": "welcome_list_native",
        "current_scan_session_mode": current_scan_session_mode,
        "session_claim_mode": (
            "current_scan_job_ids"
            if current_scan_session_mode
            else "global_claim_next"
        ),
        "jobs_claimed_count": 0,
        "jobs_sent_count": 0,
        "jobs_skipped_count": 0,
        "jobs_failed_count": 0,
        "processed_recipients": [],
        "sent_recipients": [],
        "skipped_recipients": [],
        "failed_recipients": [],
        "planned_session_jobs": [],
        "recipients_planned": [],
        "recipients_sent": [],
        "recipients_skipped": [],
        "recipients_failed": [],
        "list_navigation_total_ms": 0.0,
        "dm_send_total_ms": 0.0,
        "sender_status": "not_started",
        "session_scan_jobs_count_before_planning": len(session_scan_jobs),
        "welcome_state_history": [],
    }
    state_machine = _WelcomeStateMachine(account_id=aid, run_id=run_id)

    real_enabled, real_source = resolve_welcome_dm_real_send_enabled()
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
        sent_cap=max_jobs,
        attempt_cap=sender_attempt_cap,
        current_scan_session_mode=current_scan_session_mode,
        scan_jobs_total=len(session_scan_jobs),
    )

    boundary_candidate = str(
        scan.get("followers_suggestions_boundary_selected_candidate") or ""
    ).strip()
    boundary_candidate_ready = (
        str(scan.get("followers_suggestions_boundary_action") or "")
        == "use_visible_candidate"
        and scan.get("followers_suggestions_boundary_selected_candidate_is_real_follower") is True
        and bool(boundary_candidate)
        and _norm_username(boundary_candidate)
        in {_norm_username(str(entry.get("username") or "")) for entry in session_scan_jobs}
    )
    if boundary_candidate_ready:
        followers_ok = True
        entry_meta = {
            "recovered": False,
            "surface_decision": "followers_suggestions_boundary_visible_planned_candidate",
        }
        log(
            "info",
            "welcome_sender_entry_surface_boundary_candidate_accepted",
            account_id=aid,
            username=boundary_candidate,
            surface_decision=entry_meta["surface_decision"],
        )
    else:
        followers_ok, entry_meta = _ensure_sender_entry_followers_surface(
            d, account_username=acct_user, pkg=pkg
        )
    if followers_ok:
        scan_final_idx = int(scan.get("scan_final_screen_index") or 0)
        followers_refresh_detect_hierarchy_cache(d, screen_index=scan_final_idx)
    summary["entry_surface_recovered"] = bool(entry_meta.get("recovered"))
    summary["entry_surface_decision"] = str(entry_meta.get("surface_decision") or "")

    scan_anchors = _scan_row_anchors_by_username(scan)

    planned_session_jobs: list[dict[str, Any]] = []
    selection_strategy = ""
    position_meta: dict[str, Any] = {}

    if current_scan_session_mode and not only_job_id:
        if followers_ok and session_scan_jobs:
            planned_session_jobs, selection_strategy, position_meta = (
                _resolve_session_sender_plan(
                    d,
                    scan,
                    account_username=acct_user,
                    pkg=pkg,
                    max_jobs=max_jobs,
                    scan_anchors=scan_anchors,
                    attempt_cap=sender_attempt_cap,
                )
            )
        else:
            planned_session_jobs = []
            selection_strategy = (
                "current_scan_jobs_blocked_by_surface"
                if session_scan_jobs
                else "no_current_scan_jobs"
            )
            position_meta = {
                "scan_final_screen_index": int(scan.get("scan_final_screen_index") or 0),
                "scan_jobs_total": len(session_scan_jobs),
            }
        summary["planned_session_jobs"] = list(planned_session_jobs)
        summary["recipients_planned"] = [
            str(p.get("username") or "") for p in planned_session_jobs
        ]
        summary["selection_strategy"] = selection_strategy
        summary["scan_final_screen_index"] = position_meta.get(
            "scan_final_screen_index"
        )
        if session_scan_jobs:
            log(
                "info",
                "welcome_list_sender_start_position_resolved",
                account_id=aid,
                scan_final_screen_index=position_meta.get("scan_final_screen_index"),
                visible_usernames_at_sender_start=position_meta.get(
                    "sender_start_visible_usernames"
                ),
                strategy=selection_strategy,
                restore_needed=position_meta.get("restore_needed"),
                reposition_success=position_meta.get("reposition_success"),
            )
        log(
            "info",
            "welcome_list_sender_session_plan_built",
            account_id=aid,
            plan_source="current_scan",
            planned_jobs=planned_session_jobs,
            scan_jobs_total=position_meta.get("scan_jobs_total", len(session_scan_jobs)),
            max_jobs=max_jobs,
            sent_cap=max_jobs,
            attempt_cap=sender_attempt_cap,
            selection_strategy=selection_strategy,
            scan_final_screen_index=position_meta.get("scan_final_screen_index"),
            sender_start_surface=position_meta.get("sender_start_surface"),
            sender_start_visible_usernames=position_meta.get(
                "sender_start_visible_usernames"
            ),
        )

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

    if current_scan_session_mode and not only_job_id and not session_scan_jobs:
        log(
            "info",
            "welcome_list_sender_no_current_scan_jobs",
            account_id=aid,
            account_username=acct_user,
            run_id=run_id,
            scan_jobs_total=0,
            max_jobs=max_jobs,
        )
        summary["sender_status"] = "success"
        summary["loop_exit_reason"] = "no_current_scan_jobs"
        summary["total_ms"] = round((time.perf_counter() - t0) * 1000.0, 2)
        _LAST_WELCOME_LIST_SENDER_SUMMARY = dict(summary)
        log("info", "welcome_list_sender_summary", **summary)
        log(
            "info",
            "welcome_list_sender_completed",
            account_id=aid,
            run_id=run_id,
            sender_status="success",
        )
        return 0, summary

    loop_exit_reason: str | None = None
    job_iterations: list[dict[str, Any]] = []
    current_viewport_screen_index = int(scan.get("scan_final_screen_index") or 0)

    if only_job_id:
        job_iterations = [{"job_id": only_job_id, "username": "", "planned_index": 0}]
    elif current_scan_session_mode:
        job_iterations = list(planned_session_jobs)
    else:
        job_iterations = [
            {"job_id": "", "username": "", "planned_index": i} for i in range(max_jobs)
        ]

    for planned in job_iterations:
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

        planned_job_id = str(planned.get("job_id") or "").strip()
        planned_username = str(planned.get("username") or "").strip()
        planned_index = int(planned.get("planned_index") or 0)

        if current_scan_session_mode and not only_job_id:
            if not planned_job_id:
                continue
            claim_job_id = planned_job_id
        elif only_job_id:
            claim_job_id = only_job_id
        elif current_scan_session_mode:
            continue
        else:
            claim_job_id = ""

        job = _claim_job_for_run(
            aid,
            reserved_by,
            dm_type="welcome",
            only_job_id=claim_job_id if claim_job_id else "",
        )

        if current_scan_session_mode and not only_job_id:
            if not job or not str(job.get("id") or "").strip():
                log(
                    "warning",
                    "welcome_list_sender_scan_job_claim_failed",
                    account_id=aid,
                    job_id=planned_job_id,
                    planned_username=planned_username,
                    planned_index=planned_index,
                )
                summary["jobs_failed_count"] += 1
                if planned_username:
                    summary["failed_recipients"].append(planned_username)
                    summary["recipients_failed"].append(planned_username)
                continue

            claimed_id = str(job.get("id") or "").strip()
            claimed_user = str(job.get("recipient_username") or "").strip()
            if claimed_id != planned_job_id or (
                planned_username
                and _norm_username(claimed_user) != _norm_username(planned_username)
            ):
                log(
                    "warning",
                    "welcome_list_sender_claim_outside_current_scan_blocked",
                    account_id=aid,
                    planned_job_id=planned_job_id,
                    planned_username=planned_username,
                    claimed_job_id=claimed_id,
                    claimed_username=claimed_user,
                )
                continue

            log(
                "info",
                "welcome_list_sender_job_claimed",
                claim_source="current_scan_job_id",
                job_id=claimed_id,
                recipient_username=claimed_user,
                planned_index=planned_index,
                selection_strategy=selection_strategy or planned.get("selection_reason"),
            )
        elif job and str(job.get("id") or "").strip():
            log(
                "info",
                "welcome_list_sender_job_claimed",
                claim_source="claim_next_global"
                if not claim_job_id
                else "dm_sender_only_job_id",
                job_id=str(job.get("id") or ""),
                recipient_username=job.get("recipient_username"),
                planned_index=planned_index,
            )

        if not job or not str(job.get("id") or "").strip():
            if current_scan_session_mode:
                loop_exit_reason = "scan_session_jobs_exhausted"
            else:
                loop_exit_reason = "no_pending_job"
            log(
                "info",
                "welcome_list_sender_no_pending_job",
                account_id=aid,
                run_id=run_id,
                exit_reason=loop_exit_reason,
                current_scan_session_mode=current_scan_session_mode,
            )
            break

        if _job_requires_send_unverified_quarantine(job):
            recipient = str(job.get("recipient_username") or "").strip()
            _complete_job_send_unverified_quarantine(
                job,
                last_error=str(job.get("last_error") or "send_unverified"),
                thread_state=str((job.get("metadata") or {}).get("thread_state") or "") or None,
                metadata_patch={"quarantined_on_claim": True},
                increment_attempt=False,
            )
            summary["jobs_claimed_count"] += 1
            summary["jobs_failed_count"] += 1
            summary["processed_recipients"].append(recipient)
            summary["failed_recipients"].append(recipient)
            summary["recipients_failed"].append(recipient)
            log(
                "warning",
                "welcome_list_sender_preexisting_send_unverified_blocked",
                account_id=aid,
                run_id=run_id,
                job_id=str(job.get("id") or ""),
                recipient_username=recipient,
            )
            continue

        if len(summary["processed_recipients"]) >= sender_attempt_cap:
            loop_exit_reason = "attempt_cap_reached"
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

        planned_job_context: dict[str, Any] | None = None
        if current_scan_session_mode and not only_job_id:
            if planned_index > 0:
                anchor_invalidation_reason = "cross_job_navigation_completed"
            else:
                anchor_invalidation_reason = "fresh_identity_required_before_tap"
            planned_job_context = {
                "current_scan_session": True,
                "job_id": str(job.get("id") or ""),
                "planned_index": planned_index,
                "selection_strategy": selection_strategy
                or str(planned.get("selection_reason") or ""),
                "reposition_applied": False,
                "followers_surface_fresh": True,
                "run_id": str(run_id or ""),
                "scan_generation": str(scan.get("run_id") or run_id or ""),
                "navigation_generation": (
                    f"{str(run_id or 'no-run')}:{planned_index}:followers_pre_tap"
                ),
                "anchor_invalidation_reason": anchor_invalidation_reason,
                "target_screen_index": int(planned.get("screen_index") or 0),
                "current_screen_index": current_viewport_screen_index,
            }

        try:
            result = execute_welcome_list_job(
                d,
                job,
                settings=settings,
                account_id=aid,
                account_username=acct_user,
                scan_anchors=scan_anchors,
                planned_job_context=planned_job_context,
                state_machine=state_machine,
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
        summary["welcome_state_history"].extend(
            list(result.get("welcome_state_history") or [])
        )
        navigation_meta = dict(result.get("navigation_meta") or {})
        if navigation_meta.get("resolved_screen_index") is not None:
            current_viewport_screen_index = int(
                navigation_meta.get("resolved_screen_index") or 0
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
            if str(result.get("thread_state") or "") in (
                "dm_not_available",
                "restricted_account",
            ):
                log(
                    "info",
                    "welcome_sender_job_skipped_non_dmable",
                    account_id=aid,
                    run_id=run_id,
                    job_id=str(job.get("id") or ""),
                    recipient_username=recipient,
                    thread_state=str(result.get("thread_state") or ""),
                    jobs_sent_count=int(summary["jobs_sent_count"]),
                    jobs_skipped_count=int(summary["jobs_skipped_count"]),
                    sent_cap=max_jobs,
                    attempt_cap=sender_attempt_cap,
                )
        else:
            summary["jobs_failed_count"] += 1
            summary["failed_recipients"].append(recipient)
            summary["recipients_failed"].append(recipient)

        local_failure_reason = str(result.get("failure_reason") or "").strip()
        if local_failure_reason:
            summary["failure_reason"] = local_failure_reason
            loop_exit_reason = local_failure_reason
            log(
                "error",
                "welcome_list_sender_structured_failure_preserved",
                account_id=aid,
                run_id=run_id,
                job_id=str(job.get("id") or ""),
                recipient_username=recipient,
                failure_reason=local_failure_reason,
            )
            break

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

        if (
            outcome == "sent"
            and int(summary["jobs_sent_count"]) >= max_jobs
            and bool(result.get("followers_surface_restored"))
        ):
            if state_machine.current == "followers_restored":
                state_machine.transition(
                    "next_job_ready",
                    proof="sent_cap_reached_plan_complete",
                    owner="welcome_sender_job_loop",
                    job_id=str(job.get("id") or ""),
                    username=recipient,
                )
                summary["welcome_state_history"].append(
                    dict(state_machine.history[-1])
                )
            loop_exit_reason = "sent_cap_reached"
            log(
                "info",
                "welcome_list_sender_sent_cap_exit_immediate_after_job_sent",
                account_id=aid,
                run_id=run_id,
                recipient_username=recipient,
                sent_cap=max_jobs,
                jobs_sent_count=int(summary["jobs_sent_count"]),
                followers_surface_restored=True,
            )
            log(
                "info",
                "welcome_sender_sent_cap_reached",
                account_id=aid,
                run_id=run_id,
                sent_cap=max_jobs,
                jobs_sent_count=int(summary["jobs_sent_count"]),
                jobs_skipped_count=int(summary["jobs_skipped_count"]),
                processed_recipients=list(summary["processed_recipients"]),
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

        if int(summary["jobs_sent_count"]) >= max_jobs:
            loop_exit_reason = "sent_cap_reached"
            log(
                "info",
                "welcome_sender_sent_cap_reached",
                account_id=aid,
                run_id=run_id,
                sent_cap=max_jobs,
                jobs_sent_count=int(summary["jobs_sent_count"]),
                jobs_skipped_count=int(summary["jobs_skipped_count"]),
                processed_recipients=list(summary["processed_recipients"]),
            )
            break

        if (
            outcome == "skipped"
            and str(result.get("thread_state") or "")
            in ("dm_not_available", "restricted_account")
        ):
            remaining_attempts = max(
                0,
                sender_attempt_cap - len(summary["processed_recipients"]),
            )
            if remaining_attempts > 0:
                log(
                    "info",
                    "welcome_sender_continue_after_skipped_non_dmable",
                    account_id=aid,
                    run_id=run_id,
                    recipient_username=recipient,
                    thread_state=str(result.get("thread_state") or ""),
                    remaining_attempts=remaining_attempts,
                    jobs_sent_count=int(summary["jobs_sent_count"]),
                    sent_cap=max_jobs,
                )

    if loop_exit_reason is None:
        loop_exit_reason = (
            "plan_exhausted" if current_scan_session_mode else "attempt_cap_reached"
        )
    if state_machine.current == "followers_restored" and loop_exit_reason in {
        "plan_exhausted",
        "scan_session_jobs_exhausted",
        "no_pending_job",
    }:
        state_machine.transition(
            "next_job_ready",
            proof="structured_plan_exhausted",
            owner="welcome_sender_job_loop",
        )
        summary["welcome_state_history"].append(dict(state_machine.history[-1]))
    summary["loop_exit_reason"] = loop_exit_reason
    log(
        "info",
        "welcome_list_sender_loop_finished",
        account_id=aid,
        run_id=run_id,
        exit_reason=loop_exit_reason,
        max_jobs=max_jobs,
        sent_cap=max_jobs,
        attempt_cap=sender_attempt_cap,
        jobs_claimed_count=int(summary["jobs_claimed_count"]),
        jobs_sent_count=int(summary["jobs_sent_count"]),
        jobs_skipped_count=int(summary["jobs_skipped_count"]),
    )

    total_ms = (time.perf_counter() - t0) * 1000.0
    claimed = int(summary["jobs_claimed_count"])
    failed = int(summary["jobs_failed_count"])
    sent = int(summary["jobs_sent_count"])

    if str(summary.get("loop_exit_reason") or "") == "no_current_scan_jobs":
        sender_status = "success"
        exit_code = 0
    elif sent >= max_jobs and max_jobs > 0 and failed == 0:
        sender_status = "success"
        exit_code = 0
    elif claimed == 0:
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

    if str(summary.get("failure_reason") or ""):
        sender_status = "failed"
        exit_code = 1
        summary["scan_jobs_cleanup_count"] = 0
        summary["pending_jobs_preserved_after_structured_failure"] = True

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
