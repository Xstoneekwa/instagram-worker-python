"""CP4 — session transition buffer helpers for worker runtime."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

SESSION_TRANSITION_BUFFER_MINUTES = 10
SESSION_TRANSITION_BUFFER_ACTIVE_REASON = "session_transition_buffer_active"


def _parse_iso(value: str | None) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def derive_session_transition_timestamps(
    session_start: str,
    session_end: str,
    *,
    buffer_minutes: int = SESSION_TRANSITION_BUFFER_MINUTES,
) -> dict[str, str] | None:
    start = _parse_iso(session_start)
    end = _parse_iso(session_end)
    if not start or not end or end <= start:
        return None
    buffer = timedelta(minutes=max(1, int(buffer_minutes)))
    if end - start <= buffer:
        return None
    return {
        "session_start": start.isoformat().replace("+00:00", "Z"),
        "session_end": end.isoformat().replace("+00:00", "Z"),
        "business_action_deadline": (end - buffer).isoformat().replace("+00:00", "Z"),
        "preflight_start": (start - buffer).isoformat().replace("+00:00", "Z"),
    }


def business_actions_allowed_now(
    *,
    now: datetime | None = None,
    business_action_deadline: str | None = None,
    session_end: str | None = None,
    buffer_minutes: int = SESSION_TRANSITION_BUFFER_MINUTES,
) -> bool:
    current = now or datetime.now(timezone.utc)
    deadline = _parse_iso(business_action_deadline)
    if deadline is None and session_end:
        end = _parse_iso(session_end)
        if end is not None:
            deadline = end - timedelta(minutes=max(1, int(buffer_minutes)))
    if deadline is None:
        return True
    return current < deadline


def resolve_business_action_deadline(metadata: dict[str, Any] | None, dispatch_ctx: dict[str, Any] | None = None) -> str | None:
    meta = dict(metadata or {})
    dispatch = dict(dispatch_ctx or {})
    for key in ("business_action_deadline",):
        value = str(meta.get(key) or dispatch.get(key) or "").strip()
        if value:
            return value
    starts = str(
        meta.get("scheduled_session_start")
        or meta.get("scheduled_session_at")
        or dispatch.get("starts_at")
        or ""
    ).strip()
    ends = str(
        meta.get("scheduled_session_end")
        or meta.get("scheduled_session_ends_at")
        or dispatch.get("ends_at")
        or ""
    ).strip()
    derived = derive_session_transition_timestamps(starts, ends) if starts and ends else None
    return str((derived or {}).get("business_action_deadline") or "").strip() or None
