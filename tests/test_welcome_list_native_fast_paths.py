from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import instagram_navigation as nav
import welcome_list_sender as sender


class WelcomeListNativeFastPathTests(unittest.TestCase):
    def test_current_scan_anchor_visible_uses_fast_row_without_fresh_harvest(self) -> None:
        anchors = {
            "medoc_en_mer": {
                "username": "medoc_en_mer",
                "row_index": 0,
                "tap_bounds": {"left": 10, "top": 100, "right": 500, "bottom": 180},
                "username_bounds": {"left": 10, "top": 100, "right": 260, "bottom": 180},
                "screen_index": 0,
                "extraction_source": "scan",
            }
        }
        with patch.object(
            sender,
            "harvest_visible_followers_rows",
            side_effect=AssertionError("fresh harvest should not run"),
        ):
            row, scrolls, path, meta = sender._resolve_followers_row(
                MagicMock(),
                "medoc_en_mer",
                account_username="j_automatise_pour_toi",
                scan_anchors=anchors,
                allow_scan_anchor_fast_path=True,
            )

        self.assertEqual(path, "scan_anchor_fast_path")
        self.assertEqual(scrolls, 0)
        self.assertEqual(row["username"], "medoc_en_mer")
        self.assertTrue(meta["scan_anchor_fast_path_allowed"])

    def test_stale_or_untrusted_anchor_falls_back_to_fresh_harvest(self) -> None:
        anchors = {
            "medoc_en_mer": {
                "username": "medoc_en_mer",
                "row_index": 0,
                "tap_bounds": {"left": 10, "top": 100, "right": 500, "bottom": 180},
                "screen_index": 0,
            }
        }
        rows = [
            {
                "username": "medoc_en_mer",
                "row_index": 0,
                "bounds": {"left": 10, "top": 100, "right": 500, "bottom": 180},
                "tap_bounds": {"left": 10, "top": 100, "right": 500, "bottom": 180},
            }
        ]
        with patch.object(
            sender,
            "harvest_visible_followers_rows",
            return_value=(rows, {"hierarchy_source": "fresh"}),
        ) as harvest_mock:
            row, _scrolls, path, meta = sender._resolve_followers_row(
                MagicMock(),
                "medoc_en_mer",
                account_username="j_automatise_pour_toi",
                scan_anchors=anchors,
                allow_scan_anchor_fast_path=False,
            )

        self.assertEqual(path, "scan_anchor")
        self.assertEqual(row["username"], "medoc_en_mer")
        self.assertFalse(meta["scan_anchor_fast_path_allowed"])
        harvest_mock.assert_called_once()

    def test_post_send_confirmed_fast_return_skips_second_finalize(self) -> None:
        device = MagicMock()
        det = {"is_followers_list": True, "action_bar_title": "Followers"}
        with (
            patch.object(nav.config, "WELCOME_LIST_SENDER_BACK_SETTLE_S", 0, create=True),
            patch.object(nav, "_dm_post_send_signal_poll") as poll_mock,
            patch.object(nav, "clear_dm_draft") as clear_mock,
            patch.object(nav, "finalize_dm_draft_before_back") as finalize_mock,
            patch.object(nav, "tap_instagram_action_bar_back_button", return_value=(True, "action_bar")),
            patch.object(nav, "verify_profile", return_value=True),
            patch.object(nav, "detect_followers_list_screen_fresh", return_value=(det, "")),
        ):
            out = nav.return_welcome_list_from_dm_to_followers(
                device,
                "medoc_en_mer",
                "com.instagram.androif",
                source_profile_username="j_automatise_pour_toi",
                pre_send_composer_text_len=37,
                send_already_confirmed=True,
            )

        self.assertTrue(out["post_send_signal_ok"])
        self.assertEqual(out["post_send_signal_reason"], "already_confirmed_before_return")
        self.assertTrue(out["followers_surface_ok"])
        poll_mock.assert_not_called()
        clear_mock.assert_not_called()
        finalize_mock.assert_not_called()

    def test_unconfirmed_return_keeps_conservative_finalize_path(self) -> None:
        device = MagicMock()
        det = {"is_followers_list": True, "action_bar_title": "Followers"}
        with (
            patch.object(nav.config, "WELCOME_LIST_SENDER_BACK_SETTLE_S", 0, create=True),
            patch.object(nav, "_dm_post_send_signal_poll", return_value=(True, "composer_empty")) as poll_mock,
            patch.object(nav, "clear_dm_draft") as clear_mock,
            patch.object(nav, "finalize_dm_draft_before_back") as finalize_mock,
            patch.object(nav, "tap_instagram_action_bar_back_button", return_value=(True, "action_bar")),
            patch.object(nav, "verify_profile", return_value=True),
            patch.object(nav, "detect_followers_list_screen_fresh", return_value=(det, "")),
        ):
            out = nav.return_welcome_list_from_dm_to_followers(
                device,
                "medoc_en_mer",
                "com.instagram.androif",
                source_profile_username="j_automatise_pour_toi",
                pre_send_composer_text_len=37,
            )

        self.assertTrue(out["post_send_signal_ok"])
        poll_mock.assert_called_once()
        clear_mock.assert_called_once()
        finalize_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
