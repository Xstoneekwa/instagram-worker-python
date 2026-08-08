from __future__ import annotations

import unittest
from pathlib import Path

import follow60_ordering_v2_behavioral_canary_v1 as contract


ACCOUNT = "b024e94e-395d-4f02-9787-81ddc679b014"
OTHER = "11111111-1111-4111-8111-111111111111"
ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase/migrations/20260808154030_follow60_ordering_v2_behavioral_runtime_control_v1.sql"


class Follow60OrderingV2RuntimeControlContractTests(unittest.TestCase):
    def test_runtime_config_is_disabled_and_single_account_fail_closed(self) -> None:
        self.assertEqual((False, "v2_behavioral_disabled"), contract.behavioral_runtime_scope_for_account(ACCOUNT, environ={}))
        self.assertFalse(contract.behavioral_runtime_scope_for_account(ACCOUNT, environ={contract.ENABLED_ENV: "1", contract.ALLOWLIST_ENV: ""})[0])
        self.assertFalse(contract.behavioral_runtime_scope_for_account(ACCOUNT, environ={contract.ENABLED_ENV: "1", contract.ALLOWLIST_ENV: "malformed"})[0])
        self.assertFalse(contract.behavioral_runtime_scope_for_account(ACCOUNT, environ={contract.ENABLED_ENV: "1", contract.ALLOWLIST_ENV: f"{ACCOUNT},{OTHER}"})[0])
        self.assertTrue(contract.behavioral_runtime_scope_for_account(ACCOUNT, environ={contract.ENABLED_ENV: "1", contract.ALLOWLIST_ENV: ACCOUNT})[0])
        self.assertFalse(contract.behavioral_runtime_scope_for_account(OTHER, environ={contract.ENABLED_ENV: "1", contract.ALLOWLIST_ENV: ACCOUNT})[0])

    def test_source_contains_no_rex_account_constant(self) -> None:
        source = (ROOT / "follow60_ordering_v2_behavioral_canary_v1.py").read_text()
        self.assertNotIn(ACCOUNT, source)
        self.assertNotIn("REX_ACCOUNT_ID", source)

    def test_migration_is_dormant_least_privilege_and_has_exact_counters(self) -> None:
        sql = MIGRATION.read_text()
        for signal in (
            "candidate_seen_count", "v2_selected_count", "v2_complete_count",
            "v2_partial_count", "v1_fallback_count", "max_v2_cycles = 10",
            "manual_start", "binding_consumed", "for update", "barrier_reached",
        ):
            self.assertIn(signal, sql)
        self.assertIn("enable row level security", sql)
        self.assertIn("from public, anon, authenticated", sql)
        self.assertIn("to service_role", sql)
        self.assertNotIn("insert into public.follow60_ordering_v2_behavioral_controls", sql.split("create or replace function public.arm_")[0])


if __name__ == "__main__":
    unittest.main()
