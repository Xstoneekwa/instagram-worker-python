from __future__ import annotations

import unittest
from contextlib import ExitStack
from unittest import mock

import follow_60s_canary as canary
import instagram_navigation as nav


class BelowFoldRevealOnlyV1Tests(unittest.TestCase):
    def _xml(self, *, posts: str = "26 posts", extra: str = "") -> str:
        return f"""<hierarchy>
        <node text="candidate"/><node text="{posts}"/>
        <node text="Suggested for you" bounds="[0,900][1080,980]"/>
        <node text="Highlights tray" bounds="[0,1600][1080,1900]"/>
        {extra}
        <node resource-id="bottom_navigation" bounds="[0,2200][1080,2340]"/>
        </hierarchy>"""

    def test_positive_profile_below_fold_authorizes_reveal_only(self) -> None:
        out = nav._post_follow_post_grid_evidence_from_xml(
            self._xml(), candidate_username="candidate", ww=1080, wh=2340,
            profile_origin_exact=True,
        )
        self.assertEqual(out["outcome"], "POST_GRID_REVEAL_REQUIRED")
        self.assertTrue(out["reveal_permission_only"])
        self.assertFalse(out["tap_safe"])
        self.assertIsNone(out["post_bounds"])
        self.assertEqual(
            out["reveal_required_reason"],
            "positive_posts_suggested_or_highlights_grid_below_fold",
        )

    def test_positive_profile_without_exported_overlay_label_authorizes_reveal_only(self) -> None:
        """Samsung may omit Suggested/Highlights text from the hierarchy.

        Exact identity plus an authoritative positive Posts count and the
        absence of any tap-safe cell may authorize one bounded reveal only.
        It must never manufacture tap bounds from the profile screenshot.
        """
        xml = """<hierarchy>
        <node text="candidate"/>
        <node resource-id="profile_header_count_container">
          <node text="10"/><node text="Posts"/>
        </node>
        <node resource-id="bottom_navigation" bounds="[0,2200][1080,2340]"/>
        </hierarchy>"""
        out = nav._post_follow_post_grid_evidence_from_xml(
            xml, candidate_username="candidate", ww=1080, wh=2340,
            profile_origin_exact=True,
        )
        self.assertEqual(out["outcome"], "POST_GRID_REVEAL_REQUIRED")
        self.assertTrue(out["reveal_permission_only"])
        self.assertFalse(out["tap_safe"])
        self.assertIsNone(out["post_bounds"])
        self.assertEqual(out["visible_post_count"], 0)

    def test_suggested_and_highlight_imageviews_never_block_reveal_only(self) -> None:
        xml = """<hierarchy>
        <node text="candidate"/><node text="112 posts"/>
        <node text="Suggested for you" bounds="[0,900][1080,980]"/>
        <node class="android.widget.ImageView" content-desc="Suggested profile photo" bounds="[40,1000][360,1320]"/>
        <node class="android.widget.ImageView" content-desc="Suggested profile photo" bounds="[380,1000][700,1320]"/>
        <node text="Highlights tray" bounds="[0,1500][1080,1600]"/>
        <node class="android.widget.ImageView" content-desc="Story highlight" bounds="[20,1620][340,1940]"/>
        <node resource-id="bottom_navigation" bounds="[0,2200][1080,2340]"/>
        </hierarchy>"""
        out = nav._post_follow_post_grid_evidence_from_xml(
            xml,
            candidate_username="candidate",
            ww=1080,
            wh=2340,
            profile_origin_exact=True,
        )
        self.assertEqual(out["outcome"], "POST_GRID_REVEAL_REQUIRED")
        self.assertTrue(out["reveal_permission_only"])
        self.assertTrue(out["absolute_top_left_origin_proven"])
        self.assertIsNone(out["post_bounds"])

    def test_zero_posts_never_authorizes_reveal(self) -> None:
        out = nav._post_follow_post_grid_evidence_from_xml(
            self._xml(posts="0 posts", extra='<node text="No Posts Yet"/>'),
            candidate_username="candidate", ww=1080, wh=2340,
        )
        self.assertNotEqual(out["outcome"], "POST_GRID_REVEAL_REQUIRED")

    def test_private_loading_and_story_never_authorize_reveal(self) -> None:
        for marker in (
            '<node text="This account is private"/>',
            '<node resource-id="loading_spinner"/>',
            '<node resource-id="story_viewer_root"/>',
        ):
            with self.subTest(marker=marker):
                out = nav._post_follow_post_grid_evidence_from_xml(
                    self._xml(extra=marker), candidate_username="candidate", ww=1080, wh=2340
                )
                self.assertNotEqual(out["outcome"], "POST_GRID_REVEAL_REQUIRED")

    def test_reveal_only_runs_one_reveal_and_one_fresh_dump(self) -> None:
        before = nav._post_follow_post_grid_evidence_from_xml(
            self._xml(), candidate_username="candidate", ww=1080, wh=2340,
            profile_origin_exact=True,
        )
        device = mock.MagicMock()
        device.dump_hierarchy.return_value = "<fresh/>"
        with ExitStack() as stack:
            reveal = stack.enter_context(mock.patch.object(
                nav, "_post_follow_likes_profile_scroll_swipe",
                return_value={"swipe_ok": True, "scroll_distance_px": 420},
            ))
            stack.enter_context(mock.patch.object(canary, "invalidate"))
            stack.enter_context(mock.patch.object(nav.time, "sleep"))
            stack.enter_context(mock.patch.object(
                nav, "_post_follow_post_grid_evidence_from_xml",
                return_value={
                    "outcome": "POST_ROW_POSITIVE_SAFE",
                    "post_bounds": {"left": 0, "top": 900, "right": 360, "bottom": 1260},
                },
            ))
            stack.enter_context(mock.patch.object(
                nav,
                "_post_follow_post_reveal_safe_first_row_contract",
                side_effect=lambda _d, classified, **_kwargs: {
                    **classified,
                    "outcome": "POST_ROW_POSITIVE_SAFE",
                    "post_reveal_safe_contract": "PostRevealSafeFirstRowV1",
                    "post_reveal_xml_fingerprint": "a" * 64,
                    "post_reveal_coordinate_frame": {"version": "CoordinateFrameV1"},
                    "post_reveal_package": "com.instagram.android",
                    "post_reveal_activity": "InstagramMainActivity",
                },
            ))
            out = nav._post_follow_promote_ambiguous_grid_evidence_with_fresh_vision(
                device, before, ww=1080, wh=2340, candidate_username="candidate"
            )
        reveal.assert_called_once()
        device.dump_hierarchy.assert_called_once_with(compressed=False)
        self.assertEqual(out["outcome"], "POST_ROW_POSITIVE_SAFE")
        self.assertEqual(out["reveal_count_total_for_like_phase"], 1)


if __name__ == "__main__":
    unittest.main()
