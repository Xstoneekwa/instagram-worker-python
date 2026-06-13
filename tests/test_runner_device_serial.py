"""Runner serial propagation tests with all device and Supabase effects mocked."""

from __future__ import annotations

import sys
import unittest
from unittest.mock import patch

import runner


class RunnerDeviceSerialTest(unittest.TestCase):
    def test_runner_passes_explicit_device_serial_to_connect_device(self) -> None:
        account_id = "00000000-0000-4000-8000-000000000201"
        run_id = "00000000-0000-4000-8000-000000000301"
        fake_device = type("FakeDevice", (), {"info": {"ok": True}})()

        def fake_supabase_call(name: str, **_kwargs):
            if name == "load_account":
                return {"id": account_id, "username": "cinema_catchup"}
            if name == "create_run":
                return {"id": run_id}
            return None

        argv = [
            "runner.py",
            "--account-id",
            account_id,
            "--run-type",
            "account_session",
            "--device-serial",
            "emulator-5554",
        ]
        with (
            patch.object(sys, "argv", argv),
            patch.object(runner, "_safe_supabase_call", side_effect=fake_supabase_call),
            patch.object(runner, "_load_account_session_follow_targets", return_value=([], None)),
            patch.object(runner, "_abort_if_run_request_canceled", return_value=False),
            patch.object(runner.supabase_client, "set_log_context"),
            patch.object(runner.supabase_client, "log_performance_event"),
            patch.object(runner, "init_run_file_logging", return_value=None),
            patch.object(runner, "_orf_set_runtime_context"),
            patch.object(runner, "_orf_publish_event"),
            patch.object(runner, "_orf_heartbeat_worker"),
            patch.object(runner, "connect_device", return_value=fake_device) as connect,
            patch.object(runner, "disable_android_animations"),
            patch.object(runner, "health_check", return_value=False),
            patch.object(runner, "_update_run_status_safe"),
            patch.object(runner, "reset_perf_counters"),
            patch.object(runner, "_emit_performance_summary"),
            patch.object(runner.config, "ACCOUNT_ASSIGNMENT_DISPATCH_ENABLED", False),
        ):
            exit_code = runner.main()

        self.assertNotEqual(exit_code, 0)
        connect.assert_called_once_with("emulator-5554")


if __name__ == "__main__":
    unittest.main()
