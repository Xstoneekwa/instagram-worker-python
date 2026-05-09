"""Social memory rules: runtime + DB-backed eligibility (anti-refollow, cooldowns, skip)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping


# interaction_lifecycle_state (target schema)
ACTIVE_FOLLOWING = "active_following"
UNFOLLOWED_COMPLETED = "unfollowed_completed"
BLOCKED_FUTURE_FOLLOW = "blocked_future_follow"
SKIPPED = "skipped"
FAILED = "failed"


def normalize_social_username(u: str) -> str:
    return (u or "").strip().lstrip("@").lower()


def _row_pick(row: Mapping[str, Any], key: str) -> Any:
    """Read a field from the row or from payload json (new ig_interacted_users shape)."""
    if key in row and row.get(key) is not None:
        return row.get(key)
    p = row.get("payload")
    if isinstance(p, dict) and key in p:
        return p.get(key)
    return None


def _db_row_effective_followed(db_row: Mapping[str, Any]) -> bool:
    if "followed" in db_row and db_row.get("followed") is not None:
        return bool(db_row.get("followed"))
    if db_row.get("was_successful") is True:
        it = (db_row.get("interaction_type") or "").strip().lower()
        if it != "follow":
            return False
        fs = (db_row.get("follow_status") or "").strip().lower()
        if fs in ("failed", "not_following", "unfollowed"):
            return False
        return True
    return False


def _db_row_effective_unfollowed(db_row: Mapping[str, Any]) -> bool:
    if "unfollowed" in db_row and db_row.get("unfollowed") is not None:
        return bool(db_row.get("unfollowed"))
    return bool(db_row.get("unfollowed_at"))


def _parse_iso_dt(raw: Any) -> datetime | None:
    if not raw:
        return None
    try:
        ts = str(raw).replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def days_since_timestamp(raw: Any) -> float | None:
    dt = _parse_iso_dt(raw)
    if dt is None:
        return None
    return (_now_utc() - dt).total_seconds() / 86400.0


def cooldown_until_active(raw: Any) -> bool:
    dt = _parse_iso_dt(raw)
    if dt is None:
        return False
    return _now_utc() < dt


@dataclass
class FollowEligibility:
    allowed: bool
    reason: str
    log_event: str
    interaction_state: str
    detail: dict[str, Any]


def evaluate_follow_eligibility(
    *,
    target_username: str,
    source_profile: str,
    db_row: dict[str, Any] | None,
    runtime_followed: set[str],
    runtime_unfollowed: set[str],
    runtime_interacted: set[str],
    runtime_skipped: set[str],
    config: Any,
) -> FollowEligibility:
    """
    Central gate before perform_follow_safe. Does not call Supabase.
    """
    key = normalize_social_username(target_username)
    src = normalize_social_username(source_profile)
    detail: dict[str, Any] = {
        "target_username": target_username,
        "source_profile": source_profile or "",
        "source_normalized": src,
    }

    if not getattr(config, "SOCIAL_MEMORY_ENABLED", True):
        return FollowEligibility(
            True,
            "",
            "social_memory_disabled",
            ACTIVE_FOLLOWING,
            detail,
        )

    if key in runtime_followed:
        return FollowEligibility(
            False,
            "runtime_already_followed",
            "social_memory_runtime_duplicate",
            ACTIVE_FOLLOWING,
            detail,
        )

    if key in runtime_unfollowed:
        return FollowEligibility(
            False,
            "runtime_unfollow_set",
            "social_memory_unfollow_blocked",
            UNFOLLOWED_COMPLETED,
            detail,
        )

    if key in runtime_interacted:
        return FollowEligibility(
            False,
            "runtime_already_interacted",
            "social_memory_runtime_duplicate",
            SKIPPED if key in runtime_skipped else ACTIVE_FOLLOWING,
            detail,
        )

    if db_row:
        followed = _db_row_effective_followed(db_row)
        unfollowed = _db_row_effective_unfollowed(db_row)
        detail["db_followed"] = followed
        detail["db_unfollowed"] = unfollowed
        detail["interaction_lifecycle_state"] = _row_pick(db_row, "interaction_lifecycle_state")

        if followed and unfollowed:
            return FollowEligibility(
                False,
                "refollow_after_unfollow_forbidden",
                "social_memory_follow_blocked",
                BLOCKED_FUTURE_FOLLOW,
                detail,
            )

        life = (
            str(_row_pick(db_row, "interaction_lifecycle_state") or "").strip().lower()
        )
        if life == BLOCKED_FUTURE_FOLLOW:
            return FollowEligibility(
                False,
                "lifecycle_blocked_future_follow",
                "social_memory_follow_blocked",
                BLOCKED_FUTURE_FOLLOW,
                detail,
            )
        if life == UNFOLLOWED_COMPLETED:
            return FollowEligibility(
                False,
                "lifecycle_unfollowed_completed",
                "social_memory_unfollow_blocked",
                UNFOLLOWED_COMPLETED,
                detail,
            )

        cooldown_raw = _row_pick(db_row, "cooldown_until") or db_row.get("cooldown_until")
        if cooldown_until_active(cooldown_raw):
            return FollowEligibility(
                False,
                "cooldown_until_active",
                "social_memory_cooldown_skip",
                life or SKIPPED,
                {**detail, "cooldown_until": cooldown_raw},
            )

        revisit = float(getattr(config, "SOCIAL_MEMORY_REVISIT_COOLDOWN_DAYS", 0) or 0)
        if revisit > 0:
            d_last = days_since_timestamp(db_row.get("last_interaction_at"))
            if d_last is not None and d_last < revisit:
                return FollowEligibility(
                    False,
                    "revisit_cooldown",
                    "social_memory_cooldown_skip",
                    life or ACTIVE_FOLLOWING,
                    {**detail, "days_since_last": round(d_last, 4), "revisit_days": revisit},
                )

        follow_cd = float(getattr(config, "SOCIAL_MEMORY_FOLLOW_REVISIT_COOLDOWN_DAYS", 0) or 0)
        if follow_cd > 0 and followed:
            d_follow = days_since_timestamp(
                db_row.get("last_interaction_at") or db_row.get("updated_at")
            )
            if d_follow is not None and d_follow < follow_cd:
                return FollowEligibility(
                    False,
                    "follow_revisit_cooldown",
                    "social_memory_cooldown_skip",
                    ACTIVE_FOLLOWING,
                    {**detail, "days_since": round(d_follow, 4), "follow_revisit_days": follow_cd},
                )

        i_cd = float(getattr(config, "SOCIAL_MEMORY_INTERACTION_COOLDOWN_DAYS", 0) or 0)
        if i_cd > 0 and db_row.get("last_interaction_at"):
            d_int = days_since_timestamp(db_row.get("last_interaction_at"))
            if d_int is not None and d_int < i_cd:
                return FollowEligibility(
                    False,
                    "interaction_cooldown",
                    "social_memory_cooldown_skip",
                    life or ACTIVE_FOLLOWING,
                    {**detail, "days_since_interaction": round(d_int, 4), "interaction_cooldown_days": i_cd},
                )

        # SOCIAL_MEMORY_DM_COOLDOWN_DAYS — reserved for future DM action gate (not applied to follow).

        istatus = (str(_row_pick(db_row, "interaction_status") or "").strip().lower())
        if istatus in ("blacklist", "blacklisted", "blocked"):
            return FollowEligibility(
                False,
                "interaction_blacklisted",
                "social_memory_follow_blocked",
                BLOCKED_FUTURE_FOLLOW,
                detail,
            )

        sk = int(_row_pick(db_row, "skip_count") or 0)
        max_db_skip = int(getattr(config, "SOCIAL_MEMORY_MAX_SKIP_COUNT_DB", 9999) or 9999)
        if sk >= max_db_skip:
            return FollowEligibility(
                False,
                "skip_count_exceeded",
                "social_memory_skip",
                SKIPPED,
                {**detail, "skip_count": sk},
            )

    return FollowEligibility(True, "", "social_memory_interaction_state", ACTIVE_FOLLOWING, detail)


def interaction_state_after_follow(
    *,
    skipped_tap: bool,
    follow_ui_state: str,
    ok: bool,
) -> str:
    if not ok:
        return FAILED
    if skipped_tap:
        return ACTIVE_FOLLOWING
    if (follow_ui_state or "").lower() == "requested":
        return ACTIVE_FOLLOWING
    return ACTIVE_FOLLOWING
