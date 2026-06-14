from __future__ import annotations

import os
import unittest
from unittest.mock import Mock, patch

import instagram_login_provisioner as provisioner
from instagram_login_status_classifier import (
    LoginProbeOutcome,
    classify_login_probe_outcome,
)


ACCOUNT_ID = "42c625c2-e761-4100-8a9d-7ae1373de97d"


class InstagramLoginProvisionerTest(unittest.TestCase):
    def test_flag_off_returns_disabled_without_publish(self) -> None:
        publisher = Mock()
        classification = classify_login_probe_outcome(LoginProbeOutcome.CONNECTED)

        with patch.dict(os.environ, {"INSTAGRAM_LOGIN_PROVISIONER_ENABLED": "false"}):
            result = provisioner.publish_classified_login_status(
                account_id=ACCOUNT_ID,
                classification=classification,
                publisher=publisher,
            )

        self.assertFalse(result["published"])
        self.assertEqual(result["reason"], "disabled")
        publisher.assert_not_called()

    def test_flag_on_connected_outcome_publishes_connected(self) -> None:
        publisher = Mock(return_value={"published": True})

        with patch.dict(os.environ, {"INSTAGRAM_LOGIN_PROVISIONER_ENABLED": "true"}):
            result = provisioner.run_login_provisioning_check(
                account_id=ACCOUNT_ID,
                outcome=LoginProbeOutcome.CONNECTED,
                external_request_id="login:connected",
                publisher=publisher,
            )

        self.assertTrue(result["publish_result"]["published"])
        kwargs = publisher.call_args.kwargs
        self.assertEqual(kwargs["login_status"], "connected")
        self.assertEqual(kwargs["provisioning_status"], "ready")
        self.assertEqual(kwargs["onboarding_status"], "ready")
        self.assertFalse(kwargs["reauth_required"])

    def test_flag_on_needs_2fa_publishes_verification_pending(self) -> None:
        publisher = Mock(return_value={"published": True})

        with patch.dict(os.environ, {"INSTAGRAM_LOGIN_PROVISIONER_ENABLED": "1"}):
            provisioner.run_login_provisioning_check(
                account_id=ACCOUNT_ID,
                outcome=LoginProbeOutcome.NEEDS_2FA,
                publisher=publisher,
            )

        kwargs = publisher.call_args.kwargs
        self.assertEqual(kwargs["login_status"], "needs_2fa")
        self.assertEqual(kwargs["provisioning_status"], "login_verification_pending")
        self.assertEqual(kwargs["onboarding_status"], "verification_pending")
        self.assertEqual(kwargs["reason"], "two_factor_required")

    def test_flag_on_checkpoint_publishes_verification_pending(self) -> None:
        publisher = Mock(return_value={"published": True})

        with patch.dict(os.environ, {"INSTAGRAM_LOGIN_PROVISIONER_ENABLED": "yes"}):
            provisioner.run_login_provisioning_check(
                account_id=ACCOUNT_ID,
                outcome=LoginProbeOutcome.CHECKPOINT,
                publisher=publisher,
            )

        kwargs = publisher.call_args.kwargs
        self.assertEqual(kwargs["login_status"], "checkpoint")
        self.assertEqual(kwargs["provisioning_status"], "login_verification_pending")
        self.assertEqual(kwargs["onboarding_status"], "verification_pending")
        self.assertEqual(kwargs["reason"], "checkpoint_required")

    def test_flag_on_login_failed_publishes_reauth_required(self) -> None:
        publisher = Mock(return_value={"published": True})

        with patch.dict(os.environ, {"INSTAGRAM_LOGIN_PROVISIONER_ENABLED": "on"}):
            provisioner.run_login_provisioning_check(
                account_id=ACCOUNT_ID,
                outcome=LoginProbeOutcome.LOGIN_FAILED,
                publisher=publisher,
            )

        kwargs = publisher.call_args.kwargs
        self.assertEqual(kwargs["login_status"], "failed")
        self.assertEqual(kwargs["provisioning_status"], "failed")
        self.assertEqual(kwargs["onboarding_status"], "blocked")
        self.assertTrue(kwargs["reauth_required"])
        self.assertEqual(kwargs["reauth_reason"], "credentials_invalid")

    def test_skipped_not_implemented_does_not_publish(self) -> None:
        publisher = Mock()
        classification = classify_login_probe_outcome(LoginProbeOutcome.SKIPPED_NOT_IMPLEMENTED)

        with patch.dict(os.environ, {"INSTAGRAM_LOGIN_PROVISIONER_ENABLED": "true"}):
            result = provisioner.publish_classified_login_status(
                account_id=ACCOUNT_ID,
                classification=classification,
                publisher=publisher,
            )

        self.assertFalse(result["published"])
        self.assertEqual(result["reason"], "probe_not_implemented")
        publisher.assert_not_called()

    def test_publisher_exception_is_fail_open(self) -> None:
        publisher = Mock(side_effect=RuntimeError("boom"))
        classification = classify_login_probe_outcome(LoginProbeOutcome.CONNECTED)

        with patch.dict(os.environ, {"INSTAGRAM_LOGIN_PROVISIONER_ENABLED": "true"}):
            result = provisioner.publish_classified_login_status(
                account_id=ACCOUNT_ID,
                classification=classification,
                publisher=publisher,
            )

        self.assertFalse(result["published"])
        self.assertEqual(result["reason"], "publisher_exception")

    def test_metadata_sent_contains_default_safe_fields(self) -> None:
        publisher = Mock(return_value={"published": True})

        with patch.dict(os.environ, {"INSTAGRAM_LOGIN_PROVISIONER_ENABLED": "true"}):
            provisioner.run_login_provisioning_check(
                account_id=ACCOUNT_ID,
                outcome=LoginProbeOutcome.CONNECTED,
                publisher=publisher,
            )

        metadata = publisher.call_args.kwargs["metadata"]
        self.assertEqual(metadata["source"], "provisioner")
        self.assertEqual(metadata["stage"], "login_probe")
        self.assertEqual(metadata["probe_version"], "v1")

    def test_metadata_sent_does_not_contain_forbidden_fields(self) -> None:
        publisher = Mock(return_value={"published": True})

        with patch.dict(os.environ, {"INSTAGRAM_LOGIN_PROVISIONER_ENABLED": "true"}):
            provisioner.run_login_provisioning_check(
                account_id=ACCOUNT_ID,
                outcome=LoginProbeOutcome.CONNECTED,
                metadata={
                    "password": "secret",
                    "secret_ref": "vault://x",
                    "vault": "raw",
                    "xml": "<xml />",
                    "screenshot": "base64",
                    "adb_serial": "serial",
                    "device_udid": "udid",
                    "safe": "ok",
                },
                publisher=publisher,
            )

        metadata = publisher.call_args.kwargs["metadata"]
        for key in (
            "password",
            "secret_ref",
            "vault",
            "xml",
            "screenshot",
            "adb_serial",
            "device_udid",
        ):
            self.assertNotIn(key, metadata)
        self.assertEqual(metadata["safe"], "ok")

    def test_injected_publisher_avoids_default_http_publisher(self) -> None:
        publisher = Mock(return_value={"published": True})

        with (
            patch.dict(os.environ, {"INSTAGRAM_LOGIN_PROVISIONER_ENABLED": "true"}),
            patch.object(provisioner, "_default_publisher") as default_publisher,
        ):
            provisioner.run_login_provisioning_check(
                account_id=ACCOUNT_ID,
                outcome=LoginProbeOutcome.CONNECTED,
                publisher=publisher,
            )

        publisher.assert_called_once()
        default_publisher.assert_not_called()

    def test_outcome_mock_does_not_probe_real_device(self) -> None:
        publisher = Mock(return_value={"published": True})

        with (
            patch.dict(os.environ, {"INSTAGRAM_LOGIN_PROVISIONER_ENABLED": "true"}),
            patch.object(provisioner, "probe_device_login") as probe_device_login,
        ):
            provisioner.run_login_provisioning_check(
                account_id=ACCOUNT_ID,
                outcome=LoginProbeOutcome.CONNECTED,
                publisher=publisher,
            )

        probe_device_login.assert_not_called()

    def test_flag_parsing_true_false_values(self) -> None:
        for value in ("1", "true", "TRUE", "yes", "on", "enabled"):
            with patch.dict(os.environ, {"INSTAGRAM_LOGIN_PROVISIONER_ENABLED": value}):
                self.assertTrue(provisioner.is_login_provisioner_enabled())

        for value in ("0", "false", "", "no", "off", "disabled"):
            with patch.dict(os.environ, {"INSTAGRAM_LOGIN_PROVISIONER_ENABLED": value}):
                self.assertFalse(provisioner.is_login_provisioner_enabled())

    def test_ui_probe_flag_off_does_not_dump_or_publish(self) -> None:
        device = Mock()
        publisher = Mock()

        with patch.dict(os.environ, {"INSTAGRAM_LOGIN_PROVISIONER_ENABLED": "false"}):
            result = provisioner.run_login_ui_probe_check(
                device,
                account_id=ACCOUNT_ID,
                publisher=publisher,
            )

        self.assertFalse(result["published"])
        self.assertEqual(result["reason"], "disabled")
        device.dump_hierarchy.assert_not_called()
        publisher.assert_not_called()

    def test_ui_probe_needs_2fa_publishes_needs_2fa(self) -> None:
        publisher = Mock(return_value={"published": True})
        device = _device_with_xml('<node text="Enter code" /><node text="authentication code" />')

        with patch.dict(os.environ, {"INSTAGRAM_LOGIN_PROVISIONER_ENABLED": "true"}):
            result = provisioner.run_login_ui_probe_check(
                device,
                account_id=ACCOUNT_ID,
                publisher=publisher,
            )

        self.assertEqual(result["classification"].login_status, "needs_2fa")
        kwargs = publisher.call_args.kwargs
        self.assertEqual(kwargs["login_status"], "needs_2fa")
        self.assertEqual(kwargs["provisioning_status"], "login_verification_pending")
        self.assertEqual(kwargs["onboarding_status"], "verification_pending")

    def test_ui_probe_checkpoint_publishes_checkpoint(self) -> None:
        publisher = Mock(return_value={"published": True})
        device = _device_with_xml('<node text="Suspicious login attempt" /><node text="Verify your account" />')

        with patch.dict(os.environ, {"INSTAGRAM_LOGIN_PROVISIONER_ENABLED": "true"}):
            provisioner.run_login_ui_probe_check(
                device,
                account_id=ACCOUNT_ID,
                publisher=publisher,
            )

        kwargs = publisher.call_args.kwargs
        self.assertEqual(kwargs["login_status"], "checkpoint")
        self.assertEqual(kwargs["reason"], "checkpoint_required")

    def test_ui_probe_login_failed_publishes_failed_reauth_required(self) -> None:
        publisher = Mock(return_value={"published": True})
        device = _device_with_xml('<node text="Sorry, your password was incorrect" />')

        with patch.dict(os.environ, {"INSTAGRAM_LOGIN_PROVISIONER_ENABLED": "true"}):
            provisioner.run_login_ui_probe_check(
                device,
                account_id=ACCOUNT_ID,
                publisher=publisher,
            )

        kwargs = publisher.call_args.kwargs
        self.assertEqual(kwargs["login_status"], "failed")
        self.assertTrue(kwargs["reauth_required"])
        self.assertEqual(kwargs["reauth_reason"], "credentials_invalid")

    def test_ui_probe_unknown_does_not_publish(self) -> None:
        publisher = Mock()
        device = _device_with_xml('<node text="Instagram" />')

        with patch.dict(os.environ, {"INSTAGRAM_LOGIN_PROVISIONER_ENABLED": "true"}):
            result = provisioner.run_login_ui_probe_check(
                device,
                account_id=ACCOUNT_ID,
                publisher=publisher,
            )

        self.assertEqual(result["classification"].reason, "unknown_login_probe_outcome")
        self.assertFalse(result["publish_result"]["published"])
        publisher.assert_not_called()

    def test_ui_probe_exception_is_fail_open(self) -> None:
        device = Mock()
        device.dump_hierarchy.side_effect = RuntimeError("dump failed")
        publisher = Mock()

        with patch.dict(os.environ, {"INSTAGRAM_LOGIN_PROVISIONER_ENABLED": "true"}):
            result = provisioner.run_login_ui_probe_check(
                device,
                account_id=ACCOUNT_ID,
                publisher=publisher,
            )

        self.assertEqual(result["classification"].reason, "unknown_login_probe_outcome")
        self.assertFalse(result["publish_result"]["published"])
        publisher.assert_not_called()

    def test_ui_probe_import_exception_is_fail_open(self) -> None:
        publisher = Mock()

        with (
            patch.dict(os.environ, {"INSTAGRAM_LOGIN_PROVISIONER_ENABLED": "true"}),
            patch("instagram_login_ui_probe.probe_instagram_login_ui", side_effect=RuntimeError("probe boom")),
        ):
            result = provisioner.run_login_ui_probe_check(
                Mock(),
                account_id=ACCOUNT_ID,
                publisher=publisher,
            )

        self.assertFalse(result["published"])
        self.assertEqual(result["reason"], "probe_exception")
        publisher.assert_not_called()

    def test_ui_probe_publisher_exception_is_fail_open(self) -> None:
        publisher = Mock(side_effect=RuntimeError("publish boom"))
        device = _device_with_xml(
            '<node content-desc="Home" /><node content-desc="Search" />'
            '<node content-desc="Reels" /><node content-desc="Profile" />'
        )

        with patch.dict(os.environ, {"INSTAGRAM_LOGIN_PROVISIONER_ENABLED": "true"}):
            result = provisioner.run_login_ui_probe_check(
                device,
                account_id=ACCOUNT_ID,
                publisher=publisher,
            )

        self.assertFalse(result["publish_result"]["published"])
        self.assertEqual(result["publish_result"]["reason"], "publisher_exception")

    def test_ui_probe_metadata_sent_is_safe(self) -> None:
        publisher = Mock(return_value={"published": True})
        device = _device_with_xml(
            '<node content-desc="Home" /><node content-desc="Search" />'
            '<node content-desc="Reels" /><node content-desc="Profile" />'
        )

        with patch.dict(os.environ, {"INSTAGRAM_LOGIN_PROVISIONER_ENABLED": "true"}):
            provisioner.run_login_ui_probe_check(
                device,
                account_id=ACCOUNT_ID,
                metadata={
                    "password": "secret",
                    "secret_ref": "vault://x",
                    "vault": "raw",
                    "xml": "<node />",
                    "screenshot": "base64",
                    "adb_serial": "serial",
                    "device_udid": "udid",
                    "safe": "ok",
                },
                publisher=publisher,
            )

        metadata = publisher.call_args.kwargs["metadata"]
        self.assertEqual(metadata["source"], "provisioner")
        self.assertEqual(metadata["stage"], "login_ui_probe")
        self.assertEqual(metadata["probe_version"], "v1")
        self.assertEqual(metadata["probe_type"], "login_ui")
        self.assertEqual(metadata["safe"], "ok")
        for key in (
            "password",
            "secret_ref",
            "vault",
            "xml",
            "screenshot",
            "adb_serial",
            "device_udid",
        ):
            self.assertNotIn(key, metadata)


def _device_with_xml(xml: str) -> Mock:
    device = Mock()
    device.dump_hierarchy.return_value = xml
    return device


if __name__ == "__main__":
    unittest.main()
