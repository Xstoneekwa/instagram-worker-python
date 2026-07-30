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


if __name__ == "__main__":
    unittest.main()
