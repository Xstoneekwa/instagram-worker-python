"""Pure helpers for Live View host agent (LV-Web-2A screenshot polling)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

ACTIVE_SESSION_STATUSES = frozenset({"pending", "starting", "active"})
TERMINAL_SESSION_STATUSES = frozenset({"stopped", "failed", "expired"})
OPEN_ASSIGNMENT_STATUSES = frozenset({"pending", "reserved", "active"})

FOREGROUND_PACKAGE_RE = re.compile(
    r"(?:mCurrentFocus|mFocusedApp|ResumedActivity).*?(?:\{[\s\S]*?)?\s([a-zA-Z0-9_.]+)/",
    re.MULTILINE,
)


@dataclass(frozen=True)
class LiveViewHostConfig:
    enabled: bool
    host_id: str
    poll_interval_seconds: float
    max_session_seconds: int
    frame_dir: str
    frame_upload_enabled: bool
    storage_bucket: str
    adb_path: str

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> LiveViewHostConfig:
        source = env or os.environ
        return cls(
            enabled=_env_bool(source, "LIVE_VIEW_HOST_AGENT_ENABLED", False),
            host_id=_env_str(source, "LIVE_VIEW_HOST_ID", "").strip(),
            poll_interval_seconds=max(0.5, _env_float(source, "LIVE_VIEW_POLL_INTERVAL_SECONDS", 2.0)),
            max_session_seconds=max(30, _env_int(source, "LIVE_VIEW_MAX_SESSION_SECONDS", 600)),
            frame_dir=_env_str(source, "LIVE_VIEW_FRAME_DIR", ".local/live-view-frames"),
            frame_upload_enabled=_env_bool(source, "LIVE_VIEW_FRAME_UPLOAD_ENABLED", True),
            storage_bucket=_env_str(source, "LIVE_VIEW_FRAME_STORAGE_BUCKET", "live-view-frames"),
            adb_path=_env_str(source, "ADB_PATH", "adb"),
        )


def _env_bool(source: Mapping[str, str], name: str, default: bool) -> bool:
    raw = source.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _env_int(source: Mapping[str, str], name: str, default: int) -> int:
    raw = source.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(str(raw).strip())
    except ValueError:
        return default


def _env_float(source: Mapping[str, str], name: str, default: float) -> float:
    raw = source.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return float(str(raw).strip())
    except ValueError:
        return default


def _env_str(source: Mapping[str, str], name: str, default: str) -> str:
    raw = source.get(name)
    if raw is None:
        return default
    value = str(raw).strip()
    return value if value else default


def mask_serial(serial: str | None) -> str | None:
    value = str(serial or "").strip()
    if not value:
        return None
    if len(value) <= 4:
        return "*" * len(value)
    return f"...{value[-4:]}"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(value: str | None) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def is_session_expired(row: Mapping[str, Any], *, now: datetime | None = None) -> bool:
    expires_at = parse_iso(str(row.get("expires_at") or ""))
    if expires_at is None:
        return False
    current = now or datetime.now(timezone.utc)
    return expires_at <= current


def session_runtime_expired(
    row: Mapping[str, Any],
    *,
    max_seconds: int,
    now: datetime | None = None,
) -> bool:
    started_at = parse_iso(str(row.get("started_at") or ""))
    if started_at is None:
        return False
    current = now or datetime.now(timezone.utc)
    elapsed = (current - started_at).total_seconds()
    return elapsed >= max(1, int(max_seconds))


def session_is_terminal(row: Mapping[str, Any]) -> bool:
    status = str(row.get("status") or "").strip().lower()
    return status in TERMINAL_SESSION_STATUSES


def should_claim_session(
    row: Mapping[str, Any],
    *,
    host_id: str,
    now: datetime | None = None,
) -> tuple[bool, str | None]:
    status = str(row.get("status") or "").strip().lower()
    if status != "pending":
        return False, "not_pending"
    session_host = str(row.get("host_id") or "").strip()
    if not session_host or session_host != host_id:
        return False, "host_mismatch"
    if is_session_expired(row, now=now):
        return False, "expired"
    return True, None


def package_matches(expected: str | None, actual: str | None) -> bool:
    exp = str(expected or "").strip()
    act = str(actual or "").strip()
    if not exp:
        return False
    return exp == act


def parse_foreground_package(dumpsys_window_output: str) -> str | None:
    text = str(dumpsys_window_output or "")
    if not text:
        return None
    for pattern in (
        FOREGROUND_PACKAGE_RE,
        re.compile(r"mCurrentFocus=Window\{[^\s]+\s+u\d+\s+([a-zA-Z0-9_.]+)/", re.MULTILINE),
        re.compile(r"mFocusedApp=AppWindowToken\{[^\s]+\s+token=Token\{[^\s]+\s+ActivityRecord\{[^\s]+\s+u\d+\s+([a-zA-Z0-9_.]+)/"),
    ):
        match = pattern.search(text)
        if match:
            pkg = match.group(1).strip()
            if pkg:
                return pkg
    return None


def build_agent_metadata(
    *,
    host_id: str,
    transport: str,
    capture: str,
    reason: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "source": "live_view_host_agent",
        "host_id": host_id,
        "transport": transport,
        "capture": capture,
    }
    if reason:
        payload["reason"] = reason
    if extra:
        for key, value in extra.items():
            lowered = str(key).lower()
            if lowered in {
                "password",
                "secret",
                "secret_ref",
                "token",
                "adb_serial",
                "device_udid",
                "hub_port",
                "frame_file",
                "frame_path",
                "local_frame_file",
                "local_frame_path",
                "screenshot",
                "screenshot_path",
                "service_role",
            }:
                continue
            payload[key] = value
    return payload


def merge_metadata_safe(
    existing: Mapping[str, Any] | None,
    updates: Mapping[str, Any],
) -> dict[str, Any]:
    merged = dict(existing or {})
    merged.update(updates)
    return merged


def frame_storage_object_path(session_id: str) -> str:
    sid = str(session_id or "").strip()
    return f"{sid}/latest.png"


def local_frame_path(frame_dir: str, session_id: str) -> str:
    safe_id = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(session_id))
    return os.path.join(frame_dir, f"{safe_id}.png")


def status_message_for_panel(status: str, *, failure_reason: str | None = None) -> str:
    normalized = str(status or "").strip().lower()
    if normalized == "pending":
        return "Waiting for stream"
    if normalized == "starting":
        return "Starting stream"
    if normalized == "active":
        return "Live view stream"
    if normalized == "failed":
        return failure_reason or "Live view failed"
    if normalized == "stopped":
        return "Live view stopped"
    if normalized == "expired":
        return "Live view expired"
    return "Live view unavailable"
