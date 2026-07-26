"""Deterministic, device-free replay scenarios for Following UI coverage."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from unfollow_ui_coverage_policy import (
    FollowingCoverageTracker,
    derive_adaptive_coverage_budget,
)


def _names(start: int, count: int) -> list[str]:
    return [f"fixture_user_{index:03d}" for index in range(start, start + count)]


def _tracker(
    planned: set[str],
    *,
    quota: int,
    session_seconds: int = 3600,
) -> FollowingCoverageTracker:
    budget = derive_adaptive_coverage_budget(
        quota_remaining=quota,
        eligible_remaining=len(planned),
        session_remaining_seconds=session_seconds,
    )
    return FollowingCoverageTracker(budget, planned, quota)


def run_offline_harness() -> dict[str, Any]:
    results: dict[str, Any] = {}
    # 1. No DB candidate: skip before opening/scrolling the UI.
    tracker = _tracker(set(), quota=120)
    decision = tracker.preflight_decision(elapsed_seconds=0)
    assert decision and decision.stop_reason == "eligible_targets_exhausted"
    assert tracker.scroll_passes_used == 0
    results["01_zero_db_candidate_no_scroll"] = tracker.summary()

    # 2. Seven candidates all fit in the first viewport.
    tracker = _tracker(set(_names(0, 7)), quota=7)
    decision = tracker.observe_viewport(_names(0, 7), elapsed_seconds=0, following_confirmed=True)
    assert decision.action == "act"
    for username in _names(0, 7):
        tracker.mark_action_verified(username)
    assert tracker.preflight_decision(elapsed_seconds=7).stop_reason == "unfollow_quota_reached"
    assert tracker.scroll_passes_used == 0
    results["02_seven_candidates_first_viewport"] = tracker.summary()

    # 3. Candidates dispersed across several viewports.
    tracker = _tracker({"fixture_user_002", "fixture_user_011", "fixture_user_020"}, quota=3)
    for index, viewport in enumerate((_names(0, 7), _names(7, 7), _names(14, 7))):
        decision = tracker.observe_viewport(viewport, elapsed_seconds=index * 4, following_confirmed=True)
        assert decision.action == "act"
        match = sorted(set(viewport).intersection(tracker.remaining_planned_usernames))[0]
        tracker.mark_action_verified(match)
        if index < 2:
            assert tracker.scroll_budget_decision() is None
            tracker.mark_scroll(moved=True)
    assert tracker.preflight_decision(elapsed_seconds=12).stop_reason == "unfollow_quota_reached"
    results["03_candidates_dispersed"] = tracker.summary()

    # 4. Normal overlap does not duplicate usernames or actions.
    tracker = _tracker(set(_names(0, 14)), quota=2)
    tracker.observe_viewport(_names(0, 7), elapsed_seconds=0, following_confirmed=True)
    tracker.mark_action_verified("fixture_user_000")
    tracker.mark_scroll(moved=True)
    decision = tracker.observe_viewport(_names(4, 7), elapsed_seconds=4, following_confirmed=True)
    assert decision.new_usernames_count == 4
    tracker.mark_action_verified("fixture_user_007")
    assert tracker.summary()["unique_usernames_observed"] == 11
    results["04_normal_overlap"] = tracker.summary()

    # 5. An identical viewport after scroll is bounded by fingerprints.
    tracker = _tracker(set(_names(100, 35)), quota=35)
    base = _names(0, 7)
    decision = tracker.observe_viewport(base, elapsed_seconds=0, following_confirmed=True)
    for index in range(tracker.budget.max_repeated_fingerprints):
        tracker.mark_scroll(moved=False)
        decision = tracker.observe_viewport(base, elapsed_seconds=(index + 1) * 3, following_confirmed=True)
    assert decision.action == "recover"
    assert decision.stop_reason == "ui_repeated_viewport_limit"
    assert tracker.mark_recovery(succeeded=False).stop_reason == "ui_recovery_budget_exhausted"
    results["05_identical_viewport"] = tracker.summary()

    # 6. Geometric scroll without any new username stops as no progress.
    tracker = _tracker(set(_names(100, 35)), quota=35)
    base = _names(0, 7)
    tracker.observe_viewport(base, elapsed_seconds=0, following_confirmed=True)
    decision = None
    for index in range(tracker.budget.max_consecutive_no_progress_viewports):
        tracker.mark_scroll(moved=False)
        rotated = base[index % len(base):] + base[:index % len(base)]
        decision = tracker.observe_viewport(rotated, elapsed_seconds=(index + 1) * 3, following_confirmed=True)
    assert decision and decision.action == "recover"
    assert decision.stop_reason in {"ui_no_progress", "ui_repeated_viewport_limit"}
    results["06_scroll_without_motion"] = tracker.summary()

    # 7. A -> B -> A across successful scroll generations is not a false
    # phase-global repetition stop; the independent scroll budget remains the
    # hard bound in runtime.
    tracker = _tracker(set(_names(30, 20)), quota=20)
    viewport_a, viewport_b = _names(0, 7), _names(7, 7)
    decision = tracker.observe_viewport(viewport_a, elapsed_seconds=0, following_confirmed=True)
    for index in range(1, 10):
        scroll_decision = tracker.mark_scroll(moved=True)
        if scroll_decision is not None:
            decision = scroll_decision
            break
        decision = tracker.observe_viewport(viewport_b if index % 2 else viewport_a, elapsed_seconds=index * 3, following_confirmed=True)
        if decision.action == "stop":
            break
    assert decision.action in {"scroll", "stop"}
    assert tracker.consecutive_stagnation_count == 0
    results["07_cycle_a_b_a"] = tracker.summary()

    # 8. A real list end after the plan is empty is truthful exhaustion.
    tracker = _tracker({"fixture_user_000"}, quota=2)
    tracker.observe_viewport(["fixture_user_000"], elapsed_seconds=0, following_confirmed=True)
    tracker.mark_action_verified("fixture_user_000")
    assert tracker.preflight_decision(elapsed_seconds=1).stop_reason == "eligible_targets_exhausted"
    results["08_real_end_of_list"] = tracker.summary()

    # 9. UI end with DB candidates remaining never claims eligible exhaustion.
    tracker = _tracker(set(_names(20, 7)), quota=7)
    decision = tracker.observe_viewport(_names(0, 7), elapsed_seconds=0, following_confirmed=True, end_of_list=True)
    assert decision.stop_reason == "ui_end_of_list_with_candidates_unresolved"
    results["09_candidates_unresolved_at_end"] = tracker.summary()

    # 10. Quota reached before list end stops immediately.
    tracker = _tracker(set(_names(0, 14)), quota=1)
    tracker.observe_viewport(_names(0, 7), elapsed_seconds=0, following_confirmed=True)
    tracker.mark_action_verified("fixture_user_000")
    assert tracker.preflight_decision(elapsed_seconds=1).stop_reason == "unfollow_quota_reached"
    results["10_quota_before_end"] = tracker.summary()

    # 11. T-10 cleanup reserve leaves no action time.
    tracker = _tracker(set(_names(0, 7)), quota=7, session_seconds=599)
    assert tracker.preflight_decision(elapsed_seconds=0).stop_reason == "session_time_budget_exhausted"
    results["11_time_budget_nearly_exhausted"] = tracker.summary()

    # 12. UI drift recovers once and resumes only after Following proof.
    tracker = _tracker(set(_names(0, 14)), quota=2)
    assert tracker.observe_viewport([], elapsed_seconds=0, following_confirmed=False).action == "recover"
    assert tracker.mark_recovery(succeeded=True) is None
    assert tracker.observe_viewport(_names(0, 7), elapsed_seconds=2, following_confirmed=True).action == "act"
    results["12_ui_drift_recovery_success"] = tracker.summary()

    # 13. Repeated recovery attempts exhaust their evidence-derived budget.
    tracker = _tracker(set(_names(0, 14)), quota=2)
    for _ in range(tracker.budget.max_viewport_recoveries):
        assert tracker.observe_viewport([], elapsed_seconds=0, following_confirmed=False).action == "recover"
        assert tracker.mark_recovery(succeeded=True) is None
    decision = tracker.observe_viewport([], elapsed_seconds=1, following_confirmed=False)
    assert decision.stop_reason == "ui_recovery_budget_exhausted"
    results["13_recovery_repeated_abandon"] = tracker.summary()

    # 14. Unsafe markers always win before any action.
    tracker = _tracker(set(_names(0, 7)), quota=7)
    decision = tracker.observe_viewport(_names(0, 7), elapsed_seconds=0, following_confirmed=True, unsafe_marker=True)
    assert decision.stop_reason == "unsafe_marker_detected"
    assert not tracker.verified_usernames
    results["14_unsafe_marker"] = tracker.summary()

    # 15. 120 candidates can complete without duplicate actions or cap overrun.
    tracker = _tracker(set(_names(0, 120)), quota=120)
    for index in range(0, 120, 7):
        viewport = _names(index, min(7, 120 - index))
        decision = tracker.observe_viewport(viewport, elapsed_seconds=index * 2, following_confirmed=True)
        assert decision.action == "act"
        for username in viewport:
            tracker.mark_action_verified(username)
        if index + 7 < 120:
            assert tracker.scroll_budget_decision() is None
            tracker.mark_scroll(moved=True)
    assert len(tracker.verified_usernames) == 120
    assert tracker.preflight_decision(elapsed_seconds=250).stop_reason == "unfollow_quota_reached"
    results["15_one_hundred_twenty_without_duplicate"] = tracker.summary()

    # 16. DB plan exhausted before a larger cap stops without extra scrolling.
    tracker = _tracker(set(_names(0, 3)), quota=120)
    tracker.observe_viewport(_names(0, 7), elapsed_seconds=0, following_confirmed=True)
    for username in _names(0, 3):
        tracker.mark_action_verified(username)
    assert tracker.preflight_decision(elapsed_seconds=3).stop_reason == "eligible_targets_exhausted"
    assert tracker.scroll_passes_used == 0
    results["16_plan_exhausted_before_cap"] = tracker.summary()

    # Contract proofs shared across scenarios.
    duplicate_guard = _tracker({"fixture_user_000"}, quota=2)
    duplicate_guard.mark_action_verified("fixture_user_000")
    try:
        duplicate_guard.mark_action_verified("fixture_user_000")
    except ValueError as exc:
        assert str(exc) == "duplicate_or_unplanned_unfollow"
    else:
        raise AssertionError("duplicate action guard did not fire")

    budget_probe = _tracker(set(_names(0, 120)), quota=120)
    budget_probe.budget = replace(budget_probe.budget, max_scroll_passes=1)
    budget_probe.mark_scroll(moved=True)
    decision = budget_probe.scroll_budget_decision()
    assert decision and decision.stop_reason == "ui_coverage_budget_exhausted"

    return {
        "ok": True,
        "scenario_count": len(results),
        "device_runs": 0,
        "no_infinite_loop": True,
        "duplicate_unfollow_guard": True,
        "no_false_success": all(
            scenario.get("stop_reason") != "eligible_targets_exhausted"
            or scenario.get("eligible_db_remaining") == 0
            for scenario in results.values()
        ),
        "no_cap_overrun": all(
            scenario.get("verified_unique_count", 0)
            <= scenario.get("effective_unfollow_limit", 0)
            for scenario in results.values()
        ),
        "cleanup_reserve_respected": all(
            scenario.get("minimum_session_cleanup_reserve_seconds")
            == 10 * 60
            for scenario in results.values()
        ),
        "scenarios": results,
    }
