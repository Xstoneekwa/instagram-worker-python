from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import account_session_orchestrator
import welcome_list_sender as sender


class WelcomeConsolidatedStateMachineTests(unittest.TestCase):
    def _proof_scan(self) -> dict:
        return {
            "run_id": "3d1dfcea-53de-4259-94c8-f2057a2a8f99",
            "candidate_attempt_cap": 2,
            "effective_welcome_sent_cap": 1,
            "scan_final_screen_index": 1,
            "followers_suggestions_boundary_action": "use_visible_candidate",
            "followers_suggestions_boundary_selected_candidate": "jtm.signature",
            "followers_suggestions_boundary_selected_candidate_is_real_follower": True,
            "new_follower_job_ids_enqueued": [
                {
                    "job_id": "eae34d38-bf8b-4fd5-a8ab-ab4da392af2e",
                    "username": "tanzaniaindelible",
                    "planned_index": 0,
                    "screen_index": 0,
                    "row_index": 1,
                },
                {
                    "job_id": "ffc99ef0-c35f-43ab-a6e8-67c60d5b4f2d",
                    "username": "jtm.signature",
                    "planned_index": 1,
                    "screen_index": 1,
                    "row_index": 4,
                },
            ],
        }

    def test_visible_planned_job_wins_without_destructive_reposition(self) -> None:
        with (
            patch.object(
                sender,
                "_sender_start_visible_usernames",
                return_value=(["jtm.signature"], {"hierarchy_source": "fresh_dump"}),
            ),
            patch.object(sender, "scroll_followers_list_backward") as backward,
            patch.object(sender, "scroll_followers_list_forward") as forward,
        ):
            plan, strategy, meta = sender._resolve_session_sender_plan(
                MagicMock(),
                self._proof_scan(),
                account_username="i_m_your_traker",
                pkg="com.instagram.android",
                max_jobs=1,
                attempt_cap=2,
                scan_anchors={},
            )

        self.assertEqual(strategy, "visible_planned_job_first")
        self.assertEqual(plan[0]["username"], "jtm.signature")
        self.assertEqual(plan[1]["username"], "tanzaniaindelible")
        self.assertEqual(meta["visible_planned_usernames"], ["jtm.signature"])
        self.assertFalse(meta["restore_needed"])
        backward.assert_not_called()
        forward.assert_not_called()

    def test_proof_run_replay_reaches_next_job_ready_in_exact_order(self) -> None:
        scan = self._proof_scan()
        jtm_job = {
            "id": "ffc99ef0-c35f-43ab-a6e8-67c60d5b4f2d",
            "recipient_username": "jtm.signature",
            "dm_type": "welcome",
            "message_body": "Hello jtm.signature",
            "status": "pending",
        }

        def navigate(*_args, **kwargs):
            machine = kwargs["state_machine"]
            for state, proof in (
                ("followers_stable", "fresh_followers_detection"),
                ("planned_row_freshly_resolved", "fresh_exact_username_row"),
                ("target_profile_exact", "exact_profile_username"),
                (
                    "dm_thread_exact",
                    "display_name_title_exact_username_subtitle",
                ),
            ):
                machine.transition(
                    state,
                    proof=proof,
                    owner="welcome_sender_navigation",
                    job_id=jtm_job["id"],
                    username="jtm.signature",
                )
            return (
                "empty_new_thread",
                True,
                {
                    "lookup_path_used": "fresh_visible",
                    "resolved_screen_index": 1,
                    "resolved_navigation_generation": "run:1:fresh",
                },
            )

        def send(*_args, **kwargs):
            self.assertEqual(kwargs["username"], "jtm.signature")
            self.assertEqual(kwargs["message_body"], "Hello jtm.signature")
            transition = kwargs["state_transition"]
            transition("composer_exact", "full_composer_probe")
            transition("draft_exact", "draft_readback_exact")
            transition("send_tapped", "exact_send_selector_tapped")
            transition(
                "outbound_verified",
                "pending_then_stable_outbound_bubble_emoji_neutralized",
            )
            return (
                True,
                {
                    "sent": True,
                    "send_tapped": True,
                    "outbound_pending_observed": True,
                    "outbound_stable_observed": True,
                    "emoji_normalization_used": True,
                },
                None,
            )

        def restore(*_args, **kwargs):
            machine = kwargs["state_machine"]
            machine.transition(
                "thread_exit",
                proof="canonical_thread_exit",
                owner="welcome_sender_post_dm_return",
                job_id=jtm_job["id"],
                username="jtm.signature",
            )
            machine.transition(
                "followers_restored",
                proof="fresh_followers_detection",
                owner="welcome_sender_post_dm_return",
                job_id=jtm_job["id"],
                username="jtm.signature",
            )
            return True

        with (
            patch.object(sender, "resolve_welcome_dm_real_send_enabled", return_value=(True, "test")),
            patch.object(sender, "_reset_dm_sender_session_abort"),
            patch.object(sender, "_resolve_reserved_by", return_value="RFGL145VCKE"),
            patch.object(sender, "_resolve_dm_sender_only_job_id", return_value=("", "none")),
            patch.object(sender, "followers_refresh_detect_hierarchy_cache"),
            patch.object(sender, "_sender_start_visible_usernames", return_value=(["jtm.signature"], {})),
            patch.object(sender, "_verify_followers_surface", return_value=(True, {})),
            patch.object(sender.supabase_client, "get_account_dm_settings", return_value={}),
            patch.object(sender, "_claim_job_for_run", return_value=jtm_job),
            patch.object(sender.supabase_client, "mark_dm_job_running", return_value=jtm_job),
            patch.object(sender, "_check_dm_sender_permission_blocker", return_value=False),
            patch.object(sender, "_navigate_followers_row_to_dm", side_effect=navigate),
            patch.object(sender, "_evaluate_welcome_sendability", return_value=(True, None)),
            patch.object(sender, "_perform_real_welcome_dm_send", side_effect=send),
            patch.object(sender, "_restore_followers_after_job", side_effect=restore) as restore_mock,
            patch.object(sender.supabase_client, "complete_dm_job", return_value={**jtm_job, "status": "sent"}),
            patch.object(sender, "_dm_sender_session_should_abort", return_value=False),
            patch.object(sender, "scroll_followers_list_backward") as backward,
            patch.object(sender, "scroll_followers_list_forward") as forward,
        ):
            code, summary = sender.run_welcome_list_sender(
                MagicMock(),
                account_id="83de9cc9-5c37-42d1-9edc-c924352b17b1",
                account_username="i_m_your_traker",
                run_id=scan["run_id"],
                max_jobs=1,
                scan_summary=scan,
            )

        self.assertEqual(code, 0)
        self.assertEqual(summary["recipients_planned"][0], "jtm.signature")
        self.assertEqual(summary["recipients_sent"], ["jtm.signature"])
        self.assertEqual(summary["loop_exit_reason"], "sent_cap_reached")
        self.assertEqual(
            [entry["state"] for entry in summary["welcome_state_history"]],
            [
                "followers_stable",
                "planned_row_freshly_resolved",
                "target_profile_exact",
                "dm_thread_exact",
                "composer_exact",
                "draft_exact",
                "send_tapped",
                "outbound_verified",
                "thread_exit",
                "followers_restored",
                "next_job_ready",
            ],
        )
        restore_mock.assert_called_once()
        backward.assert_not_called()
        forward.assert_not_called()
        run_follow, reason = account_session_orchestrator._should_run_follow_after_welcome(
            welcome_enabled=True,
            real_send_enabled=True,
            welcome_phase_executed=True,
            welcome_exit_code=0,
            scan_summary={"status": "success"},
            sender_summary=summary,
            welcome_session_status="success",
        )
        self.assertTrue(run_follow)
        self.assertEqual(reason, "welcome_closed_success")


if __name__ == "__main__":
    unittest.main()
