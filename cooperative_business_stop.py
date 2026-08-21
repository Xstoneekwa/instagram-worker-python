"""Durable, execution-bound cooperative stop transport for account sessions.

The control plane writes tiny local JSON tokens.  Action loops only perform a
single stat/read at their existing safe boundaries; no database poll, XML dump,
screen capture, sleep, or device action is added to the happy path.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
CONTROL_DIR_ENV = "COOPERATIVE_STOP_CONTROL_DIR"
DEFAULT_CONTROL_DIR = "/Users/admin/phonefarm-runtime/control/cooperative-stop-v1"
ALLOWED_REASONS = frozenset(
    {
        "scheduled_business_deadline",
        "follow_to_unfollow_time_handoff",
        "human_manual_stop",
        "dispatcher_watchdog",
        "deployment_shutdown",
        "control_plane_service_stop",
    }
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_utc(value: str | None) -> datetime | None:
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


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class StopContext:
    account_id: str
    request_id: str
    device_id: str
    business_session_id: str
    run_id: str = ""
    generation: str = "1"

    @classmethod
    def from_env(cls) -> "StopContext | None":
        account_id = str(os.getenv("COOPERATIVE_STOP_ACCOUNT_ID") or "").strip()
        request_id = str(os.getenv("COOPERATIVE_STOP_REQUEST_ID") or "").strip()
        device_id = str(os.getenv("COOPERATIVE_STOP_DEVICE_ID") or "").strip()
        session_id = str(os.getenv("COOPERATIVE_STOP_BUSINESS_SESSION_ID") or "").strip()
        if not account_id or not request_id or not device_id or not session_id:
            return None
        return cls(
            account_id=account_id,
            request_id=request_id,
            device_id=device_id,
            business_session_id=session_id,
            run_id=str(os.getenv("COOPERATIVE_STOP_RUN_ID") or "").strip(),
            generation=str(os.getenv("COOPERATIVE_STOP_GENERATION") or "1").strip() or "1",
        )

    def binding(self) -> dict[str, str]:
        return {
            "account_id": self.account_id,
            "request_id": self.request_id,
            "device_id": self.device_id,
            "business_session_id": self.business_session_id,
            "run_id": self.run_id,
            "generation": self.generation,
        }

    def key(self) -> str:
        raw = "\0".join(self.binding().values()).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()


def control_dir() -> Path:
    return Path(os.getenv(CONTROL_DIR_ENV) or DEFAULT_CONTROL_DIR)


def _path(context: StopContext, suffix: str) -> Path:
    return control_dir() / f"{context.key()}.{suffix}.json"


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def _read_bound(path: Path, context: StopContext) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError, TypeError):
        return None
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        return None
    binding = payload.get("binding")
    if not isinstance(binding, dict) or binding != context.binding():
        return None
    return payload


def request_stop(
    context: StopContext,
    *,
    reason: str,
    deadline: str | None = None,
    requested_at: datetime | None = None,
) -> dict[str, Any]:
    reason_value = str(reason or "").strip()
    if reason_value not in ALLOWED_REASONS:
        raise ValueError("unsupported_cooperative_stop_reason")
    existing = read_stop(context)
    if existing is not None:
        return existing
    payload = {
        "schema_version": SCHEMA_VERSION,
        "binding": context.binding(),
        "reason": reason_value,
        "requested_at": iso_utc(requested_at or _utc_now()),
        "deadline": str(deadline or "").strip() or None,
    }
    _atomic_json(_path(context, "intent"), payload)
    return payload


def read_stop(context: StopContext | None) -> dict[str, Any] | None:
    if context is None:
        return None
    payload = _read_bound(_path(context, "intent"), context)
    if payload is None or str(payload.get("reason") or "") not in ALLOWED_REASONS:
        return None
    requested = parse_utc(str(payload.get("requested_at") or ""))
    if requested is None:
        return None
    # A token from before this execution generation cannot poison a later one.
    session_start = parse_utc(os.getenv("SCHEDULED_SESSION_START"))
    if session_start is not None and requested < session_start:
        return None
    return payload


def acknowledge_stop(
    context: StopContext,
    *,
    phase: str,
    safe_boundary: str,
    session_termination_class: str,
) -> dict[str, Any]:
    intent = read_stop(context)
    if intent is None:
        raise RuntimeError("cooperative_stop_intent_missing")
    payload = {
        "schema_version": SCHEMA_VERSION,
        "binding": context.binding(),
        "reason": intent["reason"],
        "requested_at": intent["requested_at"],
        "acknowledged_at": iso_utc(_utc_now()),
        "phase": str(phase or "").strip(),
        "safe_boundary": str(safe_boundary or "").strip(),
        "session_termination_class": str(session_termination_class or "").strip(),
    }
    _atomic_json(_path(context, "ack"), payload)
    return payload


def read_ack(context: StopContext) -> dict[str, Any] | None:
    return _read_bound(_path(context, "ack"), context)


def clear_context(context: StopContext) -> None:
    for suffix in ("intent", "ack", "termination"):
        try:
            _path(context, suffix).unlink()
        except FileNotFoundError:
            pass


def clear_intent_and_ack(context: StopContext) -> None:
    """Consume a same-session phase-handoff token without touching origin proof."""
    for suffix in ("intent", "ack"):
        try:
            _path(context, suffix).unlink()
        except FileNotFoundError:
            pass


def record_termination_origin(context: StopContext, *, reason: str) -> None:
    if reason not in {
        "human_manual_stop",
        "dispatcher_watchdog",
        "deployment_shutdown",
        "control_plane_service_stop",
    }:
        raise ValueError("unsupported_termination_origin")
    _atomic_json(
        _path(context, "termination"),
        {
            "schema_version": SCHEMA_VERSION,
            "binding": context.binding(),
            "reason": reason,
            "recorded_at": iso_utc(_utc_now()),
        },
    )


def read_termination_origin(context: StopContext | None) -> str:
    payload = _read_bound(_path(context, "termination"), context) if context is not None else None
    reason = str((payload or {}).get("reason") or "")
    if reason:
        return reason
    infrastructure_origin = str(os.getenv("RUN_CONTROL_SIGNAL_ORIGIN") or "").strip()
    if infrastructure_origin == "control_plane_service_stop":
        return infrastructure_origin
    return "human_manual_stop"


def action_start_allowed(
    *,
    deadline: str | None,
    bounded_action_seconds: float,
    now: datetime | None = None,
) -> bool:
    parsed = parse_utc(deadline)
    if parsed is None:
        return True
    remaining = (parsed - (now or _utc_now())).total_seconds()
    return remaining >= max(0.0, float(bounded_action_seconds))


def follow_handoff_deadline(
    *,
    business_deadline: str | None,
    eligible_unfollows: int,
    unfollow_quota_remaining: int,
    estimated_seconds_per_unfollow: int,
    navigation_reserve_seconds: int = 30,
    recovery_reserve_seconds: int = 75,
) -> str | None:
    deadline = parse_utc(business_deadline)
    actions = min(max(0, int(eligible_unfollows)), max(0, int(unfollow_quota_remaining)))
    if deadline is None or actions <= 0:
        return None
    reserve = (
        actions * max(1, int(estimated_seconds_per_unfollow))
        + max(0, int(navigation_reserve_seconds))
        + max(0, int(recovery_reserve_seconds))
    )
    return iso_utc(datetime.fromtimestamp(deadline.timestamp() - reserve, tz=timezone.utc))
