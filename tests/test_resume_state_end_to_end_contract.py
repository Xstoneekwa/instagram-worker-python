"""Offline lifecycle/claim boundary fixtures: no dispatcher or business work."""
from __future__ import annotations

import unittest
from unittest.mock import patch
from uuid import UUID

import account_session_resume_state_contract as contract
import account_session_resume_plan_store as store
from auto_restart_runtime import validate_auto_restart_request_at_claim
from tests.test_human_confirmed_resume_claim import _metadata, _plan_row


class ResumeStateEndToEndContractGate(unittest.TestCase):
    def test_exact_lifecycle_and_store_exports(self):
        self.assertEqual(contract.DB_ALLOWED_RESUME_STATES, {
            "run_active", "pre_device_stopped", "recovery_enqueued",
            "awaiting_human_resume_authorization", "resume_requested",
            "resume_succeeded", "not_recoverable", "completed",
        })
        for symbol, value in {
            "RESUME_STATE_RUN_ACTIVE": "run_active",
            "RESUME_STATE_AWAITING_HUMAN": "awaiting_human_resume_authorization",
            "RESUME_STATE_RESUME_REQUESTED": "resume_requested",
            "RESUME_STATE_RESUME_SUCCEEDED": "resume_succeeded",
            "RESUME_STATE_NOT_RECOVERABLE": "not_recoverable",
            "RESUME_STATE_COMPLETED": "completed",
        }.items():
            self.assertEqual(getattr(store, symbol), value)
        self.assertLessEqual(contract.WORKER_EMITTABLE_RESUME_STATES, contract.DB_ALLOWED_RESUME_STATES)

    def test_all_states_have_explicit_human_claim_outcomes_for_synthetic_accounts(self):
        for account_number in range(101, 106):
            account_id = str(UUID(int=account_number))
            for state in sorted(contract.DB_ALLOWED_RESUME_STATES) + ["unknown_state", "partial_resumable"]:
                with self.subTest(account=account_id, state=state):
                    meta = _metadata(attempt_id=2, retry_index=1)
                    meta["resume_plan"]["account_id"] = account_id
                    row = _plan_row(account_id=account_id, resume_state=state)
                    with patch.object(store, "load_resume_plan", return_value=row) as load:
                        ok, reason, policy = validate_auto_restart_request_at_claim(
                            account_id=account_id, metadata=meta)
                    load.assert_called_once_with(resume_plan_id=meta["resume_plan_id"])
                    self.assertEqual(ok, state == "resume_requested")
                    if ok:
                        self.assertEqual(policy["attempt_id"], 2)
                        self.assertEqual(policy["retry_index"], 1)
                    else:
                        self.assertEqual(reason, "resume_authorization_consumed")
                        self.assertIsNone(policy)

    def test_attempts_one_to_three_preserved_and_four_refused(self):
        for attempt in range(1, 5):
            with self.subTest(attempt=attempt), patch.object(store, "load_resume_plan", return_value=_plan_row()) as load:
                ok, reason, policy = validate_auto_restart_request_at_claim(
                    account_id=_plan_row()["account_id"],
                    metadata=_metadata(attempt_id=attempt, retry_index=attempt - 1))
                self.assertEqual(ok, attempt <= 3)
                if attempt == 4:
                    load.assert_not_called()
                    self.assertEqual(reason, "resume_plan_invalid")
                    self.assertIsNone(policy)
                else:
                    self.assertEqual(policy["attempt_id"], attempt)

    def test_mapping_remains_business_outcome_to_canonical_state(self):
        for phase in ("welcome", "follow", "unfollow"):
            transition = contract.resolve_end_of_session_transition(session_plan={
                "session_termination_class": "partial_resumable", "restart_allowed": True,
                "phases_to_run": {phase: True}, "quota_remaining": {phase: 7},
                "unsafe_markers": [],
            }, session_status="success")
            self.assertEqual(transition.outcome, "partial_resumable")
            self.assertEqual(transition.resume_state, "resume_requested")
            self.assertEqual(transition.auto_restart_decision, "schedule_resume")


if __name__ == "__main__":
    unittest.main()
