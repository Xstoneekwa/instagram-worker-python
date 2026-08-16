import errno
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import logs


class FailingStream:
    def __init__(self, err, *, fail_write=False):
        self.err = err
        self.fail_write = fail_write
        self.write_calls = 0
        self.flush_calls = 0

    def write(self, _value):
        self.write_calls += 1
        if self.fail_write:
            raise self.err

    def flush(self):
        self.flush_calls += 1
        raise self.err


class LoggerStoragePressureTests(unittest.TestCase):
    def setUp(self):
        logs._reset_logging_state_for_tests()
        logs._RUN_LOG_FILE = None

    def tearDown(self):
        logs._RUN_LOG_FILE = None
        logs._reset_logging_state_for_tests()

    def test_stdout_flush_enospc_degrades_without_crash_or_repeat(self):
        stream = FailingStream(OSError(errno.ENOSPC, "full"))
        with mock.patch("logs.sys.stdout", stream), mock.patch("logs.os.write"):
            logs.log("info", "one")
            logs.log("info", "two")
        self.assertEqual(stream.write_calls, 1)
        self.assertEqual(stream.flush_calls, 1)
        self.assertTrue(logs.logging_health()["stdout_degraded"])
        self.assertEqual(logs.logging_health()["buffered_lines"], 0)

    def test_stdout_write_enospc_has_no_recursive_failure(self):
        stream = FailingStream(OSError(errno.ENOSPC, "full"), fail_write=True)
        with mock.patch("logs.sys.stdout", stream), mock.patch("logs.os.write") as raw:
            logs.log("info", "one")
            logs.log("info", "two")
        self.assertEqual(stream.write_calls, 1)
        self.assertEqual(raw.call_count, 1)

    def test_eio_is_classified(self):
        stream = FailingStream(OSError(errno.EIO, "io"), fail_write=True)
        with mock.patch("logs.sys.stdout", stream), mock.patch("logs.os.write"):
            logs.log("info", "one")
        self.assertTrue(logs.logging_health()["storage_pressure"]["active"])

    def test_other_os_output_error_degrades_without_false_storage_pressure(self):
        stream = FailingStream(OSError(errno.EBADF, "closed"), fail_write=True)
        with mock.patch("logs.sys.stdout", stream), mock.patch("logs.os.write"):
            logs.log("info", "one")
            logs.log("info", "two")
        self.assertEqual(stream.write_calls, 1)
        self.assertTrue(logs.logging_health()["stdout_degraded"])
        self.assertFalse(logs.logging_health()["storage_pressure"]["active"])

    def test_unexpected_programming_exception_is_not_swallowed(self):
        class Broken:
            def write(self, _value):
                raise ValueError("bug")
        with mock.patch("logs.sys.stdout", Broken()):
            with self.assertRaises(ValueError):
                logs.log("info", "one")

    def test_run_log_retention_is_bounded_and_keeps_newest_evidence(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            paths = []
            for index in range(4):
                path = root / f"run_20260816_{index}.log"
                path.write_bytes(b"x" * 10)
                os.utime(path, (100 + index, 100 + index))
                paths.append(path)
            with mock.patch.dict(
                os.environ,
                {
                    "PHONEFARM_RUN_LOG_MAX_FILES": "2",
                    "PHONEFARM_RUN_LOG_MAX_TOTAL_BYTES": "1000",
                    "PHONEFARM_RUN_LOG_MAX_AGE_SECONDS": "0",
                },
            ):
                logs._apply_run_log_retention(root)
            self.assertFalse(paths[0].exists())
            self.assertFalse(paths[1].exists())
            self.assertTrue(paths[2].exists())
            self.assertTrue(paths[3].exists())


if __name__ == "__main__":
    unittest.main()
