from __future__ import annotations

import unittest
from unittest.mock import patch

import runtime_events


class RuntimeEventsTest(unittest.TestCase):
    def test_disabled_returns_disabled(self) -> None:
        with patch.object(runtime_events.config, "RUNTIME_EVENTS_ENABLED", False, create=True):
            out = runtime_events.publish_runtime_event("run_started")
        self.assertEqual(out["reason"], "disabled")
        self.assertFalse(out["published"])

    def test_debug_skipped_when_include_debug_false(self) -> None:
        with (
            patch.object(runtime_events.config, "RUNTIME_EVENTS_ENABLED", True, create=True),
            patch.object(runtime_events.config, "RUNTIME_EVENTS_INCLUDE_DEBUG", False, create=True),
        ):
            out = runtime_events.publish_runtime_event("debug_probe", severity="debug")
        self.assertEqual(out["reason"], "debug_disabled")

    def test_invalid_severity_handled_fail_open(self) -> None:
        with patch.object(runtime_events.config, "RUNTIME_EVENTS_ENABLED", True, create=True):
            out = runtime_events.publish_runtime_event("run_started", severity="loud")
        self.assertEqual(out["reason"], "invalid_severity")

    def test_invalid_visibility_handled_fail_open(self) -> None:
        with patch.object(runtime_events.config, "RUNTIME_EVENTS_ENABLED", True, create=True):
            out = runtime_events.publish_runtime_event("run_started", visibility="public")
        self.assertEqual(out["reason"], "invalid_visibility")

    def test_enabled_success_calls_fake_supabase_insert(self) -> None:
        with (
            patch.object(runtime_events.config, "RUNTIME_EVENTS_ENABLED", True, create=True),
            patch.object(
                runtime_events.supabase_client,
                "insert_runtime_event",
                return_value={"id": "event-1"},
            ) as insert_event,
        ):
            out = runtime_events.publish_runtime_event(
                "run_started",
                account_id="00000000-0000-4000-8000-000000000001",
                metadata={"ok": True},
            )
        self.assertTrue(out["published"])
        self.assertEqual(out["id"], "event-1")
        self.assertEqual(insert_event.call_args.args[0]["event_type"], "run_started")

    def test_insert_failure_fail_open_no_raise(self) -> None:
        with (
            patch.object(runtime_events.config, "RUNTIME_EVENTS_ENABLED", True, create=True),
            patch.object(
                runtime_events.supabase_client,
                "insert_runtime_event",
                side_effect=RuntimeError("network down"),
            ),
        ):
            out = runtime_events.publish_runtime_event("run_started")
        self.assertFalse(out["published"])
        self.assertEqual(out["reason"], "insert_failed")

    def test_redaction_removes_sensitive_nested_keys(self) -> None:
        redacted = runtime_events.redact_metadata(
            {
                "safe": "ok",
                "password": "secret",
                "nested": {
                    "access_token": "token",
                    "keep": True,
                    "items": [{"cookie": "abc", "name": "kept"}],
                },
            }
        )
        self.assertEqual(redacted["safe"], "ok")
        self.assertNotIn("password", redacted)
        self.assertNotIn("access_token", redacted["nested"])
        self.assertNotIn("cookie", redacted["nested"]["items"][0])
        self.assertEqual(redacted["nested"]["items"][0]["name"], "kept")

    def test_client_safe_strips_ops_sensitive_adb_serial_device_udid(self) -> None:
        redacted = runtime_events.redact_metadata(
            {
                "adb_serial": "emulator-5554",
                "device_udid": "udid-secret",
                "host_machine": "host-a",
            },
            visibility="client_safe",
        )
        self.assertEqual(redacted["adb_serial_suffix"], "5554")
        self.assertIn("adb_serial_hash", redacted)
        self.assertNotIn("adb_serial", redacted)
        self.assertNotIn("device_udid", redacted)
        self.assertNotIn("host_machine", redacted)


if __name__ == "__main__":
    unittest.main()
