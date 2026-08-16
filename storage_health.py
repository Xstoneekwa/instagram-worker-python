"""Cheap host-storage health and fail-closed business-action gates.

The hot path uses ``shutil.disk_usage`` only.  Directory scans, cleanup and
network calls are deliberately excluded so the guard stays deterministic and
safe when the filesystem is already under pressure.
"""

from __future__ import annotations

import errno
import os
from dataclasses import dataclass
from pathlib import Path
import shutil
import threading
import time
from typing import Any, Iterable


HEALTHY = "healthy"
WARNING = "warning"
CRITICAL = "critical"
HOST_STORAGE_CRITICAL = "host_storage_critical"

_STORAGE_ERRNOS = frozenset(
    value
    for value in (
        errno.ENOSPC,
        getattr(errno, "EDQUOT", None),
        errno.EIO,
        errno.EROFS,
    )
    if value is not None
)


def _env_int(name: str, default: int) -> int:
    try:
        return max(0, int(str(os.environ.get(name, default)).strip()))
    except (TypeError, ValueError):
        return int(default)


def _env_float(name: str, default: float) -> float:
    try:
        return max(0.0, float(str(os.environ.get(name, default)).strip()))
    except (TypeError, ValueError):
        return float(default)


@dataclass(frozen=True)
class StorageSnapshot:
    status: str
    filesystem_path: str
    total_bytes: int
    free_bytes: int
    free_percent: float
    warning_free_bytes: int
    critical_free_bytes: int
    warning_free_percent: float
    critical_free_percent: float
    checked_at_monotonic: float
    reason: str = ""

    @property
    def new_run_allowed(self) -> bool:
        return self.status != CRITICAL

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason or None,
            "filesystem_path": self.filesystem_path,
            "total_bytes": self.total_bytes,
            "free_bytes": self.free_bytes,
            "free_percent": round(self.free_percent, 4),
            "warning_free_bytes": self.warning_free_bytes,
            "critical_free_bytes": self.critical_free_bytes,
            "warning_free_percent": self.warning_free_percent,
            "critical_free_percent": self.critical_free_percent,
            "new_run_allowed": self.new_run_allowed,
        }


class HostStorageCriticalError(RuntimeError):
    def __init__(self, boundary: str, snapshot: StorageSnapshot):
        super().__init__(HOST_STORAGE_CRITICAL)
        self.boundary = str(boundary or "unknown")
        self.snapshot = snapshot


_LOCK = threading.Lock()
_PRESSURE_ERRNO: int | None = None
_PRESSURE_SOURCE = ""
_PRESSURE_AT = 0.0
_LAST_SNAPSHOT: StorageSnapshot | None = None
_CACHE_TTL_SECONDS = 0.25


def is_storage_io_error(exc: BaseException) -> bool:
    if isinstance(exc, OSError) and getattr(exc, "errno", None) in _STORAGE_ERRNOS:
        return True
    message = str(exc or "").strip().lower()
    return any(
        marker in message
        for marker in (
            "disk i/o error",
            "database or disk is full",
            "no space left on device",
            "disk quota exceeded",
            "read-only file system",
        )
    )


def mark_storage_pressure(exc: BaseException, *, source: str) -> bool:
    """Record the first storage fault; return True only for that transition."""

    if not is_storage_io_error(exc):
        return False
    global _PRESSURE_ERRNO, _PRESSURE_SOURCE, _PRESSURE_AT
    with _LOCK:
        first = _PRESSURE_ERRNO is None
        if first:
            _PRESSURE_ERRNO = int(getattr(exc, "errno", 0) or errno.EIO)
            _PRESSURE_SOURCE = str(source or "unknown")[:120]
            _PRESSURE_AT = time.monotonic()
        return first


def storage_pressure_state() -> dict[str, Any]:
    with _LOCK:
        return {
            "active": _PRESSURE_ERRNO is not None,
            "errno": _PRESSURE_ERRNO,
            "source": _PRESSURE_SOURCE or None,
            "detected_at_monotonic": _PRESSURE_AT or None,
        }


def _configured_probe_path(paths: Iterable[str | os.PathLike[str]] | None = None) -> Path:
    configured = str(os.environ.get("PHONEFARM_STORAGE_PROBE_PATH") or "").strip()
    candidates = [configured] if configured else []
    candidates.extend(str(item) for item in (paths or ()) if str(item))
    candidates.extend(
        (
            str(os.environ.get("PHONEFARM_RUNTIME_DIR") or ""),
            str(Path(__file__).resolve().parent / "runs"),
            str(Path(__file__).resolve().parent),
        )
    )
    for raw in candidates:
        if not raw:
            continue
        path = Path(raw).expanduser()
        while not path.exists() and path != path.parent:
            path = path.parent
        if path.exists():
            return path.resolve()
    anchor = Path(__file__).resolve().anchor
    return Path(anchor or "/")


def inspect_storage_health(
    *,
    paths: Iterable[str | os.PathLike[str]] | None = None,
    force: bool = False,
) -> StorageSnapshot:
    global _LAST_SNAPSHOT
    now = time.monotonic()
    with _LOCK:
        cached = _LAST_SNAPSHOT
    if cached is not None and not force and now - cached.checked_at_monotonic <= _CACHE_TTL_SECONDS:
        return cached

    probe = _configured_probe_path(paths)
    warning_bytes = _env_int("PHONEFARM_STORAGE_WARNING_FREE_BYTES", 10 * 1024**3)
    critical_bytes = _env_int("PHONEFARM_STORAGE_CRITICAL_FREE_BYTES", 3 * 1024**3)
    warning_percent = _env_float("PHONEFARM_STORAGE_WARNING_FREE_PERCENT", 5.0)
    critical_percent = _env_float("PHONEFARM_STORAGE_CRITICAL_FREE_PERCENT", 2.0)
    try:
        usage = shutil.disk_usage(probe)
        total = int(usage.total)
        free = int(usage.free)
        free_percent = (free * 100.0 / total) if total > 0 else 0.0
        pressure = storage_pressure_state()
        if pressure["active"] or free <= critical_bytes or free_percent <= critical_percent:
            status = CRITICAL
            reason = HOST_STORAGE_CRITICAL
        elif free <= warning_bytes or free_percent <= warning_percent:
            status = WARNING
            reason = "host_storage_warning"
        else:
            status = HEALTHY
            reason = ""
        snapshot = StorageSnapshot(
            status=status,
            filesystem_path=str(probe),
            total_bytes=total,
            free_bytes=free,
            free_percent=free_percent,
            warning_free_bytes=warning_bytes,
            critical_free_bytes=critical_bytes,
            warning_free_percent=warning_percent,
            critical_free_percent=critical_percent,
            checked_at_monotonic=now,
            reason=reason,
        )
    except OSError as exc:
        mark_storage_pressure(exc, source="disk_usage")
        snapshot = StorageSnapshot(
            status=CRITICAL,
            filesystem_path=str(probe),
            total_bytes=0,
            free_bytes=0,
            free_percent=0.0,
            warning_free_bytes=warning_bytes,
            critical_free_bytes=critical_bytes,
            warning_free_percent=warning_percent,
            critical_free_percent=critical_percent,
            checked_at_monotonic=now,
            reason=HOST_STORAGE_CRITICAL,
        )
    with _LOCK:
        _LAST_SNAPSHOT = snapshot
    return snapshot


def require_new_run_allowed(*, boundary: str = "pre_run") -> StorageSnapshot:
    snapshot = inspect_storage_health(force=True)
    if not snapshot.new_run_allowed:
        raise HostStorageCriticalError(boundary, snapshot)
    return snapshot


def require_irreversible_action_allowed(*, boundary: str) -> StorageSnapshot:
    snapshot = inspect_storage_health(force=True)
    if snapshot.status == CRITICAL:
        raise HostStorageCriticalError(boundary, snapshot)
    return snapshot


def reset_storage_pressure_after_health_restore() -> bool:
    """Clear the latch only after an uncached non-critical stat succeeds."""

    global _PRESSURE_ERRNO, _PRESSURE_SOURCE, _PRESSURE_AT, _LAST_SNAPSHOT
    with _LOCK:
        prior = (_PRESSURE_ERRNO, _PRESSURE_SOURCE, _PRESSURE_AT)
        _PRESSURE_ERRNO = None
        _PRESSURE_SOURCE = ""
        _PRESSURE_AT = 0.0
        _LAST_SNAPSHOT = None
    snapshot = inspect_storage_health(force=True)
    if snapshot.status != CRITICAL:
        return prior[0] is not None
    with _LOCK:
        _PRESSURE_ERRNO, _PRESSURE_SOURCE, _PRESSURE_AT = prior
        _LAST_SNAPSHOT = snapshot
    return False


def _reset_for_tests() -> None:
    global _PRESSURE_ERRNO, _PRESSURE_SOURCE, _PRESSURE_AT, _LAST_SNAPSHOT
    with _LOCK:
        _PRESSURE_ERRNO = None
        _PRESSURE_SOURCE = ""
        _PRESSURE_AT = 0.0
        _LAST_SNAPSHOT = None
