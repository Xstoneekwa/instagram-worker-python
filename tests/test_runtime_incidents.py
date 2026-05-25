from __future__ import annotations

import unittest
from unittest.mock import patch

import runtime_incidents


class RuntimeIncidentsTest(unittest.TestCase):
    def test_disabled_returns_disabled_no_supabase_call(self) -> None:
        with (
            patch.object(runtime_incidents.config, "RUNTIME_INCIDENTS_ENABLED", False, create=True),
            patch.object(
                runtime_incidents.supabase_client,
                "upsert_account_incident",
            ) as upsert,
        ):
            out = runtime_incidents.publish_account_incident(
                "active_instagram_account_mismatch",
                "account:1:identity:mismatch:other",
            )
        self.assertFalse(out["published"])
        self.assertEqual(out["reason"], "disabled")
        upsert.assert_not_called()

    def test_invalid_incident_type_fail_open(self) -> None:
        with patch.object(runtime_incidents.config, "RUNTIME_INCIDENTS_ENABLED", True, create=True):
            out = runtime_incidents.publish_account_incident("", "dedupe-1")
        self.assertFalse(out["published"])
        self.assertEqual(out["reason"], "missing_incident_type")

    def test_invalid_dedupe_key_fail_open(self) -> None:
        with patch.object(runtime_incidents.config, "RUNTIME_INCIDENTS_ENABLED", True, create=True):
            out = runtime_incidents.publish_account_incident("device_offline", "  ")
        self.assertFalse(out["published"])
        self.assertEqual(out["reason"], "missing_dedupe_key")

    def test_invalid_severity_fail_open(self) -> None:
        with patch.object(runtime_incidents.config, "RUNTIME_INCIDENTS_ENABLED", True, create=True):
            out = runtime_incidents.publish_account_incident(
                "device_offline",
                "device:1:offline",
                severity="loud",
            )
        self.assertFalse(out["published"])
        self.assertEqual(out["reason"], "invalid_severity")

    def test_invalid_metadata_non_object_fail_open(self) -> None:
        with patch.object(runtime_incidents.config, "RUNTIME_INCIDENTS_ENABLED", True, create=True):
            out = runtime_incidents.publish_account_incident(
                "device_offline",
                "device:1:offline",
                metadata=["not", "object"],  # type: ignore[arg-type]
            )
        self.assertFalse(out["published"])
        self.assertEqual(out["reason"], "invalid_metadata")

    def test_success_calls_fake_upsert_and_returns_fields(self) -> None:
        with (
            patch.object(runtime_incidents.config, "RUNTIME_INCIDENTS_ENABLED", True, create=True),
            patch.object(
                runtime_incidents.supabase_client,
                "upsert_account_incident",
                return_value={
                    "id": "incident-1",
                    "dedupe_key": "account:1:identity:mismatch:other",
                    "status": "open",
                    "severity": "critical",
                    "occurrence_count": 2,
                },
            ) as upsert,
        ):
            out = runtime_incidents.publish_account_incident(
                "active_instagram_account_mismatch",
                "account:1:identity:mismatch:other",
                severity="critical",
                account_id="00000000-0000-4000-8000-000000000001",
            )
        self.assertTrue(out["published"])
        self.assertEqual(out["incident_id"], "incident-1")
        self.assertEqual(out["occurrence_count"], 2)
        self.assertEqual(
            upsert.call_args.args[0]["incident_type"],
            "active_instagram_account_mismatch",
        )

    def test_db_failure_fail_open_no_raise(self) -> None:
        with (
            patch.object(runtime_incidents.config, "RUNTIME_INCIDENTS_ENABLED", True, create=True),
            patch.object(
                runtime_incidents.supabase_client,
                "upsert_account_incident",
                side_effect=RuntimeError("network down"),
            ),
        ):
            out = runtime_incidents.publish_account_incident(
                "device_offline",
                "device:1:offline",
            )
        self.assertFalse(out["published"])
        self.assertEqual(out["reason"], "upsert_failed")

    def test_redaction_removes_nested_sensitive_keys(self) -> None:
        with (
            patch.object(runtime_incidents.config, "RUNTIME_INCIDENTS_ENABLED", True, create=True),
            patch.object(
                runtime_incidents.supabase_client,
                "upsert_account_incident",
                return_value={"id": "incident-2", "dedupe_key": "k", "status": "open"},
            ) as upsert,
        ):
            runtime_incidents.publish_account_incident(
                "device_offline",
                "device:1:offline",
                metadata={
                    "safe": "ok",
                    "password": "secret",
                    "nested": {"access_token": "tok", "keep": True},
                },
            )
        metadata = upsert.call_args.args[0]["metadata"]
        self.assertEqual(metadata["safe"], "ok")
        self.assertNotIn("password", metadata)
        self.assertNotIn("access_token", metadata["nested"])
        self.assertTrue(metadata["nested"]["keep"])

    def test_identity_mismatch_builder_expected_payload(self) -> None:
        payload = runtime_incidents.build_identity_mismatch_incident(
            account_id="00000000-0000-4000-8000-000000000001",
            expected_username="expected_user",
            actual_username="actual_user",
            run_id="run-1",
            verification_method="xml",
        )
        self.assertEqual(payload["incident_type"], "active_instagram_account_mismatch")
        self.assertEqual(
            payload["dedupe_key"],
            "account:00000000-0000-4000-8000-000000000001:identity:mismatch:actual_user",
        )
        self.assertEqual(payload["severity"], "critical")
        self.assertIn("expected_user", payload["admin_message"])
        self.assertIn("actual_user", payload["admin_message"])
        self.assertEqual(payload["source"], "account_identity_guard")

    def test_builder_does_not_call_supabase(self) -> None:
        with patch.object(
            runtime_incidents.supabase_client,
            "upsert_account_incident",
        ) as upsert:
            runtime_incidents.build_identity_mismatch_incident(
                account_id="1",
                expected_username="a",
                actual_username="b",
            )
            runtime_incidents.build_assignment_dispatch_incident(
                incident_type="assignment_dispatch_missing",
                account_id="1",
            )
        upsert.assert_not_called()


if __name__ == "__main__":
    unittest.main()
