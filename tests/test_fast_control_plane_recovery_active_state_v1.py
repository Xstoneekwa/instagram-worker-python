from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest import mock

import account_run_control
import auto_restart_device_lock
import control_plane_health as health
import supabase_client


class Clock:
    def __init__(self) -> None:
        self.now = 10.0

    def __call__(self) -> float:
        return self.now


class FastRecoveryPolicyTests(unittest.TestCase):
    def test_startup_evidence_migration_keeps_active_boundary_fail_closed(self) -> None:
        sql = (Path(__file__).parents[1] / "supabase/migrations/20260826134332_fast_recovery_active_state_v1.sql").read_text()
        for column in (
            "worker_spawned_at", "runner_started_at", "device_activity_started_at",
            "device_connected_at", "instagram_launch_requested_at",
            "instagram_foreground_verified_at",
        ):
            self.assertIn(column, sql)
        self.assertIn("irreversible_work_state <> 'STARTED_OR_AMBIGUOUS'", sql)
        self.assertIn("p_foreground_package is distinct from v_plan.expected_package", sql)
        self.assertIn("execution_attempt_no", (Path(__file__).parents[1] / "supabase/migrations/20260826021814_control_plane_reliability_v1.sql").read_text())

    def test_first_bounded_transient_failure_pauses_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            clock = Clock()
            breaker = health.CircuitBreaker(path=Path(tmp) / "health.json", monotonic=clock)
            breaker.record_probe_success()
            clock.now += 2.0
            breaker.record_probe_success()
            snapshot = breaker.record_failure("supabase_network_timeout")
            self.assertEqual(snapshot.state, health.DEGRADED)
            self.assertFalse(snapshot.dispatch_allowed)
            self.assertFalse(breaker.probe_due())
            clock.now += 2.0
            self.assertTrue(breaker.probe_due())

    def test_reclaim_is_single_five_second_rpc_attempt(self) -> None:
        with mock.patch.object(supabase_client, "call_rpc_once", return_value=2) as call:
            self.assertEqual(account_run_control.reclaim_stale_account_run_requests("w"), 2)
        call.assert_called_once_with(
            "reclaim_stale_account_run_requests", {"p_worker_id": "w"}, timeout_seconds=5.0
        )

    def test_reclaim_lost_response_uses_canonical_read_not_post_replay(self) -> None:
        with (
            mock.patch.object(supabase_client, "call_rpc_once", side_effect=TimeoutError("lost")) as once,
            mock.patch.object(supabase_client, "_request_json", return_value=[]) as read,
        ):
            self.assertEqual(account_run_control.reclaim_stale_account_run_requests("w"), 0)
        once.assert_called_once()
        read.assert_called_once()

    def test_safe_stop_lost_response_is_reconciled_without_post_replay(self) -> None:
        with (
            mock.patch.object(supabase_client, "call_rpc_once", side_effect=TimeoutError("lost")) as once,
            mock.patch.object(supabase_client, "load_zero_work_capsule_v1", return_value={
                "run_request_id": "request-1",
                "irreversible_work_state": "PRE_DEVICE",
                "resume_state": "recovery_enqueued",
            }),
        ):
            out = supabase_client.mark_pre_device_safe_stop_v1(
                run_id="run-1", request_id="request-1", worker_id="w", reason_code="network"
            )
        self.assertTrue(out["reconciled"])
        once.assert_called_once()

    def test_foreground_certificate_requires_canonical_reconciliation_after_lost_response(self) -> None:
        with (
            mock.patch.object(supabase_client, "call_rpc_once", side_effect=TimeoutError("lost")) as once,
            mock.patch.object(supabase_client, "load_zero_work_capsule_v1", return_value={
                "run_request_id": "request-1",
                "irreversible_work_state": "STARTED_OR_AMBIGUOUS",
                "device_connected_at": "2026-08-26T00:00:01Z",
                "instagram_foreground_verified_at": "2026-08-26T00:00:02Z",
            }),
        ):
            out = supabase_client.certify_account_startup_foreground_v1(
                run_id="run-1", request_id="request-1", worker_id="w",
                foreground_package="com.instagram.android", timestamps={},
            )
        self.assertTrue(out["reconciled"])
        once.assert_called_once()

    def test_lock_renew_and_release_use_single_attempt_then_canonical_read(self) -> None:
        with (
            mock.patch.object(supabase_client, "call_rpc_once", side_effect=TimeoutError("lost")) as once,
            mock.patch.object(auto_restart_device_lock, "_load_device_lock", return_value={
                "worker_id": "w", "request_id": "request-1",
            }),
        ):
            renewed = auto_restart_device_lock.renew_device_lock(
                device_id="device-1", worker_id="w", request_id="request-1"
            )
        self.assertTrue(renewed["reconciled"])
        once.assert_called_once()


if __name__ == "__main__":
    unittest.main()
