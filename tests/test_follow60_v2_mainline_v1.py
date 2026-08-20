import json
import time
import unittest
import uuid
from pathlib import Path
import account_session_orchestrator as account_session
from follow_outcome_contract import (
    build_follow_termination_decision,
    validate_follow_termination_decision,
)

from follow60_business_session_binding_v1 import (
    build_candidate_stage_binding,
    create_mainline_business_session_binding,
)
from follow60_mainline_v2 import (
    CANARY_HARNESS_INHERITED_BY_NORMAL_ACCOUNTS,
    DEFAULT_FOLLOW_ENGINE,
    NORMAL_RUN_HAS_TEN_CYCLE_BARRIER,
    NORMAL_RUN_REQUIRES_CANARY_CONTROL,
    default_follow_engine,
)
from follow60_ordering_v2_behavioral_canary_v1 import (
    BehavioralCanaryBindingV1,
    CANARY_BINDING_KIND,
    CANARY_TYPE,
    MAINLINE_BINDING_KIND,
    MAINLINE_SCHEMA,
    build_mainline_ordering_v2_binding,
    route_candidate_v2,
)
from follow60_ordering_v2_ledger_v1 import LedgerScope, OrderingLedger


ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 40
POINT_A_FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "follow60_field_fixture_mythyl_exit53_count1.json"
)


class Follow60V2MainlineV1Tests(unittest.TestCase):
    def binding(self):
        return create_mainline_business_session_binding(
            business_session_id=str(uuid.uuid4()),
            account_id=str(uuid.uuid4()),
            request_id=str(uuid.uuid4()),
            run_id=str(uuid.uuid4()),
            attempt_id=1,
            worker_sha=SHA,
        )

    def test_global_default_is_account_and_package_neutral(self):
        self.assertEqual("FOLLOW60_V2_MAINLINE_V1", DEFAULT_FOLLOW_ENGINE)
        self.assertEqual(DEFAULT_FOLLOW_ENGINE, default_follow_engine())
        self.assertEqual(
            DEFAULT_FOLLOW_ENGINE,
            default_follow_engine(account_id=str(uuid.uuid4()), package="premium"),
        )
        self.assertFalse(CANARY_HARNESS_INHERITED_BY_NORMAL_ACCOUNTS)
        self.assertFalse(NORMAL_RUN_REQUIRES_CANARY_CONTROL)
        self.assertFalse(NORMAL_RUN_HAS_TEN_CYCLE_BARRIER)

    def test_mainline_binding_uses_business_session_without_control(self):
        source = self.binding()
        bound, reason = build_mainline_ordering_v2_binding(
            source.to_dict(),
            account_id=source.account_id,
            run_id=source.run_id,
            request_id=source.request_id,
            business_session_id=source.business_session_id,
            attempt_id=source.attempt_id,
            worker_sha=source.worker_sha,
            completed_v2_cycles=999,
        )
        self.assertEqual("v2_mainline_binding_valid", reason)
        self.assertIsNotNone(bound)
        self.assertEqual(MAINLINE_SCHEMA, bound.schema)
        self.assertEqual(MAINLINE_BINDING_KIND, bound.binding_kind)
        self.assertEqual(999, bound.v2_complete_count)
        route, route_reason = route_candidate_v2(
            binding=bound,
            stable_proof=None,
            completed_v2_cycles=10000,
        )
        self.assertEqual("FOLLOW60_V1", route)
        self.assertEqual("candidate_not_direct_grid_safe", route_reason)

    def test_mainline_binding_rejects_any_identity_mismatch(self):
        source = self.binding()
        bound, reason = build_mainline_ordering_v2_binding(
            source.to_dict(),
            account_id=str(uuid.uuid4()),
            run_id=source.run_id,
            request_id=source.request_id,
            business_session_id=source.business_session_id,
            attempt_id=source.attempt_id,
            worker_sha=source.worker_sha,
        )
        self.assertIsNone(bound)
        self.assertEqual("business_session_account_mismatch", reason)

    def test_historical_canary_barrier_remains_fail_closed(self):
        canary = BehavioralCanaryBindingV1(
            schema=CANARY_TYPE,
            control_id=str(uuid.uuid4()),
            account_id=str(uuid.uuid4()),
            run_id=str(uuid.uuid4()),
            request_id=str(uuid.uuid4()),
            business_session_id=str(uuid.uuid4()),
            attempt_id=1,
            expected_worker_sha=SHA,
            actual_worker_sha=SHA,
            canary_type=CANARY_TYPE,
            binding_kind=CANARY_BINDING_KIND,
            max_new_cycles=10,
            baseline_follow_count=0,
            expires_at_epoch_s=time.time() + 60,
            lease_id=str(uuid.uuid4()),
            lease_nonce="nonce",
            lease_expires_at_epoch_s=time.time() + 60,
            claimed_at_epoch_s=time.time(),
            candidate_seen_count=0,
            v2_selected_count=0,
            v2_complete_count=10,
            v2_partial_count=0,
            v1_fallback_count=0,
            status="running",
        )
        self.assertEqual(
            ("CANARY_BARRIER_REACHED", "v2_cycle_barrier_reached"),
            route_candidate_v2(binding=canary, stable_proof=None),
        )

    def test_runner_normal_mainline_does_not_claim_behavioral_control(self):
        source = (ROOT / "runner.py").read_text(encoding="utf-8")
        start = source.index("if supabase_mode and run_request_id and not _follow60_canary_active:")
        end = source.index("if supabase_mode and run_id:", start)
        normal_bootstrap = source[start:end]
        self.assertIn("follow60_v2_mainline_runtime_binding_materialized", normal_bootstrap)
        self.assertNotIn("claim_follow60_ordering_v2_behavioral_binding_v1", normal_bootstrap)
        self.assertIn("follow60_mainline_active", source)
        self.assertIn("or (", source[source.index("_follow60_stage_receipts = bool("):])

    def test_true_mainline_multi_candidate_session(self):
        """Exercise successive complete cycles without any canary carrier."""

        source = self.binding()
        session_fields = {
            "account_id": source.account_id,
            "run_id": source.run_id,
            "request_id": source.request_id,
            "business_session_id": source.business_session_id,
        }
        idempotency_keys: set[str] = set()

        for index, candidate in enumerate(("alpha", "bravo", "charlie"), start=1):
            action_id = str(uuid.uuid4())
            candidate_binding = build_candidate_stage_binding(
                source,
                action_id=action_id,
                candidate_username=candidate,
                source_target_id="target-mainline",
            )
            for field, expected in session_fields.items():
                self.assertEqual(expected, candidate_binding[field])

            bound, reason = build_mainline_ordering_v2_binding(
                source.to_dict(),
                account_id=source.account_id,
                run_id=source.run_id,
                request_id=source.request_id,
                business_session_id=source.business_session_id,
                attempt_id=source.attempt_id,
                worker_sha=source.worker_sha,
                completed_v2_cycles=10 + index,
            )
            self.assertEqual("v2_mainline_binding_valid", reason)
            self.assertIsNotNone(bound)
            self.assertEqual(MAINLINE_BINDING_KIND, bound.binding_kind)
            self.assertEqual(0, bound.max_new_cycles)

            ledger = OrderingLedger(
                LedgerScope(
                    **session_fields,
                    target_id="target-mainline",
                    action_id=action_id,
                    candidate_username=candidate,
                )
            )
            for stage, payload in (
                ("profile_certified", {"exact": True}),
                ("post_opened", {"v5": True}),
                ("like_verified", {"verified": True}),
                ("profile_reentry_verified", {"exact": True}),
                ("follow_pending", {"reserved": True}),
                ("follow_verified", {"state": "following"}),
                ("mute_posts_verified", {"verified": True}),
                ("mute_stories_verified", {"verified": True}),
                ("return_ct_exact", {"exact": True}),
                ("cycle_complete", {"persisted": True}),
            ):
                receipt = ledger.apply_receipt(stage, payload)
                idempotency_keys.add(receipt["idempotency_key"])
            self.assertTrue(ledger.cycle_complete)

            route, route_reason = route_candidate_v2(
                binding=bound,
                stable_proof=None,
                completed_v2_cycles=10_000 + index,
            )
            self.assertEqual("FOLLOW60_V1", route)
            self.assertEqual("candidate_not_direct_grid_safe", route_reason)

        self.assertEqual(30, len(idempotency_keys))

    def _point_a_decision(self, follows_completed_count: int) -> dict:
        fixture = json.loads(POINT_A_FIXTURE.read_text(encoding="utf-8"))
        return build_follow_termination_decision(
            exit_code=fixture["exit_code"],
            first_causal_reason=fixture["first_causal_reason"],
            follows_completed_count=follows_completed_count,
            target_follow_budget_effective=120,
            target_attribution={
                "account_id": "00000000-0000-4000-8000-000000000001",
                "run_id": fixture["run_id"],
                "request_id": "00000000-0000-4000-8000-000000000002",
                "candidate_username": fixture["candidate_username"],
                "source_profile_username": fixture["source_profile_username"],
            },
            physical_follow_preserved=True,
            canonical_follow_receipt_present=True,
            candidate_local_failure=True,
            post_follow_recovery_required=True,
            no_new_follow_until_recovered=True,
            safe_boundary=True,
            safe_next_step="handoff_to_unfollow",
        )

    def test_point_a_real_serialization_to_orchestrator_and_unfollow_handoff(self):
        decision = self._point_a_decision(1)
        serialized_summary = json.loads(
            json.dumps(
                {
                    "follow_termination_decision": decision,
                    "follow_outcome": decision,
                    "follows_completed_count": 1,
                    "exit_code": 53,
                }
            )
        )
        consumed = account_session._authoritative_follow_termination_decision(
            exit_code=53,
            summary=serialized_summary,
        )
        self.assertTrue(consumed["authoritative"])
        self.assertTrue(consumed["accepted"])
        self.assertFalse(consumed["rotation_allowed"])
        self.assertFalse(consumed["follow_retap_allowed"])
        gate = account_session._evaluate_h3_follow_exit_code_gate(
            account_id="00000000-0000-4000-8000-000000000001",
            account_username="mythyl_fitness",
            follow_exit_code=53,
            diagnostic={
                "follow_outcome": consumed["follow_outcome"],
                "handoff_would_run": True,
                "unfollow_enabled": True,
                "unfollow_mode": "unfollow-any",
                "pending_unfollow_count": 52,
            },
            real_max_actions_effective=36,
        )
        self.assertTrue(gate["follow_exit_code_allowed"])

    def test_point_a_real_outer_rotation_stops_before_a_new_candidate(self):
        decision = self._point_a_decision(1)

        def run_followers(_device, **_kwargs):
            run_followers.last_session_summary = json.loads(
                json.dumps(
                    {
                        "follow_session_outcome": "partial_resumable",
                        "follow_stop_reason": "post_follow_required_mute_incomplete",
                        "first_causal_reason": "following_button_not_found",
                        "follows_completed_count": 1,
                        "follow_processed_count": 1,
                        "follow_termination_decision": decision,
                        "follow_outcome": decision,
                    }
                )
            )
            return 53

        run_followers.last_session_summary = {}
        result = account_session._run_follow_target_rotation(
            object(),
            account_id="00000000-0000-4000-8000-000000000001",
            account_username="mythyl_fitness",
            run_id="910e7bab-7ce7-4ac0-b061-37780e91b4c7",
            follow_targets=[
                {
                    "target_id": "00000000-0000-4000-8000-000000000003",
                    "source_profile": "sanitized_ct",
                    "target_index": 0,
                },
                {
                    "target_id": "00000000-0000-4000-8000-000000000004",
                    "source_profile": "must_not_start",
                    "target_index": 1,
                },
            ],
            run_followers_list_engine_session=run_followers,
            supabase_mode=False,
            warm_session_used=False,
            force_stop_used=False,
            max_targets_per_run=2,
            max_follows_per_target_per_run=120,
        )
        self.assertEqual(53, result["exit_code"])
        self.assertEqual(1, len(result["attempts"]))
        self.assertEqual("partial_resumable", result["summary"]["phase_status"])
        self.assertEqual(
            "handoff_to_unfollow", result["summary"]["safe_next_step"]
        )
        self.assertFalse(result["summary"]["target_rotation_allowed"])
        self.assertFalse(result["summary"]["follow_retap_allowed"])

    def test_point_a_decision_is_count_independent_after_durable_follow(self):
        for count in (1, 2, 10, 30, 80, 120):
            with self.subTest(count=count):
                decision = json.loads(json.dumps(self._point_a_decision(count)))
                valid, reason = validate_follow_termination_decision(decision)
                self.assertTrue(valid, reason)
                consumed = account_session._authoritative_follow_termination_decision(
                    exit_code=53,
                    summary={"follow_termination_decision": decision},
                )
                self.assertTrue(consumed["accepted"])

    def test_point_a_missing_or_malformed_decision_fails_closed(self):
        for summary in (
            {},
            {"follow_termination_decision": {"phase_status": "partial_resumable"}},
        ):
            with self.subTest(summary=summary):
                consumed = account_session._authoritative_follow_termination_decision(
                    exit_code=53,
                    summary=summary,
                )
                self.assertTrue(consumed["authoritative"])
                self.assertFalse(consumed["accepted"])
                self.assertEqual(
                    "follow_termination_decision_invalid",
                    consumed["reason"],
                )

    def test_point_a_zero_mutation_legacy_safety_is_preserved(self):
        base = {
            "target_local_failure": True,
            "target_safe_to_skip": True,
            "target_completed": False,
            "follow_session_outcome": "target_recovery_failed",
        }
        zero = account_session._follow_target_local_failure_contract(
            exit_code=44,
            summary={**base, "follows_completed_count": 0},
        )
        mutated = account_session._follow_target_local_failure_contract(
            exit_code=44,
            summary={**base, "follows_completed_count": 1},
        )
        self.assertTrue(zero["target_safe_to_skip"])
        self.assertFalse(mutated["target_safe_to_skip"])

    def test_point_a_global_safety_still_blocks_unfollow(self):
        decision = self._point_a_decision(1)
        for global_safety_reason in (
            "challenge_required",
            "instagram_account_restriction",
            "active_instagram_account_mismatch",
            "blocked_lease_invalid",
        ):
            with self.subTest(global_safety_reason=global_safety_reason):
                gate = account_session._evaluate_h3_follow_exit_code_gate(
                    account_id="00000000-0000-4000-8000-000000000001",
                    account_username="mythyl_fitness",
                    follow_exit_code=53,
                    diagnostic={
                        "follow_outcome": decision,
                        "handoff_would_run": True,
                        "unfollow_enabled": True,
                        "unfollow_mode": "unfollow-any",
                        "pending_unfollow_count": 52,
                        "global_safety_reason": global_safety_reason,
                    },
                    real_max_actions_effective=36,
                )
                self.assertFalse(gate["follow_exit_code_allowed"])
                self.assertTrue(gate["follow_exit_code_block_reason"])

        persistence_unavailable = dict(decision)
        persistence_unavailable["canonical_follow_receipt_present"] = False
        gate = account_session._evaluate_h3_follow_exit_code_gate(
            account_id="00000000-0000-4000-8000-000000000001",
            account_username="mythyl_fitness",
            follow_exit_code=53,
            diagnostic={
                "follow_outcome": persistence_unavailable,
                "handoff_would_run": True,
                "unfollow_enabled": True,
                "unfollow_mode": "unfollow-any",
                "pending_unfollow_count": 52,
            },
            real_max_actions_effective=36,
        )
        self.assertFalse(gate["follow_exit_code_allowed"])
        self.assertEqual(
            "follow_termination_decision_invalid",
            gate["follow_exit_code_block_reason"],
        )


if __name__ == "__main__":
    unittest.main()
