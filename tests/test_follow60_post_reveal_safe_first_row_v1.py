from __future__ import annotations

import unittest
from contextlib import ExitStack
from unittest import mock

import follow_60s_canary as canary
import instagram_navigation as nav
from tests.follow60_generic_fixtures import (
    TEST_CANARY_ACCOUNT_ID,
    TEST_CANARY_USERNAME,
    configure_canary,
)


PKG = "com.instagram.android"
ACT = "com.instagram.mainactivity.InstagramMainActivity"


class PostRevealSafeFirstRowV1Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.package_patch = mock.patch.object(nav.config, "INSTAGRAM_PACKAGE", PKG)
        self.package_patch.start()
        self.addCleanup(self.package_patch.stop)
        self.assertTrue(configure_canary(
            canary,
            account_id=TEST_CANARY_ACCOUNT_ID,
            account_username=TEST_CANARY_USERNAME,
            run_id="post-reveal-safe-row",
            package=PKG,
            resume_policy=None,
        ))
        self.device = mock.MagicMock()
        self.device.window_size.return_value = (1080, 2340)

    def tearDown(self) -> None:
        configure_canary(
            canary,
            account_id="other",
            account_username="other",
            run_id="reset",
            package=PKG,
            resume_policy=None,
        )

    def _xml(self, *, extra: str = "") -> str:
        return f"""<hierarchy>
        <node text="candidate"/>
        <node resource-id="profile_header_count_container"><node text="12"/><node text="Posts"/></node>
        <node resource-id="profile_tabs_container" bounds="[0,600][1080,740]">
          <node resource-id="profile_tab_icon_grid" content-desc="Grid view" selected="true" bounds="[0,600][360,740]"/>
        </node>
        <node class="android.widget.ImageView" resource-id="media_grid_cell_0" content-desc="Post thumbnail" bounds="[0,800][360,1160]"/>
        <node class="android.widget.ImageView" resource-id="media_grid_cell_1" content-desc="Post thumbnail" bounds="[360,800][720,1160]"/>
        <node class="android.widget.ImageView" resource-id="media_grid_cell_lower" content-desc="Post thumbnail" bounds="[0,2150][360,2200]"/>
        {extra}
        <node resource-id="bottom_navigation" bounds="[0,2200][1080,2340]"/>
        </hierarchy>"""

    def _classified(self, **overrides: object) -> dict[str, object]:
        out: dict[str, object] = {
            "outcome": "POST_ROW_POSITIVE_BUT_CLIPPED",
            "candidate_username": "candidate",
            "identity_exact": True,
            "profile_tabs_present": True,
            "grid_selected": True,
            "post_count_positive": True,
            "reels_or_tagged_selected": False,
            "loading_visible": False,
            "private_profile_visible": False,
            "suggested_region_detected": False,
            "highlights_region_detected": False,
            "tabs_bottom": 740,
            "fully_exploitable_bottom": 2200,
            "physical_cells": [
                {"left": 0, "top": 800, "right": 360, "bottom": 1160, "center_x": 180, "center_y": 980},
                {"left": 360, "top": 800, "right": 720, "bottom": 1160, "center_x": 540, "center_y": 980},
                {"left": 0, "top": 2150, "right": 360, "bottom": 2200, "center_x": 180, "center_y": 2175},
            ],
        }
        out.update(overrides)
        return out

    def _run(self, *, classified: dict[str, object] | None = None, xml: str | None = None, package: str = PKG, activity: str = ACT):
        with mock.patch.object(
            nav,
            "_followers_current_pkg_activity",
            return_value={"current_package": package, "current_activity": activity},
        ):
            return nav._post_follow_post_reveal_safe_first_row_contract(
                self.device,
                classified or self._classified(),
                hierarchy_xml=xml or self._xml(),
                candidate_username="candidate",
                expected_package=PKG,
                ww=1080,
                wh=2340,
            )

    def test_promotes_fully_visible_first_row(self) -> None:
        out = self._run()
        self.assertEqual(out["outcome"], "POST_ROW_POSITIVE_SAFE")
        self.assertEqual(out["post_reveal_safe_contract"], "PostRevealSafeFirstRowV1")

    def test_chooses_leftmost_cell_of_first_safe_row(self) -> None:
        out = self._run()
        self.assertEqual(out["post_bounds"]["left"], 0)
        self.assertEqual(out["post_bounds"]["top"], 800)

    def test_lower_clipped_row_does_not_poison_safe_first_row(self) -> None:
        out = self._run()
        self.assertTrue(out["other_row_clipped"])
        self.assertFalse(out["candidate_clipped"])

    def test_post_mute_row_below_legacy_58_percent_is_safe_when_fully_visible(self) -> None:
        cells = [
            {
                "left": 0,
                "top": 1400,
                "right": 360,
                "bottom": 1760,
                "center_x": 180,
                "center_y": 1580,
            },
            {
                "left": 0,
                "top": 2150,
                "right": 360,
                "bottom": 2200,
                "center_x": 180,
                "center_y": 2175,
            },
        ]
        out = self._run(classified=self._classified(physical_cells=cells))
        self.assertEqual(out["outcome"], "POST_ROW_POSITIVE_SAFE")
        self.assertEqual(out["post_bounds"]["top"], 1400)

    def test_post_reveal_row_below_safe_viewport_remains_rejected(self) -> None:
        cells = [
            {
                "left": 0,
                "top": 1780,
                "right": 360,
                "bottom": 2140,
                "center_x": 180,
                "center_y": 1960,
            }
        ]
        out = self._run(classified=self._classified(physical_cells=cells))
        self.assertEqual(out["outcome"], "POST_ROW_POSITIVE_BUT_CLIPPED")
        self.assertEqual(
            out["post_reveal_safe_rejection_reason"],
            "post_reveal_fully_visible_first_row_missing",
        )

    def test_fingerprint_generation_and_frame_are_bound(self) -> None:
        out = self._run()
        self.assertEqual(len(out["post_reveal_xml_fingerprint"]), 64)
        self.assertIn("post_reveal_ui_generation", out)
        self.assertTrue(out["post_reveal_coordinate_frame"])

    def test_suggested_region_rejects(self) -> None:
        out = self._run(classified=self._classified(suggested_region_detected=True))
        self.assertEqual(out["outcome"], "POST_ROW_POSITIVE_BUT_CLIPPED")

    def test_highlights_region_rejects(self) -> None:
        out = self._run(classified=self._classified(highlights_region_detected=True))
        self.assertEqual(out["outcome"], "POST_ROW_POSITIVE_BUT_CLIPPED")

    def test_story_marker_rejects(self) -> None:
        out = self._run(xml=self._xml(extra='<node resource-id="story_viewer_root"/>'))
        self.assertEqual(out["post_reveal_safe_rejection_reason"], "story_highlight_marker_present")

    def test_identity_mismatch_rejects(self) -> None:
        out = self._run(classified=self._classified(identity_exact=False))
        self.assertEqual(out["outcome"], "POST_ROW_POSITIVE_BUT_CLIPPED")

    def test_posts_tab_missing_rejects(self) -> None:
        out = self._run(classified=self._classified(profile_tabs_present=False))
        self.assertEqual(out["outcome"], "POST_ROW_POSITIVE_BUT_CLIPPED")

    def test_grid_not_selected_rejects(self) -> None:
        out = self._run(classified=self._classified(grid_selected=False))
        self.assertEqual(out["outcome"], "POST_ROW_POSITIVE_BUT_CLIPPED")

    def test_reels_or_tagged_selected_rejects(self) -> None:
        out = self._run(classified=self._classified(reels_or_tagged_selected=True))
        self.assertEqual(out["outcome"], "POST_ROW_POSITIVE_BUT_CLIPPED")

    def test_package_mismatch_rejects(self) -> None:
        out = self._run(package="other.package")
        self.assertEqual(out["post_reveal_safe_rejection_reason"], "post_reveal_package_mismatch")

    def test_activity_mismatch_rejects(self) -> None:
        out = self._run(activity="com.instagram.story.StoryActivity")
        self.assertEqual(out["post_reveal_safe_rejection_reason"], "post_reveal_activity_mismatch")

    def test_candidate_mismatch_rejects(self) -> None:
        out = self._run(classified=self._classified(candidate_username="other"))
        self.assertEqual(out["post_reveal_safe_rejection_reason"], "post_reveal_candidate_mismatch")

    def test_untrusted_coordinate_frame_rejects(self) -> None:
        out = self._run(xml=self._xml().replace('1080', '900'))
        self.assertEqual(out["post_reveal_safe_rejection_reason"], "post_reveal_frame_untrusted")

    def test_no_fully_visible_first_row_rejects(self) -> None:
        clipped = self._classified(physical_cells=[
            {"left": 0, "top": 2150, "right": 360, "bottom": 2200, "center_x": 180, "center_y": 2175},
        ])
        out = self._run(classified=clipped)
        self.assertEqual(
            out["post_reveal_safe_rejection_reason"],
            "post_reveal_fully_visible_first_row_missing",
        )

    def test_promoter_keeps_exactly_one_reveal_and_one_dump(self) -> None:
        before = self._classified(outcome="POST_GRID_REVEAL_REQUIRED")
        self.device.dump_hierarchy.return_value = self._xml()
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(
                nav,
                "_post_follow_likes_profile_scroll_swipe",
                return_value={"swipe_ok": True, "scroll_distance_px": 390},
            ))
            stack.enter_context(mock.patch.object(canary, "invalidate"))
            stack.enter_context(mock.patch.object(nav.time, "sleep"))
            stack.enter_context(mock.patch.object(
                nav,
                "_followers_current_pkg_activity",
                return_value={"current_package": PKG, "current_activity": ACT},
            ))
            out = nav._post_follow_promote_ambiguous_grid_evidence_with_fresh_vision(
                self.device,
                before,
                ww=1080,
                wh=2340,
                candidate_username="candidate",
            )
        self.device.dump_hierarchy.assert_called_once_with(compressed=False)
        self.assertEqual(out["reveal_count_total_for_like_phase"], 1)
        self.assertEqual(out["outcome"], "POST_ROW_POSITIVE_SAFE")
        self.assertEqual(len(out["post_reveal_xml_fingerprint"]), 64)
        self.assertTrue(out["post_reveal_coordinate_frame"])


if __name__ == "__main__":
    unittest.main()
