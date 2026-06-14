"""Runtime cap resolution helpers for controlled worker sessions."""

from __future__ import annotations

import os
from typing import Any, Mapping

import config

DEFAULT_FOLLOW_MAX_PER_RUN = 2
DEFAULT_FOLLOWERS_LIST_MAX_ITERATIONS_PER_RUN = 5
DEFAULT_WELCOME_SESSION_SEND_MAX_JOBS = 3
UNFOLLOW_RUNTIME_CAP_MODES = {"mini_run", "prod_normal", "incident_safety"}


def _as_nonnegative_int(value: Any, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return max(0, int(default))


def _env_present(name: str, environ: Mapping[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    raw = env.get(name)
    return raw is not None and str(raw).strip() != ""


def normalize_unfollow_runtime_cap_mode(value: Any) -> str:
    mode = str(value or "prod_normal").strip().lower().replace("-", "_")
    return mode if mode in UNFOLLOW_RUNTIME_CAP_MODES else "prod_normal"


def resolve_unfollow_runtime_cap(
    *,
    db_unfollow_per_session_limit: Any,
    runtime_cap_mode: Any = "prod_normal",
    runtime_safety_cap: Any = None,
    env_real_action_max_per_run: Any = None,
) -> dict[str, Any]:
    """Resolve Unfollow runtime caps without making env mini-run limits the prod default."""
    db_session = _as_nonnegative_int(db_unfollow_per_session_limit, 0)
    mode = normalize_unfollow_runtime_cap_mode(runtime_cap_mode)

    if mode == "prod_normal":
        return {
            "runtime_cap_mode": mode,
            "runtime_cap": db_session,
            "runtime_hard_cap": db_session,
            "runtime_cap_source": "supabase_domain_caps",
            "env_fallback_used": False,
            "limited_by_runtime_cap": False,
        }

    db_safety = None
    if runtime_safety_cap is not None and str(runtime_safety_cap).strip() != "":
        db_safety = _as_nonnegative_int(runtime_safety_cap, 0)

    env_cap = _as_nonnegative_int(env_real_action_max_per_run, 1)
    mode_cap = db_safety if db_safety is not None else env_cap
    runtime_cap = min(db_session, mode_cap)
    return {
        "runtime_cap_mode": mode,
        "runtime_cap": runtime_cap,
        "runtime_hard_cap": mode_cap,
        "runtime_cap_source": "ig_account_unfollow_settings.runtime_safety_cap"
        if db_safety is not None
        else "env_fallback_unfollow_runtime_cap",
        "env_fallback_used": db_safety is None,
        "limited_by_runtime_cap": runtime_cap < db_session,
    }


def resolve_welcome_send_limits(
    *,
    db_welcome_per_session_limit: Any,
    welcome_day_remaining_today: Any,
    total_dm_day_remaining_today: Any,
    config_module: Any = config,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Resolve the effective Welcome sender cap without exposing env values."""
    hard_cap = _as_nonnegative_int(
        getattr(config_module, "WELCOME_SESSION_SEND_MAX_JOBS", DEFAULT_WELCOME_SESSION_SEND_MAX_JOBS),
        DEFAULT_WELCOME_SESSION_SEND_MAX_JOBS,
    )
    hard_cap_env_present = _env_present("WELCOME_SESSION_SEND_MAX_JOBS", environ)
    db_session = _as_nonnegative_int(db_welcome_per_session_limit, hard_cap)
    welcome_day_remaining = _as_nonnegative_int(welcome_day_remaining_today, db_session)
    total_day_remaining = _as_nonnegative_int(total_dm_day_remaining_today, welcome_day_remaining)
    effective = min(db_session, hard_cap, welcome_day_remaining, total_day_remaining)
    return {
        "db_welcome_per_session_limit": db_session,
        "welcome_hard_cap": hard_cap,
        "hard_cap_present": hard_cap_env_present,
        "hard_cap_label": "env:WELCOME_SESSION_SEND_MAX_JOBS"
        if hard_cap_env_present
        else "config.WELCOME_SESSION_SEND_MAX_JOBS",
        "welcome_day_remaining_today": welcome_day_remaining,
        "total_dm_day_remaining_today": total_day_remaining,
        "effective_welcome_send_max": effective,
        "source": "min(db_session,hard_cap,db_day_remaining,total_dm_day_remaining)",
    }


def resolve_follow_runtime_limits(
    *,
    db_follow_per_session_limit: Any = None,
    db_max_follow_per_run: Any = None,
    follow_day_remaining_today: Any = None,
    package_follow_day_cap: Any = None,
    warmup_follow_day_cap: Any = None,
    config_module: Any = config,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Resolve Follow caps used by the followers-list engine."""
    config_follow_max = _as_nonnegative_int(
        getattr(config_module, "FOLLOW_MAX_PER_RUN", DEFAULT_FOLLOW_MAX_PER_RUN),
        DEFAULT_FOLLOW_MAX_PER_RUN,
    )
    iterations_max = _as_nonnegative_int(
        getattr(
            config_module,
            "FOLLOWERS_LIST_MAX_ITERATIONS_PER_RUN",
            DEFAULT_FOLLOWERS_LIST_MAX_ITERATIONS_PER_RUN,
        ),
        DEFAULT_FOLLOWERS_LIST_MAX_ITERATIONS_PER_RUN,
    )
    follow_env_present = _env_present("FOLLOW_MAX_PER_RUN", environ)
    iterations_env_present = _env_present("FOLLOWERS_LIST_MAX_ITERATIONS_PER_RUN", environ)
    has_db_session_cap = db_follow_per_session_limit is not None or db_max_follow_per_run is not None
    db_session = (
        _as_nonnegative_int(db_follow_per_session_limit, config_follow_max)
        if db_follow_per_session_limit is not None
        else config_follow_max
    )
    legacy_session = (
        _as_nonnegative_int(db_max_follow_per_run, db_session)
        if db_max_follow_per_run is not None
        else None
    )
    if legacy_session is not None and legacy_session > 0:
        db_session = min(db_session, legacy_session)
    day_remaining = (
        _as_nonnegative_int(follow_day_remaining_today, db_session)
        if follow_day_remaining_today is not None
        else db_session
    )
    package_cap = (
        _as_nonnegative_int(package_follow_day_cap, day_remaining)
        if package_follow_day_cap is not None
        else day_remaining
    )
    warmup_cap = (
        _as_nonnegative_int(warmup_follow_day_cap, package_cap)
        if warmup_follow_day_cap is not None
        else package_cap
    )
    effective_follow_max = min(db_session, day_remaining, package_cap, warmup_cap)
    if follow_env_present:
        effective_follow_max = min(effective_follow_max, config_follow_max)
    effective_iterations_max = (
        min(iterations_max, effective_follow_max)
        if iterations_env_present
        else effective_follow_max
        if has_db_session_cap
        else iterations_max
    )
    return {
        "code_default_follow_max": DEFAULT_FOLLOW_MAX_PER_RUN,
        "code_default_iterations_max": DEFAULT_FOLLOWERS_LIST_MAX_ITERATIONS_PER_RUN,
        "env_follow_cap_present": follow_env_present,
        "env_iterations_cap_present": iterations_env_present,
        "follow_cap_label": "env:FOLLOW_MAX_PER_RUN" if follow_env_present else "config.FOLLOW_MAX_PER_RUN",
        "iterations_cap_label": "env:FOLLOWERS_LIST_MAX_ITERATIONS_PER_RUN"
        if iterations_env_present
        else "config.FOLLOWERS_LIST_MAX_ITERATIONS_PER_RUN",
        "follow_code_cap_applied": follow_env_present or not has_db_session_cap,
        "iterations_code_cap_applied": iterations_env_present or not has_db_session_cap,
        "db_follow_per_session_limit": db_session,
        "db_follow_limit_raw": db_follow_per_session_limit,
        "db_max_follow_per_run": legacy_session,
        "follow_day_remaining_today": day_remaining,
        "package_follow_day_cap": package_cap,
        "warmup_follow_day_cap": warmup_cap,
        "effective_follow_max": effective_follow_max,
        "effective_iterations_max": effective_iterations_max,
        "source": "min(db_session,package,warmup,day_remaining)",
    }
