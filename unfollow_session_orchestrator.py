"""Unfollow session orchestrator.

Phase 2A is a non-destructive probe:
settings -> DB plan -> own profile -> own Following -> visible row harvest -> summary.
No Unfollow row CTA is tapped here.
"""

from __future__ import annotations

import time
from typing import Any

import uiautomator2 as u2

from account_identity_guard import verify_active_instagram_account_matches_expected
from logs import log
from own_following_navigation import (
    detect_own_following_list_screen,
    open_own_following_list_from_own_profile,
)
from unfollow_eligibility_engine import plan_unfollow_targets
from unfollow_list_harvest import (
    harvest_visible_following_rows_for_unfollow,
    normalize_unfollow_username,
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


def run_unfollow_session(
    d: u2.Device,
    *,
    account_id: str,
    account_username: str,
    run_id: str | None = None,
    dry_probe_only: bool = True,
) -> int:
    """Run the Phase 2A Unfollow Following-surface probe."""
    t0 = time.perf_counter()
    aid = str(account_id or "").strip()
    uname = str(account_username or "").strip()
    probe_only = bool(dry_probe_only)

    log(
        "info",
        "unfollow_session_started",
        account_id=aid,
        account_username=uname,
        run_id=run_id,
        probe_only=probe_only,
        unfollow_actions_sent=0,
    )

    settings = load_unfollow_settings(aid, ensure_row=False)
    plan = plan_unfollow_targets(aid, settings=settings)
    planned_usernames = _planned_username_set(plan)

    base_summary: dict[str, Any] = {
        "account_id": aid,
        "account_username": uname,
        "run_id": run_id,
        "unfollow_mode": settings.mode,
        "unfollow_after_days": settings.after_days,
        "plan_reason": str(plan.get("plan_reason") or ""),
        "candidates_planned_count": int(plan.get("candidates_count") or 0),
        "following_surface_ok": False,
        "visible_rows_count": 0,
        "row_cta_counts": {},
        "visible_plan_matches_count": 0,
        "visible_plan_matches_usernames": [],
        "probe_only": True,
        "unfollow_actions_sent": 0,
    }

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

    rows, harvest_meta = harvest_visible_following_rows_for_unfollow(d, account_username=uname)
    visible_rows_count = len(rows)
    row_cta_counts = dict(harvest_meta.get("row_cta_counts") or {})
    visible_matches = [
        str(row.get("username") or "")
        for row in rows
        if normalize_unfollow_username(str(row.get("username_normalized") or row.get("username") or ""))
        in planned_usernames
    ]

    status = "success" if visible_rows_count > 0 else "failed_harvest"
    summary = {
        **base_summary,
        "following_surface_ok": True,
        "visible_rows_count": visible_rows_count,
        "row_cta_counts": row_cta_counts,
        "visible_plan_matches_count": len(visible_matches),
        "visible_plan_matches_usernames": visible_matches[:50],
        "status": status,
        "failure_reason": "" if status == "success" else "no_visible_following_rows_harvested",
        "total_ms": round((time.perf_counter() - t0) * 1000.0, 2),
    }
    _emit_summary(summary)
    return 0 if status == "success" else 1


def dispatch_unfollow_session(
    d: u2.Device,
    *,
    account_id: str,
    account_username: str,
    run_id: str | None = None,
) -> int:
    return run_unfollow_session(
        d,
        account_id=account_id,
        account_username=account_username,
        run_id=run_id,
        dry_probe_only=True,
    )
