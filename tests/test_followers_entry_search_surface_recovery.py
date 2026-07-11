import unittest
from unittest.mock import MagicMock, patch

import instagram_navigation as nav


def search_surface_xml(
    *,
    source: str = "pelloux_sports_megeve",
    action_bar_title: str = "Search",
) -> str:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<hierarchy>
  <node class="android.widget.TextView" resource-id="com.instagram.androie:id/action_bar_title" text="{action_bar_title}" bounds="[0,80][1080,180]" />
  <node class="android.widget.EditText" resource-id="com.instagram.androie:id/action_bar_search_edit_text" text="Search" focused="true" bounds="[120,90][960,170]" />
  <node class="android.widget.TextView" resource-id="com.instagram.androie:id/row_search_user_username" text="{source}" bounds="[120,260][900,340]" clickable="true" />
</hierarchy>"""


def profile_surface_xml(*, source: str = "pelloux_sports_megeve") -> str:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<hierarchy>
  <node class="android.widget.TextView" resource-id="com.instagram.androie:id/action_bar_title" text="{source}" bounds="[0,80][1080,180]" />
  <node class="android.view.ViewGroup" resource-id="com.instagram.androie:id/profile_header_followers_stacked_familiar" content-desc="120 followers" bounds="[535,336][794,496]" clickable="true">
    <node class="android.widget.TextView" resource-id="com.instagram.androie:id/profile_header_familiar_followers_label" text="followers" bounds="[552,420][711,473]" />
  </node>
</hierarchy>"""


class FakeSearchRecoveryDevice:
    def __init__(self, hierarchy_xml: str) -> None:
        self.hierarchy_xml = hierarchy_xml
        self.clicks: list[str] = []

    def window_size(self) -> tuple[int, int]:
        return 1080, 2340

    def dump_hierarchy(self, compressed: bool = False) -> str:
        return self.hierarchy_xml

    def __call__(self, **kwargs):
        return MagicMock(wait=MagicMock(return_value=False))


class FollowersEntrySearchSurfaceRecoveryTest(unittest.TestCase):
    def test_search_surface_detected_without_profile_stats_band(self) -> None:
        detected, meta = nav._followers_entry_search_surface_detected_before_profile_entry(
            FakeSearchRecoveryDevice(search_surface_xml()),
            source_profile_username="pelloux_sports_megeve",
            pkg="com.instagram.androie",
            hierarchy_xml=search_surface_xml(),
        )
        self.assertTrue(detected)
        self.assertEqual(meta["reason"], "search_surface_before_profile_entry")
        self.assertFalse(meta["stats_band_present"])

    def test_profile_surface_not_detected_as_search(self) -> None:
        detected, meta = nav._followers_entry_search_surface_detected_before_profile_entry(
            FakeSearchRecoveryDevice(profile_surface_xml()),
            source_profile_username="pelloux_sports_megeve",
            pkg="com.instagram.androie",
            hierarchy_xml=profile_surface_xml(),
        )
        self.assertFalse(detected)
        self.assertEqual(meta["reason"], "profile_already_confirmed")

    @patch.object(nav, "verify_profile", return_value=True)
    @patch.object(nav, "find_first_row_search_username_hot")
    def test_profile_recovery_confirmed_before_followers_entry(
        self,
        mock_find_row,
        _mock_verify,
    ) -> None:
        row = MagicMock()
        mock_find_row.return_value = row
        ok, reason = nav._followers_entry_maybe_recover_ct_profile_from_search_surface(
            FakeSearchRecoveryDevice(search_surface_xml()),
            "pelloux_sports_megeve",
            "com.instagram.androie",
            phase="open_followers_list_from_profile",
            hierarchy_xml=search_surface_xml(),
        )
        self.assertTrue(ok)
        self.assertEqual(reason, "profile_recovery_confirmed")
        row.click.assert_called_once()

    @patch.object(nav, "verify_profile", return_value=False)
    @patch.object(nav, "find_first_row_search_username_hot")
    def test_profile_recovery_failed_when_profile_not_confirmed(
        self,
        mock_find_row,
        _mock_verify,
    ) -> None:
        row = MagicMock()
        mock_find_row.return_value = row
        ok, reason = nav._followers_entry_maybe_recover_ct_profile_from_search_surface(
            FakeSearchRecoveryDevice(search_surface_xml()),
            "pelloux_sports_megeve",
            "com.instagram.androie",
            phase="return_to_followers_list",
            hierarchy_xml=search_surface_xml(),
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "profile_not_confirmed_after_search_row_tap")

    @patch.object(nav, "_followers_open_emit_failure")
    @patch.object(nav, "_followers_open_build_failure_meta", return_value={"failure_reason": "followers_entry_profile_recovery_failed"})
    @patch.object(nav, "_followers_debug_capture", return_value={})
    @patch.object(nav, "verify_app_foreground", return_value=True)
    @patch.object(nav, "_guess_profile_screen", return_value="search_results")
    @patch.object(nav, "_followers_current_pkg_activity", return_value={"package": "com.instagram.androie"})
    @patch.object(nav, "_followers_entry_maybe_recover_ct_profile_from_search_surface", return_value=(False, "profile_not_confirmed_after_search_row_tap"))
    def test_open_followers_list_aborts_when_search_recovery_fails(
        self,
        _mock_recover,
        _mock_pkg_meta,
        _mock_guess,
        _mock_foreground,
        _mock_cap,
        _mock_meta,
        _mock_emit,
    ) -> None:
        ok, meta = nav.open_followers_list_from_profile(
            MagicMock(),
            "pelloux_sports_megeve",
            "com.instagram.androie",
            profile_verified=True,
        )
        self.assertFalse(ok)
        self.assertEqual(meta["failure_reason"], "followers_entry_profile_recovery_failed")

    @patch.object(nav, "open_followers_list_from_profile", return_value=(True, {"open_method": "followers_entry_engine_v2"}))
    @patch.object(nav, "followers_session_clear_list_committed_open")
    @patch.object(nav, "_followers_entry_maybe_recover_ct_profile_from_search_surface", return_value=(True, "profile_recovery_confirmed"))
    @patch.object(nav, "detect_followers_list_screen", return_value={"is_followers_list": False})
    def test_return_to_followers_list_reopens_after_search_recovery(
        self,
        _mock_detect,
        _mock_recover,
        _mock_clear,
        mock_open,
    ) -> None:
        d = MagicMock()
        d.press = MagicMock()
        ok, how = nav.return_to_followers_list(
            d,
            "pelloux_sports_megeve",
            "com.instagram.androie",
            max_retries=0,
        )
        self.assertTrue(ok)
        self.assertEqual(how, "reopen_after_search_profile_recovery")
        mock_open.assert_called_once()

    def test_source_profile_title_mismatch_still_blocks_entry_gate(self) -> None:
        ok_gate = nav._followers_entry_v2_entry_title_gate_ok(
            source_profile_username="pelloux_sports_megeve",
            profile_action_bar_title="Search",
            profile_verified=True,
        )
        self.assertFalse(ok_gate)

    @patch.object(nav, "force_stop")
    @patch.object(nav, "_followers_open_emit_failure")
    @patch.object(nav, "_followers_open_build_failure_meta", return_value={"failure_reason": "entry_v2_no_stat_candidate"})
    @patch.object(nav, "_followers_debug_capture", return_value={})
    @patch.object(nav, "detect_followers_entry_candidates", return_value=[{"confidence": 0.2, "method": "xml"}])
    @patch.object(nav, "_post_follow_screen_fingerprint", return_value={"fingerprint_id": "fp", "screen_class": "profile"})
    @patch("navigation_engine.observe_instagram_state", return_value={"state": "SEARCH_RESULTS", "confidence": 0.9})
    @patch.object(nav, "detect_followers_list_screen", return_value={"is_followers_list": False, "action_bar_title": "Search"})
    @patch.object(nav, "_followers_collect_profile_text_dump", return_value=[])
    @patch.object(nav, "_collect_profile_stats_band_texts", return_value=[])
    @patch.object(nav, "_followers_stats_harvest_raw_nodes_from_hierarchy_xml", return_value=[])
    @patch.object(nav, "_followers_entry_maybe_recover_ct_profile_from_search_surface", return_value=(False, "search_surface_not_detected"))
    def test_no_candidate_meets_confidence_still_aborts_without_tap(
        self,
        _mock_recover,
        _mock_harvest,
        _mock_band,
        _mock_dump,
        _mock_det_surface,
        _mock_nav,
        _mock_fp,
        _mock_candidates,
        _mock_cap,
        _mock_meta,
        _mock_emit,
        _mock_force_stop,
    ) -> None:
        d = FakeSearchRecoveryDevice(search_surface_xml())
        ok, meta = nav._open_followers_list_from_profile_v2(
            d,
            "pelloux_sports_megeve",
            "com.instagram.androie",
            profile_verified=True,
            pkg_meta={"package": "com.instagram.androie"},
            screen_guess="search_results",
            before_scan_cap={},
        )
        self.assertFalse(ok)
        self.assertEqual(meta["failure_reason"], "entry_v2_no_stat_candidate")
        d.clicks  # no coordinate taps on followers stat


if __name__ == "__main__":
    unittest.main()
