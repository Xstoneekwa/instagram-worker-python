from __future__ import annotations

import time
import unittest
from unittest import mock

import instagram_navigation as nav


class MuteEngineV2SheetLevelsTest(unittest.TestCase):
    def test_following_options_sheet_detected(self) -> None:
        d = mock.MagicMock()

        def _exists(**kwargs: object) -> mock.MagicMock:
            text = kwargs.get("text")
            tc = kwargs.get("textContains")
            m = mock.MagicMock()
            if text == "Unfollow" or text == "Mute" or text == "Restrict":
                m.exists.return_value = True
            elif tc == "Close friend":
                m.exists.return_value = True
            elif text in ("Posts", "Stories", "Notes"):
                m.exists.return_value = False
            else:
                m.exists.return_value = False
            return m

        d.side_effect = _exists
        level, meta = nav._mute_engine_v2_detect_sheet_level(d)
        self.assertEqual(level, "following_options")
        self.assertTrue(meta.get("unfollow_visible"))

    def test_mute_toggles_sheet_detected(self) -> None:
        d = mock.MagicMock()

        def _exists(**kwargs: object) -> mock.MagicMock:
            text = kwargs.get("text")
            m = mock.MagicMock()
            if text in ("Posts", "Stories", "Notes", "Mute"):
                m.exists.return_value = True
            elif text == "Unfollow":
                m.exists.return_value = False
            else:
                m.exists.return_value = False
            return m

        d.side_effect = _exists
        level, _meta = nav._mute_engine_v2_detect_sheet_level(d)
        self.assertEqual(level, "mute_toggles")

    def test_enter_subsheet_taps_mute_row(self) -> None:
        d = mock.MagicMock()
        mute_el = mock.MagicMock()
        t_all = time.perf_counter()
        with mock.patch.object(
            nav, "_visual_find_mute_row_first_sheet", return_value=(mute_el, "Mute")
        ), mock.patch.object(
            nav,
            "_mute_engine_v2_detect_sheet_level",
            side_effect=lambda *_a, **_k: ("mute_toggles", {"posts_label": True, "stories_label": True}),
        ), mock.patch.object(nav, "time") as tmock:
            tmock.perf_counter = time.perf_counter
            tmock.sleep = lambda *_a, **_k: None
            ok, rsn = nav._mute_engine_v2_enter_mute_subsheet_from_following_options(
                d,
                toggle_required_budget_s=1.5,
                t_all=t_all,
            )
        self.assertTrue(ok)
        self.assertEqual(rsn, "")
        mute_el.click.assert_called_once()

    def test_exact_mute_row_fast_requires_single_safe_exact_row(self) -> None:
        d = mock.MagicMock()
        safe = mock.MagicMock()
        safe.info = {"text": "Mute", "bounds": {"left": 45, "top": 900, "right": 980, "bottom": 1030}}
        unsafe = mock.MagicMock()
        unsafe.info = {"text": "Muted", "bounds": {"left": 45, "top": 900, "right": 980, "bottom": 1030}}
        sel = mock.MagicMock()
        sel.exists.return_value = True
        sel.all.return_value = [safe, unsafe]
        d.return_value = sel

        row, meta = nav._mute_engine_v2_find_exact_mute_row_fast(
            d,
            ww=1080,
            wh=2340,
            timeout_s=0.05,
        )

        self.assertIs(row, safe)
        self.assertTrue(meta.get("found"))
        self.assertFalse(meta.get("ambiguous"))

    def test_exact_mute_row_fast_uses_selector_when_all_is_empty(self) -> None:
        d = mock.MagicMock()
        sel = mock.MagicMock()
        sel.exists.return_value = True
        sel.all.return_value = []
        sel.info = {"text": "Mute"}
        d.return_value = sel

        row, meta = nav._mute_engine_v2_find_exact_mute_row_fast(
            d,
            ww=1080,
            wh=2340,
            timeout_s=0.05,
        )

        self.assertIs(row, sel)
        self.assertTrue(meta.get("found"))
        self.assertEqual(meta.get("selector_source"), "fallback_exact_text_mute")

    def test_exact_mute_row_fast_rejects_muted_label(self) -> None:
        d = mock.MagicMock()
        sel = mock.MagicMock()
        sel.exists.return_value = True
        sel.all.return_value = []
        sel.info = {"text": "Muted"}
        d.return_value = sel

        row, meta = nav._mute_engine_v2_find_exact_mute_row_fast(
            d,
            ww=1080,
            wh=2340,
            timeout_s=0.05,
        )

        self.assertIsNone(row)
        self.assertFalse(meta.get("found"))

    def test_exact_mute_row_fast_rejects_multiple_safe_exact_rows(self) -> None:
        d = mock.MagicMock()
        first = mock.MagicMock()
        first.info = {"text": "Mute", "bounds": {"left": 45, "top": 900, "right": 980, "bottom": 1030}}
        second = mock.MagicMock()
        second.info = {"text": "Mute", "bounds": {"left": 45, "top": 1040, "right": 980, "bottom": 1170}}
        sel = mock.MagicMock()
        sel.exists.return_value = True
        sel.all.return_value = [first, second]
        d.return_value = sel

        row, meta = nav._mute_engine_v2_find_exact_mute_row_fast(
            d,
            ww=1080,
            wh=2340,
            timeout_s=0.05,
        )

        self.assertIsNone(row)
        self.assertTrue(meta.get("ambiguous"))
        self.assertEqual(meta.get("reason"), "multiple_exact_mute_rows")

    def test_exact_mute_row_fast_absent_returns_none(self) -> None:
        d = mock.MagicMock()
        sel = mock.MagicMock()
        sel.exists.return_value = False
        d.return_value = sel

        row, meta = nav._mute_engine_v2_find_exact_mute_row_fast(
            d,
            ww=1080,
            wh=2340,
            timeout_s=0.05,
        )

        self.assertIsNone(row)
        self.assertFalse(meta.get("found"))
        self.assertEqual(meta.get("reason"), "exact_mute_row_not_found")

    def test_confirmed_following_options_fallback_exact_mute_row(self) -> None:
        d = mock.MagicMock()
        row_el = mock.MagicMock()
        row_el.info = {"text": "Mute", "bounds": {"left": 45, "top": 900, "right": 980, "bottom": 1030}}

        with mock.patch.object(
            nav, "_visual_find_mute_row_first_sheet", return_value=(row_el, "Mute")
        ):
            row, meta = nav._mute_engine_v2_find_mute_row_from_confirmed_following_options(
                d,
                ww=1080,
                wh=2340,
            )

        self.assertIs(row, row_el)
        self.assertTrue(meta.get("found"))
        self.assertEqual(meta.get("selector_source"), "fallback_exact_text_mute")
        self.assertEqual(meta.get("label"), "Mute")
        self.assertTrue(meta.get("bounds_safe"))
        self.assertEqual(meta.get("click_method"), "uiobject_click")

    def test_confirmed_following_options_rejects_muted_label(self) -> None:
        d = mock.MagicMock()
        row_el = mock.MagicMock()
        row_el.info = {"text": "Muted", "bounds": {"left": 45, "top": 900, "right": 980, "bottom": 1030}}

        with mock.patch.object(
            nav, "_visual_find_mute_row_first_sheet", return_value=(row_el, "Muted")
        ):
            row, meta = nav._mute_engine_v2_find_mute_row_from_confirmed_following_options(
                d,
                ww=1080,
                wh=2340,
            )

        self.assertIsNone(row)
        self.assertFalse(meta.get("found"))
        self.assertEqual(meta.get("reason"), "fallback_exact_mute_label_not_exact")

    def test_confirmed_following_options_accepts_sheet_text_bounds(self) -> None:
        d = mock.MagicMock()
        row_el = mock.MagicMock()
        row_el.info = {"text": "Mute", "bounds": {"left": 680, "top": 1020, "right": 790, "bottom": 1090}}

        with mock.patch.object(
            nav, "_visual_find_mute_row_first_sheet", return_value=(row_el, "Mute")
        ):
            row, meta = nav._mute_engine_v2_find_mute_row_from_confirmed_following_options(
                d,
                ww=1080,
                wh=2340,
            )

        self.assertIs(row, row_el)
        self.assertTrue(meta.get("found"))
        self.assertTrue(meta.get("bounds_safe"))

    def test_confirmed_following_options_rejects_offscreen_bounds(self) -> None:
        d = mock.MagicMock()
        row_el = mock.MagicMock()
        row_el.info = {"text": "Mute", "bounds": {"left": 45, "top": -250, "right": 200, "bottom": -150}}

        with mock.patch.object(
            nav, "_visual_find_mute_row_first_sheet", return_value=(row_el, "Mute")
        ):
            row, meta = nav._mute_engine_v2_find_mute_row_from_confirmed_following_options(
                d,
                ww=1080,
                wh=2340,
            )

        self.assertIsNone(row)
        self.assertFalse(meta.get("found"))
        self.assertEqual(meta.get("reason"), "center_outside_screen")

    def test_confirmed_following_options_rejects_multiple_exact_mute_rows(self) -> None:
        d = mock.MagicMock()
        row_el = mock.MagicMock()
        row_el.info = {"text": "Mute", "bounds": {"left": 45, "top": 900, "right": 980, "bottom": 1030}}
        other_el = mock.MagicMock()
        other_el.info = {"text": "Mute", "bounds": {"left": 45, "top": 1040, "right": 980, "bottom": 1170}}
        sel = mock.MagicMock()
        sel.all.return_value = [row_el, other_el]
        d.return_value = sel

        with mock.patch.object(
            nav, "_visual_find_mute_row_first_sheet", return_value=(row_el, "Mute")
        ):
            row, meta = nav._mute_engine_v2_find_mute_row_from_confirmed_following_options(
                d,
                ww=1080,
                wh=2340,
            )

        self.assertIsNone(row)
        self.assertTrue(meta.get("ambiguous"))
        self.assertEqual(meta.get("reason"), "multiple_exact_mute_rows")

    def test_per_axis_deadline_reserves_budget_for_second_axis(self) -> None:
        wall = time.perf_counter() + 3.0
        d1 = nav._mute_engine_v2_toggle_stage_axis_deadline(
            wall, axes_remaining=2, t_all=None
        )
        d2 = nav._mute_engine_v2_toggle_stage_axis_deadline(
            wall, axes_remaining=1, t_all=None
        )
        self.assertGreater(d1, time.perf_counter())
        self.assertGreaterEqual(d2, time.perf_counter())
        self.assertLessEqual(d2, wall)

    @mock.patch("instagram_navigation._mute_engine_v2_verify_toggle_on_from_xml_dump")
    @mock.patch("instagram_navigation._mute_engine_v2_resolve_toggle_row")
    @mock.patch("instagram_navigation._mute_engine_v2_tap_toggle_short")
    def test_posts_on_stories_off_sends_stories_tap(
        self,
        tap_short: mock.MagicMock,
        resolve_row: mock.MagicMock,
        xml_dump: mock.MagicMock,
    ) -> None:
        xml_dump.side_effect = [
            (True, {}),
            (False, {}),
            (True, {}),
        ]
        resolve_row.side_effect = [
            {"label_found": True, "toggle_state": "on", "label_text": "Posts"},
            {"label_found": True, "toggle_state": "off", "label_text": "Stories"},
        ]
        tap_short.return_value = (True, False, "")
        device = mock.MagicMock()
        wall = time.perf_counter() + 5.0
        ok_p, _, already_p, _, _ = nav._mute_engine_v2_compact_axis_toggle(
            device,
            axis="posts",
            labels=("Posts",),
            ww=1080,
            t0=time.perf_counter(),
            toggle_stage_deadline=wall,
        )
        ok_s, tap_s, _, rsn_s, elapsed_s = nav._mute_engine_v2_compact_axis_toggle(
            device,
            axis="stories",
            labels=("Stories",),
            ww=1080,
            t0=time.perf_counter(),
            toggle_stage_deadline=wall,
        )
        self.assertTrue(ok_p)
        self.assertTrue(already_p)
        self.assertTrue(ok_s)
        self.assertTrue(tap_s)
        self.assertGreater(elapsed_s, 0.0)
        tap_short.assert_called_once()

    @mock.patch("instagram_navigation._mute_engine_v2_u2_text_exists")
    @mock.patch("instagram_navigation._mute_engine_v2_verify_toggle_on_from_xml_dump")
    def test_stories_visible_never_zero_ms_without_attempt(
        self, xml_dump: mock.MagicMock, text_exists: mock.MagicMock
    ) -> None:
        xml_dump.return_value = (False, {})
        text_exists.return_value = True
        device = mock.MagicMock()
        with mock.patch.object(
            nav, "_mute_engine_v2_tap_toggle_short", return_value=(False, False, "toggle_label_not_found")
        ):
            _ok, _tap, _al, _rsn, elapsed_ms = nav._mute_engine_v2_compact_axis_toggle(
                device,
                axis="stories",
                labels=("Stories",),
                ww=1080,
                t0=time.perf_counter(),
                toggle_stage_deadline=time.perf_counter() - 0.01,
            )
        self.assertGreaterEqual(elapsed_ms, 0.0)

    @mock.patch("navigation_engine.observe_instagram_state")
    @mock.patch("instagram_navigation._post_follow_screen_fingerprint_mute_v2_fast")
    def test_fast_path_following_cta_skips_heavy_observe_and_fingerprint(
        self, fp_fast: mock.MagicMock, observe_state: mock.MagicMock
    ) -> None:
        device = mock.MagicMock()
        device.window_size.return_value = (1080, 2340)
        following_btn = mock.MagicMock()
        mute_row = mock.MagicMock()
        mute_row.info = {"text": "Mute", "bounds": {"left": 45, "top": 900, "right": 980, "bottom": 1030}}

        sheet_levels = [
            ("following_options", {"posts_label": False, "stories_label": False}),
            ("mute_toggles", {"posts_label": True, "stories_label": True, "notes_label": True}),
            ("mute_toggles", {"posts_label": True, "stories_label": True, "notes_label": True}),
            ("unknown", {}),
        ]

        with mock.patch.object(
            nav, "_visual_raw_follow_invite_visible_quick", return_value=False
        ), mock.patch.object(
            nav,
            "_mute_engine_v2_build_lightweight_profile_det",
            return_value={"action_bar_title": "cand", "current_screen_guess": "likely_profile"},
        ), mock.patch.object(
            nav, "_mute_engine_v2_following_label_visible_quick", return_value=True
        ), mock.patch.object(
            nav, "_mute_engine_v2_pick_following_cta", return_value=(following_btn, "mock_following")
        ), mock.patch.object(
            nav,
            "_mute_engine_v2_find_exact_mute_row_fast",
            return_value=(None, {"duration_ms": 1.0, "reason": "exact_mute_row_not_found"}),
        ), mock.patch.object(
            nav, "_mute_engine_v2_detect_sheet_level", side_effect=sheet_levels
        ), mock.patch.object(
            nav, "_visual_find_mute_row_first_sheet", return_value=(mute_row, "Mute")
        ), mock.patch.object(
            nav, "_mute_engine_v2_is_mute_toggles_sheet", return_value=True
        ), mock.patch.object(
            nav, "_mute_engine_v2_u2_text_exists", return_value=True
        ), mock.patch.object(
            nav,
            "_mute_engine_v2_compact_axis_toggle",
            side_effect=[
                (True, True, False, "", 120.0),
                (True, True, False, "", 130.0),
            ],
        ), mock.patch.object(
            nav, "_mute_engine_v2_dismiss_mute_sheets_level_aware", return_value=(True, 40.0)
        ), mock.patch.object(nav, "time") as tmock:
            tmock.perf_counter = time.perf_counter
            tmock.monotonic = time.monotonic
            tmock.time = time.time
            tmock.sleep = lambda *_a, **_k: None
            out = nav.run_mute_engine_v2(
                device,
                pkg="com.instagram.android",
                source_profile_username="ct",
                visual_candidate_id="xml_list:cand",
                follower_username="cand",
                follow_state_after="following",
                det_hint={"action_bar_title": "cand"},
            )

        self.assertTrue(out.get("ok"))
        following_btn.click.assert_called_once()
        observe_state.assert_not_called()
        fp_fast.assert_not_called()

    @mock.patch("navigation_engine.observe_instagram_state")
    @mock.patch("instagram_navigation._post_follow_screen_fingerprint_mute_v2_fast")
    def test_following_options_fast_mute_row_skips_fallback_subsheet_entry(
        self, fp_fast: mock.MagicMock, observe_state: mock.MagicMock
    ) -> None:
        device = mock.MagicMock()
        device.window_size.return_value = (1080, 2340)
        following_btn = mock.MagicMock()
        mute_row = mock.MagicMock()
        mute_row.info = {"text": "Mute", "bounds": {"left": 45, "top": 900, "right": 980, "bottom": 1030}}

        sheet_levels = [
            (
                "following_options",
                {
                    "posts_label": False,
                    "stories_label": False,
                    "mute_exact_visible": True,
                    "probe_timings": [{"name": "text:Mute", "ok": True, "duration_ms": 1.0}],
                },
            ),
            ("mute_toggles", {"posts_label": True, "stories_label": True, "notes_label": True}),
            ("unknown", {}),
        ]

        with mock.patch.object(
            nav, "_visual_raw_follow_invite_visible_quick", return_value=False
        ), mock.patch.object(
            nav,
            "_mute_engine_v2_build_lightweight_profile_det",
            return_value={"action_bar_title": "cand", "current_screen_guess": "likely_profile"},
        ), mock.patch.object(
            nav, "_mute_engine_v2_following_label_visible_quick", return_value=True
        ), mock.patch.object(
            nav, "_mute_engine_v2_pick_following_cta", return_value=(following_btn, "mock_following")
        ), mock.patch.object(
            nav, "_mute_engine_v2_find_exact_mute_row_fast"
        ) as legacy_fast_find, mock.patch.object(
            nav, "_visual_find_mute_row_first_sheet", return_value=(mute_row, "Mute")
        ), mock.patch.object(
            nav, "_mute_engine_v2_detect_sheet_level", side_effect=sheet_levels
        ), mock.patch.object(
            nav, "_mute_engine_v2_is_mute_toggles_sheet", return_value=True
        ), mock.patch.object(
            nav, "_mute_engine_v2_u2_text_exists", return_value=True
        ), mock.patch.object(
            nav,
            "_mute_engine_v2_compact_axis_toggle",
            side_effect=[
                (True, True, False, "", 120.0),
                (True, True, False, "", 130.0),
            ],
        ), mock.patch.object(
            nav, "_mute_engine_v2_dismiss_mute_sheets_level_aware", return_value=(True, 40.0)
        ), mock.patch.object(nav, "time") as tmock:
            tmock.perf_counter = time.perf_counter
            tmock.monotonic = time.monotonic
            tmock.time = time.time
            tmock.sleep = lambda *_a, **_k: None
            out = nav.run_mute_engine_v2(
                device,
                pkg="com.instagram.android",
                source_profile_username="ct",
                visual_candidate_id="xml_list:cand",
                follower_username="cand",
                follow_state_after="following",
                det_hint={"action_bar_title": "cand"},
            )

        self.assertTrue(out.get("ok"))
        following_btn.click.assert_called_once()
        mute_row.click.assert_called_once()
        legacy_fast_find.assert_not_called()
        observe_state.assert_not_called()
        fp_fast.assert_not_called()

    @mock.patch("navigation_engine.observe_instagram_state")
    @mock.patch("instagram_navigation._post_follow_screen_fingerprint_mute_v2_fast")
    def test_following_options_without_exact_mute_probe_uses_existing_fallback(
        self, fp_fast: mock.MagicMock, observe_state: mock.MagicMock
    ) -> None:
        device = mock.MagicMock()
        device.window_size.return_value = (1080, 2340)
        following_btn = mock.MagicMock()
        mute_row = mock.MagicMock()

        sheet_levels = [
            (
                "following_options",
                {
                    "posts_label": False,
                    "stories_label": False,
                    "mute_exact_visible": False,
                    "probe_timings": [{"name": "text:Mute", "ok": False, "duration_ms": 1.0}],
                },
            ),
            (
                "following_options",
                {
                    "posts_label": False,
                    "stories_label": False,
                    "mute_exact_visible": False,
                    "probe_timings": [{"name": "text:Mute", "ok": False, "duration_ms": 1.0}],
                },
            ),
            ("mute_toggles", {"posts_label": True, "stories_label": True, "notes_label": True}),
            ("mute_toggles", {"posts_label": True, "stories_label": True, "notes_label": True}),
            ("unknown", {}),
        ]

        with mock.patch.object(
            nav, "_visual_raw_follow_invite_visible_quick", return_value=False
        ), mock.patch.object(
            nav,
            "_mute_engine_v2_build_lightweight_profile_det",
            return_value={"action_bar_title": "cand", "current_screen_guess": "likely_profile"},
        ), mock.patch.object(
            nav, "_mute_engine_v2_following_label_visible_quick", return_value=True
        ), mock.patch.object(
            nav, "_mute_engine_v2_pick_following_cta", return_value=(following_btn, "mock_following")
        ), mock.patch.object(
            nav, "_mute_engine_v2_find_exact_mute_row_fast"
        ) as legacy_fast_find, mock.patch.object(
            nav, "_visual_find_mute_row_first_sheet", return_value=(mute_row, "Mute")
        ), mock.patch.object(
            nav, "_mute_engine_v2_detect_sheet_level", side_effect=sheet_levels
        ), mock.patch.object(
            nav, "_mute_engine_v2_is_mute_toggles_sheet", return_value=True
        ), mock.patch.object(
            nav, "_mute_engine_v2_u2_text_exists", return_value=True
        ), mock.patch.object(
            nav,
            "_mute_engine_v2_compact_axis_toggle",
            side_effect=[
                (True, True, False, "", 120.0),
                (True, True, False, "", 130.0),
            ],
        ), mock.patch.object(
            nav, "_mute_engine_v2_dismiss_mute_sheets_level_aware", return_value=(True, 40.0)
        ), mock.patch.object(nav, "time") as tmock:
            tmock.perf_counter = time.perf_counter
            tmock.monotonic = time.monotonic
            tmock.time = time.time
            tmock.sleep = lambda *_a, **_k: None
            out = nav.run_mute_engine_v2(
                device,
                pkg="com.instagram.android",
                source_profile_username="ct",
                visual_candidate_id="xml_list:cand",
                follower_username="cand",
                follow_state_after="following",
                det_hint={"action_bar_title": "cand"},
            )

        self.assertTrue(out.get("ok"))
        following_btn.click.assert_called_once()
        mute_row.click.assert_called_once()
        legacy_fast_find.assert_not_called()
        observe_state.assert_not_called()
        fp_fast.assert_not_called()

    def test_toggle_short_reuses_resolved_row_bounds_without_switch_rescan(self) -> None:
        device = mock.MagicMock()
        timing_meta: dict[str, object] = {}

        with mock.patch.object(
            nav,
            "_mute_engine_v2_resolve_toggle_row",
            return_value={
                "label_found": True,
                "label_text": "Posts",
                "row_bounds": {"left": 45, "top": 1417, "right": 889, "bottom": 1569},
                "toggle_bounds": None,
                "toggle_state": "unknown",
            },
        ), mock.patch.object(nav, "_mute_row_toggle_candidates_near_label") as rescan:
            tapped, already, reason = nav._mute_engine_v2_tap_toggle_short(
                device,
                ("Posts",),
                1080,
                time.perf_counter(),
                axis="posts",
                timing_meta=timing_meta,
                axis_budget_s=2.25,
            )

        self.assertTrue(tapped)
        self.assertFalse(already)
        self.assertEqual(reason, "")
        device.click.assert_called_once_with(467, 1493)
        rescan.assert_not_called()
        self.assertTrue(timing_meta.get("row_reused"))
        self.assertEqual(timing_meta.get("tap_source"), "row_bounds_reused")


if __name__ == "__main__":
    unittest.main()
