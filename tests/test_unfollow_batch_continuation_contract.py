from pathlib import Path
import unittest

import unfollow_session_orchestrator


class UnfollowBatchContinuationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = Path(unfollow_session_orchestrator.__file__).read_text(
            encoding="utf-8"
        )

    def test_old_two_candidate_global_stop_is_removed(self) -> None:
        self.assertNotIn("max_direct_search_retryable_failures = 2", self.source)
        self.assertNotIn(
            "direct_exact_search_retryable_failure_budget_exhausted",
            self.source,
        )

    def test_candidate_outcomes_use_distinct_v2_persistence(self) -> None:
        self.assertIn("record_unfollow_candidate_availability_v2", self.source)
        self.assertIn('"username_not_found_confirmed"', self.source)
        self.assertIn('"search_surface_unhealthy"', self.source)
        self.assertIn("mark_candidate_technical_hold", self.source)

    def test_only_three_consecutive_technical_failures_open_phase_breaker(self) -> None:
        self.assertIn("SearchSurfaceCircuitBreaker", self.source)
        self.assertIn("record_unfollow_phase_circuit_breaker_v1", self.source)
        self.assertIn("continue_to_next_candidate=not breaker_opened", self.source)

    def test_direct_search_is_armed_only_after_progressive_recovery_and_five(self) -> None:
        self.assertIn("coverage_tracker.search_recovery_attempted", self.source)
        self.assertIn(
            "coverage_tracker.post_recovery_search_scroll_count >= 5",
            self.source,
        )
        self.assertIn(
            '"ui_coverage_budget_exhausted_with_actionable_remaining"',
            self.source,
        )


if __name__ == "__main__":
    unittest.main()
