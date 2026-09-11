from __future__ import annotations

import json
from pathlib import Path
import unittest
from unittest import mock

from promotion_admission_fence_v1 import PromotionFenceError, SupabasePromotionFence


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase/migrations/20260911140247_worker_promotion_admission_fence_v1.sql"


class _Response:
    def __init__(self, value):
        self.value = value
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return None
    def read(self):
        return json.dumps(self.value).encode()


class PromotionFenceTests(unittest.TestCase):
    def test_migration_uses_persistent_non_ttl_fence(self):
        sql = MIGRATION.read_text()
        self.assertIn("'worker_promotion_active'", sql)
        self.assertIn("'infinity'::timestamptz", sql)
        self.assertNotIn("renew_phonefarm_worker_promotion", sql)

    def test_migration_covers_creation_claim_device_and_tick_paths(self):
        sql = MIGRATION.read_text()
        for table in ("account_run_requests", "ig_runs", "auto_restart_device_locks", "auto_restart_tick_locks"):
            self.assertIn(f"on public.{table}", sql)
        self.assertIn("before insert or update of status on public.account_run_requests", sql)
        self.assertIn("(to_jsonb(new)->>'status') in ('queued', 'claimed', 'starting', 'running')", sql)

    def test_functions_are_service_role_only(self):
        sql = MIGRATION.read_text()
        for name in (
            "acquire_phonefarm_worker_promotion_fence_v1",
            "inspect_phonefarm_worker_promotion_fence_v1",
            "release_phonefarm_worker_promotion_fence_v1",
        ):
            self.assertIn(f"revoke all on function public.{name}", sql)
        self.assertIn("from public, anon, authenticated", sql)
        self.assertIn("to service_role", sql)

    def test_release_is_exact_owner_bound_and_idempotent(self):
        sql = MIGRATION.read_text()
        self.assertIn("and owner_token = nullif(trim(p_transaction_id), '')", sql)
        self.assertIn("v_released := found", sql)
        self.assertIn("v_idempotent := found", sql)
        self.assertIn("'ok', v_released or v_idempotent", sql)
        self.assertIn("'worker_promotion_fence_not_owned'", sql)
        self.assertIn("'terminal_state', upper(release_reason)", sql)

    def test_client_rejects_inactive_or_wrong_transaction(self):
        fence = SupabasePromotionFence("https://example.supabase.co", "secret")
        with mock.patch(
            "promotion_admission_fence_v1.urllib.request.urlopen",
            return_value=_Response({"ok": True, "active": False, "persistent": True, "transaction_id": "other"}),
        ):
            with self.assertRaises(PromotionFenceError):
                fence.inspect(transaction_id="worker-promotion:test-0001")

    def test_client_accepts_exact_persistent_owner(self):
        value = {"ok": True, "active": True, "persistent": True, "transaction_id": "worker-promotion:test-0001"}
        fence = SupabasePromotionFence("https://example.supabase.co", "secret")
        with mock.patch("promotion_admission_fence_v1.urllib.request.urlopen", return_value=_Response(value)):
            self.assertEqual(fence.inspect(transaction_id=value["transaction_id"]), value)


if __name__ == "__main__":
    unittest.main()
