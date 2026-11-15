from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import instagram_login_email_code_executor as email_code_executor
from instagram_credentials_runtime_access import SecretValue
from instagram_login_email_code_executor import execute_email_code_challenge_resume

EMAIL_CODE_CHALLENGE_XML = (
    '<node text="Check your email" />'
    '<node text="Enter the code we sent to m*******e@hotmail.com" />'
    '<node class="android.widget.EditText" text="Enter code" editable="true" />'
    '<node text="Continue" clickable="true" />'
    '<node text="Try another way" />'
)
EMAIL_CODE_FILLED_XML = (
    '<node text="Check your email" />'
    '<node text="Enter the code we sent to m*******e@hotmail.com" />'
    '<node class="android.widget.EditText" text="123456" editable="true" />'
    '<node text="Continue" clickable="true" />'
    '<node text="Try another way" />'
)
SAVE_LOGIN_INFO_PROMPT_XML = (
    '<node text="Save your login info?" />'
    '<node text="Save" clickable="true" />'
    '<node class="android.widget.Button" text="Not now" clickable="true" '
    'enabled="true" bounds="[10,20][100,60]" />'
)
CONNECTED_XML = (
    '<node content-desc="Home" />'
    '<node content-desc="Search" />'
    '<node content-desc="Reels" />'
    '<node content-desc="Profile" />'
)
POST_LOGIN_LOCATION_SERVICES_PROMPT_XML = (
    '<node text="Set up on new device" />'
    '<node text="To use Location services, allow Instagram to access your location" />'
    '<node text="How you can use location services" />'
    '<node text="Continue" clickable="true" />'
)


class FakeTarget:
    def __init__(self, text: str = "", *, set_text_updates: bool = True, exists: bool = True) -> None:
        self.text = text
        self.clicked = False
        self.value = ""
        self.set_text_updates = set_text_updates
        self._exists = exists

    def exists(self, timeout: float = 0) -> bool:
        return self._exists

    def click(self) -> None:
        self.clicked = True

    def clear_text(self) -> None:
        self.value = ""

    def set_text(self, value: str) -> None:
        self.value = value
        if self.set_text_updates:
            self.text = value

    @property
    def info(self) -> dict:
        return {
            "text": self.text,
            "className": "android.widget.Button",
            "bounds": {"left": 10, "top": 20, "right": 100, "bottom": 60},
        }


class FakeDevice:
    def __init__(self, hierarchies: list[str], *, set_text_updates: bool = True) -> None:
        self.hierarchies = list(hierarchies)
        self.serial = "RFGL145VCKE"
        self.code_target = FakeTarget("Enter code", set_text_updates=set_text_updates)
        self.continue_target = FakeTarget("Continue")
        self.not_now_target = FakeTarget("Not now")
        self.missing_target = FakeTarget(exists=False)
        self.press_calls: list[str] = []

    def dump_hierarchy(self, compressed: bool = False) -> str:
        return self.hierarchies.pop(0) if self.hierarchies else CONNECTED_XML

    def __call__(self, **selector: str) -> FakeTarget:
        if selector.get("text") == "Enter code" or selector.get("className") == "android.widget.EditText":
            return self.code_target
        if selector.get("text") == "Continue":
            return self.continue_target
        if selector.get("text") == "Not now":
            return self.not_now_target
        return self.missing_target

    def press(self, key: str) -> None:
        self.press_calls.append(key)


class EmailCodeExecutorTests(unittest.TestCase):
    def test_resume_requires_email_code_screen(self) -> None:
        device = FakeDevice(['<node text="Log in to Instagram" /><node text="Password" />'])
        result = execute_email_code_challenge_resume(
            device,
            verification_code=SecretValue("123456"),
            sleeper=Mock(),
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.failure_reason, "verification_code_challenge_screen_required")

    def test_resume_happy_path_connected(self) -> None:
        device = FakeDevice([EMAIL_CODE_CHALLENGE_XML, CONNECTED_XML])
        result = execute_email_code_challenge_resume(
            device,
            verification_code=SecretValue("123456"),
            post_submit_wait_ms=0,
            post_submit_observation_interval_ms=1,
            max_post_submit_observations=2,
            sleeper=Mock(),
        )
        self.assertTrue(result.ok)
        self.assertTrue(result.code_entered)
        self.assertTrue(result.continue_tapped)
        self.assertEqual(result.post_submit_outcome, "connected")
        rendered = str(result.safe_metadata)
        self.assertNotIn("123456", rendered)

    def test_resume_dismisses_save_login_info_prompt_with_not_now(self) -> None:
        device = FakeDevice([EMAIL_CODE_CHALLENGE_XML, EMAIL_CODE_FILLED_XML, SAVE_LOGIN_INFO_PROMPT_XML, CONNECTED_XML])

        result = execute_email_code_challenge_resume(
            device,
            verification_code=SecretValue("123456"),
            post_submit_wait_ms=0,
            post_submit_observation_interval_ms=1,
            max_post_submit_observations=3,
            sleeper=Mock(),
        )

        self.assertTrue(result.ok)
        self.assertTrue(device.not_now_target.clicked)
        self.assertTrue(result.safe_metadata["save_login_info_prompt_detected"])
        self.assertTrue(result.safe_metadata["save_login_info_not_now_tapped"])
        self.assertIn("instagram_save_login_info_prompt_not_now", result.warnings)

    def test_resume_dismisses_post_login_location_services_once_and_confirms_home(self) -> None:
        device = FakeDevice(
            [
                EMAIL_CODE_CHALLENGE_XML,
                EMAIL_CODE_FILLED_XML,
                POST_LOGIN_LOCATION_SERVICES_PROMPT_XML,
                CONNECTED_XML,
            ]
        )

        result = execute_email_code_challenge_resume(
            device,
            verification_code=SecretValue("123456"),
            post_submit_wait_ms=0,
            post_submit_observation_interval_ms=1,
            max_post_submit_observations=3,
            sleeper=Mock(),
        )

        self.assertTrue(result.ok)
        self.assertEqual(device.press_calls, ["back"])
        self.assertEqual(result.post_submit_outcome, "connected")
        self.assertTrue(result.safe_metadata["post_login_location_services_prompt_detected"])
        self.assertTrue(result.safe_metadata["post_login_location_services_prompt_dismissed"])
        self.assertEqual(result.safe_metadata["post_login_location_services_prompt_dismiss_method"], "back")
        self.assertEqual(result.safe_metadata["post_login_location_services_prompt_dismiss_attempt_count"], 1)
        self.assertIn("post_login_location_services_prompt_dismiss_back", result.warnings)

    def test_resume_uses_adb_keyboard_fallback_when_set_text_not_confirmed(self) -> None:
        device = FakeDevice(
            [EMAIL_CODE_CHALLENGE_XML, EMAIL_CODE_CHALLENGE_XML, EMAIL_CODE_FILLED_XML, CONNECTED_XML],
            set_text_updates=False,
        )

        with (
            patch.object(email_code_executor, "adb_available", return_value=True),
            patch.object(
                email_code_executor,
                "ensure_adb_keyboard_ready",
                return_value={"ok": True, "reason": "adb_keyboard_ready"},
            ) as keyboard_ready,
            patch.object(
                email_code_executor,
                "run_adb_keyboard_b64_input",
                return_value=(True, "adb_keyboard_b64", True, True),
            ) as adb_input,
        ):
            result = execute_email_code_challenge_resume(
                device,
                verification_code=SecretValue("123456"),
                post_submit_wait_ms=0,
                post_submit_observation_interval_ms=1,
                max_post_submit_observations=2,
                sleeper=Mock(),
            )

        self.assertTrue(result.ok)
        self.assertTrue(result.code_entered)
        self.assertTrue(result.continue_tapped)
        self.assertEqual(result.safe_metadata["code_input_method"], "adb_keyboard_b64")
        self.assertTrue(result.safe_metadata["code_input_confirmed"])
        self.assertIn("verification_code_set_text_not_confirmed", result.warnings)
        self.assertIn("verification_code_input_confirmed_after_fallback", result.warnings)
        keyboard_ready.assert_called_once_with(
            "RFGL145VCKE",
            fast_ime_id="com.android.adbkeyboard/.AdbIME",
        )
        adb_input.assert_called_once()

    def test_password_screen_ready_after_code_detects_continue_password_only(self) -> None:
        password_xml = (
            '<node text="cinema_catchup" />'
            '<node class="android.widget.EditText" text="Password" editable="true" />'
            '<node text="Log in" clickable="true" />'
        )
        device = FakeDevice([password_xml])
        self.assertTrue(email_code_executor._password_screen_ready_after_code(device))

    def test_resume_does_not_continue_when_code_input_not_confirmed(self) -> None:
        device = FakeDevice(
            [EMAIL_CODE_CHALLENGE_XML, EMAIL_CODE_CHALLENGE_XML, EMAIL_CODE_CHALLENGE_XML],
            set_text_updates=False,
        )

        with patch.object(
            email_code_executor,
            "ensure_adb_keyboard_ready",
            return_value={"ok": False, "reason": "adb_keyboard_ime_not_enabled"},
        ):
            result = execute_email_code_challenge_resume(
                device,
                verification_code=SecretValue("123456"),
                sleeper=Mock(),
            )

        self.assertFalse(result.ok)
        self.assertFalse(result.code_entered)
        self.assertFalse(result.continue_tapped)
        self.assertEqual(result.failure_reason, "adb_keyboard_unavailable")
        self.assertFalse(device.continue_target.clicked)
        rendered = str(result.safe_metadata)
        self.assertNotIn("123456", rendered)

    def test_resume_reports_adb_keyboard_unavailable_when_ime_missing(self) -> None:
        device = FakeDevice(
            [EMAIL_CODE_CHALLENGE_XML, EMAIL_CODE_CHALLENGE_XML, EMAIL_CODE_CHALLENGE_XML],
            set_text_updates=False,
        )
        with (
            patch.object(email_code_executor, "adb_available", return_value=True),
            patch.object(
                email_code_executor,
                "ensure_adb_keyboard_ready",
                return_value={"ok": False, "reason": "adb_keyboard_package_missing"},
            ),
        ):
            result = execute_email_code_challenge_resume(
                device,
                verification_code=SecretValue("123456"),
                sleeper=Mock(),
            )

        self.assertFalse(result.ok)
        self.assertEqual(result.failure_reason, "adb_keyboard_unavailable")
        self.assertIn("verification_code_adb_keyboard_package_missing", result.warnings)

    def test_resume_reports_adb_keyboard_broadcast_failure(self) -> None:
        device = FakeDevice(
            [EMAIL_CODE_CHALLENGE_XML, EMAIL_CODE_CHALLENGE_XML, EMAIL_CODE_CHALLENGE_XML],
            set_text_updates=False,
        )
        with (
            patch.object(email_code_executor, "adb_available", return_value=True),
            patch.object(
                email_code_executor,
                "ensure_adb_keyboard_ready",
                return_value={"ok": True, "reason": "adb_keyboard_ready"},
            ),
            patch.object(
                email_code_executor,
                "run_adb_keyboard_b64_input",
                return_value={
                    "command_ok": False,
                    "method": "adb_keyboard_b64",
                    "switch_ok": True,
                    "broadcast_ok": False,
                    "reason": "adb_keyboard_broadcast_failed",
                },
            ),
        ):
            result = execute_email_code_challenge_resume(
                device,
                verification_code=SecretValue("123456"),
                sleeper=Mock(),
            )

        self.assertFalse(result.ok)
        self.assertEqual(result.failure_reason, "adb_keyboard_broadcast_failed")
        self.assertIn("verification_code_adb_keyboard_broadcast_failed", result.warnings)


class PostCodeTransitionCompletionTests(unittest.TestCase):
    def observe(self, hierarchies, *, budget=4, expected_package="com.instagram.androig"):
        from tests.test_instagram_post_verification_completion import NavigationEducationDevice
        d = NavigationEducationDevice(hierarchies)
        waits = []; warnings = []; timings = {}
        r = email_code_executor._observe_post_submit_settled(
            d, timings=timings, warnings=warnings, timer=lambda: 0,
            sleeper=waits.append, initial_wait_ms=10000, interval_ms=1000, max_observations=budget,
            expected_package_name=expected_package,
        )
        return d, r, timings, waits, warnings

    def test_transient_code_save_not_now_education_home_uses_same_four_observations(self):
        from tests.test_instagram_post_verification_completion import SAVE_LOGIN_XML, HOME_XML, navigation_education_xml
        d, r, timing, waits, warnings = self.observe([EMAIL_CODE_CHALLENGE_XML, SAVE_LOGIN_XML, navigation_education_xml(), CONNECTED_XML])
        self.assertEqual(r['outcome'], 'connected')
        self.assertEqual(timing['post_submit_observation_count'], 4)
        self.assertEqual(waits, [10, 1, 1, 1])
        self.assertEqual(d.save_not_now_target.click_calls, 1)
        self.assertEqual(d.education_target.click_calls, 1)
        self.assertEqual(d.press_calls, [])
        self.assertIn('navigation_education_got_it_acknowledged', warnings)

    def test_still_required_uses_finite_budget_without_resend_or_click(self):
        d, r, timing, waits, _ = self.observe([EMAIL_CODE_CHALLENGE_XML] * 6)
        self.assertEqual(r['outcome'], 'verification_pending')
        self.assertEqual(timing['post_submit_observation_count'], 4)
        self.assertEqual(len(waits), 4)
        self.assertEqual(d.education_target.click_calls, 0)
        self.assertEqual(d.press_calls, [])

    def test_explicit_terminal_failure_does_not_consume_remaining_transition_budget(self):
        with patch('instagram_login_password_form_executor._classify_post_submit_hierarchy', return_value={'outcome':'login_failed','terminal':True,'reason':'verification_code_invalid','screen_type':'verification_code_invalid'}):
            _, r, timing, _, _ = self.observe([EMAIL_CODE_CHALLENGE_XML] * 4)
        self.assertEqual(r['outcome'], 'login_failed')
        self.assertEqual(timing['post_submit_observation_count'], 1)

    def test_expired_code_is_not_treated_as_in_flight_transition(self):
        with patch('instagram_login_password_form_executor._classify_post_submit_hierarchy', return_value={'outcome':'verification_pending','terminal':True,'verification_code_challenge_detected':True,'verification_code_expired':True}):
            _, _, timing, _, _ = self.observe([EMAIL_CODE_CHALLENGE_XML] * 4)
        self.assertEqual(timing['post_submit_observation_count'], 1)

    def test_repeated_education_modal_is_not_clicked_twice(self):
        from tests.test_instagram_post_verification_completion import navigation_education_xml
        d, r, _, _, _ = self.observe([navigation_education_xml()] * 4)
        self.assertEqual(d.education_target.click_calls, 1)
        self.assertEqual(r['reason'], 'navigation_education_surface_unchanged')
        self.assertNotEqual(r['outcome'], 'connected')

    def test_new_acknowledgement_refuses_another_clone_than_pre_code_package(self):
        from tests.test_instagram_post_verification_completion import navigation_education_xml
        d, r, _, _, _ = self.observe([navigation_education_xml()], expected_package="com.instagram.androie")
        self.assertEqual(d.education_target.click_calls, 0)
        self.assertEqual(r['reason'], 'navigation_education_surface_unproved')

    def test_new_acknowledgement_requires_pre_code_package_proof(self):
        from tests.test_instagram_post_verification_completion import navigation_education_xml
        d, r, _, _, _ = self.observe([navigation_education_xml()], expected_package="")
        self.assertEqual(d.education_target.click_calls, 0)
        self.assertEqual(r['reason'], 'navigation_education_surface_unproved')
