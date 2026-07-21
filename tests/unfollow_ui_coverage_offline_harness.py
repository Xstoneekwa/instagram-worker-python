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

    # 1. Normal multi-viewport progress.
    planned = set(_names(0, 21))
    tracker = _tracker(planned, quota=3)
    for index, viewport in enumerate((_names(0, 7), _names(7, 7), _names(14, 7))):
        decision = tracker.observe_viewport(
            viewport, elapsed_seconds=index * 4, following_confirmed=True
        )
        assert decision.action == "act"
        tracker.mark_action_verified(viewport[0])
        if index < 2:
            assert tracker.scroll_budget_decision() is None
            tracker.mark_scroll(moved=True)
    decision = tracker.preflight_decision(elapsed_seconds=15)
    assert decision and decision.stop_reason == "unfollow_quota_reached"
    results["normal_progress"] = tracker.summary()

    # 2. Repeated usernames across viewports still count only once.
    planned = set(_names(0, 14))
    tracker = _tracker(planned, quota=2)
    tracker.observe_viewport(_names(0, 7), elapsed_seconds=0, following_confirmed=True)
    tracker.mark_action_verified("fixture_user_000")
    tracker.mark_scroll(moved=True)
    decision = tracker.observe_viewport(
        _names(4, 7), elapsed_seconds=4, following_confirmed=True
    )
    assert decision.new_usernames_count == 4
    tracker.mark_action_verified("fixture_user_007")
    assert tracker.summary()["unique_usernames_observed"] == 11
    results["overlapping_usernames"] = tracker.summary()

    # 3. Scrolls can move geometrically while yielding no new usernames.
    planned = set(_names(0, 14))
    tracker = _tracker(planned, quota=14)
    base = _names(0, 7)
    tracker.observe_viewport(base, elapsed_seconds=0, following_confirmed=True)
    tracker.mark_scroll(moved=False)
    tracker.observe_viewport(base[1:] + base[:1], elapsed_seconds=3, following_confirmed=True)
    tracker.mark_scroll(moved=False)
    decision = tracker.observe_viewport(
        base[2:] + base[:2], elapsed_seconds=6, following_confirmed=True
    )
    assert decision.stop_reason == "ui_no_progress"
    results["scroll_without_progress"] = tracker.summary()

    # 4. Identical viewport fingerprint is bounded independently.
    planned = set(_names(0, 35))
    tracker = _tracker(planned, quota=35)
    base = _names(0, 7)
    decision = tracker.observe_viewport(base, elapsed_seconds=0, following_confirmed=True)
    for index in range(tracker.budget.max_repeated_fingerprints):
        tracker.mark_scroll(moved=False)
        decision = tracker.observe_viewport(
            base, elapsed_seconds=(index + 1) * 3, following_confirmed=True
        )
    assert decision.stop_reason == "ui_repeated_viewport_limit"
    results["identical_viewport"] = tracker.summary()

    # 5. A real list end with no DB supply remaining is truthful exhaustion.
    tracker = _tracker({"fixture_user_000"}, quota=2)
    tracker.observe_viewport(
        ["fixture_user_000"], elapsed_seconds=0, following_confirmed=True
    )
    tracker.mark_action_verified("fixture_user_000")
    decision = tracker.preflight_decision(elapsed_seconds=1)
    assert decision and decision.stop_reason == "eligible_targets_exhausted"
    results["real_end_of_list"] = tracker.summary()

    # 6. End of UI coverage while DB candidates remain must not claim exhaustion.
    tracker = _tracker(set(_names(20, 7)), quota=7)
    decision = tracker.observe_viewport(
        _names(0, 7),
        elapsed_seconds=0,
        following_confirmed=True,
        end_of_list=True,
    )
    assert decision.stop_reason == "ui_end_of_list_with_candidates_unresolved"
    results["db_candidates_outside_coverage"] = tracker.summary()

    # 7. Navigation drift gets one bounded recovery and resumes only after proof.
    tracker = _tracker(set(_names(0, 14)), quota=2)
    decision = tracker.observe_viewport([], elapsed_seconds=0, following_confirmed=False)
    assert decision.action == "recover"
    assert tracker.mark_recovery(succeeded=True) is None
    decision = tracker.observe_viewport(
        _names(0, 7), elapsed_seconds=2, following_confirmed=True
    )
    assert decision.action == "act"
    results["navigation_drift_recovery"] = tracker.summary()

    # 8. T-10 cleanup reserve leaves no business-action time.
    tracker = _tracker(set(_names(0, 7)), quota=7, session_seconds=599)
    decision = tracker.preflight_decision(elapsed_seconds=0)
    assert decision and decision.stop_reason == "session_time_budget_exhausted"
    results["session_time_low"] = tracker.summary()

    # 9. Quota wins before UI end.
    tracker = _tracker(set(_names(0, 14)), quota=1)
    tracker.observe_viewport(_names(0, 7), elapsed_seconds=0, following_confirmed=True)
    tracker.mark_action_verified("fixture_user_000")
    decision = tracker.preflight_decision(elapsed_seconds=1)
    assert decision and decision.stop_reason == "unfollow_quota_reached"
    results["quota_reached"] = tracker.summary()

    # 10. Empty DB plan is the only direct eligible_targets_exhausted path.
    tracker = _tracker(set(), quota=120)
    decision = tracker.preflight_decision(elapsed_seconds=0)
    assert decision and decision.stop_reason == "eligible_targets_exhausted"
    results["candidates_exhausted"] = tracker.summary()

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
        "scenarios": results,
    }
