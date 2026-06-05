from __future__ import annotations

import time
import unittest
from unittest import mock

import instagram_navigation as nav


class PostFollowLikeSamsungFastTest(unittest.TestCase):
    def test_post_follow_surface_truth_confirms_profile_over_followers_list(self) -> None:
        device = mock.MagicMock()
        logs: list[tuple[str, str, dict[str, object]]] = []
        with mock.patch.object(
            nav, "read_current_profile_username_for_follow_gate", return_value="cand"
        ), mock.patch.object(
            nav,
            "visual_profile_already_following_before_follow",
            return_value={"already_following": True, "detection_method": "ui_text_following_exact"},
        ), mock.patch.object(
            nav, "log", side_effect=lambda level, event, **kw: logs.append((level, event, kw))
        ):
            out = nav._post_follow_resolve_surface_truth(
                device,
                candidate_username="cand",
                source_profile_username="ct",
                follow_state_after="following",
                det={"action_bar_title": "cand", "is_followers_list": True},
                nav_obs={"state": "FOLLOWERS_LIST"},
                fp={"screen_class": "followers_list"},
            )

        self.assertEqual(out["decision"], "candidate_profile_confirmed")
        self.assertTrue(out["evidence_profile"])
        self.assertTrue(out["evidence_followers_list"])
        self.assertIn("post_follow_surface_conflict", [event for _level, event, _kw in logs])
        self.assertIn(
            "post_follow_candidate_profile_confirmed",
            [event for _level, event, _kw in logs],
        )

    def test_post_follow_recover_reopens_visible_row(self) -> None:
        device = mock.MagicMock()
        row = {"username": "cand", "row_center": [100, 200]}
        with mock.patch.object(
            nav.config, "POST_FOLLOW_RECOVER_CANDIDATE_PROFILE_FROM_LIST", True, create=True
        ), mock.patch.object(
            nav, "find_visible_followers_row_by_username", return_value=row
        ), mock.patch.object(
            nav, "tap_followers_list_username_row", return_value=(True, 100, 200)
        ), mock.patch.object(
            nav, "read_current_profile_username_for_follow_gate", return_value="cand"
        ), mock.patch.object(
            nav, "_follow_ui_state_snapshot", return_value="following"
        ), mock.patch.object(nav, "time") as tmock:
            tmock.sleep = lambda *_a, **_k: None
            tmock.perf_counter = time.perf_counter
            tmock.monotonic = time.monotonic
            out = nav._post_follow_recover_candidate_profile_from_list(
                device,
                candidate_username="cand",
                source_profile_username="ct",
            )
        self.assertTrue(out["recovered"])
        self.assertEqual(out["reason"], "profile_reopened_with_following_cta")

    def test_suggested_overlay_pre_probe_fast_skip_without_screenshot(self) -> None:
        device = mock.MagicMock()
        device.window_size.return_value = (1080, 2340)
        with mock.patch.object(
            nav.config, "POST_FOLLOW_POST_LIKES_ENABLED", True, create=True
        ), mock.patch.object(
            nav, "_post_follow_likes_grid_ui_surface_hints",
            side_effect=[
                {"suggested_for_you": True, "discover_people": False, "profile_tabs_visible": True},
                {"suggested_for_you": True, "discover_people": False, "profile_tabs_visible": True},
            ],
        ), mock.patch.object(
            nav, "_post_follow_likes_visible_grid_cell_under_suggested",
            return_value={"reliable": False, "reason": "no_xml_thumbnail_below_tabs"},
        ), mock.patch.object(
            nav,
            "_post_follow_likes_try_clear_suggested_overlay_fast",
            return_value={"handled": True, "action": "micro_scroll_reveal_grid", "elapsed_ms": 120.0},
        ), mock.patch.object(nav, "screenshot") as shot, mock.patch.object(
            nav, "time"
        ) as tmock:
            tmock.perf_counter = time.perf_counter
            tmock.monotonic = time.monotonic
            tmock.time = time.time
            tmock.sleep = lambda *_a, **_k: None
            out = nav.ensure_post_grid_visible_for_post_follow_likes(
                device,
                source_profile_username="ct",
                follower_username="cand",
                visual_candidate_id="vc-1",
                budget_s=8.0,
            )
        shot.assert_not_called()
        self.assertEqual(out.get("failure_reason"), "post_grid_partial_suggested_overlay_fast")
        self.assertTrue((out.get("likes_perf_grid") or {}).get("fast_overlay_skip"))

    def test_suggested_with_visible_cell_skips_fast_abort(self) -> None:
        device = mock.MagicMock()
        device.window_size.return_value = (1080, 2340)
        with mock.patch.object(
            nav.config, "POST_FOLLOW_POST_LIKES_ENABLED", True, create=True
        ), mock.patch.object(
            nav, "_post_follow_likes_grid_ui_surface_hints",
            return_value={
                "suggested_for_you": True,
                "discover_people": False,
                "profile_tabs_visible": True,
            },
        ), mock.patch.object(
            nav,
            "_post_follow_likes_visible_grid_cell_under_suggested",
            return_value={
                "reliable": True,
                "cell": {"center_x": 180, "center_y": 980},
                "reason": "xml_thumbnail_below_tabs",
            },
        ), mock.patch.object(
            nav, "_post_follow_likes_try_clear_suggested_overlay_fast"
        ) as clear_fn, mock.patch.object(nav, "screenshot") as shot, mock.patch(
            "PIL.Image.open"
        ), mock.patch.object(nav, "time") as tmock:
            tmock.perf_counter = time.perf_counter
            tmock.monotonic = time.monotonic
            tmock.time = time.time
            tmock.sleep = lambda *_a, **_k: None
            out = nav.ensure_post_grid_visible_for_post_follow_likes(
                device,
                source_profile_username="ct",
                follower_username="cand",
                visual_candidate_id="vc-1",
                budget_s=8.0,
            )
        clear_fn.assert_not_called()
        shot.assert_not_called()
        self.assertTrue(out.get("ok"))
        self.assertIsNotNone(out.get("direct_post_cell_under_suggested"))
        self.assertFalse((out.get("likes_perf_grid") or {}).get("fast_overlay_skip"))

    def test_like_grid_global_cap_skips_without_screenshot_when_phase_exceeds_cap(self) -> None:
        device = mock.MagicMock()
        with mock.patch.object(
            nav,
            "_post_follow_likes_grid_ui_surface_hints",
            return_value={"suggested_for_you": True, "discover_people": False},
        ), mock.patch.object(nav, "screenshot") as shot:
            out = nav.ensure_post_grid_visible_for_post_follow_likes(
                device,
                source_profile_username="ct",
                follower_username="cand",
                visual_candidate_id="vc-1",
                budget_s=8.0,
                likes_perf_phase_t0=time.perf_counter()
                - nav._POST_FOLLOW_LIKE_OVERLAY_PHASE_CAP_S
                - 0.1,
            )
        shot.assert_not_called()
        self.assertEqual(out.get("failure_reason"), "like_grid_global_cap_skipped")
        self.assertTrue((out.get("likes_perf_grid") or {}).get("fast_overlay_skip"))

    def test_post_follow_recover_skips_when_row_missing(self) -> None:
        device = mock.MagicMock()
        with mock.patch.object(
            nav.config, "POST_FOLLOW_RECOVER_CANDIDATE_PROFILE_FROM_LIST", True, create=True
        ), mock.patch.object(
            nav, "find_visible_followers_row_by_username", return_value=None
        ):
            out = nav._post_follow_recover_candidate_profile_from_list(
                device,
                candidate_username="cand",
                source_profile_username="ct",
            )
        self.assertFalse(out["recovered"])
        self.assertEqual(out["reason"], "candidate_row_not_visible")

    def test_post_follow_surface_truth_lost_without_candidate_evidence(self) -> None:
        device = mock.MagicMock()
        with mock.patch.object(
            nav, "read_current_profile_username_for_follow_gate", return_value="ct"
        ), mock.patch.object(
            nav,
            "visual_profile_already_following_before_follow",
            return_value={"already_following": False, "detection_method": "none"},
        ):
            out = nav._post_follow_resolve_surface_truth(
                device,
                candidate_username="cand",
                source_profile_username="ct",
                follow_state_after="following",
                det={"action_bar_title": "ct", "is_followers_list": True},
                nav_obs={"state": "FOLLOWERS_LIST"},
                fp={"screen_class": "followers_list"},
            )

        self.assertEqual(out["decision"], "candidate_profile_lost")
        self.assertFalse(out["evidence_profile"])
        self.assertTrue(out["evidence_followers_list"])

    def test_like_precheck_skips_when_mute_sheet_still_open(self) -> None:
        device = mock.MagicMock()
        with mock.patch.object(
            nav,
            "_post_follow_overlay_ui_hints",
            return_value={"likely_mute_toggle_sheet": True},
        ), mock.patch.object(
            nav,
            "_mute_engine_v2_dismiss_mute_sheet",
            return_value=(False, 120.0),
        ), mock.patch.object(
            nav,
            "_mute_engine_v2_mute_sheet_still_visible",
            return_value=True,
        ):
            out = nav._post_follow_like_precheck_mute_sheet(
                device,
                visual_candidate_id="vc-1",
                source_profile_username="ct",
                follower_username="cand",
            )
        self.assertTrue(out["skip_like"])
        self.assertEqual(out["skip_reason"], "mute_sheet_still_open")

    def test_like_surface_precheck_candidate_title_overrides_generic_followers_list(self) -> None:
        device = mock.MagicMock()
        with mock.patch.object(
            nav, "is_followers_list_surface_quick", return_value=True
        ), mock.patch.object(
            nav, "read_current_profile_username_for_follow_gate", return_value="cand"
        ), mock.patch.object(
            nav, "_post_follow_likes_grid_ui_surface_hints"
        ) as hints:
            out = nav._post_follow_like_precheck_surface(
                device,
                source_profile_username="ct",
                follower_username="cand",
                visual_candidate_id="vc-1",
            )

        self.assertFalse(out["skip_like"])
        self.assertEqual(out["skip_reason"], "")
        self.assertTrue(out["profile_candidate_visible"])
        hints.assert_called()

    def test_like_surface_precheck_skips_followers_list_without_candidate_evidence(self) -> None:
        device = mock.MagicMock()
        with mock.patch.object(
            nav, "is_followers_list_surface_quick", return_value=True
        ), mock.patch.object(
            nav, "read_current_profile_username_for_follow_gate", return_value="ct"
        ), mock.patch.object(
            nav, "_post_follow_likes_grid_ui_surface_hints"
        ) as hints:
            out = nav._post_follow_like_precheck_surface(
                device,
                source_profile_username="ct",
                follower_username="cand",
                visual_candidate_id="vc-1",
            )

        self.assertTrue(out["skip_like"])
        self.assertEqual(out["skip_reason"], "followers_list_visible_before_like")
        hints.assert_called()

    def test_like_surface_precheck_skips_wrong_profile_before_grid_probe(self) -> None:
        device = mock.MagicMock()
        with mock.patch.object(
            nav, "is_followers_list_surface_quick", return_value=False
        ), mock.patch.object(
            nav, "read_current_profile_username_for_follow_gate", return_value="other_user"
        ):
            out = nav._post_follow_like_precheck_surface(
                device,
                source_profile_username="ct",
                follower_username="cand",
                visual_candidate_id="vc-1",
            )

        self.assertTrue(out["skip_like"])
        self.assertEqual(out["skip_reason"], "candidate_profile_not_visible_before_like")

    def test_like_phase_no_grid_probe_when_surface_precheck_blocks(self) -> None:
        device = mock.MagicMock()
        with mock.patch.object(
            nav.config, "POST_FOLLOW_POST_LIKES_ENABLED", True, create=True
        ), mock.patch.object(
            nav.config, "ENABLE_REAL_VISUAL_POST_LIKE", True, create=True
        ), mock.patch.object(
            nav, "_post_follow_like_precheck_mute_sheet", return_value={"skip_like": False}
        ), mock.patch.object(
            nav,
            "_post_follow_like_precheck_surface",
            return_value={
                "skip_like": True,
                "skip_reason": "followers_list_visible_before_like",
                "precheck_ms": 2.0,
            },
        ), mock.patch.object(
            nav, "read_current_profile_username_for_follow_gate", return_value="cand"
        ), mock.patch(
            "navigation_engine.observe_instagram_state",
            return_value={"state": "CANDIDATE_PROFILE", "confidence": 0.9},
        ), mock.patch.object(
            nav, "ensure_post_grid_visible_for_post_follow_likes"
        ) as grid_fn:
            out = nav.run_post_follow_post_likes_phase(
                device,
                pkg="com.instagram.android",
                source_profile_username="ct",
                follower_username="cand",
                visual_candidate_id="vc-1",
                follow_success_verified=True,
                follow_state_after="following",
                skipped_tap=False,
            )

        grid_fn.assert_not_called()
        self.assertEqual(out.get("phase_outcome"), "skipped")
        self.assertEqual(out.get("skipped_reason"), "followers_list_visible_before_like")

    def test_partial_suggested_overlay_helper(self) -> None:
        self.assertTrue(
            nav._post_follow_like_partial_suggested_overlay(
                {"grid_state": "partial", "suggested_for_you": True}
            )
        )
        self.assertFalse(
            nav._post_follow_like_partial_suggested_overlay(
                {"grid_state": "visible", "suggested_for_you": True}
            )
        )

    def test_grid_cell_tap_center_on_1080x2340(self) -> None:
        iw, ih, ww, wh = 1080, 2340, 1080, 2340
        cell_w = iw // 3
        cell_h = cell_w
        x0, y0 = 0, 800
        tx, ty = nav._visual_grid_cell_tap_xy_device(
            x0,
            y0,
            cell_w,
            cell_h,
            iw=iw,
            ih=ih,
            ww=ww,
            wh=wh,
            tap_frac_x=nav._POST_FOLLOW_GRID_CELL_TAP_FRAC_X,
            tap_frac_y=nav._POST_FOLLOW_GRID_CELL_TAP_FRAC_Y,
        )
        self.assertEqual(tx, 180)
        self.assertEqual(ty, 980)

    def test_post_follow_viewer_poll_caps_below_legacy(self) -> None:
        self.assertLess(
            nav._POST_FOLLOW_VIEWER_OPEN_POLL_MAX_S,
            nav._VISUAL_POST_VIEWER_OPEN_POLL_MAX_S,
        )
        self.assertLess(
            nav._POST_FOLLOW_VIEWER_OPEN_WALL_CAP_S,
            nav._VISUAL_POST_VIEWER_OPEN_POLL_MAX_S
            + nav._VISUAL_POST_VIEWER_OPEN_POLL_RETRY_MAX_S,
        )

    @mock.patch("instagram_navigation._visual_detect_post_viewer_opened_after_tap")
    @mock.patch("instagram_navigation.time.sleep")
    def test_fast_viewer_wait_early_exit_on_detect(
        self, _sleep: mock.MagicMock, detect: mock.MagicMock
    ) -> None:
        detect.return_value = {
            "post_detected": True,
            "viewer_detect_path": "phase_a_like_unlike_fast",
        }
        d = mock.MagicMock()
        t0 = time.perf_counter()
        out = nav._visual_wait_post_viewer_opened_after_tap(
            d,
            pkg="com.instagram.android",
            expected_follower_username="cand",
            act_before=None,
            poll_label="first_tap",
            post_follow_fast=True,
        )
        elapsed = time.perf_counter() - t0
        self.assertTrue(out.get("post_detected"))
        self.assertLess(elapsed, nav._POST_FOLLOW_VIEWER_OPEN_WALL_CAP_S + 0.15)
        detect.assert_called()
        self.assertTrue(
            all(
                c.kwargs.get("post_follow_fast") is True
                for c in detect.call_args_list
            )
        )

    @mock.patch("instagram_navigation._visual_detect_post_viewer_opened_after_tap")
    @mock.patch("instagram_navigation.time.sleep")
    def test_fast_viewer_wait_respects_wall_cap_when_detect_slow(
        self, _sleep: mock.MagicMock, detect: mock.MagicMock
    ) -> None:
        def _slow_detect(*_a: object, **_k: object) -> dict[str, object]:
            time.sleep(0.35)
            return {"post_detected": False, "prof_still_on_candidate_profile": True}

        detect.side_effect = _slow_detect
        d = mock.MagicMock()
        t0 = time.perf_counter()
        out = nav._visual_wait_post_viewer_opened_after_tap(
            d,
            pkg="com.instagram.android",
            expected_follower_username="cand",
            act_before=None,
            post_follow_fast=True,
        )
        elapsed = time.perf_counter() - t0
        self.assertFalse(out.get("post_detected"))
        self.assertLessEqual(elapsed, nav._POST_FOLLOW_VIEWER_OPEN_WALL_CAP_S + 0.25)

    def test_likes_disabled_early_exit_has_no_grid_cost(self) -> None:
        d = mock.MagicMock()
        with mock.patch.object(
            nav.config, "POST_FOLLOW_POST_LIKES_ENABLED", False, create=True
        ), mock.patch.object(
            nav, "ensure_post_grid_visible_for_post_follow_likes"
        ) as grid_fn:
            out = nav.run_post_follow_post_likes_phase(
                d,
                pkg="com.instagram.android",
                source_profile_username="src",
                follower_username="cand",
                visual_candidate_id="vc1",
                follow_success_verified=True,
                follow_state_after="following",
                skipped_tap=False,
            )
        grid_fn.assert_not_called()
        self.assertEqual(out.get("phase_outcome"), "skipped")
        self.assertEqual(out.get("skipped_reason"), "likes_disabled_by_config")

    def test_open_post_budget_constants(self) -> None:
        self.assertLessEqual(nav._POST_FOLLOW_LIKE_OPEN_POST_MAX_S, 4.0)
        self.assertLessEqual(nav._POST_FOLLOW_LIKE_GRID_PREP_MAX_S, 6.0)

    def test_overlay_strategy_perf_is_reported_in_summary_payload(self) -> None:
        payload = nav._post_follow_like_perf_summary_payload(
            timings={"likes_total_ms": 3000.0, "overlay_strategy_ms": 320.0},
            post_open={},
            like_perf={},
            result="failed_safe_continue",
            failure_reason="post_grid_partial_suggested_overlay",
        )

        self.assertEqual(payload["overlay_strategy_ms"], 320.0)
        self.assertEqual(payload["failure_reason"], "post_grid_partial_suggested_overlay")

    def test_suggested_overlay_scroll_strategy_budget_is_bounded_but_not_precheck_starved(self) -> None:
        self.assertGreaterEqual(nav._POST_FOLLOW_LIKE_OVERLAY_PHASE_CAP_S, 10.5)
        self.assertLessEqual(nav._POST_FOLLOW_LIKE_OVERLAY_PHASE_CAP_S, 12.0)
        self.assertLessEqual(nav._POST_FOLLOW_LIKE_GRID_PREP_MAX_S, 6.0)
        self.assertLessEqual(nav._POST_FOLLOW_LIKE_PARTIAL_GRID_DETECT_CAP_S, 4.5)
        self.assertLessEqual(nav._POST_FOLLOW_RETURN_CT_POST_LIKE_RECOVERY_CAP_S, 8.0)


if __name__ == "__main__":
    unittest.main()
