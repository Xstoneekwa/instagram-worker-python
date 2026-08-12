from __future__ import annotations

import ast
from pathlib import Path
import unittest
from unittest.mock import patch

import account_run_request_consumer as consumer


ROOT = Path(__file__).resolve().parents[1]
ACCOUNT_ID = "00000000-0000-4000-8000-000000000201"
REQUEST_ID = "00000000-0000-4000-8000-000000000101"
RUN_ID = "00000000-0000-4000-8000-000000000301"
APP_INSTANCE_ID = "00000000-0000-4000-8000-000000000501"
DEVICE_SERIAL = "RFGL145VCKE"
PACKAGE_NAME = "com.instagram.androif"


class CanonicalAutoLoginRoutingTest(unittest.TestCase):
    def _command(self, run_type: str = "login_provisioning") -> list[str]:
        with patch.object(consumer, "_load_expected_username", return_value="lorielebras_autom"):
            return consumer._build_runner_command(
                ACCOUNT_ID,
                run_type,
                RUN_ID,
                run_request_id=REQUEST_ID,
                device_serial=DEVICE_SERIAL,
                package_name=PACKAGE_NAME,
                app_instance_id=APP_INSTANCE_ID,
            )

    def test_login_provisioning_routes_directly_to_canonical_cli(self) -> None:
        command = self._command()

        self.assertEqual(command[command.index("-m") + 1], "instagram_login_provisioner_cli")
        self.assertNotIn("historical_auto_login_07ee_adapter", command)
        self.assertEqual(
            command[command.index("--request-id") + 1],
            "00000000-0000-4000-8000-000000000101",
        )
        self.assertEqual(command[command.index("--package-name") + 1], PACKAGE_NAME)
        self.assertEqual(command[command.index("--expected-app-instance-id") + 1], APP_INSTANCE_ID)

    def test_email_resume_uses_the_same_canonical_cli(self) -> None:
        command = self._command("login_email_code_resume")

        self.assertEqual(command[command.index("-m") + 1], "instagram_login_provisioner_cli")
        self.assertNotIn("historical_auto_login_07ee_adapter", command)

    def test_authoritative_runtime_implementation_count_is_one(self) -> None:
        consumer_source = (ROOT / "account_run_request_consumer.py").read_text(encoding="utf-8")
        cli_source = (ROOT / "instagram_login_provisioner_cli.py").read_text(encoding="utf-8")

        self.assertNotIn('"historical_auto_login_07ee_adapter"', consumer_source)
        self.assertIn("run_login_provisioning_flow", cli_source)
        runtime_entrypoints = [
            module
            for module in ("instagram_login_provisioner_cli", "historical_auto_login_07ee_adapter")
            if f'cli_module = "{module}"' in consumer_source
        ]
        self.assertEqual(runtime_entrypoints, ["instagram_login_provisioner_cli"])

    def test_canonical_engine_contains_required_modern_contracts(self) -> None:
        cli_source = (ROOT / "instagram_login_provisioner_cli.py").read_text(encoding="utf-8")
        orchestrator_source = (ROOT / "instagram_login_provisioner_orchestrator.py").read_text(encoding="utf-8")
        executor_source = (ROOT / "instagram_login_password_form_executor.py").read_text(encoding="utf-8")

        parsed_cli = ast.parse(cli_source)
        imported_names = {
            alias.name
            for node in ast.walk(parsed_cli)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }
        self.assertIn("run_login_provisioning_flow", imported_names)
        for channel in (
            "email_code_challenge",
            "sms_code_challenge",
            "whatsapp_code_challenge",
            "authenticator_app_code_challenge",
        ):
            self.assertIn(channel, cli_source)
        self.assertIn("returned_login_form_recovery", executor_source)
        self.assertIn("_verify_connected_identity_before_ready", orchestrator_source)
        self.assertIn("follow_source_rotation_provision", orchestrator_source)


if __name__ == "__main__":
    unittest.main()
