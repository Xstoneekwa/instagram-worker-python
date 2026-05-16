"""
Session perf state for the inter-candidate cycle:
  post_follow_post_likes_phase_success (N) -> follow_verify_success (N+1).

Instrumentation only — no flow behavior changes.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
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


_STATE = _InterCandidatePerfState()


def _reset_state() -> None:
    global _STATE
    _STATE = _InterCandidatePerfState()


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
    _STATE.loop_iterations_until_pick += 1


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

    return {
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
    }


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
