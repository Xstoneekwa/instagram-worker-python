"""Unit tests for schedule gate helpers and assignment resolver schedule parity."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import assignment_dispatch_resolver as resolver


class ScheduleResolverTests(unittest.TestCase):
    @patch("assignment_dispatch_resolver._window_contains_now", return_value=True)
    @patch("assignment_dispatch_resolver.supabase_client.load_open_account_assignment_for_dispatch")
    @patch("assignment_dispatch_resolver.supabase_client.call_rpc")
    def test_enforce_window_blocks_phone_rest(self, mock_call_rpc, mock_load_assignment, _mock_window):
        mock_load_assignment.return_value = {
            "id": "asg-1",
            "assignment_type": "full_cycle",
            "slot_kind": "full_cycle_6h",
            "starts_at": "2026-06-01T00:00:00+00:00",
            "ends_at": "2026-06-01T06:00:00+00:00",
            "device_id": "dev-1",
            "clone_id": "clone-1",
            "app_instance_id": "app-instance-1",
            "phone_devices": {"adb_serial": "emulator-5554", "device_kind": "emulator", "pool_type": "full_cycle"},
            "phone_clones": {"clone_index": 1, "clone_label": "Clone 1"},
            "phone_app_instances": {"instance_type": "clone", "instance_index": 1, "visible_label": "Instagram 1"},
        }
        mock_call_rpc.return_value = {
            "ok": False,
            "reason": "phone_rest_active",
            "assignment_id": "asg-1",
        }

        ctx = resolver.resolve_account_assignment_runtime_context(
            "acct-1",
            "account_session",
            require_assignment=True,
            enforce_window=True,
        )

        self.assertFalse(ctx["assignment_found"])
        self.assertEqual(ctx["reason"], "phone_rest_active")
        mock_call_rpc.assert_called_once()


if __name__ == "__main__":
    unittest.main()
