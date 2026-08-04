from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
import unittest
from unittest import mock

import follow_60s_canary as canary
import instagram_navigation as nav


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "follow60_postgrid_below_fold"
CASES = {
    "emberlangley": 2165,
    "lescreasdesasa13": 145,
    "mangana83": 1333,
    "rempicard": 148,
    "farmasi__benedicte_": 218,
}


class RealBelowFoldLayoutsV1Tests(unittest.TestCase):
    def _classify(self, username: str) -> dict[str, object]:
        xml = (FIXTURE_DIR / f"{username}.xml").read_text(encoding="utf-8")
        return nav._post_follow_post_grid_evidence_from_xml(
            xml,
            candidate_username=username,
            ww=1080,
            wh=2340,
            profile_origin_exact=True,
        )

    def test_all_five_real_layouts_authorize_reveal_without_text_or_tabs(self) -> None:
        for username, expected_count in CASES.items():
            with self.subTest(username=username):
                out = self._classify(username)
                self.assertEqual(out["posts_count_value"], expected_count)
                self.assertTrue(out["post_count_positive"])
                self.assertEqual(out["outcome"], "POST_GRID_REVEAL_REQUIRED")
                self.assertTrue(out["reveal_permission_only"])
                self.assertFalse(out["tap_safe"])
                self.assertIsNone(out["post_bounds"])

    def test_reveal_permission_executes_once_then_requires_fresh_xml(self) -> None:
        for username in CASES:
            with self.subTest(username=username):
                evidence = self._classify(username)
                device = mock.MagicMock()
                device.dump_hierarchy.return_value = "<fresh-safe/>"
                with ExitStack() as stack:
                    swipe = stack.enter_context(mock.patch.object(
                        nav,
                        "_post_follow_likes_profile_scroll_swipe",
                        return_value={"swipe_ok": True, "scroll_distance_px": 390},
                    ))
                    classify = stack.enter_context(mock.patch.object(
                        nav,
                        "_post_follow_post_grid_evidence_from_xml",
                        return_value={
                            "outcome": "POST_ROW_POSITIVE_SAFE",
                            "post_bounds": {
                                "left": 0, "top": 900, "right": 360, "bottom": 1260,
                            },
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
                    stack.enter_context(mock.patch.object(canary, "invalidate"))
                    stack.enter_context(mock.patch.object(nav.time, "sleep"))
                    out = nav._post_follow_promote_ambiguous_grid_evidence_with_fresh_vision(
                        device,
                        evidence,
                        ww=1080,
                        wh=2340,
                        candidate_username=username,
                    )
                swipe.assert_called_once()
                device.dump_hierarchy.assert_called_once_with(compressed=False)
                classify.assert_called_once_with(
                    "<fresh-safe/>",
                    candidate_username=username,
                    ww=1080,
                    wh=2340,
                    profile_identity_exact=True,
                )
                self.assertEqual(out["reveal_count_total_for_like_phase"], 1)

    def test_unsafe_surfaces_never_get_reveal_permission(self) -> None:
        base = (FIXTURE_DIR / "emberlangley.xml").read_text(encoding="utf-8")
        for marker in (
            '<node text="No Posts Yet"/>',
            '<node text="This account is private"/>',
            '<node class="android.widget.ProgressBar"/>',
            '<node resource-id="story_viewer_root"/>',
            '<node resource-id="highlight_viewer_root"/>',
        ):
            with self.subTest(marker=marker):
                xml = base.replace("</hierarchy>", f"{marker}</hierarchy>")
                out = nav._post_follow_post_grid_evidence_from_xml(
                    xml,
                    candidate_username="emberlangley",
                    ww=1080,
                    wh=2340,
                )
                self.assertNotEqual(out["outcome"], "POST_GRID_REVEAL_REQUIRED")


if __name__ == "__main__":
    unittest.main()
