"""Device connection and low-level helpers (ADB / uiautomator2)."""

from __future__ import annotations

import random
import subprocess
import time
from typing import Callable, TypeVar

import uiautomator2 as u2

import config
from logs import log

T = TypeVar("T")

# Once per worker process: original IME before first FastInput switch (restore target).
_ime_session_original: str | None = None
# True after global animation scales are set to 0 for this process.
_android_animations_disabled_session: bool = False


def get_device_serial(d: u2.Device) -> str | None:
    """ADB serial for subprocess adb calls; falls back to config.DEVICE_SERIAL."""
    s = getattr(d, "serial", None)
    if s:
        return str(s)
    return config.DEVICE_SERIAL


def _adb_run(serial: str | None, argv: list[str], *, timeout_s: float = 45.0) -> tuple[int, str, str]:
    cmd = ["adb"]
    if serial:
        cmd.extend(["-s", serial])
    cmd.extend(argv)
    try:
        p = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        out = (p.stdout or "").strip()
        err = (p.stderr or "").strip()
        return int(p.returncode), out, err
    except Exception as e:
        log("warning", "adb_run_failed", argv=argv[:8], error=str(e))
        return 1, "", str(e)


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
    Send text via FastInputIME broadcast (no shell quoting of msg — argv list).
    """
    code, out, err = _adb_run(
        serial,
        [
            "shell",
            "am",
            "broadcast",
            "-a",
            "ADB_INPUT_TEXT",
            "--es",
            "msg",
            text,
        ],
    )
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
