"""Immutable account-scoped protection-list snapshot for one worker run.

The dispatcher performs the only backend read. Business-action modules consume the
serialized environment snapshot and never query per candidate or per action.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Callable

SNAPSHOT_ENV = "ACCOUNT_PROTECTION_LISTS_SNAPSHOT_JSON"
REQUIRED_ENV = "ACCOUNT_PROTECTION_LISTS_REQUIRED"
_USERNAME_RE = re.compile(r"^[a-z0-9._]{1,30}$")


def normalize_username(value: Any) -> str:
    normalized = str(value or "").strip().lower().lstrip("@").strip()
    return normalized if _USERNAME_RE.fullmatch(normalized) else ""


@dataclass(frozen=True)
class AccountProtectionSnapshot:
    account_id: str
    interaction_blacklist: frozenset[str]
    unfollow_whitelist: frozenset[str]
    versions: dict[str, int]
    loaded_at: str
    source: str = "account_protection_list_entries"

    def as_payload(self) -> dict[str, Any]:
        return {
            "ok": True,
            "account_id": self.account_id,
            "source": self.source,
            "loaded_at": self.loaded_at,
            "lists": {
                "interaction_blacklist": sorted(self.interaction_blacklist),
                "unfollow_whitelist": sorted(self.unfollow_whitelist),
            },
            "versions": dict(self.versions),
        }


def parse_snapshot(payload: Any, *, expected_account_id: str | None = None) -> AccountProtectionSnapshot:
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise ValueError("protection_lists_snapshot_not_ok")
    account_id = str(payload.get("account_id") or "").strip()
    if not account_id or (expected_account_id and account_id != str(expected_account_id).strip()):
        raise ValueError("protection_lists_account_mismatch")
    if str(payload.get("source") or "") != "account_protection_list_entries":
        raise ValueError("protection_lists_source_invalid")
    lists = payload.get("lists")
    versions_raw = payload.get("versions")
    if not isinstance(lists, dict) or not isinstance(versions_raw, dict):
        raise ValueError("protection_lists_payload_invalid")

    normalized_lists: dict[str, frozenset[str]] = {}
    versions: dict[str, int] = {}
    for kind in ("interaction_blacklist", "unfollow_whitelist"):
        raw_items = lists.get(kind)
        if not isinstance(raw_items, list):
            raise ValueError(f"protection_lists_{kind}_invalid")
        normalized: set[str] = set()
        for raw in raw_items:
            username = normalize_username(raw)
            if not username:
                raise ValueError(f"protection_lists_{kind}_username_invalid")
            normalized.add(username)
        normalized_lists[kind] = frozenset(normalized)
        try:
            version = int(versions_raw.get(kind, 0))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"protection_lists_{kind}_version_invalid") from exc
        if version < 0:
            raise ValueError(f"protection_lists_{kind}_version_invalid")
        versions[kind] = version

    loaded_at = str(payload.get("loaded_at") or "").strip()
    if not loaded_at:
        raise ValueError("protection_lists_loaded_at_missing")
    return AccountProtectionSnapshot(
        account_id=account_id,
        interaction_blacklist=normalized_lists["interaction_blacklist"],
        unfollow_whitelist=normalized_lists["unfollow_whitelist"],
        versions=versions,
        loaded_at=loaded_at,
    )


def load_snapshot_for_run(account_id: str, rpc: Callable[[str, dict[str, Any]], Any]) -> AccountProtectionSnapshot:
    payload = rpc("get_account_protection_lists_for_run", {"p_account_id": str(account_id)})
    return parse_snapshot(payload, expected_account_id=account_id)


def snapshot_from_env(*, expected_account_id: str | None = None) -> AccountProtectionSnapshot:
    raw = str(os.environ.get(SNAPSHOT_ENV) or "").strip()
    if not raw:
        raise ValueError("protection_lists_snapshot_missing")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("protection_lists_snapshot_json_invalid") from exc
    return parse_snapshot(payload, expected_account_id=expected_account_id)


def serialize_snapshot(snapshot: AccountProtectionSnapshot) -> str:
    return json.dumps(snapshot.as_payload(), separators=(",", ":"), sort_keys=True)


def is_interaction_blocked(username: Any) -> bool:
    normalized = normalize_username(username)
    if not normalized:
        return os.environ.get(REQUIRED_ENV) == "1"
    if not str(os.environ.get(SNAPSHOT_ENV) or "").strip() and os.environ.get(REQUIRED_ENV) != "1":
        return False
    return normalized in snapshot_from_env().interaction_blacklist


def is_unfollow_protected(username: Any) -> bool:
    normalized = normalize_username(username)
    if not normalized:
        return os.environ.get(REQUIRED_ENV) == "1"
    if not str(os.environ.get(SNAPSHOT_ENV) or "").strip() and os.environ.get(REQUIRED_ENV) != "1":
        return False
    return normalized in snapshot_from_env().unfollow_whitelist


def snapshot_metadata() -> dict[str, Any]:
    snapshot = snapshot_from_env()
    return {
        "protection_lists_source": "canonical_v1",
        "protection_lists_storage": snapshot.source,
        "lists_loaded_at": snapshot.loaded_at,
        "lists_version": dict(snapshot.versions),
        "blacklist_count": len(snapshot.interaction_blacklist),
        "interaction_blacklist_version": snapshot.versions["interaction_blacklist"],
        "interaction_blacklist_count": len(snapshot.interaction_blacklist),
        "unfollow_whitelist_version": snapshot.versions["unfollow_whitelist"],
        "unfollow_whitelist_count": len(snapshot.unfollow_whitelist),
    }
