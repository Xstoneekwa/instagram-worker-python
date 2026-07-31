"""Process-local hard latch for account-scoped Follow canary device actions.

The latch is set before any stop persistence.  Patched uiautomator2 action
entrypoints refuse every subsequent tap/swipe/key action, including UiObject
clicks that delegate back to the Device session.
"""

from __future__ import annotations

import atexit
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from types import MethodType
from typing import Any

from logs import log


class DeviceActionBlocked(RuntimeError):
    pass


_LOCK = threading.RLock()
_ENABLED = False
_STOP_REQUESTED = False
_ACTIVE_ACTIONS = 0
_TRACE: dict[str, Any] = {}
_TRACE_DIR = Path("/tmp/phonefarm-follow60-stop-traces")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def configure(*, enabled: bool, account_id: str, run_id: str) -> None:
    global _ENABLED, _STOP_REQUESTED, _ACTIVE_ACTIONS, _TRACE
    with _LOCK:
        _ENABLED = bool(enabled)
        _STOP_REQUESTED = False
        _ACTIVE_ACTIONS = 0
        _TRACE = {"account_id": account_id, "run_id": run_id, "configured_at": _now()}


def request_stop(*, signal_number: int | None = None, reason: str = "manual_stop") -> dict[str, Any]:
    global _STOP_REQUESTED
    with _LOCK:
        if not _STOP_REQUESTED:
            _STOP_REQUESTED = True
            _TRACE["stop_latch_set_at"] = _now()
            _TRACE["stop_reason"] = reason
            _TRACE["signal_number"] = signal_number
        if _ACTIVE_ACTIONS == 0 and "device_actions_quiesced_at" not in _TRACE:
            _TRACE["device_actions_quiesced_at"] = _now()
        return dict(_TRACE)


def stop_requested() -> bool:
    with _LOCK:
        return bool(_ENABLED and _STOP_REQUESTED)


def assert_device_action_allowed(action: str) -> None:
    with _LOCK:
        if _ENABLED and _STOP_REQUESTED:
            log("warning", "follow_60s_device_action_blocked", action=action, **dict(_TRACE))
            raise DeviceActionBlocked(f"device_action_blocked_after_stop:{action}")


def _before(action: str) -> None:
    global _ACTIVE_ACTIONS
    with _LOCK:
        if _ENABLED and _STOP_REQUESTED:
            log("warning", "follow_60s_device_action_blocked", action=action, **dict(_TRACE))
            raise DeviceActionBlocked(f"device_action_blocked_after_stop:{action}")
        _ACTIVE_ACTIONS += 1
        _TRACE["last_device_action_at"] = _now()
        _TRACE["last_device_action"] = action


def _after() -> None:
    global _ACTIVE_ACTIONS
    with _LOCK:
        _ACTIVE_ACTIONS = max(0, _ACTIVE_ACTIONS - 1)
        if _STOP_REQUESTED and _ACTIVE_ACTIONS == 0 and "device_actions_quiesced_at" not in _TRACE:
            _TRACE["device_actions_quiesced_at"] = _now()


def install_device_guard(device: Any) -> Any:
    if not _ENABLED or getattr(device, "_follow60_action_guard_installed", False):
        return device
    for name in (
        "click",
        "swipe",
        "press",
        "long_click",
        "drag",
        "send_keys",
        "clear_text",
        "app_start",
        "app_stop",
        "unlock",
        "screen_on",
        "screen_off",
        "open_url",
    ):
        original = getattr(device, name, None)
        if not callable(original):
            continue

        def guarded(self: Any, *args: Any, __name: str = name, __original: Any = original, **kwargs: Any) -> Any:
            _before(__name)
            try:
                return __original(*args, **kwargs)
            finally:
                _after()

        setattr(device, name, MethodType(guarded, device))
    original_shell = getattr(device, "shell", None)
    if callable(original_shell):
        def guarded_shell(self: Any, command: Any, *args: Any, **kwargs: Any) -> Any:
            command_text = " ".join(command) if isinstance(command, (list, tuple)) else str(command or "")
            is_ui_action = any(token in command_text.lower() for token in ("input tap", "input swipe", "input keyevent", "monkey "))
            if not is_ui_action:
                return original_shell(command, *args, **kwargs)
            _before("shell_ui_action")
            try:
                return original_shell(command, *args, **kwargs)
            finally:
                _after()
        setattr(device, "shell", MethodType(guarded_shell, device))
    setattr(device, "_follow60_action_guard_installed", True)
    log("info", "follow_60s_device_action_guard_installed", **dict(_TRACE))
    return device


def trace() -> dict[str, Any]:
    with _LOCK:
        return dict(_TRACE)


def mark_worker_stopped() -> None:
    if not _ENABLED:
        return
    with _LOCK:
        _TRACE["worker_stopped_at"] = _now()
        payload = dict(_TRACE)
    try:
        _TRACE_DIR.mkdir(parents=True, exist_ok=True)
        run_id = str(payload.get("run_id") or "unknown").replace("/", "_")
        (_TRACE_DIR / f"{run_id}.json").write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    except Exception as exc:
        log("warning", "follow_60s_stop_trace_write_failed", reason=str(exc)[:200])


atexit.register(mark_worker_stopped)
