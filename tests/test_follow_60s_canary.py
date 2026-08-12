from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import follow_60s_canary as canary
import instagram_navigation as nav
from tests.follow60_generic_fixtures import (
    TEST_CANARY_ACCOUNT_ID,
    TEST_CANARY_USERNAME,
    configure_canary,
)


class Follow60sCanaryRuntimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.log_patch = patch.object(canary, "log")
        self.log_patch.start()

    def tearDown(self) -> None:
        self.log_patch.stop()
        configure_canary(canary,
            account_id="other",
            account_username="other",
            run_id="reset",
            package="com.instagram.android",
            resume_policy=None,
        )

    def _configure_canary(self, resume_policy=None) -> bool:
        return configure_canary(canary,
            account_id=TEST_CANARY_ACCOUNT_ID,
            account_username=TEST_CANARY_USERNAME,
            run_id="run-1",
            package="com.instagram.android",
            resume_policy=resume_policy,
        )

    def test_only_bound_account_first_natural_attempt_is_enabled(self) -> None:
        self.assertTrue(self._configure_canary())
        self.assertTrue(canary.enabled("mute_like_handoff"))

        self.assertFalse(self._configure_canary({"attempt_id": 2}))
        self.assertFalse(canary.enabled())

        self.assertFalse(
            configure_canary(canary,
                account_id="another-account",
                account_username="another",
                run_id="run-2",
                package="com.instagram.android",
                resume_policy=None,
            )
        )

        self.assertFalse(
            configure_canary(canary,
                account_id="unbound-account",
                account_username="unbound_account",
                run_id="run-unbound-mainline",
                package="com.instagram.androig",
                resume_policy=None,
            )
        )

    def test_matching_fresh_safe_bounds_proof_reuses(self) -> None:
        self._configure_canary()
        canary.stash(
            "cell",
            subject_username="ct",
            target_username="candidate",
            package="com.instagram.android",
            activity="ProfileActivity",
            surface="candidate_profile_post_grid",
            detection_source="fresh_xml",
            ttl_ms=1250.0,
            bounds={"left": 10, "top": 200, "right": 310, "bottom": 500},
            xml="<hierarchy generation='1'/>",
        )
        proof, age_ms, reason = canary.consume(
            "cell",
            subject_username="ct",
            target_username="candidate",
            package="com.instagram.android",
            activity="ProfileActivity",
            surface="candidate_profile_post_grid",
            require_safe_bounds=True,
            screen_size=(1080, 2340),
        )
        self.assertIsNotNone(proof)
        self.assertGreaterEqual(age_ms, 0.0)
        self.assertEqual(reason, "")

    def test_mismatch_or_navigation_invalidates_and_falls_back(self) -> None:
        self._configure_canary()
        canary.stash(
            "profile",
            subject_username="ct",
            target_username="candidate",
            package="com.instagram.android",
            activity="ProfileActivity",
            surface="candidate_profile_after_like",
            detection_source="exact_action_bar",
            ttl_ms=1800.0,
        )
        proof, _, reason = canary.consume(
            "profile",
            subject_username="ct",
            target_username="wrong-candidate",
        )
        self.assertIsNone(proof)
        self.assertEqual(reason, "target_username_mismatch")

        canary.invalidate("unexpected_back")
        proof, _, reason = canary.consume("profile")
        self.assertIsNone(proof)
        self.assertEqual(reason, "invalidated:unexpected_back")

    def test_bounds_contract_rejects_right_side_or_bottom_risk(self) -> None:
        ok, reason = canary.safe_bounds(
            {"left": 800, "top": 100, "right": 1000, "bottom": 300},
            screen_size=(1080, 2340),
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "bounds_hit_region_unsafe")

    def test_metadata_mismatch_rejects_fresh_proof(self) -> None:
        self._configure_canary()
        canary.stash(
            "opening_follow_composite",
            subject_username="ct",
            target_username="candidate",
            package="com.instagram.android",
            activity="",
            surface="candidate_profile_follow_ready",
            detection_source="composite",
            ttl_ms=3500.0,
            metadata={"navigation_token": "generation-1"},
        )
        proof, _, reason = canary.consume(
            "opening_follow_composite",
            subject_username="ct",
            target_username="candidate",
            package="com.instagram.android",
            surface="candidate_profile_follow_ready",
            metadata_equals={"navigation_token": "generation-2"},
        )
        self.assertIsNone(proof)
        self.assertEqual(reason, "metadata_navigation_token_mismatch")

    def test_optimization_outcomes_are_aggregated_separately(self) -> None:
        self._configure_canary()
        canary.record_outcome(
            "mute_known_depth",
            "used",
            estimated_gain_ms=1400.0,
        )
        canary.record_outcome(
            "mute_known_depth",
            "fallback",
            reason="proof_missing",
            fallback_used=True,
            dumps=1,
        )
        stats = canary.stats()["optimization_counts"]["mute_known_depth"]
        self.assertEqual(stats["used"], 1)
        self.assertEqual(stats["fallback"], 1)
        self.assertEqual(stats["dumps"], 1)
        self.assertEqual(stats["estimated_gain_ms"], 1400.0)

    def test_ten_synthetic_cycles_reuse_each_stage_scoped_proof(self) -> None:
        self._configure_canary()
        for index in range(10):
            candidate = f"candidate_{index}"
            generation = str(canary.runtime_context()["ui_generation"])
            canary.stash(
                "opening_follow_composite",
                subject_username="ct",
                target_username=candidate,
                package="com.instagram.android",
                activity="ProfileActivity",
                surface="candidate_profile_follow_ready",
                detection_source="mono_capture",
                ttl_ms=3500.0,
                metadata={"cycle": str(index + 1)},
            )
            opening, opening_age, opening_reason = canary.consume(
                "opening_follow_composite",
                subject_username="ct",
                target_username=candidate,
                package="com.instagram.android",
                activity="ProfileActivity",
                surface="candidate_profile_follow_ready",
                consume_once=True,
                metadata_equals={"cycle": str(index + 1)},
            )
            self.assertIsNotNone(opening)
            self.assertGreaterEqual(opening_age, 0.0)
            self.assertEqual(opening_reason, "")

            canary.create_candidate_profile_verdict(
                candidate_username=candidate,
                package="com.instagram.android",
                activity="ProfileActivity",
                navigation_generation=generation,
                exact_identity=True,
                sheet_closed=True,
                mute_posts_verified=True,
                mute_stories_verified=True,
                viewport_fingerprint=f"viewport-{index}",
            post_grid_outcome="POST_ROW_POSITIVE_SAFE",
                post_bounds={"left": 10, "top": 900, "right": 330, "bottom": 1220},
            )
            verdict, verdict_age, verdict_reason = canary.get_candidate_profile_verdict(
                candidate_username=candidate,
                package="com.instagram.android",
                activity="ProfileActivity",
                navigation_generation=generation,
            )
            self.assertIsNotNone(verdict)
            self.assertGreaterEqual(verdict_age, 0.0)
            self.assertEqual(verdict_reason, "")

            canary.stash_post_grid_evidence(
                candidate_username=candidate,
                package_name="com.instagram.android",
                activity_name="com.instagram.mainactivity.InstagramMainActivity",
                navigation_generation=generation,
                viewport_fingerprint=f"viewport-{index}",
                outcome="POST_ROW_POSITIVE_SAFE",
                mute_sheet_closed=True,
                mute_posts_verified=True,
                mute_stories_verified=True,
                profile_identity_method="test_exact_profile",
                screen_width=1080,
                screen_height=2340,
                grid_tab_state="selected_or_physical_row",
                post_count_positive=True,
                physical_post_cells=[{"left": 10, "top": 900, "right": 330, "bottom": 1220}],
                first_post_bounds={"left": 10, "top": 900, "right": 330, "bottom": 1220},
                first_post_cell_source="test_fresh_xml",
                ttl_ms=3000.0,
            )
            grid, grid_age, grid_reason = canary.consume_post_grid_evidence(
                candidate_username=candidate,
                package="com.instagram.android",
                activity="com.instagram.mainactivity.InstagramMainActivity",
                navigation_generation=generation,
                viewport_fingerprint=f"viewport-{index}",
                screen_size=(1080, 2340),
            )
            self.assertIsNotNone(grid)
            self.assertGreaterEqual(grid_age, 0.0)
            self.assertEqual(grid_reason, "")

            canary.stash_next_candidate_snapshot(
                source_profile_username="ct",
                package="com.instagram.android",
                activity="FollowersActivity",
                navigation_generation=generation,
                viewport_fingerprint=f"ct-viewport-{index}",
                detection={
                    "is_followers_list": True,
                    "dedup_fingerprint": f"rows-{index}",
                },
            )
            snapshot, snapshot_age, snapshot_reason = (
                canary.consume_next_candidate_snapshot(
                    source_profile_username="ct",
                    package="com.instagram.android",
                    activity="FollowersActivity",
                    navigation_generation=generation,
                    viewport_fingerprint=f"ct-viewport-{index}",
                )
            )
            self.assertIsNotNone(snapshot)
            self.assertGreaterEqual(snapshot_age, 0.0)
            self.assertEqual(snapshot_reason, "")

        counts = canary.stats()["proof_counts"]
        self.assertEqual(counts["opening_follow_composite"]["reused"], 10)
        self.assertEqual(counts["candidate_profile_verdict"]["reused"], 10)
        self.assertEqual(counts["post_grid_evidence"]["reused"], 10)
        self.assertEqual(counts["next_candidate_snapshot"]["reused"], 10)

    def test_opening_composite_proof_contains_public_identity_and_cta(self) -> None:
        self._configure_canary()
        proof_dict = nav.build_pre_follow_observation_proof(
            follower_username="candidate",
            source_profile_username="ct_source",
            visual_candidate_id="vc-1",
            action_bar_title="candidate",
            navigation_state="CANDIDATE_PROFILE",
            navigation_confidence=0.95,
            follow_header_state="follow",
            private_probe_payload={
                "private_profile_detected": False,
                "detection_method": "fresh_xml_no_private_markers",
                "probe_ms": 120.0,
                "package": "com.instagram.android",
                "activity": "com.instagram.profile.ProfileActivity",
                "package_exact": True,
                "follow_cta_positive": True,
                "navigation_generation": "generation-1",
                "xml_fingerprint": "xml-proof-1",
            },
            navigation_token="generation-1",
        )
        self.assertEqual(proof_dict["follow_header_state"], "follow")
        proof, _, reason = canary.consume(
            "opening_follow_composite",
            subject_username="ct_source",
            target_username="candidate",
            package="com.instagram.android",
            surface="candidate_profile_follow_ready",
            metadata_equals={
                "visual_candidate_id": "vc-1",
                "navigation_token": "generation-1",
            },
        )
        self.assertIsNotNone(proof)
        self.assertEqual(reason, "")


class Follow60sReturnHandoffTest(unittest.TestCase):
    def test_exact_candidate_proof_sends_one_back_then_keeps_exact_ct_gate(self) -> None:
        nav._post_follow_return_take_pending_visual_evidence_for_runner()
        device = MagicMock()
        det = {
            "is_followers_list": True,
            "action_bar_title": "ct_source",
            "open_detection_method": "own_unified_follow_list",
            "candidate_username_count": 4,
            "signals": ["selected_followers_tab", "follow_list_username"],
        }
        with patch.object(
            nav, "verify_app_foreground", return_value=True
        ), patch.object(
            nav, "detect_followers_list_screen", return_value=det
        ) as detect, patch.object(
            nav, "verify_followers_list_surface_is_ct_account", return_value=True
        ) as exact_ct, patch.object(
            nav.time, "sleep", return_value=None
        ) as sleep, patch.object(
            nav, "log"
        ):
            ok, how, failure = nav.post_follow_controlled_return_to_followers_list(
                device,
                pkg="com.instagram.android",
                source_profile_username="ct_source",
                follower_username="candidate",
                visual_candidate_id="vc-1",
                det={},
                max_rounds=1,
                compact_after_follow_verified_mute=True,
                compact_reason="follow_verified_mute_success",
                immediate_candidate_back_proof=True,
            )

        self.assertTrue(ok)
        self.assertEqual(how, "fresh_candidate_proof_one_back_then_exact_ct")
        self.assertIsNone(failure)
        device.press.assert_called_once_with("back")
        detect.assert_called_once()
        exact_ct.assert_called_once()
        sleep.assert_not_called()
        pending = nav._post_follow_return_take_pending_visual_evidence_for_runner()
        self.assertEqual(
            pending["return_list_detection"]["action_bar_title"], "ct_source"
        )
        self.assertGreater(
            pending["return_list_detection_created_at_monotonic"], 0.0
        )

    def test_exact_candidate_proof_polls_only_when_ct_is_still_transitioning(self) -> None:
        nav._post_follow_return_take_pending_visual_evidence_for_runner()
        device = MagicMock()
        transitioning = {
            "is_followers_list": False,
            "action_bar_title": "candidate",
        }
        exact_ct = {
            "is_followers_list": True,
            "action_bar_title": "ct_source",
            "open_detection_method": "own_unified_follow_list",
            "candidate_username_count": 4,
            "signals": ["selected_followers_tab", "follow_list_username"],
        }
        with patch.object(
            nav, "verify_app_foreground", return_value=True
        ), patch.object(
            nav,
            "detect_followers_list_screen",
            side_effect=[transitioning, exact_ct],
        ) as detect, patch.object(
            nav,
            "verify_followers_list_surface_is_ct_account",
            side_effect=[False, True],
        ), patch.object(
            nav.time, "sleep", return_value=None
        ) as sleep, patch.object(nav, "log"):
            ok, how, failure = nav.post_follow_controlled_return_to_followers_list(
                device,
                pkg="com.instagram.android",
                source_profile_username="ct_source",
                follower_username="candidate",
                visual_candidate_id="vc-1",
                det={},
                max_rounds=1,
                compact_after_follow_verified_mute=True,
                compact_reason="follow_verified_mute_success",
                immediate_candidate_back_proof=True,
            )

        self.assertTrue(ok)
        self.assertEqual(how, "fresh_candidate_proof_one_back_then_exact_ct")
        self.assertIsNone(failure)
        self.assertEqual(detect.call_count, 2)
        sleep.assert_called_once_with(0.06)


class Follow60sMuteKnownDepthTest(unittest.TestCase):
    def tearDown(self) -> None:
        configure_canary(canary,
            account_id="other",
            account_username="other",
            run_id="reset",
            package="com.instagram.android",
            resume_policy=None,
        )

    def test_following_options_depth_is_reused_before_final_profile_proof(self) -> None:
        configure_canary(canary,
            account_id=TEST_CANARY_ACCOUNT_ID,
            account_username=TEST_CANARY_USERNAME,
            run_id="run-1",
            package="com.instagram.android",
            resume_policy=None,
        )
        device = MagicMock()
        following_options = {
            "reason": "following_options_still_visible",
            "following_options_marker_visible": True,
            "toggles_visible": False,
        }
        profile = {
            "reason": "sheet_absent_profile_visible",
            "profile_marker_visible": True,
        }
        with patch.object(
            nav,
            "_mute_engine_v2_fast_sheet_closed_profile_proof",
            side_effect=[(False, following_options), (True, profile)],
        ), patch.object(nav.time, "sleep", return_value=None), patch.object(nav, "log"):
            ok, _ = nav._mute_engine_v2_dismiss_mute_sheets_level_aware(
                device,
                visual_candidate_id="vc-1",
                source_profile_username="ct_source",
                confirmed_sheet_level="mute_toggles",
            )
        self.assertTrue(ok)
        self.assertEqual(device.press.call_count, 2)
        stats = canary.stats()["optimization_counts"]["mute_known_depth"]
        self.assertEqual(stats["used"], 1)


class Follow60sImmutableEvidenceContractsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.log_patch = patch.object(canary, "log")
        self.log_patch.start()
        configure_canary(canary,
            account_id=TEST_CANARY_ACCOUNT_ID,
            account_username=TEST_CANARY_USERNAME,
            run_id="run-j-automatise",
            package="com.instagram.android",
            resume_policy=None,
        )

    def tearDown(self) -> None:
        self.log_patch.stop()
        configure_canary(canary,
            account_id="other", account_username="other", run_id="reset",
            package="com.instagram.android", resume_policy=None,
        )

    def test_candidate_verdict_is_reusable_without_rereading_ui_proof(self) -> None:
        verdict = canary.create_candidate_profile_verdict(
            candidate_username="candidate", package="com.instagram.android",
            activity="ProfileActivity", navigation_generation="g1",
            exact_identity=True, sheet_closed=True,
            mute_posts_verified=True, mute_stories_verified=True,
        )
        self.assertIsNotNone(verdict)
        for _ in range(3):
            reused, _, reason = canary.get_candidate_profile_verdict(
                candidate_username="candidate", package="com.instagram.android",
                activity="ProfileActivity", navigation_generation="g1",
            )
            self.assertIsNotNone(reused)
            self.assertEqual(reason, "")

    def test_candidate_verdict_rejects_candidate_or_generation_mismatch(self) -> None:
        canary.create_candidate_profile_verdict(
            candidate_username="candidate", package="pkg", activity="act",
            navigation_generation="g1", exact_identity=True, sheet_closed=True,
            mute_posts_verified=True, mute_stories_verified=True,
        )
        self.assertEqual(
            canary.get_candidate_profile_verdict(candidate_username="other")[2],
            "missing_verdict",
        )
        self.assertEqual(
            canary.get_candidate_profile_verdict(
                candidate_username="candidate", navigation_generation="g2"
            )[2],
            "navigation_generation_mismatch",
        )

    def test_post_grid_evidence_is_single_consume_and_bounds_safe(self) -> None:
        canary.stash_post_grid_evidence(
            candidate_username="candidate", package_name="com.instagram.android",
            activity_name="com.instagram.mainactivity.InstagramMainActivity",
            navigation_generation="g1", viewport_fingerprint="v1",
            outcome="POST_ROW_POSITIVE_SAFE",
            mute_sheet_closed=True, mute_posts_verified=True,
            mute_stories_verified=True, profile_identity_method="test_exact_profile",
            screen_width=1080, screen_height=2340,
            grid_tab_state="selected_or_physical_row", post_count_positive=True,
            physical_post_cells=[{"left": 10, "top": 300, "right": 300, "bottom": 650}],
            first_post_bounds={"left": 10, "top": 300, "right": 300, "bottom": 650},
            first_post_cell_source="test_fresh_xml",
        )
        ev, _, reason = canary.consume_post_grid_evidence(
            candidate_username="candidate", package="com.instagram.android",
            activity="com.instagram.mainactivity.InstagramMainActivity",
            navigation_generation="g1", viewport_fingerprint="v1",
            screen_size=(1080, 2340),
        )
        self.assertIsNotNone(ev)
        self.assertEqual(reason, "")
        self.assertEqual(
            canary.consume_post_grid_evidence(candidate_username="candidate")[2],
            "missing_evidence",
        )

    def _stash_dimension_evidence(
        self,
        *,
        candidate: str,
        raw_height: int,
        canonical_height: int,
        source: str,
        inset_bottom: int = 0,
        fingerprint: str = "viewport-v2",
    ) -> None:
        canary.stash_post_grid_evidence(
            candidate_username=candidate,
            package_name="com.instagram.android",
            activity_name="com.instagram.mainactivity.InstagramMainActivity",
            navigation_generation="g1",
            viewport_fingerprint=fingerprint,
            outcome="POST_ROW_POSITIVE_SAFE",
            mute_sheet_closed=True,
            mute_posts_verified=True,
            mute_stories_verified=True,
            profile_identity_method="test_exact_profile",
            screen_width=1080,
            screen_height=canonical_height,
            producer_screen_width=1080,
            producer_screen_height=raw_height,
            screen_dimensions_source=source,
            screen_inset_bottom=inset_bottom,
            grid_tab_state="selected_or_physical_row",
            post_count_positive=True,
            physical_post_cells=[
                {"left": 0, "top": 900, "right": 360, "bottom": 1260}
            ],
            first_post_bounds={
                "left": 0, "top": 900, "right": 360, "bottom": 1260
            },
        )

    def test_post_grid_dimensions_accept_exact_and_explicit_normalized_frames(self) -> None:
        self._stash_dimension_evidence(
            candidate="exact",
            raw_height=2340,
            canonical_height=2340,
            source="raw_window_exact",
        )
        exact, _, exact_reason = canary.consume_post_grid_evidence(
            candidate_username="exact",
            package="com.instagram.android",
            activity="com.instagram.mainactivity.InstagramMainActivity",
            navigation_generation="g1",
            viewport_fingerprint="viewport-v2",
            screen_size=(1080, 2340),
        )
        self.assertIsNotNone(exact)
        self.assertEqual(exact_reason, "")

        self._stash_dimension_evidence(
            candidate="normalized",
            raw_height=2400,
            canonical_height=2340,
            source="hierarchy_coordinate_frame",
            inset_bottom=60,
        )
        normalized, _, normalized_reason = canary.consume_post_grid_evidence(
            candidate_username="normalized",
            package="com.instagram.android",
            activity="com.instagram.mainactivity.InstagramMainActivity",
            navigation_generation="g1",
            viewport_fingerprint="viewport-v2",
            screen_size=(1080, 2400),
        )
        self.assertIsNotNone(normalized)
        self.assertEqual(normalized_reason, "")

    def test_post_grid_dimensions_reject_inset_viewport_and_untrusted_frames(self) -> None:
        cases = (
            ("inset", 2400, 2340, "hierarchy_coordinate_frame", 60, (1080, 2280), "coordinate_frame_viewport_mismatch"),
            ("viewport", 2400, 2340, "hierarchy_coordinate_frame", 60, (1080, 2500), "coordinate_frame_viewport_mismatch"),
            ("untrusted", 2340, 2340, "screen_dimensions_untrusted", 0, (1080, 2340), "coordinate_frame_untrusted"),
        )
        for candidate, raw_h, canonical_h, source, inset, consumer, expected in cases:
            with self.subTest(case=candidate):
                self._stash_dimension_evidence(
                    candidate=candidate,
                    raw_height=raw_h,
                    canonical_height=canonical_h,
                    source=source,
                    inset_bottom=inset,
                )
                evidence, _, reason = canary.consume_post_grid_evidence(
                    candidate_username=candidate,
                    package="com.instagram.android",
                    activity="com.instagram.mainactivity.InstagramMainActivity",
                    navigation_generation="g1",
                    viewport_fingerprint="viewport-v2",
                    screen_size=consumer,
                )
                self.assertIsNone(evidence)
                self.assertEqual(reason, expected)

    def test_grid_classification_ignores_candidate_context_fingerprint_domain(self) -> None:
        self._stash_dimension_evidence(
            candidate="fingerprint",
            raw_height=2340,
            canonical_height=2340,
            source="raw_window_exact",
        )
        evidence, _, reason = canary.consume_post_grid_evidence(
            candidate_username="fingerprint",
            package="com.instagram.android",
            activity="com.instagram.mainactivity.InstagramMainActivity",
            navigation_generation="g1",
            viewport_fingerprint="different-viewport",
            screen_size=(1080, 2340),
        )
        self.assertIsInstance(evidence, canary.GridClassificationProof)
        self.assertEqual(reason, "")
        self.assertEqual(evidence.viewport_fingerprint, "viewport-v2")

    def test_terminal_outcome_is_unique_per_candidate_and_feature(self) -> None:
        self.assertTrue(
            canary.record_terminal_outcome(
                "mute_like_handoff",
                candidate_username="candidate",
                status="used",
            )
        )
        self.assertFalse(
            canary.record_terminal_outcome(
                "mute_like_handoff",
                candidate_username="candidate",
                status="fallback",
                reason="late_probe",
                fallback_used=True,
            )
        )
        stats = canary.stats()["optimization_counts"]["mute_like_handoff"]
        self.assertEqual(stats["used"], 1)
        self.assertEqual(stats["fallback"], 0)

    def test_ambiguous_post_grid_evidence_is_returned_for_one_direct_golden_decision(self) -> None:
        canary.stash_post_grid_evidence(
            candidate_username="candidate", package_name="com.instagram.android",
            activity_name="com.instagram.mainactivity.InstagramMainActivity",
            navigation_generation="g1", viewport_fingerprint="v1",
            outcome="POST_GRID_AMBIGUOUS_FINAL",
            mute_sheet_closed=True, mute_posts_verified=True,
            mute_stories_verified=True, profile_identity_method="test_exact_profile",
            screen_width=1080, screen_height=2340,
            grid_tab_state="ambiguous",
        )
        ev, _, reason = canary.consume_post_grid_evidence(
            candidate_username="candidate", package="com.instagram.android",
            activity="com.instagram.mainactivity.InstagramMainActivity",
            navigation_generation="g1", viewport_fingerprint="v1",
            screen_size=(1080, 2340),
        )
        self.assertIsNotNone(ev)
        self.assertEqual(ev.outcome, "POST_GRID_AMBIGUOUS_FINAL")
        self.assertEqual(reason, "")

    def test_clipped_positive_row_survives_contract_for_single_reveal(self) -> None:
        canary.stash_post_grid_evidence(
            candidate_username="candidate", package_name="com.instagram.android",
            activity_name="com.instagram.mainactivity.InstagramMainActivity",
            navigation_generation="g1", viewport_fingerprint="v1",
            outcome="POST_ROW_POSITIVE_BUT_CLIPPED",
            mute_sheet_closed=True, mute_posts_verified=True,
            mute_stories_verified=True, profile_identity_method="test_exact_profile",
            screen_width=1080, screen_height=2340,
            grid_tab_state="selected_or_physical_row", post_count_positive=True,
            physical_post_cells=[{"left": 10, "top": 2100, "right": 350, "bottom": 2340}],
            first_post_bounds={"left": 10, "top": 2100, "right": 350, "bottom": 2340},
            first_post_cell_source="test_clipped_xml",
        )
        ev, _, reason = canary.consume_post_grid_evidence(
            candidate_username="candidate", package="com.instagram.android",
            activity="com.instagram.mainactivity.InstagramMainActivity",
            navigation_generation="g1", viewport_fingerprint="v1",
            screen_size=(1080, 2340),
        )
        self.assertIsNotNone(ev)
        self.assertEqual(ev.outcome, "POST_ROW_POSITIVE_BUT_CLIPPED")
        self.assertEqual(reason, "")

    def test_canary_one_shot_resume_requires_exact_source_phase_and_quota(self) -> None:
        source_run_id = "rotated-source-run"
        policy = {
            "prior_run_id": source_run_id,
            "restart_allowed": True,
            "resume_plan_id": "resume-plan-v2",
            "incident_id": "reviewed-incident-v2",
            "restriction_preflight_only": False,
            "request_metadata": {
                "source": "auto_restart_tick",
                "recovery_mode": "human_confirmed_resume",
            },
            "phases_to_run": {"follow": True, "welcome": False, "unfollow": False},
            "quota_remaining": {
                "follow": 27,
                "welcome": 0,
                "unfollow": 0,
            },
            "frozen_phase_plan": {
                "account_id": TEST_CANARY_ACCOUNT_ID,
                "package_contract_ready": True,
                "follow_60s_canary_contract": {
                    "schema": canary.ONE_SHOT_CONTRACT_SCHEMA,
                    "source_run_id": source_run_id,
                    "follow_quota": 27,
                    "golden_fallback_policy": "proof_rejection_only",
                    "expires_at": "2999-01-01T00:00:00+00:00",
                },
            },
        }
        allowed = lambda value: canary._one_shot_resume_allowed(
            value, account_id=TEST_CANARY_ACCOUNT_ID
        )
        self.assertTrue(allowed(policy)[0])
        with self.subTest("valid frozen one-shot"):
            self.assertTrue(configure_canary(canary,
                account_id=TEST_CANARY_ACCOUNT_ID,
                account_username=TEST_CANARY_USERNAME,
                run_id="resume-run",
                package="com.instagram.androig",
                resume_policy=policy,
            ))
            mismatched_source = {
                **policy,
                "prior_run_id": "different-run",
            }
            self.assertEqual(
                allowed(mismatched_source),
                (False, "canary_contract_source_mismatch"),
            )
            self.assertFalse(allowed({
                **policy,
                "phases_to_run": {"follow": True, "welcome": False, "unfollow": True},
            })[0])
            self.assertFalse(allowed({
                **policy, "quota_remaining": {"follow": 0}
            })[0])
            self.assertFalse(allowed({
                **policy,
                "frozen_phase_plan": {
                    **policy["frozen_phase_plan"],
                    "follow_60s_canary_contract": {},
                },
            })[0])
            self.assertFalse(allowed({
                **policy,
                "frozen_phase_plan": {
                    **policy["frozen_phase_plan"],
                    "follow_60s_canary_contract": {
                        **policy["frozen_phase_plan"]["follow_60s_canary_contract"],
                        "golden_fallback_policy": "always",
                    },
                },
            })[0])

    def test_armed_control_resume_uses_exact_contract_without_human_incident_fields(self) -> None:
        policy = {
            "prior_run_id": "source-run",
            "restart_allowed": True,
            "phases_to_run": {"welcome": False, "follow": True, "unfollow": False},
            "quota_remaining": {"follow": 10, "welcome": 0, "unfollow": 0},
            "request_metadata": {
                "source": "auto_restart_tick",
                "recovery_mode": "follow60_armed_control_resume",
            },
            "frozen_phase_plan": {
                "account_id": "account-1",
                "package_contract_ready": True,
                "phase_plan_source": "follow60_armed_control",
                "follow_60s_canary_contract": {
                    "schema": "FOLLOW_60S_ONE_SHOT_V2",
                    "control_id": "control-1",
                    "source_run_id": "source-run",
                    "follow_quota": 10,
                    "golden_fallback_policy": "proof_rejection_only",
                    "expires_at": "2099-01-01T00:00:00+00:00",
                },
            },
        }
        self.assertEqual(
            canary._one_shot_resume_allowed(policy, account_id="account-1"),
            (True, ""),
        )
        policy["frozen_phase_plan"]["phase_plan_source"] = "legacy"
        self.assertEqual(
            canary._one_shot_resume_allowed(policy, account_id="account-1"),
            (False, "armed_control_phase_plan_source_mismatch"),
        )

    def test_snapshot_is_single_consume_and_invalidated_by_viewport_change(self) -> None:
        canary.stash_next_candidate_snapshot(
            source_profile_username="ct", package="pkg", activity="act",
            navigation_generation="g1", viewport_fingerprint="v1",
            detection={"is_followers_list": True, "action_bar_title": "ct"},
        )
        snap, _, reason = canary.consume_next_candidate_snapshot(
            source_profile_username="ct", package="pkg", activity="act",
            navigation_generation="g1", viewport_fingerprint="v2",
        )
        self.assertIsNone(snap)
        self.assertEqual(reason, "viewport_mismatch")
        self.assertEqual(
            canary.consume_next_candidate_snapshot(source_profile_username="ct")[2],
            "missing_snapshot",
        )

    def test_scroll_preserves_semantic_verdict_but_clears_geometry(self) -> None:
        canary.create_candidate_profile_verdict(
            candidate_username="candidate", package="pkg", activity="act",
            navigation_generation="g1", exact_identity=True, sheet_closed=True,
            mute_posts_verified=True, mute_stories_verified=True,
        )
        canary.stash_post_grid_evidence(
            candidate_username="candidate", package_name="com.instagram.android",
            activity_name="com.instagram.mainactivity.InstagramMainActivity",
            navigation_generation="g1", viewport_fingerprint="v1",
            outcome="NO_POSTS_POSITIVE",
            mute_sheet_closed=True, mute_posts_verified=True,
            mute_stories_verified=True, profile_identity_method="test_exact_profile",
            screen_width=1080, screen_height=2340,
            grid_tab_state="selected_no_posts", no_posts_positive=True,
        )
        canary.stash_next_candidate_snapshot(
            source_profile_username="ct", package="pkg", activity="act",
            navigation_generation="g1", viewport_fingerprint="v1",
            detection={"is_followers_list": True},
        )
        canary.invalidate("scroll")
        stats = canary.stats()
        self.assertEqual(stats["live_candidate_verdict_count"], 1)
        self.assertEqual(stats["live_post_grid_evidence_count"], 0)
        self.assertFalse(stats["live_next_candidate_snapshot"])
        verdict, _, reason = canary.get_candidate_profile_verdict(
            candidate_username="candidate", package="pkg", activity="act",
            navigation_generation="g2",
        )
        self.assertIsNotNone(verdict)
        self.assertEqual(reason, "")
        self.assertIsNone(verdict.post_bounds)

    def test_navigation_invalidation_clears_semantic_verdict(self) -> None:
        canary.create_candidate_profile_verdict(
            candidate_username="candidate", package="pkg", activity="act",
            navigation_generation="g1", exact_identity=True, sheet_closed=True,
            mute_posts_verified=True, mute_stories_verified=True,
        )
        canary.invalidate("planned_post_cell_tap")
        self.assertEqual(canary.stats()["live_candidate_verdict_count"], 0)


class Follow60sSingleCaptureClassifiersTest(unittest.TestCase):
    def tearDown(self) -> None:
        configure_canary(canary,
            account_id="other",
            account_username="other",
            run_id="reset",
            package="com.instagram.android",
            resume_policy=None,
        )

    def test_mute_sheet_levels_use_one_xml_classification(self) -> None:
        level1_xml = """<hierarchy><node text="Close friends"/><node text="Mute"/>
        <node text="Restrict"/><node text="Unfollow"/></hierarchy>"""
        level2_xml = """<hierarchy><node text="Mute"/><node text="Posts"/>
        <node text="Stories"/><node text="Notes"/></hierarchy>"""
        self.assertEqual(
            nav._mute_engine_v2_detect_sheet_level_from_xml(level1_xml)[0],
            "following_options",
        )
        self.assertEqual(
            nav._mute_engine_v2_detect_sheet_level_from_xml(level2_xml)[0],
            "mute_toggles",
        )

    def test_mute_sheet_single_xml_indexes_posts_and_stories_switches(self) -> None:
        xml = """<hierarchy><node text="Mute"/><node text="Notes"/>
        <node text="Posts"><node class="android.widget.Switch" checkable="true"
        checked="false" bounds="[800,900][1020,1040]"/></node>
        <node text="Stories"><node class="android.widget.Switch" checkable="true"
        checked="true" bounds="[800,1050][1020,1190]"/></node></hierarchy>"""
        level, meta = nav._mute_engine_v2_detect_sheet_level_from_xml(xml)
        self.assertEqual(level, "mute_toggles")
        self.assertEqual(meta["switch_index"]["posts"]["checked"], False)
        self.assertEqual(meta["switch_index"]["stories"]["checked"], True)

    def test_pre_follow_mono_capture_requires_positive_public_surface(self) -> None:
        device = MagicMock()
        device.dump_hierarchy.return_value = """<hierarchy>
        <node text="candidate"/><node text="Posts"/><node text="Followers"/>
        <node text="Following"/><node text="Follow"
        resource-id="com.instagram.android:id/profile_header_follow_button"
        bounds="[700,420][1040,560]"/>
        <node resource-id="com.instagram.android:id/profile_tabs_container"/>
        </hierarchy>"""
        with patch.object(
            nav,
            "_followers_current_pkg_activity",
            return_value={
                "current_package": "com.instagram.android",
                "current_activity": "ProfileActivity",
            },
        ):
            out = nav.acquire_pre_follow_mono_capture(
                device,
                follower_username="candidate",
                expected_package="com.instagram.android",
            )
        self.assertTrue(out["ok"])
        self.assertFalse(out["private_probe_payload"]["private_profile_detected"])
        self.assertTrue(out["private_probe_payload"]["public_profile_proven"])
        device.dump_hierarchy.assert_called_once()

    def test_pre_follow_mono_capture_fails_closed_for_private_shape_without_marker(self) -> None:
        device = MagicMock()
        # Regression fixture for dant9491_: identity, stats and Follow are not
        # sufficient when Instagram omits the private copy from accessibility.
        device.dump_hierarchy.return_value = """<hierarchy>
        <node text="dant9491_"/><node text="26 posts"/>
        <node text="41 followers"/><node text="114 following"/>
        <node text="Follow"
        resource-id="com.instagram.android:id/profile_header_follow_button"
        bounds="[10,280][430,320]"/></hierarchy>"""
        with patch.object(
            nav,
            "_followers_current_pkg_activity",
            return_value={
                "current_package": "com.instagram.android",
                "current_activity": "ProfileActivity",
            },
        ):
            out = nav.acquire_pre_follow_mono_capture(
                device,
                follower_username="dant9491_",
                expected_package="com.instagram.android",
            )
        self.assertFalse(out["ok"])
        self.assertFalse(out["private_probe_payload"]["private_profile_detected"])
        self.assertFalse(out["private_probe_payload"]["public_profile_proven"])

    def test_pre_follow_mono_capture_precomputes_v2_grid_once_when_requested(self) -> None:
        device = MagicMock()
        device.dump_hierarchy.return_value = """<hierarchy>
        <node bounds="[0,0][1080,2340]"/><node text="candidate"/>
        <node content-desc="8 posts"
        resource-id="com.instagram.android:id/profile_header_post_count"/>
        <node text="42 followers"/>
        <node text="17 following"/><node text="Follow"
        resource-id="com.instagram.android:id/profile_header_follow_button"
        bounds="[700,420][1040,560]"/>
        <node resource-id="profile_tabs_container" bounds="[0,700][1080,850]">
          <node resource-id="profile_tab_icon_view" content-desc="Grid view"
          selected="true" bounds="[0,700][360,850]"/>
        </node>
        <node class="android.widget.ImageView" resource-id="profile_grid_media_0"
        content-desc="Post thumbnail, row 1, column 1"
        bounds="[0,900][360,1260]"/></hierarchy>"""
        with patch.object(
            nav,
            "_followers_current_pkg_activity",
            return_value={
                "current_package": "com.instagram.android",
                "current_activity": "ProfileActivity",
            },
        ), patch.object(
            nav,
            "_post_follow_post_grid_evidence_from_xml",
            wraps=nav._post_follow_post_grid_evidence_from_xml,
        ) as grid:
            out = nav.acquire_pre_follow_mono_capture(
                device,
                follower_username="candidate",
                expected_package="com.instagram.android",
                prepare_ordering_v2_evidence=True,
            )
        self.assertTrue(out["ok"])
        grid.assert_called_once()
        self.assertIsInstance(out["_ordering_v2_existing_grid_evidence"], dict)
        self.assertEqual(out["_ordering_v2_existing_viewport"], [1080, 2340])
        self.assertEqual(
            out["structured_post_count_observation"]["posts_count"], 8
        )
        self.assertIn("grid_classification_cpu_ms", out["mono_capture_breakdown_ms"])

    def test_pre_follow_mono_capture_rejects_private_even_with_follow_cta(self) -> None:
        device = MagicMock()
        device.dump_hierarchy.return_value = """<hierarchy>
        <node text="candidate"/><node text="Posts"/><node text="Followers"/>
        <node text="Following"/><node text="Follow"/>
        <node text="This account is private"/></hierarchy>"""
        out = nav.acquire_pre_follow_mono_capture(
            device, follower_username="candidate"
        )
        self.assertFalse(out["ok"])
        self.assertTrue(out["private_probe_payload"]["private_profile_detected"])

    def test_pre_follow_mono_capture_rejects_missing_cta_or_package_mismatch(self) -> None:
        device = MagicMock()
        public_without_cta = """<hierarchy><node text="candidate"/>
        <node text="Posts"/><node text="Followers"/><node text="Following"/>
        </hierarchy>"""
        device.dump_hierarchy.return_value = public_without_cta
        with patch.object(
            nav,
            "_followers_current_pkg_activity",
            return_value={
                "current_package": "com.instagram.android",
                "current_activity": "ProfileActivity",
            },
        ):
            no_cta = nav.acquire_pre_follow_mono_capture(
                device,
                follower_username="candidate",
                expected_package="com.instagram.android",
            )
        self.assertFalse(no_cta["ok"])
        self.assertFalse(no_cta["follow_cta_positive"])

        device.dump_hierarchy.return_value = """<hierarchy>
        <node text="candidate"/><node text="Posts"/><node text="Followers"/>
        <node text="Following"/><node text="Follow"
        resource-id="com.instagram.android:id/profile_header_follow_button"
        bounds="[700,420][1040,560]"/></hierarchy>"""
        with patch.object(
            nav,
            "_followers_current_pkg_activity",
            return_value={
                "current_package": "other.package",
                "current_activity": "ProfileActivity",
            },
        ):
            wrong_package = nav.acquire_pre_follow_mono_capture(
                device,
                follower_username="candidate",
                expected_package="com.instagram.android",
            )
        self.assertFalse(wrong_package["ok"])
        self.assertFalse(wrong_package["package_exact"])

    def test_post_grid_single_xml_yields_safe_top_left_bounds(self) -> None:
        xml = """<hierarchy>
        <node text="candidate" bounds="[0,80][500,160]"/>
        <node content-desc="Profile tab grid" selected="true" bounds="[0,700][360,820]"/>
        <node class="android.widget.ImageView" bounds="[0,900][350,1250]"/>
        </hierarchy>"""
        out = nav._post_follow_post_grid_evidence_from_xml(
            xml, candidate_username="candidate", ww=1080, wh=2340
        )
        self.assertEqual(out["outcome"], "POST_ROW_POSITIVE_SAFE")
        self.assertEqual(out["post_bounds"]["left"], 0)

    def test_post_grid_dimension_producer_classifies_exact_normalized_and_untrusted(self) -> None:
        exact = nav._post_follow_screen_dimensions_from_hierarchy(
            '<hierarchy><node bounds="[0,0][1080,2400]"/></hierarchy>',
            raw_width=1080,
            raw_height=2400,
        )
        self.assertEqual(exact["screen_dimensions_source"], "raw_window_exact")
        self.assertEqual(exact["screen_height"], 2400)

        normalized = nav._post_follow_screen_dimensions_from_hierarchy(
            '<hierarchy><node bounds="[0,0][1080,2340]"/></hierarchy>',
            raw_width=1080,
            raw_height=2400,
        )
        self.assertEqual(
            normalized["screen_dimensions_source"],
            "hierarchy_coordinate_frame",
        )
        self.assertEqual(normalized["screen_height"], 2340)
        self.assertEqual(normalized["screen_inset_bottom"], 60)

        untrusted = nav._post_follow_screen_dimensions_from_hierarchy(
            '<hierarchy><node bounds="[0,0][1000,2340]"/></hierarchy>',
            raw_width=1080,
            raw_height=2400,
        )
        self.assertFalse(untrusted["screen_dimensions_trusted"])
        self.assertEqual(
            untrusted["screen_dimensions_source"],
            "screen_dimensions_untrusted",
        )

    def test_post_grid_one_to_three_visible_posts_are_positive_without_scroll(self) -> None:
        for count in (1, 2, 3):
            cells = "".join(
                f'<node class="android.widget.ImageView" '
                f'content-desc="Post thumbnail {index + 1}" '
                f'bounds="[{index * 360},900][{(index + 1) * 360},1260]"/>'
                for index in range(count)
            )
            xml = (
                '<hierarchy><node text="candidate"/>'
                '<node resource-id="com.instagram.android:id/profile_tabs_container" '
                'bounds="[0,700][1080,820]"/>'
                '<node content-desc="Profile tab grid" bounds="[0,700][360,820]"/>'
                f'{cells}</hierarchy>'
            )
            with self.subTest(visible_posts=count):
                out = nav._post_follow_post_grid_evidence_from_xml(
                    xml, candidate_username="candidate", ww=1080, wh=2340
                )
                self.assertEqual(out["outcome"], "POST_ROW_POSITIVE_SAFE")
                self.assertEqual(out["visible_post_count"], count)
                self.assertEqual(len(out["physical_cells"]), count)
                self.assertEqual(out["grid_tab_state"], "selected_or_physical_row")

    def test_post_grid_deduplicates_nested_physical_cell_nodes(self) -> None:
        xml = """<hierarchy><node text="candidate"/>
        <node resource-id="com.instagram.android:id/profile_tabs_container"
              bounds="[0,700][1080,820]"/>
        <node content-desc="Profile tab grid" bounds="[0,700][360,820]"/>
        <node class="android.widget.FrameLayout" content-desc="Post thumbnail"
              bounds="[0,900][360,1260]">
          <node class="android.widget.ImageView" content-desc="Post image"
                bounds="[2,902][358,1258]"/>
        </node></hierarchy>"""
        out = nav._post_follow_post_grid_evidence_from_xml(
            xml, candidate_username="candidate", ww=1080, wh=2340
        )
        self.assertEqual(out["outcome"], "POST_ROW_POSITIVE_SAFE")
        self.assertEqual(out["visible_post_count"], 1)

    def test_post_grid_rejects_generic_square_view_without_media_semantics(self) -> None:
        xml = """<hierarchy><node text="candidate"/>
        <node resource-id="com.instagram.android:id/profile_tabs_container"
              bounds="[0,700][1080,820]"/>
        <node content-desc="Profile tab grid" selected="true"
              bounds="[0,700][360,820]"/>
        <node class="android.view.ViewGroup" content-desc="Suggested account"
              bounds="[0,900][360,1260]"/></hierarchy>"""
        out = nav._post_follow_post_grid_evidence_from_xml(
            xml, candidate_username="candidate", ww=1080, wh=2340
        )
        self.assertEqual(out["outcome"], "POST_GRID_AMBIGUOUS_FINAL")
        self.assertEqual(out["visible_post_count"], 0)

    def test_final_mute_close_uses_one_xml_for_identity_verdict_and_grid(self) -> None:
        self.assertTrue(
            configure_canary(canary,
                account_id=TEST_CANARY_ACCOUNT_ID,
                account_username=TEST_CANARY_USERNAME,
                run_id="mono-final-close",
                package="com.instagram.android",
                resume_policy=None,
            )
        )
        device = MagicMock()
        device.window_size.return_value = (1080, 2340)
        device.dump_hierarchy.return_value = """<hierarchy>
        <node text="candidate"
              resource-id="com.instagram.android:id/action_bar_title"/>
        <node resource-id="com.instagram.android:id/profile_tabs_container"
              bounds="[0,700][1080,820]"/>
        <node content-desc="Profile tab grid" selected="true"
              bounds="[0,700][360,820]"/>
        <node class="android.widget.ImageView" content-desc="Post thumbnail"
              bounds="[0,900][360,1260]"/></hierarchy>"""
        with patch.object(
            nav,
            "_followers_current_pkg_activity",
            return_value={
                "current_package": "com.instagram.android",
                "current_activity": "ProfileActivity",
            },
        ), patch.object(
            nav,
            "read_current_profile_username_for_follow_gate",
            side_effect=AssertionError("identity must come from the mono XML"),
        ):
            out = nav._publish_post_mute_verdict_at_final_sheet_close(
                device,
                source_profile_username="ct",
                candidate_username="candidate",
                visual_candidate_id="vc-1",
                candidate_context={},
                mute_posts_verified=True,
                mute_stories_verified=True,
                started_at=0.0,
            )
        self.assertIsNotNone(out)
        self.assertEqual(out["post_grid_outcome"], "POST_ROW_POSITIVE_SAFE")
        self.assertEqual(
            out["post_grid_metadata"]["final_proof_package"],
            "com.instagram.android",
        )
        self.assertEqual(
            out["post_grid_metadata"]["final_proof_activity"],
            "ProfileActivity",
        )
        device.dump_hierarchy.assert_called_once_with(compressed=False)

    def test_post_grid_accepts_clipped_visible_cell_but_rejects_reels_tab(self) -> None:
        base = """<hierarchy><node text="candidate"/>
        <node resource-id="com.instagram.android:id/profile_tabs_container"
              bounds="[0,700][1080,820]"/>
        <node content-desc="Profile tab grid" bounds="[0,700][360,820]"/>
        {extra}
        <node class="android.widget.ImageView" content-desc="Post thumbnail"
              bounds="[0,850][360,1050]"/></hierarchy>"""
        clipped = nav._post_follow_post_grid_evidence_from_xml(
            base.format(extra=""), candidate_username="candidate", ww=1080, wh=1800
        )
        self.assertEqual(clipped["outcome"], "POST_ROW_POSITIVE_SAFE")
        reels = nav._post_follow_post_grid_evidence_from_xml(
            base.format(
                extra='<node content-desc="Profile tab reels" selected="true" '
                'bounds="[360,700][720,820]"/>'
            ),
            candidate_username="candidate",
            ww=1080,
            wh=1800,
        )
        self.assertEqual(reels["outcome"], "POST_GRID_AMBIGUOUS_FINAL")

    def test_post_grid_single_xml_no_posts_requires_identity_and_tabs(self) -> None:
        xml = """<hierarchy><node text="candidate"/>
        <node content-desc="Profile tab grid" selected="true" bounds="[0,700][360,820]"/>
        <node text="No Posts Yet"/></hierarchy>"""
        out = nav._post_follow_post_grid_evidence_from_xml(
            xml, candidate_username="candidate", ww=1080, wh=2340
        )
        self.assertEqual(out["outcome"], "NO_POSTS_POSITIVE")

    def test_ambiguous_xml_goes_directly_to_golden_without_vision(self) -> None:
        device = MagicMock()
        xml = """<hierarchy><node text="candidate"/>
        <node content-desc="Profile tab grid" selected="true"
              bounds="[0,700][360,820]"/></hierarchy>"""
        raw = nav._post_follow_post_grid_evidence_from_xml(
            xml, candidate_username="candidate", ww=1080, wh=2340
        )
        self.assertEqual(raw["outcome"], "POST_GRID_AMBIGUOUS_FINAL")
        with patch.object(
            nav,
            "_post_follow_likes_probe_top_left_vision_cell_meta",
            return_value={
                "reliable": True,
                "reason": "vision_thumbnail_top_left",
                "vision_top_left_variance": 321.5,
                "screenshot_path": "/tmp/fresh-grid.png",
                "cell": {
                    "left": 0,
                    "top": 900,
                    "right": 360,
                    "bottom": 1260,
                    "center_x": 180,
                    "center_y": 1080,
                },
            },
        ) as probe:
            out = nav._post_follow_promote_ambiguous_grid_evidence_with_fresh_vision(
                device, raw, ww=1080, wh=2340
            )
        probe.assert_not_called()
        self.assertEqual(out["outcome"], "POST_GRID_AMBIGUOUS_FINAL")
        self.assertFalse(out["fast_vision_probe_attempted"])

    def test_ambiguous_xml_keeps_golden_fallback_when_vision_is_not_safe(self) -> None:
        device = MagicMock()
        raw = {
            "outcome": "POST_GRID_AMBIGUOUS_FINAL",
            "identity_exact": True,
            "profile_tabs_present": True,
            "grid_selected": True,
            "tabs_bottom": 820,
            "loading_visible": False,
            "private_profile_visible": False,
            "reels_or_tagged_selected": False,
        }
        with patch.object(
            nav,
            "_post_follow_likes_probe_top_left_vision_cell_meta",
            return_value={
                "reliable": False,
                "reason": "vision_thumbnail_top_left_not_found",
                "cell": None,
            },
        ):
            out = nav._post_follow_promote_ambiguous_grid_evidence_with_fresh_vision(
                device, raw, ww=1080, wh=2340
            )
        self.assertEqual(out["outcome"], "POST_GRID_AMBIGUOUS_FINAL")
        self.assertFalse(out["fast_vision_probe_attempted"])

    def test_suggested_overlay_never_promotes_to_direct_post_tap(self) -> None:
        device = MagicMock()
        raw = {
            "outcome": "POST_GRID_AMBIGUOUS_FINAL",
            "identity_exact": True,
            "profile_tabs_present": True,
            "grid_selected": True,
            "tabs_bottom": 820,
            "loading_visible": False,
            "private_profile_visible": False,
            "reels_or_tagged_selected": False,
            "suggested_overlay_visible": True,
        }
        with patch.object(
            nav, "_post_follow_likes_probe_top_left_vision_cell_meta"
        ) as probe:
            out = nav._post_follow_promote_ambiguous_grid_evidence_with_fresh_vision(
                device, raw, ww=1080, wh=2340
            )
        probe.assert_not_called()
        self.assertEqual(out["outcome"], "POST_GRID_AMBIGUOUS_FINAL")
        self.assertFalse(out["fast_vision_probe_attempted"])


if __name__ == "__main__":
    unittest.main()
