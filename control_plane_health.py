"""Durable dispatcher-only control-plane circuit breaker.

The file is an operational projection, never business truth.  Startup always
enters RECOVERING and requires two fresh probes separated by the configured
stabilization interval before dispatch may resume.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Callable

HEALTHY = "HEALTHY"
DEGRADED = "DEGRADED"
UNAVAILABLE = "UNAVAILABLE"
RECOVERING = "RECOVERING"
VALID_STATES = {HEALTHY, DEGRADED, UNAVAILABLE, RECOVERING}

DEFAULT_PATH = Path(
    os.environ.get(
        "CONTROL_PLANE_HEALTH_PATH",
        "/Users/admin/phonefarm-runtime/run/control-plane-health.json",
    )
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class Snapshot:
    state: str
    reason_family: str
    consecutive_failures: int
    next_probe_at: float
    changed_at: str
    generation: int

    @property
    def dispatch_allowed(self) -> bool:
        return self.state in {HEALTHY, DEGRADED}


class CircuitBreaker:
    def __init__(
        self,
        *,
        path: Path | str = DEFAULT_PATH,
        failure_threshold: int = 3,
        probe_interval_seconds: float = 2.0,
        recovery_stabilization_seconds: float = 2.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.path = Path(path)
        self.failure_threshold = max(1, int(failure_threshold))
        self.probe_interval_seconds = max(0.1, float(probe_interval_seconds))
        self.recovery_stabilization_seconds = max(
            0.1, float(recovery_stabilization_seconds)
        )
        self._monotonic = monotonic
        self._positive_probes = 0
        self._first_positive_at: float | None = None
        previous_generation = self._read_generation()
        now = self._monotonic()
        self.snapshot = Snapshot(
            state=RECOVERING,
            reason_family="startup_fresh_probe_required",
            consecutive_failures=0,
            next_probe_at=now,
            changed_at=_utc_now(),
            generation=previous_generation + 1,
        )
        self._write()

    def _read_generation(self) -> int:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if str(raw.get("state") or "") not in VALID_STATES:
                return 0
            return max(0, int(raw.get("generation") or 0))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return 0

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(asdict(self.snapshot), sort_keys=True) + "\n"
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", dir=str(self.path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
            directory_fd = os.open(str(self.path.parent), os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass

    def _replace(self, **changes: object) -> Snapshot:
        values = asdict(self.snapshot)
        state_changed = changes.get("state", values["state"]) != values["state"]
        values.update(changes)
        if state_changed:
            values["changed_at"] = _utc_now()
        values["generation"] = int(values["generation"]) + 1
        self.snapshot = Snapshot(**values)
        self._write()
        return self.snapshot

    def record_failure(self, reason_family: str) -> Snapshot:
        self._positive_probes = 0
        self._first_positive_at = None
        failures = self.snapshot.consecutive_failures + 1
        state = UNAVAILABLE if failures >= self.failure_threshold else DEGRADED
        return self._replace(
            state=state,
            reason_family=str(reason_family or "transient_control_plane_failure")[:120],
            consecutive_failures=failures,
            next_probe_at=self._monotonic() + self.probe_interval_seconds,
        )

    def probe_due(self) -> bool:
        return (
            self.snapshot.state in {UNAVAILABLE, RECOVERING}
            and self._monotonic() >= self.snapshot.next_probe_at
        )

    def record_operation_success(self) -> Snapshot:
        if self.snapshot.state != DEGRADED:
            return self.snapshot
        return self._replace(
            state=HEALTHY,
            reason_family="normal_operation_succeeded",
            consecutive_failures=0,
            next_probe_at=0.0,
        )

    def record_probe_success(self) -> tuple[Snapshot, bool]:
        """Return snapshot and whether this is the one HEALTHY transition."""
        now = self._monotonic()
        if self.snapshot.state not in {UNAVAILABLE, RECOVERING}:
            return self.snapshot, False
        first = self._first_positive_at
        if first is None:
            self._positive_probes = 1
            self._first_positive_at = now
            return (
                self._replace(
                    state=RECOVERING,
                    reason_family="first_positive_probe",
                    consecutive_failures=0,
                    next_probe_at=now + self.recovery_stabilization_seconds,
                ),
                False,
            )
        if now - first < self.recovery_stabilization_seconds:
            return (
                self._replace(next_probe_at=first + self.recovery_stabilization_seconds),
                False,
            )
        self._positive_probes = 2
        return (
            self._replace(
                state=HEALTHY,
                reason_family="two_fresh_positive_probes",
                consecutive_failures=0,
                next_probe_at=0.0,
            ),
            True,
        )

    def record_probe_failure(self, reason_family: str) -> Snapshot:
        self._positive_probes = 0
        self._first_positive_at = None
        failures = max(self.failure_threshold, self.snapshot.consecutive_failures + 1)
        return self._replace(
            state=UNAVAILABLE,
            reason_family=str(reason_family or "probe_failed")[:120],
            consecutive_failures=failures,
            next_probe_at=self._monotonic() + self.probe_interval_seconds,
        )
