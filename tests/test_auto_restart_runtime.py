import unittest
from unittest.mock import patch

from account_session_manual_resume import build_manual_resume_command
from account_session_reliability_schema import build_admin_reliability_snapshot
from account_session_resume_engine import build_account_session_resume_plan
from auto_restart_runtime import (
    is_auto_restart_request,
    phase_enabled,
    validate_auto_restart_request_at_claim,
)


class AutoRestartRuntimeTests(unittest.TestCase):
    def test_is_auto_restart_request(self) -> None:
        self.assertTrue(
            is_auto_restart_request({"auto_restart": True, "source": "auto_restart_tick"})
        )
        self.assertFalse(is_auto_restart_request({"auto_restart": True, "source": "manual"}))

    def test_phase_enabled(self) -> None:
        policy = {"phases_to_run": {"welcome": False, "follow": True, "unfollow": False}}
        self.assertFalse(phase_enabled("welcome", default=True, policy=policy))
        self.assertTrue(phase_enabled("follow", default=False, policy=policy))

    def test_checkpoint_payload_is_not_an_instagram_challenge(self) -> None:
        summary = {
            "account_id": "11111111-1111-4111-8111-111111111111",
            "account_username": "fixture_user",
            "auto_restart_resume_plan": {
                "restart_allowed": True,
                "phases_to_run": {"welcome": False, "follow": False, "unfollow": True},
                "quota_remaining": {"follow": 0, "unfollow": 3, "total": 3},
                "unfollow_checkpoint": {
                    "checkpoint": {"last_safe_checkpoint": "following_list_after_scroll"},
                    "remaining_usernames": ["one", "two", "three"],
                },
            },
        }
        result = build_manual_resume_command(summary)
        self.assertTrue(result["manual_resume_allowed"])
        self.assertEqual(result["unsafe_markers"], [])

    def test_explicit_challenge_failure_field_still_blocks(self) -> None:
        summary = {
            "account_id": "11111111-1111-4111-8111-111111111111",
            "account_username": "fixture_user",
            "root_failure_code": "instagram_challenge_checkpoint",
            "auto_restart_resume_plan": {
                "restart_allowed": True,
                "phases_to_run": {"welcome": False, "follow": False, "unfollow": True},
                "quota_remaining": {"follow": 0, "unfollow": 3, "total": 3},
            },
        }
        result = build_manual_resume_command(summary)
        self.assertFalse(result["manual_resume_allowed"])
        self.assertEqual(result["manual_resume_block_reason"], "challenge")

    def test_checkpoint_payload_is_safe_in_resume_and_reliability_contracts(self) -> None:
        summary = {
            "account_id": "11111111-1111-4111-8111-111111111111",
            "account_username": "fixture_user",
            "session_status": "success",
            "session_termination_class": "partial_resumable",
            "restart_eligibility": "eligible",
            "unfollow_phase_status": "partial_resumable",
            "mandatory_unfollow_executed": True,
            "unfollow_outcome": {
                "phase_status": "partial_resumable",
                "planned_candidate_count": 3,
                "persisted_count": 1,
                "remaining_count": 2,
                "checkpoint": {
                    "schema": "UNFOLLOW_CHECKPOINT_V1",
                    "last_safe_checkpoint": "following_list_after_scroll",
                    "remaining_usernames": ["one", "two"],
                },
            },
        }
        plan = build_account_session_resume_plan(summary)
        snapshot = build_admin_reliability_snapshot(summary, plan)
        self.assertEqual(plan["unsafe_markers"], [])
        self.assertEqual(snapshot["unsafe_markers"], [])
        self.assertNotIn("challenge", snapshot["badges"])
        self.assertNotIn("needs_human_review", snapshot["badges"])

    def test_explicit_challenge_blocks_resume_and_reliability_contracts(self) -> None:
        summary = {
            "account_id": "11111111-1111-4111-8111-111111111111",
            "account_username": "fixture_user",
            "session_status": "failed",
            "session_termination_class": "partial_resumable",
            "restart_eligibility": "eligible",
            "root_failure_code": "instagram_challenge_checkpoint",
        }
        plan = build_account_session_resume_plan(summary)
        snapshot = build_admin_reliability_snapshot(summary, plan)
        self.assertIn("challenge", plan["unsafe_markers"])
        self.assertIn("challenge", snapshot["unsafe_markers"])

    def test_validate_blocks_missing_prior_run(self) -> None:
        ok, reason, policy = validate_auto_restart_request_at_claim(
            account_id="11111111-1111-4111-8111-111111111111",
            metadata={"auto_restart": True, "source": "auto_restart_tick", "resume_plan_version": 1},
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "resume_plan_invalid")
        self.assertIsNone(policy)

    @patch("auto_restart_runtime.load_prior_run_summary")
    def test_validate_rejects_unknown_required_field(self, load_summary) -> None:
        summary = {
            "account_id": "11111111-1111-4111-8111-111111111111",
            "account_username": "fixture_user",
            "run_id": "22222222-2222-4222-8222-222222222222",
            "auto_restart_resume_plan": {
                "restart_allowed": True,
                "phases_to_run": {"welcome": False, "follow": True, "unfollow": False},
            },
        }
        load_summary.return_value = summary
        ok, reason, policy = validate_auto_restart_request_at_claim(
            account_id="11111111-1111-4111-8111-111111111111",
            metadata={
                "auto_restart": True,
                "source": "auto_restart_tick",
                "resume_plan_version": 1,
                "resume_plan_schema": "AUTO_RESTART_RESUME_PLAN_V1",
                "prior_run_id": "22222222-2222-4222-8222-222222222222",
                "resume_plan": {
                    "schema": "AUTO_RESTART_RESUME_PLAN_V1",
                    "restart_allowed": True,
                    "phases_to_run": {"welcome": False, "follow": True, "unfollow": False},
                },
            },
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "unknown_required_field")
        self.assertIsNone(policy)

    @patch("auto_restart_runtime.load_prior_run_summary")
    def test_validate_rejects_quota_inconsistency(self, load_summary) -> None:
        summary = {
            "account_id": "11111111-1111-4111-8111-111111111111",
            "account_username": "fixture_user",
            "run_id": "22222222-2222-4222-8222-222222222222",
            "auto_restart_resume_plan": {
                "restart_allowed": True,
                "phases_to_run": {"welcome": False, "follow": True, "unfollow": False},
                "quota_remaining": {"follow": -1, "unfollow": 0, "welcome": 0},
            },
        }
        load_summary.return_value = summary
        ok, reason, policy = validate_auto_restart_request_at_claim(
            account_id="11111111-1111-4111-8111-111111111111",
            metadata={
                "auto_restart": True,
                "source": "auto_restart_tick",
                "resume_plan_version": 1,
                "resume_plan_schema": "AUTO_RESTART_RESUME_PLAN_V1",
                "prior_run_id": "22222222-2222-4222-8222-222222222222",
                "resume_plan": summary["auto_restart_resume_plan"],
            },
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "quota_inconsistency_blocked")
        self.assertIsNone(policy)

    @patch("auto_restart_runtime.load_prior_run_summary")
    def test_validate_accepts_supported_resume(self, load_summary) -> None:
        summary = {
            "account_id": "11111111-1111-4111-8111-111111111111",
            "account_username": "fixture_user",
            "run_id": "22222222-2222-4222-8222-222222222222",
            "session_termination_class": "partial_resumable",
            "restart_eligibility": "eligible",
            "auto_restart_resume_plan": {
                "restart_allowed": True,
                "restart_block_reason": "",
                "session_termination_class": "partial_resumable",
                "phases_to_run": {"welcome": False, "follow": True, "unfollow": False},
                "quota_remaining": {"follow": 5, "unfollow": 0, "total": 5},
                "unfollow_checkpoint": {
                    "cursor_schema": "UNFOLLOW_CURSOR_V2",
                    "depth": 4,
                    "anchor_hashes": ["a3:fixture"],
                },
            },
            "follow_quota_remaining": 5,
            "follows_completed_count": 10,
            "follow_quota_target": 15,
        }
        load_summary.return_value = summary
        manual = build_manual_resume_command(summary, resume_plan=summary["auto_restart_resume_plan"])
        self.assertTrue(manual["manual_resume_allowed"])
        ok, reason, policy = validate_auto_restart_request_at_claim(
            account_id="11111111-1111-4111-8111-111111111111",
            metadata={
                "auto_restart": True,
                "source": "auto_restart_tick",
                "resume_plan_version": 1,
                "resume_plan_schema": "AUTO_RESTART_RESUME_PLAN_V1",
                "prior_run_id": "22222222-2222-4222-8222-222222222222",
                "resume_plan": {
                    **summary["auto_restart_resume_plan"],
                    "schema": "AUTO_RESTART_RESUME_PLAN_V1",
                },
            },
        )
        self.assertTrue(ok, reason)
        self.assertIsNotNone(policy)
        self.assertEqual(policy["prior_run_id"], "22222222-2222-4222-8222-222222222222")
        self.assertEqual(policy["unfollow_checkpoint"]["depth"], 4)

    @patch("auto_restart_runtime.load_prior_run_summary")
    def test_embedded_request_cannot_erase_canonical_unfollow_cursor(self, load_summary) -> None:
        account_id = "11111111-1111-4111-8111-111111111111"
        prior_run_id = "22222222-2222-4222-8222-222222222222"
        checkpoint = {
            "cursor_schema": "UNFOLLOW_CURSOR_V2",
            "depth": 21,
            "generation": 79,
            "anchor_hashes": ["a3:canonical"],
            "remaining_usernames": ["one", "two"],
        }
        load_summary.return_value = {
            "account_id": account_id,
            "account_username": "fixture_user",
            "run_id": prior_run_id,
            "session_termination_class": "partial_resumable",
            "restart_eligibility": "eligible",
            "auto_restart_resume_plan": {
                "restart_allowed": True,
                "restart_block_reason": "",
                "session_termination_class": "partial_resumable",
                "phases_to_run": {"welcome": False, "follow": False, "unfollow": True},
                "quota_remaining": {"follow": 0, "unfollow": 2, "total": 2},
                "unfollow_checkpoint": checkpoint,
            },
        }
        ok, reason, policy = validate_auto_restart_request_at_claim(
            account_id=account_id,
            metadata={
                "auto_restart": True,
                "source": "auto_restart_tick",
                "resume_plan_version": 1,
                "resume_plan_schema": "AUTO_RESTART_RESUME_PLAN_V1",
                "prior_run_id": prior_run_id,
                # This mirrors the production request that carried phases and
                # quota but omitted the cursor persisted on the prior run.
                "resume_plan": {
                    "schema": "AUTO_RESTART_RESUME_PLAN_V1",
                    "restart_allowed": True,
                    "restart_block_reason": "",
                    "session_termination_class": "partial_resumable",
                    "phases_to_run": {
                        "welcome": False,
                        "follow": False,
                        "unfollow": True,
                    },
                    "quota_remaining": {"follow": 0, "unfollow": 2, "total": 2},
                },
            },
        )
        self.assertTrue(ok, reason)
        self.assertEqual(policy["unfollow_checkpoint"], checkpoint)
        self.assertEqual(policy["phases_to_run"], {"welcome": False, "follow": False, "unfollow": True})

    def test_resume_plan_default_cooldown_is_ten_minutes(self) -> None:
        with patch("account_session_resume_engine.config.AUTO_RESTART_DELAY_MINUTES", 10):
            plan = build_account_session_resume_plan(
                {
                    "session_termination_class": "partial_resumable",
                    "restart_eligibility": "eligible",
                    "follow_phase_status": "partial_resumable",
                    "follow_quota_target": 10,
                    "follows_completed_count": 5,
                    "follow_quota_remaining": 5,
                }
            )
        self.assertEqual(plan["restart_delay_minutes"], 10)
        self.assertIs(plan["restart_delay_authoritative"], False)
        self.assertEqual(
            plan["restart_delay_policy_source"],
            "backend:auto_restart_settings:global",
        )


if __name__ == "__main__":
    unittest.main()
