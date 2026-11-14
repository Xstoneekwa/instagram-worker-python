from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import assignment_dispatch_resolver as resolver
import supabase_client


def _assignment(
    *,
    assignment_type: str = "outreach_only",
    status: str = "reserved",
    starts_at: str | None = None,
    ends_at: str | None = None,
    adb_serial: str = "emulator-5554",
    package_name: str = "com.instagram.androie",
) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "id": "assignment-1",
        "account_id": "account-1",
        "device_id": "device-1",
        "clone_id": "clone-1",
        "app_instance_id": "app-instance-1",
        "assignment_type": assignment_type,
        "slot_kind": "outreach_short"
        if assignment_type == "outreach_only"
        else "full_cycle_6h",
        "status": status,
        "starts_at": starts_at or (now - timedelta(minutes=5)).isoformat(),
        "ends_at": ends_at or (now + timedelta(minutes=5)).isoformat(),
        "phone_device": {
            "id": "device-1",
            "device_kind": "emulator",
            "adb_serial": adb_serial,
            "device_udid": "udid-secret",
            "host_machine": "host-secret",
            "hub_label": "hub-secret",
            "hub_port": "port-secret",
            "pool_type": "outreach_only"
            if assignment_type == "outreach_only"
            else "full_cycle",
        },
        "phone_clone": {
            "id": "clone-1",
            "clone_index": 1,
            "clone_label": "clone-a",
        },
        "phone_app_instance": {
            "id": "app-instance-1",
            "instance_type": "clone",
            "instance_index": 1,
            "visible_label": "Instagram 1",
            "package_name": package_name,
        },
    }


class LoginAssignmentPoolTests(unittest.TestCase):
    def resolve(self, assignment, run_type="login_provisioning", **kw):
        with patch.object(resolver.supabase_client, "load_open_account_assignment_for_dispatch", return_value=assignment):
            return resolver.resolve_account_assignment_runtime_context("account-1", run_type, require_assignment=True, **kw)

    def test_both_services_use_the_exact_assigned_clone_for_both_login_types(self):
        for pool in ("full_cycle", "outreach_only"):
            for run_type in ("login_provisioning", "login_email_code_resume"):
                with self.subTest(pool=pool, run_type=run_type):
                    row = _assignment(assignment_type=pool)
                    row.update(slot_kind="manual_only", starts_at=None, ends_at=None)
                    with patch.object(supabase_client, "load_account", side_effect=AssertionError("legacy_account_device_not_authority")):
                        ctx = self.resolve(row, run_type)
                    self.assertTrue(ctx["assignment_found"])
                    self.assertEqual(ctx["reason"], "assignment_resolved")
                    self.assertFalse(ctx["fallback_used"])
                    self.assertEqual(ctx["package_name"], "com.instagram.androie")
                    self.assertEqual(ctx["app_instance_index"], 1)
                    self.assertEqual(ctx["device_id"], row["device_id"])

    def test_missing_assignment_remains_unknown(self):
        ctx = self.resolve(None)
        self.assertFalse(ctx["assignment_found"])
        self.assertEqual(ctx["reason"], "assignment_not_found")

    def test_unrecognized_pool_is_not_login_capacity(self):
        ctx = self.resolve(_assignment(assignment_type="unsupported_pool"))
        self.assertFalse(ctx["assignment_found"])
        self.assertEqual(ctx["reason"], "assignment_type_incompatible")

    def test_missing_serial_cannot_fall_back_to_a_different_device(self):
        ctx = self.resolve(_assignment(adb_serial=""))
        self.assertFalse(ctx["assignment_found"])
        self.assertEqual(ctx["reason"], "assignment_device_missing_adb_serial")

    def test_explicitly_enforced_window_is_still_enforced(self):
        now = datetime.now(timezone.utc)
        ctx = self.resolve(_assignment(starts_at=(now+timedelta(hours=1)).isoformat(),
                                      ends_at=(now+timedelta(hours=2)).isoformat()), enforce_window=True)
        self.assertFalse(ctx["assignment_found"])
        self.assertEqual(ctx["reason"], "assignment_window_inactive")

    def test_existing_outreach_slot_authority_is_not_bypassed(self):
        row = _assignment()
        row["metadata"] = {"outreach_slot_grid_v1": {"enabled": True}}
        with patch.object(resolver, "_evaluate_schedule_gate", return_value={"ok": False, "reason": "canonical_hold"}):
            ctx = self.resolve(row)
        self.assertFalse(ctx["assignment_found"])
        self.assertEqual(ctx["reason"], "canonical_hold")

    def test_outreach_login_does_not_grant_follow_unfollow_or_welcome(self):
        for run_type in ("account_session", "unfollow_session", "unfollow_outreach_pipeline", "dm_welcome_baseline", "dm_welcome_session_send", "scheduled_session_preflight"):
            with self.subTest(run_type=run_type):
                ctx = self.resolve(_assignment(), run_type)
                self.assertFalse(ctx["assignment_found"])
                self.assertEqual(ctx["reason"], "assignment_type_incompatible")


class AssignmentDispatchResolverTest(unittest.TestCase):
    def _resolve(self, assignment: dict | None, **kwargs) -> dict:
        with patch.object(
            resolver.supabase_client,
            "load_open_account_assignment_for_dispatch",
            return_value=assignment,
        ):
            return resolver.resolve_account_assignment_runtime_context(
                "account-1",
                kwargs.pop("run_type", "outreach_session"),
                **kwargs,
            )

    def test_outreach_only_outreach_session_accepted(self) -> None:
        ctx = self._resolve(_assignment(assignment_type="outreach_only"))
        self.assertTrue(ctx["assignment_found"])
        self.assertEqual(ctx["adb_serial"], "emulator-5554")
        self.assertEqual(ctx["app_instance_id"], "app-instance-1")
        self.assertEqual(ctx["app_instance_label"], "Instagram 1")
        self.assertEqual(ctx["package_name"], "com.instagram.androie")
        self.assertEqual(ctx["reason"], "assignment_resolved")

    def test_full_cycle_outreach_session_accepted(self) -> None:
        ctx = self._resolve(_assignment(assignment_type="full_cycle"))
        self.assertTrue(ctx["assignment_found"])
        self.assertEqual(ctx["assignment_type"], "full_cycle")

    def test_full_cycle_unfollow_session_accepted(self) -> None:
        ctx = self._resolve(
            _assignment(
                assignment_type="full_cycle",
                adb_serial="RFGL145VCKE",
                package_name="com.instagram.androie",
            ),
            run_type="unfollow_session",
            require_assignment=True,
            enforce_window=False,
        )
        self.assertTrue(ctx["assignment_found"])
        self.assertEqual(ctx["assignment_type"], "full_cycle")
        self.assertEqual(ctx["slot_kind"], "full_cycle_6h")
        self.assertEqual(ctx["run_type"], "unfollow_session")
        self.assertEqual(ctx["reason"], "assignment_resolved")
        self.assertEqual(ctx["adb_serial"], "RFGL145VCKE")
        self.assertEqual(ctx["package_name"], "com.instagram.androie")
        self.assertFalse(ctx["fallback_used"])

    def test_full_cycle_unfollow_outreach_pipeline_accepted(self) -> None:
        ctx = self._resolve(
            _assignment(
                assignment_type="full_cycle",
                adb_serial="RFGL145VCKE",
                package_name="com.instagram.androie",
            ),
            run_type="unfollow_outreach_pipeline",
            require_assignment=True,
            enforce_window=False,
        )
        self.assertTrue(ctx["assignment_found"])
        self.assertEqual(ctx["assignment_type"], "full_cycle")
        self.assertEqual(ctx["slot_kind"], "full_cycle_6h")
        self.assertEqual(ctx["run_type"], "unfollow_outreach_pipeline")
        self.assertEqual(ctx["reason"], "assignment_resolved")
        self.assertEqual(ctx["adb_serial"], "RFGL145VCKE")
        self.assertEqual(ctx["package_name"], "com.instagram.androie")
        self.assertFalse(ctx["fallback_used"])

    def test_full_cycle_welcome_session_send_accepted(self) -> None:
        ctx = self._resolve(
            _assignment(assignment_type="full_cycle"),
            run_type="dm_welcome_session_send",
        )
        self.assertTrue(ctx["assignment_found"])
        self.assertEqual(ctx["assignment_type"], "full_cycle")
        self.assertEqual(ctx["reason"], "assignment_resolved")

    def test_full_cycle_welcome_baseline_accepted(self) -> None:
        ctx = self._resolve(
            _assignment(
                assignment_type="full_cycle",
                adb_serial="RFGL145VCKE",
                package_name="com.instagram.androif",
            ),
            run_type="dm_welcome_baseline",
            require_assignment=True,
            enforce_window=False,
        )
        self.assertTrue(ctx["assignment_found"])
        self.assertEqual(ctx["assignment_type"], "full_cycle")
        self.assertEqual(ctx["slot_kind"], "full_cycle_6h")
        self.assertEqual(ctx["reason"], "assignment_resolved")
        self.assertEqual(ctx["adb_serial"], "RFGL145VCKE")
        self.assertEqual(ctx["package_name"], "com.instagram.androif")
        self.assertFalse(ctx["fallback_used"])

    def test_full_cycle_welcome_baseline_resolves_inside_active_window(self) -> None:
        now = datetime.now(timezone.utc)
        ctx = self._resolve(
            _assignment(
                assignment_type="full_cycle",
                adb_serial="RFGL145VCKE",
                starts_at=(now - timedelta(hours=1)).isoformat(),
                ends_at=(now + timedelta(hours=5)).isoformat(),
            ),
            run_type="dm_welcome_baseline",
            require_assignment=True,
            enforce_window=False,
        )
        self.assertTrue(ctx["assignment_found"])
        self.assertEqual(ctx["reason"], "assignment_resolved")
        self.assertEqual(ctx["adb_serial"], "RFGL145VCKE")

    def test_full_cycle_welcome_session_send_resolves_outside_window_when_not_enforced(
        self,
    ) -> None:
        now = datetime.now(timezone.utc)
        ctx = self._resolve(
            _assignment(
                assignment_type="full_cycle",
                adb_serial="RFGL145VCKE",
                starts_at=(now - timedelta(hours=2)).isoformat(),
                ends_at=(now - timedelta(hours=1)).isoformat(),
            ),
            run_type="dm_welcome_session_send",
            enforce_window=False,
        )
        self.assertTrue(ctx["assignment_found"])
        self.assertEqual(ctx["reason"], "assignment_resolved")
        self.assertEqual(ctx["adb_serial"], "RFGL145VCKE")
        self.assertEqual(ctx["package_name"], "com.instagram.androie")

    def test_outreach_only_welcome_session_send_incompatible(self) -> None:
        ctx = self._resolve(
            _assignment(assignment_type="outreach_only"),
            run_type="dm_welcome_session_send",
        )
        self.assertFalse(ctx["assignment_found"])
        self.assertEqual(ctx["reason"], "assignment_type_incompatible")

    def test_outreach_only_welcome_baseline_incompatible(self) -> None:
        ctx = self._resolve(
            _assignment(assignment_type="outreach_only"),
            run_type="dm_welcome_baseline",
            require_assignment=True,
        )
        self.assertFalse(ctx["assignment_found"])
        self.assertEqual(ctx["reason"], "assignment_type_incompatible")
        self.assertFalse(ctx["fallback_used"])

    def test_full_cycle_welcome_scan_not_globally_allowed(self) -> None:
        ctx = self._resolve(
            _assignment(assignment_type="full_cycle"),
            run_type="dm_welcome_scan",
            require_assignment=True,
        )
        self.assertFalse(ctx["assignment_found"])
        self.assertEqual(ctx["reason"], "assignment_type_incompatible")

    def test_unsupported_run_type_still_blocked(self) -> None:
        ctx = self._resolve(
            _assignment(assignment_type="full_cycle"),
            run_type="unsupported_pipeline",
            require_assignment=True,
        )
        self.assertFalse(ctx["assignment_found"])
        self.assertEqual(ctx["reason"], "assignment_type_incompatible")
        self.assertFalse(ctx["fallback_used"])

    def test_outreach_only_account_session_incompatible(self) -> None:
        ctx = self._resolve(
            _assignment(assignment_type="outreach_only"),
            run_type="account_session",
        )
        self.assertFalse(ctx["assignment_found"])
        self.assertEqual(ctx["reason"], "assignment_type_incompatible")
        self.assertFalse(ctx["fallback_used"])

    def test_outreach_only_unfollow_session_incompatible(self) -> None:
        ctx = self._resolve(
            _assignment(assignment_type="outreach_only"),
            run_type="unfollow_session",
            require_assignment=True,
        )
        self.assertFalse(ctx["assignment_found"])
        self.assertEqual(ctx["reason"], "assignment_type_incompatible")
        self.assertFalse(ctx["fallback_used"])

    def test_outreach_only_unfollow_outreach_pipeline_incompatible(self) -> None:
        ctx = self._resolve(
            _assignment(assignment_type="outreach_only"),
            run_type="unfollow_outreach_pipeline",
            require_assignment=True,
        )
        self.assertFalse(ctx["assignment_found"])
        self.assertEqual(ctx["reason"], "assignment_type_incompatible")
        self.assertFalse(ctx["fallback_used"])

    def test_full_cycle_unfollow_outreach_pipeline_missing_device_still_blocks(self) -> None:
        ctx = self._resolve(
            _assignment(assignment_type="full_cycle", adb_serial=""),
            run_type="unfollow_outreach_pipeline",
            require_assignment=True,
        )
        self.assertFalse(ctx["assignment_found"])
        self.assertEqual(ctx["reason"], "assignment_device_missing_adb_serial")
        self.assertFalse(ctx["fallback_used"])

    def test_full_cycle_unfollow_session_missing_device_still_blocks(self) -> None:
        ctx = self._resolve(
            _assignment(assignment_type="full_cycle", adb_serial=""),
            run_type="unfollow_session",
            require_assignment=True,
        )
        self.assertFalse(ctx["assignment_found"])
        self.assertEqual(ctx["reason"], "assignment_device_missing_adb_serial")
        self.assertFalse(ctx["fallback_used"])

    def test_missing_assignment_fallback_when_not_required(self) -> None:
        ctx = self._resolve(None, require_assignment=False)
        self.assertFalse(ctx["assignment_found"])
        self.assertTrue(ctx["fallback_used"])
        self.assertEqual(ctx["reason"], "assignment_not_found")

    def test_missing_assignment_no_fallback_when_required(self) -> None:
        ctx = self._resolve(None, require_assignment=True)
        self.assertFalse(ctx["assignment_found"])
        self.assertFalse(ctx["fallback_used"])
        self.assertEqual(ctx["reason"], "assignment_not_found")

    def test_enforce_window_blocks_outside_window(self) -> None:
        now = datetime.now(timezone.utc)
        ctx = self._resolve(
            _assignment(
                starts_at=(now - timedelta(hours=2)).isoformat(),
                ends_at=(now - timedelta(hours=1)).isoformat(),
            ),
            enforce_window=True,
        )
        self.assertFalse(ctx["assignment_found"])
        self.assertEqual(ctx["reason"], "assignment_window_inactive")

    def test_window_not_enforced_resolves_outside_window(self) -> None:
        now = datetime.now(timezone.utc)
        ctx = self._resolve(
            _assignment(
                starts_at=(now - timedelta(hours=2)).isoformat(),
                ends_at=(now - timedelta(hours=1)).isoformat(),
            ),
            enforce_window=False,
        )
        self.assertTrue(ctx["assignment_found"])

    def test_missing_device_adb_serial_returns_stable_block_reason(self) -> None:
        ctx = self._resolve(_assignment(assignment_type="full_cycle", adb_serial=""), run_type="account_session")
        self.assertFalse(ctx["assignment_found"])
        self.assertEqual(ctx["reason"], "assignment_device_missing_adb_serial")
        self.assertFalse(ctx["fallback_used"])

    def test_redacted_log_fields_hide_sensitive_values(self) -> None:
        ctx = self._resolve(_assignment())
        fields = resolver.sensitive_log_fields(ctx, include_sensitive=False)
        self.assertNotIn("adb_serial", fields)
        self.assertNotIn("device_udid", fields)
        self.assertNotIn("host_machine", fields)
        self.assertEqual(fields["adb_serial_suffix"], "5554")
        self.assertTrue(fields["adb_serial_hash"])


class SupabaseAssignmentDispatchHelperTest(unittest.TestCase):
    def test_helper_reads_only_reserved_active_and_fetches_device_clone(self) -> None:
        calls: list[tuple[str, str, dict]] = []

        def fake_request(method: str, table: str, *, query=None, body=None, **_kwargs):
            calls.append((method, table, dict(query or {})))
            self.assertIsNone(body)
            if table == "account_assignments":
                self.assertEqual(query["status"], "in.(reserved,active)")
                return [
                    {
                        "id": "assignment-1",
                        "account_id": "account-1",
                        "device_id": "device-1",
                        "clone_id": "clone-1",
                        "app_instance_id": "app-instance-1",
                        "status": "reserved",
                    }
                ]
            if table == "phone_devices":
                return [{"id": "device-1", "adb_serial": "emulator-5554"}]
            if table == "phone_clones":
                return [{"id": "clone-1", "clone_index": 1}]
            if table == "phone_app_instances":
                return [{"id": "app-instance-1", "instance_index": 1, "visible_label": "Instagram 1"}]
            raise AssertionError(f"unexpected table {table}")

        with patch.object(supabase_client, "_request_json", side_effect=fake_request):
            row = supabase_client.load_open_account_assignment_for_dispatch("account-1")

        self.assertEqual(row["phone_device"]["adb_serial"], "emulator-5554")
        self.assertEqual(row["phone_clone"]["clone_index"], 1)
        self.assertEqual(row["phone_app_instance"]["visible_label"], "Instagram 1")
        self.assertEqual([call[0] for call in calls], ["GET", "GET", "GET", "GET"])
        self.assertEqual([call[1] for call in calls], [
            "account_assignments",
            "phone_devices",
            "phone_clones",
            "phone_app_instances",
        ])


if __name__ == "__main__":
    unittest.main()
