from __future__ import annotations

import unittest

from follow_outcome_contract import build_follow_outcome


class FollowOutcomeContractTests(unittest.TestCase):
    def test_verified_actions_never_erase_local_partial_stop(self) -> None:
        outcome = build_follow_outcome(
            stable_reason="visible_window_exhausted_scroll_failed",
            verified_actions=18,
            target_actions=40,
            current_target_id="ct-1",
            remaining_target_ids=["ct-2", "ct-3"],
            safe_boundary=False,
        )
        self.assertEqual(outcome["phase_status"], "partial_resumable")
        self.assertFalse(outcome["completed"])
        self.assertTrue(outcome["partial"])
        self.assertEqual(outcome["remaining_actions"], 22)
        self.assertEqual(outcome["current_ct_result"], "locally_blocked")
        self.assertEqual(outcome["safe_next_step"], "rotate_next_ct")

    def test_critical_marker_stops_account_session(self) -> None:
        outcome = build_follow_outcome(
            stable_reason="instagram_challenge_detected",
            verified_actions=18,
            target_actions=40,
            remaining_target_ids=["ct-2"],
        )
        self.assertEqual(outcome["phase_status"], "blocked_critical")
        self.assertEqual(outcome["scope"], "account_session")
        self.assertEqual(outcome["safe_next_step"], "end_session")

    def test_max_ct_limit_is_resumable_not_completed(self) -> None:
        outcome = build_follow_outcome(
            stable_reason="max_targets_per_run_reached",
            verified_actions=18,
            target_actions=40,
            remaining_target_ids=["ct-5"],
            safe_boundary=True,
        )
        self.assertEqual(outcome["phase_status"], "partial_resumable")
        self.assertEqual(outcome["safe_next_step"], "schedule_resume")


if __name__ == "__main__":
    unittest.main()
