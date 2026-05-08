# instagram-worker-python (experimental)

Minimal proof-of-concept worker using [uiautomator2](https://github.com/openatx/uiautomator2) (OpenATX) for **safe** Instagram navigation only: connect → open app → search → Accounts tab → exact profile row → verify profile. **No DMs, follows, likes, comments, or story taps.**

This project is separate from the Node/Appium `instagram-worker`.

## Prerequisites

- Python 3.10+
- Android device or emulator with **USB debugging** (or TCP/IP adb)
- Instagram installed (`com.instagram.android`)
- Default PoC username to open: `xstonekwa` (change in `config.py`)

## Install

```bash
cd instagram-worker-python
pip install -r requirements.txt
python -m uiautomator2 init
```

`uiautomator2 init` installs the ATX agent on the connected device (required once per device / after agent updates).

## Run

```bash
python runner.py
```

Structured JSON logs are printed to stdout (one object per line).

## Configuration

Edit `config.py`:

- `TARGET_USERNAME` — handle to search (exact match under Accounts)
- `DEVICE_SERIAL` — `None` for default adb device, or set to a serial (e.g. `emulator-5554`)

## Files

| File | Role |
|------|------|
| `config.py` | Constants (package, username, timeouts) |
| `device.py` | Connect, shell, force-stop, start app, screenshot, retries |
| `instagram_navigation.py` | Search, Accounts tab, exact row, profile check |
| `runner.py` | Single safe run + per-phase timings |
| `logs.py` | JSON-style console logging |

## Safety

This PoC only performs navigation steps listed in `runner.py`. Extend with care; avoid automating engagement or bulk actions that violate Instagram’s terms or local law.

## Phone farm: Instagram update lock (optional hardening)

For **dedicated automation devices**, `config.py` can freeze the Instagram build so Play Store or manual installs do not silently change UI selectors (`resource-id`, Follow/Send, followers list, DM flows).

When `LOCK_INSTAGRAM_AUTO_UPDATE` is `True`, the runner calls `lock_instagram_update_system` right after a successful `health_check`. That routine is **best-effort** (try/except per ADB step): it never aborts the run by itself. The **normal** lock targets updates only: hide distracting notifications for Instagram (`cmd package set-distracting-restriction`), disable Play Store for user 0 (`DISABLE_PLAY_STORE_FOR_PHONE_FARM`), set `REQUEST_INSTALL_PACKAGES` to `ignore` for Instagram, and turn off `auto_update_apps` where the setting exists. **Instagram stays runnable** so automation is not blocked by the lock itself.

Optional `pm suspend` on Instagram is **off by default** (`SUSPEND_INSTAGRAM_APP_FOR_LOCK = False`). Suspending Instagram is **not recommended** for active runs: it can prevent the app from starting. Enable it only for exceptional device-hardening scenarios.

A local **`lock_state.json`** (gitignored) stores the **authorized** `versionName` / `versionCode`, lock timestamp, and device serial. `check_instagram_version_lock` parses `dumpsys package com.instagram.android`, logs `instagram_version_detected` and `instagram_lock_state_detected`. If the package is **suspended** or (when `ENABLE_STRICT_UPDATE_LOCK` is `True`) the installed build no longer matches the baseline, the run is marked unsafe; with strict lock enabled, a **critical** log is emitted and the runner exits with code **43** before any DM/follow/followers automation.

If Instagram was suspended earlier (e.g. legacy lock or manual ADB), use the technical unlock flow or `pm unsuspend` so the worker can start the app again.

**Technical unlock** (operator with `UPDATE_UNLOCK_CODE` in `config.py`, or direct ADB maintenance):

```bash
adb shell pm enable com.android.vending
adb shell pm unsuspend com.instagram.android
```

Programmatic unlock from Python (same code as in config):

```python
from device import connect_device, unlock_instagram_updates
import config

d = connect_device(config.DEVICE_SERIAL)
unlock_instagram_updates(d, "YOUR_TECH_CODE")
```

Set `LOCK_INSTAGRAM_AUTO_UPDATE`, `DISABLE_PLAY_STORE_FOR_PHONE_FARM`, and `ENABLE_STRICT_UPDATE_LOCK` to `False` on general-purpose dev phones; keep defaults `True` only on **isolated farm handsets** where Play Store is meant to stay off and IG updates are deliberate, operator-controlled events.
