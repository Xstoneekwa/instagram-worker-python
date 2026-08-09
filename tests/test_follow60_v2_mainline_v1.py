import time
import unittest
import uuid
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
