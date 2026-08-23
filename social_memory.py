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
KNOWN_PROCESSED = "KNOWN_PROCESSED"
KNOWN_NOT_PROCESSED = "KNOWN_NOT_PROCESSED"
UNKNOWN = "UNKNOWN"
SOCIAL_MEMORY_NORMALIZATION_VERSION = "ig_handle_lower_v1"
ALREADY_INTERACTED_LIKE = "already_interacted_like"
ALREADY_INTERACTED_MUTE = "already_interacted_mute"
FOLLOW_MUTATION_AMBIGUOUS = "follow_mutation_ambiguous"
EVER_FOLLOWED_CANONICAL_AT = "ever_followed_canonical_at"
EVER_FOLLOWED_ACTION_ID = "ever_followed_action_id"


def normalize_social_username(u: str) -> str:
    return (u or "").strip().lstrip("@").lower()


def resolve_exact_batch_state(
    envelope: Mapping[str, Any] | None,
    *,
    account_id: str,
    requested_keys: list[str],
    query_generation: str,
    revision_generation: str,
) -> dict[str, tuple[str, dict[str, Any] | None]]:
    """Validate the complete batch contract before authorizing absence.

    A missing row is KNOWN_NOT_PROCESSED only after exact scope, normalization,
    generation and completeness proof.  Any mismatch is UNKNOWN.
    """
    keys = []
    for raw in requested_keys:
        key = normalize_social_username(raw)
        if key and key not in keys:
            keys.append(key)
    unknown = {key: (UNKNOWN, None) for key in keys}
    if not isinstance(envelope, Mapping):
        return unknown
    if (
        str(envelope.get("schema") or "") != "SOCIAL_MEMORY_BATCH_EXACT_V1"
        or str(envelope.get("account_id") or "") != str(account_id or "")
        or list(envelope.get("requested_keys") or []) != keys
        or str(envelope.get("normalization_version") or "")
        != SOCIAL_MEMORY_NORMALIZATION_VERSION
        or str(envelope.get("query_generation") or "") != str(query_generation or "")
        or str(envelope.get("revision_generation") or "")
        != str(revision_generation or "")
        or envelope.get("complete") is not True
        or envelope.get("truncated") is not False
    ):
        return unknown
    rows = envelope.get("rows_by_key")
    if not isinstance(rows, Mapping) or any(str(k) not in keys for k in rows):
        return unknown
    out: dict[str, tuple[str, dict[str, Any] | None]] = {}
    for key in keys:
        row = rows.get(key)
        if row is None:
            out[key] = (KNOWN_NOT_PROCESSED, None)
        elif isinstance(row, dict):
            out[key] = (KNOWN_PROCESSED, dict(row))
        else:
            return unknown
    return out


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


def durable_already_interacted_evidence(
    db_row: Mapping[str, Any] | None,
) -> tuple[bool, bool, dict[str, Any]]:
    """Return durable Like/Mute truth only; transient UI observations never qualify."""
    if not db_row:
        return False, False, {"memory_status": "no_row"}
    payload = db_row.get("payload") if isinstance(db_row.get("payload"), Mapping) else {}
    like_marker = payload.get(ALREADY_INTERACTED_LIKE)
    mute_marker = payload.get(ALREADY_INTERACTED_MUTE)
    last_likes = payload.get("last_post_likes")
    if not isinstance(last_likes, Mapping):
        last_likes = {}
    try:
        posts_liked_count = int(db_row.get("posts_liked_count") or 0)
    except (TypeError, ValueError):
        posts_liked_count = 0
    try:
        last_liked_count = int(last_likes.get("liked_count") or 0)
    except (TypeError, ValueError):
        last_liked_count = 0
    like_durable = bool(
        posts_liked_count > 0
        or last_liked_count > 0
        or like_marker is True
        or (isinstance(like_marker, Mapping) and like_marker.get("durable") is True)
    )
    mute_durable = bool(
        db_row.get("last_muted_at")
        and (db_row.get("muted_posts") is True or db_row.get("muted_stories") is True)
    ) or bool(
        mute_marker is True
        or (isinstance(mute_marker, Mapping) and mute_marker.get("durable") is True)
    )
    return like_durable, mute_durable, {
        "already_interacted_like": like_durable,
        "already_interacted_mute": mute_durable,
        "posts_liked_count": posts_liked_count,
        "last_liked_count": last_liked_count,
        "last_muted_at": db_row.get("last_muted_at"),
    }


def durable_ever_followed_canonical_projection(
    db_row: Mapping[str, Any] | None,
) -> tuple[bool, bool, dict[str, Any]]:
    """Read the monotone canonical Follow projection without inventing truth.

    Both fields are written from one successful canonical event.  A partial
    projection is contradictory/unknown and therefore fail-closed, but is not
    promoted to permanent Follow-once truth by the Worker.
    """
    if not db_row:
        return False, False, {"ever_followed_canonical": False}
    canonical_at = _row_pick(db_row, EVER_FOLLOWED_CANONICAL_AT)
    action_id = _row_pick(db_row, EVER_FOLLOWED_ACTION_ID)
    complete = bool(canonical_at and action_id)
    incomplete = bool(canonical_at) != bool(action_id)
    return complete, incomplete, {
        "ever_followed_canonical": complete,
        "ever_followed_canonical_projection_incomplete": incomplete,
        "ever_followed_canonical_at": canonical_at,
        "ever_followed_action_id": action_id,
    }


# Follow statuses that indicate an ongoing follow relationship (localized column + payload variants).
_DB_PERSISTENT_ACTIVE_FOLLOW_STATUSES = frozenset(
    {"following", "requested", "already_following"}
)


def db_row_persistent_active_follow_connection(
    db_row: Mapping[str, Any] | None,
) -> tuple[bool, str, dict[str, Any]]:
    """
    Narrow gate for the visual followers pipeline only: skip re-processing when Supabase row
    already reflects an active follow link. Does not replace evaluate_follow_eligibility().
    """
    detail: dict[str, Any] = {}
    if not db_row:
        detail["memory_status"] = "no_row"
        return False, "no_db_row", detail

    fs_col = str(db_row.get("follow_status") or "").strip().lower()
    fs_payload = str(_row_pick(db_row, "follow_status") or "").strip().lower()
    fs_follow_payload = str(_row_pick(db_row, "following_status") or "").strip().lower()
    fs_eff = fs_col or fs_payload or fs_follow_payload

    followed = _db_row_effective_followed(db_row)
    unfollowed = _db_row_effective_unfollowed(db_row)
    life_raw = str(_row_pick(db_row, "interaction_lifecycle_state") or "").strip().lower()

    detail["memory_status"] = "has_row"
    detail["following_status"] = fs_eff
    detail["interaction_lifecycle_state"] = life_raw
    detail["db_followed"] = followed
    detail["db_unfollowed"] = unfollowed

    if followed and unfollowed:
        detail["memory_status"] = "ambiguous_follow_and_unfollow"
        return False, "ambiguous_db_state", detail

    if fs_eff and fs_eff in _DB_PERSISTENT_ACTIVE_FOLLOW_STATUSES:
        return True, "follow_status_active_connection", detail

    if life_raw == str(ACTIVE_FOLLOWING).strip().lower():
        if not unfollowed:
            return True, "lifecycle_active_following", detail
        return False, "lifecycle_active_but_unfollowed", detail

    if followed and not unfollowed:
        return True, "db_followed_not_unfollowed", detail

    detail["memory_status"] = "row_not_actively_connected"
    return False, "not_persistent_connection", detail


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

        interacted_like, interacted_mute, interacted_detail = (
            durable_already_interacted_evidence(db_row)
        )
        detail.update(interacted_detail)
        payload = db_row.get("payload") if isinstance(db_row.get("payload"), Mapping) else {}
        ambiguous_follow = payload.get(FOLLOW_MUTATION_AMBIGUOUS)
        if ambiguous_follow is True or (
            isinstance(ambiguous_follow, Mapping)
            and ambiguous_follow.get("durable") is True
            and ambiguous_follow.get("resolved") is not True
        ):
            return FollowEligibility(
                False,
                "durable_follow_mutation_ambiguous_unreconciled",
                "social_memory_follow_ambiguity_blocked",
                FAILED,
                {**detail, "follow_mutation_ambiguous": True},
            )
        ever_followed, ever_followed_incomplete, ever_followed_detail = (
            durable_ever_followed_canonical_projection(db_row)
        )
        detail.update(ever_followed_detail)
        if ever_followed_incomplete:
            return FollowEligibility(
                False,
                "canonical_follow_projection_incomplete",
                "social_memory_follow_blocked",
                FAILED,
                detail,
            )
        if ever_followed:
            return FollowEligibility(
                False,
                "already_followed_canonical_once",
                "social_memory_follow_blocked",
                BLOCKED_FUTURE_FOLLOW,
                detail,
            )
        if interacted_like or interacted_mute:
            reason = (
                "durable_already_interacted_like_and_mute"
                if interacted_like and interacted_mute
                else "durable_already_interacted_like"
                if interacted_like
                else "durable_already_interacted_mute"
            )
            return FollowEligibility(
                False,
                reason,
                "social_memory_already_interacted_follow_excluded",
                SKIPPED,
                detail,
            )

        if followed and unfollowed:
            return FollowEligibility(
                False,
                "refollow_after_unfollow_forbidden",
                "social_memory_follow_blocked",
                BLOCKED_FUTURE_FOLLOW,
                detail,
            )

        persistent_active, persistent_reason, persistent_detail = (
            db_row_persistent_active_follow_connection(db_row)
        )
        if persistent_active:
            return FollowEligibility(
                False,
                "persistent_active_follow_connection",
                "social_memory_follow_blocked",
                ACTIVE_FOLLOWING,
                {**detail, **persistent_detail, "persistent_reason": persistent_reason},
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
