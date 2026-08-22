from __future__ import annotations

import inspect
import unittest

import unfollow_session_orchestrator as orchestrator
from unfollow_session_completion_policy import (
    UnfollowSessionCompletionPolicy,
    healthy_session_target,
)


class UnfollowOneHealthySessionContractTests(unittest.TestCase):
    def test_mythyl_53_eligible_cap120_exhausts_53_in_one_healthy_session(self) -> None:
        self.assertEqual(healthy_session_target(53, 120), 53)

    def test_loriele_21_eligible_cap120_exhausts_21_in_one_healthy_session(self) -> None:
        self.assertEqual(healthy_session_target(21, 120), 21)

    def test_growth_80_eligible_cap80_reaches_80_in_one_healthy_session(self) -> None:
        self.assertEqual(healthy_session_target(80, 80), 80)

    def test_pro_120_eligible_cap120_reaches_120_in_one_healthy_session(self) -> None:
        self.assertEqual(healthy_session_target(120, 120), 120)

    def test_premium_120_eligible_cap120_reaches_120_in_one_healthy_session(self) -> None:
        self.assertEqual(healthy_session_target(120, 120), 120)

    def test_recoverable_private_profile_does_not_kill_session(self) -> None:
        policy = UnfollowSessionCompletionPolicy()
        first = policy.record_candidate_failure(
            "private_candidate",
            safe_state_restored=True,
            replay_forbidden=True,
        )
        self.assertTrue(first.continue_session)
        self.assertFalse(first.global_circuit_open)
        policy.record_verified_success("next_candidate")
        self.assertEqual(policy.consecutive_candidate_failures, 0)

    def test_recoverable_cta_probe_failure_does_not_kill_session(self) -> None:
        policy = UnfollowSessionCompletionPolicy()
        first = policy.record_candidate_failure(
            "wide_cta_candidate",
            safe_state_restored=True,
        )
        self.assertTrue(first.continue_session)
        self.assertTrue(first.retry_in_same_session)

    def test_candidate_recovery_budget_does_not_exhaust_global_session(self) -> None:
        policy = UnfollowSessionCompletionPolicy()
        policy.record_candidate_failure("one", safe_state_restored=True)
        exhausted = policy.record_candidate_failure("one", safe_state_restored=True)
        self.assertTrue(exhausted.candidate_recovery_exhausted)
        self.assertTrue(exhausted.continue_session)
        self.assertFalse(exhausted.global_circuit_open)

    def test_success_resets_consecutive_recovery_counter(self) -> None:
        policy = UnfollowSessionCompletionPolicy()
        policy.record_candidate_failure("one", safe_state_restored=True)
        policy.record_candidate_failure("two", safe_state_restored=True)
        policy.record_verified_success("healthy")
        after_reset = policy.record_candidate_failure("three", safe_state_restored=True)
        self.assertEqual(policy.consecutive_candidate_failures, 1)
        self.assertTrue(after_reset.continue_session)
        self.assertEqual(policy.successful_resets, 1)

    def test_end_of_list_searches_remaining_plan_in_same_session(self) -> None:
        policy = UnfollowSessionCompletionPolicy()
        policy.record_candidate_failure("remaining", safe_state_restored=True)
        retry = policy.begin_retry_generation({"remaining"}, {"remaining"})
        self.assertEqual(retry, {"remaining"})
        self.assertEqual(policy.retry_generations_started, 1)

    def test_plan_remaining_prevents_normal_terminalization(self) -> None:
        source = inspect.getsource(orchestrator._run_real_unfollow_multi_loop)
        partial_branch = source.split('if hybrid.mode == "partial_resumable":', 1)[1]
        self.assertLess(
            partial_branch.index("begin_retry_generation"),
            partial_branch.index("return emit_final"),
        )

    def test_three_consecutive_systemic_failures_open_global_circuit(self) -> None:
        policy = UnfollowSessionCompletionPolicy()
        self.assertFalse(
            policy.record_candidate_failure(
                "one", safe_state_restored=True, failure_scope="global"
            ).global_circuit_open
        )
        self.assertFalse(
            policy.record_candidate_failure(
                "two", safe_state_restored=True, failure_scope="global"
            ).global_circuit_open
        )
        self.assertTrue(
            policy.record_candidate_failure(
                "three", safe_state_restored=True, failure_scope="global"
            ).global_circuit_open
        )

    def test_three_unrelated_candidate_local_failures_do_not_stop_later_success(self) -> None:
        policy = UnfollowSessionCompletionPolicy()
        decisions = [
            policy.record_candidate_failure(
                username,
                safe_state_restored=True,
                failure_scope="candidate_local",
            )
            for username in ("one", "two", "three")
        ]
        self.assertTrue(all(item.continue_session for item in decisions))
        self.assertFalse(any(item.global_circuit_open for item in decisions))
        policy.record_verified_success("four")
        self.assertEqual(policy.consecutive_global_failures, 0)

    def test_one_bounded_global_recovery_resets_s1_and_cannot_repeat(self) -> None:
        policy = UnfollowSessionCompletionPolicy()
        for username in ("one", "two", "three"):
            policy.record_candidate_failure(
                username, safe_state_restored=True, failure_scope="global"
            )
        self.assertTrue(
            policy.record_bounded_global_recovery(safe_state_restored=True)
        )
        self.assertEqual(policy.consecutive_candidate_failures, 0)
        self.assertFalse(
            policy.record_bounded_global_recovery(safe_state_restored=True)
        )

    def test_terminal_not_found_is_removed_from_active_plan_and_persisted(self) -> None:
        source = inspect.getsource(orchestrator._run_real_unfollow_multi_loop)
        terminal = source.split(
            'if classification == "username_not_found_confirmed":', 1
        )[1].split("direct_search_retryable_failures += 1", 1)[0]
        self.assertIn("record_unfollow_candidate_not_found", terminal)
        self.assertIn("max_attempts=2", terminal)
        self.assertIn("terminal_not_found", terminal)
        self.assertIn("mark_candidate_unavailable", terminal)
        self.assertIn("backlog_actionable=False", terminal)


if __name__ == "__main__":
    unittest.main()
