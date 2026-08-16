import errno
import os
import unittest
from unittest import mock

import storage_health


class StorageHealthTests(unittest.TestCase):
    def setUp(self):
        storage_health._reset_for_tests()
        self.env = mock.patch.dict(
            os.environ,
            {
                "PHONEFARM_STORAGE_WARNING_FREE_BYTES": "1000",
                "PHONEFARM_STORAGE_CRITICAL_FREE_BYTES": "100",
                "PHONEFARM_STORAGE_WARNING_FREE_PERCENT": "10",
                "PHONEFARM_STORAGE_CRITICAL_FREE_PERCENT": "1",
            },
            clear=False,
        )
        self.env.start()

    def tearDown(self):
        self.env.stop()
        storage_health._reset_for_tests()

    def snapshot(self, free):
        return mock.Mock(total=10_000, used=10_000 - free, free=free)

    def test_healthy_warning_and_critical_thresholds(self):
        with mock.patch("storage_health.shutil.disk_usage", return_value=self.snapshot(5_000)):
            self.assertEqual(storage_health.inspect_storage_health(force=True).status, "healthy")
        with mock.patch("storage_health.shutil.disk_usage", return_value=self.snapshot(900)):
            self.assertEqual(storage_health.inspect_storage_health(force=True).status, "warning")
        with mock.patch("storage_health.shutil.disk_usage", return_value=self.snapshot(50)):
            snap = storage_health.inspect_storage_health(force=True)
            self.assertEqual(snap.status, "critical")
            self.assertFalse(snap.new_run_allowed)

    def test_pressure_latch_blocks_action_until_real_health_restore(self):
        storage_health.mark_storage_pressure(OSError(errno.ENOSPC, "full"), source="test")
        with mock.patch("storage_health.shutil.disk_usage", return_value=self.snapshot(5_000)):
            with self.assertRaises(storage_health.HostStorageCriticalError):
                storage_health.require_irreversible_action_allowed(boundary="candidate")
            self.assertTrue(storage_health.reset_storage_pressure_after_health_restore())
            self.assertTrue(storage_health.require_new_run_allowed().new_run_allowed)

    def test_sqlite_disk_io_error_is_classified(self):
        self.assertTrue(storage_health.is_storage_io_error(RuntimeError("disk I/O error")))
        self.assertFalse(storage_health.is_storage_io_error(RuntimeError("programming bug")))


if __name__ == "__main__":
    unittest.main()
