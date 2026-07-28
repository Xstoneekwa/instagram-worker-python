from __future__ import annotations

import unittest
from unittest.mock import patch

import supabase_client
from unfollow_ui_coverage_policy import build_unfollow_outcome


class UnfollowCandidateAvailabilityTests(unittest.TestCase):
    def test_account_scoped_snapshot_is_loaded_once_and_normalized(self) -> None:
        with patch.object(
            supabase_client,
            "_request_json",
            return_value=[
                {
                    "account_id": "account-1",
                    "normalized_username": "Missing.User",
                    "status": "temporary_unavailable",
                }
            ],
        ) as request:
            out = supabase_client.fetch_unfollow_candidate_availability("account-1")
        self.assertIn("missing.user", out)
        request.assert_called_once()
        self.assertEqual(request.call_args.args[1], "ig_unfollow_candidate_availability")
        self.assertEqual(request.call_args.kwargs["query"]["account_id"], "eq.account-1")

    def test_availability_snapshot_paginates_without_a_silent_global_cap(self) -> None:
        first_page = [
            {
                "account_id": "account-1",
                "normalized_username": f"candidate_{index}",
                "status": "temporary_unavailable",
            }
            for index in range(1000)
        ]
        second_page = [
            {
                "account_id": "account-1",
                "normalized_username": "last_candidate",
                "status": "exhausted",
            }
        ]
        with patch.object(
            supabase_client,
            "_request_json",
            side_effect=[first_page, second_page],
        ) as request:
            out = supabase_client.fetch_unfollow_candidate_availability("account-1")
        self.assertEqual(len(out), 1001)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args_list[0].kwargs["query"]["offset"], "0")
        self.assertEqual(request.call_args_list[1].kwargs["query"]["offset"], "1000")

    def test_repeated_availability_page_fails_closed(self) -> None:
        page = [
            {
                "account_id": "account-1",
                "normalized_username": f"candidate_{index}",
                "status": "temporary_unavailable",
            }
            for index in range(1000)
        ]
        with patch.object(
            supabase_client,
            "_request_json",
            side_effect=[page, page],
        ):
            with self.assertRaisesRegex(RuntimeError, "repeated_page"):
                supabase_client.fetch_unfollow_candidate_availability("account-1")

    def test_not_found_rpc_is_bounded_and_account_scoped(self) -> None:
        with patch.object(
            supabase_client,
            "_call_rpc",
            return_value={
                "ok": True,
                "status": "temporary_unavailable",
                "not_found_attempt_count": 1,
            },
        ) as rpc:
            out = supabase_client.record_unfollow_candidate_not_found(
                "account-1",
                "Missing.User",
                source_run_id="run-1",
                reason="unfollow_candidate_account_unavailable",
            )
        self.assertTrue(out["ok"])
        self.assertEqual(rpc.call_args.args[0], "record_unfollow_candidate_not_found_v1")
        payload = rpc.call_args.args[1]
        self.assertEqual(payload["p_account_id"], "account-1")
        self.assertEqual(payload["p_normalized_username"], "missing.user")
        self.assertEqual(payload["p_cooldown_hours"], 24)
        self.assertEqual(payload["p_max_attempts"], 2)

    def test_not_found_is_never_persisted_as_unfollow_success(self) -> None:
        with patch.object(
            supabase_client,
            "_call_rpc",
            return_value={"ok": True, "status": "exhausted"},
        ) as rpc:
            supabase_client.record_unfollow_candidate_not_found(
                "account-1",
                "removed.account",
                source_run_id="run-1",
                reason="unfollow_candidate_account_unavailable",
            )
        self.assertNotIn("unfollowed", rpc.call_args.args[1])
        self.assertNotIn("success", rpc.call_args.args[1])

    def test_not_found_lifecycle_persistence_failure_blocks_automatic_resume(self) -> None:
        outcome = build_unfollow_outcome(
            stable_reason="unfollow_candidate_availability_persistence_failed",
            raw_candidate_count=1,
            eligible_candidate_count=1,
            planned_candidate_count=1,
            attempted_count=0,
            verified_count=0,
            persisted_count=0,
            tracker=None,
        )
        self.assertEqual(outcome["phase_status"], "failed_internal")
        self.assertFalse(outcome["resume_recommended"])


if __name__ == "__main__":
    unittest.main()
