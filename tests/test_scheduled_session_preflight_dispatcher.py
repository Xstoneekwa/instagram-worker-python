"""Tests for scheduled session preflight dispatcher support."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import account_run_request_consumer as consumer
import scheduled_session_preflight_control as preflight_control


class ScheduledSessionPreflightDispatcherTest(unittest.TestCase):
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

    def test_reconcile_dashboard_action_marks_blocked_as_action_required(self) -> None:
        with patch.object(preflight_control.supabase_client, "call_rpc") as call_rpc:
            preflight_control.reconcile_preflight_dashboard_action(
                account_id="00000000-0000-4000-8000-000000000201",
                assignment_id="00000000-0000-4000-8000-000000000301",
                starts_at="2026-07-08T22:00:00+00:00",
                preflight_status="preflight_blocked",
                reason_code="identity_mismatch",
            )
        call_rpc.assert_called_once()
        payload = call_rpc.call_args.args[1]
        self.assertEqual(payload["p_status"], "action_required")
        self.assertTrue(payload["p_requires_client_action"])

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
