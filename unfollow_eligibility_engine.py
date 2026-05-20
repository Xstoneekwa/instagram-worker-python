"""
Offline unfollow eligibility (Phase 1A — no UI).

Strict DB modes: unfollow, unfollow-non-followers.
Any-* modes return an empty plan until Following list UI exists.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import supabase_client
from logs import log
from social_memory import UNFOLLOWED_COMPLETED, normalize_social_username
from unfollow_settings import (
    UNFOLLOW_MODES_DB_STRICT,
    UNFOLLOW_MODES_REQUIRE_FOLLOWING_UI,
    UnfollowSettings,
    load_unfollow_settings,
)

_ACTIVE_FOLLOWING = "active_following"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _row_pick(row: dict[str, Any], key: str) -> Any:
    if key in row and row.get(key) is not None:
        return row.get(key)
    payload = row.get("payload")
    if isinstance(payload, dict) and key in payload:
        return payload.get(key)
    return None


def _row_lifecycle(row: dict[str, Any]) -> str:
    return str(_row_pick(row, "interaction_lifecycle_state") or "").strip().lower()


def _row_effective_unfollowed(row: dict[str, Any]) -> bool:
    if row.get("unfollowed_at"):
        return True
    if row.get("unfollowed") is True:
        return True
    return _row_lifecycle(row) == UNFOLLOWED_COMPLETED


def _row_is_following_back(row: dict[str, Any]) -> bool | None:
    raw = _row_pick(row, "is_following_back")
    if raw is None:
        return None
    return bool(raw)


def _row_follow_status(row: dict[str, Any]) -> str:
    return str(_row_pick(row, "follow_status") or "").strip().lower()


def _resolve_eligible_unfollow_at(
    row: dict[str, Any],
    *,
    after_days: int,
) -> datetime | None:
    stored = supabase_client.parse_utc_iso_timestamp(row.get("eligible_unfollow_at"))
    if stored is not None:
        return stored
    followed_at = supabase_client.parse_utc_iso_timestamp(row.get("followed_at"))
    if followed_at is None:
        return None
    return followed_at + timedelta(days=max(0, int(after_days)))


def _lifecycle_allows_strict_unfollow(row: dict[str, Any]) -> bool:
    if _row_effective_unfollowed(row):
        return False
    life = _row_lifecycle(row)
    if life in (UNFOLLOWED_COMPLETED, "blocked_future_follow"):
        return False
    if life == _ACTIVE_FOLLOWING:
        return True
    if life in ("", "failed", "skipped"):
        return bool(row.get("followed_at")) and bool(row.get("followed_by_bot"))
    return False


def _empty_plan(
    account_id: str,
    settings: UnfollowSettings,
    *,
    plan_reason: str,
    skipped_counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    return {
        "account_id": account_id,
        "unfollow_mode": settings.mode,
        "unfollow_enabled": settings.enabled,
        "plan_reason": plan_reason,
        "candidates": [],
        "candidates_count": 0,
        "skipped_counts": dict(skipped_counts or {}),
    }


def plan_unfollow_targets(
    account_id: str,
    *,
    settings: UnfollowSettings | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """
    Build planned_unfollow_targets from DB for strict modes only.
    Does not perform any Instagram UI navigation.
    """
    aid = str(account_id or "").strip()
    if not aid:
        raise ValueError("account_id is required")

    cfg = settings or load_unfollow_settings(aid, ensure_row=False)
    session_cap = int(limit if limit is not None else cfg.session_limit)
    session_cap = max(0, session_cap)

    skipped: dict[str, int] = {
        "too_soon": 0,
        "whitelist": 0,
        "already_unfollowed": 0,
        "missing_followed_by_bot": 0,
        "missing_followed_at": 0,
        "lifecycle_ineligible": 0,
        "follow_status_not_following": 0,
        "missing_followback_confirmation": 0,
        "not_following_back": 0,
    }

    if not cfg.enabled:
        out = _empty_plan(aid, cfg, plan_reason="unfollow_disabled")
        log("info", "unfollow_eligibility_plan_built", **out)
        return out

    if cfg.mode in UNFOLLOW_MODES_REQUIRE_FOLLOWING_UI:
        out = _empty_plan(
            aid,
            cfg,
            plan_reason="unfollow_any_mode_requires_following_ui_scan",
        )
        log(
            "info",
            "unfollow_eligibility_plan_built",
            account_id=aid,
            unfollow_mode=cfg.mode,
            plan_reason=out["plan_reason"],
            candidates_count=0,
        )
        return out

    if cfg.mode not in UNFOLLOW_MODES_DB_STRICT:
        out = _empty_plan(aid, cfg, plan_reason="unfollow_mode_not_supported_offline")
        log("info", "unfollow_eligibility_plan_built", **out)
        return out

    now = _now_utc()
    fetch_cap = max(session_cap * 4, 50) if session_cap > 0 else 200
    rows = supabase_client.fetch_unfollow_strict_candidate_rows(aid, limit=fetch_cap)

    candidates: list[dict[str, Any]] = []

    for row in rows:
        if session_cap > 0 and len(candidates) >= session_cap:
            break

        if _row_effective_unfollowed(row):
            skipped["already_unfollowed"] += 1
            continue

        if not bool(row.get("followed_by_bot")):
            skipped["missing_followed_by_bot"] += 1
            continue

        if not row.get("followed_at"):
            skipped["missing_followed_at"] += 1
            continue

        if bool(row.get("whitelist_protected")):
            skipped["whitelist"] += 1
            continue

        if _row_follow_status(row) != "following":
            skipped["follow_status_not_following"] += 1
            continue

        if not _lifecycle_allows_strict_unfollow(row):
            skipped["lifecycle_ineligible"] += 1
            continue

        eligible_at = _resolve_eligible_unfollow_at(row, after_days=cfg.after_days)
        if eligible_at is None:
            skipped["missing_followed_at"] += 1
            continue

        if now < eligible_at:
            skipped["too_soon"] += 1
            continue

        followback = _row_is_following_back(row)
        if cfg.mode == "unfollow-non-followers":
            if followback is None:
                skipped["missing_followback_confirmation"] += 1
                continue
            if followback:
                skipped["not_following_back"] += 1
                continue

        username = str(row.get("username") or "").strip()
        if not username:
            continue

        candidates.append(
            {
                "username": username,
                "username_normalized": normalize_social_username(username),
                "followed_at": row.get("followed_at"),
                "eligible_unfollow_at": eligible_at.isoformat(),
                "eligible_unfollow_at_source": (
                    "column"
                    if row.get("eligible_unfollow_at")
                    else "computed_from_followed_at"
                ),
                "is_following_back": followback,
                "source_profile": str(
                    row.get("last_source_profile")
                    or row.get("source_profile")
                    or ""
                ),
                "interaction_row_id": str(row.get("id") or ""),
                "eligibility_reason": "bot_follow_delay_elapsed",
            }
        )

    out = {
        "account_id": aid,
        "unfollow_mode": cfg.mode,
        "unfollow_enabled": cfg.enabled,
        "unfollow_after_days": cfg.after_days,
        "plan_reason": "strict_db_eligibility",
        "candidates": candidates,
        "candidates_count": len(candidates),
        "skipped_counts": skipped,
        "session_limit_applied": session_cap,
    }
    log(
        "info",
        "unfollow_eligibility_plan_built",
        account_id=aid,
        unfollow_mode=cfg.mode,
        plan_reason=out["plan_reason"],
        candidates_count=out["candidates_count"],
        skipped_counts=skipped,
        session_limit_applied=session_cap,
    )
    return out
