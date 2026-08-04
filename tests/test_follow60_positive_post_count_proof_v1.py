from __future__ import annotations

import time
import unittest
from unittest.mock import patch

import instagram_navigation as nav


ACTIVITY = "com.instagram.mainactivity.InstagramMainActivity"


class _Device:
    def __init__(self, xml: str = "<hierarchy />") -> None:
        self.xml = xml
        self.dump_count = 0

    def dump_hierarchy(self, compressed: bool = False) -> str:
        self.dump_count += 1
        return self.xml


class PositivePostCountProofV1Tests(unittest.TestCase):
    def setUp(self) -> None:
        nav._POSITIVE_POST_COUNT_PROOF_V1_CONSUMED_NONCES.clear()
        self.now = time.monotonic()

    def _observation(self, candidate: str = "maj_albon", count: int = 859) -> dict:
        return {
            "version": "OfficialPositivePostCountObservationV1",
            "candidate_username": candidate,
            "package": nav.config.INSTAGRAM_PACKAGE,
            "activity": ACTIVITY,
            "posts_count": count,
            "source": "profile_post_count_official_header_subtree",
            "navigation_generation": "nav-7",
            "ui_generation": 7,
            "captured_at_monotonic": self.now,
        }

    def _proof_and_binding(
        self, candidate: str = "maj_albon", count: int = 859
    ) -> tuple[dict, dict]:
        proof = nav._build_positive_post_count_proof_v1(
            self._observation(candidate, count),
            account_id="account-1",
            run_id="run-1",
            request_id="request-1",
            action_id="action-1",
            candidate_username=candidate,
            target_id="target-1",
            package=nav.config.INSTAGRAM_PACKAGE,
            activity=ACTIVITY,
        )
        self.assertIsNotNone(proof)
        binding = {
            "account_id": "account-1",
            "run_id": "run-1",
            "request_id": "request-1",
            "action_id": "action-1",
            "source_target_id": "target-1",
            "candidate_username": candidate,
            "positive_post_count_navigation_generation": "nav-7",
            "positive_post_count_ui_generation": 7,
            "positive_post_count_proof_v1": dict(proof or {}),
        }
        return dict(proof or {}), binding

    @staticmethod
    def _ambiguous_grid(**overrides: object) -> dict:
        grid = {
            "outcome": "POST_GRID_AMBIGUOUS_FINAL",
            "identity_exact": True,
            "profile_tabs_present": False,
            "grid_selected": False,
            "tabs_bottom": 0,
            "rejection_reason": "post_grid_ambiguous",
            "physical_cells": [],
            "post_bounds": None,
            "private_profile_visible": False,
            "loading_visible": False,
            "reels_or_tagged_selected": False,
            "reels_tab_state": "not_selected",
            "tagged_tab_state": "not_selected",
            "empty_marker_xml": False,
            "posts_count_zero_exact": False,
        }
        grid.update(overrides)
        return grid

    def _authorize(
        self,
        proof: dict,
        binding: dict,
        grid: dict | None = None,
        *,
        candidate: str = "maj_albon",
        now: float | None = None,
    ) -> tuple[dict, str]:
        return nav._authorize_post_grid_reveal_with_positive_post_count_proof_v1(
            grid or self._ambiguous_grid(),
            proof,
            expected_stage_binding=binding,
            candidate_username=candidate,
            live_package=nav.config.INSTAGRAM_PACKAGE,
            live_activity=ACTIVITY,
            current_ui_generation=8,
            now_monotonic=self.now + 0.5 if now is None else now,
        )

    def test_maj_albon_official_count_authorizes_reveal_only(self) -> None:
        proof, binding = self._proof_and_binding("maj_albon", 859)
        out, reason = self._authorize(proof, binding)
        self.assertEqual(reason, "")
        self.assertEqual(out["outcome"], "POST_GRID_REVEAL_REQUIRED")
        self.assertTrue(out["reveal_permission_only"])
        self.assertFalse(out["tap_safe"])
        self.assertIsNone(out["post_bounds"])

    def test_lelaboimages_official_count_authorizes_reveal_only(self) -> None:
        proof, binding = self._proof_and_binding("lelaboimages", 40)
        out, reason = self._authorize(
            proof, binding, candidate="lelaboimages"
        )
        self.assertEqual(reason, "")
        self.assertEqual(out["posts_count_value"], 40)
        self.assertFalse(out["tap_safe"])

    def test_candidate_mismatch_fails_closed(self) -> None:
        proof, binding = self._proof_and_binding()
        _, reason = self._authorize(
            proof, binding, candidate="different_candidate"
        )
        self.assertEqual(reason, "positive_post_count_proof_candidate_mismatch")

    def test_stale_proof_fails_closed(self) -> None:
        proof, binding = self._proof_and_binding()
        _, reason = self._authorize(
            proof, binding, now=self.now + 31.0
        )
        self.assertEqual(reason, "positive_post_count_proof_stale")

    def test_navigation_generation_mismatch_fails_closed(self) -> None:
        proof, binding = self._proof_and_binding()
        binding["positive_post_count_navigation_generation"] = "nav-other"
        _, reason = self._authorize(proof, binding)
        self.assertEqual(
            reason, "positive_post_count_proof_navigation_generation_mismatch"
        )

    def test_profile_fingerprint_mismatch_fails_closed(self) -> None:
        proof, binding = self._proof_and_binding()
        proof["profile_fingerprint"] = "tampered"
        _, reason = self._authorize(proof, binding)
        self.assertEqual(
            reason, "positive_post_count_proof_fingerprint_incompatible"
        )

    def test_zero_post_count_never_builds_proof(self) -> None:
        proof = nav._build_positive_post_count_proof_v1(
            self._observation(count=0),
            account_id="account-1",
            run_id="run-1",
            request_id="request-1",
            action_id="action-1",
            candidate_username="maj_albon",
            target_id="target-1",
            package=nav.config.INSTAGRAM_PACKAGE,
            activity=ACTIVITY,
        )
        self.assertIsNone(proof)

    def test_private_or_loading_surface_fails_closed(self) -> None:
        for unsafe in (
            {"private_profile_visible": True},
            {"loading_visible": True},
        ):
            with self.subTest(unsafe=unsafe):
                proof, binding = self._proof_and_binding()
                _, reason = self._authorize(
                    proof, binding, self._ambiguous_grid(**unsafe)
                )
                self.assertEqual(
                    reason, "positive_post_count_proof_unsafe_profile_surface"
                )

    def test_reels_or_tagged_selection_fails_closed(self) -> None:
        for key in ("reels_tab_state", "tagged_tab_state"):
            with self.subTest(key=key):
                proof, binding = self._proof_and_binding()
                _, reason = self._authorize(
                    proof, binding, self._ambiguous_grid(**{key: "selected"})
                )
                self.assertEqual(
                    reason, "positive_post_count_proof_unsafe_profile_surface"
                )

    def test_empty_or_exact_zero_surface_fails_closed(self) -> None:
        for key in ("empty_marker_xml", "posts_count_zero_exact"):
            with self.subTest(key=key):
                proof, binding = self._proof_and_binding()
                _, reason = self._authorize(
                    proof, binding, self._ambiguous_grid(**{key: True})
                )
                self.assertEqual(
                    reason, "positive_post_count_proof_unsafe_profile_surface"
                )

    def test_proof_is_consumed_exactly_once(self) -> None:
        proof, binding = self._proof_and_binding()
        kwargs = {
            "expected_stage_binding": binding,
            "candidate_username": "maj_albon",
            "live_package": nav.config.INSTAGRAM_PACKAGE,
            "live_activity": ACTIVITY,
            "profile_grid": self._ambiguous_grid(),
            "current_ui_generation": 8,
            "now_monotonic": self.now + 0.5,
        }
        first, first_reason = nav._consume_positive_post_count_proof_v1_for_reveal(
            proof, **kwargs
        )
        second, second_reason = nav._consume_positive_post_count_proof_v1_for_reveal(
            proof, **kwargs
        )
        self.assertTrue(first)
        self.assertEqual(first_reason, "")
        self.assertFalse(second)
        self.assertEqual(second_reason, "positive_post_count_proof_consumed")

    def test_reveal_requires_exactly_one_fresh_xml_and_invalidates_old_bounds(self) -> None:
        device = _Device("<hierarchy><node text='fresh'/></hierarchy>")
        evidence = self._ambiguous_grid(
            outcome="POST_GRID_REVEAL_REQUIRED",
            reveal_permission_only=True,
            candidate_username="maj_albon",
        )
        fresh = {
            "outcome": "POST_ROW_POSITIVE_SAFE",
            "post_bounds": {"left": 1, "top": 2, "right": 3, "bottom": 4},
            "physical_cells": [
                {"left": 1, "top": 2, "right": 3, "bottom": 4}
            ],
        }
        with patch.object(
            nav,
            "_post_follow_likes_profile_scroll_swipe",
            return_value={"swipe_ok": True, "scroll_distance_px": 120},
        ), patch.object(
            nav, "_post_follow_post_grid_evidence_from_xml", return_value=fresh
        ), patch.object(
            nav,
            "_post_follow_post_reveal_safe_first_row_contract",
            side_effect=lambda _d, classified, **_kwargs: dict(classified),
        ):
            out = nav._post_follow_promote_ambiguous_grid_evidence_with_fresh_vision(
                device,
                evidence,
                ww=1080,
                wh=2340,
                candidate_username="maj_albon",
            )
        self.assertEqual(device.dump_count, 1)
        self.assertEqual(out["reacquire_dump_count"], 1)
        self.assertTrue(out["old_bounds_invalidated"])
        self.assertEqual(out["reveal_count_total_for_like_phase"], 1)

    def test_fresh_xml_ambiguity_routes_directly_to_golden(self) -> None:
        device = _Device("<hierarchy><node text='still ambiguous'/></hierarchy>")
        evidence = self._ambiguous_grid(
            outcome="POST_GRID_REVEAL_REQUIRED",
            reveal_permission_only=True,
            candidate_username="maj_albon",
        )
        with patch.object(
            nav,
            "_post_follow_likes_profile_scroll_swipe",
            return_value={"swipe_ok": True, "scroll_distance_px": 120},
        ), patch.object(
            nav,
            "_post_follow_post_grid_evidence_from_xml",
            return_value={
                "outcome": "POST_GRID_AMBIGUOUS_FINAL",
                "rejection_reason": "fresh_direct_post_cell_missing",
            },
        ):
            out = nav._post_follow_promote_ambiguous_grid_evidence_with_fresh_vision(
                device,
                evidence,
                ww=1080,
                wh=2340,
                candidate_username="maj_albon",
            )
        self.assertEqual(device.dump_count, 1)
        self.assertEqual(out["outcome"], "POST_GRID_AMBIGUOUS_FINAL")
        self.assertEqual(out["golden_reason"], "clipped_reacquisition_not_tap_safe")
        self.assertFalse(out["fast_vision_probe_attempted"])

    def test_official_header_capture_is_reused_without_new_acquisition(self) -> None:
        for candidate, count in (("maj_albon", 859), ("lelaboimages", 40)):
            with self.subTest(candidate=candidate):
                xml = f"""<hierarchy>
                  <node text='{candidate}' />
                  <node resource-id='profile_header_count_container'>
                    <node text='{count}'/><node text='Posts'/>
                  </node>
                </hierarchy>"""
                out = nav._official_positive_post_count_observation_from_pre_follow_capture(
                    hierarchy_xml=xml,
                    candidate_username=candidate,
                    package=nav.config.INSTAGRAM_PACKAGE,
                    activity=ACTIVITY,
                    navigation_generation="nav-7",
                    ui_generation=7,
                    captured_at_monotonic=self.now,
                )
                self.assertIsNotNone(out)
                self.assertEqual(out["posts_count"], count)
                self.assertIn(out["source"], nav._POSITIVE_POST_COUNT_OFFICIAL_SOURCES)


if __name__ == "__main__":
    unittest.main()
