from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from login_challenge_provenance import (
    PROVENANCE_KIND_ACTIVE_RUN,
    ChallengeProvenanceVerdict,
    evaluate_historical_email_challenge_provenance,
    evaluate_pre_input_email_challenge,
)


NOW = datetime(2026, 6, 23, 12, 0, tzinfo=timezone.utc)
EMAIL_SIGNALS = {"screen_type": "email_code_challenge", "email_code_challenge_present": True}


def strong_action(**overrides):
    base = {
        "id": "action-1",
        "account_id": "account-1",
        "action_type": "enter_email_verification_code",
        "status": "pending_verification",
        "created_at": (NOW - timedelta(minutes=2)).isoformat(),
        "updated_at": (NOW - timedelta(minutes=2)).isoformat(),
        "metadata": {
            "provenance_kind": PROVENANCE_KIND_ACTIVE_RUN,
            "stage": "post_submit",
            "run_id": "run-active-1",
            "expected_app_instance_id": "clone-1",
            "assignment_id": "assignment-1",
            "credentials_version": 1,
        },
    }
    if overrides:
        metadata = {**base["metadata"], **(overrides.pop("metadata", {}) or {})}
        base.update(overrides)
        base["metadata"] = metadata
    return base


class LoginChallengeProvenanceTest(unittest.TestCase):
    def test_pre_input_orphan_without_historical_record_is_blocked(self) -> None:
        verdict = evaluate_pre_input_email_challenge(
            routing_signals=EMAIL_SIGNALS,
            package_guard_mismatch=False,
            account_id="account-1",
            run_id="run-new-1",
            expected_app_instance_id="clone-1",
            assignment_id="assignment-1",
            credentials_version=1,
            historical_action=None,
            now=NOW,
        )
        self.assertFalse(verdict.accepted)
        self.assertEqual(verdict.reason, "pre_input_challenge_orphan")
        self.assertEqual(verdict.proof_kind, "none")

    def test_pre_input_same_clone_without_provenance_is_blocked(self) -> None:
        verdict = evaluate_pre_input_email_challenge(
            routing_signals=EMAIL_SIGNALS,
            package_guard_mismatch=False,
            account_id="account-1",
            run_id="run-new-1",
            expected_app_instance_id="clone-1",
            assignment_id="assignment-1",
            credentials_version=1,
            historical_action=None,
            now=NOW,
        )
        self.assertEqual(verdict.reason, "pre_input_challenge_orphan")

    def test_historical_strong_provenance_can_be_adopted(self) -> None:
        verdict = evaluate_historical_email_challenge_provenance(
            action_row=strong_action(),
            account_id="account-1",
            run_id="run-active-1",
            expected_app_instance_id="clone-1",
            assignment_id="assignment-1",
            credentials_version=1,
            assignment_updated_at=(NOW - timedelta(hours=2)).isoformat(),
            now=NOW,
        )
        self.assertTrue(verdict.accepted)
        self.assertEqual(verdict.proof_kind, "historical_strong")

    def test_historical_partial_provenance_is_blocked(self) -> None:
        action = strong_action(metadata={"assignment_id": ""})
        verdict = evaluate_historical_email_challenge_provenance(
            action_row=action,
            account_id="account-1",
            run_id="run-active-1",
            expected_app_instance_id="clone-1",
            assignment_id="assignment-1",
            credentials_version=1,
            now=NOW,
        )
        self.assertFalse(verdict.accepted)
        self.assertEqual(verdict.reason, "historical_provenance_partial")

    def test_historical_app_instance_mismatch_is_blocked(self) -> None:
        verdict = evaluate_historical_email_challenge_provenance(
            action_row=strong_action(),
            account_id="account-1",
            run_id="run-active-1",
            expected_app_instance_id="clone-other",
            assignment_id="assignment-1",
            credentials_version=1,
            now=NOW,
        )
        self.assertFalse(verdict.accepted)
        self.assertEqual(verdict.reason, "historical_app_instance_mismatch")


if __name__ == "__main__":
    unittest.main()
