from __future__ import annotations

import unittest

import account_session_orchestrator as orchestrator
import runner
from follow_outcome_contract import build_follow_outcome


class Follow60EvaluationBarrierTerminalizationTests(unittest.TestCase):
    def _verdict(self, **overrides):
        values = {
            "barrier_due": True,
            "cycle_complete": True,
            "canonical_count_match": True,
            "follow_verified": True,
            "mute_posts_verified": True,
            "mute_stories_verified": True,
            "like_terminal_safe": True,
            "return_ct_exact": True,
            "critical_receipts_acknowledged": True,
            "critical_outbox_pending": 0,
            "action_in_progress": False,
            "next_candidate_started": False,
        }
        values.update(overrides)
        return runner._follow60_evaluation_terminal_contract(**values)

    def test_complete_cycle_is_dedicated_success_terminal(self):
        verdict = self._verdict()
        self.assertTrue(verdict["ok"])
        self.assertEqual(
            verdict["terminal_status"],
            "completed_waiting_operator_evaluation",
        )

    def test_barrier_not_due_is_rejected(self):
        self.assertFalse(self._verdict(barrier_due=False)["ok"])

    def test_incomplete_cycle_is_rejected(self):
        self.assertFalse(self._verdict(cycle_complete=False)["ok"])

    def test_canonical_count_mismatch_is_rejected(self):
        self.assertFalse(self._verdict(canonical_count_match=False)["ok"])

    def test_unverified_follow_is_rejected(self):
        self.assertFalse(self._verdict(follow_verified=False)["ok"])

    def test_unverified_mute_posts_is_rejected(self):
        self.assertFalse(self._verdict(mute_posts_verified=False)["ok"])

    def test_unverified_mute_stories_is_rejected(self):
        self.assertFalse(self._verdict(mute_stories_verified=False)["ok"])

    def test_unsafe_like_terminal_is_rejected(self):
        self.assertFalse(self._verdict(like_terminal_safe=False)["ok"])

    def test_missing_exact_return_ct_is_rejected(self):
        self.assertFalse(self._verdict(return_ct_exact=False)["ok"])

    def test_unacknowledged_critical_receipts_are_rejected(self):
        self.assertFalse(
            self._verdict(critical_receipts_acknowledged=False)["ok"]
        )

    def test_nonempty_critical_outbox_is_rejected(self):
        verdict = self._verdict(critical_outbox_pending=1)
        self.assertFalse(verdict["ok"])
        self.assertIn("critical_outbox_empty", verdict["failed_checks"])

    def test_action_in_progress_is_rejected(self):
        self.assertFalse(self._verdict(action_in_progress=True)["ok"])

    def test_next_candidate_started_is_rejected(self):
        self.assertFalse(self._verdict(next_candidate_started=True)["ok"])

    def test_follow_outcome_preserves_evaluation_terminal(self):
        outcome = build_follow_outcome(
            stable_reason="follow60_evaluation_barrier_reached",
            verified_actions=10,
            target_actions=40,
            safe_boundary=True,
        )
        self.assertEqual(
            outcome["phase_status"],
            "completed_waiting_operator_evaluation",
        )
        self.assertTrue(outcome["completed"])
        self.assertFalse(outcome["partial"])
        self.assertEqual(outcome["safe_next_step"], "wait_operator_evaluation")

    def test_phase_terminal_contract_accepts_evaluation_terminal(self):
        contract = orchestrator._phase_terminal_contract(
            welcome="not_planned",
            follow="completed_waiting_operator_evaluation",
            unfollow="skipped_cleanly",
            outreach="not_planned",
        )
        self.assertTrue(contract["ok"])

    def test_session_class_does_not_become_partial_for_remaining_quota(self):
        result = orchestrator._session_termination_class(
            session_status="success",
            follow_phase_executed=True,
            follow_exit_code=0,
            follow_quota_remaining=30,
            follow_to_unfollow_diagnostic={},
            follow_to_unfollow_real={},
            follow_phase_skipped_reason=None,
            transition_reason="",
            follow_session_outcome="evaluation_barrier_reached",
        )
        self.assertEqual(result, "completed_waiting_operator_evaluation")

    def test_auto_restart_is_not_needed_at_evaluation_terminal(self):
        eligibility = orchestrator._restart_eligibility(
            session_termination_class="completed_waiting_operator_evaluation",
            follow_quota_remaining=30,
            follow_to_unfollow_diagnostic={},
            follow_to_unfollow_real={},
        )
        self.assertEqual(eligibility, ("not_needed", "waiting_operator_evaluation"))


if __name__ == "__main__":
    unittest.main()
