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
from unfollow_settings import load_unfollow_settings

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


def _base_session_summary(
    *,
    aid: str,
    uname: str,
    run_id: str | None,
    settings: Any,
    plan: dict[str, Any],
    probe_only: bool,
) -> dict[str, Any]:
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
        "probe_target_username": "",
        "probe_target_selection_reason": "",
        "real_target_username": "",
        "target_profile_open_ok": False,
        "following_actions_sheet_open_ok": False,
        "unfollow_option_visible": False,
        "return_to_following_list_ok": False,
        "probe_only": probe_only,
        "real_action_enabled": _real_action_enabled(),
        "real_action_max_per_run": _real_action_max_per_run(),
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


def run_unfollow_session(
    d: u2.Device,
    *,
    account_id: str,
    account_username: str,
    run_id: str | None = None,
    dry_probe_only: bool = True,
) -> int:
    """Run unfollow_session: probe by default; real Unfollow only with explicit config opt-in."""
    t0 = time.perf_counter()
    aid = str(account_id or "").strip()
    uname = str(account_username or "").strip()
    config_real_enabled = _real_action_enabled()
    real_action_max = _real_action_max_per_run()

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
    persist_out = supabase_client.record_unfollow_interaction_outcome(
        aid,
        target_username,
        run_id=run_id,
        unfollow_ok=verify_ok,
        unfollow_mode_applied=str(settings.mode or ""),
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
