import unittest

from tests.unfollow_ui_coverage_offline_harness import run_offline_harness
from unfollow_ui_coverage_policy import (
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
        self.assertEqual(out.budget_formula_version, "adaptive_recent_yield_v2")

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
