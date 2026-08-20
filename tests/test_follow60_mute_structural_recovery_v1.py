from __future__ import annotations

import json
import unittest
from pathlib import Path

import account_session_orchestrator as orchestrator
import instagram_navigation as navigation
from follow_outcome_contract import build_follow_termination_decision


class Follow60MuteStructuralRecoveryV1Test(unittest.TestCase):
    @staticmethod
    def _mythyl_field_fixture() -> dict:
        fixture_path = (
            Path(__file__).parent
            / "fixtures"
            / "follow60_field_fixture_mythyl_action_row_ownership.json"
        )
        return json.loads(fixture_path.read_text(encoding="utf-8"))

    def test_mythyl_canonical_compact_following_cta_is_owned_without_peers(self) -> None:
        fixture = self._mythyl_field_fixture()
        cta = fixture["following_cta"]
        result = navigation._mute_engine_v2_following_cta_structure_ok(
            bounds=cta["bounds"],
            peer_controls=fixture["peer_controls"],
            ww=fixture["window"]["width"],
            wh=fixture["window"]["height"],
            candidate_profile_confirmed=fixture["candidate_profile_confirmed"],
            candidate_text=cta["text"],
            resource_id=cta["resource_id"],
            profile_surface_certified=fixture["profile_surface_certified"],
            evidence_fresh=fixture["evidence_fresh"],
        )
        self.assertEqual(result, (True, fixture["expected_matcher"]))

    def test_canonical_profile_cta_scales_without_fixed_absolute_coordinates(self) -> None:
        for ww, wh, bounds in (
            (720, 1560, {"left": 22, "top": 394, "right": 319, "bottom": 455}),
            (1080, 2340, {"left": 33, "top": 592, "right": 479, "bottom": 682}),
            (1440, 3120, {"left": 44, "top": 789, "right": 639, "bottom": 909}),
        ):
            with self.subTest(window=(ww, wh)):
                self.assertEqual(
                    navigation._mute_engine_v2_following_cta_structure_ok(
                        bounds=bounds,
                        peer_controls=[],
                        ww=ww,
                        wh=wh,
                        candidate_profile_confirmed=True,
                        candidate_text="Following",
                        resource_id="com.instagram.clone:id/profile_header_follow_button",
                        profile_surface_certified=True,
                        evidence_fresh=True,
                    ),
                    (True, "canonical_profile_header_cta"),
                )

    def test_low_profile_header_wide_following_cta_is_owned_structurally(self) -> None:
        ok, reason = navigation._mute_engine_v2_following_cta_structure_ok(
            bounds={"left": 20, "top": 1210, "right": 1060, "bottom": 1300},
            peer_controls=[],
            ww=1080,
            wh=2340,
            candidate_profile_confirmed=True,
        )
        self.assertTrue(ok)
        self.assertEqual(reason, "wide_profile_action_cta")

    def test_compact_following_cta_requires_aligned_action_peer(self) -> None:
        bounds = {"left": 20, "top": 900, "right": 360, "bottom": 990}
        ok, reason = navigation._mute_engine_v2_following_cta_structure_ok(
            bounds=bounds,
            peer_controls=[
                {
                    "text": "Message",
                    "resource_id": "profile_action_message",
                    "bounds": {"left": 380, "top": 902, "right": 760, "bottom": 988},
                }
            ],
            ww=1080,
            wh=2340,
            candidate_profile_confirmed=True,
        )
        self.assertTrue(ok)
        self.assertEqual(reason, "aligned_profile_action_peer")

    def test_wrong_profile_and_unowned_list_rows_fail_closed(self) -> None:
        bounds = {"left": 700, "top": 1100, "right": 1040, "bottom": 1190}
        wrong_profile = navigation._mute_engine_v2_following_cta_structure_ok(
            bounds=bounds,
            peer_controls=[],
            ww=1080,
            wh=2340,
            candidate_profile_confirmed=False,
        )
        unowned_row = navigation._mute_engine_v2_following_cta_structure_ok(
            bounds=bounds,
            peer_controls=[],
            ww=1080,
            wh=2340,
            candidate_profile_confirmed=True,
        )
        self.assertEqual(wrong_profile, (False, "candidate_profile_identity_unproved"))
        self.assertEqual(unowned_row, (False, "profile_action_row_ownership_unproved"))

    def test_non_following_header_labels_remain_rejected(self) -> None:
        for label in ("Follow", "Message", "Contact", "Follow back"):
            with self.subTest(label=label):
                self.assertFalse(
                    navigation._mute_engine_v2_following_header_text_ok(label)
                )

    def test_canonical_resource_never_overrides_identity_surface_or_freshness(self) -> None:
        common = {
            "bounds": {"left": 33, "top": 592, "right": 479, "bottom": 682},
            "peer_controls": [],
            "ww": 1080,
            "wh": 2340,
            "candidate_text": "Following",
            "resource_id": "com.instagram.androif:id/profile_header_follow_button",
        }
        cases = (
            (
                {"candidate_profile_confirmed": False},
                "candidate_profile_identity_unproved",
            ),
            (
                {
                    "candidate_profile_confirmed": True,
                    "profile_surface_certified": False,
                },
                "candidate_profile_surface_unproved",
            ),
            (
                {
                    "candidate_profile_confirmed": True,
                    "profile_surface_certified": True,
                    "evidence_fresh": False,
                },
                "candidate_profile_evidence_stale",
            ),
        )
        for overrides, expected_reason in cases:
            with self.subTest(reason=expected_reason):
                ok, reason = navigation._mute_engine_v2_following_cta_structure_ok(
                    **common,
                    **overrides,
                )
                self.assertFalse(ok)
                self.assertEqual(reason, expected_reason)

    def test_message_contact_and_follow_state_false_positives_fail_closed(self) -> None:
        compact_bounds = {"left": 33, "top": 592, "right": 479, "bottom": 682}
        wide_bounds = {"left": 20, "top": 592, "right": 1060, "bottom": 682}
        cases = (
            ("Message", "profile_header_follow_button", compact_bounds),
            ("Contact", "profile_header_follow_button", compact_bounds),
            ("Follow", "profile_header_follow_button", compact_bounds),
            ("Following", "profile_header_message_button", compact_bounds),
            ("Following", "profile_header_contact_button", compact_bounds),
            ("Following", "profile_header_message_button", wide_bounds),
            ("Following", "profile_header_contact_button", wide_bounds),
        )
        for text, rid, bounds in cases:
            with self.subTest(text=text, rid=rid):
                ok, _reason = navigation._mute_engine_v2_following_cta_structure_ok(
                    bounds=bounds,
                    peer_controls=[],
                    ww=1080,
                    wh=2340,
                    candidate_profile_confirmed=True,
                    candidate_text=text,
                    resource_id=f"com.instagram.android:id/{rid}",
                    profile_surface_certified=True,
                    evidence_fresh=True,
                )
                self.assertFalse(ok)

    def test_candidate_bounds_must_be_inside_current_app_window(self) -> None:
        cases = (
            ({"left": -1, "top": 592, "right": 479, "bottom": 682}, "outside_app_window"),
            ({"left": 33, "top": 592, "right": 1081, "bottom": 682}, "outside_app_window"),
            ({"left": 479, "top": 592, "right": 33, "bottom": 682}, "invalid_candidate_bounds"),
        )
        for bounds, expected_reason in cases:
            with self.subTest(reason=expected_reason):
                self.assertEqual(
                    navigation._mute_engine_v2_following_cta_structure_ok(
                        bounds=bounds,
                        peer_controls=[],
                        ww=1080,
                        wh=2340,
                        candidate_profile_confirmed=True,
                        candidate_text="Following",
                        resource_id="com.instagram.android:id/profile_header_follow_button",
                        profile_surface_certified=True,
                        evidence_fresh=True,
                    ),
                    (False, expected_reason),
                )

    def test_ambiguous_canonical_following_controls_fail_closed(self) -> None:
        controls = [
            {
                "text": "Following",
                "resource_id": "com.instagram.android:id/profile_header_follow_button",
                "bounds": bounds,
            }
            for bounds in (
                {"left": 33, "top": 592, "right": 479, "bottom": 682},
                {"left": 520, "top": 592, "right": 966, "bottom": 682},
            )
        ]
        self.assertEqual(
            navigation._mute_engine_v2_following_cta_structure_ok(
                bounds=controls[0]["bounds"],
                peer_controls=controls,
                ww=1080,
                wh=2340,
                candidate_profile_confirmed=True,
                candidate_text="Following",
                resource_id=controls[0]["resource_id"],
                profile_surface_certified=True,
                evidence_fresh=True,
            ),
            (False, "ambiguous_canonical_profile_cta"),
        )

    def test_duplicate_accessibility_views_for_same_bounds_are_not_ambiguous(self) -> None:
        bounds = {"left": 33, "top": 592, "right": 479, "bottom": 682}
        duplicate_views = [
            {
                "text": "Following",
                "resource_id": "com.instagram.android:id/profile_header_follow_button",
                "bounds": bounds,
            },
            {
                "text": "Following",
                "resource_id": "com.instagram.android:id/profile_header_follow_button",
                "bounds": dict(bounds),
            },
        ]
        self.assertEqual(
            navigation._mute_engine_v2_following_cta_structure_ok(
                bounds=bounds,
                peer_controls=duplicate_views,
                ww=1080,
                wh=2340,
                candidate_profile_confirmed=True,
                candidate_text="Following",
                resource_id=duplicate_views[0]["resource_id"],
            ),
            (True, "canonical_profile_header_cta"),
        )

    def test_real_profile_layout_matrix_remains_fail_closed(self) -> None:
        canonical_rid = "com.instagram.android:id/profile_header_follow_button"
        compact = {"left": 33, "top": 592, "right": 479, "bottom": 682}
        message = {
            "text": "Message",
            "resource_id": "com.instagram.android:id/profile_header_message_button",
            "bounds": {"left": 500, "top": 592, "right": 920, "bottom": 682},
        }
        contact = {
            "text": "Contact",
            "resource_id": "com.instagram.android:id/profile_header_contact_button",
            "bounds": {"left": 500, "top": 592, "right": 920, "bottom": 682},
        }
        layouts = {
            "A_standard_following_message": ([message], compact, True),
            "B_lower_profile": ([], {"left": 33, "top": 1200, "right": 479, "bottom": 1290}, True),
            "C_following_only": ([], compact, True),
            "D_private_profile": ([], compact, True),
            "E_business_contact": ([contact], compact, True),
            "F_reordered_action_row": ([contact, message], compact, True),
            "G_flattened_accessibility": ([], compact, True),
            "H_outside_profile_region": ([], {"left": 33, "top": 2050, "right": 479, "bottom": 2140}, False),
        }
        for layout, (peers, bounds, expected) in layouts.items():
            with self.subTest(layout=layout):
                ok, _reason = navigation._mute_engine_v2_following_cta_structure_ok(
                    bounds=bounds,
                    peer_controls=peers,
                    ww=1080,
                    wh=2340,
                    candidate_profile_confirmed=True,
                    candidate_text="Following",
                    resource_id=canonical_rid,
                    profile_surface_certified=True,
                    evidence_fresh=True,
                )
                self.assertEqual(ok, expected)

    def test_wrong_screen_list_and_modal_following_fail_closed(self) -> None:
        for surface in ("wrong_screen", "followers_list", "modal"):
            with self.subTest(surface=surface):
                ok, reason = navigation._mute_engine_v2_following_cta_structure_ok(
                    bounds={"left": 33, "top": 592, "right": 479, "bottom": 682},
                    peer_controls=[],
                    ww=1080,
                    wh=2340,
                    candidate_profile_confirmed=True,
                    candidate_text="Following",
                    resource_id="com.instagram.android:id/profile_header_follow_button",
                    profile_surface_certified=False,
                    evidence_fresh=True,
                )
                self.assertEqual((ok, reason), (False, "candidate_profile_surface_unproved"))

    def test_exit_53_handoff_requires_complete_candidate_local_contract(self) -> None:
        outcome = build_follow_termination_decision(
            exit_code=53,
            first_causal_reason="following_button_not_found",
            follows_completed_count=1,
            target_follow_budget_effective=120,
            target_attribution={"candidate_username": "sanitized_candidate"},
            physical_follow_preserved=True,
            canonical_follow_receipt_present=True,
            candidate_local_failure=True,
            post_follow_recovery_required=True,
            no_new_follow_until_recovered=True,
            safe_boundary=True,
            safe_next_step="handoff_to_unfollow",
        )
        self.assertEqual(
            orchestrator._follow_exit_handoff_gate(53, outcome),
            (True, "follow_candidate_local_post_follow_partial_safe_for_unfollow"),
        )
        outcome["safe_boundary"] = False
        self.assertEqual(
            orchestrator._follow_exit_handoff_gate(53, outcome),
            (False, "follow_termination_decision_invalid"),
        )

    def test_restart_is_blocked_until_candidate_local_recovery_completes(self) -> None:
        eligibility = orchestrator._restart_eligibility(
            session_termination_class="partial_resumable",
            follow_quota_remaining=10,
            follow_to_unfollow_diagnostic={},
            follow_to_unfollow_real={},
            follow_outcome={
                "post_follow_recovery_required": True,
                "no_new_follow_until_recovered": True,
            },
        )
        self.assertEqual(
            eligibility,
            ("blocked", "candidate_local_post_follow_recovery_required"),
        )


if __name__ == "__main__":
    unittest.main()
