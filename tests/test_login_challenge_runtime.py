from __future__ import annotations

import unittest
from unittest.mock import patch

import login_challenge_runtime as runtime


class LoginChallengeRuntimeTests(unittest.TestCase):
    def test_publish_login_challenge_incident_uses_safe_metadata(self) -> None:
        with patch.object(runtime, "publish_account_incident", return_value={"published": True}) as publish:
            runtime.publish_login_challenge_pending_incident(
                account_id="11111111-1111-4111-8111-111111111111",
                expected_username="cinema_catchup",
                run_id="run-1",
                challenge_type="email",
                screen_type="email_code_challenge",
                reason="email_verification_code_required",
                dashboard_action_type="enter_email_verification_code",
                masked_email_present=True,
            )
        kwargs = publish.call_args.kwargs
        self.assertEqual(kwargs["incident_type"], "email_verification_code_required")
        self.assertEqual(kwargs["metadata"]["dashboard_action_type"], "enter_email_verification_code")
        self.assertNotIn("verification_code", kwargs["metadata"])

    def test_consume_verification_code_for_worker_delegates_rpc(self) -> None:
        with patch.object(runtime, "call_rpc", return_value={"ok": True, "verification_code": "123456"}) as rpc:
            out = runtime.consume_verification_code_for_worker(
                action_id="22222222-2222-4222-8222-222222222222",
                account_id="11111111-1111-4111-8111-111111111111",
                run_id="run-1",
            )
        self.assertTrue(out["ok"])
        rpc.assert_called_once()
