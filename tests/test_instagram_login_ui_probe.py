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
        self.assertTrue(signals["has_use_another_profile_button"])
        self.assertTrue(signals["has_create_new_account_button"])
        self.assertTrue(signals["continue_as_candidate"])

    def test_continue_as_i_m_your_traker_detected_with_realistic_xml_noise(self) -> None:
        xml = (
            '<hierarchy rotation="0">'
            '<node package="com.instagram.android" resource-id="android:id/status" text="1.0" />'
            '<node text="" content-desc="Instagram" />'
            '<node text="i_m_your_traker" resource-id="com.instagram.android:id/username" />'
            '<node text="Continue" clickable="true" />'
            '<node text="Use another profile" clickable="true" />'
            '<node text="Create new account" clickable="true" />'
            '<node text="Meta" />'
            '</hierarchy>'
        )

        signals = extract_login_screen_signals_from_hierarchy(xml)

        self.assertEqual(signals["screen_type"], "continue_as_candidate")
        self.assertEqual(signals["suggested_username"], "i_m_your_traker")
        self.assertTrue(signals["has_continue_button"])
        self.assertTrue(signals["has_use_another_profile_button"])
        self.assertTrue(signals["has_create_new_account_button"])
        rendered = str(signals)
        for forbidden in ("secret_ref", "vault", "<hierarchy", "screenshot", "emulator-5554"):
            self.assertNotIn(forbidden, rendered)

    def test_continue_as_random_old_profile_detected_generically(self) -> None:
        xml = (
            '<hierarchy rotation="0">'
            '<node text="" content-desc="Instagram" />'
            '<node text="random_old_profile" />'
            '<node text="Continue" clickable="true" />'
            '<node text="Use another profile" clickable="true" />'
            '<node text="Create new account" clickable="true" />'
            '<node text="Meta" />'
            '</hierarchy>'
        )

        signals = extract_login_screen_signals_from_hierarchy(xml)

        self.assertEqual(signals["screen_type"], "continue_as_candidate")
        self.assertTrue(signals["continue_as_candidate"])
        self.assertEqual(signals["suggested_username"], "random_old_profile")
        self.assertTrue(signals["has_continue_button"])
        self.assertTrue(signals["has_use_another_profile_button"])

    def test_account_picker_detects_multiple_usernames_generically(self) -> None:
        xml = (
            '<hierarchy rotation="0">'
            '<node text="" content-desc="Instagram" />'
            '<node text="random_expected" />'
            '<node text="random_old_profile" />'
            '<node text="Use another profile" />'
            '<node text="Create new account" />'
            '<node content-desc="Meta logo" />'
            '</hierarchy>'
        )

        signals = extract_login_screen_signals_from_hierarchy(xml, expected_username="random_expected")

        self.assertEqual(signals["screen_type"], "account_picker")
        self.assertTrue(signals["account_picker"])
        self.assertEqual(signals["available_usernames"], ["random_expected", "random_old_profile"])
        self.assertTrue(signals["expected_username_present"])
        self.assertEqual(signals["expected_username_match_count"], 1)
        self.assertTrue(signals["has_use_another_profile_button"])
        self.assertTrue(signals["has_create_new_account_button"])
        self.assertTrue(signals["meta_present"])

    def test_detects_active_account_home_markers(self) -> None:
        xml = (
            '<node text="Instagram" />'
            '<node text="Your story" />'
            '<node text="Suggested for you" />'
            '<node content-desc="Profile" />'
        )

        signals = extract_login_screen_signals_from_hierarchy(xml)

        self.assertEqual(signals["screen_type"], "active_account_home")
        self.assertTrue(signals["active_account_home"])

    def test_detects_active_account_profile_username(self) -> None:
        xml = (
            '<node text="random_old_profile" />'
            '<node text="Edit profile" />'
            '<node text="Share profile" />'
            '<node text="0 posts" />'
            '<node text="0 followers" />'
            '<node text="2 following" />'
        )

        signals = extract_login_screen_signals_from_hierarchy(xml, expected_username="random_expected")

        self.assertEqual(signals["screen_type"], "active_account_profile")
        self.assertTrue(signals["active_account_profile"])
        self.assertEqual(signals["actual_logged_in_username"], "random_old_profile")

    def test_detects_account_switcher_and_add_account_sheets(self) -> None:
        switcher_xml = (
            '<node text="random_old_profile" />'
            '<node text="Add Instagram account" />'
            '<node text="Go to Accounts Center" />'
        )
        add_xml = (
            '<node text="Add account" />'
            '<node text="Log into existing account" />'
            '<node text="Create new account" />'
        )

        switcher = extract_login_screen_signals_from_hierarchy(switcher_xml)
        add = extract_login_screen_signals_from_hierarchy(add_xml)

        self.assertEqual(switcher["screen_type"], "account_switcher_sheet")
        self.assertTrue(switcher["has_add_instagram_account_button"])
        self.assertEqual(add["screen_type"], "add_account_sheet")
        self.assertTrue(add["has_log_into_existing_account_button"])

    def test_extracts_login_form_empty_signals(self) -> None:
        xml = (
            '<node text="Username, email or mobile number" />'
            '<node text="Password" />'
            '<node text="Log in" />'
            '<node text="Forgot password?" />'
            '<node text="Create new account" />'
            '<node text="Meta" />'
        )

        signals = extract_login_screen_signals_from_hierarchy(xml)

        self.assertEqual(signals["screen_type"], "login_form_empty")
        self.assertTrue(signals["has_username_field"])
        self.assertTrue(signals["has_password_field"])
        self.assertTrue(signals["has_login_button"])
        self.assertTrue(signals["username_editable_present"])
        self.assertTrue(signals["password_field_editable_present"])
        self.assertTrue(signals["forgot_password_present"])
        self.assertTrue(signals["has_create_new_account_button"])
        self.assertTrue(signals["meta_present"])
        self.assertTrue(signals["password_required"])
        self.assertTrue(signals["ready_for_credentials_flow"])
        self.assertFalse(signals["continue_password_only"])

    def test_login_form_empty_with_secondary_signals_stays_login_form(self) -> None:
        xml = (
            '<node text="English (US)" />'
            '<node text="Username, email or mobile number" />'
            '<node text="Password" />'
            '<node text="Log in" />'
            '<node text="Forgot password?" />'
            '<node text="Create new account" />'
            '<node content-desc="Meta logo" />'
        )

        signals = extract_login_screen_signals_from_hierarchy(xml)

        self.assertEqual(signals["screen_type"], "login_form_empty")
        self.assertTrue(signals["ready_for_credentials_flow"])
        self.assertTrue(signals["forgot_password_present"])
        self.assertTrue(signals["meta_present"])

    def test_extracts_continue_password_only_signals(self) -> None:
        xml = (
            '<node text="random_expected" />'
            '<node text="Password" />'
            '<node text="Log in" />'
            '<node text="Forgot password?" />'
        )

        signals = extract_login_screen_signals_from_hierarchy(xml)

        self.assertEqual(signals["screen_type"], "continue_password_only")
        self.assertEqual(signals["suggested_username"], "random_expected")
        self.assertTrue(signals["continue_password_only"])
        self.assertTrue(signals["has_password_field"])
        self.assertTrue(signals["has_login_button"])
        self.assertFalse(signals["has_username_field"])
        self.assertTrue(signals["password_required"])
        self.assertTrue(signals["ready_for_password_submit"])
        self.assertFalse(signals["ready_for_credentials_flow"])

    def test_continue_password_only_tolerates_password_manager_overlay(self) -> None:
        xml = (
            '<node text="random_expected" />'
            '<node text="Password" />'
            '<node text="Suggest strong password" />'
            '<node text="And save to your Google account" />'
            '<node text="Log in" />'
        )

        signals = extract_login_screen_signals_from_hierarchy(xml)

        self.assertEqual(signals["screen_type"], "continue_password_only")
        self.assertTrue(signals["overlay_present"])
        self.assertEqual(signals["overlay_type"], "password_manager_or_autofill")
        self.assertFalse(signals["overlay_blocking_business"])
        self.assertTrue(signals["ready_for_password_submit"])

    def test_continue_password_only_tolerates_autofill_overlay_with_login_accessible(self) -> None:
        xml = (
            '<node text="random_expected" />'
            '<node text="Password" />'
            '<node text="Autofill" />'
            '<node text="Password manager" />'
            '<node text="Log in" />'
        )

        signals = extract_login_screen_signals_from_hierarchy(xml)

        self.assertEqual(signals["screen_type"], "continue_password_only")
        self.assertTrue(signals["overlay_present"])
        self.assertEqual(signals["overlay_type"], "password_manager_or_autofill")
        self.assertTrue(signals["ready_for_password_submit"])

    def test_loading_transition_is_unknown_with_transition_signal(self) -> None:
        signals = extract_login_screen_signals_from_hierarchy('<node text="Loading..." />')

        self.assertEqual(signals["screen_type"], "unknown")
        self.assertTrue(signals["transition_loading"])

    def test_extracts_unknown_for_ambiguous_signals(self) -> None:
        signals = extract_login_screen_signals_from_hierarchy('<node text="Instagram" />')

        self.assertEqual(signals["screen_type"], "unknown")
        self.assertEqual(signals["suggested_username"], "")


if __name__ == "__main__":
    unittest.main()
