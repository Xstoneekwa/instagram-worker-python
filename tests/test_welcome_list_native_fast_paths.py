from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import instagram_navigation as nav
import welcome_list_sender as sender


class WelcomeListNativeFastPathTests(unittest.TestCase):
    def _anchor(self, username: str = "medoc_en_mer") -> dict:
        return {
            "username": username,
            "row_index": 0,
            "tap_bounds": {"left": 10, "top": 100, "right": 500, "bottom": 180},
            "username_bounds": {"left": 10, "top": 100, "right": 260, "bottom": 180},
            "screen_index": 0,
            "extraction_source": "scan",
            "job_id": "job-1",
            "scan_generation": "scan-1",
        }

    def _fresh_row(
        self,
        username: str = "medoc_en_mer",
        *,
        top: int = 400,
        cta: str = "message",
    ) -> dict:
        return {
            "username": username,
            "row_index": 2,
            "bounds": {"left": 20, "top": top, "right": 300, "bottom": top + 60},
            "username_bounds": {"left": 20, "top": top, "right": 300, "bottom": top + 60},
            "tap_bounds": {"left": 20, "top": top - 40, "right": 300, "bottom": top + 100},
            "row_cta_xml_class": cta,
            "extraction_source": "own_unified_follow_list_username_xml",
            "hierarchy_source": "fresh_dump",
        }

    def _resolve(self, rows: list[dict], **kwargs):
        anchors = {"medoc_en_mer": self._anchor()}
        with (
            patch.object(sender, "_verify_followers_surface", return_value=(True, {})),
            patch.object(
                sender,
                "harvest_visible_followers_rows",
                return_value=(rows, {"hierarchy_source": "fresh_dump"}),
            ),
            patch.object(
                sender,
                "followers_suggestions_boundary_from_cached_hierarchy",
                return_value={"is_boundary": False},
            ),
        ):
            return sender._resolve_followers_row(
                MagicMock(),
                "medoc_en_mer",
                account_username="j_automatise_pour_toi",
                scan_anchors=anchors,
                allow_scan_anchor_fast_path=True,
                job_id="job-1",
                scan_generation="scan-1",
                navigation_generation="nav-1",
                **kwargs,
            )

    def test_current_scan_anchor_never_authorizes_old_coordinates(self) -> None:
        anchors = {
            "medoc_en_mer": self._anchor()
        }
        fresh_row = self._fresh_row(top=520)
        with (
            patch.object(sender, "_verify_followers_surface", return_value=(True, {})),
            patch.object(
                sender,
                "harvest_visible_followers_rows",
                return_value=([fresh_row], {"hierarchy_source": "fresh_dump"}),
            ) as harvest_mock,
        ):
            row, scrolls, path, meta = sender._resolve_followers_row(
                MagicMock(),
                "medoc_en_mer",
                account_username="j_automatise_pour_toi",
                scan_anchors=anchors,
                allow_scan_anchor_fast_path=True,
                job_id="job-1",
                scan_generation="scan-1",
                navigation_generation="nav-1",
            )

        self.assertEqual(path, "fresh_visible")
        self.assertEqual(scrolls, 0)
        self.assertEqual(row["username"], "medoc_en_mer")
        self.assertEqual(row["tap_bounds"], fresh_row["tap_bounds"])
        self.assertNotEqual(row["tap_bounds"], anchors["medoc_en_mer"]["tap_bounds"])
        self.assertTrue(meta["scan_anchor_fast_path_allowed"])
        self.assertFalse(meta["scan_anchor_coordinates_reused"])
        harvest_mock.assert_called_once()

    def test_repositioned_list_uses_exact_username_at_new_bounds(self) -> None:
        row, scrolls, path, _meta = self._resolve([self._fresh_row(top=760)])

        self.assertEqual(path, "fresh_visible")
        self.assertEqual(scrolls, 0)
        self.assertEqual(row["tap_bounds"]["top"], 720)
        self.assertTrue(row["welcome_fresh_identity_resolved"])

    def test_recycled_coordinates_with_different_username_never_tap(self) -> None:
        other = self._fresh_row("wrong_profile", top=100)
        with (
            patch.object(sender, "_verify_followers_surface", return_value=(True, {})),
            patch.object(
                sender,
                "harvest_visible_followers_rows",
                return_value=([other], {"hierarchy_source": "fresh_dump"}),
            ),
            patch.object(
                sender,
                "followers_suggestions_boundary_from_cached_hierarchy",
                return_value={"is_boundary": False},
            ),
            patch.object(sender, "scroll_followers_list_forward", return_value=False),
            patch.object(sender, "tap_followers_list_username_row") as tap_mock,
        ):
            row, _scrolls, path, _meta = sender._resolve_followers_row(
                MagicMock(),
                "medoc_en_mer",
                account_username="j_automatise_pour_toi",
                scan_anchors={"medoc_en_mer": self._anchor()},
                allow_scan_anchor_fast_path=True,
                job_id="job-1",
                scan_generation="scan-1",
                navigation_generation="nav-1",
            )

        self.assertIsNone(row)
        self.assertEqual(path, "welcome_planned_row_not_found")
        tap_mock.assert_not_called()

    def test_username_found_after_bounded_scroll_uses_fresh_bounds(self) -> None:
        initial = self._fresh_row("other_user", top=400)
        target = self._fresh_row(top=880)
        with (
            patch.object(sender, "_verify_followers_surface", return_value=(True, {})),
            patch.object(
                sender,
                "harvest_visible_followers_rows",
                side_effect=[
                    ([initial], {"hierarchy_source": "fresh_dump"}),
                    ([target], {"hierarchy_source": "fresh_dump"}),
                ],
            ),
            patch.object(
                sender,
                "followers_suggestions_boundary_from_cached_hierarchy",
                return_value={"is_boundary": False},
            ),
            patch.object(sender, "scroll_followers_list_forward", return_value=True),
            patch.object(sender.config, "WELCOME_LIST_SENDER_SCROLL_SETTLE_S", 0),
        ):
            row, scrolls, path, meta = sender._resolve_followers_row(
                MagicMock(),
                "medoc_en_mer",
                account_username="j_automatise_pour_toi",
                scan_anchors={"medoc_en_mer": self._anchor()},
                job_id="job-1",
                scan_generation="scan-1",
                navigation_generation="nav-1",
            )

        self.assertEqual(path, "bounded_forward_find")
        self.assertEqual(scrolls, 1)
        self.assertEqual(row["tap_bounds"]["top"], 840)
        self.assertEqual(meta["resolved_navigation_generation"], "nav-1:forward:1")

    def test_suggestions_row_is_never_selected_or_scrolled_past(self) -> None:
        suggestion = self._fresh_row(cta="follow")
        with (
            patch.object(sender, "_verify_followers_surface", return_value=(True, {})),
            patch.object(
                sender,
                "harvest_visible_followers_rows",
                return_value=([suggestion], {"hierarchy_source": "fresh_dump"}),
            ),
            patch.object(
                sender,
                "followers_suggestions_boundary_from_cached_hierarchy",
                return_value={"is_boundary": True},
            ),
            patch.object(sender, "scroll_followers_list_forward") as scroll_mock,
        ):
            row, scrolls, path, _meta = sender._resolve_followers_row(
                MagicMock(),
                "medoc_en_mer",
                account_username="j_automatise_pour_toi",
                scan_anchors={"medoc_en_mer": self._anchor()},
                job_id="job-1",
                scan_generation="scan-1",
                navigation_generation="nav-1",
            )

        self.assertIsNone(row)
        self.assertEqual(scrolls, 0)
        self.assertEqual(path, "welcome_suggestions_boundary_reached")
        scroll_mock.assert_not_called()

    def test_offscreen_planned_job_uses_bounded_backward_reposition(self) -> None:
        current = self._fresh_row("visible_suggestion", top=700, cta="follow")
        target = self._fresh_row(top=440)
        with (
            patch.object(sender, "_verify_followers_surface", return_value=(True, {})),
            patch.object(
                sender,
                "harvest_visible_followers_rows",
                side_effect=[
                    ([current], {"hierarchy_source": "fresh_dump"}),
                    ([target], {"hierarchy_source": "fresh_dump"}),
                ],
            ),
            patch.object(
                sender,
                "followers_suggestions_boundary_from_cached_hierarchy",
                side_effect=[{"is_boundary": True}, {"is_boundary": False}],
            ),
            patch.object(sender, "scroll_followers_list_backward", return_value=True) as backward,
            patch.object(sender, "scroll_followers_list_forward") as forward,
            patch.object(sender, "followers_clear_detect_hierarchy_cache"),
            patch.object(sender, "followers_refresh_detect_hierarchy_cache"),
            patch.object(sender.config, "WELCOME_LIST_SENDER_SCROLL_SETTLE_S", 0),
        ):
            row, scrolls, path, meta = sender._resolve_followers_row(
                MagicMock(),
                "medoc_en_mer",
                account_username="j_automatise_pour_toi",
                scan_anchors={"medoc_en_mer": self._anchor()},
                job_id="job-1",
                scan_generation="scan-1",
                navigation_generation="nav-1",
                target_screen_index=0,
                current_screen_index=2,
            )

        self.assertEqual(path, "bounded_backward_find")
        self.assertEqual(scrolls, 1)
        self.assertEqual(row["username"], "medoc_en_mer")
        self.assertEqual(meta["resolved_screen_index"], 1)
        backward.assert_called_once()
        forward.assert_not_called()

    def test_tap_authorization_rejects_expired_snapshot(self) -> None:
        row = sender._mark_fresh_welcome_row(
            self._fresh_row(),
            job_id="job-1",
            scan_generation="scan-1",
            navigation_generation="nav-1",
            viewport_fingerprint="viewport-1",
            snapshot_captured_at=10.0,
        )
        with patch.object(sender.time, "perf_counter", return_value=20.0):
            allowed, reason, _age, _observed = sender._authorize_welcome_row_tap(
                row,
                expected_username="medoc_en_mer",
                job_id="job-1",
                scan_generation="scan-1",
                navigation_generation="nav-1",
            )

        self.assertFalse(allowed)
        self.assertEqual(reason, "snapshot_ttl_expired")

    def test_tap_authorization_rejects_wrong_username_or_generation(self) -> None:
        row = sender._mark_fresh_welcome_row(
            self._fresh_row("wrong_profile"),
            job_id="job-1",
            scan_generation="scan-1",
            navigation_generation="nav-1",
            viewport_fingerprint="viewport-1",
            snapshot_captured_at=100.0,
        )
        with patch.object(sender.time, "perf_counter", return_value=100.1):
            allowed, reason, _age, observed = sender._authorize_welcome_row_tap(
                row,
                expected_username="medoc_en_mer",
                job_id="job-1",
                scan_generation="scan-1",
                navigation_generation="nav-2",
            )

        self.assertFalse(allowed)
        self.assertEqual(reason, "username_mismatch")
        self.assertEqual(observed, "wrong_profile")

    def test_tap_authorization_rejects_navigation_generation_change(self) -> None:
        row = sender._mark_fresh_welcome_row(
            self._fresh_row(),
            job_id="job-1",
            scan_generation="scan-1",
            navigation_generation="nav-1",
            viewport_fingerprint="viewport-1",
            snapshot_captured_at=100.0,
        )
        with patch.object(sender.time, "perf_counter", return_value=100.1):
            allowed, reason, _age, observed = sender._authorize_welcome_row_tap(
                row,
                expected_username="medoc_en_mer",
                job_id="job-1",
                scan_generation="scan-1",
                navigation_generation="nav-2",
            )

        self.assertFalse(allowed)
        self.assertEqual(reason, "navigation_generation_changed")
        self.assertEqual(observed, "medoc_en_mer")

    def test_unstable_followers_surface_blocks_before_harvest(self) -> None:
        with (
            patch.object(sender, "_verify_followers_surface", return_value=(False, "")),
            patch.object(sender, "harvest_visible_followers_rows") as harvest_mock,
        ):
            row, scrolls, path, _meta = sender._resolve_followers_row(
                MagicMock(),
                "medoc_en_mer",
                account_username="j_automatise_pour_toi",
                scan_anchors={"medoc_en_mer": self._anchor()},
                job_id="job-1",
                scan_generation="scan-1",
                navigation_generation="nav-1",
            )

        self.assertIsNone(row)
        self.assertEqual(scrolls, 0)
        self.assertEqual(path, "followers_surface_not_stable")
        harvest_mock.assert_not_called()

    def test_invalid_current_bounds_block_tap(self) -> None:
        row = sender._mark_fresh_welcome_row(
            self._fresh_row(),
            job_id="job-1",
            scan_generation="scan-1",
            navigation_generation="nav-1",
            viewport_fingerprint="viewport-1",
            snapshot_captured_at=100.0,
        )
        row["tap_bounds"] = {"left": 20, "top": 400, "right": 20, "bottom": 460}
        with (
            patch.object(sender, "_resolve_followers_row", return_value=(
                row,
                0,
                "fresh_visible",
                {"resolved_navigation_generation": "nav-1"},
            )),
            patch.object(sender.time, "perf_counter", return_value=100.1),
            patch.object(sender, "tap_followers_list_username_row") as tap_mock,
        ):
            state, ok, _meta = sender._navigate_followers_row_to_dm(
                MagicMock(),
                "medoc_en_mer",
                pkg="com.instagram.android",
                account_username="j_automatise_pour_toi",
                scan_anchors={"medoc_en_mer": self._anchor()},
                planned_job_context={
                    "job_id": "job-1",
                    "scan_generation": "scan-1",
                    "navigation_generation": "nav-1",
                },
            )

        self.assertFalse(ok)
        self.assertEqual(state, "row_tap_blocked_current_bounds_invalid")
        tap_mock.assert_not_called()

    def test_profile_identity_mismatch_never_opens_dm_thread(self) -> None:
        row = sender._mark_fresh_welcome_row(
            self._fresh_row(),
            job_id="job-1",
            scan_generation="scan-1",
            navigation_generation="nav-1",
            viewport_fingerprint="viewport-1",
            snapshot_captured_at=100.0,
        )
        with (
            patch.object(sender, "_resolve_followers_row", return_value=(
                row,
                0,
                "fresh_visible",
                {"resolved_navigation_generation": "nav-1"},
            )),
            patch.object(sender.time, "perf_counter", return_value=100.1),
            patch.object(
                sender,
                "tap_followers_list_username_row",
                return_value=(True, 100, 420),
            ),
            patch.object(sender.config, "PROFILE_POST_TAP_STABILIZE_S", 0),
            patch.object(sender, "verify_profile", return_value=True),
            patch.object(
                sender,
                "verify_welcome_profile_username_exact",
                return_value=(False, "profile_username_mismatch", "wrong_profile"),
            ),
            patch.object(sender, "open_dm_thread_from_profile") as open_dm_mock,
        ):
            state, ok, meta = sender._navigate_followers_row_to_dm(
                MagicMock(),
                "medoc_en_mer",
                pkg="com.instagram.android",
                account_username="j_automatise_pour_toi",
                scan_anchors={"medoc_en_mer": self._anchor()},
                planned_job_context={
                    "job_id": "job-1",
                    "scan_generation": "scan-1",
                    "navigation_generation": "nav-1",
                },
            )

        self.assertFalse(ok)
        self.assertEqual(state, "profile_username_mismatch")
        self.assertEqual(meta["observed_profile_username"], "wrong_profile")
        open_dm_mock.assert_not_called()

    def test_recovery_invalidation_reason_is_logged_without_anchor_reuse(self) -> None:
        with patch.object(sender, "log") as log_mock:
            row, _scrolls, path, _meta = self._resolve(
                [self._fresh_row()],
                anchor_invalidation_reason="canonical_followers_reopen_completed",
            )

        self.assertEqual(path, "fresh_visible")
        self.assertEqual(row["username"], "medoc_en_mer")
        invalidation = [
            call
            for call in log_mock.call_args_list
            if len(call.args) > 1 and call.args[1] == "welcome_row_anchor_invalidated"
        ]
        self.assertEqual(len(invalidation), 1)
        self.assertEqual(
            invalidation[0].kwargs["reason"],
            "canonical_followers_reopen_completed",
        )

    def test_post_send_confirmed_fast_return_skips_second_finalize(self) -> None:
        device = MagicMock()
        det = {"is_followers_list": True, "action_bar_title": "Followers"}
        with (
            patch.object(nav.config, "WELCOME_LIST_SENDER_BACK_SETTLE_S", 0, create=True),
            patch.object(nav, "_dm_post_send_signal_poll") as poll_mock,
            patch.object(nav, "clear_dm_draft") as clear_mock,
            patch.object(nav, "finalize_dm_draft_before_back") as finalize_mock,
            patch.object(nav, "tap_instagram_action_bar_back_button", return_value=(True, "action_bar")),
            patch.object(
                nav,
                "verify_welcome_profile_username_exact",
                return_value=(True, "exact_profile_username", "medoc_en_mer"),
            ),
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
            patch.object(
                nav,
                "verify_welcome_profile_username_exact",
                return_value=(True, "exact_profile_username", "medoc_en_mer"),
            ),
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
