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


def _profile_shell(candidate: str, count: int, body: str = "") -> str:
    return f"""<hierarchy>
    <node text="{candidate}"/>
    <node resource-id="profile_header_count_container">
      <node text="{count}"/><node text="Posts"/>
    </node>
    {body}
    <node content-desc="Profile tab grid" selected="true"/>
    </hierarchy>"""


class NoPostsAndBelowFoldV4Tests(unittest.TestCase):
    def test_structural_zero_posts_pair_is_positive_no_posts(self) -> None:
        xml = _profile_shell(
            "candidate",
            0,
            '<node text="Suggested for you"/>'
            '<node class="android.widget.ImageView" content-desc="Suggested account photo" '
            'bounds="[0,800][360,1160]"/>',
        )
        out = nav._post_follow_post_grid_evidence_from_xml(
            xml, candidate_username="candidate", ww=1080, wh=2340
        )
        self.assertEqual(out["outcome"], "NO_POSTS_POSITIVE")
        self.assertTrue(out["posts_count_zero_exact"])
        self.assertEqual(out["physical_cell_count"], 0)
        self.assertIsNone(out["post_bounds"])

    def test_unrelated_zero_is_not_a_no_posts_proof(self) -> None:
        xml = """<hierarchy><node text="candidate"/>
        <node><node text="0"/><node text="Following"/></node>
        <node><node text="12"/><node text="Posts"/></node>
        <node text="Suggested for you"/>
        <node content-desc="Profile tab grid" selected="true"/>
        </hierarchy>"""
        out = nav._post_follow_post_grid_evidence_from_xml(
            xml, candidate_username="candidate", ww=1080, wh=2340
        )
        self.assertFalse(out["posts_count_zero_exact"])
        self.assertEqual(out["outcome"], "POST_GRID_REVEAL_REQUIRED")

    def test_nested_official_header_zero_is_positive_without_reveal(self) -> None:
        xml = """<hierarchy><node text="candidate"/>
        <node resource-id="profile_header_count_container">
          <node><node><node text="0"/></node><node><node text="Posts"/></node></node>
        </node>
        <node resource-id="profile_tabs_container">
          <node resource-id="profile_tab_icon_view" content-desc="Grid view"
                selected="true"/>
        </node><node resource-id="profile_empty_camera_artwork"
                   class="android.widget.ImageView" bounds="[250,900][830,1480]"/>
        </hierarchy>"""
        out = nav._post_follow_post_grid_evidence_from_xml(
            xml, candidate_username="candidate", ww=1080, wh=2340
        )
        self.assertEqual(out["outcome"], "NO_POSTS_POSITIVE")
        self.assertEqual(
            out["posts_count_source"],
            "profile_post_count_official_header_subtree",
        )
        self.assertEqual(out["physical_cell_count"], 0)
        self.assertFalse(out.get("reveal_scroll_attempted", False))

    def test_safe_first_row_is_not_rejected_by_clipped_lower_row(self) -> None:
        xml = """<hierarchy><node text="candidate"/>
        <node resource-id="profile_header_count_container"><node text="8"/><node text="Posts"/></node>
        <node resource-id="profile_tabs_container" bounds="[0,700][1080,850]">
          <node resource-id="profile_tab_icon_view" content-desc="Grid view"
                selected="true" bounds="[0,700][360,850]"/>
        </node>
        <node class="android.widget.ImageView" resource-id="profile_grid_media_0"
              bounds="[0,900][360,1260]"/>
        <node class="android.widget.ImageView" resource-id="profile_grid_media_1"
              bounds="[360,900][720,1260]"/>
        <node class="android.widget.ImageView" resource-id="profile_grid_media_2"
              bounds="[0,1900][360,2200]"/>
        <node resource-id="bottom_navigation" bounds="[0,2200][1080,2340]"/>
        </hierarchy>"""
        out = nav._post_follow_post_grid_evidence_from_xml(
            xml, candidate_username="candidate", ww=1080, wh=2340
        )
        self.assertEqual(out["outcome"], "POST_ROW_POSITIVE_SAFE")
        self.assertTrue(out["other_row_clipped"])
        self.assertFalse(out["candidate_clipped"])
        self.assertTrue(out["tap_safe"])

    def test_empty_state_artwork_never_becomes_media(self) -> None:
        xml = """<hierarchy><node text="candidate"/>
        <node content-desc="Profile tab grid" selected="true"/>
        <node resource-id="com.instagram.android:id/profile_empty_state">
          <node class="android.widget.ImageView" content-desc="Camera image"
                bounds="[0,1000][360,1360]"/>
          <node text="No Posts Yet"/>
        </node></hierarchy>"""
        out = nav._post_follow_post_grid_evidence_from_xml(
            xml, candidate_username="candidate", ww=1080, wh=2340
        )
        self.assertEqual(out["outcome"], "NO_POSTS_POSITIVE")
        self.assertTrue(out["empty_state_subtree_detected"])
        self.assertEqual(out["physical_cell_count"], 0)
        self.assertFalse(out.get("clipped_detected", False))

    def test_positive_posts_below_suggested_and_highlights_requires_reveal_only(self) -> None:
        xml = _profile_shell(
            "candidate",
            54,
            '<node text="Suggested for you"/>'
            '<node resource-id="profile_highlights_tray" content-desc="Story highlights"/>',
        )
        out = nav._post_follow_post_grid_evidence_from_xml(
            xml, candidate_username="candidate", ww=1080, wh=2340
        )
        self.assertEqual(out["outcome"], "POST_GRID_REVEAL_REQUIRED")
        self.assertTrue(out["post_count_positive"])
        self.assertTrue(out["reveal_permission_only"])
        self.assertFalse(out["tap_safe"])
        self.assertEqual(out["physical_cell_count"], 0)

    def test_reveal_required_performs_one_scroll_and_one_fresh_xml(self) -> None:
        evidence = nav._post_follow_post_grid_evidence_from_xml(
            _profile_shell("candidate", 24, '<node text="Suggested for you"/>'),
            candidate_username="candidate", ww=1080, wh=2340,
        )
        device = mock.MagicMock()
        device.dump_hierarchy.return_value = "<fresh-safe/>"
        with ExitStack() as stack:
            swipe = stack.enter_context(mock.patch.object(
                nav, "_post_follow_likes_profile_scroll_swipe",
                return_value={"swipe_ok": True, "scroll_distance_px": 390},
            ))
            classify = stack.enter_context(mock.patch.object(
                nav, "_post_follow_post_grid_evidence_from_xml",
                return_value={
                    "outcome": "POST_ROW_POSITIVE_SAFE",
                    "post_bounds": {"left": 0, "top": 900, "right": 360, "bottom": 1260},
                },
            ))
            stack.enter_context(mock.patch.object(canary, "invalidate"))
            stack.enter_context(mock.patch.object(nav.time, "sleep"))
            out = nav._post_follow_promote_ambiguous_grid_evidence_with_fresh_vision(
                device, evidence, ww=1080, wh=2340, candidate_username="candidate"
            )
        swipe.assert_called_once()
        device.dump_hierarchy.assert_called_once_with(compressed=False)
        classify.assert_called_once()
        self.assertEqual(out["outcome"], "POST_ROW_POSITIVE_SAFE")
        self.assertEqual(out["reveal_count_total_for_like_phase"], 1)
        self.assertTrue(out["old_bounds_invalidated"])

    def test_post_reveal_clipped_goes_golden_without_second_scroll(self) -> None:
        evidence = nav._post_follow_post_grid_evidence_from_xml(
            _profile_shell("candidate", 24, '<node text="Suggested for you"/>'),
            candidate_username="candidate", ww=1080, wh=2340,
        )
        device = mock.MagicMock()
        device.dump_hierarchy.return_value = "<fresh-clipped/>"
        with ExitStack() as stack:
            swipe = stack.enter_context(mock.patch.object(
                nav, "_post_follow_likes_profile_scroll_swipe",
                return_value={"swipe_ok": True, "scroll_distance_px": 390},
            ))
            stack.enter_context(mock.patch.object(
                nav, "_post_follow_post_grid_evidence_from_xml",
                return_value={
                    "outcome": "POST_ROW_POSITIVE_BUT_CLIPPED",
                    "rejection_reason": "positive_post_row_clipped",
                },
            ))
            stack.enter_context(mock.patch.object(canary, "invalidate"))
            stack.enter_context(mock.patch.object(nav.time, "sleep"))
            out = nav._post_follow_promote_ambiguous_grid_evidence_with_fresh_vision(
                device, evidence, ww=1080, wh=2340, candidate_username="candidate"
            )
        swipe.assert_called_once()
        self.assertEqual(out["outcome"], "POST_GRID_AMBIGUOUS_FINAL")
        self.assertEqual(out["reveal_count_total_for_like_phase"], 1)


class LikeTapContextFromA2V5V4Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(configure_canary(
            canary,
            account_id=TEST_CANARY_ACCOUNT_ID,
            account_username=TEST_CANARY_USERNAME,
            run_id="like-v4",
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
            "run_id": "like-v4",
            "request_id": "request-v4",
            "action_id": "action-v4",
            "attempt_id": 2,
            "business_session_id": "business-v4",
            "control_id": "control-v4",
            "worker_sha": "worker-v4",
        }

    def _xml(self, *, story: bool = False) -> str:
        story_node = '<node resource-id="story_viewer_root"/>' if story else ""
        return (
            f'<hierarchy>{story_node}<node text="Posts"/><node text="candidate"/>'
            '<node content-desc="Like" clickable="true" '
            'bounds="[40,1700][120,1800]"/></hierarchy>'
        )

    def _open_context(self, *, story: bool = False) -> dict[str, object]:
        return {
            "post_open_surface_audit": {
                "snapshot_xml": self._xml(story=story),
                "snapshot_captured_at_monotonic": time.perf_counter(),
                "snapshot_package": PKG,
                "snapshot_activity": ACT,
                "like_surface_ok": not story,
                "post_identity_confirmed": not story,
            }
        }

    def test_a2_v5_snapshot_builds_like_context_without_new_dump(self) -> None:
        with mock.patch.object(
            nav, "_followers_current_pkg_activity",
            return_value={"current_package": PKG, "current_activity": ACT},
        ):
            ctx, reason = nav._create_like_tap_context_v2(
                self.device,
                expected_package=PKG,
                expected_follower_username="candidate",
                expected_stage_binding=self._binding(),
                post_open_context=self._open_context(),
                allow_fresh_dump=False,
            )
        self.assertEqual(reason, "")
        self.device.dump_hierarchy.assert_not_called()
        self.assertEqual(ctx["snapshot_source"], "a2_v5_snapshot")
        self.assertEqual(ctx["attempt_id"], 2)
        self.assertEqual(ctx["business_session_id"], "business-v4")
        self.assertEqual(ctx["control_id"], "control-v4")
        self.assertEqual(ctx["worker_sha"], "worker-v4")
        self.assertTrue(ctx["one_shot_nonce"])

    def test_story_highlight_still_wins_without_reacquisition(self) -> None:
        with mock.patch.object(
            nav, "_followers_current_pkg_activity",
            return_value={"current_package": PKG, "current_activity": ACT},
        ):
            ctx, reason = nav._create_like_tap_context_v2(
                self.device,
                expected_package=PKG,
                expected_follower_username="candidate",
                expected_stage_binding=self._binding(),
                post_open_context=self._open_context(story=True),
                allow_fresh_dump=False,
            )
        self.assertIsNone(ctx)
        self.assertEqual(reason, "liketapcontext_story_or_highlight_detected")
        self.device.dump_hierarchy.assert_not_called()

    def test_invalid_snapshot_allows_exactly_one_explicit_reacquisition(self) -> None:
        self.device.dump_hierarchy.return_value = self._xml()
        with mock.patch.object(
            nav, "_followers_current_pkg_activity",
            return_value={"current_package": PKG, "current_activity": ACT},
        ):
            ctx, reason = nav._create_like_tap_context_v2(
                self.device,
                expected_package=PKG,
                expected_follower_username="candidate",
                expected_stage_binding=self._binding(),
                post_open_context={},
                allow_fresh_dump=True,
                force_fresh_dump=True,
            )
        self.assertEqual(reason, "")
        self.device.dump_hierarchy.assert_called_once()
        self.assertEqual(ctx["snapshot_source"], "single_reacquisition")

    def test_generation_change_and_consumed_context_fail_closed(self) -> None:
        with mock.patch.object(
            nav, "_followers_current_pkg_activity",
            return_value={"current_package": PKG, "current_activity": ACT},
        ):
            ctx, reason = nav._create_like_tap_context_v2(
                self.device,
                expected_package=PKG,
                expected_follower_username="candidate",
                expected_stage_binding=self._binding(),
                post_open_context=self._open_context(),
                allow_fresh_dump=False,
            )
        self.assertEqual(reason, "")
        canary.invalidate("test_navigation")
        ok, validation_reason, _ = nav._validate_like_tap_context_v2(
            ctx,
            expected_stage_binding=self._binding(),
            expected_follower_username="candidate",
        )
        self.assertFalse(ok)
        self.assertEqual(validation_reason, "liketapcontext_generation_changed")
        ctx["canonical_generation"] = canary.runtime_context()["ui_generation"]
        ctx["consumed"] = True
        ctx["proof_hash"] = nav._like_tap_context_v2_proof_hash(ctx)
        ok, validation_reason, _ = nav._validate_like_tap_context_v2(
            ctx,
            expected_stage_binding=self._binding(),
            expected_follower_username="candidate",
        )
        self.assertFalse(ok)
        self.assertEqual(validation_reason, "liketapcontext_consumed")


if __name__ == "__main__":
    unittest.main()
