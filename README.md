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
