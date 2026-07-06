"""Runner clone-package propagation tests (dispatcher --package-name -> config)."""

from __future__ import annotations

import sys
import unittest
from unittest.mock import patch

import runner


ACCOUNT_ID = "00000000-0000-4000-8000-000000000201"
RUN_ID = "00000000-0000-4000-8000-000000000301"
CLONE_PACKAGE = "com.instagram.androif"


def _base_argv(*extra: str) -> list[str]:
    return [
        "runner.py",
        "--account-id",
        ACCOUNT_ID,
        "--run-type",
        "account_session",
        "--device-serial",
        "emulator-5554",
        *extra,
    ]


def _fake_supabase_call(name: str, **_kwargs):
    if name == "load_account":
        return {"id": ACCOUNT_ID, "username": "mythyl_fitness"}
    if name == "create_run":
        return {"id": RUN_ID}
    return None


class RunnerPackageDispatchTest(unittest.TestCase):
    def _run_main(self, argv: list[str], **extra_patches):
        fake_device = type("FakeDevice", (), {"info": {"ok": True}})()
        seen: dict[str, str] = {}

        def capture_package(*_a, **_k):
            # health_check is the first call after connect; capture the
            # effective package at that point then abort the run early.
            seen["package"] = str(runner.config.INSTAGRAM_PACKAGE or "")
            return False

        patches = [
            patch.object(sys, "argv", argv),
            patch.object(runner, "_safe_supabase_call", side_effect=_fake_supabase_call),
            patch.object(runner, "_load_account_session_follow_targets", return_value=([], None)),
            patch.object(runner, "_abort_if_run_request_canceled", return_value=False),
            patch.object(runner.supabase_client, "set_log_context"),
            patch.object(runner.supabase_client, "log_performance_event"),
            patch.object(runner, "init_run_file_logging", return_value=None),
            patch.object(runner, "_orf_set_runtime_context"),
            patch.object(runner, "_orf_publish_event"),
            patch.object(runner, "_orf_heartbeat_worker"),
            patch.object(runner, "connect_device", return_value=fake_device),
            patch.object(runner, "disable_android_animations"),
            patch.object(runner, "health_check", side_effect=capture_package),
            patch.object(runner, "_update_run_status_safe"),
            patch.object(runner, "reset_perf_counters"),
            patch.object(runner, "_emit_performance_summary"),
            patch.object(runner.config, "INSTAGRAM_PACKAGE", "com.instagram.android"),
        ]
        patches.extend(extra_patches.values())
        for p in patches:
            p.start()
        try:
            exit_code = runner.main()
        finally:
            for p in reversed(patches):
                p.stop()
        return exit_code, seen

    def test_cli_package_name_overrides_default_primary_package(self) -> None:
        argv = _base_argv("--package-name", CLONE_PACKAGE)
        exit_code, seen = self._run_main(
            argv,
            dispatch_disabled=patch.object(
                runner.config, "ACCOUNT_ASSIGNMENT_DISPATCH_ENABLED", False
            ),
        )
        self.assertNotEqual(exit_code, 0)
        self.assertEqual(seen.get("package"), CLONE_PACKAGE)

    def test_without_cli_package_runner_keeps_default_package(self) -> None:
        argv = _base_argv()
        exit_code, seen = self._run_main(
            argv,
            dispatch_disabled=patch.object(
                runner.config, "ACCOUNT_ASSIGNMENT_DISPATCH_ENABLED", False
            ),
        )
        self.assertNotEqual(exit_code, 0)
        self.assertEqual(seen.get("package"), "com.instagram.android")

    def test_assignment_resolver_package_wins_over_cli_on_divergence(self) -> None:
        dispatch_ctx = {
            "assignment_found": True,
            "assignment_id": "assignment-1",
            "account_id": ACCOUNT_ID,
            "assignment_type": "full_cycle",
            "slot_kind": "full_cycle_6h",
            "device_id": "device-1",
            "clone_id": None,
            "app_instance_id": "app-instance-1",
            "device_kind": "physical_phone",
            "adb_serial": "emulator-5554",
            "package_name": "com.instagram.androig",
            "source": "account_assignments",
            "fallback_used": False,
            "reason": "assignment_resolved",
            "run_type": "account_session",
        }
        argv = _base_argv("--package-name", CLONE_PACKAGE)
        exit_code, seen = self._run_main(
            argv,
            dispatch_enabled=patch.object(
                runner.config, "ACCOUNT_ASSIGNMENT_DISPATCH_ENABLED", True
            ),
            dispatch_run_types=patch.object(
                runner.config,
                "ACCOUNT_ASSIGNMENT_DISPATCH_RUN_TYPES",
                "account_session",
            ),
            resolver=patch.object(
                runner,
                "resolve_account_assignment_runtime_context",
                return_value=dispatch_ctx,
            ),
        )
        self.assertNotEqual(exit_code, 0)
        self.assertEqual(seen.get("package"), "com.instagram.androig")


if __name__ == "__main__":
    unittest.main()
