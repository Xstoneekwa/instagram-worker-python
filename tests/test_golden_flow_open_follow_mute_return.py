import unittest
from unittest.mock import MagicMock, patch

import config
import instagram_navigation as nav


class GoldenFlowModeHelpersTest(unittest.TestCase):
    def test_search_recovery_default_is_fallback_not_pre_entry(self) -> None:
        with patch.object(config, "FOLLOWERS_ENTRY_SEARCH_SURFACE_RECOVERY_MODE", "fallback"):
            self.assertFalse(nav._followers_entry_search_surface_recovery_pre_entry_enabled())
            self.assertTrue(nav._followers_entry_search_surface_recovery_fallback_enabled())

    def test_search_recovery_always_enables_pre_entry(self) -> None:
        with patch.object(config, "FOLLOWERS_ENTRY_SEARCH_SURFACE_RECOVERY_MODE", "always"):
            self.assertTrue(nav._followers_entry_search_surface_recovery_pre_entry_enabled())

    def test_return_ct_stale_action_bar_default_off(self) -> None:
        with patch.object(config, "POST_FOLLOW_RETURN_CT_STALE_ACTION_BAR_MODE", "off"):
            self.assertFalse(nav._post_follow_return_ct_allow_stale_action_bar(golden_strict_failed=True))

    def test_return_ct_stale_action_bar_fallback_only_after_strict_fail(self) -> None:
        with patch.object(config, "POST_FOLLOW_RETURN_CT_STALE_ACTION_BAR_MODE", "fallback"):
            self.assertFalse(nav._post_follow_return_ct_allow_stale_action_bar(golden_strict_failed=False))
            self.assertTrue(nav._post_follow_return_ct_allow_stale_action_bar(golden_strict_failed=True))


class GoldenOpenFollowersListNominalTest(unittest.TestCase):
    @patch.object(nav, "_followers_entry_maybe_recover_ct_profile_from_search_surface")
    @patch.object(nav, "verify_app_foreground", return_value=True)
    @patch.object(nav, "_try_followers_entry_fast_path_from_profile", return_value=("failed", {"reason": "forced"}))
    @patch.object(nav, "_followers_open_emit_failure")
    @patch.object(nav, "_followers_open_build_failure_meta", return_value={"failure_reason": "forced"})
    @patch.object(nav, "_followers_debug_capture", return_value={})
    @patch.object(nav, "_guess_profile_screen", return_value="profile")
    @patch.object(nav, "_followers_current_pkg_activity", return_value={})
    def test_open_followers_list_skips_pre_entry_search_recovery_in_fallback_mode(
        self,
        *_mocks,
    ) -> None:
        device = MagicMock()
        with patch.object(config, "FOLLOWERS_ENTRY_SEARCH_SURFACE_RECOVERY_MODE", "fallback"), patch.object(
            config,
            "ENABLE_FOLLOWERS_ENTRY_ENGINE_V2",
            True,
        ), patch.object(
            nav,
            "followers_session_list_committed_open_for",
            return_value=False,
        ):
            nav.open_followers_list_from_profile(
                device,
                "pelloux_sports_megeve",
                "com.instagram.androie",
                profile_verified=True,
            )
        nav._followers_entry_maybe_recover_ct_profile_from_search_surface.assert_not_called()


class GoldenReturnFollowersListNominalTest(unittest.TestCase):
    @patch.object(nav, "open_followers_list_from_profile", return_value=(True, {}))
    @patch.object(nav, "verify_profile", return_value=True)
    @patch.object(nav, "detect_followers_list_screen", return_value={"is_followers_list": False})
    @patch.object(nav, "_followers_entry_maybe_recover_ct_profile_from_search_surface")
    def test_return_to_followers_list_uses_golden_reopen_before_search_recovery(
        self,
        search_rec_mock,
        *_mocks,
    ) -> None:
        device = MagicMock()
        with patch.object(config, "FOLLOWERS_ENTRY_SEARCH_SURFACE_RECOVERY_MODE", "fallback"):
            ok, reason = nav.return_to_followers_list(
                device,
                "pelloux_sports_megeve",
                "com.instagram.androie",
                max_retries=0,
            )
        self.assertTrue(ok)
        self.assertEqual(reason, "reopen_from_source_profile")
        search_rec_mock.assert_not_called()


class GoldenReturnCtNominalTest(unittest.TestCase):
    def test_list_confirmed_nominal_rejects_stale_action_bar_when_mode_off(self) -> None:
        det = {
            "is_followers_list": True,
            "action_bar_title": "candidate_handle",
            "own_unified_followers_list_detected": True,
            "open_detection_method": "own_unified_follow_list",
            "follow_list_username_count": 3,
            "has_tab_layout": True,
            "signals": ["selected_followers_tab"],
        }
        with patch.object(nav, "detect_followers_list_screen", return_value=det), patch.object(
            nav,
            "verify_followers_list_surface_is_ct_account",
            return_value=False,
        ), patch.object(config, "POST_FOLLOW_RETURN_CT_STALE_ACTION_BAR_MODE", "off"):
            ok, _, _ = nav.post_follow_controlled_return_to_followers_list(
                MagicMock(),
                pkg="com.instagram.androie",
                source_profile_username="ct_source",
                follower_username="candidate_handle",
                visual_candidate_id="vc-1",
                det={},
                compact_after_follow_verified_mute=True,
            )
        self.assertFalse(ok)


class GoldenMuteWrapperUntouchedTest(unittest.TestCase):
    def test_mute_engine_v2_entrypoints_still_present(self) -> None:
        self.assertTrue(callable(nav.run_mute_engine_v2))
        self.assertTrue(callable(nav.run_visual_candidate_post_follow_phase))


if __name__ == "__main__":
    unittest.main()
