from __future__ import annotations

import unittest

import account_session_orchestrator as orchestrator
import instagram_navigation as navigation
from follow_outcome_contract import build_follow_termination_decision


class Follow60MuteStructuralRecoveryV1Test(unittest.TestCase):
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
