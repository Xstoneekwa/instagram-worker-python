from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import account_identity_guard as identity_guard
from instagram_post_verification_completion import (
    classify_post_verification_surface,
    prepare_post_verification_identity_surface,
)
from own_profile_navigation import (
    OwnProfileNavigationOutcome,
    classify_own_profile_destination,
    navigate_to_own_profile_from_bottom_nav,
    open_own_profile_from_bottom_nav,
)


PACKAGE = "com.instagram.androig"
HOME_XML = (
    '<hierarchy><node package="com.instagram.androig" resource-id="com.instagram.androig:id/tab_bar">'
    '<node package="com.instagram.androig" resource-id="com.instagram.androig:id/feed_tab" content-desc="Home" />'
    '<node package="com.instagram.androig" resource-id="com.instagram.androig:id/profile_tab" content-desc="Profile" />'
    "</node></hierarchy>"
)
MODERN_HOME_XML = (
    '<hierarchy><node package="com.instagram.androig" text="Instagram" />'
    '<node package="com.instagram.androig" text="Your story" />'
    '<node package="com.instagram.androig" text="Follow" />'
    "</hierarchy>"
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
    '<node package="com.instagram.androig" class="android.widget.Button" '
    'text="Next" clickable="true" enabled="true" bounds="[10,20][100,60]" />'
    '</hierarchy>'
)
SAVE_LOGIN_XML = (
    '<hierarchy><node package="com.instagram.androig" text="Save your login info?" />'
    '<node package="com.instagram.androig" class="android.widget.Button" '
    'content-desc="Not now" clickable="true" enabled="true" bounds="[10,20][100,60]">'
    '<node package="com.instagram.androig" class="android.view.View" '
    'text="Not now" clickable="false" enabled="true" bounds="[30,30][80,50]" />'
    '</node>'
    '<node package="com.instagram.androig" text="Save" /></hierarchy>'
)
SAVE_LOGIN_ORPHAN_XML = (
    '<hierarchy><node package="com.instagram.androig" text="Save your login info?" />'
    '<node package="com.instagram.androig" text="Not now" clickable="false" '
    'enabled="true" bounds="[30,30][80,50]" />'
    '<node package="com.instagram.androig" text="Save" /></hierarchy>'
)
SAVE_LOGIN_AMBIGUOUS_XML = (
    '<hierarchy><node package="com.instagram.androig" text="Save your login info?" />'
    '<node class="android.widget.Button" content-desc="Not now" clickable="true" '
    'enabled="true" bounds="[10,20][100,60]" />'
    '<node class="android.widget.Button" content-desc="Not now" clickable="true" '
    'enabled="true" bounds="[10,80][100,120]" />'
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
CONTACT_SYNC_PREPARATION_XML = (
    '<hierarchy><node package="com.instagram.androig" '
    'text="Next, you can allow access to your contacts to make it easier to find your friends" />'
    '<node package="com.instagram.androig" '
    'text="Your contacts will be periodically synced and stored securely" />'
    '<node package="com.instagram.androig" class="android.widget.Button" '
    'text="Next" clickable="true" enabled="true" bounds="[10,20][100,60]" />'
    '<node package="com.instagram.androig" '
    'text="By tapping Next, you can choose to sync your contacts or skip this step." />'
    '</hierarchy>'
)
ANDROID_CONTACT_PERMISSION_XML = (
    '<hierarchy><node package="com.google.android.permissioncontroller" '
    'text="Allow Instagram to access your contacts?" />'
    '<node class="android.widget.Button" text="While using the app" clickable="true" />'
    '<node class="android.widget.Button" text="Don\'t allow" clickable="true" /></hierarchy>'
)
UNKNOWN_XML = '<hierarchy><node package="com.instagram.androig" text="Unexpected surface" /></hierarchy>'


class FakeDevice:
    def __init__(
        self,
        hierarchies: list[str],
        *,
        current_package: str = PACKAGE,
        save_not_now_count: int | None = None,
    ) -> None:
        self.hierarchies = list(hierarchies)
        self.last = self.hierarchies[-1] if self.hierarchies else ""
        self.press_calls: list[str] = []
        self.current_package = current_package
        self.selector = Mock()
        self.selector.wait.return_value = True
        if save_not_now_count is None:
            save_not_now_count = 1 if any(item == SAVE_LOGIN_XML for item in self.hierarchies) else 0
        self.save_not_now_target = _ActionTarget("Not now", save_not_now_count)
        notification_count = 1 if any(item == NOTIFICATIONS_XML for item in self.hierarchies) else 0
        self.notification_target = _ActionTarget("Next", notification_count)
        contact_count = 1 if any(item == CONTACT_SYNC_PREPARATION_XML for item in self.hierarchies) else 0
        self.contact_sync_target = _ActionTarget("Next", contact_count)
        self.missing_target = _ActionTarget("", 0)

    def __call__(self, **kwargs):
        if (
            (kwargs.get("text") == "Not now" or kwargs.get("description") == "Not now")
            and kwargs.get("className") == "android.widget.Button"
            and kwargs.get("clickable") is True
            and kwargs.get("enabled") is True
        ):
            return self.save_not_now_target
        if (
            kwargs.get("text") == "Next"
            and kwargs.get("className") == "android.widget.Button"
            and kwargs.get("clickable") is True
            and kwargs.get("enabled") is True
        ):
            if "contacts" in self.last.casefold():
                return self.contact_sync_target
            return self.notification_target
        if "text" in kwargs or "description" in kwargs:
            return self.missing_target
        return self.selector

    def dump_hierarchy(self, compressed: bool = False) -> str:
        if self.hierarchies:
            self.last = self.hierarchies.pop(0)
        return self.last

    def press(self, key: str) -> None:
        self.press_calls.append(key)

    def app_current(self) -> dict[str, str]:
        return {"package": self.current_package}


class _ActionTarget:
    def __init__(self, text: str, count: int) -> None:
        self._count = count
        self.click_calls = 0
        self.info = {
            "text": text,
            "className": "android.widget.Button",
            "clickable": True,
            "enabled": True,
            "bounds": {"left": 10, "top": 20, "right": 100, "bottom": 60},
        }

    def count(self) -> int:
        return self._count

    def click(self) -> None:
        self.click_calls += 1


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
        self.assertEqual(device.notification_target.click_calls, 0)

    def test_save_login_info_is_recovered_once(self) -> None:
        device, result = self._recover(SAVE_LOGIN_XML)
        self.assertTrue(result.safe_for_identity_guard)
        self.assertEqual(device.press_calls, [])
        self.assertEqual(device.save_not_now_target.click_calls, 1)

    def test_save_login_info_zero_not_now_controls_fails_closed_without_back(self) -> None:
        device = FakeDevice([SAVE_LOGIN_ORPHAN_XML], save_not_now_count=0)
        result = prepare_post_verification_identity_surface(
            device,
            expected_package_name=PACKAGE,
            sleeper=lambda _: None,
        )
        self.assertFalse(result.safe_for_identity_guard)
        self.assertEqual(result.failure_reason, "not_now_button_not_found")
        self.assertEqual(device.press_calls, [])
        self.assertEqual(device.save_not_now_target.click_calls, 0)

    def test_save_login_info_ambiguous_not_now_controls_fails_closed(self) -> None:
        device = FakeDevice([SAVE_LOGIN_AMBIGUOUS_XML], save_not_now_count=2)
        result = prepare_post_verification_identity_surface(
            device,
            expected_package_name=PACKAGE,
            sleeper=lambda _: None,
        )
        self.assertFalse(result.safe_for_identity_guard)
        self.assertEqual(result.failure_reason, "ambiguous_not_now_control")
        self.assertEqual(result.metadata["matched_control_count"], 2)
        self.assertEqual(device.press_calls, [])
        self.assertEqual(device.save_not_now_target.click_calls, 0)

    def test_save_login_info_unchanged_after_not_now_fails_without_double_tap(self) -> None:
        device = FakeDevice([SAVE_LOGIN_XML, SAVE_LOGIN_XML])
        result = prepare_post_verification_identity_surface(
            device,
            expected_package_name=PACKAGE,
            sleeper=lambda _: None,
        )
        self.assertFalse(result.safe_for_identity_guard)
        self.assertEqual(result.failure_reason, "post_verification_surface_unchanged_after_not_now")
        self.assertEqual(device.save_not_now_target.click_calls, 1)
        self.assertEqual(device.press_calls, [])

    def test_save_info_then_notification_uses_not_now_then_one_back(self) -> None:
        device = FakeDevice(
            [SAVE_LOGIN_XML, NOTIFICATIONS_XML, NOTIFICATIONS_XML, HOME_XML, HOME_XML]
        )
        result = prepare_post_verification_identity_surface(
            device,
            expected_package_name=PACKAGE,
            sleeper=lambda _: None,
        )
        self.assertTrue(result.safe_for_identity_guard)
        self.assertEqual(device.press_calls, ["back"])
        self.assertEqual(device.notification_target.click_calls, 0)
        self.assertEqual(device.save_not_now_target.click_calls, 1)
        self.assertEqual(
            result.recovered_screen_types,
            ("save_login_info_prompt", "instagram_turn_on_notifications_prompt"),
        )

    def test_sync_contacts_is_recovered_once(self) -> None:
        device, result = self._recover(SYNC_CONTACTS_XML)
        self.assertEqual(result.recovered_screen_types, ("post_login_sync_contacts_prompt",))
        self.assertEqual(device.press_calls, ["back"])

    def test_discover_people_is_recovered_once(self) -> None:
        device, result = self._recover(DISCOVER_XML)
        self.assertEqual(result.recovered_screen_types, ("post_login_discover_people_prompt",))
        self.assertEqual(device.press_calls, ["back"])

    def test_contact_sync_preparation_is_classified_and_uses_next_once(self) -> None:
        screen, _ = classify_post_verification_surface(
            CONTACT_SYNC_PREPARATION_XML,
            expected_package_name=PACKAGE,
        )
        self.assertEqual(screen, "instagram_contact_sync_preparation_prompt")
        device = FakeDevice([CONTACT_SYNC_PREPARATION_XML, HOME_XML, HOME_XML])
        result = prepare_post_verification_identity_surface(
            device,
            expected_package_name=PACKAGE,
            sleeper=lambda _: None,
        )
        self.assertTrue(result.safe_for_identity_guard)
        self.assertEqual(device.contact_sync_target.click_calls, 1)
        self.assertEqual(device.press_calls, [])

    def test_contact_sync_preparation_requires_fingerprint_transition(self) -> None:
        device = FakeDevice([CONTACT_SYNC_PREPARATION_XML, CONTACT_SYNC_PREPARATION_XML])
        result = prepare_post_verification_identity_surface(
            device,
            expected_package_name=PACKAGE,
            sleeper=lambda _: None,
        )
        self.assertFalse(result.safe_for_identity_guard)
        self.assertEqual(
            result.failure_reason,
            "post_verification_surface_unchanged_after_contact_sync_next",
        )
        self.assertEqual(device.contact_sync_target.click_calls, 1)
        self.assertEqual(device.press_calls, [])

    def test_android_contacts_permission_is_not_misclassified_as_preparation(self) -> None:
        screen, _ = classify_post_verification_surface(
            ANDROID_CONTACT_PERMISSION_XML,
            expected_package_name=PACKAGE,
        )
        self.assertEqual(screen, "android_instagram_contacts_permission")

    def test_android_contacts_permission_uses_back_then_continues_to_identity_surface(self) -> None:
        device = FakeDevice([ANDROID_CONTACT_PERMISSION_XML, HOME_XML, HOME_XML])
        result = prepare_post_verification_identity_surface(
            device,
            expected_package_name=PACKAGE,
            sleeper=lambda _: None,
        )
        self.assertTrue(result.safe_for_identity_guard)
        self.assertEqual(device.press_calls, ["back"])
        self.assertEqual(
            result.recovered_screen_types,
            ("android_instagram_contacts_permission",),
        )

    def test_unchanged_android_contacts_permission_fails_after_one_back(self) -> None:
        device = FakeDevice([ANDROID_CONTACT_PERMISSION_XML, ANDROID_CONTACT_PERMISSION_XML])
        result = prepare_post_verification_identity_surface(
            device,
            expected_package_name=PACKAGE,
            sleeper=lambda _: None,
        )
        self.assertFalse(result.safe_for_identity_guard)
        self.assertEqual(result.failure_reason, "post_verification_surface_unchanged_after_back")
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
        self.assertEqual(device.notification_target.click_calls, 0)

    def test_unchanged_notification_surface_fails_without_back_or_double_tap(self) -> None:
        device = FakeDevice([NOTIFICATIONS_XML, NOTIFICATIONS_XML])
        result = prepare_post_verification_identity_surface(
            device,
            expected_package_name=PACKAGE,
            sleeper=lambda _: None,
        )
        self.assertFalse(result.safe_for_identity_guard)
        self.assertEqual(result.failure_reason, "post_verification_surface_unchanged_after_back")
        self.assertEqual(device.press_calls, ["back"])
        self.assertEqual(device.notification_target.click_calls, 0)

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

    def test_modern_home_without_legacy_tab_ids_enters_identity_handoff(self) -> None:
        screen, signals = classify_post_verification_surface(
            MODERN_HOME_XML,
            expected_package_name=PACKAGE,
        )
        self.assertEqual(screen, "active_account_home")
        self.assertEqual(
            signals["connected_surface_proof"],
            "instagram_authenticated_surface_classifier",
        )

    def test_modern_home_is_not_connected_without_exact_identity_guard(self) -> None:
        device = FakeDevice([MODERN_HOME_XML, MODERN_HOME_XML])
        with (
            patch.object(identity_guard.config, "INSTAGRAM_PACKAGE", PACKAGE),
            patch.object(identity_guard, "open_own_profile_from_bottom_nav", return_value=False),
        ):
            result = identity_guard.verify_active_instagram_account_matches_expected(
                device,
                expected_account_username="bmybusinesses",
                run_type="login_provisioning",
                stage="login_provisioning_post_login_identity",
            )
        self.assertFalse(result.ok)
        self.assertEqual(result.failure_reason, "own_profile_navigation_stayed_on_home")

    def test_modern_home_wrong_package_never_enters_identity_handoff(self) -> None:
        wrong = MODERN_HOME_XML.replace(PACKAGE, "com.instagram.android")
        result = prepare_post_verification_identity_surface(
            FakeDevice([wrong]),
            expected_package_name=PACKAGE,
            sleeper=lambda _: None,
        )
        self.assertFalse(result.safe_for_identity_guard)
        self.assertEqual(result.failure_reason, "post_verification_human_assistance_required")

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

    def test_simplified_save_notification_home_profile_identity_path(self) -> None:
        device = FakeDevice(
            [SAVE_LOGIN_XML, NOTIFICATIONS_XML, NOTIFICATIONS_XML, HOME_XML, HOME_XML, HOME_XML, PROFILE_XML]
        )
        with (
            patch.object(identity_guard.config, "INSTAGRAM_PACKAGE", PACKAGE),
            patch.object(identity_guard, "open_own_profile_from_bottom_nav", return_value=True),
        ):
            result = identity_guard.verify_active_instagram_account_matches_expected(
                device,
                expected_account_username="bmybusinesses",
                run_type="login_provisioning",
                stage="login_provisioning_post_login_identity",
            )
        self.assertTrue(result.ok)
        self.assertEqual(result.actual_logged_in_username, "bmybusinesses")
        self.assertEqual(device.save_not_now_target.click_calls, 1)
        self.assertEqual(device.notification_target.click_calls, 0)
        self.assertEqual(device.press_calls, ["back"])
        self.assertNotIn(
            "instagram_contact_sync_preparation_prompt",
            result.meta["post_verification_recovered_screen_types"],
        )

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
        device = FakeDevice([HOME_XML, PROFILE_XML], current_package="")
        with (
            patch("own_profile_navigation.config.INSTAGRAM_PACKAGE", PACKAGE),
            patch("own_profile_navigation.verify_app_foreground", return_value=False),
            patch("own_profile_navigation.time.sleep"),
        ):
            self.assertTrue(
                open_own_profile_from_bottom_nav(
                    device,
                    expected_username="bmybusinesses",
                )
            )
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

    def test_bound_clone_package_is_used_for_foreground_and_profile_resource_lookup(self) -> None:
        clone_package = "com.instagram.androig"
        device = FakeDevice([PROFILE_XML], current_package=clone_package)
        with (
            patch("own_profile_navigation.config.INSTAGRAM_PACKAGE", "com.instagram.android"),
            patch("own_profile_navigation.verify_app_foreground", return_value=True) as foreground,
            patch("own_profile_navigation.time.sleep"),
        ):
            self.assertTrue(
                open_own_profile_from_bottom_nav(
                    device,
                    expected_package_name=clone_package,
                    expected_username="bmybusinesses",
                )
            )
        foreground.assert_called_once_with(device, clone_package)
        self.assertEqual(device.selector.click.call_count, 1)

    def test_bound_clone_package_mismatch_fails_closed(self) -> None:
        clone_package = "com.instagram.androig"
        device = FakeDevice([HOME_XML], current_package="com.instagram.android")
        with (
            patch("own_profile_navigation.config.INSTAGRAM_PACKAGE", "com.instagram.android"),
            patch("own_profile_navigation.verify_app_foreground", return_value=False) as foreground,
            patch("own_profile_navigation.time.sleep"),
        ):
            self.assertFalse(
                open_own_profile_from_bottom_nav(
                    device,
                    expected_package_name=clone_package,
                )
            )
        foreground.assert_called_once_with(device, clone_package)
        self.assertEqual(device.selector.click.call_count, 0)

    def test_profile_tab_click_without_destination_proof_is_not_success(self) -> None:
        device = FakeDevice([HOME_XML])
        with (
            patch("own_profile_navigation.config.INSTAGRAM_PACKAGE", PACKAGE),
            patch("own_profile_navigation.verify_app_foreground", return_value=True),
            patch("own_profile_navigation.time.sleep"),
        ):
            result = navigate_to_own_profile_from_bottom_nav(
                device,
                expected_username="bmybusinesses",
            )
        self.assertFalse(result.ok)
        self.assertNotEqual(result.outcome, OwnProfileNavigationOutcome.PROFILE_OPENED)

    def test_loading_after_click_retries_once_and_requires_profile_proof(self) -> None:
        loading = '<hierarchy><node class="android.widget.ProgressBar" /></hierarchy>'
        device = FakeDevice([loading, PROFILE_XML])
        with (
            patch("own_profile_navigation.config.INSTAGRAM_PACKAGE", PACKAGE),
            patch("own_profile_navigation.verify_app_foreground", return_value=True),
            patch("own_profile_navigation.time.sleep") as sleep,
        ):
            result = navigate_to_own_profile_from_bottom_nav(
                device,
                expected_username="bmybusinesses",
            )
        self.assertTrue(result.ok)
        self.assertTrue(result.loading_retry_used)
        self.assertEqual(result.outcome, OwnProfileNavigationOutcome.PROFILE_OPENED)
        self.assertEqual(sleep.call_count, 2)

    def test_what_happened_and_restriction_surfaces_are_distinct_destinations(self) -> None:
        what_happened = '<hierarchy><node text="What happened" /><node class="android.widget.ProgressBar" /></hierarchy>'
        restriction = (
            '<hierarchy><node text="We added a restriction to your account" />'
            '<node text="You can\'t send messages" /></hierarchy>'
        )
        self.assertEqual(
            classify_own_profile_destination(what_happened)[0],
            OwnProfileNavigationOutcome.WHAT_HAPPENED_SCREEN,
        )
        self.assertEqual(
            classify_own_profile_destination(restriction)[0],
            OwnProfileNavigationOutcome.RESTRICTION_SCREEN,
        )

    def test_wrong_profile_title_never_proves_own_profile(self) -> None:
        wrong = PROFILE_XML.replace("bmybusinesses", "different_account")
        outcome, _ = classify_own_profile_destination(
            wrong,
            expected_username="bmybusinesses",
        )
        self.assertEqual(outcome, OwnProfileNavigationOutcome.UNKNOWN_SCREEN)


if __name__ == "__main__":
    unittest.main()
