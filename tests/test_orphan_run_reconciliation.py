from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import orphan_run_reconciliation as reconciliation


NOW = datetime(2026, 8, 13, 1, 30, tzinfo=timezone.utc)
REQUEST_ID = "623d40d2-3a80-48a5-b575-3e8221f2bab1"
ACCOUNT_ID = "8bdd2dde-6b14-4ca8-bd7b-bedf67302fc4"
RUN_ID = "8b4b54d5-f12a-4d4a-b254-88d5caf1ddd9"


class OrphanRunReconciliationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = SimpleNamespace(
            worker_id="run-dispatcher:test",
            heartbeat_seconds=20,
            subprocess_timeout_seconds=7200,
        )

    def rows(self, *, heartbeat=None, lock=None, updated_at="2026-08-12T22:13:00+00:00"):
        request = {
            "id": REQUEST_ID,
            "account_id": ACCOUNT_ID,
            "status": "running",
            "run_id": RUN_ID,
            "requested_run_type": "account_session",
            "started_at": "2026-08-12T22:01:00+00:00",
            "lease_expires_at": "2026-08-12T22:06:00+00:00",
            "cancel_requested_at": "2026-08-13T00:49:00+00:00",
        }
        run = {
            "id": RUN_ID,
            "account_id": ACCOUNT_ID,
            "status": "running",
            "started_at": "2026-08-12T22:01:00+00:00",
            "updated_at": updated_at,
        }

        def request_json(_method, endpoint, **_kwargs):
            return {
                "account_run_requests": [request],
                "ig_runs": [run],
                "worker_heartbeats": [heartbeat] if heartbeat else [],
                "auto_restart_device_locks": [lock] if lock else [],
                "account_session_resume_plans": [],
            }[endpoint]

        return request_json

    def test_stale_canceled_orphan_is_terminalized(self) -> None:
        with (
            patch.object(reconciliation.supabase_client, "_request_json", side_effect=self.rows()) as request_json,
            patch.object(reconciliation, "reconcile_linked_ig_run_terminal", return_value={"reconciled": True}) as run_terminal,
            patch.object(reconciliation, "complete_account_run_request", return_value={"status": "canceled"}) as request_terminal,
            patch.object(reconciliation, "insert_manual_run_audit"),
            patch.object(reconciliation, "log"),
        ):
            result = reconciliation.reconcile_orphaned_active_runs(self.cfg, now=NOW)
        self.assertEqual(result["reconciled"], 1)
        self.assertEqual(run_terminal.call_args.kwargs["terminal_status"], "canceled")
        self.assertEqual(request_terminal.call_args.args[2], "canceled")
        self.assertTrue(any(call.args[1] == "account_session_resume_plans" for call in request_json.call_args_list))

    def test_fresh_worker_heartbeat_protects_live_run(self) -> None:
        heartbeat = {"current_run_id": RUN_ID, "status": "running", "last_seen_at": NOW.isoformat()}
        with (
            patch.object(reconciliation.supabase_client, "_request_json", side_effect=self.rows(heartbeat=heartbeat)),
            patch.object(reconciliation, "reconcile_linked_ig_run_terminal") as terminal,
        ):
            result = reconciliation.reconcile_orphaned_active_runs(self.cfg, now=NOW)
        self.assertEqual(result["reconciled"], 0)
        terminal.assert_not_called()

    def test_live_device_lock_protects_live_run(self) -> None:
        lock = {
            "run_id": RUN_ID,
            "request_id": REQUEST_ID,
            "lease_expires_at": "2026-08-13T01:35:00+00:00",
            "heartbeat_at": NOW.isoformat(),
            "release_reason": None,
        }
        with (
            patch.object(reconciliation.supabase_client, "_request_json", side_effect=self.rows(lock=lock)),
            patch.object(reconciliation, "reconcile_linked_ig_run_terminal") as terminal,
        ):
            result = reconciliation.reconcile_orphaned_active_runs(self.cfg, now=NOW)
        self.assertEqual(result["reconciled"], 0)
        terminal.assert_not_called()

    def test_recent_progress_protects_run(self) -> None:
        with (
            patch.object(
                reconciliation.supabase_client,
                "_request_json",
                side_effect=self.rows(updated_at="2026-08-13T01:20:00+00:00"),
            ),
            patch.object(reconciliation, "reconcile_linked_ig_run_terminal") as terminal,
        ):
            result = reconciliation.reconcile_orphaned_active_runs(self.cfg, now=NOW)
        self.assertEqual(result["reconciled"], 0)
        terminal.assert_not_called()

    def test_run_write_failure_never_terminalizes_request(self) -> None:
        with (
            patch.object(reconciliation.supabase_client, "_request_json", side_effect=self.rows()),
            patch.object(reconciliation, "reconcile_linked_ig_run_terminal", return_value={"reconciled": False}),
            patch.object(reconciliation, "complete_account_run_request") as request_terminal,
        ):
            result = reconciliation.reconcile_orphaned_active_runs(self.cfg, now=NOW)
        self.assertEqual(result["reconciled"], 0)
        request_terminal.assert_not_called()


if __name__ == "__main__":
    unittest.main()
