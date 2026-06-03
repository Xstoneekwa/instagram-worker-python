from __future__ import annotations

import unittest
from unittest.mock import patch

import login_dashboard_action_publisher as publisher


class LoginDashboardActionPublisherTests(unittest.TestCase):
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
