from __future__ import annotations

import unittest
from unittest import mock

import instagram_navigation as nav


PKG = "com.instagram.android"
ACT = "com.instagram.mainactivity.InstagramMainActivity"


class AbsoluteTopLeftIdentityV1Tests(unittest.TestCase):
    def _nostalgie_xml(self) -> str:
        return """<hierarchy>
        <node text="candidate"/><node text="78 posts"/>
        <node resource-id="profile_tabs_container" bounds="[0,904][1080,1040]">
          <node resource-id="profile_tab_icon_grid" content-desc="Grid view" selected="true" bounds="[0,904][360,1040]"/>
        </node>
        <node class="android.widget.ImageView" content-desc="Post thumbnail, row 1, column 1" bounds="[0,1041][359,1520]"/>
        <node class="android.widget.ImageView" content-desc="Post thumbnail, row 1, column 2" bounds="[360,1041][719,1520]"/>
        <node class="android.widget.ImageView" content-desc="Post thumbnail, row 1, column 3" bounds="[720,1041][1080,1521]"/>
        <node class="android.widget.ImageView" content-desc="Post thumbnail, row 2, column 1" bounds="[0,1522][359,2001]"/>
        <node class="android.widget.ImageView" content-desc="Post thumbnail, row 3, column 1" bounds="[0,2150][359,2200]"/>
        <node resource-id="bottom_navigation" bounds="[0,2200][1080,2340]"/>
        </hierarchy>"""

    def test_fully_visible_top_left_ignores_lower_clipped_row(self) -> None:
        out = nav._post_follow_post_grid_evidence_from_xml(
            self._nostalgie_xml(), candidate_username="candidate", ww=1080, wh=2340
        )
        self.assertEqual(out["outcome"], "POST_ROW_POSITIVE_SAFE")
        self.assertTrue(out["top_left_fully_visible"])
        self.assertTrue(out["lower_row_clipped"])
        self.assertEqual(out["absolute_row_index"], 1)
        self.assertEqual(out["absolute_column_index"], 1)
        self.assertEqual(out["post_bounds"]["top"], 1041)

    def test_post_reveal_requires_same_absolute_row_and_column(self) -> None:
        device = mock.MagicMock()
        classified = {
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
            "physical_cells": [{
                "left": 0, "top": 800, "right": 360, "bottom": 1160,
                "center_x": 180, "center_y": 980,
                "absolute_row_index": 2, "absolute_column_index": 1,
            }],
        }
        xml = '<hierarchy><node bounds="[0,0][1080,2340]"/></hierarchy>'
        with mock.patch.object(
            nav, "_followers_current_pkg_activity",
            return_value={"current_package": PKG, "current_activity": ACT},
        ):
            out = nav._post_follow_post_reveal_safe_first_row_contract(
                device, classified, hierarchy_xml=xml,
                candidate_username="candidate", expected_package=PKG,
                ww=1080, wh=2340,
                expected_absolute_cell={"row": 1, "column": 1},
            )
        self.assertEqual(out["outcome"], "POST_ROW_POSITIVE_BUT_CLIPPED")
        self.assertEqual(
            out["post_reveal_safe_rejection_reason"],
            "absolute_grid_cell_identity_not_preserved",
        )
        self.assertFalse(out["row_identity_preserved"])


if __name__ == "__main__":
    unittest.main()
