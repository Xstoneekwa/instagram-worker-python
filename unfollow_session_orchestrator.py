"""Unfollow session orchestrator.

Phase 2A/2B: non-destructive probe through sheet detection.
Phase 2C: optional real Unfollow action behind explicit config/env opt-in.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import uiautomator2 as u2

import config
import account_protection_lists
import supabase_client
from account_identity_guard import verify_active_instagram_account_matches_expected
from instagram_list_continuation import (
    InstagramListContinuationSignals,
    adaptive_follow_scroll_geometry,
    classify_instagram_list_continuation,
    compare_instagram_list_viewports,
    viewport_fingerprint,
)
from logs import log
from runtime_caps import resolve_unfollow_runtime_cap
from own_following_navigation import (
    apply_unfollow_following_sort_mode,
    detect_own_following_list_screen,
    detect_unfollow_following_sort_control,
    open_own_following_list_from_own_profile,
    verify_unfollow_following_sort_applied,
)
from unfollow_eligibility_engine import (
    evaluate_visible_unfollow_candidates,
    plan_unfollow_targets,
)
from unfollow_list_harvest import (
    harvest_visible_following_rows_for_unfollow,
    normalize_unfollow_username,
)
from unfollow_profile_probe import (
    open_unfollow_actions_sheet_from_profile_probe,
    return_to_following_list_after_unfollow_action,
    return_to_following_list_after_unfollow_probe,
    tap_following_list_username_row_for_unfollow_probe,
    tap_unfollow_in_following_sheet,
    verify_unfollow_action_success_after_tap,
    verify_unfollow_target_profile_strict,
)
from unfollow_settings import UNFOLLOW_MODE_ANY, load_unfollow_settings
from unfollow_ui_coverage_policy import (
    HISTORICAL_ACTION_P90_SECONDS,
    HISTORICAL_ACTION_SAMPLE_COUNT,
    HISTORICAL_ROWS_PER_VIEWPORT,
    HISTORICAL_VIEWPORT_P90_SECONDS,
    HISTORICAL_VIEWPORT_SAMPLE_COUNT,
    SCHEDULED_SESSION_CLEANUP_RESERVE_SECONDS,
    FollowingCoverageTracker,
    build_unfollow_outcome,
    derive_adaptive_coverage_budget,
)
from unfollow_hybrid_strategy import (
    CURSOR_RESTORE_SCROLL_LIMIT,
    DIRECT_SEARCH_FALLBACK_BATCH_LIMIT,
    can_arm_direct_search_fallback,
    choose_hybrid_selection,
    cursor_anchor_matches,
    open_exact_profile_for_unfollow,
)
from unfollow_diagnostic_contract_v2 import UnfollowDiagnosticSession
from unfollow_action_outcome import (
    ACTION_ATTEMPTED_AMBIGUOUS_COOLDOWN_MINUTES,
    UnfollowActionOutcomeClass,
    ambiguous_failure_reason,
    decide_verify_failure_after_recovery,
    is_action_attempted_ambiguous_reason,
)
from unfollow_search_health_policy import SearchSurfaceCircuitBreaker
from instagram_navigation import return_to_search_from_profile

_LAST_UNFOLLOW_SESSION_PROBE_SUMMARY: dict[str, Any] = {}


def get_last_unfollow_session_probe_summary() -> dict[str, Any]:
    return dict(_LAST_UNFOLLOW_SESSION_PROBE_SUMMARY)


def _emit_summary(summary: dict[str, Any]) -> None:
    global _LAST_UNFOLLOW_SESSION_PROBE_SUMMARY
    _LAST_UNFOLLOW_SESSION_PROBE_SUMMARY = dict(summary)
    log("info", "unfollow_session_probe_summary", **summary)


def _return_after_unfollow_profile(
    d: u2.Device,
    *,
    account_username: str,
    direct_exact_search: bool,
) -> dict[str, Any]:
    """Prefer the shallow Search return for direct fallback candidates."""

    if direct_exact_search:
        if return_to_search_from_profile(d, config.INSTAGRAM_PACKAGE):
            return {
                "ok": True,
                "destination": "search",
                "search_session_reused": True,
                "following_list_reopened": False,
            }
        following_ok, _meta = open_own_following_list_from_own_profile(
            d,
            account_username,
        )
        return {
            "ok": bool(following_ok),
            "destination": "following" if following_ok else "unknown",
            "search_session_reused": False,
            "following_list_reopened": bool(following_ok),
        }
    returned = return_to_following_list_after_unfollow_action(
        d,
        account_username=account_username,
    )
    return {
        **dict(returned or {}),
        "destination": "following" if bool((returned or {}).get("ok")) else "unknown",
        "search_session_reused": False,
        "following_list_reopened": False,
    }


def _planned_username_set(plan: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for cand in list(plan.get("candidates") or []):
        if not isinstance(cand, dict):
            continue
        key = normalize_unfollow_username(
            str(cand.get("username_normalized") or cand.get("username") or "")
        )
        if key:
            out.add(key)
    return out


def _scroll_budget_stop_reason(
    planned_usernames: set[str],
    completed_usernames: set[str],
) -> tuple[str, int]:
    remaining = len(planned_usernames - completed_usernames)
    reason = (
        "ui_coverage_budget_exhausted"
        if remaining > 0
        else "eligible_targets_exhausted"
    )
    return reason, remaining


def _observe_canonical_unfollow_list_continuation(
    rows: list[dict[str, Any]],
    harvest_meta: dict[str, Any],
    *,
    before_row_ids: list[str] | None = None,
    account_id: str = "",
    run_id: str | None = None,
) -> str:
    """Read-only shared-contract observation; Unfollow decisions stay unchanged."""
    row_ids = [
        str(row.get("username_normalized") or row.get("username") or "")
        for row in rows
        if isinstance(row, dict)
    ]
    continuity = compare_instagram_list_viewports(
        before_row_ids or [],
        row_ids,
        require_overlap=bool(before_row_ids),
    )
    state = classify_instagram_list_continuation(
        InstagramListContinuationSignals(
            flow="unfollow",
            expected_surface_selected=True,
            primary_row_ids=tuple(row_ids),
            suggestions_visible=bool(
                harvest_meta.get("suggested_for_you_visible")
                or harvest_meta.get("following_list_end_detected")
            ),
            scroll_attempted=before_row_ids is not None,
            viewport_fingerprint_before=continuity.fingerprint_before,
            viewport_fingerprint_after=continuity.fingerprint_after,
            overlap_count=continuity.overlap_count,
            scroll_excessive=continuity.excessive,
            continuation_probe_count=1,
        )
    )
    try:
        if row_ids:
            log(
                "info",
                "instagram_list_primary_rows_detected",
                flow="unfollow",
                account_id=str(account_id or ""),
                target_id="",
                run_id=str(run_id or ""),
                viewport_fingerprint_before=continuity.fingerprint_before,
                viewport_fingerprint_after=continuity.fingerprint_after,
                visible_primary_row_count=len(row_ids),
                overlap_count=continuity.overlap_count,
                scroll_distance=0.0,
                reason=f"read_only_observation:{state.value}",
                elapsed_ms=0.0,
            )
    except Exception:
        pass
    return state.value


def _select_probe_target_row(
    rows: list[dict[str, Any]],
    planned_usernames: set[str],
) -> tuple[dict[str, Any] | None, str]:
    for row in rows:
        key = normalize_unfollow_username(
            str(row.get("username_normalized") or row.get("username") or "")
        )
        if key and key in planned_usernames:
            return row, "plan_match_visible"
    if rows:
        return rows[0], "first_visible_row_ui_probe_fallback"
    return None, ""


def _planned_candidates_by_username(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for cand in list(plan.get("candidates") or []):
        if not isinstance(cand, dict):
            continue
        key = normalize_unfollow_username(
            str(cand.get("username_normalized") or cand.get("username") or "")
        )
        if key:
            out[key] = cand
    return out


def _visible_candidates_by_username(visible_eval: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for cand in list(visible_eval.get("visible_eligible_matches") or []):
        if not isinstance(cand, dict):
            continue
        key = normalize_unfollow_username(
            str(cand.get("username_normalized") or cand.get("username") or "")
        )
        if key:
            out[key] = cand
    return out


def _select_visible_eligible_target_row(
    rows: list[dict[str, Any]],
    visible_candidates_by_username: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any] | None, str]:
    for row in rows:
        key = normalize_unfollow_username(
            str(row.get("username_normalized") or row.get("username") or "")
        )
        if key and key in visible_candidates_by_username:
            return row, "visible_eligible_db_lookup"
    return None, ""


def _select_visible_any_target_row(
    rows: list[dict[str, Any]],
    visible_candidates_by_username: dict[str, dict[str, Any]],
    *,
    completed_usernames: set[str],
) -> tuple[dict[str, Any] | None, str]:
    for row in rows:
        key = normalize_unfollow_username(
            str(row.get("username_normalized") or row.get("username") or "")
        )
        if not key or key in completed_usernames:
            continue
        if key and key in visible_candidates_by_username:
            return row, "visible_any_safe_row"
    return None, ""


def _visible_username_keys(usernames: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in usernames:
        key = normalize_unfollow_username(str(raw or ""))
        if key and key not in seen:
            seen.add(key)
            out.append(key)
    return out


def build_unfollow_diagnostic_v2(
    *,
    rows: list[dict[str, Any]],
    visible_eval: dict[str, Any],
    planned_usernames: set[str],
    row_cache: dict[str, dict[str, Any] | None],
    attempted_usernames: set[str],
    verified_usernames: set[str],
    persisted_usernames: set[str],
    viewport_index: int,
    scroll_depth: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Build read-only Unfollow lineage diagnostics from already loaded data.

    The capped plan is authoritative only for its own members.  Candidates
    outside that plan deliberately expose ``db_eligible_at_start=None`` rather
    than inventing eligibility for a backlog row that was not admitted at T0.
    """
    ordered = _visible_username_keys(
        [str(row.get("username") or "") for row in rows]
    )
    eligible = _visible_candidates_by_username(visible_eval)
    skip_reasons: dict[str, str] = {}
    for row in list(visible_eval.get("visible_ineligible_rows") or []):
        if not isinstance(row, dict):
            continue
        key = normalize_unfollow_username(
            str(row.get("username_normalized") or row.get("username") or "")
        )
        if key:
            skip_reasons[key] = str(row.get("skip_reason") or "unknown")

    eligibility_map: dict[str, bool] = {}
    acted_map: dict[str, dict[str, bool]] = {}
    lineage: list[dict[str, Any]] = []
    for key in ordered:
        is_eligible = key in eligible
        eligibility_map[key] = is_eligible
        action_state = {
            "attempted": key in attempted_usernames,
            "verified": key in verified_usernames,
            "persisted": key in persisted_usernames,
        }
        acted_map[key] = action_state
        planned_at_start = key in planned_usernames
        lineage.append(
            {
                "username": key,
                "db_eligible_at_start": True if planned_at_start else None,
                "db_eligibility_scope": (
                    "known_from_capped_session_plan"
                    if planned_at_start
                    else "unknown_outside_capped_session_plan"
                ),
                "db_row_present_at_viewport": isinstance(row_cache.get(key), dict),
                "ui_seen": True,
                "viewport_index": max(1, int(viewport_index or 1)),
                "scroll_depth": max(0, int(scroll_depth or 0)),
                "eligible_at_viewport": is_eligible,
                "skip_reason": skip_reasons.get(key, ""),
                "action_attempted": action_state["attempted"],
                "action_verified": action_state["verified"],
                "persistence_ok": action_state["persisted"],
                "monotonic_s": round(time.perf_counter(), 6),
            }
        )

    viewport = {
        "viewport_index": max(1, int(viewport_index or 1)),
        "scroll_depth": max(0, int(scroll_depth or 0)),
        "ordered_visible_usernames": ordered,
        "viewport_fingerprint": viewport_fingerprint(ordered),
        "candidate_eligibility_map": eligibility_map,
        "candidate_skip_reason_map": skip_reasons,
        "candidate_acted_map": acted_map,
        "monotonic_s": round(time.perf_counter(), 6),
    }
    return viewport, lineage


def _real_action_enabled() -> bool:
    return bool(getattr(config, "UNFOLLOW_SESSION_REAL_ACTION_ENABLED", False))


def _real_action_max_per_run() -> int:
    return max(0, int(getattr(config, "UNFOLLOW_SESSION_REAL_ACTION_MAX_PER_RUN", 1)))


def _effective_real_action_max_per_run(
    settings: Any,
    env_hard_cap: int | None = None,
    day_remaining: int | None = None,
) -> int:
    db_limit = max(0, int(getattr(settings, "session_limit", 0) or 0))
    env_cap = _real_action_max_per_run() if env_hard_cap is None else max(0, int(env_hard_cap))
    runtime_cap = resolve_unfollow_runtime_cap(
        db_unfollow_per_session_limit=db_limit,
        runtime_cap_mode=getattr(settings, "runtime_cap_mode", "prod_normal"),
        runtime_safety_cap=getattr(settings, "runtime_safety_cap", None),
        env_real_action_max_per_run=env_cap,
    )
    caps = [db_limit, int(runtime_cap.get("runtime_cap") or 0)]
    if str(runtime_cap.get("runtime_cap_mode") or "") != "prod_normal":
        caps.append(env_cap)
    if day_remaining is not None:
        caps.append(max(0, int(day_remaining)))
    return min(caps)


def _parse_runtime_deadline(value: str | None) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _unfollow_time_budget(
    requested_actions: int,
    *,
    business_action_deadline: str | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    deadline = _parse_runtime_deadline(business_action_deadline)
    estimate = max(
        1,
        int(
            getattr(
                config,
                "UNFOLLOW_CONSERVATIVE_ACTION_SECONDS",
                HISTORICAL_ACTION_P90_SECONDS,
            )
        ),
    )
    reserve = max(0, int(getattr(config, "UNFOLLOW_FINALIZATION_RESERVE_SECONDS", 30)))
    if deadline is None:
        return {
            "business_action_deadline": None,
            "remaining_seconds": None,
            "estimated_seconds_per_action": estimate,
            "finalization_reserve_seconds": reserve,
            "time_bounded_action_cap": max(0, int(requested_actions)),
        }
    current = now or datetime.now(timezone.utc)
    remaining = max(0.0, (deadline - current).total_seconds())
    time_cap = max(0, int((remaining - reserve) // estimate))
    return {
        "business_action_deadline": deadline.isoformat().replace("+00:00", "Z"),
        "remaining_seconds": round(remaining, 3),
        "estimated_seconds_per_action": estimate,
        "finalization_reserve_seconds": reserve,
        "time_bounded_action_cap": min(max(0, int(requested_actions)), time_cap),
    }


def _scroll_max_passes() -> int:
    return max(0, int(getattr(config, "UNFOLLOW_SESSION_SCROLL_MAX_PASSES", 10)))


def _runtime_adaptive_coverage_budget(
    *,
    quota_remaining: int,
    eligible_remaining: int,
    business_action_deadline: str | None,
    now: datetime | None = None,
    outreach_reserve_seconds: int = 0,
) -> Any:
    deadline = _parse_runtime_deadline(business_action_deadline)
    current = now or datetime.now(timezone.utc)
    if deadline is not None:
        # business_action_deadline is already session_end-T10. Reconstruct the
        # scheduled remaining duration so the pure policy subtracts T10 once.
        session_remaining_seconds = max(0.0, (deadline - current).total_seconds())
        session_remaining_seconds += SCHEDULED_SESSION_CLEANUP_RESERVE_SECONDS
        deadline_source = "scheduler_business_action_deadline"
    else:
        session_remaining_seconds = max(
            SCHEDULED_SESSION_CLEANUP_RESERVE_SECONDS,
            int(getattr(config, "UNFOLLOW_FALLBACK_SESSION_SECONDS", 6 * 60 * 60)),
        )
        deadline_source = "fallback_six_hour_window"
    return derive_adaptive_coverage_budget(
        quota_remaining=quota_remaining,
        eligible_remaining=eligible_remaining,
        session_remaining_seconds=session_remaining_seconds,
        deadline_source=deadline_source,
        recovery_reserve_seconds=int(
            getattr(config, "UNFOLLOW_RECOVERY_RESERVE_SECONDS", 75)
        ),
        outreach_reserve_seconds=max(0, int(outreach_reserve_seconds)),
        navigation_reserve_seconds=int(
            getattr(config, "UNFOLLOW_NAVIGATION_RESERVE_SECONDS", 30)
        ),
        lightweight_deadline_check_interval_seconds=int(
            getattr(config, "UNFOLLOW_DEADLINE_CHECK_INTERVAL_SECONDS", 5 * 60)
        ),
        lightweight_deadline_check_action_interval=int(
            getattr(config, "UNFOLLOW_DEADLINE_CHECK_ACTION_INTERVAL", 25)
        ),
    )


_UNFOLLOW_UNSAFE_MARKER_TEXT = {
    "challenge_required": "challenge_required",
    "help us confirm": "identity_confirmation_required",
    "verify your identity": "identity_confirmation_required",
    "we restrict certain activity": "activity_restricted",
    "try again later": "activity_restricted",
    "your account has been compromised": "account_compromised",
    "account suspended": "account_suspended",
}


def _detect_unfollow_unsafe_markers(d: u2.Device) -> list[str]:
    try:
        hierarchy = str(d.dump_hierarchy(compressed=False) or "").lower()
    except Exception:
        return []
    return sorted(
        {
            marker
            for text, marker in _UNFOLLOW_UNSAFE_MARKER_TEXT.items()
            if text in hierarchy
        }
    )


def _merge_unfollow_surface_unsafe_markers(
    markers: list[str],
    following_detection: dict[str, Any],
) -> list[str]:
    merged = set(markers)
    if str(following_detection.get("failure_reason") or "") == "following_list_account_title_mismatch":
        merged.add("active_account_mismatch")
    return sorted(merged)


def _scroll_v2_lite_enabled() -> bool:
    return bool(getattr(config, "UNFOLLOW_SESSION_SCROLL_V2_LITE_ENABLED", False))


def _scroll_v2_lite_distance_ratio() -> float:
    return max(
        0.30,
        min(float(getattr(config, "UNFOLLOW_SESSION_SCROLL_V2_LITE_DISTANCE_RATIO", 0.72)), 0.78),
    )


def _scroll_v2_lite_settle_s() -> float:
    return max(
        0.20,
        min(float(getattr(config, "UNFOLLOW_SESSION_SCROLL_V2_LITE_SETTLE_S", 0.45)), 1.00),
    )


def _scroll_v2_lite_min_new_usernames() -> int:
    return max(0, int(getattr(config, "UNFOLLOW_SESSION_SCROLL_V2_LITE_MIN_NEW_USERNAMES", 3)))


def _scroll_v2_lite_max_unchanged_scrolls() -> int:
    return max(1, int(getattr(config, "UNFOLLOW_SESSION_SCROLL_V2_LITE_MAX_UNCHANGED_SCROLLS", 3)))


def _max_recoverable_action_failures() -> int:
    # A single transient CTA/render failure must never terminate a full phase.
    return max(1, int(getattr(config, "UNFOLLOW_SESSION_MAX_RECOVERABLE_ACTION_FAILURES", 2)))


def _stop_after_skipped() -> int:
    return max(0, int(getattr(config, "UNFOLLOW_SESSION_STOP_AFTER_SKIPPED", 0)))


def _max_minutes() -> int:
    return max(0, int(getattr(config, "UNFOLLOW_SESSION_MAX_MINUTES", 0)))


_STRICT_EXPLORATION_SKIP_REASONS = {
    "not_found_in_interacted_users",
    "too_soon",
    "whitelist",
    "already_unfollowed",
    "missing_followed_by_bot",
    "missing_followed_at",
    "lifecycle_ineligible",
    "follow_status_not_following",
    "missing_followback_confirmation",
    "not_following_back",
    "followback_state_not_allowed",
    "unfollow_disabled",
    "unfollow_mode_not_supported",
    "invalid_username",
    "own_account",
    "already_completed_in_run",
}


def _base_session_summary(
    *,
    aid: str,
    uname: str,
    run_id: str | None,
    settings: Any,
    plan: dict[str, Any],
    probe_only: bool,
    real_action_enabled: bool | None = None,
    real_action_max_per_run: int | None = None,
) -> dict[str, Any]:
    real_enabled = _real_action_enabled() if real_action_enabled is None else bool(real_action_enabled)
    real_max = (
        _real_action_max_per_run()
        if real_action_max_per_run is None
        else max(0, int(real_action_max_per_run))
    )
    return {
        "account_id": aid,
        "account_username": uname,
        "run_id": run_id,
        "unfollow_mode": settings.mode,
        "unfollow_after_days": settings.after_days,
        "unfollow_sort_mode_requested": str(getattr(settings, "sort_mode", "default") or "default"),
        "plan_reason": str(plan.get("plan_reason") or ""),
        "candidates_planned_count": int(plan.get("candidates_count") or 0),
        "eligible_total": int(
            plan.get("eligible_total")
            if plan.get("eligible_total") is not None
            else plan.get("candidates_count")
            or 0
        ),
        "unplanned_eligible_count": int(plan.get("unplanned_eligible_count") or 0),
        "last_run_eligible_at_start": int(
            plan.get("eligible_total")
            if plan.get("eligible_total") is not None
            else plan.get("candidates_count")
            or 0
        ),
        "source_rows_loaded": int(plan.get("source_rows_loaded") or 0),
        "query_limit": int(plan.get("query_limit") or 0),
        "page_size": int(plan.get("page_size") or plan.get("query_limit") or 0),
        "pages_loaded": int(plan.get("pages_loaded") or 0),
        "page_requests": int(plan.get("page_requests") or 0),
        "pagination_used": bool(plan.get("pagination_used")),
        "pagination_strategy": str(plan.get("pagination_strategy") or ""),
        "candidate_scan_exhaustive": bool(plan.get("candidate_scan_exhaustive")),
        "candidate_funnel_reconciled": bool(plan.get("candidate_funnel_reconciled")),
        "candidate_skip_counts": dict(plan.get("skipped_counts") or {}),
        "candidate_skipped_total": int(plan.get("skipped_total") or 0),
        "scan_as_of": str(plan.get("scan_as_of") or ""),
        "following_surface_ok": False,
        "visible_rows_count": 0,
        "row_cta_counts": {},
        "visible_plan_matches_count": 0,
        "visible_plan_matches_usernames": [],
        "visible_eligible_matches_count": 0,
        "visible_eligible_matches_usernames": [],
        "visible_eligibility_lookup_count": 0,
        "visible_eligibility_skip_counts": {},
        "visible_match_source": "",
        "visible_eligibility_lookup_ms": 0.0,
        "visible_eligibility_eval_ms": 0.0,
        "visible_target_selection_ms": 0.0,
        "visible_eligibility_cache_hits": 0,
        "visible_eligibility_cache_misses": 0,
        "visible_eligibility_db_query_count": 0,
        "visible_eligibility_total_db_queries": 0,
        "visible_eligibility_total_cache_hits": 0,
        "visible_eligibility_total_cache_misses": 0,
        "visible_eligibility_total_lookup_ms": 0.0,
        "visible_eligibility_total_eval_ms": 0.0,
        "multi_action_mode": False,
        "scroll_passes_used": 0,
        "scroll_stop_reason": "",
        "multi_action_stop_reason": "",
        "eligible_db_remaining": int(
            plan.get("eligible_total")
            if plan.get("eligible_total") is not None
            else plan.get("candidates_count")
            or 0
        ),
        "ui_coverage_status": "not_started",
        "resume_recommended": False,
        "probe_target_username": "",
        "probe_target_selection_reason": "",
        "real_target_username": "",
        "target_profile_open_ok": False,
        "following_actions_sheet_open_ok": False,
        "unfollow_option_visible": False,
        "return_to_following_list_ok": False,
        "probe_only": probe_only,
        "real_action_enabled": real_enabled,
        "real_action_max_per_run": real_max,
        "db_unfollow_per_day_limit": int(getattr(settings, "day_limit", 0) or 0),
        "unfollows_done_today": 0,
        "unfollow_day_remaining_today": int(getattr(settings, "day_limit", 0) or 0),
        "source_day_counter": "ig_interacted_users.unfollowed_at",
        "unfollow_actions_sent": 0,
        "unfollow_actions_verified": 0,
        "unfollow_actions_failed": 0,
        "unfollow_results_persisted_count": 0,
        "unfollow_outcomes_persisted_count": 0,
        "unfollow_action_verify_ok": False,
        "unfollow_persistence_ok": False,
        "unfollow_observed_success_count": 0,
        "unfollow_observed_success_usernames": [],
        "unfollow_observed_successes": [],
        "unfollow_enabled": bool(settings.enabled),
        "sort_apply_attempted": False,
        "sort_apply_ok": False,
        "sort_mode_ui_before": "",
        "sort_mode_ui_after": "",
        "sort_verification_strength": "",
        "pre_sort_visible_rows_count": 0,
        "post_sort_visible_rows_count": 0,
        "pre_sort_visible_plan_matches_count": 0,
        "post_sort_visible_plan_matches_count": 0,
        "any_mode_visible_candidates_count": 0,
        "any_mode_whitelist_skips_count": 0,
        "any_mode_rows_without_interaction_history_count": 0,
        "any_mode_selected_count": 0,
    }


def _harvest_summary_fields(
    rows: list[dict[str, Any]],
    harvest_meta: dict[str, Any],
    planned_usernames: set[str],
) -> dict[str, Any]:
    visible_matches = [
        str(row.get("username") or "")
        for row in rows
        if normalize_unfollow_username(str(row.get("username_normalized") or row.get("username") or ""))
        in planned_usernames
    ]
    return {
        "following_surface_ok": True,
        "visible_rows_count": len(rows),
        "row_cta_counts": dict(harvest_meta.get("row_cta_counts") or {}),
        "visible_plan_matches_count": len(visible_matches),
        "visible_plan_matches_usernames": visible_matches[:50],
        "following_list_end_detected": bool(harvest_meta.get("following_list_end_detected")),
        "suggested_for_you_visible": bool(harvest_meta.get("suggested_for_you_visible")),
        "suggestion_follow_buttons_count": int(harvest_meta.get("suggestion_follow_buttons_count") or 0),
        "following_list_end_reason": str(harvest_meta.get("following_list_end_reason") or ""),
    }


def _visible_eligibility_summary_fields(visible_eval: dict[str, Any]) -> dict[str, Any]:
    return {
        "visible_eligible_matches_count": int(
            visible_eval.get("visible_eligible_matches_count") or 0
        ),
        "visible_eligible_matches_usernames": list(
            visible_eval.get("visible_eligible_matches_usernames") or []
        )[:50],
        "visible_eligibility_lookup_count": int(
            visible_eval.get("visible_eligibility_lookup_count") or 0
        ),
        "visible_eligibility_skip_counts": dict(
            visible_eval.get("visible_eligibility_skip_counts") or {}
        ),
        "visible_match_source": str(
            visible_eval.get("visible_match_source") or "visible_username_db_lookup"
        ),
        "visible_eligibility_lookup_ms": float(
            visible_eval.get("visible_eligibility_lookup_ms") or 0.0
        ),
        "visible_eligibility_eval_ms": float(
            visible_eval.get("visible_eligibility_eval_ms") or 0.0
        ),
        "visible_target_selection_ms": float(
            visible_eval.get("visible_target_selection_ms") or 0.0
        ),
        "visible_eligibility_cache_hits": int(
            visible_eval.get("visible_eligibility_cache_hits") or 0
        ),
        "visible_eligibility_cache_misses": int(
            visible_eval.get("visible_eligibility_cache_misses") or 0
        ),
        "visible_eligibility_db_query_count": int(
            visible_eval.get("visible_eligibility_db_query_count") or 0
        ),
    }


def _visible_any_summary_fields(visible_eval: dict[str, Any]) -> dict[str, Any]:
    return {
        "visible_eligible_matches_count": int(
            visible_eval.get("visible_eligible_matches_count") or 0
        ),
        "visible_eligible_matches_usernames": list(
            visible_eval.get("visible_eligible_matches_usernames") or []
        )[:50],
        "visible_eligibility_lookup_count": int(
            visible_eval.get("visible_eligibility_lookup_count") or 0
        ),
        "visible_eligibility_skip_counts": dict(
            visible_eval.get("visible_eligibility_skip_counts") or {}
        ),
        "visible_match_source": str(visible_eval.get("visible_match_source") or "visible_any_ui_rows"),
        "visible_eligibility_lookup_ms": float(
            visible_eval.get("visible_eligibility_lookup_ms") or 0.0
        ),
        "visible_eligibility_eval_ms": float(
            visible_eval.get("visible_eligibility_eval_ms") or 0.0
        ),
        "visible_eligibility_cache_hits": int(
            visible_eval.get("visible_eligibility_cache_hits") or 0
        ),
        "visible_eligibility_cache_misses": int(
            visible_eval.get("visible_eligibility_cache_misses") or 0
        ),
        "visible_eligibility_db_query_count": int(
            visible_eval.get("visible_eligibility_db_query_count") or 0
        ),
        "any_mode_visible_candidates_count": int(
            visible_eval.get("any_mode_visible_candidates_count") or 0
        ),
        "any_mode_whitelist_skips_count": int(
            visible_eval.get("any_mode_whitelist_skips_count") or 0
        ),
        "any_mode_rows_without_interaction_history_count": int(
            visible_eval.get("any_mode_rows_without_interaction_history_count") or 0
        ),
    }


def _evaluate_visible_rows_for_unfollow_probe(
    aid: str,
    uname: str,
    rows: list[dict[str, Any]],
    *,
    settings: Any,
    row_cache: dict[str, dict[str, Any] | None],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, Any], bool]:
    any_mode_active = str(getattr(settings, "mode", "") or "") == UNFOLLOW_MODE_ANY
    if any_mode_active:
        visible_eval = _evaluate_visible_unfollow_any_with_session_cache(
            aid,
            rows,
            account_username=uname,
            row_cache=row_cache,
            completed_usernames=set(),
        )
        visible_candidates = _visible_candidates_by_username(visible_eval)
        return visible_eval, visible_candidates, _visible_any_summary_fields(visible_eval), True

    visible_usernames = [str(row.get("username") or "") for row in rows]
    visible_eval = _evaluate_visible_unfollow_with_session_cache(
        aid,
        visible_usernames,
        settings=settings,
        row_cache=row_cache,
    )
    return (
        visible_eval,
        _visible_candidates_by_username(visible_eval),
        _visible_eligibility_summary_fields(visible_eval),
        False,
    )


def _evaluate_visible_unfollow_with_session_cache(
    aid: str,
    visible_usernames: list[str],
    *,
    settings: Any,
    row_cache: dict[str, dict[str, Any] | None],
) -> dict[str, Any]:
    keys = _visible_username_keys(visible_usernames)
    missing = [key for key in keys if key not in row_cache]
    cache_hits = len(keys) - len(missing)

    lookup_t0 = time.perf_counter()
    fetched: dict[str, dict[str, Any]] = {}
    if missing:
        fetched = supabase_client.fetch_visible_unfollow_eligibility_rows(aid, missing)
        for key in missing:
            row_cache[key] = fetched.get(key)
    lookup_ms = round((time.perf_counter() - lookup_t0) * 1000.0, 2)

    rows_for_eval = {
        key: row
        for key, row in row_cache.items()
        if key in keys and isinstance(row, dict)
    }
    eval_t0 = time.perf_counter()
    visible_eval = evaluate_visible_unfollow_candidates(
        aid,
        keys,
        settings=settings,
        db_rows_by_username=rows_for_eval,
    )
    eval_ms = round((time.perf_counter() - eval_t0) * 1000.0, 2)
    visible_eval.update(
        {
            "visible_eligibility_lookup_ms": lookup_ms,
            "visible_eligibility_eval_ms": eval_ms,
            "visible_eligibility_cache_hits": cache_hits,
            "visible_eligibility_cache_misses": len(missing),
            "visible_eligibility_db_query_count": 1 if missing else 0,
            "visible_eligibility_cache_size": len(row_cache),
        }
    )
    log(
        "info",
        "unfollow_visible_eligibility_cache_stats",
        visible_usernames_count=len(keys),
        visible_eligibility_cache_hits=cache_hits,
        visible_eligibility_cache_misses=len(missing),
        visible_eligibility_db_query_count=1 if missing else 0,
        visible_eligibility_lookup_ms=lookup_ms,
        visible_eligibility_eval_ms=eval_ms,
        visible_eligibility_cache_size=len(row_cache),
    )
    return visible_eval


def _evaluate_visible_unfollow_any_with_session_cache(
    aid: str,
    rows: list[dict[str, Any]],
    *,
    account_username: str,
    row_cache: dict[str, dict[str, Any] | None],
    completed_usernames: set[str],
) -> dict[str, Any]:
    keys = _visible_username_keys([str(row.get("username") or "") for row in rows])
    log(
        "info",
        "unfollow_any_visible_candidate_evaluation_started",
        account_id=aid,
        account_username=account_username,
        visible_usernames_count=len(keys),
        completed_usernames_count=len(completed_usernames),
    )
    missing = [key for key in keys if key not in row_cache]
    cache_hits = len(keys) - len(missing)

    lookup_t0 = time.perf_counter()
    fetched: dict[str, dict[str, Any]] = {}
    if missing:
        fetched = supabase_client.fetch_visible_unfollow_eligibility_rows(aid, missing)
        for key in missing:
            row_cache[key] = fetched.get(key)
    lookup_ms = round((time.perf_counter() - lookup_t0) * 1000.0, 2)

    eval_t0 = time.perf_counter()
    account_key = normalize_unfollow_username(account_username)
    skip_counts: dict[str, int] = {
        "invalid_username": 0,
        "own_account": 0,
        "already_completed_in_run": 0,
        "already_unfollowed": 0,
        "row_cta_follow": 0,
        "row_cta_follow_back": 0,
        "whitelist": 0,
        "candidate_action_ambiguous_cooldown": 0,
    }
    candidates: list[dict[str, Any]] = []
    ineligible_rows: list[dict[str, Any]] = []
    rows_without_history = 0
    whitelist_skips = 0

    for row in rows:
        username = str(row.get("username") or "")
        key = normalize_unfollow_username(str(row.get("username_normalized") or username))
        row_index = int(row.get("row_index") or 0)
        row_cta_class = str(row.get("row_cta_class") or "unknown")
        db_row = row_cache.get(key)
        interaction_row_id = str((db_row or {}).get("id") or "") if isinstance(db_row, dict) else ""
        reject_reason = ""

        if not key:
            reject_reason = "invalid_username"
        elif key == account_key:
            reject_reason = "own_account"
        elif key in completed_usernames:
            reject_reason = "already_completed_in_run"
        elif row_cta_class == "follow":
            reject_reason = "row_cta_follow"
        elif row_cta_class == "follow_back":
            reject_reason = "row_cta_follow_back"
        elif account_protection_lists.is_unfollow_protected(key):
            reject_reason = "whitelist"
        elif isinstance(db_row, dict) and db_row.get("unfollowed_at"):
            reject_reason = "already_unfollowed"
        elif isinstance(db_row, dict) and is_action_attempted_ambiguous_reason(
            str(db_row.get("unfollow_skip_reason") or "")
        ):
            attempted_at = supabase_client.parse_utc_iso_timestamp(
                db_row.get("last_unfollow_attempt_at")
            )
            if (
                attempted_at is not None
                and attempted_at
                + timedelta(minutes=ACTION_ATTEMPTED_AMBIGUOUS_COOLDOWN_MINUTES)
                > datetime.now(timezone.utc)
            ):
                reject_reason = "candidate_action_ambiguous_cooldown"

        if reject_reason:
            skip_counts[reject_reason] = int(skip_counts.get(reject_reason, 0)) + 1
            reject_event = {
                "own_account": "unfollow_any_reject_own_account",
                "already_completed_in_run": "unfollow_any_session_cache_skip",
                "already_unfollowed": "unfollow_any_visible_stale_already_unfollowed_skip",
                "row_cta_follow": "unfollow_any_reject_row_cta_follow",
                "row_cta_follow_back": "unfollow_any_reject_row_cta_follow_back",
            }.get(reject_reason)
            if reject_event:
                log(
                    "info",
                    reject_event,
                    username=username or key,
                    username_normalized=key,
                    row_index=row_index,
                    row_cta_class=row_cta_class,
                    interaction_row_id=interaction_row_id,
                    reject_reason=reject_reason,
                )
            if reject_reason == "already_unfollowed":
                log(
                    "info",
                    "unfollow_any_visible_following_but_db_already_unfollowed",
                    username=username or key,
                    username_normalized=key,
                    row_index=row_index,
                    row_cta_class=row_cta_class,
                    interaction_row_id=interaction_row_id,
                    unfollowed_at=(db_row or {}).get("unfollowed_at") if isinstance(db_row, dict) else None,
                    reject_reason=reject_reason,
                )
            if reject_reason == "whitelist":
                whitelist_skips += 1
                log(
                    "info",
                    "unfollow_any_whitelist_skip",
                    username=username or key,
                    username_normalized=key,
                    row_index=row_index,
                    row_cta_class=row_cta_class,
                    interaction_row_id=interaction_row_id,
                    reject_reason=reject_reason,
                )
            log(
                "info",
                "unfollow_any_visible_candidate_rejected",
                username=username or key,
                username_normalized=key,
                row_index=row_index,
                row_cta_class=row_cta_class,
                interaction_row_id=interaction_row_id,
                reject_reason=reject_reason,
            )
            ineligible_rows.append(
                {
                    "username": username or key,
                    "username_normalized": key,
                    "visible_index": row_index,
                    "eligible": False,
                    "skip_reason": reject_reason,
                    "interaction_row_id": interaction_row_id,
                    "row_cta_class": row_cta_class,
                }
            )
            continue

        if db_row is None:
            rows_without_history += 1

        candidate = {
            "username": username or key,
            "username_normalized": key,
            "visible_index": row_index,
            "interaction_row_id": interaction_row_id,
            "row_cta_class": row_cta_class,
            "eligibility_reason": "unfollow_any_visible_safe_row",
            "has_interaction_history": isinstance(db_row, dict),
        }
        candidates.append(candidate)
        log(
            "info",
            "unfollow_any_visible_candidate_allowed",
            username=username or key,
            username_normalized=key,
            row_index=row_index,
            row_cta_class=row_cta_class,
            interaction_row_id=interaction_row_id,
        )

    eval_ms = round((time.perf_counter() - eval_t0) * 1000.0, 2)
    out = {
        "visible_eligible_matches": candidates,
        "visible_ineligible_rows": ineligible_rows,
        "visible_eligibility_skip_counts": skip_counts,
        "visible_eligibility_lookup_count": len(keys),
        "visible_eligible_matches_count": len(candidates),
        "visible_eligible_matches_usernames": [
            str(c.get("username") or "") for c in candidates
        ],
        "visible_match_source": "visible_any_ui_rows",
        "visible_eligibility_lookup_ms": lookup_ms,
        "visible_eligibility_eval_ms": eval_ms,
        "visible_eligibility_cache_hits": cache_hits,
        "visible_eligibility_cache_misses": len(missing),
        "visible_eligibility_db_query_count": 1 if missing else 0,
        "visible_eligibility_cache_size": len(row_cache),
        "any_mode_visible_candidates_count": len(candidates),
        "any_mode_whitelist_skips_count": whitelist_skips,
        "any_mode_rows_without_interaction_history_count": rows_without_history,
    }
    log(
        "info",
        "unfollow_any_visible_candidates_evaluated",
        account_id=aid,
        visible_eligibility_lookup_count=out["visible_eligibility_lookup_count"],
        any_mode_visible_candidates_count=out["any_mode_visible_candidates_count"],
        any_mode_whitelist_skips_count=whitelist_skips,
        any_mode_rows_without_interaction_history_count=rows_without_history,
        visible_eligibility_cache_hits=cache_hits,
        visible_eligibility_cache_misses=len(missing),
        visible_eligibility_db_query_count=1 if missing else 0,
        visible_eligibility_lookup_ms=lookup_ms,
        visible_eligibility_eval_ms=eval_ms,
    )
    if candidates:
        log(
            "info",
            "unfollow_any_visible_candidate_found",
            account_id=aid,
            visible_eligible_matches_count=len(candidates),
            visible_eligible_matches_usernames=out["visible_eligible_matches_usernames"][:50],
        )
    else:
        log(
            "info",
            "unfollow_any_visible_candidate_none_found",
            account_id=aid,
            visible_eligibility_lookup_count=out["visible_eligibility_lookup_count"],
            visible_eligibility_skip_counts=skip_counts,
        )
    return out


def _aggregate_visible_any_perf_totals(totals: dict[str, Any], visible_eval: dict[str, Any]) -> None:
    totals["any_mode_visible_candidates_count"] = int(
        totals.get("any_mode_visible_candidates_count") or 0
    ) + int(visible_eval.get("any_mode_visible_candidates_count") or 0)
    totals["any_mode_whitelist_skips_count"] = int(
        totals.get("any_mode_whitelist_skips_count") or 0
    ) + int(visible_eval.get("any_mode_whitelist_skips_count") or 0)
    totals["any_mode_rows_without_interaction_history_count"] = int(
        totals.get("any_mode_rows_without_interaction_history_count") or 0
    ) + int(visible_eval.get("any_mode_rows_without_interaction_history_count") or 0)


def _aggregate_visible_perf_totals(totals: dict[str, Any], visible_eval: dict[str, Any]) -> None:
    totals["visible_eligibility_total_db_queries"] = int(
        totals.get("visible_eligibility_total_db_queries") or 0
    ) + int(visible_eval.get("visible_eligibility_db_query_count") or 0)
    totals["visible_eligibility_total_cache_hits"] = int(
        totals.get("visible_eligibility_total_cache_hits") or 0
    ) + int(visible_eval.get("visible_eligibility_cache_hits") or 0)
    totals["visible_eligibility_total_cache_misses"] = int(
        totals.get("visible_eligibility_total_cache_misses") or 0
    ) + int(visible_eval.get("visible_eligibility_cache_misses") or 0)
    totals["visible_eligibility_total_lookup_ms"] = round(
        float(totals.get("visible_eligibility_total_lookup_ms") or 0.0)
        + float(visible_eval.get("visible_eligibility_lookup_ms") or 0.0),
        2,
    )
    totals["visible_eligibility_total_eval_ms"] = round(
        float(totals.get("visible_eligibility_total_eval_ms") or 0.0)
        + float(visible_eval.get("visible_eligibility_eval_ms") or 0.0),
        2,
    )


def _scroll_following_list_for_unfollow(
    d: u2.Device,
    *,
    account_username: str,
    before_rows: list[dict[str, Any]] | None = None,
    previous_actual_overlap: int | None = None,
) -> dict[str, Any]:
    """Advance Following with the shared Follow 7+1 continuity contract."""
    strategy = "canonical_adaptive_7_plus_1"
    settle_s = max(0.45, _scroll_v2_lite_settle_s())
    try:
        w, h = d.window_size()
    except Exception:
        w, h = 1080, 2400
    if before_rows is None:
        before_rows, _ = harvest_visible_following_rows_for_unfollow(
            d,
            account_username=account_username,
        )
    before_rows = list(before_rows or [])
    before_ids = _visible_username_keys(
        [str(row.get("username_normalized") or row.get("username") or "") for row in before_rows]
    )
    row_centers_y = [
        int(list(row.get("row_center") or [0, 0])[1])
        for row in before_rows
        if len(list(row.get("row_center") or [])) >= 2
        and int(list(row.get("row_center") or [0, 0])[1]) > 0
    ]
    geometry = adaptive_follow_scroll_geometry(
        w,
        h,
        row_centers_y,
        previous_actual_overlap=previous_actual_overlap,
    )
    x = int(geometry["x"])
    y_start = int(geometry["y_start"])
    y_end = int(geometry["y_end"])
    distance_ratio = float(geometry["distance_ratio"])
    out = {
        "ok": False,
        "failure_reason": "",
        "tap_x": x,
        "start_y": y_start,
        "end_y": y_end,
        "scroll_strategy": strategy,
        "scroll_v2_lite_enabled": False,
        "scroll_distance_ratio": round(float(distance_ratio), 4),
        "scroll_settle_s": round(float(settle_s), 3),
        "scroll_duration_ms": 0.0,
        "target_new_rows": int(geometry.get("target_new_rows") or 0),
        "target_overlap_rows": int(geometry.get("target_overlap_rows") or 0),
        "median_row_spacing_px": int(geometry.get("median_row_spacing_px") or 0),
        "adaptive": bool(geometry.get("adaptive")),
        "viewport_fingerprint_before": "",
        "viewport_fingerprint_after": "",
        "actual_new_rows": 0,
        "actual_overlap": 0,
        "depth_advanced": False,
        "corrective_backstep_used": False,
    }

    def safe_log_fields() -> dict[str, Any]:
        # Viewport fingerprints and counts are sufficient for continuity
        # forensics; never emit the harvested username rows as one log payload.
        return {
            key: value
            for key, value in out.items()
            if key not in {"rows_after", "harvest_meta_after", "surface_detection"}
        }
    log(
        "info",
        "unfollow_scroll_7_plus_1_started",
        scroll_strategy=strategy,
        target_new_rows=out["target_new_rows"],
        target_overlap_rows=out["target_overlap_rows"],
        visible_primary_row_count=len(before_ids),
        scroll_distance_ratio=out["scroll_distance_ratio"],
        start_y=y_start,
        end_y=y_end,
    )
    scroll_t0 = time.perf_counter()
    try:
        d.swipe(x, y_start, x, y_end, float(geometry["duration_s"]))
    except Exception as exc:
        out["failure_reason"] = "swipe_failed"
        out["error"] = str(exc)[:200]
        out["scroll_duration_ms"] = round((time.perf_counter() - scroll_t0) * 1000.0, 2)
        return out
    time.sleep(settle_s)
    out["scroll_duration_ms"] = round((time.perf_counter() - scroll_t0) * 1000.0, 2)
    after_rows, after_meta = harvest_visible_following_rows_for_unfollow(
        d,
        account_username=account_username,
    )
    after_ids = _visible_username_keys(
        [str(row.get("username_normalized") or row.get("username") or "") for row in after_rows]
    )
    continuity = compare_instagram_list_viewports(before_ids, after_ids)
    out.update(
        {
            "rows_after": after_rows,
            "harvest_meta_after": after_meta,
            "viewport_fingerprint_before": continuity.fingerprint_before,
            "viewport_fingerprint_after": continuity.fingerprint_after,
            "actual_new_rows": continuity.new_row_count,
            "actual_overlap": continuity.overlap_count,
            "continuity_reason": continuity.reason,
            "scroll_excessive": continuity.excessive,
            "viewport_unchanged": continuity.unchanged,
            "depth_advanced": continuity.continuity_proved,
        }
    )
    det = detect_own_following_list_screen(d, account_username=account_username)
    out["surface_detection"] = det
    out["surface_ok_after_scroll"] = bool(det.get("is_following_list"))
    out["end_of_list_detected"] = bool(
        det.get("following_list_end_detected")
        or after_meta.get("following_list_end_detected")
    )
    if not det.get("is_following_list"):
        out["failure_reason"] = str(det.get("failure_reason") or "following_surface_lost_after_scroll")
        return out
    if continuity.continuity_proved:
        out["ok"] = True
        log("info", "unfollow_scroll_7_plus_1_overlap_verified", **safe_log_fields())
        return out
    if out["end_of_list_detected"]:
        # Suggestions are a safe boundary, never primary rows and never a
        # validated progressive scroll depth.
        out["ok"] = True
        out["safe_boundary"] = True
        log("info", "unfollow_scroll_7_plus_1_boundary_reached", **safe_log_fields())
        return out
    if continuity.excessive:
        out["corrective_backstep_used"] = True
        try:
            correction_distance = min(int(h * 0.18), int(geometry["distance_px"]))
            correction_start = int(h * 0.38)
            correction_end = min(int(h * 0.62), correction_start + correction_distance)
            d.swipe(x, correction_start, x, correction_end, 0.34)
            time.sleep(settle_s)
            corrected_rows, corrected_meta = harvest_visible_following_rows_for_unfollow(
                d,
                account_username=account_username,
            )
            corrected_ids = _visible_username_keys(
                [str(row.get("username_normalized") or row.get("username") or "") for row in corrected_rows]
            )
            corrected = compare_instagram_list_viewports(before_ids, corrected_ids)
            out.update(
                {
                    "rows_after": corrected_rows,
                    "harvest_meta_after": corrected_meta,
                    "viewport_fingerprint_after": corrected.fingerprint_after,
                    "actual_new_rows": corrected.new_row_count,
                    "actual_overlap": corrected.overlap_count,
                    "continuity_reason": corrected.reason,
                    "scroll_excessive": corrected.excessive,
                    "viewport_unchanged": corrected.unchanged,
                    "depth_advanced": corrected.continuity_proved,
                }
            )
            if corrected.continuity_proved:
                out["ok"] = True
                log("info", "unfollow_scroll_7_plus_1_overlap_recovered", **safe_log_fields())
                return out
        except Exception as exc:
            out["corrective_backstep_error"] = str(exc)[:200]
        out["failure_reason"] = "unfollow_scroll_overlap_not_recovered"
        log("warning", "unfollow_scroll_7_plus_1_overlap_failed", **safe_log_fields())
        return out
    # An unchanged viewport is valid stagnation evidence but not progressive
    # depth. It must not consume one of the 10+recovery+5 scrolls.
    if continuity.unchanged:
        out["ok"] = True
        log("info", "unfollow_scroll_7_plus_1_no_progress", **safe_log_fields())
        return out
    out["failure_reason"] = "unfollow_scroll_continuity_unproved"
    log("warning", "unfollow_scroll_7_plus_1_continuity_unproved", **safe_log_fields())
    return out


def _persist_unfollow_outcome_for_session(
    aid: str,
    target_username: str,
    *,
    run_id: str | None,
    settings: Any,
    verify_ok: bool,
    interaction_row_id: str | None,
    failure_reason: str,
) -> dict[str, Any]:
    mode = str(getattr(settings, "mode", "") or "")
    return supabase_client.record_unfollow_interaction_outcome(
        aid,
        target_username,
        run_id=run_id,
        unfollow_ok=verify_ok,
        unfollow_mode_applied=mode,
        interaction_row_id=interaction_row_id,
        failure_reason=failure_reason,
        allow_any_upsert=(mode == UNFOLLOW_MODE_ANY),
    )


def resolve_effective_unfollow_day_progress(
    *,
    day_limit: int,
    persisted_daily_count: int,
    quota_remaining_hint: int | None = None,
) -> dict[str, int | None]:
    """Resolve a fail-closed day counter from DB plus the canonical resume plan.

    The resume plan can be fresher than the mutable interaction projection while
    deferred persistence is draining.  It may only reduce the remaining budget;
    it can never grant more work than the direct persisted counter.
    """
    limit = max(0, int(day_limit or 0))
    persisted = max(0, int(persisted_daily_count or 0))
    hinted_remaining: int | None = None
    if quota_remaining_hint is not None:
        try:
            hinted_remaining = max(0, min(limit, int(quota_remaining_hint)))
        except (TypeError, ValueError):
            hinted_remaining = 0
    hinted_done = max(0, limit - hinted_remaining) if hinted_remaining is not None else 0
    effective_done = max(persisted, hinted_done)
    return {
        "day_limit": limit,
        "persisted_daily_count": persisted,
        "quota_remaining_hint": hinted_remaining,
        "hinted_done": hinted_done,
        "effective_done": effective_done,
        "remaining": max(0, limit - effective_done),
    }


def unfollow_quota_reached_after_persist(
    *,
    day_limit: int,
    effective_done_at_start: int,
    verified_persisted_in_run: int,
) -> bool:
    limit = max(0, int(day_limit or 0))
    return bool(
        limit > 0
        and max(0, int(effective_done_at_start or 0))
        + max(0, int(verified_persisted_in_run or 0))
        >= limit
    )


def unfollow_persistence_count_delta(
    *,
    verify_ok: bool,
    persist_ok: bool,
) -> dict[str, int]:
    """Separate persisted business actions from persisted audit outcomes.

    A failed UI verification is still written as an audit outcome, but it must
    never inflate the verified-and-persisted Unfollow counter used by the
    checkpoint, reconciliation, quota, or BotApp.
    """
    return {
        "verified_persisted": 1 if verify_ok and persist_ok else 0,
        "outcome_persisted": 1 if persist_ok else 0,
    }


def _log_unfollow_success_observed(
    *,
    account_id: str,
    username: str,
    run_id: str | None,
    persist_out: dict[str, Any],
    interaction_row_id: str | None = None,
) -> dict[str, Any]:
    observed = {
        "account_id": str(account_id or ""),
        "username": str(username or ""),
        "run_id": str(run_id or "") or None,
        "interaction_row_id": str(
            interaction_row_id or persist_out.get("interaction_row_id") or ""
        ).strip() or None,
        "unfollowed_at": persist_out.get("unfollowed_at"),
        "reason": "unfollow_verified_and_persisted",
    }
    log("info", "unfollow_success_observed", **observed)
    return observed


def _run_real_unfollow_multi_loop(
    d: u2.Device,
    *,
    aid: str,
    uname: str,
    run_id: str | None,
    settings: Any,
    base_summary: dict[str, Any],
    planned_usernames: set[str],
    planned_by_username: dict[str, dict[str, Any]],
    rows: list[dict[str, Any]],
    harvest_meta: dict[str, Any],
    harvest_fields: dict[str, Any],
    visible_eligibility_row_cache: dict[str, dict[str, Any] | None],
    real_action_max: int,
    unfollow_day_limit: int,
    effective_unfollows_done_at_start: int,
    business_action_deadline: str | None,
    adaptive_coverage_budget: Any,
    resume_checkpoint: dict[str, Any] | None,
    diagnostic_session: UnfollowDiagnosticSession | None,
    t0: float,
) -> int:
    verified = 0
    sent = 0
    failed = 0
    persisted = 0
    persisted_outcomes = 0
    scroll_passes_used = 0
    scroll_stop_reason = ""
    stop_reason = ""
    completed_usernames: set[str] = set()
    failed_usernames_this_run: set[str] = set()
    action_attempted_usernames: set[str] = set()
    action_verified_usernames: set[str] = set()
    action_persisted_usernames: set[str] = set()
    unfollow_observed_successes: list[dict[str, Any]] = []
    recoverable_action_failure_usernames: list[str] = []
    recoverable_action_failure_reasons: dict[str, str] = {}
    recoverable_action_failures_count = 0
    session_continued_after_recoverable_failure = False
    max_recoverable_action_failures = _max_recoverable_action_failures()
    recoverable_verify_failures_count = 0
    recoverable_verify_failure_usernames: list[str] = []
    recoverable_verify_failure_streak_class = ""
    recoverable_verify_failure_streak_count = 0
    any_mode_selected_count = 0
    last_fields = dict(harvest_fields)
    any_mode_active = str(getattr(settings, "mode", "") or "") == UNFOLLOW_MODE_ANY
    coverage_started_at = time.perf_counter()
    coverage_tracker = (
        None
        if any_mode_active
        else FollowingCoverageTracker(
            adaptive_coverage_budget,
            set(planned_usernames),
            real_action_max,
        )
    )
    if coverage_tracker is not None and isinstance(resume_checkpoint, dict):
        for prior_unavailable in list(resume_checkpoint.get("unavailable_usernames") or []):
            coverage_tracker.mark_candidate_unavailable(str(prior_unavailable or ""))
    totals: dict[str, Any] = {
        "visible_eligibility_total_db_queries": 0,
        "visible_eligibility_total_cache_hits": 0,
        "visible_eligibility_total_cache_misses": 0,
        "visible_eligibility_total_lookup_ms": 0.0,
        "visible_eligibility_total_eval_ms": 0.0,
        "any_mode_visible_candidates_count": 0,
        "any_mode_whitelist_skips_count": 0,
        "any_mode_rows_without_interaction_history_count": 0,
        "any_mode_selected_count": 0,
        "scroll_strategy_used": "v2_lite" if _scroll_v2_lite_enabled() else "legacy",
        "scroll_v2_lite_enabled": _scroll_v2_lite_enabled(),
        "scroll_avg_new_usernames_per_pass": 0.0,
        "scroll_avg_overlap_ratio": 0.0,
        "scroll_unchanged_streak_max": 0,
        "scroll_surface_failures_count": 0,
        "scroll_v2_lite_fallback_count": 0,
        "recoverable_action_failures_count": 0,
        "recoverable_action_failure_usernames": [],
        "recoverable_action_failure_reasons": {},
        "max_recoverable_action_failures": max_recoverable_action_failures,
        "session_continued_after_recoverable_failure": False,
        "recoverable_verify_failures_count": 0,
        "recoverable_verify_failure_usernames": [],
        "recoverable_verify_failure_streak_class": "",
        "recoverable_verify_failure_streak_count": 0,
        "adaptive_ui_coverage_enabled": not any_mode_active,
        "coverage_historical_action_sample_count": HISTORICAL_ACTION_SAMPLE_COUNT,
        "coverage_historical_viewport_sample_count": HISTORICAL_VIEWPORT_SAMPLE_COUNT,
    }
    max_scroll_passes = (
        int(adaptive_coverage_budget.max_scroll_passes)
        if coverage_tracker is not None
        else _scroll_max_passes()
    )
    stop_after_skipped_effective = _stop_after_skipped()
    max_minutes_effective = _max_minutes()
    exploration_started_at = time.perf_counter()
    seen_usernames: set[str] = set()
    skipped_usernames_by_reason: dict[str, set[str]] = {
        reason: set() for reason in sorted(_STRICT_EXPLORATION_SKIP_REASONS)
    }
    eligible_seen_usernames: set[str] = set()
    db_matched_usernames: set[str] = set()
    visible_plan_matched_usernames: set[str] = set()
    visible_plan_matches_current: list[str] = []
    visible_usernames_seen_total = 0
    duplicate_visible_usernames_count = 0
    scroll_progress_unique_new_count = 0
    scroll_new_usernames_total = 0
    scroll_overlap_ratio_total = 0.0
    scroll_progress_eval_count = 0
    unchanged_scroll_streak = 0
    unchanged_scroll_streak_max = 0
    scroll_surface_failures_count = 0
    scroll_v2_lite_fallback_count = 0
    direct_search_attempted: set[str] = set()
    direct_search_unavailable: set[str] = set()
    direct_search_ambiguous: set[str] = set()
    direct_search_verified_profiles = 0
    direct_search_session_reused_count = 0
    direct_search_retryable_failures = 0
    candidate_availability_persistence_failures = 0
    candidate_availability_persistence_failure_usernames: list[str] = []
    search_surface_health = SearchSurfaceCircuitBreaker()
    direct_fallback_armed = False

    def safe_diagnostic_call(method_name: str, **kwargs: Any) -> Any:
        if diagnostic_session is None:
            return None
        try:
            method = getattr(diagnostic_session, method_name)
            return method(**kwargs)
        except Exception as exc:
            log(
                "warning",
                "unfollow_diagnostic_v2_emit_failed",
                diagnostic_method=method_name,
                error=str(exc)[:500],
                behavior_affected=False,
            )
            return None

    def coverage_elapsed_seconds() -> float:
        return max(0.0, time.perf_counter() - coverage_started_at)

    def refresh_coverage_summary_totals() -> None:
        if coverage_tracker is None:
            return
        totals.update(coverage_tracker.summary())

    def recover_following_viewport(*, trigger_reason: str) -> tuple[bool, str]:
        if coverage_tracker is None:
            return False, "ui_recovery_budget_exhausted"
        if (
            coverage_tracker.viewport_recoveries_used
            >= coverage_tracker.budget.max_viewport_recoveries
        ):
            decision = coverage_tracker.mark_recovery(succeeded=False)
            refresh_coverage_summary_totals()
            return False, str(decision.stop_reason if decision else "ui_recovery_budget_exhausted")
        returned = return_to_following_list_after_unfollow_action(
            d,
            account_username=uname,
        )
        recovery_method = "bounded_back"
        recovery_ok = bool(returned.get("ok"))
        if not recovery_ok:
            recovery_ok, _open_meta = open_own_following_list_from_own_profile(d, uname)
            recovery_method = "own_profile_following_reopen"
        decision = coverage_tracker.mark_recovery(succeeded=recovery_ok)
        refresh_coverage_summary_totals()
        log(
            "info",
            "unfollow_viewport_recovery_completed",
            trigger_reason=trigger_reason,
            recovery_method=recovery_method,
            recovery_ok=recovery_ok,
            viewport_recoveries_used=coverage_tracker.viewport_recoveries_used,
            max_viewport_recoveries=coverage_tracker.budget.max_viewport_recoveries,
            stop_reason=str(decision.stop_reason if decision else ""),
        )
        return recovery_ok and decision is None, str(decision.stop_reason if decision else "")

    def recover_exact_following_after_ambiguous_action(
        *,
        trigger_reason: str,
    ) -> dict[str, Any]:
        """Perform one bounded recovery and prove the exact safe boundary."""

        returned = return_to_following_list_after_unfollow_action(
            d,
            account_username=uname,
        )
        recovery_method = "bounded_back"

        try:
            current_app = dict(d.app_current() or {})
            current_package = str(current_app.get("package") or "")
            current_activity = str(current_app.get("activity") or "")
            package_activity_ok = bool(
                current_package == str(config.INSTAGRAM_PACKAGE or "")
                and current_activity.endswith(".InstagramMainActivity")
            )
        except Exception as exc:
            current_package = ""
            current_activity = ""
            package_activity_ok = False
            returned = {
                **dict(returned or {}),
                "app_current_error": str(exc)[:300],
            }

        following_det = detect_own_following_list_screen(
            d,
            account_username=uname,
        )
        unsafe_markers = _merge_unfollow_surface_unsafe_markers(
            _detect_unfollow_unsafe_markers(d),
            following_det,
        )
        account_identity_ok = str(following_det.get("failure_reason") or "") != (
            "following_list_account_title_mismatch"
        )
        exact_following_list_restored = bool(
            returned.get("ok")
            and following_det.get("is_following_list")
            and account_identity_ok
        )

        coverage_stop_reason = ""
        if coverage_tracker is not None:
            decision = coverage_tracker.mark_recovery(
                succeeded=bool(
                    exact_following_list_restored
                    and package_activity_ok
                    and not unsafe_markers
                ),
                progress_proved=True,
            )
            refresh_coverage_summary_totals()
            coverage_stop_reason = str(decision.stop_reason if decision else "")
            if decision is not None:
                exact_following_list_restored = False

        out = {
            "ok": bool(
                exact_following_list_restored
                and package_activity_ok
                and not unsafe_markers
                and not coverage_stop_reason
            ),
            "recovery_method": recovery_method,
            "recovery_max_attempts": 1,
            "return_to_following_list_ok": bool(returned.get("ok")),
            "exact_following_list_restored": exact_following_list_restored,
            "package_activity_ok": package_activity_ok,
            "account_identity_ok": account_identity_ok,
            "current_package": current_package,
            "current_activity": current_activity,
            "following_list_reason": str(following_det.get("detected_reason") or ""),
            "following_list_failure_reason": str(following_det.get("failure_reason") or ""),
            "unsafe_markers": unsafe_markers,
            "coverage_stop_reason": coverage_stop_reason,
            "failure_reason": str(
                coverage_stop_reason
                or returned.get("failure_reason")
                or following_det.get("failure_reason")
                or ("package_activity_mismatch" if not package_activity_ok else "")
                or ("unsafe_marker_detected" if unsafe_markers else "")
            ),
        }
        log(
            "info" if out["ok"] else "error",
            "unfollow_ambiguous_action_exact_following_recovery_completed",
            trigger_reason=trigger_reason,
            **out,
        )
        return out

    def exploration_fields(*, exploration_stop_reason: str = "") -> dict[str, Any]:
        skip_reason_counts_total = {
            reason: len(usernames)
            for reason, usernames in sorted(skipped_usernames_by_reason.items())
            if usernames
        }
        skipped_union: set[str] = set()
        for usernames in skipped_usernames_by_reason.values():
            skipped_union.update(usernames)
        unique_seen = len(seen_usernames)
        unique_eligible = len(eligible_seen_usernames)
        return {
            "unique_usernames_seen_count": unique_seen,
            "unique_skipped_usernames_count": len(skipped_union),
            "skip_reason_counts_total": skip_reason_counts_total,
            "unique_eligible_seen_count": unique_eligible,
            "eligible_hit_rate": round(unique_eligible / unique_seen, 4) if unique_seen else 0.0,
            "db_match_rate": round(len(db_matched_usernames) / unique_seen, 4) if unique_seen else 0.0,
            "visible_usernames_seen_total": visible_usernames_seen_total,
            "duplicate_visible_usernames_count": duplicate_visible_usernames_count,
            "scroll_progress_unique_new_count": scroll_progress_unique_new_count,
            "exploration_stop_reason": exploration_stop_reason,
            "stop_after_skipped_effective": stop_after_skipped_effective,
            "max_minutes_effective": max_minutes_effective,
            "planned_usernames_count": len(planned_usernames),
            "visible_plan_matches_count_current": len(visible_plan_matches_current),
            "visible_plan_matches_usernames_current": visible_plan_matches_current[:50],
            "visible_plan_matches_count_total_unique": len(visible_plan_matched_usernames),
            "visible_plan_match_rate": (
                round(len(visible_plan_matched_usernames) / len(planned_usernames), 4)
                if planned_usernames
                else 0.0
            ),
            "plan_guided_mode": "enforced_allowlist",
        }

    def refresh_recoverable_action_summary_totals() -> None:
        totals["recoverable_action_failures_count"] = recoverable_action_failures_count
        totals["recoverable_action_failure_usernames"] = recoverable_action_failure_usernames[:50]
        totals["recoverable_action_failure_reasons"] = dict(recoverable_action_failure_reasons)
        totals["max_recoverable_action_failures"] = max_recoverable_action_failures
        totals["session_continued_after_recoverable_failure"] = session_continued_after_recoverable_failure
        totals["recoverable_verify_failures_count"] = recoverable_verify_failures_count
        totals["recoverable_verify_failure_usernames"] = recoverable_verify_failure_usernames[:50]
        totals["recoverable_verify_failure_streak_class"] = recoverable_verify_failure_streak_class
        totals["recoverable_verify_failure_streak_count"] = recoverable_verify_failure_streak_count

    def is_recoverable_action_sheet_failure(sheet_out: dict[str, Any]) -> bool:
        reason = str(sheet_out.get("failure_reason") or "").strip()
        retry_reason = str(sheet_out.get("retry_failure_reason") or "").strip()
        recoverable_reasons = {
            "actions_sheet_signals_missing",
            "actions_sheet_signals_missing_after_retry",
            "sheet_not_opened",
            "actions_sheet_not_opened",
            "following_button_not_found",
            "following_cta_surface_not_stable",
            "following_cta_terminally_absent",
            "following_button_pre_tap_revalidation_failed",
            "following_button_bounds_shift_too_large",
            "unfollow_option_missing",
        }
        return bool(
            not bool(sheet_out.get("ok"))
            and not bool(sheet_out.get("unfollow_option_visible"))
            and (reason in recoverable_reasons or retry_reason in recoverable_reasons)
        )

    def unique_skipped_usernames_count() -> int:
        skipped_union: set[str] = set()
        for usernames in skipped_usernames_by_reason.values():
            skipped_union.update(usernames)
        return len(skipped_union)

    def max_minutes_reached() -> bool:
        if max_minutes_effective <= 0:
            return False
        elapsed_minutes = (time.perf_counter() - exploration_started_at) / 60.0
        return elapsed_minutes >= float(max_minutes_effective)

    def refresh_scroll_summary_totals() -> None:
        totals["scroll_avg_new_usernames_per_pass"] = (
            round(scroll_new_usernames_total / scroll_progress_eval_count, 4)
            if scroll_progress_eval_count
            else 0.0
        )
        totals["scroll_avg_overlap_ratio"] = (
            round(scroll_overlap_ratio_total / scroll_progress_eval_count, 4)
            if scroll_progress_eval_count
            else 0.0
        )
        totals["scroll_unchanged_streak_max"] = unchanged_scroll_streak_max
        totals["scroll_surface_failures_count"] = scroll_surface_failures_count
        totals["scroll_v2_lite_fallback_count"] = scroll_v2_lite_fallback_count

    def record_exploration_viewport(
        visible_usernames: list[str],
        visible_eval: dict[str, Any],
    ) -> dict[str, Any]:
        nonlocal visible_usernames_seen_total
        nonlocal duplicate_visible_usernames_count
        nonlocal scroll_progress_unique_new_count
        nonlocal visible_plan_matches_current

        new_keys: set[str] = set()
        current_plan_matches: list[str] = []
        current_plan_match_keys: set[str] = set()
        scroll_progress_unique_new_count = 0
        for idx, raw in enumerate(visible_usernames):
            visible_usernames_seen_total += 1
            key = normalize_unfollow_username(str(raw or ""))
            if not key:
                skipped_usernames_by_reason.setdefault("invalid_username", set()).add(
                    f"invalid:{visible_usernames_seen_total}:{idx}"
                )
                continue
            if key in planned_usernames and key not in current_plan_match_keys:
                current_plan_match_keys.add(key)
                current_plan_matches.append(str(raw or key))
            if key in seen_usernames:
                duplicate_visible_usernames_count += 1
                continue
            seen_usernames.add(key)
            new_keys.add(key)
            scroll_progress_unique_new_count += 1
            if key in planned_usernames:
                visible_plan_matched_usernames.add(key)

        visible_plan_matches_current = current_plan_matches[:50]

        for cand in list(visible_eval.get("visible_eligible_matches") or []):
            if not isinstance(cand, dict):
                continue
            key = normalize_unfollow_username(
                str(cand.get("username_normalized") or cand.get("username") or "")
            )
            if key and key in new_keys and key not in completed_usernames:
                eligible_seen_usernames.add(key)
                db_matched_usernames.add(key)

        for row in list(visible_eval.get("visible_ineligible_rows") or []):
            if not isinstance(row, dict):
                continue
            key = normalize_unfollow_username(
                str(row.get("username_normalized") or row.get("username") or "")
            )
            if not key or key not in new_keys:
                continue
            if key in completed_usernames or key in failed_usernames_this_run:
                skipped_usernames_by_reason.setdefault("already_completed_in_run", set()).add(key)
                continue
            reason = str(row.get("skip_reason") or "").strip() or "unknown"
            if reason not in _STRICT_EXPLORATION_SKIP_REASONS:
                skipped_usernames_by_reason.setdefault(reason, set())
            skipped_usernames_by_reason[reason].add(key)
            if str(row.get("interaction_row_id") or "").strip():
                db_matched_usernames.add(key)

        return exploration_fields()

    def emit_final(status: str, failure_reason: str = "") -> int:
        event_name = (
            "unfollow_multi_action_loop_stopped"
            if status.startswith("failed_")
            else "unfollow_multi_action_loop_completed"
        )
        exploration_stop = stop_reason or status
        exploration_summary = exploration_fields(exploration_stop_reason=exploration_stop)
        refresh_scroll_summary_totals()
        refresh_recoverable_action_summary_totals()
        refresh_coverage_summary_totals()
        if coverage_tracker is None:
            totals["lightweight_deadline_checks"] = any_mode_deadline_checks
            totals["per_scroll_full_recalculations"] = 0
        log(
            "info",
            event_name,
            status=status,
            stop_reason=exploration_stop,
            failure_reason=failure_reason,
            unfollow_actions_verified_so_far=verified,
            real_action_max_per_run=real_action_max,
            scroll_passes_used=scroll_passes_used,
            scroll_stop_reason=scroll_stop_reason,
        )
        log(
            "info",
            "unfollow_exploration_v2_completed",
            scroll_passes_used=scroll_passes_used,
            **exploration_summary,
        )
        remaining_planned_count = (
            len(coverage_tracker.remaining_planned_usernames)
            if coverage_tracker is not None
            else len(planned_usernames - completed_usernames)
        )
        safe_diagnostic_call(
            "emit_terminal",
            status=status,
            stop_reason=exploration_stop,
            row_cache=visible_eligibility_row_cache,
            plan_remaining=remaining_planned_count,
            attempted_usernames=set(action_attempted_usernames),
            verified_usernames=set(action_verified_usernames),
            persisted_usernames=set(action_persisted_usernames),
            failed_count=failed,
            filtered_count=unique_skipped_usernames_count(),
            end_of_list_status=str(
                last_fields.get("following_list_end_reason")
                or last_fields.get("scroll_stop_reason")
                or ""
            ),
        )
        stable_reason = str(failure_reason or stop_reason or "").strip()
        if candidate_availability_persistence_failures > 0:
            stable_reason = "unfollow_candidate_availability_persistence_failed"
        if not stable_reason:
            stable_reason = {
                "success_real_unfollow_multi_limit_reached": "unfollow_quota_reached",
                "no_quota": "unfollow_quota_reached",
                "no_more_following_rows": "no_more_following_rows",
                "success_unfollow_skipped_insufficient_time": "session_time_budget_exhausted",
            }.get(status, status)
        canonical_outcome = build_unfollow_outcome(
            stable_reason=stable_reason,
            raw_candidate_count=int(base_summary.get("source_rows_loaded") or 0),
            eligible_candidate_count=int(base_summary.get("eligible_total") or 0),
            planned_candidate_count=len(planned_usernames),
            attempted_count=sent,
            verified_count=verified,
            persisted_count=persisted,
            tracker=coverage_tracker,
            unplanned_eligible_count=int(
                base_summary.get("unplanned_eligible_count") or 0
            ),
        )
        global_remaining_count = int(canonical_outcome.get("remaining_count") or 0)
        summary = {
            **base_summary,
            **last_fields,
            **totals,
            **exploration_summary,
            "multi_action_mode": True,
            "real_action_max_per_run": real_action_max,
            "unfollow_day_quota": max(0, int(unfollow_day_limit)),
            "unfollows_done_at_start": max(0, int(effective_unfollows_done_at_start)),
            "effective_unfollows_done": max(
                0,
                int(effective_unfollows_done_at_start) + int(verified),
            ),
            "unfollow_actions_sent": sent,
            "unfollow_actions_verified": verified,
            "unfollow_actions_failed": failed,
            "unfollow_results_persisted_count": persisted,
            "unfollow_outcomes_persisted_count": persisted_outcomes,
            "attempted": sent,
            "verified": verified,
            "persisted": persisted,
            "unfollow_observed_success_count": len(unfollow_observed_successes),
            "unfollow_observed_success_usernames": [
                str(item.get("username") or "")
                for item in unfollow_observed_successes[:50]
                if str(item.get("username") or "").strip()
            ],
            "unfollow_observed_successes": unfollow_observed_successes[:50],
            "scroll_passes_used": scroll_passes_used,
            "scroll_stop_reason": scroll_stop_reason,
            "multi_action_stop_reason": exploration_stop,
            "last_run_attempted": sent,
            "last_run_verified": verified,
            "last_run_remaining_eligible": global_remaining_count,
            "eligible_db_remaining": global_remaining_count,
            "planned_eligible_remaining": remaining_planned_count,
            "ui_coverage_status": (
                "partial"
                if global_remaining_count > 0
                else "complete"
            ),
            "phase_duration_seconds": round(coverage_elapsed_seconds(), 3),
            "hybrid_unfollow_strategy": "progressive_cursor_direct_v1",
            "direct_search_attempted_count": len(direct_search_attempted),
            "direct_search_verified_profiles": direct_search_verified_profiles,
            "direct_search_session_reused_count": direct_search_session_reused_count,
            "direct_search_retryable_failures": direct_search_retryable_failures,
            "direct_search_unavailable_count": len(direct_search_unavailable),
            "direct_search_ambiguous_count": len(direct_search_ambiguous),
            "candidate_availability_persistence_failures": candidate_availability_persistence_failures,
            "candidate_availability_persistence_failure_usernames": candidate_availability_persistence_failure_usernames[:50],
            **search_surface_health.as_dict(),
            "cleanup_reserve_seconds": SCHEDULED_SESSION_CLEANUP_RESERVE_SECONDS,
            "stop_reason": exploration_stop,
            "unfollow_outcome": canonical_outcome,
            "unfollow_checkpoint": canonical_outcome.get("checkpoint"),
            "resume_recommended": bool(canonical_outcome.get("resume_recommended")),
            "resume_strategy": canonical_outcome.get("resume_strategy"),
            "status": status,
            "failure_reason": failure_reason,
            "total_ms": round((time.perf_counter() - t0) * 1000.0, 2),
        }
        log(
            "info",
            "unfollow_candidate_funnel",
            account_id=aid,
            run_id=run_id,
            source_total=int(base_summary.get("source_rows_loaded") or 0),
            eligible_total=int(base_summary.get("eligible_total") or 0),
            planned_total=len(planned_usernames),
            unplanned_eligible_total=int(
                base_summary.get("unplanned_eligible_count") or 0
            ),
            selected_total=sent,
            attempted_total=sent,
            verified_total=verified,
            persisted_total=persisted,
            persisted_outcomes_total=persisted_outcomes,
            failed_outcomes_persisted_total=max(
                0,
                persisted_outcomes - persisted,
            ),
            remaining_eligible=global_remaining_count,
            planned_remaining=remaining_planned_count,
            query_limit=int(base_summary.get("query_limit") or 0),
            page_size=int(base_summary.get("page_size") or 0),
            pages_loaded=int(base_summary.get("pages_loaded") or 0),
            page_requests=int(base_summary.get("page_requests") or 0),
            pagination_used=bool(base_summary.get("pagination_used")),
            pagination_strategy=str(base_summary.get("pagination_strategy") or ""),
            candidate_scan_exhaustive=bool(
                base_summary.get("candidate_scan_exhaustive")
            ),
            candidate_funnel_reconciled=bool(
                base_summary.get("candidate_funnel_reconciled")
            ),
            candidate_skip_reason_counts=dict(
                base_summary.get("candidate_skip_counts") or {}
            ),
            ui_skip_reason_counts=dict(
                exploration_summary.get("skip_reason_counts_total") or {}
            ),
            stop_reason=exploration_stop,
        )
        log(
            "info",
            "unfollow_run_reconciliation",
            account_id=aid,
            run_id=run_id,
            worker_verified=verified,
            persisted_actions=persisted,
            persisted_outcomes=persisted_outcomes,
            summary_actions=int(summary.get("unfollow_results_persisted_count") or 0),
            botapp_displayed_actions=None,
            reconciliation_ok=(verified == persisted),
            discrepancy_reason="" if verified == persisted else "verified_persisted_mismatch",
        )
        _emit_summary(summary)
        return 0 if not status.startswith("failed_") else 1

    log(
        "info",
        "unfollow_multi_action_loop_started",
        account_id=aid,
        account_username=uname,
        real_action_max_per_run=real_action_max,
        scroll_max_passes=max_scroll_passes,
        adaptive_coverage_budget=adaptive_coverage_budget.as_dict(),
    )
    log(
        "info",
        "unfollow_exploration_v2_started",
        scroll_passes_used=scroll_passes_used,
        **exploration_fields(),
    )

    iteration_index = 0
    any_mode_deadline_checks = 0
    any_mode_last_deadline_check = -float("inf")
    while verified < real_action_max:
        target_opened_directly = False
        if coverage_tracker is not None:
            coverage_preflight = coverage_tracker.preflight_decision(
                elapsed_seconds=coverage_elapsed_seconds()
            )
            if coverage_preflight is not None:
                stop_reason = coverage_preflight.stop_reason
                return emit_final("success_real_unfollow_multi_partial_exhausted")
        if coverage_tracker is None:
            elapsed = coverage_elapsed_seconds()
            check_due = (
                any_mode_deadline_checks == 0
                or elapsed - any_mode_last_deadline_check
                >= adaptive_coverage_budget.lightweight_deadline_check_interval_seconds
                or verified > 0
                and verified % adaptive_coverage_budget.lightweight_deadline_check_action_interval == 0
            )
            if elapsed >= adaptive_coverage_budget.max_unfollow_phase_duration_seconds:
                stop_reason = "session_time_budget_exhausted"
                any_mode_deadline_checks += 1
                return emit_final("success_unfollow_skipped_insufficient_time")
            if check_due:
                any_mode_deadline_checks += 1
                any_mode_last_deadline_check = elapsed
        iteration_index += 1
        log(
            "info",
            "unfollow_multi_action_iteration_started",
            iteration_index=iteration_index,
            unfollow_actions_verified_so_far=verified,
            real_action_max_per_run=real_action_max,
            scroll_passes_used=scroll_passes_used,
        )

        while True:
            selection_t0 = time.perf_counter()
            if coverage_tracker is not None:
                hybrid = choose_hybrid_selection(
                    coverage_tracker.remaining_planned_usernames,
                    scan_exhausted=direct_fallback_armed,
                    already_direct_searched=direct_search_attempted,
                )
                fallback_batch_exhausted = bool(
                    direct_fallback_armed
                    and len(direct_search_attempted) >= DIRECT_SEARCH_FALLBACK_BATCH_LIMIT
                )
                if fallback_batch_exhausted:
                    stop_reason = "direct_fallback_batch_limit_reached"
                    return emit_final(
                        "success_real_unfollow_multi_partial_exhausted",
                        stop_reason,
                    )
                if hybrid.mode == "direct_exact" and not fallback_batch_exhausted:
                    target_username = str(hybrid.usernames[0])
                    target_key = normalize_unfollow_username(target_username)
                    direct_search_attempted.add(target_key)
                    direct_result = open_exact_profile_for_unfollow(d, target_username)
                    log(
                        "info",
                        "unfollow_direct_exact_search_completed",
                        username=target_username,
                        result_status=str(direct_result.get("status") or ""),
                        reason=str(direct_result.get("reason") or ""),
                        exact_match_count=int(direct_result.get("exact_match_count") or 0),
                        remaining_count=len(coverage_tracker.remaining_planned_usernames),
                        selection_reason=hybrid.reason,
                        local_retry_count=int(direct_result.get("local_retry_count") or 0),
                    )
                    if bool(direct_result.get("ok")):
                        search_surface_health.record("exact_result_visible")
                        direct_search_verified_profiles += 1
                        target_row = {
                            "username": target_username,
                            "username_normalized": target_key,
                            "row_index": -1,
                            "direct_exact_search": True,
                        }
                        visible_candidates = {
                            target_key: planned_by_username.get(target_key) or {}
                        }
                        selection_reason = hybrid.reason
                        target_opened_directly = True
                        break
                    direct_status = str(
                        direct_result.get("status") or "search_surface_unhealthy"
                    )
                    stable_failure_reason = str(
                        direct_result.get("reason") or "search_surface_unhealthy"
                    )
                    classification = (
                        "username_not_found_confirmed"
                        if direct_status == "username_not_found_confirmed"
                        else "search_surface_unhealthy"
                    )
                    availability_state: dict[str, Any] = {}
                    try:
                        availability_state = (
                            supabase_client.record_unfollow_candidate_availability_v2(
                                aid,
                                target_key,
                                source_run_id=run_id,
                                classification=classification,
                                reason=stable_failure_reason,
                                technical_cooldown_minutes=30,
                            )
                        )
                    except Exception as exc:
                        candidate_availability_persistence_failures += 1
                        if target_key not in candidate_availability_persistence_failure_usernames:
                            candidate_availability_persistence_failure_usernames.append(target_key)
                        log(
                            "error",
                            "unfollow_candidate_availability_v2_persist_failed",
                            account_id=aid,
                            run_id=run_id,
                            username=target_username,
                            classification=classification,
                            reason="unfollow_candidate_availability_persistence_failed",
                            error=str(exc)[:500],
                        )
                        stop_reason = (
                            "unfollow_candidate_availability_persistence_failed"
                        )
                        return emit_final("failed_unfollow_multi_action", stop_reason)
                    if classification == "username_not_found_confirmed":
                        search_surface_health.record(classification)
                        coverage_tracker.mark_candidate_unavailable(target_key)
                        direct_search_unavailable.add(target_key)
                        log(
                            "info",
                            "unfollow_candidate_not_found_terminalized",
                            account_id=aid,
                            run_id=run_id,
                            username=target_username,
                            reason=stable_failure_reason,
                            local_retry_count=int(
                                direct_result.get("local_retry_count") or 0
                            ),
                            persisted=bool(availability_state.get("ok")),
                            availability_status=str(
                                availability_state.get("status") or ""
                            ),
                            terminal_at=availability_state.get("terminal_at"),
                            backlog_actionable=False,
                            unfollow_marked_success=False,
                        )
                        continue

                    direct_search_retryable_failures += 1
                    if direct_status == "ambiguous":
                        direct_search_ambiguous.add(target_key)
                    coverage_tracker.mark_candidate_technical_hold(target_key)
                    breaker_opened = search_surface_health.record(classification)
                    log(
                        "warning",
                        "unfollow_direct_exact_technical_candidate_held",
                        username=target_username,
                        reason=stable_failure_reason,
                        retryable_failure_count=direct_search_retryable_failures,
                        consecutive_technical_failures=(
                            search_surface_health.consecutive_technical_failures
                        ),
                        circuit_breaker_open=breaker_opened,
                        continue_to_next_candidate=not breaker_opened,
                        availability_status=str(
                            availability_state.get("status") or ""
                        ),
                        next_retry_at=availability_state.get("next_retry_at"),
                    )
                    if breaker_opened:
                        stop_reason = search_surface_health.stable_reason
                        try:
                            supabase_client.record_unfollow_phase_circuit_breaker_v1(
                                aid,
                                source_run_id=run_id,
                                stable_reason=stop_reason,
                                technical_failure_count=(
                                    search_surface_health.total_technical_failures
                                ),
                                usernames=sorted(direct_search_attempted),
                                cooldown_minutes=30,
                            )
                        except Exception as exc:
                            log(
                                "error",
                                "unfollow_phase_circuit_breaker_persist_failed",
                                account_id=aid,
                                run_id=run_id,
                                reason=(
                                    "unfollow_phase_circuit_breaker_persistence_failed"
                                ),
                                error=str(exc)[:500],
                            )
                            stop_reason = (
                                "unfollow_phase_circuit_breaker_persistence_failed"
                            )
                            return emit_final(
                                "failed_unfollow_multi_action",
                                stop_reason,
                            )
                        return emit_final(
                            "success_real_unfollow_multi_partial_exhausted",
                            stop_reason,
                        )
                    continue
                if hybrid.mode == "complete":
                    stop_reason = "eligible_targets_exhausted"
                    return emit_final("success_real_unfollow_multi_partial_exhausted")
                if hybrid.mode == "partial_resumable":
                    stop_reason = hybrid.reason
                    return emit_final(
                        "success_real_unfollow_multi_partial_exhausted",
                        stop_reason,
                    )
            if any_mode_active:
                visible_eval = _evaluate_visible_unfollow_any_with_session_cache(
                    aid,
                    rows,
                    account_username=uname,
                    row_cache=visible_eligibility_row_cache,
                    completed_usernames=completed_usernames,
                )
                _aggregate_visible_perf_totals(totals, visible_eval)
                _aggregate_visible_any_perf_totals(totals, visible_eval)
                visible_candidates = _visible_candidates_by_username(visible_eval)
                eval_fields = {
                    **_harvest_summary_fields(rows, harvest_meta, planned_usernames),
                    **_visible_any_summary_fields(visible_eval),
                }
                eval_fields.update(
                    {
                        "visible_eligible_matches_count": len(visible_candidates),
                        "visible_eligible_matches_usernames": [
                            str(c.get("username") or "")
                            for c in visible_candidates.values()
                        ][:50],
                    }
                )
                target_row, selection_reason = _select_visible_any_target_row(
                    rows,
                    visible_candidates,
                    completed_usernames=completed_usernames,
                )
            else:
                visible_usernames = [str(row.get("username") or "") for row in rows]
                following_det = detect_own_following_list_screen(
                    d,
                    account_username=uname,
                )
                unsafe_markers = _merge_unfollow_surface_unsafe_markers(
                    _detect_unfollow_unsafe_markers(d),
                    following_det,
                )
                coverage_decision = coverage_tracker.observe_viewport(
                    visible_usernames,
                    elapsed_seconds=coverage_elapsed_seconds(),
                    following_confirmed=bool(following_det.get("is_following_list")),
                    unsafe_marker=bool(unsafe_markers),
                    end_of_list=bool(harvest_meta.get("following_list_end_detected")),
                    surface_signature=str(
                        following_det.get("detected_reason")
                        or following_det.get("failure_reason")
                        or "following_unknown"
                    ),
                )
                log(
                    "info",
                    "unfollow_ui_coverage_viewport_decision",
                    decision=coverage_decision.action,
                    viewport_fingerprint=coverage_decision.fingerprint,
                    new_usernames_count=coverage_decision.new_usernames_count,
                    following_confirmed=bool(following_det.get("is_following_list")),
                    following_failure_reason=str(following_det.get("failure_reason") or ""),
                    unsafe_markers=unsafe_markers,
                    **coverage_tracker.summary(),
                )
                if coverage_decision.action == "recover":
                    recovered, recovery_stop = recover_following_viewport(
                        trigger_reason=str(
                            coverage_decision.stop_reason
                            or following_det.get("failure_reason")
                            or "following_state_unconfirmed"
                        )
                    )
                    if not recovered:
                        stop_reason = recovery_stop or "ui_recovery_budget_exhausted"
                        return emit_final("failed_unfollow_multi_action", stop_reason)
                    rows, harvest_meta = harvest_visible_following_rows_for_unfollow(
                        d,
                        account_username=uname,
                    )
                    continue
                if coverage_decision.action == "stop":
                    stop_reason = coverage_decision.stop_reason
                    if (
                        can_arm_direct_search_fallback(
                            stop_reason,
                            remaining_count=len(
                                coverage_tracker.remaining_planned_usernames
                            ),
                        )
                        and not direct_fallback_armed
                    ):
                        direct_fallback_armed = True
                        log(
                            "info",
                            "unfollow_direct_fallback_armed",
                            reason=stop_reason,
                            remaining_count=len(coverage_tracker.remaining_planned_usernames),
                            direct_batch_limit=DIRECT_SEARCH_FALLBACK_BATCH_LIMIT,
                        )
                        continue
                    status = (
                        "failed_unfollow_multi_action"
                        if stop_reason == "unsafe_marker_detected"
                        else "success_real_unfollow_multi_partial_exhausted"
                    )
                    return emit_final(status, stop_reason if status.startswith("failed_") else "")
                visible_eval = _evaluate_visible_unfollow_with_session_cache(
                    aid,
                    visible_usernames,
                    settings=settings,
                    row_cache=visible_eligibility_row_cache,
                )
                _aggregate_visible_perf_totals(totals, visible_eval)
                exploration_viewport_fields = record_exploration_viewport(
                    visible_usernames,
                    visible_eval,
                )
                visible_candidates = _visible_candidates_by_username(visible_eval)
                for visible_key in list(visible_candidates):
                    if visible_key not in planned_usernames:
                        visible_candidates.pop(visible_key, None)
                for done in completed_usernames:
                    visible_candidates.pop(done, None)
                for failed_key in failed_usernames_this_run:
                    visible_candidates.pop(failed_key, None)
                eval_fields = {
                    **_harvest_summary_fields(rows, harvest_meta, planned_usernames),
                    **_visible_eligibility_summary_fields(visible_eval),
                }
                eval_fields.update(
                    {
                        "visible_eligible_matches_count": len(visible_candidates),
                        "visible_eligible_matches_usernames": [
                            str(c.get("username") or "")
                            for c in visible_candidates.values()
                        ][:50],
                    }
                )
                target_row, selection_reason = _select_visible_eligible_target_row(
                    rows,
                    visible_candidates,
                )
                log(
                    "info",
                    "unfollow_exploration_v2_viewport_evaluated",
                    scroll_passes_used=scroll_passes_used,
                    **exploration_viewport_fields,
                )
            eval_fields["visible_target_selection_ms"] = round(
                (time.perf_counter() - selection_t0) * 1000.0,
                2,
            )
            diagnostic_viewport, diagnostic_candidates = build_unfollow_diagnostic_v2(
                rows=rows,
                visible_eval=visible_eval,
                planned_usernames=planned_usernames,
                row_cache=visible_eligibility_row_cache,
                attempted_usernames=action_attempted_usernames,
                verified_usernames=action_verified_usernames,
                persisted_usernames=action_persisted_usernames,
                viewport_index=iteration_index,
                scroll_depth=scroll_passes_used,
            )
            log(
                "info",
                "unfollow_diagnostic_v2_viewport",
                account_id=aid,
                run_id=run_id,
                **diagnostic_viewport,
            )
            for diagnostic_candidate in diagnostic_candidates:
                log(
                    "info",
                    "unfollow_candidate_lineage_v1",
                    account_id=aid,
                    run_id=run_id,
                    lineage_stage="viewport_observed",
                    **diagnostic_candidate,
                )
            diagnostic_plan_remaining = (
                len(coverage_tracker.remaining_planned_usernames)
                if coverage_tracker is not None
                else len(planned_usernames - completed_usernames)
            )
            safe_diagnostic_call(
                "observe_viewport",
                rows=rows,
                visible_eval=visible_eval,
                row_cache=visible_eligibility_row_cache,
                scroll_depth=scroll_passes_used,
                plan_remaining=diagnostic_plan_remaining,
                verified_count=verified,
                attempted_usernames=set(action_attempted_usernames),
                verified_usernames=set(action_verified_usernames),
                persisted_usernames=set(action_persisted_usernames),
                harvest_meta=dict(harvest_meta or {}),
                safe_stop_reason=stop_reason,
                viewport_fingerprint_fn=viewport_fingerprint,
            )
            last_fields = {**last_fields, **eval_fields}
            if target_row is not None:
                break

            log(
                "info",
                "unfollow_multi_action_target_exhausted_in_viewport",
                iteration_index=iteration_index,
                unfollow_actions_verified_so_far=verified,
                real_action_max_per_run=real_action_max,
                visible_eligible_matches_count=0,
                scroll_passes_used=scroll_passes_used,
            )
            if bool(harvest_meta.get("following_list_end_detected")):
                scroll_stop_reason = "following_list_end_reached"
                stop_reason = "following_list_end_reached"
                end_fields = {
                    "following_list_end_detected": True,
                    "suggested_for_you_visible": bool(harvest_meta.get("suggested_for_you_visible")),
                    "suggestion_follow_buttons_count": int(harvest_meta.get("suggestion_follow_buttons_count") or 0),
                    "following_list_end_reason": str(
                        harvest_meta.get("following_list_end_reason")
                        or "suggested_for_you_section_visible"
                    ),
                }
                last_fields = {**last_fields, **end_fields}
                log(
                    "info",
                    "unfollow_following_list_end_detected",
                    scroll_passes_used=scroll_passes_used,
                    visible_rows_count=len(rows),
                    **end_fields,
                )
                log(
                    "info",
                    "unfollow_exploration_v2_end_of_list_reached",
                    scroll_passes_used=scroll_passes_used,
                    **exploration_fields(exploration_stop_reason=stop_reason),
                    **end_fields,
                )
                status = (
                    "success_real_unfollow_multi_partial_end_of_list"
                    if verified > 0
                    else "no_more_following_rows"
                )
                return emit_final(status)

            if (
                coverage_tracker is None
                and
                stop_after_skipped_effective > 0
                and unique_skipped_usernames_count() >= stop_after_skipped_effective
            ):
                scroll_stop_reason = "stop_after_skipped_reached"
                stop_reason = "stop_after_skipped_reached"
                log(
                    "info",
                    "unfollow_exploration_v2_stop_after_skipped_reached",
                    scroll_passes_used=scroll_passes_used,
                    **exploration_fields(exploration_stop_reason=stop_reason),
                )
                status = (
                    "success_real_unfollow_multi_partial_exhausted"
                    if verified > 0
                    else "no_visible_eligible_unfollow_target"
                )
                return emit_final(status)

            if coverage_tracker is None and max_minutes_reached():
                scroll_stop_reason = "max_minutes_reached"
                stop_reason = "max_minutes_reached"
                log(
                    "info",
                    "unfollow_exploration_v2_max_minutes_reached",
                    scroll_passes_used=scroll_passes_used,
                    **exploration_fields(exploration_stop_reason=stop_reason),
                )
                status = (
                    "success_real_unfollow_multi_partial_exhausted"
                    if verified > 0
                    else "no_visible_eligible_unfollow_target"
                )
                return emit_final(status)

            current_scroll_budget = (
                int(coverage_tracker.budget.adaptive_scroll_budget)
                if coverage_tracker is not None
                else max_scroll_passes
            )
            if scroll_passes_used >= current_scroll_budget:
                scroll_stop_reason = "scroll_budget_exhausted"
                if coverage_tracker is not None:
                    coverage_stop = coverage_tracker.scroll_budget_decision()
                    stop_reason = str(
                        coverage_stop.stop_reason
                        if coverage_stop is not None
                        else "ui_coverage_budget_exhausted"
                    )
                    remaining_planned_count = len(
                        coverage_tracker.remaining_planned_usernames
                    )
                else:
                    stop_reason, remaining_planned_count = _scroll_budget_stop_reason(
                        planned_usernames,
                        completed_usernames,
                    )
                log(
                    "info",
                    "unfollow_multi_action_loop_completed",
                    stop_reason=stop_reason,
                    scroll_stop_reason=scroll_stop_reason,
                    unfollow_actions_verified_so_far=verified,
                    real_action_max_per_run=real_action_max,
                    remaining_planned_count=remaining_planned_count,
                    adaptive_scroll_budget=current_scroll_budget,
                )
                if (
                    coverage_tracker is not None
                    and remaining_planned_count > 0
                    and not direct_fallback_armed
                    and coverage_tracker.search_recovery_attempted
                    and coverage_tracker.post_recovery_search_scroll_count >= 5
                ):
                    stop_reason = (
                        "ui_coverage_budget_exhausted_with_actionable_remaining"
                    )
                    if can_arm_direct_search_fallback(
                        stop_reason,
                        remaining_count=remaining_planned_count,
                    ):
                        direct_fallback_armed = True
                        log(
                            "info",
                            "unfollow_direct_fallback_armed_after_coverage_budget",
                            reason=stop_reason,
                            remaining_count=remaining_planned_count,
                            direct_batch_limit=DIRECT_SEARCH_FALLBACK_BATCH_LIMIT,
                            progressive_primary_completed=True,
                        )
                        continue
                status = (
                    "success_real_unfollow_multi_partial_exhausted"
                    if verified > 0
                    else "no_visible_eligible_unfollow_target"
                )
                return emit_final(status)

            log(
                "info",
                "unfollow_multi_action_scroll_started",
                iteration_index=iteration_index,
                unfollow_actions_verified_so_far=verified,
                real_action_max_per_run=real_action_max,
                scroll_passes_used=scroll_passes_used,
            )
            before_scroll_keys = _visible_username_keys(
                [str(row.get("username") or "") for row in rows]
            )
            scroll = _scroll_following_list_for_unfollow(
                d,
                account_username=uname,
                before_rows=rows,
                previous_actual_overlap=(
                    int(last_fields.get("unfollow_scroll_actual_overlap") or 0)
                    if last_fields
                    else None
                ),
            )
            if not scroll.get("ok"):
                scroll_stop_reason = str(scroll.get("failure_reason") or "following_surface_lost_after_scroll")
                stop_reason = "scroll_surface_lost"
                if str(scroll_stop_reason) != "swipe_failed":
                    scroll_surface_failures_count += 1
                if bool(scroll.get("scroll_v2_lite_enabled")):
                    scroll_v2_lite_fallback_count += 1
                refresh_scroll_summary_totals()
                log(
                    "info",
                    "unfollow_multi_action_scroll_surface_lost",
                    iteration_index=iteration_index,
                    scroll_passes_used=scroll_passes_used,
                    stop_reason=stop_reason,
                    failure_reason=scroll_stop_reason,
                )
                if coverage_tracker is not None:
                    recovered, recovery_stop = recover_following_viewport(
                        trigger_reason=scroll_stop_reason
                    )
                    if recovered:
                        rows, harvest_meta = harvest_visible_following_rows_for_unfollow(
                            d,
                            account_username=uname,
                        )
                        continue
                    stop_reason = recovery_stop or "ui_recovery_budget_exhausted"
                    return emit_final("failed_unfollow_multi_action", stop_reason)
                stop_reason = "scroll_surface_lost"
                return emit_final("failed_unfollow_multi_action", scroll_stop_reason)
            if bool(scroll.get("depth_advanced")):
                scroll_passes_used += 1
            if "rows_after" in scroll:
                rows = list(scroll.get("rows_after") or [])
                harvest_meta = dict(scroll.get("harvest_meta_after") or {})
            else:
                rows, harvest_meta = harvest_visible_following_rows_for_unfollow(
                    d,
                    account_username=uname,
                )
            after_scroll_keys = _visible_username_keys(
                [str(row.get("username") or "") for row in rows]
            )
            _observe_canonical_unfollow_list_continuation(
                rows,
                harvest_meta,
                before_row_ids=before_scroll_keys,
                account_id=aid,
                run_id=run_id,
            )
            before_set = set(before_scroll_keys)
            after_set = set(after_scroll_keys)
            new_after_count = len([key for key in after_scroll_keys if key not in before_set])
            overlap_count = len(before_set.intersection(after_set))
            after_count = len(after_scroll_keys)
            overlap_ratio = round(overlap_count / after_count, 4) if after_count else 0.0
            if after_scroll_keys and after_scroll_keys == before_scroll_keys:
                unchanged_scroll_streak += 1
            else:
                unchanged_scroll_streak = 0
            if coverage_tracker is not None:
                coverage_tracker.mark_scroll(
                    moved=bool(scroll.get("depth_advanced"))
                )
                refresh_coverage_summary_totals()
            unchanged_scroll_streak_max = max(unchanged_scroll_streak_max, unchanged_scroll_streak)
            scroll_new_usernames_total += new_after_count
            scroll_overlap_ratio_total += overlap_ratio
            scroll_progress_eval_count += 1
            if bool(scroll.get("scroll_v2_lite_enabled")) and new_after_count < _scroll_v2_lite_min_new_usernames():
                scroll_v2_lite_fallback_count += 1
                log(
                    "info",
                    "unfollow_scroll_v2_lite_fallback",
                    fallback_reason="low_new_usernames_after_scroll",
                    min_new_usernames=_scroll_v2_lite_min_new_usernames(),
                    new_usernames_after_scroll_count=new_after_count,
                    scroll_passes_used=scroll_passes_used,
                    **{
                        k: v
                        for k, v in scroll.items()
                        if k not in {"surface_detection"}
                    },
                )
            refresh_scroll_summary_totals()
            progress_fields = {
                "scroll_strategy": str(scroll.get("scroll_strategy") or "legacy"),
                "scroll_distance_ratio": float(scroll.get("scroll_distance_ratio") or 0.0),
                "scroll_settle_s": float(scroll.get("scroll_settle_s") or 0.0),
                "scroll_duration_ms": float(scroll.get("scroll_duration_ms") or 0.0),
                "before_visible_usernames_count": len(before_scroll_keys),
                "after_visible_usernames_count": after_count,
                "new_usernames_after_scroll_count": new_after_count,
                "overlap_usernames_count": overlap_count,
                "overlap_ratio": overlap_ratio,
                "unchanged_scroll_streak": unchanged_scroll_streak,
                "unchanged_scroll_stop_threshold": (
                    _scroll_v2_lite_max_unchanged_scrolls()
                    if bool(scroll.get("scroll_v2_lite_enabled"))
                    else 1
                ),
                "surface_ok_after_scroll": bool(scroll.get("surface_ok_after_scroll")),
                "target_new_rows": int(scroll.get("target_new_rows") or 0),
                "target_overlap_rows": int(scroll.get("target_overlap_rows") or 0),
                "unfollow_scroll_actual_new_rows": int(scroll.get("actual_new_rows") or 0),
                "unfollow_scroll_actual_overlap": int(scroll.get("actual_overlap") or 0),
                "unfollow_scroll_depth_advanced": bool(scroll.get("depth_advanced")),
                "unfollow_scroll_continuity_reason": str(scroll.get("continuity_reason") or ""),
                "unfollow_scroll_corrective_backstep_used": bool(
                    scroll.get("corrective_backstep_used")
                ),
                "end_of_list_detected": bool(
                    scroll.get("end_of_list_detected")
                    or harvest_meta.get("following_list_end_detected")
                ),
            }
            log(
                "info",
                "unfollow_scroll_progress_evaluated",
                scroll_passes_used=scroll_passes_used,
                **progress_fields,
            )
            log(
                "info",
                "unfollow_multi_action_scroll_completed",
                iteration_index=iteration_index,
                unfollow_actions_verified_so_far=verified,
                real_action_max_per_run=real_action_max,
                visible_rows_count=len(rows),
                scroll_passes_used=scroll_passes_used,
                before_scroll_usernames=before_scroll_keys[:20],
                after_scroll_usernames=after_scroll_keys[:20],
                **progress_fields,
            )
            # Feed the measured overlap into the next adaptive 7+1 scroll.
            # Without this state carry-over every pass was recalculated as if
            # no prior overlap had been observed.
            last_fields = {**last_fields, **progress_fields}
            if coverage_tracker is None and bool(harvest_meta.get("following_list_end_detected")) and (
                not after_scroll_keys or after_scroll_keys == before_scroll_keys
            ):
                scroll_stop_reason = "following_list_end_reached"
                stop_reason = "following_list_end_reached"
                end_fields = {
                    "following_list_end_detected": True,
                    "suggested_for_you_visible": bool(harvest_meta.get("suggested_for_you_visible")),
                    "suggestion_follow_buttons_count": int(harvest_meta.get("suggestion_follow_buttons_count") or 0),
                    "following_list_end_reason": str(
                        harvest_meta.get("following_list_end_reason")
                        or "suggested_for_you_section_visible"
                    ),
                }
                last_fields = {
                    **last_fields,
                    **_harvest_summary_fields(rows, harvest_meta, planned_usernames),
                    **end_fields,
                }
                log(
                    "info",
                    "unfollow_following_list_end_detected",
                    scroll_passes_used=scroll_passes_used,
                    visible_rows_count=len(rows),
                    **end_fields,
                )
                log(
                    "info",
                    "unfollow_exploration_v2_end_of_list_reached",
                    scroll_passes_used=scroll_passes_used,
                    **exploration_fields(exploration_stop_reason=stop_reason),
                    **end_fields,
                )
                status = (
                    "success_real_unfollow_multi_partial_end_of_list"
                    if verified > 0
                    else "no_more_following_rows"
                )
                return emit_final(status)

            unchanged_stop_threshold = (
                _scroll_v2_lite_max_unchanged_scrolls()
                if bool(scroll.get("scroll_v2_lite_enabled"))
                else 1
            )
            if (
                coverage_tracker is None
                and
                after_scroll_keys
                and after_scroll_keys == before_scroll_keys
                and unchanged_scroll_streak >= unchanged_stop_threshold
            ):
                scroll_stop_reason = "end_of_list_or_no_new_rows_detected"
                stop_reason, remaining_planned_count = _scroll_budget_stop_reason(
                    planned_usernames,
                    completed_usernames,
                )
                log(
                    "info",
                    "unfollow_multi_action_loop_completed",
                    stop_reason=stop_reason,
                    scroll_stop_reason=scroll_stop_reason,
                    unfollow_actions_verified_so_far=verified,
                    real_action_max_per_run=real_action_max,
                    scroll_passes_used=scroll_passes_used,
                    remaining_planned_count=remaining_planned_count,
                )
                status = (
                    "success_real_unfollow_multi_partial_exhausted"
                    if verified > 0
                    else "no_visible_eligible_unfollow_target"
                )
                return emit_final(status)

        target_username = str(target_row.get("username") or "")
        target_key = normalize_unfollow_username(target_username)
        if account_protection_lists.is_unfollow_protected(target_key):
            stop_reason = "unfollow_target_protected_at_final_action_gate"
            log(
                "error",
                "unfollow_final_action_protection_gate_blocked",
                account_id=aid,
                run_id=run_id,
                username_normalized=target_key,
                source="account_protection_list_entries",
            )
            return emit_final("failed_unfollow_multi_action", stop_reason)
        if any_mode_active:
            any_mode_selected_count += 1
            totals["any_mode_selected_count"] = any_mode_selected_count
            cand = visible_candidates.get(target_key) or {}
            log(
                "info",
                "unfollow_any_target_selected",
                username=target_username,
                username_normalized=target_key,
                row_index=int(target_row.get("row_index") or 0),
                selection_reason=selection_reason,
                row_cta_class=str(target_row.get("row_cta_class") or ""),
                interaction_row_id=str(cand.get("interaction_row_id") or ""),
            )
        log(
            "info",
            "unfollow_visible_eligible_target_selected",
            username=target_username,
            username_normalized=target_key,
            row_index=int(target_row.get("row_index") or 0),
            selection_reason=selection_reason,
            visible_match_source=str(last_fields.get("visible_match_source") or ""),
        )
        log(
            "info",
            "unfollow_real_target_selected",
            username=target_username,
            username_normalized=target_key,
            row_index=int(target_row.get("row_index") or 0),
            selection_reason=selection_reason,
            row_cta_class=str(target_row.get("row_cta_class") or ""),
            cta_text=str(target_row.get("cta_text") or "")[:80],
        )

        if coverage_tracker is not None and not target_opened_directly:
            current_surface = detect_own_following_list_screen(
                d,
                account_username=uname,
            )
            unsafe_markers = _merge_unfollow_surface_unsafe_markers(
                _detect_unfollow_unsafe_markers(d),
                current_surface,
            )
            if unsafe_markers:
                stop_reason = "unsafe_marker_detected"
                log(
                    "error",
                    "unfollow_action_blocked_unsafe_marker",
                    unsafe_markers=unsafe_markers,
                    viewport_fingerprint=coverage_tracker.terminal_fingerprint,
                )
                return emit_final("failed_unfollow_multi_action", stop_reason)
            if not bool(current_surface.get("is_following_list")):
                recovered, recovery_stop = recover_following_viewport(
                    trigger_reason=str(
                        current_surface.get("failure_reason")
                        or "following_state_unconfirmed_before_action"
                    )
                )
                if not recovered:
                    stop_reason = recovery_stop or "ui_recovery_budget_exhausted"
                    return emit_final("failed_unfollow_multi_action", stop_reason)
                rows, harvest_meta = harvest_visible_following_rows_for_unfollow(
                    d,
                    account_username=uname,
                )
                continue

        target_fields = {
            **last_fields,
            "probe_target_username": "",
            "probe_target_selection_reason": "",
            "real_target_username": target_username,
        }
        if target_opened_directly:
            tapped, tap_meta = True, {"ok": True, "method": "direct_exact_search"}
        else:
            tapped, tap_meta = tap_following_list_username_row_for_unfollow_probe(
                d,
                target_row,
                selection_reason=selection_reason,
            )
        if not tapped:
            failed += 1
            stop_reason = "target_row_tap_failed"
            last_fields = {**target_fields, "unfollow_actions_failed": failed}
            return emit_final(
                "failed_unfollow_multi_action",
                str(tap_meta.get("failure_reason") or "target_row_tap_failed"),
            )

        profile_det = (
            {"ok": True, "method": "direct_exact_search_preverified"}
            if target_opened_directly
            else verify_unfollow_target_profile_strict(
                d,
                expected_target_username=target_username,
            )
        )
        if not profile_det.get("ok"):
            failed += 1
            stop_reason = "target_profile_open_failed"
            ret = _return_after_unfollow_profile(
                d,
                account_username=uname,
                direct_exact_search=target_opened_directly,
            )
            last_fields = {
                **target_fields,
                "target_profile_open_ok": False,
                "return_to_following_list_ok": bool(ret.get("ok")),
                "unfollow_actions_failed": failed,
            }
            return emit_final(
                "failed_unfollow_multi_action",
                str(profile_det.get("failure_reason") or "target_profile_open_failed"),
            )

        sheet = open_unfollow_actions_sheet_from_profile_probe(
            d,
            expected_target_username=target_username,
            profile_exact_confirmed=True,
        )
        if not sheet.get("ok"):
            failed += 1
            ret = _return_after_unfollow_profile(
                d,
                account_username=uname,
                direct_exact_search=target_opened_directly,
            )
            return_ok = bool(ret.get("ok"))
            sheet_failure_reason = str(sheet.get("failure_reason") or "actions_sheet_open_failed")
            recoverable = is_recoverable_action_sheet_failure(sheet)
            recovery_stop_reason = ""
            if recoverable and not return_ok:
                return_ok, recovery_stop_reason = recover_following_viewport(
                    trigger_reason=sheet_failure_reason,
                )
            log(
                "info",
                "unfollow_recoverable_action_failure_detected",
                username=target_username,
                username_normalized=target_key,
                failure_reason=sheet_failure_reason,
                retry_failure_reason=str(sheet.get("retry_failure_reason") or ""),
                return_to_following_list_ok=return_ok,
                recovery_stop_reason=recovery_stop_reason,
                recoverable=recoverable,
                recoverable_action_failures_count=recoverable_action_failures_count,
                max_recoverable_action_failures=max_recoverable_action_failures,
                unfollow_actions_sent=sent,
            )
            last_fields = {
                **target_fields,
                "target_profile_open_ok": True,
                "following_actions_sheet_open_ok": False,
                "following_actions_sheet_failure_reason": str(
                    sheet.get("failure_reason") or "actions_sheet_open_failed"
                ),
                "unfollow_option_visible": bool(sheet.get("unfollow_option_visible")),
                "sheet_context_signals": dict(sheet.get("sheet_context_signals") or {}),
                "return_to_following_list_ok": return_ok,
                "unfollow_actions_failed": failed,
            }
            if sheet_failure_reason == "already_not_following_confirmed":
                try:
                    availability_state = (
                        supabase_client.record_unfollow_already_not_following_v1(
                            aid,
                            target_key,
                            source_run_id=run_id,
                            relationship_state=str(
                                sheet.get("positive_not_following_state") or ""
                            ),
                        )
                    )
                except Exception as exc:
                    candidate_availability_persistence_failures += 1
                    if target_key not in candidate_availability_persistence_failure_usernames:
                        candidate_availability_persistence_failure_usernames.append(target_key)
                    log(
                        "error",
                        "unfollow_already_not_following_terminal_persist_failed",
                        account_id=aid,
                        run_id=run_id,
                        username=target_username,
                        error=str(exc)[:500],
                    )
                    stop_reason = "unfollow_candidate_availability_persistence_failed"
                    return emit_final("failed_unfollow_multi_action", stop_reason)
                failed = max(0, failed - 1)
                last_fields["unfollow_actions_failed"] = failed
                if coverage_tracker is not None:
                    coverage_tracker.mark_candidate_unavailable(target_key)
                    refresh_coverage_summary_totals()
                completed_usernames.add(target_key)
                visible_eligibility_row_cache[target_key] = None
                log(
                    "info",
                    "unfollow_already_not_following_terminalized",
                    account_id=aid,
                    run_id=run_id,
                    username=target_username,
                    relationship_state=str(
                        sheet.get("positive_not_following_state") or ""
                    ),
                    persisted=bool(availability_state.get("ok")),
                    availability_status=str(availability_state.get("status") or ""),
                    terminal_at=availability_state.get("terminal_at"),
                    backlog_actionable=False,
                    unfollow_marked_success=False,
                    candidate_outcome_class=(
                        UnfollowActionOutcomeClass.ALREADY_NOT_FOLLOWING_CONFIRMED.value
                    ),
                )
                if not return_ok:
                    return_ok, recovery_stop_reason = recover_following_viewport(
                        trigger_reason="already_not_following_confirmed"
                    )
                    last_fields["return_to_following_list_ok"] = return_ok
                    if not return_ok:
                        stop_reason = str(
                            recovery_stop_reason
                            or "unfollow_already_not_following_return_failed"
                        )
                        return emit_final("failed_unfollow_multi_action", stop_reason)
                if not bool(ret.get("search_session_reused")):
                    rows, harvest_meta = harvest_visible_following_rows_for_unfollow(
                        d,
                        account_username=uname,
                    )
                    last_fields = {
                        **last_fields,
                        **_harvest_summary_fields(rows, harvest_meta, planned_usernames),
                    }
                continue
            if recoverable and coverage_tracker is not None:
                coverage_tracker.mark_candidate_retryable(target_key)
                refresh_coverage_summary_totals()
            if (
                recoverable
                and return_ok
                and recoverable_action_failures_count < max_recoverable_action_failures
            ):
                recoverable_action_failures_count += 1
                session_continued_after_recoverable_failure = True
                failed_usernames_this_run.add(target_key)
                completed_usernames.add(target_key)
                visible_eligibility_row_cache[target_key] = None
                if target_username not in recoverable_action_failure_usernames:
                    recoverable_action_failure_usernames.append(target_username)
                recoverable_action_failure_reasons[target_username] = sheet_failure_reason
                refresh_recoverable_action_summary_totals()
                log(
                    "info",
                    "unfollow_recoverable_action_failure_skipped_target",
                    username=target_username,
                    username_normalized=target_key,
                    failure_reason=sheet_failure_reason,
                    recoverable_action_failures_count=recoverable_action_failures_count,
                    max_recoverable_action_failures=max_recoverable_action_failures,
                    candidate_outcome_class=(
                        UnfollowActionOutcomeClass.TRANSIENT_UI_FAILURE.value
                    ),
                )
                if not bool(ret.get("search_session_reused")):
                    rows, harvest_meta = harvest_visible_following_rows_for_unfollow(
                        d,
                        account_username=uname,
                    )
                    last_fields = {
                        **last_fields,
                        **_harvest_summary_fields(rows, harvest_meta, planned_usernames),
                    }
                log(
                    "info",
                    "unfollow_recoverable_action_failure_continue",
                    username=target_username,
                    username_normalized=target_key,
                    failure_reason=sheet_failure_reason,
                    unfollow_actions_verified_so_far=verified,
                    real_action_max_per_run=real_action_max,
                    scroll_passes_used=scroll_passes_used,
                    return_to_following_list_ok=return_ok,
                )
                continue
            if recoverable:
                log(
                    "info",
                    "unfollow_recoverable_action_failure_limit_reached",
                    username=target_username,
                    username_normalized=target_key,
                    failure_reason=sheet_failure_reason,
                    recoverable_action_failures_count=recoverable_action_failures_count,
                    max_recoverable_action_failures=max_recoverable_action_failures,
                )
                stop_reason = (
                    recovery_stop_reason
                    or "recoverable_action_failure_budget_exhausted"
                )
                return emit_final(
                    "success_real_unfollow_multi_partial_exhausted",
                    sheet_failure_reason,
                )
            stop_reason = "actions_sheet_open_failed"
            return emit_final("failed_unfollow_multi_action", sheet_failure_reason)
        if not bool(sheet.get("unfollow_option_visible")):
            failed += 1
            stop_reason = "unfollow_option_missing"
            ret = _return_after_unfollow_profile(
                d,
                account_username=uname,
                direct_exact_search=target_opened_directly,
            )
            last_fields = {
                **target_fields,
                "target_profile_open_ok": True,
                "following_actions_sheet_open_ok": True,
                "unfollow_option_visible": False,
                "return_to_following_list_ok": bool(ret.get("ok")),
                "unfollow_actions_failed": failed,
            }
            return emit_final("failed_unfollow_multi_action", "unfollow_option_missing")

        tap_out = tap_unfollow_in_following_sheet(d, target_username=target_username)
        if tap_out.get("ok"):
            sent += 1
            action_attempted_usernames.add(target_key)
            safe_diagnostic_call("record_action_attempt", username=target_key)
            if coverage_tracker is not None:
                coverage_tracker.mark_action_attempted(target_key)
        else:
            failed += 1
            stop_reason = "unfollow_tap_failed"
            ret = _return_after_unfollow_profile(
                d,
                account_username=uname,
                direct_exact_search=target_opened_directly,
            )
            last_fields = {
                **target_fields,
                "target_profile_open_ok": True,
                "following_actions_sheet_open_ok": True,
                "unfollow_option_visible": True,
                "return_to_following_list_ok": bool(ret.get("ok")),
                "unfollow_actions_sent": sent,
                "unfollow_actions_failed": failed,
            }
            return emit_final(
                "failed_unfollow_multi_action",
                str(tap_out.get("failure_reason") or "unfollow_tap_failed"),
            )

        verify_out = verify_unfollow_action_success_after_tap(d, target_username=target_username)
        verify_ok = bool(verify_out.get("ok"))
        verify_failure_reason = str(
            verify_out.get("failure_reason")
            or tap_out.get("failure_reason")
            or "unfollow_verify_failed"
        )
        candidate_outcome_class = (
            UnfollowActionOutcomeClass.VERIFIED_UNFOLLOW.value
            if verify_ok
            else UnfollowActionOutcomeClass.ACTION_ATTEMPTED_AMBIGUOUS.value
        )
        durable_failure_reason = (
            "" if verify_ok else ambiguous_failure_reason(verify_failure_reason)
        )
        if verify_ok:
            action_verified_usernames.add(target_key)
        if verify_ok and coverage_tracker is not None:
            coverage_tracker.mark_action_verified(target_key)
        cand = (
            _visible_candidates_by_username({"visible_eligible_matches": list(visible_candidates.values())}).get(target_key)
            or planned_by_username.get(target_key)
            or {}
        )
        persist_out = _persist_unfollow_outcome_for_session(
            aid,
            target_username,
            run_id=run_id,
            settings=settings,
            verify_ok=verify_ok,
            interaction_row_id=str(cand.get("interaction_row_id") or cand.get("id") or "").strip() or None,
            failure_reason=durable_failure_reason,
        )
        persist_ok = bool(persist_out.get("ok"))
        persistence_delta = unfollow_persistence_count_delta(
            verify_ok=verify_ok,
            persist_ok=persist_ok,
        )
        persisted += int(persistence_delta["verified_persisted"])
        persisted_outcomes += int(persistence_delta["outcome_persisted"])
        if persist_ok:
            if verify_ok:
                action_persisted_usernames.add(target_key)
            if verify_ok and coverage_tracker is not None:
                coverage_tracker.mark_action_persisted(target_key)
            if verify_ok:
                interaction_row_id = str(persist_out.get("interaction_row_id") or "").strip() or None
                unfollow_observed_successes.append(
                    _log_unfollow_success_observed(
                        account_id=aid,
                        username=target_username,
                        run_id=run_id,
                        persist_out=persist_out,
                        interaction_row_id=interaction_row_id,
                    )
                )
        else:
            log(
                "info",
                "unfollow_result_persist_failed",
                account_id=aid,
                username=target_username,
                reason=str(persist_out.get("error") or "persist_failed"),
            )
        log(
            "info",
            "unfollow_candidate_lineage_v1",
            account_id=aid,
            run_id=run_id,
            username=target_key,
            lineage_stage="action_terminal",
            db_eligible_at_start=(True if target_key in planned_usernames else None),
            db_eligibility_scope=(
                "known_from_capped_session_plan"
                if target_key in planned_usernames
                else "unknown_outside_capped_session_plan"
            ),
            ui_seen=True,
            viewport_index=iteration_index,
            scroll_depth=scroll_passes_used,
            skip_reason=str(
                durable_failure_reason
                or persist_out.get("error")
                or ""
            ),
            candidate_outcome_class=candidate_outcome_class,
            action_attempted=target_key in action_attempted_usernames,
            action_verified=target_key in action_verified_usernames,
            persistence_ok=target_key in action_persisted_usernames,
            monotonic_s=round(time.perf_counter(), 6),
        )
        safe_diagnostic_call(
            "emit_action_terminal",
            username=target_key,
            row_cache=visible_eligibility_row_cache,
            attempted=target_key in action_attempted_usernames,
            verified=target_key in action_verified_usernames,
            persisted=target_key in action_persisted_usernames,
            failure_reason=str(
                durable_failure_reason
                or persist_out.get("error")
                or ""
            ),
        )
        if verify_ok and persist_ok:
            recoverable_verify_failure_streak_class = ""
            recoverable_verify_failure_streak_count = 0
            refresh_recoverable_action_summary_totals()
            verified += 1
            completed_usernames.add(target_key)
            visible_eligibility_row_cache[target_key] = None
            effective_unfollows_done = (
                max(0, int(effective_unfollows_done_at_start)) + int(verified)
            )
            quota_reached = unfollow_quota_reached_after_persist(
                day_limit=unfollow_day_limit,
                effective_done_at_start=effective_unfollows_done_at_start,
                verified_persisted_in_run=verified,
            )
            log(
                "info",
                "unfollow_post_persist_quota_evaluated",
                account_id=aid,
                run_id=run_id,
                target_username=target_username,
                unfollow_day_quota=max(0, int(unfollow_day_limit)),
                unfollows_done_at_start=max(0, int(effective_unfollows_done_at_start)),
                verified_persisted_in_run=int(verified),
                effective_unfollows_done=effective_unfollows_done,
                quota_reached=quota_reached,
            )
            if quota_reached:
                stop_reason = "unfollow_quota_reached"
                last_fields = {
                    **target_fields,
                    "target_profile_open_ok": True,
                    "following_actions_sheet_open_ok": True,
                    "unfollow_option_visible": True,
                    "unfollow_actions_sent": sent,
                    "unfollow_actions_verified": verified,
                    "unfollow_actions_failed": failed,
                    "unfollow_results_persisted_count": persisted,
                    "unfollow_action_verify_ok": True,
                    "unfollow_persistence_ok": True,
                    "return_to_following_list_ok": None,
                    "safe_boundary": "post_action_verified_and_persisted",
                    "unfollow_day_quota": int(unfollow_day_limit),
                    "effective_unfollows_done": effective_unfollows_done,
                }
                log(
                    "info",
                    "unfollow_quota_reached_immediate_stop",
                    account_id=aid,
                    run_id=run_id,
                    target_username=target_username,
                    unfollow_day_quota=int(unfollow_day_limit),
                    effective_unfollows_done=effective_unfollows_done,
                    actions_after_quota=0,
                    scrolls_after_quota=0,
                )
                return emit_final("success_real_unfollow_multi_quota_reached")

        if not verify_ok:
            failed += 1
            post_action_unsafe_markers = _detect_unfollow_unsafe_markers(d)
            recovery_out: dict[str, Any]
            if post_action_unsafe_markers:
                recovery_out = {
                    "ok": False,
                    "return_to_following_list_ok": False,
                    "exact_following_list_restored": False,
                    "package_activity_ok": False,
                    "account_identity_ok": True,
                    "unsafe_markers": post_action_unsafe_markers,
                    "failure_reason": "unsafe_marker_detected",
                    "recovery_max_attempts": 0,
                }
            else:
                recovery_out = recover_exact_following_after_ambiguous_action(
                    trigger_reason=verify_failure_reason,
                )

            decision = decide_verify_failure_after_recovery(
                verification_ok=False,
                action_attempted=target_key in action_attempted_usernames,
                failure_reason=verify_failure_reason,
                exact_following_list_restored=bool(
                    recovery_out.get("exact_following_list_restored")
                ),
                package_activity_ok=bool(recovery_out.get("package_activity_ok")),
                account_identity_ok=bool(recovery_out.get("account_identity_ok")),
                unsafe_markers_present=bool(recovery_out.get("unsafe_markers")),
                persistence_ok=persist_ok,
                previous_failure_class=recoverable_verify_failure_streak_class,
                previous_consecutive_count=recoverable_verify_failure_streak_count,
                max_consecutive_failures=max_recoverable_action_failures,
            )
            recoverable_verify_failure_streak_class = decision.recovery_class.value
            recoverable_verify_failure_streak_count = decision.next_consecutive_count
            if decision.recovery_class == UnfollowActionOutcomeClass.VERIFY_FAILED_RECOVERABLE:
                recoverable_verify_failures_count += 1
                if target_username not in recoverable_verify_failure_usernames:
                    recoverable_verify_failure_usernames.append(target_username)
            refresh_recoverable_action_summary_totals()

            last_fields = {
                **target_fields,
                "target_profile_open_ok": True,
                "following_actions_sheet_open_ok": True,
                "unfollow_option_visible": True,
                "unfollow_actions_sent": sent,
                "unfollow_actions_verified": verified,
                "unfollow_actions_failed": failed,
                "unfollow_results_persisted_count": persisted,
                "unfollow_action_verify_ok": False,
                "unfollow_persistence_ok": persist_ok,
                "return_to_following_list_ok": bool(
                    recovery_out.get("return_to_following_list_ok")
                ),
                "exact_following_list_restored": bool(
                    recovery_out.get("exact_following_list_restored")
                ),
                "unfollow_candidate_outcome_class": decision.candidate_outcome_class.value,
                "unfollow_recovery_class": decision.recovery_class.value,
                "unfollow_recovery_max_attempts": int(
                    recovery_out.get("recovery_max_attempts") or 0
                ),
                "recoverable_verify_failure_streak_count": (
                    recoverable_verify_failure_streak_count
                ),
            }
            log(
                "info" if decision.should_continue else "error",
                "unfollow_verify_failure_classified",
                account_id=aid,
                run_id=run_id,
                username=target_username,
                username_normalized=target_key,
                candidate_outcome_class=decision.candidate_outcome_class.value,
                recovery_class=decision.recovery_class.value,
                stable_reason=decision.stable_reason,
                persistence_ok=persist_ok,
                exact_following_list_restored=bool(
                    recovery_out.get("exact_following_list_restored")
                ),
                package_activity_ok=bool(recovery_out.get("package_activity_ok")),
                account_identity_ok=bool(recovery_out.get("account_identity_ok")),
                unsafe_markers=list(recovery_out.get("unsafe_markers") or []),
                recovery_max_attempts=int(
                    recovery_out.get("recovery_max_attempts") or 0
                ),
                recoverable_verify_failure_streak_count=(
                    recoverable_verify_failure_streak_count
                ),
                max_recoverable_action_failures=max_recoverable_action_failures,
                circuit_breaker_open=decision.circuit_breaker_open,
                should_continue=decision.should_continue,
                false_success_persisted=False,
                second_unfollow_tap_allowed=False,
            )
            if decision.should_continue:
                session_continued_after_recoverable_failure = True
                failed_usernames_this_run.add(target_key)
                completed_usernames.add(target_key)
                visible_eligibility_row_cache[target_key] = None
                if coverage_tracker is not None:
                    coverage_tracker.mark_candidate_technical_hold(target_key)
                    refresh_coverage_summary_totals()
                refresh_recoverable_action_summary_totals()
                rows, harvest_meta = harvest_visible_following_rows_for_unfollow(
                    d,
                    account_username=uname,
                )
                last_fields = {
                    **last_fields,
                    **_harvest_summary_fields(rows, harvest_meta, planned_usernames),
                }
                log(
                    "info",
                    "unfollow_verify_failure_recovered_continue",
                    account_id=aid,
                    run_id=run_id,
                    username=target_username,
                    username_normalized=target_key,
                    failure_reason=verify_failure_reason,
                    durable_failure_reason=decision.stable_reason,
                    unfollow_actions_verified_so_far=verified,
                    real_action_max_per_run=real_action_max,
                    session_cap_remaining=max(0, real_action_max - verified),
                    no_second_unfollow_tap=True,
                    no_success_persistence=True,
                )
                continue

            if decision.circuit_breaker_open:
                stop_reason = "recoverable_verify_failure_circuit_open"
                return emit_final(
                    "success_real_unfollow_multi_partial_exhausted",
                    stop_reason,
                )
            stop_reason = str(
                recovery_out.get("failure_reason")
                or persist_out.get("error")
                or "unfollow_verify_failed_unsafe_state"
            )
            return emit_final(
                "failed_unfollow_multi_action",
                stop_reason,
            )

        ret = _return_after_unfollow_profile(
            d,
            account_username=uname,
            direct_exact_search=target_opened_directly,
        )
        return_ok = bool(ret.get("ok"))
        search_session_reused = bool(ret.get("search_session_reused"))
        if search_session_reused:
            direct_search_session_reused_count += 1
        if not persist_ok:
            failed += 1
            stop_reason = "unfollow_persistence_failed"
            last_fields = {
                **target_fields,
                "target_profile_open_ok": True,
                "following_actions_sheet_open_ok": True,
                "unfollow_option_visible": True,
                "unfollow_actions_sent": sent,
                "unfollow_actions_verified": verified,
                "unfollow_actions_failed": failed,
                "unfollow_results_persisted_count": persisted,
                "unfollow_action_verify_ok": True,
                "unfollow_persistence_ok": False,
                "return_to_following_list_ok": return_ok,
            }
            return emit_final(
                "failed_unfollow_multi_action",
                str(persist_out.get("error") or "unfollow_persistence_failed"),
            )
        if not return_ok:
            failed += 1
            stop_reason = "return_to_following_list_failed"
            last_fields = {
                **target_fields,
                "target_profile_open_ok": True,
                "following_actions_sheet_open_ok": True,
                "unfollow_option_visible": True,
                "unfollow_actions_sent": sent,
                "unfollow_actions_verified": verified,
                "unfollow_actions_failed": failed,
                "unfollow_results_persisted_count": persisted,
                "unfollow_action_verify_ok": True,
                "unfollow_persistence_ok": True,
                "return_to_following_list_ok": False,
            }
            return emit_final("failed_unfollow_multi_action", "return_to_following_list_failed")

        if coverage_tracker is not None:
            coverage_tracker.mark_safe_profile_return(target_key)
            refresh_coverage_summary_totals()
        last_fields = {
            **target_fields,
            "target_profile_open_ok": True,
            "following_actions_sheet_open_ok": True,
            "unfollow_option_visible": True,
            "unfollow_actions_sent": sent,
            "unfollow_actions_verified": verified,
            "unfollow_actions_failed": failed,
            "unfollow_results_persisted_count": persisted,
            "unfollow_action_verify_ok": True,
            "unfollow_persistence_ok": True,
            "return_to_following_list_ok": True,
            "return_destination": str(ret.get("destination") or ""),
            "search_session_reused": search_session_reused,
        }
        log(
            "info",
            "unfollow_multi_action_iteration_completed",
            iteration_index=iteration_index,
            target_username=target_username,
            unfollow_actions_verified_so_far=verified,
            real_action_max_per_run=real_action_max,
            scroll_passes_used=scroll_passes_used,
        )
        if not search_session_reused:
            rows, harvest_meta = harvest_visible_following_rows_for_unfollow(
                d,
                account_username=uname,
            )

    stop_reason = "limit_reached"
    log(
        "info",
        "unfollow_multi_action_loop_completed",
        stop_reason=stop_reason,
        unfollow_actions_verified_so_far=verified,
        real_action_max_per_run=real_action_max,
        scroll_passes_used=scroll_passes_used,
    )
    return emit_final("success_real_unfollow_multi_limit_reached")


def run_unfollow_session(
    d: u2.Device,
    *,
    account_id: str,
    account_username: str,
    run_id: str | None = None,
    dry_probe_only: bool = True,
    real_action_enabled_override: bool | None = None,
    real_action_max_override: int | None = None,
    business_action_deadline: str | None = None,
    outreach_reserve_seconds: int = 0,
    resume_checkpoint: dict[str, Any] | None = None,
    quota_remaining_hint: int | None = None,
    request_id: str | None = None,
    business_session_id: str | None = None,
    worker_sha: str | None = None,
    session_attempt: int = 1,
) -> int:
    """Run unfollow_session: probe by default; real Unfollow only with explicit config opt-in."""
    t0 = time.perf_counter()
    aid = str(account_id or "").strip()
    uname = str(account_username or "").strip()
    config_real_enabled = (
        _real_action_enabled()
        if real_action_enabled_override is None
        else bool(real_action_enabled_override)
    )
    settings = load_unfollow_settings(aid, ensure_row=False)
    env_real_action_max = (
        _real_action_max_per_run()
        if real_action_max_override is None
        else max(0, int(real_action_max_override))
    )
    db_unfollow_day_limit = max(0, int(getattr(settings, "day_limit", 0) or 0))
    try:
        unfollows_done_today = supabase_client.count_successful_unfollows_today(aid)
    except Exception as exc:
        unfollows_done_today = db_unfollow_day_limit
        log(
            "error",
            "unfollow_day_counter_load_failed",
            account_id=aid,
            run_id=run_id,
            error=str(exc)[:500],
        )
    day_progress = resolve_effective_unfollow_day_progress(
        day_limit=db_unfollow_day_limit,
        persisted_daily_count=int(unfollows_done_today or 0),
        quota_remaining_hint=quota_remaining_hint,
    )
    hinted_remaining = day_progress["quota_remaining_hint"]
    hinted_done_today = int(day_progress["hinted_done"] or 0)
    effective_unfollows_done_at_start = int(day_progress["effective_done"] or 0)
    unfollow_day_remaining_today = int(day_progress["remaining"] or 0)
    runtime_cap_resolution = resolve_unfollow_runtime_cap(
        db_unfollow_per_session_limit=getattr(settings, "session_limit", 0),
        runtime_cap_mode=getattr(settings, "runtime_cap_mode", "prod_normal"),
        runtime_safety_cap=getattr(settings, "runtime_safety_cap", None),
        env_real_action_max_per_run=env_real_action_max,
    )
    real_action_max = _effective_real_action_max_per_run(
        settings,
        env_real_action_max,
        unfollow_day_remaining_today,
    )
    resolved_deadline = str(
        business_action_deadline or os.environ.get("BUSINESS_ACTION_DEADLINE") or ""
    ).strip() or None
    domain_real_action_max = real_action_max
    protected_usernames = account_protection_lists.unfollow_whitelist_for_run(aid)
    plan = plan_unfollow_targets(
        aid,
        settings=settings,
        protected_usernames=protected_usernames,
    )
    planned_usernames = _planned_username_set(plan)
    planned_by_username = _planned_candidates_by_username(plan)
    unexpected_protected_candidates = planned_usernames.intersection(protected_usernames)
    if unexpected_protected_candidates:
        log(
            "error",
            "unfollow_plan_protection_invariant_failed",
            account_id=aid,
            run_id=run_id,
            protected_candidate_count=len(unexpected_protected_candidates),
            source="account_protection_list_entries",
        )
        raise RuntimeError("unfollow_plan_contains_protected_candidate")
    handoff_budget = _runtime_adaptive_coverage_budget(
        quota_remaining=domain_real_action_max,
        eligible_remaining=len(planned_usernames),
        business_action_deadline=resolved_deadline,
        outreach_reserve_seconds=outreach_reserve_seconds,
    )
    real_action_max = min(domain_real_action_max, int(handoff_budget.planned_unfollows))
    time_budget = {
        "business_action_deadline": handoff_budget.deadline_source.startswith("scheduler")
        and resolved_deadline
        or None,
        "remaining_seconds": handoff_budget.scheduled_session_remaining_seconds,
        "estimated_seconds_per_action": handoff_budget.estimated_seconds_per_unfollow,
        "finalization_reserve_seconds": handoff_budget.minimum_session_cleanup_reserve_seconds,
        "time_bounded_action_cap": real_action_max,
        "deadline_source": handoff_budget.deadline_source,
    }
    log(
        "info",
        "unfollow_effective_limits_resolved",
        account_id=aid,
        run_id=run_id,
        unfollow_mode=str(getattr(settings, "mode", "") or ""),
        db_unfollow_per_session_limit=int(getattr(settings, "session_limit", 0) or 0),
        db_unfollow_per_day_limit=db_unfollow_day_limit,
        unfollows_done_today=int(unfollows_done_today or 0),
        quota_remaining_hint=hinted_remaining,
        hinted_unfollows_done_today=hinted_done_today,
        effective_unfollows_done_at_start=effective_unfollows_done_at_start,
        unfollow_day_remaining_today=unfollow_day_remaining_today,
        env_real_action_max_per_run=env_real_action_max,
        runtime_cap_mode=str(runtime_cap_resolution.get("runtime_cap_mode") or ""),
        runtime_cap_source=str(runtime_cap_resolution.get("runtime_cap_source") or ""),
        runtime_mode_cap=int(runtime_cap_resolution.get("runtime_cap") or 0),
        effective_real_action_max_per_run=real_action_max,
        domain_real_action_max_per_run=domain_real_action_max,
        eligible_candidates=len(planned_usernames),
        quota_remaining=domain_real_action_max,
        cleanup_reserve=handoff_budget.minimum_session_cleanup_reserve_seconds,
        recovery_reserve=handoff_budget.recovery_reserve_seconds,
        outreach_reserve=handoff_budget.outreach_reserve_seconds,
        capacity_estimate=handoff_budget.conservative_capacity,
        planned_unfollows=handoff_budget.planned_unfollows,
        handoff_budget=handoff_budget.as_dict(),
        **time_budget,
        source_day_counter="ig_interacted_users.unfollowed_at",
        source="min(db_session,env_hard_cap,runtime_mode_cap,db_day_remaining)",
    )
    visible_eligibility_row_cache: dict[str, dict[str, Any] | None] = {}

    if bool(dry_probe_only):
        real_action_active = False
    else:
        real_action_active = bool(
            config_real_enabled and settings.enabled and real_action_max > 0
        )
    probe_only = not real_action_active

    log(
        "info",
        "unfollow_session_started",
        account_id=aid,
        account_username=uname,
        run_id=run_id,
        probe_only=probe_only,
        real_action_enabled=config_real_enabled,
        real_action_active=real_action_active,
        real_action_max_per_run=real_action_max,
        unfollow_enabled=bool(settings.enabled),
        unfollow_actions_sent=0,
    )

    base_summary = _base_session_summary(
        aid=aid,
        uname=uname,
        run_id=run_id,
        settings=settings,
        plan=plan,
        probe_only=probe_only,
        real_action_enabled=config_real_enabled,
        real_action_max_per_run=real_action_max,
    )
    base_summary.update(
        {
            "db_unfollow_per_day_limit": db_unfollow_day_limit,
            "unfollows_done_today": int(unfollows_done_today or 0),
            "quota_remaining_hint": hinted_remaining,
            "hinted_unfollows_done_today": hinted_done_today,
            "effective_unfollows_done_at_start": effective_unfollows_done_at_start,
            "unfollow_day_remaining_today": unfollow_day_remaining_today,
            "source_day_counter": "ig_interacted_users.unfollowed_at",
            **time_budget,
        }
    )
    diagnostic_session: UnfollowDiagnosticSession | None = None
    if real_action_active:
        try:
            diagnostic_session = UnfollowDiagnosticSession(
                account_id=aid,
                request_id=(request_id or os.environ.get("ACCOUNT_RUN_REQUEST_ID")),
                run_id=run_id,
                business_session_id=business_session_id,
                worker_sha=(worker_sha or os.environ.get("WORKER_GIT_SHA")),
                session_attempt=max(1, int(session_attempt or 1)),
                plan=plan,
                protected_usernames=set(protected_usernames),
                plan_cap=int(getattr(settings, "session_limit", 0) or 0),
                session_quota=real_action_max,
                daily_remaining=unfollow_day_remaining_today,
                time_budget_seconds=handoff_budget.scheduled_session_remaining_seconds,
                cleanup_reserve=handoff_budget.minimum_session_cleanup_reserve_seconds,
                expected_max_actions=real_action_max,
                plan_admission_required=(
                    str(getattr(settings, "mode", "") or "") != UNFOLLOW_MODE_ANY
                ),
                emit=log,
            )
            diagnostic_session.emit_start()
        except Exception as exc:
            diagnostic_session = None
            log(
                "warning",
                "unfollow_diagnostic_v2_start_failed",
                account_id=aid,
                run_id=run_id,
                error=str(exc)[:500],
                behavior_affected=False,
            )

    def emit_early_diagnostic_terminal(status: str, reason: str) -> None:
        if diagnostic_session is None:
            return
        try:
            diagnostic_session.emit_terminal(
                status=status,
                stop_reason=reason,
                row_cache=visible_eligibility_row_cache,
                plan_remaining=len(planned_usernames),
                attempted_usernames=set(),
                verified_usernames=set(),
                persisted_usernames=set(),
                failed_count=1 if status.startswith("failed_") else 0,
                filtered_count=0,
                end_of_list_status="not_reached",
            )
        except Exception as exc:
            log(
                "warning",
                "unfollow_diagnostic_v2_terminal_failed",
                account_id=aid,
                run_id=run_id,
                error=str(exc)[:500],
                behavior_affected=False,
            )

    if (
        not bool(dry_probe_only)
        and config_real_enabled
        and bool(settings.enabled)
        and int(time_budget["time_bounded_action_cap"]) <= 0
    ):
        summary = {
            **base_summary,
            "status": "success_unfollow_skipped_insufficient_time",
            "failure_reason": "",
            "multi_action_stop_reason": "unfollow_skipped_insufficient_time",
            "total_ms": round((time.perf_counter() - t0) * 1000.0, 2),
        }
        log("info", "unfollow_skipped_insufficient_time", **time_budget)
        emit_early_diagnostic_terminal(
            "success_unfollow_skipped_insufficient_time",
            "unfollow_skipped_insufficient_time",
        )
        _emit_summary(summary)
        return 0

    if (
        not bool(dry_probe_only)
        and config_real_enabled
        and bool(settings.enabled)
        and unfollow_day_remaining_today <= 0
    ):
        summary = {
            **base_summary,
            "status": "no_quota",
            "failure_reason": "unfollow_day_limit_reached",
            "multi_action_stop_reason": "unfollow_day_limit_reached",
            "total_ms": round((time.perf_counter() - t0) * 1000.0, 2),
        }
        emit_early_diagnostic_terminal("no_quota", "unfollow_day_limit_reached")
        _emit_summary(summary)
        return 0

    identity = verify_active_instagram_account_matches_expected(
        d,
        expected_account_username=uname,
        account_id=aid,
        run_type="unfollow_session",
        run_id=run_id,
        stage="unfollow_session_preflight",
    )
    if not identity.ok:
        summary = {
            **base_summary,
            "status": "failed_active_instagram_account_mismatch",
            "failure_reason": "active_instagram_account_mismatch",
            "actual_logged_in_username": identity.actual_logged_in_username,
            "account_identity_failure_reason": identity.failure_reason,
            "account_identity_verification_method": identity.verification_method,
            "following_surface_ok": False,
            "visible_rows_count": 0,
            "visible_plan_matches_count": 0,
            "total_ms": round((time.perf_counter() - t0) * 1000.0, 2),
        }
        emit_early_diagnostic_terminal(
            "failed_active_instagram_account_mismatch",
            "active_instagram_account_mismatch",
        )
        _emit_summary(summary)
        return 1

    ok_open, open_meta = open_own_following_list_from_own_profile(d, uname)
    if not ok_open:
        summary = {
            **base_summary,
            "status": "failed_open_following",
            "failure_reason": str((open_meta or {}).get("failure_reason") or "open_following_failed"),
            "total_ms": round((time.perf_counter() - t0) * 1000.0, 2),
        }
        emit_early_diagnostic_terminal(
            "failed_open_following",
            str((open_meta or {}).get("failure_reason") or "open_following_failed"),
        )
        _emit_summary(summary)
        return 1

    det = detect_own_following_list_screen(d, account_username=uname)
    if not bool(det.get("is_following_list")):
        summary = {
            **base_summary,
            "status": "failed_surface",
            "failure_reason": str(det.get("failure_reason") or "following_surface_not_verified"),
            "following_surface_ok": False,
            "total_ms": round((time.perf_counter() - t0) * 1000.0, 2),
        }
        emit_early_diagnostic_terminal(
            "failed_surface",
            str(det.get("failure_reason") or "following_surface_not_verified"),
        )
        _emit_summary(summary)
        return 1

    requested_sort_mode = str(getattr(settings, "sort_mode", "default") or "default").strip().lower() or "default"
    rows, harvest_meta = harvest_visible_following_rows_for_unfollow(d, account_username=uname)
    pre_sort_harvest_fields = _harvest_summary_fields(rows, harvest_meta, planned_usernames)
    sort_fields: dict[str, Any] = {
        "unfollow_sort_mode_requested": requested_sort_mode,
        "sort_apply_attempted": requested_sort_mode != "default",
        "sort_apply_ok": requested_sort_mode == "default",
        "sort_mode_ui_before": "",
        "sort_mode_ui_after": "",
        "sort_verification_strength": "not_applicable_default" if requested_sort_mode == "default" else "",
        "pre_sort_visible_rows_count": int(pre_sort_harvest_fields.get("visible_rows_count") or 0),
        "post_sort_visible_rows_count": int(pre_sort_harvest_fields.get("visible_rows_count") or 0),
        "pre_sort_visible_plan_matches_count": int(pre_sort_harvest_fields.get("visible_plan_matches_count") or 0),
        "post_sort_visible_plan_matches_count": int(pre_sort_harvest_fields.get("visible_plan_matches_count") or 0),
    }

    if requested_sort_mode == "default":
        apply_unfollow_following_sort_mode(d, requested_sort_mode=requested_sort_mode)
        harvest_fields = {
            **pre_sort_harvest_fields,
            **sort_fields,
        }
    else:
        sort_control = detect_unfollow_following_sort_control(d)
        sort_fields["sort_mode_ui_before"] = str(sort_control.get("current_sort_mode_ui_guess") or "")
        sort_apply = apply_unfollow_following_sort_mode(
            d,
            requested_sort_mode=requested_sort_mode,
            sort_control=sort_control,
        )
        if not bool(sort_apply.get("ok")):
            summary = {
                **base_summary,
                **pre_sort_harvest_fields,
                **sort_fields,
                "sort_apply_ok": False,
                "status": "failed_unfollow_sort_apply",
                "failure_reason": str(sort_apply.get("failure_reason") or "unfollow_sort_apply_failed"),
                "total_ms": round((time.perf_counter() - t0) * 1000.0, 2),
            }
            emit_early_diagnostic_terminal(
                "failed_unfollow_sort_apply",
                str(sort_apply.get("failure_reason") or "unfollow_sort_apply_failed"),
            )
            _emit_summary(summary)
            return 1

        sort_verify = verify_unfollow_following_sort_applied(
            d,
            account_username=uname,
            requested_sort_mode=requested_sort_mode,
        )
        if not bool(sort_verify.get("ok")):
            summary = {
                **base_summary,
                **pre_sort_harvest_fields,
                **sort_fields,
                "sort_apply_ok": False,
                "sort_mode_ui_after": str(sort_verify.get("sort_mode_ui_after") or ""),
                "sort_verification_strength": str(sort_verify.get("verification_strength") or ""),
                "status": "failed_unfollow_sort_verify",
                "failure_reason": str(sort_verify.get("failure_reason") or "unfollow_sort_verify_failed"),
                "total_ms": round((time.perf_counter() - t0) * 1000.0, 2),
            }
            emit_early_diagnostic_terminal(
                "failed_unfollow_sort_verify",
                str(sort_verify.get("failure_reason") or "unfollow_sort_verify_failed"),
            )
            _emit_summary(summary)
            return 1

        rows, harvest_meta = harvest_visible_following_rows_for_unfollow(d, account_username=uname)
        post_sort_harvest_fields = _harvest_summary_fields(rows, harvest_meta, planned_usernames)
        sort_fields.update(
            {
                "sort_apply_ok": True,
                "sort_mode_ui_after": str(sort_verify.get("sort_mode_ui_after") or ""),
                "sort_verification_strength": str(sort_verify.get("verification_strength") or ""),
                "post_sort_visible_rows_count": int(post_sort_harvest_fields.get("visible_rows_count") or 0),
                "post_sort_visible_plan_matches_count": int(
                    post_sort_harvest_fields.get("visible_plan_matches_count") or 0
                ),
            }
        )
        harvest_fields = {
            **post_sort_harvest_fields,
            **sort_fields,
        }

    cursor_restore_fields: dict[str, Any] = {
        "cursor_restore_attempted": False,
        "cursor_restore_succeeded": False,
        "cursor_restore_scrolls": 0,
        "cursor_restore_reason": "no_checkpoint",
    }
    if real_action_active and isinstance(resume_checkpoint, dict):
        prior_depth = max(0, int(resume_checkpoint.get("depth") or 0))
        restore_limit = min(prior_depth, CURSOR_RESTORE_SCROLL_LIMIT)
        if restore_limit > 0 and list(resume_checkpoint.get("anchor_hashes") or []):
            cursor_restore_fields.update(
                {
                    "cursor_restore_attempted": True,
                    "cursor_restore_reason": "anchor_not_found_within_bound",
                }
            )
            for restore_index in range(restore_limit + 1):
                visible_handles = [str(row.get("username") or "") for row in rows]
                if cursor_anchor_matches(visible_handles, resume_checkpoint):
                    cursor_restore_fields.update(
                        {
                            "cursor_restore_succeeded": True,
                            "cursor_restore_reason": "checkpoint_anchor_found",
                        }
                    )
                    break
                if restore_index >= restore_limit:
                    break
                scroll = _scroll_following_list_for_unfollow(
                    d,
                    account_username=uname,
                    before_rows=rows,
                )
                if not bool(scroll.get("ok")):
                    cursor_restore_fields["cursor_restore_reason"] = str(
                        scroll.get("failure_reason") or "cursor_restore_scroll_failed"
                    )
                    break
                if not bool(scroll.get("depth_advanced")):
                    cursor_restore_fields["cursor_restore_reason"] = str(
                        scroll.get("continuity_reason")
                        or (
                            "following_list_end_reached"
                            if scroll.get("end_of_list_detected")
                            else "cursor_restore_scroll_not_validated"
                        )
                    )
                    break
                cursor_restore_fields["cursor_restore_scrolls"] = restore_index + 1
                if "rows_after" in scroll:
                    rows = list(scroll.get("rows_after") or [])
                    harvest_meta = dict(scroll.get("harvest_meta_after") or {})
                else:
                    rows, harvest_meta = harvest_visible_following_rows_for_unfollow(
                        d,
                        account_username=uname,
                    )
            log(
                "info",
                "unfollow_cursor_restore_completed",
                prior_depth=prior_depth,
                restore_limit=restore_limit,
                **cursor_restore_fields,
            )
            harvest_fields = {
                **harvest_fields,
                **_harvest_summary_fields(rows, harvest_meta, planned_usernames),
                **cursor_restore_fields,
            }

    if real_action_active:
        return _run_real_unfollow_multi_loop(
            d,
            aid=aid,
            uname=uname,
            run_id=run_id,
            settings=settings,
            base_summary=base_summary,
            planned_usernames=planned_usernames,
            planned_by_username=planned_by_username,
            rows=rows,
            harvest_meta=harvest_meta,
            harvest_fields=harvest_fields,
            visible_eligibility_row_cache=visible_eligibility_row_cache,
            real_action_max=real_action_max,
            unfollow_day_limit=db_unfollow_day_limit,
            effective_unfollows_done_at_start=effective_unfollows_done_at_start,
            business_action_deadline=resolved_deadline,
            adaptive_coverage_budget=handoff_budget,
            resume_checkpoint=resume_checkpoint,
            diagnostic_session=diagnostic_session,
            t0=t0,
        )

    visible_eval, visible_candidates_by_username, visible_eligibility_fields, any_mode_probe_active = (
        _evaluate_visible_rows_for_unfollow_probe(
            aid,
            uname,
            rows,
            settings=settings,
            row_cache=visible_eligibility_row_cache,
        )
    )
    harvest_fields = {
        **harvest_fields,
        **visible_eligibility_fields,
    }

    if real_action_active:
        selection_t0 = time.perf_counter()
        target_row, selection_reason = _select_visible_eligible_target_row(
            rows,
            visible_candidates_by_username,
        )
        harvest_fields["visible_target_selection_ms"] = round(
            (time.perf_counter() - selection_t0) * 1000.0,
            2,
        )
        if target_row is None:
            log(
                "info",
                "unfollow_real_no_visible_eligible_target",
                account_id=aid,
                account_username=uname,
                visible_plan_matches_count=harvest_fields["visible_plan_matches_count"],
                visible_eligible_matches_count=harvest_fields["visible_eligible_matches_count"],
                visible_eligibility_skip_counts=harvest_fields["visible_eligibility_skip_counts"],
                visible_match_source=harvest_fields["visible_match_source"],
            )
            summary = {
                **base_summary,
                **harvest_fields,
                "status": "no_visible_eligible_unfollow_target",
                "failure_reason": "",
                "total_ms": round((time.perf_counter() - t0) * 1000.0, 2),
            }
            _emit_summary(summary)
            return 0
        target_username = str(target_row.get("username") or "")
        log(
            "info",
            "unfollow_visible_eligible_target_selected",
            username=target_username,
            username_normalized=normalize_unfollow_username(target_username),
            row_index=int(target_row.get("row_index") or 0),
            selection_reason=selection_reason,
            visible_match_source=harvest_fields["visible_match_source"],
        )
        log(
            "info",
            "unfollow_real_target_selected",
            username=target_username,
            username_normalized=normalize_unfollow_username(target_username),
            row_index=int(target_row.get("row_index") or 0),
            selection_reason=selection_reason,
            row_cta_class=str(target_row.get("row_cta_class") or ""),
            cta_text=str(target_row.get("cta_text") or "")[:80],
        )
    else:
        if any_mode_probe_active:
            selection_t0 = time.perf_counter()
            target_row, selection_reason = _select_visible_any_target_row(
                rows,
                visible_candidates_by_username,
                completed_usernames=set(),
            )
            harvest_fields["visible_target_selection_ms"] = round(
                (time.perf_counter() - selection_t0) * 1000.0,
                2,
            )
            if target_row is None:
                summary = {
                    **base_summary,
                    **harvest_fields,
                    "status": "no_visible_eligible_unfollow_target",
                    "failure_reason": "unfollow_any_no_safe_candidate",
                    "total_ms": round((time.perf_counter() - t0) * 1000.0, 2),
                }
                _emit_summary(summary)
                return 0
        else:
            target_row, selection_reason = _select_probe_target_row(rows, planned_usernames)
        if target_row is None:
            summary = {
                **base_summary,
                **harvest_fields,
                "status": "failed_no_visible_probe_target",
                "failure_reason": "no_visible_following_rows_harvested",
                "total_ms": round((time.perf_counter() - t0) * 1000.0, 2),
            }
            _emit_summary(summary)
            return 1
        target_username = str(target_row.get("username") or "")
        if any_mode_probe_active:
            cand = visible_candidates_by_username.get(normalize_unfollow_username(target_username)) or {}
            log(
                "info",
                "unfollow_any_visible_candidate_selected",
                username=target_username,
                username_normalized=normalize_unfollow_username(target_username),
                row_index=int(target_row.get("row_index") or 0),
                selection_reason=selection_reason,
                row_cta_class=str(target_row.get("row_cta_class") or ""),
                interaction_row_id=str(cand.get("interaction_row_id") or ""),
            )
        log(
            "info",
            "unfollow_probe_target_row_selected",
            username=target_username,
            username_normalized=normalize_unfollow_username(target_username),
            row_index=int(target_row.get("row_index") or 0),
            selection_reason=selection_reason,
            row_cta_class=str(target_row.get("row_cta_class") or ""),
            cta_text=str(target_row.get("cta_text") or "")[:80],
        )

    tapped, tap_meta = tap_following_list_username_row_for_unfollow_probe(
        d,
        target_row,
        selection_reason=selection_reason,
    )
    target_fields = {
        **harvest_fields,
        "probe_target_username": target_username if not real_action_active else "",
        "probe_target_selection_reason": selection_reason if not real_action_active else "",
        "real_target_username": target_username if real_action_active else "",
    }

    if not tapped:
        summary = {
            **base_summary,
            **target_fields,
            "status": "failed_target_profile_open",
            "failure_reason": str(tap_meta.get("failure_reason") or "target_row_tap_failed"),
            "total_ms": round((time.perf_counter() - t0) * 1000.0, 2),
        }
        _emit_summary(summary)
        return 1

    profile_det = verify_unfollow_target_profile_strict(
        d,
        expected_target_username=target_username,
    )
    if not profile_det.get("ok"):
        ret_fn = (
            return_to_following_list_after_unfollow_action
            if real_action_active
            else return_to_following_list_after_unfollow_probe
        )
        ret = ret_fn(d, account_username=uname)
        summary = {
            **base_summary,
            **target_fields,
            "target_profile_open_ok": False,
            "return_to_following_list_ok": bool(ret.get("ok")),
            "status": "failed_target_profile_open",
            "failure_reason": str(profile_det.get("failure_reason") or "target_profile_open_failed"),
            "total_ms": round((time.perf_counter() - t0) * 1000.0, 2),
        }
        _emit_summary(summary)
        return 1

    sheet = open_unfollow_actions_sheet_from_profile_probe(
        d,
        expected_target_username=target_username,
        profile_exact_confirmed=True,
    )
    if not sheet.get("ok"):
        ret_fn = (
            return_to_following_list_after_unfollow_action
            if real_action_active
            else return_to_following_list_after_unfollow_probe
        )
        ret = ret_fn(d, account_username=uname)
        status = (
            "failed_following_button_not_found"
            if str(sheet.get("failure_reason") or "") in {
                "following_button_not_found",
                "following_cta_surface_not_stable",
                "following_cta_terminally_absent",
            }
            else "failed_actions_sheet_open"
        )
        summary = {
            **base_summary,
            **target_fields,
            "target_profile_open_ok": True,
            "following_actions_sheet_open_ok": False,
            "unfollow_option_visible": False,
            "return_to_following_list_ok": bool(ret.get("ok")),
            "status": status,
            "failure_reason": str(sheet.get("failure_reason") or "actions_sheet_open_failed"),
            "total_ms": round((time.perf_counter() - t0) * 1000.0, 2),
        }
        _emit_summary(summary)
        return 1

    unfollow_visible = bool(sheet.get("unfollow_option_visible"))
    if not unfollow_visible:
        ret = return_to_following_list_after_unfollow_probe(d, account_username=uname)
        summary = {
            **base_summary,
            **target_fields,
            "target_profile_open_ok": True,
            "following_actions_sheet_open_ok": True,
            "unfollow_option_visible": False,
            "return_to_following_list_ok": bool(ret.get("ok")),
            "status": "failed_unfollow_option_missing",
            "failure_reason": "unfollow_option_missing",
            "total_ms": round((time.perf_counter() - t0) * 1000.0, 2),
        }
        _emit_summary(summary)
        return 1

    if not real_action_active:
        ret = return_to_following_list_after_unfollow_probe(d, account_username=uname)
        return_ok = bool(ret.get("ok"))
        if not return_ok:
            status = "failed_return_to_following_list"
            failure_reason = str(ret.get("failure_reason") or "return_to_following_list_failed")
        else:
            status = "success_probe"
            failure_reason = ""
        summary = {
            **base_summary,
            **target_fields,
            "target_profile_open_ok": True,
            "following_actions_sheet_open_ok": True,
            "unfollow_option_visible": True,
            "return_to_following_list_ok": return_ok,
            "status": status,
            "failure_reason": failure_reason,
            "total_ms": round((time.perf_counter() - t0) * 1000.0, 2),
        }
        _emit_summary(summary)
        return 0 if status == "success_probe" else 1

    # Phase 2C: real Unfollow tap (max 1 per run; visible DB eligibility only).
    tap_out = tap_unfollow_in_following_sheet(d, target_username=target_username)
    actions_sent = 1 if tap_out.get("ok") else 0
    if not tap_out.get("ok"):
        ret = return_to_following_list_after_unfollow_action(d, account_username=uname)
        summary = {
            **base_summary,
            **target_fields,
            "target_profile_open_ok": True,
            "following_actions_sheet_open_ok": True,
            "unfollow_option_visible": True,
            "unfollow_actions_sent": actions_sent,
            "unfollow_actions_failed": 1,
            "return_to_following_list_ok": bool(ret.get("ok")),
            "status": "failed_unfollow_tap",
            "failure_reason": str(tap_out.get("failure_reason") or "unfollow_tap_failed"),
            "total_ms": round((time.perf_counter() - t0) * 1000.0, 2),
        }
        _emit_summary(summary)
        return 1

    verify_out = verify_unfollow_action_success_after_tap(d, target_username=target_username)
    verify_ok = bool(verify_out.get("ok"))
    target_key = normalize_unfollow_username(target_username)
    cand = (
        visible_candidates_by_username.get(target_key)
        or planned_by_username.get(target_key)
        or {}
    )
    interaction_row_id = str(cand.get("interaction_row_id") or cand.get("id") or "").strip() or None
    persist_out = _persist_unfollow_outcome_for_session(
        aid,
        target_username,
        run_id=run_id,
        settings=settings,
        verify_ok=verify_ok,
        interaction_row_id=interaction_row_id,
        failure_reason=str(verify_out.get("failure_reason") or tap_out.get("failure_reason") or ""),
    )
    persist_ok = bool(persist_out.get("ok"))
    persistence_delta = unfollow_persistence_count_delta(
        verify_ok=verify_ok,
        persist_ok=persist_ok,
    )
    unfollow_observed_successes: list[dict[str, Any]] = []
    if persist_ok:
        log(
            "info",
            "unfollow_result_persisted",
            account_id=aid,
            username=target_username,
            unfollow_ok=verify_ok,
            interaction_row_id=interaction_row_id,
        )
        if verify_ok:
            unfollow_observed_successes.append(
                _log_unfollow_success_observed(
                    account_id=aid,
                    username=target_username,
                    run_id=run_id,
                    persist_out=persist_out,
                    interaction_row_id=interaction_row_id,
                )
            )
    else:
        log(
            "info",
            "unfollow_result_persist_failed",
            account_id=aid,
            username=target_username,
            reason=str(persist_out.get("error") or "persist_failed"),
        )

    ret = return_to_following_list_after_unfollow_action(d, account_username=uname)
    return_ok = bool(ret.get("ok"))

    if not verify_ok:
        status = "failed_unfollow_verify"
        failure_reason = str(verify_out.get("failure_reason") or "unfollow_verify_failed")
    elif not persist_ok:
        status = "failed_unfollow_persistence"
        failure_reason = str(persist_out.get("error") or "unfollow_persistence_failed")
    elif not return_ok:
        status = "failed_return_to_following_list"
        failure_reason = str(ret.get("failure_reason") or "return_to_following_list_failed")
    else:
        status = "success_real_unfollow"
        failure_reason = ""

    summary = {
        **base_summary,
        **target_fields,
        "target_profile_open_ok": True,
        "following_actions_sheet_open_ok": True,
        "unfollow_option_visible": True,
        "unfollow_actions_sent": 1,
        "unfollow_actions_verified": 1 if verify_ok else 0,
        "unfollow_actions_failed": 0 if verify_ok else 1,
        "unfollow_results_persisted_count": persistence_delta["verified_persisted"],
        "unfollow_outcomes_persisted_count": persistence_delta["outcome_persisted"],
        "unfollow_action_verify_ok": verify_ok,
        "unfollow_persistence_ok": persist_ok,
        "unfollow_observed_success_count": len(unfollow_observed_successes),
        "unfollow_observed_success_usernames": [
            str(item.get("username") or "")
            for item in unfollow_observed_successes
            if str(item.get("username") or "").strip()
        ],
        "unfollow_observed_successes": unfollow_observed_successes,
        "return_to_following_list_ok": return_ok,
        "status": status,
        "failure_reason": failure_reason,
        "total_ms": round((time.perf_counter() - t0) * 1000.0, 2),
    }
    _emit_summary(summary)
    return 0 if status == "success_real_unfollow" else 1


def dispatch_unfollow_session(
    d: u2.Device,
    *,
    account_id: str,
    account_username: str,
    run_id: str | None = None,
    request_id: str | None = None,
    business_session_id: str | None = None,
    worker_sha: str | None = None,
    session_attempt: int = 1,
) -> int:
    # Real Unfollow is gated by UNFOLLOW_SESSION_REAL_ACTION_ENABLED (+ unfollow_enabled).
    return run_unfollow_session(
        d,
        account_id=account_id,
        account_username=account_username,
        run_id=run_id,
        request_id=request_id,
        business_session_id=business_session_id,
        worker_sha=worker_sha,
        session_attempt=session_attempt,
        dry_probe_only=False,
    )
