from __future__ import annotations

import json
import unittest
from pathlib import Path

from account_session_resume_state_contract import (
    DB_ALLOWED_RESUME_STATES,
    WORKER_EMITTABLE_RESUME_STATES,
    resolve_end_of_session_transition,
)


class ResumeStateContractTest(unittest.TestCase):
    def test_worker_states_are_db_subset(self) -> None:
        self.assertLessEqual(WORKER_EMITTABLE_RESUME_STATES, DB_ALLOWED_RESUME_STATES)
        self.assertNotIn("partial_resumable", WORKER_EMITTABLE_RESUME_STATES)

    def test_safe_partial_outcome_maps_to_resume_requested(self) -> None:
        transition = resolve_end_of_session_transition(
            session_plan={
                "session_termination_class": "partial_resumable",
                "restart_allowed": True,
                "quota_remaining": {"total": 30, "follow": 30},
                "phases_to_run": {"welcome": False, "follow": True, "unfollow": False},
                "unsafe_markers": [],
            },
            session_status="success",
        )
        self.assertEqual(transition.outcome, "partial_resumable")
        self.assertEqual(transition.resume_state, "resume_requested")
        self.assertEqual(transition.auto_restart_decision, "schedule_resume")

    def test_unsafe_partial_fails_closed(self) -> None:
        transition = resolve_end_of_session_transition(
            session_plan={
                "session_termination_class": "partial_resumable",
                "restart_allowed": True,
                "quota_remaining": {"total": 30},
                "phases_to_run": {"follow": True},
                "unsafe_markers": ["account_mismatch"],
            },
            session_status="success",
        )
        self.assertEqual(transition.resume_state, "not_recoverable")
        self.assertEqual(transition.auto_restart_decision, "blocked")

    def test_loriele_fixture_preserves_90_of_120_and_schedules_natural_resume(self) -> None:
        fixture = json.loads(
            (Path(__file__).parent / "fixtures/loriele_resume_plan_contract_v1.json").read_text()
        )
        transition = resolve_end_of_session_transition(
            session_plan=fixture["session_plan"],
            session_status=fixture["session_status"],
        )
        self.assertEqual(fixture["session_plan"]["quota_done"]["follow"], 90)
        self.assertEqual(fixture["session_plan"]["quota_remaining"]["follow"], 30)
        self.assertEqual(transition.resume_state, "resume_requested")
        self.assertEqual(transition.auto_restart_decision, "schedule_resume")


if __name__ == "__main__":
    unittest.main()
