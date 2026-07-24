"""Structured JSON-style logs to stdout and optional per-run file under runs/."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

_RUN_LOG_FILE: TextIO | None = None
_RUN_LOG_PATH: str | None = None


def _runs_dir() -> Path:
    return Path(__file__).resolve().parent / "runs"


def get_run_log_file_path() -> str | None:
    """Absolute path of the current run log file, if file logging is active."""
    return _RUN_LOG_PATH


def close_run_file_logging() -> None:
    global _RUN_LOG_FILE, _RUN_LOG_PATH
    if _RUN_LOG_FILE is not None:
        try:
            _RUN_LOG_FILE.flush()
            _RUN_LOG_FILE.close()
        except Exception:
            pass
        _RUN_LOG_FILE = None


def init_run_file_logging(run_id: str) -> str | None:
    """
    Open runs/run_<timestamp>_<run_id>.log for append of JSON lines for this process.
    Safe run_id for filenames; returns absolute path or None on failure.
    """
    global _RUN_LOG_FILE, _RUN_LOG_PATH
    close_run_file_logging()
    _RUN_LOG_PATH = None
    try:
        rd = _runs_dir()
        rd.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
        raw = (run_id or "").strip() or "norunid"
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in raw)[:96]
        path = rd / f"run_{ts}_{safe}.log"
        _RUN_LOG_FILE = path.open("w", encoding="utf-8")
        _RUN_LOG_PATH = str(path.resolve())
        return _RUN_LOG_PATH
    except OSError:
        _RUN_LOG_FILE = None
        _RUN_LOG_PATH = None
        return None


def log(level: str, event: str, **fields: Any) -> None:
    """Emit one JSON object per line (level, event, optional fields) to stdout and run file."""
    include_null = bool(fields.pop("_include_null_fields", False))
    payload: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "level": level,
        "event": event,
    }
    for k, v in fields.items():
        if include_null or v is not None:
            payload[k] = v
    line = json.dumps(payload, default=str) + "\n"
    sys.stdout.write(line)
    sys.stdout.flush()
    if _RUN_LOG_FILE is not None:
        try:
            _RUN_LOG_FILE.write(line)
            _RUN_LOG_FILE.flush()
        except Exception:
            pass
