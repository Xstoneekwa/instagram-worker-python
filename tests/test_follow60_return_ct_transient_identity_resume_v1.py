from __future__ import annotations

import unittest
from unittest import mock

import account_session_orchestrator as aso
import instagram_navigation as nav


class _Device:
    def app_current(self):
        return {
            "package": "com.instagram.android",
            "activity": "com.instagram.mainactivity.InstagramMainActivity",
        }

    def press(self, _key):
        return None


class Follow60ReturnCtTransientIdentityResumeTests(unittest.TestCase):
    def test_ct_username_render_is_reobserved_without_second_ui_action(self):
        device = _Device()
        observed = iter(["", "", "source.ct"])

        with mock.patch.object(nav, "detect_followers_list_screen_fresh", return_value=({"is_followers_list": False}, "")), mock.patch.object(
            nav, "verify_profile", return_value=True
        ), mock.patch.object(
            nav,
            "read_current_profile_username_for_follow_gate",
            side_effect=lambda _d: next(observed),
        ), mock.patch.object(
            nav, "open_followers_list_from_profile", return_value=(True, {})
        ) as reopen, mock.patch.object(
            nav, "_profile_signal_b_username_top_band", return_value=None
        ), mock.patch.object(
            nav.time, "sleep", return_value=None
        ):
            ok, method = nav.return_to_followers_list(
                device,
                "source.ct",
                "com.instagram.android",
                max_retries=0,
            )

        self.assertTrue(ok)
        self.assertEqual(method, "reopen_from_source_profile")
        reopen.assert_called_once()

    def test_ct_username_missing_remains_fail_closed_after_bounded_rechecks(self):
        device = _Device()
        reads = []

        def missing(_device):
            reads.append(1)
            return ""

        with mock.patch.object(nav, "detect_followers_list_screen_fresh", return_value=({"is_followers_list": False}, "")), mock.patch.object(
            nav, "verify_profile", return_value=True
        ), mock.patch.object(
            nav, "read_current_profile_username_for_follow_gate", side_effect=missing
        ), mock.patch.object(
            nav, "open_followers_list_from_profile"
        ) as reopen, mock.patch.object(
            nav, "_profile_signal_b_username_top_band", return_value=None
        ), mock.patch.object(
            nav.time, "sleep", return_value=None
        ), mock.patch.object(
            nav, "_followers_entry_search_surface_recovery_fallback_enabled", return_value=False
        ):
            ok, method = nav.return_to_followers_list(
                device,
                "source.ct",
                "com.instagram.android",
                max_retries=0,
            )

        self.assertFalse(ok)
        self.assertEqual(method, "followers_surface_reacquisition_unproved")
        self.assertEqual(len(reads), 4)
        reopen.assert_not_called()

    def test_transient_return_ct_exit_is_resumable_with_remaining_quota(self):
        termination = aso._session_termination_class(
            session_status="failed",
            follow_phase_executed=True,
            follow_exit_code=42,
            follow_quota_remaining=78,
            follow_to_unfollow_diagnostic={},
            follow_to_unfollow_real={},
            follow_phase_skipped_reason=None,
            transition_reason="welcome_disabled_bypass",
            follow_session_outcome="partial_resumable",
        )
        eligibility = aso._restart_eligibility(
            session_termination_class=termination,
            follow_quota_remaining=78,
            follow_to_unfollow_diagnostic={},
            follow_to_unfollow_real={},
        )

        self.assertEqual(termination, "partial_resumable")
        self.assertEqual(eligibility, ("eligible", "quota_remaining"))

    def test_other_exit_42_remains_non_recoverable(self):
        termination = aso._session_termination_class(
            session_status="failed",
            follow_phase_executed=True,
            follow_exit_code=42,
            follow_quota_remaining=78,
            follow_to_unfollow_diagnostic={},
            follow_to_unfollow_real={},
            follow_phase_skipped_reason=None,
            transition_reason="welcome_disabled_bypass",
            follow_session_outcome="",
        )
        self.assertEqual(termination, "non_recoverable_failure")


if __name__ == "__main__":
    unittest.main()
