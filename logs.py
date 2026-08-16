"""Structured JSON-style logs to stdout and optional per-run file under runs/."""

from __future__ import annotations

import json
import errno
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

import storage_health

_RUN_LOG_FILE: TextIO | None = None
_RUN_LOG_PATH: str | None = None
_STDOUT_DEGRADED = False
_FILE_DEGRADED = False
_DEGRADED_DIAGNOSTIC_EMITTED = False

_OUTPUT_ERRNOS = frozenset(
    value
    for value in (
        errno.ENOSPC,
        getattr(errno, "EDQUOT", None),
        errno.EIO,
        errno.EROFS,
        errno.EPIPE,
    )
    if value is not None
)


def _runs_dir() -> Path:
    return Path(__file__).resolve().parent / "runs"


def get_run_log_file_path() -> str | None:
    """Absolute path of the current run log file, if file logging is active."""
    return _RUN_LOG_PATH


def close_run_file_logging() -> None:
    global _RUN_LOG_FILE, _RUN_LOG_PATH, _FILE_DEGRADED
    if _RUN_LOG_FILE is not None:
        try:
            _RUN_LOG_FILE.flush()
            _RUN_LOG_FILE.close()
        except OSError as exc:
            _classify_output_error(exc, channel="run_log_close")
        _RUN_LOG_FILE = None
    _FILE_DEGRADED = False


def _retention_int(name: str, default: int) -> int:
    try:
        return max(0, int(str(os.environ.get(name, default)).strip()))
    except (TypeError, ValueError):
        return int(default)


def _apply_run_log_retention(rd: Path) -> None:
    """Bound run-log growth once per run; never scans from the hot log path."""

    max_files = _retention_int("PHONEFARM_RUN_LOG_MAX_FILES", 500)
    max_total_bytes = _retention_int("PHONEFARM_RUN_LOG_MAX_TOTAL_BYTES", 2 * 1024**3)
    max_age_seconds = _retention_int("PHONEFARM_RUN_LOG_MAX_AGE_SECONDS", 30 * 86400)
    now = time.time()
    rows: list[tuple[float, int, Path]] = []
    for path in rd.glob("run_*.log"):
        try:
            stat = path.stat()
        except OSError:
            continue
        rows.append((float(stat.st_mtime), int(stat.st_size), path))
    rows.sort(key=lambda item: (item[0], item[2].name))
    total = sum(item[1] for item in rows)
    count = len(rows)
    for mtime, size, path in rows:
        expired = bool(max_age_seconds and now - mtime > max_age_seconds)
        over_count = bool(max_files and count > max_files)
        over_bytes = bool(max_total_bytes and total > max_total_bytes)
        if not (expired or over_count or over_bytes):
            continue
        try:
            path.unlink()
        except OSError:
            continue
        count -= 1
        total -= size


def init_run_file_logging(run_id: str) -> str | None:
    """
    Open runs/run_<timestamp>_<run_id>.log for append of JSON lines for this process.
    Safe run_id for filenames; returns absolute path or None on failure.
    """
    global _RUN_LOG_FILE, _RUN_LOG_PATH, _FILE_DEGRADED
    close_run_file_logging()
    _RUN_LOG_PATH = None
    _FILE_DEGRADED = False
    try:
        rd = _runs_dir()
        rd.mkdir(parents=True, exist_ok=True)
        _apply_run_log_retention(rd)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
        raw = (run_id or "").strip() or "norunid"
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in raw)[:96]
        path = rd / f"run_{ts}_{safe}.log"
        _RUN_LOG_FILE = path.open("w", encoding="utf-8")
        _RUN_LOG_PATH = str(path.resolve())
        return _RUN_LOG_PATH
    except OSError as exc:
        _classify_output_error(exc, channel="run_log_init")
        _RUN_LOG_FILE = None
        _RUN_LOG_PATH = None
        return None


def _emit_degraded_diagnostic_once(exc: OSError, *, channel: str) -> None:
    global _DEGRADED_DIAGNOSTIC_EMITTED
    if _DEGRADED_DIAGNOSTIC_EMITTED:
        return
    _DEGRADED_DIAGNOSTIC_EMITTED = True
    payload = (
        f'{{"level":"critical","event":"logging_io_degraded",'
        f'"channel":"{channel}","errno":{int(getattr(exc, "errno", 0) or 0)}}}\n'
    ).encode("ascii", errors="replace")
    try:
        os.write(2, payload[:512])
    except OSError:
        return


def _classify_output_error(exc: OSError, *, channel: str) -> None:
    if getattr(exc, "errno", None) in _OUTPUT_ERRNOS:
        storage_health.mark_storage_pressure(exc, source=f"logger_{channel}")
    _emit_degraded_diagnostic_once(exc, channel=channel)


def _write_payload(payload: dict[str, Any]) -> None:
    global _STDOUT_DEGRADED, _FILE_DEGRADED
    line = json.dumps(payload, default=str) + "\n"
    if not _STDOUT_DEGRADED:
        try:
            sys.stdout.write(line)
            sys.stdout.flush()
        except OSError as exc:
            _classify_output_error(exc, channel="stdout")
            _STDOUT_DEGRADED = True
    if _RUN_LOG_FILE is not None and not _FILE_DEGRADED:
        try:
            _RUN_LOG_FILE.write(line)
            _RUN_LOG_FILE.flush()
        except OSError as exc:
            _classify_output_error(exc, channel="run_file")
            _FILE_DEGRADED = True


def logging_health() -> dict[str, Any]:
    return {
        "stdout_degraded": _STDOUT_DEGRADED,
        "run_file_degraded": _FILE_DEGRADED,
        "storage_pressure": storage_health.storage_pressure_state(),
        "buffered_lines": 0,
    }


def _reset_logging_state_for_tests() -> None:
    global _STDOUT_DEGRADED, _FILE_DEGRADED, _DEGRADED_DIAGNOSTIC_EMITTED
    _STDOUT_DEGRADED = False
    _FILE_DEGRADED = False
    _DEGRADED_DIAGNOSTIC_EMITTED = False
    storage_health._reset_for_tests()


def log(level: str, event: str, **fields: Any) -> None:
    """Emit one JSON object per line and feed the CPU-only Ordering V2 observer."""
    include_null = bool(fields.pop("_include_null_fields", False))
    payload: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "level": level,
        "event": event,
    }
    observed_fields: dict[str, Any] = {}
    for k, v in fields.items():
        if include_null or v is not None:
            payload[k] = v
            observed_fields[k] = v
    _write_payload(payload)

    # The observer only reads fields already emitted by V1.  It is isolated so
    # import, enrichment, or serialization failures can never affect V1 logs or
    # execution.  Terminal events bypass ``log`` to prevent recursion.
    try:
        from follow60_ordering_v2_shadow import (
            PUBLIC_EVENT,
            observe_runtime_event,
        )

        terminal_events = observe_runtime_event(event, observed_fields)
        for terminal in terminal_events:
            if not isinstance(terminal, dict):
                continue
            _write_payload(
                {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "level": "info",
                    "event": PUBLIC_EVENT,
                    **terminal,
                }
            )
    except Exception:
        pass
