"""
Unfollow account settings loader (Phase 1A — no UI).

Package defaults (Growth / Pro / Premium): unfollow_mode = unfollow, after_days = 3.
Per-account overrides live in ig_account_unfollow_settings.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import supabase_client
from logs import log

UNFOLLOW_MODE_UNFOLLOW = "unfollow"
UNFOLLOW_MODE_NON_FOLLOWERS = "unfollow-non-followers"
UNFOLLOW_MODE_ANY = "unfollow-any"
UNFOLLOW_MODE_ANY_NON_FOLLOWERS = "unfollow-any-non-followers"
UNFOLLOW_MODE_ANY_FOLLOWERS = "unfollow-any-followers"

UNFOLLOW_MODES: frozenset[str] = frozenset(
    {
        UNFOLLOW_MODE_UNFOLLOW,
        UNFOLLOW_MODE_NON_FOLLOWERS,
        UNFOLLOW_MODE_ANY,
        UNFOLLOW_MODE_ANY_NON_FOLLOWERS,
        UNFOLLOW_MODE_ANY_FOLLOWERS,
    }
)

UNFOLLOW_MODES_DB_STRICT: frozenset[str] = frozenset(
    {UNFOLLOW_MODE_UNFOLLOW, UNFOLLOW_MODE_NON_FOLLOWERS}
)

UNFOLLOW_MODES_REQUIRE_FOLLOWING_UI: frozenset[str] = frozenset(
    {
        UNFOLLOW_MODE_ANY,
        UNFOLLOW_MODE_ANY_NON_FOLLOWERS,
        UNFOLLOW_MODE_ANY_FOLLOWERS,
    }
)

UNFOLLOW_SORT_DEFAULT = "default"
UNFOLLOW_SORT_NEWEST_TO_OLDEST = "newest-to-oldest"
UNFOLLOW_SORT_OLDEST_TO_NEWEST = "oldest-to-newest"

UNFOLLOW_SORT_MODES: frozenset[str] = frozenset(
    {
        UNFOLLOW_SORT_DEFAULT,
        UNFOLLOW_SORT_NEWEST_TO_OLDEST,
        UNFOLLOW_SORT_OLDEST_TO_NEWEST,
    }
)

# Official package defaults (applied when inserting a new settings row).
PACKAGE_DEFAULT_UNFOLLOW_MODE = UNFOLLOW_MODE_UNFOLLOW
PACKAGE_DEFAULT_UNFOLLOW_AFTER_DAYS = 3
PACKAGE_DEFAULT_SESSION_LIMIT = 50
PACKAGE_DEFAULT_DAY_LIMIT = 200


@dataclass(frozen=True)
class UnfollowSettings:
    account_id: str
    enabled: bool
    unfollow_only: bool
    do_unfollow_first: bool
    after_days: int
    mode: str
    sort_mode: str
    session_limit: int
    day_limit: int
    defaults_used: bool
    package_default_snapshot: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "unfollow_enabled": self.enabled,
            "unfollow_only": self.unfollow_only,
            "do_unfollow_first": self.do_unfollow_first,
            "unfollow_after_days": self.after_days,
            "unfollow_mode": self.mode,
            "unfollow_sort_mode": self.sort_mode,
            "unfollow_per_session_limit": self.session_limit,
            "unfollow_per_day_limit": self.day_limit,
            "defaults_used": self.defaults_used,
            "package_default_snapshot": dict(self.package_default_snapshot),
        }


def _package_default_snapshot() -> dict[str, Any]:
    return {
        "unfollow_mode": PACKAGE_DEFAULT_UNFOLLOW_MODE,
        "unfollow_after_days": PACKAGE_DEFAULT_UNFOLLOW_AFTER_DAYS,
        "unfollow_per_session_limit": PACKAGE_DEFAULT_SESSION_LIMIT,
        "unfollow_per_day_limit": PACKAGE_DEFAULT_DAY_LIMIT,
        "unfollow_sort_mode": UNFOLLOW_SORT_DEFAULT,
        "source": "package_default_growth_pro_premium",
    }


def _coerce_mode(raw: Any) -> str:
    mode = str(raw or "").strip().lower()
    if mode in UNFOLLOW_MODES:
        return mode
    return PACKAGE_DEFAULT_UNFOLLOW_MODE


def _coerce_sort_mode(raw: Any) -> str:
    sort_mode = str(raw or "").strip().lower()
    if sort_mode in UNFOLLOW_SORT_MODES:
        return sort_mode
    return UNFOLLOW_SORT_DEFAULT


def _coerce_nonnegative_int(raw: Any, default: int) -> int:
    if raw is None or str(raw).strip() == "":
        return max(0, int(default))
    return max(0, int(raw))


def _row_to_settings(account_id: str, row: dict[str, Any], *, defaults_used: bool) -> UnfollowSettings:
    snap = row.get("package_default_snapshot")
    if not isinstance(snap, dict):
        snap = _package_default_snapshot()
    return UnfollowSettings(
        account_id=account_id,
        enabled=bool(row.get("unfollow_enabled")),
        unfollow_only=bool(row.get("unfollow_only")),
        do_unfollow_first=bool(row.get("do_unfollow_first")),
        after_days=_coerce_nonnegative_int(
            row.get("unfollow_after_days"),
            PACKAGE_DEFAULT_UNFOLLOW_AFTER_DAYS,
        ),
        mode=_coerce_mode(row.get("unfollow_mode")),
        sort_mode=_coerce_sort_mode(row.get("unfollow_sort_mode")),
        session_limit=_coerce_nonnegative_int(
            row.get("unfollow_per_session_limit"),
            PACKAGE_DEFAULT_SESSION_LIMIT,
        ),
        day_limit=_coerce_nonnegative_int(
            row.get("unfollow_per_day_limit"),
            PACKAGE_DEFAULT_DAY_LIMIT,
        ),
        defaults_used=defaults_used,
        package_default_snapshot=dict(snap),
    )


def _defaults_settings(account_id: str) -> UnfollowSettings:
    return UnfollowSettings(
        account_id=account_id,
        enabled=False,
        unfollow_only=False,
        do_unfollow_first=False,
        after_days=PACKAGE_DEFAULT_UNFOLLOW_AFTER_DAYS,
        mode=PACKAGE_DEFAULT_UNFOLLOW_MODE,
        sort_mode=UNFOLLOW_SORT_DEFAULT,
        session_limit=PACKAGE_DEFAULT_SESSION_LIMIT,
        day_limit=PACKAGE_DEFAULT_DAY_LIMIT,
        defaults_used=True,
        package_default_snapshot=_package_default_snapshot(),
    )


def load_unfollow_settings(
    account_id: str,
    *,
    ensure_row: bool = False,
) -> UnfollowSettings:
    """
    Load ig_account_unfollow_settings for account_id.
    When ensure_row=True, create a row with package defaults if missing.
  When ensure_row=False and row missing, return in-memory defaults (enabled=false).
    """
    aid = str(account_id or "").strip()
    if not aid:
        raise ValueError("account_id is required")

    row = supabase_client.get_account_unfollow_settings(aid)
    if row:
        settings = _row_to_settings(aid, row, defaults_used=False)
        log(
            "info",
            "unfollow_settings_loaded",
            account_id=aid,
            unfollow_enabled=settings.enabled,
            unfollow_mode=settings.mode,
            unfollow_after_days=settings.after_days,
            defaults_used=False,
        )
        return settings

    if ensure_row:
        row = supabase_client.ensure_account_unfollow_settings(aid)
        settings = _row_to_settings(aid, row, defaults_used=False)
        log(
            "info",
            "unfollow_settings_loaded",
            account_id=aid,
            unfollow_enabled=settings.enabled,
            unfollow_mode=settings.mode,
            unfollow_after_days=settings.after_days,
            defaults_used=False,
            row_created=True,
        )
        return settings

    settings = _defaults_settings(aid)
    log(
        "info",
        "unfollow_settings_missing_defaults_used",
        account_id=aid,
        unfollow_enabled=settings.enabled,
        unfollow_mode=settings.mode,
        unfollow_after_days=settings.after_days,
    )
    return settings


def compute_eligible_unfollow_at_iso(
    followed_at_iso: str | None,
    *,
    after_days: int,
) -> str | None:
    """
    Contract for future follow persistence (Phase 1B+):
      eligible_unfollow_at = followed_at + unfollow_after_days

    Not wired into record_follow_interaction_outcome in Phase 1A.
    """
    dt = supabase_client.parse_utc_iso_timestamp(followed_at_iso)
    if dt is None:
        return None
    from datetime import timedelta, timezone

    eligible = dt + timedelta(days=max(0, int(after_days)))
    if eligible.tzinfo is None:
        eligible = eligible.replace(tzinfo=timezone.utc)
    return eligible.astimezone(timezone.utc).isoformat()
