"""
V4.7 — Account session: optional Welcome DM block → Followers list engine (Follow/Likes).

Run type: account_session (wired from runner.py).
dm_welcome_session_send remains a standalone diagnostic run type.
"""

from __future__ import annotations

import time
from typing import Any, Callable

import uiautomator2 as u2

import config
import supabase_client
from device import app_start, press_home
from dm_follow_handoff import HandoffResult, prepare_dm_to_follow_handoff
from dm_sender_engine import _resolve_dm_sender_real_send_enabled
from instagram_navigation import verify_app_foreground
from logs import log
from own_profile_navigation import open_own_profile_from_bottom_nav, verify_own_profile
from unfollow_session_orchestrator import (
    get_last_unfollow_session_probe_summary,
    run_unfollow_session,
)
from unfollow_eligibility_engine import plan_unfollow_targets
from unfollow_settings import UNFOLLOW_MODES_DB_STRICT, load_unfollow_settings
from welcome_list_sender import get_last_welcome_list_sender_summary
from welcome_scan_producer import get_last_welcome_scan_summary
from welcome_session_orchestrator import dispatch_welcome_session_send

FollowEngineRunner = Callable[..., int]


def _welcome_session_status_label(
    scan_code: int,
    sender_code: int,
    *,
    sender_blocked: bool,
    sender_status: str,
) -> str:
    if sender_blocked:
        return "failed"
    ss = str(sender_status or "").strip()
    if ss == "partial_success":
        return "partial_success"
    if ss in ("failed", "blocked_disabled"):
        return "failed"
    if ss == "success" and scan_code == 0:
        return "success"
    if scan_code != 0 and sender_code != 0:
        return "failed"
    if scan_code != 0 or sender_code != 0:
        return "partial_success"
    return "success"


def _should_run_follow_after_welcome(
    *,
    welcome_enabled: bool,
    real_send_enabled: bool,
    welcome_phase_executed: bool,
    welcome_exit_code: int,
    scan_summary: dict[str, Any],
    sender_summary: dict[str, Any],
    welcome_session_status: str,
) -> tuple[bool, str]:
    if not welcome_enabled:
        return True, "welcome_disabled_bypass"

    if not real_send_enabled:
        return False, "welcome_real_send_disabled"

    if not welcome_phase_executed:
        return False, "welcome_sender_failed"

    scan_status = str(scan_summary.get("status") or "")
    if scan_status == "failed":
        return False, "welcome_scan_failed"

    failure_reason = str(sender_summary.get("failure_reason") or "")
    if failure_reason.startswith("followers_surface"):
        return False, "welcome_surface_unstable"

    jobs_failed = int(sender_summary.get("jobs_failed_count") or 0)
    if jobs_failed > 0:
        return False, "welcome_sender_failed_jobs"

    sender_status = str(sender_summary.get("sender_status") or "")
    if sender_status == "failed":
        return False, "welcome_sender_failed"

    if welcome_session_status == "success":
        return True, "welcome_closed_success"

    if welcome_session_status == "partial_success" and jobs_failed == 0:
        return True, "welcome_closed_with_expected_skips"

    if welcome_session_status == "failed":
        return False, "welcome_sender_failed"

    return False, "welcome_sender_failed"


def _account_session_status(
    *,
    transition_reason: str,
    follow_phase_executed: bool,
    follow_exit_code: int | None,
    welcome_blocked_follow: bool,
) -> str:
    if transition_reason == "welcome_real_send_disabled":
        return "failed"
    if welcome_blocked_follow:
        return "failed"
    if not follow_phase_executed:
        return "failed"
    if follow_exit_code is None:
        return "failed"
    if follow_exit_code in (0, 97, 98):
        return "success"
    return "failed"


def _follow_exit_handoff_gate(follow_exit_code: int | None) -> tuple[bool, str]:
    if follow_exit_code == 0:
        return True, "follow_completed"
    if follow_exit_code == 97:
        return True, "follow_partial_safe_stop_probe_candidate"
    if follow_exit_code == 98:
        return False, "follow_exit_code_not_allowed"
    if follow_exit_code is None:
        return False, "follow_not_executed"
    return False, "follow_exit_code_not_allowed"


def _run_follow_to_unfollow_handoff_diagnostic(
    *,
    account_id: str,
    account_username: str,
    run_id: str | None,
    followers_source_username: str,
    follow_phase_executed: bool,
    follow_exit_code: int | None,
    follow_total_ms: float,
    session_started_at: float,
) -> dict[str, Any]:
    """H1 only: DB/settings diagnostic, no Unfollow dispatch and no UI navigation."""
    diag_t0 = time.perf_counter()
    aid = str(account_id or "").strip()
    uname = str(account_username or "").strip()
    src = str(followers_source_username or "").strip()
    session_elapsed_ms = (time.perf_counter() - float(session_started_at)) * 1000.0

    log(
        "info",
        "follow_to_unfollow_handoff_diagnostic_started",
        account_id=aid,
        account_username=uname,
        run_id=run_id,
        followers_source_username=src or None,
        previous_phase="follow",
        follow_phase_executed=bool(follow_phase_executed),
        follow_exit_code=follow_exit_code,
        follow_engine_exit_code=follow_exit_code,
        follow_total_ms=round(float(follow_total_ms), 2),
    )

    summary: dict[str, Any] = {
        "status": "completed",
        "account_id": aid,
        "account_username": uname,
        "run_id": run_id,
        "followers_source_username": src or None,
        "follow_phase_executed": bool(follow_phase_executed),
        "follow_exit_code": follow_exit_code,
        "follow_engine_exit_code": follow_exit_code,
        "follow_total_ms": round(float(follow_total_ms), 2),
        "previous_phase": "follow",
        "diagnostic_only": True,
        "handoff_would_run": False,
        "would_launch_unfollow": False,
        "handoff_decision": "would_skip_unfollow",
        "handoff_skip_reason": "",
        "pending_unfollow_count": 0,
        "pending_unfollow_count_scope": "probe_limit_1",
        "has_pending_unfollow": False,
        "unfollow_enabled": False,
        "unfollow_mode": "",
        "unfollow_sort_mode": "",
        "unfollow_session_limit": 0,
        "unfollow_plan_reason": "",
        "plan_reason": "",
        "unfollow_plan_skipped_counts": {},
        "skipped_counts": {},
        "session_elapsed_ms": round(session_elapsed_ms, 2),
        "session_time_remaining_ms": None,
        "diagnostic_ms": 0.0,
    }

    skip_reasons: list[str] = []
    if not aid or not uname:
        skip_reasons.append("missing_account_context")
    follow_gate_ok, follow_gate_reason = _follow_exit_handoff_gate(follow_exit_code)
    if not follow_phase_executed:
        skip_reasons.append("follow_phase_not_executed")
    if not follow_gate_ok:
        skip_reasons.append(follow_gate_reason)

    try:
        settings = load_unfollow_settings(aid, ensure_row=False)
        plan = plan_unfollow_targets(
            aid,
            settings=settings,
            limit=1,
        )

        pending_unfollow_count = int(plan.get("candidates_count") or 0)
        plan_reason = str(plan.get("plan_reason") or "")
        mode = str(settings.mode or "")

        summary.update(
            {
                "pending_unfollow_count": pending_unfollow_count,
                "has_pending_unfollow": pending_unfollow_count > 0,
                "unfollow_enabled": bool(settings.enabled),
                "unfollow_mode": mode,
                "unfollow_sort_mode": str(settings.sort_mode or ""),
                "unfollow_session_limit": int(settings.session_limit),
                "unfollow_plan_reason": plan_reason,
                "plan_reason": plan_reason,
                "unfollow_plan_skipped_counts": dict(plan.get("skipped_counts") or {}),
                "skipped_counts": dict(plan.get("skipped_counts") or {}),
            }
        )

        if not bool(settings.enabled):
            skip_reasons.append("unfollow_disabled")
        if mode not in UNFOLLOW_MODES_DB_STRICT:
            skip_reasons.append(
                "unfollow_mode_ui_dependent"
                if mode.startswith("unfollow-any")
                else "unfollow_mode_not_supported_offline"
            )
        if pending_unfollow_count <= 0:
            skip_reasons.append("no_pending_unfollow")

        unique_skip_reasons: list[str] = []
        for reason in skip_reasons:
            reason_s = str(reason or "").strip()
            if reason_s and reason_s not in unique_skip_reasons:
                unique_skip_reasons.append(reason_s)

        would_launch = not unique_skip_reasons
        summary.update(
            {
                "handoff_would_run": bool(would_launch),
                "would_launch_unfollow": bool(would_launch),
                "handoff_decision": (
                    "would_launch_unfollow" if would_launch else "would_skip_unfollow"
                ),
                "handoff_skip_reason": (
                    "" if would_launch else (unique_skip_reasons[0] if unique_skip_reasons else "unknown")
                ),
                "handoff_skip_reasons": unique_skip_reasons,
            }
        )
    except Exception as e:
        summary.update(
            {
                "status": "failed",
                "handoff_decision": "would_skip_unfollow",
                "handoff_skip_reason": "pending_count_failed",
                "handoff_skip_reasons": ["pending_count_failed"],
                "diagnostic_error": str(e),
            }
        )

    summary["diagnostic_ms"] = round((time.perf_counter() - diag_t0) * 1000.0, 2)
    log("info", "follow_to_unfollow_handoff_diagnostic_completed", **summary)
    return summary


def _follow_to_unfollow_probe_enabled() -> bool:
    return bool(getattr(config, "ACCOUNT_SESSION_FOLLOW_TO_UNFOLLOW_PROBE_ENABLED", False))


def _current_package(d: u2.Device) -> str:
    try:
        return str((d.app_current() or {}).get("package") or "")
    except Exception:
        return ""


def _prepare_follow_to_unfollow_probe_surface(
    d: u2.Device,
    *,
    account_id: str,
    account_username: str,
    run_id: str | None,
) -> dict[str, Any]:
    """
    H2-only surface prep before the Unfollow probe.

    This intentionally does not open Following; the Unfollow orchestrator keeps
    ownership of identity guard, own profile navigation, Following open, and harvest.
    """
    t0 = time.perf_counter()
    aid = str(account_id or "").strip()
    uname = str(account_username or "").strip()
    pkg = str(getattr(config, "INSTAGRAM_PACKAGE", "") or "com.instagram.android")
    method = "home_app_start_own_profile"
    out: dict[str, Any] = {
        "surface_prep_attempted": True,
        "surface_prep_ok": False,
        "surface_prep_method": method,
        "surface_prep_ms": 0.0,
        "surface_prep_failure_reason": "",
        "current_package": _current_package(d),
        "own_profile_verified": False,
    }

    def _finish(ok: bool, failure_reason: str = "") -> dict[str, Any]:
        out["surface_prep_ok"] = bool(ok)
        out["surface_prep_failure_reason"] = str(failure_reason or "")
        out["current_package"] = _current_package(d)
        out["surface_prep_ms"] = round((time.perf_counter() - t0) * 1000.0, 2)
        log(
            "info",
            "follow_to_unfollow_probe_surface_prep_completed",
            account_id=aid,
            account_username=uname,
            run_id=run_id,
            ok=out["surface_prep_ok"],
            method=out["surface_prep_method"],
            duration_ms=out["surface_prep_ms"],
            current_package=out["current_package"] or None,
            failure_reason=out["surface_prep_failure_reason"] or None,
            own_profile_verified=out["own_profile_verified"],
        )
        if not ok:
            log(
                "error",
                "follow_to_unfollow_probe_surface_prep_failed",
                account_id=aid,
                account_username=uname,
                run_id=run_id,
                method=out["surface_prep_method"],
                duration_ms=out["surface_prep_ms"],
                current_package=out["current_package"] or None,
                failure_reason=out["surface_prep_failure_reason"] or "surface_prep_failed",
            )
        return out

    log(
        "info",
        "follow_to_unfollow_probe_surface_prep_started",
        account_id=aid,
        account_username=uname,
        run_id=run_id,
        method=method,
        current_package=out["current_package"] or None,
    )

    try:
        press_home(d)
        time.sleep(0.3)
        app_start(d, pkg)
        settle_s = min(float(getattr(config, "APP_START_WAIT_S", 2.5) or 2.5), 2.5)
        if settle_s > 0:
            time.sleep(settle_s)

        if not verify_app_foreground(d, pkg):
            return _finish(False, "instagram_not_foreground_after_app_start")

        if not open_own_profile_from_bottom_nav(d):
            return _finish(False, "own_profile_open_failed")

        verified, meta = verify_own_profile(d, uname)
        out["own_profile_verified"] = bool(verified)
        out["own_profile_meta"] = meta
        if not verified:
            return _finish(False, "own_profile_verify_failed")

        return _finish(True)
    except Exception as e:
        out["surface_prep_exception"] = str(e)
        return _finish(False, "surface_prep_exception")


def _probe_summary_from_unfollow_summary(
    *,
    enabled: bool,
    executed: bool,
    probe_only: bool,
    exit_code: int | None,
    unfollow_summary: dict[str, Any],
    skip_reason: str = "",
) -> dict[str, Any]:
    actions_sent = int(unfollow_summary.get("unfollow_actions_sent") or 0)
    return {
        "enabled": bool(enabled),
        "executed": bool(executed),
        "probe_only": bool(probe_only),
        "status": str(unfollow_summary.get("status") or ""),
        "exit_code": exit_code,
        "following_surface_ok": bool(unfollow_summary.get("following_surface_ok")),
        "visible_rows_count": int(unfollow_summary.get("visible_rows_count") or 0),
        "visible_plan_matches_count": int(
            unfollow_summary.get("visible_plan_matches_count") or 0
        ),
        "unfollow_actions_sent": actions_sent,
        "unfollow_actions_verified": int(unfollow_summary.get("unfollow_actions_verified") or 0),
        "failure_reason": str(unfollow_summary.get("failure_reason") or ""),
        "total_ms": float(unfollow_summary.get("total_ms") or 0.0),
        "skip_reason": str(skip_reason or ""),
    }


def _skip_follow_to_unfollow_probe(
    *,
    account_id: str,
    account_username: str,
    run_id: str | None,
    probe_enabled: bool,
    skip_reason: str,
    diagnostic: dict[str, Any],
) -> dict[str, Any]:
    summary = {
        "enabled": bool(probe_enabled),
        "executed": False,
        "probe_only": True,
        "skip_reason": str(skip_reason or "probe_skipped"),
        "status": "skipped",
        "unfollow_actions_sent": 0,
        "unfollow_actions_verified": 0,
    }
    log(
        "info",
        "follow_to_unfollow_handoff_probe_skipped",
        account_id=account_id,
        account_username=account_username,
        run_id=run_id,
        skip_reason=summary["skip_reason"],
        handoff_would_run=bool(diagnostic.get("handoff_would_run")),
        probe_enabled=bool(probe_enabled),
        unfollow_enabled=bool(diagnostic.get("unfollow_enabled")),
        unfollow_mode=str(diagnostic.get("unfollow_mode") or ""),
        has_pending_unfollow=bool(diagnostic.get("has_pending_unfollow")),
    )
    return summary


def _run_follow_to_unfollow_probe(
    d: u2.Device,
    *,
    account_id: str,
    account_username: str,
    run_id: str | None,
    follow_exit_code: int | None,
    follow_total_ms: float,
    diagnostic: dict[str, Any],
) -> dict[str, Any]:
    """H2 only: forced Unfollow probe. Never dispatches real Unfollow."""
    t0 = time.perf_counter()
    aid = str(account_id or "").strip()
    uname = str(account_username or "").strip()
    mode = str(diagnostic.get("unfollow_mode") or "")
    pending_count = int(diagnostic.get("pending_unfollow_count") or 0)
    surface_prep: dict[str, Any] = {}

    log(
        "info",
        "follow_to_unfollow_handoff_probe_started",
        account_id=aid,
        account_username=uname,
        run_id=run_id,
        previous_phase="follow",
        follow_exit_code=follow_exit_code,
        follow_total_ms=round(float(follow_total_ms), 2),
        unfollow_mode=mode,
        pending_unfollow_count=pending_count,
        probe_only=True,
    )

    try:
        surface_prep = _prepare_follow_to_unfollow_probe_surface(
            d,
            account_id=aid,
            account_username=uname,
            run_id=run_id,
        )
        if not bool(surface_prep.get("surface_prep_ok")):
            out = {
                "enabled": True,
                "executed": False,
                "probe_only": True,
                "status": "skipped",
                "exit_code": None,
                "following_surface_ok": False,
                "visible_rows_count": 0,
                "visible_plan_matches_count": 0,
                "unfollow_actions_sent": 0,
                "unfollow_actions_verified": 0,
                "failure_reason": str(
                    surface_prep.get("surface_prep_failure_reason")
                    or "surface_prep_failed"
                ),
                "skip_reason": "surface_prep_failed",
                "total_ms": round((time.perf_counter() - t0) * 1000.0, 2),
                **surface_prep,
            }
            log(
                "info",
                "follow_to_unfollow_handoff_probe_skipped",
                account_id=aid,
                account_username=uname,
                run_id=run_id,
                skip_reason=out["skip_reason"],
                failure_reason=out["failure_reason"],
                probe_only=True,
                surface_prep_attempted=out.get("surface_prep_attempted"),
                surface_prep_ok=out.get("surface_prep_ok"),
                surface_prep_method=out.get("surface_prep_method"),
                surface_prep_ms=out.get("surface_prep_ms"),
                surface_prep_failure_reason=out.get("surface_prep_failure_reason"),
                unfollow_actions_sent=0,
            )
            return out

        # Protection H2: call the low-level probe API with dry_probe_only=True.
        exit_code = run_unfollow_session(
            d,
            account_id=aid,
            account_username=uname,
            run_id=run_id,
            dry_probe_only=True,
        )
        unfollow_summary = get_last_unfollow_session_probe_summary()
        out = _probe_summary_from_unfollow_summary(
            enabled=True,
            executed=True,
            probe_only=True,
            exit_code=int(exit_code),
            unfollow_summary=unfollow_summary,
        )
        out.update(surface_prep)
        out["total_ms"] = float(unfollow_summary.get("total_ms") or round((time.perf_counter() - t0) * 1000.0, 2))
        if int(out.get("unfollow_actions_sent") or 0) != 0:
            out["status"] = "failed_probe_actions_sent_nonzero"
            out["failure_reason"] = "probe_actions_sent_nonzero"
            log(
                "error",
                "follow_to_unfollow_handoff_probe_failed",
                account_id=aid,
                account_username=uname,
                run_id=run_id,
                probe_only=True,
                failure_reason=out["failure_reason"],
                unfollow_actions_sent=out.get("unfollow_actions_sent"),
            )
        log(
            "info",
            "follow_to_unfollow_handoff_probe_completed",
            account_id=aid,
            account_username=uname,
            run_id=run_id,
            probe_only=True,
            status=out.get("status"),
            exit_code=out.get("exit_code"),
            following_surface_ok=out.get("following_surface_ok"),
            visible_rows_count=out.get("visible_rows_count"),
            visible_plan_matches_count=out.get("visible_plan_matches_count"),
            unfollow_actions_sent=out.get("unfollow_actions_sent"),
            unfollow_actions_verified=out.get("unfollow_actions_verified"),
            failure_reason=out.get("failure_reason"),
            surface_prep_attempted=out.get("surface_prep_attempted"),
            surface_prep_ok=out.get("surface_prep_ok"),
            surface_prep_method=out.get("surface_prep_method"),
            surface_prep_ms=out.get("surface_prep_ms"),
            surface_prep_failure_reason=out.get("surface_prep_failure_reason"),
            total_ms=out.get("total_ms"),
        )
        return out
    except Exception as e:
        out = {
            "enabled": True,
            "executed": True,
            "probe_only": True,
            "status": "failed_exception",
            "exit_code": 1,
            "following_surface_ok": False,
            "visible_rows_count": 0,
            "visible_plan_matches_count": 0,
            "unfollow_actions_sent": 0,
            "unfollow_actions_verified": 0,
            "failure_reason": str(e),
            "total_ms": round((time.perf_counter() - t0) * 1000.0, 2),
            **surface_prep,
        }
        log(
            "error",
            "follow_to_unfollow_handoff_probe_failed",
            account_id=aid,
            account_username=uname,
            run_id=run_id,
            probe_only=True,
            status=out["status"],
            failure_reason=out["failure_reason"],
            surface_prep_attempted=out.get("surface_prep_attempted"),
            surface_prep_ok=out.get("surface_prep_ok"),
            surface_prep_method=out.get("surface_prep_method"),
            surface_prep_ms=out.get("surface_prep_ms"),
            surface_prep_failure_reason=out.get("surface_prep_failure_reason"),
            unfollow_actions_sent=0,
            total_ms=out["total_ms"],
        )
        return out


def run_account_session(
    d: u2.Device,
    *,
    account_id: str,
    account_username: str,
    run_id: str | None,
    source_profile_username: str,
    run_followers_list_engine_session: FollowEngineRunner,
    supabase_mode: bool,
    warm_session_used: bool,
    force_stop_used: bool,
) -> int:
    t0 = time.perf_counter()
    aid = str(account_id or "").strip()
    uname = str(account_username or "").strip()
    src = str(source_profile_username or "").strip()

    log(
        "info",
        "account_session_started",
        account_id=aid,
        account_username=uname,
        run_id=run_id,
        followers_source_username=src or None,
    )

    if not src:
        log("error", "account_session_aborted", reason="missing_followers_source_username")
        log(
            "info",
            "account_session_summary",
            account_id=aid,
            account_username=uname,
            run_id=run_id,
            total_ms=round((time.perf_counter() - t0) * 1000.0, 2),
            session_status="failed",
            transition_reason="missing_followers_source_username",
            welcome_enabled=False,
            welcome_phase_executed=False,
            follow_phase_executed=False,
            follow_phase_skipped_reason="missing_followers_source_username",
        )
        return 1

    settings: dict[str, Any] = {}
    try:
        settings = supabase_client.get_account_dm_settings(aid) or {}
    except Exception as e:
        log("error", "account_session_settings_load_failed", error=str(e))

    welcome_enabled = bool(settings.get("welcome_enabled"))
    real_send_enabled, real_send_source = _resolve_dm_sender_real_send_enabled()

    welcome_phase_executed = False
    welcome_bypass_reason: str | None = None
    welcome_exit_code = 0
    welcome_session_status = "skipped"
    welcome_t0 = welcome_t1 = 0.0
    scan_summary: dict[str, Any] = {}
    sender_summary: dict[str, Any] = {}

    if not welcome_enabled:
        welcome_bypass_reason = "welcome_disabled"
        log(
            "info",
            "account_session_welcome_bypassed",
            account_id=aid,
            run_id=run_id,
            reason=welcome_bypass_reason,
        )
    elif not real_send_enabled:
        welcome_bypass_reason = "welcome_real_send_disabled"
        welcome_session_status = "failed"
        log(
            "error",
            "account_session_welcome_blocked",
            account_id=aid,
            run_id=run_id,
            reason=welcome_bypass_reason,
            real_send_source=real_send_source,
        )
    else:
        welcome_phase_executed = True
        log(
            "info",
            "account_session_welcome_phase_started",
            account_id=aid,
            account_username=uname,
            run_id=run_id,
        )
        welcome_t0 = time.perf_counter()
        welcome_exit_code = dispatch_welcome_session_send(
            d,
            account_id=aid,
            account_username=uname,
            run_id=run_id,
        )
        welcome_t1 = time.perf_counter()
        scan_summary = get_last_welcome_scan_summary()
        sender_summary = get_last_welcome_list_sender_summary()
        scan_code = 0 if str(scan_summary.get("status") or "") == "success" else 1
        sender_blocked = str(sender_summary.get("sender_status") or "") == "blocked_disabled"
        welcome_session_status = _welcome_session_status_label(
            scan_code,
            welcome_exit_code,
            sender_blocked=sender_blocked,
            sender_status=str(sender_summary.get("sender_status") or ""),
        )
        log(
            "info",
            "account_session_welcome_phase_completed",
            account_id=aid,
            run_id=run_id,
            welcome_exit_code=welcome_exit_code,
            welcome_session_status=welcome_session_status,
            welcome_scan_status=scan_summary.get("status"),
            welcome_sender_status=sender_summary.get("sender_status"),
            welcome_sender_jobs_sent_count=sender_summary.get("jobs_sent_count"),
            welcome_sender_jobs_failed_count=sender_summary.get("jobs_failed_count"),
            welcome_total_ms=round((welcome_t1 - welcome_t0) * 1000.0, 2),
        )

    follow_phase_executed = False
    follow_phase_skipped_reason: str | None = None
    follow_exit_code: int | None = None
    follow_t0 = follow_t1 = 0.0
    handoff_result: HandoffResult | None = None
    follow_to_unfollow_diagnostic: dict[str, Any] = {}
    follow_to_unfollow_probe: dict[str, Any] = {
        "enabled": _follow_to_unfollow_probe_enabled(),
        "executed": False,
        "probe_only": True,
        "skip_reason": "probe_disabled"
        if not _follow_to_unfollow_probe_enabled()
        else "follow_phase_not_completed",
        "status": "skipped",
        "unfollow_actions_sent": 0,
        "unfollow_actions_verified": 0,
    }

    run_follow, transition_reason = _should_run_follow_after_welcome(
        welcome_enabled=welcome_enabled,
        real_send_enabled=real_send_enabled,
        welcome_phase_executed=welcome_phase_executed,
        welcome_exit_code=welcome_exit_code,
        scan_summary=scan_summary,
        sender_summary=sender_summary,
        welcome_session_status=welcome_session_status,
    )

    welcome_blocked_follow = not run_follow and welcome_enabled

    if not run_follow:
        follow_phase_skipped_reason = transition_reason
        log(
            "info",
            "account_session_follow_phase_skipped",
            account_id=aid,
            run_id=run_id,
            reason=transition_reason,
        )
    else:
        follow_ready = True
        if welcome_phase_executed:
            handoff_result = prepare_dm_to_follow_handoff(
                d,
                account_username=uname,
                source_profile_username=src,
                welcome_phase_executed=True,
                sender_summary=sender_summary,
            )
            log(
                "info",
                "account_session_handoff_completed",
                account_id=aid,
                run_id=run_id,
                handoff_ok=handoff_result.ok,
                handoff_reason=handoff_result.reason,
                handoff_surface_label=handoff_result.surface_label,
                handoff_prepare_ms=round(handoff_result.prepare_ms, 2),
                handoff_dm_thread_recovered=handoff_result.dm_thread_recovered,
                handoff_followers_surface_ok=handoff_result.followers_surface_ok,
                handoff_resets_applied=handoff_result.resets_applied,
            )
            if not handoff_result.ok:
                follow_ready = False
                follow_phase_skipped_reason = handoff_result.reason
                log(
                    "info",
                    "account_session_follow_phase_skipped",
                    account_id=aid,
                    run_id=run_id,
                    reason=handoff_result.reason,
                    prior_transition_reason=transition_reason,
                )
        if follow_ready:
            log(
                "info",
                "account_session_follow_phase_started",
                account_id=aid,
                run_id=run_id,
                followers_source_username=src,
                transition_reason=transition_reason,
                handoff_applied=bool(welcome_phase_executed),
                handoff_reason=(
                    handoff_result.reason if handoff_result is not None else None
                ),
            )
            follow_t0 = time.perf_counter()
            follow_exit_code = int(
                run_followers_list_engine_session(
                    d,
                    source_profile_username=src,
                    account_id=aid,
                    run_id=str(run_id or ""),
                    supabase_mode=supabase_mode,
                    warm_session_used=warm_session_used,
                    force_stop_used=force_stop_used,
                )
            )
            follow_t1 = time.perf_counter()
            follow_phase_executed = True
            log(
                "info",
                "account_session_follow_phase_completed",
                account_id=aid,
                run_id=run_id,
                follow_engine_exit_code=follow_exit_code,
                follow_total_ms=round((follow_t1 - follow_t0) * 1000.0, 2),
            )
            follow_to_unfollow_diagnostic = _run_follow_to_unfollow_handoff_diagnostic(
                account_id=aid,
                account_username=uname,
                run_id=run_id,
                followers_source_username=src,
                follow_phase_executed=follow_phase_executed,
                follow_exit_code=follow_exit_code,
                follow_total_ms=(follow_t1 - follow_t0) * 1000.0,
                session_started_at=t0,
            )
            probe_enabled = _follow_to_unfollow_probe_enabled()
            if not probe_enabled:
                follow_to_unfollow_probe = _skip_follow_to_unfollow_probe(
                    account_id=aid,
                    account_username=uname,
                    run_id=run_id,
                    probe_enabled=False,
                    skip_reason="probe_disabled",
                    diagnostic=follow_to_unfollow_diagnostic,
                )
            elif not bool(follow_to_unfollow_diagnostic.get("handoff_would_run")):
                follow_to_unfollow_probe = _skip_follow_to_unfollow_probe(
                    account_id=aid,
                    account_username=uname,
                    run_id=run_id,
                    probe_enabled=True,
                    skip_reason=str(
                        follow_to_unfollow_diagnostic.get("handoff_skip_reason")
                        or "handoff_gates_not_met"
                    ),
                    diagnostic=follow_to_unfollow_diagnostic,
                )
            else:
                follow_to_unfollow_probe = _run_follow_to_unfollow_probe(
                    d,
                    account_id=aid,
                    account_username=uname,
                    run_id=run_id,
                    follow_exit_code=follow_exit_code,
                    follow_total_ms=(follow_t1 - follow_t0) * 1000.0,
                    diagnostic=follow_to_unfollow_diagnostic,
                )

    session_status = _account_session_status(
        transition_reason=transition_reason,
        follow_phase_executed=follow_phase_executed,
        follow_exit_code=follow_exit_code,
        welcome_blocked_follow=welcome_blocked_follow,
    )
    exit_code = 0 if session_status == "success" else 1
    total_ms = (time.perf_counter() - t0) * 1000.0

    log(
        "info",
        "account_session_summary",
        account_id=aid,
        account_username=uname,
        run_id=run_id,
        total_ms=round(total_ms, 2),
        session_status=session_status,
        transition_reason=transition_reason,
        welcome_enabled=welcome_enabled,
        welcome_phase_executed=welcome_phase_executed,
        welcome_bypass_reason=welcome_bypass_reason,
        welcome_session_status=welcome_session_status,
        welcome_exit_code=welcome_exit_code,
        welcome_scan_status=scan_summary.get("status"),
        welcome_scan_jobs_enqueued_count=scan_summary.get("jobs_enqueued_count"),
        welcome_scan_stop_reason=scan_summary.get("stop_reason"),
        welcome_sender_status=sender_summary.get("sender_status"),
        welcome_sender_exit_code=welcome_exit_code if welcome_phase_executed else None,
        welcome_sender_jobs_claimed_count=sender_summary.get("jobs_claimed_count"),
        welcome_sender_jobs_sent_count=sender_summary.get("jobs_sent_count"),
        welcome_sender_jobs_skipped_count=sender_summary.get("jobs_skipped_count"),
        welcome_sender_jobs_failed_count=sender_summary.get("jobs_failed_count"),
        welcome_sender_loop_exit_reason=sender_summary.get("loop_exit_reason"),
        welcome_total_ms=round((welcome_t1 - welcome_t0) * 1000.0, 2) if welcome_phase_executed else 0.0,
        welcome_scan_total_ms=scan_summary.get("total_ms"),
        welcome_sender_total_ms=sender_summary.get("total_ms"),
        follow_phase_executed=follow_phase_executed,
        follow_phase_skipped_reason=follow_phase_skipped_reason,
        followers_source_username=src,
        follow_engine_exit_code=follow_exit_code,
        follow_total_ms=round((follow_t1 - follow_t0) * 1000.0, 2) if follow_phase_executed else 0.0,
        follow_to_unfollow_handoff_diagnostic_status=follow_to_unfollow_diagnostic.get("status"),
        follow_to_unfollow_handoff={
            "diagnostic_only": True,
            "would_run": follow_to_unfollow_diagnostic.get("handoff_would_run"),
            "skip_reason": follow_to_unfollow_diagnostic.get("handoff_skip_reason"),
            "pending_unfollow_count": follow_to_unfollow_diagnostic.get("pending_unfollow_count"),
            "has_pending_unfollow": follow_to_unfollow_diagnostic.get("has_pending_unfollow"),
            "unfollow_mode": follow_to_unfollow_diagnostic.get("unfollow_mode"),
            "plan_reason": follow_to_unfollow_diagnostic.get("plan_reason"),
        } if follow_to_unfollow_diagnostic else None,
        follow_to_unfollow_would_launch_unfollow=follow_to_unfollow_diagnostic.get(
            "would_launch_unfollow"
        ),
        follow_to_unfollow_handoff_would_run=follow_to_unfollow_diagnostic.get(
            "handoff_would_run"
        ),
        follow_to_unfollow_handoff_decision=follow_to_unfollow_diagnostic.get(
            "handoff_decision"
        ),
        follow_to_unfollow_handoff_skip_reason=follow_to_unfollow_diagnostic.get(
            "handoff_skip_reason"
        ),
        pending_unfollow_count=follow_to_unfollow_diagnostic.get("pending_unfollow_count"),
        has_pending_unfollow=follow_to_unfollow_diagnostic.get("has_pending_unfollow"),
        pending_unfollow_count_scope=follow_to_unfollow_diagnostic.get(
            "pending_unfollow_count_scope"
        ),
        follow_to_unfollow_unfollow_enabled=follow_to_unfollow_diagnostic.get(
            "unfollow_enabled"
        ),
        follow_to_unfollow_unfollow_mode=follow_to_unfollow_diagnostic.get("unfollow_mode"),
        follow_to_unfollow_unfollow_plan_reason=follow_to_unfollow_diagnostic.get(
            "unfollow_plan_reason"
        ),
        follow_to_unfollow_diagnostic_ms=follow_to_unfollow_diagnostic.get("diagnostic_ms"),
        follow_to_unfollow_probe=follow_to_unfollow_probe,
        handoff_ok=handoff_result.ok if handoff_result is not None else None,
        handoff_reason=handoff_result.reason if handoff_result is not None else None,
        handoff_surface_label=handoff_result.surface_label if handoff_result is not None else None,
        handoff_prepare_ms=(
            round(handoff_result.prepare_ms, 2) if handoff_result is not None else None
        ),
        real_send_enabled=real_send_enabled,
        real_send_source=real_send_source,
    )
    return exit_code


def dispatch_account_session(
    d: u2.Device,
    *,
    account_id: str,
    account_username: str,
    run_id: str | None,
    source_profile_username: str,
    run_followers_list_engine_session: FollowEngineRunner,
    supabase_mode: bool,
    warm_session_used: bool,
    force_stop_used: bool,
) -> int:
    return run_account_session(
        d,
        account_id=account_id,
        account_username=account_username,
        run_id=run_id,
        source_profile_username=source_profile_username,
        run_followers_list_engine_session=run_followers_list_engine_session,
        supabase_mode=supabase_mode,
        warm_session_used=warm_session_used,
        force_stop_used=force_stop_used,
    )
