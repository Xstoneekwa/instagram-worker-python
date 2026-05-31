"""Account-level Follow settings (no navigation side effects)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import supabase_client
from logs import log


@dataclass(frozen=True)
class FollowSettings:
    account_id: str
    dont_follow_private_accounts: bool
    min_followers: int | None
    max_followers: int | None
    min_posts: int | None
    defaults_used: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "dont_follow_private_accounts": self.dont_follow_private_accounts,
            "min_followers": self.min_followers,
            "max_followers": self.max_followers,
            "min_posts": self.min_posts,
            "defaults_used": self.defaults_used,
        }


def _optional_nonnegative_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _row_to_settings(
    account_id: str,
    row: dict[str, Any],
    *,
    defaults_used: bool,
) -> FollowSettings:
    return FollowSettings(
        account_id=account_id,
        dont_follow_private_accounts=bool(
            row.get("dont_follow_private_accounts", True)
        ),
        min_followers=_optional_nonnegative_int(row.get("min_followers")),
        max_followers=_optional_nonnegative_int(row.get("max_followers")),
        min_posts=_optional_nonnegative_int(row.get("min_posts")),
        defaults_used=defaults_used,
    )


def _default_settings(account_id: str) -> FollowSettings:
    return FollowSettings(
        account_id=account_id,
        dont_follow_private_accounts=True,
        min_followers=None,
        max_followers=None,
        min_posts=None,
        defaults_used=True,
    )


def follow_filter_thresholds_active(settings: FollowSettings) -> bool:
    return any(
        value is not None
        for value in (
            settings.min_followers,
            settings.max_followers,
            settings.min_posts,
        )
    )


def account_follow_filter_pass(
    settings: FollowSettings,
    metrics: dict[str, Any],
) -> tuple[bool, str]:
    """Account-level Follow profile metrics filter.

    Metrics extraction is best-effort; weak extraction preserves the existing
    fail-open runtime behavior so we do not skip candidates on stale/partial UI.
    """
    if not follow_filter_thresholds_active(settings):
        return True, "no_follow_filter_thresholds_configured"
    if not isinstance(metrics, dict) or not metrics.get("extraction_ok", False):
        return True, "metrics_extraction_failed_skip_filter"

    def _metric_int(key: str) -> int | None:
        value = metrics.get(key)
        if value is None or isinstance(value, bool):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    followers_count = _metric_int("followers_count")
    posts_count = _metric_int("posts_count")

    if (
        settings.min_followers is not None
        and followers_count is not None
        and followers_count < settings.min_followers
    ):
        return False, "skip_followers_below_min"
    if (
        settings.max_followers is not None
        and followers_count is not None
        and followers_count > settings.max_followers
    ):
        return False, "skip_followers_above_max"
    if (
        settings.min_posts is not None
        and posts_count is not None
        and posts_count < settings.min_posts
    ):
        return False, "skip_posts_below_min"
    return True, "follow_filter_thresholds_pass"


def load_follow_settings(
    account_id: str,
    *,
    ensure_row: bool = False,
) -> FollowSettings:
    """Load account Follow settings; missing rows default to safe private-skip behavior."""
    aid = str(account_id or "").strip()
    if not aid:
        settings = _default_settings("")
        log(
            "info",
            "follow_settings_missing_defaults_used",
            account_id="",
            dont_follow_private_accounts=settings.dont_follow_private_accounts,
            reason="missing_account_id",
        )
        return settings

    row = supabase_client.get_account_follow_settings(aid)
    if row:
        settings = _row_to_settings(aid, row, defaults_used=False)
        log(
            "info",
            "follow_settings_loaded",
            account_id=aid,
            dont_follow_private_accounts=settings.dont_follow_private_accounts,
            min_followers=settings.min_followers,
            max_followers=settings.max_followers,
            min_posts=settings.min_posts,
            defaults_used=False,
        )
        return settings

    if ensure_row:
        row = supabase_client.ensure_account_follow_settings(aid)
        settings = _row_to_settings(aid, row, defaults_used=False)
        log(
            "info",
            "follow_settings_loaded",
            account_id=aid,
            dont_follow_private_accounts=settings.dont_follow_private_accounts,
            min_followers=settings.min_followers,
            max_followers=settings.max_followers,
            min_posts=settings.min_posts,
            defaults_used=False,
            row_created=True,
        )
        return settings

    settings = _default_settings(aid)
    log(
        "info",
        "follow_settings_missing_defaults_used",
        account_id=aid,
        dont_follow_private_accounts=settings.dont_follow_private_accounts,
        min_followers=settings.min_followers,
        max_followers=settings.max_followers,
        min_posts=settings.min_posts,
    )
    return settings
