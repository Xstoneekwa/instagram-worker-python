import unittest
from unittest import mock
from pathlib import Path

import instagram_navigation
import runner
import storage_health


class _Element:
    def __init__(self):
        self.info = {"bounds": {"top": 100, "bottom": 140, "right": 300}}
    def wait(self, timeout=0):
        return True


class _Device:
    def __init__(self):
        self.tap_count = 0
        self.element = _Element()
    def __call__(self, **_kwargs):
        return self.element
    def click(self, *_args):
        self.tap_count += 1


class Follow60StorageBusinessGuardTests(unittest.TestCase):
    def tearDown(self):
        runner._CURRENT_RUN_ID = None
        runner._CURRENT_ACCOUNT_ID = None
        runner._CURRENT_RUN_REQUEST_ID = None
        runner._CURRENT_DEVICE = None
        runner._RUNTIME_FOLLOW_COUNT = 0

    def test_critical_storage_blocks_mute_toggle_without_tap(self):
        device = _Device()
        error = storage_health.HostStorageCriticalError(
            "test",
            storage_health.StorageSnapshot(
                status="critical", filesystem_path="/", total_bytes=1,
                free_bytes=0, free_percent=0.0, warning_free_bytes=1,
                critical_free_bytes=1, warning_free_percent=1.0,
                critical_free_percent=1.0, checked_at_monotonic=1.0,
                reason="host_storage_critical",
            ),
        )
        with mock.patch(
            "instagram_navigation._visual_switch_checked_near_row",
            return_value=False,
        ), mock.patch(
            "instagram_navigation.storage_health.require_irreversible_action_allowed",
            side_effect=error,
        ):
            with self.assertRaises(storage_health.HostStorageCriticalError):
                instagram_navigation._visual_tap_toggle_row_for_label(
                    device, ("Posts",), 1080
                )
        self.assertEqual(device.tap_count, 0)

    def test_follow_and_like_guards_are_immediately_before_taps(self):
        source = Path(instagram_navigation.__file__).read_text(encoding="utf-8")
        self.assertIn(
            '_require_irreversible_social_action_storage("follow_pre_tap")\n'
            "        if tap_exact and tap_coords_ready:",
            source,
        )
        self.assertIn(
            '_require_irreversible_social_action_storage("like_pre_tap")\n'
            "            d.click(tap_x, tap_y)",
            source,
        )

    def test_in_run_storage_failure_preserves_confirmed_count_and_cleans_up(self):
        snapshot = storage_health.StorageSnapshot(
            status="critical", filesystem_path="/", total_bytes=100,
            free_bytes=0, free_percent=0.0, warning_free_bytes=10,
            critical_free_bytes=2, warning_free_percent=5.0,
            critical_free_percent=2.0, checked_at_monotonic=1.0,
            reason="host_storage_critical",
        )
        runner._CURRENT_RUN_ID = "run-1"
        runner._CURRENT_ACCOUNT_ID = "account-1"
        runner._CURRENT_RUN_REQUEST_ID = "request-1"
        runner._CURRENT_DEVICE = object()
        runner._RUNTIME_FOLLOW_COUNT = 7
        with mock.patch(
            "runner._main_impl",
            side_effect=storage_health.HostStorageCriticalError("candidate", snapshot),
        ), mock.patch("runner._update_run_status_safe") as update, mock.patch(
            "runner._return_with_cleanup", return_value=78
        ) as cleanup, mock.patch("runner.log"):
            self.assertEqual(runner.main(), 78)
        self.assertEqual(update.call_args.kwargs["totals"]["success"], 7)
        self.assertTrue(
            update.call_args.kwargs["performance_summary"]["no_false_action_receipt"]
        )
        cleanup.assert_called_once_with(runner._CURRENT_DEVICE, 78)


if __name__ == "__main__":
    unittest.main()
