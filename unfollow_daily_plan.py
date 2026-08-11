"""Authoritative business-day Unfollow cohort and resume contract.

The existing Unfollow checkpoint remains the durable carrier.  This module
adds a stable, ordered daily plan to that checkpoint; it does not introduce a
second table, job, or scheduler.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Iterable

from social_memory import normalize_social_username


SCHEMA = "UNFOLLOW_DAILY_PLAN_V1"


def _key(value: Any) -> str:
    return normalize_social_username(str(value or ""))


def _ordered_unique_candidates(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = _key(row.get("username_normalized") or row.get("username"))
        if not key or key in seen:
            continue
        seen.add(key)
        out.append({**row, "username_normalized": key})
    return out


def contract_version(settings: Any) -> str:
    payload = {
        "mode": str(getattr(settings, "mode", "") or ""),
        "after_days": max(0, int(getattr(settings, "after_days", 0) or 0)),
        "day_limit": max(0, int(getattr(settings, "day_limit", 0) or 0)),
        "session_limit": max(0, int(getattr(settings, "session_limit", 0) or 0)),
        "package_default_snapshot": dict(
            getattr(settings, "package_default_snapshot", {}) or {}
        ),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()[:20]


def _plan_id(
    *,
    account_id: str,
    business_date_sast: str,
    package_contract_version: str,
    ordered_usernames: list[str],
) -> str:
    payload = "\n".join(
        [account_id, business_date_sast, package_contract_version, *ordered_usernames]
    ).encode()
    return "udp1_" + hashlib.sha256(payload).hexdigest()[:32]


def _daily_plan_from_checkpoint(checkpoint: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(checkpoint, dict):
        return {}
    nested = checkpoint.get("daily_plan")
    if isinstance(nested, dict):
        return nested
    if checkpoint.get("schema") == SCHEMA:
        return checkpoint
    return {}


def frozen_unfollow_after_days(
    *,
    account_id: str,
    business_date_sast: str,
    current_after_days: int,
    resume_checkpoint: dict[str, Any] | None,
) -> int:
    """Return the policy frozen by today's authoritative plan, when present."""

    prior = _daily_plan_from_checkpoint(resume_checkpoint)
    snapshot = prior.get("policy_snapshot")
    if not isinstance(snapshot, dict):
        return max(0, int(current_after_days or 0))
    valid_prior = bool(
        prior.get("schema") == SCHEMA
        and str(prior.get("account_id") or "") == str(account_id or "").strip()
        and str(prior.get("business_date_sast") or "")
        == str(business_date_sast or "").strip()
        and str(prior.get("plan_id") or "")
    )
    if not valid_prior or snapshot.get("unfollow_after_days") is None:
        return max(0, int(current_after_days or 0))
    return max(0, int(snapshot["unfollow_after_days"]))


def prepare_authoritative_daily_plan(
    *,
    account_id: str,
    business_date_sast: str,
    package_contract_version: str,
    daily_quota_target: int,
    session_quota_target: int | None = None,
    current_unfollow_after_days: int,
    current_eligible_candidates: Iterable[dict[str, Any]],
    resume_checkpoint: dict[str, Any] | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Freeze once, then reuse the same ordered remaining queue.

    Candidates eligible only after the freeze are reported separately and are
    never silently appended to the authoritative cohort.
    """

    aid = str(account_id or "").strip()
    business_date = str(business_date_sast or "").strip()
    version = str(package_contract_version or "").strip()
    if not aid or not business_date or not version:
        raise ValueError("unfollow_daily_plan_identity_required")

    current = _ordered_unique_candidates(current_eligible_candidates)
    daily_quota = max(0, int(daily_quota_target or 0))
    session_quota = max(0, int(session_quota_target or daily_quota))
    explicit_lower_session_cap = bool(session_quota and daily_quota and session_quota < daily_quota)
    current_by_key = {row["username_normalized"]: row for row in current}
    current_order = [row["username_normalized"] for row in current]
    prior = _daily_plan_from_checkpoint(resume_checkpoint)
    valid_prior = bool(
        prior.get("schema") == SCHEMA
        and str(prior.get("account_id") or "") == aid
        and str(prior.get("business_date_sast") or "") == business_date
        and str(prior.get("plan_id") or "")
    )

    if valid_prior:
        initial_order = [
            key for key in (_key(value) for value in prior.get("ordered_candidates") or []) if key
        ]
        initial_set = set(initial_order)
        remaining_order = [
            key for key in (_key(value) for value in prior.get("remaining_candidates") or []) if key
        ]
        terminal_or_ineligible = [key for key in remaining_order if key not in current_by_key]
        actionable_order = [key for key in remaining_order if key in current_by_key]
        newly_eligible = [key for key in current_order if key not in initial_set]
        plan = {
            **prior,
            "schema": SCHEMA,
            "resume_reused": True,
            "cohort_rebuilt": False,
            "remaining_candidates": actionable_order,
            "currently_terminal_or_ineligible": terminal_or_ineligible,
            "newly_eligible_after_plan_freeze": newly_eligible,
            "newly_eligible_policy": "classified_not_silently_appended",
            "last_loaded_at": datetime.now(timezone.utc).isoformat(),
            "session_quota_target": session_quota,
            "multi_session_required_by_explicit_cap_override": explicit_lower_session_cap,
            "current_package_contract_version_observed": version,
            "package_contract_changed_after_plan_freeze": str(
                prior.get("package_contract_version") or ""
            ) != version,
        }
        return {
            "daily_plan": plan,
            "candidates": [current_by_key[key] for key in actionable_order],
            "resume_reused": True,
        }

    ordered = current_order
    now = created_at or datetime.now(timezone.utc).isoformat()
    plan = {
        "schema": SCHEMA,
        "plan_id": _plan_id(
            account_id=aid,
            business_date_sast=business_date,
            package_contract_version=version,
            ordered_usernames=ordered,
        ),
        "account_id": aid,
        "business_date_sast": business_date,
        "package_contract_version": version,
        "policy_snapshot": {
            "unfollow_after_days": max(0, int(current_unfollow_after_days or 0)),
            "source": "ig_account_unfollow_settings_at_plan_freeze",
        },
        "cohort_created_at": now,
        "initial_db_eligible_count": len(ordered),
        "daily_quota_target": daily_quota,
        "session_quota_target": session_quota,
        "multi_session_required_by_explicit_cap_override": explicit_lower_session_cap,
        "ordered_candidates": ordered,
        "remaining_candidates": ordered,
        "verified_candidates": [],
        "persisted_candidates": [],
        "unavailable_candidates": [],
        "retryable_candidates": [],
        "currently_terminal_or_ineligible": [],
        "newly_eligible_after_plan_freeze": [],
        "newly_eligible_policy": "classified_not_silently_appended",
        "resume_reused": False,
        "cohort_rebuilt": False,
        "last_loaded_at": now,
    }
    return {"daily_plan": plan, "candidates": current, "resume_reused": False}


def checkpoint_daily_plan(
    daily_plan: dict[str, Any],
    unfollow_checkpoint: dict[str, Any] | None,
) -> dict[str, Any]:
    checkpoint = dict(unfollow_checkpoint or {})
    prior_remaining = [
        key
        for key in (_key(value) for value in daily_plan.get("remaining_candidates") or [])
        if key
    ]
    remaining_set = {
        key
        for key in (_key(value) for value in checkpoint.get("remaining_usernames") or [])
        if key
    }
    def cumulative(field: str, checkpoint_field: str) -> list[str]:
        return sorted(
            {
                *{
                    _key(value)
                    for value in daily_plan.get(field) or []
                    if _key(value)
                },
                *{
                    _key(value)
                    for value in checkpoint.get(checkpoint_field) or []
                    if _key(value)
                },
            }
        )

    daily = {
        **daily_plan,
        "remaining_candidates": [key for key in prior_remaining if key in remaining_set],
        "verified_candidates": cumulative("verified_candidates", "verified_usernames"),
        "persisted_candidates": cumulative("persisted_candidates", "persisted_usernames"),
        "unavailable_candidates": cumulative("unavailable_candidates", "unavailable_usernames"),
        "retryable_candidates": cumulative("retryable_candidates", "retryable_usernames"),
        "last_checkpoint_at": datetime.now(timezone.utc).isoformat(),
    }
    checkpoint["daily_plan"] = daily
    return checkpoint
