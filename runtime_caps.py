"""Runtime cap resolution helpers for controlled worker sessions."""

from __future__ import annotations

import os
from typing import Any, Mapping

import config

DEFAULT_FOLLOW_MAX_PER_RUN = 2
DEFAULT_FOLLOWERS_LIST_MAX_ITERATIONS_PER_RUN = 5
DEFAULT_WELCOME_SESSION_SEND_MAX_JOBS = 3


def _as_nonnegative_int(value: Any, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return max(0, int(default))


def _env_present(name: str, environ: Mapping[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    raw = env.get(name)
    return raw is not None and str(raw).strip() != ""


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
    config_module: Any = config,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Resolve Follow caps used by the followers-list engine."""
    follow_max = _as_nonnegative_int(
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
    return {
        "code_default_follow_max": DEFAULT_FOLLOW_MAX_PER_RUN,
        "code_default_iterations_max": DEFAULT_FOLLOWERS_LIST_MAX_ITERATIONS_PER_RUN,
        "env_follow_cap_present": follow_env_present,
        "env_iterations_cap_present": iterations_env_present,
        "follow_cap_label": "env:FOLLOW_MAX_PER_RUN" if follow_env_present else "config.FOLLOW_MAX_PER_RUN",
        "iterations_cap_label": "env:FOLLOWERS_LIST_MAX_ITERATIONS_PER_RUN"
        if iterations_env_present
        else "config.FOLLOWERS_LIST_MAX_ITERATIONS_PER_RUN",
        "effective_follow_max": follow_max,
        "effective_iterations_max": iterations_max,
    }
