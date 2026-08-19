from __future__ import annotations

import json
import unittest
from pathlib import Path

import account_session_orchestrator as account_session
import post_follow_stage_outbox
from worker_runtime_identity import WorkerRuntimeIdentity


FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "follow60_production_stability_closure_v1.json"
)


def _partial_flush() -> dict[str, object]:
    return {
        "ok": False,
        "reason": "follow60_candidate_local_post_follow_recovery_required",
        "candidate_local": True,
        "partial_resumable": True,
        "partial_receipts_persisted": True,
        "retained_for_idempotent_recovery": True,
        "persisted_stages": ["mute_stories_verified", "return_ct_exact"],
        "missing_required_stages": ["mute_posts_verified"],
        "action_id_hash": "a" * 64,
        "candidate_username": "sanitized_candidate",
    }


def _partial_outcome() -> dict[str, object]:
    return {
        "phase_status": "partial_resumable",
        "scope": "follow_phase",
        "safe_boundary": True,
        "candidate_local_failure": True,
        "post_follow_recovery_required": True,
        "safe_next_step": "handoff_to_unfollow",
        "no_new_follow_until_recovered": True,
    }


class Follow60ProductionStabilityClosureV1Test(unittest.TestCase):
    def test_follow_persisted_partial_receipts_are_local_and_never_retap(self) -> None:
        classified = post_follow_stage_outbox.classify_post_follow_flush(
            _partial_flush(), canonical_follow_persisted=True
        )
        self.assertEqual(
            classified["failure_class"],
            "target_local_follow_durable_post_follow_pending",
        )
        self.assertTrue(classified["partial_resumable"])
        self.assertFalse(classified["follow_retap_allowed"])
        self.assertEqual(classified["safe_next_step"], "handoff_to_unfollow")

    def test_return_ok_shadow_flag_is_not_part_of_authoritative_classification(self) -> None:
        flush = _partial_flush()
        flush["return_ok"] = False
        classified = post_follow_stage_outbox.classify_post_follow_flush(
            flush, canonical_follow_persisted=True
        )
        self.assertTrue(classified["candidate_local"])
        self.assertTrue(classified["safe_boundary"])

    def test_canonical_follow_persistence_failure_remains_fail_closed(self) -> None:
        classified = post_follow_stage_outbox.classify_post_follow_flush(
            _partial_flush(), canonical_follow_persisted=False
        )
        self.assertEqual(
            classified["failure_class"], "canonical_follow_persistence_failure"
        )
        self.assertFalse(classified["candidate_local"])
        self.assertEqual(classified["safe_next_step"], "stop_fail_closed")
        self.assertFalse(classified["follow_retap_allowed"])

    def test_unproved_partial_receipt_cannot_be_downgraded_to_local(self) -> None:
        flush = _partial_flush()
        flush["persisted_stages"] = ["mute_stories_verified"]
        classified = post_follow_stage_outbox.classify_post_follow_flush(
            flush, canonical_follow_persisted=True
        )
        self.assertEqual(
            classified["failure_class"],
            "post_follow_persistence_unclassified_fail_closed",
        )
        self.assertFalse(classified["safe_boundary"])

    def test_exit_53_exact_contract_can_handoff_to_unfollow(self) -> None:
        gate = account_session._evaluate_h3_follow_exit_code_gate(
            account_id="00000000-0000-4000-8000-000000000001",
            account_username="sanitized_account",
            follow_exit_code=53,
            diagnostic={
                "follow_outcome": _partial_outcome(),
                "handoff_would_run": True,
                "unfollow_enabled": True,
                "unfollow_mode": "unfollow-any",
                "pending_unfollow_count": 1,
            },
            real_max_actions_effective=1,
        )
        self.assertTrue(gate["follow_exit_code_allowed"])
        self.assertEqual(
            gate["follow_exit_code_allow_reason"],
            "follow_candidate_local_post_follow_partial_safe_for_unfollow",
        )

    def test_exit_53_without_exact_contract_is_blocked(self) -> None:
        gate = account_session._evaluate_h3_follow_exit_code_gate(
            account_id="00000000-0000-4000-8000-000000000001",
            account_username="sanitized_account",
            follow_exit_code=53,
            diagnostic={
                "follow_outcome": {"phase_status": "partial_resumable"},
                "handoff_would_run": True,
                "unfollow_enabled": True,
                "unfollow_mode": "unfollow-any",
                "pending_unfollow_count": 1,
            },
            real_max_actions_effective=1,
        )
        self.assertFalse(gate["follow_exit_code_allowed"])
        self.assertEqual(
            gate["follow_exit_code_block_reason"],
            "follow_candidate_local_partial_contract_unproved",
        )

    def test_exit_53_still_blocks_account_or_platform_safety_markers(self) -> None:
        gate = account_session._evaluate_h3_follow_exit_code_gate(
            account_id="00000000-0000-4000-8000-000000000001",
            account_username="sanitized_account",
            follow_exit_code=53,
            diagnostic={
                "follow_outcome": _partial_outcome(),
                "handoff_would_run": True,
                "unfollow_enabled": True,
                "unfollow_mode": "unfollow-any",
                "pending_unfollow_count": 1,
                "platform_state": "challenge",
            },
            real_max_actions_effective=1,
        )
        self.assertFalse(gate["follow_exit_code_allowed"])
        self.assertIn("unsafe_follow_signal_challenge", gate["follow_exit_code_block_reason"])

    def test_real_runtime_identity_type_is_exercised(self) -> None:
        identity = WorkerRuntimeIdentity(
            runtime_root="/sanitized/release",
            worker_sha="b" * 40,
            source="production_stability_fixture",
        )
        self.assertTrue(identity.runtime_root_ok)
        self.assertEqual(identity.worker_sha, "b" * 40)
        self.assertFalse(hasattr(identity, "full_sha"))

    def test_authoritative_incident_fixture_suite_contains_all_fourteen_cases(self) -> None:
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema"], "PHONE_FARM_PRODUCTION_INCIDENT_FIXTURES_V1")
        cases = payload["cases"]
        self.assertEqual(len(cases), 14)
        self.assertEqual(len({case["id"] for case in cases}), 14)
        self.assertIn(
            "follow_partial_to_unfollow", {case["id"] for case in cases}
        )


if __name__ == "__main__":
    unittest.main()
