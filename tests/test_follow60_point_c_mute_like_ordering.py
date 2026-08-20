from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import unittest

import follow60_ordering_v2_behavioral_canary_v1 as ordering
from follow60_ordering_v2_ledger_v1 import LedgerScope, OrderingLedger
import instagram_navigation as nav


FIXTURE = Path(__file__).parent / "fixtures" / "follow60_field_mythyl_mute_like_ordering.json"


def _plan(*, mute_required: bool, authorized: bool = True):
    proof = SimpleNamespace(
        candidate_username="bilalmnakhry",
        action_id="mythyl-action-18",
        proof_hash="proof-point-c",
        top_left_identity="absolute_row_1_column_1_unique",
        payload=lambda: {"candidate_username": "bilalmnakhry"},
        post_grid_evidence={"cell": "top_left"},
    )
    deferred = SimpleNamespace(payload=lambda: {"action_id": "mythyl-action-18"})
    return ordering.CandidateCyclePlanV2(
        selected_path="POST_FIRST_V2",
        binding=SimpleNamespace(),
        stable_proof=proof,
        deferred_follow=deferred,
        started_at_monotonic=1.0,
        required_post_follow_mute=mute_required,
        new_like_action_authorized=authorized,
        enforce_current_like_evidence=True,
    )


class PointCMuteLikeOrderingTests(unittest.TestCase):
    def test_field_fixture_records_skip_not_like(self):
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual("bilalmnakhry", payload["candidate_username"])
        self.assertFalse(payload["like_tap_sent"])
        self.assertEqual("LIKE_NOT_PERFORMED", payload["like_action_state"])
        self.assertEqual(0, payload["like_counter_delta"])
        self.assertFalse(payload["like_receipt_created"])

        scenarios = payload["deterministic_scenarios"]
        self.assertEqual("LIKE_NOT_PERFORMED", scenarios["new_like_before_mute"]["state"])
        self.assertEqual(
            "POST_ALREADY_LIKED_NO_ACTION",
            scenarios["already_liked_before_mute"]["state"],
        )
        self.assertEqual("LIKE_ACTION_PERFORMED_NOW", scenarios["normal_happy_path"]["state"])
        self.assertEqual(0, scenarios["receipt_replay"]["counter_delta"])

    def test_required_mute_blocks_post_first_before_engine(self):
        receipts = []
        called = []
        orchestrator = ordering.Follow60OrderingV2OrchestratorV1(
            ledger_apply=lambda stage, payload: receipts.append((stage, payload)) or {"ok": True}
        )
        out = orchestrator.execute_post_first(
            _plan(mute_required=True, authorized=False),
            post_like_engine=lambda _ctx: called.append(True) or {},
        )
        self.assertFalse(out["ok"])
        self.assertEqual("required_mute_precludes_post_first_like", out["reason"])
        self.assertEqual([], called)
        self.assertEqual([], receipts)
        self.assertFalse(out["physical_action_started"])

    def test_current_like_requires_tap_fresh_verify_and_exact_binding(self):
        receipts = []
        orchestrator = ordering.Follow60OrderingV2OrchestratorV1(
            ledger_apply=lambda stage, payload: receipts.append((stage, payload)) or {"ok": True}
        )
        out = orchestrator.execute_post_first(
            _plan(mute_required=False),
            post_like_engine=lambda _ctx: {
                "post_opened": True,
                "liked_count": 1,
                "like_action_state": "LIKE_ACTION_PERFORMED_NOW",
                "real_tap_sent": True,
                "fresh_like_verified": True,
            },
        )
        self.assertTrue(out["ok"])
        stage, receipt = receipts[-1]
        self.assertEqual("like_verified", stage)
        self.assertEqual("bilalmnakhry", receipt["candidate_username"])
        self.assertEqual("mythyl-action-18", receipt["action_id"])
        self.assertTrue(receipt["fresh_like_verified"])

    def test_positive_count_without_current_action_evidence_is_not_a_like(self):
        receipts = []
        orchestrator = ordering.Follow60OrderingV2OrchestratorV1(
            ledger_apply=lambda stage, payload: receipts.append((stage, payload)) or {"ok": True}
        )
        out = orchestrator.execute_post_first(
            _plan(mute_required=False),
            post_like_engine=lambda _ctx: {"post_opened": True, "liked_count": 1},
        )
        self.assertFalse(out["ok"])
        self.assertNotIn("like_verified", [stage for stage, _ in receipts])

    def test_terminal_binding_distinguishes_action_from_safe_skip(self):
        performed = {
            "liked_count": 1,
            "like_action_state": "LIKE_ACTION_PERFORMED_NOW",
            "real_tap_sent": True,
            "fresh_like_verified": True,
        }
        self.assertEqual(
            ("verified", "like_verified"),
            nav._post_follow_like_terminal_binding(performed, {"like_verified": True}),
        )
        already = {
            "liked_count": 0,
            "phase_outcome": "skipped",
            "ok": True,
            "like_action_state": "POST_ALREADY_LIKED_NO_ACTION",
        }
        self.assertEqual(
            ("safe_skip", "like_safe_skip"),
            nav._post_follow_like_terminal_binding(already, {}),
        )

    def test_ledger_rejects_like_without_exact_current_action_evidence(self):
        scope = LedgerScope(
            account_id="account-point-c",
            run_id="run-point-c",
            request_id="request-point-c",
            business_session_id="session-point-c",
            target_id="target-point-c",
            action_id="mythyl-action-18",
            candidate_username="bilalmnakhry",
        )
        ledger = OrderingLedger(scope)
        ledger.apply_receipt("profile_certified", {"proof": "fresh"})
        ledger.apply_receipt("post_opened", {"method": "SAFE"})

        invalid = {
            "liked_count": 1,
            "like_action_state": "POST_ALREADY_LIKED_NO_ACTION",
            "real_tap_sent": False,
            "fresh_like_verified": True,
            "candidate_username": "bilalmnakhry",
            "action_id": "mythyl-action-18",
            "stable_proof_hash": "proof-point-c",
            "media_binding": "absolute_row_1_column_1_unique",
        }
        with self.assertRaisesRegex(ValueError, "ledger_like_verified_without_current_action"):
            ledger.apply_receipt("like_verified", invalid)

        valid = {
            **invalid,
            "like_action_state": "LIKE_ACTION_PERFORMED_NOW",
            "real_tap_sent": True,
        }
        inserted = ledger.apply_receipt("like_verified", valid)
        self.assertTrue(inserted["inserted"])
        replay = ledger.apply_receipt("like_verified", valid)
        self.assertTrue(replay["duplicate"])

    def test_wrong_candidate_binding_cannot_create_like_receipt(self):
        scope = LedgerScope(
            account_id="account-point-c",
            run_id="run-point-c",
            request_id="request-point-c",
            business_session_id="session-point-c",
            target_id="target-point-c",
            action_id="mythyl-action-18",
            candidate_username="bilalmnakhry",
        )
        ledger = OrderingLedger(scope)
        ledger.apply_receipt("profile_certified", {"proof": "fresh"})
        ledger.apply_receipt("post_opened", {"method": "SAFE"})
        with self.assertRaisesRegex(ValueError, "ledger_like_verified_candidate_binding_mismatch"):
            ledger.apply_receipt(
                "like_verified",
                {
                    "liked_count": 1,
                    "like_action_state": "LIKE_ACTION_PERFORMED_NOW",
                    "real_tap_sent": True,
                    "fresh_like_verified": True,
                    "candidate_username": "wrong_candidate",
                    "action_id": "mythyl-action-18",
                    "stable_proof_hash": "proof-point-c",
                    "media_binding": "absolute_row_1_column_1_unique",
                },
            )


if __name__ == "__main__":
    unittest.main()
