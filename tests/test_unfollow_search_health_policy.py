import unittest

from unfollow_search_health_policy import SearchSurfaceCircuitBreaker


class UnfollowSearchHealthPolicyTests(unittest.TestCase):
    def test_two_technical_failures_do_not_stop_third_candidate(self) -> None:
        policy = SearchSurfaceCircuitBreaker()
        self.assertFalse(policy.record("search_surface_unhealthy"))
        self.assertFalse(policy.record("search_surface_unhealthy"))
        self.assertFalse(policy.opened)

    def test_third_exact_result_resets_consecutive_failures(self) -> None:
        policy = SearchSurfaceCircuitBreaker()
        policy.record("search_surface_unhealthy")
        policy.record("search_surface_unhealthy")
        self.assertFalse(policy.record("exact_result_visible"))
        self.assertEqual(policy.consecutive_technical_failures, 0)
        self.assertFalse(policy.opened)

    def test_confirmed_not_found_proves_healthy_surface_and_resets(self) -> None:
        policy = SearchSurfaceCircuitBreaker()
        policy.record("search_surface_unhealthy")
        self.assertFalse(policy.record("username_not_found_confirmed"))
        self.assertEqual(policy.consecutive_technical_failures, 0)

    def test_three_consecutive_technical_failures_open_unfollow_only_breaker(self) -> None:
        policy = SearchSurfaceCircuitBreaker()
        policy.record("search_surface_unhealthy")
        policy.record("search_surface_unhealthy")
        self.assertTrue(policy.record("search_surface_unhealthy"))
        self.assertEqual(
            policy.stable_reason,
            "unfollow_search_surface_consecutive_failure_limit_reached",
        )

    def test_first_open_circuit_gets_one_bounded_same_session_recovery(self) -> None:
        policy = SearchSurfaceCircuitBreaker()
        for _ in range(3):
            policy.record("search_surface_unhealthy")
        self.assertTrue(policy.can_attempt_bounded_recovery())
        policy.record_bounded_recovery(True)
        self.assertFalse(policy.opened)
        self.assertEqual(policy.consecutive_technical_failures, 0)
        self.assertFalse(policy.can_attempt_bounded_recovery())
        self.assertTrue(policy.recovery_succeeded)

    def test_failed_bounded_recovery_keeps_circuit_fail_closed(self) -> None:
        policy = SearchSurfaceCircuitBreaker()
        for _ in range(3):
            policy.record("search_surface_unhealthy")
        policy.record_bounded_recovery(False)
        self.assertTrue(policy.opened)
        self.assertFalse(policy.can_attempt_bounded_recovery())

    def test_mixed_healthy_results_prevent_global_failure_accumulation(self) -> None:
        policy = SearchSurfaceCircuitBreaker()
        sequence = [
            "search_surface_unhealthy",
            "username_not_found_confirmed",
            "search_surface_unhealthy",
            "exact_result_visible",
            "search_surface_unhealthy",
        ]
        self.assertFalse(any(policy.record(item) for item in sequence))
        self.assertEqual(policy.total_technical_failures, 3)
        self.assertEqual(policy.consecutive_technical_failures, 1)

    def test_two_confirmed_absent_then_three_exact_results_complete_batch(self) -> None:
        policy = SearchSurfaceCircuitBreaker()
        sequence = [
            "username_not_found_confirmed",
            "username_not_found_confirmed",
            "exact_result_visible",
            "exact_result_visible",
            "exact_result_visible",
        ]
        self.assertEqual([policy.record(item) for item in sequence], [False] * 5)
        self.assertEqual(policy.healthy_outcomes, 5)

    def test_two_unhealthy_candidates_do_not_block_third_exact_candidate(self) -> None:
        policy = SearchSurfaceCircuitBreaker()
        outcomes = [
            policy.record("search_surface_unhealthy"),
            policy.record("search_surface_unhealthy"),
            policy.record("exact_result_visible"),
        ]
        self.assertEqual(outcomes, [False, False, False])
        self.assertEqual(policy.consecutive_technical_failures, 0)
        self.assertFalse(policy.opened)

    def test_candidate_local_timeouts_never_open_global_search_breaker(self) -> None:
        policy = SearchSurfaceCircuitBreaker()
        outcomes = [
            policy.record(
                "search_surface_unhealthy",
                failure_scope="candidate_local",
            )
            for _ in range(6)
        ]
        self.assertEqual(outcomes, [False] * 6)
        self.assertEqual(policy.candidate_local_failures, 6)
        self.assertEqual(policy.consecutive_technical_failures, 0)
        self.assertFalse(policy.opened)


if __name__ == "__main__":
    unittest.main()
