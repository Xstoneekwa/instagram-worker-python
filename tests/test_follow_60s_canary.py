from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import follow_60s_canary as canary
import instagram_navigation as nav


class Follow60sCanaryRuntimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.log_patch = patch.object(canary, "log")
        self.log_patch.start()

    def tearDown(self) -> None:
        self.log_patch.stop()
        canary.configure(
            account_id="other",
            account_username="other",
            run_id="reset",
            package="com.instagram.android",
            resume_policy=None,
        )

    def _configure_loriele(self, resume_policy=None) -> bool:
        return canary.configure(
            account_id=canary.LORIELE_ACCOUNT_ID,
            account_username="lorielebras_autom",
            run_id="run-1",
            package="com.instagram.android",
            resume_policy=resume_policy,
        )

    def test_only_loriele_first_natural_attempt_is_enabled(self) -> None:
        self.assertTrue(self._configure_loriele())
        self.assertTrue(canary.enabled("mute_like_handoff"))

        self.assertFalse(self._configure_loriele({"attempt_id": 2}))
        self.assertFalse(canary.enabled())

        self.assertFalse(
            canary.configure(
                account_id="another-account",
                account_username="another",
                run_id="run-2",
                package="com.instagram.android",
                resume_policy=None,
            )
        )

    def test_matching_fresh_safe_bounds_proof_reuses(self) -> None:
        self._configure_loriele()
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
        self._configure_loriele()
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
        self._configure_loriele()
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
        self._configure_loriele()
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

    def test_opening_composite_proof_contains_public_identity_and_cta(self) -> None:
        self._configure_loriele()
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
        ), patch.object(
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


class Follow60sMuteKnownDepthTest(unittest.TestCase):
    def tearDown(self) -> None:
        canary.configure(
            account_id="other",
            account_username="other",
            run_id="reset",
            package="com.instagram.android",
            resume_policy=None,
        )

    def test_following_options_depth_is_reused_before_final_profile_proof(self) -> None:
        canary.configure(
            account_id=canary.LORIELE_ACCOUNT_ID,
            account_username="lorielebras_autom",
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


if __name__ == "__main__":
    unittest.main()
