"""Sanity checks for schedule production-ready migration contract."""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase" / "migrations" / "20260601152833_schedule_production_ready.sql"


class ScheduleMigrationContractTests(unittest.TestCase):
    def _sql(self) -> str:
        return MIGRATION.read_text(encoding="utf-8")

    def test_migration_contains_required_rpcs(self) -> None:
        sql = self._sql()
        for fragment in (
            "phone_rest_windows",
            "assignment_source",
            "list_available_assignment_slots",
            "assign_account_slot",
            "evaluate_account_schedule_gate",
            "phone device already has an overlapping open assignment slot",
            "outreach_40m",
        ):
            self.assertIn(fragment, sql)

    def test_phone_rest_supports_weekly_and_one_off_ranges(self) -> None:
        sql = self._sql()
        for fragment in (
            "Natural phone rest is dynamic post-session buffer",
            "explicit fixed blackout/maintenance windows",
            "rest_type text not null default 'weekly'",
            "rest_type in ('weekly', 'one_off')",
            "rest_date date",
            "starts_at_local time not null",
            "ends_at_local time not null",
            "tsrange(v_slot_start_local, v_slot_end_local, '[)')",
            "tsrange(v_rest_start_local, v_rest_end_local, '[)')",
        ):
            self.assertIn(fragment, sql)
        self.assertNotIn("v_slot_end := v_slot_end + interval '24 hours'", sql)

    def test_phone_wide_collision_uses_exclusion_constraint(self) -> None:
        sql = self._sql()
        for fragment in (
            "create extension if not exists btree_gist",
            "account_assignments_device_open_no_overlap",
            "exclude using gist",
            "device_id with =",
            "tstzrange(starts_at, ends_at, '[)') with &&",
            "where (status in ('pending', 'reserved', 'active'))",
        ):
            self.assertIn(fragment, sql)

    def test_schedule_gate_detects_assignment_slot_conflict(self) -> None:
        sql = self._sql()
        self.assertIn("'reason', 'assignment_slot_conflict'", sql)
        self.assertIn("aa.device_id = v_assignment.device_id", sql)
        self.assertIn("aa.account_id <> v_account_id", sql)
        self.assertIn("aa.id <> v_assignment.id", sql)

    def test_assign_slot_releases_old_clone_and_is_idempotent(self) -> None:
        sql = self._sql()
        for fragment in (
            "v_old_clone_id := v_existing.clone_id",
            "'idempotent', true",
            "v_old_clone_id <> v_clone_id",
            "set status = 'available'",
            "current_account_id = null",
        ):
            self.assertIn(fragment, sql)

    def test_timezone_validation_uses_pg_timezone_names(self) -> None:
        sql = self._sql()
        for fragment in (
            "pg_catalog.pg_timezone_names",
            "invalid_timezone",
            "validate_phone_device_timezone",
            "validate_phone_rest_window",
        ):
            self.assertIn(fragment, sql)

    def test_outreach_only_policy_is_supported_but_disabled_by_default(self) -> None:
        sql = self._sql()
        for fragment in (
            "device_outreach_rest_policies",
            "status text not null default 'disabled'",
            "reserved_slot_indexes integer[] not null default '{}'::integer[]",
            "slot_reserved_for_outreach_rest",
            "'outreach_rest_reserved'",
        ):
            self.assertIn(fragment, sql)
        self.assertNotIn("insert into public.device_outreach_rest_policies", sql.lower())

    def test_assignment_slot_window_matches_runtime_profile(self) -> None:
        sql = self._sql()
        for fragment in (
            "validate_assignment_slot_window",
            "p_assignment_type = 'full_cycle'",
            "v_duration_seconds = 21600",
            "v_local_hour in (0, 6, 12, 18)",
            "p_assignment_type = 'outreach_only'",
            "v_duration_seconds = 2400",
            "(v_local_total_minutes % 40) = 0",
            "assignment_slot_kind_window_mismatch",
        ):
            self.assertIn(fragment, sql)


if __name__ == "__main__":
    unittest.main()
