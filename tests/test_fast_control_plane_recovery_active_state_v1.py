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

    def test_lock_renew_missing_token_is_not_reconciled(self) -> None:
        with mock.patch.object(supabase_client, "call_rpc_once") as once, mock.patch.object(auto_restart_device_lock, "_load_device_lock") as read:
            result = auto_restart_device_lock.renew_device_lock(device_id="device-1", worker_id="w", request_id="request-1")
        self.assertFalse(result["ok"])
        self.assertFalse(result["reconciled"])
        once.assert_not_called()
        read.assert_not_called()

    def test_lock_renew_lost_response_requires_exact_generation(self) -> None:
        from datetime import datetime, timedelta, timezone
        token = "11111111-1111-4111-8111-111111111111"
        expected = dict(device_id="device-1", worker_id="w", request_id="request-1", account_id="account-1", lease_id=token)
        variants = ({}, {"lease_id": "22222222-2222-4222-8222-222222222222"}, {"lease_id": None}, {"account_id": "account-2"}, {"request_id": "request-2"}, {"worker_id": "other-worker"}, {"device_id": "device-2"})
        for delta in variants:
            with self.subTest(delta=delta):
                def canonical_read(*args):
                    now = datetime.now(timezone.utc)
                    return {**expected, "heartbeat_at": now.isoformat(), "lease_expires_at": (now + timedelta(minutes=15)).isoformat(), **delta}
                with mock.patch.object(supabase_client, "call_rpc_once", side_effect=TimeoutError("lost")) as once, mock.patch.object(auto_restart_device_lock, "_load_device_lock", side_effect=canonical_read) as read:
                    result = auto_restart_device_lock.renew_device_lock(device_id="device-1", worker_id="w", request_id="request-1", account_id="account-1", expected_lease_id=token)
                self.assertEqual(result["reconciled"], not delta)
                self.assertEqual(result["ok"], not delta)
                once.assert_called_once()
                self.assertEqual(once.call_args.args[1]["p_expected_lease_id"], token)
                read.assert_called_once()

    def test_lock_renew_old_heartbeat_does_not_prove_renewal(self) -> None:
        token = "11111111-1111-4111-8111-111111111111"
        row = dict(device_id="device-1", worker_id="w", request_id="request-1", account_id="account-1", lease_id=token, heartbeat_at="2000-01-01T00:00:00+00:00", lease_expires_at="2099-01-01T00:00:00+00:00")
        with mock.patch.object(supabase_client, "call_rpc_once", side_effect=TimeoutError("lost")), mock.patch.object(auto_restart_device_lock, "_load_device_lock", return_value=row):
            result = auto_restart_device_lock.renew_device_lock(device_id="device-1", worker_id="w", request_id="request-1", account_id="account-1", expected_lease_id=token)
        self.assertFalse(result["reconciled"])

    def test_lock_renew_success_response_must_bind_exact_token(self) -> None:
        token = "11111111-1111-4111-8111-111111111111"
        row = dict(ok=True, renewed=True, device_id="device-1", worker_id="w", request_id="request-1", account_id="account-1", lease_id=token)
        for payload, accepted in ((row, True), ({**row, "lease_id": "22222222-2222-4222-8222-222222222222"}, False), ({"ok": True, "renewed": True}, False)):
            with self.subTest(payload=payload), mock.patch.object(supabase_client, "call_rpc_once", return_value=payload), mock.patch.object(auto_restart_device_lock, "_load_device_lock") as read:
                result = auto_restart_device_lock.renew_device_lock(device_id="device-1", worker_id="w", request_id="request-1", account_id="account-1", expected_lease_id=token)
            self.assertEqual(result["ok"], accepted)
            read.assert_not_called()



    def test_lock_renew_canonical_order_concurrent(self) -> None:
        """Exercise the real renewal RPC against the admission lock boundary."""
        import json
        import pathlib
        import subprocess
        import tempfile
        import time

        pg = pathlib.Path("/opt/homebrew/opt/postgresql@17/bin")
        migration = pathlib.Path("/Users/admin/Projects/backend-fleet-control-v1/supabase/migrations/20260906171034_fleet_admission_authority_v1.sql").read_text()
        marker = "create or replace function public.auto_restart_renew_device_lock("
        renewal = marker + migration.rsplit(marker, 1)[1]
        renewal = renewal.split("$device_renew_fenced$;", 1)[0] + "$device_renew_fenced$;"
        device = "30000000-0000-4000-8000-000000000001"
        account = "10000000-0000-4000-8000-000000000001"
        request = "20000000-0000-4000-8000-000000000001"
        token = "40000000-0000-4000-8000-000000000001"
        a = b = None
        started = False
        with tempfile.TemporaryDirectory(prefix="fleet-renew-order-", dir="/private/tmp") as temporary:
            directory = pathlib.Path(temporary).resolve()
            try:
                subprocess.run([str(pg / "initdb"), "-D", str(directory / "data"), "--auth=trust", "--no-locale", "-E", "UTF8"], check=True, stdout=subprocess.DEVNULL, timeout=20)
                subprocess.run([str(pg / "pg_ctl"), "-D", str(directory / "data"), "-l", str(directory / "server.log"), "-o", "-h '' -k " + str(directory) + " -p 55483", "-w", "start"], check=True, stdout=subprocess.DEVNULL, timeout=20)
                started = True
                command = [str(pg / "psql"), "-h", str(directory), "-p", "55483", "-d", "postgres", "-Atq", "-v", "ON_ERROR_STOP=1"]
                fixture = """
                    create role service_role;
                    create table public.account_run_requests(id uuid primary key, account_id uuid, status text);
                    create table public.auto_restart_device_locks(device_id uuid primary key, worker_id text, request_id uuid, account_id uuid, lease_id uuid, lease_expires_at timestamptz, heartbeat_at timestamptz, updated_at timestamptz);
                    insert into public.account_run_requests values ('%s','%s','running');
                    insert into public.auto_restart_device_locks values ('%s','same-dispatcher','%s','%s','%s',now()+interval '15 minutes',now(),now());
                """ % (request, account, device, request, account, token)
                subprocess.run(command, input=fixture + renewal + " grant execute on function public.auto_restart_renew_device_lock(uuid,text,uuid,integer,uuid,uuid) to service_role;", text=True, check=True, capture_output=True, timeout=10)
                a = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
                a.stdin.write("BEGIN; SET statement_timeout='5s'; SET application_name='fixture_admission_order'; SELECT device_id FROM public.auto_restart_device_locks WHERE device_id='" + device + "' FOR UPDATE;\n\\echo DEVICE_LOCK_HELD\n")
                a.stdin.flush()
                while True:
                    line = a.stdout.readline()
                    self.assertTrue(line, "admission boundary failed before device lock")
                    if "DEVICE_LOCK_HELD" in line:
                        break
                statement = "SET statement_timeout='5s'; SET application_name='fixture_fenced_renewal'; SET ROLE service_role; SELECT public.auto_restart_renew_device_lock('%s','same-dispatcher','%s',900,'%s','%s');" % (device, request, token, account)
                b = subprocess.Popen(command + ["-c", statement], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                blocked = False
                for _ in range(100):
                    observed = subprocess.check_output(command + ["-c", "SELECT count(*) FROM pg_stat_activity WHERE application_name='fixture_fenced_renewal' AND wait_event_type='Lock' AND cardinality(pg_blocking_pids(pid))>0"], text=True, timeout=5).strip()
                    if observed == "1":
                        blocked = True
                        break
                    time.sleep(.02)
                self.assertTrue(blocked, "renewal did not reach the controlled wait")
                a.stdin.write("SELECT id FROM public.account_run_requests WHERE id='" + request + "' FOR UPDATE; COMMIT;\n\\q\n")
                a.stdin.flush()
                _, admission_error = a.communicate(timeout=10)
                renewal_output, renewal_error = b.communicate(timeout=10)
                self.assertEqual(a.returncode, 0, admission_error)
                self.assertEqual(b.returncode, 0, renewal_error)
                self.assertNotIn("deadlock detected", admission_error + renewal_error)
                self.assertTrue(json.loads(renewal_output.strip())["ok"])
            finally:
                for process in (a, b):
                    if process is not None and process.poll() is None:
                        process.kill()
                        process.communicate(timeout=5)
                if started:
                    subprocess.run([str(pg / "pg_ctl"), "-D", str(directory / "data"), "-m", "immediate", "-w", "stop"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)



if __name__ == "__main__":
    unittest.main()
