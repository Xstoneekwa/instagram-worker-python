"""Failure-isolated timing for existing run operations; never performs phone work."""

from __future__ import annotations

import os
import resource
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable


SCHEMA_VERSION = "RUN_LATENCY_OBSERVABILITY_V1"
PUBLIC_EVENT = "run_latency_observation_v1"
_MAX_OPEN_PHASES = 32

_PHASE_STARTS = {
    "followers_candidate_selected": "cycle",
    "visual_candidate_profile_state_entered": "profile_open",
    "follow_action_tap_started": "follow_verify",
    "post_follow_mute_started": "mute",
    "post_follow_post_likes_phase_started": "like",
    "post_follow_return_ct_started": "return_ct",
    "supabase_persist_step_started": "persistence",
    "p0c_recovery_before_ct_resume_started": "p0c",
    "ct_resume_checkpoint_load_claim_started": "ct_checkpoint_claim",
    "ct_resume_first_scroll_started": "ct_first_scroll",
}
_PHASE_ENDS = {
    "pre_follow_perf_summary": "profile_open",
    "follow_action_verified": "follow_verify",
    "post_follow_mute_success": "mute",
    "post_follow_mute_failed": "mute",
    "post_follow_like_perf_summary": "like",
    "post_follow_return_ct_success": "return_ct",
    "post_follow_return_ct_failed": "return_ct",
    "supabase_persist_step_completed": "persistence",
    "p0c_recovery_before_ct_resume_completed": "p0c",
    "ct_resume_checkpoint_load_claim_completed": "ct_checkpoint_claim",
    "ct_resume_first_scroll_completed": "ct_first_scroll",
    "post_return_critical_persist_completed": "cycle",
    "follow_target_private_streak_safe_boundary_reached": "cycle",
}


def _cpu_ms() -> float:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return (float(usage.ru_utime) + float(usage.ru_stime)) * 1000.0


def _safe_identity(fields: dict[str, Any]) -> dict[str, Any]:
    allowed = (
        "run_id",
        "request_id",
        "account_id",
        "source_profile_username",
        "source_profile",
        "target_id",
        "candidate_username",
        "follower_username",
        "device_serial",
        "device_id",
        "app_instance_id",
        "instagram_package",
        "instagram_version",
        "worker_sha",
    )
    identity = {key: fields[key] for key in allowed if fields.get(key) not in (None, "")}
    if fields.get("target_username") not in (None, ""):
        identity["source_profile_username"] = fields["target_username"]
    return identity


@dataclass
class _ObserverState:
    context: dict[str, Any] = field(default_factory=dict)
    phases: dict[str, tuple[float, float]] = field(default_factory=dict)
    previous_cycle_end_ms: float | None = None
    cycle_index: int = 0
    cycle_metrics: dict[str, float] = field(default_factory=dict)
    log_events_in_cycle: int = 0


_LOCAL = threading.local()


def _state() -> _ObserverState:
    value = getattr(_LOCAL, "state", None)
    if not isinstance(value, _ObserverState):
        value = _ObserverState()
        _LOCAL.state = value
    return value


def reset_for_tests() -> None:
    _LOCAL.state = _ObserverState()


def current_context() -> dict[str, Any]:
    """Return only the bounded identity currently correlated to the run."""

    return dict(_state().context)


def _metric_add(state: _ObserverState, key: str, value: Any) -> None:
    try:
        numeric = max(0.0, float(value or 0.0))
    except (TypeError, ValueError):
        return
    state.cycle_metrics[key] = float(state.cycle_metrics.get(key, 0.0)) + numeric


def _metric_max(state: _ObserverState, key: str, value: Any) -> None:
    try:
        numeric = max(0.0, float(value or 0.0))
    except (TypeError, ValueError):
        return
    state.cycle_metrics[key] = max(float(state.cycle_metrics.get(key, 0.0)), numeric)


def _cycle_aggregate(state: _ObserverState) -> dict[str, Any]:
    metrics = state.cycle_metrics
    return {
        "wait_requested_total_ms": round(metrics.get("wait_requested_total_ms", 0.0), 2),
        "wait_actual_total_ms": round(metrics.get("wait_actual_total_ms", 0.0), 2),
        "wait_overshoot_total_ms": round(metrics.get("wait_overshoot_total_ms", 0.0), 2),
        "adb_call_count": int(metrics.get("adb_call_count", 0.0)),
        "adb_total_ms": round(metrics.get("adb_total_ms", 0.0), 2),
        "adb_max_ms": round(metrics.get("adb_max_ms", 0.0), 2),
        "uiautomator_call_count": int(metrics.get("uiautomator_call_count", 0.0)),
        "uiautomator_total_ms": round(metrics.get("uiautomator_total_ms", 0.0), 2),
        "uiautomator_max_ms": round(metrics.get("uiautomator_max_ms", 0.0), 2),
        "xml_capture_count": int(metrics.get("xml_capture_count", 0.0)),
        "xml_capture_total_ms": round(metrics.get("xml_capture_total_ms", 0.0), 2),
        "xml_size_bytes": int(metrics.get("xml_size_bytes", 0.0)),
        "rpc_count": int(metrics.get("rpc_count", 0.0)),
        "rpc_total_ms": round(metrics.get("rpc_total_ms", 0.0), 2),
        "rpc_max_ms": round(metrics.get("rpc_max_ms", 0.0), 2),
        "log_events_per_cycle": int(state.log_events_in_cycle),
    }


def _timing_stamp() -> dict[str, Any]:
    return {
        "monotonic_ms": round(time.monotonic() * 1000.0, 3),
        "mac_display_correlation_ready": True,
    }


def observe_runtime_event(event: str, fields: dict[str, Any]) -> list[dict[str, Any]]:
    """Consume already-emitted fields and return bounded derived events."""

    if str(event or "") == PUBLIC_EVENT:
        return []
    state = _state()
    incoming = _safe_identity(dict(fields or {}))
    incoming_run_id = str(incoming.get("run_id") or "")
    prior_run_id = str(state.context.get("run_id") or "")
    if incoming_run_id and prior_run_id and incoming_run_id != prior_run_id:
        state = _ObserverState()
        _LOCAL.state = state
    if str(event or "") in {"follow_target_selected", "target_scan_started"}:
        state.context.pop("candidate_username", None)
        state.context.pop("follower_username", None)
    state.context.update(incoming)
    event_name = str(event or "")
    if "cycle" in state.phases:
        state.log_events_in_cycle += 1
    if event_name == "run_latency_wait_v1":
        _metric_add(state, "wait_requested_total_ms", fields.get("requested_ms"))
        _metric_add(state, "wait_actual_total_ms", fields.get("actual_ms"))
        _metric_add(state, "wait_overshoot_total_ms", fields.get("overshoot_ms"))
        return []
    if event_name == "run_latency_io_timing_v1":
        kind = str(fields.get("io_kind") or "")
        duration = fields.get("duration_ms")
        if kind in {"adb", "uiautomator", "xml_capture"}:
            count_key = "xml_capture_count" if kind == "xml_capture" else f"{kind}_call_count"
            _metric_add(state, count_key, 1)
            _metric_add(state, f"{kind}_total_ms", duration)
            _metric_max(state, f"{kind}_max_ms", duration)
        if kind == "xml_capture":
            _metric_add(state, "xml_size_bytes", fields.get("xml_size_bytes"))
        return []
    if event_name == "run_latency_rpc_timing_v1":
        _metric_add(state, "rpc_count", 1)
        _metric_add(state, "rpc_total_ms", fields.get("duration_ms"))
        _metric_max(state, "rpc_max_ms", fields.get("duration_ms"))
        return []
    if event_name == "target_scan_completed":
        seen = max(0, int(fields.get("candidates_seen") or 0))
        opened = max(0, int(fields.get("candidates_opened") or 0))
        rejected = max(0, int(fields.get("candidates_rejected") or 0))
        return [{
            "schema_version": SCHEMA_VERSION,
            "metric": "ct_profile_density",
            "candidates_seen": seen,
            "candidates_opened": opened,
            "candidates_rejected": rejected,
            "open_density_ratio": round(opened / seen, 4) if seen else 0.0,
            "rejection_density_ratio": round(rejected / seen, 4) if seen else 0.0,
            **_timing_stamp(),
            **state.context,
        }]
    phase = _PHASE_STARTS.get(event_name)
    if phase:
        now_ms = time.monotonic() * 1000.0
        cpu_ms = _cpu_ms()
        derived: list[dict[str, Any]] = []
        if phase == "cycle" and phase in state.phases:
            previous = state.phases.pop(phase)
            wall_ms = max(0.0, now_ms - previous[0])
            process_cpu_ms = max(0.0, cpu_ms - previous[1])
            derived.append({
                "schema_version": SCHEMA_VERSION,
                "metric": "phase_duration",
                "phase": "cycle",
                "source_event": "next_candidate_safe_boundary",
                "duration_ms": round(wall_ms, 2),
                "process_cpu_ms": round(process_cpu_ms, 2),
                "cpu_busy_ratio": round(min(1.0, process_cpu_ms / wall_ms), 4) if wall_ms else 0.0,
                "cycle_index": state.cycle_index,
                **_cycle_aggregate(state),
                **_timing_stamp(),
                **state.context,
            })
            state.previous_cycle_end_ms = now_ms
            state.phases.clear()
            state.cycle_metrics.clear()
            state.log_events_in_cycle = 0
        if len(state.phases) >= _MAX_OPEN_PHASES:
            state.phases.clear()
        state.phases[phase] = (now_ms, cpu_ms)
        if phase == "cycle":
            state.cycle_index += 1
            if state.previous_cycle_end_ms is not None:
                derived.append({
                    "schema_version": SCHEMA_VERSION,
                    "metric": "inter_cycle_gap",
                    "duration_ms": round(max(0.0, now_ms - state.previous_cycle_end_ms), 2),
                    "cycle_index": state.cycle_index,
                    **_timing_stamp(),
                    **state.context,
                })
        return derived
    phase = _PHASE_ENDS.get(event_name)
    if not phase:
        return []
    now_ms = time.monotonic() * 1000.0
    cpu_ms = _cpu_ms()
    started = state.phases.pop(phase, None)
    if started is None:
        return []
    wall_ms = max(0.0, now_ms - started[0])
    process_cpu_ms = max(0.0, cpu_ms - started[1])
    if phase == "cycle":
        state.previous_cycle_end_ms = now_ms
        state.phases.clear()
    row = {
        "schema_version": SCHEMA_VERSION,
        "metric": "phase_duration",
        "phase": phase,
        "source_event": str(event or ""),
        "duration_ms": round(wall_ms, 2),
        "process_cpu_ms": round(process_cpu_ms, 2),
        "cpu_busy_ratio": round(min(1.0, process_cpu_ms / wall_ms), 4) if wall_ms else 0.0,
        "cycle_index": state.cycle_index,
        **_timing_stamp(),
        **state.context,
    }
    if phase == "cycle":
        row.update(_cycle_aggregate(state))
        state.cycle_metrics.clear()
        state.log_events_in_cycle = 0
    return [row]


def observed_wait(
    requested_seconds: float,
    *,
    reason: str,
    sleep: Callable[[float], None] = time.sleep,
    emit: Callable[..., None] | None = None,
) -> None:
    """Time one existing wait without adding or changing the wait itself."""

    requested = max(0.0, float(requested_seconds))
    started = time.monotonic()
    sleep(requested)
    actual_ms = max(0.0, (time.monotonic() - started) * 1000.0)
    if emit is not None:
        try:
            emit(
                "info",
                "run_latency_wait_v1",
                schema_version=SCHEMA_VERSION,
                reason=str(reason or "existing_wait")[:120],
                requested_ms=round(requested * 1000.0, 2),
                actual_ms=round(actual_ms, 2),
                overshoot_ms=round(max(0.0, actual_ms - requested * 1000.0), 2),
                monotonic_clock=True,
                monotonic_ms=round(time.monotonic() * 1000.0, 3),
                **current_context(),
            )
        except Exception:
            pass


_DEVICE_METHODS = frozenset(
    {"shell", "dump_hierarchy", "click", "swipe", "press", "app_current", "app_start", "wait_activity"}
)
_UI_METHODS = frozenset(
    {"click", "click_exists", "long_click", "wait", "exists", "get_text", "set_text", "clear_text", "swipe", "scroll"}
)


class _TimedProxy:
    def __init__(self, raw: Any, *, kind: str, emit: Callable[..., None] | None, methods: frozenset[str]):
        object.__setattr__(self, "_raw", raw)
        object.__setattr__(self, "_kind", kind)
        object.__setattr__(self, "_emit", emit)
        object.__setattr__(self, "_methods", methods)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        result = self._raw(*args, **kwargs)
        return _TimedProxy(result, kind="uiautomator", emit=self._emit, methods=_UI_METHODS)

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._raw, name)
        if name == "exists" and callable(attr):
            return _TimedExistsProxy(attr, emit=self._emit)
        if name not in self._methods or not callable(attr):
            return attr

        def timed(*args: Any, **kwargs: Any) -> Any:
            started = time.monotonic()
            cpu_started = _cpu_ms()
            succeeded = False
            result: Any = None
            try:
                result = attr(*args, **kwargs)
                succeeded = True
                return result
            finally:
                wall_ms = max(0.0, (time.monotonic() - started) * 1000.0)
                if self._emit is not None:
                    try:
                        io_kind = (
                            "adb" if name == "shell"
                            else "xml_capture" if name == "dump_hierarchy"
                            else "uiautomator"
                        )
                        self._emit(
                            "info",
                            "run_latency_io_timing_v1",
                            schema_version=SCHEMA_VERSION,
                            io_kind=io_kind,
                            operation=name,
                            duration_ms=round(wall_ms, 2),
                            process_cpu_ms=round(max(0.0, _cpu_ms() - cpu_started), 2),
                            success=succeeded,
                            result_kind=type(result).__name__ if succeeded else "error",
                            xml_size_bytes=(
                                len(result.encode("utf-8"))
                                if name == "dump_hierarchy" and isinstance(result, str)
                                else 0
                            ),
                            payload_captured=False,
                            extra_phone_call=False,
                            monotonic_ms=round(time.monotonic() * 1000.0, 3),
                            **current_context(),
                        )
                    except Exception:
                        pass

        return timed

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._raw, name, value)

    def __repr__(self) -> str:
        return repr(self._raw)


class _TimedExistsProxy:
    """Preserve uiautomator2's callable/bool ``exists`` object exactly."""

    def __init__(self, raw: Any, *, emit: Callable[..., None] | None):
        self._raw = raw
        self._emit = emit

    def _measure(self, operation: Callable[[], Any]) -> Any:
        started = time.monotonic()
        cpu_started = _cpu_ms()
        succeeded = False
        result: Any = None
        try:
            result = operation()
            succeeded = True
            return result
        finally:
            if self._emit is not None:
                try:
                    self._emit(
                        "info",
                        "run_latency_io_timing_v1",
                        schema_version=SCHEMA_VERSION,
                        io_kind="uiautomator",
                        operation="exists",
                        duration_ms=round(max(0.0, (time.monotonic() - started) * 1000.0), 2),
                        process_cpu_ms=round(max(0.0, _cpu_ms() - cpu_started), 2),
                        success=succeeded,
                        result_kind=type(result).__name__ if succeeded else "error",
                        xml_size_bytes=0,
                        payload_captured=False,
                        extra_phone_call=False,
                        monotonic_ms=round(time.monotonic() * 1000.0, 3),
                        **current_context(),
                    )
                except Exception:
                    pass

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self._measure(lambda: self._raw(*args, **kwargs))

    def __bool__(self) -> bool:
        return bool(self._measure(lambda: bool(self._raw)))

    def __repr__(self) -> str:
        return repr(bool(self))


def instrument_device(device: Any, *, emit: Callable[..., None] | None = None) -> Any:
    """Wrap existing calls only. If wrapping cannot be established, return the raw device."""

    if device is None or isinstance(device, _TimedProxy):
        return device
    if os.environ.get("PHONEFARM_RUN_LATENCY_OBSERVABILITY_V1", "1").strip().lower() in {"0", "false", "no"}:
        return device
    try:
        return _TimedProxy(device, kind="device", emit=emit, methods=_DEVICE_METHODS)
    except Exception:
        return device
