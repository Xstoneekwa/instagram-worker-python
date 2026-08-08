from __future__ import annotations

from dataclasses import replace
import unittest
from unittest import mock

import instagram_navigation as nav
import post_open_intent_v2 as intent_v2
from tests.test_follow60_ordering_v2_behavioral_canary_v1 import _binding, _stable


class PostOpenIntentV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        intent_v2._reset_consumed_nonces_for_tests()
        self.binding = {
            "account_id": "account", "run_id": "run", "request_id": "request",
            "action_id": "action", "attempt_id": 1,
            "business_session_id": "session", "control_id": "control",
            "worker_sha": "a" * 40,
        }

    def _intent(self, *, branch: str = "SAFE", row: int = 0, column: int = 0):
        return intent_v2.create_post_open_intent_v2(
            binding=self.binding, candidate_username="candidate", target_id="target",
            target_username="ct", package="com.instagram.android",
            activity="com.instagram.mainactivity.InstagramMainActivity",
            source_branch=branch, absolute_row=row, absolute_column=column,
            bounds={"left": 0, "top": 900, "right": 360, "bottom": 1260},
            coordinate_frame={"transform_version": "coordinate_frame_v1"},
            viewport={"width": 1080, "height": 2340}, insets={"bottom": 126},
            navigation_generation=4, scroll_generation=2, ui_generation=6,
            xml_hash="xml-hash", fingerprint="viewport-fingerprint",
            post_grid_classification="POST_ROW_POSITIVE_SAFE",
            candidate_bound_provenance="final_mute_close_xml", ttl_ms=1250,
            created_at_monotonic=100.0,
        )

    def _consume(self, intent, **overrides):
        values = {
            "binding": self.binding, "candidate_username": "candidate",
            "package": "com.instagram.android",
            "activity": "com.instagram.mainactivity.InstagramMainActivity",
            "viewport": {"width": 1080, "height": 2340},
            "navigation_generation": 4, "scroll_generation": 2,
            "ui_generation": 6, "now_monotonic": 100.4,
        }
        values.update(overrides)
        return intent_v2.consume_post_open_intent_v2(intent, **values)

    def test_safe_and_golden_share_the_same_contract(self):
        for branch in ("SAFE", "GOLDEN"):
            with self.subTest(branch=branch):
                item = self._intent(branch=branch)
                self.assertIsNotNone(item)
                self.assertEqual(item.version, "PostOpenIntentV2")

    def test_intent_is_consumed_once(self):
        item = self._intent()
        self.assertIsNotNone(self._consume(item)[0])
        self.assertEqual(self._consume(item)[2], "post_open_intent_already_consumed")

    def test_generation_candidate_and_binding_mismatch_fail_closed(self):
        cases = (
            ({"navigation_generation": 5}, "post_open_intent_navigation_generation_mismatch"),
            ({"candidate_username": "other"}, "post_open_intent_candidate_mismatch"),
            ({"binding": {**self.binding, "run_id": "other"}}, "post_open_intent_run_mismatch"),
        )
        for values, reason in cases:
            with self.subTest(reason=reason):
                item = self._intent()
                self.assertEqual(self._consume(item, **values)[2], reason)

    def test_stale_bounds_and_ttl_fail_closed(self):
        item = self._intent()
        self.assertEqual(
            self._consume(item, viewport={"width": 720, "height": 1600})[2],
            "post_open_intent_viewport_or_bounds_stale",
        )
        item = self._intent()
        self.assertEqual(
            self._consume(item, now_monotonic=101.3)[2],
            "post_open_intent_expired",
        )

    def test_row_two_never_becomes_row_one(self):
        item = self._intent(row=1, column=0)
        accepted, _, reason = self._consume(item)
        self.assertEqual(reason, "")
        self.assertEqual(accepted.absolute_row, 1)
        self.assertEqual(accepted.absolute_column, 0)

    def test_invalidated_intent_is_rejected(self):
        item = replace(
            self._intent(), invalidated=True,
            invalidation_reason="planned_scroll_after_intent",
        )
        self.assertEqual(self._consume(item)[2], "planned_scroll_after_intent")

    def test_safe_intent_reuses_terminal_surface_without_device_reacquisition(self):
        package, activity = nav._post_open_intent_surface_from_final_proof(
            {
                "final_proof_package": "com.instagram.android",
                "final_proof_activity": (
                    "com.instagram.mainactivity.InstagramMainActivity"
                ),
            },
            expected_package="com.instagram.android",
        )
        self.assertEqual(package, "com.instagram.android")
        self.assertEqual(
            activity,
            "com.instagram.mainactivity.InstagramMainActivity",
        )

    def test_post_reveal_surface_supersedes_mute_close_surface(self):
        package, activity = nav._post_open_intent_surface_from_final_proof(
            {
                "final_proof_package": "com.instagram.android",
                "final_proof_activity": "stale.ProfileActivity",
                "post_reveal_package": "com.instagram.android",
                "post_reveal_activity": (
                    "com.instagram.mainactivity.InstagramMainActivity"
                ),
            },
            expected_package="com.instagram.android",
        )
        self.assertEqual(package, "com.instagram.android")
        self.assertIn("InstagramMainActivity", activity)

    def test_terminal_surface_mismatch_fails_closed(self):
        self.assertEqual(
            nav._post_open_intent_surface_from_final_proof(
                {
                    "final_proof_package": "other.package",
                    "final_proof_activity": "other.MainActivity",
                },
                expected_package="com.instagram.android",
            ),
            ("", ""),
        )

    def _ordering_v2_stage_binding(self, binding, stable):
        return {
            "account_id": binding.account_id,
            "run_id": binding.run_id,
            "request_id": binding.request_id,
            "action_id": stable.action_id,
            "attempt_id": binding.attempt_id,
            "business_session_id": binding.business_session_id,
            "control_id": binding.control_id,
            "worker_sha": binding.actual_worker_sha,
            "source_target_id": stable.target_id,
        }

    def _ordering_v2_intent(self, runtime):
        binding, _ = _binding()
        stable = _stable(binding)
        evidence = dict(stable.post_grid_evidence)
        package, activity = nav._post_open_intent_surface_from_final_proof(
            evidence, expected_package="com.instagram.android"
        )
        with mock.patch(
            "follow_60s_canary.runtime_context", return_value=runtime
        ):
            intent = nav._create_post_open_intent_from_final_proof(
                binding=self._ordering_v2_stage_binding(binding, stable),
                candidate_username=stable.candidate_username,
                target_username="ct",
                source_branch="SAFE",
                bounds=dict(evidence["post_bounds"]),
                package=package,
                activity=activity,
                evidence=evidence,
                viewport_width=int(evidence["screen_width"]),
                viewport_height=int(evidence["screen_height"]),
                xml_hash=str(evidence["source_xml_fingerprint"]),
                fingerprint=str(evidence["source_xml_fingerprint"]),
                absolute_row=1,
                absolute_column=1,
                candidate_bound_provenance="ordering_v2_initial_mono_xml",
            )
        return intent

    def test_ordering_v2_initial_proof_creates_intent_without_reacquisition(self):
        intent = self._ordering_v2_intent({
            "navigation_counter": 4,
            "scroll_counter": 2,
            "ui_generation": 6,
        })
        self.assertIsNotNone(intent)
        self.assertEqual(intent.source_branch, "SAFE")

    def test_ordering_v2_initial_proof_generation_mismatch_fails_closed(self):
        intent = self._ordering_v2_intent({
            "navigation_counter": 5,
            "scroll_counter": 2,
            "ui_generation": 6,
        })
        self.assertIsNone(intent)


if __name__ == "__main__":
    unittest.main()
