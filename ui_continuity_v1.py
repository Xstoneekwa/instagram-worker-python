"""Local suspension-aware fence for critical UI evidence.

No device, network, file, sleep or background polling on the hot path.
Observation is distinct from admission. Only the canonical fresh Identity
Guard re-entry may complete revalidation; an interrupted action is never retried.
"""
from __future__ import annotations

import ctypes
from contextlib import contextmanager
from dataclasses import dataclass
import math
import sys
import threading
import time
from typing import Callable


REASON = "ui_continuity_unproved_after_suspension"
# Differential clock evidence, NOT elapsed call duration or heartbeat age.
CLOCK_SKEW_NS = 250_000_000


class UiContinuityLost(BaseException):
    """Must escape candidate-local catch-all/retry loops without further taps."""
    def __init__(self, evidence: dict):
        super().__init__(REASON)
        self.evidence = dict(evidence)


def _continuous_clock() -> tuple[Callable[[], int], str]:
    if sys.platform == "darwin":
        library = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
        class Timebase(ctypes.Structure):
            _fields_ = [("numer", ctypes.c_uint32), ("denom", ctypes.c_uint32)]
        tb = Timebase()
        library.mach_timebase_info.argtypes = [ctypes.POINTER(Timebase)]
        if library.mach_timebase_info(ctypes.byref(tb)) != 0 or not tb.denom:
            raise RuntimeError("ui_continuous_clock_unavailable")
        clock = library.mach_continuous_time
        clock.argtypes = []
        clock.restype = ctypes.c_uint64
        return lambda: clock() * tb.numer // tb.denom, "mach_continuous_time"
    if hasattr(time, "CLOCK_BOOTTIME"):
        return lambda: time.clock_gettime_ns(time.CLOCK_BOOTTIME), "CLOCK_BOOTTIME"
    # Conservative portable fallback; rollback is also explicitly rejected.
    return time.time_ns, "wall_clock_fail_closed_fallback"


@dataclass(frozen=True)
class Sample:
    continuous: int
    monotonic: int
    wall: int


class ContinuityFence:
    def __init__(self, sample: Callable[[], Sample], clock_source: str):
        self.sample = sample
        self.clock_source = clock_source
        self.previous = sample()
        self.failure: dict | None = None
        self.epoch = 0
        self.verifying = False
        self.last_invalidation_monotonic = 0.0
        self._lock = threading.RLock()

    def observe(self, boundary: str) -> int:
        """Poll continuity but allow reads while admission is blocked."""
        with self._lock:
            try:
                now = self.sample()
            except Exception:
                self.epoch += 1
                self.last_invalidation_monotonic = float("inf")
                self.failure = {"reason": REASON, "cause": "clock_sample_failed",
                                "boundary": boundary, "clock_source": self.clock_source,
                                "fresh_identity_required": True, "old_ui_evidence_actionable": False}
                raise UiContinuityLost(self.failure) from None
            before = self.previous
            dc = now.continuous - before.continuous
            dm = now.monotonic - before.monotonic
            dw = now.wall - before.wall
            self.previous = now
            if self.last_invalidation_monotonic == float("inf"):
                self.last_invalidation_monotonic = max(before.monotonic, now.monotonic) / 1e9
            cause = ""
            if min(dc, dm, dw) < 0:
                cause = "clock_rollback"
            elif abs(dc - dm) > CLOCK_SKEW_NS:
                cause = "suspend_inclusive_clock_discontinuity"
            elif abs(dw - dc) > CLOCK_SKEW_NS:
                cause = "wall_clock_discontinuity"
            if cause:
                self.epoch += 1
                self.last_invalidation_monotonic = max(
                    self.last_invalidation_monotonic, before.monotonic / 1e9, now.monotonic / 1e9
                )
                self.failure = {"reason": REASON, "cause": cause, "boundary": boundary,
                                "clock_source": self.clock_source,
                                "continuous_delta_ns": dc, "monotonic_delta_ns": dm,
                                "wall_delta_ns": dw, "fresh_identity_required": True,
                                "old_ui_evidence_actionable": False, "epoch": self.epoch}
            return self.epoch

    def check(self, boundary: str) -> None:
        with self._lock:
            self.observe(boundary)
            if self.failure is not None or self.verifying:
                raise UiContinuityLost(self.failure or {"reason": REASON, "cause": "verification_in_progress"})

    def finish_observation(self, started_epoch: int, boundary: str) -> None:
        if self.observe(boundary) != started_epoch:
            raise UiContinuityLost(self.failure or {"reason": REASON, "cause": "interrupted_observation"})

    @contextmanager
    def revalidation(self):
        """Single bounded read-only bootstrap; failure leaves admission closed."""
        with self._lock:
            epoch = self.observe("revalidation:begin")
            if self.verifying or self.failure is None:
                raise UiContinuityLost({"reason": REASON, "cause": "invalid_revalidation_entry"})
            self.verifying = True
            started_at = self.previous.continuous
        try:
            yield epoch
            # Successful context exit means the canonical caller proved identity.
            with self._lock:
                self.finish_observation(epoch, "revalidation:complete")
                if self.previous.continuous - started_at > 20_000_000_000:
                    raise UiContinuityLost({"reason": REASON, "cause": "revalidation_proof_expired"})
                self.failure = None
        finally:
            self.verifying = False


_fence: ContinuityFence | None = None
_attempt: str | None = None


def begin_attempt(run_id: str) -> None:
    """Called once by runner; same-attempt reconfiguration cannot clear a fence."""
    global _fence, _attempt
    if _fence is not None:
        if _attempt != run_id:
            raise RuntimeError("ui_continuity_new_attempt_requires_new_process")
        check("attempt_reentry")
        return
    continuous, source = _continuous_clock()
    _fence = ContinuityFence(lambda: Sample(continuous(), time.monotonic_ns(), time.time_ns()), source)
    _attempt = run_id


def armed() -> bool:
    return _fence is not None


def check(boundary: str) -> None:
    if _fence is not None:
        _fence.check(boundary)


def failed() -> dict | None:
    return dict(_fence.failure) if _fence is not None and _fence.failure else None


def epoch() -> int:
    return _fence.epoch if _fence is not None else 0


def observe(boundary: str) -> int:
    return _fence.observe(boundary) if _fence is not None else 0


def finish_observation(started_epoch: int, boundary: str) -> None:
    if _fence is not None:
        _fence.finish_observation(started_epoch, boundary)


def require_epoch(proof_epoch: int, boundary: str) -> None:
    check(boundary)
    if proof_epoch != epoch():
        raise UiContinuityLost({"reason": REASON, "cause": "old_ui_epoch", "boundary": boundary,
                                "proof_epoch": proof_epoch, "epoch": epoch()})


def require_capture(captured_at: float, boundary: str) -> None:
    """Compatibility fence for legacy transported UI evidence with timestamps."""
    check(boundary)
    if _fence is not None and _fence.epoch:
        if not math.isfinite(captured_at) or captured_at <= _fence.last_invalidation_monotonic:
            raise UiContinuityLost({"reason": REASON, "cause": "pre_suspension_capture", "boundary": boundary})


@contextmanager
def revalidation():
    if _fence is None:
        raise RuntimeError("ui_continuity_not_armed")
    with _fence.revalidation() as ticket:
        yield ticket
