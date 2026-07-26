"""Runner-side request terminalization after session cleanup."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import runner


class RunnerRequestTerminalizationTest(unittest.TestCase):
    def tearDown(self) -> None:
        runner._CURRENT_RUN_REQUEST_ID = None

    def test_terminalize_after_cleanup_maps_completed_idempotently(self) -> None:
        runner._CURRENT_RUN_REQUEST_ID = "00000000-0000-4000-8000-000000000101"
        with (
            patch("account_run_control.complete_account_run_request", return_value={"status": "completed"}) as complete,
            patch.object(runner, "_run_control_dispatcher_worker_id", return_value="run-dispatcher:test"),
            patch.object(runner, "log"),
        ):
            runner._terminalize_run_request_after_cleanup("completed")

        complete.assert_called_once_with(
            "00000000-0000-4000-8000-000000000101",
            "run-dispatcher:test",
            "completed",
        )

    def test_terminalize_after_cleanup_maps_stopped_to_canceled(self) -> None:
        runner._CURRENT_RUN_REQUEST_ID = "00000000-0000-4000-8000-000000000101"
        with (
            patch("account_run_control.complete_account_run_request") as complete,
            patch.object(runner, "_run_control_dispatcher_worker_id", return_value="run-dispatcher:test"),
            patch.object(runner, "log"),
        ):
            runner._terminalize_run_request_after_cleanup("stopped")

        self.assertEqual(complete.call_args.args[2], "canceled")


if __name__ == "__main__":
    unittest.main()
