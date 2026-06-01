"""Contract checks for admin lifecycle status migration."""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase" / "migrations" / "20260601193000_admin_lifecycle_status.sql"


class AdminLifecycleStatusContractTests(unittest.TestCase):
    def _sql(self) -> str:
        return MIGRATION.read_text(encoding="utf-8")

    def test_admin_lifecycle_status_is_separate_from_runtime_statuses(self) -> None:
        sql = self._sql()
        for fragment in (
            "add column if not exists admin_lifecycle_status text",
            "ig_accounts_admin_lifecycle_status_check",
            "release_schedule_capacity_on_account_admin_lifecycle",
            "ig_accounts_release_schedule_capacity_on_admin_lifecycle",
            "account_paused_keep_assignment",
            "account_needs_assistance_keep_assignment",
            "account_cancelled_release",
            "'active'",
            "'paused'",
            "'cancelled'",
            "'needs_assistance'",
            "'pending_cancellation'",
            "Do not mix with login/provisioning/onboarding or runtime stopped",
        ):
            self.assertIn(fragment, sql)

    def test_visible_ui_statuses_do_not_add_archived_to_this_step(self) -> None:
        sql = self._sql()
        self.assertIn("active, pending, onboarding, paused, cancelled, needs_assistance", sql)
        self.assertNotIn("'archived'", sql)

    def test_cancelled_is_british_spelling_for_admin_lifecycle(self) -> None:
        sql = self._sql()
        self.assertIn("then 'cancelled'", sql)
        self.assertNotIn("'canceled'", sql.split("check (admin_lifecycle_status in", 1)[1])

    def test_migration_order_is_safe_for_phone_app_instances_dependency(self) -> None:
        phone_migration = ROOT / "supabase" / "migrations" / "20260601185000_phone_app_instances_schedule_capacity.sql"
        self.assertLess(phone_migration.name, MIGRATION.name)
        self.assertNotIn("admin_lifecycle_status", phone_migration.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
