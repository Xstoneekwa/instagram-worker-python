from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import control_plane_health as health
import supabase_client


class Clock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class ControlPlaneReliabilityV1ChaosMatrix(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "control-plane-health.json"
        self.clock = Clock()

    def breaker(self) -> health.CircuitBreaker:
        return health.CircuitBreaker(
            path=self.path,
            monotonic=self.clock,
            failure_threshold=3,
            probe_interval_seconds=2,
            recovery_stabilization_seconds=2,
        )

    def test_01_missing_state_starts_recovering(self) -> None:
        self.assertEqual(self.breaker().snapshot.state, health.RECOVERING)

    def test_02_previous_green_is_never_trusted_on_startup(self) -> None:
        self.path.write_text(json.dumps({
            "state": "HEALTHY", "reason_family": "old", "consecutive_failures": 0,
            "next_probe_at": 0, "changed_at": "old", "generation": 7,
        }))
        current = self.breaker().snapshot
        self.assertEqual(current.state, health.RECOVERING)
        self.assertEqual(current.generation, 8)

    def test_03_corrupt_state_starts_recovering(self) -> None:
        self.path.write_text("not-json")
        self.assertEqual(self.breaker().snapshot.state, health.RECOVERING)

    def test_04_first_transient_failure_is_degraded(self) -> None:
        breaker = self.breaker()
        breaker.record_probe_success()
        self.clock.advance(2)
        breaker.record_probe_success()
        self.assertEqual(breaker.record_failure("timeout").state, health.DEGRADED)

    def test_05_third_consecutive_failure_is_unavailable(self) -> None:
        breaker = self.breaker()
        self.assertEqual(breaker.record_failure("timeout").state, health.DEGRADED)
        self.assertEqual(breaker.record_failure("timeout").state, health.DEGRADED)
        self.assertEqual(breaker.record_failure("timeout").state, health.UNAVAILABLE)

    def test_06_unavailable_forbids_dispatch(self) -> None:
        breaker = self.breaker()
        for _ in range(3):
            breaker.record_failure("dns")
        self.assertFalse(breaker.snapshot.dispatch_allowed)

    def test_07_first_positive_probe_is_recovering(self) -> None:
        breaker = self.breaker()
        snapshot, transitioned = breaker.record_probe_success()
        self.assertEqual(snapshot.state, health.RECOVERING)
        self.assertFalse(transitioned)

    def test_08_second_probe_before_two_seconds_stays_recovering(self) -> None:
        breaker = self.breaker()
        breaker.record_probe_success()
        self.clock.advance(1.9)
        snapshot, transitioned = breaker.record_probe_success()
        self.assertEqual(snapshot.state, health.RECOVERING)
        self.assertFalse(transitioned)

    def test_09_second_fresh_probe_transitions_healthy(self) -> None:
        breaker = self.breaker()
        breaker.record_probe_success()
        self.clock.advance(2)
        snapshot, transitioned = breaker.record_probe_success()
        self.assertEqual(snapshot.state, health.HEALTHY)
        self.assertTrue(transitioned)

    def test_10_healthy_transition_signal_is_one_shot(self) -> None:
        breaker = self.breaker()
        breaker.record_probe_success()
        self.clock.advance(2)
        self.assertTrue(breaker.record_probe_success()[1])
        self.assertFalse(breaker.record_probe_success()[1])

    def test_11_durable_file_has_exact_public_fields(self) -> None:
        self.breaker()
        payload = json.loads(self.path.read_text())
        self.assertEqual(set(payload), {
            "state", "reason_family", "consecutive_failures", "next_probe_at",
            "changed_at", "generation",
        })

    def test_12_transient_classifier_excludes_contract_failures(self) -> None:
        transient = supabase_client.SupabaseRestError("supabase_network_timeout", status=503)
        contract = supabase_client.SupabaseRestError(
            "supabase_rpc_business_rule_rejected", status=500
        )
        self.assertTrue(supabase_client.is_transient_control_plane_exception(transient))
        self.assertFalse(supabase_client.is_transient_control_plane_exception(contract))

    def test_13_sql_contract_has_bounded_idempotent_zero_work_recovery(self) -> None:
        sql = (Path(__file__).parents[1] / "supabase/migrations/20260826021814_control_plane_reliability_v1.sql").read_text()
        self.assertIn("execution_attempt_no between 1 and 3", sql)
        self.assertIn("control-plane-zero-work:", sql)
        self.assertIn("irreversible_work_state <> 'PRE_DEVICE'", sql)
        self.assertIn("on conflict (device_id) do update", sql)


if __name__ == "__main__":
    unittest.main()
