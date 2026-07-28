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


_STRICT_SKIP_KEYS = (
    "invalid_username",
    "duplicate_username_row",
    "too_soon",
    "whitelist",
    "already_unfollowed",
    "missing_followed_by_bot",
    "missing_followed_at",
    "lifecycle_ineligible",
    "follow_status_not_following",
    "missing_followback_confirmation",
    "not_following_back",
)


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


def _empty_skip_counts(*, include_visible_keys: bool = False) -> dict[str, int]:
    keys = list(_STRICT_SKIP_KEYS)
    if include_visible_keys:
        keys.extend(
            (
                "not_found_in_interacted_users",
                "followback_state_not_allowed",
                "unfollow_disabled",
                "unfollow_mode_not_supported",
            )
        )
    return {key: 0 for key in keys}


def _strict_unfollow_skip_reason(
    row: dict[str, Any],
    *,
    settings: UnfollowSettings,
    now: datetime,
    visible_lookup: bool = False,
) -> str:
    if _row_effective_unfollowed(row):
        return "already_unfollowed"

    if not bool(row.get("followed_by_bot")):
        return "missing_followed_by_bot"

    if not row.get("followed_at"):
        return "missing_followed_at"

    if _row_follow_status(row) != "following":
        return "follow_status_not_following"

    if not _lifecycle_allows_strict_unfollow(row):
        return "lifecycle_ineligible"

    eligible_at = _resolve_eligible_unfollow_at(row, after_days=settings.after_days)
    if eligible_at is None:
        return "missing_followed_at"

    if now < eligible_at:
        return "too_soon"

    followback = _row_is_following_back(row)
    if settings.mode == "unfollow-non-followers":
        if followback is None:
            return "missing_followback_confirmation"
        if followback:
            return "followback_state_not_allowed" if visible_lookup else "not_following_back"

    return ""


def _candidate_payload_from_row(
    row: dict[str, Any],
    *,
    settings: UnfollowSettings,
) -> dict[str, Any]:
    eligible_at = _resolve_eligible_unfollow_at(row, after_days=settings.after_days)
    username = str(row.get("username") or "").strip()
    return {
        "username": username,
        "username_normalized": normalize_social_username(username),
        "followed_at": row.get("followed_at"),
        "eligible_unfollow_at": eligible_at.isoformat() if eligible_at is not None else "",
        "eligible_unfollow_at_source": (
            "column" if row.get("eligible_unfollow_at") else "computed_from_followed_at"
        ),
        "is_following_back": _row_is_following_back(row),
        "source_profile": str(
            row.get("last_source_profile")
            or row.get("source_profile")
            or ""
        ),
        "interaction_row_id": str(row.get("id") or ""),
        "eligibility_reason": "bot_follow_delay_elapsed",
    }


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
        "eligible_total": 0,
        "unplanned_eligible_count": 0,
        "skipped_counts": dict(skipped_counts or {}),
        "candidate_scan_exhaustive": False,
        "candidate_funnel_reconciled": True,
    }


def plan_unfollow_targets(
    account_id: str,
    *,
    settings: UnfollowSettings | None = None,
    limit: int | None = None,
    as_of: datetime | None = None,
    protected_usernames: set[str] | frozenset[str] | None = None,
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

    skipped = _empty_skip_counts()

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

    now = as_of or _now_utc()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)
    protected = {
        key
        for key in (
            normalize_social_username(str(value or ""))
            for value in (protected_usernames or set())
        )
        if key
    }
    fetch_cap = max(session_cap * 4, 50) if session_cap > 0 else 200
    loaded = supabase_client.fetch_unfollow_strict_candidate_rows(
        aid,
        limit=fetch_cap,
        after_days=cfg.after_days,
        as_of=now,
        include_metadata=True,
    )
    if (
        isinstance(loaded, tuple)
        and len(loaded) == 2
        and isinstance(loaded[0], list)
        and isinstance(loaded[1], dict)
    ):
        rows = loaded[0]
        scan_metadata = dict(loaded[1])
    elif isinstance(loaded, list):
        # Compatibility for tests and downstream adapters that still mock the
        # historical list-only return shape.
        rows = loaded
        scan_metadata = {
            "source_rows_loaded": len(rows),
            "page_size": fetch_cap,
            "pages_loaded": 1 if rows else 0,
            "page_requests": 1,
            "pagination_used": False,
            "candidate_scan_exhaustive": True,
            "pagination_strategy": "mock_or_legacy_adapter",
            "scan_as_of": now.isoformat(),
        }
    else:
        raise RuntimeError("unfollow_candidate_scan_contract_invalid")

    eligible_candidates: list[dict[str, Any]] = []
    seen_eligible_usernames: set[str] = set()

    for row in rows:
        username = str(row.get("username") or "").strip()
        username_key = normalize_social_username(username)
        if not username_key:
            skipped["invalid_username"] = int(skipped.get("invalid_username", 0)) + 1
            continue

        skip_reason = _strict_unfollow_skip_reason(row, settings=cfg, now=now)
        if skip_reason:
            skipped[skip_reason] = int(skipped.get(skip_reason, 0)) + 1
            continue

        if username_key in protected:
            skipped["whitelist"] = int(skipped.get("whitelist", 0)) + 1
            continue

        if username_key in seen_eligible_usernames:
            skipped["duplicate_username_row"] = int(
                skipped.get("duplicate_username_row", 0)
            ) + 1
            continue

        seen_eligible_usernames.add(username_key)
        eligible_candidates.append(_candidate_payload_from_row(row, settings=cfg))

    eligible_total = len(eligible_candidates)
    candidates = eligible_candidates[:session_cap] if session_cap > 0 else []
    unplanned_eligible_count = max(0, eligible_total - len(candidates))
    skipped_total = sum(max(0, int(value or 0)) for value in skipped.values())
    source_rows_loaded = len(rows)
    candidate_funnel_reconciled = source_rows_loaded == eligible_total + skipped_total
    if not candidate_funnel_reconciled:
        raise RuntimeError("unfollow_candidate_funnel_reconciliation_failed")

    out = {
        "account_id": aid,
        "unfollow_mode": cfg.mode,
        "unfollow_enabled": cfg.enabled,
        "unfollow_after_days": cfg.after_days,
        "plan_reason": "strict_db_eligibility",
        "candidates": candidates,
        "candidates_count": len(candidates),
        "eligible_total": eligible_total,
        "unplanned_eligible_count": unplanned_eligible_count,
        "skipped_counts": skipped,
        "skipped_total": skipped_total,
        "session_limit_applied": session_cap,
        "source_rows_loaded": source_rows_loaded,
        "query_limit": int(scan_metadata.get("page_size") or fetch_cap),
        "page_size": int(scan_metadata.get("page_size") or fetch_cap),
        "pages_loaded": int(scan_metadata.get("pages_loaded") or 0),
        "page_requests": int(scan_metadata.get("page_requests") or 0),
        "pagination_used": bool(scan_metadata.get("pagination_used")),
        "pagination_strategy": str(scan_metadata.get("pagination_strategy") or ""),
        "candidate_scan_exhaustive": bool(
            scan_metadata.get("candidate_scan_exhaustive")
        ),
        "candidate_funnel_reconciled": candidate_funnel_reconciled,
        "scan_as_of": str(scan_metadata.get("scan_as_of") or now.isoformat()),
    }
    log(
        "info",
        "unfollow_eligibility_plan_built",
        account_id=aid,
        unfollow_mode=cfg.mode,
        plan_reason=out["plan_reason"],
        candidates_count=out["candidates_count"],
        eligible_total=eligible_total,
        unplanned_eligible_count=unplanned_eligible_count,
        skipped_counts=skipped,
        skipped_total=skipped_total,
        session_limit_applied=session_cap,
        source_rows_loaded=out["source_rows_loaded"],
        query_limit=out["query_limit"],
        page_size=out["page_size"],
        pages_loaded=out["pages_loaded"],
        page_requests=out["page_requests"],
        pagination_used=out["pagination_used"],
        pagination_strategy=out["pagination_strategy"],
        candidate_scan_exhaustive=out["candidate_scan_exhaustive"],
        candidate_funnel_reconciled=candidate_funnel_reconciled,
        scan_as_of=out["scan_as_of"],
    )
    return out


def evaluate_visible_unfollow_candidates(
    account_id: str,
    visible_usernames: list[str],
    *,
    settings: UnfollowSettings,
    db_rows_by_username: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Evaluate strict Unfollow eligibility for currently visible Following rows.
    This is intentionally independent from the per-session execution plan cap.
    """
    aid = str(account_id or "").strip()
    normalized_visible: list[str] = []
    seen: set[str] = set()
    for raw in visible_usernames:
        key = normalize_social_username(str(raw or ""))
        if key and key not in seen:
            seen.add(key)
            normalized_visible.append(key)

    log(
        "info",
        "unfollow_visible_eligibility_lookup_started",
        account_id=aid,
        visible_usernames_count=len(normalized_visible),
        unfollow_mode=settings.mode,
        unfollow_after_days=settings.after_days,
    )

    rows_by_username = db_rows_by_username
    if rows_by_username is None:
        rows_by_username = supabase_client.fetch_visible_unfollow_eligibility_rows(
            aid,
            normalized_visible,
        )
    rows_by_username = {
        normalize_social_username(str(k or "")): v
        for k, v in dict(rows_by_username or {}).items()
        if normalize_social_username(str(k or "")) and isinstance(v, dict)
    }

    now = _now_utc()
    skip_counts = _empty_skip_counts(include_visible_keys=True)
    eligible_matches: list[dict[str, Any]] = []
    ineligible_rows: list[dict[str, Any]] = []
    global_skip_reason = ""
    if not settings.enabled:
        global_skip_reason = "unfollow_disabled"
    elif settings.mode not in UNFOLLOW_MODES_DB_STRICT:
        global_skip_reason = "unfollow_mode_not_supported"

    for idx, username_key in enumerate(normalized_visible):
        if global_skip_reason:
            skip_counts[global_skip_reason] += 1
            evaluated = {
                "username": username_key,
                "username_normalized": username_key,
                "visible_index": idx,
                "eligible": False,
                "skip_reason": global_skip_reason,
            }
            ineligible_rows.append(evaluated)
            log("info", "unfollow_visible_candidate_eligibility_evaluated", **evaluated)
            continue

        row = rows_by_username.get(username_key)
        if row is None:
            skip_reason = "not_found_in_interacted_users"
            skip_counts[skip_reason] += 1
            evaluated = {
                "username": username_key,
                "username_normalized": username_key,
                "visible_index": idx,
                "eligible": False,
                "skip_reason": skip_reason,
            }
            ineligible_rows.append(evaluated)
            log("info", "unfollow_visible_candidate_eligibility_evaluated", **evaluated)
            continue

        skip_reason = _strict_unfollow_skip_reason(
            row,
            settings=settings,
            now=now,
            visible_lookup=True,
        )
        eligible = not bool(skip_reason)
        evaluated = {
            "username": str(row.get("username") or username_key),
            "username_normalized": username_key,
            "visible_index": idx,
            "eligible": eligible,
            "skip_reason": skip_reason,
            "interaction_row_id": str(row.get("id") or ""),
            "followed_at": row.get("followed_at"),
            "eligible_unfollow_at": row.get("eligible_unfollow_at"),
            "follow_status": _row_follow_status(row),
            "unfollowed_at": row.get("unfollowed_at"),
        }
        if eligible:
            candidate = _candidate_payload_from_row(row, settings=settings)
            candidate["visible_index"] = idx
            eligible_matches.append(candidate)
        else:
            skip_counts[skip_reason] = int(skip_counts.get(skip_reason, 0)) + 1
            ineligible_rows.append(evaluated)
        log("info", "unfollow_visible_candidate_eligibility_evaluated", **evaluated)

    out = {
        "visible_eligible_matches": eligible_matches,
        "visible_ineligible_rows": ineligible_rows,
        "visible_eligibility_skip_counts": skip_counts,
        "visible_eligibility_lookup_count": len(normalized_visible),
        "visible_eligible_matches_count": len(eligible_matches),
        "visible_eligible_matches_usernames": [
            str(c.get("username") or "") for c in eligible_matches
        ],
        "visible_match_source": "visible_username_db_lookup",
    }
    log(
        "info",
        "unfollow_visible_eligibility_lookup_completed",
        account_id=aid,
        visible_eligibility_lookup_count=out["visible_eligibility_lookup_count"],
        visible_eligible_matches_count=out["visible_eligible_matches_count"],
        visible_eligible_matches_usernames=out["visible_eligible_matches_usernames"][:50],
        visible_eligibility_skip_counts=skip_counts,
        visible_match_source=out["visible_match_source"],
    )
    return out
