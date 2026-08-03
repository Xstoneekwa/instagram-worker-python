from __future__ import annotations

import hashlib
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
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
FIXTURES = Path(__file__).parent / "fixtures" / "follow60_compact_gridtab_exactlike_v5"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class CompactCountAndGridTabV5Tests(unittest.TestCase):
    def test_photosens_compact_count_and_exact_nested_grid_require_reveal(self) -> None:
        out = nav._post_follow_post_grid_evidence_from_xml(
            _fixture("photosens_below_fold.xml"),
            candidate_username="photosens_by_sand",
            ww=1080,
            wh=2340,
        )
        self.assertEqual(out["posts_count_value"], 101)
        self.assertEqual(out["posts_count_source"], "profile_post_count_accessibility_compact")
        self.assertTrue(out["posts_tab_selected"])
        self.assertEqual(out["posts_tab_source"], "profile_tab_icon_grid_view_selected")
        self.assertEqual(out["outcome"], "POST_GRID_REVEAL_REQUIRED")
        self.assertEqual(
            out["post_grid_reveal_required_reason"],
            "positive_posts_suggested_or_highlights_grid_below_fold",
        )

    def test_audrey_compact_count_and_exact_nested_grid_require_reveal(self) -> None:
        out = nav._post_follow_post_grid_evidence_from_xml(
            _fixture("audrey_below_fold.xml"),
            candidate_username="audrey.digitallife",
            ww=1080,
            wh=2340,
        )
        self.assertEqual(out["posts_count_value"], 25)
        self.assertTrue(out["post_count_positive"])
        self.assertTrue(out["profile_tabs_present"])
        self.assertTrue(out["grid_selected"])
        self.assertFalse(out.get("tap_safe", False))
        self.assertEqual(out["outcome"], "POST_GRID_REVEAL_REQUIRED")

    def test_compact_count_outside_profile_header_is_not_promoted(self) -> None:
        xml = """<hierarchy>
        <node text="candidate" />
        <node resource-id="suggested_accounts"><node text="101posts" /></node>
        <node resource-id="profile_tabs_container">
          <node resource-id="profile_tab_icon_view" content-desc="Grid view" selected="true" />
        </node></hierarchy>"""
        out = nav._post_follow_post_grid_evidence_from_xml(
            xml, candidate_username="candidate", ww=1080, wh=2340
        )
        self.assertFalse(out["post_count_positive"])
        self.assertIsNone(out["posts_count_value"])

    def test_reels_selected_does_not_promote_grid(self) -> None:
        xml = """<hierarchy><node resource-id="profile_header_container">
        <node text="candidate"/><node text="25posts"/></node>
        <node resource-id="profile_tabs_container">
          <node resource-id="profile_tab_icon_view" content-desc="Grid view" selected="false" />
          <node resource-id="profile_tab_icon_view" content-desc="Reels" selected="true" />
        </node></hierarchy>"""
        out = nav._post_follow_post_grid_evidence_from_xml(
            xml, candidate_username="candidate", ww=1080, wh=2340
        )
        self.assertFalse(out["grid_selected"])
        self.assertTrue(out["reels_or_tagged_selected"])
        self.assertNotEqual(out["outcome"], "POST_GRID_REVEAL_REQUIRED")

    def test_one_reveal_reacquires_fresh_safe_secours_grid(self) -> None:
        before = nav._post_follow_post_grid_evidence_from_xml(
            _fixture("photosens_below_fold.xml"),
            candidate_username="photosens_by_sand",
            ww=1080,
            wh=2340,
        )
        device = mock.MagicMock()
        device.dump_hierarchy.return_value = _fixture("secours_after_reveal.xml")
        with ExitStack() as stack:
            swipe = stack.enter_context(mock.patch.object(
                nav,
                "_post_follow_likes_profile_scroll_swipe",
                return_value={"swipe_ok": True, "scroll_distance_px": 390},
            ))
            invalidate = stack.enter_context(mock.patch.object(canary, "invalidate"))
            stack.enter_context(mock.patch.object(nav.time, "sleep"))
            out = nav._post_follow_promote_ambiguous_grid_evidence_with_fresh_vision(
                device,
                before,
                ww=1080,
                wh=2340,
                candidate_username="secourscatholique_laseyne",
            )
        swipe.assert_called_once()
        invalidate.assert_called_once_with("post_grid_v2_single_reveal_scroll")
        device.dump_hierarchy.assert_called_once_with(compressed=False)
        self.assertEqual(out["outcome"], "POST_ROW_POSITIVE_SAFE")
        self.assertEqual(out["post_reveal_classification"], "POST_ROW_POSITIVE_SAFE")
        self.assertEqual(out["reveal_count_total_for_like_phase"], 1)
        self.assertTrue(out["old_bounds_invalidated"])


class ExactA2V5LikeTransportV5Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(configure_canary(
            canary,
            account_id=TEST_CANARY_ACCOUNT_ID,
            account_username=TEST_CANARY_USERNAME,
            run_id="exact-like-v5",
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

    def _binding(self) -> dict[str, object]:
        return {
            "account_id": TEST_CANARY_ACCOUNT_ID,
            "run_id": "exact-like-v5",
            "request_id": "request-v5",
            "action_id": "action-v5",
            "attempt_id": 1,
        }

    def _xml(self, candidate: str, top: int, *, state: str = "Like", story: bool = False) -> str:
        story_node = '<node resource-id="story_viewer_root" />' if story else ""
        return (
            f'<hierarchy>{story_node}<node text="Posts"/><node text="{candidate}"/>'
            f'<node resource-id="com.instagram.android:id/row_feed_button_like" '
            f'content-desc="{state}" clickable="true" '
            f'bounds="[34,{top}][126,{top + 70}]"/></hierarchy>'
        )

    def _post_open_context(self, xml: str, candidate: str) -> dict[str, object]:
        exact = nav._hierarchy_collect_like_semantic_nodes(xml)[0]
        fingerprint = hashlib.sha256(xml.encode()).hexdigest()[:20]
        stage = {
            "version": "PostOpenContextV1",
            **self._binding(),
            "candidate_username": candidate,
            "package": PKG,
            "activity": ACT,
            "viewer_type": "Posts",
            "v5_positive": True,
            "story_or_highlight_detected": False,
            "exact_like_proof": exact,
            "exact_like_source": "a2_v5_exact_like",
            "exact_like_bounds": exact["matched_node_bounds"],
            "exact_like_bounds_hash": "fixture-bounds",
            "exact_like_xml_fingerprint": fingerprint,
            "exact_like_ui_generation": canary.runtime_context()["ui_generation"],
            "stage_nonce": "fixture-stage",
            "created_at_monotonic": time.monotonic(),
        }
        stage["proof_hash"] = nav._post_open_context_v1_proof_hash(stage)
        return {
            "post_open_surface_audit": {
                "snapshot_xml": xml,
                "snapshot_captured_at_monotonic": time.perf_counter(),
            },
            "post_open_context_v1": stage,
        }

    def _create(self, xml: str, candidate: str):
        with mock.patch.object(
            nav,
            "_followers_current_pkg_activity",
            return_value={"current_package": PKG, "current_activity": ACT},
        ):
            return nav._create_like_tap_context_v2(
                self.device,
                expected_package=PKG,
                expected_follower_username=candidate,
                expected_stage_binding=self._binding(),
                post_open_context=self._post_open_context(xml, candidate),
                allow_fresh_dump=False,
            )

    def test_secours_exact_like_at_y1817_is_transported_without_fixed_band(self) -> None:
        xml = self._xml("secours_candidate", 1782)
        ctx, reason = self._create(xml, "secours_candidate")
        self.assertEqual(reason, "")
        self.assertTrue(ctx["exact_like_transport_used"])
        self.assertEqual(ctx["like_bounds"]["top"], 1782)
        self.device.dump_hierarchy.assert_not_called()

    def test_bri_exact_like_at_y1547_is_transported(self) -> None:
        xml = _fixture("bri_post_viewer.xml")
        ctx, reason = self._create(xml, "bri_candidate")
        self.assertEqual(reason, "")
        self.assertEqual(ctx["like_bounds"]["top"], 1512)

    def test_christian_exact_like_at_y390_is_transported(self) -> None:
        xml = _fixture("christian_post_viewer.xml")
        ctx, reason = self._create(xml, "christian_candidate")
        self.assertEqual(reason, "")
        self.assertEqual(ctx["like_bounds"]["top"], 354)
        legacy_ok, legacy_reason = nav._follow_60s_safe_bounds_for_like(
            ctx["like_bounds"], screen_size=(1080, 2340)
        )
        self.assertFalse(legacy_ok)
        self.assertEqual(legacy_reason, "like_bounds_outside_action_band")

    def test_immutable_postopen_bridge_does_not_reparse_v5_on_happy_path(self) -> None:
        xml = (
            '<hierarchy><node resource-id="com.instagram.android:id/row_feed_button_like" '
            'content-desc="Like" clickable="true" bounds="[34,1512][126,1582]"/>'
            '</hierarchy>'
        )
        post_open = self._post_open_context(xml, "candidate")
        with mock.patch.object(
            nav,
            "_followers_current_pkg_activity",
            return_value={"current_package": PKG, "current_activity": ACT},
        ), mock.patch.object(
            nav,
            "_post_open_hierarchy_identity_signals",
            side_effect=AssertionError("happy path must consume PostOpenContextV1"),
        ):
            ctx, reason = nav._create_like_tap_context_v2(
                self.device,
                expected_package=PKG,
                expected_follower_username="candidate",
                expected_stage_binding=self._binding(),
                post_open_context=post_open,
                allow_fresh_dump=False,
            )
        self.assertEqual(reason, "")
        self.assertTrue(ctx["exact_like_transport_used"])
        self.assertEqual(ctx["candidate_identity_source"], "post_open_context_v1")
        self.device.dump_hierarchy.assert_not_called()

    def test_tampered_immutable_exact_like_proof_is_rejected(self) -> None:
        xml = self._xml("candidate", 1512)
        post_open = self._post_open_context(xml, "candidate")
        post_open["post_open_context_v1"]["exact_like_proof"][
            "matched_node_content_desc"
        ] = "Comment"
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
                post_open_context=post_open,
                allow_fresh_dump=False,
            )
        self.assertIsNone(ctx)
        self.assertEqual(reason, "liketapcontext_post_open_hash_mismatch")

    def test_story_highlight_wins_even_with_exact_like(self) -> None:
        xml = self._xml("candidate", 1512, story=True)
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
                post_open_context={
                    "post_open_surface_audit": {
                        "snapshot_xml": xml,
                        "snapshot_captured_at_monotonic": time.perf_counter(),
                    }
                },
                allow_fresh_dump=False,
            )
        self.assertIsNone(ctx)
        self.assertEqual(reason, "liketapcontext_story_or_highlight_detected")

    def test_already_liked_is_rejected(self) -> None:
        xml = self._xml("candidate", 1512, state="Unlike")
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
                post_open_context={
                    "post_open_surface_audit": {
                        "snapshot_xml": xml,
                        "snapshot_captured_at_monotonic": time.perf_counter(),
                    }
                },
                allow_fresh_dump=False,
            )
        self.assertIsNone(ctx)
        self.assertEqual(reason, "liketapcontext_already_liked")

    def test_non_like_action_node_is_rejected(self) -> None:
        xml = (
            '<hierarchy><node text="Posts"/><node text="candidate"/>'
            '<node resource-id="com.instagram.android:id/row_feed_button_comment" '
            'content-desc="Comment" clickable="true" bounds="[34,1512][126,1582]"/>'
            '</hierarchy>'
        )
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
                post_open_context={
                    "post_open_surface_audit": {
                        "snapshot_xml": xml,
                        "snapshot_captured_at_monotonic": time.perf_counter(),
                    }
                },
                allow_fresh_dump=False,
            )
        self.assertIsNone(ctx)
        self.assertEqual(reason, "liketapcontext_exact_like_missing")

    def test_wrong_binding_rejects_transported_proof(self) -> None:
        xml = self._xml("candidate", 1512)
        post_open = self._post_open_context(xml, "candidate")
        post_open["post_open_context_v1"]["request_id"] = "wrong-request"
        post_open["post_open_context_v1"]["proof_hash"] = nav._post_open_context_v1_proof_hash(
            post_open["post_open_context_v1"]
        )
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
                post_open_context=post_open,
                allow_fresh_dump=False,
            )
        self.assertIsNone(ctx)
        self.assertEqual(reason, "liketapcontext_post_open_binding_mismatch")

    def test_generation_change_rejects_at_consumption(self) -> None:
        ctx, reason = self._create(self._xml("candidate", 1512), "candidate")
        self.assertEqual(reason, "")
        canary.invalidate("test_generation_change")
        ok, validation_reason, _ = nav._validate_like_tap_context_v2(
            ctx,
            expected_stage_binding=self._binding(),
            expected_follower_username="candidate",
        )
        self.assertFalse(ok)
        self.assertEqual(validation_reason, "liketapcontext_generation_changed")


if __name__ == "__main__":
    unittest.main()
