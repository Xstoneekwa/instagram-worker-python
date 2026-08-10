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

    def test_detects_join_instagram_landing(self) -> None:
        xml = (
            '<node text="Join Instagram" />'
            '<node text="Share what you&apos;re into with the people who get you." />'
            '<node text="Get started" clickable="true" />'
            '<node text="I already have a profile" clickable="true" />'
            '<node text="Meta" />'
        )

        result = probe_login_ui_from_hierarchy(xml)
        signals = extract_login_screen_signals_from_hierarchy(xml)

        self.assertEqual(result.outcome, LoginProbeOutcome.UNKNOWN)
        self.assertEqual(result.reason, "join_instagram_landing_detected")
        self.assertEqual(result.metadata["screen_type"], "join_instagram_landing")
        self.assertEqual(signals["screen_type"], "join_instagram_landing")
        self.assertTrue(signals["join_instagram_landing_detected"])
        self.assertTrue(signals["has_already_have_profile_button"])
        self.assertTrue(signals["has_get_started_button"])

    def test_detects_needs_2fa(self) -> None:
        xml = '<node text="Enter code" /><node text="authentication code" />'

        outcome = detect_login_probe_outcome_from_hierarchy(xml)

        self.assertEqual(outcome, LoginProbeOutcome.NEEDS_2FA)

    def test_detects_email_code_challenge_as_verification_pending(self) -> None:
        xml = (
            '<node text="Check your email" />'
            '<node text="Enter the code we sent to m*******e@hotmail.com" />'
            '<node class="android.widget.EditText" text="Enter code" editable="true" />'
            '<node text="Get a new code" />'
            '<node text="Continue" />'
            '<node text="Try another way" />'
        )

        result = probe_login_ui_from_hierarchy(xml, stage="post_submit")
        signals = extract_login_screen_signals_from_hierarchy(xml)

        self.assertEqual(result.outcome, LoginProbeOutcome.VERIFICATION_PENDING)
        self.assertEqual(result.reason, "verification_code_required")
        self.assertEqual(result.metadata["screen_type"], "email_code_challenge")
        self.assertEqual(result.metadata["challenge_type"], "email")
        self.assertTrue(result.metadata["masked_email_present"])
        self.assertEqual(signals["screen_type"], "email_code_challenge")
        self.assertTrue(signals["email_code_challenge_present"])

    def test_detects_email_code_challenge_with_resend_and_masked_email_without_full_header(self) -> None:
        xml = (
            '<node text="Enter the code we sent to m*******e@hotmail.com" />'
            '<node class="android.widget.EditText" text="Enter code" editable="true" />'
            '<node text="Get a new code" />'
            '<node text="Continue" />'
        )

        result = probe_login_ui_from_hierarchy(xml, stage="post_submit")

        self.assertEqual(result.outcome, LoginProbeOutcome.VERIFICATION_PENDING)
        self.assertEqual(result.reason, "verification_code_required")
        self.assertEqual(result.metadata["screen_type"], "email_code_challenge")
        self.assertEqual(result.metadata["challenge_type"], "email")
        self.assertTrue(result.metadata["masked_email_present"])

    def test_detects_french_email_code_challenge(self) -> None:
        xml = (
            '<node text="Vérifiez votre e-mail" />'
            '<node text="Entrez le code que nous avons envoyé à m*******e@hotmail.com" />'
            '<node class="android.widget.EditText" text="Entrez le code" editable="true" />'
            '<node text="Recevoir un nouveau code" />'
            '<node text="Essayer une autre méthode" />'
        )

        result = probe_login_ui_from_hierarchy(xml, stage="post_submit")

        self.assertEqual(result.outcome, LoginProbeOutcome.VERIFICATION_PENDING)
        self.assertEqual(result.reason, "verification_code_required")
        self.assertEqual(result.metadata["screen_type"], "email_code_challenge")
        self.assertEqual(result.metadata["challenge_type"], "email")

    def test_detects_unsupported_post_submit_challenge(self) -> None:
        xml = (
            '<node text="Was this you?" />'
            '<node text="Try another way" />'
            '<node text="Approve this login" />'
        )
        result = probe_login_ui_from_hierarchy(xml)
        self.assertEqual(result.outcome, LoginProbeOutcome.UNSUPPORTED_POST_SUBMIT_CHALLENGE)
        self.assertEqual(result.reason, "unsupported_post_submit_challenge")
        self.assertTrue(result.metadata["human_review_required"])

    def test_detects_checkpoint(self) -> None:
        xml = '<node text="Help us confirm it’s you" /><node text="Verify your account" />'

        outcome = detect_login_probe_outcome_from_hierarchy(xml)

        self.assertEqual(outcome, LoginProbeOutcome.CHECKPOINT)

    def test_detects_login_failed_wrong_password(self) -> None:
        xml = '<node text="Sorry, your password was incorrect. Please try again." />'

        outcome = detect_login_probe_outcome_from_hierarchy(xml)

        self.assertEqual(outcome, LoginProbeOutcome.LOGIN_FAILED)

    def test_detects_password_required_dialog_signals(self) -> None:
        xml = (
            '<node text="Password required" />'
            '<node text="Enter your password to continue." />'
            '<node text="OK" />'
        )

        result = probe_login_ui_from_hierarchy(xml)
        signals = extract_login_screen_signals_from_hierarchy(xml)

        self.assertEqual(result.outcome, LoginProbeOutcome.UNKNOWN)
        self.assertEqual(result.reason, "password_required_dialog")
        self.assertTrue(result.metadata["password_required_dialog_present"])
        self.assertEqual(signals["screen_type"], "password_required_dialog")
        self.assertTrue(signals["password_required_dialog_present"])
        self.assertTrue(signals["has_ok_button"])

    def test_detects_google_password_manager_save_prompt(self) -> None:
        xml = (
            '<node text="Google Password Manager" />'
            '<node text="Save password for Instagram?" />'
            '<node text="cinema_catchup" />'
            '<node text="••••••••••" />'
            '<node text="Continue" clickable="true" />'
        )

        result = probe_login_ui_from_hierarchy(xml)
        signals = extract_login_screen_signals_from_hierarchy(xml, expected_username="cinema_catchup")

        self.assertEqual(result.outcome, LoginProbeOutcome.UNKNOWN)
        self.assertEqual(result.reason, "google_password_manager_save_prompt")
        self.assertTrue(result.metadata["save_password_prompt_present"])
        self.assertEqual(signals["screen_type"], "google_password_manager_save_prompt")
        self.assertTrue(signals["save_password_prompt_present"])
        self.assertTrue(signals["google_password_manager_save_prompt"])
        self.assertTrue(signals["save_password_prompt"])

    def test_detects_samsung_pass_save_password_prompt(self) -> None:
        xml = (
            '<node text="Samsung Pass" />'
            '<node text="Save password for Instagram?" />'
            '<node text="cinema_catchup" />'
            '<node text="••••••••••" />'
            '<node text="Cancel" clickable="true" />'
            '<node text="Save" clickable="true" />'
        )

        result = probe_login_ui_from_hierarchy(xml)
        signals = extract_login_screen_signals_from_hierarchy(xml, expected_username="cinema_catchup")

        self.assertEqual(result.outcome, LoginProbeOutcome.UNKNOWN)
        self.assertEqual(result.reason, "samsung_pass_save_password_prompt")
        self.assertTrue(result.metadata["save_password_prompt_present"])
        self.assertTrue(result.metadata["samsung_pass_save_password_prompt_present"])
        self.assertEqual(signals["screen_type"], "samsung_pass_save_password_prompt")
        self.assertTrue(signals["save_password_prompt"])
        self.assertTrue(signals["samsung_pass_save_password_prompt"])

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

    def test_detects_post_login_location_services_prompt_as_connected(self) -> None:
        xml = (
            '<node text="Set up on new device" />'
            '<node text="To use Location services, allow Instagram to access your location" />'
            '<node text="How you can use location services" />'
            '<node text="How we&apos;ll use this information" />'
            '<node text="How you can control this" />'
            '<node text="Continue" clickable="true" />'
        )

        result = probe_login_ui_from_hierarchy(xml, stage="post_submit")
        signals = extract_login_screen_signals_from_hierarchy(xml)

        self.assertTrue(result.ok)
        self.assertEqual(result.outcome, LoginProbeOutcome.CONNECTED)
        self.assertEqual(result.reason, "connected_post_login_location_services_prompt")
        self.assertEqual(result.metadata["screen_type"], "connected_post_login_location_services_prompt")
        self.assertEqual(signals["screen_type"], "connected_post_login_location_services_prompt")
        self.assertTrue(signals["post_login_location_services_prompt"])
        self.assertTrue(signals["connected_post_login_setup"])

    def test_detects_instagram_turn_on_notifications_prompt_as_connected(self) -> None:
        xml = (
            '<node text="Turn on notifications" />'
            '<node text="Find out right away when people follow you or like and comment on your posts." />'
            '<node text="Next" clickable="true" />'
        )

        result = probe_login_ui_from_hierarchy(xml, stage="post_submit")
        signals = extract_login_screen_signals_from_hierarchy(xml)

        self.assertTrue(result.ok)
        self.assertEqual(result.outcome, LoginProbeOutcome.CONNECTED)
        self.assertEqual(result.reason, "instagram_turn_on_notifications_prompt")
        self.assertEqual(result.metadata["screen_type"], "instagram_turn_on_notifications_prompt")
        self.assertTrue(signals["instagram_turn_on_notifications_prompt"])
        self.assertTrue(signals["notifications_prompt_detected"])
        self.assertTrue(signals["has_notifications_next_button"])
        self.assertFalse(signals["has_notifications_skip_button"])
        self.assertTrue(signals["connected_post_login_setup"])

    def test_detects_instagram_turn_on_notifications_prompt_with_skip_as_connected(self) -> None:
        xml = (
            '<node text="Turn on notifications" />'
            '<node text="Find out right away when people follow you or like and comment on your posts." />'
            '<node text="Next" clickable="true" />'
            '<node text="Skip" clickable="true" />'
        )

        result = probe_login_ui_from_hierarchy(xml, stage="post_submit")
        signals = extract_login_screen_signals_from_hierarchy(xml)

        self.assertTrue(result.ok)
        self.assertEqual(result.outcome, LoginProbeOutcome.CONNECTED)
        self.assertTrue(signals["has_notifications_skip_button"])
        self.assertTrue(signals["has_notifications_next_button"])

    def test_detects_android_instagram_notification_settings_as_connected(self) -> None:
        xml = (
            '<node text="Instagram" />'
            '<node text="Allow notifications" />'
            '<node text="All notifications from this app are blocked." />'
        )

        result = probe_login_ui_from_hierarchy(xml, stage="post_submit")
        signals = extract_login_screen_signals_from_hierarchy(xml)

        self.assertTrue(result.ok)
        self.assertEqual(result.outcome, LoginProbeOutcome.CONNECTED)
        self.assertEqual(result.reason, "android_instagram_notification_settings")
        self.assertEqual(result.metadata["screen_type"], "android_instagram_notification_settings")
        self.assertTrue(signals["android_instagram_notification_settings"])
        self.assertTrue(signals["android_notification_settings_detected"])
        self.assertTrue(signals["connected_post_login_setup"])

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

    def test_continue_as_wins_over_active_home_when_both_markers_present(self) -> None:
        xml = (
            '<node text="Instagram" />'
            '<node text="Your story" />'
            '<node text="Suggested for you" />'
            '<node text="random_old_profile" />'
            '<node text="Continue" clickable="true" />'
            '<node text="Use another profile" />'
            '<node text="Create new account" />'
        )

        signals = extract_login_screen_signals_from_hierarchy(xml, expected_username="random_expected")

        self.assertEqual(signals["screen_type"], "continue_as_candidate")
        self.assertTrue(signals["continue_as_candidate"])

    def test_detects_active_account_profile_username(self) -> None:
        xml = (
            '<node text="random_old_profile" />'
            '<node text="Edit profile" />'
            '<node text="Share profile" />'
            '<node text="0 posts" />'
            '<node text="0 followers" />'
            '<node text="2 following" />'
            '<node resource-id="com.instagram.android:id/action_bar_button_action" clickable="true" bounds="[930,150][1020,240]" />'
        )

        signals = extract_login_screen_signals_from_hierarchy(xml, expected_username="random_expected")

        self.assertEqual(signals["screen_type"], "active_account_profile")
        self.assertTrue(signals["active_account_profile"])
        self.assertEqual(signals["actual_logged_in_username"], "random_old_profile")
        self.assertTrue(signals["profile_menu_ready"])

    def test_profile_username_is_not_replaced_by_discover_people_suggestion(self) -> None:
        xml = (
            '<node text="studio.cmb74" resource-id="com.instagram.androif:id/suggestion_username" '
            'bounds="[40,1100][420,1180]" />'
            '<node text="j_automatise_pour_toi" resource-id="com.instagram.androif:id/action_bar_title" '
            'bounds="[40,120][620,220]" />'
            '<node text="Edit profile" bounds="[40,600][500,700]" />'
            '<node text="Share profile" bounds="[520,600][1020,700]" />'
            '<node text="1 posts" />'
            '<node text="54 followers" />'
            '<node text="77 following" />'
        )

        signals = extract_login_screen_signals_from_hierarchy(
            xml,
            expected_username="lorielebras_autom",
        )

        self.assertEqual(signals["screen_type"], "active_account_profile")
        self.assertEqual(signals["actual_logged_in_username"], "j_automatise_pour_toi")
        self.assertEqual(signals["suggested_username"], "studio.cmb74")

    def test_profile_suggestion_alone_is_not_reported_as_active_username(self) -> None:
        xml = (
            '<node text="studio.cmb74" resource-id="com.instagram.androif:id/suggestion_username" '
            'bounds="[40,1100][420,1180]" />'
            '<node text="Edit profile" bounds="[40,600][500,700]" />'
            '<node text="Share profile" bounds="[520,600][1020,700]" />'
        )

        signals = extract_login_screen_signals_from_hierarchy(
            xml,
            expected_username="lorielebras_autom",
        )

        self.assertEqual(signals["actual_logged_in_username"], "")
        self.assertEqual(signals["suggested_username"], "studio.cmb74")

    def test_active_account_profile_marks_menu_missing_transient(self) -> None:
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
        self.assertTrue(signals["profile_menu_missing_transient"])

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

    def test_detects_logout_fallback_screens(self) -> None:
        settings_xml = (
            '<node text="Settings and activity" />'
            '<node text="More info and support" />'
            '<node text="Login" />'
            '<node text="Add account" />'
            '<node text="Log out" />'
        )
        save_xml = (
            '<node text="Save your login info?" />'
            '<node text="Save" />'
            '<node text="Not now" />'
        )
        confirm_xml = (
            '<node text="Log out of your account?" />'
            '<node text="Cancel" />'
            '<node text="Log out" />'
        )

        settings = extract_login_screen_signals_from_hierarchy(settings_xml)
        save = extract_login_screen_signals_from_hierarchy(save_xml)
        confirm = extract_login_screen_signals_from_hierarchy(confirm_xml)

        self.assertEqual(settings["screen_type"], "settings_and_activity")
        self.assertTrue(settings["has_log_out_button"])
        self.assertEqual(save["screen_type"], "save_login_info_prompt")
        self.assertTrue(save["has_not_now_button"])
        self.assertEqual(confirm["screen_type"], "logout_confirmation_prompt")
        self.assertTrue(confirm["has_cancel_button"])

    def test_extracts_login_form_empty_signals(self) -> None:
        xml = (
            '<node class="android.widget.EditText" text="Username, email or mobile number" editable="true" />'
            '<node class="android.widget.EditText" text="Password" password="true" editable="true" />'
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
            '<node class="android.widget.EditText" text="Username, email or mobile number" editable="true" />'
            '<node class="android.widget.EditText" text="Password" password="true" editable="true" />'
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

    def test_login_form_empty_variant_a_without_instagram_logo(self) -> None:
        """Samsung clone layout: fields high on screen, no Instagram branding node."""
        xml = (
            '<node text="English (US)" />'
            '<node class="android.widget.EditText" text="Username, email or mobile number" editable="true" />'
            '<node class="android.widget.EditText" text="Password" editable="true" />'
            '<node text="Log in" clickable="true" />'
            '<node text="Forgot password?" />'
            '<node text="Create new account" clickable="true" />'
            '<node text="Meta" />'
        )

        signals = extract_login_screen_signals_from_hierarchy(xml)

        self.assertEqual(signals["screen_type"], "login_form_empty")
        self.assertTrue(signals["has_username_field"])
        self.assertTrue(signals["has_password_field"])
        self.assertTrue(signals["has_login_button"])
        self.assertTrue(signals["has_create_new_account_button"])
        self.assertTrue(signals["ready_for_credentials_flow"])
        self.assertFalse(signals["active_account_home"])

    def test_login_form_empty_variant_b_with_instagram_logo(self) -> None:
        """Primary-style layout: Instagram logo present; logo is not required for classification."""
        xml = (
            '<node text="English (US)" />'
            '<node content-desc="Instagram" />'
            '<node text="Instagram" />'
            '<node class="android.widget.EditText" text="Username, email or mobile number" editable="true" />'
            '<node class="android.widget.EditText" text="Password" editable="true" />'
            '<node text="Log in" clickable="true" />'
            '<node text="Forgot password?" />'
            '<node text="Create new account" clickable="true" />'
            '<node text="Meta" />'
        )

        signals = extract_login_screen_signals_from_hierarchy(xml)

        self.assertEqual(signals["screen_type"], "login_form_empty")
        self.assertTrue(signals["ready_for_credentials_flow"])
        self.assertFalse(signals["continue_password_only"])

    def test_extracts_login_form_prefilled_username_signals(self) -> None:
        xml = (
            '<node class="android.widget.EditText" text="i_m_your_traker" editable="true" />'
            '<node class="android.widget.EditText" text="Password" editable="true" />'
            '<node text="Log in" />'
            '<node text="Create new account" />'
            '<node text="Meta" />'
        )

        signals = extract_login_screen_signals_from_hierarchy(xml, expected_username="cinema_catchup")

        self.assertEqual(signals["screen_type"], "login_form_prefilled_username")
        self.assertTrue(signals["username_field_present"])
        self.assertTrue(signals["username_field_editable_present"])
        self.assertTrue(signals["username_prefilled_present"])
        self.assertEqual(signals["prefilled_username"], "i_m_your_traker")
        self.assertTrue(signals["password_field_present"])
        self.assertTrue(signals["login_button_present"])
        self.assertTrue(signals["ready_for_credentials_flow"])

    def test_extracts_continue_password_only_signals(self) -> None:
        xml = (
            '<node text="random_expected" />'
            '<node class="android.widget.EditText" text="Password" password="true" editable="true" />'
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
            '<node class="android.widget.EditText" text="Password" password="true" editable="true" />'
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
            '<node class="android.widget.EditText" text="Password" password="true" editable="true" />'
            '<node text="Autofill" />'
            '<node text="Password manager" />'
            '<node text="Log in" />'
        )

        signals = extract_login_screen_signals_from_hierarchy(xml)

        self.assertEqual(signals["screen_type"], "continue_password_only")
        self.assertTrue(signals["overlay_present"])
        self.assertEqual(signals["overlay_type"], "password_manager_or_autofill")
        self.assertTrue(signals["ready_for_password_submit"])

    def test_forgot_password_link_does_not_prove_secret_input(self) -> None:
        xml = (
            '<node class="android.widget.EditText" text="Username, email or mobile number" editable="true" />'
            '<node text="Forgot password?" clickable="true" />'
            '<node text="Log in" clickable="true" />'
        )

        signals = extract_login_screen_signals_from_hierarchy(xml)

        self.assertEqual(signals["screen_type"], "login_form_username_step")
        self.assertTrue(signals["has_username_field"])
        self.assertFalse(signals["has_password_field"])
        self.assertEqual(signals["password_field_candidate_count"], 0)
        self.assertTrue(signals["ready_for_username_step"])

    def test_password_resource_id_proves_secret_input_without_visible_label(self) -> None:
        xml = (
            '<node class="android.widget.EditText" resource-id="com.instagram.android:id/login_username" editable="true" />'
            '<node class="android.widget.EditText" resource-id="com.instagram.android:id/login_password" editable="true" />'
            '<node text="Log in" clickable="true" />'
        )

        signals = extract_login_screen_signals_from_hierarchy(xml)

        self.assertEqual(signals["screen_type"], "login_form_empty")
        self.assertTrue(signals["has_password_field"])
        self.assertEqual(signals["password_field_proof"], "resource_id")

    def test_localized_password_property_proves_secret_input(self) -> None:
        xml = (
            '<node class="android.widget.EditText" text="Nom d’utilisateur" editable="true" />'
            '<node class="android.widget.EditText" text="Mot de passe" password="true" editable="true" />'
            '<node text="Log in" clickable="true" />'
        )

        signals = extract_login_screen_signals_from_hierarchy(xml)

        self.assertEqual(signals["screen_type"], "login_form_empty")
        self.assertEqual(signals["password_field_proof"], "android_password_property")

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
