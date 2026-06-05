from __future__ import annotations

import unittest
from unittest import mock

import instagram_navigation as nav


class MuteEngineV2BudgetTest(unittest.TestCase):
    def test_remaining_below_required_skips_before_toggle(self) -> None:
        starved, required = nav._mute_engine_v2_budget_starved_for_toggle(
            0.951,
            want_posts=True,
            want_stories=True,
        )

        self.assertTrue(starved)
        self.assertEqual(required, nav.MIN_MUTE_TOGGLE_STAGE_SAMSUNG_SAFE_REQUIRED_S)

    def test_remaining_just_sufficient_allows_posts_and_stories_toggle(self) -> None:
        starved, required = nav._mute_engine_v2_budget_starved_for_toggle(
            1.567,
            want_posts=True,
            want_stories=True,
        )

        self.assertFalse(starved)
        self.assertLess(required, 1.567)

    def test_sheet_visible_budget_uses_samsung_safe_floor_not_old_pessimistic_floor(self) -> None:
        old_floor = nav.MIN_MUTE_TOGGLE_STAGE_BUDGET_S * 0.92
        required = nav._mute_engine_v2_toggle_required_budget_s(
            want_posts=True,
            want_stories=True,
        )

        self.assertEqual(round(old_floor, 2), 1.84)
        self.assertEqual(required, nav.MIN_MUTE_TOGGLE_STAGE_SAMSUNG_SAFE_REQUIRED_S)
        self.assertLess(required, old_floor)

    def test_pre_toggle_wait_preserves_toggle_budget_instead_of_sleeping_globally(self) -> None:
        sleep_s = nav._mute_engine_v2_pre_toggle_wait_s(
            remaining_s=1.567,
            desired_s=0.42,
            required_toggle_s=nav.MIN_MUTE_TOGGLE_STAGE_SAMSUNG_SAFE_REQUIRED_S,
        )

        self.assertGreaterEqual(sleep_s, 0.0)
        self.assertLess(sleep_s, 0.06)

    def test_mute_disabled_requires_no_toggle_budget(self) -> None:
        starved, required = nav._mute_engine_v2_budget_starved_for_toggle(
            0.0,
            want_posts=False,
            want_stories=False,
        )

        self.assertFalse(starved)
        self.assertEqual(required, 0.0)

    def test_single_axis_keeps_smaller_budget(self) -> None:
        starved, required = nav._mute_engine_v2_budget_starved_for_toggle(
            0.9,
            want_posts=True,
            want_stories=False,
        )

        self.assertFalse(starved)
        self.assertLess(required, nav.MIN_MUTE_TOGGLE_STAGE_SAMSUNG_SAFE_REQUIRED_S)

    def test_post_sheet_true_starved_floor_allows_compact_attempt_near_samsung_floor(self) -> None:
        self.assertLess(
            nav._MUTE_V2_POST_SHEET_TRUE_STARVED_S,
            nav.MIN_MUTE_TOGGLE_STAGE_SAMSUNG_SAFE_REQUIRED_S,
        )
        self.assertGreater(1.43, nav._MUTE_V2_POST_SHEET_TRUE_STARVED_S)

    def test_mute_perf_summary_payload_includes_sheet_dismiss_ms(self) -> None:
        payload = nav._post_follow_mute_perf_summary_payload(
            timings={"mute_total_ms": 100.0, "sheet_dismiss_ms": 42.0},
            result="partial_success",
            skip_reason="mute_partial_one_toggle",
            budget_s=4.0,
            effective_total_budget_s=7.5,
            toggle_stage_required_budget_s=1.52,
        )
        self.assertEqual(payload.get("sheet_dismiss_ms"), 42.0)

    @mock.patch("instagram_navigation._mute_engine_v2_verify_toggle_on_from_xml_dump")
    @mock.patch("instagram_navigation._mute_engine_v2_tap_toggle_short")
    def test_compact_axis_toggle_xml_first_skips_live_diag(
        self, tap_short: mock.MagicMock, xml_dump: mock.MagicMock
    ) -> None:
        xml_dump.side_effect = [
            (True, {"xml_verify_ok": True}),
            (True, {"xml_verify_ok": True}),
        ]
        tap_short.return_value = (False, True, "")
        device = mock.MagicMock()
        ok, tap_att, already, rsn, _ms = nav._mute_engine_v2_compact_axis_toggle(
            device,
            axis="stories",
            labels=("Stories",),
            ww=1080,
            t0=__import__("time").perf_counter(),
            toggle_stage_deadline=__import__("time").perf_counter() + 5.0,
        )
        self.assertTrue(ok)
        self.assertFalse(tap_att)
        self.assertTrue(already)
        tap_short.assert_not_called()

    def test_toggle_stage_axis_deadline_splits_remaining_wall(self) -> None:
        import time as time_mod

        wall = time_mod.perf_counter() + 2.0
        d1 = nav._mute_engine_v2_toggle_stage_axis_deadline(wall, axes_remaining=2)
        d2 = nav._mute_engine_v2_toggle_stage_axis_deadline(wall, axes_remaining=1)
        self.assertGreater(d1, time_mod.perf_counter())
        self.assertGreaterEqual(d2, time_mod.perf_counter())
        self.assertLessEqual(d2, wall)

    def test_map_toggle_wall_cap_to_axis_budget_exhausted(self) -> None:
        self.assertEqual(
            nav._mute_engine_v2_map_toggle_rsn("toggle_stage_wall_cap"),
            "mute_axis_budget_exhausted",
        )

    def test_missing_axis_reports_exact_axis(self) -> None:
        self.assertEqual(
            nav._mute_engine_v2_missing_axis(
                want_posts=True,
                want_stories=True,
                posts_ok=True,
                stories_ok=False,
            ),
            "stories",
        )
        self.assertEqual(
            nav._mute_engine_v2_missing_axis(
                want_posts=True,
                want_stories=True,
                posts_ok=False,
                stories_ok=True,
            ),
            "posts",
        )


    def test_toggle_stage_wall_cap_allows_dual_axis_samsung(self) -> None:
        self.assertGreaterEqual(nav._MUTE_V2_TOGGLE_STAGE_WALL_CAP_S, 4.0)
        self.assertGreaterEqual(nav._MUTE_V2_PER_AXIS_TOGGLE_MIN_S, 1.0)
        self.assertGreaterEqual(nav._POST_FOLLOW_LIKE_OVERLAY_PHASE_CAP_S, 10.5)
        self.assertGreaterEqual(nav._MUTE_ENGINE_V2_EFFECTIVE_TOTAL_S, 13.0)


if __name__ == "__main__":
    unittest.main()
