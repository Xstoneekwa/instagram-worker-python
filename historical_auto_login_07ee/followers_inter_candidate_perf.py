"""
Session perf state for the inter-candidate cycle:
  post_follow_post_likes_phase_success (N) -> follow_verify_success (N+1).

Instrumentation only — no flow behavior changes.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from logs import log


def _ms_between(t_start: float, t_end: float) -> float:
    if t_start <= 0.0 or t_end <= 0.0 or t_end < t_start:
        return 0.0
    return round((t_end - t_start) * 1000.0, 2)


def _is_placeholder_profile_username(username: str) -> bool:
    u = str(username or "").strip().lstrip("@")
    if not u:
        return True
    if u.startswith("vf_row") or u.startswith("vfp_"):
        return True
    return False


@dataclass
class _SegmentBIterationSnap:
    iteration_index: int = 0
    t_start: float = 0.0
    t_picker_start: float = 0.0
    t_picker_end: float = 0.0
    t_mapping_start: float = 0.0
    t_mapping_end: float = 0.0
    t_scroll_start: float = 0.0
    t_scroll_end: float = 0.0
    t_refresh_after_scroll_end: float = 0.0
    t_pre_picker_loop_detect_start: float = 0.0
    t_pre_picker_loop_detect_end: float = 0.0
    t_pre_picker_committed_revalidate_start: float = 0.0
    t_pre_picker_committed_revalidate_end: float = 0.0
    t_pre_picker_injection_capture_start: float = 0.0
    t_pre_picker_injection_capture_end: float = 0.0

    picker_candidate_count: int = 0
    picker_empty: bool = False
    picker_empty_reason: str = ""
    picker_error: str = ""

    rows_spans_detected: int = 0
    mapped_count: int = 0
    cta_allowed_count: int = 0
    tap_safe_candidate_count: int = 0
    row_mapping_empty_reason: str = ""

    vision_gate_passed: bool = False
    vision_gate_block_reason: str = ""
    visual_follow_button_count: int = 0
    visual_user_rows_detected: int = 0

    evidence_source: str = ""
    scroll_used: bool = False
    scroll_reason: str = ""
    scroll_reason_armed: str = ""

    candidates_injected_count: int = 0
    mapping_ran: bool = False
    picker_ran: bool = False

    internal_gate_skipped: bool = False
    internal_gate_ms: float = 0.0
    picker_core_ms: float = 0.0


@dataclass
class _InterCandidatePerfState:
    active: bool = False
    from_follower_username: str = ""
    to_follower_username: str = ""
    source_profile_username: str = ""
    expected_username_hint: str = ""

    t_cycle_start: float = 0.0
    t_post_return_start: float = 0.0
    t_post_return_success: float = 0.0
    t_candidate_selected: float = 0.0
    t_profile_verified_real: float = 0.0
    t_guard_started: float = 0.0
    t_guard_passed: float = 0.0
    t_open_profile_start: float = 0.0

    t_social_memory_start: float = 0.0
    t_social_memory_end: float = 0.0
    t_profile_settle_start: float = 0.0
    t_profile_settle_end: float = 0.0
    t_private_detect_start: float = 0.0
    t_private_detect_end: float = 0.0

    post_return_detect_calls: int = 0
    open_profile_detect_calls: int = 0
    detect_bucket: str | None = None

    loop_iterations_until_pick: int = 0
    picker_empty_count: int = 0
    exploratory_scrolls_used: int = 0
    picker_refresh_settle_ms: float = 0.0

    counting_loop_iterations: bool = False
    profile_verified_real_username: str = ""

    segment_b_iter: _SegmentBIterationSnap | None = None
    segment_b_empty_reasons: list[str] = field(default_factory=list)
    segment_b_scroll_reasons: list[str] = field(default_factory=list)
    segment_b_total_picker_ms: float = 0.0
    segment_b_total_row_mapping_ms: float = 0.0
    segment_b_total_scroll_ms: float = 0.0
    segment_b_total_refresh_after_scroll_ms: float = 0.0
    segment_b_iteration_perf_emitted: int = 0


_STATE = _InterCandidatePerfState()


def _reset_state() -> None:
    global _STATE
    _STATE = _InterCandidatePerfState()


def _segment_b_window_active() -> bool:
    return bool(
        _STATE.active
        and _STATE.t_post_return_success > 0.0
        and _STATE.t_candidate_selected <= 0.0
        and _STATE.counting_loop_iterations
    )


def _infer_segment_b_iteration_outcome(snap: _SegmentBIterationSnap) -> str:
    if snap.candidates_injected_count > 0:
        return "candidate_selected"
    if snap.scroll_used:
        return "retry_after_scroll"
    if snap.picker_empty and not snap.scroll_used:
        return "empty_no_scroll"
    return "other"


def _emit_segment_b_iteration_perf(*, outcome: str) -> None:
    snap = _STATE.segment_b_iter
    if snap is None or not _segment_b_window_active():
        return
    now = time.monotonic()
    picker_ms = _ms_between(snap.t_picker_start, snap.t_picker_end)
    row_mapping_ms = _ms_between(snap.t_mapping_start, snap.t_mapping_end)
    scroll_ms = _ms_between(snap.t_scroll_start, snap.t_scroll_end)
    refresh_ms = _ms_between(snap.t_scroll_end, snap.t_refresh_after_scroll_end)
    if snap.t_refresh_after_scroll_end <= 0.0 and snap.t_scroll_end > 0.0:
        refresh_ms = 0.0
    iteration_total_ms = _ms_between(snap.t_start, now)

    _STATE.segment_b_total_picker_ms += picker_ms
    _STATE.segment_b_total_row_mapping_ms += row_mapping_ms
    _STATE.segment_b_total_scroll_ms += scroll_ms
    _STATE.segment_b_total_refresh_after_scroll_ms += refresh_ms
    _STATE.segment_b_iteration_perf_emitted += 1

    empty_reason = str(snap.picker_empty_reason or snap.picker_error or "").strip()
    if snap.picker_empty and empty_reason:
        _STATE.segment_b_empty_reasons.append(empty_reason)
    scroll_reason = str(snap.scroll_reason or snap.scroll_reason_armed or "").strip()
    if snap.scroll_used and scroll_reason:
        _STATE.segment_b_scroll_reasons.append(scroll_reason)

    payload: dict[str, Any] = {
        "from_follower_username": _STATE.from_follower_username,
        "to_follower_username": _STATE.expected_username_hint,
        "source_profile_username": _STATE.source_profile_username,
        "iteration_index": int(snap.iteration_index),
        "iteration_total_ms": iteration_total_ms,
        "picker_ms": picker_ms,
        "row_mapping_ms": row_mapping_ms,
        "scroll_ms": scroll_ms,
        "refresh_after_scroll_ms": refresh_ms,
        "pre_picker_loop_detect_ms": _ms_between(
            snap.t_pre_picker_loop_detect_start, snap.t_pre_picker_loop_detect_end
        ),
        "pre_picker_committed_revalidate_ms": _ms_between(
            snap.t_pre_picker_committed_revalidate_start,
            snap.t_pre_picker_committed_revalidate_end,
        ),
        "pre_picker_injection_capture_ms": _ms_between(
            snap.t_pre_picker_injection_capture_start,
            snap.t_pre_picker_injection_capture_end,
        ),
        "picker_candidate_count": int(snap.picker_candidate_count),
        "picker_empty": bool(snap.picker_empty),
        "picker_empty_reason": empty_reason,
        "rows_spans_detected": int(snap.rows_spans_detected),
        "mapped_count": int(snap.mapped_count),
        "cta_allowed_count": int(snap.cta_allowed_count),
        "tap_safe_candidate_count": int(snap.tap_safe_candidate_count),
        "vision_gate_passed": bool(snap.vision_gate_passed),
        "vision_gate_block_reason": str(snap.vision_gate_block_reason or ""),
        "evidence_source": str(snap.evidence_source or "other"),
        "visual_follow_button_count": int(snap.visual_follow_button_count),
        "visual_user_rows_detected": int(snap.visual_user_rows_detected),
        "scroll_used": bool(snap.scroll_used),
        "scroll_reason": scroll_reason,
        "iteration_outcome": str(outcome or _infer_segment_b_iteration_outcome(snap)),
        "picker_ran": bool(snap.picker_ran),
        "mapping_ran": bool(snap.mapping_ran),
        "picker_error": str(snap.picker_error or ""),
        "row_mapping_empty_reason": str(snap.row_mapping_empty_reason or ""),
        "candidates_injected_count": int(snap.candidates_injected_count),
        "internal_gate_skipped": bool(snap.internal_gate_skipped),
        "internal_gate_ms": float(snap.internal_gate_ms),
        "picker_core_ms": float(snap.picker_core_ms),
    }
    try:
        log("info", "followers_segment_b_picker_iteration_perf", **payload)
    except Exception:
        pass
    _STATE.segment_b_iter = None


def _segment_b_start_iteration() -> None:
    if not _segment_b_window_active():
        return
    _STATE.segment_b_iter = _SegmentBIterationSnap(
        iteration_index=int(_STATE.loop_iterations_until_pick),
        t_start=time.monotonic(),
    )


def _segment_b_finalize_current_iteration(*, outcome: str | None = None) -> None:
    if _STATE.segment_b_iter is None:
        return
    oc = outcome or _infer_segment_b_iteration_outcome(_STATE.segment_b_iter)
    _emit_segment_b_iteration_perf(outcome=oc)


def inter_candidate_set_detect_bucket(bucket: str | None) -> None:
    """Bucket: ``post_return`` | ``open_profile`` | None."""
    if _STATE.active:
        _STATE.detect_bucket = bucket


def inter_candidate_count_detect_followers_list() -> None:
    if not _STATE.active:
        return
    b = _STATE.detect_bucket
    if b == "post_return":
        _STATE.post_return_detect_calls += 1
    elif b == "open_profile":
        _STATE.open_profile_detect_calls += 1


def inter_candidate_segment_b_note_evidence_source(*, source: str) -> None:
    if not _segment_b_window_active() or _STATE.segment_b_iter is None:
        return
    _STATE.segment_b_iter.evidence_source = str(source or "other").strip() or "other"


def inter_candidate_segment_b_note_gate(
    *,
    gate_passed: bool,
    gate_block_reason: str = "",
    visual_follow_button_count: int = 0,
    visual_user_rows_detected: int = 0,
) -> None:
    if not _segment_b_window_active() or _STATE.segment_b_iter is None:
        return
    snap = _STATE.segment_b_iter
    snap.vision_gate_passed = bool(gate_passed)
    snap.vision_gate_block_reason = str(gate_block_reason or "")
    try:
        snap.visual_follow_button_count = int(visual_follow_button_count)
        snap.visual_user_rows_detected = int(visual_user_rows_detected)
    except (TypeError, ValueError):
        pass


def _segment_b_mark_phase_start(snap: _SegmentBIterationSnap, field_start: str) -> None:
    t = time.monotonic()
    if getattr(snap, field_start, 0.0) <= 0.0:
        setattr(snap, field_start, t)


def _segment_b_mark_phase_end(snap: _SegmentBIterationSnap, field_end: str) -> None:
    t = time.monotonic()
    if getattr(snap, field_end, 0.0) <= 0.0:
        setattr(snap, field_end, t)


def inter_candidate_segment_b_pre_picker_loop_detect_start() -> None:
    if not _segment_b_window_active() or _STATE.segment_b_iter is None:
        return
    _segment_b_mark_phase_start(_STATE.segment_b_iter, "t_pre_picker_loop_detect_start")


def inter_candidate_segment_b_pre_picker_loop_detect_end() -> None:
    if not _segment_b_window_active() or _STATE.segment_b_iter is None:
        return
    _segment_b_mark_phase_end(_STATE.segment_b_iter, "t_pre_picker_loop_detect_end")


def inter_candidate_segment_b_pre_picker_committed_revalidate_start() -> None:
    if not _segment_b_window_active() or _STATE.segment_b_iter is None:
        return
    _segment_b_mark_phase_start(_STATE.segment_b_iter, "t_pre_picker_committed_revalidate_start")


def inter_candidate_segment_b_pre_picker_committed_revalidate_end() -> None:
    if not _segment_b_window_active() or _STATE.segment_b_iter is None:
        return
    _segment_b_mark_phase_end(_STATE.segment_b_iter, "t_pre_picker_committed_revalidate_end")


def inter_candidate_segment_b_pre_picker_injection_capture_start() -> None:
    if not _segment_b_window_active() or _STATE.segment_b_iter is None:
        return
    _segment_b_mark_phase_start(_STATE.segment_b_iter, "t_pre_picker_injection_capture_start")


def inter_candidate_segment_b_pre_picker_injection_capture_end() -> None:
    if not _segment_b_window_active() or _STATE.segment_b_iter is None:
        return
    _segment_b_mark_phase_end(_STATE.segment_b_iter, "t_pre_picker_injection_capture_end")


def inter_candidate_segment_b_picker_phase_start() -> None:
    if not _segment_b_window_active() or _STATE.segment_b_iter is None:
        return
    snap = _STATE.segment_b_iter
    snap.picker_ran = True
    if snap.t_picker_start <= 0.0:
        snap.t_picker_start = time.monotonic()


def inter_candidate_segment_b_note_picker_result(
    vp_inj: dict[str, Any] | None,
) -> None:
    if not _segment_b_window_active() or _STATE.segment_b_iter is None:
        return
    snap = _STATE.segment_b_iter
    if snap.t_picker_end <= 0.0 and snap.t_picker_start > 0.0:
        snap.t_picker_end = time.monotonic()
    inj = vp_inj if isinstance(vp_inj, dict) else {}
    try:
        snap.picker_candidate_count = int(inj.get("candidate_count") or 0)
    except (TypeError, ValueError):
        snap.picker_candidate_count = 0
    snap.picker_empty = snap.picker_candidate_count <= 0
    snap.picker_empty_reason = str(
        inj.get("empty_reason") or inj.get("reason") or ""
    ).strip()
    snap.picker_error = str(inj.get("picker_error") or "").strip()
    snap.internal_gate_skipped = bool(inj.get("internal_gate_skipped"))
    try:
        snap.internal_gate_ms = float(inj.get("internal_gate_ms") or 0.0)
    except (TypeError, ValueError):
        snap.internal_gate_ms = 0.0
    try:
        snap.picker_core_ms = float(inj.get("picker_core_ms") or 0.0)
    except (TypeError, ValueError):
        snap.picker_core_ms = 0.0
    try:
        spans = inj.get("spans_count")
        if spans is not None:
            snap.rows_spans_detected = int(spans)
    except (TypeError, ValueError):
        pass


def inter_candidate_segment_b_mapping_phase_start() -> None:
    if not _segment_b_window_active() or _STATE.segment_b_iter is None:
        return
    snap = _STATE.segment_b_iter
    snap.mapping_ran = True
    if snap.t_mapping_start <= 0.0:
        snap.t_mapping_start = time.monotonic()


def inter_candidate_segment_b_note_mapping_result(
    row_mapping_diag: dict[str, Any] | None,
) -> None:
    if not _segment_b_window_active() or _STATE.segment_b_iter is None:
        return
    snap = _STATE.segment_b_iter
    if snap.t_mapping_end <= 0.0 and snap.t_mapping_start > 0.0:
        snap.t_mapping_end = time.monotonic()
    diag = row_mapping_diag if isinstance(row_mapping_diag, dict) else {}
    try:
        snap.mapped_count = int(diag.get("mapped_count") or 0)
        snap.cta_allowed_count = int(diag.get("cta_allowed_count") or 0)
        snap.tap_safe_candidate_count = int(snap.mapped_count)
        if snap.rows_spans_detected <= 0:
            snap.rows_spans_detected = int(diag.get("span_count") or 0)
    except (TypeError, ValueError):
        pass
    snap.row_mapping_empty_reason = str(diag.get("row_mapping_empty_reason") or "").strip()


def inter_candidate_segment_b_note_candidates_injected(*, count: int) -> None:
    if not _segment_b_window_active() or _STATE.segment_b_iter is None:
        return
    try:
        _STATE.segment_b_iter.candidates_injected_count = int(count)
    except (TypeError, ValueError):
        pass


def inter_candidate_segment_b_note_scroll_reason_armed(*, reason: str) -> None:
    if not _segment_b_window_active() or _STATE.segment_b_iter is None:
        return
    r = str(reason or "").strip()
    if r:
        _STATE.segment_b_iter.scroll_reason_armed = r


def inter_candidate_segment_b_note_scroll_deferred_from_pick(
    *,
    empty_reason: str = "",
    picker_error: str = "",
    defer_kind: str = "",
) -> None:
    """Map defer branch to a stable scroll_reason label (instrumentation only)."""
    pe = str(picker_error or "").strip()
    er = str(empty_reason or "").strip()
    dk = str(defer_kind or "").strip()
    reason = "other"
    if pe == "vision_validation_rejected" or er == "vision_validation_rejected":
        reason = "vision_rejected"
    elif er == "no_blue_follow_spans" or dk == "zero_follow_spans":
        reason = "no_blue_follow_spans"
    elif dk in ("unsafe_low_follow", "no_tap_safe_committed"):
        reason = "no_tap_safe_candidate"
    elif dk == "vision_rejected_mapping_without_username":
        reason = "vision_rejected_mapping_anonymous"
    elif er in ("no_rows_after_filter",):
        reason = "no_rows_after_filter"
    inter_candidate_segment_b_note_scroll_reason_armed(reason=reason)


def inter_candidate_segment_b_abandon_mapping_anonymous_pick_before_open() -> None:
    """Finalize Segment B iteration as scroll retry without closing the pick window (C1)."""
    if _STATE.segment_b_iter is not None and _segment_b_window_active():
        _segment_b_finalize_current_iteration(outcome="retry_after_scroll")


def inter_candidate_segment_b_scroll_phase_start() -> None:
    if not _segment_b_window_active() or _STATE.segment_b_iter is None:
        return
    snap = _STATE.segment_b_iter
    if snap.t_scroll_start <= 0.0:
        snap.t_scroll_start = time.monotonic()
    if not snap.scroll_reason and snap.scroll_reason_armed:
        snap.scroll_reason = snap.scroll_reason_armed


def inter_candidate_segment_b_note_exploratory_scroll_used(
    *,
    scroll_profile: str = "",
) -> None:
    if not _segment_b_window_active() or _STATE.segment_b_iter is None:
        return
    snap = _STATE.segment_b_iter
    snap.scroll_used = True
    if snap.t_scroll_end <= 0.0 and snap.t_scroll_start > 0.0:
        snap.t_scroll_end = time.monotonic()
    prof = str(scroll_profile or "").strip()
    if prof and not snap.scroll_reason:
        snap.scroll_reason = prof


def inter_candidate_segment_b_note_refresh_after_scroll() -> None:
    if not _segment_b_window_active() or _STATE.segment_b_iter is None:
        return
    snap = _STATE.segment_b_iter
    snap.t_refresh_after_scroll_end = time.monotonic()
    if snap.evidence_source != "post_return_injection":
        snap.evidence_source = "post_scroll_refresh"


def inter_candidate_on_likes_phase_success(
    *,
    follower_username: str,
    source_profile_username: str = "",
) -> None:
    _reset_state()
    _STATE.active = True
    _STATE.from_follower_username = str(follower_username or "").strip().lstrip("@")
    _STATE.source_profile_username = str(source_profile_username or "").strip().lstrip("@")
    _STATE.t_cycle_start = time.monotonic()
    try:
        log(
            "info",
            "followers_inter_candidate_cycle_started",
            from_follower_username=_STATE.from_follower_username,
            source_profile_username=_STATE.source_profile_username,
        )
    except Exception:
        pass


def inter_candidate_on_post_return_ct_started() -> None:
    if not _STATE.active:
        return
    _STATE.t_post_return_start = time.monotonic()
    inter_candidate_set_detect_bucket("post_return")


def inter_candidate_on_post_return_ct_finished() -> None:
    inter_candidate_set_detect_bucket(None)


def inter_candidate_on_post_return_ct_success() -> None:
    if not _STATE.active:
        return
    _STATE.t_post_return_success = time.monotonic()
    _STATE.counting_loop_iterations = True
    _STATE.loop_iterations_until_pick = 0


def inter_candidate_on_picker_refresh_settle(*, settle_s: float) -> None:
    if not _STATE.active:
        return
    try:
        _STATE.picker_refresh_settle_ms += round(max(0.0, float(settle_s)) * 1000.0, 2)
    except (TypeError, ValueError):
        pass


def inter_candidate_on_loop_iteration() -> None:
    if not _STATE.active or not _STATE.counting_loop_iterations:
        return
    if _STATE.t_candidate_selected > 0.0:
        return
    if _STATE.segment_b_iter is not None:
        _segment_b_finalize_current_iteration(outcome=None)
    _STATE.loop_iterations_until_pick += 1
    _segment_b_start_iteration()


def inter_candidate_on_picker_empty() -> None:
    if not _STATE.active:
        return
    _STATE.picker_empty_count += 1


def inter_candidate_on_exploratory_scroll_used() -> None:
    if not _STATE.active:
        return
    _STATE.exploratory_scrolls_used += 1


def inter_candidate_on_candidate_selected(
    *,
    follower_username: str = "",
    resolved_username_hint: str = "",
) -> None:
    if not _STATE.active:
        return
    if _STATE.segment_b_iter is not None and _segment_b_window_active():
        _segment_b_finalize_current_iteration(outcome="candidate_selected")
    _STATE.counting_loop_iterations = False
    _STATE.t_candidate_selected = time.monotonic()
    hint = str(follower_username or resolved_username_hint or "").strip().lstrip("@")
    if hint and not _is_placeholder_profile_username(hint):
        _STATE.expected_username_hint = hint


def inter_candidate_on_open_profile_started() -> None:
    if not _STATE.active:
        return
    if _STATE.t_open_profile_start <= 0.0:
        _STATE.t_open_profile_start = time.monotonic()
    inter_candidate_set_detect_bucket("open_profile")


def inter_candidate_on_open_profile_finished() -> None:
    inter_candidate_set_detect_bucket(None)


def inter_candidate_on_profile_verify_success(*, username: str) -> None:
    if not _STATE.active or _STATE.t_candidate_selected <= 0.0:
        return
    if _STATE.t_profile_verified_real > 0.0:
        return
    un = str(username or "").strip().lstrip("@")
    if _is_placeholder_profile_username(un):
        return
    _STATE.t_profile_verified_real = time.monotonic()
    _STATE.profile_verified_real_username = un


def inter_candidate_on_profile_settle_before_observe() -> None:
    if not _STATE.active:
        return
    if _STATE.t_profile_settle_start <= 0.0:
        _STATE.t_profile_settle_start = time.monotonic()


def inter_candidate_on_profile_observe_after_settle() -> None:
    if not _STATE.active:
        return
    if _STATE.t_profile_settle_end <= 0.0:
        _STATE.t_profile_settle_end = time.monotonic()


def inter_candidate_on_private_profile_detect_started() -> None:
    if not _STATE.active:
        return
    if _STATE.t_private_detect_start <= 0.0:
        _STATE.t_private_detect_start = time.monotonic()


def inter_candidate_on_private_profile_detect_finished() -> None:
    if not _STATE.active:
        return
    if _STATE.t_private_detect_end <= 0.0:
        _STATE.t_private_detect_end = time.monotonic()


def inter_candidate_on_social_memory_check_started() -> None:
    if not _STATE.active:
        return
    if _STATE.t_social_memory_start <= 0.0:
        _STATE.t_social_memory_start = time.monotonic()


def inter_candidate_on_social_memory_loaded() -> None:
    if not _STATE.active:
        return
    if _STATE.t_social_memory_end <= 0.0:
        _STATE.t_social_memory_end = time.monotonic()


def inter_candidate_on_screen_guard_started() -> None:
    if not _STATE.active:
        return
    if _STATE.t_guard_started <= 0.0:
        _STATE.t_guard_started = time.monotonic()


def inter_candidate_on_screen_guard_passed() -> None:
    if not _STATE.active:
        return
    if _STATE.t_guard_passed <= 0.0:
        _STATE.t_guard_passed = time.monotonic()


def _build_summary_payload(
    *,
    target_username: str,
    verify_phase_ms: float | None,
) -> dict[str, Any]:
    now = time.monotonic()
    t0 = _STATE.t_cycle_start
    t_a = _STATE.t_post_return_success or _STATE.t_post_return_start
    t_b = _STATE.t_candidate_selected
    t_c = _STATE.t_profile_verified_real
    t_d = _STATE.t_guard_passed
    t_e = now

    post_return_ct_ms = _ms_between(_STATE.t_post_return_start, _STATE.t_post_return_success)
    open_profile_ms = 0.0
    if _STATE.t_open_profile_start > 0.0 and t_c > 0.0:
        open_profile_ms = _ms_between(_STATE.t_open_profile_start, t_c)
    elif _STATE.t_open_profile_start > 0.0 and t_b > 0.0:
        open_profile_ms = _ms_between(_STATE.t_open_profile_start, t_b)

    social_memory_pre_follow_ms = _ms_between(
        _STATE.t_social_memory_start, _STATE.t_social_memory_end
    )
    profile_settle_ms = _ms_between(
        _STATE.t_profile_settle_start, _STATE.t_profile_settle_end
    )
    private_profile_detect_ms = _ms_between(
        _STATE.t_private_detect_start, _STATE.t_private_detect_end
    )
    screen_guard_ms = _ms_between(_STATE.t_guard_started, _STATE.t_guard_passed)

    follow_verify_phase_ms = 0.0
    if verify_phase_ms is not None:
        try:
            follow_verify_phase_ms = round(float(verify_phase_ms), 2)
        except (TypeError, ValueError):
            pass

    segment_a = _ms_between(t0, t_a) if t_a > 0.0 else 0.0
    segment_b = _ms_between(t_a, t_b) if t_a > 0.0 and t_b > 0.0 else 0.0
    segment_c = _ms_between(t_b, t_c) if t_b > 0.0 and t_c > 0.0 else 0.0
    segment_d = _ms_between(t_c, t_d) if t_c > 0.0 and t_d > 0.0 else 0.0
    segment_e = _ms_between(t_d, t_e) if t_d > 0.0 else 0.0

    payload: dict[str, Any] = {
        "from_follower_username": _STATE.from_follower_username,
        "to_follower_username": str(target_username or "").strip().lstrip("@"),
        "source_profile_username": _STATE.source_profile_username,
        "inter_candidate_total_ms": _ms_between(t0, now),
        "segment_a_post_like_to_ct_list_ms": segment_a,
        "segment_b_ct_list_to_candidate_selected_ms": segment_b,
        "segment_c_selected_to_profile_verified_ms": segment_c,
        "segment_d_profile_verified_to_guard_passed_ms": segment_d,
        "segment_e_guard_passed_to_follow_verified_ms": segment_e,
        "post_return_ct_ms": post_return_ct_ms,
        "post_return_detect_calls": int(_STATE.post_return_detect_calls),
        "loop_iterations_until_pick": int(_STATE.loop_iterations_until_pick),
        "picker_empty_count": int(_STATE.picker_empty_count),
        "exploratory_scrolls_used": int(_STATE.exploratory_scrolls_used),
        "picker_refresh_settle_ms": round(float(_STATE.picker_refresh_settle_ms), 2),
        "open_profile_ms": open_profile_ms,
        "open_profile_detect_calls": int(_STATE.open_profile_detect_calls),
        "social_memory_pre_follow_ms": social_memory_pre_follow_ms,
        "profile_settle_ms": profile_settle_ms,
        "private_profile_detect_ms": private_profile_detect_ms,
        "screen_guard_ms": screen_guard_ms,
        "follow_verify_phase_ms": follow_verify_phase_ms,
        "profile_verified_username": _STATE.profile_verified_real_username,
        "outcome": "success",
        "segment_b_iteration_count": int(_STATE.segment_b_iteration_perf_emitted),
        "segment_b_empty_reasons": list(_STATE.segment_b_empty_reasons),
        "segment_b_scroll_reasons": list(_STATE.segment_b_scroll_reasons),
        "segment_b_total_picker_ms": round(float(_STATE.segment_b_total_picker_ms), 2),
        "segment_b_total_row_mapping_ms": round(
            float(_STATE.segment_b_total_row_mapping_ms), 2
        ),
        "segment_b_total_scroll_ms": round(float(_STATE.segment_b_total_scroll_ms), 2),
        "segment_b_total_refresh_after_scroll_ms": round(
            float(_STATE.segment_b_total_refresh_after_scroll_ms), 2
        ),
    }
    return payload


def inter_candidate_on_follow_verify_success(
    *,
    target_username: str,
    verify_phase_ms: float | None = None,
) -> None:
    if not _STATE.active or _STATE.t_cycle_start <= 0.0:
        return
    payload = _build_summary_payload(
        target_username=target_username,
        verify_phase_ms=verify_phase_ms,
    )
    try:
        log("info", "followers_inter_candidate_cycle_completed", **payload)
    except Exception:
        pass
    try:
        log("info", "followers_inter_candidate_cycle_perf_summary", **payload)
    except Exception:
        pass
    _reset_state()
