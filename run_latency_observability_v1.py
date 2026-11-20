"""Failure-isolated timing for existing run operations; never performs phone work."""

from __future__ import annotations

import math
import os
import resource
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable


SCHEMA_VERSION = "RUN_LATENCY_OBSERVABILITY_V1"
PUBLIC_EVENT = "run_latency_observation_v1"
_MAX_OPEN_PHASES = 32
_MAX_WINDOW_INTERVALS = 1024
_TIMING_ENABLED = "PHONEFARM_RUN_LATENCY_OBSERVABILITY_V1"

_PHASE_STARTS = {
    "followers_candidate_selected": "cycle",
    "visual_candidate_profile_state_entered": "profile_open",
    "follower_profile_open_started": "candidate_open",
    "open_search_started": "ct_search",
    "startup_ct_row_detect_started": "ct_row",
    "startup_ct_profile_verify_started": "ct_profile",
    "startup_followers_open_requested": "ct_followers",
    "follow_ct_search_results_blank_after_query": "ct_search_recovery",
    "follow_target_rotation_requested": "ct_rotation",
    "follow_target_local_failure_skipped_current_attempt": "ct_rotation",
    "follow_target_switched": "ct_rotation",
    "post_like_surface_to_scroll_gap_started": "post_open",
    "visual_post_like_tap_sent": "like_verify",
    "visual_post_like_verify_success": "like_return_profile",
    "pre_follow_tap_ready": "follow_dispatch",
    "follow_action_tap_started": "follow_verify",
    "follow_action_exact_follow_tap_sent": "follow_verify",
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
    "follower_profile_open_success": "candidate_open",
    "follower_profile_open_failed": "candidate_open",
    "search_typed": "ct_search",
    "startup_ct_row_detect_completed": "ct_row",
    "startup_ct_profile_verify_completed": "ct_profile",
    "follow_ct_search_results_recovery_completed": "ct_search_recovery",
    "followers_entry_post_tap_xml_fast_confirmed": "ct_followers",
    "post_follow_post_likes_perf_post_open_done": "post_open",
    "visual_post_like_verify_success": "like_verify",
    "post_follow_like_completed": "like_return_profile",
    "follow_tap_sent": "follow_dispatch",
    "follow_action_verified": "follow_verify",
    "follow_action_exact_follow_verified": "follow_verify",
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

_TERMINAL_EVENTS = {
    "manual_stop_signal_received", "runtime_termination_signal_received",
    "startup_cancellation_safe_stop", "run_aborted", "follow_latency_run_ending",
}
_TIMED_OPERATIONS = ("xml_parse", "xml_capture", "uiautomator", "wait", "rpc", "adb")
_PHASE_IDENTITY_FIELDS = (
    "run_id", "request_id", "account_id", "target_id", "source_profile_username",
    "candidate_username",
)


def timing_enabled() -> bool:
    return os.environ.get(_TIMING_ENABLED, "1").strip().lower() not in {"0", "false", "no"}


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
    if "source_profile_username" not in identity and fields.get("target_username") not in (None, ""):
        identity["source_profile_username"] = fields["target_username"]
    if "candidate_username" not in identity and "follower_username" in identity:
        identity["candidate_username"] = identity["follower_username"]
    return identity


@dataclass
class _OpenPhase:
    started_ms: float
    cpu_ms: float
    invocation: int
    cycle_index: int
    identity: dict[str, Any]


@dataclass
class _ObserverState:
    context: dict[str, Any] = field(default_factory=dict)
    phases: dict[str, _OpenPhase] = field(default_factory=dict)
    previous_cycle_end_ms: float | None = None
    previous_admission_ms: float | None = None
    previous_admission_cycle: int = 0
    cycle_index: int = 0
    phase_sequence: int = 0
    terminated: bool = False
    cycle_metrics: dict[str, float] = field(default_factory=dict)
    intervals: list[tuple[float, float, str]] = field(default_factory=list)
    intervals_truncated: bool = False
    log_events_in_cycle: int = 0
    dm446_context: dict[str, Any] = field(default_factory=dict)
    dm446_generation: int = 0


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

    state = _state()
    if state.dm446_context:
        # Outreach spans carry their own opaque binding, never a prior
        # candidate username left by an unrelated logger/previous job.
        return {**state.dm446_context, 'ui_generation': state.dm446_generation,
                'generation_scope': 'observed_proxy_mutations_not_admission_authority'}
    cycle = state.phases.get("cycle")
    return {**state.context, "cycle_index": cycle.cycle_index if cycle else 0}


@contextmanager
def observed_operation(operation: str, *, kind: str, emit, **identity):
    """Measure an existing operation and optionally annotate its structured outcome."""
    outcome: dict[str, Any] = {}
    try:
        started = time.monotonic() if timing_enabled() else None
        if started is not None:
            context = current_context()
            binding = dict(identity) if "request_id" in identity else {**context, **identity}
    except Exception:
        started = None
    complete = False
    try:
        yield outcome
        complete = True
    finally:
        if started is not None:
            try:
                finished = time.monotonic()
                emit(
                    "info", "run_latency_operation_v1",
                    schema_version=SCHEMA_VERSION, operation=operation, io_kind=kind,
                    duration_ms=round((finished - started) * 1000.0, 3),
                    monotonic_ms=round(finished * 1000.0, 3),
                    outcome=outcome.get("status", "completed" if complete else "interrupted"),
                    _defer_follow_timing=True,
                    **binding,
                    **{key: value for key, value in outcome.items() if key != "status"},
                )
            except Exception:
                pass


@contextmanager
def dm446_span(callsite: str, *, identity=None, emit=None):
    """Bounded opt-in spans on the existing observer; no I/O or payload reads."""
    enabled = os.environ.get('PHONEFARM_RUN_LATENCY_OBSERVABILITY_V1', '1').strip().lower() not in {'0', 'false', 'no'}
    state = _state()
    prior = state.dm446_context
    if not enabled or (not prior and identity is None):
        yield
        return
    try:
        allowed = ('run_id', 'job_id', 'account_id', 'request_id', 'visit_index')
        incoming = {k: v for k, v in (identity or {}).items() if k in allowed and v is not None}
        started = time.monotonic()
        if not prior:
            # Use the existing new-run reset before any nested RPC log can
            # invalidate the active scoped observer state.
            observe_runtime_event('dm446_timing_context', incoming)
            state = _state()
            state.dm446_generation = 0
        state.dm446_context = {**prior, **incoming, 'callsite': callsite[:96], 'phase': callsite[:96]}
    except Exception:
        yield
        return
    success = False
    try:
        yield
        success = True
    finally:
        try:
            if emit is not None:
                emit('info', PUBLIC_EVENT, schema_version=SCHEMA_VERSION,
                     metric='dm446_callsite', duration_ms=round(max(0., (time.monotonic()-started)*1000.), 3),
                     success=success, payload_captured=False, extra_phone_call=False,
                     monotonic_ms=round(time.monotonic()*1000., 3), **current_context())
        except Exception:
            pass
        finally:
            state.dm446_context = prior


def dm446_wait(seconds, *, reason, sleep, emit=None):
    """Measure the same existing wait only inside a scoped Outreach span."""
    if not _state().dm446_context:
        sleep(seconds)
        return
    observed_wait(seconds, reason=reason, sleep=sleep, emit=emit)


def _dm446_io(operation, name, emit):
    # A timing/sink failure must never suppress, repeat or replace the call.
    try:
        started = time.monotonic()
    except Exception:
        return operation()
    state = _state()
    if name in {'click', 'click_exists', 'press', 'swipe', 'scroll', 'set_text', 'clear_text', 'long_click', 'app_start', 'shell'}:
        state.dm446_generation += 1
    success = False
    result = None
    try:
        result = operation()
        success = True
        return result
    finally:
        if emit is not None:
            try:
                emit('info', 'run_latency_io_timing_v1', schema_version=SCHEMA_VERSION,
                     io_kind='xml_capture' if name=='dump_hierarchy' else 'adb' if name=='shell' else 'uiautomator',
                     operation=name, duration_ms=round(max(0., (time.monotonic()-started)*1000.), 3),
                     success=success, result_kind=type(result).__name__ if success else 'error',
                     xml_size_bytes=len(result.encode('utf-8')) if name=='dump_hierarchy' and isinstance(result,str) else 0,
                     payload_captured=False, extra_phone_call=False,
                     transport_scope='outer_adapter_call_not_internal_ADB_count',
                     monotonic_ms=round(time.monotonic()*1000.,3), **current_context())
            except Exception:
                pass


_FIRST_SCROLL_BOUNDARIES = (
    "first_scroll_requested_monotonic_ms",
    "first_scroll_physical_entry_monotonic_ms",
    "first_scroll_physical_complete_monotonic_ms",
    "first_scroll_stabilization_complete_monotonic_ms",
    "first_scroll_verify_complete_monotonic_ms",
)


def first_scroll_segment_fields(boundaries: dict[str, Any] | None) -> dict[str, Any]:
    """Derive bounded CT Resume first-scroll timings without affecting execution."""

    result: dict[str, Any] = {
        "scroll_request_to_physical_ms": None,
        "scroll_physical_envelope_ms": None,
        "scroll_stabilization_ms": None,
        "scroll_verify_ms": None,
        "first_scroll_segmented_total_ms": None,
        "first_scroll_segmentation_status": "BOUNDARY_UNAVAILABLE",
        "first_scroll_monotonic_clock": True,
    }
    try:
        source = boundaries if isinstance(boundaries, dict) else {}
        stamps = [float(source[key]) for key in _FIRST_SCROLL_BOUNDARIES]
        if not all(math.isfinite(value) for value in stamps):
            return result
        if any(later < earlier for earlier, later in zip(stamps, stamps[1:])):
            result["first_scroll_segmentation_status"] = "BOUNDARY_ORDER_INVALID"
            return result
        segments = [max(0.0, later - earlier) for earlier, later in zip(stamps, stamps[1:])]
        rounded = [round(value, 3) for value in segments]
        result.update(
            {
                "scroll_request_to_physical_ms": rounded[0],
                "scroll_physical_envelope_ms": rounded[1],
                "scroll_stabilization_ms": rounded[2],
                "scroll_verify_ms": rounded[3],
                "first_scroll_segmented_total_ms": round(sum(segments), 3),
                "first_scroll_segmentation_status": "COMPLETE",
            }
        )
    except Exception:
        pass
    return result


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
        "rpc_attempt_count_measured": int(metrics.get("rpc_attempt_count_measured", 0.0)),
        "rpc_unmeasured_attempt_operations": int(metrics.get("rpc_unmeasured_attempt_operations", 0.0)),
        "rpc_backoff_ms_measured": round(metrics.get("rpc_backoff_ms_measured", 0.0), 3),
        "log_events_per_cycle": int(state.log_events_in_cycle),
    }


def _timing_stamp() -> dict[str, Any]:
    return {
        "monotonic_ms": round(time.monotonic() * 1000.0, 3),
        "mac_display_correlation_ready": True,
    }


def _matches_phase(phase: _OpenPhase, fields: dict[str, Any], *, check_invocation=True) -> bool:
    if check_invocation and fields.get("phase_invocation", phase.invocation) != phase.invocation:
        return False
    if phase.cycle_index and fields.get("cycle_index", phase.cycle_index) != phase.cycle_index:
        return False
    explicit = dict(fields)
    if not explicit.get("candidate_username"):
        explicit["candidate_username"] = explicit.get("follower_username")
    return all(
        not explicit.get(key) or not phase.identity.get(key) or explicit[key] == phase.identity[key]
        for key in _PHASE_IDENTITY_FIELDS
    )


def _record_interval(state: _ObserverState, fields: dict[str, Any], kind: str, duration_key: str) -> None:
    end = fields.get("monotonic_ms")
    duration = fields.get(duration_key)
    if not isinstance(end, (int, float)) or not isinstance(duration, (int, float)):
        return
    if not math.isfinite(end) or not math.isfinite(duration) or duration <= 0:
        return
    if len(state.intervals) >= _MAX_WINDOW_INTERVALS:
        state.intervals_truncated = True
        return
    state.intervals.append((end - duration, end, kind))


def _window_totals(state: _ObserverState, start: float, end: float) -> dict[str, Any]:
    edges = []
    for left, right, kind in state.intervals:
        left, right = max(left, start), min(right, end)
        if left < right:
            edges.extend(((left, kind, 1), (right, kind, -1)))
    active = dict.fromkeys(_TIMED_OPERATIONS, 0)
    measured = dict.fromkeys(_TIMED_OPERATIONS, 0.0)
    previous = start
    for instant, kind, change in sorted(edges):
        # A more specific inner observation wins over an outer envelope.
        winner = next((key for key in _TIMED_OPERATIONS if active[key]), None)
        if winner:
            measured[winner] += instant - previous
        active[kind] += change
        previous = instant
    values = {kind: round(value, 3) for kind, value in measured.items() if value}
    return {
        "exclusive_operation_ms": values,
        "unclassified_ms": round(max(0.0, end - start - sum(measured.values())), 3),
        "operation_intervals_truncated": state.intervals_truncated,
    }


def _clear_window(state: _ObserverState) -> None:
    state.cycle_metrics.clear()
    state.intervals.clear()
    state.intervals_truncated = False
    state.log_events_in_cycle = 0


def _phase_row(
    state: _ObserverState,
    phase: str,
    event: str,
    opened: _OpenPhase,
    now_ms: float,
    cpu_ms: float,
    *,
    outcome: str,
    reason: str | None = None,
) -> dict[str, Any]:
    wall_ms = max(0.0, now_ms - opened.started_ms)
    process_cpu_ms = max(0.0, cpu_ms - opened.cpu_ms)
    row = {
        **opened.identity,
        "schema_version": SCHEMA_VERSION,
        "metric": "phase_duration",
        "phase": phase,
        "source_event": event,
        "phase_invocation": opened.invocation,
        "outcome": outcome,
        "duration_ms": round(wall_ms, 2),
        "process_cpu_ms": round(process_cpu_ms, 2),
        "cpu_busy_ratio": round(min(1.0, process_cpu_ms / wall_ms), 4) if wall_ms else 0.0,
        "cycle_index": opened.cycle_index,
        "phase_started_monotonic_ms": round(opened.started_ms, 3),
        "monotonic_ms": round(now_ms, 3),
        "mac_display_correlation_ready": True,
    }
    if reason:
        row["terminal_reason"] = reason[:120]
    if phase == "cycle":
        row.update(_cycle_aggregate(state), **_window_totals(state, opened.started_ms, now_ms))
    return row


def _close_open_phases(state, event, now_ms, cpu_ms, reason, *, keep_rotation=False):
    rows = []
    for phase, opened in tuple(state.phases.items()):
        if keep_rotation and phase == "ct_rotation":
            continue
        rows.append(_phase_row(
            state, phase, event, opened, now_ms, cpu_ms, outcome="interrupted", reason=reason,
        ))
        del state.phases[phase]
    return rows


def observe_runtime_event(event: str, fields: dict[str, Any]) -> list[dict[str, Any]]:
    """Consume already-emitted fields and return bounded derived events."""
    if event == PUBLIC_EVENT or not timing_enabled():
        return []
    state = _state()
    incoming = _safe_identity(fields)
    incoming_run_id = str(incoming.get("run_id") or "")
    prior_run_id = str(state.context.get("run_id") or "")
    timed = event in {
        "run_latency_wait_v1", "run_latency_io_timing_v1", "run_latency_rpc_timing_v1",
        "run_latency_operation_v1",
    }
    if incoming_run_id and prior_run_id and incoming_run_id != prior_run_id:
        if timed or event in _PHASE_ENDS or event in _TERMINAL_EVENTS:
            return []
        state = _ObserverState()
        _LOCAL.state = state

    active_cycle = state.phases.get("cycle")
    if timed:
        if state.terminated or (active_cycle and not _matches_phase(active_cycle, fields, check_invocation=False)):
            return []
        if not active_cycle and state.previous_cycle_end_ms is None:
            # Startup remains visible in operation rows, outside candidate aggregates.
            return []
        if not active_cycle and fields.get("cycle_index", 0) != 0:
            return []
        if event == "run_latency_wait_v1":
            _record_interval(state, fields, "wait", "actual_ms")
            for key in ("requested", "actual", "overshoot"):
                _metric_add(state, f"wait_{key}_total_ms", fields.get(f"{key}_ms"))
        elif event == "run_latency_rpc_timing_v1":
            _record_interval(state, fields, "rpc", "duration_ms")
            _metric_add(state, "rpc_count", 1)
            _metric_add(state, "rpc_total_ms", fields.get("duration_ms"))
            _metric_max(state, "rpc_max_ms", fields.get("duration_ms"))
            _metric_add(state, "rpc_attempt_count_measured", fields.get("attempt_count"))
            _metric_add(state, "rpc_unmeasured_attempt_operations", fields.get("attempt_count") is None)
            _metric_add(state, "rpc_backoff_ms_measured", fields.get("backoff_ms"))
        else:
            kind = fields.get("io_kind")
            if kind in _TIMED_OPERATIONS:
                _record_interval(state, fields, kind, "duration_ms")
            # These are physical wrapper call counts, separate from coarse operation spans.
            if event == "run_latency_io_timing_v1" and kind in {"adb", "uiautomator", "xml_capture"}:
                count_key = "xml_capture_count" if kind == "xml_capture" else f"{kind}_call_count"
                _metric_add(state, count_key, 1)
                _metric_add(state, f"{kind}_total_ms", fields.get("duration_ms"))
                _metric_max(state, f"{kind}_max_ms", fields.get("duration_ms"))
                if kind == "xml_capture":
                    _metric_add(state, "xml_size_bytes", fields.get("xml_size_bytes"))
        if active_cycle:
            state.log_events_in_cycle += 1
        return []

    if state.terminated:
        return []
    if active_cycle and event != "followers_candidate_selected" and not _matches_phase(
        active_cycle, fields, check_invocation=False,
    ):
        return []

    if event == "followers_candidate_selected":
        now_ms, cpu_ms = time.monotonic() * 1000.0, _cpu_ms()
        rows = []
        if active_cycle:
            rows.extend(_close_open_phases(
                state, "next_candidate_safe_boundary", now_ms, cpu_ms, "next_candidate_selected",
                keep_rotation=True,
            ))
            state.previous_cycle_end_ms = now_ms
            _clear_window(state)
        for key in ("candidate_username", "follower_username"):
            state.context.pop(key, None)
        state.context.update(incoming)
        state.cycle_index += 1
        if state.previous_cycle_end_ms is not None:
            rows.append({
                **state.context,
                "schema_version": SCHEMA_VERSION,
                "metric": "inter_cycle_gap",
                "duration_ms": round(max(0.0, now_ms - state.previous_cycle_end_ms), 2),
                "cycle_index": state.cycle_index,
                **_window_totals(state, state.previous_cycle_end_ms, now_ms),
                **_timing_stamp(),
            })
        _clear_window(state)
        state.previous_cycle_end_ms = None
        state.phase_sequence += 1
        state.phases["cycle"] = _OpenPhase(
            now_ms, cpu_ms, state.phase_sequence, state.cycle_index, dict(state.context),
        )
        return rows

    if event in _TERMINAL_EVENTS or event == "target_scan_completed":
        now_ms, cpu_ms = time.monotonic() * 1000.0, _cpu_ms()
        reason = str(fields.get("first_causal_reason") or fields.get("stop_reason") or fields.get("reason") or event)
        rows = _close_open_phases(
            state, event, now_ms, cpu_ms, reason, keep_rotation=event == "target_scan_completed",
        )
        _clear_window(state)
        state.previous_cycle_end_ms = None
        if event != "target_scan_completed":
            state.terminated = True
            return rows
        seen = max(0, int(fields.get("candidates_seen") or 0))
        opened = max(0, int(fields.get("candidates_opened") or 0))
        rejected = max(0, int(fields.get("candidates_rejected") or 0))
        rows.append({
            **state.context, **incoming,
            "schema_version": SCHEMA_VERSION,
            "metric": "ct_profile_density",
            "candidates_seen": seen,
            "candidates_opened": opened,
            "candidates_rejected": rejected,
            "open_density_ratio": round(opened / seen, 4) if seen else 0.0,
            "rejection_density_ratio": round(rejected / seen, 4) if seen else 0.0,
            **_timing_stamp(),
        })
        return rows

    if event in {"follow_target_selected", "target_scan_started"}:
        for key in ("candidate_username", "follower_username"):
            state.context.pop(key, None)
    if active_cycle:
        state.log_events_in_cycle += 1
    # target_username alone can name the candidate in action events.
    if active_cycle and not fields.get("source_profile_username"):
        incoming.pop("source_profile_username", None)
    state.context.update(incoming)

    rows = []
    if event == "follower_profile_open_row_action_token_committed" and active_cycle:
        now_ms = time.monotonic() * 1000.0
        previous = state.previous_admission_ms
        first_for_candidate = state.previous_admission_cycle != active_cycle.cycle_index
        state.phase_sequence += 1
        rows.append({
            **active_cycle.identity,
            "schema_version": SCHEMA_VERSION,
            "metric": "candidate_admission",
            "source_event": event,
            "phase_invocation": state.phase_sequence,
            "cycle_index": active_cycle.cycle_index,
            "first_for_candidate": first_for_candidate,
            "elapsed_since_selection_ms": round(max(0.0, now_ms - active_cycle.started_ms), 3),
            "admission_gap_ms": round(now_ms - previous, 3)
            if first_for_candidate and previous is not None else None,
            "monotonic_ms": round(now_ms, 3),
        })
        if first_for_candidate:
            state.previous_admission_ms = now_ms
            state.previous_admission_cycle = active_cycle.cycle_index
    end_phase = _PHASE_ENDS.get(event)
    end_phases = ("ct_followers", "ct_rotation") if event == "follow_latency_ct_confirmed" else (end_phase,)
    for phase in end_phases:
        opened = state.phases.get(phase)
        bind = {k: v for k, v in fields.items() if k not in {"source_profile_username", "target_id"}} if phase == "ct_rotation" else fields
        if phase == "ct_rotation" and opened:
            next_source = opened.identity.get("next_source_profile")
            next_target_id = opened.identity.get("next_target_id")
            if (not next_source or incoming.get("source_profile_username") != next_source
                    or (next_target_id and incoming.get("target_id") != next_target_id)):
                continue
        if opened and _matches_phase(opened, bind):
            now_ms, cpu_ms = time.monotonic() * 1000.0, _cpu_ms()
            del state.phases[phase]
            recovery_reason = fields.get("final_reason") if phase == "ct_search_recovery" else None
            failed = (event.endswith("_failed") or (phase == "ct_row" and fields.get("found") is False)
                      or recovery_reason == "search_results_still_blank_after_recovery")
            rows.append(_phase_row(
                state, phase, event, opened, now_ms, cpu_ms,
                outcome="failed" if failed else "completed", reason=recovery_reason,
            ))
            if phase == "cycle":
                rows.extend(_close_open_phases(state, event, now_ms, cpu_ms, "cycle_closed", keep_rotation=True))
                state.previous_cycle_end_ms = now_ms
                _clear_window(state)

    phase = _PHASE_STARTS.get(event)
    if phase and len(state.phases) < _MAX_OPEN_PHASES:
        now_ms, cpu_ms = time.monotonic() * 1000.0, _cpu_ms()
        opened = state.phases.get(phase)
        identity = dict(state.context)
        if phase == "ct_rotation":
            source = (fields.get("from_source_target") or fields.get("source_target")
                      or fields.get("source_profile") or fields.get("source_profile_username"))
            next_source = fields.get("to_source_target") or fields.get("next_source_profile")
            next_target_id = fields.get("next_target_id")
            if not source or source == next_source:
                return rows
            identity["source_profile_username"] = source
            reason = fields.get("first_causal_reason") or fields.get("reason")
            if isinstance(reason, str):
                identity["rotation_reason"] = reason[:120]
            if next_source:
                identity["next_source_profile"] = next_source
            if next_target_id:
                identity["next_target_id"] = next_target_id
            if opened and opened.identity.get("source_profile_username") == source:
                pinned_next = opened.identity.get("next_source_profile")
                pinned_id = opened.identity.get("next_target_id")
                if (not pinned_next or not next_source or pinned_next == next_source) and (
                    not pinned_id or not next_target_id or pinned_id == next_target_id
                ):
                    opened.identity.update({
                        key: identity[key] for key in ("next_source_profile", "next_target_id") if key in identity
                    })
                    return rows
        if opened:
            rows.append(_phase_row(state, phase, event, opened, now_ms, cpu_ms,
                                   outcome="interrupted", reason="phase_restarted"))
        state.phase_sequence += 1
        state.phases[phase] = _OpenPhase(
            now_ms, cpu_ms, state.phase_sequence, active_cycle.cycle_index if active_cycle else 0,
            identity,
        )
    return rows


def observed_wait(
    requested_seconds: float,
    *,
    reason: str,
    sleep: Callable[[float], None] = time.sleep,
    emit: Callable[..., None] | None = None,
    defer_timing: bool = False,
) -> None:
    """Time one existing wait without adding or changing the wait itself."""

    requested = max(0.0, float(requested_seconds))
    try:
        started = time.monotonic() if timing_enabled() else None
        binding = current_context() if started is not None else {}
    except Exception:
        started = None
    sleep(requested)
    if emit is not None and started is not None:
        try:
            finished = time.monotonic()
            actual_ms = max(0.0, (finished - started) * 1000.0)
            emit(
                "info",
                "run_latency_wait_v1",
                schema_version=SCHEMA_VERSION,
                reason=str(reason or "existing_wait")[:120],
                requested_ms=round(requested * 1000.0, 2),
                actual_ms=round(actual_ms, 2),
                overshoot_ms=round(max(0.0, actual_ms - requested * 1000.0), 2),
                monotonic_clock=True,
                monotonic_ms=round(finished * 1000.0, 3),
                **({"_defer_follow_timing": True} if defer_timing else {}),
                **binding,
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
    def __init__(self, raw: Any, *, kind: str, emit: Callable[..., None] | None, methods: frozenset[str], properties: bool = False):
        object.__setattr__(self, "_raw", raw)
        object.__setattr__(self, "_kind", kind)
        object.__setattr__(self, "_emit", emit)
        object.__setattr__(self, "_methods", methods)
        object.__setattr__(self, "_properties", properties)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        result = self._raw(*args, **kwargs)
        return _TimedProxy(result, kind="uiautomator", emit=self._emit, methods=_UI_METHODS, properties=self._properties)

    def __getattr__(self, name: str) -> Any:
        if self._properties and name in {'info', 'count'}:
            return _dm446_io(lambda: getattr(self._raw, name), name, self._emit)
        attr = getattr(self._raw, name)
        if name == "exists" and callable(attr):
            return _TimedExistsProxy(attr, emit=self._emit)
        if name not in self._methods or not callable(attr):
            return attr
        if self._properties:
            return lambda *args, **kwargs: _dm446_io(lambda: attr(*args, **kwargs), name, self._emit)

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


def instrument_device(device: Any, *, emit: Callable[..., None] | None = None, observe_properties: bool = False) -> Any:
    """Wrap existing calls only. If wrapping cannot be established, return the raw device."""

    if device is None or isinstance(device, _TimedProxy):
        return device
    if not timing_enabled():
        return device
    try:
        methods = _DEVICE_METHODS | {'window_size'} if observe_properties else _DEVICE_METHODS
        return _TimedProxy(device, kind="device", emit=emit, methods=methods, properties=observe_properties)
    except Exception:
        return device
