"""Tests for Run Control dispatcher."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import account_run_request_consumer as consumer


class AccountRunRequestConsumerTest(unittest.TestCase):
    def test_load_dispatcher_config_defaults(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            cfg = consumer.load_dispatcher_config()
        self.assertFalse(cfg.enabled)
        self.assertTrue(cfg.health_only)
        self.assertFalse(cfg.launch_enabled)

    def test_dispatcher_is_healthy_false_when_disabled(self) -> None:
        cfg = consumer.DispatcherConfig(
            enabled=False,
            health_only=True,
            launch_enabled=False,
            worker_id="run-dispatcher:test",
            poll_seconds=5.0,
            lease_seconds=120,
            heartbeat_seconds=20.0,
            allowed_run_types=["account_session"],
            test_account_ids=set(),
            subprocess_timeout_seconds=7200,
            require_assignment=False,
            enforce_assignment_window=False,
        )
        self.assertFalse(consumer.dispatcher_is_healthy(cfg))

    def test_dispatcher_is_healthy_true_for_fresh_heartbeat(self) -> None:
        cfg = consumer.DispatcherConfig(
            enabled=True,
            health_only=True,
            launch_enabled=False,
            worker_id="run-dispatcher:test",
            poll_seconds=5.0,
            lease_seconds=120,
            heartbeat_seconds=20.0,
            allowed_run_types=["account_session"],
            test_account_ids=set(),
            subprocess_timeout_seconds=7200,
            require_assignment=False,
            enforce_assignment_window=False,
        )
        fresh = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
        with patch.object(
            consumer.supabase_client,
            "_request_json",
            return_value=[{"worker_id": "run-dispatcher:test", "status": "idle", "last_seen_at": fresh}],
        ):
            self.assertTrue(consumer.dispatcher_is_healthy(cfg))

    def test_run_once_health_only_skips_claim(self) -> None:
        cfg = consumer.DispatcherConfig(
            enabled=True,
            health_only=True,
            launch_enabled=False,
            worker_id="run-dispatcher:test",
            poll_seconds=5.0,
            lease_seconds=120,
            heartbeat_seconds=20.0,
            allowed_run_types=["account_session"],
            test_account_ids=set(),
            subprocess_timeout_seconds=7200,
            require_assignment=False,
            enforce_assignment_window=False,
        )
        with (
            patch.object(consumer, "_heartbeat") as heartbeat,
            patch.object(consumer, "reclaim_stale_account_run_requests", return_value=0) as reclaim,
            patch.object(consumer, "claim_next_account_run_request") as claim,
        ):
            result = consumer.run_once(cfg)
        self.assertEqual(result["mode"], "health_only")
        heartbeat.assert_called_once()
        reclaim.assert_called_once()
        claim.assert_not_called()

    def test_finalize_subprocess_nonzero_reconciles_linked_run(self) -> None:
        cfg = consumer.DispatcherConfig(
            enabled=True,
            health_only=False,
            launch_enabled=True,
            worker_id="run-dispatcher:test",
            poll_seconds=5.0,
            lease_seconds=120,
            heartbeat_seconds=20.0,
            allowed_run_types=["account_session"],
            test_account_ids=set(),
            subprocess_timeout_seconds=7200,
            require_assignment=False,
            enforce_assignment_window=False,
        )
        request = {
            "id": "req-1",
            "account_id": "acct-1",
            "run_id": "run-1",
            "status": "running",
        }
        with (
            patch.object(consumer, "get_account_run_request", return_value=request),
            patch.object(consumer, "complete_account_run_request") as complete,
            patch.object(consumer, "_reconcile_linked_run", return_value={"reconciled": True}) as reconcile,
            patch.object(consumer, "_audit") as audit,
        ):
            consumer._finalize_manual_run_after_subprocess(
                cfg,
                request_id="req-1",
                account_id="acct-1",
                exit_code=1,
            )
        complete.assert_called_once()
        self.assertEqual(complete.call_args.args[2], "failed")
        reconcile.assert_called_once()
        self.assertEqual(reconcile.call_args.kwargs["terminal_status"], "failed")
        audit.assert_called_once()
        self.assertEqual(audit.call_args.kwargs["action_type"], "manual_run_failed")

    def test_finalize_subprocess_without_linked_run_skips_reconcile_patch(self) -> None:
        cfg = consumer.DispatcherConfig(
            enabled=True,
            health_only=False,
            launch_enabled=True,
            worker_id="run-dispatcher:test",
            poll_seconds=5.0,
            lease_seconds=120,
            heartbeat_seconds=20.0,
            allowed_run_types=["account_session"],
            test_account_ids=set(),
            subprocess_timeout_seconds=7200,
            require_assignment=False,
            enforce_assignment_window=False,
        )
        with (
            patch.object(consumer, "get_account_run_request", return_value={"id": "req-2", "status": "running"}),
            patch.object(consumer, "complete_account_run_request"),
            patch.object(
                consumer,
                "reconcile_linked_ig_run_terminal",
                return_value={"reconciled": False, "reason": "no_run_id"},
            ) as reconcile,
            patch.object(consumer, "_audit"),
        ):
            consumer._finalize_manual_run_after_subprocess(
                cfg,
                request_id="req-2",
                account_id="acct-1",
                exit_code=1,
            )
        reconcile.assert_called_once()
        self.assertIsNone(reconcile.call_args.kwargs.get("run_id"))

    def test_wait_for_subprocess_terminates_on_cancel_request(self) -> None:
        cfg = consumer.DispatcherConfig(
            enabled=True,
            health_only=False,
            launch_enabled=True,
            worker_id="run-dispatcher:test",
            poll_seconds=5.0,
            lease_seconds=120,
            heartbeat_seconds=20.0,
            allowed_run_types=["account_session"],
            test_account_ids=set(),
            subprocess_timeout_seconds=7200,
            require_assignment=False,
            enforce_assignment_window=False,
        )

        class FakeProc:
            def __init__(self) -> None:
                self.terminated = False

            def poll(self) -> int | None:
                return None

            def send_signal(self, _signal: int) -> None:
                self.terminated = True

            def wait(self, timeout: int | None = None) -> int:
                return -15

            def kill(self) -> None:
                raise AssertionError("kill should not be needed after SIGTERM")

        proc = FakeProc()
        with (
            patch.object(
                consumer,
                "get_account_run_request",
                return_value={"id": "req-3", "status": "running", "cancel_requested_at": "now"},
            ),
            patch.object(consumer, "log"),
        ):
            exit_code, timed_out = consumer._wait_for_subprocess(
                cfg,
                proc,  # type: ignore[arg-type]
                request_id="req-3",
                account_id="acct-1",
            )
        self.assertTrue(proc.terminated)
        self.assertEqual(exit_code, -15)
        self.assertFalse(timed_out)


if __name__ == "__main__":
    unittest.main()
