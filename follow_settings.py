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
    defaults_used: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "dont_follow_private_accounts": self.dont_follow_private_accounts,
            "defaults_used": self.defaults_used,
        }


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
        defaults_used=defaults_used,
    )


def _default_settings(account_id: str) -> FollowSettings:
    return FollowSettings(
        account_id=account_id,
        dont_follow_private_accounts=True,
        defaults_used=True,
    )


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
    )
    return settings
