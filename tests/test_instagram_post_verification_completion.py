from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import account_identity_guard as identity_guard
from instagram_post_verification_completion import (
    classify_post_verification_surface,
    prepare_post_verification_identity_surface,
)
from own_profile_navigation import open_own_profile_from_bottom_nav


PACKAGE = "com.instagram.androig"
HOME_XML = (
    '<hierarchy><node package="com.instagram.androig" resource-id="com.instagram.androig:id/tab_bar">'
    '<node package="com.instagram.androig" resource-id="com.instagram.androig:id/feed_tab" content-desc="Home" />'
    '<node package="com.instagram.androig" resource-id="com.instagram.androig:id/profile_tab" content-desc="Profile" />'
    "</node></hierarchy>"
)
PROFILE_XML = (
    '<hierarchy><node package="com.instagram.androig" text="bmybusinesses" '
    'resource-id="com.instagram.androig:id/action_bar_title" /></hierarchy>'
)
LOCATION_XML = (
    '<hierarchy><node package="com.instagram.androig" text="Set up on new device" />'
    '<node package="com.instagram.androig" text="To use Location services, allow Instagram to access your location" />'
    '<node package="com.instagram.androig" text="How you can use location services" />'
    '<node package="com.instagram.androig" text="How we\'ll use this information" />'
    '<node package="com.instagram.androig" text="Continue" /></hierarchy>'
)
NOTIFICATIONS_XML = (
    '<hierarchy><node package="com.instagram.androig" text="Turn on notifications" />'
    '<node package="com.instagram.androig" text="Find out right away when people follow you" />'
    '<node package="com.instagram.androig" text="like and comment on your posts" />'
    '<node package="com.instagram.androig" text="Skip" /></hierarchy>'
)
SAVE_LOGIN_XML = (
    '<hierarchy><node package="com.instagram.androig" text="Save your login info?" />'
    '<node package="com.instagram.androig" text="Not now" />'
    '<node package="com.instagram.androig" text="Save" /></hierarchy>'
)
SYNC_CONTACTS_XML = (
    '<hierarchy><node package="com.instagram.androig" text="Sync contacts" />'
    '<node package="com.instagram.androig" text="Connect contacts to find people" />'
    '<node package="com.instagram.androig" text="Not now" /></hierarchy>'
)
DISCOVER_XML = (
    '<hierarchy><node package="com.instagram.androig" text="Discover people" />'
    '<node package="com.instagram.androig" text="Find people to follow" />'
    '<node package="com.instagram.androig" text="Get started" /></hierarchy>'
)
UNKNOWN_XML = '<hierarchy><node package="com.instagram.androig" text="Unexpected surface" /></hierarchy>'


class FakeDevice:
    def __init__(self, hierarchies: list[str], *, current_package: str = PACKAGE) -> None:
        self.hierarchies = list(hierarchies)
        self.last = self.hierarchies[-1] if self.hierarchies else ""
        self.press_calls: list[str] = []
        self.current_package = current_package
        self.selector = Mock()
        self.selector.wait.return_value = True

    def __call__(self, **_kwargs):
        return self.selector

    def dump_hierarchy(self, compressed: bool = False) -> str:
        if self.hierarchies:
            self.last = self.hierarchies.pop(0)
        return self.last

    def press(self, key: str) -> None:
        self.press_calls.append(key)

    def app_current(self) -> dict[str, str]:
        return {"package": self.current_package}


class PostVerificationCompletionTests(unittest.TestCase):
    def _recover(self, first: str):
        device = FakeDevice([first, HOME_XML, HOME_XML])
        result = prepare_post_verification_identity_surface(
            device,
            expected_package_name=PACKAGE,
            sleeper=lambda _: None,
        )
        return device, result

    def test_email_post_login_uses_channel_neutral_location_recovery(self) -> None:
        device, result = self._recover(LOCATION_XML)
        self.assertTrue(result.safe_for_identity_guard)
        self.assertEqual(device.press_calls, ["back"])

    def test_sms_post_login_uses_same_channel_neutral_recovery(self) -> None:
        device, result = self._recover(LOCATION_XML)
        self.assertEqual(result.recovered_screen_types, ("connected_post_login_location_services_prompt",))
        self.assertEqual(device.press_calls, ["back"])

    def test_whatsapp_post_login_uses_same_channel_neutral_recovery(self) -> None:
        _, result = self._recover(LOCATION_XML)
        self.assertEqual(result.screen_type, "active_account_home")

    def test_authenticator_post_login_uses_same_channel_neutral_recovery(self) -> None:
        _, result = self._recover(LOCATION_XML)
        self.assertTrue(result.fingerprint_changed)

    def test_setup_new_device_location_screen_is_recognized(self) -> None:
        screen, _ = classify_post_verification_surface(LOCATION_XML, expected_package_name=PACKAGE)
        self.assertEqual(screen, "connected_post_login_location_services_prompt")

    def test_notification_onboarding_is_recovered_once(self) -> None:
        device, result = self._recover(NOTIFICATIONS_XML)
        self.assertTrue(result.safe_for_identity_guard)
        self.assertEqual(device.press_calls, ["back"])

    def test_save_login_info_is_recovered_once(self) -> None:
        device, result = self._recover(SAVE_LOGIN_XML)
        self.assertTrue(result.safe_for_identity_guard)
        self.assertEqual(device.press_calls, ["back"])

    def test_sync_contacts_is_recovered_once(self) -> None:
        device, result = self._recover(SYNC_CONTACTS_XML)
        self.assertEqual(result.recovered_screen_types, ("post_login_sync_contacts_prompt",))
        self.assertEqual(device.press_calls, ["back"])

    def test_discover_people_is_recovered_once(self) -> None:
        device, result = self._recover(DISCOVER_XML)
        self.assertEqual(result.recovered_screen_types, ("post_login_discover_people_prompt",))
        self.assertEqual(device.press_calls, ["back"])

    def test_two_successive_known_setup_surfaces_are_recovered_with_fresh_observations(self) -> None:
        device = FakeDevice([LOCATION_XML, NOTIFICATIONS_XML, NOTIFICATIONS_XML, HOME_XML, HOME_XML])
        result = prepare_post_verification_identity_surface(
            device,
            expected_package_name=PACKAGE,
            sleeper=lambda _: None,
        )
        self.assertTrue(result.safe_for_identity_guard)
        self.assertEqual(
            result.recovered_screen_types,
            (
                "connected_post_login_location_services_prompt",
                "instagram_turn_on_notifications_prompt",
            ),
        )
        self.assertEqual(device.press_calls, ["back", "back"])

    def test_unchanged_surface_never_receives_second_blind_back(self) -> None:
        device = FakeDevice([LOCATION_XML, LOCATION_XML])
        result = prepare_post_verification_identity_surface(device, expected_package_name=PACKAGE, sleeper=lambda _: None)
        self.assertFalse(result.safe_for_identity_guard)
        self.assertEqual(result.failure_reason, "post_verification_surface_unchanged_after_back")
        self.assertEqual(device.press_calls, ["back"])

    def test_unknown_surface_requires_human_assistance_without_back(self) -> None:
        device = FakeDevice([UNKNOWN_XML])
        result = prepare_post_verification_identity_surface(device, expected_package_name=PACKAGE, sleeper=lambda _: None)
        self.assertFalse(result.safe_for_identity_guard)
        self.assertEqual(result.failure_reason, "post_verification_human_assistance_required")
        self.assertEqual(device.press_calls, [])

    def test_code_acceptance_does_not_equal_connected_without_identity(self) -> None:
        device = FakeDevice([HOME_XML, HOME_XML])
        with (
            patch.object(identity_guard.config, "INSTAGRAM_PACKAGE", PACKAGE),
            patch.object(identity_guard, "open_own_profile_from_bottom_nav", return_value=False),
        ):
            result = identity_guard.verify_active_instagram_account_matches_expected(
                device,
                expected_account_username="bmybusinesses",
                run_type="login_email_code_resume",
                stage="login_provisioning_post_login_identity",
            )
        self.assertFalse(result.ok)
        self.assertEqual(result.failure_reason, "own_profile_open_failed")

    def test_location_recovery_still_requires_exact_identity_guard(self) -> None:
        device = FakeDevice([LOCATION_XML, HOME_XML, HOME_XML, HOME_XML, PROFILE_XML])
        with (
            patch.object(identity_guard.config, "INSTAGRAM_PACKAGE", PACKAGE),
            patch.object(identity_guard, "open_own_profile_from_bottom_nav", return_value=True),
        ):
            result = identity_guard.verify_active_instagram_account_matches_expected(
                device,
                expected_account_username="bmybusinesses",
                run_type="login_email_code_resume",
                stage="login_provisioning_post_login_identity",
            )
        self.assertTrue(result.ok)
        self.assertEqual(result.actual_logged_in_username, "bmybusinesses")
        self.assertEqual(device.press_calls, ["back"])

    def test_location_recovery_wrong_account_fails_closed(self) -> None:
        wrong_profile = PROFILE_XML.replace("bmybusinesses", "another_account")
        device = FakeDevice([LOCATION_XML, HOME_XML, HOME_XML, HOME_XML, wrong_profile])
        with (
            patch.object(identity_guard.config, "INSTAGRAM_PACKAGE", PACKAGE),
            patch.object(identity_guard, "open_own_profile_from_bottom_nav", return_value=True),
        ):
            result = identity_guard.verify_active_instagram_account_matches_expected(
                device,
                expected_account_username="bmybusinesses",
                run_type="login_sms_code_resume",
                stage="login_provisioning_post_login_identity",
            )
        self.assertFalse(result.ok)
        self.assertEqual(result.failure_reason, "active_instagram_account_mismatch")

    def test_home_xml_proves_foreground_when_app_current_is_temporarily_blank(self) -> None:
        device = FakeDevice([HOME_XML], current_package="")
        with (
            patch("own_profile_navigation.config.INSTAGRAM_PACKAGE", PACKAGE),
            patch("own_profile_navigation.verify_app_foreground", return_value=False),
            patch("own_profile_navigation.time.sleep"),
        ):
            self.assertTrue(open_own_profile_from_bottom_nav(device))
        self.assertEqual(device.selector.click.call_count, 1)

    def test_nonempty_foreground_package_mismatch_never_uses_hierarchy_fallback(self) -> None:
        device = FakeDevice([HOME_XML], current_package="com.example.other")
        with (
            patch("own_profile_navigation.config.INSTAGRAM_PACKAGE", PACKAGE),
            patch("own_profile_navigation.verify_app_foreground", return_value=False),
            patch("own_profile_navigation.time.sleep"),
        ):
            self.assertFalse(open_own_profile_from_bottom_nav(device))
        self.assertEqual(device.selector.click.call_count, 0)


if __name__ == "__main__":
    unittest.main()
