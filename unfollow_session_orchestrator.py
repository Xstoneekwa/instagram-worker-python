"""Unfollow session orchestrator.

Phase 2A/2B: non-destructive probe through sheet detection.
Phase 2C: optional real Unfollow action behind explicit config/env opt-in.
"""

from __future__ import annotations

import time
from typing import Any

import uiautomator2 as u2

import config
import supabase_client
from account_identity_guard import verify_active_instagram_account_matches_expected
from logs import log
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

_LAST_UNFOLLOW_SESSION_PROBE_SUMMARY: dict[str, Any] = {}


def get_last_unfollow_session_probe_summary() -> dict[str, Any]:
    return dict(_LAST_UNFOLLOW_SESSION_PROBE_SUMMARY)


def _emit_summary(summary: dict[str, Any]) -> None:
    global _LAST_UNFOLLOW_SESSION_PROBE_SUMMARY
    _LAST_UNFOLLOW_SESSION_PROBE_SUMMARY = dict(summary)
    log("info", "unfollow_session_probe_summary", **summary)


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


def _real_action_enabled() -> bool:
    return bool(getattr(config, "UNFOLLOW_SESSION_REAL_ACTION_ENABLED", False))


def _real_action_max_per_run() -> int:
    return max(0, int(getattr(config, "UNFOLLOW_SESSION_REAL_ACTION_MAX_PER_RUN", 1)))


def _scroll_max_passes() -> int:
    return max(0, int(getattr(config, "UNFOLLOW_SESSION_SCROLL_MAX_PASSES", 10)))


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
    return max(0, int(getattr(config, "UNFOLLOW_SESSION_MAX_RECOVERABLE_ACTION_FAILURES", 2)))


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
        "unfollow_actions_sent": 0,
        "unfollow_actions_verified": 0,
        "unfollow_actions_failed": 0,
        "unfollow_results_persisted_count": 0,
        "unfollow_action_verify_ok": False,
        "unfollow_persistence_ok": False,
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
        "row_cta_follow": 0,
        "row_cta_follow_back": 0,
        "whitelist": 0,
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
        elif isinstance(db_row, dict) and bool(db_row.get("whitelist_protected")):
            reject_reason = "whitelist"

        if reject_reason:
            skip_counts[reject_reason] = int(skip_counts.get(reject_reason, 0)) + 1
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
        elif isinstance(db_row, dict) and db_row.get("unfollowed_at"):
            log(
                "info",
                "unfollow_any_visible_following_but_db_already_unfollowed",
                username=username or key,
                username_normalized=key,
                row_index=row_index,
                row_cta_class=row_cta_class,
                interaction_row_id=interaction_row_id,
                unfollowed_at=db_row.get("unfollowed_at"),
            )

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


def _scroll_following_list_for_unfollow(d: u2.Device, *, account_username: str) -> dict[str, Any]:
    v2_enabled = _scroll_v2_lite_enabled()
    strategy = "v2_lite" if v2_enabled else "legacy"
    settle_s = _scroll_v2_lite_settle_s() if v2_enabled else 0.55
    try:
        w, h = d.window_size()
    except Exception:
        w, h = 1080, 2400
    x = int(w * 0.50)
    if v2_enabled:
        distance_ratio = _scroll_v2_lite_distance_ratio()
        y_start_ratio = 0.86
        y_end_ratio = max(0.10, y_start_ratio - distance_ratio)
        distance_ratio = y_start_ratio - y_end_ratio
        y_start = int(h * y_start_ratio)
        y_end = int(h * y_end_ratio)
        log(
            "info",
            "unfollow_scroll_v2_lite_started",
            scroll_strategy=strategy,
            scroll_distance_ratio=round(float(y_start_ratio - y_end_ratio), 4),
            scroll_settle_s=round(float(settle_s), 3),
            tap_x=x,
            start_y=y_start,
            end_y=y_end,
        )
    else:
        y_start = int(h * 0.78)
        y_end = int(h * 0.36)
        distance_ratio = 0.42
    out = {
        "ok": False,
        "failure_reason": "",
        "tap_x": x,
        "start_y": y_start,
        "end_y": y_end,
        "scroll_strategy": strategy,
        "scroll_v2_lite_enabled": v2_enabled,
        "scroll_distance_ratio": round(float(distance_ratio), 4),
        "scroll_settle_s": round(float(settle_s), 3),
        "scroll_duration_ms": 0.0,
    }
    scroll_t0 = time.perf_counter()
    try:
        d.swipe(x, y_start, x, y_end, 0.10)
    except Exception as exc:
        out["failure_reason"] = "swipe_failed"
        out["error"] = str(exc)[:200]
        out["scroll_duration_ms"] = round((time.perf_counter() - scroll_t0) * 1000.0, 2)
        if v2_enabled:
            log("info", "unfollow_scroll_v2_lite_fallback", **out)
        return out
    time.sleep(settle_s)
    out["scroll_duration_ms"] = round((time.perf_counter() - scroll_t0) * 1000.0, 2)
    det = detect_own_following_list_screen(d, account_username=account_username)
    out["surface_detection"] = det
    out["surface_ok_after_scroll"] = bool(det.get("is_following_list"))
    out["end_of_list_detected"] = bool(det.get("following_list_end_detected"))
    if not det.get("is_following_list"):
        out["failure_reason"] = str(det.get("failure_reason") or "following_surface_lost_after_scroll")
        if v2_enabled:
            log("info", "unfollow_scroll_v2_lite_fallback", **out)
        return out
    out["ok"] = True
    if v2_enabled:
        log("info", "unfollow_scroll_v2_lite_completed", **out)
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
    t0: float,
) -> int:
    verified = 0
    sent = 0
    failed = 0
    persisted = 0
    scroll_passes_used = 0
    scroll_stop_reason = ""
    stop_reason = ""
    completed_usernames: set[str] = set()
    failed_usernames_this_run: set[str] = set()
    recoverable_action_failure_usernames: list[str] = []
    recoverable_action_failure_reasons: dict[str, str] = {}
    recoverable_action_failures_count = 0
    session_continued_after_recoverable_failure = False
    max_recoverable_action_failures = _max_recoverable_action_failures()
    any_mode_selected_count = 0
    last_fields = dict(harvest_fields)
    any_mode_active = str(getattr(settings, "mode", "") or "") == UNFOLLOW_MODE_ANY
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
    }
    max_scroll_passes = _scroll_max_passes()
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
            "plan_guided_mode": "instrumentation_only",
        }

    def refresh_recoverable_action_summary_totals() -> None:
        totals["recoverable_action_failures_count"] = recoverable_action_failures_count
        totals["recoverable_action_failure_usernames"] = recoverable_action_failure_usernames[:50]
        totals["recoverable_action_failure_reasons"] = dict(recoverable_action_failure_reasons)
        totals["max_recoverable_action_failures"] = max_recoverable_action_failures
        totals["session_continued_after_recoverable_failure"] = session_continued_after_recoverable_failure

    def is_recoverable_action_sheet_failure(sheet_out: dict[str, Any], *, return_ok: bool) -> bool:
        reason = str(sheet_out.get("failure_reason") or "").strip()
        retry_reason = str(sheet_out.get("retry_failure_reason") or "").strip()
        recoverable_reasons = {
            "actions_sheet_signals_missing",
            "actions_sheet_signals_missing_after_retry",
            "sheet_not_opened",
            "actions_sheet_not_opened",
        }
        return bool(
            return_ok
            and not bool(sheet_out.get("ok"))
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
        summary = {
            **base_summary,
            **last_fields,
            **totals,
            **exploration_summary,
            "multi_action_mode": True,
            "real_action_max_per_run": real_action_max,
            "unfollow_actions_sent": sent,
            "unfollow_actions_verified": verified,
            "unfollow_actions_failed": failed,
            "unfollow_results_persisted_count": persisted,
            "scroll_passes_used": scroll_passes_used,
            "scroll_stop_reason": scroll_stop_reason,
            "multi_action_stop_reason": exploration_stop,
            "status": status,
            "failure_reason": failure_reason,
            "total_ms": round((time.perf_counter() - t0) * 1000.0, 2),
        }
        _emit_summary(summary)
        return 0 if not status.startswith("failed_") else 1

    log(
        "info",
        "unfollow_multi_action_loop_started",
        account_id=aid,
        account_username=uname,
        real_action_max_per_run=real_action_max,
        scroll_max_passes=max_scroll_passes,
    )
    log(
        "info",
        "unfollow_exploration_v2_started",
        scroll_passes_used=scroll_passes_used,
        **exploration_fields(),
    )

    iteration_index = 0
    while verified < real_action_max:
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

            if max_minutes_reached():
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

            if scroll_passes_used >= max_scroll_passes:
                scroll_stop_reason = "scroll_budget_exhausted"
                stop_reason = "eligible_targets_exhausted"
                log(
                    "info",
                    "unfollow_multi_action_loop_completed",
                    stop_reason=stop_reason,
                    scroll_stop_reason=scroll_stop_reason,
                    unfollow_actions_verified_so_far=verified,
                    real_action_max_per_run=real_action_max,
                )
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
            scroll = _scroll_following_list_for_unfollow(d, account_username=uname)
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
                return emit_final("failed_unfollow_multi_action", scroll_stop_reason)
            scroll_passes_used += 1
            rows, harvest_meta = harvest_visible_following_rows_for_unfollow(d, account_username=uname)
            after_scroll_keys = _visible_username_keys(
                [str(row.get("username") or "") for row in rows]
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
            if bool(harvest_meta.get("following_list_end_detected")) and (
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
                after_scroll_keys
                and after_scroll_keys == before_scroll_keys
                and unchanged_scroll_streak >= unchanged_stop_threshold
            ):
                scroll_stop_reason = "end_of_list_or_no_new_rows_detected"
                stop_reason = "eligible_targets_exhausted"
                log(
                    "info",
                    "unfollow_multi_action_loop_completed",
                    stop_reason=stop_reason,
                    scroll_stop_reason=scroll_stop_reason,
                    unfollow_actions_verified_so_far=verified,
                    real_action_max_per_run=real_action_max,
                    scroll_passes_used=scroll_passes_used,
                )
                status = (
                    "success_real_unfollow_multi_partial_exhausted"
                    if verified > 0
                    else "no_visible_eligible_unfollow_target"
                )
                return emit_final(status)

        target_username = str(target_row.get("username") or "")
        target_key = normalize_unfollow_username(target_username)
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

        target_fields = {
            **last_fields,
            "probe_target_username": "",
            "probe_target_selection_reason": "",
            "real_target_username": target_username,
        }
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

        profile_det = verify_unfollow_target_profile_strict(
            d,
            expected_target_username=target_username,
        )
        if not profile_det.get("ok"):
            failed += 1
            stop_reason = "target_profile_open_failed"
            ret = return_to_following_list_after_unfollow_action(d, account_username=uname)
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
        )
        if not sheet.get("ok"):
            failed += 1
            ret = return_to_following_list_after_unfollow_action(d, account_username=uname)
            return_ok = bool(ret.get("ok"))
            sheet_failure_reason = str(sheet.get("failure_reason") or "actions_sheet_open_failed")
            recoverable = is_recoverable_action_sheet_failure(sheet, return_ok=return_ok)
            log(
                "info",
                "unfollow_recoverable_action_failure_detected",
                username=target_username,
                username_normalized=target_key,
                failure_reason=sheet_failure_reason,
                retry_failure_reason=str(sheet.get("retry_failure_reason") or ""),
                return_to_following_list_ok=return_ok,
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
            if recoverable and recoverable_action_failures_count < max_recoverable_action_failures:
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
                )
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
            stop_reason = "actions_sheet_open_failed"
            return emit_final(
                "failed_unfollow_multi_action",
                str(sheet.get("failure_reason") or "actions_sheet_open_failed"),
            )
        if not bool(sheet.get("unfollow_option_visible")):
            failed += 1
            stop_reason = "unfollow_option_missing"
            ret = return_to_following_list_after_unfollow_action(d, account_username=uname)
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
        else:
            failed += 1
            stop_reason = "unfollow_tap_failed"
            ret = return_to_following_list_after_unfollow_action(d, account_username=uname)
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
            failure_reason=str(verify_out.get("failure_reason") or tap_out.get("failure_reason") or ""),
        )
        persist_ok = bool(persist_out.get("ok"))
        if persist_ok:
            persisted += 1
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
            failed += 1
            stop_reason = "unfollow_verify_failed"
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
                "return_to_following_list_ok": return_ok,
            }
            return emit_final(
                "failed_unfollow_multi_action",
                str(verify_out.get("failure_reason") or "unfollow_verify_failed"),
            )
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

        verified += 1
        completed_usernames.add(target_key)
        visible_eligibility_row_cache[target_key] = None
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
        rows, harvest_meta = harvest_visible_following_rows_for_unfollow(d, account_username=uname)

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
    real_action_max = (
        _real_action_max_per_run()
        if real_action_max_override is None
        else max(0, int(real_action_max_override))
    )

    settings = load_unfollow_settings(aid, ensure_row=False)
    plan = plan_unfollow_targets(aid, settings=settings)
    planned_usernames = _planned_username_set(plan)
    planned_by_username = _planned_candidates_by_username(plan)
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
            t0=t0,
        )

    visible_usernames = [str(row.get("username") or "") for row in rows]
    visible_eval = _evaluate_visible_unfollow_with_session_cache(
        aid,
        visible_usernames,
        settings=settings,
        row_cache=visible_eligibility_row_cache,
    )
    visible_candidates_by_username = _visible_candidates_by_username(visible_eval)
    visible_eligibility_fields = _visible_eligibility_summary_fields(visible_eval)
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
            if str(sheet.get("failure_reason") or "") == "following_button_not_found"
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
    if persist_ok:
        log(
            "info",
            "unfollow_result_persisted",
            account_id=aid,
            username=target_username,
            unfollow_ok=verify_ok,
            interaction_row_id=interaction_row_id,
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
        "unfollow_results_persisted_count": 1 if persist_ok else 0,
        "unfollow_action_verify_ok": verify_ok,
        "unfollow_persistence_ok": persist_ok,
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
) -> int:
    # Real Unfollow is gated by UNFOLLOW_SESSION_REAL_ACTION_ENABLED (+ unfollow_enabled).
    return run_unfollow_session(
        d,
        account_id=account_id,
        account_username=account_username,
        run_id=run_id,
        dry_probe_only=False,
    )
