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
        },
    }


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
        self.assertEqual(ctx["reason"], "assignment_resolved")

    def test_full_cycle_outreach_session_accepted(self) -> None:
        ctx = self._resolve(_assignment(assignment_type="full_cycle"))
        self.assertTrue(ctx["assignment_found"])
        self.assertEqual(ctx["assignment_type"], "full_cycle")

    def test_outreach_only_account_session_incompatible(self) -> None:
        ctx = self._resolve(
            _assignment(assignment_type="outreach_only"),
            run_type="account_session",
        )
        self.assertFalse(ctx["assignment_found"])
        self.assertEqual(ctx["reason"], "assignment_type_incompatible")
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
