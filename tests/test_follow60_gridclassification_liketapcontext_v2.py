from __future__ import annotations

import time
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


class GridClassificationAndTapProofV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(configure_canary(
            canary,
            account_id=TEST_CANARY_ACCOUNT_ID,
            account_username=TEST_CANARY_USERNAME,
            run_id="grid-v2",
            package=PKG,
            resume_policy=None,
        ))

    def tearDown(self) -> None:
        configure_canary(
            canary,
            account_id="other",
            account_username="other",
            run_id="reset",
            package=PKG,
            resume_policy=None,
        )

    def _frame(self) -> canary.CoordinateFrameV1:
        frame = canary.build_coordinate_frame_v1(
            source="raw_window_exact",
            raw_width=1080,
            raw_height=2340,
            canonical_width=1080,
            canonical_height=2340,
            navigation_generation=str(canary.runtime_context()["ui_generation"]),
            scroll_generation=int(canary.runtime_context()["scroll_counter"]),
        )
        assert frame is not None
        return frame

    def test_grid_classification_uses_one_canonical_generation_domain(self) -> None:
        proof = canary.stash_post_grid_evidence(
            candidate_username="candidate",
            package_name=PKG,
            activity_name=ACT,
            navigation_generation="candidate-context-generation",
            viewport_fingerprint="classified-xml",
            outcome=canary.POST_ROW_POSITIVE_BUT_CLIPPED,
            mute_sheet_closed=True,
            mute_posts_verified=True,
            mute_stories_verified=True,
            profile_identity_method="exact",
            screen_width=1080,
            screen_height=2340,
            grid_tab_state="selected",
            post_count_positive=True,
            physical_post_cells=[
                {"left": 0, "top": 2180, "right": 360, "bottom": 2330}
            ],
            first_post_bounds={
                "left": 0, "top": 2180, "right": 360, "bottom": 2330
            },
            coordinate_frame=self._frame(),
        )
        self.assertIsInstance(proof, canary.GridClassificationProof)
        self.assertEqual(proof.source_navigation_generation, "candidate-context-generation")
        consumed, _, reason = canary.consume_post_grid_evidence(
            candidate_username="candidate",
            package=PKG,
            activity=ACT,
            navigation_generation="different-context-domain-is-ignored",
            viewport_fingerprint="different-context-fingerprint-is-ignored",
            screen_size=(1080, 2340),
        )
        self.assertIsNotNone(consumed)
        self.assertEqual(reason, "")
        self.assertEqual(
            consumed.canonical_generation,
            canary.runtime_context()["ui_generation"],
        )

    def test_fresh_tap_proof_requires_same_xml_and_invalidates_on_scroll(self) -> None:
        xml = '<hierarchy><node text="candidate" bounds="[0,0][1080,2340]"/></hierarchy>'
        proof = canary.stash_fresh_tap_proof(
            "tap",
            subject_username="ct",
            target_username="candidate",
            package=PKG,
            activity=ACT,
            surface="candidate_profile_post_grid",
            bounds={"left": 0, "top": 900, "right": 360, "bottom": 1260},
            hierarchy_xml=xml,
            coordinate_frame=self._frame(),
            detection_source="fresh_xml",
            ttl_ms=500.0,
        )
        self.assertIsInstance(proof, canary.FreshTapProof)
        consumed, _, reason = canary.consume_fresh_tap_proof(
            "tap",
            subject_username="ct",
            target_username="candidate",
            package=PKG,
            activity=ACT,
            surface="candidate_profile_post_grid",
            screen_size=(1080, 2340),
            hierarchy_xml=xml,
        )
        self.assertIsNotNone(consumed)
        self.assertEqual(reason, "")

        canary.stash_fresh_tap_proof(
            "tap2",
            subject_username="ct",
            target_username="candidate",
            package=PKG,
            activity=ACT,
            surface="candidate_profile_post_grid",
            bounds={"left": 0, "top": 900, "right": 360, "bottom": 1260},
            hierarchy_xml=xml,
            coordinate_frame=self._frame(),
            detection_source="fresh_xml",
        )
        rejected, _, reason = canary.consume_fresh_tap_proof(
            "tap2",
            subject_username="ct",
            target_username="candidate",
            package=PKG,
            activity=ACT,
            surface="candidate_profile_post_grid",
            screen_size=(1080, 2340),
            hierarchy_xml=xml.replace("candidate", "other"),
        )
        self.assertIsNone(rejected)
        self.assertEqual(reason, "tap_xml_fingerprint_mismatch")

    def test_missing_tab_bounds_clipped_row_is_structural_and_suggested_is_excluded(self) -> None:
        xml = """<hierarchy>
        <node text="candidate"/><node text="15 posts"/>
        <node text="Suggested for you" bounds="[0,600][1080,680]"/>
        <node class="android.widget.ImageView" content-desc="Suggested account photo"
              bounds="[0,700][360,1060]"/>
        <node content-desc="Profile tab grid" selected="true"/>
        <node class="android.widget.ImageView" content-desc="Post thumbnail"
              bounds="[0,2100][360,2280]"/>
        <node resource-id="com.instagram.android:id/bottom_navigation"
              bounds="[0,2200][1080,2340]"/>
        </hierarchy>"""
        out = nav._post_follow_post_grid_evidence_from_xml(
            xml, candidate_username="candidate", ww=1080, wh=2340
        )
        self.assertEqual(out["outcome"], "POST_ROW_POSITIVE_BUT_CLIPPED")
        self.assertEqual(out["tabs_boundary_source"], "xml_order_after_posts_tab")
        self.assertTrue(out["suggested_region_separate"])
        self.assertEqual(len(out["physical_cells"]), 1)
        self.assertEqual(out["physical_cells"][0]["top"], 2100)

    def test_suggested_after_unbounded_tab_never_becomes_media(self) -> None:
        xml = """<hierarchy><node text="candidate"/><node text="15 posts"/>
        <node content-desc="Profile tab grid" selected="true"/>
        <node text="Suggested for you"/>
        <node class="android.widget.ImageView" content-desc="Suggested account photo"
              bounds="[0,2100][360,2280]"/>
        </hierarchy>"""
        out = nav._post_follow_post_grid_evidence_from_xml(
            xml, candidate_username="candidate", ww=1080, wh=2340
        )
        self.assertEqual(out["outcome"], "POST_GRID_AMBIGUOUS_FINAL")
        self.assertEqual(out["rejection_reason"], "profile_tabs_bounds_missing")

    def test_clipped_contract_performs_one_reveal_and_reacquires_safe_xml(self) -> None:
        device = mock.MagicMock()
        device.dump_hierarchy.return_value = "<fresh-safe-grid/>"
        clipped = {
            "outcome": "POST_ROW_POSITIVE_BUT_CLIPPED",
            "identity_exact": True,
            "profile_tabs_present": True,
            "grid_selected": True,
            "loading_visible": False,
            "private_profile_visible": False,
            "reels_or_tagged_selected": False,
            "tabs_bottom": 0,
            "tabs_boundary_source": "xml_order_after_posts_tab",
            "absolute_top_left_origin_proven": True,
            "candidate_username": "candidate",
            "post_bounds": {"left": 0, "top": 2180, "right": 360, "bottom": 2330},
        }
        fresh_safe = {
            "outcome": "POST_ROW_POSITIVE_SAFE",
            "post_bounds": {"left": 0, "top": 900, "right": 360, "bottom": 1260},
        }
        with ExitStack() as stack:
            reveal = stack.enter_context(mock.patch.object(
                nav,
                "_post_follow_likes_profile_scroll_swipe",
                return_value={"swipe_ok": True, "scroll_distance_px": 420},
            ))
            classify = stack.enter_context(mock.patch.object(
                nav,
                "_post_follow_post_grid_evidence_from_xml",
                return_value=dict(fresh_safe),
            ))
            invalidate = stack.enter_context(mock.patch.object(canary, "invalidate"))
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
            stack.enter_context(mock.patch.object(nav.time, "sleep"))
            out = nav._post_follow_promote_ambiguous_grid_evidence_with_fresh_vision(
                device,
                clipped,
                ww=1080,
                wh=2340,
                candidate_username="candidate",
            )
        reveal.assert_called_once()
        device.dump_hierarchy.assert_called_once_with(compressed=False)
        classify.assert_called_once_with(
            "<fresh-safe-grid/>",
            candidate_username="candidate",
            ww=1080,
            wh=2340,
            profile_identity_exact=True,
        )
        invalidate.assert_called_once_with("post_grid_v2_single_reveal_scroll")
        self.assertEqual(out["outcome"], "POST_ROW_POSITIVE_SAFE")
        self.assertEqual(out["reveal_count_total_for_like_phase"], 1)
        self.assertTrue(out["old_bounds_invalidated"])
        self.assertEqual(out["post_bounds"]["top"], 900)


class LikeTapContextV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(configure_canary(
            canary,
            account_id=TEST_CANARY_ACCOUNT_ID,
            account_username=TEST_CANARY_USERNAME,
            run_id="like-v2",
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

    def _binding(self) -> dict[str, str]:
        return {
            "account_id": TEST_CANARY_ACCOUNT_ID,
            "run_id": "like-v2",
            "request_id": "request-v2",
            "action_id": "action-v2",
        }

    def _xml(self, *, story: bool = False) -> str:
        story_node = '<node resource-id="com.instagram.android:id/story_viewer_root"/>' if story else ""
        return (
            f'<hierarchy>{story_node}<node text="Posts"/><node text="candidate"/>'
            '<node content-desc="Like" clickable="true" '
            'bounds="[40,1700][120,1800]"/></hierarchy>'
        )

    def test_like_context_is_created_at_last_boundary_and_old_postopen_age_is_irrelevant(self) -> None:
        self.device.dump_hierarchy.return_value = self._xml()
        with mock.patch.object(
            nav,
            "_followers_current_pkg_activity",
            return_value={"current_package": PKG, "current_activity": ACT},
        ):
            ctx, reason = nav._create_like_tap_context_v2(
                self.device,
                expected_package=PKG,
                expected_follower_username="candidate",
                expected_stage_binding=self._binding(),
                post_open_context={},
            )
        self.assertEqual(reason, "")
        self.assertEqual(ctx["version"], "LikeTapContextV2")
        self.assertTrue(ctx["v5_positive"])
        self.assertEqual(ctx["like_bounds"]["left"], 40)
        ok, reason, age = nav._validate_like_tap_context_v2(
            ctx,
            expected_stage_binding=self._binding(),
            expected_follower_username="candidate",
        )
        self.assertTrue(ok)
        self.assertEqual(reason, "")
        self.assertLess(age, ctx["ttl_ms"])

    def test_story_marker_wins_even_with_exact_like_and_candidate(self) -> None:
        self.device.dump_hierarchy.return_value = self._xml(story=True)
        with mock.patch.object(
            nav,
            "_followers_current_pkg_activity",
            return_value={"current_package": PKG, "current_activity": ACT},
        ):
            ctx, reason = nav._create_like_tap_context_v2(
                self.device,
                expected_package=PKG,
                expected_follower_username="candidate",
                expected_stage_binding=self._binding(),
                post_open_context={},
            )
        self.assertIsNone(ctx)
        self.assertEqual(reason, "liketapcontext_story_or_highlight_detected")

    def test_like_context_has_an_independent_strict_tap_ttl(self) -> None:
        self.device.dump_hierarchy.return_value = self._xml()
        with mock.patch.object(
            nav,
            "_followers_current_pkg_activity",
            return_value={"current_package": PKG, "current_activity": ACT},
        ):
            ctx, reason = nav._create_like_tap_context_v2(
                self.device,
                expected_package=PKG,
                expected_follower_username="candidate",
                expected_stage_binding=self._binding(),
                post_open_context={},
            )
        self.assertEqual(reason, "")
        ctx["created_at_monotonic"] = time.monotonic() - 1.0
        ctx["ttl_ms"] = 250.0
        ctx["proof_hash"] = nav._like_tap_context_v2_proof_hash(ctx)
        ok, reason, age = nav._validate_like_tap_context_v2(
            ctx,
            expected_stage_binding=self._binding(),
            expected_follower_username="candidate",
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "liketapcontext_ttl_expired")
        self.assertGreater(age, ctx["ttl_ms"])


if __name__ == "__main__":
    unittest.main()
