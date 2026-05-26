from __future__ import annotations

import unittest

from instagram_login_status_classifier import LoginProbeOutcome
from instagram_login_ui_probe import (
    detect_login_probe_outcome_from_hierarchy,
    extract_login_screen_signals_from_hierarchy,
    probe_instagram_login_ui,
    probe_login_ui_from_hierarchy,
)


class FakeDevice:
    def __init__(self, hierarchy: str | None = None, exc: Exception | None = None) -> None:
        self.hierarchy = hierarchy
        self.exc = exc
        self.dump_calls = 0

    def dump_hierarchy(self, compressed: bool = False) -> str:
        self.dump_calls += 1
        if self.exc:
            raise self.exc
        return str(self.hierarchy or "")


class InstagramLoginUiProbeTest(unittest.TestCase):
    def test_detects_login_screen_as_logged_out(self) -> None:
        xml = '<node text="Log in to Instagram" /><node text="Username" /><node text="Password" />'

        result = probe_login_ui_from_hierarchy(xml)

        self.assertEqual(result.outcome, LoginProbeOutcome.LOGGED_OUT)
        self.assertEqual(result.reason, "login_screen_signal")

    def test_detects_needs_2fa(self) -> None:
        xml = '<node text="Enter code" /><node text="authentication code" />'

        outcome = detect_login_probe_outcome_from_hierarchy(xml)

        self.assertEqual(outcome, LoginProbeOutcome.NEEDS_2FA)

    def test_detects_checkpoint(self) -> None:
        xml = '<node text="Help us confirm it’s you" /><node text="Verify your account" />'

        outcome = detect_login_probe_outcome_from_hierarchy(xml)

        self.assertEqual(outcome, LoginProbeOutcome.CHECKPOINT)

    def test_detects_login_failed_wrong_password(self) -> None:
        xml = '<node text="Sorry, your password was incorrect. Please try again." />'

        outcome = detect_login_probe_outcome_from_hierarchy(xml)

        self.assertEqual(outcome, LoginProbeOutcome.LOGIN_FAILED)

    def test_detects_connected_with_sufficient_connected_signals(self) -> None:
        xml = (
            '<node content-desc="Home" />'
            '<node content-desc="Search" />'
            '<node content-desc="Reels" />'
            '<node content-desc="Profile" />'
        )

        result = probe_login_ui_from_hierarchy(xml)

        self.assertTrue(result.ok)
        self.assertEqual(result.outcome, LoginProbeOutcome.CONNECTED)
        self.assertEqual(result.reason, "connected_ui_signal")

    def test_empty_xml_is_unknown(self) -> None:
        result = probe_login_ui_from_hierarchy("")

        self.assertEqual(result.outcome, LoginProbeOutcome.UNKNOWN)
        self.assertEqual(result.reason, "empty_hierarchy")

    def test_ambiguous_xml_is_unknown(self) -> None:
        xml = '<node text="Instagram" /><node text="Search" />'

        result = probe_login_ui_from_hierarchy(xml)

        self.assertEqual(result.outcome, LoginProbeOutcome.UNKNOWN)
        self.assertEqual(result.reason, "ambiguous_or_unknown_ui")

    def test_dump_hierarchy_exception_returns_unknown_safe_error(self) -> None:
        device = FakeDevice(exc=RuntimeError("device down"))

        result = probe_instagram_login_ui(device, account_id="account-id")

        self.assertEqual(result.outcome, LoginProbeOutcome.UNKNOWN)
        self.assertEqual(result.reason, "dump_hierarchy_failed")
        self.assertEqual(result.error, "dump_hierarchy_failed")
        self.assertEqual(device.dump_calls, 1)

    def test_result_metadata_does_not_include_raw_xml(self) -> None:
        xml = '<node text="Log in to Instagram" /><node text="Username" /><node text="Password" />'

        result = probe_login_ui_from_hierarchy(xml)

        self.assertNotIn("xml", result.metadata)
        self.assertNotIn("hierarchy_xml", result.metadata)
        self.assertNotIn(xml, result.metadata.values())

    def test_result_metadata_does_not_include_screenshot_or_device_id(self) -> None:
        device = FakeDevice(
            '<node content-desc="Home" /><node content-desc="Search" />'
            '<node content-desc="Reels" /><node content-desc="Profile" />'
        )

        result = probe_instagram_login_ui(
            device,
            account_id="account-id",
            expected_username="cinema_catchup",
        )

        self.assertEqual(result.outcome, LoginProbeOutcome.CONNECTED)
        for key in ("screenshot", "screenshot_path", "adb_serial", "device_udid"):
            self.assertNotIn(key, result.metadata)
        self.assertTrue(result.metadata["expected_username_present"])

    def test_extracts_continue_as_candidate_signals(self) -> None:
        xml = (
            '<node text="Instagram" />'
            '<node text="i_m_your_traker" />'
            '<node text="Continue" />'
            '<node text="Use another profile" />'
            '<node text="Create new account" />'
        )

        signals = extract_login_screen_signals_from_hierarchy(xml)

        self.assertEqual(signals["screen_type"], "continue_as_candidate")
        self.assertEqual(signals["suggested_username"], "i_m_your_traker")
        self.assertTrue(signals["has_continue_button"])
        self.assertTrue(signals["has_use_another_profile"])

    def test_extracts_login_form_empty_signals(self) -> None:
        xml = (
            '<node text="Username, email or mobile number" />'
            '<node text="Password" />'
            '<node text="Log in" />'
            '<node text="Forgot password?" />'
        )

        signals = extract_login_screen_signals_from_hierarchy(xml)

        self.assertEqual(signals["screen_type"], "login_form_empty")
        self.assertTrue(signals["has_username_field"])
        self.assertTrue(signals["has_password_field"])
        self.assertTrue(signals["has_login_button"])

    def test_extracts_unknown_for_ambiguous_signals(self) -> None:
        signals = extract_login_screen_signals_from_hierarchy('<node text="Instagram" />')

        self.assertEqual(signals["screen_type"], "unknown")
        self.assertEqual(signals["suggested_username"], "")


if __name__ == "__main__":
    unittest.main()
