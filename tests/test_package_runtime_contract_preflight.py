from __future__ import annotations

import unittest
from unittest.mock import patch

import account_run_request_consumer as consumer
import account_session_orchestrator as orchestrator


class PackageRuntimeContractPreflightTest(unittest.TestCase):
    def test_worker_accepts_only_ready_contract(self) -> None:
        with patch.object(
            consumer.supabase_client,
            "call_rpc",
            return_value={"ok": True, "reason": "ready", "commercial_package_code": "premium"},
        ) as rpc:
            ok, reason, contract = consumer._load_package_runtime_contract("account-1")
        self.assertTrue(ok)
        self.assertEqual(reason, "ready")
        self.assertEqual(contract["commercial_package_code"], "premium")
        rpc.assert_called_once_with("account_package_runtime_contract_status", {"p_account_id": "account-1"})

    def test_worker_fails_closed_when_contract_rpc_is_unavailable(self) -> None:
        with patch.object(consumer.supabase_client, "call_rpc", side_effect=RuntimeError("offline")):
            ok, reason, contract = consumer._load_package_runtime_contract("account-1")
        self.assertFalse(ok)
        self.assertEqual(reason, "package_settings_incomplete")
        self.assertEqual(contract, {})

    def test_supabase_rotation_settings_do_not_fallback_when_missing(self) -> None:
        with patch.object(orchestrator.supabase_client, "load_account_follow_source_settings", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "package_settings_incomplete"):
                orchestrator._resolve_follow_source_rotation_settings("account-1", strict=True)

    def test_non_supabase_rotation_mode_retains_explicit_ops_fallback(self) -> None:
        with patch.object(orchestrator.supabase_client, "load_account_follow_source_settings", return_value=None):
            value = orchestrator._resolve_follow_source_rotation_settings("account-1", strict=False)
        self.assertNotEqual(value["settings_source"], "account")


if __name__ == "__main__":
    unittest.main()
