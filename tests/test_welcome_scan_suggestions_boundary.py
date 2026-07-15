from __future__ import annotations

import unittest
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import welcome_scan_producer as scan


SUGGESTIONS_XML = """<hierarchy>
  <node text="28 followers" class="android.widget.Button" selected="true" />
  <node text="See all suggestions" class="android.widget.TextView" />
  <node text="Follow" class="android.widget.Button" />
  <node text="" content-desc="Remove" class="android.widget.ImageView" />
</hierarchy>"""

LOADING_XML = """<hierarchy>
  <node text="28 followers" class="android.widget.Button" selected="true" />
  <node text="" class="android.widget.ProgressBar" />
</hierarchy>"""

STABLE_XML = """<hierarchy>
  <node text="28 followers" class="android.widget.Button" selected="true" />
  <node text="known_follower" resource-id="com.instagram.androie:id/follow_list_username" />
</hierarchy>"""


class WelcomeScanSuggestionsBoundaryTest(unittest.TestCase):
    def _common_patches(self, rows: list[dict]):
        return (
            patch.object(scan.config, "WELCOME_SCAN_CANDIDATE_ATTEMPT_CAP", 10, create=True),
            patch.object(scan.supabase_client, "ensure_account_dm_settings", return_value={
                "welcome_enabled": True,
                "welcome_baseline_completed_at": "2026-07-14T00:00:00Z",
                "welcome_template_id": "template-1",
                "welcome_per_session_limit": 10,
                "welcome_per_day_limit": 10,
                "total_dm_per_day_limit": 10,
            }),
            patch.object(scan.supabase_client, "get_account_dm_counter_today", return_value={}),
            patch.object(scan, "open_own_profile_from_bottom_nav", return_value=True),
            patch.object(scan, "verify_own_profile", return_value=(True, {})),
            patch.object(scan, "open_own_followers_list_from_own_profile", return_value=(True, {"det": {"is_followers_list": True}})),
            patch.object(scan, "harvest_visible_followers_rows", return_value=(rows, {"visible_rows_count": len(rows), "extraction_methods": ["test"]})),
            patch.object(scan.supabase_client, "mark_followbacks_from_seen_followers", return_value={}),
            patch.object(scan.supabase_client, "fetch_pending_welcome_jobs_by_usernames", return_value={}),
        )

    def test_strong_followers_rows_then_suggestions_preserves_four_planned_jobs(self) -> None:
        rows = [
            {
                "username": f"new_follower_{index}",
                "screen_index": 0,
                "row_cta_xml_class": "message",
            }
            for index in range(4)
        ]
        enqueue = MagicMock(side_effect=lambda _account_id, username, **_kwargs: {"id": f"job-{username}", "status": "pending"})
        with ExitStack() as stack:
            for context in self._common_patches(rows):
                stack.enter_context(context)
            stack.enter_context(patch.object(scan.supabase_client, "fetch_followers_by_usernames", return_value={}))
            stack.enter_context(patch.object(scan.supabase_client, "enqueue_welcome_dm_job_if_eligible", enqueue))
            scroll = stack.enter_context(patch.object(scan, "scroll_followers_list_forward", return_value=True))
            log_mock = stack.enter_context(patch.object(scan, "log"))
            stack.enter_context(patch.object(scan, "followers_refresh_detect_hierarchy_cache", return_value=SUGGESTIONS_XML))
            stack.enter_context(patch.object(scan, "detect_followers_list_screen", return_value={"is_followers_list": False, "current_screen_guess": "likely_profile"}))
            code = scan.run_welcome_scan_producer(
                MagicMock(),
                account_id="account-1",
                account_username="i_m_your_traker",
                run_id="run-suggestions",
            )

        summary = scan.get_last_welcome_scan_summary()
        self.assertEqual(code, 0)
        self.assertEqual(summary["stop_reason"], "followers_suggestions_boundary")
        self.assertEqual(summary["jobs_enqueued_count"], 4)
        self.assertTrue(summary["followers_suggestions_boundary_detected"])
        self.assertFalse(summary["followers_suggestions_boundary_backtrack_attempted"])
        self.assertEqual(summary["followers_suggestions_boundary_action"], "use_visible_candidate")
        self.assertTrue(summary["followers_suggestions_boundary_selected_candidate_is_real_follower"])
        self.assertEqual(scroll.call_args.kwargs["scroll_profile"], "welcome_soft")
        planned = next(call for call in log_mock.call_args_list if call.args[1] == "followers_soft_scroll_planned")
        self.assertEqual(planned.kwargs["distance_px"], 585)
        self.assertEqual(planned.kwargs["ratio"], 0.25)

    def test_suggestions_follow_row_is_never_used_as_visible_welcome_candidate(self) -> None:
        rows = [{
            "username": "planned_welcome_user",
            "screen_index": 0,
            "row_cta_xml_class": "follow",
        }]
        enqueue = MagicMock(return_value={"id": "job-planned", "status": "pending"})
        with ExitStack() as stack:
            for context in self._common_patches(rows):
                stack.enter_context(context)
            stack.enter_context(patch.object(scan.supabase_client, "fetch_followers_by_usernames", return_value={}))
            stack.enter_context(patch.object(scan.supabase_client, "enqueue_welcome_dm_job_if_eligible", enqueue))
            stack.enter_context(patch.object(scan, "scroll_followers_list_forward", return_value=True))
            backtrack = stack.enter_context(patch.object(scan, "scroll_followers_list_backward", return_value=True))
            stack.enter_context(patch.object(scan, "followers_refresh_detect_hierarchy_cache", return_value=SUGGESTIONS_XML))
            stack.enter_context(patch.object(
                scan,
                "detect_followers_list_screen",
                side_effect=[
                    {"is_followers_list": False, "current_screen_guess": "likely_profile"},
                    {"is_followers_list": True, "current_screen_guess": "followers_list"},
                ],
            ))
            code = scan.run_welcome_scan_producer(
                MagicMock(),
                account_id="account-1",
                account_username="i_m_your_traker",
                run_id="run-never-use-suggestion",
            )

        summary = scan.get_last_welcome_scan_summary()
        self.assertEqual(code, 0)
        self.assertEqual(summary["followers_suggestions_boundary_action"], "compact_up_recovery")
        self.assertIsNone(summary["followers_suggestions_boundary_selected_candidate"])
        self.assertFalse(summary["followers_suggestions_boundary_selected_candidate_is_real_follower"])
        self.assertEqual(backtrack.call_count, 1)

    def test_loading_boundary_without_jobs_recovers_one_stable_screen(self) -> None:
        rows = [{"username": "known_follower", "screen_index": 0}]
        known = {"known_follower": {"follower_username": "known_follower", "welcome_dm_status": "skipped"}}
        with ExitStack() as stack:
            for context in self._common_patches(rows):
                stack.enter_context(context)
            stack.enter_context(patch.object(scan.supabase_client, "fetch_followers_by_usernames", return_value=known))
            enqueue = stack.enter_context(patch.object(
                scan.supabase_client,
                "enqueue_welcome_dm_job_if_eligible",
                return_value=None,
            ))
            stack.enter_context(patch.object(scan, "scroll_followers_list_forward", return_value=True))
            backtrack = stack.enter_context(patch.object(scan, "scroll_followers_list_backward", return_value=True))
            stack.enter_context(patch.object(scan, "followers_refresh_detect_hierarchy_cache", return_value=LOADING_XML))
            stack.enter_context(patch.object(
                scan,
                "detect_followers_list_screen",
                side_effect=[
                    {"is_followers_list": False, "current_screen_guess": "likely_profile"},
                    {"is_followers_list": True, "current_screen_guess": "followers_list"},
                ],
            ))
            code = scan.run_welcome_scan_producer(
                MagicMock(),
                account_id="account-1",
                account_username="i_m_your_traker",
                run_id="run-loading",
            )

        summary = scan.get_last_welcome_scan_summary()
        self.assertEqual(code, 0)
        self.assertEqual(summary["stop_reason"], "followers_suggestions_boundary")
        self.assertEqual(summary["jobs_enqueued_count"], 0)
        self.assertTrue(summary["followers_suggestions_boundary_detected"])
        self.assertTrue(summary["followers_suggestions_boundary_backtrack_attempted"])
        self.assertEqual(summary["followers_suggestions_boundary_action"], "compact_up_recovery")
        self.assertEqual(summary["followers_suggestions_boundary_final_surface"], "followers_list")
        self.assertEqual(backtrack.call_count, 1)
        self.assertEqual(backtrack.call_args.kwargs["scroll_profile"], "compact")
        enqueue.assert_not_called()

    def test_known_pending_followers_restore_jobs_before_suggestions_boundary(self) -> None:
        rows = [
            {
                "username": f"retryable_{index}",
                "screen_index": 0,
                "row_cta_xml_class": "message",
            }
            for index in range(4)
        ]
        known = {
            row["username"]: {
                "follower_username": row["username"],
                "welcome_dm_status": "pending",
                "skip_reason": "",
            }
            for row in rows
        }
        enqueue = MagicMock(side_effect=lambda _account_id, username, **_kwargs: {"id": f"job-{username}", "status": "pending"})
        with ExitStack() as stack:
            for context in self._common_patches(rows):
                stack.enter_context(context)
            stack.enter_context(patch.object(scan.supabase_client, "fetch_followers_by_usernames", return_value=known))
            stack.enter_context(patch.object(scan.supabase_client, "enqueue_welcome_dm_job_if_eligible", enqueue))
            stack.enter_context(patch.object(scan, "scroll_followers_list_forward", return_value=True))
            stack.enter_context(patch.object(scan, "followers_refresh_detect_hierarchy_cache", return_value=SUGGESTIONS_XML))
            stack.enter_context(patch.object(scan, "detect_followers_list_screen", return_value={"is_followers_list": False, "current_screen_guess": "likely_profile"}))
            code = scan.run_welcome_scan_producer(
                MagicMock(),
                account_id="account-1",
                account_username="i_m_your_traker",
                run_id="run-retryable-suggestions",
            )

        summary = scan.get_last_welcome_scan_summary()
        self.assertEqual(code, 0)
        self.assertEqual(summary["stop_reason"], "followers_suggestions_boundary")
        self.assertEqual(summary["jobs_enqueued_count"], 4)
        self.assertEqual(len(summary["new_follower_job_ids_enqueued"]), 4)
        self.assertEqual(enqueue.call_count, 4)

    def test_boundary_recovery_failure_is_structured_safe_stop(self) -> None:
        rows = [{"username": "known_follower", "screen_index": 0}]
        known = {"known_follower": {"follower_username": "known_follower", "welcome_dm_status": "skipped"}}
        with ExitStack() as stack:
            for context in self._common_patches(rows):
                stack.enter_context(context)
            stack.enter_context(patch.object(scan.supabase_client, "fetch_followers_by_usernames", return_value=known))
            stack.enter_context(patch.object(scan.supabase_client, "enqueue_welcome_dm_job_if_eligible", return_value=None))
            stack.enter_context(patch.object(scan, "scroll_followers_list_forward", return_value=True))
            backtrack = stack.enter_context(patch.object(scan, "scroll_followers_list_backward", return_value=True))
            stack.enter_context(patch.object(scan, "followers_refresh_detect_hierarchy_cache", return_value=SUGGESTIONS_XML))
            stack.enter_context(patch.object(
                scan,
                "detect_followers_list_screen",
                return_value={"is_followers_list": False, "current_screen_guess": "likely_profile"},
            ))
            code = scan.run_welcome_scan_producer(
                MagicMock(),
                account_id="account-1",
                account_username="i_m_your_traker",
                run_id="run-recovery-failed",
            )

        summary = scan.get_last_welcome_scan_summary()
        self.assertEqual(code, 1)
        self.assertEqual(summary["stop_reason"], "followers_suggestions_boundary_recovery_failed")
        self.assertEqual(summary["failure_reason"], "followers_suggestions_boundary_recovery_failed")
        self.assertEqual(summary["followers_suggestions_boundary_action"], "safe_stop")
        self.assertEqual(backtrack.call_count, 1)


if __name__ == "__main__":
    unittest.main()
