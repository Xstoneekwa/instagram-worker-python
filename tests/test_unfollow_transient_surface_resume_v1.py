import unittest
from unittest.mock import Mock, patch

import account_session_orchestrator as account_session
import own_following_navigation as following_navigation


def _transient_detection():
    return {
        "is_following_list": False,
        "failure_reason": "following_usernames_missing",
        "following_tab_active": True,
        "follow_list_container_present": False,
        "recycler_present": True,
        "listview_present": True,
        "following_list_end_detected": False,
        "usernames_visible_count": 0,
        "active_tab_text": "252 following",
    }


def _loaded_detection():
    return {
        **_transient_detection(),
        "is_following_list": True,
        "failure_reason": "",
        "usernames_visible_count": 8,
    }


class UnfollowTransientSurfaceResumeTests(unittest.TestCase):
    @patch.object(following_navigation.time, "sleep")
    @patch.object(following_navigation, "_tap_profile_following_stat")
    @patch.object(following_navigation, "verify_own_profile")
    @patch.object(following_navigation, "open_own_profile_from_bottom_nav")
    @patch.object(following_navigation, "detect_own_following_list_screen")
    def test_transient_empty_rows_are_reobserved_without_second_tap(
        self, detect, open_profile, verify_profile, tap_following, sleep
    ):
        detect.side_effect = [_transient_detection(), _loaded_detection()]
        open_profile.return_value = True
        verify_profile.return_value = (True, {})
        tap_following.return_value = (True, {"tap_method": "resource_id"})

        ok, meta = following_navigation.open_own_following_list_from_own_profile(
            Mock(), "example_account"
        )

        self.assertTrue(ok)
        self.assertEqual(meta["det"]["transient_empty_rechecks"], 1)
        self.assertEqual(tap_following.call_count, 1)
        self.assertEqual(detect.call_count, 2)

    @patch.object(following_navigation.time, "sleep")
    @patch.object(following_navigation, "_tap_profile_following_stat")
    @patch.object(following_navigation, "verify_own_profile")
    @patch.object(following_navigation, "open_own_profile_from_bottom_nav")
    @patch.object(following_navigation, "detect_own_following_list_screen")
    def test_transient_empty_rows_have_bounded_retry_budget(
        self, detect, open_profile, verify_profile, tap_following, sleep
    ):
        detect.side_effect = [_transient_detection()] * 4
        open_profile.return_value = True
        verify_profile.return_value = (True, {})
        tap_following.return_value = (True, {"tap_method": "resource_id"})

        ok, meta = following_navigation.open_own_following_list_from_own_profile(
            Mock(), "example_account"
        )

        self.assertFalse(ok)
        self.assertEqual(meta["det"]["transient_empty_rechecks"], 3)
        self.assertEqual(tap_following.call_count, 1)
        self.assertEqual(detect.call_count, 4)

    def test_failed_unfollow_surface_with_backlog_is_resumable(self):
        real = {
            "executed": True,
            "status": "failed_open_following",
            "failure_reason": "following_usernames_missing",
            "last_run_remaining_eligible": 87,
            "unfollow_actions_sent": 0,
            "unfollow_actions_verified": 0,
            "unfollow_results_persisted_count": 0,
        }
        termination = account_session._session_termination_class(
            session_status="failed",
            follow_phase_executed=True,
            follow_exit_code=0,
            follow_quota_remaining=0,
            follow_to_unfollow_diagnostic={},
            follow_to_unfollow_real=real,
            follow_phase_skipped_reason=None,
            transition_reason="welcome_disabled_bypass",
        )
        self.assertEqual(termination, "partial_resumable")
        eligibility = account_session._restart_eligibility(
            session_termination_class=termination,
            follow_quota_remaining=0,
            follow_to_unfollow_diagnostic={},
            follow_to_unfollow_real=real,
        )
        self.assertEqual(
            eligibility,
            ("eligible", "unfollow_surface_transient_backlog_remaining"),
        )

    def test_no_backlog_does_not_invent_resume(self):
        real = {
            "executed": True,
            "status": "failed_open_following",
            "last_run_remaining_eligible": 0,
            "unfollow_actions_sent": 0,
        }
        termination = account_session._session_termination_class(
            session_status="failed",
            follow_phase_executed=True,
            follow_exit_code=0,
            follow_quota_remaining=0,
            follow_to_unfollow_diagnostic={},
            follow_to_unfollow_real=real,
            follow_phase_skipped_reason=None,
            transition_reason="welcome_disabled_bypass",
        )
        self.assertEqual(termination, "completed")


if __name__ == "__main__":
    unittest.main()
