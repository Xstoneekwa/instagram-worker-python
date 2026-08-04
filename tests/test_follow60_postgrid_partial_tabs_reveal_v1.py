from __future__ import annotations

import unittest

import instagram_navigation as nav


def _partial_tabs_xml(*, username: str, posts: int, selected_non_grid: str = "") -> str:
    """Samsung profile state where Instagram exports only partial tab structure.

    The Grid icon is visible and selected on screen, but it is not nested below
    ``profile_tabs_container`` in the hierarchy.  A separate Tagged/Reels
    marker still makes the profile-tabs surface structurally observable.
    Suggested accounts and Highlights occupy the viewport; no post cell is
    exported before the one bounded reveal.
    """
    non_grid = (
        f'<node resource-id="profile_tab_{selected_non_grid}" '
        f'content-desc="{selected_non_grid.title()}" selected="true" />'
        if selected_non_grid
        else '<node resource-id="profile_tab_tagged" content-desc="Tagged" selected="false" />'
    )
    return f"""<hierarchy>
      <node text="{username}" />
      <node resource-id="profile_header_count_container">
        <node text="{posts}"/><node text="Posts"/>
      </node>
      <node text="Suggested for you" bounds="[0,900][1080,980]"/>
      <node class="android.widget.ImageView" content-desc="Suggested profile photo" bounds="[40,1000][360,1320]"/>
      <node text="Highlights tray" bounds="[0,1500][1080,1600]"/>
      <node class="android.widget.ImageView" content-desc="Story highlight" bounds="[20,1620][340,1940]"/>
      <node resource-id="profile_tab_icon_view" content-desc="Grid view" selected="true" />
      {non_grid}
      <node resource-id="bottom_navigation" bounds="[0,2200][1080,2340]"/>
    </hierarchy>"""


class PartialTabsBelowFoldRevealV1Tests(unittest.TestCase):
    def test_positive_posts_partial_tabs_authorize_reveal_only(self) -> None:
        for username, posts in (
            ("burgergeeksaintherblain", 101),
            ("eclipse.acoustique", 9),
        ):
            with self.subTest(username=username):
                out = nav._post_follow_post_grid_evidence_from_xml(
                    _partial_tabs_xml(username=username, posts=posts),
                    candidate_username=username,
                    ww=1080,
                    wh=2340,
                    profile_identity_exact=True,
                    profile_origin_exact=True,
                )
                self.assertTrue(out["post_count_positive"])
                self.assertTrue(out["profile_tabs_present"])
                self.assertFalse(out["reels_or_tagged_selected"])
                self.assertEqual(out.get("visible_post_count", 0), 0)
                self.assertEqual(out["outcome"], "POST_GRID_REVEAL_REQUIRED")
                self.assertTrue(out["reveal_permission_only"])
                self.assertFalse(out["tap_safe"])
                self.assertIsNone(out["post_bounds"])

    def test_zero_posts_never_gain_reveal_permission(self) -> None:
        out = nav._post_follow_post_grid_evidence_from_xml(
            _partial_tabs_xml(username="zero.posts", posts=0),
            candidate_username="zero.posts",
            ww=1080,
            wh=2340,
            profile_identity_exact=True,
            profile_origin_exact=True,
        )
        self.assertNotEqual(out["outcome"], "POST_GRID_REVEAL_REQUIRED")

    def test_selected_reels_or_tagged_remains_fail_closed(self) -> None:
        for selected in ("reels", "tagged"):
            with self.subTest(selected=selected):
                out = nav._post_follow_post_grid_evidence_from_xml(
                    _partial_tabs_xml(
                        username="non.grid", posts=25, selected_non_grid=selected
                    ),
                    candidate_username="non.grid",
                    ww=1080,
                    wh=2340,
                    profile_identity_exact=True,
                    profile_origin_exact=True,
                )
                self.assertNotEqual(out["outcome"], "POST_GRID_REVEAL_REQUIRED")


if __name__ == "__main__":
    unittest.main()
