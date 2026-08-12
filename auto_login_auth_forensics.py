"""Passive, secret-safe telemetry for the canonical Instagram auth flow.

The recorder is deliberately decision-blind: it only observes hierarchy data
that the login engine already collected.  It never sleeps, dumps UI state,
takes screenshots, writes the database, or performs a device action.
"""

from __future__ import annotations

import hashlib
import os
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Mapping


FLAG_NAME = "AUTO_LOGIN_AUTH_FORENSICS_V1_ENABLED"
ACCOUNT_IDS_FLAG_NAME = "AUTO_LOGIN_AUTH_FORENSICS_V1_ACCOUNT_IDS"
SCHEMA_VERSION = "auto_login_auth_forensics_v1"
_TRUE_VALUES = {"1", "true", "yes", "on", "enabled"}
_MAX_EVENTS = 96
_ALLOWED_CONTEXT = (
    "request_id",
    "run_id",
    "account_id",
    "app_instance_id",
    "device_id",
    "worker_sha",
    "expected_package",
)
_SENSITIVE_ATTR = re.compile(
    r'\s(?:text|content-desc|hint|value|password|username|email|phone)="[^"]*"',
    re.IGNORECASE,
)
_SAFE_NODE_ATTR = re.compile(
    r'\s(class|resource-id|package|clickable|enabled|focusable|focused|selected|checked|bounds)="([^"]*)"',
    re.IGNORECASE,
)


def enabled_from_env(env: Mapping[str, str] | None = None) -> bool:
    source = os.environ if env is None else env
    return str(source.get(FLAG_NAME) or "").strip().lower() in _TRUE_VALUES


def enabled_for_account(account_id: str, env: Mapping[str, str] | None = None) -> bool:
    source = os.environ if env is None else env
    if not enabled_from_env(source):
        return False
    allowlist = {
        item.strip()
        for item in str(source.get(ACCOUNT_IDS_FLAG_NAME) or "").split(",")
        if item.strip()
    }
    return not allowlist or str(account_id or "").strip() in allowlist


def safe_hierarchy_fingerprint(hierarchy_xml: str | None) -> str:
    """Hash structural attributes only; text/password length never enters it."""

    xml = _SENSITIVE_ATTR.sub("", str(hierarchy_xml or ""))
    structural = "|".join(
        f"{name.lower()}={value}"
        for name, value in _SAFE_NODE_ATTR.findall(xml)
    )
    if not structural:
        structural = "empty"
    return hashlib.sha256(structural.encode("utf-8", errors="ignore")).hexdigest()[:20]


def safe_ui_states(hierarchy_xml: str | None, observed: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return categorical states only; never return field content or its length."""

    xml = str(hierarchy_xml or "")
    lower = xml.lower()
    data = dict(observed or {})
    return {
        "button_state": _button_state(lower),
        "username_field_state": _field_state(lower, "username"),
        "password_field_state": _field_state(lower, "password"),
        "challenge_state": "present" if bool(
            data.get("verification_code_challenge_detected")
            or data.get("email_code_challenge_detected")
            or data.get("challenge_type")
        ) else "absent",
        "system_overlay_state": "present" if bool(
            data.get("save_password_prompt_present")
            or data.get("save_login_info_prompt_present")
            or data.get("post_login_location_services_prompt_present")
            or data.get("instagram_turn_on_notifications_prompt_present")
            or data.get("android_notification_settings_present")
        ) else "absent",
    }


def _button_state(lower_xml: str) -> str:
    if not lower_xml:
        return "unknown"
    login_surface = any(token in lower_xml for token in ("log in", "log_in_button", "login_button"))
    if not login_surface:
        return "not_visible"
    if 'enabled="false"' in lower_xml:
        return "visible_disabled_or_unknown"
    return "visible_enabled_or_unknown"


def _field_state(lower_xml: str, kind: str) -> str:
    if not lower_xml:
        return "unknown"
    tokens = ("password",) if kind == "password" else ("username", "email or mobile", "phone number")
    return "present_redacted" if any(token in lower_xml for token in tokens) else "not_visible"


class AuthForensicsTrace:
    """In-memory bounded recorder. Snapshotting is the only flush boundary."""

    def __init__(
        self,
        *,
        enabled: bool,
        context: Mapping[str, Any] | None = None,
        timer: Callable[[], float] | None = None,
        utc_now: Callable[[], datetime] | None = None,
    ) -> None:
        self.enabled = bool(enabled)
        self._timer = timer or time.perf_counter
        self._utc_now = utc_now or (lambda: datetime.now(timezone.utc))
        self._start = self._timer()
        self._submit_started: dict[int, float] = {}
        self._initial_pid: str | None = None
        self._loading_started: dict[int, float] = {}
        self._events: list[dict[str, Any]] = []
        self._dropped = 0
        self._overhead_ms: list[float] = []
        self.context = {
            key: str((context or {}).get(key) or "")[:160]
            for key in _ALLOWED_CONTEXT
        }
        self.auth_trace_id = str(uuid.uuid4()) if self.enabled else ""

    @classmethod
    def from_env(
        cls,
        *,
        context: Mapping[str, Any] | None = None,
        timer: Callable[[], float] | None = None,
        utc_now: Callable[[], datetime] | None = None,
        env: Mapping[str, str] | None = None,
    ) -> "AuthForensicsTrace":
        context_data = dict(context or {})
        return cls(
            enabled=enabled_for_account(str(context_data.get("account_id") or ""), env),
            context=context_data,
            timer=timer,
            utc_now=utc_now,
        )

    def record(self, event: str, *, attempt: int = 1, **fields: Any) -> None:
        if not self.enabled:
            return
        started = self._timer()
        if len(self._events) >= _MAX_EVENTS:
            self._dropped += 1
            return
        safe_fields = _safe_fields(fields)
        self._events.append(
            {
                "event": str(event or "UNKNOWN")[:80],
                "attempt_number": max(1, int(attempt or 1)),
                "monotonic_ms": round((started - self._start) * 1000.0, 3),
                "utc": self._utc_now().astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                "auth_trace_id": self.auth_trace_id,
                **self.context,
                **safe_fields,
            }
        )
        self._overhead_ms.append(max(0.0, (self._timer() - started) * 1000.0))

    def mark_submit_tap(self, *, attempt: int = 1) -> None:
        if self.enabled:
            self._submit_started[max(1, int(attempt or 1))] = self._timer()
        self.record("LOGIN_SUBMIT_TAP", attempt=attempt)

    def observe(
        self,
        *,
        attempt: int,
        hierarchy_xml: str | None,
        observed: Mapping[str, Any] | None,
        app_current: Mapping[str, Any] | None = None,
    ) -> None:
        if not self.enabled:
            return
        current = dict(app_current or {})
        pid = str(current.get("pid") or "")[:32]
        if pid and self._initial_pid is None:
            self._initial_pid = pid
        submitted = self._submit_started.get(max(1, int(attempt or 1)))
        elapsed = None if submitted is None else round((self._timer() - submitted) * 1000.0, 3)
        classified = dict(observed or {})
        attempt_number = max(1, int(attempt or 1))
        classified_state = str(classified.get("outcome") or classified.get("screen_type") or "unknown")[:80]
        screen_type = str(classified.get("screen_type") or "unknown")[:80]
        is_loading = screen_type == "loading" or classified_state == "loading"
        if is_loading and attempt_number not in self._loading_started:
            self._loading_started[attempt_number] = self._timer()
            self.record("AUTH_LOADING_STARTED", attempt=attempt_number, elapsed_since_tap_ms=elapsed)
        elif not is_loading and attempt_number in self._loading_started:
            started = self._loading_started.pop(attempt_number)
            self.record(
                "AUTH_LOADING_ENDED",
                attempt=attempt_number,
                loading_duration_ms=round((self._timer() - started) * 1000.0, 3),
                next_state=classified_state,
            )
        self.record(
            "AUTH_OBSERVATION",
            attempt=attempt_number,
            elapsed_since_tap_ms=elapsed,
            classified_state=classified_state,
            screen_type=screen_type,
            foreground_package=str(current.get("package") or "")[:160],
            foreground_activity=str(current.get("activity") or "")[:160],
            window_focus=("instagram_foreground" if current.get("package") == self.context.get("expected_package") else "not_instagram_or_unknown"),
            instagram_pid_present=bool(pid),
            instagram_pid_changed=bool(pid and self._initial_pid and pid != self._initial_pid),
            hierarchy_fingerprint=safe_hierarchy_fingerprint(hierarchy_xml),
            **safe_ui_states(hierarchy_xml, classified),
        )

    def snapshot(self, *, terminal_outcome: str = "") -> dict[str, Any]:
        if not self.enabled:
            return {"enabled": False, "schema_version": SCHEMA_VERSION}
        overhead = sorted(self._overhead_ms)
        median = overhead[len(overhead) // 2] if overhead else 0.0
        return {
            "enabled": True,
            "schema_version": SCHEMA_VERSION,
            "auth_trace_id": self.auth_trace_id,
            **self.context,
            "terminal_outcome": str(terminal_outcome or "")[:100],
            "events": list(self._events),
            "event_count": len(self._events),
            "dropped_event_count": self._dropped,
            "median_observation_overhead_ms": round(median, 3),
            "passive_sources": {
                "existing_hierarchy": True,
                "app_current_if_cached": True,
                "accessibility_stream": False,
                "network_trace": False,
                "logcat_window": False,
            },
        }


def _safe_fields(fields: Mapping[str, Any]) -> dict[str, Any]:
    blocked = (
        "secret",
        "password",
        "username",
        "email",
        "phone",
        "vault",
        "xml",
        "content",
        "ime",
        "keyboard",
    )
    safe: dict[str, Any] = {}
    for key, value in fields.items():
        name = str(key or "")
        categorical_exception = name in {"username_field_state", "password_field_state"}
        if not categorical_exception and any(token in name.lower() for token in blocked):
            continue
        if isinstance(value, bool) or value is None or isinstance(value, (int, float)):
            safe[name] = value
        elif isinstance(value, str):
            safe[name] = value[:240]
    return safe
