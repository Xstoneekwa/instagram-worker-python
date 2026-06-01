"""Contract checks for schedule assignment_type resolution priority."""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase" / "migrations" / "20260601153801_schedule_resolve_full_cycle_priority.sql"


class ScheduleAssignmentTypeResolverTests(unittest.TestCase):
    def _sql(self) -> str:
        return MIGRATION.read_text(encoding="utf-8")

    def test_resolver_function_exists_with_full_cycle_priority(self) -> None:
        sql = self._sql()
        for fragment in (
            "resolve_account_schedule_assignment_type",
            "outreach_standalone",
            "if v_has_full_cycle then",
            "return 'full_cycle'",
            "return 'outreach_only'",
            "Outreach entitlement/add-on does not override full_cycle",
        ):
            self.assertIn(fragment, sql)

    def test_list_slots_uses_resolver_not_latest_subscription_only(self) -> None:
        sql = self._sql()
        self.assertIn(
            "public.resolve_account_schedule_assignment_type(p_account_id)",
            sql,
        )
        self.assertIn("and cs.subscription_type = v_assignment_type", sql)
        self.assertNotIn("order by cs.starts_at desc\n  limit 1;\n\n  if v_assignment_type is null", sql)

    def test_assign_slot_uses_resolver_and_matching_subscription(self) -> None:
        sql = self._sql()
        self.assertIn(
            "v_assignment_type := public.resolve_account_schedule_assignment_type(v_account_id)",
            sql,
        )
        self.assertIn(
            "and cs.subscription_type = v_assignment_type",
            sql,
        )

    def test_list_slots_exposes_resolved_slot_kind(self) -> None:
        sql = self._sql()
        self.assertIn("'slot_kind', v_slot_kind", sql)

    def test_full_cycle_addon_scenarios_documented(self) -> None:
        sql = self._sql()
        self.assertIn("Pro/full_cycle accounts with Outreach add-on keep full_cycle schedule slots", sql)
        self.assertNotIn("commercial_addons", sql.lower())
        self.assertNotIn("client_entitlements", sql.lower())
        self.assertNotIn("feature_code = 'outreach'", sql.lower())


if __name__ == "__main__":
    unittest.main()
