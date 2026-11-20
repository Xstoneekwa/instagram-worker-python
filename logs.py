"""Structured JSON-style logs to stdout and optional per-run file under runs/."""

from __future__ import annotations

import json
import errno
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

import storage_health
from runtime_artifacts import runtime_logs_root

_RUN_LOG_FILE: TextIO | None = None
_RUN_LOG_PATH: str | None = None
_STDOUT_DEGRADED = False
_FILE_DEGRADED = False
_DEGRADED_DIAGNOSTIC_EMITTED = False
_FOLLOW_TIMING = threading.local()
_FOLLOW_TIMING_LIMIT = 64
_OUTPUT_LOCK = threading.RLock()

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
    return runtime_logs_root() / "runs"


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
    _FOLLOW_TIMING.carrier = None
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


def _write_payload(payload: dict[str, Any], *, observations=()) -> None:
    global _STDOUT_DEGRADED, _FILE_DEGRADED
    lines = [json.dumps(payload, default=str) + "\n"]
    for observation in observations:
        try:
            lines.append(json.dumps(observation, default=str) + "\n")
        except Exception:
            # A malformed optional row must never suppress its primary record.
            continue
    line = "".join(lines)
    with _OUTPUT_LOCK:
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
    _FOLLOW_TIMING.carrier = None
    storage_health._reset_for_tests()


_TIMING_KEYS = ("run_id", "request_id", "account_id")
_DEFERRED_TIMING_EVENTS = frozenset({
    "run_latency_operation_v1", "run_latency_wait_v1", "run_latency_rpc_timing_v1",
    "follow_latency_ct_confirmed", "follow_latency_run_ending",
})


def _timing_binding(fields, context):
    explicit = {key: fields[key] for key in _TIMING_KEYS if fields.get(key)}
    if any(context.get(key) and value != context[key] for key, value in explicit.items()):
        return explicit
    return {**{key: context[key] for key in _TIMING_KEYS if context.get(key)}, **explicit}


def _same_timing_run(left, right):
    return (
        any(left.get(key) and left[key] == right.get(key) for key in ("run_id", "request_id"))
        and all(left[key] == right[key] for key in left.keys() & right.keys())
    )


def _take_follow_timing(binding):
    slot = _FOLLOW_TIMING.__dict__
    saved = slot.pop("carrier", None)
    if saved is None:
        return []
    owner, rows, omitted = saved
    if not _same_timing_run(owner, binding):
        slot.setdefault("carrier", saved)
        return []
    if omitted:
        rows = [*rows, {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": "warning",
            "event": "run_latency_observation_v1",
            "metric": "unavailable_timing_samples",
            "reason": "carrier_limit",
            "sample_count": omitted,
            **owner,
        }]
    return rows


def _observer_rows(observer, event_name, event, fields):
    try:
        return [{"ts": datetime.now(timezone.utc).isoformat(), "level": "info",
                 "event": event_name, **row}
                for row in observer(event, fields) if isinstance(row, dict)]
    except Exception:
        return []


def log(level: str, event: str, **fields: Any) -> None:
    """Emit JSONL, sharing an existing write with correlated timing observations."""
    include_null = bool(fields.pop("_include_null_fields", False))
    defer_timing = bool(fields.pop("_defer_follow_timing", False)) and event in _DEFERRED_TIMING_EVENTS
    is_timing = str(event or "").startswith("run_latency_")
    context = {}
    if defer_timing or is_timing or getattr(_FOLLOW_TIMING, "carrier", None):
        try:
            from run_latency_observability_v1 import current_context

            context = current_context()
            if is_timing and all(not (fields.get(key) and context.get(key))
                                 or fields[key] == context[key] for key in _TIMING_KEYS):
                for key, value in context.items():
                    fields.setdefault(key, value)
        except Exception:
            pass

    observed = {key: value for key, value in fields.items() if include_null or value is not None}
    payload = {"ts": datetime.now(timezone.utc).isoformat(), "level": level, "event": event, **observed}
    binding = _timing_binding(observed, context)
    defer_timing = defer_timing and bool(binding.get("run_id") or binding.get("request_id"))

    shadow_rows = []
    latency_rows = []
    try:
        if not defer_timing:
            try:
                from follow60_ordering_v2_shadow import PUBLIC_EVENT, observe_runtime_event

                shadow_rows = _observer_rows(observe_runtime_event, PUBLIC_EVENT, event, observed)
            except Exception:
                pass
        try:
            from run_latency_observability_v1 import PUBLIC_EVENT as LATENCY_EVENT
            from run_latency_observability_v1 import observe_runtime_event as observe_latency

            latency_rows = _observer_rows(observe_latency, LATENCY_EVENT, event, observed)
        except Exception:
            pass
    finally:
        if not defer_timing:
            observations = _take_follow_timing(binding) + latency_rows
            # Preserve the primary and Ordering shadow boundaries and order.
            if shadow_rows:
                _write_payload(payload)
                try:
                    for row in shadow_rows[:-1]:
                        _write_payload(row)
                    _write_payload(shadow_rows[-1], observations=observations)
                except Exception:
                    pass
            elif observations:
                _write_payload(payload, observations=observations)
            else:
                _write_payload(payload)

    if defer_timing:
        saved = getattr(_FOLLOW_TIMING, "carrier", None)
        owner, rows, omitted = saved if saved and _same_timing_run(saved[0], binding) else (binding, [], 0)
        # Only supplemental timings ride a later event. Business receipts
        # and security events write synchronously at their own callsites.
        additions = [payload, *latency_rows]
        space = _FOLLOW_TIMING_LIMIT - len(rows)
        _FOLLOW_TIMING.carrier = ({**owner, **binding}, rows + additions[:space],
                                  omitted + max(0, len(additions) - space))
