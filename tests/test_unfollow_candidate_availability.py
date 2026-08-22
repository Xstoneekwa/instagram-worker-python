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

    def test_v2_confirmed_not_found_is_staged_as_retryable_first_proof(self) -> None:
        with patch.object(
            supabase_client,
            "_call_rpc",
            return_value={
                "ok": True,
                "status": "temporary_unavailable",
                "not_found_attempt_count": 1,
                "next_retry_at": "2026-07-30T18:00:00Z",
            },
        ) as rpc:
            out = supabase_client.record_unfollow_candidate_availability_v2(
                "account-1",
                "Missing.User",
                source_run_id="run-1",
                classification="username_not_found_confirmed",
                reason="username_not_found_confirmed",
            )
        self.assertEqual(out["status"], "temporary_unavailable")
        self.assertEqual(
            rpc.call_args.args[0],
            "record_unfollow_candidate_availability_v2",
        )
        self.assertEqual(
            rpc.call_args.args[1]["p_classification"],
            "username_not_found_confirmed",
        )

    def test_v2_technical_failure_is_retryable_hold(self) -> None:
        with patch.object(
            supabase_client,
            "_call_rpc",
            return_value={
                "ok": True,
                "status": "search_surface_unhealthy",
                "next_retry_at": "2026-07-29T18:30:00Z",
            },
        ) as rpc:
            out = supabase_client.record_unfollow_candidate_availability_v2(
                "account-1",
                "candidate",
                source_run_id="run-1",
                classification="search_surface_unhealthy",
                reason="search_results_loading_timeout",
                technical_cooldown_minutes=30,
            )
        self.assertEqual(out["status"], "search_surface_unhealthy")
        self.assertEqual(rpc.call_args.args[1]["p_technical_cooldown_minutes"], 30)

    def test_v2_positive_already_not_following_is_terminal(self) -> None:
        with patch.object(
            supabase_client,
            "_call_rpc",
            return_value={
                "ok": True,
                "status": "already_not_following_confirmed",
                "terminal_at": "2026-08-09T12:00:00Z",
            },
        ) as rpc:
            out = supabase_client.record_unfollow_already_not_following_v1(
                "account-1",
                "already.done",
                source_run_id="run-1",
                relationship_state="follow",
            )
        self.assertEqual(out["status"], "already_not_following_confirmed")
        self.assertEqual(
            rpc.call_args.args[1]["p_relationship_state"],
            "follow",
        )

    def test_v2_rejects_unknown_candidate_classification(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported"):
            supabase_client.record_unfollow_candidate_availability_v2(
                "account-1",
                "candidate",
                source_run_id="run-1",
                classification="unfollowed",
                reason="wrong",
            )

    def test_phase_circuit_breaker_is_unfollow_scoped_and_bounded(self) -> None:
        with patch.object(
            supabase_client,
            "_call_rpc",
            return_value={"ok": True, "status": "open"},
        ) as rpc:
            supabase_client.record_unfollow_phase_circuit_breaker_v1(
                "account-1",
                source_run_id="run-1",
                stable_reason=(
                    "unfollow_search_surface_consecutive_failure_limit_reached"
                ),
                technical_failure_count=3,
                usernames=[f"candidate_{index}" for index in range(20)],
            )
        self.assertEqual(
            rpc.call_args.args[0],
            "record_unfollow_phase_circuit_breaker_v1",
        )
        self.assertEqual(len(rpc.call_args.args[1]["p_usernames"]), 10)

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

    def test_phase_circuit_blocks_immediate_resume_but_keeps_partial_state(self) -> None:
        outcome = build_unfollow_outcome(
            stable_reason=(
                "unfollow_search_surface_consecutive_failure_limit_reached"
            ),
            raw_candidate_count=3,
            eligible_candidate_count=3,
            planned_candidate_count=3,
            attempted_count=0,
            verified_count=0,
            persisted_count=0,
            tracker=None,
        )
        self.assertFalse(outcome["resume_recommended"])
        self.assertTrue(outcome["phase_circuit_open"])
        self.assertEqual(outcome["resume_strategy"], "wait_until_next_retry_at")


if __name__ == "__main__":
    unittest.main()
