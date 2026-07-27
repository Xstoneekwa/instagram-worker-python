import unittest

from account_session_resume_engine import build_account_session_resume_plan
from tests.unfollow_ui_coverage_offline_harness import run_offline_harness
from unfollow_ui_coverage_policy import (
    FollowingCoverageTracker,
    HISTORICAL_ACTION_P90_SECONDS,
    HISTORICAL_VIEWPORT_P90_SECONDS,
    SCHEDULED_SESSION_CLEANUP_RESERVE_SECONDS,
    UNFOLLOW_POST_RECOVERY_STAGNATION_LIMIT,
    UNFOLLOW_POST_RECOVERY_SEARCH_SCROLL_LIMIT,
    UNFOLLOW_PRE_RECOVERY_STAGNATION_LIMIT,
    UNFOLLOW_PRE_RECOVERY_SEARCH_SCROLL_LIMIT,
    build_unfollow_outcome,
    derive_adaptive_coverage_budget,
    viewport_fingerprint,
)


class UnfollowUiCoveragePolicyTests(unittest.TestCase):
    @staticmethod
    def _progressive_search_tracker(candidate_scroll: int) -> tuple[FollowingCoverageTracker, object]:
        budget = derive_adaptive_coverage_budget(
            quota_remaining=35,
            eligible_remaining=35,
            session_remaining_seconds=6 * 60 * 60,
        )
        tracker = FollowingCoverageTracker(
            budget,
            {"planned_candidate", *{f"planned_{index}" for index in range(34)}},
            35,
        )
        tracker.observe_viewport(
            ["initial_unrelated"],
            elapsed_seconds=0,
            following_confirmed=True,
        )
        decision = None
        for scroll_index in range(1, candidate_scroll + 1):
            tracker.mark_scroll(moved=True)
            visible = (
                ["planned_candidate"]
                if scroll_index == candidate_scroll
                else [f"unrelated_{scroll_index}"]
            )
            decision = tracker.observe_viewport(
                visible,
                elapsed_seconds=float(scroll_index),
                following_confirmed=True,
            )
            if decision.action == "recover":
                tracker.mark_recovery(succeeded=True, progress_proved=True)
        return tracker, decision

    def test_first_candidate_on_fifth_progressive_scroll(self) -> None:
        tracker, decision = self._progressive_search_tracker(5)
        self.assertEqual(decision.action, "act")
        self.assertEqual(tracker.search_scroll_count, 0)
        self.assertFalse(tracker.search_recovery_attempted)

    def test_first_candidate_on_tenth_progressive_scroll_needs_no_recovery(self) -> None:
        tracker, decision = self._progressive_search_tracker(10)
        self.assertEqual(decision.action, "act")
        self.assertFalse(tracker.search_recovery_attempted)
        self.assertEqual(tracker.search_scroll_count, 0)

    def test_first_candidate_on_fifteenth_progressive_scroll_after_recovery(self) -> None:
        tracker, decision = self._progressive_search_tracker(15)
        self.assertEqual(decision.action, "act")
        self.assertEqual(tracker.viewport_recoveries_used, 1)
        self.assertEqual(tracker.search_scroll_count, 0)

    def test_fifteen_progressive_scrolls_without_candidate_stop_resumable(self) -> None:
        budget = derive_adaptive_coverage_budget(
            quota_remaining=35,
            eligible_remaining=35,
            session_remaining_seconds=6 * 60 * 60,
        )
        tracker = FollowingCoverageTracker(
            budget,
            {f"planned_{index}" for index in range(35)},
            35,
        )
        tracker.observe_viewport(["initial"], elapsed_seconds=0, following_confirmed=True)
        decision = None
        for scroll_index in range(1, 16):
            tracker.mark_scroll(moved=True)
            decision = tracker.observe_viewport(
                [f"unrelated_{scroll_index}"],
                elapsed_seconds=float(scroll_index),
                following_confirmed=True,
            )
            if decision.action == "recover":
                self.assertEqual(scroll_index, UNFOLLOW_PRE_RECOVERY_SEARCH_SCROLL_LIMIT)
                self.assertIsNone(tracker.mark_recovery(succeeded=True, progress_proved=True))
        self.assertEqual(decision.action, "stop")
        self.assertEqual(decision.stop_reason, "ui_progressive_search_limit_after_recovery")
        self.assertEqual(
            tracker.post_recovery_search_scroll_count,
            UNFOLLOW_POST_RECOVERY_SEARCH_SCROLL_LIMIT,
        )
        self.assertEqual(tracker.total_search_scroll_count, 15)

    def test_eight_actions_then_next_progressive_search_starts_from_one(self) -> None:
        names = {f"candidate_{index:02d}" for index in range(35)}
        budget = derive_adaptive_coverage_budget(
            quota_remaining=35,
            eligible_remaining=35,
            session_remaining_seconds=6 * 60 * 60,
        )
        tracker = FollowingCoverageTracker(budget, names, 35)
        for username in sorted(names)[:8]:
            tracker.mark_action_attempted(username)
            tracker.mark_action_verified(username)
            tracker.mark_action_persisted(username)
            tracker.mark_safe_profile_return(username)
        tracker.observe_viewport(["unrelated_initial"], elapsed_seconds=0, following_confirmed=True)
        tracker.mark_scroll(moved=True)
        decision = tracker.observe_viewport(
            ["unrelated_next"], elapsed_seconds=1, following_confirmed=True
        )
        self.assertEqual(decision.action, "scroll")
        self.assertEqual(tracker.search_scroll_count, 1)
        self.assertEqual(tracker.repeated_viewport_stagnation_count, 0)

    def test_plan_35_checkpoint_preserves_8_and_resumes_27_without_duplicates(self) -> None:
        names = {f"candidate_{index:02d}" for index in range(35)}
        budget = derive_adaptive_coverage_budget(
            quota_remaining=35,
            eligible_remaining=35,
            session_remaining_seconds=6 * 60 * 60,
        )
        tracker = FollowingCoverageTracker(budget, names, 35)
        for username in sorted(names)[:8]:
            tracker.mark_action_attempted(username)
            tracker.mark_action_verified(username)
            tracker.mark_action_persisted(username)
            tracker.mark_safe_profile_return(username)
        outcome = build_unfollow_outcome(
            stable_reason="following_button_not_found",
            raw_candidate_count=127,
            eligible_candidate_count=35,
            planned_candidate_count=35,
            attempted_count=8,
            verified_count=8,
            persisted_count=8,
            tracker=tracker,
        )
        checkpoint = outcome["checkpoint"]
        self.assertEqual(outcome["phase_status"], "partial_resumable")
        self.assertEqual(outcome["remaining_count"], 27)
        self.assertEqual(len(checkpoint["persisted_usernames"]), 8)
        self.assertEqual(len(checkpoint["remaining_usernames"]), 27)
        self.assertTrue(
            set(checkpoint["persisted_usernames"]).isdisjoint(
                checkpoint["remaining_usernames"]
            )
        )

    def test_transient_cta_failure_is_checkpointed_retryable_not_lost(self) -> None:
        budget = derive_adaptive_coverage_budget(
            quota_remaining=2,
            eligible_remaining=2,
            session_remaining_seconds=3600,
        )
        tracker = FollowingCoverageTracker(budget, {"missing_cta", "next_candidate"}, 2)
        tracker.mark_candidate_retryable("missing_cta")
        checkpoint = tracker.checkpoint()
        self.assertEqual(checkpoint["retryable_usernames"], ["missing_cta"])
        self.assertEqual(
            checkpoint["remaining_usernames"],
            ["missing_cta", "next_candidate"],
        )
        self.assertEqual(tracker.summary()["retryable_candidates_count"], 1)

    def test_unavailable_candidate_and_retryable_candidate_have_distinct_semantics(self) -> None:
        budget = derive_adaptive_coverage_budget(
            quota_remaining=2,
            eligible_remaining=2,
            session_remaining_seconds=3600,
        )
        tracker = FollowingCoverageTracker(budget, {"deleted", "transient"}, 2)
        tracker.mark_candidate_unavailable("deleted")
        tracker.mark_candidate_retryable("transient")
        checkpoint = tracker.checkpoint()
        self.assertEqual(checkpoint["unavailable_usernames"], ["deleted"])
        self.assertEqual(checkpoint["retryable_usernames"], ["transient"])
        self.assertEqual(checkpoint["remaining_usernames"], ["transient"])

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

    def test_five_successful_actions_returning_to_same_viewport_never_stagnate(self) -> None:
        names = [f"candidate_{index}" for index in range(7)]
        budget = derive_adaptive_coverage_budget(
            quota_remaining=7,
            eligible_remaining=7,
            session_remaining_seconds=3600,
        )
        tracker = FollowingCoverageTracker(budget, set(names), 7)
        for index in range(5):
            decision = tracker.observe_viewport(
                names,
                elapsed_seconds=float(index),
                following_confirmed=True,
            )
            self.assertEqual(decision.action, "act")
            username = names[index]
            tracker.mark_action_attempted(username)
            tracker.mark_action_verified(username)
            tracker.mark_action_persisted(username)
            tracker.mark_safe_profile_return(username)
            self.assertEqual(tracker.consecutive_stagnation_count, 0)
        self.assertEqual(len(tracker.persisted_usernames), 5)
        self.assertEqual(tracker.viewport_recoveries_used, 0)

    def test_true_consecutive_stagnation_recovers_before_stop(self) -> None:
        budget = derive_adaptive_coverage_budget(
            quota_remaining=5,
            eligible_remaining=5,
            session_remaining_seconds=3600,
        )
        tracker = FollowingCoverageTracker(
            budget,
            {f"planned_{index}" for index in range(5)},
            5,
        )
        visible = [f"other_{index}" for index in range(7)]
        tracker.observe_viewport(visible, elapsed_seconds=0, following_confirmed=True)
        decision = None
        for index in range(UNFOLLOW_PRE_RECOVERY_STAGNATION_LIMIT):
            decision = tracker.observe_viewport(
                visible,
                elapsed_seconds=float(index + 1),
                following_confirmed=True,
            )
        self.assertIsNotNone(decision)
        self.assertEqual(decision.action, "recover")
        self.assertEqual(decision.stop_reason, "ui_repeated_viewport_limit")
        self.assertIsNone(tracker.mark_recovery(succeeded=True, progress_proved=True))
        self.assertEqual(tracker.consecutive_stagnation_count, 0)

    def test_two_stagnations_do_not_trigger_recovery(self) -> None:
        budget = derive_adaptive_coverage_budget(
            quota_remaining=5,
            eligible_remaining=5,
            session_remaining_seconds=3600,
        )
        tracker = FollowingCoverageTracker(budget, {f"planned_{i}" for i in range(5)}, 5)
        visible = ["other_a", "other_b"]
        tracker.observe_viewport(visible, elapsed_seconds=0, following_confirmed=True)
        first = tracker.observe_viewport(visible, elapsed_seconds=1, following_confirmed=True)
        second = tracker.observe_viewport(visible, elapsed_seconds=2, following_confirmed=True)
        self.assertEqual((first.action, second.action), ("scroll", "scroll"))
        self.assertEqual(tracker.pre_recovery_stagnation_count, 2)
        self.assertFalse(tracker.recovery_attempted)

    def test_progress_on_third_observation_resets_without_recovery(self) -> None:
        budget = derive_adaptive_coverage_budget(quota_remaining=5, eligible_remaining=5, session_remaining_seconds=3600)
        tracker = FollowingCoverageTracker(budget, {f"planned_{i}" for i in range(5)}, 5)
        visible = ["other_a", "other_b"]
        tracker.observe_viewport(visible, elapsed_seconds=0, following_confirmed=True)
        tracker.observe_viewport(visible, elapsed_seconds=1, following_confirmed=True)
        tracker.observe_viewport(visible, elapsed_seconds=2, following_confirmed=True)
        tracker.mark_scroll(moved=True)
        decision = tracker.observe_viewport(visible, elapsed_seconds=3, following_confirmed=True)
        self.assertNotEqual(decision.action, "recover")
        self.assertEqual(tracker.pre_recovery_stagnation_count, 0)
        self.assertEqual(tracker.reset_reason, "verified_action_progress_credit")
        self.assertFalse(tracker.recovery_attempted)

    def test_successful_recovery_with_proved_progress_resets_completely(self) -> None:
        budget = derive_adaptive_coverage_budget(quota_remaining=5, eligible_remaining=5, session_remaining_seconds=3600)
        tracker = FollowingCoverageTracker(budget, {f"planned_{i}" for i in range(5)}, 5)
        visible = ["other_a", "other_b"]
        tracker.observe_viewport(visible, elapsed_seconds=0, following_confirmed=True)
        for index in range(3):
            decision = tracker.observe_viewport(visible, elapsed_seconds=index + 1, following_confirmed=True)
        self.assertEqual(decision.action, "recover")
        self.assertIsNone(tracker.mark_recovery(succeeded=True, progress_proved=True))
        self.assertTrue(tracker.recovery_attempted)
        self.assertTrue(tracker.recovery_succeeded)
        self.assertEqual(tracker.pre_recovery_stagnation_count, 0)
        self.assertEqual(tracker.post_recovery_stagnation_count, 0)
        self.assertFalse(tracker.post_recovery_observation_active)
        self.assertEqual(tracker.reset_reason, "recovery_progress_proved")

    def test_recovery_without_progress_plus_one_stagnation_continues(self) -> None:
        tracker, visible = self._tracker_at_recovery_boundary()
        self.assertIsNone(tracker.mark_recovery(succeeded=True, progress_proved=False))
        decision = tracker.observe_viewport(visible, elapsed_seconds=4, following_confirmed=True)
        self.assertEqual(decision.action, "scroll")
        self.assertEqual(tracker.post_recovery_stagnation_count, 1)

    def test_recovery_without_progress_plus_two_stagnations_is_partial_resumable(self) -> None:
        tracker, visible = self._tracker_at_recovery_boundary()
        self.assertIsNone(tracker.mark_recovery(succeeded=True, progress_proved=False))
        tracker.observe_viewport(visible, elapsed_seconds=4, following_confirmed=True)
        decision = tracker.observe_viewport(visible, elapsed_seconds=5, following_confirmed=True)
        self.assertEqual(tracker.post_recovery_stagnation_count, UNFOLLOW_POST_RECOVERY_STAGNATION_LIMIT)
        self.assertEqual(decision.action, "stop")
        self.assertEqual(decision.stop_reason, "ui_repeated_viewport_limit_after_recovery")
        outcome = build_unfollow_outcome(
            stable_reason=decision.stop_reason,
            raw_candidate_count=5,
            eligible_candidate_count=5,
            planned_candidate_count=5,
            attempted_count=0,
            verified_count=0,
            persisted_count=0,
            tracker=tracker,
        )
        self.assertEqual(outcome["phase_status"], "partial_resumable")
        self.assertTrue(outcome["resume_recommended"])
        self.assertEqual(outcome["stable_reason"], "ui_repeated_viewport_limit_after_recovery")

    def test_verified_unfollow_between_repetitions_resets_to_zero(self) -> None:
        budget = derive_adaptive_coverage_budget(quota_remaining=2, eligible_remaining=2, session_remaining_seconds=3600)
        tracker = FollowingCoverageTracker(budget, {"planned_a", "planned_b"}, 2)
        unrelated = ["other_a"]
        tracker.observe_viewport(unrelated, elapsed_seconds=0, following_confirmed=True)
        tracker.observe_viewport(unrelated, elapsed_seconds=1, following_confirmed=True)
        self.assertEqual(tracker.pre_recovery_stagnation_count, 1)
        tracker.mark_action_attempted("planned_a")
        tracker.mark_action_verified("planned_a")
        tracker.mark_action_persisted("planned_a")
        self.assertEqual(tracker.consecutive_stagnation_count, 0)
        self.assertEqual(tracker.reset_reason, "unfollow_persisted")
        decision = tracker.observe_viewport(unrelated, elapsed_seconds=2, following_confirmed=True)
        self.assertNotEqual(decision.action, "recover")
        self.assertEqual(tracker.pre_recovery_stagnation_count, 0)

    def test_no_stagnation_accumulates_between_successful_actions(self) -> None:
        budget = derive_adaptive_coverage_budget(quota_remaining=3, eligible_remaining=3, session_remaining_seconds=3600)
        tracker = FollowingCoverageTracker(budget, {"planned_a", "planned_b", "planned_c"}, 3)
        for index, username in enumerate(("planned_a", "planned_b", "planned_c")):
            tracker.mark_action_attempted(username)
            tracker.mark_action_verified(username)
            tracker.mark_action_persisted(username)
            tracker.mark_safe_profile_return(username)
            decision = tracker.observe_viewport(["same_row"], elapsed_seconds=float(index), following_confirmed=True)
            self.assertNotEqual(decision.action, "recover")
            self.assertEqual(tracker.consecutive_stagnation_count, 0)
        self.assertFalse(tracker.recovery_attempted)

    @staticmethod
    def _tracker_at_recovery_boundary() -> tuple[FollowingCoverageTracker, list[str]]:
        budget = derive_adaptive_coverage_budget(quota_remaining=5, eligible_remaining=5, session_remaining_seconds=3600)
        tracker = FollowingCoverageTracker(budget, {f"planned_{i}" for i in range(5)}, 5)
        visible = ["other_a", "other_b"]
        tracker.observe_viewport(visible, elapsed_seconds=0, following_confirmed=True)
        decision = None
        for index in range(3):
            decision = tracker.observe_viewport(visible, elapsed_seconds=index + 1, following_confirmed=True)
        assert decision is not None and decision.action == "recover"
        return tracker, visible

    def test_partial_outcome_preserves_checkpoint_and_remaining_plan(self) -> None:
        names = {f"candidate_{index:03d}" for index in range(120)}
        budget = derive_adaptive_coverage_budget(
            quota_remaining=120,
            eligible_remaining=120,
            session_remaining_seconds=6 * 60 * 60,
        )
        tracker = FollowingCoverageTracker(budget, names, 120)
        for username in sorted(names)[:5]:
            tracker.mark_action_attempted(username)
            tracker.mark_action_verified(username)
            tracker.mark_action_persisted(username)
            tracker.mark_safe_profile_return(username)
        outcome = build_unfollow_outcome(
            stable_reason="ui_repeated_viewport_limit",
            raw_candidate_count=267,
            eligible_candidate_count=120,
            planned_candidate_count=120,
            attempted_count=5,
            verified_count=5,
            persisted_count=5,
            tracker=tracker,
        )
        self.assertEqual(outcome["phase_status"], "partial_resumable")
        self.assertTrue(outcome["resume_recommended"])
        self.assertEqual(outcome["remaining_count"], 115)
        self.assertEqual(len(outcome["checkpoint"]["persisted_usernames"]), 5)
        self.assertEqual(len(outcome["checkpoint"]["remaining_usernames"]), 115)
        self.assertTrue(
            set(outcome["checkpoint"]["persisted_usernames"]).isdisjoint(
                outcome["checkpoint"]["remaining_usernames"]
            )
        )

    def test_partial_unfollow_outcome_overrides_legacy_mandatory_done_gate(self) -> None:
        outcome = {
            "phase_status": "partial_resumable",
            "stable_reason": "ui_repeated_viewport_limit",
            "planned_candidate_count": 120,
            "persisted_count": 5,
            "remaining_count": 115,
            "last_safe_checkpoint": "persisted:candidate_004",
            "resume_recommended": True,
            "checkpoint": {
                "schema": "UNFOLLOW_CHECKPOINT_V1",
                "persisted_usernames": [f"candidate_{index:03d}" for index in range(5)],
                "remaining_usernames": [f"candidate_{index:03d}" for index in range(5, 120)],
            },
        }
        plan = build_account_session_resume_plan(
            {
                "session_termination_class": "partial_resumable",
                "restart_eligibility": "eligible",
                "business_session_id": "mythyl-fixture",
                "welcome_enabled": False,
                "follow_quota_target": 18,
                "follows_completed_count": 18,
                "follow_quota_remaining": 0,
                "mandatory_unfollow_executed": True,
                "unfollow_outcome": outcome,
                "unfollow_phase_status": "partial_resumable",
            }
        )
        self.assertTrue(plan["restart_allowed"])
        self.assertEqual(plan["phases_to_run"]["follow"], False)
        self.assertEqual(plan["phases_to_run"]["unfollow"], True)
        self.assertEqual(plan["quota_remaining"]["unfollow"], 115)
        self.assertEqual(plan["unfollow_outcome"]["checkpoint"]["schema"], "UNFOLLOW_CHECKPOINT_V1")


if __name__ == "__main__":
    unittest.main()
