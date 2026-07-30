from __future__ import annotations

import unittest
from unittest import mock

import runner


class RunnerPersistPostFollowTest(unittest.TestCase):
    def test_account_session_performance_summary_includes_session_counters(self) -> None:
        with mock.patch.dict(
            runner._SESSION_COUNTERS,
            {"follows": 1, "likes": 0, "interactions": 1, "successful_interactions": 1},
            clear=False,
        ), mock.patch(
            "account_session_orchestrator.get_last_account_session_summary",
            return_value={"unfollow_actions_verified": 0},
        ):
            summary = runner._build_account_session_completion_performance_summary(
                exit_code=0,
                account_username="i_m_your_traker",
                followers_source_username="vipbeach",
                target_id="target-1",
                target_selection_source="ig_targets",
            )

        self.assertEqual(summary["run_type"], "account_session")
        self.assertEqual(summary["session_counters"]["follows"], 1)
        self.assertEqual(summary["session_counters"]["likes"], 0)

    def test_account_session_performance_summary_reconciles_verified_unfollows(self) -> None:
        with mock.patch.dict(
            runner._SESSION_COUNTERS,
            {
                "follows": 50,
                "unfollows": 0,
                "likes": 49,
                "interactions": 99,
                "successful_interactions": 50,
            },
            clear=False,
        ), mock.patch(
            "account_session_orchestrator.get_last_account_session_summary",
            return_value={"unfollow_actions_verified": 36},
        ):
            summary = runner._build_account_session_completion_performance_summary(
                exit_code=0,
                account_username="j_automatise_pour_toi",
                followers_source_username="caseykingphoto",
                target_id="target-1",
                target_selection_source="ig_targets",
            )

        self.assertEqual(summary["session_counters"]["unfollows"], 36)
        self.assertEqual(summary["session_counters"]["interactions"], 135)
        self.assertEqual(summary["session_counters"]["successful_interactions"], 86)

    def test_account_session_performance_summary_does_not_double_count_unfollows(self) -> None:
        with mock.patch.dict(
            runner._SESSION_COUNTERS,
            {
                "unfollows": 6,
                "interactions": 12,
                "successful_interactions": 8,
            },
            clear=False,
        ), mock.patch(
            "account_session_orchestrator.get_last_account_session_summary",
            return_value={"unfollow_actions_verified": 6},
        ):
            summary = runner._build_account_session_completion_performance_summary(
                exit_code=0,
                account_username="account",
                followers_source_username=None,
                target_id=None,
                target_selection_source=None,
            )

        self.assertEqual(summary["session_counters"]["unfollows"], 6)
        self.assertEqual(summary["session_counters"]["interactions"], 12)
        self.assertEqual(summary["session_counters"]["successful_interactions"], 8)

    def test_account_session_persists_exact_welcome_surface_failure(self) -> None:
        with mock.patch(
            "welcome_list_sender.get_last_welcome_list_sender_summary",
            return_value={
                "failure_reason": "followers_surface_missing_at_start",
                "entry_surface_decision": "recovered_snapshot_rejected",
            },
        ), mock.patch(
            "welcome_scan_producer.get_last_welcome_scan_summary",
            return_value={"stop_reason": "followers_surface_lost", "jobs_enqueued_count": 4},
        ):
            summary = runner._build_account_session_completion_performance_summary(
                exit_code=1,
                account_username="i_m_your_traker",
                followers_source_username="dr_dlimi",
                target_id="target-1",
                target_selection_source="ig_targets",
            )

        self.assertEqual(summary["reason"], "recovered_snapshot_rejected")
        self.assertEqual(summary["welcome_sender_failure_reason"], "followers_surface_missing_at_start")
        self.assertEqual(summary["welcome_scan_jobs_enqueued_count"], 4)

    def test_persist_after_post_follow_does_not_use_eng_log(self) -> None:
        with mock.patch.object(
            runner, "follow_persistence_rpc_v1_enabled", return_value=False
        ), mock.patch.object(
            runner, "_timed_safe_supabase_call", return_value={"ok": True}
        ) as supa, mock.patch.object(runner, "log") as log_fn:
            runner._persist_verified_follow_success_to_supabase(
                supabase_mode=True,
                account_id="acct-1",
                follower_un="cand_user",
                source_profile_username="ct_user",
                run_id="run-1",
                follow_out={"ok": True, "skipped_tap": False, "follow_state_after": "following"},
                fs_af="following",
                f_st="following",
                target_id="tgt-1",
                phase="after_post_follow",
            )
        self.assertGreaterEqual(supa.call_count, 1)
        first_call = supa.call_args_list[0]
        self.assertEqual(first_call[0][1], "record_follow_interaction_outcome")
        logged_kinds = [
            (c.kwargs or {}).get("kind")
            for c in log_fn.call_args_list
            if len(c.args) >= 2 and c.args[1] == "social_memory_updated"
        ]
        self.assertIn("follow_success", logged_kinds)

    def test_persist_noop_when_not_supabase(self) -> None:
        with mock.patch.object(runner, "_safe_supabase_call") as supa:
            runner._persist_verified_follow_success_to_supabase(
                supabase_mode=False,
                account_id="acct-1",
                follower_un="cand_user",
                source_profile_username="ct_user",
                run_id="run-1",
                follow_out={"ok": True},
                fs_af="following",
                f_st="following",
                target_id=None,
                phase="after_post_follow",
            )
        supa.assert_not_called()


if __name__ == "__main__":
    unittest.main()
