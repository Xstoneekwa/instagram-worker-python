from __future__ import annotations

import unittest
from unittest.mock import patch

import account_run_request_consumer as consumer
import historical_auto_login_07ee_adapter as adapter


ACCOUNT_ID = "00000000-0000-4000-8000-000000000201"
REQUEST_ID = "00000000-0000-4000-8000-000000000101"
RUN_ID = "00000000-0000-4000-8000-000000000301"
APP_INSTANCE_ID = "00000000-0000-4000-8000-000000000501"
DEVICE_SERIAL = "RFGL145VCKE"
PACKAGE_NAME = "com.instagram.androif"


class HistoricalAutoLogin07eeAdapterTest(unittest.TestCase):
    def _invocation(self) -> adapter.HistoricalAutoLoginInvocation:
        return adapter.HistoricalAutoLoginInvocation(
            account_id=ACCOUNT_ID,
            request_id=REQUEST_ID,
            run_id=RUN_ID,
            expected_username="lorielebras_autom",
            device_serial=DEVICE_SERIAL,
            package_name=PACKAGE_NAME,
            app_instance_id=APP_INSTANCE_ID,
            publish=True,
            json_output=True,
        )

    def test_historical_command_transmits_binding_without_modification(self) -> None:
        command = adapter.build_historical_command(self._invocation())

        self.assertIn("historical_auto_login_07ee/instagram_login_provisioner_cli.py", command[1])
        self.assertEqual(command[command.index("--account-id") + 1], ACCOUNT_ID)
        self.assertEqual(command[command.index("--run-id") + 1], RUN_ID)
        self.assertEqual(command[command.index("--device-serial") + 1], DEVICE_SERIAL)
        self.assertEqual(command[command.index("--package-name") + 1], PACKAGE_NAME)
        self.assertEqual(command[command.index("--expected-app-instance-id") + 1], APP_INSTANCE_ID)
        self.assertIn("--publish", command)
        self.assertIn("--json", command)

    def test_adapter_propagates_engine_exit_code_without_retry(self) -> None:
        calls: list[tuple[list[str], dict[str, str]]] = []

        class Result:
            returncode = 75

        def fake_run(command: list[str], env: dict[str, str], check: bool) -> Result:
            calls.append((list(command), dict(env)))
            return Result()

        exit_code = adapter.execute_historical_engine(
            self._invocation(),
            run_process=fake_run,
            environ={"SAFE_TEST": "1"},
        )

        self.assertEqual(exit_code, 75)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1], {"SAFE_TEST": "1"})

    def test_published_connected_ready_historical_login_reuses_default_hook(self) -> None:
        with patch.object(adapter, "_read_historical_summary", return_value={
            "ok": True,
            "completed": True,
            "final_outcome": "connected",
            "status_candidate": "connected",
            "published": True,
            "publish_reason": "published_connected",
        }), patch.object(adapter.supabase_client, "load_account", return_value={
            "login_status": "connected",
            "provisioning_status": "ready",
        }), patch.object(adapter, "maybe_provision_follow_source_rotation_on_ready", return_value={
            "ok": True,
            "action": "created_row_30_4",
        }) as provision:
            result = adapter._provision_follow_source_defaults_after_historical_success(self._invocation())

        self.assertTrue(result["ok"])
        provision.assert_called_once_with(
            account_id=ACCOUNT_ID,
            account_username="lorielebras_autom",
            final_provisioning_status="ready",
            context="historical_auto_login_07ee_published_ready",
        )

    def test_historical_login_without_successful_publish_never_creates_defaults(self) -> None:
        with patch.object(adapter, "_read_historical_summary", return_value={"ok": False}), patch.object(
            adapter, "maybe_provision_follow_source_rotation_on_ready"
        ) as provision:
            result = adapter._provision_follow_source_defaults_after_historical_success(self._invocation())

        self.assertTrue(result["skipped"])
        provision.assert_not_called()

    def test_existing_custom_defaults_are_left_to_canonical_idempotent_hook(self) -> None:
        with patch.object(adapter, "_read_historical_summary", return_value={
            "ok": True, "completed": True, "final_outcome": "connected", "status_candidate": "connected",
            "published": True, "publish_reason": "published_connected",
        }), patch.object(adapter.supabase_client, "load_account", return_value={
            "login_status": "connected", "provisioning_status": "ready",
        }), patch.object(adapter, "maybe_provision_follow_source_rotation_on_ready", return_value={
            "ok": False, "action": "existing_non_contract_row_requires_explicit_repair", "db_mutation_performed": False,
        }) as provision:
            result = adapter._provision_follow_source_defaults_after_historical_success(self._invocation())

        self.assertFalse(result["ok"])
        provision.assert_called_once()

    def test_login_provisioning_routes_only_to_historical_adapter(self) -> None:
        with patch.object(consumer, "_load_expected_username", return_value="lorielebras_autom"):
            command = consumer._build_runner_command(
                ACCOUNT_ID,
                "login_provisioning",
                RUN_ID,
                run_request_id=REQUEST_ID,
                device_serial=DEVICE_SERIAL,
                package_name=PACKAGE_NAME,
                app_instance_id=APP_INSTANCE_ID,
            )

        self.assertIn("historical_auto_login_07ee_adapter", command)
        self.assertNotIn("instagram_login_provisioner_cli", command)
        self.assertEqual(command[command.index("--request-id") + 1], REQUEST_ID)
        self.assertEqual(command[command.index("--package-name") + 1], PACKAGE_NAME)

    def test_email_resume_keeps_current_cli(self) -> None:
        with patch.object(consumer, "_load_expected_username", return_value="lorielebras_autom"):
            command = consumer._build_runner_command(
                ACCOUNT_ID,
                "login_email_code_resume",
                RUN_ID,
                run_request_id=REQUEST_ID,
                device_serial=DEVICE_SERIAL,
                package_name=PACKAGE_NAME,
                app_instance_id=APP_INSTANCE_ID,
                metadata_safe={"action_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"},
            )

        self.assertIn("instagram_login_provisioner_cli", command)
        self.assertNotIn("historical_auto_login_07ee_adapter", command)

    def test_non_login_run_types_never_route_to_historical_adapter(self) -> None:
        commands = [
            consumer._build_runner_command(ACCOUNT_ID, "account_session", REQUEST_ID),
            consumer._build_runner_command(
                ACCOUNT_ID,
                "scheduled_session_preflight",
                REQUEST_ID,
                device_serial=DEVICE_SERIAL,
                package_name=PACKAGE_NAME,
                metadata_safe={"expected_username": "lorielebras_autom"},
            ),
            consumer._build_runner_command(
                ACCOUNT_ID,
                "login_orphan_challenge_recovery",
                REQUEST_ID,
                device_serial=DEVICE_SERIAL,
                package_name=PACKAGE_NAME,
                app_instance_id=APP_INSTANCE_ID,
            ),
        ]

        for command in commands:
            self.assertNotIn("historical_auto_login_07ee_adapter", command)

    def test_current_terminalization_handles_historical_exit_code(self) -> None:
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
            "id": REQUEST_ID,
            "account_id": ACCOUNT_ID,
            "run_id": RUN_ID,
            "requested_run_type": "login_provisioning",
            "status": "running",
        }

        with (
            patch.object(consumer, "get_account_run_request", return_value=request),
            patch.object(consumer, "complete_account_run_request") as complete,
            patch.object(consumer, "_reconcile_linked_run", return_value={"reconciled": True}) as reconcile,
            patch.object(consumer, "_safe_login_provisioner_summary_for_audit") as modern_summary,
            patch.object(consumer, "_terminalize_auto_login_failure") as modern_terminalization,
            patch.object(consumer, "_audit") as audit,
        ):
            consumer._finalize_manual_run_after_subprocess(
                cfg,
                request_id=REQUEST_ID,
                account_id=ACCOUNT_ID,
                exit_code=75,
            )

        complete.assert_called_once()
        self.assertEqual(complete.call_args.args[2], "failed")
        reconcile.assert_called_once()
        self.assertEqual(reconcile.call_args.kwargs["exit_code"], 75)
        audit.assert_called_once()
        self.assertEqual(audit.call_args.kwargs["action_type"], "manual_run_failed")
        modern_summary.assert_not_called()
        modern_terminalization.assert_not_called()


if __name__ == "__main__":
    unittest.main()
