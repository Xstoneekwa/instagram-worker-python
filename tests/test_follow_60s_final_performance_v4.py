from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import follow_60s_canary as canary
import instagram_navigation as nav
import runner
from tests.follow60_generic_fixtures import (
    TEST_CANARY_ACCOUNT_ID,
    TEST_CANARY_USERNAME,
    configure_canary,
)


class CoordinateFrameV1ContractTest(unittest.TestCase):
    def test_sixty_exact_and_normalized_frames_are_deterministic(self) -> None:
        scenarios = []
        for index in range(30):
            width = 1080 + index
            height = 2340 + index
            scenarios.append(("raw_window_exact", width, height, width, height, 0))
            scenarios.append(
                ("hierarchy_coordinate_frame", width, height + 60, width, height, 60)
            )
        self.assertEqual(len(scenarios), 60)
        for source, raw_w, raw_h, canonical_w, canonical_h, bottom in scenarios:
            with self.subTest(source=source, raw_w=raw_w, raw_h=raw_h):
                first = canary.build_coordinate_frame_v1(
                    source=source,
                    raw_width=raw_w,
                    raw_height=raw_h,
                    canonical_width=canonical_w,
                    canonical_height=canonical_h,
                    inset_bottom=bottom,
                )
                second = canary.build_coordinate_frame_v1(
                    source=source,
                    raw_width=raw_w,
                    raw_height=raw_h,
                    canonical_width=canonical_w,
                    canonical_height=canonical_h,
                    inset_bottom=bottom,
                )
                self.assertIsNotNone(first)
                self.assertEqual(first.transform_hash, second.transform_hash)
                ok, reason = canary.validate_coordinate_frame_v1(
                    first, consumer_size=(raw_w, raw_h)
                )
                self.assertTrue(ok)
                self.assertEqual(
                    reason,
                    "coordinate_frame_exact_match"
                    if source == "raw_window_exact"
                    else "coordinate_frame_normalized_match",
                )

    def test_frame_rejects_version_hash_inset_and_viewport_drift(self) -> None:
        frame = canary.build_coordinate_frame_v1(
            source="hierarchy_coordinate_frame",
            raw_width=1080,
            raw_height=2400,
            canonical_width=1080,
            canonical_height=2340,
            inset_bottom=60,
        )
        payload = dict(frame.__dict__)
        for key, value, expected in (
            ("transform_version", "coordinate_frame_v0", "coordinate_frame_transform_version_mismatch"),
            ("transform_hash", "tampered", "coordinate_frame_untrusted"),
        ):
            changed = dict(payload)
            changed[key] = value
            self.assertEqual(
                canary.validate_coordinate_frame_v1(
                    changed, consumer_size=(1080, 2400)
                ),
                (False, expected),
            )
        self.assertEqual(
            canary.validate_coordinate_frame_v1(frame, consumer_size=(1080, 2340)),
            (False, "coordinate_frame_inset_mismatch"),
        )
        self.assertEqual(
            canary.validate_coordinate_frame_v1(frame, consumer_size=(1080, 2500)),
            (False, "coordinate_frame_viewport_mismatch"),
        )
        self.assertEqual(
            canary.validate_coordinate_frame_v1(
                frame,
                consumer_size=(1080, 2400),
                consumer_orientation="landscape",
            ),
            (False, "coordinate_frame_viewport_mismatch"),
        )
        dense_frame = canary.build_coordinate_frame_v1(
            source="raw_window_exact",
            raw_width=1080,
            raw_height=2400,
            canonical_width=1080,
            canonical_height=2400,
            density=2.75,
        )
        self.assertEqual(
            canary.validate_coordinate_frame_v1(
                dense_frame,
                consumer_size=(1080, 2400),
                consumer_density=3.0,
            ),
            (False, "coordinate_frame_viewport_mismatch"),
        )

    def test_status_inset_screenshot_size_and_generation_are_exact(self) -> None:
        frame = canary.build_coordinate_frame_v1(
            source="hierarchy_coordinate_frame",
            raw_width=1080,
            raw_height=2400,
            canonical_width=1080,
            canonical_height=2316,
            inset_top=24,
            inset_bottom=60,
            navigation_generation="nav-7",
            scroll_generation=3,
        )
        self.assertIsNotNone(frame)
        self.assertEqual(frame.system_insets["top"], 24)
        self.assertEqual(frame.system_insets["bottom"], 60)
        self.assertEqual(
            canary.validate_coordinate_frame_v1(
                frame,
                consumer_size=(1080, 2400),
                navigation_generation="nav-7",
                scroll_generation=3,
            ),
            (True, "coordinate_frame_normalized_match"),
        )
        self.assertEqual(
            canary.validate_coordinate_frame_v1(
                frame,
                consumer_size=(1080, 2340),
                navigation_generation="nav-7",
                scroll_generation=3,
            ),
            (False, "coordinate_frame_viewport_mismatch"),
        )
        for navigation, scroll in (("nav-8", 3), ("nav-7", 4)):
            with self.subTest(navigation=navigation, scroll=scroll):
                self.assertEqual(
                    canary.validate_coordinate_frame_v1(
                        frame,
                        consumer_size=(1080, 2400),
                        navigation_generation=navigation,
                        scroll_generation=scroll,
                    ),
                    (False, "coordinate_frame_stale"),
                )


class FinalMuteBoundaryContractTest(unittest.TestCase):
    def tearDown(self) -> None:
        nav._clear_post_mute_sheet_closed_proof_stash()
        configure_canary(canary,
            account_id="other",
            account_username="other",
            run_id="reset",
            package="com.instagram.android",
            resume_policy=None,
        )

    def test_non_authoritative_fallback_cannot_replace_typed_final_proof(self) -> None:
        original = {
            "stashed_at_monotonic": 1.0,
            "source_profile_username": "ct",
            "candidate_username": "candidate",
            "visual_candidate_id": "vc-1",
            "action_bar_title": "candidate",
            "candidate_context": {
                "post_grid_metadata": {
                    "coordinate_frame": {
                        "transform_version": "coordinate_frame_v1",
                        "transform_hash": "trusted-frame",
                    }
                }
            },
        }
        nav._post_mute_sheet_closed_proof_stash = original
        with patch.object(nav, "log"):
            nav._stash_post_mute_sheet_closed_proof(
                source_profile_username="ct",
                candidate_username="candidate",
                visual_candidate_id="vc-1",
                action_bar_title="candidate",
                duration_ms=5.0,
                candidate_context={"post_grid_metadata": {}},
                authoritative_final_close=False,
            )
        self.assertEqual(nav._post_mute_sheet_closed_proof_stash, original)

    def test_stable_close_capture_uses_one_xml_and_exact_identity(self) -> None:
        device = MagicMock()
        device.dump_hierarchy.return_value = (
            '<hierarchy><node resource-id="com.instagram.android:id/action_bar_title" '
            'text="candidate"/><node resource-id="com.instagram.android:id/profile_tabs_container"/>'
            '<node content-desc="Profile tab grid" selected="true"/></hierarchy>'
        )
        ok, result = nav._mute_engine_v2_stable_sheet_closed_profile_capture(
            device, candidate_username="candidate"
        )
        self.assertTrue(ok)
        self.assertEqual(result["reason"], "stable_close_exact_profile_single_xml")
        device.dump_hierarchy.assert_called_once_with(compressed=False)

    def test_bounded_empty_grid_recheck_promotes_exact_no_posts(self) -> None:
        configure_canary(canary,
            account_id=TEST_CANARY_ACCOUNT_ID,
            account_username=TEST_CANARY_USERNAME,
            run_id="empty-grid-v4",
            package="com.instagram.android",
            resume_policy=None,
        )
        initial = (
            '<hierarchy><node resource-id="com.instagram.android:id/action_bar_title" '
            'text="candidate" bounds="[0,0][1080,120]"/>'
            '<node resource-id="com.instagram.android:id/profile_tabs_container" '
            'bounds="[0,700][1080,820]"/>'
            '<node content-desc="Profile tab grid" selected="true" '
            'bounds="[0,700][360,820]"/><node bounds="[0,0][1080,2340]"/></hierarchy>'
        )
        final = initial.replace(
            '</hierarchy>', '<node text="No Posts Yet"/></hierarchy>'
        )
        device = MagicMock()
        device.window_size.return_value = (1080, 2340)
        device.dump_hierarchy.return_value = final
        with patch.object(
            nav,
            "_followers_current_pkg_activity",
            return_value={
                "current_package": "com.instagram.android",
                "current_activity": "com.instagram.mainactivity.InstagramMainActivity",
            },
        ), patch.object(nav.time, "sleep"):
            result = nav._publish_post_mute_verdict_at_final_sheet_close(
                device,
                source_profile_username="ct",
                candidate_username="candidate",
                visual_candidate_id="vc-1",
                candidate_context={},
                mute_posts_verified=True,
                mute_stories_verified=True,
                started_at=0.0,
                profile_xml=initial,
            )
        self.assertEqual(result["post_grid_outcome"], "NO_POSTS_POSITIVE")
        self.assertTrue(result["post_grid_no_posts_positive"])
        self.assertEqual(result["post_grid_dump_count"], 2)
        device.dump_hierarchy.assert_called_once_with(compressed=False)

    def test_no_posts_requires_selected_grid_and_rejects_reels(self) -> None:
        base = (
            '<hierarchy><node text="candidate"/><node text="No Posts Yet"/>'
            '<node resource-id="com.instagram.android:id/profile_tabs_container" '
            'bounds="[0,700][1080,820]"/>{tab}</hierarchy>'
        )
        missing = nav._post_follow_post_grid_evidence_from_xml(
            base.format(tab='<node content-desc="Profile tab grid"/>'),
            candidate_username="candidate",
            ww=1080,
            wh=2340,
        )
        reels = nav._post_follow_post_grid_evidence_from_xml(
            base.format(tab='<node content-desc="Profile tab reels" selected="true"/>'),
            candidate_username="candidate",
            ww=1080,
            wh=2340,
        )
        self.assertEqual(missing["outcome"], "POST_GRID_AMBIGUOUS_FINAL")
        self.assertEqual(reels["outcome"], "POST_GRID_AMBIGUOUS_FINAL")

    def test_clipped_grid_with_untrusted_frame_never_reveals(self) -> None:
        configure_canary(canary,
            account_id=TEST_CANARY_ACCOUNT_ID,
            account_username=TEST_CANARY_USERNAME,
            run_id="clipped-untrusted-v4",
            package="com.instagram.android",
            resume_policy=None,
        )
        device = MagicMock()
        device.window_size.return_value = (1080, 2340)
        clipped = {
            "outcome": "POST_ROW_POSITIVE_BUT_CLIPPED",
            "identity_exact": True,
            "profile_tabs_present": True,
            "grid_selected": True,
            "physical_cells": [
                {"left": 0, "top": 2200, "right": 360, "bottom": 2340}
            ],
            "post_count_positive": True,
            "coordinate_frame": None,
        }
        with (
            patch.object(
                nav,
                "_followers_current_pkg_activity",
                return_value={
                    "current_package": "com.instagram.android",
                    "current_activity": (
                        "com.instagram.mainactivity.InstagramMainActivity"
                    ),
                },
            ),
            patch.object(
                nav,
                "_post_follow_post_grid_evidence_from_xml",
                return_value=clipped,
            ),
            patch.object(
                nav,
                "_post_follow_screen_dimensions_from_hierarchy",
                return_value={
                    "screen_width": 1080,
                    "screen_height": 2340,
                    "screen_dimensions_source": "screen_dimensions_untrusted",
                    "screen_dimensions_trusted": False,
                },
            ),
            patch.object(
                nav,
                "_post_follow_promote_ambiguous_grid_evidence_with_fresh_vision",
            ) as reveal,
        ):
            context = nav._publish_post_mute_verdict_at_final_sheet_close(
                device,
                source_profile_username="ct",
                candidate_username="candidate",
                visual_candidate_id="vc-1",
                candidate_context={"navigation_generation": "0"},
                mute_posts_verified=True,
                mute_stories_verified=True,
                started_at=0.0,
                profile_xml=(
                    '<hierarchy><node '
                    'resource-id="com.instagram.android:id/action_bar_title" '
                    'text="candidate"/><node bounds="[0,0][1080,2340]"/>'
                    '</hierarchy>'
                ),
            )
        reveal.assert_not_called()
        self.assertEqual(context["post_grid_outcome"], "POST_GRID_AMBIGUOUS_FINAL")
        self.assertEqual(
            context["post_grid_metadata"]["fast_vision_probe_rejection_reason"],
            "coordinate_frame_untrusted_before_reveal",
        )


class SnapshotViewportExhaustionTest(unittest.TestCase):
    def test_snapshot_exhaustion_requires_every_row_runtime_seen(self) -> None:
        self.assertTrue(
            runner._followers_snapshot_viewport_exhausted(
                [
                    {"username": "a", "already_seen_runtime": True},
                    {"username": "b", "already_seen_runtime": True},
                ]
            )
        )
        self.assertFalse(
            runner._followers_snapshot_viewport_exhausted(
                [
                    {"username": "a", "already_seen_runtime": True},
                    {"username": "b", "already_seen_runtime": False},
                ]
            )
        )
        self.assertFalse(runner._followers_snapshot_viewport_exhausted([]))


if __name__ == "__main__":
    unittest.main()
