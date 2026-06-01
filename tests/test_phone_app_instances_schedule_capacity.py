"""Contract checks for phone app instances Schedule capacity migration."""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase" / "migrations" / "20260601185000_phone_app_instances_schedule_capacity.sql"


class PhoneAppInstancesScheduleCapacityTests(unittest.TestCase):
    def _sql(self) -> str:
        return MIGRATION.read_text(encoding="utf-8")

    def test_phone_app_instances_model_represents_primary_and_clones(self) -> None:
        sql = self._sql()
        for fragment in (
            "create table if not exists public.phone_app_instances",
            "instance_type text not null",
            "instance_index integer not null",
            "visible_label text not null",
            "package_name text",
            "launch_activity text",
            "is_launchable boolean not null default true",
            "usable_for_auto_login boolean not null default true",
            "instance_type in ('primary_app', 'clone')",
            "instance_type = 'primary_app' and instance_index = 0",
            "instance_type = 'clone' and instance_index >= 1",
        ):
            self.assertIn(fragment, sql)

    def test_app_instance_constraints_and_service_role_access(self) -> None:
        sql = self._sql()
        for fragment in (
            "phone_app_instances_device_index_key",
            "phone_app_instances_device_package_key",
            "where package_name is not null and trim(package_name) <> ''",
            "phone_app_instances_service_role_all",
            "revoke all on public.phone_app_instances from anon",
            "revoke all on public.phone_app_instances from authenticated",
        ):
            self.assertIn(fragment, sql)

    def test_assignments_gain_app_instance_id_without_dropping_clone_compat(self) -> None:
        sql = self._sql()
        for fragment in (
            "add column if not exists app_instance_id uuid",
            "account_assignments_app_instance_id_fkey",
            "alter column clone_id drop not null",
            "update public.account_assignments aa",
            "set app_instance_id = aa.clone_id",
            "phone_clones remains a legacy compatibility table",
        ):
            self.assertIn(fragment, sql)
        self.assertNotIn("drop table public.phone_clones", sql.lower())

    def test_availability_uses_app_instances_not_clone_only(self) -> None:
        sql = self._sql()
        for fragment in (
            "from public.phone_app_instances pai",
            "pai.instance_type = 'primary_app'",
            "pai.instance_type = 'clone'",
            "pai.status = 'available'",
            "pai.usable_for_auto_login",
            "pai.is_launchable",
            "pai.current_account_id is null",
            "aa.app_instance_id = pai.id",
            "'no_app_instance_available'",
            "'app_instance_availability'",
        ):
            self.assertIn(fragment, sql)

    def test_app_instance_availability_counts_inventory_rows_once(self) -> None:
        sql = self._sql()
        self.assertIn("'occupied', count(*) filter (where pai.status = 'occupied' or pai.current_account_id is not null)", sql)
        self.assertIn("'available', count(*) filter (\n      where pai.status = 'available'", sql)
        availability_block_start = sql.index("select jsonb_build_object(\n    'total', count(*)")
        availability_block_end = sql.index("v_slot_date := coalesce", availability_block_start)
        availability_block = sql[availability_block_start:availability_block_end]
        self.assertIn("from public.phone_app_instances pai", availability_block)
        self.assertNotIn("join public.account_assignments", availability_block.lower())

    def test_assign_slot_writes_app_instance_and_keeps_legacy_clone_id(self) -> None:
        sql = self._sql()
        for fragment in (
            "v_app_instance_id uuid",
            "v_preferred_app_instance_id uuid := p_clone_id",
            "app_instance_id = v_app_instance_id",
            "clone_id = v_clone_id",
            "where id = v_app_instance_id",
            "'app_instance_id', v_app_instance_id",
            "raise exception 'no_app_instance_available'",
        ):
            self.assertIn(fragment, sql)

    def test_assign_slot_reuses_existing_account_app_instance_before_free_instance(self) -> None:
        sql = self._sql()
        assign_start = sql.index("create or replace function public.assign_account_slot(")
        assign_end = sql.index("create or replace function public.evaluate_account_schedule_gate", assign_start)
        assign_sql = sql[assign_start:assign_end]
        self.assertIn("v_account_app_instance_id uuid", sql)
        self.assertIn("pai.status = 'occupied'", sql)
        self.assertIn("pai.current_account_id = v_account_id", sql)
        self.assertIn("order by case when pai.instance_type = 'primary_app' then 0 else 1 end, pai.instance_index asc", sql)
        self.assertIn("v_app_instance_id := v_account_app_instance_id", sql)
        self.assertLess(assign_sql.index("v_app_instance_id := v_account_app_instance_id"), assign_sql.index("pai.status = 'available'"))

    def test_assign_slot_relinks_same_slot_when_existing_assignment_uses_other_instance(self) -> None:
        sql = self._sql()
        self.assertIn("and (v_account_app_instance_id is null or v_account_app_instance_id = v_existing.app_instance_id) then", sql)
        self.assertIn("v_old_app_instance_id := v_existing.app_instance_id", sql)
        self.assertIn("reassignment_release_old_instance", sql)

    def test_assign_slot_falls_back_to_available_instance_without_existing_account_instance(self) -> None:
        sql = self._sql()
        self.assertIn("if v_app_instance_id is null then\n    select pai.id", sql)
        self.assertIn("pai.status = 'available'", sql)
        self.assertIn("pai.current_account_id is null", sql)
        self.assertIn("for update skip locked", sql)

    def test_assign_slot_rejects_instance_occupied_by_another_account(self) -> None:
        sql = self._sql()
        self.assertIn("v_instance_current_account_id is not null and v_instance_current_account_id <> v_account_id", sql)
        self.assertIn("raise exception 'preferred_app_instance_incompatible'", sql)
        self.assertIn("aa.account_id <> v_account_id", sql)

    def test_assign_slot_avoids_double_occupation_for_same_account(self) -> None:
        sql = self._sql()
        self.assertIn("v_app_instance_id := v_account_app_instance_id", sql)
        self.assertIn("if v_old_app_instance_id is not null and v_old_app_instance_id <> v_app_instance_id then", sql)
        self.assertIn("public.release_app_instance_if_unused", sql)

    def test_release_rules_cover_account_and_assignment_terminal_states(self) -> None:
        sql = self._sql()
        for fragment in (
            "release_account_schedule_capacity",
            "account_cancelled_release",
            "account_archived_release",
            "manual_assignment_release",
            "reassignment_release_old_instance",
            "clone_reset_release",
            "no_open_assignment_after_release",
            "runtime_stopped_keep_assignment",
            "account_assignments_status_check",
            "'canceled'",
            "'expired'",
            "'archived'",
        ):
            self.assertIn(fragment, sql)

    def test_release_guards_avoid_dangerous_reuse(self) -> None:
        sql = self._sql()
        for fragment in (
            "account_has_active_runtime_session",
            "'active_session_guard'",
            "status in ('queued', 'pending', 'starting', 'running', 'in_progress', 'active')",
            "status in ('queued', 'claimed', 'starting', 'running', 'in_progress')",
            "pai.status = 'occupied'",
            "pai.status <> 'disabled'",
            "not exists (\n      select 1\n      from public.account_assignments aa",
            "assignment_terminal_update_blocked_active_run",
        ):
            self.assertIn(fragment, sql)
        self.assertNotIn("account_stopped_release", sql)
        self.assertNotIn("new.status not in ('stopped'", sql)
        self.assertNotIn("admin_lifecycle_status", sql)

    def test_runtime_audit_is_safe_and_contains_expected_fields(self) -> None:
        sql = self._sql()
        for fragment in (
            "audit_schedule_capacity_event",
            "insert into public.runtime_events",
            "'admin_only'",
            "'safe_audit', true",
            "'old_status'",
            "'new_status'",
            "'released_by'",
            "'source'",
        ):
            self.assertIn(fragment, sql)
        forbidden = ("password", "token", "device_serial", "device_udid", "raw_xml")
        for word in forbidden:
            self.assertNotIn(word, sql.lower())

    def test_release_keeps_legacy_clone_compatibility_when_instance_is_clone(self) -> None:
        sql = self._sql()
        for fragment in (
            "update public.phone_clones pc",
            "pc.id = p_app_instance_id",
            "legacy_phone_clones_id",
            "current_account_id = null",
        ):
            self.assertIn(fragment, sql)


if __name__ == "__main__":
    unittest.main()
