from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import unittest

import account_session_orchestrator as orchestrator
from account_session_resume_engine import build_account_session_resume_plan
from follow_outcome_contract import build_follow_termination_decision


FIXTURE = Path(__file__).parent / "fixtures" / "follow_partial_unfollow_handoff_point_d.json"


class FollowPartialUnfollowHandoffPointDTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        cls.now = datetime(2026, 8, 21, 10, 0, tzinfo=timezone.utc)

    def _follow_outcome(self, count: int = 1) -> dict:
        return build_follow_termination_decision(
            exit_code=53,
            first_causal_reason="following_button_not_found",
            follows_completed_count=count,
            target_follow_budget_effective=120,
            target_attribution=self.fixture["target_attribution"],
            physical_follow_preserved=True,
            canonical_follow_receipt_present=True,
            candidate_local_failure=True,
            post_follow_recovery_required=True,
            no_new_follow_until_recovered=True,
            safe_boundary=True,
            safe_next_step="handoff_to_unfollow",
        )

    def _diagnostic(self, **updates: object) -> dict:
        out = {
            "run_id": self.fixture["run_id"],
            "handoff_would_run": True,
            "handoff_skip_reason": "",
            "unfollow_enabled": True,
            "unfollow_mode": self.fixture["unfollow_mode"],
            "pending_unfollow_count": self.fixture["eligible_unfollow_candidates"],
            "canonical_global_blockers": [],
        }
        out.update(updates)
        return out

    def _evaluate(self, *, outcome: dict | None = None, diagnostic: dict | None = None, deadline_seconds: int = 3600) -> dict:
        return orchestrator._evaluate_follow_partial_unfollow_handoff(
            account_id=self.fixture["account_id"],
            account_username=self.fixture["account_username"],
            run_id=self.fixture["run_id"],
            business_session_id=self.fixture["business_session_id"],
            follow_outcome=outcome or self._follow_outcome(),
            diagnostic=diagnostic or self._diagnostic(),
            real_max_actions_effective=self.fixture["real_max_actions_effective"],
            business_action_deadline=(self.now + timedelta(seconds=deadline_seconds)).isoformat(),
            now=self.now,
        )

    def test_cumulative_follow_count_never_changes_safe_handoff(self) -> None:
        for count in self.fixture["follow_counts"]:
            with self.subTest(count=count):
                result = self._evaluate(outcome=self._follow_outcome(count))
                self.assertTrue(result["allowed"])
                self.assertEqual("enter_unfollow", result["decision"])
                self.assertFalse(result["new_follow_allowed"])
                self.assertTrue(result["post_follow_recovery_still_pending"])

    def test_exit_53_without_authoritative_receipt_fails_closed(self) -> None:
        outcome = self._follow_outcome()
        outcome["canonical_follow_receipt_present"] = False
        result = self._evaluate(outcome=outcome)
        self.assertFalse(result["allowed"])
        self.assertTrue(result["block_reason"].startswith("follow_termination_decision_invalid"))

    def test_each_structured_global_blocker_stops_handoff(self) -> None:
        blockers = (
            "challenge",
            "restriction",
            "active_instagram_account_mismatch",
            "canonical_persistence_unavailable",
            "p0c_ambiguous_mutation_pending",
            "invalid_lease",
            "device_unavailable",
            "runtime_identity_invalid",
            "global_stop_requested",
            "current_blocking_incident",
        )
        for blocker in blockers:
            with self.subTest(blocker=blocker):
                result = self._evaluate(
                    diagnostic=self._diagnostic(canonical_global_blockers=[blocker])
                )
                self.assertFalse(result["allowed"])
                self.assertEqual(blocker, result["block_reason"])
                self.assertTrue(result["global_stop"])

    def test_unfollow_prerequisites_and_deadline_fail_closed(self) -> None:
        cases = (
            (self._diagnostic(unfollow_enabled=False), "unfollow_disabled", 3600),
            (self._diagnostic(pending_unfollow_count=0), "unfollow_skipped_no_safe_candidate", 3600),
            (self._diagnostic(), "insufficient_safe_business_time", 10),
        )
        for diagnostic, reason, seconds in cases:
            with self.subTest(reason=reason):
                result = self._evaluate(diagnostic=diagnostic, deadline_seconds=seconds)
                self.assertFalse(result["allowed"])
                self.assertIn(reason, result["block_reasons"])

    def test_restart_is_recovery_first_and_completed_unfollow_does_not_replay(self) -> None:
        outcome = self._follow_outcome()
        eligibility = orchestrator._restart_eligibility(
            session_termination_class="partial_resumable",
            follow_quota_remaining=119,
            follow_to_unfollow_diagnostic={},
            follow_to_unfollow_real={},
            follow_outcome=outcome,
        )
        self.assertEqual(("eligible", "post_follow_recovery_first"), eligibility)
        plan = build_account_session_resume_plan(
            {
                "business_session_id": self.fixture["business_session_id"],
                "current_attempt_id": 1,
                "session_termination_class": "partial_resumable",
                "restart_eligibility": "eligible",
                "follow_outcome": outcome,
                "follow_quota_target": 120,
                "follows_completed_count": 1,
                "follow_quota_remaining": 119,
                "unfollow_outcome": {
                    "phase_status": "quota_reached",
                    "persisted_count": 52,
                    "remaining_count": 0
                },
                "unfollow_quota_target": 52,
                "unfollow_actions_verified": 52,
                "mandatory_unfollow_satisfied": True,
                "welcome_enabled": False,
            }
        )
        self.assertTrue(plan["restart_allowed"])
        self.assertTrue(plan["phases_to_run"]["post_follow_recovery"])
        self.assertFalse(plan["phases_to_run"]["follow"])
        self.assertFalse(plan["phases_to_run"]["unfollow"])
        self.assertEqual("post_follow_recovery", plan["safe_next_step"])
        self.assertEqual(self.fixture["business_session_id"], plan["business_session_id"])

    def test_partial_follow_plus_completed_unfollow_is_not_full_completion(self) -> None:
        termination = orchestrator._session_termination_class(
            session_status="success",
            follow_phase_executed=True,
            follow_exit_code=53,
            follow_quota_remaining=119,
            follow_to_unfollow_diagnostic={},
            follow_to_unfollow_real={"executed": True, "status": "completed"},
            follow_phase_skipped_reason=None,
            transition_reason="follow_candidate_local_post_follow_partial_safe_for_unfollow",
            follow_session_outcome="partial_resumable",
        )
        self.assertEqual("partial_resumable", termination)


if __name__ == "__main__":
    unittest.main()
