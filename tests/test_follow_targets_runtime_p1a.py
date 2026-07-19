from __future__ import annotations

import unittest
from unittest.mock import patch

import runner
import supabase_client


class FollowTargetsRuntimeP1aTest(unittest.TestCase):
    def test_load_eligible_follow_targets_filters_ct1_statuses(self) -> None:
        rows = [
            {
                "id": "t1",
                "account_id": "acct",
                "target_username": "Valid.One",
                "status": "valid",
                "quality_status": "eligible",
                "verification_status": "found",
                "created_at": "2026-01-01T00:00:00Z",
                "last_used_at": "2026-01-05T00:00:00Z",
            },
            {
                "id": "t2",
                "account_id": "acct",
                "target_username": "active_two",
                "status": "active",
                "quality_status": "eligible",
                "verification_status": "found",
                "created_at": "2026-01-02T00:00:00Z",
                "last_used_at": None,
            },
            {
                "id": "pending",
                "account_id": "acct",
                "target_username": "pending_legacy",
                "status": "pending",
                "quality_status": "eligible",
                "verification_status": "found",
            },
            {
                "id": "bad_quality",
                "account_id": "acct",
                "target_username": "bad_quality",
                "status": "valid",
                "quality_status": "rejected_verified",
                "verification_status": "found",
            },
            {
                "id": "pending_verification",
                "account_id": "acct",
                "target_username": "pending_verification",
                "status": "valid",
                "quality_status": "eligible",
                "verification_status": "pending",
            },
            {
                "id": "archived",
                "account_id": "acct",
                "target_username": "archived",
                "status": "valid",
                "quality_status": "eligible",
                "verification_status": "found",
                "archived_at": "2026-01-03T00:00:00Z",
            },
            {
                "id": "poor",
                "account_id": "acct",
                "target_username": "poor",
                "status": "poor_performance",
                "quality_status": "eligible",
                "verification_status": "found",
            },
        ]
        with patch.object(supabase_client, "_request_json", return_value=rows) as request_json:
            out = supabase_client.load_eligible_follow_targets("acct", limit=10)

        self.assertEqual([row["id"] for row in out], ["t2", "t1"])
        self.assertEqual(out[1]["target_username"], "valid.one")
        query = request_json.call_args.kwargs["query"]
        self.assertEqual(query["status"], "in.(valid,active)")
        self.assertEqual(query["order"], "created_at.asc")

    def test_account_session_source_plan_uses_db_target_before_env(self) -> None:
        with (
            patch.object(
                runner,
                "_safe_supabase_call",
                return_value=[{
                    "id": "target-id",
                    "target_username": "db_source",
                    "source_profile_username": "db_source",
                    "selection_source": "ig_targets",
                }],
            ),
            patch.object(runner.config, "FOLLOWERS_SOURCE_USERNAME", "env_source"),
            patch.object(runner.config, "FOLLOWERS_SOURCE_USERNAME_OPS_OVERRIDE", False),
        ):
            targets, reason = runner._load_account_session_follow_targets("acct", 5)

        self.assertIsNone(reason)
        self.assertEqual(targets[0]["id"], "target-id")
        self.assertEqual(targets[0]["source_profile_username"], "db_source")

    def test_account_session_source_plan_blocks_without_target_or_override(self) -> None:
        with (
            patch.object(runner, "_safe_supabase_call", return_value=[]),
            patch.object(runner.config, "FOLLOWERS_SOURCE_USERNAME", "env_source"),
            patch.object(runner.config, "FOLLOWERS_SOURCE_USERNAME_OPS_OVERRIDE", False),
        ):
            targets, reason = runner._load_account_session_follow_targets("acct", 5)

        self.assertEqual(targets, [])
        self.assertEqual(reason, "no_eligible_targets")

    def test_account_session_source_plan_allows_explicit_ops_override(self) -> None:
        with (
            patch.object(runner, "_safe_supabase_call", return_value=[]),
            patch.object(runner.config, "FOLLOWERS_SOURCE_USERNAME", "ops_source"),
            patch.object(runner.config, "FOLLOWERS_SOURCE_USERNAME_OPS_OVERRIDE", True),
        ):
            targets, reason = runner._load_account_session_follow_targets("acct", 5)

        self.assertIsNone(reason)
        self.assertEqual(targets[0]["selection_source"], "ops_override")
        self.assertEqual(targets[0]["source_profile_username"], "ops_source")

    def test_record_follow_interaction_outcome_includes_target_id_payload(self) -> None:
        target_id = "11111111-2222-4333-8444-555555555555"
        with patch.object(
            supabase_client, "merge_interacted_user_row", return_value={"ok": True}
        ) as merge, patch(
            "unfollow_settings.load_unfollow_settings"
        ) as load_settings, patch(
            "unfollow_settings.compute_eligible_unfollow_at_iso",
            return_value="2026-01-04T00:00:00+00:00",
        ):
            load_settings.return_value.after_days = 3
            supabase_client.record_follow_interaction_outcome(
                "acct",
                "candidate",
                "source",
                run_id="run-id",
                session_id="session-id",
                follow_ok=True,
                skipped_tap=True,
                follow_state_after="following",
                follow_status="already_following",
                target_id=target_id,
            )

        patch_body = merge.call_args.args[3]
        self.assertEqual(patch_body["payload"]["target_id"], target_id)
        self.assertEqual(patch_body["source_target_id"], target_id)
        self.assertEqual(patch_body["ct_id"], target_id)
        self.assertEqual(patch_body["source_target_username"], "source")
        self.assertEqual(patch_body["evidence_source"], "worker_follow_outcome")
        self.assertEqual(patch_body["evidence_confidence"], "high")
        self.assertIn("via CT @source", patch_body["evidence_summary"])
        self.assertNotIn("followed", patch_body)
        self.assertNotIn("unfollowed", patch_body)

    def test_verified_follow_preserves_canonical_unfollow_eligibility(self) -> None:
        with patch.object(
            supabase_client, "merge_interacted_user_row", return_value={"ok": True}
        ) as merge, patch.object(
            supabase_client, "record_interaction_event", return_value={"ok": True}
        ), patch(
            "unfollow_settings.load_unfollow_settings"
        ) as load_settings, patch(
            "unfollow_settings.compute_eligible_unfollow_at_iso",
            return_value="2026-01-04T00:00:00+00:00",
        ):
            load_settings.return_value.after_days = 3
            supabase_client.record_follow_interaction_outcome(
                "acct",
                "candidate",
                "source",
                run_id="run-id",
                session_id="session-id",
                follow_ok=True,
                skipped_tap=False,
                follow_state_after="following",
                follow_status="following",
            )

        patch_body = merge.call_args.args[3]
        self.assertEqual(
            patch_body["eligible_unfollow_at"], "2026-01-04T00:00:00+00:00"
        )
        self.assertNotIn("followed", patch_body)
        self.assertNotIn("unfollowed", patch_body)


if __name__ == "__main__":
    unittest.main()
