from __future__ import annotations

import unittest

from instagram_login_status_classifier import (
    LoginProbeOutcome,
    classify_login_probe_outcome,
)


class InstagramLoginStatusClassifierTest(unittest.TestCase):
    def test_connected_mapping_complete(self) -> None:
        result = classify_login_probe_outcome(LoginProbeOutcome.CONNECTED)

        self.assertTrue(result.ok)
        self.assertEqual(result.login_status, "connected")
        self.assertEqual(result.provisioning_status, "ready")
        self.assertEqual(result.onboarding_status, "ready")
        self.assertFalse(result.reauth_required)
        self.assertIsNone(result.reauth_reason)
        self.assertEqual(result.reason, "login_connected")
        self.assertTrue(result.should_publish)

    def test_connected_status_replaces_client_ready_to_connect_projection(self) -> None:
        connected = classify_login_probe_outcome(LoginProbeOutcome.CONNECTED)
        not_started = classify_login_probe_outcome("unknown")

        self.assertEqual(connected.login_status, "connected")
        self.assertEqual(connected.provisioning_status, "ready")
        self.assertEqual(connected.onboarding_status, "ready")
        self.assertTrue(connected.should_publish)
        self.assertNotEqual(not_started.login_status, "connected")
        self.assertNotEqual(not_started.provisioning_status, "ready")

    def test_needs_2fa_mapping_complete(self) -> None:
        result = classify_login_probe_outcome("needs_2fa")

        self.assertFalse(result.ok)
        self.assertEqual(result.login_status, "needs_2fa")
        self.assertEqual(result.provisioning_status, "login_verification_pending")
        self.assertEqual(result.onboarding_status, "verification_pending")
        self.assertIsNone(result.reauth_required)
        self.assertIsNone(result.reauth_reason)
        self.assertEqual(result.reason, "two_factor_required")
        self.assertTrue(result.should_publish)

    def test_checkpoint_mapping_complete(self) -> None:
        result = classify_login_probe_outcome(LoginProbeOutcome.CHECKPOINT)

        self.assertFalse(result.ok)
        self.assertEqual(result.login_status, "checkpoint")
        self.assertEqual(result.provisioning_status, "login_verification_pending")
        self.assertEqual(result.onboarding_status, "verification_pending")
        self.assertIsNone(result.reauth_required)
        self.assertIsNone(result.reauth_reason)
        self.assertEqual(result.reason, "checkpoint_required")
        self.assertTrue(result.should_publish)

    def test_login_failed_mapping_with_reauth_required(self) -> None:
        result = classify_login_probe_outcome(LoginProbeOutcome.LOGIN_FAILED)

        self.assertFalse(result.ok)
        self.assertEqual(result.login_status, "failed")
        self.assertEqual(result.provisioning_status, "failed")
        self.assertEqual(result.onboarding_status, "blocked")
        self.assertTrue(result.reauth_required)
        self.assertEqual(result.reauth_reason, "credentials_invalid")
        self.assertEqual(result.reason, "login_failed")
        self.assertTrue(result.should_publish)

    def test_login_failed_allows_login_failed_reauth_reason(self) -> None:
        result = classify_login_probe_outcome(
            LoginProbeOutcome.LOGIN_FAILED,
            login_failed_reauth_reason="login_failed",
        )

        self.assertEqual(result.reauth_reason, "login_failed")

    def test_logged_out_mapping_complete(self) -> None:
        result = classify_login_probe_outcome(LoginProbeOutcome.LOGGED_OUT)

        self.assertFalse(result.ok)
        self.assertEqual(result.login_status, "logged_out")
        self.assertEqual(result.provisioning_status, "login_pending")
        self.assertEqual(result.onboarding_status, "credentials_submitted")
        self.assertIsNone(result.reauth_required)
        self.assertIsNone(result.reauth_reason)
        self.assertEqual(result.reason, "session_expired")
        self.assertTrue(result.should_publish)

    def test_skipped_not_implemented_is_not_publishable(self) -> None:
        result = classify_login_probe_outcome(LoginProbeOutcome.SKIPPED_NOT_IMPLEMENTED)

        self.assertFalse(result.ok)
        self.assertFalse(result.should_publish)
        self.assertEqual(result.reason, "probe_not_implemented")
        self.assertIsNone(result.login_status)

    def test_unknown_is_not_publishable(self) -> None:
        result = classify_login_probe_outcome("unexpected")

        self.assertFalse(result.ok)
        self.assertEqual(result.outcome, LoginProbeOutcome.UNKNOWN)
        self.assertFalse(result.should_publish)
        self.assertEqual(result.reason, "unknown_login_probe_outcome")
        self.assertEqual(result.error, "unsupported_login_probe_outcome")

    def test_default_metadata_is_added(self) -> None:
        result = classify_login_probe_outcome(LoginProbeOutcome.CONNECTED)

        self.assertEqual(result.metadata["source"], "provisioner")
        self.assertEqual(result.metadata["stage"], "login_probe")
        self.assertEqual(result.metadata["probe_version"], "v1")

    def test_password_metadata_is_removed(self) -> None:
        result = classify_login_probe_outcome(
            LoginProbeOutcome.CONNECTED,
            metadata={"password": "do-not-keep", "safe": "ok"},
        )

        self.assertNotIn("password", result.metadata)
        self.assertEqual(result.metadata["safe"], "ok")

    def test_secret_and_device_metadata_is_removed_recursively(self) -> None:
        result = classify_login_probe_outcome(
            LoginProbeOutcome.CONNECTED,
            metadata={
                "secret_ref": "vault://x",
                "vault": "raw",
                "xml": "<node />",
                "screenshot": "base64",
                "adb_serial": "device-1",
                "device_udid": "udid",
                "nested": {"session_cookie": "cookie", "safe": "ok"},
                "items": [{"token": "token", "kept": True}],
            },
        )

        for key in ("secret_ref", "vault", "xml", "screenshot", "adb_serial", "device_udid"):
            self.assertNotIn(key, result.metadata)
        self.assertNotIn("session_cookie", result.metadata["nested"])
        self.assertEqual(result.metadata["nested"]["safe"], "ok")
        self.assertNotIn("token", result.metadata["items"][0])
        self.assertTrue(result.metadata["items"][0]["kept"])

    def test_mismatch_is_not_mapped_here(self) -> None:
        result = classify_login_probe_outcome("mismatch")

        self.assertEqual(result.outcome, LoginProbeOutcome.UNKNOWN)
        self.assertFalse(result.should_publish)
        self.assertNotEqual(result.login_status, "mismatch")


if __name__ == "__main__":
    unittest.main()
