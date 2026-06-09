"""Device connection and low-level helpers (ADB / uiautomator2)."""

from __future__ import annotations

import base64
import hmac
import json
import os
import random
import re
import shlex
import shutil
import subprocess
import time
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, TypeVar

import uiautomator2 as u2

import config
from logs import log

T = TypeVar("T")

# Once per worker process: original IME before first FastInput switch (restore target).
_ime_session_original: str | None = None
# True after global animation scales are set to 0 for this process.
_android_animations_disabled_session: bool = False

# Local baseline for strict version lock (per clone / phone farm host).
LOCK_STATE_PATH = Path(__file__).resolve().parent / "lock_state.json"
_PLAY_STORE_PKG = "com.android.vending"
_VERSION_NAME_RE = re.compile(r"versionName=([^\s\]]+)")
_VERSION_CODE_RE = re.compile(r"versionCode=(\d+)")


def get_device_serial(d: u2.Device) -> str | None:
    """ADB serial for subprocess adb calls; falls back to config.DEVICE_SERIAL."""
    s = getattr(d, "serial", None)
    if s:
        return str(s)
    return config.DEVICE_SERIAL


def _adb_candidate_paths() -> list[str]:
    candidates: list[str] = []
    env_path = str(os.environ.get("ADB_PATH") or "").strip()
    if env_path:
        candidates.append(env_path)
    for key in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        root = str(os.environ.get(key) or "").strip()
        if root:
            candidates.append(str(Path(root).expanduser() / "platform-tools" / "adb"))
    candidates.extend(
        [
            str(Path.home() / "Library" / "Android" / "sdk" / "platform-tools" / "adb"),
            "/opt/homebrew/bin/adb",
            "/usr/local/bin/adb",
        ]
    )
    return candidates


@lru_cache(maxsize=1)
def resolve_adb_path() -> str | None:
    """Resolve adb executable for subprocess use (cached per process)."""

    seen: set[str] = set()
    for candidate in _adb_candidate_paths():
        path = Path(candidate).expanduser()
        path_text = str(path)
        if not path_text or path_text in seen:
            continue
        seen.add(path_text)
        if path.is_file() and os.access(path, os.X_OK):
            return path_text
    path_env = os.environ.get("PATH", "")
    discovered = shutil.which("adb", path=path_env or None)
    return discovered if discovered else None


def adb_available() -> bool:
    return resolve_adb_path() is not None


def _fast_ime_package(fast_ime_id: str | None = None) -> str:
    text = str(fast_ime_id or getattr(config, "FAST_IME", "") or "").strip()
    return text.split("/", 1)[0] if "/" in text else text


def inspect_adb_keyboard_state(serial: str | None, *, fast_ime_id: str | None = None) -> dict[str, Any]:
    """Return safe ADBKeyboard readiness state for diagnostics and preflight."""

    fast_ime = str(fast_ime_id or getattr(config, "FAST_IME", "") or "").strip()
    package_name = _fast_ime_package(fast_ime)
    state: dict[str, Any] = {
        "adb_path_resolved": adb_available(),
        "adb_keyboard_package_present": False,
        "adb_keyboard_ime_listed": False,
        "adb_keyboard_default": False,
        "default_input_method_suffix": "",
        "reason": "",
    }
    if not serial:
        state["reason"] = "adb_serial_missing"
        return state
    if not fast_ime or not package_name:
        state["reason"] = "fast_ime_not_configured"
        return state
    if not state["adb_path_resolved"]:
        state["reason"] = "adb_not_available"
        return state

    pkg_code, _, _ = _adb_run(serial, ["shell", "pm", "path", package_name], timeout_s=10.0)
    state["adb_keyboard_package_present"] = pkg_code == 0

    ime_code, ime_out, _ = _adb_run(serial, ["shell", "ime", "list", "-s"], timeout_s=10.0)
    ime_lines = [line.strip() for line in str(ime_out or "").splitlines() if line.strip()]
    state["adb_keyboard_ime_listed"] = ime_code == 0 and any(fast_ime in line for line in ime_lines)

    default = get_current_ime(serial)
    state["adb_keyboard_default"] = default == fast_ime
    state["default_input_method_suffix"] = default.split("/")[-1] if default else ""

    if not state["adb_keyboard_package_present"]:
        state["reason"] = "adb_keyboard_package_missing"
    elif not state["adb_keyboard_ime_listed"]:
        state["reason"] = "adb_keyboard_ime_not_enabled"
    elif not state["adb_keyboard_default"]:
        state["reason"] = "adb_keyboard_not_default"
    else:
        state["reason"] = "adb_keyboard_ready"
    return state


def ensure_adb_keyboard_ready(serial: str | None, *, fast_ime_id: str | None = None) -> dict[str, Any]:
    """Ensure configured ADBKeyboard IME is installed, enabled, and default."""

    fast_ime = str(fast_ime_id or getattr(config, "FAST_IME", "") or "").strip()
    state = inspect_adb_keyboard_state(serial, fast_ime_id=fast_ime)
    if state.get("reason") == "adb_keyboard_ready":
        return {**state, "ok": True, "enable_attempted": False, "set_default_attempted": False}
    if not serial or not fast_ime or not state.get("adb_path_resolved") or not state.get("adb_keyboard_package_present"):
        return {**state, "ok": False, "enable_attempted": False, "set_default_attempted": False}

    enable_attempted = False
    set_default_attempted = False
    if not state.get("adb_keyboard_ime_listed"):
        enable_attempted = True
        _adb_run(serial, ["shell", "ime", "enable", fast_ime], timeout_s=10.0)

    if not state.get("adb_keyboard_default"):
        set_default_attempted = True
        set_ime(serial, fast_ime)

    refreshed = inspect_adb_keyboard_state(serial, fast_ime_id=fast_ime)
    return {
        **refreshed,
        "ok": refreshed.get("reason") == "adb_keyboard_ready",
        "enable_attempted": enable_attempted,
        "set_default_attempted": set_default_attempted,
    }


def runner_subprocess_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Build a subprocess env with adb discoverable under launchd/minimal PATH."""

    env = dict(base or os.environ)
    adb_path = resolve_adb_path()
    if not adb_path:
        return env
    env["ADB_PATH"] = adb_path
    adb_dir = str(Path(adb_path).parent)
    existing_path = str(env.get("PATH") or "")
    path_parts = [part for part in [adb_dir, existing_path] if part]
    if path_parts:
        env["PATH"] = os.pathsep.join(path_parts)
    return env


def _adb_run(serial: str | None, argv: list[str], *, timeout_s: float = 45.0) -> tuple[int, str, str]:
    adb_path = resolve_adb_path()
    if not adb_path:
        log("warning", "adb_run_failed", argv=argv[:8], error="adb_not_available")
        return 1, "", "adb_not_available"
    cmd = [adb_path]
    if serial:
        cmd.extend(["-s", serial])
    cmd.extend(argv)
    try:
        p = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            env=runner_subprocess_env(),
        )
        out = (p.stdout or "").strip()
        err = (p.stderr or "").strip()
        return int(p.returncode), out, err
    except Exception as e:
        log("warning", "adb_run_failed", argv=argv[:8], error=str(e))
        return 1, "", str(e)


def _adb_shell_stdin(serial: str | None, shell_line: str, *, timeout_s: float = 10.0) -> tuple[int, str, str]:
    """Run `adb shell` with commands from stdin so sensitive input stays out of argv."""

    adb_path = resolve_adb_path()
    if not adb_path:
        log("warning", "adb_shell_stdin_failed", error="adb_not_available")
        return 1, "", "adb_not_available"
    cmd = [adb_path]
    if serial:
        cmd.extend(["-s", serial])
    cmd.append("shell")
    try:
        p = subprocess.run(
            cmd,
            input=shell_line,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            env=runner_subprocess_env(),
        )
        return int(p.returncode), (p.stdout or "").strip(), (p.stderr or "").strip()
    except Exception as exc:
        log("warning", "adb_shell_stdin_failed", error=type(exc).__name__)
        return 1, "", type(exc).__name__


def run_adb_keyboard_b64_input(
    serial: str,
    value: str,
    *,
    fast_ime_id: str,
) -> tuple[bool, str, bool, bool]:
    """Switch to FAST_IME when needed and inject text via ADB_INPUT_B64 broadcast."""

    result = run_adb_keyboard_b64_input_detailed(serial, value, fast_ime_id=fast_ime_id)
    return (
        bool(result.get("command_ok")),
        str(result.get("method") or "adb_keyboard_b64"),
        bool(result.get("switch_ok")),
        bool(result.get("broadcast_ok")),
    )


def run_adb_keyboard_b64_input_detailed(
    serial: str,
    value: str,
    *,
    fast_ime_id: str,
) -> dict[str, Any]:
    """Detailed ADBKeyboard input result without exposing entered text."""

    fast_ime_id = str(fast_ime_id or "").strip()
    ready = ensure_adb_keyboard_ready(serial, fast_ime_id=fast_ime_id)
    if not ready.get("ok"):
        return {
            "command_ok": False,
            "method": "adb_keyboard_b64",
            "switch_ok": False,
            "broadcast_ok": False,
            "reason": str(ready.get("reason") or "adb_keyboard_unavailable"),
            "adb_keyboard_ready": ready,
        }
    encoded = base64.b64encode(str(value or "").encode("utf-8")).decode("ascii")
    # Send through adb shell stdin so secret-derived input never appears in host argv.
    broadcast_code, _, _ = _adb_shell_stdin(
        serial,
        f"am broadcast -a ADB_INPUT_B64 --es msg {shlex.quote(encoded)}\n",
        timeout_s=10.0,
    )
    if broadcast_code != 0:
        text_code, _, _ = _adb_shell_stdin(
            serial,
            f"am broadcast -a ADB_INPUT_TEXT --es msg {shlex.quote(str(value or ''))}\n",
            timeout_s=10.0,
        )
        return {
            "command_ok": text_code == 0,
            "method": "adb_keyboard_text",
            "switch_ok": True,
            "broadcast_ok": text_code == 0,
            "reason": "" if text_code == 0 else "adb_keyboard_broadcast_failed",
            "adb_keyboard_ready": ready,
            "b64_broadcast_ok": False,
        }
    return {
        "command_ok": True,
        "method": "adb_keyboard_b64",
        "switch_ok": True,
        "broadcast_ok": True,
        "reason": "",
        "adb_keyboard_ready": ready,
        "b64_broadcast_ok": True,
    }


def _adb_shell(serial: str | None, *shell_tokens: str) -> tuple[int, str, str]:
    return _adb_run(serial, ["shell", *shell_tokens])


def get_current_ime(serial: str | None) -> str:
    """Current default input method id (e.g. com.pkg/.Service)."""
    code, out, _ = _adb_shell(serial, "settings", "get", "secure", "default_input_method")
    if code != 0:
        return ""
    return (out or "").strip()


def is_fast_ime_available(serial: str | None) -> bool:
    """True if FAST_IME appears in enabled IME list."""
    target = getattr(config, "FAST_IME", "") or ""
    if not target:
        return False
    code, out, _ = _adb_shell(serial, "ime", "list", "-s")
    if code != 0:
        return False
    lines = out.splitlines() if out else []
    return any(target in line for line in lines)


def set_ime(serial: str | None, ime: str) -> bool:
    code, _, _ = _adb_shell(serial, "ime", "set", ime)
    ok = code == 0
    if not ok:
        log("warning", "ime_set_failed", ime=ime, exit_code=code)
    return ok


def fast_input_text(serial: str | None, text: str) -> tuple[int, str]:
    """
    Send text via FastInputIME broadcast.

    Uses ``adb shell sh -c`` with a single quoted message so spaces, newlines,
    apostrophes and unicode are not split by the device shell (avoids ``pkg=merci`` /
    ``Ceci: inaccessible`` failures).
    """
    import shlex

    inner = f"am broadcast -a ADB_INPUT_TEXT --es msg {shlex.quote(str(text or ''))}"
    code, out, err = _adb_run(serial, ["shell", "sh", "-c", inner])
    tail = (out + " " + err).strip()[-300:]
    log("debug", "fast_input_broadcast", exit_code=code, output_tail=tail)
    return code, tail


def disable_android_animations(serial: str | None = None) -> None:
    """Disable window/transition/animator scales once per worker process."""
    global _android_animations_disabled_session
    if _android_animations_disabled_session:
        return
    cmds = (
        ("settings", "put", "global", "window_animation_scale", "0"),
        ("settings", "put", "global", "transition_animation_scale", "0"),
        ("settings", "put", "global", "animator_duration_scale", "0"),
    )
    for c in cmds:
        code, _, err = _adb_shell(serial, *c)
        if code != 0:
            log("warning", "disable_animations_step_failed", cmd=c, error=err)
    _android_animations_disabled_session = True
    log("info", "android_animations_disabled")


def run_fast_ime_input(
    serial: str | None,
    text: str,
    *,
    fast_ime_id: str,
) -> tuple[bool, str, bool, bool]:
    """
    Switch to configured fast IME (e.g. ADBKeyboard) if needed, ADB_INPUT_TEXT broadcast,
    restore session IME when we switched.

    Returns:
        (command_ok, typing_method_tag, fast_ime_switch_ok, fast_ime_broadcast_ok)
        - fast_ime_switch_ok: True if already on fast IME or switch to it succeeded.
        - fast_ime_broadcast_ok: True if broadcast exited 0 (False if switch failed before send).
    """
    global _ime_session_original
    fast_ime_id = (fast_ime_id or "").strip()
    if not fast_ime_id:
        return False, "", False, False

    cur = get_current_ime(serial)
    cur_n = cur.strip()
    on_fast = cur_n == fast_ime_id

    switched_to_fast = False
    switch_ok: bool
    if on_fast:
        switch_ok = True
    else:
        if _ime_session_original is None and cur_n:
            _ime_session_original = cur_n
        switch_ok = set_ime(serial, fast_ime_id)
        if not switch_ok:
            return False, "", False, False
        time.sleep(0.05)
        switched_to_fast = True

    code, _ = fast_input_text(serial, text)
    broadcast_ok = code == 0

    if switched_to_fast and _ime_session_original:
        if not set_ime(serial, _ime_session_original):
            log("warning", "ime_restore_failed", target=_ime_session_original)

    if not broadcast_ok:
        return False, "", switch_ok, False
    return True, "fast_ime", switch_ok, True


def connect_device(serial: str | None = None) -> u2.Device:
    """Connect to one Android device. serial=None uses default USB/emulator device."""
    d = u2.connect(serial) if serial else u2.connect()
    info = d.info
    log("info", "device_connected", serial=serial, device_info=info)
    return d


def shell(d: u2.Device, command: str) -> tuple[int, str, str]:
    """Run shell command on device; returns (exit_code, stdout, stderr)."""
    r = d.shell(command)
    if isinstance(r, str):
        return 0, r, ""
    out = getattr(r, "output", str(r))
    code = int(getattr(r, "exit_code", 0))
    return code, out, ""


def force_stop(d: u2.Device, package: str) -> None:
    """Force-stop app (clean slate before open)."""
    code, out, _ = shell(d, f"am force-stop {package}")
    log("info", "force_stop", package=package, exit_code=code, output_tail=out[-200:] if out else "")


def app_start(d: u2.Device, package: str, wait: bool = True) -> None:
    """Start package main activity."""
    d.app_start(package, stop=False)
    log("info", "app_start", package=package)
    if wait:
        time.sleep(0.3)


def press_home(d: u2.Device) -> None:
    d.press("home")
    log("info", "press_home")


def screenshot(d: u2.Device, path: str) -> None:
    d.screenshot(path)
    log("info", "screenshot_saved", path=path)


def health_check(d: u2.Device) -> bool:
    """Quick responsiveness check."""
    try:
        code, out, _ = shell(d, "echo ok")
        ok = code == 0 and "ok" in (out or "").strip().lower()
        log("info", "health_check", ok=ok)
        return ok
    except Exception as e:
        log("error", "health_check_failed", error=str(e))
        return False


def retry_until(
    fn: Callable[[], T | None],
    *,
    timeout_s: float,
    poll_s: float,
    desc: str,
) -> T | None:
    """Poll fn until it returns truthy or timeout. Fast exit on success."""
    deadline = time.perf_counter() + timeout_s
    last_err: str | None = None
    while time.perf_counter() < deadline:
        try:
            r = fn()
            if r:
                return r
        except Exception as e:
            last_err = str(e)
        time.sleep(poll_s)
    log("warning", "retry_until_timeout", desc=desc, last_error=last_err)
    return None


def retry_until_jitter(
    fn: Callable[[], T | None],
    *,
    timeout_s: float,
    poll_min_s: float,
    poll_max_s: float,
    desc: str,
) -> T | None:
    """Poll with random interval in [poll_min_s, poll_max_s] for ATX-style load spreading."""
    deadline = time.perf_counter() + timeout_s
    last_err: str | None = None
    lo, hi = min(poll_min_s, poll_max_s), max(poll_min_s, poll_max_s)
    while time.perf_counter() < deadline:
        try:
            r = fn()
            if r:
                return r
        except Exception as e:
            last_err = str(e)
        time.sleep(random.uniform(lo, hi))
    log("warning", "retry_until_jitter_timeout", desc=desc, last_error=last_err)
    return None


def _lock_state_read() -> dict[str, Any]:
    try:
        if LOCK_STATE_PATH.is_file():
            raw = json.loads(LOCK_STATE_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return raw
    except Exception as e:
        log("warning", "instagram_lock_state_read_failed", error=str(e))
    return {}


def _lock_state_write(data: dict[str, Any]) -> None:
    try:
        LOCK_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        LOCK_STATE_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except Exception as e:
        log("warning", "instagram_lock_state_write_failed", error=str(e))


def _parse_package_versions(dumpsys_out: str) -> tuple[str | None, int | None]:
    """First versionName / versionCode pair seen in dumpsys package output."""
    names = _VERSION_NAME_RE.findall(dumpsys_out or "")
    codes = _VERSION_CODE_RE.findall(dumpsys_out or "")
    vn = names[0] if names else None
    vc: int | None = None
    if codes:
        try:
            vc = int(codes[0])
        except ValueError:
            vc = None
    return vn, vc


def _fetch_package_version(serial: str | None, pkg: str) -> tuple[str | None, int | None]:
    code, out, _ = _adb_shell(serial, "dumpsys", "package", pkg)
    if code != 0 or not (out or "").strip():
        return None, None
    return _parse_package_versions(out)


def _package_disabled_for_user0(serial: str | None, pkg: str) -> bool | None:
    """None if unknown; True if package appears in disabled list."""
    try:
        code, out, _ = _adb_shell(serial, "pm", "list", "packages", "-d")
        if code != 0:
            return None
        for line in (out or "").splitlines():
            line = line.strip()
            if line == f"package:{pkg}":
                return True
        return False
    except Exception:
        return None


def _package_suspended(serial: str | None, pkg: str) -> bool | None:
    """Parse dumpsys package … for suspended=true (OEM-dependent formatting)."""
    try:
        code, out, _ = _adb_shell(serial, "dumpsys", "package", pkg)
        if code != 0 or not out:
            return None
        lowered = out.lower()
        if "suspended=true" in lowered:
            return True
        if "suspended=false" in lowered:
            return False
        return None
    except Exception:
        return None


def _lock_run_step(
    serial: str | None,
    argv: list[str],
    *,
    step: str,
    log_manual_block: bool = False,
) -> bool:
    try:
        code, out, err = _adb_shell(serial, *argv)
        ok = code == 0
        tail = ((out or "") + " " + (err or "")).strip()[-240:]
        log(
            "info",
            "instagram_auto_update_lock_step",
            step=step,
            ok=ok,
            exit_code=code,
            output_tail=tail,
        )
        if log_manual_block and ok:
            log("info", "instagram_manual_update_blocked", step=step, mechanism="appops_or_settings")
        return ok
    except Exception as e:
        log(
            "info",
            "instagram_auto_update_lock_step",
            step=step,
            ok=False,
            error=str(e),
        )
        return False


def lock_instagram_update_system(
    d: u2.Device,
    pkg: str | None = None,
) -> None:
    """
    Device hardening (best-effort): reduce accidental IG / Play Store updates via ADB.
    Does not suspend Instagram unless SUSPEND_INSTAGRAM_APP_FOR_LOCK is True (suspend can block app start).
    Never raises; never fails the main runner — logs only.
    """
    pkg = (pkg or getattr(config, "INSTAGRAM_PACKAGE", "") or "com.instagram.android").strip()
    serial = get_device_serial(d)
    log(
        "info",
        "instagram_auto_update_lock_started",
        package=pkg,
        disable_play_store=bool(getattr(config, "DISABLE_PLAY_STORE_FOR_PHONE_FARM", True)),
        suspend_instagram_app=bool(getattr(config, "SUSPEND_INSTAGRAM_APP_FOR_LOCK", False)),
    )
    try:
        steps_ok = 0
        steps_total = 0

        def _one(argv: list[str], step: str, *, manual_block: bool = False) -> None:
            nonlocal steps_ok, steps_total
            steps_total += 1
            if _lock_run_step(serial, argv, step=step, log_manual_block=manual_block):
                steps_ok += 1

        _one(
            ["cmd", "package", "set-distracting-restriction", pkg, "hide-notifications"],
            "set_distracting_restriction_hide_notifications",
        )
        if bool(getattr(config, "DISABLE_PLAY_STORE_FOR_PHONE_FARM", True)):
            _one(
                ["pm", "disable-user", "--user", "0", _PLAY_STORE_PKG],
                "disable_play_store_user0",
                manual_block=True,
            )
        if bool(getattr(config, "SUSPEND_INSTAGRAM_APP_FOR_LOCK", False)):
            _one(["pm", "suspend", pkg], "pm_suspend_instagram")
        _one(
            ["appops", "set", pkg, "REQUEST_INSTALL_PACKAGES", "ignore"],
            "appops_ignore_request_install",
            manual_block=True,
        )
        _one(
            ["settings", "put", "global", "auto_update_apps", "0"],
            "settings_auto_update_apps_off",
            manual_block=True,
        )

        # Record baseline version + lock metadata for strict mode.
        try:
            vn, vc = _fetch_package_version(serial, pkg)
            prev = _lock_state_read()
            merged: dict[str, Any] = {
                **prev,
                "authorized_version_name": vn or prev.get("authorized_version_name"),
                "authorized_version_code": vc
                if vc is not None
                else prev.get("authorized_version_code"),
                "locked_at_iso": datetime.now(timezone.utc).isoformat(),
                "device_serial": serial or prev.get("device_serial"),
                "lock_applied": True,
                "package": pkg,
            }
            _lock_state_write(merged)
        except Exception as e:
            log("warning", "instagram_lock_state_update_after_lock_failed", error=str(e))

        log(
            "info",
            "instagram_auto_update_lock_done",
            package=pkg,
            steps_ok=steps_ok,
            steps_total=steps_total,
        )
    except Exception as e:
        log("info", "instagram_auto_update_lock_failed", error=str(e), package=pkg)


def unlock_instagram_updates(d: u2.Device, unlock_code: str) -> bool:
    """
    Re-enable Play Store and unsuspend Instagram when unlock_code matches config.
    Best-effort ADB; logs security outcomes.
    """
    expected = str(getattr(config, "UPDATE_UNLOCK_CODE", "") or "")
    log("info", "instagram_update_unlock_attempt")
    if not expected or not hmac.compare_digest(
        str(unlock_code or ""),
        expected,
    ):
        log("warning", "instagram_update_unlock_denied", reason="invalid_or_missing_code")
        return False
    serial = get_device_serial(d)
    try:
        _lock_run_step(
            serial,
            ["pm", "enable", _PLAY_STORE_PKG],
            step="enable_play_store",
        )
        pkg = str(getattr(config, "INSTAGRAM_PACKAGE", "") or "com.instagram.android")
        _lock_run_step(
            serial,
            ["pm", "unsuspend", pkg],
            step="pm_unsuspend_instagram",
        )
        log("info", "instagram_update_unlock_success", package=pkg)
        try:
            prev = _lock_state_read()
            prev["lock_applied"] = False
            prev["unlocked_at_iso"] = datetime.now(timezone.utc).isoformat()
            prev["device_serial"] = serial or prev.get("device_serial")
            _lock_state_write(prev)
        except Exception:
            pass
        return True
    except Exception as e:
        log("error", "instagram_update_unlock_failed", error=str(e))
        return False


def check_instagram_version_lock(d: u2.Device) -> dict[str, Any]:
    """
    Inspect IG version + lock-related signals; update strict unsafe_for_automation
    when installed build diverges from lock_state.json baseline.
    """
    pkg = str(getattr(config, "INSTAGRAM_PACKAGE", "") or "com.instagram.android")
    serial = get_device_serial(d)
    strict = bool(getattr(config, "ENABLE_STRICT_UPDATE_LOCK", False))

    vn, vc = _fetch_package_version(serial, pkg)
    log(
        "info",
        "instagram_version_detected",
        package=pkg,
        versionName=vn,
        versionCode=vc,
    )

    play_off = _package_disabled_for_user0(serial, _PLAY_STORE_PKG)
    ig_sus = _package_suspended(serial, pkg)

    state = _lock_state_read()
    auth_name = state.get("authorized_version_name")
    auth_code = state.get("authorized_version_code")
    baseline_missing = auth_name is None and auth_code is None

    if baseline_missing and (vn is not None or vc is not None):
        try:
            state = {
                **state,
                "authorized_version_name": vn,
                "authorized_version_code": vc,
                "baseline_set_at_iso": datetime.now(timezone.utc).isoformat(),
                "device_serial": serial or state.get("device_serial"),
                "package": pkg,
            }
            _lock_state_write(state)
            auth_name, auth_code = vn, vc
        except Exception as e:
            log("warning", "instagram_lock_baseline_init_failed", error=str(e))

    automation_unsafe_reason: str | None = None
    unsafe = False
    if ig_sus is True:
        unsafe = True
        automation_unsafe_reason = "instagram_app_suspended"

    version_mismatch = False
    if strict and not baseline_missing:
        if auth_code is not None and vc is not None:
            if int(auth_code) != int(vc):
                version_mismatch = True
        elif auth_name and vn:
            if str(auth_name).strip() != str(vn).strip():
                version_mismatch = True
    if version_mismatch:
        unsafe = True
        if automation_unsafe_reason is None:
            automation_unsafe_reason = "instagram_version_mismatch"

    payload: dict[str, Any] = {
        "versionName": vn,
        "versionCode": vc,
        "play_store_disabled": play_off,
        "instagram_suspended": ig_sus,
        "strict_lock_enabled": strict,
        "unsafe_for_automation": unsafe,
        "automation_unsafe_reason": automation_unsafe_reason,
        "authorized_version_name": state.get("authorized_version_name"),
        "authorized_version_code": state.get("authorized_version_code"),
        "lock_state_path": str(LOCK_STATE_PATH),
    }
    log("info", "instagram_lock_state_detected", **payload)
    if unsafe and strict:
        if automation_unsafe_reason == "instagram_app_suspended":
            log(
                "critical",
                "instagram_app_suspended",
                reason="instagram_app_suspended",
                **payload,
            )
        else:
            log(
                "critical",
                "instagram_strict_lock_version_mismatch",
                **payload,
            )
    return payload
