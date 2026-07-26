from __future__ import annotations

import unittest
from unittest.mock import patch

import account_run_request_consumer as consumer


class AccountProtectionDispatcherTest(unittest.TestCase):
    def test_dispatcher_snapshot_loader_uses_one_rpc(self) -> None:
        result = {
            "ok": True,
            "account_id": "11111111-1111-4111-8111-111111111111",
            "source": "account_protection_list_entries",
            "loaded_at": "2026-07-26T03:00:00+00:00",
            "lists": {"interaction_blacklist": [], "unfollow_whitelist": []},
            "versions": {"interaction_blacklist": 0, "unfollow_whitelist": 0},
        }
        with patch.object(consumer.supabase_client, "call_rpc", return_value=result) as rpc:
            ok, reason, serialized, metadata = consumer._load_account_protection_snapshot(result["account_id"])
        self.assertTrue(ok)
        self.assertEqual(reason, "ready")
        self.assertIn("account_protection_list_entries", serialized)
        self.assertEqual(metadata["protection_lists_source"], "canonical_v1")
        self.assertEqual(metadata["protection_lists_storage"], "account_protection_list_entries")
        self.assertEqual(metadata["blacklist_count"], 0)
        self.assertEqual(metadata["lists_version"], {"interaction_blacklist": 0, "unfollow_whitelist": 0})
        rpc.assert_called_once_with("get_account_protection_lists_for_run", {"p_account_id": result["account_id"]})

    def test_dispatcher_snapshot_failure_blocks_safely(self) -> None:
        with patch.object(consumer.supabase_client, "call_rpc", side_effect=RuntimeError("down")):
            ok, reason, serialized, metadata = consumer._load_account_protection_snapshot("11111111-1111-4111-8111-111111111111")
        self.assertFalse(ok)
        self.assertEqual(reason, "interaction_blacklist_load_failed")
        self.assertEqual(serialized, "")
        self.assertEqual(metadata, {})

    def test_dispatcher_reports_isolated_whitelist_contract_failure(self) -> None:
        result = {
            "ok": True,
            "account_id": "11111111-1111-4111-8111-111111111111",
            "source": "account_protection_list_entries",
            "loaded_at": "2026-07-26T03:00:00+00:00",
            "lists": {"interaction_blacklist": [], "unfollow_whitelist": "bad"},
            "versions": {"interaction_blacklist": 0, "unfollow_whitelist": 0},
        }
        with patch.object(consumer.supabase_client, "call_rpc", return_value=result):
            ok, reason, serialized, metadata = consumer._load_account_protection_snapshot(result["account_id"])
        self.assertFalse(ok)
        self.assertEqual(reason, "unfollow_whitelist_load_failed")
        self.assertEqual(serialized, "")
        self.assertEqual(metadata, {})


if __name__ == "__main__":
    unittest.main()
