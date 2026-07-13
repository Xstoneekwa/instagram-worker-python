from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import welcome_list_sender as sender


class WelcomeListSenderRestoreTest(unittest.TestCase):
    def test_restore_scrolls_in_place_when_followers_surface_committed(self) -> None:
        device = MagicMock()
        det_followers = {"is_followers_list": True, "open_detection_method": "own_unified_follow_list"}
        with (
            patch.object(sender, "detect_followers_list_screen_fresh", side_effect=[(det_followers, {}), (det_followers, {})]),
            patch.object(sender, "scroll_followers_list_backward", return_value=True) as scroll_mock,
            patch.object(sender, "followers_clear_detect_hierarchy_cache"),
            patch.object(sender, "followers_refresh_detect_hierarchy_cache"),
            patch.object(sender, "followers_session_clear_list_committed_open") as clear_mock,
        ):
            ok, method = sender._restore_followers_list_to_scan_start_zone(
                device,
                account_username="cinema_catchup",
                pkg="com.instagram.android",
                scan_final_screen_index=1,
            )

        self.assertTrue(ok)
        self.assertEqual(method, "followers_scroll_backward_to_scan_start")
        scroll_mock.assert_called_once()
        clear_mock.assert_not_called()

    def test_restore_clears_committed_open_before_profile_reopen_fallback(self) -> None:
        device = MagicMock()
        det_profile = {
            "is_followers_list": False,
            "action_bar_title": "cinema_catchup",
            "current_screen_guess": "likely_profile",
        }
        with (
            patch.object(sender, "detect_followers_list_screen_fresh", return_value=(det_profile, {})),
            patch.object(sender, "scroll_followers_list_backward") as scroll_mock,
            patch.object(sender, "followers_session_clear_list_committed_open") as clear_mock,
            patch.object(sender, "followers_clear_detect_hierarchy_cache"),
            patch.object(sender, "followers_refresh_detect_hierarchy_cache"),
            patch(
                "own_profile_navigation.open_own_followers_list_from_own_profile",
                return_value=(True, {}),
            ),
        ):
            ok, method = sender._restore_followers_list_to_scan_start_zone(
                device,
                account_username="cinema_catchup",
                pkg="com.instagram.android",
                scan_final_screen_index=1,
            )

        self.assertTrue(ok)
        self.assertEqual(method, "profile_back_reopen_followers")
        scroll_mock.assert_not_called()
        clear_mock.assert_called_once_with("cinema_catchup")

    def test_sender_entry_recovers_followers_surface_before_planning(self) -> None:
        device = MagicMock()
        det_followers = {
            "is_followers_list": True,
            "open_detection_method": "own_unified_follow_list",
        }
        with (
            patch.object(
                sender,
                "_verify_followers_surface",
                side_effect=[(False, {}), (True, {})],
            ) as verify_mock,
            patch.object(sender, "detect_followers_list_screen_fresh", return_value=(det_followers, {})),
            patch.object(sender, "followers_session_clear_list_committed_open") as clear_mock,
            patch.object(sender, "followers_clear_detect_hierarchy_cache"),
            patch(
                "own_profile_navigation.open_own_followers_list_from_own_profile",
                return_value=(True, {"method": "canonical"}),
            ) as open_mock,
        ):
            ok, meta = sender._ensure_sender_entry_followers_surface(
                device,
                account_username="cinema_catchup",
                pkg="com.instagram.android",
            )

        self.assertTrue(ok)
        self.assertTrue(meta["recovered"])
        verify_mock.assert_called_once()
        clear_mock.assert_called_once_with("cinema_catchup")
        open_mock.assert_called_once()

    def test_sender_entry_fails_closed_when_recovery_cannot_reopen_followers(self) -> None:
        device = MagicMock()
        with (
            patch.object(sender, "_verify_followers_surface", return_value=(False, {})),
            patch.object(sender, "followers_session_clear_list_committed_open"),
            patch(
                "own_profile_navigation.open_own_followers_list_from_own_profile",
                return_value=(False, {"failure_reason": "profile_not_open"}),
            ),
        ):
            ok, meta = sender._ensure_sender_entry_followers_surface(
                device,
                account_username="cinema_catchup",
                pkg="com.instagram.android",
            )

        self.assertFalse(ok)
        self.assertFalse(meta["recovered"])

    def test_post_job_recovery_opens_own_followers_after_send_failure_profile_drift(self) -> None:
        device = MagicMock()
        det_lost = {
            "is_followers_list": False,
            "action_bar_title": "verslaresilience_",
            "current_screen_guess": "likely_profile",
        }
        det_followers = {
            "is_followers_list": True,
            "action_bar_title": "Followers",
            "open_detection_method": "own_unified_follow_list",
        }
        with (
            patch.object(sender, "is_dm_thread_screen", return_value=False),
            patch.object(
                sender,
                "detect_followers_list_screen_fresh",
                side_effect=[
                    (det_lost, {}),
                    (det_lost, {}),
                    (det_lost, {}),
                    (det_followers, {}),
                ],
            ),
            patch("instagram_navigation.tap_instagram_action_bar_back_button", return_value=(True, "action_bar")),
            patch.object(sender, "followers_session_clear_list_committed_open") as clear_mock,
            patch("own_profile_navigation.open_own_profile_from_bottom_nav", return_value=True) as profile_mock,
            patch(
                "own_profile_navigation.open_own_followers_list_from_own_profile",
                return_value=(True, {"open_method": "canonical"}),
            ) as followers_mock,
            patch.object(sender, "followers_clear_detect_hierarchy_cache"),
            patch.object(sender, "followers_refresh_detect_hierarchy_cache"),
        ):
            ok = sender._restore_followers_after_job(
                device,
                "avoga_aventure_travel",
                pkg="com.instagram.android",
                account_username="i_m_your_traker",
            )

        self.assertTrue(ok)
        clear_mock.assert_called_once_with("i_m_your_traker")
        profile_mock.assert_called_once()
        followers_mock.assert_called_once()

    def test_post_job_recovery_fails_closed_when_followers_cannot_be_restored(self) -> None:
        device = MagicMock()
        det_lost = {
            "is_followers_list": False,
            "action_bar_title": "verslaresilience_",
            "current_screen_guess": "likely_profile",
        }
        with (
            patch.object(sender, "is_dm_thread_screen", return_value=False),
            patch.object(sender, "detect_followers_list_screen_fresh", return_value=(det_lost, {})),
            patch("instagram_navigation.tap_instagram_action_bar_back_button", return_value=(False, "missing")),
            patch.object(sender, "followers_session_clear_list_committed_open"),
            patch("own_profile_navigation.open_own_profile_from_bottom_nav", return_value=True),
            patch(
                "own_profile_navigation.open_own_followers_list_from_own_profile",
                return_value=(False, {"failure_reason": "followers_list_not_detected"}),
            ),
        ):
            ok = sender._restore_followers_after_job(
                device,
                "avoga_aventure_travel",
                pkg="com.instagram.android",
                account_username="i_m_your_traker",
            )

        self.assertFalse(ok)

    def test_skip_current_scan_session_jobs_targets_scan_enqueued_only(self) -> None:
        scan_summary = {
            "new_follower_job_ids_enqueued": [
                {"job_id": "job-a", "username": "user_a"},
                {"job_id": "job-b", "username": "user_b"},
            ]
        }
        with patch.object(sender.supabase_client, "complete_dm_job") as complete_mock:
            count = sender._skip_current_scan_session_jobs(
                scan_summary,
                run_id="run-1",
                reason="welcome_sender_failed_before_send",
                last_error="followers_surface_lost",
            )

        self.assertEqual(count, 2)
        self.assertEqual(complete_mock.call_count, 2)
        complete_mock.assert_any_call(
            "job-a",
            "skipped",
            skip_reason="welcome_sender_failed_before_send",
            last_error="followers_surface_lost",
            metadata_patch={
                "cleanup_run_id": "run-1",
                "cleanup_reason": "welcome_sender_failed_before_send",
            },
        )

    def _run_sender_with_results(self, results: list[dict]) -> tuple[int, dict, MagicMock]:
        device = MagicMock()
        jobs = [
            {"id": "job-a", "recipient_username": "pmwzstella", "dm_type": "welcome"},
            {"id": "job-b", "recipient_username": "espehair", "dm_type": "welcome"},
        ]
        scan_summary = {
            "candidate_attempt_cap": 2,
            "effective_welcome_sent_cap": 1,
            "new_follower_job_ids_enqueued": [
                {"job_id": "job-a", "username": "pmwzstella", "planned_index": 0},
                {"job_id": "job-b", "username": "espehair", "planned_index": 1},
            ],
            "new_follower_visible_rows_enqueued": [],
        }
        execute_mock = MagicMock(side_effect=results)
        verify_surface_mock = MagicMock(return_value=(True, {}))
        with (
            patch.object(sender, "resolve_welcome_dm_real_send_enabled", return_value=(True, "test")),
            patch.object(sender, "_reset_dm_sender_session_abort"),
            patch.object(sender, "_resolve_reserved_by", return_value="RFGL145VCKE"),
            patch.object(sender, "_resolve_dm_sender_only_job_id", return_value=("", "none")),
            patch.object(sender, "_verify_followers_surface", verify_surface_mock),
            patch.object(sender, "followers_refresh_detect_hierarchy_cache"),
            patch.object(sender, "_sender_start_visible_usernames", return_value=(["pmwzstella", "espehair"], {})),
            patch.object(sender, "_claim_job_for_run", side_effect=jobs),
            patch.object(sender.supabase_client, "get_account_dm_settings", return_value={}),
            patch.object(sender, "execute_welcome_list_job", execute_mock),
            patch.object(sender, "_dm_sender_session_should_abort", return_value=False),
        ):
            code, summary = sender.run_welcome_list_sender(
                device,
                account_id="acct-1",
                account_username="j_automatise_pour_toi",
                run_id="run-1",
                max_jobs=1,
                scan_summary=scan_summary,
            )
        self._last_verify_surface_mock = verify_surface_mock
        return code, summary, execute_mock

    def test_sender_continues_after_non_dmable_skip_and_sends_second(self) -> None:
        code, summary, execute_mock = self._run_sender_with_results(
            [
                {
                    "outcome": "skipped",
                    "thread_state": "dm_not_available",
                    "list_navigation_ms": 10,
                    "dm_send_ms": 0,
                },
                {
                    "outcome": "sent",
                    "thread_state": "empty_new_thread",
                    "followers_surface_restored": True,
                    "list_navigation_ms": 20,
                    "dm_send_ms": 30,
                },
            ]
        )

        self.assertEqual(code, 0)
        self.assertEqual(summary["sender_status"], "success")
        self.assertEqual(summary["jobs_claimed_count"], 2)
        self.assertEqual(summary["jobs_sent_count"], 1)
        self.assertEqual(summary["jobs_skipped_count"], 1)
        self.assertEqual(summary["loop_exit_reason"], "sent_cap_reached")
        self.assertEqual(summary["recipients_sent"], ["espehair"])
        self.assertEqual(summary["recipients_skipped"], ["pmwzstella"])
        self.assertEqual(execute_mock.call_count, 2)

    def test_sender_stops_after_first_sent_and_does_not_attempt_second(self) -> None:
        code, summary, execute_mock = self._run_sender_with_results(
            [
                {
                    "outcome": "sent",
                    "thread_state": "empty_new_thread",
                    "followers_surface_restored": True,
                    "list_navigation_ms": 20,
                    "dm_send_ms": 30,
                },
                AssertionError("second job should not be attempted"),
            ]
        )

        self.assertEqual(code, 0)
        self.assertEqual(summary["sender_status"], "success")
        self.assertEqual(summary["jobs_claimed_count"], 1)
        self.assertEqual(summary["jobs_sent_count"], 1)
        self.assertEqual(summary["jobs_skipped_count"], 0)
        self.assertEqual(summary["loop_exit_reason"], "sent_cap_reached")
        self.assertEqual(execute_mock.call_count, 1)
        self.assertEqual(self._last_verify_surface_mock.call_count, 2)

    def test_sender_sent_cap_partial_finalize_keeps_post_job_surface_check(self) -> None:
        code, summary, execute_mock = self._run_sender_with_results(
            [
                {
                    "outcome": "sent",
                    "thread_state": "empty_new_thread",
                    "post_finalize_partial": True,
                    "list_navigation_ms": 20,
                    "dm_send_ms": 30,
                },
                AssertionError("second job should not be attempted"),
            ]
        )

        self.assertEqual(code, 0)
        self.assertEqual(summary["sender_status"], "success")
        self.assertEqual(summary["jobs_sent_count"], 1)
        self.assertEqual(summary["loop_exit_reason"], "sent_cap_reached")
        self.assertEqual(execute_mock.call_count, 1)
        self.assertEqual(self._last_verify_surface_mock.call_count, 3)

    def _execute_job_with_send_finalize(self, failure_reason: str | None):
        job = {
            "id": "job-1",
            "recipient_username": "recipient",
            "dm_type": "welcome",
            "message_body": "Salut",
        }
        restore_mock = MagicMock(return_value=True)
        with (
            patch.object(sender.supabase_client, "mark_dm_job_running", return_value=job),
            patch.object(sender, "_check_dm_sender_permission_blocker", return_value=False),
            patch.object(sender, "_navigate_followers_row_to_dm", return_value=("empty_new_thread", True, {})),
            patch.object(sender, "_evaluate_welcome_sendability", return_value=(True, None)),
            patch.object(sender, "_perform_real_welcome_dm_send", return_value=(True, {"sent": True}, failure_reason)),
            patch.object(sender.supabase_client, "complete_dm_job", return_value={**job, "status": "sent"}) as complete_mock,
            patch.object(sender, "_restore_followers_after_job", restore_mock),
        ):
            result = sender.execute_welcome_list_job(
                MagicMock(),
                job,
                settings={},
                account_id="acct-1",
                account_username="j_automatise_pour_toi",
                scan_anchors={},
            )
        self._last_complete_dm_job_mock = complete_mock
        return result, restore_mock

    def test_execute_sent_job_skips_final_restore_when_send_finalize_restored_followers(self) -> None:
        result, restore_mock = self._execute_job_with_send_finalize(None)

        self.assertEqual(result["outcome"], "sent")
        self.assertTrue(result["followers_surface_restored"])
        restore_mock.assert_not_called()

    def test_execute_sent_job_records_strong_outbound_proof_metadata(self) -> None:
        result, _restore_mock = self._execute_job_with_send_finalize(None)

        self.assertEqual(result["outcome"], "sent")
        complete_call = self._last_complete_dm_job_mock.call_args
        metadata_patch = complete_call.kwargs["metadata_patch"]
        self.assertEqual(metadata_patch["send_verification_status"], "verified")
        self.assertTrue(metadata_patch["outbound_bubble_evidence_found"])

    def test_execute_sent_job_keeps_final_restore_when_send_finalize_partial(self) -> None:
        result, restore_mock = self._execute_job_with_send_finalize("post_finalize_partial")

        self.assertEqual(result["outcome"], "sent")
        self.assertTrue(result["post_finalize_partial"])
        self.assertFalse(result["followers_surface_restored"])
        restore_mock.assert_called_once()

    def test_execute_duplicate_prevented_does_not_complete_sent_job(self) -> None:
        job = {
            "id": "job-1",
            "recipient_username": "recipient",
            "dm_type": "welcome",
            "message_body": "Salut",
        }
        with (
            patch.object(sender.supabase_client, "mark_dm_job_running", return_value=job),
            patch.object(sender, "_check_dm_sender_permission_blocker", return_value=False),
            patch.object(sender, "_navigate_followers_row_to_dm", return_value=("empty_new_thread", True, {})),
            patch.object(sender, "_evaluate_welcome_sendability", return_value=(True, None)),
            patch.object(
                sender,
                "_perform_real_welcome_dm_send",
                return_value=(True, {"sent": True, "duplicate_prevented": True}, None),
            ),
            patch.object(
                sender,
                "_complete_job_failed_retry",
                return_value=({**job, "status": "pending"}, "failed_retry"),
            ) as retry_mock,
            patch.object(sender.supabase_client, "complete_dm_job") as complete_mock,
            patch.object(sender, "_restore_followers_after_job", return_value=True),
        ):
            result = sender.execute_welcome_list_job(
                MagicMock(),
                job,
                settings={},
                account_id="acct-1",
                account_username="j_automatise_pour_toi",
                scan_anchors={},
            )

        self.assertEqual(result["outcome"], "failed_retry")
        self.assertEqual(
            retry_mock.call_args.kwargs.get("last_error"),
            "send_without_strong_outbound_proof",
        )
        complete_mock.assert_not_called()

    def test_execute_unverified_send_does_not_complete_sent_job(self) -> None:
        job = {
            "id": "job-1",
            "recipient_username": "recipient",
            "dm_type": "welcome",
            "message_body": "Salut",
        }
        with (
            patch.object(sender.supabase_client, "mark_dm_job_running", return_value=job),
            patch.object(sender, "_check_dm_sender_permission_blocker", return_value=False),
            patch.object(
                sender,
                "_navigate_followers_row_to_dm",
                return_value=("empty_new_thread", True, {}),
            ),
            patch.object(sender, "_evaluate_welcome_sendability", return_value=(True, None)),
            patch.object(
                sender,
                "_perform_real_welcome_dm_send",
                return_value=(False, {"sent": False, "reason": "send_unverified"}, "send_unverified"),
            ),
            patch.object(
                sender,
                "_complete_job_failed_retry",
                return_value=({**job, "status": "pending"}, "failed_retry"),
            ) as retry_mock,
            patch.object(sender.supabase_client, "complete_dm_job") as complete_mock,
            patch.object(sender, "_restore_followers_after_job", return_value=True),
        ):
            result = sender.execute_welcome_list_job(
                MagicMock(),
                job,
                settings={},
                account_id="acct-1",
                account_username="j_automatise_pour_toi",
                scan_anchors={},
            )

        self.assertEqual(result["outcome"], "failed_retry")
        retry_mock.assert_called_once()
        self.assertEqual(retry_mock.call_args.kwargs.get("last_error"), "send_unverified")
        complete_mock.assert_not_called()

    def test_sender_all_non_dmable_skips_is_not_false_success(self) -> None:
        code, summary, execute_mock = self._run_sender_with_results(
            [
                {
                    "outcome": "skipped",
                    "thread_state": "dm_not_available",
                    "list_navigation_ms": 10,
                    "dm_send_ms": 0,
                },
                {
                    "outcome": "skipped",
                    "thread_state": "restricted_account",
                    "list_navigation_ms": 10,
                    "dm_send_ms": 0,
                },
            ]
        )

        self.assertEqual(code, 0)
        self.assertEqual(summary["sender_status"], "partial_success")
        self.assertEqual(summary["jobs_sent_count"], 0)
        self.assertEqual(summary["jobs_skipped_count"], 2)
        self.assertEqual(summary["loop_exit_reason"], "attempt_cap_reached")
        self.assertEqual(execute_mock.call_count, 2)


if __name__ == "__main__":
    unittest.main()
