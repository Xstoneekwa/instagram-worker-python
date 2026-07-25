import unittest

from tests.unfollow_ui_coverage_offline_harness import run_offline_harness
from unfollow_ui_coverage_policy import (
    FollowingCoverageTracker,
    HISTORICAL_ACTION_P90_SECONDS,
    HISTORICAL_VIEWPORT_P90_SECONDS,
    SCHEDULED_SESSION_CLEANUP_RESERVE_SECONDS,
    derive_adaptive_coverage_budget,
    viewport_fingerprint,
)


class UnfollowUiCoveragePolicyTests(unittest.TestCase):
    def test_offline_harness_covers_all_required_scenarios(self) -> None:
        out = run_offline_harness()
        self.assertTrue(out["ok"])
        self.assertEqual(out["scenario_count"], 16)
        self.assertEqual(out["device_runs"], 0)
        self.assertTrue(out["no_infinite_loop"])
        self.assertTrue(out["duplicate_unfollow_guard"])
        self.assertTrue(out["no_false_success"])
        self.assertTrue(out["no_cap_overrun"])
        self.assertTrue(out["cleanup_reserve_respected"])
        for scenario in out["scenarios"].values():
            self.assertGreater(scenario["theoretical_max_loop_steps"], 0)

    def test_budget_is_derived_from_historical_p90_and_cleanup_reserve(self) -> None:
        out = derive_adaptive_coverage_budget(
            quota_remaining=120,
            eligible_remaining=120,
            session_remaining_seconds=3600,
        )
        self.assertEqual(out.estimated_seconds_per_unfollow, HISTORICAL_ACTION_P90_SECONDS)
        self.assertEqual(out.estimated_seconds_per_viewport, HISTORICAL_VIEWPORT_P90_SECONDS)
        self.assertEqual(
            out.minimum_session_cleanup_reserve_seconds,
            SCHEDULED_SESSION_CLEANUP_RESERVE_SECONDS,
        )
        self.assertLessEqual(
            out.max_unfollow_phase_duration_seconds,
            3600 - SCHEDULED_SESSION_CLEANUP_RESERVE_SECONDS,
        )
        self.assertLessEqual(
            out.max_scroll_passes,
            out.required_viewports + out.diagnostic_viewport_allowance - 1,
        )
        self.assertLessEqual(out.adaptive_scroll_budget, out.max_scroll_passes_absolute)
        self.assertEqual(out.budget_formula_version, "handoff_capacity_v3")

    def test_mythyl_fixture_allows_first_scroll_without_all_or_nothing_reservation(self) -> None:
        old_fallback_seconds = 2454
        old_available = old_fallback_seconds - SCHEDULED_SESSION_CLEANUP_RESERVE_SECONDS
        self.assertLess(old_available - (120 * 15) - 75, 0)

        out = derive_adaptive_coverage_budget(
            quota_remaining=120,
            eligible_remaining=199,
            session_remaining_seconds=6 * 60 * 60,
            deadline_source="scheduler_business_action_deadline",
            recovery_reserve_seconds=75,
            navigation_reserve_seconds=30,
        )
        self.assertEqual(out.planned_unfollows, 120)
        self.assertGreater(out.adaptive_scroll_budget, 0)
        self.assertEqual(out.conservative_capacity, 1393)
        self.assertEqual(out.recovery_reserve_seconds, 75)

    def test_viewport_observation_does_not_recalculate_handoff_budget(self) -> None:
        budget = derive_adaptive_coverage_budget(
            quota_remaining=120,
            eligible_remaining=199,
            session_remaining_seconds=6 * 60 * 60,
        )
        tracker = FollowingCoverageTracker(
            planned_usernames={f"candidate_{index}" for index in range(199)},
            quota_target=120,
            budget=budget,
        )
        before = tracker.budget
        decision = tracker.observe_viewport(
            ["visible_1", "visible_2", "visible_3", "visible_4"],
            elapsed_seconds=5.0,
            following_confirmed=True,
        )
        self.assertIsNotNone(decision)
        self.assertEqual(decision.action, "scroll")
        self.assertIs(tracker.budget, before)
        self.assertEqual(tracker.summary()["per_scroll_full_recalculations"], 0)

    def test_lightweight_deadline_guard_is_periodic_and_monotonic(self) -> None:
        budget = derive_adaptive_coverage_budget(
            quota_remaining=120,
            eligible_remaining=199,
            session_remaining_seconds=6 * 60 * 60,
            lightweight_deadline_check_interval_seconds=300,
            lightweight_deadline_check_action_interval=25,
        )
        tracker = FollowingCoverageTracker(
            planned_usernames={f"candidate_{index}" for index in range(199)},
            quota_target=120,
            budget=budget,
        )
        self.assertIsNone(tracker.preflight_decision(elapsed_seconds=0.0))
        self.assertEqual(tracker.lightweight_deadline_checks, 1)
        self.assertIsNone(tracker.preflight_decision(elapsed_seconds=299.0))
        self.assertEqual(tracker.lightweight_deadline_checks, 1)
        self.assertIsNone(tracker.preflight_decision(elapsed_seconds=300.0))
        self.assertEqual(tracker.lightweight_deadline_checks, 2)

    def test_cleanup_recovery_outreach_and_navigation_reserves_bound_capacity(self) -> None:
        out = derive_adaptive_coverage_budget(
            quota_remaining=120,
            eligible_remaining=199,
            session_remaining_seconds=3600,
            recovery_reserve_seconds=75,
            outreach_reserve_seconds=300,
            navigation_reserve_seconds=30,
        )
        expected_seconds = 3600 - 600 - 75 - 300 - 30
        self.assertEqual(out.conservative_capacity, expected_seconds // 15)
        self.assertEqual(out.outreach_reserve_seconds, 300)
        self.assertEqual(out.max_unfollow_phase_duration_seconds, 3600 - 600 - 75 - 300)

    def test_recent_ui_yields_adapt_budget_without_candidates_divided_by_rows(self) -> None:
        sparse = derive_adaptive_coverage_budget(
            quota_remaining=20,
            eligible_remaining=20,
            session_remaining_seconds=3600,
            recent_unique_usernames_per_viewport=7,
            recent_candidates_found_per_viewport=0.5,
        )
        dense = derive_adaptive_coverage_budget(
            quota_remaining=20,
            eligible_remaining=20,
            session_remaining_seconds=3600,
            recent_unique_usernames_per_viewport=7,
            recent_candidates_found_per_viewport=5,
        )
        self.assertGreater(sparse.required_viewports, dense.required_viewports)
        self.assertNotEqual(sparse.required_viewports, 3)
        self.assertEqual(sparse.recent_candidates_found_per_viewport, 0.5)

    def test_fingerprint_is_stable_redacted_and_order_sensitive(self) -> None:
        first = viewport_fingerprint(["@Alpha", "Beta"])
        same = viewport_fingerprint(["alpha", "beta"])
        reordered = viewport_fingerprint(["beta", "alpha"])
        self.assertEqual(first, same)
        self.assertNotEqual(first, reordered)
        self.assertNotIn("alpha", first)
        self.assertEqual(len(first), 20)


if __name__ == "__main__":
    unittest.main()
