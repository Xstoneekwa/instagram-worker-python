from __future__ import annotations

import unittest
from unittest.mock import patch

import login_dashboard_action_publisher as publisher


class LoginDashboardActionPublisherTests(unittest.TestCase):
    def test_incident_lineage_is_persisted_by_rpc(self) -> None:
        captured: dict = {}

        def _call_rpc(name: str, params: dict) -> dict:
            captured["name"] = name
            captured["params"] = params
            return {"id": "action-1", "incident_id": params["p_incident_id"]}

        with patch.object(publisher, "call_rpc", side_effect=_call_rpc):
            out = publisher.upsert_login_challenge_dashboard_action(
                account_id="11111111-1111-4111-8111-111111111111",
                action_type="review_login_challenge",
                incident_id="22222222-2222-4222-8222-222222222222",
                run_id="run-1",
            )

        self.assertTrue(out["published"])
        self.assertEqual(
            captured["params"]["p_incident_id"],
            "22222222-2222-4222-8222-222222222222",
        )

    def test_standalone_action_keeps_null_incident_lineage(self) -> None:
        captured: dict = {}

        with patch.object(
            publisher,
            "call_rpc",
            side_effect=lambda name, params: captured.update(name=name, params=params) or {"id": "action-1"},
        ):
            publisher.upsert_login_challenge_dashboard_action(
                account_id="11111111-1111-4111-8111-111111111111",
                action_type="review_login_challenge",
                run_id="run-1",
            )

        self.assertIsNone(captured["params"]["p_incident_id"])

    def test_fallback_persists_incident_lineage(self) -> None:
        requests: list[tuple[str, str, dict]] = []

        def _request(method: str, table: str, **kwargs: object) -> list[dict]:
            requests.append((method, table, dict(kwargs)))
            if method == "GET":
                return []
            return [{"id": "action-fallback"}]

        with (
            patch.object(publisher, "call_rpc", side_effect=RuntimeError("rpc unavailable")),
            patch("supabase_client._request_json", side_effect=_request),
        ):
            out = publisher.upsert_login_challenge_dashboard_action(
                account_id="11111111-1111-4111-8111-111111111111",
                action_type="review_login_challenge",
                incident_id="22222222-2222-4222-8222-222222222222",
                run_id="run-1",
            )

        self.assertTrue(out["published"])
        post = next(row for row in requests if row[0] == "POST")
        self.assertEqual(
            post[2]["body"]["incident_id"],
            "22222222-2222-4222-8222-222222222222",
        )

    def test_fallback_retry_without_incident_does_not_erase_existing_lineage(self) -> None:
        requests: list[tuple[str, str, dict]] = []

        def _request(method: str, table: str, **kwargs: object) -> list[dict]:
            requests.append((method, table, dict(kwargs)))
            if method == "GET":
                return [{"id": "action-existing", "status": "pending"}]
            return [{"id": "action-existing"}]

        with (
            patch.object(publisher, "call_rpc", side_effect=RuntimeError("rpc unavailable")),
            patch("supabase_client._request_json", side_effect=_request),
        ):
            out = publisher.upsert_login_challenge_dashboard_action(
                account_id="11111111-1111-4111-8111-111111111111",
                action_type="review_login_challenge",
                run_id="run-1",
            )

        self.assertTrue(out["published"])
        patch_request = next(row for row in requests if row[0] == "PATCH")
        self.assertNotIn("incident_id", patch_request[2]["body"])

    def test_disabled_returns_reason(self) -> None:
        with patch.object(publisher, "_enabled", return_value=False):
            out = publisher.upsert_login_challenge_dashboard_action(
                account_id="11111111-1111-4111-8111-111111111111",
                action_type="enter_email_verification_code",
            )
        self.assertFalse(out["published"])
        self.assertEqual(out["reason"], "disabled")

    def test_upsert_email_action_strips_forbidden_metadata(self) -> None:
        captured: dict = {}

        def _call_rpc(name: str, params: dict) -> dict:
            captured["name"] = name
            captured["params"] = params
            return {"id": "action-1"}

        with patch.object(publisher, "call_rpc", side_effect=_call_rpc):
            out = publisher.upsert_login_challenge_dashboard_action(
                account_id="11111111-1111-4111-8111-111111111111",
                action_type="enter_email_verification_code",
                run_id="run-1",
                challenge_type="email",
                screen_type="email_code_challenge",
                masked_email_present=True,
                metadata={"verification_code": "123456", "safe": "ok"},
            )

        self.assertTrue(out["published"])
        self.assertEqual(captured["name"], "upsert_login_challenge_dashboard_action")
        metadata = captured["params"]["p_metadata"]
        self.assertEqual(metadata["safe"], "ok")
        self.assertNotIn("verification_code", metadata)
        self.assertEqual(metadata["challenge_type"], "email")
        self.assertEqual(metadata["ttl_minutes"], publisher.EMAIL_CODE_ACTION_TTL_MINUTES)
        self.assertIn("action_expires_at", metadata)
        self.assertEqual(captured["params"]["p_action_type"], "enter_email_verification_code")
        self.assertEqual(captured["params"]["p_status"], "pending")
        self.assertEqual(captured["params"]["p_audience"], "client")
        self.assertTrue(captured["params"]["p_requires_client_action"])

    def test_upsert_login_package_mismatch_action_is_admin_critical_and_safe(self) -> None:
        captured: dict = {}

        def _call_rpc(name: str, params: dict) -> dict:
            captured["name"] = name
            captured["params"] = params
            return {"id": "action-2"}

        with patch.object(publisher, "call_rpc", side_effect=_call_rpc):
            out = publisher.upsert_login_challenge_dashboard_action(
                account_id="11111111-1111-4111-8111-111111111111",
                action_type="review_login_package_mismatch",
                run_id="run-1",
                metadata={
                    "expected_package_name": "com.instagram.androie",
                    "actual_foreground_package": "com.instagram.android",
                    "password": "do-not-leak",
                    "secret_ref": "do-not-leak",
                },
            )

        self.assertTrue(out["published"])
        params = captured["params"]
        self.assertEqual(params["p_action_type"], "review_login_package_mismatch")
        self.assertEqual(params["p_status"], "pending")
        self.assertEqual(params["p_severity"], "critical")
        self.assertEqual(params["p_audience"], "admin")
        self.assertFalse(params["p_requires_client_action"])
        self.assertTrue(params["p_blocking_campaign"])
        self.assertIn("Wrong app/clone detected", params["p_safe_client_message"])
        metadata = params["p_metadata"]
        self.assertEqual(metadata["expected_package_name"], "com.instagram.androie")
        self.assertEqual(metadata["actual_foreground_package"], "com.instagram.android")
        self.assertNotIn("password", metadata)
        self.assertNotIn("secret_ref", metadata)

    def test_wrong_password_action_is_account_scoped_client_safe_and_opaque(self) -> None:
        captured: dict = {}

        def _call_rpc(name: str, params: dict) -> dict:
            captured["name"] = name
            captured["params"] = params
            return {"id": "action-password"}

        with patch.object(publisher, "call_rpc", side_effect=_call_rpc):
            out = publisher.upsert_login_challenge_dashboard_action(
                account_id="11111111-1111-4111-8111-111111111111",
                action_type="update_instagram_password",
                run_id="run-1",
                metadata={"reason_code": "instagram_credentials_rejected", "password": "never"},
            )

        self.assertTrue(out["published"])
        params = captured["params"]
        self.assertEqual(params["p_action_type"], "update_instagram_password")
        self.assertEqual(params["p_audience"], "client")
        self.assertTrue(params["p_requires_client_action"])
        self.assertTrue(params["p_blocking_campaign"])
        self.assertEqual(params["p_action_deep_link"], "/instagram-client?view=account")
        self.assertNotIn("password", params["p_metadata"])
