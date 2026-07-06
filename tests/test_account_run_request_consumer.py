"""Tests for Run Control dispatcher."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import account_run_request_consumer as consumer

TEST_REQUEST_ID = "00000000-0000-4000-8000-000000000101"
TEST_ACCOUNT_ID = "00000000-0000-4000-8000-000000000201"
TEST_RUN_ID = "00000000-0000-4000-8000-000000000301"


class AccountRunRequestConsumerTest(unittest.TestCase):
    def test_load_dispatcher_config_defaults(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            cfg = consumer.load_dispatcher_config()
        self.assertFalse(cfg.enabled)
        self.assertTrue(cfg.health_only)
        self.assertFalse(cfg.launch_enabled)

    def test_dispatcher_is_healthy_false_when_disabled(self) -> None:
        cfg = consumer.DispatcherConfig(
            enabled=False,
            health_only=True,
            launch_enabled=False,
            worker_id="run-dispatcher:test",
            poll_seconds=5.0,
            lease_seconds=120,
            heartbeat_seconds=20.0,
            allowed_run_types=["account_session"],
            test_account_ids=set(),
            subprocess_timeout_seconds=7200,
            require_assignment=False,
            enforce_assignment_window=False,
        )
        self.assertFalse(consumer.dispatcher_is_healthy(cfg))

    def test_dispatcher_is_healthy_true_for_fresh_heartbeat(self) -> None:
        cfg = consumer.DispatcherConfig(
            enabled=True,
            health_only=True,
            launch_enabled=False,
            worker_id="run-dispatcher:test",
            poll_seconds=5.0,
            lease_seconds=120,
            heartbeat_seconds=20.0,
            allowed_run_types=["account_session"],
            test_account_ids=set(),
            subprocess_timeout_seconds=7200,
            require_assignment=False,
            enforce_assignment_window=False,
        )
        fresh = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
        with patch.object(
            consumer.supabase_client,
            "_request_json",
            return_value=[{"worker_id": "run-dispatcher:test", "status": "idle", "last_seen_at": fresh}],
        ):
            self.assertTrue(consumer.dispatcher_is_healthy(cfg))

    def test_run_once_launch_enabled_idle_skips_complete(self) -> None:
        cfg = consumer.DispatcherConfig(
            enabled=True,
            health_only=False,
            launch_enabled=True,
            worker_id="run-dispatcher:test",
            poll_seconds=5.0,
            lease_seconds=120,
            heartbeat_seconds=20.0,
            allowed_run_types=["account_session"],
            test_account_ids=set(),
            subprocess_timeout_seconds=7200,
            require_assignment=False,
            enforce_assignment_window=False,
        )
        with (
            patch.object(consumer, "_heartbeat"),
            patch.object(consumer, "reclaim_stale_account_run_requests", return_value=0),
            patch.object(consumer, "claim_next_account_run_request", return_value=None),
            patch.object(consumer, "complete_account_run_request") as complete,
        ):
            result = consumer.run_once(cfg)
        self.assertEqual(result["mode"], "idle")
        complete.assert_not_called()

    def test_run_once_launch_enabled_invalid_claim_row_is_idle_no_complete(self) -> None:
        cfg = consumer.DispatcherConfig(
            enabled=True,
            health_only=False,
            launch_enabled=True,
            worker_id="run-dispatcher:test",
            poll_seconds=5.0,
            lease_seconds=120,
            heartbeat_seconds=20.0,
            allowed_run_types=["account_session"],
            test_account_ids=set(),
            subprocess_timeout_seconds=7200,
            require_assignment=False,
            enforce_assignment_window=False,
        )
        with (
            patch.object(consumer, "_heartbeat"),
            patch.object(consumer, "reclaim_stale_account_run_requests", return_value=0),
            patch.object(
                consumer,
                "claim_next_account_run_request",
                return_value={"status": "claimed"},
            ),
            patch.object(consumer, "complete_account_run_request") as complete,
            patch.object(consumer, "_handle_claimed_request") as handle,
        ):
            result = consumer.run_once(cfg)
        self.assertEqual(result["mode"], "idle")
        self.assertEqual(result.get("reason"), "invalid_claim_row")
        complete.assert_not_called()
        handle.assert_not_called()

    def test_handle_claimed_request_invalid_row_does_not_complete(self) -> None:
        cfg = consumer.DispatcherConfig(
            enabled=True,
            health_only=False,
            launch_enabled=True,
            worker_id="run-dispatcher:test",
            poll_seconds=5.0,
            lease_seconds=120,
            heartbeat_seconds=20.0,
            allowed_run_types=["account_session"],
            test_account_ids=set(),
            subprocess_timeout_seconds=7200,
            require_assignment=False,
            enforce_assignment_window=False,
        )
        with patch.object(consumer, "complete_account_run_request") as complete:
            consumer._handle_claimed_request(cfg, {"status": "claimed"})
        complete.assert_not_called()

    def test_launch_preflight_blocks_when_active_queue_present(self) -> None:
        cfg = consumer.DispatcherConfig(
            enabled=True,
            health_only=False,
            launch_enabled=True,
            worker_id="run-dispatcher:test",
            poll_seconds=5.0,
            lease_seconds=120,
            heartbeat_seconds=20.0,
            allowed_run_types=["account_session"],
            test_account_ids=set(),
            subprocess_timeout_seconds=7200,
            require_assignment=False,
            enforce_assignment_window=False,
        )
        with (
            patch.dict("os.environ", {"RUN_CONTROL_DISPATCHER_ALLOW_EXISTING_QUEUE": "false"}, clear=False),
            patch.object(
                consumer,
                "summarize_active_account_run_requests",
                return_value={
                    "active_count": 1,
                    "requests": [
                        {
                            "request_id": TEST_REQUEST_ID,
                            "account_id": TEST_ACCOUNT_ID,
                            "status": "queued",
                            "requested_run_type": "account_session",
                            "created_at": "2026-06-09T10:00:00+00:00",
                        }
                    ],
                    "read_failed": False,
                    "error": None,
                },
            ),
        ):
            preflight = consumer.evaluate_launch_mode_startup_preflight(cfg)
        self.assertFalse(preflight["ok"])
        self.assertEqual(preflight["reason"], "active_queue_present")
        self.assertEqual(preflight["active_count"], 1)

    def test_launch_preflight_allows_empty_queue(self) -> None:
        cfg = consumer.DispatcherConfig(
            enabled=True,
            health_only=False,
            launch_enabled=True,
            worker_id="run-dispatcher:test",
            poll_seconds=5.0,
            lease_seconds=120,
            heartbeat_seconds=20.0,
            allowed_run_types=["account_session"],
            test_account_ids=set(),
            subprocess_timeout_seconds=7200,
            require_assignment=False,
            enforce_assignment_window=False,
        )
        with patch.object(
            consumer,
            "summarize_active_account_run_requests",
            return_value={"active_count": 0, "requests": [], "read_failed": False, "error": None},
        ):
            preflight = consumer.evaluate_launch_mode_startup_preflight(cfg)
        self.assertTrue(preflight["ok"])
        self.assertEqual(preflight["reason"], "ready")

    def test_launch_preflight_allows_health_only_with_active_queue(self) -> None:
        cfg = consumer.DispatcherConfig(
            enabled=True,
            health_only=True,
            launch_enabled=False,
            worker_id="run-dispatcher:test",
            poll_seconds=5.0,
            lease_seconds=120,
            heartbeat_seconds=20.0,
            allowed_run_types=["account_session"],
            test_account_ids=set(),
            subprocess_timeout_seconds=7200,
            require_assignment=False,
            enforce_assignment_window=False,
        )
        with patch.object(
            consumer,
            "summarize_active_account_run_requests",
            return_value={
                "active_count": 2,
                "requests": [],
                "read_failed": False,
                "error": None,
            },
        ):
            preflight = consumer.evaluate_launch_mode_startup_preflight(cfg)
        self.assertTrue(preflight["ok"])
        self.assertEqual(preflight["mode"], "health_only")

    def test_run_forever_exits_when_launch_preflight_blocks(self) -> None:
        cfg = consumer.DispatcherConfig(
            enabled=True,
            health_only=False,
            launch_enabled=True,
            worker_id="run-dispatcher:test",
            poll_seconds=5.0,
            lease_seconds=120,
            heartbeat_seconds=20.0,
            allowed_run_types=["account_session"],
            test_account_ids=set(),
            subprocess_timeout_seconds=7200,
            require_assignment=False,
            enforce_assignment_window=False,
        )
        with (
            patch.object(consumer, "load_dispatcher_config", return_value=cfg),
            patch.object(
                consumer,
                "evaluate_launch_mode_startup_preflight",
                return_value={"ok": False, "reason": "active_queue_present", "active_count": 1, "requests": []},
            ),
            patch.object(consumer, "_heartbeat") as heartbeat,
        ):
            code = consumer.run_forever(cfg)
        self.assertEqual(code, 3)
        heartbeat.assert_not_called()

    def test_run_once_health_only_skips_claim(self) -> None:
        cfg = consumer.DispatcherConfig(
            enabled=True,
            health_only=True,
            launch_enabled=False,
            worker_id="run-dispatcher:test",
            poll_seconds=5.0,
            lease_seconds=120,
            heartbeat_seconds=20.0,
            allowed_run_types=["account_session"],
            test_account_ids=set(),
            subprocess_timeout_seconds=7200,
            require_assignment=False,
            enforce_assignment_window=False,
        )
        with (
            patch.object(consumer, "_heartbeat") as heartbeat,
            patch.object(consumer, "reclaim_stale_account_run_requests", return_value=0) as reclaim,
            patch.object(consumer, "claim_next_account_run_request") as claim,
        ):
            result = consumer.run_once(cfg)
        self.assertEqual(result["mode"], "health_only")
        heartbeat.assert_called_once()
        reclaim.assert_called_once()
        claim.assert_not_called()

    def test_handle_claimed_request_passes_assignment_device_serial_to_runner(self) -> None:
        cfg = consumer.DispatcherConfig(
            enabled=True,
            health_only=False,
            launch_enabled=True,
            worker_id="run-dispatcher:test",
            poll_seconds=5.0,
            lease_seconds=120,
            heartbeat_seconds=20.0,
            allowed_run_types=["account_session"],
            test_account_ids=set(),
            subprocess_timeout_seconds=7200,
            require_assignment=True,
            enforce_assignment_window=False,
        )
        request = {
            "id": TEST_REQUEST_ID,
            "account_id": TEST_ACCOUNT_ID,
            "requested_run_type": "account_session",
            "status": "claimed",
        }
        dispatch_ctx = {
            "assignment_found": True,
            "assignment_id": "assignment-1",
            "account_id": TEST_ACCOUNT_ID,
            "assignment_type": "full_cycle",
            "slot_kind": "full_cycle_6h",
            "device_id": "device-1",
            "clone_id": None,
            "app_instance_id": "app-instance-1",
            "device_kind": "emulator",
            "adb_serial": "emulator-5554",
            "package_name": "com.instagram.androif",
            "source": "account_assignments",
            "fallback_used": False,
            "reason": "assignment_resolved",
            "run_type": "account_session",
        }

        class FakeProc:
            def poll(self) -> int:
                return 0

        with (
            patch.object(consumer, "_account_is_launch_allowed", return_value=(True, None)),
            patch.object(consumer, "mark_account_run_request_starting", return_value=True),
            patch.object(consumer, "resolve_account_assignment_runtime_context", return_value=dispatch_ctx),
            patch.object(consumer, "_heartbeat"),
            patch.object(consumer.subprocess, "Popen", return_value=FakeProc()) as popen,
            patch.object(consumer, "_finalize_manual_run_after_subprocess"),
            patch.object(consumer, "_audit"),
        ):
            consumer._handle_claimed_request(cfg, request)

        cmd = popen.call_args.args[0]
        self.assertIn("--device-serial", cmd)
        self.assertEqual(cmd[cmd.index("--device-serial") + 1], "emulator-5554")
        self.assertIn("--package-name", cmd)
        self.assertEqual(cmd[cmd.index("--package-name") + 1], "com.instagram.androif")
        self.assertIn("--expected-app-instance-id", cmd)
        self.assertEqual(cmd[cmd.index("--expected-app-instance-id") + 1], "app-instance-1")

    def test_build_account_session_runner_command_includes_clone_package(self) -> None:
        cmd = consumer._build_runner_command(
            TEST_ACCOUNT_ID,
            "account_session",
            TEST_REQUEST_ID,
            device_serial="RFGL145LZHE",
            package_name="com.instagram.androif",
            app_instance_id="59f82a36-155c-4073-9765-28dac46b56ff",
        )
        self.assertIn("runner.py", cmd[1])
        self.assertEqual(cmd[cmd.index("--device-serial") + 1], "RFGL145LZHE")
        self.assertEqual(cmd[cmd.index("--package-name") + 1], "com.instagram.androif")
        self.assertEqual(
            cmd[cmd.index("--expected-app-instance-id") + 1],
            "59f82a36-155c-4073-9765-28dac46b56ff",
        )

    def test_build_account_session_runner_command_omits_package_when_unresolved(self) -> None:
        cmd = consumer._build_runner_command(
            TEST_ACCOUNT_ID,
            "account_session",
            TEST_REQUEST_ID,
            device_serial="RFGL145LZHE",
            package_name=None,
            app_instance_id=None,
        )
        self.assertNotIn("--package-name", cmd)
        self.assertNotIn("--expected-app-instance-id", cmd)

    def test_login_subprocess_receives_runner_env_with_adb_path(self) -> None:
        cfg = consumer.DispatcherConfig(
            enabled=True,
            health_only=False,
            launch_enabled=True,
            worker_id="worker-1",
            poll_seconds=1.0,
            lease_seconds=60,
            heartbeat_seconds=20.0,
            allowed_run_types=["login_provisioning"],
            test_account_ids=set(),
            subprocess_timeout_seconds=120,
            require_assignment=True,
            enforce_assignment_window=False,
        )
        request = {
            "id": TEST_REQUEST_ID,
            "account_id": TEST_ACCOUNT_ID,
            "requested_run_type": "login_provisioning",
            "status": "claimed",
        }
        dispatch_ctx = {
            "assignment_found": True,
            "assignment_id": "assignment-1",
            "account_id": TEST_ACCOUNT_ID,
            "assignment_type": "full_cycle",
            "slot_kind": "full_cycle_6h",
            "device_id": "device-1",
            "clone_id": None,
            "app_instance_id": "app-instance-1",
            "device_kind": "physical_phone",
            "adb_serial": "RFGL145VCKE",
            "package_name": "com.instagram.androie",
            "source": "account_assignments",
            "fallback_used": False,
            "reason": "assignment_resolved",
            "run_type": "login_provisioning",
        }

        class FakeProc:
            def poll(self) -> int:
                return 0

        fake_env = {"PATH": "/usr/bin", "ADB_PATH": "/tmp/platform-tools/adb"}
        with (
            patch.object(consumer, "_account_is_launch_allowed", return_value=(True, None)),
            patch.object(consumer, "mark_account_run_request_starting", return_value=True),
            patch.object(consumer, "resolve_account_assignment_runtime_context", return_value=dispatch_ctx),
            patch.object(consumer, "_heartbeat"),
            patch.object(consumer, "runner_subprocess_env", return_value=fake_env),
            patch.object(consumer, "_create_and_link_login_run", return_value=TEST_RUN_ID),
            patch.object(consumer, "_load_expected_username", return_value="cinema_catchup"),
            patch.object(consumer.subprocess, "Popen", return_value=FakeProc()) as popen,
            patch.object(consumer, "_finalize_manual_run_after_subprocess"),
            patch.object(consumer, "_audit"),
        ):
            consumer._handle_claimed_request(cfg, request)

        self.assertEqual(popen.call_args.kwargs.get("env"), fake_env)
        self.assertEqual(fake_env["ADB_PATH"], "/tmp/platform-tools/adb")
        self.assertEqual(fake_env["LOGIN_PROVISIONER_PUBLISH_ENABLED"], "true")

    def test_build_login_email_code_resume_command(self) -> None:
        with patch.object(consumer, "_load_expected_username", return_value="cinema_catchup"):
            cmd = consumer._build_runner_command(
                TEST_ACCOUNT_ID,
                "login_email_code_resume",
                TEST_REQUEST_ID,
                device_serial="RFGL145VCKE",
                package_name="com.instagram.androie",
                app_instance_id="7637db9a-3581-4099-8068-d5eb1ed86f96",
                metadata_safe={"action_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"},
            )
        self.assertIn("-m", cmd)
        self.assertIn("instagram_login_provisioner_cli", cmd)
        self.assertIn("--resume-email-code-from-action", cmd)
        self.assertIn("--verification-action-id", cmd)
        self.assertEqual(cmd[cmd.index("--verification-action-id") + 1], "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
        self.assertEqual(cmd[cmd.index("--package-name") + 1], "com.instagram.androie")
        self.assertEqual(cmd[cmd.index("--expected-app-instance-id") + 1], "7637db9a-3581-4099-8068-d5eb1ed86f96")
        self.assertIn("--publish", cmd)
        self.assertNotIn("--no-publish", cmd)
        self.assertNotIn("verification_code", " ".join(cmd))

    def test_build_login_provisioning_command(self) -> None:
        with patch.object(consumer, "_load_expected_username", return_value="cinema_catchup"):
            cmd = consumer._build_runner_command(
                TEST_ACCOUNT_ID,
                "login_provisioning",
                TEST_REQUEST_ID,
                device_serial="RFGL145VCKE",
                package_name="com.instagram.androie",
                app_instance_id="7637db9a-3581-4099-8068-d5eb1ed86f96",
            )
        self.assertIn("instagram_login_provisioner_cli", cmd)
        self.assertNotIn("--resume-email-code-from-action", cmd)
        self.assertIn("--publish", cmd)
        self.assertNotIn("--no-publish", cmd)
        self.assertIn("--expected-username", cmd)
        self.assertEqual(cmd[cmd.index("--package-name") + 1], "com.instagram.androie")
        self.assertEqual(cmd[cmd.index("--expected-app-instance-id") + 1], "7637db9a-3581-4099-8068-d5eb1ed86f96")

    def test_login_provisioner_env_enables_controlled_publish(self) -> None:
        with patch.object(consumer, "runner_subprocess_env", return_value={"PATH": "/usr/bin"}):
            env = consumer._login_provisioner_env()
        self.assertEqual(env["LOGIN_PROVISIONER_PUBLISH_ENABLED"], "true")
        self.assertEqual(env["PATH"], "/usr/bin")

    def test_handle_claimed_request_blocks_assignment_device_without_serial(self) -> None:
        cfg = consumer.DispatcherConfig(
            enabled=True,
            health_only=False,
            launch_enabled=True,
            worker_id="run-dispatcher:test",
            poll_seconds=5.0,
            lease_seconds=120,
            heartbeat_seconds=20.0,
            allowed_run_types=["account_session"],
            test_account_ids=set(),
            subprocess_timeout_seconds=7200,
            require_assignment=False,
            enforce_assignment_window=False,
        )
        request = {
            "id": TEST_REQUEST_ID,
            "account_id": TEST_ACCOUNT_ID,
            "requested_run_type": "account_session",
            "status": "claimed",
        }
        dispatch_ctx = {
            "assignment_found": False,
            "assignment_id": "assignment-1",
            "account_id": TEST_ACCOUNT_ID,
            "assignment_type": "full_cycle",
            "device_id": "device-1",
            "app_instance_id": "app-instance-1",
            "adb_serial": None,
            "source": "account_assignments",
            "fallback_used": False,
            "reason": "assignment_device_missing_adb_serial",
            "run_type": "account_session",
        }

        with (
            patch.object(consumer, "_account_is_launch_allowed", return_value=(True, None)),
            patch.object(consumer, "mark_account_run_request_starting", return_value=True),
            patch.object(consumer, "resolve_account_assignment_runtime_context", return_value=dispatch_ctx),
            patch.object(consumer.subprocess, "Popen") as popen,
            patch.object(consumer, "_safe_complete_account_run_request") as complete,
            patch.object(consumer, "_audit"),
        ):
            consumer._handle_claimed_request(cfg, request)

        popen.assert_not_called()
        complete.assert_called_once()
        self.assertEqual(complete.call_args.kwargs["error_code"], "assignment_device_missing_adb_serial")

    def test_handle_claimed_request_blocks_login_run_without_device_serial(self) -> None:
        cfg = consumer.DispatcherConfig(
            enabled=True,
            health_only=False,
            launch_enabled=True,
            worker_id="run-dispatcher:test",
            poll_seconds=5.0,
            lease_seconds=120,
            heartbeat_seconds=20.0,
            allowed_run_types=["login_email_code_resume"],
            test_account_ids=set(),
            subprocess_timeout_seconds=7200,
            require_assignment=False,
            enforce_assignment_window=False,
        )
        request = {
            "id": TEST_REQUEST_ID,
            "account_id": TEST_ACCOUNT_ID,
            "requested_run_type": "login_email_code_resume",
            "metadata_safe": {"action_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"},
            "status": "claimed",
        }
        dispatch_ctx = {
            "assignment_found": True,
            "assignment_id": "assignment-1",
            "account_id": TEST_ACCOUNT_ID,
            "assignment_type": "full_cycle",
            "device_id": "device-1",
            "app_instance_id": "app-instance-1",
            "adb_serial": "",
            "source": "account_assignments",
            "fallback_used": False,
            "reason": "assignment_resolved",
            "run_type": "login_email_code_resume",
        }

        with (
            patch.object(consumer, "_account_is_launch_allowed", return_value=(True, None)),
            patch.object(consumer, "mark_account_run_request_starting", return_value=True),
            patch.object(consumer, "resolve_account_assignment_runtime_context", return_value=dispatch_ctx),
            patch.object(consumer.subprocess, "Popen") as popen,
            patch.object(consumer, "_safe_complete_account_run_request") as complete,
            patch.object(consumer, "_audit"),
        ):
            consumer._handle_claimed_request(cfg, request)

        popen.assert_not_called()
        complete.assert_called_once()
        self.assertEqual(complete.call_args.kwargs["error_code"], "login_device_serial_required")

    def test_finalize_subprocess_nonzero_reconciles_linked_run(self) -> None:
        cfg = consumer.DispatcherConfig(
            enabled=True,
            health_only=False,
            launch_enabled=True,
            worker_id="run-dispatcher:test",
            poll_seconds=5.0,
            lease_seconds=120,
            heartbeat_seconds=20.0,
            allowed_run_types=["account_session"],
            test_account_ids=set(),
            subprocess_timeout_seconds=7200,
            require_assignment=False,
            enforce_assignment_window=False,
        )
        request = {
            "id": TEST_REQUEST_ID,
            "account_id": TEST_ACCOUNT_ID,
            "run_id": TEST_RUN_ID,
            "status": "running",
        }
        with (
            patch.object(consumer, "get_account_run_request", return_value=request),
            patch.object(consumer, "complete_account_run_request") as complete,
            patch.object(consumer, "_reconcile_linked_run", return_value={"reconciled": True}) as reconcile,
            patch.object(consumer, "_audit") as audit,
        ):
            consumer._finalize_manual_run_after_subprocess(
                cfg,
                request_id=TEST_REQUEST_ID,
                account_id=TEST_ACCOUNT_ID,
                exit_code=1,
            )
        complete.assert_called_once()
        self.assertEqual(complete.call_args.args[2], "failed")
        reconcile.assert_called_once()
        self.assertEqual(reconcile.call_args.kwargs["terminal_status"], "failed")
        audit.assert_called_once()
        self.assertEqual(audit.call_args.kwargs["action_type"], "manual_run_failed")

    def test_finalize_subprocess_verification_pending_keeps_request_active(self) -> None:
        cfg = consumer.DispatcherConfig(
            enabled=True,
            health_only=False,
            launch_enabled=True,
            worker_id="run-dispatcher:test",
            poll_seconds=5.0,
            lease_seconds=120,
            heartbeat_seconds=20.0,
            allowed_run_types=["login_provisioning"],
            test_account_ids=set(),
            subprocess_timeout_seconds=7200,
            require_assignment=False,
            enforce_assignment_window=False,
        )
        request = {
            "id": TEST_REQUEST_ID,
            "account_id": TEST_ACCOUNT_ID,
            "run_id": TEST_RUN_ID,
            "status": "running",
        }
        summary = {
            "run_id": TEST_RUN_ID,
            "final_outcome": "verification_pending",
            "dashboard_action_type": "enter_email_verification_code",
            "final_login_status": "verification_pending",
            "final_provisioning_status": "login_verification_pending",
        }
        with (
            patch.object(consumer, "get_account_run_request", return_value=request),
            patch.object(consumer, "complete_account_run_request") as complete,
            patch.object(consumer, "_reconcile_linked_run", return_value={"reconciled": True}) as reconcile,
            patch.object(consumer, "_safe_login_provisioner_summary_for_audit", return_value=summary),
            patch.object(consumer, "_audit") as audit,
        ):
            consumer._finalize_manual_run_after_subprocess(
                cfg,
                request_id=TEST_REQUEST_ID,
                account_id=TEST_ACCOUNT_ID,
                exit_code=1,
            )
        complete.assert_not_called()
        reconcile.assert_not_called()
        audit.assert_called_once()
        self.assertEqual(audit.call_args.kwargs["action_type"], "manual_run_verification_paused")
        self.assertEqual(audit.call_args.kwargs["payload"]["login_provisioner_summary"]["final_outcome"], "verification_pending")

    def test_finalize_subprocess_orphan_challenge_blocked_marks_request_blocked(self) -> None:
        cfg = consumer.DispatcherConfig(
            enabled=True,
            health_only=False,
            launch_enabled=True,
            worker_id="run-dispatcher:test",
            poll_seconds=5.0,
            lease_seconds=120,
            heartbeat_seconds=20.0,
            allowed_run_types=["login_provisioning"],
            test_account_ids=set(),
            subprocess_timeout_seconds=7200,
            require_assignment=False,
            enforce_assignment_window=False,
        )
        request = {
            "id": TEST_REQUEST_ID,
            "account_id": TEST_ACCOUNT_ID,
            "run_id": TEST_RUN_ID,
            "status": "running",
        }
        summary = {
            "run_id": TEST_RUN_ID,
            "final_outcome": "blocked",
            "reason": "orphan_challenge_provenance_weak",
            "failure_reason": "pre_input_challenge_orphan",
        }
        with (
            patch.object(consumer, "get_account_run_request", return_value=request),
            patch.object(consumer, "complete_account_run_request") as complete,
            patch.object(consumer, "_reconcile_linked_run", return_value={"reconciled": True}) as reconcile,
            patch.object(consumer, "_safe_login_provisioner_summary_for_audit", return_value=summary),
            patch.object(consumer, "_audit") as audit,
        ):
            consumer._finalize_manual_run_after_subprocess(
                cfg,
                request_id=TEST_REQUEST_ID,
                account_id=TEST_ACCOUNT_ID,
                exit_code=1,
            )
        complete.assert_called_once()
        self.assertEqual(complete.call_args.args[2], "blocked")
        self.assertEqual(complete.call_args.kwargs["error_code"], "orphan_challenge_provenance_weak")
        reconcile.assert_called_once()
        self.assertEqual(audit.call_args.kwargs["action_type"], "manual_run_blocked")

    def test_finalize_subprocess_real_failure_still_marks_failed(self) -> None:
        cfg = consumer.DispatcherConfig(
            enabled=True,
            health_only=False,
            launch_enabled=True,
            worker_id="run-dispatcher:test",
            poll_seconds=5.0,
            lease_seconds=120,
            heartbeat_seconds=20.0,
            allowed_run_types=["login_provisioning"],
            test_account_ids=set(),
            subprocess_timeout_seconds=7200,
            require_assignment=False,
            enforce_assignment_window=False,
        )
        request = {
            "id": TEST_REQUEST_ID,
            "account_id": TEST_ACCOUNT_ID,
            "run_id": TEST_RUN_ID,
            "status": "running",
        }
        summary = {
            "run_id": TEST_RUN_ID,
            "final_outcome": "wrong_app_package",
            "submit_executed": False,
        }
        with (
            patch.object(consumer, "get_account_run_request", return_value=request),
            patch.object(consumer, "complete_account_run_request") as complete,
            patch.object(consumer, "_reconcile_linked_run", return_value={"reconciled": True}) as reconcile,
            patch.object(consumer, "_safe_login_provisioner_summary_for_audit", return_value=summary),
            patch.object(consumer, "_audit") as audit,
        ):
            consumer._finalize_manual_run_after_subprocess(
                cfg,
                request_id=TEST_REQUEST_ID,
                account_id=TEST_ACCOUNT_ID,
                exit_code=1,
            )
        complete.assert_called_once()
        self.assertEqual(complete.call_args.args[2], "failed")
        reconcile.assert_called_once()
        self.assertEqual(audit.call_args.kwargs["action_type"], "manual_run_failed")

    def test_finalize_subprocess_nonzero_attaches_login_provisioner_summary_when_available(self) -> None:
        cfg = consumer.DispatcherConfig(
            enabled=True,
            health_only=False,
            launch_enabled=True,
            worker_id="run-dispatcher:test",
            poll_seconds=5.0,
            lease_seconds=120,
            heartbeat_seconds=20.0,
            allowed_run_types=["login_provisioning"],
            test_account_ids=set(),
            subprocess_timeout_seconds=7200,
            require_assignment=False,
            enforce_assignment_window=False,
        )
        request = {
            "id": TEST_REQUEST_ID,
            "account_id": TEST_ACCOUNT_ID,
            "run_id": TEST_RUN_ID,
            "status": "running",
        }
        summary = {"run_id": TEST_RUN_ID, "final_outcome": "wrong_app_package", "submit_executed": False}
        with (
            patch.object(consumer, "get_account_run_request", return_value=request),
            patch.object(consumer, "complete_account_run_request"),
            patch.object(consumer, "_reconcile_linked_run", return_value={"reconciled": True}),
            patch.object(consumer, "_safe_login_provisioner_summary_for_audit", return_value=summary),
            patch.object(consumer, "_audit") as audit,
        ):
            consumer._finalize_manual_run_after_subprocess(
                cfg,
                request_id=TEST_REQUEST_ID,
                account_id=TEST_ACCOUNT_ID,
                exit_code=1,
            )
        payload = audit.call_args.kwargs["payload"]
        self.assertEqual(payload["login_provisioner_summary"]["final_outcome"], "wrong_app_package")
        self.assertFalse(payload["login_provisioner_summary"]["submit_executed"])

    def test_finalize_subprocess_without_linked_run_skips_reconcile_patch(self) -> None:
        cfg = consumer.DispatcherConfig(
            enabled=True,
            health_only=False,
            launch_enabled=True,
            worker_id="run-dispatcher:test",
            poll_seconds=5.0,
            lease_seconds=120,
            heartbeat_seconds=20.0,
            allowed_run_types=["account_session"],
            test_account_ids=set(),
            subprocess_timeout_seconds=7200,
            require_assignment=False,
            enforce_assignment_window=False,
        )
        with (
            patch.object(
                consumer,
                "get_account_run_request",
                return_value={"id": TEST_REQUEST_ID, "status": "running"},
            ),
            patch.object(consumer, "complete_account_run_request"),
            patch.object(
                consumer,
                "reconcile_linked_ig_run_terminal",
                return_value={"reconciled": False, "reason": "no_run_id"},
            ) as reconcile,
            patch.object(consumer, "_audit"),
        ):
            consumer._finalize_manual_run_after_subprocess(
                cfg,
                request_id="req-2",
                account_id="acct-1",
                exit_code=1,
            )
        reconcile.assert_called_once()
        self.assertIsNone(reconcile.call_args.kwargs.get("run_id"))

    def test_wait_for_subprocess_terminates_on_cancel_request(self) -> None:
        cfg = consumer.DispatcherConfig(
            enabled=True,
            health_only=False,
            launch_enabled=True,
            worker_id="run-dispatcher:test",
            poll_seconds=5.0,
            lease_seconds=120,
            heartbeat_seconds=20.0,
            allowed_run_types=["account_session"],
            test_account_ids=set(),
            subprocess_timeout_seconds=7200,
            require_assignment=False,
            enforce_assignment_window=False,
        )

        class FakeProc:
            def __init__(self) -> None:
                self.terminated = False

            def poll(self) -> int | None:
                return None

            def send_signal(self, _signal: int) -> None:
                self.terminated = True

            def wait(self, timeout: int | None = None) -> int:
                return -15

            def kill(self) -> None:
                raise AssertionError("kill should not be needed after SIGTERM")

        proc = FakeProc()
        with (
            patch.object(
                consumer,
                "get_account_run_request",
                return_value={
                    "id": TEST_REQUEST_ID,
                    "status": "running",
                    "cancel_requested_at": "now",
                },
            ),
            patch.object(consumer, "log"),
        ):
            exit_code, timed_out = consumer._wait_for_subprocess(
                cfg,
                proc,  # type: ignore[arg-type]
                request_id=TEST_REQUEST_ID,
                account_id=TEST_ACCOUNT_ID,
            )
        self.assertTrue(proc.terminated)
        self.assertEqual(exit_code, -15)
        self.assertFalse(timed_out)


    def test_build_orphan_recovery_command_uses_recovery_cli(self) -> None:
        cmd = consumer._build_orphan_recovery_command(
            TEST_ACCOUNT_ID,
            TEST_REQUEST_ID,
            device_serial="RFGL145LZHE",
            package_name="com.instagram.androie",
            app_instance_id="clone-1",
            assignment_id="assignment-1",
            credentials_version=1,
        )
        self.assertIn("login_orphan_challenge_recovery_cli", cmd)
        self.assertIn("--json", cmd)
        self.assertIn(TEST_ACCOUNT_ID, cmd)


if __name__ == "__main__":
    unittest.main()
