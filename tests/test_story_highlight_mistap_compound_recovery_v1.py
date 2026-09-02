from __future__ import annotations

import unittest
from unittest import mock

import instagram_navigation as nav


def _profile_xml(
    *,
    width: int = 1080,
    height: int = 2340,
    media: str = "",
    posts: int = 1,
) -> str:
    tabs_top = int(height * 0.30)
    tabs_bottom = tabs_top + int(height * 0.05)
    return (
        '<hierarchy><node text="cand" />'
        f'<node text="{posts} posts" />'
        '<node resource-id="com.instagram.android:id/profile_tabs_container" '
        f'bounds="[0,{tabs_top}][{width},{tabs_bottom}]">'
        '<node resource-id="com.instagram.android:id/profile_tab_icon_view" '
        'content-desc="Grid view" selected="true" '
        f'bounds="[0,{tabs_top}][{width // 3},{tabs_bottom}]"/>'
        f'</node>{media}</hierarchy>'
    )


def _real_cell_xml(*, width: int = 1080, height: int = 2340) -> str:
    top = int(height * 0.40)
    cell = (
        '<node class="android.widget.ImageView" '
        'content-desc="Photo, row 1 column 1" '
        f'bounds="[0,{top}][{width // 3},{top + width // 3}]"/>'
    )
    return _profile_xml(width=width, height=height, media=cell)


def _compound_prompt_xml(*, ambiguous: bool = False) -> str:
    first = (
        '<node text="Not now" clickable="true" enabled="true" '
        'bounds="[80,1700][1000,1830]">'
        '<node content-desc="Not now" clickable="true" enabled="true" '
        'bounds="[380,1730][700,1800]"/></node>'
    )
    second = (
        '<node content-desc="Not now" clickable="true" enabled="true" '
        'bounds="[80,1840][1000,1970]"/>'
        if ambiguous
        else ""
    )
    return (
        '<hierarchy>'
        '<node text="Leave a comment and show friends some love"/>'
        '<node text="You can now comment on stories"/>'
        '<node text="Try it" clickable="true" enabled="true" '
        'bounds="[80,1560][1000,1690]"/>'
        f'{first}{second}</hierarchy>'
    )


class _Device:
    def __init__(
        self,
        *,
        activity: str = "com.instagram.mainactivity.InstagramMainActivity",
    ) -> None:
        self.activity = activity
        self.clicks: list[tuple[int, int]] = []
        self.backs: list[str] = []

    def click(self, x: int, y: int) -> None:
        self.clicks.append((x, y))

    def press(self, key: str) -> None:
        self.backs.append(key)

    def app_current(self) -> dict[str, str]:
        return {
            "package": "com.instagram.android",
            "activity": self.activity,
        }


class StoryHighlightMisTapClassificationTests(unittest.TestCase):
    def _classify(self, xml: str, *, width: int = 1080, height: int = 2340):
        return nav._post_follow_post_grid_evidence_from_xml(
            xml,
            candidate_username="cand",
            ww=width,
            wh=height,
            profile_identity_exact=True,
            profile_origin_exact=True,
        )

    def test_a_real_first_post_grid_cell_is_accepted(self) -> None:
        out = self._classify(_real_cell_xml())
        self.assertEqual(out["outcome"], "POST_ROW_POSITIVE_SAFE")
        self.assertTrue(out["post_bounds"]["grid_lineage_proven"])


class GoldenHighlightPreventionTests(unittest.TestCase):
    def _classify(self, xml: str, *, width: int = 1080, height: int = 2340):
        return nav._post_follow_post_grid_evidence_from_xml(
            xml,
            candidate_username="cand",
            ww=width,
            wh=height,
            profile_identity_exact=True,
            profile_origin_exact=True,
        )

    def _proof(self, **overrides: object) -> dict[str, object]:
        proof: dict[str, object] = {
            "physical_cells": [{"grid_lineage_proven": True}],
            "highlights_region_detected": True,
            "highlights_region_separate": True,
            "suggested_region_separate": True,
        }
        proof.update(overrides)
        return proof

    def _guard(
        self,
        *,
        proof: dict[str, object] | None = None,
        existence: bool = True,
        profile_lock: bool = True,
        tabs_bottom: int | None = 700,
        col: int = 0,
        row: int = 0,
        top: int = 900,
        hints: dict[str, object] | None = None,
    ) -> tuple[bool, str]:
        return nav._post_follow_golden_post_open_intent_lineage_guard(
            proof if proof is not None else self._proof(),
            post_existence_reusable=existence,
            profile_lock_proved=profile_lock,
            profile_tabs_bottom_y_px=tabs_bottom,
            profile_tabs_margin_px=12,
            selected_col=col,
            selected_row=row,
            selected_top_y_px=top,
            ui_hints=hints,
        )

    def test_highlights_and_posts_with_separate_grid_lineage_pass(self) -> None:
        self.assertEqual(
            self._guard(),
            (True, "golden_fresh_structural_grid_lineage_proved"),
        )

    def test_highlights_and_reels_without_reusable_post_proof_fail(self) -> None:
        ok, _ = self._guard(existence=False)
        self.assertFalse(ok)

    def test_first_row_overlapping_highlights_fails(self) -> None:
        ok, reason = self._guard(top=705)
        self.assertFalse(ok)
        self.assertIn("overlaps", reason)

    def test_compact_layout_with_proved_boundary_passes(self) -> None:
        self.assertTrue(self._guard(tabs_bottom=430, top=455)[0])

    def test_partial_scroll_invalidating_profile_lock_fails(self) -> None:
        self.assertFalse(self._guard(profile_lock=False)[0])

    def test_stale_xml_transport_fails(self) -> None:
        self.assertFalse(self._guard(profile_lock=False)[0])

    def test_no_post_profile_fails(self) -> None:
        self.assertFalse(self._guard(existence=False)[0])

    def test_overlapping_bounds_fail(self) -> None:
        self.assertFalse(self._guard(tabs_bottom=900, top=905)[0])

    def test_suggested_layout_requires_separate_region(self) -> None:
        ok, reason = self._guard(
            proof=self._proof(suggested_region_separate=False),
            hints={"suggested_for_you": True},
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "golden_suggested_region_not_separate")

    def test_b_field_shape_highlight_descendant_near_grid_is_rejected(self) -> None:
        # Field-faithful structural evidence only: semantic Highlight ancestor,
        # image-shaped child, exact selected Grid boundary.  No unobserved
        # resource-id is invented for the Highlight itself.
        media = (
            '<node content-desc="Highlight">'
            '<node class="android.widget.ImageView" content-desc="Photo" '
            'bounds="[0,940][360,1300]"/></node>'
        )
        xml = _profile_xml(media=media, posts=711)
        # The historical heuristic accepted this shape: one-third width,
        # ImageView class and geometry below the selected Grid tab.
        self.assertIn('class="android.widget.ImageView"', xml)
        self.assertIn('bounds="[0,940][360,1300]"', xml)
        out = self._classify(xml)
        self.assertNotEqual(out["outcome"], "POST_ROW_POSITIVE_SAFE")
        self.assertIsNone(out.get("post_bounds"))

    def test_c_highlight_ring_above_grid_is_rejected(self) -> None:
        highlight = (
            '<node content-desc="Highlight ring">'
            '<node class="android.widget.ImageView" content-desc="Photo" '
            'bounds="[0,360][360,720]"/></node>'
        )
        out = self._classify(_profile_xml(media=highlight, posts=12))
        self.assertNotEqual(out["outcome"], "POST_ROW_POSITIVE_SAFE")

    def test_d_public_profile_with_one_real_post_remains_accepted(self) -> None:
        out = self._classify(_real_cell_xml())
        self.assertEqual(out["visible_post_count"], 1)
        self.assertEqual(out["absolute_row_index"], 1)
        self.assertEqual(out["absolute_column_index"], 1)

    def test_e_no_post_profile_has_no_false_grid_evidence(self) -> None:
        xml = _profile_xml(media='<node text="No Posts Yet"/>', posts=0)
        out = self._classify(xml)
        self.assertEqual(out["outcome"], "NO_POSTS_POSITIVE")
        self.assertEqual(out["physical_cells"], [])

    def test_f_samsung_bounds_do_not_replace_structural_lineage(self) -> None:
        out = self._classify(
            _real_cell_xml(width=720, height=1600),
            width=720,
            height=1600,
        )
        self.assertEqual(out["outcome"], "POST_ROW_POSITIVE_SAFE")
        self.assertTrue(out["post_bounds"]["grid_lineage_proven"])


class StoryCommentEducationClassifierTests(unittest.TestCase):
    def test_j_parent_child_not_now_resolves_to_one_physical_action(self) -> None:
        out = nav._story_comment_education_prompt_from_xml(
            _compound_prompt_xml()
        )
        self.assertTrue(out["recognized"])
        self.assertEqual(out["not_now_semantic_node_count"], 2)
        self.assertEqual(out["canonical_target_count"], 1)
        self.assertEqual(out["canonical_target"]["semantic_label"], "Not now")

    def test_k_genuinely_distinct_not_now_actions_are_ambiguous(self) -> None:
        out = nav._story_comment_education_prompt_from_xml(
            _compound_prompt_xml(ambiguous=True)
        )
        self.assertTrue(out["recognized"])
        self.assertEqual(out["canonical_target_count"], 2)
        self.assertEqual(
            out["reason"],
            "story_comment_education_not_now_target_ambiguous",
        )

    def test_proved_prompt_without_actionable_not_now_fails_closed(self) -> None:
        xml = _compound_prompt_xml().replace(
            'clickable="true" enabled="true" bounds="[80,1700][1000,1830]"',
            'clickable="false" enabled="true" bounds="[80,1700][1000,1830]"',
        ).replace(
            'clickable="true" enabled="true" bounds="[380,1730][700,1800]"',
            'clickable="false" enabled="true" bounds="[380,1730][700,1800]"',
        )
        out = nav._story_comment_education_prompt_from_xml(xml)
        self.assertTrue(out["recognized"])
        self.assertEqual(out["canonical_target_count"], 0)
        self.assertEqual(
            out["reason"], "story_comment_education_not_now_target_missing"
        )

    def test_generic_not_now_without_story_semantics_is_not_classified(self) -> None:
        out = nav._story_comment_education_prompt_from_xml(
            '<hierarchy><node text="Not now" clickable="true" '
            'enabled="true" bounds="[1,1][10,10]"/></hierarchy>'
        )
        self.assertFalse(out["recognized"])

    def test_profile_message_cta_alone_is_not_story_proof(self) -> None:
        out = nav._strong_story_highlight_recovery_proof_from_xml(
            '<hierarchy><node text="Message"/>'
            '<node content-desc="Profile tab grid"/></hierarchy>'
        )
        self.assertFalse(out["story_or_highlight_proved"])


class CompoundStoryRecoveryTests(unittest.TestCase):
    def _meta(self, device: _Device) -> dict[str, str]:
        return {
            "current_package": "com.instagram.android",
            "current_activity": device.activity,
        }

    def test_g_loriele_simple_highlight_recovery_is_one_back(self) -> None:
        device = _Device()
        with mock.patch.object(
            nav.config, "INSTAGRAM_PACKAGE", "com.instagram.android"
        ), mock.patch.object(
            nav, "_followers_current_pkg_activity",
            side_effect=lambda _d: self._meta(device),
        ), mock.patch.object(
            nav, "_wait_exact_candidate_profile_after_navigation",
            return_value={
                "exact_profile_proved": True,
                "return_profile_poll_count": 1,
                "return_profile_fast_confirmed": True,
                "return_profile_poll_wait_ms": 10.0,
            },
        ):
            out = nav.visual_return_to_profile_from_post(
                device,
                source_profile_username="ct",
                expected_profile_username="cand",
                open_surface_kind="highlight",
            )
        self.assertTrue(out["ok"])
        self.assertEqual(device.backs, ["back"])
        self.assertEqual(device.clicks, [])

    def test_normal_post_return_adds_no_xml_dump(self) -> None:
        device = _Device()
        with mock.patch.object(
            nav.config, "INSTAGRAM_PACKAGE", "com.instagram.android"
        ), mock.patch.object(
            nav, "_followers_current_pkg_activity",
            side_effect=lambda _d: self._meta(device),
        ), mock.patch.object(
            nav, "_visual_wait_exact_candidate_profile_after_back_fast",
            return_value={"exact_profile_proved": True},
        ) as fast_wait, mock.patch.object(
            nav, "_dump_post_viewer_hierarchy"
        ) as dump:
            out = nav.visual_return_to_profile_from_post(
                device,
                source_profile_username="ct",
                expected_profile_username="cand",
                open_surface_kind="post",
            )
        self.assertTrue(out["ok"])
        self.assertEqual(device.backs, ["back"])
        fast_wait.assert_called_once()
        dump.assert_not_called()

    def test_post_identity_unproven_recovery_is_one_back_without_business_action(self) -> None:
        device = _Device()
        with mock.patch.object(
            nav.config, "INSTAGRAM_PACKAGE", "com.instagram.android"
        ), mock.patch.object(
            nav, "_followers_current_pkg_activity",
            side_effect=lambda _d: self._meta(device),
        ), mock.patch.object(
            nav, "_visual_wait_exact_candidate_profile_after_back_fast",
            return_value={"exact_profile_proved": True},
        ) as fast_wait:
            out = nav.visual_return_to_profile_from_post(
                device,
                source_profile_username="ct",
                expected_profile_username="cand",
                open_surface_kind="post_identity_unproven",
            )
        self.assertTrue(out["ok"])
        self.assertEqual(device.backs, ["back"])
        self.assertEqual(device.clicks, [])
        fast_wait.assert_called_once()

    def test_h_story_viewer_recovery_is_one_back(self) -> None:
        device = _Device()
        with mock.patch.object(
            nav.config, "INSTAGRAM_PACKAGE", "com.instagram.android"
        ), mock.patch.object(
            nav, "_followers_current_pkg_activity",
            side_effect=lambda _d: self._meta(device),
        ), mock.patch.object(
            nav, "_wait_exact_candidate_profile_after_navigation",
            return_value={"exact_profile_proved": True},
        ):
            out = nav.visual_return_to_profile_from_post(
                device,
                source_profile_username="ct",
                expected_profile_username="cand",
                open_surface_kind="story",
            )
        self.assertTrue(out["ok"])
        self.assertEqual(device.backs, ["back"])

    def test_i_compound_prompt_clicks_not_now_then_one_back(self) -> None:
        device = _Device(activity="com.instagram.modal.TransparentModalActivity")
        with mock.patch.object(
            nav.config, "INSTAGRAM_PACKAGE", "com.instagram.android"
        ), mock.patch.object(
            nav, "_followers_current_pkg_activity",
            side_effect=lambda _d: self._meta(device),
        ), mock.patch.object(
            nav, "_wait_story_or_exact_profile_after_popup",
            return_value={
                "story_or_highlight_proved": True,
                "exact_profile_proved": False,
            },
        ), mock.patch.object(
            nav, "_wait_exact_candidate_profile_after_navigation",
            return_value={"exact_profile_proved": True},
        ):
            out = nav.visual_return_to_profile_from_post(
                device,
                source_profile_username="ct",
                expected_profile_username="cand",
                initial_hierarchy_xml=_compound_prompt_xml(),
                open_surface_kind="unknown",
            )
        self.assertTrue(out["ok"])
        self.assertTrue(out["not_now_clicked"])
        self.assertEqual(device.clicks, [(540, 1765)])
        self.assertEqual(device.backs, ["back"])

    def test_k_ambiguous_prompt_fails_without_gesture(self) -> None:
        device = _Device(activity="com.instagram.modal.TransparentModalActivity")
        with mock.patch.object(
            nav.config, "INSTAGRAM_PACKAGE", "com.instagram.android"
        ), mock.patch.object(
            nav, "_followers_current_pkg_activity",
            side_effect=lambda _d: self._meta(device),
        ):
            out = nav.visual_return_to_profile_from_post(
                device,
                source_profile_username="ct",
                expected_profile_username="cand",
                initial_hierarchy_xml=_compound_prompt_xml(ambiguous=True),
                open_surface_kind="unknown",
            )
        self.assertFalse(out["ok"])
        self.assertEqual(device.clicks, [])
        self.assertEqual(device.backs, [])

    def test_l_transparent_modal_cannot_be_profile_success(self) -> None:
        device = _Device(activity="com.instagram.modal.TransparentModalActivity")
        hierarchy = (
            '<hierarchy><node resource-id="com.instagram.android:id/action_bar_title" '
            'text="cand"/><node content-desc="Profile tab grid"/></hierarchy>'
        )
        out = nav._exact_candidate_profile_from_fresh_hierarchy(
            device,
            hierarchy_xml=hierarchy,
            pkg="com.instagram.android",
            expected_profile_username="cand",
        )
        self.assertFalse(out["exact_profile_proved"])
        self.assertEqual(out["identity_reason"], "profile_identity_dangerous_activity")

    def test_m_unknown_after_not_now_sends_no_blind_back(self) -> None:
        device = _Device(activity="com.instagram.modal.TransparentModalActivity")
        with mock.patch.object(
            nav.config, "INSTAGRAM_PACKAGE", "com.instagram.android"
        ), mock.patch.object(
            nav, "_followers_current_pkg_activity",
            side_effect=lambda _d: self._meta(device),
        ), mock.patch.object(
            nav, "_wait_story_or_exact_profile_after_popup",
            return_value={
                "story_or_highlight_proved": False,
                "exact_profile_proved": False,
            },
        ):
            out = nav.visual_return_to_profile_from_post(
                device,
                source_profile_username="ct",
                expected_profile_username="cand",
                initial_hierarchy_xml=_compound_prompt_xml(),
                open_surface_kind="unknown",
            )
        self.assertFalse(out["ok"])
        self.assertEqual(len(device.clicks), 1)
        self.assertEqual(device.backs, [])

    def test_n_candidate_identity_mismatch_after_back_fails_closed(self) -> None:
        device = _Device()
        with mock.patch.object(
            nav.config, "INSTAGRAM_PACKAGE", "com.instagram.android"
        ), mock.patch.object(
            nav, "_followers_current_pkg_activity",
            side_effect=lambda _d: self._meta(device),
        ), mock.patch.object(
            nav, "_wait_exact_candidate_profile_after_navigation",
            return_value={
                "exact_profile_proved": False,
                "reason": "fresh_candidate_profile_contract_failed",
            },
        ):
            out = nav.visual_return_to_profile_from_post(
                device,
                source_profile_username="ct",
                expected_profile_username="cand",
                open_surface_kind="highlight",
            )
        self.assertFalse(out["ok"])
        self.assertEqual(device.backs, ["back"])


if __name__ == "__main__":
    unittest.main()
