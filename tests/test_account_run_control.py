"""Tests for Run Control helper module."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import account_run_control


class AccountRunControlTest(unittest.TestCase):
    def test_create_account_run_request_calls_rpc(self) -> None:
        with patch.object(
            account_run_control.supabase_client,
            "call_rpc",
            return_value={"id": "req-1", "status": "queued"},
        ) as rpc:
            row = account_run_control.create_account_run_request(
                account_id="00000000-0000-4000-8000-000000000001",
                requested_run_type="account_session",
                idempotency_key="dashboard:test",
            )
        self.assertEqual(row["id"], "req-1")
        self.assertEqual(rpc.call_args.args[0], "create_account_run_request")
        self.assertEqual(
            rpc.call_args.args[1]["p_account_id"],
            "00000000-0000-4000-8000-000000000001",
        )

    def test_claim_next_account_run_request_returns_none(self) -> None:
        with patch.object(account_run_control.supabase_client, "call_rpc", return_value=None):
            row = account_run_control.claim_next_account_run_request("run-dispatcher:test")
        self.assertIsNone(row)

    def test_claim_next_account_run_request_rejects_row_without_uuid(self) -> None:
        with patch.object(
            account_run_control.supabase_client,
            "call_rpc",
            return_value={"status": "claimed"},
        ):
            row = account_run_control.claim_next_account_run_request("run-dispatcher:test")
        self.assertIsNone(row)

    def test_complete_account_run_request_skips_empty_request_id(self) -> None:
        with patch.object(account_run_control.supabase_client, "call_rpc") as rpc:
            row = account_run_control.complete_account_run_request(
                "",
                "run-dispatcher:test",
                "failed",
            )
        self.assertIsNone(row)
        rpc.assert_not_called()

    def test_normalize_request_uuid_accepts_canonical_value(self) -> None:
        value = account_run_control.normalize_request_uuid(
            "00000000-0000-4000-8000-000000000099"
        )
        self.assertEqual(value, "00000000-0000-4000-8000-000000000099")

    def test_is_account_run_request_cancel_requested(self) -> None:
        with patch.object(account_run_control.supabase_client, "call_rpc", return_value=True):
            self.assertTrue(
                account_run_control.is_account_run_request_cancel_requested(
                    "00000000-0000-4000-8000-000000000099"
                )
            )

    def test_reclaim_stale_account_run_requests_parses_int(self) -> None:
        with patch.object(account_run_control.supabase_client, "call_rpc_once", return_value=3):
            self.assertEqual(account_run_control.reclaim_stale_account_run_requests("worker"), 3)

    def test_reconcile_linked_ig_run_no_run_id_is_noop(self) -> None:
        result = account_run_control.reconcile_linked_ig_run_terminal(
            run_id=None,
            terminal_status="failed",
        )
        self.assertFalse(result["reconciled"])
        self.assertEqual(result["reason"], "no_run_id")

    def test_reconcile_linked_ig_run_nonzero_exit_marks_failed(self) -> None:
        run_id = "00000000-0000-4000-8000-000000000010"
        account_id = "00000000-0000-4000-8000-000000000001"
        with (
            patch.object(
                account_run_control,
                "get_ig_run_by_id",
                return_value={"id": run_id, "account_id": account_id, "status": "running"},
            ),
            patch.object(account_run_control.supabase_client, "_request_json") as request_json,
        ):
            result = account_run_control.reconcile_linked_ig_run_terminal(
                run_id=run_id,
                terminal_status="failed",
                account_id=account_id,
            )
        self.assertTrue(result["reconciled"])
        self.assertEqual(result["terminal_status"], "failed")
        request_json.assert_called_once()
        self.assertEqual(request_json.call_args.kwargs["body"]["status"], "failed")

    def test_reconcile_linked_ig_run_without_run_id_does_not_patch(self) -> None:
        with patch.object(account_run_control.supabase_client, "_request_json") as request_json:
            result = account_run_control.reconcile_linked_ig_run_terminal(
                run_id="",
                terminal_status="failed",
            )
        self.assertFalse(result["reconciled"])
        request_json.assert_not_called()

    def test_reconcile_canceled_maps_to_stopped(self) -> None:
        run_id = "00000000-0000-4000-8000-000000000011"
        with (
            patch.object(
                account_run_control,
                "get_ig_run_by_id",
                return_value={"id": run_id, "account_id": "acct-1", "status": "running"},
            ),
            patch.object(account_run_control.supabase_client, "_request_json") as request_json,
        ):
            result = account_run_control.reconcile_linked_ig_run_terminal(
                run_id=run_id,
                terminal_status="canceled",
            )
        self.assertTrue(result["reconciled"])
        self.assertEqual(result["terminal_status"], "stopped")
        self.assertEqual(request_json.call_args.kwargs["body"]["status"], "stopped")

    def test_reconcile_exit_zero_marks_completed(self) -> None:
        run_id = "00000000-0000-4000-8000-000000000012"
        with (
            patch.object(
                account_run_control,
                "get_ig_run_by_id",
                return_value={"id": run_id, "account_id": "acct-1", "status": "running"},
            ),
            patch.object(account_run_control.supabase_client, "_request_json") as request_json,
        ):
            result = account_run_control.reconcile_linked_ig_run_terminal(
                run_id=run_id,
                terminal_status="completed",
            )
        self.assertTrue(result["reconciled"])
        self.assertEqual(request_json.call_args.kwargs["body"]["status"], "completed")
        self.assertIn("completed_at", request_json.call_args.kwargs["body"])

    def test_reconcile_is_idempotent_when_already_terminal(self) -> None:
        run_id = "00000000-0000-4000-8000-000000000013"
        snapshots = [
            {"id": run_id, "account_id": "acct-1", "status": "running"},
            {"id": run_id, "account_id": "acct-1", "status": "failed"},
        ]

        def _get_run(_run_id: str) -> dict:
            return snapshots.pop(0) if snapshots else {"id": run_id, "account_id": "acct-1", "status": "failed"}

        with (
            patch.object(account_run_control, "get_ig_run_by_id", side_effect=_get_run),
            patch.object(account_run_control.supabase_client, "_request_json") as request_json,
        ):
            first = account_run_control.reconcile_linked_ig_run_terminal(
                run_id=run_id,
                terminal_status="failed",
            )
            second = account_run_control.reconcile_linked_ig_run_terminal(
                run_id=run_id,
                terminal_status="failed",
            )
        self.assertTrue(first["reconciled"])
        self.assertFalse(second["reconciled"])
        self.assertEqual(second["reason"], "already_terminal")
        request_json.assert_called_once()

    def test_reconcile_rejects_account_mismatch(self) -> None:
        run_id = "00000000-0000-4000-8000-000000000014"
        with patch.object(
            account_run_control,
            "get_ig_run_by_id",
            return_value={
                "id": run_id,
                "account_id": "00000000-0000-4000-8000-000000000099",
                "status": "running",
            },
        ):
            result = account_run_control.reconcile_linked_ig_run_terminal(
                run_id=run_id,
                terminal_status="failed",
                account_id="00000000-0000-4000-8000-000000000001",
            )
        self.assertFalse(result["reconciled"])
        self.assertEqual(result["reason"], "account_mismatch")


if __name__ == "__main__":
    unittest.main()
