from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

import device


class DeviceAdbResolveTests(unittest.TestCase):
    def setUp(self) -> None:
        device.resolve_adb_path.cache_clear()

    def tearDown(self) -> None:
        device.resolve_adb_path.cache_clear()

    def test_resolve_adb_path_uses_adb_path_env(self) -> None:
        fake = "/tmp/fake-adb-path-for-tests"
        with (
            patch.object(Path, "is_file", return_value=True),
            patch.object(os, "access", return_value=True),
            patch.dict(os.environ, {"ADB_PATH": fake}, clear=False),
        ):
            device.resolve_adb_path.cache_clear()
            self.assertEqual(device.resolve_adb_path(), fake)

    def test_runner_subprocess_env_prepends_adb_dir_to_path(self) -> None:
        fake = "/tmp/fake-adb-path-for-tests"
        with patch.object(device, "resolve_adb_path", return_value=fake):
            env = device.runner_subprocess_env({"PATH": "/usr/bin"})
        self.assertEqual(env["ADB_PATH"], fake)
        self.assertTrue(env["PATH"].startswith("/tmp:"))
        self.assertIn("/usr/bin", env["PATH"])

    def test_adb_available_false_when_unresolved(self) -> None:
        with patch.object(device, "resolve_adb_path", return_value=None):
            self.assertFalse(device.adb_available())

    def test_ensure_adb_keyboard_ready_sets_default_when_available(self) -> None:
        first = {
            "adb_path_resolved": True,
            "adb_keyboard_package_present": True,
            "adb_keyboard_ime_listed": True,
            "adb_keyboard_default": False,
            "reason": "adb_keyboard_not_default",
        }
        second = {**first, "adb_keyboard_default": True, "reason": "adb_keyboard_ready"}
        with (
            patch.object(device, "inspect_adb_keyboard_state", side_effect=[first, second]),
            patch.object(device, "set_ime", return_value=True) as set_ime,
        ):
            result = device.ensure_adb_keyboard_ready("serial", fast_ime_id="com.android.adbkeyboard/.AdbIME")

        self.assertTrue(result["ok"])
        self.assertTrue(result["set_default_attempted"])
        set_ime.assert_called_once()

    def test_ensure_adb_keyboard_ready_fails_clearly_when_package_missing(self) -> None:
        state = {
            "adb_path_resolved": True,
            "adb_keyboard_package_present": False,
            "adb_keyboard_ime_listed": False,
            "adb_keyboard_default": False,
            "reason": "adb_keyboard_package_missing",
        }
        with (
            patch.object(device, "inspect_adb_keyboard_state", return_value=state),
            patch.object(device, "set_ime") as set_ime,
        ):
            result = device.ensure_adb_keyboard_ready("serial", fast_ime_id="com.android.adbkeyboard/.AdbIME")

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "adb_keyboard_package_missing")
        set_ime.assert_not_called()

    def test_run_adb_keyboard_b64_input_detailed_reports_broadcast_failure(self) -> None:
        ready = {
            "ok": True,
            "adb_path_resolved": True,
            "adb_keyboard_package_present": True,
            "adb_keyboard_ime_listed": True,
            "adb_keyboard_default": True,
            "reason": "adb_keyboard_ready",
        }
        with (
            patch.object(device, "ensure_adb_keyboard_ready", return_value=ready),
            patch.object(device, "_adb_shell_stdin", return_value=(1, "", "broadcast failed")),
        ):
            result = device.run_adb_keyboard_b64_input_detailed(
                "serial",
                "safe-test",
                fast_ime_id="com.android.adbkeyboard/.AdbIME",
            )

        self.assertFalse(result["broadcast_ok"])
        self.assertEqual(result["reason"], "adb_keyboard_broadcast_failed")

    def test_run_adb_keyboard_b64_input_detailed_falls_back_to_text_action(self) -> None:
        ready = {
            "ok": True,
            "adb_path_resolved": True,
            "adb_keyboard_package_present": True,
            "adb_keyboard_ime_listed": True,
            "adb_keyboard_default": True,
            "reason": "adb_keyboard_ready",
        }
        with (
            patch.object(device, "ensure_adb_keyboard_ready", return_value=ready),
            patch.object(device, "_adb_shell_stdin", side_effect=[(1, "", ""), (0, "", "")]) as shell_stdin,
        ):
            result = device.run_adb_keyboard_b64_input_detailed(
                "serial",
                "safe-test",
                fast_ime_id="com.android.adbkeyboard/.AdbIME",
            )

        self.assertTrue(result["broadcast_ok"])
        self.assertEqual(result["method"], "adb_keyboard_text")
        self.assertFalse(result["b64_broadcast_ok"])
        self.assertEqual(shell_stdin.call_count, 2)


if __name__ == "__main__":
    unittest.main()
