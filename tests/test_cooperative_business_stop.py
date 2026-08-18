from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock
from types import SimpleNamespace

import account_run_request_consumer as consumer

from cooperative_business_stop import (
    StopContext,
    acknowledge_stop,
    action_start_allowed,
    clear_context,
    follow_handoff_deadline,
    read_ack,
    read_stop,
    request_stop,
)


class CooperativeBusinessStopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(
            os.environ,
            {"COOPERATIVE_STOP_CONTROL_DIR": self.tmp.name},
            clear=False,
        )
        self.env.start()
        self.context = StopContext(
            account_id="account-a",
            request_id="request-a",
            device_id="phone-a",
            business_session_id="session-a",
            generation="generation-a",
        )

    def tearDown(self) -> None:
        clear_context(self.context)
        self.env.stop()
        self.tmp.cleanup()

    def test_intent_is_idempotent_and_ack_is_exactly_bound(self) -> None:
        first = request_stop(self.context, reason="scheduled_business_deadline")
        second = request_stop(self.context, reason="scheduled_business_deadline")
        self.assertEqual(first, second)
        ack = acknowledge_stop(
            self.context,
            phase="follow",
            safe_boundary="before_candidate_selection",
            session_termination_class="scheduled_safe_stop",
        )
        self.assertEqual(read_ack(self.context), ack)
        other = StopContext(
            account_id="account-b",
            request_id="request-a",
            device_id="phone-a",
            business_session_id="session-a",
            generation="generation-a",
        )
        self.assertIsNone(read_stop(other))

    def test_old_session_token_cannot_stop_new_session(self) -> None:
        request_stop(self.context, reason="scheduled_business_deadline")
        new_context = StopContext(
            account_id="account-a",
            request_id="request-new",
            device_id="phone-a",
            business_session_id="session-new",
            generation="generation-new",
        )
        self.assertIsNone(read_stop(new_context))

    def test_action_start_guard_uses_bounded_remaining_time(self) -> None:
        now = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
        deadline = (now + timedelta(seconds=20)).isoformat()
        self.assertTrue(action_start_allowed(deadline=deadline, bounded_action_seconds=15, now=now))
        self.assertFalse(action_start_allowed(deadline=deadline, bounded_action_seconds=21, now=now))

    def test_follow_handoff_is_dynamic_and_absent_without_unfollow_work(self) -> None:
        deadline = "2026-08-18T16:00:00Z"
        self.assertIsNone(
            follow_handoff_deadline(
                business_deadline=deadline,
                eligible_unfollows=0,
                unfollow_quota_remaining=120,
                estimated_seconds_per_unfollow=15,
            )
        )
        self.assertEqual(
            follow_handoff_deadline(
                business_deadline=deadline,
                eligible_unfollows=3,
                unfollow_quota_remaining=2,
                estimated_seconds_per_unfollow=15,
                navigation_reserve_seconds=30,
                recovery_reserve_seconds=75,
            ),
            "2026-08-18T15:57:45Z",
        )

    def test_scheduled_session_is_not_killed_by_fixed_7200_seconds(self) -> None:
        proc = mock.Mock()
        proc.poll.side_effect = [None, 0]
        cfg = SimpleNamespace(
            subprocess_timeout_seconds=7200,
            heartbeat_seconds=15,
            worker_id="worker-a",
        )
        with mock.patch.object(consumer, "get_account_run_request", return_value={"status": "running"}), \
             mock.patch.object(consumer.time, "monotonic", side_effect=[0.0, 8000.0, 8001.0]), \
             mock.patch.object(consumer.time, "sleep", return_value=None), \
             mock.patch.object(consumer, "_terminate_subprocess") as terminate:
            result = consumer._wait_for_subprocess(
                cfg,
                proc,
                request_id="request-a",
                account_id="account-a",
                stop_context=self.context,
                business_action_deadline="2099-08-18T15:50:00Z",
                scheduled_session_end="2099-08-18T16:00:00Z",
            )
        self.assertEqual(result, (0, False))
        terminate.assert_not_called()

    def test_preemptive_worker_ack_is_captured_before_terminal_cleanup(self) -> None:
        request_stop(
            self.context,
            reason="scheduled_business_deadline",
            deadline="2099-08-18T15:50:00Z",
        )
        acknowledge_stop(
            self.context,
            phase="follow",
            safe_boundary="before_candidate_selection",
            session_termination_class="scheduled_safe_stop",
        )
        proc = mock.Mock()
        proc.poll.return_value = 0
        cfg = SimpleNamespace(
            subprocess_timeout_seconds=7200,
            heartbeat_seconds=15,
            worker_id="worker-a",
        )
        diagnostics: dict[str, object] = {}
        result = consumer._wait_for_subprocess(
            cfg,
            proc,
            request_id="request-a",
            account_id="account-a",
            stop_context=self.context,
            business_action_deadline="2099-08-18T15:50:00Z",
            scheduled_session_end="2099-08-18T16:00:00Z",
            diagnostics=diagnostics,
        )
        self.assertEqual(result, (0, False))
        self.assertTrue(diagnostics["acknowledged"])
        self.assertEqual(
            diagnostics["originating_stop_reason"],
            "scheduled_business_deadline",
        )
        self.assertEqual(diagnostics["phase"], "follow")

    def test_hard_device_boundary_uses_watchdog_not_manual_stop(self) -> None:
        proc = mock.Mock()
        proc.poll.return_value = None
        cfg = SimpleNamespace(
            subprocess_timeout_seconds=7200,
            heartbeat_seconds=15,
            worker_id="worker-a",
        )
        diagnostics: dict[str, object] = {}
        with mock.patch.object(consumer, "get_account_run_request", return_value={"status": "running"}), \
             mock.patch.object(consumer, "_terminate_subprocess", return_value=143) as terminate:
            result = consumer._wait_for_subprocess(
                cfg,
                proc,
                request_id="request-a",
                account_id="account-a",
                stop_context=self.context,
                business_action_deadline="2020-08-18T15:50:00Z",
                scheduled_session_end="2020-08-18T16:00:00Z",
                diagnostics=diagnostics,
            )
        self.assertEqual(result, (143, True))
        self.assertEqual(diagnostics["originating_stop_reason"], "dispatcher_watchdog")
        terminate.assert_called_once()


if __name__ == "__main__":
    unittest.main()
