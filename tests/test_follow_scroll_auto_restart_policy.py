from __future__ import annotations

import unittest

from account_session_resume_engine import build_account_session_resume_plan


def _summary(**overrides: object) -> dict:
    summary = {
        "account_id": "11111111-1111-4111-8111-111111111111",
        "run_id": "22222222-2222-4222-8222-222222222222",
        "session_status": "success",
        "session_termination_class": "partial_resumable",
        "restart_eligibility": "eligible",
        "welcome_enabled": False,
        "welcome_phase_status": "completed",
        "follow_phase_status": "partial_resumable",
        "unfollow_phase_status": "completed",
        "follow_quota_target": 40,
        "follows_completed_count": 17,
        "follow_quota_remaining": 23,
        "mandatory_unfollow_executed": True,
        "unfollow_quota_target": 0,
        "unfollow_actions_verified": 0,
        "follow_stop_reason": "visible_window_exhausted_scroll_failed",
    }
    summary.update(overrides)
    return summary


class FollowScrollAutoRestartPolicyTests(unittest.TestCase):
    def test_safe_checkpoint_is_eligible_with_remaining_quota(self) -> None:
        plan = build_account_session_resume_plan(
            _summary(
                target_rotation_safe_after_scroll_failure=True,
                scroll_failure_surface_ambiguous=False,
            )
        )
        self.assertTrue(plan["restart_allowed"])
        self.assertEqual(plan["restart_block_reason"], "")
        self.assertEqual(plan["quota_remaining"]["follow"], 23)
        self.assertTrue(plan["phases_to_run"]["follow"])

    def test_ambiguous_checkpoint_blocks_automatic_resume(self) -> None:
        plan = build_account_session_resume_plan(
            _summary(
                target_rotation_safe_after_scroll_failure=False,
                scroll_failure_surface_ambiguous=True,
            )
        )
        self.assertFalse(plan["restart_allowed"])
        self.assertEqual(plan["restart_block_reason"], "unsafe_follow_resume_checkpoint")

    def test_missing_checkpoint_proof_blocks_automatic_resume(self) -> None:
        plan = build_account_session_resume_plan(_summary())
        self.assertFalse(plan["restart_allowed"])
        self.assertEqual(plan["restart_block_reason"], "unsafe_follow_resume_checkpoint")

    def test_completed_quota_never_restarts(self) -> None:
        plan = build_account_session_resume_plan(
            _summary(
                session_termination_class="completed",
                restart_eligibility="not_needed",
                follows_completed_count=40,
                follow_quota_remaining=0,
                follow_stop_reason="global_follow_cap_reached",
            )
        )
        self.assertFalse(plan["restart_allowed"])
        self.assertIn(plan["restart_block_reason"], {"session_completed", "restart_not_needed"})

    def test_existing_non_scroll_resumable_contract_is_unchanged(self) -> None:
        plan = build_account_session_resume_plan(
            _summary(
                session_status="failed",
                follow_stop_reason="recoverable_python_runtime_failure",
                follow_quota_remaining=5,
            )
        )
        self.assertTrue(plan["restart_allowed"])
        self.assertEqual(plan["restart_block_reason"], "")


if __name__ == "__main__":
    unittest.main()
