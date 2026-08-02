"""Tests for scheduled session preflight dispatcher support."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import account_run_request_consumer as consumer
import scheduled_session_preflight_control as preflight_control

TEST_REQUEST_ID = "00000000-0000-4000-8000-000000000101"
TEST_ACCOUNT_ID = "00000000-0000-4000-8000-000000000201"
TEST_DEVICE_ID = "00000000-0000-4000-8000-000000000401"
TEST_APP_INSTANCE_ID = "00000000-0000-4000-8000-000000000501"
TEST_ASSIGNMENT_ID = "00000000-0000-4000-8000-000000000601"


class ScheduledSessionPreflightDispatcherTest(unittest.TestCase):
    def setUp(self) -> None:
        self.package_runtime_contract = patch.object(
            consumer,
            "_load_package_runtime_contract",
            return_value=(True, "ready", {"ok": True, "reason": "ready"}),
        )
        self.package_runtime_contract.start()
        self.addCleanup(self.package_runtime_contract.stop)

    def test_load_dispatcher_config_includes_scheduled_session_preflight(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            with patch.object(
                consumer.config,
                "RUN_CONTROL_DISPATCHER_ALLOWED_RUN_TYPES",
                "account_session,outreach_session,login_provisioning,login_email_code_resume,login_orphan_challenge_recovery,scheduled_session_preflight",
            ):
                cfg = consumer.load_dispatcher_config()
        self.assertIn("scheduled_session_preflight", cfg.allowed_run_types)

    def test_device_bound_run_types_include_preflight(self) -> None:
        self.assertIn("scheduled_session_preflight", consumer.DEVICE_BOUND_RUN_TYPES)

    def test_build_scheduled_session_preflight_command(self) -> None:
        cmd = consumer._build_scheduled_session_preflight_command(
            "acct-1",
            "req-1",
            device_serial="serial-1",
            package_name="com.instagram.androie",
            expected_username="tracker_user",
            metadata_safe={"preflight_id": "preflight-1", "verification_only": True},
        )
        self.assertIn("scheduled_session_preflight_runner.py", " ".join(cmd))
        self.assertIn("--preflight-id", cmd)
        self.assertIn("preflight-1", cmd)
        self.assertIn("--expected-username", cmd)
        self.assertIn("tracker_user", cmd)

    def test_build_runner_command_routes_preflight(self) -> None:
        with patch.object(consumer, "_load_expected_username", return_value="tracker_user"):
            cmd = consumer._build_runner_command(
                "acct-1",
                "scheduled_session_preflight",
                "req-1",
                device_serial="serial-1",
                package_name="com.instagram.androie",
                metadata_safe={"preflight_id": "preflight-1"},
            )
        self.assertIn("scheduled_session_preflight_runner.py", " ".join(cmd))

    def test_build_runner_command_routes_preflight(self) -> None:
        with patch.object(consumer, "_load_expected_username", return_value="tracker_user"):
            cmd = consumer._build_runner_command(
                "acct-1",
                "scheduled_session_preflight",
                "req-1",
                device_serial="serial-1",
                package_name="com.instagram.androie",
                metadata_safe={"preflight_id": "preflight-1"},
            )
        self.assertIn("scheduled_session_preflight_runner.py", " ".join(cmd))

    def test_handle_claimed_preflight_passes_assignment_device_serial_to_runner(self) -> None:
        cfg = consumer.DispatcherConfig(
            enabled=True,
            health_only=False,
            launch_enabled=True,
            worker_id="run-dispatcher:test",
            poll_seconds=5.0,
            lease_seconds=120,
            heartbeat_seconds=20.0,
            allowed_run_types=["scheduled_session_preflight"],
            test_account_ids=set(),
            subprocess_timeout_seconds=7200,
            require_assignment=False,
            enforce_assignment_window=False,
        )
        request = {
            "id": TEST_REQUEST_ID,
            "account_id": TEST_ACCOUNT_ID,
            "requested_run_type": "scheduled_session_preflight",
            "status": "claimed",
            "metadata_safe": {
                "preflight_id": "00000000-0000-4000-8000-000000000701",
                "expected_username": "mythyl_fitness",
                "expected_package": "com.instagram.android",
            },
        }
        dispatch_ctx = {
            "assignment_found": True,
            "assignment_id": TEST_ASSIGNMENT_ID,
            "account_id": TEST_ACCOUNT_ID,
            "assignment_type": "full_cycle",
            "slot_kind": "full_cycle_6h",
            "device_id": TEST_DEVICE_ID,
            "clone_id": None,
            "app_instance_id": TEST_APP_INSTANCE_ID,
            "device_kind": "physical",
            "adb_serial": "RFGL145LZHE",
            "package_name": "com.instagram.android",
            "source": "account_assignments",
            "fallback_used": False,
            "reason": "assignment_resolved",
            "run_type": "scheduled_session_preflight",
        }

        class FakeProc:
            def poll(self) -> int:
                return 0

        with (
            patch.object(consumer, "_account_is_launch_allowed", return_value=(True, None)),
            patch.object(consumer, "mark_account_run_request_starting", return_value=True),
            patch.object(consumer, "_validate_assignment", return_value=(True, None, dispatch_ctx)),
            patch.object(consumer, "evaluate_queued_run_commercial_policy", return_value=(True, None, {})),
            patch.object(preflight_control, "evaluate_scheduled_session_preflight_claim", return_value=(True, None, None)),
            patch.object(consumer, "transfer_device_lock", return_value={"transferred": True}),
            patch.object(consumer, "renew_device_lock", return_value={"renewed": True}),
            patch.object(consumer, "_heartbeat"),
            patch.object(consumer, "_runner_runtime_identity_env", return_value={}),
            patch.object(consumer.subprocess, "Popen", return_value=FakeProc()) as popen,
            patch.object(consumer, "_finalize_manual_run_after_subprocess"),
            patch.object(consumer, "_audit"),
        ):
            consumer._handle_claimed_request(cfg, request)

        cmd = popen.call_args.args[0]
        self.assertIn("--device-serial", cmd)
        self.assertEqual(cmd[cmd.index("--device-serial") + 1], "RFGL145LZHE")
        self.assertIn("--package-name", cmd)
        self.assertEqual(cmd[cmd.index("--package-name") + 1], "com.instagram.android")

    def test_handle_claimed_preflight_blocks_without_device_serial(self) -> None:
        cfg = consumer.DispatcherConfig(
            enabled=True,
            health_only=False,
            launch_enabled=True,
            worker_id="run-dispatcher:test",
            poll_seconds=5.0,
            lease_seconds=120,
            heartbeat_seconds=20.0,
            allowed_run_types=["scheduled_session_preflight"],
            test_account_ids=set(),
            subprocess_timeout_seconds=7200,
            require_assignment=False,
            enforce_assignment_window=False,
        )
        request = {
            "id": "00000000-0000-4000-8000-000000000102",
            "account_id": TEST_ACCOUNT_ID,
            "requested_run_type": "scheduled_session_preflight",
            "status": "claimed",
            "metadata_safe": {"preflight_id": "00000000-0000-4000-8000-000000000702"},
        }
        dispatch_ctx = {
            "assignment_found": True,
            "assignment_id": TEST_ASSIGNMENT_ID,
            "account_id": TEST_ACCOUNT_ID,
            "assignment_type": "full_cycle",
            "device_id": TEST_DEVICE_ID,
            "app_instance_id": TEST_APP_INSTANCE_ID,
            "adb_serial": "",
            "reason": "assignment_resolved",
            "run_type": "scheduled_session_preflight",
        }

        with (
            patch.object(consumer, "_account_is_launch_allowed", return_value=(True, None)),
            patch.object(consumer, "mark_account_run_request_starting", return_value=True),
            patch.object(consumer, "_validate_assignment", return_value=(True, None, dispatch_ctx)),
            patch.object(consumer, "evaluate_queued_run_commercial_policy", return_value=(True, None, {})),
            patch.object(preflight_control, "evaluate_scheduled_session_preflight_claim", return_value=(True, None, None)),
            patch.object(consumer, "transfer_device_lock", return_value={"transferred": True}),
            patch.object(consumer, "renew_device_lock", return_value={"renewed": True}),
            patch.object(consumer, "release_device_lock", return_value={"released": True}),
            patch.object(preflight_control, "terminalize_scheduled_session_preflight_request") as terminalize,
            patch.object(consumer, "_safe_complete_account_run_request") as complete,
            patch.object(consumer.subprocess, "Popen") as popen,
            patch.object(consumer, "_audit"),
        ):
            consumer._handle_claimed_request(cfg, request)

        popen.assert_not_called()
        terminalize.assert_called_once()
        self.assertEqual(terminalize.call_args.kwargs["reason_code"], "device_serial_missing")
        complete.assert_called_once()
        self.assertEqual(complete.call_args.kwargs["error_code"], "device_serial_missing")

    def test_evaluate_claim_marks_expired_preflight(self) -> None:
        now = datetime(2026, 7, 8, 17, 0, tzinfo=timezone.utc)
        request = {
            "id": "req-1",
            "metadata_safe": {"preflight_id": "preflight-1"},
        }
        with patch.object(
            preflight_control,
            "_load_preflight_row",
            return_value={
                "id": "preflight-1",
                "status": "preflight_running",
                "expires_at": "2026-07-08T16:00:00+00:00",
                "scheduled_window_end": "2026-07-08T22:00:00+00:00",
                "business_action_deadline": "2026-07-08T21:50:00+00:00",
            },
        ):
            ok, terminal_status, reason = preflight_control.evaluate_scheduled_session_preflight_claim(
                request=request,
                now=now,
            )
        self.assertFalse(ok)
        self.assertEqual(terminal_status, "preflight_expired")
        self.assertEqual(reason, "preflight_start_window_elapsed")

    def test_evaluate_claim_allows_late_preflight_after_session_start(self) -> None:
        now = datetime(2026, 7, 8, 22, 5, tzinfo=timezone.utc)
        request = {
            "id": "req-late",
            "metadata_safe": {
                "preflight_id": "preflight-late",
                "late_preflight": True,
                "scheduled_session_at": "2026-07-08T22:00:00+00:00",
                "business_action_deadline": "2026-07-09T03:50:00+00:00",
            },
        }
        with patch.object(
            preflight_control,
            "_load_preflight_row",
            return_value={
                "id": "preflight-late",
                "status": "preflight_running",
                "expires_at": "2026-07-08T22:00:00+00:00",
                "scheduled_window_start": "2026-07-08T22:00:00+00:00",
                "scheduled_window_end": "2026-07-09T04:00:00+00:00",
                "business_action_deadline": "2026-07-09T03:50:00+00:00",
            },
        ):
            ok, terminal_status, reason = preflight_control.evaluate_scheduled_session_preflight_claim(
                request=request,
                now=now,
            )
        self.assertTrue(ok)
        self.assertIsNone(terminal_status)
        self.assertIsNone(reason)

    def test_reconcile_dashboard_action_marks_blocked_as_pending_with_real_reason(self) -> None:
        with patch.object(preflight_control.supabase_client, "call_rpc") as call_rpc:
            preflight_control.reconcile_preflight_dashboard_action(
                account_id="00000000-0000-4000-8000-000000000201",
                assignment_id="00000000-0000-4000-8000-000000000301",
                starts_at="2026-07-08T22:00:00+00:00",
                preflight_status="preflight_blocked",
                reason_code="own_profile_open_failed",
            )
        call_rpc.assert_called_once()
        self.assertEqual(call_rpc.call_args.args[0], "upsert_account_dashboard_action")
        payload = call_rpc.call_args.args[1]
        self.assertEqual(payload["p_status"], "pending")
        self.assertTrue(payload["p_requires_client_action"])
        self.assertEqual(payload["p_severity"], "error")
        self.assertEqual(payload["p_metadata"]["reason_code"], "own_profile_open_failed")
        self.assertIn("own_profile_open_failed", payload["p_admin_message"])

    def test_reconcile_dashboard_action_technical_block_is_warning(self) -> None:
        with patch.object(preflight_control.supabase_client, "call_rpc") as call_rpc:
            preflight_control.reconcile_preflight_dashboard_action(
                account_id="00000000-0000-4000-8000-000000000201",
                assignment_id="00000000-0000-4000-8000-000000000301",
                starts_at="2026-07-08T22:00:00+00:00",
                preflight_status="preflight_blocked",
                reason_code="device_serial_missing",
            )
        payload = call_rpc.call_args.args[1]
        self.assertEqual(payload["p_status"], "pending")
        self.assertEqual(payload["p_severity"], "warning")

    def test_reconcile_dashboard_action_resolves_active_action_on_ready(self) -> None:
        with (
            patch.object(
                preflight_control,
                "_find_active_preflight_dashboard_action_id",
                return_value="00000000-0000-4000-8000-000000000901",
            ),
            patch.object(preflight_control.supabase_client, "call_rpc") as call_rpc,
        ):
            preflight_control.reconcile_preflight_dashboard_action(
                account_id="00000000-0000-4000-8000-000000000201",
                assignment_id="00000000-0000-4000-8000-000000000301",
                starts_at="2026-07-08T22:00:00+00:00",
                preflight_status="preflight_ready",
            )
        call_rpc.assert_called_once()
        self.assertEqual(call_rpc.call_args.args[0], "transition_account_dashboard_action")
        payload = call_rpc.call_args.args[1]
        self.assertEqual(payload["p_action_id"], "00000000-0000-4000-8000-000000000901")
        self.assertEqual(payload["p_new_status"], "resolved")

    def test_reconcile_dashboard_action_expired_without_active_action_is_noop(self) -> None:
        with (
            patch.object(
                preflight_control,
                "_find_active_preflight_dashboard_action_id",
                return_value=None,
            ),
            patch.object(preflight_control.supabase_client, "call_rpc") as call_rpc,
        ):
            preflight_control.reconcile_preflight_dashboard_action(
                account_id="00000000-0000-4000-8000-000000000201",
                assignment_id="00000000-0000-4000-8000-000000000301",
                starts_at="2026-07-08T22:00:00+00:00",
                preflight_status="preflight_expired",
                reason_code="preflight_start_window_elapsed",
            )
        call_rpc.assert_not_called()

    def test_load_preflight_row_selects_real_reason_code(self) -> None:
        with patch.object(
            preflight_control.supabase_client,
            "_request_json",
            return_value=[{"id": "00000000-0000-4000-8000-000000000301", "reason_code": "own_profile_open_failed"}],
        ) as request_json:
            row = preflight_control._load_preflight_row("00000000-0000-4000-8000-000000000301")
        self.assertEqual(row["reason_code"], "own_profile_open_failed")
        select = request_json.call_args.kwargs["query"]["select"]
        self.assertIn("reason_code", select)

    def test_evaluate_claim_returns_real_reason_for_blocked_row(self) -> None:
        request = {
            "id": "00000000-0000-4000-8000-000000000101",
            "account_id": "00000000-0000-4000-8000-000000000201",
            "metadata_safe": {"preflight_id": "00000000-0000-4000-8000-000000000301"},
        }
        with patch.object(
            preflight_control,
            "_load_preflight_row",
            return_value={
                "id": "00000000-0000-4000-8000-000000000301",
                "status": "preflight_blocked",
                "reason_code": "own_profile_open_failed",
                "expires_at": "2026-07-08T22:00:00+00:00",
            },
        ):
            ok, terminal_status, reason = preflight_control.evaluate_scheduled_session_preflight_claim(
                request=request,
                now=datetime(2026, 7, 8, 17, 0, tzinfo=timezone.utc),
            )
        self.assertFalse(ok)
        self.assertEqual(terminal_status, "preflight_blocked")
        self.assertEqual(reason, "own_profile_open_failed")

    def test_reconcile_terminalizes_expired_queued_preflight(self) -> None:
        now = datetime(2026, 7, 8, 17, 0, tzinfo=timezone.utc)
        queued = [{
            "id": "00000000-0000-4000-8000-000000000101",
            "account_id": "00000000-0000-4000-8000-000000000201",
            "status": "queued",
            "requested_run_type": "scheduled_session_preflight",
            "created_at": (now - timedelta(minutes=40)).isoformat(),
            "metadata_safe": {"preflight_id": "00000000-0000-4000-8000-000000000301"},
        }]
        with (
            patch.object(preflight_control, "_list_queued_preflight_requests", return_value=queued),
            patch.object(
                preflight_control,
                "_load_preflight_row",
                return_value={
                    "id": "00000000-0000-4000-8000-000000000301",
                    "status": "preflight_running",
                    "expires_at": "2026-07-08T16:00:00+00:00",
                    "scheduled_window_end": "2026-07-08T22:00:00+00:00",
                    "business_action_deadline": "2026-07-08T21:50:00+00:00",
                },
            ),
            patch.object(preflight_control, "terminalize_scheduled_session_preflight_request", return_value=True) as terminalize,
        ):
            handled = preflight_control.reconcile_stale_scheduled_session_preflight_requests(
                worker_id="run-dispatcher:test",
                allowed_run_types=["scheduled_session_preflight", "account_session"],
                now=now,
            )
        self.assertEqual(handled, 1)
        terminalize.assert_called_once()

    def test_reconcile_reports_dispatcher_unavailable_without_terminalizing(self) -> None:
        now = datetime(2026, 7, 8, 17, 0, tzinfo=timezone.utc)
        queued = [{
            "id": "00000000-0000-4000-8000-000000000102",
            "account_id": "00000000-0000-4000-8000-000000000202",
            "status": "queued",
            "requested_run_type": "scheduled_session_preflight",
            "created_at": (now - timedelta(minutes=20)).isoformat(),
            "metadata_safe": {"preflight_id": "00000000-0000-4000-8000-000000000302"},
        }]
        with (
            patch.object(preflight_control, "_list_queued_preflight_requests", return_value=queued),
            patch.object(preflight_control, "_upsert_dispatcher_unavailable_action") as report,
            patch.object(preflight_control, "terminalize_scheduled_session_preflight_request") as terminalize,
        ):
            handled = preflight_control.reconcile_stale_scheduled_session_preflight_requests(
                worker_id="run-dispatcher:test",
                allowed_run_types=["account_session"],
                now=now,
            )
        self.assertEqual(handled, 0)
        report.assert_called_once()
        terminalize.assert_not_called()


if __name__ == "__main__":
    unittest.main()
