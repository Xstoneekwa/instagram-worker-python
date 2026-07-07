"""
V4.7 — Account session: optional Welcome DM block → Followers list engine (Follow/Likes).

Run type: account_session (wired from runner.py).
dm_welcome_session_send remains a standalone diagnostic run type.
"""

from __future__ import annotations

import time
import os
from typing import Any, Callable

import uiautomator2 as u2

import config
import supabase_client
from account_session_reliability_schema import (
    build_admin_reliability_snapshot,
    build_escalation_event,
)
from account_session_resume_engine import build_account_session_resume_plan
from device import app_start, press_home
from dm_follow_handoff import HandoffResult, prepare_dm_to_follow_handoff
from dm_sender_engine import resolve_welcome_dm_real_send_enabled
from instagram_navigation import verify_app_foreground
from logs import log
from own_profile_navigation import open_own_profile_from_bottom_nav, verify_own_profile
from runtime_caps import resolve_unfollow_runtime_cap
from unfollow_session_orchestrator import (
    get_last_unfollow_session_probe_summary,
    run_unfollow_session,
)
from unfollow_eligibility_engine import plan_unfollow_targets
from unfollow_settings import UNFOLLOW_MODE_ANY, UNFOLLOW_MODES_DB_STRICT, load_unfollow_settings
from welcome_list_sender import get_last_welcome_list_sender_summary
from welcome_scan_producer import get_last_welcome_scan_summary
from welcome_session_orchestrator import dispatch_welcome_session_send
from account_commercial_policy import (
    commercial_policy_boundary_blocks_phase,
    load_account_commercial_policy_revision,
)

FollowEngineRunner = Callable[..., int]
FastRotationRunner = Callable[..., dict[str, Any]]

H3_SUPPORTED_UNFOLLOW_MODES = frozenset({*UNFOLLOW_MODES_DB_STRICT, UNFOLLOW_MODE_ANY})
FOLLOW_TARGET_MAX_TARGETS_PER_RUN_ENV = "FOLLOW_TARGET_ROTATION_MAX_TARGETS_PER_RUN"
FOLLOW_TARGET_MAX_FOLLOWS_PER_TARGET_ENV = "FOLLOW_TARGET_MAX_FOLLOWS_PER_TARGET_PER_RUN"
FOLLOW_TARGET_EXHAUSTION_EXIT_CODES = frozenset({66})
FOLLOW_TARGET_EXHAUSTION_TOKENS = frozenset(
    {
        "followers_engine_sparse_exhausted",
        "no_candidates_after_sparse_scrolls",
        "list_progressive_exploration_exhausted",
        "no_followable_candidates_bounded_exploration",
        "no_followable_candidates_after_bounded_exploration",
        "bounded_exploration_exhausted",
    }
)
FOLLOW_TARGET_NON_EXHAUSTION_TOKENS = frozenset(
    {
        "checkpoint",
        "credential",
        "password",
        "login",
        "identity",
        "device",
        "rate_limit",
        "wrong_surface",
        "review_popup",
        "crash",
        "exception",
        "support_required",
    }
)


def _startup_timing_log(
    event: str,
    started_at: float,
    *,
    phase: str,
    substep: str,
    source: str,
    **fields: Any,
) -> None:
    try:
        log(
            "info",
            event,
            duration_ms=round((time.perf_counter() - started_at) * 1000.0, 2),
            phase=str(phase or ""),
            substep=str(substep or ""),
            source=str(source or ""),
            **fields,
        )
    except Exception:
        pass


def _is_unfollow_any_mode(mode: str) -> bool:
    return str(mode or "").strip().lower() == UNFOLLOW_MODE_ANY


def _h3_supports_unfollow_mode(mode: str) -> bool:
    return str(mode or "").strip().lower() in H3_SUPPORTED_UNFOLLOW_MODES


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


def _as_optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _setting_source_from_env() -> str:
    if (
        str(os.getenv(FOLLOW_TARGET_MAX_TARGETS_PER_RUN_ENV) or "").strip()
        or str(os.getenv(FOLLOW_TARGET_MAX_FOLLOWS_PER_TARGET_ENV) or "").strip()
    ):
        return "env"
    return "default"


def _validate_rotation_setting(value: Any, *, field: str, lower: int, upper: int) -> int:
    parsed = _as_optional_int(value)
    if parsed is None or parsed < lower or parsed > upper:
        raise ValueError(f"{field}_out_of_bounds_{lower}_{upper}")
    return parsed


def _resolve_rotation_setting_with_fallback(
    value: Any,
    *,
    field: str,
    lower: int,
    upper: int,
    fallback: int,
    account_id: str,
) -> tuple[int, bool]:
    try:
        return _validate_rotation_setting(value, field=field, lower=lower, upper=upper), False
    except ValueError as exc:
        log(
            "warning",
            "follow_source_rotation_setting_fallback_used",
            account_id=account_id,
            field=field,
            reason=str(exc),
            fallback_value=fallback,
            lower_bound=lower,
            upper_bound=upper,
        )
        return fallback, True


def _resolve_follow_source_rotation_settings(account_id: str) -> dict[str, Any]:
    max_targets_upper = int(getattr(config, "FOLLOW_TARGET_ROTATION_MAX_TARGETS_PER_RUN_UPPER_BOUND", 10) or 10)
    max_follows_upper = int(getattr(config, "FOLLOW_TARGET_MAX_FOLLOWS_PER_TARGET_PER_RUN_UPPER_BOUND", 50) or 50)
    fallback = {
        "max_targets_per_run": int(getattr(config, "FOLLOW_TARGET_ROTATION_MAX_TARGETS_PER_RUN", 3) or 3),
        "max_follows_per_target_per_run": int(getattr(config, "FOLLOW_TARGET_MAX_FOLLOWS_PER_TARGET_PER_RUN", 2) or 2),
        "settings_source": _setting_source_from_env(),
        "bounds": {
            "max_targets_per_run": {"min": 1, "max": max_targets_upper},
            "max_follows_per_target_per_run": {"min": 1, "max": max_follows_upper},
        },
    }
    try:
        row = supabase_client.load_account_follow_source_settings(account_id)
    except Exception as exc:
        log(
            "warning",
            "follow_source_rotation_settings_load_failed",
            account_id=account_id,
            reason=str(exc),
            fallback_source=fallback["settings_source"],
        )
        return fallback
    if not row:
        return fallback
    max_targets_per_run, max_targets_fallback_used = _resolve_rotation_setting_with_fallback(
        row.get("max_targets_per_run"),
        field="max_targets_per_run",
        lower=1,
        upper=max_targets_upper,
        fallback=int(fallback["max_targets_per_run"]),
        account_id=account_id,
    )
    max_follows_per_target_per_run, max_follows_fallback_used = _resolve_rotation_setting_with_fallback(
        row.get("max_follows_per_target_per_run"),
        field="max_follows_per_target_per_run",
        lower=1,
        upper=max_follows_upper,
        fallback=int(fallback["max_follows_per_target_per_run"]),
        account_id=account_id,
    )
    return {
        "max_targets_per_run": max_targets_per_run,
        "max_follows_per_target_per_run": max_follows_per_target_per_run,
        "settings_source": (
            "account_with_fallback"
            if max_targets_fallback_used or max_follows_fallback_used
            else "account"
        ),
        "bounds": fallback["bounds"],
    }


def _last_follow_engine_summary(run_followers_list_engine_session: FollowEngineRunner) -> dict[str, Any]:
    raw = getattr(run_followers_list_engine_session, "last_session_summary", None)
    return dict(raw) if isinstance(raw, dict) else {}


def _as_target_id(value: Any) -> str:
    return str(value or "").strip()


def _as_source_profile(value: Any) -> str:
    return str(value or "").strip().lstrip("@").lower()


def _record_follow_target_metric(event: str, **kwargs: Any) -> dict[str, Any]:
    fn = getattr(supabase_client, f"record_follow_source_{event}", None)
    if fn is None:
        return {"ok": False, "error": "metrics_helper_missing"}
    try:
        out = fn(**kwargs)
        return dict(out) if isinstance(out, dict) else {"ok": bool(out)}
    except Exception as exc:
        log(
            "warning",
            "follow_target_metric_persist_failed",
            metrics_event=event,
            account_id=str(kwargs.get("account_id") or ""),
            target_id=str(kwargs.get("target_id") or ""),
            error=str(exc)[:300],
        )
        return {"ok": False, "error": str(exc)}


def _follow_target_key(target: dict[str, Any]) -> str:
    return _as_target_id(target.get("target_id") or target.get("id")) or _as_source_profile(
        target.get("source_profile") or target.get("source_profile_username") or target.get("target_username")
    )


def _rotation_target_from_row(row: dict[str, Any], index: int) -> dict[str, Any] | None:
    source_profile = _as_source_profile(
        row.get("source_profile") or row.get("source_profile_username") or row.get("target_username") or row.get("username")
    )
    if not source_profile:
        return None
    return {
        "target_id": _as_target_id(row.get("target_id") or row.get("id")) or None,
        "source_profile": source_profile,
        "target_index": index,
        "selection_source": str(row.get("selection_source") or "ig_targets").strip() or "ig_targets",
    }


def _build_follow_rotation_targets(
    *,
    follow_targets: list[dict[str, Any]] | None,
    fallback_source_profile: str,
    fallback_target_id: str | None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    rows = list(follow_targets or [])
    if not rows and _as_source_profile(fallback_source_profile):
        rows = [{
            "id": fallback_target_id,
            "source_profile_username": fallback_source_profile,
            "selection_source": "single_target",
        }]
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        target = _rotation_target_from_row(row, index)
        if not target:
            continue
        key = _follow_target_key(target)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(target)
    return out


def _resolve_max_follow_targets_per_run(total_targets: int, configured: int | None = None) -> int:
    raw = configured
    if raw is None:
        raw = int(getattr(config, "FOLLOW_TARGET_ROTATION_MAX_TARGETS_PER_RUN", 3) or 3)
    upper = int(getattr(config, "FOLLOW_TARGET_ROTATION_MAX_TARGETS_PER_RUN_UPPER_BOUND", 10) or 10)
    return max(1, min(int(raw), max(1, int(total_targets or 1)), upper))


def _resolve_max_follows_per_target_per_run(configured: int | None = None) -> int:
    raw = configured
    if raw is None:
        raw = int(getattr(config, "FOLLOW_TARGET_MAX_FOLLOWS_PER_TARGET_PER_RUN", 2) or 2)
    upper = int(getattr(config, "FOLLOW_TARGET_MAX_FOLLOWS_PER_TARGET_PER_RUN_UPPER_BOUND", 50) or 50)
    return max(1, min(int(raw), upper))


def is_follow_target_exhaustion_outcome(
    *,
    exit_code: int | None = None,
    outcome: str | None = None,
    reason: str | None = None,
    summary: dict[str, Any] | None = None,
) -> bool:
    data = dict(summary or {})
    code = exit_code if exit_code is not None else _as_optional_int(data.get("exit_code"))
    if code in FOLLOW_TARGET_EXHAUSTION_EXIT_CODES:
        return True
    follows_completed = _as_optional_int(data.get("follows_completed_count"))
    if follows_completed is not None and follows_completed > 0:
        return False
    text = " ".join(
        str(part or "").strip().lower()
        for part in (
            outcome,
            reason,
            data.get("follow_session_outcome"),
            data.get("follow_stop_reason"),
        )
        if str(part or "").strip()
    )
    if any(token in text for token in FOLLOW_TARGET_NON_EXHAUSTION_TOKENS):
        return False
    return any(token in text for token in FOLLOW_TARGET_EXHAUSTION_TOKENS)


def is_follow_target_budget_reached(summary: dict[str, Any], target_budget: int) -> bool:
    follows_completed = _as_optional_int(summary.get("follows_completed_count")) or 0
    return follows_completed >= max(1, int(target_budget))


def _run_follow_target_rotation(
    d: u2.Device,
    *,
    account_id: str,
    account_username: str,
    run_id: str | None,
    follow_targets: list[dict[str, Any]],
    run_followers_list_engine_session: FollowEngineRunner,
    supabase_mode: bool,
    warm_session_used: bool,
    force_stop_used: bool,
    max_targets_per_run: int | None = None,
    max_follows_per_target_per_run: int | None = None,
    fast_rotate_to_next_target_from_followers: FastRotationRunner | None = None,
) -> dict[str, Any]:
    total_targets = len(follow_targets)
    max_targets = _resolve_max_follow_targets_per_run(total_targets, max_targets_per_run)
    max_follows_per_target = _resolve_max_follows_per_target_per_run(max_follows_per_target_per_run)
    bounded_targets = follow_targets[:max_targets]
    exhausted_keys: set[str] = set()
    budget_reached_keys: set[str] = set()
    exhausted_targets: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []
    global_follows_completed = 0
    global_follow_goal: int | None = None
    final_exit_code = 1
    final_summary: dict[str, Any] = {}
    final_reason = "no_follow_targets"
    final_target: dict[str, Any] | None = None
    prevalidated_followers_target_key: str | None = None
    prevalidated_followers_meta: dict[str, Any] = {}
    t0 = time.perf_counter()

    log(
        "info",
        "follow_target_rotation_started",
        account_id=account_id,
        account_username=account_username,
        run_id=run_id,
        total_targets=total_targets,
        max_targets_per_run=max_targets,
        max_follows_per_target_per_run=max_follows_per_target,
    )

    for attempt_index, target in enumerate(bounded_targets):
        target_key = _follow_target_key(target)
        if not target_key or target_key in exhausted_keys or target_key in budget_reached_keys:
            continue
        global_remaining = (
            max(0, global_follow_goal - global_follows_completed)
            if global_follow_goal is not None
            else max_follows_per_target
        )
        if global_remaining <= 0:
            target_id = _as_target_id(target.get("target_id")) or None
            source_profile = _as_source_profile(target.get("source_profile"))
            target_index = int(target.get("target_index") or attempt_index)
            log(
                "info",
                "target_rotation_stopped_global_cap",
                account_id=account_id,
                run_id=run_id,
                target_id=target_id,
                source_profile=source_profile,
                target_index=target_index,
                current_count=global_follows_completed,
                cap=global_follow_goal,
                follows_done=global_follows_completed,
                stop_reason="global_follow_cap_reached",
                candidates_not_scanned_due_to_cap=True,
            )
            log(
                "info",
                "target_rotation_decision",
                from_target=source_profile,
                to_target="",
                reason="global_follow_cap_reached",
                global_follows_done=global_follows_completed,
                global_follow_cap=global_follow_goal,
            )
            log(
                "info",
                "run_follow_phase_completed_due_to_cap",
                account_id=account_id,
                run_id=run_id,
                target_id=target_id,
                source_profile=source_profile,
                target_index=target_index,
                follows_done=global_follows_completed,
                cap=global_follow_goal,
                stop_reason="global_follow_cap_reached",
                candidates_not_scanned_due_to_cap=True,
            )
            final_exit_code = 0
            final_reason = "global_follow_cap_reached"
            final_summary.update(
                {
                    "exit_code": 0,
                    "follow_session_outcome": "global_follow_cap_reached",
                    "follow_stop_reason": "global_follow_cap_reached",
                    "global_follows_completed": global_follows_completed,
                    "global_follows_goal_effective": global_follow_goal,
                    "current_target": source_profile,
                    "candidates_not_scanned_due_to_cap": True,
                    "rotation_attempts": attempts,
                }
            )
            final_target = target
            break
        target_budget = min(max_follows_per_target, global_remaining)
        target_id = _as_target_id(target.get("target_id")) or None
        source_profile = _as_source_profile(target.get("source_profile"))
        target_index = int(target.get("target_index") or attempt_index)
        t_target_selected = time.perf_counter()
        log(
            "info",
            "follow_target_selected",
            account_id=account_id,
            account_username=account_username,
            run_id=run_id,
            target_id=target_id,
            source_profile=source_profile,
            target_index=target_index,
            total_targets=total_targets,
            max_targets_per_run=max_targets,
            target_budget=target_budget,
            global_follow_remaining=global_remaining,
            selection_source=str(target.get("selection_source") or ""),
        )
        _startup_timing_log(
            "startup_timing_target_selection_completed",
            t_target_selected,
            phase="target_settings",
            substep="rotation_target_selected",
            source="follow_target_rotation",
            cache_hit=False,
            reused_signal=True,
            duplicate_detected=True,
            account_id=account_id,
            run_id=run_id,
            target_index=target_index,
            total_targets=total_targets,
            selection_source=str(target.get("selection_source") or ""),
        )
        log(
            "info",
            "target_scan_started",
            target_username=source_profile,
            target_index=target_index,
            max_targets_per_run=max_targets,
            account_id=account_id,
            run_id=run_id,
            target_id=target_id,
        )
        if supabase_mode:
            _record_follow_target_metric(
                "target_selected",
                account_id=account_id,
                target_id=target_id,
                source_profile=source_profile,
                run_id=run_id,
            )
        start_from_current_followers_list = (
            bool(prevalidated_followers_target_key)
            and target_key == prevalidated_followers_target_key
        )
        call_kwargs: dict[str, Any] = {
            "source_profile_username": source_profile,
            "target_id": target_id,
            "account_id": account_id,
            "run_id": str(run_id or ""),
            "supabase_mode": supabase_mode,
            "warm_session_used": warm_session_used,
            "force_stop_used": force_stop_used,
            "target_follow_budget": target_budget,
        }
        if start_from_current_followers_list:
            call_kwargs["start_from_current_followers_list"] = True
            call_kwargs["prevalidated_followers_list_meta"] = dict(prevalidated_followers_meta)
            prevalidated_followers_target_key = None
            prevalidated_followers_meta = {}
        follow_t0 = time.perf_counter()
        exit_code = int(run_followers_list_engine_session(d, **call_kwargs))
        follow_total_ms = round((time.perf_counter() - follow_t0) * 1000.0, 2)
        summary = _last_follow_engine_summary(run_followers_list_engine_session)
        summary.update(
            {
                "target_id": target_id or "",
                "source_profile_username": source_profile,
                "target_index": target_index,
                "total_targets": total_targets,
                "max_targets_per_run": max_targets,
                "target_budget": target_budget,
                "exit_code": exit_code,
            }
        )
        target_follows_completed = _as_optional_int(summary.get("follows_completed_count")) or 0
        global_follows_completed += target_follows_completed
        summary_reason = str(summary.get("follow_stop_reason") or summary.get("follow_session_outcome") or "")
        if summary_reason == "global_follow_cap_reached":
            summary_global_completed = (
                _as_optional_int(summary.get("global_follows_completed"))
                or _as_optional_int(summary.get("follows_done"))
                or _as_optional_int(summary.get("current_count"))
            )
            if summary_global_completed is not None:
                global_follows_completed = max(global_follows_completed, summary_global_completed)
        summary_global_goal = _as_optional_int(summary.get("global_follows_goal_effective"))
        if summary_global_goal is not None:
            global_follow_goal = (
                min(global_follow_goal, summary_global_goal)
                if global_follow_goal is not None
                else summary_global_goal
            )
        attempts.append(
            {
                "target_id": target_id,
                "source_profile": source_profile,
                "target_index": target_index,
                "exit_code": exit_code,
                "follow_session_outcome": summary.get("follow_session_outcome"),
                "follow_stop_reason": summary.get("follow_stop_reason"),
                "follows_completed_count": target_follows_completed,
                "target_budget": target_budget,
                "global_follows_completed": global_follows_completed,
            }
        )
        final_exit_code = exit_code
        final_summary = summary
        final_target = target
        log(
            "info",
            "account_session_follow_phase_completed",
            account_id=account_id,
            run_id=run_id,
            follow_engine_exit_code=exit_code,
            target_id=target_id,
            source_profile=source_profile,
            target_index=target_index,
            total_targets=total_targets,
            follows_completed_count=target_follows_completed,
            follow_processed_count=summary.get("follow_processed_count"),
            follow_session_outcome=summary.get("follow_session_outcome"),
            follow_stop_reason=summary.get("follow_stop_reason"),
            target_budget=target_budget,
            global_follows_completed=global_follows_completed,
            global_follow_goal=global_follow_goal,
            follow_total_ms=follow_total_ms,
        )
        log(
            "info",
            "target_scan_completed",
            target_username=source_profile,
            duration_ms=follow_total_ms,
            candidates_seen=summary.get("candidates_seen_count"),
            candidates_opened=summary.get("candidates_opened_count"),
            candidates_rejected=summary.get("candidates_rejected_count"),
            follows_sent=target_follows_completed,
            stop_reason=summary_reason,
            target_index=target_index,
            max_targets_per_run=max_targets,
        )
        exhausted = is_follow_target_exhaustion_outcome(
            exit_code=exit_code,
            outcome=str(summary.get("follow_session_outcome") or ""),
            reason=str(summary.get("follow_stop_reason") or ""),
            summary=summary,
        )
        global_cap_reached = (
            global_follow_goal is not None
            and global_follows_completed >= global_follow_goal
        )
        budget_reached = is_follow_target_budget_reached(summary, target_budget)
        if summary_reason == "global_follow_cap_reached":
            final_reason = "global_follow_cap_reached"
            final_exit_code = 0
            final_summary.update(
                {
                    "exit_code": 0,
                    "follow_session_outcome": "global_follow_cap_reached",
                    "follow_stop_reason": "global_follow_cap_reached",
                    "global_follows_completed": global_follows_completed,
                    "global_follows_goal_effective": global_follow_goal,
                    "current_target": source_profile,
                    "candidates_not_scanned_due_to_cap": True,
                    "rotation_attempts": attempts,
                }
            )
            log(
                "info",
                "target_rotation_stopped_global_cap",
                account_id=account_id,
                run_id=run_id,
                target_id=target_id,
                source_profile=source_profile,
                target_index=target_index,
                current_count=global_follows_completed,
                cap=global_follow_goal,
                follows_done=global_follows_completed,
                stop_reason="global_follow_cap_reached",
                candidates_not_scanned_due_to_cap=True,
            )
            log(
                "info",
                "target_rotation_decision",
                from_target=source_profile,
                to_target="",
                reason="global_follow_cap_reached",
                global_follows_done=global_follows_completed,
                global_follow_cap=global_follow_goal,
            )
            log(
                "info",
                "run_follow_phase_completed_due_to_cap",
                account_id=account_id,
                run_id=run_id,
                target_id=target_id,
                source_profile=source_profile,
                target_index=target_index,
                follows_done=global_follows_completed,
                cap=global_follow_goal,
                stop_reason="global_follow_cap_reached",
                candidates_not_scanned_due_to_cap=True,
            )
            break
        if budget_reached and not exhausted:
            budget_reached_keys.add(target_key)
            log(
                "info",
                "follow_target_budget_reached",
                account_id=account_id,
                run_id=run_id,
                target_id=target_id,
                source_profile=source_profile,
                target_index=target_index,
                total_targets=total_targets,
                target_follows_completed=target_follows_completed,
                target_budget=target_budget,
                global_follow_remaining=max(0, (global_follow_goal or global_follows_completed) - global_follows_completed),
                reason="target_budget_reached",
            )
            if supabase_mode:
                _record_follow_target_metric(
                    "target_budget_reached",
                    account_id=account_id,
                    target_id=target_id,
                    source_profile=source_profile,
                    run_id=run_id,
                    target_follows_completed=target_follows_completed,
                    target_budget=target_budget,
                )
            if global_cap_reached:
                final_reason = "global_follow_cap_reached"
                final_exit_code = 0
                final_summary.update(
                    {
                        "exit_code": 0,
                        "follow_session_outcome": "global_follow_cap_reached",
                        "follow_stop_reason": "global_follow_cap_reached",
                        "global_follows_completed": global_follows_completed,
                        "global_follows_goal_effective": global_follow_goal,
                        "current_target": source_profile,
                        "candidates_not_scanned_due_to_cap": True,
                        "rotation_attempts": attempts,
                    }
                )
                log(
                    "info",
                    "target_rotation_stopped_global_cap",
                    account_id=account_id,
                    run_id=run_id,
                    target_id=target_id,
                    source_profile=source_profile,
                    target_index=target_index,
                    current_count=global_follows_completed,
                    cap=global_follow_goal,
                    follows_done=global_follows_completed,
                    stop_reason="global_follow_cap_reached",
                    candidates_not_scanned_due_to_cap=True,
                )
                log(
                    "info",
                    "target_rotation_decision",
                    from_target=source_profile,
                    to_target="",
                    reason="global_follow_cap_reached",
                    global_follows_done=global_follows_completed,
                    global_follow_cap=global_follow_goal,
                )
                log(
                    "info",
                    "run_follow_phase_completed_due_to_cap",
                    account_id=account_id,
                    run_id=run_id,
                    target_id=target_id,
                    source_profile=source_profile,
                    target_index=target_index,
                    follows_done=global_follows_completed,
                    cap=global_follow_goal,
                    stop_reason="global_follow_cap_reached",
                    candidates_not_scanned_due_to_cap=True,
                )
                break
            remaining_budget_targets = [
                candidate
                for candidate in bounded_targets[attempt_index + 1 :]
                if _follow_target_key(candidate) not in exhausted_keys
                and _follow_target_key(candidate) not in budget_reached_keys
            ]
            if remaining_budget_targets:
                next_target = remaining_budget_targets[0]
                next_target_id = _as_target_id(next_target.get("target_id")) or None
                next_source_profile = _as_source_profile(next_target.get("source_profile"))
                next_target_index = int(next_target.get("target_index") or 0)
                rotation_payload = {
                    "account_id": account_id,
                    "run_id": run_id,
                    "from_source_target": source_profile,
                    "to_source_target": next_source_profile,
                    "target_id": target_id,
                    "next_target_id": next_target_id,
                    "target_index": target_index,
                    "next_target_index": next_target_index,
                    "total_targets": total_targets,
                    "reason": "target_budget_reached",
                    "target_follows_sent_this_run": target_follows_completed,
                    "max_follows_per_target_per_run": target_budget,
                    "total_follows_this_run": global_follows_completed,
                    "effective_follow_cap": global_follow_goal,
                }
                log(
                    "info",
                    "target_rotation_decision",
                    from_target=source_profile,
                    to_target=next_source_profile,
                    reason="target_budget_reached",
                    global_follows_done=global_follows_completed,
                    global_follow_cap=global_follow_goal,
                )
                log("info", "follow_target_rotation_requested", **rotation_payload)
                fast_rotation_result: dict[str, Any] | None = None
                if fast_rotate_to_next_target_from_followers is not None:
                    fast_rotation_result = fast_rotate_to_next_target_from_followers(
                        d,
                        account_id=account_id,
                        run_id=run_id,
                        from_source_target=source_profile,
                        to_source_target=next_source_profile,
                    )
                    if bool((fast_rotation_result or {}).get("ok")):
                        prevalidated_followers_target_key = _follow_target_key(next_target)
                        prevalidated_followers_meta = {
                            "fast_target_rotation": fast_rotation_result,
                            "fast_target_rotation_prevalidated": True,
                        }
                    else:
                        fallback_payload = {
                            **rotation_payload,
                            "reason": str((fast_rotation_result or {}).get("reason") or "fast_target_rotation_failed"),
                            "step": str(
                                ((fast_rotation_result or {}).get("steps_completed") or [""])[-1]
                                if (fast_rotation_result or {}).get("steps_completed")
                                else "fast_rotation"
                            ),
                            "elapsed_ms": int((fast_rotation_result or {}).get("elapsed_ms") or 0),
                        }
                        log(
                            "warning",
                            "follow_target_fast_rotation_fallback_standard",
                            **fallback_payload,
                        )
                log(
                    "info",
                    "follow_target_switched",
                    account_id=account_id,
                    run_id=run_id,
                    target_id=target_id,
                    source_profile=source_profile,
                    next_target_id=next_target_id,
                    next_source_profile=next_source_profile,
                    target_index=target_index,
                    next_target_index=next_target_index,
                    total_targets=total_targets,
                    reason="target_budget_reached",
                )
                log(
                    "info",
                    "follow_target_rotation_completed",
                    **rotation_payload,
                    fast_rotation_ok=bool((fast_rotation_result or {}).get("ok")) if fast_rotation_result is not None else None,
                    fast_rotation_reason=str((fast_rotation_result or {}).get("reason") or "") if fast_rotation_result is not None else "",
                )
                continue
            final_reason = "target_budget_reached"
            final_exit_code = 0
            final_summary.update(
                {
                    "exit_code": 0,
                    "follow_session_outcome": "target_budget_reached",
                    "follow_stop_reason": "target_budget_reached",
                    "global_follows_completed": global_follows_completed,
                    "global_follows_goal_effective": global_follow_goal,
                    "rotation_attempts": attempts,
                }
            )
            break
        if not exhausted:
            if supabase_mode and exit_code not in (0, 97, 98):
                _record_follow_target_metric(
                    "runtime_error_non_exhaustion",
                    account_id=account_id,
                    target_id=target_id,
                    source_profile=source_profile,
                    run_id=run_id,
                    reason=str(summary.get("follow_stop_reason") or summary.get("follow_session_outcome") or f"exit_code_{exit_code}"),
                    outcome=str(summary.get("follow_session_outcome") or ""),
                )
            final_reason = str(summary.get("follow_session_outcome") or "target_completed")
            break
        exhausted_keys.add(target_key)
        exhausted_targets.append(
            {
                "target_id": target_id,
                "source_profile": source_profile,
                "target_index": target_index,
                "exit_code": exit_code,
                "reason": str(summary.get("follow_stop_reason") or summary.get("follow_session_outcome") or "target_exhausted"),
            }
        )
        log(
            "info",
            "follow_target_exhausted",
            account_id=account_id,
            run_id=run_id,
            target_id=target_id,
            source_profile=source_profile,
            target_index=target_index,
            total_targets=total_targets,
            reason=str(summary.get("follow_stop_reason") or ""),
            outcome=str(summary.get("follow_session_outcome") or ""),
            exit_code=exit_code,
        )
        if supabase_mode:
            _record_follow_target_metric(
                "target_exhausted",
                account_id=account_id,
                target_id=target_id,
                source_profile=source_profile,
                run_id=run_id,
                reason=str(summary.get("follow_stop_reason") or summary.get("follow_session_outcome") or "target_exhausted"),
                outcome=str(summary.get("follow_session_outcome") or ""),
            )
        remaining = [
            candidate
            for candidate in bounded_targets[attempt_index + 1 :]
            if _follow_target_key(candidate) not in exhausted_keys
        ]
        if remaining:
            next_target = remaining[0]
            next_source_profile = _as_source_profile(next_target.get("source_profile"))
            log(
                "info",
                "target_rotation_decision",
                from_target=source_profile,
                to_target=next_source_profile,
                reason="target_exhausted",
                global_follows_done=global_follows_completed,
                global_follow_cap=global_follow_goal,
            )
            log(
                "info",
                "follow_target_switched",
                account_id=account_id,
                run_id=run_id,
                target_id=target_id,
                source_profile=source_profile,
                next_target_id=_as_target_id(next_target.get("target_id")) or None,
                next_source_profile=next_source_profile,
                target_index=target_index,
                next_target_index=int(next_target.get("target_index") or 0),
                total_targets=total_targets,
                reason="target_exhausted",
            )
            continue
        if total_targets > len(bounded_targets):
            final_reason = "max_targets_per_run_reached"
            final_exit_code = 0
            final_summary.update(
                {
                    "exit_code": 0,
                    "follow_session_outcome": "target_rotation_max_targets_reached",
                    "follow_stop_reason": "max_targets_per_run_reached",
                    "all_targets_exhausted": False,
                    "exhausted_targets_count": len(exhausted_targets),
                    "rotation_attempts": attempts,
                }
            )
            break
        final_reason = "all_targets_exhausted"
        final_exit_code = 0
        final_summary.update(
            {
                "exit_code": 0,
                "follow_session_outcome": "no_followable_candidates_all_targets",
                "follow_stop_reason": "all_targets_exhausted",
                "all_targets_exhausted": True,
                "exhausted_targets_count": len(exhausted_targets),
                "rotation_attempts": attempts,
            }
        )
        log(
            "info",
            "follow_targets_all_exhausted",
            account_id=account_id,
            run_id=run_id,
            target_id=target_id,
            source_profile=source_profile,
            target_index=target_index,
            total_targets=total_targets,
            reason="all_targets_exhausted",
            outcome="no_followable_candidates_all_targets",
        )
        break

    if not attempts:
        final_summary = {
            "exit_code": 0,
            "follow_processed_count": 0,
            "follows_completed_count": 0,
            "follows_goal_effective": 0,
            "follow_session_outcome": "no_followable_candidates_all_targets",
            "follow_stop_reason": "all_targets_exhausted",
            "all_targets_exhausted": True,
            "rotation_attempts": [],
        }
        final_exit_code = 0
        final_reason = "all_targets_exhausted"
        log(
            "info",
            "follow_targets_all_exhausted",
            account_id=account_id,
            run_id=run_id,
            target_id=None,
            source_profile=None,
            target_index=None,
            total_targets=total_targets,
            reason="all_targets_exhausted",
            outcome="no_followable_candidates_all_targets",
        )

    if attempts:
        final_summary["last_target_follows_completed_count"] = attempts[-1].get("follows_completed_count")
        final_summary["follows_completed_count"] = global_follows_completed
        final_summary["global_follows_completed"] = global_follows_completed
        if global_follow_goal is not None:
            final_summary["follows_goal_effective"] = global_follow_goal
            final_summary["global_follows_goal_effective"] = global_follow_goal

    log(
        "info",
        "follow_target_rotation_completed",
        account_id=account_id,
        account_username=account_username,
        run_id=run_id,
        target_id=_as_target_id((final_target or {}).get("target_id")) or None,
        source_profile=_as_source_profile((final_target or {}).get("source_profile")),
        target_index=(final_target or {}).get("target_index"),
        total_targets=total_targets,
        max_targets_per_run=max_targets,
        max_follows_per_target_per_run=max_follows_per_target,
        attempts_count=len(attempts),
        exhausted_targets_count=len(exhausted_targets),
        global_follows_completed=global_follows_completed,
        global_follows_goal_effective=global_follow_goal,
        exit_code=final_exit_code,
        reason=final_reason,
        outcome=str(final_summary.get("follow_session_outcome") or ""),
        total_ms=round((time.perf_counter() - t0) * 1000.0, 2),
    )
    return {
        "exit_code": final_exit_code,
        "summary": final_summary,
        "attempts": attempts,
        "exhausted_targets": exhausted_targets,
        "all_targets_exhausted": bool(final_summary.get("all_targets_exhausted")),
            "global_follows_completed": global_follows_completed,
            "global_follows_goal_effective": global_follow_goal,
        "reason": final_reason,
    }


def _diagnostic_text(*parts: Any) -> str:
    chunks: list[str] = []
    for part in parts:
        if isinstance(part, dict):
            chunks.extend(str(v) for v in part.values())
        elif isinstance(part, (list, tuple, set)):
            chunks.extend(str(v) for v in part)
        elif part is not None:
            chunks.append(str(part))
    return " ".join(chunks).lower()


def _blocked_class_from_markers(*parts: Any) -> str | None:
    text = _diagnostic_text(*parts)
    if "active_instagram_account_mismatch" in text or "account_mismatch" in text:
        return "blocked_account_mismatch"
    if "challenge" in text:
        return "blocked_challenge"
    if "restriction" in text or "restricted" in text or "blocked" in text:
        return "blocked_restriction"
    return None


def _phase_statuses(
    *,
    welcome_enabled: bool,
    welcome_phase_executed: bool,
    welcome_session_status: str,
    follow_phase_executed: bool,
    follow_phase_skipped_reason: str | None,
    follow_exit_code: int | None,
    follow_to_unfollow_real: dict[str, Any],
) -> tuple[str, str, str]:
    if not welcome_enabled:
        welcome_phase_status = "skipped"
    elif not welcome_phase_executed:
        welcome_phase_status = "not_started"
    elif str(welcome_session_status or "") == "success":
        welcome_phase_status = "completed"
    elif str(welcome_session_status or "") == "partial_success":
        welcome_phase_status = "partial"
    elif str(welcome_session_status or "") == "failed":
        welcome_phase_status = "failed"
    else:
        welcome_phase_status = "unknown"

    if not follow_phase_executed:
        follow_phase_status = (
            "skipped" if str(follow_phase_skipped_reason or "").strip() else "not_started"
        )
    elif follow_exit_code == 0:
        follow_phase_status = "completed"
    elif follow_exit_code == 97:
        follow_phase_status = "partial_safe_stopped"
    elif follow_exit_code == 98:
        follow_phase_status = "partial_safe_stopped"
    elif follow_exit_code in (3, 4, 40, 44, 53, 54):
        follow_phase_status = "partial_resumable"
    elif follow_exit_code is None:
        follow_phase_status = "unknown"
    else:
        follow_phase_status = "failed"

    real_status = str(follow_to_unfollow_real.get("status") or "")
    if not bool(follow_to_unfollow_real.get("enabled")):
        unfollow_phase_status = "skipped"
    elif not bool(follow_to_unfollow_real.get("executed")):
        unfollow_phase_status = "skipped"
    elif real_status == "success_real_unfollow":
        unfollow_phase_status = "completed"
    elif real_status.startswith("failed"):
        unfollow_phase_status = "failed"
    elif real_status:
        unfollow_phase_status = real_status
    else:
        unfollow_phase_status = "unknown"

    return welcome_phase_status, follow_phase_status, unfollow_phase_status


def _session_termination_class(
    *,
    session_status: str,
    follow_phase_executed: bool,
    follow_exit_code: int | None,
    follow_quota_remaining: int | None,
    follow_to_unfollow_diagnostic: dict[str, Any],
    follow_to_unfollow_real: dict[str, Any],
    follow_phase_skipped_reason: str | None,
    transition_reason: str,
    follow_session_outcome: str | None = None,
) -> str:
    blocked = _blocked_class_from_markers(
        follow_to_unfollow_diagnostic,
        follow_to_unfollow_real,
        follow_phase_skipped_reason,
        transition_reason,
    )
    if blocked:
        return blocked
    if not follow_phase_executed:
        return "unknown" if session_status != "failed" else "recoverable_failure"
    if str(follow_session_outcome or "").strip() == "no_followable_candidates_all_targets":
        return "completed"
    if follow_exit_code == 0:
        if follow_quota_remaining is not None and follow_quota_remaining > 0:
            return "partial_resumable"
        return "completed"
    if follow_exit_code == 97:
        if bool(follow_to_unfollow_real.get("executed")):
            return "partial_safe_but_continued"
        return "partial_safe_stopped"
    if follow_exit_code == 98:
        return "partial_safe_stopped"
    if follow_exit_code in (3, 4, 40, 44, 53, 54):
        return "recoverable_failure" if follow_exit_code in (3, 4, 40, 44) else "partial_resumable"
    if follow_exit_code in (42, 71, 72, 74, 75, 96, 99):
        return "non_recoverable_failure"
    if follow_exit_code is None:
        return "unknown"
    return "unknown"


def _restart_eligibility(
    *,
    session_termination_class: str,
    follow_quota_remaining: int | None,
    follow_to_unfollow_diagnostic: dict[str, Any],
    follow_to_unfollow_real: dict[str, Any],
) -> tuple[str, str]:
    blocked = _blocked_class_from_markers(
        follow_to_unfollow_diagnostic,
        follow_to_unfollow_real,
    )
    if blocked:
        return "blocked", blocked
    if session_termination_class == "completed":
        return "not_needed", "session_completed"
    if session_termination_class == "partial_safe_but_continued":
        if follow_quota_remaining is not None and follow_quota_remaining > 0:
            return "eligible", "quota_remaining_after_safe_continued"
        return "not_needed", "safe_continued_no_known_quota_remaining"
    if session_termination_class in ("partial_safe_stopped", "partial_resumable"):
        if follow_quota_remaining is not None and follow_quota_remaining <= 0:
            return "not_needed", "no_quota_remaining"
        if follow_quota_remaining is None:
            return "unknown", "quota_remaining_unknown"
        return "eligible", "quota_remaining"
    if session_termination_class == "recoverable_failure":
        return "eligible", "recoverable_failure"
    if session_termination_class.startswith("blocked_"):
        return "blocked", session_termination_class
    if session_termination_class == "non_recoverable_failure":
        return "blocked", "non_recoverable_failure"
    return "unknown", "termination_class_unknown"


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
        is_unfollow_any = _is_unfollow_any_mode(mode)
        has_pending_unfollow = (
            pending_unfollow_count > 0
            if not is_unfollow_any
            else bool(settings.enabled)
        )
        pending_scope = (
            "following_ui_safe_candidate_required"
            if is_unfollow_any
            else "probe_limit_1"
        )

        summary.update(
            {
                "pending_unfollow_count": pending_unfollow_count,
                "pending_unfollow_count_scope": pending_scope,
                "has_pending_unfollow": has_pending_unfollow,
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
        if not _h3_supports_unfollow_mode(mode):
            skip_reasons.append(
                "unfollow_mode_ui_dependent"
                if mode.startswith("unfollow-any")
                else "unfollow_mode_not_supported_offline"
            )
        if not is_unfollow_any and pending_unfollow_count <= 0:
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


def _follow_to_unfollow_real_enabled(account_id: str | None = None) -> bool:
    if not account_id:
        return bool(getattr(config, "ACCOUNT_SESSION_FOLLOW_TO_UNFOLLOW_REAL_ENABLED", False))
    try:
        settings = load_unfollow_settings(str(account_id), ensure_row=False)
    except Exception:
        return bool(getattr(config, "ACCOUNT_SESSION_FOLLOW_TO_UNFOLLOW_REAL_ENABLED", False))

    if str(getattr(settings, "runtime_cap_mode", "prod_normal") or "prod_normal") == "prod_normal":
        return bool(
            getattr(settings, "enabled", False)
            and str(getattr(settings, "mode", "") or "") in H3_SUPPORTED_UNFOLLOW_MODES
            and int(getattr(settings, "session_limit", 0) or 0) > 0
            and int(getattr(settings, "day_limit", 0) or 0) > 0
        )
    return bool(getattr(config, "ACCOUNT_SESSION_FOLLOW_TO_UNFOLLOW_REAL_ENABLED", False))


def _follow_to_unfollow_real_max_actions_requested() -> int:
    try:
        return int(getattr(config, "ACCOUNT_SESSION_FOLLOW_TO_UNFOLLOW_REAL_MAX_ACTIONS", 1))
    except (TypeError, ValueError):
        return 1


def _follow_to_unfollow_real_hard_max() -> int:
    try:
        raw = int(getattr(config, "ACCOUNT_SESSION_FOLLOW_TO_UNFOLLOW_REAL_HARD_MAX", 3))
    except (TypeError, ValueError):
        raw = 3
    return max(0, min(raw, 10))


def _h3_nonnegative_int(value: Any, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return max(0, int(default))


def _global_unfollow_session_real_max() -> int:
    return _h3_nonnegative_int(
        getattr(config, "UNFOLLOW_SESSION_REAL_ACTION_MAX_PER_RUN", 1),
        1,
    )


def _resolve_follow_to_unfollow_runtime_cap(account_id: str | None = None) -> dict[str, Any]:
    requested = _follow_to_unfollow_real_max_actions_requested()
    hard_max = _follow_to_unfollow_real_hard_max()
    h3_env_effective = max(0, min(int(requested), hard_max))
    global_unfollow_env_cap = _global_unfollow_session_real_max()
    env_effective = min(h3_env_effective, global_unfollow_env_cap)
    if not account_id:
        return {
            "runtime_cap": env_effective,
            "runtime_hard_cap": hard_max,
            "runtime_cap_mode": "env_fallback",
            "runtime_cap_source": "env_fallback_unfollow_runtime_cap",
            "env_fallback_used": True,
            "h3_requested_cap": requested,
            "h3_hard_cap": hard_max,
            "h3_env_cap": h3_env_effective,
            "global_unfollow_env_cap": global_unfollow_env_cap,
            "source": "min(h3_requested,h3_hard,global_unfollow_env)",
        }

    settings = load_unfollow_settings(str(account_id), ensure_row=False)
    runtime = resolve_unfollow_runtime_cap(
        db_unfollow_per_session_limit=getattr(settings, "session_limit", 0),
        runtime_cap_mode=getattr(settings, "runtime_cap_mode", "prod_normal"),
        runtime_safety_cap=getattr(settings, "runtime_safety_cap", None),
        env_real_action_max_per_run=env_effective,
    )
    db_day_limit = _h3_nonnegative_int(getattr(settings, "day_limit", 0), 0)
    try:
        unfollows_done_today = supabase_client.count_successful_unfollows_today(str(account_id))
    except Exception:
        unfollows_done_today = db_day_limit
    day_remaining = max(0, db_day_limit - int(unfollows_done_today or 0))
    domain_cap = _h3_nonnegative_int(runtime.get("runtime_cap"), 0)
    effective = min(domain_cap, env_effective, day_remaining)
    out = dict(runtime)
    out.update(
        {
            "runtime_cap": effective,
            "runtime_hard_cap": min(
                _h3_nonnegative_int(runtime.get("runtime_hard_cap"), domain_cap),
                hard_max,
                global_unfollow_env_cap,
            ),
            "h3_requested_cap": requested,
            "h3_hard_cap": hard_max,
            "h3_env_cap": h3_env_effective,
            "global_unfollow_env_cap": global_unfollow_env_cap,
            "db_unfollow_per_day_limit": db_day_limit,
            "unfollows_done_today": int(unfollows_done_today or 0),
            "unfollow_day_remaining_today": day_remaining,
            "source": "min(domain_runtime,h3_requested,h3_hard,global_unfollow_env,db_day_remaining)",
        }
    )
    return out


def _follow_to_unfollow_real_max_actions_effective(account_id: str | None = None) -> int:
    return int(_resolve_follow_to_unfollow_runtime_cap(account_id).get("runtime_cap") or 0)


def _account_session_outreach_addon_enabled() -> bool:
    return bool(getattr(config, "ACCOUNT_SESSION_OUTREACH_ADDON_ENABLED", False))


def _account_session_outreach_addon_max_jobs() -> int:
    try:
        return max(0, int(getattr(config, "ACCOUNT_SESSION_OUTREACH_ADDON_MAX_JOBS", 1)))
    except (TypeError, ValueError):
        return 1


def _skip_account_session_outreach_addon(
    *,
    enabled: bool,
    reason: str,
    max_jobs: int | None = None,
    prepare_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    out = {
        "enabled": bool(enabled),
        "executed": False,
        "status": "disabled" if not enabled else "skipped",
        "skip_reason": str(reason or ""),
        "max_jobs": max_jobs,
        "prepared_jobs_count": 0,
        "jobs_claimed": 0,
        "jobs_completed": 0,
        "jobs_failed": 0,
        "jobs_skipped": 0,
    }
    if prepare_summary:
        out["prepare_summary"] = dict(prepare_summary)
        out["prepared_jobs_count"] = int(prepare_summary.get("prepared_jobs_count") or 0)
    return out


def _run_account_session_outreach_addon(
    d: u2.Device,
    *,
    account_id: str,
    account_username: str,
    run_id: str | None,
) -> dict[str, Any]:
    enabled = _account_session_outreach_addon_enabled()
    max_jobs = _account_session_outreach_addon_max_jobs()
    if not enabled:
        return _skip_account_session_outreach_addon(
            enabled=False,
            reason="addon_disabled",
            max_jobs=max_jobs,
        )
    if max_jobs <= 0:
        return _skip_account_session_outreach_addon(
            enabled=True,
            reason="addon_max_jobs_zero",
            max_jobs=max_jobs,
        )

    from outreach_session_orchestrator import (
        dispatch_outreach_session,
        get_last_outreach_session_summary,
        prepare_outreach_session,
    )

    log(
        "info",
        "account_session_outreach_addon_started",
        account_id=account_id,
        account_username=account_username,
        run_id=run_id,
        max_jobs=max_jobs,
        external_queue_only=True,
    )
    prepare_summary = prepare_outreach_session(
        d,
        account_id=account_id,
        account_username=account_username,
        run_id=run_id,
        reject_unfollow_handoff_jobs=True,
        max_jobs_override=max_jobs,
    )
    prepare_status = str(prepare_summary.get("session_status") or "")
    prepared_jobs_count = int(prepare_summary.get("prepared_jobs_count") or 0)
    if int(prepare_summary.get("exit_code") or 0) != 0:
        out = _skip_account_session_outreach_addon(
            enabled=True,
            reason=str(prepare_summary.get("failure_reason") or "outreach_prepare_failed"),
            max_jobs=max_jobs,
            prepare_summary=prepare_summary,
        )
        out["status"] = "blocked"
        log("info", "account_session_outreach_addon_blocked", account_id=account_id, run_id=run_id, **out)
        return out
    if prepare_status == "no_quota":
        out = _skip_account_session_outreach_addon(
            enabled=True,
            reason="outreach_no_quota",
            max_jobs=max_jobs,
            prepare_summary=prepare_summary,
        )
        log("info", "account_session_outreach_addon_skipped", account_id=account_id, run_id=run_id, **out)
        return out
    if prepared_jobs_count <= 0:
        out = _skip_account_session_outreach_addon(
            enabled=True,
            reason="no_pending_outreach_job",
            max_jobs=max_jobs,
            prepare_summary=prepare_summary,
        )
        log("info", "account_session_outreach_addon_skipped", account_id=account_id, run_id=run_id, **out)
        return out

    exit_code = dispatch_outreach_session(
        d,
        account_id=account_id,
        account_username=account_username,
        run_id=run_id,
        prepared_outreach=prepare_summary,
    )
    outreach_summary = get_last_outreach_session_summary()
    jobs_claimed = int(outreach_summary.get("jobs_claimed") or 0)
    jobs_completed = int(outreach_summary.get("jobs_completed") or 0)
    jobs_failed = int(outreach_summary.get("jobs_failed") or 0)
    jobs_skipped = int(outreach_summary.get("jobs_skipped") or 0)
    out = {
        "enabled": True,
        "executed": True,
        "status": str(outreach_summary.get("session_status") or ("completed" if exit_code == 0 else "failed")),
        "exit_code": int(exit_code),
        "max_jobs": max_jobs,
        "prepared_jobs_count": prepared_jobs_count,
        "prepared_job_ids": list(prepare_summary.get("prepared_job_ids") or []),
        "jobs_claimed": jobs_claimed,
        "jobs_completed": jobs_completed,
        "jobs_failed": jobs_failed,
        "jobs_skipped": jobs_skipped,
        "prepare_summary": prepare_summary,
        "outreach_summary": outreach_summary,
    }
    log("info", "account_session_outreach_addon_completed", account_id=account_id, run_id=run_id, **out)
    return out


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
    Follow -> Unfollow handoff surface prep shared by H2 probe and H3 real.

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


def _real_summary_from_unfollow_summary(
    *,
    enabled: bool,
    executed: bool,
    exit_code: int | None,
    unfollow_summary: dict[str, Any],
    real_max_actions_requested: int,
    real_max_actions_effective: int,
    real_hard_max: int,
    follow_exit_gate: dict[str, Any] | None = None,
    skip_reason: str = "",
) -> dict[str, Any]:
    gate = dict(follow_exit_gate or {})
    mode = str(unfollow_summary.get("unfollow_mode") or "")
    status = str(unfollow_summary.get("status") or "")
    failure_reason = str(unfollow_summary.get("failure_reason") or "")
    actions_sent = int(unfollow_summary.get("unfollow_actions_sent") or 0)
    if _is_unfollow_any_mode(mode):
        if actions_sent > 0:
            skip_reason = "unfollow_any_executed"
        elif failure_reason == "active_instagram_account_mismatch":
            skip_reason = "unfollow_any_identity_guard_failed"
        elif status in {
            "no_visible_eligible_unfollow_target",
            "no_more_following_rows",
        }:
            skip_reason = "unfollow_any_no_safe_candidate"
            failure_reason = failure_reason or "unfollow_any_no_safe_candidate"
        elif status in {"no_quota"} or failure_reason == "unfollow_day_limit_reached":
            skip_reason = "unfollow_any_cap_exhausted"
            failure_reason = failure_reason or "unfollow_any_cap_exhausted"
        elif status in {"failed_open_following", "failed_surface"}:
            skip_reason = "unfollow_any_surface_unavailable"
            failure_reason = failure_reason or "unfollow_any_surface_unavailable"
    return {
        "enabled": bool(enabled),
        "executed": bool(executed),
        "probe_only": False,
        "status": str(unfollow_summary.get("status") or ""),
        "exit_code": exit_code,
        "real_max_actions": int(real_max_actions_effective),
        "real_max_actions_requested": int(real_max_actions_requested),
        "real_hard_max": int(real_hard_max),
        "real_max_actions_effective": int(real_max_actions_effective),
        "following_surface_ok": bool(unfollow_summary.get("following_surface_ok")),
        "visible_rows_count": int(unfollow_summary.get("visible_rows_count") or 0),
        "visible_plan_matches_count": int(
            unfollow_summary.get("visible_plan_matches_count") or 0
        ),
        "unfollow_actions_sent": actions_sent,
        "unfollow_actions_verified": int(
            unfollow_summary.get("unfollow_actions_verified") or 0
        ),
        "unfollow_actions_failed": int(
            unfollow_summary.get("unfollow_actions_failed") or 0
        ),
        "unfollow_results_persisted_count": int(
            unfollow_summary.get("unfollow_results_persisted_count") or 0
        ),
        "failure_reason": failure_reason,
        "unfollow_total_ms": float(unfollow_summary.get("total_ms") or 0.0),
        "skip_reason": str(skip_reason or ""),
        "follow_exit_code_allowed": bool(gate.get("follow_exit_code_allowed")),
        "follow_exit_code_allow_reason": str(gate.get("follow_exit_code_allow_reason") or ""),
        "follow_exit_code_block_reason": str(gate.get("follow_exit_code_block_reason") or ""),
        "allowed_follow_exit_codes": list(gate.get("allowed_follow_exit_codes") or [0, 97]),
        "safe_partial_follow_exit_code": int(gate.get("safe_partial_follow_exit_code") or 97),
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


def _skip_follow_to_unfollow_real(
    *,
    account_id: str,
    account_username: str,
    run_id: str | None,
    real_enabled: bool,
    skip_reason: str,
    diagnostic: dict[str, Any],
    follow_exit_code: int | None,
    real_max_actions_requested: int,
    real_max_actions_effective: int,
    real_hard_max: int,
    follow_exit_gate: dict[str, Any] | None = None,
    failure_reason: str = "",
    surface_prep: dict[str, Any] | None = None,
) -> dict[str, Any]:
    gate = dict(follow_exit_gate or {})
    summary = {
        "enabled": bool(real_enabled),
        "executed": False,
        "probe_only": False,
        "skip_reason": str(skip_reason or "real_handoff_skipped"),
        "status": "skipped",
        "exit_code": None,
        "real_max_actions": int(real_max_actions_effective),
        "real_max_actions_requested": int(real_max_actions_requested),
        "real_hard_max": int(real_hard_max),
        "real_max_actions_effective": int(real_max_actions_effective),
        "unfollow_actions_sent": 0,
        "unfollow_actions_verified": 0,
        "unfollow_actions_failed": 0,
        "unfollow_results_persisted_count": 0,
        "following_surface_ok": False,
        "visible_rows_count": 0,
        "visible_plan_matches_count": 0,
        "failure_reason": str(failure_reason or ""),
        "follow_exit_code_allowed": bool(gate.get("follow_exit_code_allowed")),
        "follow_exit_code_allow_reason": str(gate.get("follow_exit_code_allow_reason") or ""),
        "follow_exit_code_block_reason": str(gate.get("follow_exit_code_block_reason") or ""),
        "allowed_follow_exit_codes": list(gate.get("allowed_follow_exit_codes") or [0, 97]),
        "safe_partial_follow_exit_code": int(gate.get("safe_partial_follow_exit_code") or 97),
    }
    if surface_prep:
        summary.update(surface_prep)
    log(
        "info",
        "follow_to_unfollow_handoff_real_skipped",
        account_id=account_id,
        account_username=account_username,
        run_id=run_id,
        skip_reason=summary["skip_reason"],
        real_enabled=bool(real_enabled),
        handoff_would_run=bool(diagnostic.get("handoff_would_run")),
        follow_exit_code=follow_exit_code,
        unfollow_enabled=bool(diagnostic.get("unfollow_enabled")),
        unfollow_mode=str(diagnostic.get("unfollow_mode") or ""),
        has_pending_unfollow=bool(diagnostic.get("has_pending_unfollow")),
        real_max_actions=int(real_max_actions_effective),
        real_max_actions_requested=int(real_max_actions_requested),
        real_max_actions_effective=int(real_max_actions_effective),
        follow_exit_code_allowed=summary["follow_exit_code_allowed"],
        follow_exit_code_allow_reason=summary["follow_exit_code_allow_reason"],
        follow_exit_code_block_reason=summary["follow_exit_code_block_reason"],
        allowed_follow_exit_codes=summary["allowed_follow_exit_codes"],
        safe_partial_follow_exit_code=summary["safe_partial_follow_exit_code"],
        failure_reason=summary["failure_reason"] or None,
    )
    return summary


def _follow_to_unfollow_real_skip_reason(
    *,
    account_id: str,
    account_username: str,
    follow_exit_code: int | None,
    diagnostic: dict[str, Any],
    real_max_actions_effective: int,
    follow_exit_gate: dict[str, Any] | None = None,
) -> str:
    gate = dict(follow_exit_gate or {})
    if not str(account_id or "").strip() or not str(account_username or "").strip():
        return "missing_account_context"
    if not bool(gate.get("follow_exit_code_allowed")):
        return str(gate.get("follow_exit_code_block_reason") or "follow_exit_code_not_allowed_h3_real")
    if not bool(diagnostic.get("unfollow_enabled")):
        return "unfollow_disabled"
    mode = str(diagnostic.get("unfollow_mode") or "")
    is_unfollow_any = _is_unfollow_any_mode(mode)
    if not _h3_supports_unfollow_mode(mode):
        return "unfollow_skipped_mode_not_supported_for_h3_real"
    if int(real_max_actions_effective) < 1:
        return "unfollow_any_cap_exhausted" if is_unfollow_any else "real_max_actions_invalid"
    if not is_unfollow_any and not bool(diagnostic.get("has_pending_unfollow")):
        return "unfollow_skipped_no_safe_candidate"
    if not bool(diagnostic.get("handoff_would_run")):
        return str(diagnostic.get("handoff_skip_reason") or "handoff_gates_not_met")
    return ""


def _evaluate_h3_follow_exit_code_gate(
    *,
    account_id: str,
    account_username: str,
    follow_exit_code: int | None,
    diagnostic: dict[str, Any],
    real_max_actions_effective: int,
) -> dict[str, Any]:
    allowed_codes = [0, 97]
    out: dict[str, Any] = {
        "follow_exit_code": follow_exit_code,
        "follow_exit_code_allowed": False,
        "follow_exit_code_allow_reason": "",
        "follow_exit_code_block_reason": "",
        "allowed_follow_exit_codes": allowed_codes,
        "safe_partial_follow_exit_code": 97,
        "follow_phase_executed": True,
        "follows_completed_count": None,
        "follow_session_outcome": "",
        "follow_stop_reason": "",
    }
    if follow_exit_code == 0:
        out.update(
            {
                "follow_exit_code_allowed": True,
                "follow_exit_code_allow_reason": "follow_completed",
            }
        )
        return out
    if follow_exit_code != 97:
        out["follow_exit_code_block_reason"] = "follow_exit_code_not_allowed_h3_real"
        return out

    blockers: list[str] = []
    if not str(account_id or "").strip() or not str(account_username or "").strip():
        blockers.append("missing_account_context")
    if not bool(diagnostic.get("handoff_would_run")):
        blockers.append(str(diagnostic.get("handoff_skip_reason") or "handoff_gates_not_met"))
    if not bool(diagnostic.get("unfollow_enabled")):
        blockers.append("unfollow_disabled")
    mode = str(diagnostic.get("unfollow_mode") or "")
    is_unfollow_any = _is_unfollow_any_mode(mode)
    if not _h3_supports_unfollow_mode(mode):
        blockers.append("unfollow_skipped_mode_not_supported_for_h3_real")
    if not is_unfollow_any and int(diagnostic.get("pending_unfollow_count") or 0) <= 0:
        blockers.append("unfollow_skipped_no_safe_candidate")
    if int(real_max_actions_effective) < 1:
        blockers.append("unfollow_any_cap_exhausted" if is_unfollow_any else "real_max_actions_invalid")

    diagnostic_blob = " ".join(str(v).lower() for v in diagnostic.values())
    unsafe_markers = (
        "active_instagram_account_mismatch",
        "account_mismatch",
        "challenge",
        "restriction",
        "restricted",
        "blocked",
        "crash",
        "exception",
    )
    for marker in unsafe_markers:
        if marker in diagnostic_blob:
            blockers.append(f"unsafe_follow_signal_{marker}")
            break

    if blockers:
        out["follow_exit_code_block_reason"] = "|".join(
            reason for reason in dict.fromkeys(blockers) if reason
        )
        return out

    out.update(
        {
            "follow_exit_code_allowed": True,
            "follow_exit_code_allow_reason": "partial_safe_follow_exit_97",
        }
    )
    return out


def _run_follow_to_unfollow_real(
    d: u2.Device,
    *,
    account_id: str,
    account_username: str,
    run_id: str | None,
    follow_exit_code: int | None,
    follow_total_ms: float,
    diagnostic: dict[str, Any],
) -> dict[str, Any]:
    """H3 only: explicit real Unfollow handoff with a hard low cap."""
    t0 = time.perf_counter()
    aid = str(account_id or "").strip()
    uname = str(account_username or "").strip()
    mode = str(diagnostic.get("unfollow_mode") or "")
    pending_count = int(diagnostic.get("pending_unfollow_count") or 0)
    real_max_requested = _follow_to_unfollow_real_max_actions_requested()
    runtime_cap_resolution = _resolve_follow_to_unfollow_runtime_cap(aid)
    real_hard_max = int(runtime_cap_resolution.get("runtime_hard_cap") or _follow_to_unfollow_real_hard_max())
    real_max_effective = int(runtime_cap_resolution.get("runtime_cap") or 0)
    surface_prep: dict[str, Any] = {}
    follow_exit_gate = _evaluate_h3_follow_exit_code_gate(
        account_id=aid,
        account_username=uname,
        follow_exit_code=follow_exit_code,
        diagnostic=diagnostic,
        real_max_actions_effective=real_max_effective,
    )

    log(
        "info",
        "follow_to_unfollow_handoff_real_started",
        account_id=aid,
        account_username=uname,
        run_id=run_id,
        previous_phase="follow",
        follow_exit_code=follow_exit_code,
        follow_total_ms=round(float(follow_total_ms), 2),
        unfollow_mode=mode,
        pending_unfollow_count=pending_count,
        real_max_actions=int(real_max_effective),
        real_max_actions_requested=int(real_max_requested),
        real_hard_max=int(real_hard_max),
        real_max_actions_effective=int(real_max_effective),
        runtime_cap_mode=str(runtime_cap_resolution.get("runtime_cap_mode") or ""),
        runtime_cap_source=str(runtime_cap_resolution.get("runtime_cap_source") or ""),
        surface_prep_required=True,
    )
    log(
        "info",
        "follow_to_unfollow_handoff_follow_exit_code_evaluated",
        account_id=aid,
        account_username=uname,
        run_id=run_id,
        follow_exit_code=follow_exit_code,
        follow_exit_code_allowed=bool(follow_exit_gate.get("follow_exit_code_allowed")),
        follow_exit_code_allow_reason=str(follow_exit_gate.get("follow_exit_code_allow_reason") or ""),
        follow_exit_code_block_reason=str(follow_exit_gate.get("follow_exit_code_block_reason") or ""),
        allowed_follow_exit_codes=list(follow_exit_gate.get("allowed_follow_exit_codes") or [0, 97]),
        safe_partial_follow_exit_code=int(follow_exit_gate.get("safe_partial_follow_exit_code") or 97),
        follow_phase_executed=bool(follow_exit_gate.get("follow_phase_executed")),
        follows_completed_count=follow_exit_gate.get("follows_completed_count"),
        follow_session_outcome=str(follow_exit_gate.get("follow_session_outcome") or ""),
        follow_stop_reason=str(follow_exit_gate.get("follow_stop_reason") or ""),
    )

    skip_reason = _follow_to_unfollow_real_skip_reason(
        account_id=aid,
        account_username=uname,
        follow_exit_code=follow_exit_code,
        diagnostic=diagnostic,
        real_max_actions_effective=real_max_effective,
        follow_exit_gate=follow_exit_gate,
    )
    if skip_reason:
        return _skip_follow_to_unfollow_real(
            account_id=aid,
            account_username=uname,
            run_id=run_id,
            real_enabled=True,
            skip_reason=skip_reason,
            diagnostic=diagnostic,
            follow_exit_code=follow_exit_code,
            real_max_actions_requested=real_max_requested,
            real_max_actions_effective=real_max_effective,
            real_hard_max=real_hard_max,
            follow_exit_gate=follow_exit_gate,
        )

    try:
        surface_prep = _prepare_follow_to_unfollow_probe_surface(
            d,
            account_id=aid,
            account_username=uname,
            run_id=run_id,
        )
        if not bool(surface_prep.get("surface_prep_ok")):
            surface_skip_reason = (
                "unfollow_any_surface_unavailable"
                if _is_unfollow_any_mode(mode)
                else "surface_prep_failed"
            )
            return _skip_follow_to_unfollow_real(
                account_id=aid,
                account_username=uname,
                run_id=run_id,
                real_enabled=True,
                skip_reason=surface_skip_reason,
                diagnostic=diagnostic,
                follow_exit_code=follow_exit_code,
                real_max_actions_requested=real_max_requested,
                real_max_actions_effective=real_max_effective,
                real_hard_max=real_hard_max,
                follow_exit_gate=follow_exit_gate,
                failure_reason=str(
                    surface_prep.get("surface_prep_failure_reason")
                    or "surface_prep_failed"
                ),
                surface_prep=surface_prep,
            )

        exit_code = run_unfollow_session(
            d,
            account_id=aid,
            account_username=uname,
            run_id=run_id,
            dry_probe_only=False,
            real_action_enabled_override=True,
            real_action_max_override=real_max_effective,
        )
        unfollow_summary = get_last_unfollow_session_probe_summary()
        out = _real_summary_from_unfollow_summary(
            enabled=True,
            executed=True,
            exit_code=int(exit_code),
            unfollow_summary=unfollow_summary,
            real_max_actions_requested=real_max_requested,
            real_max_actions_effective=real_max_effective,
            real_hard_max=real_hard_max,
            follow_exit_gate=follow_exit_gate,
        )
        out.update(surface_prep)
        out["total_ms"] = round((time.perf_counter() - t0) * 1000.0, 2)

        if int(out.get("unfollow_actions_sent") or 0) > int(real_max_effective):
            out["status"] = "failed_real_actions_cap_exceeded"
            out["failure_reason"] = "real_actions_cap_exceeded"
            log(
                "error",
                "follow_to_unfollow_handoff_real_failed",
                account_id=aid,
                account_username=uname,
                run_id=run_id,
                status=out["status"],
                failure_reason=out["failure_reason"],
                unfollow_actions_sent=out.get("unfollow_actions_sent"),
                real_max_actions=int(real_max_effective),
            )

        log(
            "info",
            "follow_to_unfollow_handoff_real_completed",
            account_id=aid,
            account_username=uname,
            run_id=run_id,
            status=out.get("status"),
            exit_code=out.get("exit_code"),
            unfollow_actions_sent=out.get("unfollow_actions_sent"),
            unfollow_actions_verified=out.get("unfollow_actions_verified"),
            unfollow_actions_failed=out.get("unfollow_actions_failed"),
            unfollow_results_persisted_count=out.get("unfollow_results_persisted_count"),
            following_surface_ok=out.get("following_surface_ok"),
            visible_rows_count=out.get("visible_rows_count"),
            visible_plan_matches_count=out.get("visible_plan_matches_count"),
            failure_reason=out.get("failure_reason"),
            total_ms=out.get("total_ms"),
            real_max_actions=out.get("real_max_actions"),
            real_max_actions_requested=out.get("real_max_actions_requested"),
            real_hard_max=out.get("real_hard_max"),
            real_max_actions_effective=out.get("real_max_actions_effective"),
            follow_exit_code_allowed=out.get("follow_exit_code_allowed"),
            follow_exit_code_allow_reason=out.get("follow_exit_code_allow_reason"),
            follow_exit_code_block_reason=out.get("follow_exit_code_block_reason"),
            surface_prep_attempted=out.get("surface_prep_attempted"),
            surface_prep_ok=out.get("surface_prep_ok"),
        )
        return out
    except Exception as e:
        out = {
            "enabled": True,
            "executed": True,
            "probe_only": False,
            "status": "failed_exception",
            "exit_code": 1,
            "real_max_actions": int(real_max_effective),
            "real_max_actions_requested": int(real_max_requested),
            "real_hard_max": int(real_hard_max),
            "real_max_actions_effective": int(real_max_effective),
            "follow_exit_code_allowed": bool(follow_exit_gate.get("follow_exit_code_allowed")),
            "follow_exit_code_allow_reason": str(follow_exit_gate.get("follow_exit_code_allow_reason") or ""),
            "follow_exit_code_block_reason": str(follow_exit_gate.get("follow_exit_code_block_reason") or ""),
            "allowed_follow_exit_codes": list(follow_exit_gate.get("allowed_follow_exit_codes") or [0, 97]),
            "safe_partial_follow_exit_code": int(follow_exit_gate.get("safe_partial_follow_exit_code") or 97),
            "following_surface_ok": False,
            "visible_rows_count": 0,
            "visible_plan_matches_count": 0,
            "unfollow_actions_sent": 0,
            "unfollow_actions_verified": 0,
            "unfollow_actions_failed": 0,
            "unfollow_results_persisted_count": 0,
            "failure_reason": str(e),
            "total_ms": round((time.perf_counter() - t0) * 1000.0, 2),
            **surface_prep,
        }
        log(
            "error",
            "follow_to_unfollow_handoff_real_failed",
            account_id=aid,
            account_username=uname,
            run_id=run_id,
            status=out["status"],
            failure_reason=out["failure_reason"],
            unfollow_actions_sent=0,
            real_max_actions=int(real_max_effective),
            surface_prep_attempted=out.get("surface_prep_attempted"),
            surface_prep_ok=out.get("surface_prep_ok"),
            total_ms=out["total_ms"],
        )
        return out


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
    target_id: str | None = None,
    follow_targets: list[dict[str, Any]] | None = None,
    max_follow_targets_per_run: int | None = None,
    max_follows_per_target_per_run: int | None = None,
    fast_rotate_to_next_target_from_followers: FastRotationRunner | None = None,
    auto_restart_resume_policy: dict[str, Any] | None = None,
) -> int:
    t0 = time.perf_counter()
    aid = str(account_id or "").strip()
    uname = str(account_username or "").strip()
    src = str(source_profile_username or "").strip()
    tid = str(target_id or "").strip()

    log(
        "info",
        "account_session_started",
        account_id=aid,
        account_username=uname,
        run_id=run_id,
        followers_source_username=src or None,
        target_id=tid or None,
    )

    session_policy_revision = str(
        (load_account_commercial_policy_revision(aid) or {}).get("revision_token") or ""
    ).strip() or None

    t_settings_load = time.perf_counter()
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
    _startup_timing_log(
        "startup_timing_target_selection_completed",
        t_settings_load,
        phase="target_settings",
        substep="account_dm_settings_load",
        source="account_session_orchestrator",
        cache_hit=False,
        reused_signal=False,
        duplicate_detected=False,
        account_id=aid,
        run_id=run_id,
        settings_loaded=bool(settings),
    )

    welcome_enabled = bool(settings.get("welcome_enabled"))
    if auto_restart_resume_policy:
        from auto_restart_runtime import phase_enabled

        welcome_enabled = phase_enabled("welcome", default=welcome_enabled, policy=auto_restart_resume_policy)
        log(
            "info",
            "auto_restart_resume_policy_applied",
            account_id=aid,
            run_id=run_id,
            prior_run_id=auto_restart_resume_policy.get("prior_run_id"),
            phases_to_run=auto_restart_resume_policy.get("phases_to_run"),
        )
    real_send_enabled, real_send_source = resolve_welcome_dm_real_send_enabled()

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
    elif commercial_policy_boundary_blocks_phase(
        aid,
        bound_revision=session_policy_revision,
        run_id=run_id,
        boundary="before_welcome_phase",
    ):
        welcome_bypass_reason = "commercial_policy_revision_changed"
        welcome_session_status = "skipped"
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
    follow_engine_summary: dict[str, Any] = {}
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
    initial_real_enabled = _follow_to_unfollow_real_enabled(aid)
    initial_real_max_effective = _follow_to_unfollow_real_max_actions_effective(aid)
    follow_to_unfollow_real: dict[str, Any] = {
        "enabled": initial_real_enabled,
        "executed": False,
        "probe_only": False,
        "skip_reason": "real_handoff_disabled"
        if not initial_real_enabled
        else "follow_phase_not_completed",
        "status": "skipped",
        "real_max_actions": initial_real_max_effective,
        "real_max_actions_requested": _follow_to_unfollow_real_max_actions_requested(),
        "real_hard_max": _follow_to_unfollow_real_hard_max(),
        "real_max_actions_effective": initial_real_max_effective,
        "follow_exit_code_allowed": False,
        "follow_exit_code_allow_reason": "",
        "follow_exit_code_block_reason": "",
        "allowed_follow_exit_codes": [0, 97],
        "safe_partial_follow_exit_code": 97,
        "unfollow_actions_sent": 0,
        "unfollow_actions_verified": 0,
        "unfollow_actions_failed": 0,
        "unfollow_results_persisted_count": 0,
    }
    account_session_outreach_addon: dict[str, Any] = _skip_account_session_outreach_addon(
        enabled=_account_session_outreach_addon_enabled(),
        reason="addon_disabled"
        if not _account_session_outreach_addon_enabled()
        else "follow_phase_not_completed",
        max_jobs=_account_session_outreach_addon_max_jobs(),
    )

    run_follow, transition_reason = _should_run_follow_after_welcome(
        welcome_enabled=welcome_enabled,
        real_send_enabled=real_send_enabled,
        welcome_phase_executed=welcome_phase_executed,
        welcome_exit_code=welcome_exit_code,
        scan_summary=scan_summary,
        sender_summary=sender_summary,
        welcome_session_status=welcome_session_status,
    )
    if auto_restart_resume_policy:
        from auto_restart_runtime import phase_enabled

        if not phase_enabled("follow", default=run_follow, policy=auto_restart_resume_policy):
            run_follow = False
            follow_phase_skipped_reason = "auto_restart_resume_skip_follow"

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
            if commercial_policy_boundary_blocks_phase(
                aid,
                bound_revision=session_policy_revision,
                run_id=run_id,
                boundary="before_follow_phase",
            ):
                follow_ready = False
                follow_phase_skipped_reason = "commercial_policy_revision_changed"
            log(
                "info",
                "account_session_follow_phase_started",
                account_id=aid,
                run_id=run_id,
                followers_source_username=src,
                target_id=tid or None,
                transition_reason=transition_reason,
                handoff_applied=bool(welcome_phase_executed),
                handoff_reason=(
                    handoff_result.reason if handoff_result is not None else None
                ),
            )
            follow_t0 = time.perf_counter()
            t_rotation_targets = time.perf_counter()
            rotation_targets = _build_follow_rotation_targets(
                follow_targets=follow_targets,
                fallback_source_profile=src,
                fallback_target_id=tid or None,
            )
            _startup_timing_log(
                "startup_timing_target_selection_completed",
                t_rotation_targets,
                phase="target_settings",
                substep="rotation_targets_built",
                source="account_session_orchestrator",
                cache_hit=False,
                reused_signal=bool(follow_targets),
                duplicate_detected=bool(follow_targets),
                account_id=aid,
                run_id=run_id,
                target_count=len(rotation_targets),
            )
            t_rotation_settings = time.perf_counter()
            rotation_settings = _resolve_follow_source_rotation_settings(aid)
            log(
                "info",
                "follow_source_rotation_settings_loaded",
                account_id=aid,
                run_id=run_id,
                max_follows_per_target_per_run=rotation_settings["max_follows_per_target_per_run"],
                max_targets_per_run=rotation_settings["max_targets_per_run"],
                settings_source=rotation_settings["settings_source"],
                bounds=rotation_settings["bounds"],
            )
            _startup_timing_log(
                "startup_timing_target_selection_completed",
                t_rotation_settings,
                phase="target_settings",
                substep="follow_source_rotation_settings_load",
                source="account_session_orchestrator",
                cache_hit=False,
                reused_signal=False,
                duplicate_detected=False,
                account_id=aid,
                run_id=run_id,
                settings_source=rotation_settings["settings_source"],
            )
            rotation_result = _run_follow_target_rotation(
                d,
                account_id=aid,
                account_username=uname,
                run_id=run_id,
                follow_targets=rotation_targets,
                run_followers_list_engine_session=run_followers_list_engine_session,
                supabase_mode=supabase_mode,
                warm_session_used=warm_session_used,
                force_stop_used=force_stop_used,
                max_targets_per_run=(
                    max_follow_targets_per_run
                    if max_follow_targets_per_run is not None
                    else int(rotation_settings["max_targets_per_run"])
                ),
                max_follows_per_target_per_run=(
                    max_follows_per_target_per_run
                    if max_follows_per_target_per_run is not None
                    else int(rotation_settings["max_follows_per_target_per_run"])
                ),
                fast_rotate_to_next_target_from_followers=fast_rotate_to_next_target_from_followers,
            )
            follow_t1 = time.perf_counter()
            follow_phase_executed = True
            follow_exit_code = int(rotation_result.get("exit_code") or 0)
            follow_engine_summary = dict(rotation_result.get("summary") or {})
            follow_engine_summary["rotation_attempts"] = list(rotation_result.get("attempts") or [])
            follow_engine_summary["exhausted_targets"] = list(rotation_result.get("exhausted_targets") or [])
            follow_engine_summary["rotation_reason"] = str(rotation_result.get("reason") or "")
            follow_engine_summary["follow_total_ms"] = round((follow_t1 - follow_t0) * 1000.0, 2)
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
            real_enabled = _follow_to_unfollow_real_enabled(aid)
            if auto_restart_resume_policy:
                from auto_restart_runtime import phase_enabled

                if not phase_enabled("unfollow", default=real_enabled, policy=auto_restart_resume_policy):
                    real_enabled = False
            if real_enabled:
                if commercial_policy_boundary_blocks_phase(
                    aid,
                    bound_revision=session_policy_revision,
                    run_id=run_id,
                    boundary="before_unfollow_phase",
                ):
                    follow_to_unfollow_real = _skip_follow_to_unfollow_real(
                        account_id=aid,
                        account_username=uname,
                        run_id=run_id,
                        real_enabled=False,
                        skip_reason="commercial_policy_revision_changed",
                        diagnostic=follow_to_unfollow_diagnostic,
                        follow_exit_code=follow_exit_code,
                        real_max_actions_requested=_follow_to_unfollow_real_max_actions_requested(),
                        real_max_actions_effective=_follow_to_unfollow_real_max_actions_effective(aid),
                        real_hard_max=_follow_to_unfollow_real_hard_max(),
                    )
                else:
                    if probe_enabled:
                        follow_to_unfollow_probe = _skip_follow_to_unfollow_probe(
                            account_id=aid,
                            account_username=uname,
                            run_id=run_id,
                            probe_enabled=True,
                            skip_reason="real_handoff_enabled",
                            diagnostic=follow_to_unfollow_diagnostic,
                        )
                        follow_to_unfollow_probe["probe_bypassed_reason"] = "real_handoff_enabled"
                    follow_to_unfollow_real = _run_follow_to_unfollow_real(
                        d,
                        account_id=aid,
                        account_username=uname,
                        run_id=run_id,
                        follow_exit_code=follow_exit_code,
                        follow_total_ms=(follow_t1 - follow_t0) * 1000.0,
                        diagnostic=follow_to_unfollow_diagnostic,
                    )
            else:
                real_skip_reason = (
                    "unfollow_any_handoff_disabled"
                    if _is_unfollow_any_mode(
                        str(follow_to_unfollow_diagnostic.get("unfollow_mode") or "")
                    )
                    else "real_handoff_disabled"
                )
                follow_to_unfollow_real = _skip_follow_to_unfollow_real(
                    account_id=aid,
                    account_username=uname,
                    run_id=run_id,
                    real_enabled=False,
                    skip_reason=real_skip_reason,
                    diagnostic=follow_to_unfollow_diagnostic,
                    follow_exit_code=follow_exit_code,
                    real_max_actions_requested=_follow_to_unfollow_real_max_actions_requested(),
                    real_max_actions_effective=_follow_to_unfollow_real_max_actions_effective(aid),
                    real_hard_max=_follow_to_unfollow_real_hard_max(),
                )
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

    if _account_session_outreach_addon_enabled():
        if not follow_phase_executed:
            account_session_outreach_addon = _skip_account_session_outreach_addon(
                enabled=True,
                reason="follow_phase_not_executed",
                max_jobs=_account_session_outreach_addon_max_jobs(),
            )
        elif not bool(follow_to_unfollow_real.get("executed")):
            account_session_outreach_addon = _skip_account_session_outreach_addon(
                enabled=True,
                reason=str(follow_to_unfollow_real.get("skip_reason") or "h3_unfollow_not_executed"),
                max_jobs=_account_session_outreach_addon_max_jobs(),
            )
        else:
            if commercial_policy_boundary_blocks_phase(
                aid,
                bound_revision=session_policy_revision,
                run_id=run_id,
                boundary="before_outreach_phase",
            ):
                account_session_outreach_addon = _skip_account_session_outreach_addon(
                    enabled=True,
                    reason="commercial_policy_revision_changed",
                    max_jobs=_account_session_outreach_addon_max_jobs(),
                )
            else:
                account_session_outreach_addon = _run_account_session_outreach_addon(
                    d,
                    account_id=aid,
                    account_username=uname,
                    run_id=run_id,
                )

    session_status = _account_session_status(
        transition_reason=transition_reason,
        follow_phase_executed=follow_phase_executed,
        follow_exit_code=follow_exit_code,
        welcome_blocked_follow=welcome_blocked_follow,
    )
    exit_code = 0 if session_status == "success" else 1
    total_ms = (time.perf_counter() - t0) * 1000.0
    follows_completed_count = _as_optional_int(
        follow_engine_summary.get("follows_completed_count")
    )
    follow_processed_count = _as_optional_int(
        follow_engine_summary.get("follow_processed_count")
    )
    follows_goal_effective = _as_optional_int(
        follow_engine_summary.get("follows_goal_effective")
    )
    follow_quota_target = follows_goal_effective
    follow_quota_remaining = (
        max(0, follow_quota_target - follows_completed_count)
        if follow_quota_target is not None and follows_completed_count is not None
        else None
    )
    follow_session_outcome = str(
        follow_engine_summary.get("follow_session_outcome") or ""
    )
    follow_stop_reason = str(follow_engine_summary.get("follow_stop_reason") or "")
    (
        welcome_phase_status,
        follow_phase_status,
        unfollow_phase_status,
    ) = _phase_statuses(
        welcome_enabled=welcome_enabled,
        welcome_phase_executed=welcome_phase_executed,
        welcome_session_status=welcome_session_status,
        follow_phase_executed=follow_phase_executed,
        follow_phase_skipped_reason=follow_phase_skipped_reason,
        follow_exit_code=follow_exit_code,
        follow_to_unfollow_real=follow_to_unfollow_real,
    )
    session_termination_class = _session_termination_class(
        session_status=session_status,
        follow_phase_executed=follow_phase_executed,
        follow_exit_code=follow_exit_code,
        follow_quota_remaining=follow_quota_remaining,
        follow_to_unfollow_diagnostic=follow_to_unfollow_diagnostic,
        follow_to_unfollow_real=follow_to_unfollow_real,
        follow_phase_skipped_reason=follow_phase_skipped_reason,
        transition_reason=transition_reason,
        follow_session_outcome=follow_session_outcome,
    )
    restart_eligibility, restart_block_reason = _restart_eligibility(
        session_termination_class=session_termination_class,
        follow_quota_remaining=follow_quota_remaining,
        follow_to_unfollow_diagnostic=follow_to_unfollow_diagnostic,
        follow_to_unfollow_real=follow_to_unfollow_real,
    )
    mandatory_unfollow_executed = bool(
        follow_to_unfollow_real.get("executed")
        and int(follow_to_unfollow_real.get("unfollow_actions_sent") or 0) > 0
    )
    auto_restart_v1b_dry_run = auto_restart_resume_policy is None
    auto_restart_v1b_enabled = bool(getattr(config, "AUTO_RESTART_ENABLED", False))
    auto_restart_resume_plan: dict[str, Any] | None = None
    auto_restart_resume_plan_error: str | None = None
    auto_restart_restart_allowed = False
    auto_restart_restart_block_reason = ""
    auto_restart_phases_to_run: dict[str, Any] | None = None
    auto_restart_quota_remaining: dict[str, Any] | None = None
    auto_restart_reason = ""
    try:
        auto_restart_summary = {
            "account_id": aid,
            "account_username": uname,
            "run_id": run_id,
            "session_status": session_status,
            "session_termination_class": session_termination_class,
            "restart_eligibility": restart_eligibility,
            "restart_block_reason": restart_block_reason,
            "welcome_enabled": welcome_enabled,
            "welcome_phase_status": welcome_phase_status,
            "follow_phase_status": follow_phase_status,
            "unfollow_phase_status": unfollow_phase_status,
            "follow_engine_exit_code": follow_exit_code,
            "follows_completed_count": follows_completed_count,
            "follow_processed_count": follow_processed_count,
            "follows_goal_effective": follows_goal_effective,
            "follow_quota_target": follow_quota_target,
            "follow_quota_remaining": follow_quota_remaining,
            "follow_session_outcome": follow_session_outcome or None,
            "follow_stop_reason": follow_stop_reason or None,
            "follow_to_unfollow_handoff_skip_reason": follow_to_unfollow_diagnostic.get(
                "handoff_skip_reason"
            ),
            "pending_unfollow_count": follow_to_unfollow_diagnostic.get(
                "pending_unfollow_count"
            ),
            "mandatory_unfollow_executed": mandatory_unfollow_executed,
            "unfollow_actions_verified": follow_to_unfollow_real.get(
                "unfollow_actions_verified"
            ),
            "unfollow_results_persisted_count": follow_to_unfollow_real.get(
                "unfollow_results_persisted_count"
            ),
            "follow_to_unfollow_real": follow_to_unfollow_real,
            "account_session_outreach_addon": account_session_outreach_addon,
        }
        log(
            "info",
            "auto_restart_v1b_dry_run_started",
            account_id=aid,
            account_username=uname,
            run_id=run_id,
            auto_restart_enabled=auto_restart_v1b_enabled,
            dry_run=auto_restart_v1b_dry_run,
            session_termination_class=session_termination_class,
            restart_eligibility=restart_eligibility,
        )
        auto_restart_resume_plan = build_account_session_resume_plan(
            auto_restart_summary,
            settings={
                "auto_restart_delay_minutes": getattr(
                    config,
                    "AUTO_RESTART_DELAY_MINUTES",
                    20,
                ),
                "auto_restart_max_attempts_per_session": getattr(
                    config,
                    "AUTO_RESTART_MAX_ATTEMPTS_PER_SESSION",
                    2,
                ),
            },
        )
        auto_restart_restart_allowed = bool(
            auto_restart_resume_plan.get("restart_allowed")
        )
        auto_restart_restart_block_reason = str(
            auto_restart_resume_plan.get("restart_block_reason") or ""
        )
        _phases = auto_restart_resume_plan.get("phases_to_run")
        auto_restart_phases_to_run = _phases if isinstance(_phases, dict) else None
        _quota_remaining = auto_restart_resume_plan.get("quota_remaining")
        auto_restart_quota_remaining = (
            _quota_remaining if isinstance(_quota_remaining, dict) else None
        )
        auto_restart_reason = str(auto_restart_resume_plan.get("reason") or "")
        log(
            "info",
            "auto_restart_v1b_dry_run_completed",
            account_id=aid,
            account_username=uname,
            run_id=run_id,
            auto_restart_enabled=auto_restart_v1b_enabled,
            dry_run=auto_restart_v1b_dry_run,
            restart_allowed=auto_restart_restart_allowed,
            restart_block_reason=auto_restart_restart_block_reason,
            phases_to_run=auto_restart_phases_to_run,
            quota_remaining=auto_restart_quota_remaining,
            reason=auto_restart_reason,
        )
    except Exception as e:
        auto_restart_resume_plan_error = str(e)
        auto_restart_restart_allowed = False
        auto_restart_restart_block_reason = "resume_plan_builder_failed"
        auto_restart_reason = "resume_plan_builder_failed"
        log(
            "warning",
            "auto_restart_v1b_dry_run_failed",
            account_id=aid,
            account_username=uname,
            run_id=run_id,
            auto_restart_enabled=auto_restart_v1b_enabled,
            dry_run=auto_restart_v1b_dry_run,
            error=auto_restart_resume_plan_error,
        )

    # P3: persist the end-of-session restart verdict on the canonical
    # per-run resume plan row (created early by the runner). Best-effort.
    if run_id:
        try:
            from account_session_resume_plan_store import record_end_of_session

            record_end_of_session(
                run_id=run_id,
                session_plan=auto_restart_resume_plan,
                session_status=session_status,
            )
        except Exception as e:
            log(
                "warning",
                "resume_plan_end_of_session_persist_failed",
                account_id=aid,
                run_id=run_id,
                error=str(e)[:300],
            )

    reliability_v1d_enabled = True
    reliability_v1d_dry_run = True
    admin_reliability_snapshot: dict[str, Any] | None = None
    admin_reliability_snapshot_error: str | None = None
    escalation_event: dict[str, Any] | None = None
    escalation_event_error: str | None = None
    escalation_required = False
    escalation_event_type: str | None = None
    escalation_severity: str | None = None
    escalation_reason: str | None = None
    escalation_action_required: str | None = None
    reliability_summary = {
        "account_id": aid,
        "account_username": uname,
        "run_id": run_id,
        "package_name": str(getattr(config, "INSTAGRAM_PACKAGE", "") or ""),
        "session_status": session_status,
        "session_termination_class": session_termination_class,
        "restart_eligibility": restart_eligibility,
        "restart_block_reason": restart_block_reason,
        "welcome_enabled": welcome_enabled,
        "welcome_phase_status": welcome_phase_status,
        "follow_phase_status": follow_phase_status,
        "unfollow_phase_status": unfollow_phase_status,
        "follow_engine_exit_code": follow_exit_code,
        "follows_completed_count": follows_completed_count,
        "follow_processed_count": follow_processed_count,
        "follows_goal_effective": follows_goal_effective,
        "follow_quota_target": follow_quota_target,
        "follow_quota_remaining": follow_quota_remaining,
        "follow_session_outcome": follow_session_outcome or None,
        "follow_stop_reason": follow_stop_reason or None,
        "welcome_sender_jobs_sent_count": sender_summary.get("jobs_sent_count"),
        "mandatory_unfollow_executed": mandatory_unfollow_executed,
        "unfollow_actions_verified": follow_to_unfollow_real.get(
            "unfollow_actions_verified"
        ),
        "unfollow_results_persisted_count": follow_to_unfollow_real.get(
            "unfollow_results_persisted_count"
        ),
        "follow_to_unfollow_real": follow_to_unfollow_real,
        "account_session_outreach_addon": account_session_outreach_addon,
        "auto_restart_restart_allowed": auto_restart_restart_allowed,
        "auto_restart_restart_block_reason": auto_restart_restart_block_reason,
        "auto_restart_resume_plan": auto_restart_resume_plan,
        "auto_restart_resume_plan_error": auto_restart_resume_plan_error,
    }
    log(
        "info",
        "reliability_v1d_dry_run_started",
        account_id=aid,
        account_username=uname,
        run_id=run_id,
        reliability_v1d_enabled=reliability_v1d_enabled,
        dry_run=reliability_v1d_dry_run,
        session_termination_class=session_termination_class,
        restart_eligibility=restart_eligibility,
    )
    try:
        admin_reliability_snapshot = build_admin_reliability_snapshot(
            reliability_summary,
            resume_plan=auto_restart_resume_plan,
        )
    except Exception as e:
        admin_reliability_snapshot_error = str(e)
        log(
            "warning",
            "reliability_v1d_dry_run_failed",
            account_id=aid,
            account_username=uname,
            run_id=run_id,
            reliability_v1d_enabled=reliability_v1d_enabled,
            dry_run=reliability_v1d_dry_run,
            stage="admin_reliability_snapshot",
            error=admin_reliability_snapshot_error,
        )
    try:
        escalation_event = build_escalation_event(
            reliability_summary,
            resume_plan=auto_restart_resume_plan,
        )
        if isinstance(escalation_event, dict):
            escalation_required = True
            escalation_event_type = str(escalation_event.get("event_type") or "")
            escalation_severity = str(escalation_event.get("severity") or "")
            escalation_reason = str(escalation_event.get("reason") or "")
            escalation_action_required = str(
                escalation_event.get("action_required") or ""
            )
    except Exception as e:
        escalation_event_error = str(e)
        log(
            "warning",
            "reliability_v1d_dry_run_failed",
            account_id=aid,
            account_username=uname,
            run_id=run_id,
            reliability_v1d_enabled=reliability_v1d_enabled,
            dry_run=reliability_v1d_dry_run,
            stage="escalation_event",
            error=escalation_event_error,
        )
    log(
        "info",
        "reliability_v1d_dry_run_completed",
        account_id=aid,
        account_username=uname,
        run_id=run_id,
        reliability_v1d_enabled=reliability_v1d_enabled,
        dry_run=reliability_v1d_dry_run,
        snapshot_built=admin_reliability_snapshot is not None,
        escalation_required=escalation_required,
        escalation_event_type=escalation_event_type,
        escalation_severity=escalation_severity,
        escalation_reason=escalation_reason,
        snapshot_error=admin_reliability_snapshot_error,
        escalation_error=escalation_event_error,
    )

    log(
        "info",
        "account_session_summary",
        account_id=aid,
        account_username=uname,
        run_id=run_id,
        total_ms=round(total_ms, 2),
        session_status=session_status,
        session_termination_class=session_termination_class,
        restart_eligibility=restart_eligibility,
        restart_block_reason=restart_block_reason,
        transition_reason=transition_reason,
        welcome_phase_status=welcome_phase_status,
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
        follow_phase_status=follow_phase_status,
        follow_phase_executed=follow_phase_executed,
        follow_phase_skipped_reason=follow_phase_skipped_reason,
        followers_source_username=src,
        follow_engine_exit_code=follow_exit_code,
        follows_completed_count=follows_completed_count,
        follow_session_outcome=follow_session_outcome or None,
        follow_stop_reason=follow_stop_reason or None,
        follow_processed_count=follow_processed_count,
        follows_goal_effective=follows_goal_effective,
        follow_quota_target=follow_quota_target,
        follow_quota_remaining=follow_quota_remaining,
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
        unfollow_phase_status=unfollow_phase_status,
        follow_to_unfollow_probe=follow_to_unfollow_probe,
        follow_to_unfollow_real=follow_to_unfollow_real,
        account_session_outreach_addon=account_session_outreach_addon,
        mandatory_unfollow_executed=mandatory_unfollow_executed,
        auto_restart_v1b_enabled=auto_restart_v1b_enabled,
        auto_restart_v1b_dry_run=auto_restart_v1b_dry_run,
        auto_restart_resume_plan=auto_restart_resume_plan,
        auto_restart_resume_plan_error=auto_restart_resume_plan_error,
        auto_restart_restart_allowed=auto_restart_restart_allowed,
        auto_restart_restart_block_reason=auto_restart_restart_block_reason,
        auto_restart_phases_to_run=auto_restart_phases_to_run,
        auto_restart_quota_remaining=auto_restart_quota_remaining,
        auto_restart_reason=auto_restart_reason,
        reliability_v1d_enabled=reliability_v1d_enabled,
        reliability_v1d_dry_run=reliability_v1d_dry_run,
        admin_reliability_snapshot=admin_reliability_snapshot,
        admin_reliability_snapshot_error=admin_reliability_snapshot_error,
        escalation_event=escalation_event,
        escalation_event_error=escalation_event_error,
        escalation_required=escalation_required,
        escalation_event_type=escalation_event_type,
        escalation_severity=escalation_severity,
        escalation_reason=escalation_reason,
        escalation_action_required=escalation_action_required,
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
    target_id: str | None = None,
    follow_targets: list[dict[str, Any]] | None = None,
    max_follow_targets_per_run: int | None = None,
    max_follows_per_target_per_run: int | None = None,
    fast_rotate_to_next_target_from_followers: FastRotationRunner | None = None,
    auto_restart_resume_policy: dict[str, Any] | None = None,
) -> int:
    return run_account_session(
        d,
        account_id=account_id,
        account_username=account_username,
        run_id=run_id,
        source_profile_username=source_profile_username,
        target_id=target_id,
        follow_targets=follow_targets,
        max_follow_targets_per_run=max_follow_targets_per_run,
        max_follows_per_target_per_run=max_follows_per_target_per_run,
        fast_rotate_to_next_target_from_followers=fast_rotate_to_next_target_from_followers,
        run_followers_list_engine_session=run_followers_list_engine_session,
        supabase_mode=supabase_mode,
        warm_session_used=warm_session_used,
        force_stop_used=force_stop_used,
        auto_restart_resume_policy=auto_restart_resume_policy,
    )
