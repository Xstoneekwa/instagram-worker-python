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


if __name__ == "__main__":
    unittest.main()
