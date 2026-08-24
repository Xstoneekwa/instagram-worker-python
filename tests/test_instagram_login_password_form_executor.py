from __future__ import annotations

import json
import unittest
from dataclasses import asdict
from unittest.mock import Mock
from unittest.mock import patch

from instagram_credentials_runtime_access import SecretValue
import instagram_login_password_form_executor as password_executor
from instagram_login_password_form_executor import (
    advance_login_username_step,
    execute_login_form_credentials,
)


USERNAME = "cinema_catchup"
PASSWORD = "fake-password-for-unit-tests"
LOGIN_FORM_SIGNALS = {
    "screen_type": "login_form_empty",
    "has_username_field": True,
    "has_password_field": True,
    "has_login_button": True,
    "password_field_proof": "localized_accessibility_label",
}
PASSWORD_ONLY_SIGNALS = {
    "screen_type": "continue_password_only",
    "suggested_username": "cinema_catchup",
    "has_username_field": False,
    "has_password_field": True,
    "has_login_button": True,
    "password_field_proof": "localized_accessibility_label",
}
PREFILLED_USERNAME_SIGNALS = {
    "screen_type": "login_form_prefilled_username",
    "has_username_field": True,
    "username_field_present": True,
    "username_field_editable_present": True,
    "username_prefilled_present": True,
    "prefilled_username": "i_m_your_traker",
    "has_password_field": True,
    "has_login_button": True,
    "password_field_proof": "localized_accessibility_label",
}

USERNAME_STEP_SIGNALS = {
    "screen_type": "login_form_username_step",
    "has_username_field": True,
    "has_password_field": False,
    "has_login_button": True,
}
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
    '<node text="How we&apos;ll use this information" />'
    '<node text="How you can control this" />'
    '<node text="Continue" clickable="true" />'
)
INSTAGRAM_TURN_ON_NOTIFICATIONS_NEXT_ONLY_XML = (
    '<node text="Turn on notifications" />'
    '<node text="Find out right away when people follow you or like and comment on your posts." />'
    '<node text="Next" clickable="true" />'
)
INSTAGRAM_TURN_ON_NOTIFICATIONS_SKIP_XML = (
    '<node text="Turn on notifications" />'
    '<node text="Find out right away when people follow you or like and comment on your posts." />'
    '<node text="Next" clickable="true" />'
    '<node text="Skip" clickable="true" />'
)
ANDROID_INSTAGRAM_NOTIFICATION_SETTINGS_XML = (
    '<node text="Instagram" />'
    '<node text="Allow notifications" clickable="true" />'
    '<node text="All notifications from this app are blocked." />'
)
NEEDS_2FA_XML = '<node text="Enter code" /><node text="authentication code" />'
CHECKPOINT_XML = '<node text="Help us confirm it’s you" /><node text="Verify your account" />'
LOGIN_FAILED_XML = '<node text="Sorry, your password was incorrect. Please try again." />'
LOGGED_OUT_XML = '<node text="Log in to Instagram" /><node text="Username" /><node text="Password" />'
LOADING_XML = '<node text="Loading..." />'
EMAIL_CODE_CHALLENGE_XML = (
    '<node text="Check your email" />'
    '<node text="Enter the code we sent to m*******e@hotmail.com" />'
    '<node class="android.widget.EditText" text="Enter code" editable="true" />'
    '<node text="Get a new code" />'
    '<node text="Continue" />'
    '<node text="Try another way" />'
)
SENSITIVE_XML = '<node text="password secret_ref Vault token emulator-5554 screenshot" />'
PASSWORD_REQUIRED_XML = (
    '<node text="Password required" />'
    '<node text="Enter your password to continue." />'
    '<node text="OK" />'
)
GOOGLE_SAVE_PASSWORD_PROMPT_XML = (
    '<node text="Google Password Manager" />'
    '<node text="Save password for Instagram?" />'
    '<node text="cinema_catchup" />'
    '<node text="••••••••••" />'
    '<node text="Continue" clickable="true" />'
)
SAMSUNG_PASS_SAVE_PASSWORD_PROMPT_XML = (
    '<node text="Samsung Pass" />'
    '<node text="Save password for Instagram?" />'
    '<node text="cinema_catchup" />'
    '<node text="••••••••••" />'
    '<node text="Cancel" clickable="true" />'
    '<node text="Save" clickable="true" />'
)
INSTAGRAM_SAVE_LOGIN_INFO_PROMPT_XML = (
    '<node text="Save your login info?" />'
    '<node text="Save" clickable="true" />'
    '<node text="Not now" clickable="true" />'
)
FILLED_LOGIN_FORM_XML = (
    '<node class="android.widget.EditText" text="cinema_catchup" editable="true" />'
    '<node class="android.widget.EditText" text="••••••••" password="true" editable="true" />'
    '<node text="Log in" clickable="true" enabled="true" />'
)


class TrackingSecretValue(SecretValue):
    def __init__(self, value: str) -> None:
        super().__init__(value)
        self.reveal_calls = 0
        self.str_calls = 0
        self.repr_calls = 0

    def reveal_for_login_executor(self) -> str:
        self.reveal_calls += 1
        return super().reveal_for_login_executor()

    def __str__(self) -> str:
        self.str_calls += 1
        raise AssertionError("SecretValue.__str__ must not be used for password input")

    def __repr__(self) -> str:
        self.repr_calls += 1
        raise AssertionError("SecretValue.__repr__ must not be used for password input")


class FakeSelector:
    def __init__(self, count: int = 0, *, click_exc: Exception | None = None, set_exc: Exception | None = None) -> None:
        self._count = count
        self.click_exc = click_exc
        self.set_exc = set_exc
        self.click_calls = 0
        self.clear_calls = 0
        self.set_text_calls: list[str] = []
        self.click_failures_remaining = 0
        self.info: dict | None = None

    def count(self) -> int:
        return self._count

    def click(self) -> None:
        self.click_calls += 1
        if self.click_failures_remaining > 0:
            self.click_failures_remaining -= 1
            raise RuntimeError("temporary click block")
        if self.click_exc:
            raise self.click_exc

    def clear_text(self) -> None:
        self.clear_calls += 1

    def set_text(self, value: str) -> None:
        self.set_text_calls.append(value)
        if self.set_exc:
            raise self.set_exc


class MultiSelector(FakeSelector):
    def __init__(self, selectors: list[FakeSelector]) -> None:
        super().__init__(len(selectors))
        self._selectors = selectors

    def all(self) -> list[FakeSelector]:
        return list(self._selectors)


class CountOnlySelector(FakeSelector):
    """uiautomator2 selector variant with count()/instance lookup but no all()."""

    pass


class TrackingTextSelector(FakeSelector):
    def __init__(
        self,
        count: int = 1,
        *,
        text: str,
        clear_fails: bool = False,
        clear_keeps_text: bool = False,
        set_exc: Exception | None = None,
    ) -> None:
        super().__init__(count, set_exc=set_exc)
        self.info = {"text": text}
        self.clear_fails = clear_fails
        self.clear_keeps_text = clear_keeps_text

    def clear_text(self) -> None:
        self.clear_calls += 1
        if self.clear_fails:
            raise RuntimeError("clear failed")
        if not self.clear_keeps_text:
            self.info["text"] = ""

    def set_text(self, value: str) -> None:
        super().set_text(value)
        if not self.set_exc:
            self.info["text"] = value


class EditTextUsernameSelector(TrackingTextSelector):
    pass


class SetTextOnlyUsernameSelector(FakeSelector):
    def __init__(self, *, text: str) -> None:
        super().__init__(1)
        self.info = {"text": text, "className": "android.widget.EditText"}

    def set_text(self, value: str) -> None:
        super().set_text(value)
        if not self.set_exc:
            self.info["text"] = value


class PlaceholderStickyUsernameSelector(FakeSelector):
    """Accessibility readback keeps the hint even after set_text on empty login forms."""

    def __init__(self) -> None:
        super().__init__(1)
        self.info = {
            "text": "Username, email or mobile number",
            "className": "android.widget.EditText",
        }

    def clear_text(self) -> None:
        self.clear_calls += 1

    def set_text(self, value: str) -> None:
        super().set_text(value)
        if not self.set_exc:
            self.info["text"] = "Username, email or mobile number"


class PlaceholderStickyPasswordSelector(FakeSelector):
    """Samsung clone readback: set_text runs but the Password placeholder stays visible."""

    def __init__(self) -> None:
        super().__init__(1)
        self.info = {"text": "Password", "className": "android.widget.EditText"}

    def clear_text(self) -> None:
        self.clear_calls += 1

    def set_text(self, value: str) -> None:
        super().set_text(value)
        if not self.set_exc:
            self.info["text"] = "Password"


class FakeDevice:
    def __init__(self, hierarchy: str = CONNECTED_XML, *, dump_exc: Exception | None = None) -> None:
        self.hierarchy = hierarchy
        self.dump_exc = dump_exc
        self.dump_calls = 0
        self.selector_calls: list[dict] = []
        self.selectors: dict[tuple[str, str], FakeSelector] = {}
        self.press_calls: list[str] = []
        self.hierarchies: list[str] | None = None
        self.serial: str | None = None
        self.login_submit_hierarchy = FILLED_LOGIN_FORM_XML

    def add_selector(self, key: str, value: str, selector: FakeSelector) -> FakeSelector:
        self.selectors[(key, value)] = selector
        return selector

    def __call__(self, **kwargs):
        self.selector_calls.append(dict(kwargs))
        if "className" in kwargs and "instance" in kwargs:
            return self.selectors.get(
                ("className_instance", f"{kwargs['className']}:{kwargs['instance']}"),
                FakeSelector(0),
            )
        key, value = next(iter(kwargs.items()))
        return self.selectors.get((key, value), FakeSelector(0))

    def dump_hierarchy(self, compressed: bool = False) -> str:
        self.dump_calls += 1
        if self.dump_exc:
            raise self.dump_exc
        if self.hierarchies is not None:
            if len(self.hierarchies) == 1:
                return self.hierarchies[0]
            return self.hierarchies.pop(0)
        return self.hierarchy

    def dump_login_submit_hierarchy(self) -> str:
        return self.login_submit_hierarchy

    def press(self, key: str) -> None:
        self.press_calls.append(str(key))


class RefreshingLoginButtonDevice(FakeDevice):
    def __init__(self, *, stale: FakeSelector, fresh: FakeSelector) -> None:
        super().__init__(FILLED_LOGIN_FORM_XML)
        self.stale = stale
        self.fresh = fresh
        self.login_lookup_count = 0

    def __call__(self, **kwargs):
        if kwargs == {"text": "Log in"}:
            self.login_lookup_count += 1
            return self.stale if self.login_lookup_count == 1 else self.fresh
        return super().__call__(**kwargs)


class SequencedLoginButtonDevice(FakeDevice):
    def __init__(self, buttons: list[FakeSelector], hierarchies: list[str]) -> None:
        super().__init__(FILLED_LOGIN_FORM_XML)
        self.buttons = list(buttons)
        self.login_lookup_count = 0
        self.hierarchies = list(hierarchies)

    def __call__(self, **kwargs):
        if kwargs == {"text": "Log in"}:
            index = min(self.login_lookup_count, len(self.buttons) - 1)
            self.login_lookup_count += 1
            return self.buttons[index]
        return super().__call__(**kwargs)


def configured_device(hierarchy: str = CONNECTED_XML) -> tuple[FakeDevice, FakeSelector, FakeSelector, FakeSelector]:
    device = FakeDevice(hierarchy)
    device.hierarchies = [hierarchy]
    username = device.add_selector("text", "Username, email or mobile number", FakeSelector(1))
    password = device.add_selector("text", "Password", FakeSelector(1))
    login = device.add_selector("text", "Log in", FakeSelector(1))
    return device, username, password, login


class InstagramLoginPasswordFormExecutorTest(unittest.TestCase):
    def setUp(self) -> None:
        self._ensure_adb_keyboard_ready_patch = patch.object(
            password_executor,
            "ensure_adb_keyboard_ready",
            return_value={"ok": True, "reason": "adb_keyboard_ready"},
        )
        self._ensure_adb_keyboard_ready_patch.start()

    def tearDown(self) -> None:
        self._ensure_adb_keyboard_ready_patch.stop()

    def test_username_only_step_advances_without_reading_or_injecting_secret(self) -> None:
        device = FakeDevice()
        username = device.add_selector(
            "text",
            "Username, email or mobile number",
            TrackingTextSelector(text="Username, email or mobile number"),
        )
        login = device.add_selector("text", "Log in", FakeSelector(1))
        device.hierarchies = [
            '<node text="cinema_catchup" />'
            '<node class="android.widget.EditText" text="Password" password="true" editable="true" />'
            '<node text="Log in" clickable="true" />'
        ]

        result = advance_login_username_step(
            device,
            expected_username=USERNAME,
            prevalidated_signals=USERNAME_STEP_SIGNALS,
            observation_interval_ms=0,
            sleeper=Mock(),
        )

        self.assertTrue(result.ok)
        self.assertTrue(result.executed)
        self.assertEqual(username.set_text_calls, [USERNAME])
        self.assertEqual(login.click_calls, 1)
        self.assertEqual(result.post_action_signals["screen_type"], "continue_password_only")
        self.assertFalse(result.safe_metadata["secret_read"])
        self.assertFalse(result.safe_metadata["credential_input_attempted"])

    def test_username_only_step_is_bounded_when_transition_never_arrives(self) -> None:
        device = FakeDevice()
        device.add_selector(
            "text",
            "Username, email or mobile number",
            TrackingTextSelector(text="Username, email or mobile number"),
        )
        device.add_selector("text", "Log in", FakeSelector(1))
        username_only_xml = (
            '<node class="android.widget.EditText" text="Username, email or mobile number" editable="true" />'
            '<node text="Forgot password?" />'
            '<node text="Log in" clickable="true" />'
        )
        device.hierarchies = [username_only_xml, username_only_xml, username_only_xml]

        result = advance_login_username_step(
            device,
            expected_username=USERNAME,
            prevalidated_signals=USERNAME_STEP_SIGNALS,
            max_observations=3,
            observation_interval_ms=0,
            sleeper=Mock(),
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.failure_reason, "username_step_transition_not_reached")
        self.assertEqual(len(result.observations), 3)
        self.assertFalse(result.safe_metadata["secret_read"])

    def test_unproven_single_edittext_is_never_used_for_secret_input(self) -> None:
        device = FakeDevice()
        unproven = device.add_selector(
            "className",
            "android.widget.EditText",
            FakeSelector(1),
        )
        device.add_selector("text", "Log in", FakeSelector(1))
        secret = TrackingSecretValue(PASSWORD)

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=secret,
            prevalidated_signals={
                **PASSWORD_ONLY_SIGNALS,
                "password_field_proof": "",
            },
            sleeper=Mock(),
        )

        self.assertEqual(result.failure_reason, "password_field_not_found")
        self.assertFalse(result.executed)
        self.assertEqual(secret.reveal_calls, 0)
        self.assertEqual(unproven.set_text_calls, [])

    def test_valid_login_form_enters_username_password_and_taps_login(self) -> None:
        device, username, password_selector, login = configured_device()
        secret = TrackingSecretValue(PASSWORD)
        sleeper = Mock()

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=secret,
            prevalidated_signals=LOGIN_FORM_SIGNALS,
            sleeper=sleeper,
        )

        self.assertTrue(result.ok)
        self.assertTrue(result.executed)
        self.assertEqual(result.action, "login_form_submit")
        self.assertEqual(username.set_text_calls, [USERNAME])
        self.assertEqual(password_selector.set_text_calls, [PASSWORD])
        self.assertEqual(login.click_calls, 1)
        self.assertTrue(result.username_entered)
        self.assertTrue(result.password_entered)
        self.assertTrue(result.submit_tapped)
        self.assertGreaterEqual(result.safe_metadata["username_input_ms"], 0)
        sleeper.assert_any_call(1.0)

    def test_login_form_empty_placeholder_readback_is_ignored_and_password_submits(self) -> None:
        device = FakeDevice(CONNECTED_XML)
        username = device.add_selector(
            "className",
            "android.widget.EditText",
            PlaceholderStickyUsernameSelector(),
        )
        password_selector = device.add_selector("text", "Password", FakeSelector(1))
        login = device.add_selector("text", "Log in", FakeSelector(1))
        secret = TrackingSecretValue(PASSWORD)

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=secret,
            prevalidated_signals=LOGIN_FORM_SIGNALS,
            sleeper=Mock(),
        )

        self.assertTrue(result.executed)
        self.assertEqual(result.failure_reason, None)
        self.assertEqual(username.set_text_calls, [USERNAME])
        self.assertEqual(secret.reveal_calls, 1)
        self.assertEqual(password_selector.set_text_calls, [PASSWORD])
        self.assertEqual(login.click_calls, 1)
        self.assertFalse(result.safe_metadata["username_replaced"])
        self.assertEqual(result.safe_metadata["username_input_result"], "username_input_assumed")
        self.assertTrue(result.safe_metadata["username_placeholder_ignored"])

    def test_login_form_empty_never_returns_still_prefilled_for_placeholder_only(self) -> None:
        device = FakeDevice(CONNECTED_XML)
        device.add_selector(
            "className",
            "android.widget.EditText",
            PlaceholderStickyUsernameSelector(),
        )
        device.add_selector("text", "Password", FakeSelector(1))
        device.add_selector("text", "Log in", FakeSelector(1))

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=TrackingSecretValue(PASSWORD),
            prevalidated_signals=LOGIN_FORM_SIGNALS,
            sleeper=Mock(),
        )

        self.assertNotEqual(result.failure_reason, "username_still_prefilled_after_input")
        self.assertNotEqual(result.safe_metadata["username_input_result"], "username_still_prefilled_after_input")

    def test_login_form_empty_hierarchy_confirms_username_after_placeholder_readback(self) -> None:
        device = FakeDevice()
        device.add_selector(
            "className",
            "android.widget.EditText",
            PlaceholderStickyUsernameSelector(),
        )
        device.add_selector("text", "Password", FakeSelector(1))
        device.add_selector("text", "Log in", FakeSelector(1))
        device.hierarchies = [
            '<node class="android.widget.EditText" text="cinema_catchup" />'
            '<node class="android.widget.EditText" text="Password" />'
            '<node text="Log in" />',
            CONNECTED_XML,
        ]
        secret = TrackingSecretValue(PASSWORD)

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=secret,
            prevalidated_signals=LOGIN_FORM_SIGNALS,
            sleeper=Mock(),
        )

        self.assertTrue(result.executed)
        self.assertEqual(result.safe_metadata["username_input_result"], "username_input_confirmed")
        self.assertEqual(result.safe_metadata["username_input_confirmed"], "true")
        self.assertTrue(result.safe_metadata["username_placeholder_ignored"])

    def test_prefilled_wrong_username_is_cleared_replaced_then_password_submitted(self) -> None:
        device = FakeDevice(CONNECTED_XML)
        username = device.add_selector("text", "i_m_your_traker", TrackingTextSelector(text="i_m_your_traker"))
        password_selector = device.add_selector("text", "Password", FakeSelector(1))
        login = device.add_selector("text", "Log in", FakeSelector(1))
        secret = TrackingSecretValue(PASSWORD)

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=secret,
            prevalidated_signals=PREFILLED_USERNAME_SIGNALS,
            sleeper=Mock(),
        )

        self.assertTrue(result.executed)
        self.assertEqual(result.failure_reason, None)
        self.assertEqual(username.clear_calls, 1)
        self.assertEqual(username.set_text_calls, [USERNAME])
        self.assertEqual(password_selector.set_text_calls, [PASSWORD])
        self.assertEqual(login.click_calls, 1)
        self.assertTrue(result.safe_metadata["username_replaced"])
        self.assertEqual(result.safe_metadata["username_input_confirmed"], "true")
        self.assertEqual(result.safe_metadata["username_input_result"], "username_input_confirmed")

    def test_prefilled_expected_username_submits_password_without_blocking(self) -> None:
        device = FakeDevice(CONNECTED_XML)
        username = device.add_selector("text", USERNAME, TrackingTextSelector(text=USERNAME))
        password_selector = device.add_selector("text", "Password", FakeSelector(1))
        login = device.add_selector("text", "Log in", FakeSelector(1))

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=SecretValue(PASSWORD),
            prevalidated_signals={**PREFILLED_USERNAME_SIGNALS, "prefilled_username": USERNAME},
            sleeper=Mock(),
        )

        self.assertTrue(result.executed)
        self.assertEqual(result.failure_reason, None)
        self.assertEqual(username.set_text_calls, [])
        self.assertEqual(password_selector.set_text_calls, [PASSWORD])
        self.assertEqual(login.click_calls, 1)
        self.assertFalse(result.safe_metadata["username_replaced"])

    def test_prefilled_username_not_editable_refuses_before_password_reveal(self) -> None:
        device = FakeDevice(CONNECTED_XML)
        secret = TrackingSecretValue(PASSWORD)

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=secret,
            prevalidated_signals={
                **PREFILLED_USERNAME_SIGNALS,
                "username_field_editable_present": False,
                "username_editable_present": False,
            },
        )

        self.assertEqual(result.failure_reason, "username_prefilled_not_editable")
        self.assertFalse(result.executed)
        self.assertEqual(secret.reveal_calls, 0)

    def test_prefilled_username_prefers_edittext_selector(self) -> None:
        device = FakeDevice(CONNECTED_XML)
        username = device.add_selector(
            "className",
            "android.widget.EditText",
            EditTextUsernameSelector(text="i_m_your_traker"),
        )
        password_selector = device.add_selector("text", "Password", FakeSelector(1))
        login = device.add_selector("text", "Log in", FakeSelector(1))

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=SecretValue(PASSWORD),
            prevalidated_signals=PREFILLED_USERNAME_SIGNALS,
            sleeper=Mock(),
        )

        self.assertTrue(result.executed)
        self.assertEqual(username.set_text_calls, [USERNAME])
        self.assertTrue(result.safe_metadata["username_replaced"])
        self.assertEqual(result.safe_metadata["username_input_method"], "set_text")

    def test_prefilled_username_clear_keeps_text_then_set_text_replaces(self) -> None:
        device = FakeDevice(CONNECTED_XML)
        username = device.add_selector(
            "text",
            "i_m_your_traker",
            TrackingTextSelector(text="i_m_your_traker", clear_keeps_text=True),
        )
        password_selector = device.add_selector("text", "Password", FakeSelector(1))
        login = device.add_selector("text", "Log in", FakeSelector(1))
        secret = TrackingSecretValue(PASSWORD)

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=secret,
            prevalidated_signals=PREFILLED_USERNAME_SIGNALS,
            sleeper=Mock(),
        )

        self.assertTrue(result.executed)
        self.assertEqual(username.set_text_calls, [USERNAME])
        self.assertEqual(secret.reveal_calls, 1)
        self.assertEqual(password_selector.set_text_calls, [PASSWORD])

    def test_prefilled_username_without_clear_text_uses_set_text_replace(self) -> None:
        device = FakeDevice(CONNECTED_XML)
        username = device.add_selector(
            "text",
            "i_m_your_traker",
            SetTextOnlyUsernameSelector(text="i_m_your_traker"),
        )
        password_selector = device.add_selector("text", "Password", FakeSelector(1))
        login = device.add_selector("text", "Log in", FakeSelector(1))
        secret = TrackingSecretValue(PASSWORD)

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=secret,
            prevalidated_signals=PREFILLED_USERNAME_SIGNALS,
            sleeper=Mock(),
        )

        self.assertTrue(result.executed)
        self.assertIn(USERNAME, username.set_text_calls)
        self.assertEqual(secret.reveal_calls, 1)

    def test_prefilled_username_still_old_after_input_stops_without_password_reveal(self) -> None:
        device = FakeDevice(CONNECTED_XML)
        username = device.add_selector(
            "text",
            "i_m_your_traker",
            TrackingTextSelector(text="i_m_your_traker", clear_keeps_text=True, set_exc=RuntimeError("set failed")),
        )
        password_selector = device.add_selector("text", "Password", FakeSelector(1))
        login = device.add_selector("text", "Log in", FakeSelector(1))
        secret = TrackingSecretValue(PASSWORD)

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=secret,
            prevalidated_signals=PREFILLED_USERNAME_SIGNALS,
            sleeper=Mock(),
        )

        self.assertIn(
            result.failure_reason,
            {"username_clear_failed", "username_input_failed", "username_still_prefilled_after_input"},
        )
        self.assertFalse(result.executed)
        self.assertEqual(secret.reveal_calls, 0)
        self.assertEqual(password_selector.set_text_calls, [])
        self.assertEqual(login.click_calls, 0)

    def test_prefilled_username_clear_failure_refuses_before_password_reveal(self) -> None:
        device = FakeDevice(CONNECTED_XML)
        username = device.add_selector(
            "text",
            "i_m_your_traker",
            TrackingTextSelector(text="i_m_your_traker", clear_fails=True),
        )
        password_selector = device.add_selector("text", "Password", FakeSelector(1))
        login = device.add_selector("text", "Log in", FakeSelector(1))
        secret = TrackingSecretValue(PASSWORD)

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=secret,
            prevalidated_signals=PREFILLED_USERNAME_SIGNALS,
            sleeper=Mock(),
        )

        self.assertTrue(result.executed)
        self.assertEqual(secret.reveal_calls, 1)
        self.assertEqual(username.set_text_calls, ["", USERNAME])
        self.assertEqual(password_selector.set_text_calls, [PASSWORD])

    def test_password_revealed_only_via_explicit_reveal(self) -> None:
        device, _username, password_selector, _login = configured_device()
        secret = TrackingSecretValue(PASSWORD)

        execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=secret,
            prevalidated_signals=LOGIN_FORM_SIGNALS,
            sleeper=Mock(),
        )

        self.assertEqual(secret.reveal_calls, 1)
        self.assertEqual(secret.str_calls, 0)
        self.assertEqual(secret.repr_calls, 0)
        self.assertEqual(password_selector.set_text_calls, [PASSWORD])

    def test_password_only_form_enters_password_without_username(self) -> None:
        device = FakeDevice(CONNECTED_XML)
        username = device.add_selector("text", "Username, email or mobile number", FakeSelector(0))
        password_selector = device.add_selector("text", "Password", FakeSelector(1))
        login = device.add_selector("text", "Log in", FakeSelector(1))

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=SecretValue(PASSWORD),
            prevalidated_signals=PASSWORD_ONLY_SIGNALS,
            sleeper=Mock(),
        )

        self.assertTrue(result.ok)
        self.assertTrue(result.executed)
        self.assertFalse(result.username_entered)
        self.assertTrue(result.password_entered)
        self.assertEqual(username.click_calls, 0)
        self.assertEqual(password_selector.set_text_calls, [PASSWORD])
        self.assertEqual(login.click_calls, 1)
        self.assertTrue(result.safe_metadata["password_only_mode"])

    def test_password_only_prefers_unique_edittext_over_password_label(self) -> None:
        device = FakeDevice(CONNECTED_XML)
        password_label = device.add_selector("text", "Password", FakeSelector(1))
        password_edit_text = device.add_selector("className", "android.widget.EditText", FakeSelector(1))
        password_edit_text.info = {"text": "••••••••", "focused": True}
        login = device.add_selector("text", "Log in", FakeSelector(1))

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=SecretValue(PASSWORD),
            prevalidated_signals=PASSWORD_ONLY_SIGNALS,
            sleeper=Mock(),
        )

        self.assertTrue(result.executed)
        self.assertEqual(password_label.set_text_calls, [])
        self.assertEqual(password_edit_text.set_text_calls, [PASSWORD])
        self.assertEqual(login.click_calls, 1)

    def test_password_only_strong_password_overlay_still_submits(self) -> None:
        device = FakeDevice(CONNECTED_XML)
        device.add_selector("text", "Password", FakeSelector(1))
        login = device.add_selector("text", "Log in", FakeSelector(1))

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=SecretValue(PASSWORD),
            prevalidated_signals={**PASSWORD_ONLY_SIGNALS, "overlay_present": True},
            sleeper=Mock(),
        )

        self.assertTrue(result.ok)
        self.assertEqual(login.click_calls, 1)

    def test_password_only_saved_password_overlay_still_submits(self) -> None:
        device = FakeDevice(CONNECTED_XML)
        password_selector = device.add_selector("text", "Password", FakeSelector(1))
        login = device.add_selector("text", "Log in", FakeSelector(1))

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=SecretValue(PASSWORD),
            prevalidated_signals={**PASSWORD_ONLY_SIGNALS, "password_overlay_present": True},
            sleeper=Mock(),
        )

        self.assertTrue(result.ok)
        self.assertEqual(password_selector.set_text_calls, [PASSWORD])
        self.assertEqual(login.click_calls, 1)

    def test_password_only_overlay_blocks_login_once_then_recovers(self) -> None:
        device = FakeDevice(CONNECTED_XML)
        password_selector = device.add_selector("text", "Password", FakeSelector(1))
        login = device.add_selector("text", "Log in", FakeSelector(1))
        login.click_failures_remaining = 1

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=SecretValue(PASSWORD),
            prevalidated_signals={**PASSWORD_ONLY_SIGNALS, "overlay_present": True},
            sleeper=Mock(),
        )

        self.assertTrue(result.ok)
        self.assertEqual(login.click_calls, 2)
        self.assertEqual(password_selector.click_calls, 2)
        self.assertEqual(device.press_calls, ["back"])
        self.assertIn("overlay_submit_recovery_once", result.warnings)

    def test_password_only_overlay_blocks_login_twice_no_loop(self) -> None:
        device = FakeDevice(CONNECTED_XML)
        device.add_selector("text", "Password", FakeSelector(1))
        login = device.add_selector("text", "Log in", FakeSelector(1))
        login.click_failures_remaining = 2

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=SecretValue(PASSWORD),
            prevalidated_signals={**PASSWORD_ONLY_SIGNALS, "overlay_present": True},
            sleeper=Mock(),
        )

        self.assertEqual(result.failure_reason, "submit_failed")
        self.assertEqual(login.click_calls, 2)
        self.assertFalse(result.submit_tapped)

    def test_post_submit_logged_out_then_connected_settles_to_connected(self) -> None:
        device, _username, _password_selector, _login = configured_device()
        device.hierarchies = [LOGGED_OUT_XML, CONNECTED_XML]
        sleeper = Mock()

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=SecretValue(PASSWORD),
            prevalidated_signals=LOGIN_FORM_SIGNALS,
            post_submit_wait_ms=0,
            post_submit_observation_interval_ms=1,
            max_post_submit_observations=4,
            sleeper=sleeper,
        )

        self.assertEqual(result.post_submit_outcome, "connected")
        self.assertEqual(result.post_submit_probe_reason, "connected_ui_signal")
        self.assertEqual(result.safe_metadata["post_submit_observation_count"], 2)
        self.assertEqual(result.safe_metadata["post_submit_wait_total_ms"], 2)
        self.assertEqual(result.safe_metadata["post_submit_screens"], ["logged_out", "connected"])
        self.assertGreaterEqual(sleeper.call_count, 2)

    def test_post_submit_loading_then_connected_settles_to_connected(self) -> None:
        device, _username, _password_selector, _login = configured_device()
        device.hierarchies = [LOADING_XML, CONNECTED_XML]

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=SecretValue(PASSWORD),
            prevalidated_signals=LOGIN_FORM_SIGNALS,
            post_submit_wait_ms=0,
            post_submit_observation_interval_ms=1,
            max_post_submit_observations=4,
            sleeper=Mock(),
        )

        self.assertEqual(result.post_submit_outcome, "connected")
        self.assertEqual(result.safe_metadata["post_submit_screens"], ["loading", "connected"])

    def test_post_submit_location_services_prompt_is_connected_and_safe_backed(self) -> None:
        device, _username, _password_selector, login = configured_device()
        continue_button = device.add_selector("text", "Continue", FakeSelector(1))
        device.hierarchies = [POST_LOGIN_LOCATION_SERVICES_PROMPT_XML]

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=SecretValue(PASSWORD),
            prevalidated_signals=LOGIN_FORM_SIGNALS,
            post_submit_wait_ms=0,
            post_submit_observation_interval_ms=1,
            max_post_submit_observations=2,
            sleeper=Mock(),
        )

        self.assertEqual(result.post_submit_outcome, "connected")
        self.assertEqual(result.post_submit_screen_type, "connected_post_login_location_services_prompt")
        self.assertEqual(result.post_submit_probe_reason, "connected_post_login_location_services_prompt")
        self.assertEqual(
            result.safe_metadata["post_submit_screens"],
            ["connected_post_login_location_services_prompt"],
        )
        self.assertTrue(result.safe_metadata["post_login_location_services_prompt_detected"])
        self.assertTrue(result.safe_metadata["post_login_location_services_prompt_dismissed"])
        self.assertEqual(result.safe_metadata["post_login_location_services_prompt_dismiss_method"], "back")
        self.assertEqual(device.press_calls, ["back"])
        self.assertEqual(continue_button.click_calls, 0)
        self.assertEqual(login.click_calls, 1)

    def test_post_submit_notifications_skip_direct_is_connected(self) -> None:
        device, _username, _password_selector, login = configured_device()
        skip_button = device.add_selector("text", "Skip", FakeSelector(1))
        device.hierarchies = [INSTAGRAM_TURN_ON_NOTIFICATIONS_SKIP_XML, CONNECTED_XML]

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=SecretValue(PASSWORD),
            prevalidated_signals=LOGIN_FORM_SIGNALS,
            post_submit_wait_ms=0,
            post_submit_observation_interval_ms=1,
            max_post_submit_observations=4,
            sleeper=Mock(),
        )

        self.assertEqual(result.post_submit_outcome, "connected")
        self.assertEqual(result.post_submit_probe_reason, "connected_ui_signal")
        self.assertTrue(result.safe_metadata["notifications_prompt_detected"])
        self.assertTrue(result.safe_metadata["notifications_skip_tap_sent"])
        self.assertFalse(result.safe_metadata["notifications_next_tap_sent"])
        self.assertFalse(result.safe_metadata["notifications_skip_after_settings_sent"])
        self.assertEqual(skip_button.click_calls, 1)
        self.assertEqual(login.click_calls, 1)

    def test_post_submit_notifications_next_android_back_skip_flow(self) -> None:
        device, _username, _password_selector, login = configured_device()
        next_button = device.add_selector("text", "Next", FakeSelector(1))
        skip_button = device.add_selector("text", "Skip", FakeSelector(1))
        allow_toggle = device.add_selector("text", "Allow notifications", FakeSelector(1))
        device.hierarchies = [
            INSTAGRAM_TURN_ON_NOTIFICATIONS_NEXT_ONLY_XML,
            ANDROID_INSTAGRAM_NOTIFICATION_SETTINGS_XML,
            INSTAGRAM_TURN_ON_NOTIFICATIONS_SKIP_XML,
            CONNECTED_XML,
        ]

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=SecretValue(PASSWORD),
            prevalidated_signals=LOGIN_FORM_SIGNALS,
            post_submit_wait_ms=0,
            post_submit_observation_interval_ms=1,
            max_post_submit_observations=8,
            sleeper=Mock(),
        )

        self.assertEqual(result.post_submit_outcome, "connected")
        self.assertTrue(result.safe_metadata["notifications_prompt_detected"])
        self.assertTrue(result.safe_metadata["notifications_next_tap_sent"])
        self.assertTrue(result.safe_metadata["android_notification_settings_detected"])
        self.assertTrue(result.safe_metadata["android_back_from_notification_settings_sent"])
        self.assertTrue(result.safe_metadata["notifications_skip_after_settings_sent"])
        self.assertEqual(next_button.click_calls, 1)
        self.assertEqual(skip_button.click_calls, 1)
        self.assertEqual(allow_toggle.click_calls, 0)
        self.assertEqual(device.press_calls, ["back"])
        self.assertEqual(login.click_calls, 1)

    def test_post_submit_notifications_prompt_does_not_mark_failed(self) -> None:
        device, _username, _password_selector, login = configured_device()
        device.add_selector("text", "Next", FakeSelector(1))
        device.hierarchies = [INSTAGRAM_TURN_ON_NOTIFICATIONS_NEXT_ONLY_XML]

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=SecretValue(PASSWORD),
            prevalidated_signals=LOGIN_FORM_SIGNALS,
            post_submit_wait_ms=0,
            post_submit_observation_interval_ms=1,
            max_post_submit_observations=2,
            sleeper=Mock(),
        )

        self.assertEqual(result.post_submit_outcome, "connected")
        self.assertEqual(result.post_submit_screen_type, "instagram_turn_on_notifications_prompt")
        self.assertEqual(result.post_submit_probe_reason, "instagram_turn_on_notifications_prompt")
        self.assertTrue(result.safe_metadata["notifications_prompt_detected"])
        self.assertTrue(result.safe_metadata["notifications_next_tap_sent"])
        self.assertEqual(login.click_calls, 1)

    def test_post_submit_all_loading_returns_still_loading_timeout(self) -> None:
        device, _username, _password_selector, _login = configured_device()
        device.hierarchies = [LOADING_XML, LOADING_XML, LOADING_XML]

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=SecretValue(PASSWORD),
            prevalidated_signals=LOGIN_FORM_SIGNALS,
            post_submit_wait_ms=0,
            post_submit_observation_interval_ms=1,
            post_submit_timeout_ms=3,
            sleeper=Mock(),
        )

        self.assertEqual(result.post_submit_outcome, "login_submit_still_loading")
        self.assertEqual(result.post_submit_probe_reason, "post_submit_loading_timeout")
        self.assertTrue(result.safe_metadata["post_submit_loading_timeout"])
        self.assertEqual(result.safe_metadata["post_submit_timeout_ms"], 3)
        self.assertEqual(result.safe_metadata["post_submit_interval_ms"], 1)
        self.assertEqual(result.safe_metadata["final_terminal_screen"], "loading")

    def test_post_submit_unknown_non_loading_remains_unknown_after_settling(self) -> None:
        device, _username, _password_selector, _login = configured_device()
        unknown_xml = '<node text="Instagram" />'
        device.hierarchies = [unknown_xml, unknown_xml]

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=SecretValue(PASSWORD),
            prevalidated_signals=LOGIN_FORM_SIGNALS,
            post_submit_wait_ms=0,
            post_submit_observation_interval_ms=1,
            max_post_submit_observations=2,
            sleeper=Mock(),
        )

        self.assertEqual(result.post_submit_outcome, "unknown")
        self.assertEqual(result.post_submit_probe_reason, "post_submit_unknown_after_settling")
        self.assertFalse(result.safe_metadata["post_submit_loading_timeout"])

    def test_post_submit_final_recheck_detects_late_email_challenge_after_unknown_transition(self) -> None:
        device, _username, _password_selector, _login = configured_device()
        unknown_xml = '<node text="Instagram" />'
        device.hierarchies = [LOADING_XML, LOGGED_OUT_XML, unknown_xml, EMAIL_CODE_CHALLENGE_XML]

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=SecretValue(PASSWORD),
            prevalidated_signals=LOGIN_FORM_SIGNALS,
            post_submit_wait_ms=0,
            post_submit_observation_interval_ms=1,
            max_post_submit_observations=3,
            sleeper=Mock(),
        )

        self.assertEqual(result.post_submit_outcome, "verification_pending")
        self.assertEqual(result.post_submit_probe_reason, "verification_code_required")
        self.assertEqual(result.post_submit_screen_type, "email_code_challenge")
        self.assertEqual(
            result.safe_metadata["post_submit_screens"],
            ["loading", "logged_out", "unknown", "email_code_challenge"],
        )
        self.assertTrue(result.safe_metadata["email_code_challenge_detected"])
        self.assertIn("post_submit_final_recheck_terminal", result.warnings)

    def test_post_submit_password_required_uses_bounded_recovery(self) -> None:
        device, _username, password_selector, login = configured_device()
        ok = device.add_selector("text", "OK", FakeSelector(1))
        device.hierarchies = [PASSWORD_REQUIRED_XML, CONNECTED_XML]

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=SecretValue(PASSWORD),
            prevalidated_signals=LOGIN_FORM_SIGNALS,
            post_submit_wait_ms=0,
            post_submit_observation_interval_ms=1,
            max_post_submit_observations=2,
            sleeper=Mock(),
        )

        self.assertTrue(result.safe_metadata["password_required_dialog_detected"])
        self.assertTrue(result.safe_metadata["password_required_retry_attempted"])
        self.assertEqual(result.safe_metadata["password_required_retry_count"], 1)
        self.assertEqual(ok.click_calls, 1)
        self.assertEqual(login.click_calls, 2)
        self.assertGreaterEqual(len(password_selector.set_text_calls), 2)
        self.assertEqual(result.post_submit_outcome, "connected")

    def test_save_password_prompt_post_submit_dismisses_without_continue(self) -> None:
        device, _username, _password_selector, login = configured_device()
        continue_button = device.add_selector("text", "Continue", FakeSelector(1))
        device.hierarchies = [GOOGLE_SAVE_PASSWORD_PROMPT_XML, CONNECTED_XML]

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=SecretValue(PASSWORD),
            prevalidated_signals=LOGIN_FORM_SIGNALS,
            post_submit_wait_ms=0,
            post_submit_observation_interval_ms=1,
            max_post_submit_observations=4,
            sleeper=Mock(),
        )

        self.assertEqual(result.post_submit_outcome, "connected")
        self.assertEqual(result.safe_metadata["post_submit_screens"], ["google_password_manager_save_prompt", "connected"])
        self.assertTrue(result.safe_metadata["save_password_prompt_detected"])
        self.assertTrue(result.safe_metadata["save_password_prompt_dismissed"])
        self.assertEqual(result.safe_metadata["save_password_prompt_dismiss_attempt_count"], 1)
        self.assertEqual(result.safe_metadata["dismiss_method"], "back")
        self.assertEqual(result.safe_metadata["post_dismiss_screen_type"], "connected")
        self.assertEqual(device.press_calls, ["back"])
        self.assertEqual(continue_button.click_calls, 0)
        self.assertEqual(login.click_calls, 1)

    def test_post_submit_loading_then_save_password_prompt_uses_dismiss_path(self) -> None:
        device, _username, _password_selector, _login = configured_device()
        continue_button = device.add_selector("text", "Continue", FakeSelector(1))
        device.hierarchies = [LOADING_XML, GOOGLE_SAVE_PASSWORD_PROMPT_XML, CONNECTED_XML]

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=SecretValue(PASSWORD),
            prevalidated_signals=LOGIN_FORM_SIGNALS,
            post_submit_wait_ms=0,
            post_submit_observation_interval_ms=1,
            max_post_submit_observations=4,
            sleeper=Mock(),
        )

        self.assertEqual(result.post_submit_outcome, "connected")
        self.assertEqual(
            result.safe_metadata["post_submit_screens"],
            ["loading", "google_password_manager_save_prompt", "connected"],
        )
        self.assertTrue(result.safe_metadata["save_password_prompt_detected"])
        self.assertEqual(result.safe_metadata["save_password_prompt_dismiss_attempt_count"], 1)
        self.assertEqual(continue_button.click_calls, 0)

    def test_save_password_prompt_after_dismiss_connected_home(self) -> None:
        device, _username, _password_selector, _login = configured_device()
        device.hierarchies = [GOOGLE_SAVE_PASSWORD_PROMPT_XML, CONNECTED_XML]

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=SecretValue(PASSWORD),
            prevalidated_signals=LOGIN_FORM_SIGNALS,
            post_submit_wait_ms=0,
            post_submit_observation_interval_ms=1,
            max_post_submit_observations=4,
            sleeper=Mock(),
        )

        self.assertEqual(result.post_submit_outcome, "connected")
        self.assertTrue(result.safe_metadata["save_password_prompt_dismissed"])

    def test_save_password_prompt_after_dismiss_needs_2fa(self) -> None:
        device, _username, _password_selector, _login = configured_device()
        device.hierarchies = [GOOGLE_SAVE_PASSWORD_PROMPT_XML, NEEDS_2FA_XML]

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=SecretValue(PASSWORD),
            prevalidated_signals=LOGIN_FORM_SIGNALS,
            post_submit_wait_ms=0,
            post_submit_observation_interval_ms=1,
            max_post_submit_observations=4,
            sleeper=Mock(),
        )

        self.assertEqual(result.post_submit_outcome, "needs_2fa")
        self.assertEqual(result.safe_metadata["post_dismiss_screen_type"], "needs_2fa")

    def test_save_password_prompt_after_dismiss_checkpoint(self) -> None:
        device, _username, _password_selector, _login = configured_device()
        device.hierarchies = [GOOGLE_SAVE_PASSWORD_PROMPT_XML, CHECKPOINT_XML]

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=SecretValue(PASSWORD),
            prevalidated_signals=LOGIN_FORM_SIGNALS,
            post_submit_wait_ms=0,
            post_submit_observation_interval_ms=1,
            max_post_submit_observations=4,
            sleeper=Mock(),
        )

        self.assertEqual(result.post_submit_outcome, "checkpoint")
        self.assertEqual(result.safe_metadata["post_dismiss_screen_type"], "checkpoint")

    def test_samsung_pass_save_password_prompt_taps_cancel_never_save(self) -> None:
        device, _username, _password_selector, _login = configured_device()
        cancel = device.add_selector("text", "Cancel", FakeSelector(1))
        save = device.add_selector("text", "Save", FakeSelector(1))
        device.hierarchies = [SAMSUNG_PASS_SAVE_PASSWORD_PROMPT_XML, EMAIL_CODE_CHALLENGE_XML]

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=SecretValue(PASSWORD),
            prevalidated_signals=LOGIN_FORM_SIGNALS,
            post_submit_wait_ms=0,
            post_submit_observation_interval_ms=1,
            max_post_submit_observations=4,
            sleeper=Mock(),
        )

        self.assertEqual(result.post_submit_outcome, "verification_pending")
        self.assertTrue(result.safe_metadata["samsung_pass_save_password_prompt_detected"])
        self.assertTrue(result.safe_metadata["samsung_pass_save_password_prompt_cancelled"])
        self.assertEqual(result.safe_metadata["dismiss_method"], "cancel")
        self.assertEqual(cancel.click_calls, 1)
        self.assertEqual(save.click_calls, 0)
        self.assertEqual(device.press_calls, [])
        self.assertIn("samsung_pass_save_password_prompt_detected", result.warnings)
        self.assertIn("samsung_pass_save_password_prompt_cancelled", result.warnings)

    def test_instagram_save_login_info_prompt_taps_not_now_never_save(self) -> None:
        device, _username, _password_selector, _login = configured_device()
        not_now = device.add_selector("text", "Not now", FakeSelector(1))
        save = device.add_selector("text", "Save", FakeSelector(1))
        device.hierarchies = [INSTAGRAM_SAVE_LOGIN_INFO_PROMPT_XML, EMAIL_CODE_CHALLENGE_XML]

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=SecretValue(PASSWORD),
            prevalidated_signals=LOGIN_FORM_SIGNALS,
            post_submit_wait_ms=0,
            post_submit_observation_interval_ms=1,
            max_post_submit_observations=4,
            sleeper=Mock(),
        )

        self.assertEqual(result.post_submit_outcome, "verification_pending")
        self.assertTrue(result.safe_metadata["instagram_save_login_info_prompt_detected"])
        self.assertTrue(result.safe_metadata["instagram_save_login_info_prompt_not_now"])
        self.assertEqual(result.safe_metadata["dismiss_method"], "not_now")
        self.assertEqual(not_now.click_calls, 1)
        self.assertEqual(save.click_calls, 0)
        self.assertEqual(device.press_calls, [])
        self.assertIn("instagram_save_login_info_prompt_detected", result.warnings)
        self.assertIn("instagram_save_login_info_prompt_not_now", result.warnings)

    def test_save_password_prompt_still_visible_once_then_dismissed_after_second_attempt(self) -> None:
        device, _username, _password_selector, _login = configured_device()
        device.hierarchies = [
            GOOGLE_SAVE_PASSWORD_PROMPT_XML,
            GOOGLE_SAVE_PASSWORD_PROMPT_XML,
            CONNECTED_XML,
        ]

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=SecretValue(PASSWORD),
            prevalidated_signals=LOGIN_FORM_SIGNALS,
            post_submit_wait_ms=0,
            post_submit_observation_interval_ms=1,
            max_post_submit_observations=4,
            sleeper=Mock(),
        )

        self.assertEqual(result.post_submit_outcome, "connected")
        self.assertTrue(result.safe_metadata["save_password_prompt_detected"])
        self.assertTrue(result.safe_metadata["save_password_prompt_dismissed"])
        self.assertEqual(result.safe_metadata["save_password_prompt_dismiss_attempt_count"], 2)
        self.assertEqual(device.press_calls, ["back", "back"])

    def test_save_password_prompt_still_visible_after_two_dismiss_attempts_blocks(self) -> None:
        device, _username, _password_selector, _login = configured_device()
        continue_button = device.add_selector("text", "Continue", FakeSelector(1))
        device.hierarchies = [
            GOOGLE_SAVE_PASSWORD_PROMPT_XML,
            GOOGLE_SAVE_PASSWORD_PROMPT_XML,
            GOOGLE_SAVE_PASSWORD_PROMPT_XML,
        ]

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=SecretValue(PASSWORD),
            prevalidated_signals=LOGIN_FORM_SIGNALS,
            post_submit_wait_ms=0,
            post_submit_observation_interval_ms=1,
            max_post_submit_observations=4,
            sleeper=Mock(),
        )

        self.assertEqual(result.post_submit_outcome, "save_password_prompt_blocking")
        self.assertEqual(result.post_submit_probe_reason, "save_password_prompt_not_dismissed_after_2_attempts")
        self.assertTrue(result.safe_metadata["save_password_prompt_detected"])
        self.assertFalse(result.safe_metadata["save_password_prompt_dismissed"])
        self.assertEqual(result.safe_metadata["save_password_prompt_dismiss_attempt_count"], 2)
        self.assertEqual(device.press_calls, ["back", "back"])
        self.assertEqual(continue_button.click_calls, 0)

    def test_post_submit_needs_2fa_is_terminal(self) -> None:
        device, _username, _password_selector, _login = configured_device()
        device.hierarchies = [NEEDS_2FA_XML, CONNECTED_XML]

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=SecretValue(PASSWORD),
            prevalidated_signals=LOGIN_FORM_SIGNALS,
            post_submit_wait_ms=0,
            post_submit_observation_interval_ms=1,
            max_post_submit_observations=4,
            sleeper=Mock(),
        )

        self.assertEqual(result.post_submit_outcome, "needs_2fa")
        self.assertEqual(result.safe_metadata["post_submit_observation_count"], 1)

    def test_post_submit_checkpoint_is_terminal(self) -> None:
        device, _username, _password_selector, _login = configured_device()
        device.hierarchies = [CHECKPOINT_XML, CONNECTED_XML]

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=SecretValue(PASSWORD),
            prevalidated_signals=LOGIN_FORM_SIGNALS,
            post_submit_wait_ms=0,
            post_submit_observation_interval_ms=1,
            max_post_submit_observations=4,
            sleeper=Mock(),
        )

        self.assertEqual(result.post_submit_outcome, "checkpoint")
        self.assertEqual(result.safe_metadata["post_submit_observation_count"], 1)

    def test_post_submit_email_code_challenge_is_terminal_verification_pending(self) -> None:
        device, _username, _password_selector, login = configured_device()
        device.hierarchies = [LOADING_XML, EMAIL_CODE_CHALLENGE_XML, LOGGED_OUT_XML]

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=SecretValue(PASSWORD),
            prevalidated_signals=LOGIN_FORM_SIGNALS,
            post_submit_wait_ms=0,
            post_submit_observation_interval_ms=1,
            max_post_submit_observations=4,
            sleeper=Mock(),
        )

        self.assertEqual(result.post_submit_outcome, "verification_pending")
        self.assertEqual(result.post_submit_probe_reason, "verification_code_required")
        self.assertEqual(result.post_submit_screen_type, "email_code_challenge")
        self.assertEqual(result.safe_metadata["post_submit_observation_count"], 2)
        self.assertEqual(result.safe_metadata["post_submit_screens"], ["loading", "email_code_challenge"])
        self.assertTrue(result.safe_metadata["email_code_challenge_detected"])
        self.assertEqual(result.safe_metadata["challenge_type"], "email")
        self.assertTrue(result.safe_metadata["masked_email_present"])
        self.assertEqual(login.click_calls, 1)
        self.assertNotIn("post_submit_logged_out_after_settling", result.warnings)

    def test_post_submit_login_failed_is_terminal_no_retry(self) -> None:
        device, _username, _password_selector, login = configured_device()
        device.hierarchies = [LOGIN_FAILED_XML, CONNECTED_XML]

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=SecretValue(PASSWORD),
            prevalidated_signals=LOGIN_FORM_SIGNALS,
            post_submit_wait_ms=0,
            post_submit_observation_interval_ms=1,
            max_post_submit_observations=4,
            sleeper=Mock(),
        )

        self.assertEqual(result.post_submit_outcome, "login_failed")
        self.assertEqual(result.post_submit_probe_reason, "instagram_wrong_password")
        self.assertEqual(result.safe_metadata["post_submit_observation_count"], 1)
        self.assertEqual(login.click_calls, 1)

    def test_silent_logged_out_return_remains_unknown_and_fail_closed(self) -> None:
        device, _username, _password_selector, _login = configured_device()
        device.hierarchies = [LOGGED_OUT_XML, LOGGED_OUT_XML, LOGGED_OUT_XML, LOGGED_OUT_XML]

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=SecretValue(PASSWORD),
            prevalidated_signals=LOGIN_FORM_SIGNALS,
            post_submit_wait_ms=0,
            post_submit_observation_interval_ms=1,
            max_post_submit_observations=4,
            sleeper=Mock(),
        )

        self.assertEqual(result.post_submit_outcome, "logged_out")
        self.assertEqual(result.post_submit_probe_reason, "unknown_logged_out_return")
        self.assertEqual(result.safe_metadata["post_submit_observation_count"], 4)
        self.assertEqual(result.safe_metadata["post_submit_wait_total_ms"], 4)
        self.assertEqual(result.safe_metadata["final_terminal_screen"], "logged_out")

    def test_post_submit_timing_metadata_present(self) -> None:
        device, _username, _password_selector, _login = configured_device()
        device.hierarchies = [CONNECTED_XML]

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=SecretValue(PASSWORD),
            prevalidated_signals=LOGIN_FORM_SIGNALS,
            post_submit_wait_ms=0,
            sleeper=Mock(),
        )

        self.assertIn("post_submit_wait_total_ms", result.timings)
        self.assertIn("post_submit_observation_count", result.timings)
        self.assertIn("post_submit_screens", result.safe_metadata)
        self.assertIn("final_terminal_screen", result.safe_metadata)

    def test_non_login_form_screen_refuses_without_action(self) -> None:
        device, username, password_selector, login = configured_device()

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=SecretValue(PASSWORD),
            prevalidated_signals={**LOGIN_FORM_SIGNALS, "screen_type": "unknown"},
        )

        self.assertEqual(result.failure_reason, "login_form_not_validated")
        self.assertFalse(result.executed)
        self.assertEqual(username.click_calls + password_selector.click_calls + login.click_calls, 0)

    def test_username_field_absent_refuses_without_action(self) -> None:
        device = FakeDevice()
        device.add_selector("text", "Password", FakeSelector(1))
        device.add_selector("text", "Log in", FakeSelector(1))

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=SecretValue(PASSWORD),
            prevalidated_signals=LOGIN_FORM_SIGNALS,
        )

        self.assertEqual(result.failure_reason, "username_field_not_found")
        self.assertFalse(result.executed)

    def test_password_field_absent_refuses_without_action(self) -> None:
        device = FakeDevice()
        device.add_selector("text", "Username, email or mobile number", FakeSelector(1))
        device.add_selector("text", "Log in", FakeSelector(1))

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=SecretValue(PASSWORD),
            prevalidated_signals=LOGIN_FORM_SIGNALS,
        )

        self.assertEqual(result.failure_reason, "password_field_not_found")
        self.assertFalse(result.executed)

    def test_login_button_absent_refuses_without_action(self) -> None:
        device = FakeDevice()
        device.add_selector("text", "Username, email or mobile number", FakeSelector(1))
        device.add_selector("text", "Password", FakeSelector(1))

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=SecretValue(PASSWORD),
            prevalidated_signals=LOGIN_FORM_SIGNALS,
        )

        self.assertEqual(result.failure_reason, "login_button_not_found")
        self.assertFalse(result.executed)

    def test_expected_username_empty_refuses_without_action(self) -> None:
        device, username, _password_selector, _login = configured_device()

        result = execute_login_form_credentials(
            device,
            expected_username=" ",
            password=SecretValue(PASSWORD),
            prevalidated_signals=LOGIN_FORM_SIGNALS,
        )

        self.assertEqual(result.failure_reason, "expected_username_missing")
        self.assertEqual(username.click_calls, 0)
        self.assertEqual(device.selector_calls, [])

    def test_empty_password_secret_refuses_before_password_input(self) -> None:
        device, username, password_selector, login = configured_device()

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=SecretValue(""),
            prevalidated_signals=LOGIN_FORM_SIGNALS,
        )

        self.assertEqual(result.failure_reason, "password_secret_missing")
        self.assertFalse(result.executed)
        self.assertEqual(username.click_calls, 1)
        self.assertEqual(password_selector.click_calls + login.click_calls, 0)

    def test_ambiguous_form_signal_refuses_without_action(self) -> None:
        device, username, _password_selector, _login = configured_device()

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=SecretValue(PASSWORD),
            prevalidated_signals={**LOGIN_FORM_SIGNALS, "ambiguous_login_form": True},
        )

        self.assertEqual(result.failure_reason, "ambiguous_login_form")
        self.assertEqual(username.click_calls, 0)

    def test_ambiguous_selector_refuses_without_action(self) -> None:
        device, username, _password_selector, _login = configured_device()
        username._count = 2

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=SecretValue(PASSWORD),
            prevalidated_signals=LOGIN_FORM_SIGNALS,
        )

        self.assertEqual(result.failure_reason, "ambiguous_login_form")
        self.assertFalse(result.executed)

    def test_text_and_description_for_same_target_are_deduped(self) -> None:
        device = FakeDevice(CONNECTED_XML)
        password_selector = device.add_selector("text", "Password", FakeSelector(1))
        password_selector.info = {
            "resourceName": "password",
            "className": "android.widget.EditText",
            "bounds": {"left": 100, "top": 900, "right": 980, "bottom": 1020},
        }
        password_description = device.add_selector("description", "Password", FakeSelector(1))
        password_description.info = dict(password_selector.info)
        login = device.add_selector("text", "Log in", FakeSelector(1))
        login.info = {
            "resourceName": "login",
            "className": "android.widget.Button",
            "bounds": {"left": 100, "top": 1100, "right": 980, "bottom": 1220},
        }
        login_description = device.add_selector("description", "Log in", FakeSelector(1))
        login_description.info = dict(login.info)

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=SecretValue(PASSWORD),
            prevalidated_signals=PASSWORD_ONLY_SIGNALS,
            sleeper=Mock(),
        )

        self.assertTrue(result.executed)
        self.assertEqual(result.failure_reason, None)
        self.assertEqual(password_selector.set_text_calls, [PASSWORD])
        self.assertEqual(login.click_calls, 1)

    def test_unique_primary_selector_ignores_ambiguous_secondary_selector(self) -> None:
        device = FakeDevice(CONNECTED_XML)
        password_selector = device.add_selector("text", "Password", FakeSelector(1))
        device.add_selector("description", "Password", FakeSelector(1))
        login = device.add_selector("text", "Log in", FakeSelector(1))
        device.add_selector("description", "Log in", FakeSelector(2))

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=SecretValue(PASSWORD),
            prevalidated_signals=PASSWORD_ONLY_SIGNALS,
            sleeper=Mock(),
        )

        self.assertTrue(result.executed)
        self.assertEqual(result.failure_reason, None)
        self.assertEqual(password_selector.set_text_calls, [PASSWORD])
        self.assertEqual(login.click_calls, 1)

    def test_username_input_exception_returns_input_failed(self) -> None:
        device, username, _password_selector, login = configured_device()
        username.set_exc = RuntimeError("username boom")

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=SecretValue(PASSWORD),
            prevalidated_signals=LOGIN_FORM_SIGNALS,
        )

        self.assertEqual(result.failure_reason, "input_failed")
        self.assertFalse(result.executed)
        self.assertEqual(login.click_calls, 0)

    def test_password_input_exception_returns_input_failed(self) -> None:
        device, _username, password_selector, login = configured_device()
        password_selector.set_exc = RuntimeError("password boom")

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=SecretValue(PASSWORD),
            prevalidated_signals=LOGIN_FORM_SIGNALS,
        )

        self.assertEqual(result.failure_reason, "password_input_failed")
        self.assertFalse(result.executed)
        self.assertTrue(result.username_entered)
        self.assertEqual(login.click_calls, 0)

    def test_submit_tap_exception_returns_submit_failed(self) -> None:
        device, _username, _password_selector, login = configured_device()
        login.click_exc = RuntimeError("submit boom")

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=SecretValue(PASSWORD),
            prevalidated_signals=LOGIN_FORM_SIGNALS,
        )

        self.assertEqual(result.failure_reason, "submit_failed")
        self.assertFalse(result.executed)
        self.assertEqual(login.click_calls, 1)

    def test_post_submit_dump_called_once_when_enabled(self) -> None:
        device, _username, _password_selector, _login = configured_device()

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=SecretValue(PASSWORD),
            prevalidated_signals=LOGIN_FORM_SIGNALS,
            sleeper=Mock(),
        )

        self.assertTrue(result.executed)
        self.assertEqual(device.dump_calls, 1)

    def test_post_submit_connected_detected(self) -> None:
        result = self._execute_with_post_xml(CONNECTED_XML)

        self.assertEqual(result.post_submit_outcome, "connected")
        self.assertEqual(result.post_submit_screen_type, "connected")

    def test_post_submit_needs_2fa_detected(self) -> None:
        result = self._execute_with_post_xml(NEEDS_2FA_XML)

        self.assertEqual(result.post_submit_outcome, "needs_2fa")

    def test_post_submit_checkpoint_detected(self) -> None:
        result = self._execute_with_post_xml(CHECKPOINT_XML)

        self.assertEqual(result.post_submit_outcome, "checkpoint")

    def test_post_submit_login_failed_detected(self) -> None:
        result = self._execute_with_post_xml(LOGIN_FAILED_XML)

        self.assertEqual(result.post_submit_outcome, "login_failed")
        self.assertEqual(result.post_submit_probe_reason, "instagram_wrong_password")

    def test_post_submit_password_required_dialog_detected_without_retry(self) -> None:
        device, _username, _password_selector, _login = configured_device(PASSWORD_REQUIRED_XML)
        device.add_selector("text", "OK", FakeSelector(1))

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=SecretValue(PASSWORD),
            prevalidated_signals=LOGIN_FORM_SIGNALS,
            max_password_required_retry=0,
            sleeper=Mock(),
        )

        self.assertEqual(result.post_submit_outcome, "password_input_missing_or_not_accepted")
        self.assertEqual(result.post_submit_screen_type, "password_required_dialog")
        self.assertEqual(result.failure_reason, "password_input_missing_or_not_accepted")
        self.assertTrue(result.safe_metadata["password_required_dialog_detected"])
        self.assertFalse(result.safe_metadata["password_required_retry_attempted"])

    def test_password_required_dialog_retries_once_after_ok_refill(self) -> None:
        device, _username, password_selector, login = configured_device(PASSWORD_REQUIRED_XML)
        ok = device.add_selector("text", "OK", FakeSelector(1))
        device.hierarchies = [PASSWORD_REQUIRED_XML, CONNECTED_XML]

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=SecretValue(PASSWORD),
            prevalidated_signals=PASSWORD_ONLY_SIGNALS,
            sleeper=Mock(),
        )

        self.assertEqual(result.post_submit_outcome, "connected")
        self.assertEqual(login.click_calls, 2)
        self.assertEqual(ok.click_calls, 1)
        self.assertEqual(password_selector.set_text_calls, [PASSWORD, PASSWORD])
        self.assertTrue(result.safe_metadata["password_required_retry_attempted"])
        self.assertEqual(result.safe_metadata["password_required_retry_count"], 1)
        self.assertTrue(result.safe_metadata["password_refill_attempted"])
        self.assertTrue(result.safe_metadata["second_submit_executed"])

    def test_password_required_dialog_reappears_after_retry_no_second_retry(self) -> None:
        device, _username, _password_selector, login = configured_device(PASSWORD_REQUIRED_XML)
        ok = device.add_selector("text", "OK", FakeSelector(1))
        device.hierarchies = [PASSWORD_REQUIRED_XML, PASSWORD_REQUIRED_XML]

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=SecretValue(PASSWORD),
            prevalidated_signals=PASSWORD_ONLY_SIGNALS,
            sleeper=Mock(),
        )

        self.assertEqual(result.post_submit_outcome, "password_input_failed")
        self.assertEqual(result.failure_reason, "password_input_failed")
        self.assertEqual(login.click_calls, 2)
        self.assertEqual(ok.click_calls, 1)
        self.assertEqual(result.safe_metadata["password_required_retry_count"], 1)

    def test_empty_password_field_readback_blocks_submit(self) -> None:
        device, _username, password_selector, login = configured_device()
        password_selector.info = {"text": ""}

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=SecretValue(PASSWORD),
            prevalidated_signals=LOGIN_FORM_SIGNALS,
            sleeper=Mock(),
        )

        self.assertIn(
            result.failure_reason,
            {
                "password_input_not_confirmed",
                "password_input_failed",
                "password_input_missing_or_not_accepted",
                "password_input_unavailable",
                "adb_not_available",
                "adb_serial_missing",
            },
        )
        self.assertFalse(result.executed)
        self.assertEqual(login.click_calls, 0)
        self.assertFalse(result.safe_metadata["input_action_reported_success"])

    def test_masked_password_field_readback_allows_submit(self) -> None:
        device, _username, password_selector, login = configured_device(CONNECTED_XML)
        password_selector.info = {"text": "••••••••"}

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=SecretValue(PASSWORD),
            prevalidated_signals=LOGIN_FORM_SIGNALS,
            sleeper=Mock(),
        )

        self.assertTrue(result.executed)
        self.assertEqual(login.click_calls, 1)

    def test_adb_keyboard_input_success_allows_submit_without_set_text(self) -> None:
        device, _username, password_selector, login = configured_device(CONNECTED_XML)
        device.serial = "emulator-5554"
        device.selectors[("text", "Password")] = PlaceholderStickyPasswordSelector()
        password_selector = device.selectors[("text", "Password")]
        device.hierarchies = [
            '<node class="android.widget.EditText" text="••••••••" editable="true" />',
            CONNECTED_XML,
        ]

        with (
            patch.object(password_executor, "is_fast_ime_available", return_value=True),
            patch.object(password_executor, "adb_available", return_value=True),
            patch.object(
                password_executor,
                "ensure_adb_keyboard_ready",
                return_value={"ok": True, "reason": "adb_keyboard_ready"},
            ),
            patch.object(
                password_executor,
                "run_adb_keyboard_b64_input",
                return_value=(True, "adb_keyboard_b64", True, True),
            ) as fast_input,
        ):
            result = execute_login_form_credentials(
                device,
                expected_username=USERNAME,
                password=SecretValue(PASSWORD),
                prevalidated_signals=PASSWORD_ONLY_SIGNALS,
                sleeper=Mock(),
            )

        self.assertTrue(result.executed)
        self.assertEqual(result.safe_metadata["input_method_used"], "adb_keyboard_b64")
        self.assertTrue(result.safe_metadata["input_action_reported_success"])
        self.assertEqual(result.safe_metadata["password_field_non_empty_confirmed"], "true")
        self.assertEqual(password_selector.set_text_calls, [])
        self.assertEqual(login.click_calls, 1)
        self.assertEqual(fast_input.call_count, 1)
        self.assertIn("password_human_event_input_used", result.warnings)

    def test_prefilled_password_bullets_after_adb_input_allows_submit(self) -> None:
        device = FakeDevice(
            '<node class="android.widget.EditText" text="cinema_catchup" editable="true" />'
            '<node class="android.widget.EditText" text="••••••••" editable="true" />'
        )
        device.serial = "emulator-5554"
        username = TrackingTextSelector(text="i_m_your_traker")
        password_selector = FakeSelector(1)
        password_selector.info = {"text": "Password", "className": "android.widget.EditText"}
        device.add_selector("className", "android.widget.EditText", MultiSelector([username, password_selector]))
        login = device.add_selector("text", "Log in", FakeSelector(1))

        with patch.object(password_executor, "is_fast_ime_available", return_value=True), patch.object(
            password_executor,
            "run_adb_keyboard_b64_input",
            return_value=(True, "adb_keyboard_b64", True, True),
        ):
            result = execute_login_form_credentials(
                device,
                expected_username=USERNAME,
                password=SecretValue(PASSWORD),
                prevalidated_signals=PREFILLED_USERNAME_SIGNALS,
                dump_after_submit=False,
                sleeper=Mock(),
            )

        self.assertTrue(result.executed)
        self.assertEqual(login.click_calls, 1)
        self.assertEqual(result.safe_metadata["password_field_non_empty_confirmed"], "true")
        self.assertEqual(result.safe_metadata["password_confirm_method"], "hierarchy_masked_password")
        self.assertEqual(result.safe_metadata["password_input_method"], "adb_keyboard_b64")

    def test_password_target_uses_second_edittext_when_placeholder_would_match_wrong_field(self) -> None:
        device = FakeDevice(CONNECTED_XML)
        username = TrackingTextSelector(text=USERNAME)
        password_selector = TrackingTextSelector(text="Password")
        password_selector.info["className"] = "android.widget.EditText"
        device.add_selector("className", "android.widget.EditText", MultiSelector([username, password_selector]))
        login = device.add_selector("text", "Log in", FakeSelector(1))

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=SecretValue(PASSWORD),
            prevalidated_signals=LOGIN_FORM_SIGNALS,
            dump_after_submit=False,
            sleeper=Mock(),
        )

        self.assertTrue(result.executed)
        self.assertEqual(password_selector.set_text_calls, [PASSWORD])
        self.assertEqual(login.click_calls, 1)

    def test_password_target_uses_instance_lookup_when_selector_all_unavailable(self) -> None:
        device = FakeDevice(CONNECTED_XML)
        username = TrackingTextSelector(text=USERNAME)
        password_selector = TrackingTextSelector(text="Password")
        password_selector.info["className"] = "android.widget.EditText"
        device.add_selector("text", "Username, email or mobile number", username)
        device.add_selector("className", "android.widget.EditText", CountOnlySelector(2))
        device.add_selector("className_instance", "android.widget.EditText:0", username)
        device.add_selector("className_instance", "android.widget.EditText:1", password_selector)
        login = device.add_selector("text", "Log in", FakeSelector(1))

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=SecretValue(PASSWORD),
            prevalidated_signals=LOGIN_FORM_SIGNALS,
            dump_after_submit=False,
            sleeper=Mock(),
        )

        self.assertTrue(result.executed)
        self.assertEqual(password_selector.set_text_calls, [PASSWORD])
        self.assertEqual(login.click_calls, 1)

    def test_adb_keyboard_unknown_confirmation_allows_bounded_submit(self) -> None:
        device, _username, password_selector, login = configured_device(CONNECTED_XML)
        device.serial = "emulator-5554"
        password_selector.info = {"text": "Password", "className": "android.widget.EditText"}

        with patch.object(password_executor, "is_fast_ime_available", return_value=True), patch.object(
            password_executor,
            "run_adb_keyboard_b64_input",
            return_value=(True, "adb_keyboard_b64", True, True),
        ):
            result = execute_login_form_credentials(
                device,
                expected_username=USERNAME,
                password=SecretValue(PASSWORD),
                prevalidated_signals=PASSWORD_ONLY_SIGNALS,
                dump_after_submit=False,
                sleeper=Mock(),
            )

        self.assertTrue(result.executed)
        self.assertEqual(result.safe_metadata["password_field_non_empty_confirmed"], "unknown_but_input_success")
        self.assertEqual(result.safe_metadata["password_input_result"], "password_input_assumed")
        self.assertEqual(login.click_calls, 1)

    def test_password_field_clearly_empty_after_input_blocks_submit(self) -> None:
        device = FakeDevice('<node class="android.widget.EditText" text="" editable="true" />')
        device.serial = "emulator-5554"
        password_selector = device.add_selector("text", "Password", PlaceholderStickyPasswordSelector())
        login = device.add_selector("text", "Log in", FakeSelector(1))

        with patch.object(password_executor, "is_fast_ime_available", return_value=True), patch.object(
            password_executor,
            "run_adb_keyboard_b64_input",
            return_value=(True, "adb_keyboard_b64", True, True),
        ):
            result = execute_login_form_credentials(
                device,
                expected_username=USERNAME,
                password=SecretValue(PASSWORD),
                prevalidated_signals=PASSWORD_ONLY_SIGNALS,
                sleeper=Mock(),
            )

        self.assertIn(
            result.failure_reason,
            {"password_input_not_confirmed", "password_input_failed", "password_input_missing_or_not_accepted"},
        )
        self.assertFalse(result.executed)
        self.assertEqual(result.safe_metadata["password_field_non_empty_confirmed"], "false")
        self.assertEqual(login.click_calls, 0)
        self.assertIn("password_input_fallback_failed", result.warnings)

    def test_unknown_adb_confirmation_password_required_recovery_still_bounded(self) -> None:
        device, _username, password_selector, _login = configured_device(PASSWORD_REQUIRED_XML)
        device.serial = "emulator-5554"
        password_selector.info = {"text": "Password", "className": "android.widget.EditText"}

        with patch.object(password_executor, "is_fast_ime_available", return_value=True), patch.object(
            password_executor,
            "run_adb_keyboard_b64_input",
            return_value=(True, "adb_keyboard_b64", True, True),
        ):
            result = execute_login_form_credentials(
                device,
                expected_username=USERNAME,
                password=SecretValue(PASSWORD),
                prevalidated_signals=PASSWORD_ONLY_SIGNALS,
                sleeper=Mock(),
            )

        self.assertTrue(result.safe_metadata["password_required_dialog_detected"])
        self.assertEqual(result.safe_metadata["password_required_retry_count"], 1)

    def test_blocked_secret_payload_shape_prevents_login_submit(self) -> None:
        device, _username, password_selector, login = configured_device()
        payload = json.dumps(
            {
                "password": PASSWORD,
                "account_id": "00000000-0000-4000-8000-000000000000",
                "credentials_version": 1000,
                "created_at": "2026-05-27T00:00:00Z",
            }
        )

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=SecretValue(payload),
            prevalidated_signals=PASSWORD_ONLY_SIGNALS,
            sleeper=Mock(),
        )

        self.assertEqual(result.failure_reason, "blocked_secret_payload_shape")
        self.assertFalse(result.executed)
        self.assertEqual(login.click_calls, 0)
        self.assertEqual(password_selector.set_text_calls, [])
        self.assertEqual(result.safe_metadata["password_submit_result"], "blocked_secret_payload_shape")

    def test_adb_keyboard_unavailable_falls_back_to_set_text(self) -> None:
        device, _username, password_selector, login = configured_device(CONNECTED_XML)
        device.serial = "emulator-5554"
        password_selector.info = {"text": "••••••••"}

        with patch.object(password_executor, "is_fast_ime_available", return_value=False), patch.object(
            password_executor,
            "run_adb_keyboard_b64_input",
        ) as fast_input:
            result = execute_login_form_credentials(
                device,
                expected_username=USERNAME,
                password=SecretValue(PASSWORD),
                prevalidated_signals=PASSWORD_ONLY_SIGNALS,
                sleeper=Mock(),
            )

        self.assertTrue(result.executed)
        self.assertEqual(result.safe_metadata["input_method_used"], "set_text")
        self.assertEqual(password_selector.set_text_calls, [PASSWORD])
        self.assertEqual(login.click_calls, 1)
        fast_input.assert_not_called()

    def test_adb_keyboard_unavailable_is_retryable_when_set_text_not_confirmed(self) -> None:
        device, _username, _password_selector, login = configured_device(CONNECTED_XML)
        device.serial = "unit-test-serial"
        device.selectors[("text", "Password")] = PlaceholderStickyPasswordSelector()

        with (
            patch.object(password_executor, "adb_available", return_value=True),
            patch.object(
                password_executor,
                "ensure_adb_keyboard_ready",
                return_value={"ok": False, "reason": "adb_keyboard_package_missing"},
            ),
        ):
            result = execute_login_form_credentials(
                device,
                expected_username=USERNAME,
                password=SecretValue(PASSWORD),
                prevalidated_signals=PASSWORD_ONLY_SIGNALS,
                sleeper=Mock(),
            )

        self.assertFalse(result.executed)
        self.assertEqual(result.failure_reason, "password_input_missing_or_not_accepted")
        self.assertEqual(login.click_calls, 0)
        self.assertEqual(result.safe_metadata["password_confirm_method"], "target_accessibility_empty")

    def test_adb_keyboard_broadcast_failure_blocks_submit(self) -> None:
        device, _username, _password_selector, login = configured_device(CONNECTED_XML)
        device.serial = "unit-test-serial"
        device.selectors[("text", "Password")] = PlaceholderStickyPasswordSelector()

        with (
            patch.object(password_executor, "adb_available", return_value=True),
            patch.object(
                password_executor,
                "ensure_adb_keyboard_ready",
                return_value={"ok": True, "reason": "adb_keyboard_ready"},
            ),
            patch.object(
                password_executor,
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
            result = execute_login_form_credentials(
                device,
                expected_username=USERNAME,
                password=SecretValue(PASSWORD),
                prevalidated_signals=PASSWORD_ONLY_SIGNALS,
                sleeper=Mock(),
            )

        self.assertFalse(result.executed)
        self.assertEqual(result.failure_reason, "password_input_missing_or_not_accepted")
        self.assertEqual(login.click_calls, 0)
        self.assertEqual(result.safe_metadata["password_confirm_method"], "target_accessibility_empty")

    def test_result_safe_dict_contains_no_password(self) -> None:
        device, _username, _password_selector, _login = configured_device(SENSITIVE_XML)

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=SecretValue(PASSWORD),
            prevalidated_signals=LOGIN_FORM_SIGNALS,
            sleeper=Mock(),
        )
        rendered = json.dumps(asdict(result), sort_keys=True)

        self.assertNotIn(PASSWORD, rendered)
        self.assertNotIn("fake-password", rendered)
        self.assertNotIn("password secret_ref Vault token", rendered)

    def test_result_safe_dict_excludes_secret_ref_vault_token_device_xml_screenshot(self) -> None:
        device, _username, _password_selector, _login = configured_device(SENSITIVE_XML)

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=SecretValue(PASSWORD),
            prevalidated_signals=LOGIN_FORM_SIGNALS,
            sleeper=Mock(),
        )
        rendered = json.dumps(asdict(result), sort_keys=True)

        for forbidden in (
            "secret_ref",
            "vault",
            "Vault",
            "token",
            "emulator-5554",
            "device_udid",
            "adb_serial",
            "xml",
            "screenshot",
        ):
            self.assertNotIn(forbidden, rendered)

    def test_post_submit_wait_ms_clamped_0_to_3000(self) -> None:
        for raw_wait, expected_wait, expected_sleep in ((-5, 0, 1.0), (9999, 3000, 3.0)):
            with self.subTest(raw_wait=raw_wait):
                device, _username, _password_selector, _login = configured_device()
                sleeper = Mock()

                result = execute_login_form_credentials(
                    device,
                    expected_username=USERNAME,
                    password=SecretValue(PASSWORD),
                    prevalidated_signals=LOGIN_FORM_SIGNALS,
                    post_submit_wait_ms=raw_wait,
                    sleeper=sleeper,
                )

                self.assertEqual(result.timings["post_submit_wait_ms"], expected_wait)
                sleeper.assert_any_call(expected_sleep)

    def test_no_retry_by_default(self) -> None:
        device, username, password_selector, login = configured_device()
        device.dump_exc = RuntimeError("dump boom")

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=SecretValue(PASSWORD),
            prevalidated_signals=LOGIN_FORM_SIGNALS,
            sleeper=Mock(),
        )

        self.assertEqual(result.failure_reason, "post_submit_dump_failed")
        self.assertEqual(username.click_calls, 1)
        self.assertEqual(password_selector.click_calls, 1)
        self.assertEqual(login.click_calls, 1)
        self.assertEqual(device.dump_calls, 1)

    def _execute_with_post_xml(self, xml: str):
        device, _username, _password_selector, _login = configured_device(xml)
        return execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=SecretValue(PASSWORD),
            prevalidated_signals=LOGIN_FORM_SIGNALS,
            sleeper=Mock(),
        )

    def test_set_text_empty_then_adb_keyboard_b64_confirms_and_submits(self) -> None:
        device, username, password_selector, login = configured_device(CONNECTED_XML)
        device.serial = "emulator-5554"
        device.selectors[("text", "Password")] = PlaceholderStickyPasswordSelector()
        password_selector = device.selectors[("text", "Password")]
        device.hierarchies = [
            '<node class="android.widget.EditText" text="cinema_catchup" />'
            '<node class="android.widget.EditText" text="••••••••" />'
            '<node text="Log in" />',
            CONNECTED_XML,
        ]

        with (
            patch.object(password_executor, "is_fast_ime_available", return_value=True),
            patch.object(password_executor, "adb_available", return_value=True),
            patch.object(
                password_executor,
                "run_adb_keyboard_b64_input",
                return_value=(True, "adb_keyboard_b64", True, True),
            ) as fast_input,
        ):
            result = execute_login_form_credentials(
                device,
                expected_username=USERNAME,
                password=SecretValue(PASSWORD),
                prevalidated_signals=LOGIN_FORM_SIGNALS,
                sleeper=Mock(),
            )

        self.assertTrue(result.executed)
        self.assertEqual(username.set_text_calls, [])
        self.assertEqual(password_selector.set_text_calls, [])
        self.assertEqual(result.safe_metadata["input_method_used"], "adb_keyboard_b64")
        self.assertEqual(login.click_calls, 1)
        self.assertEqual(fast_input.call_count, 2)
        self.assertIn("username_human_event_input_used", result.warnings)
        self.assertIn("password_human_event_input_used", result.warnings)

    def test_set_text_empty_and_adb_fallback_failure_blocks_submit_without_leak(self) -> None:
        device, _username, password_selector, login = configured_device()
        device.serial = "emulator-5554"
        device.selectors[("text", "Password")] = PlaceholderStickyPasswordSelector()
        password_selector = device.selectors[("text", "Password")]

        with patch.object(password_executor, "is_fast_ime_available", return_value=True), patch.object(
            password_executor,
            "run_adb_keyboard_b64_input",
            return_value=(False, "adb_keyboard_b64", False, False),
        ):
            result = execute_login_form_credentials(
                device,
                expected_username=USERNAME,
                password=SecretValue(PASSWORD),
                prevalidated_signals=LOGIN_FORM_SIGNALS,
                sleeper=Mock(),
            )

        rendered = json.dumps(asdict(result), sort_keys=True)
        self.assertIn(
            result.failure_reason,
            {"password_input_failed", "password_input_missing_or_not_accepted", "adb_keyboard_b64_failed"},
        )
        self.assertFalse(result.executed)
        self.assertEqual(login.click_calls, 0)
        self.assertNotIn(PASSWORD, rendered)
        self.assertIn("password_input_fallback_failed", result.warnings)

    def test_login_form_empty_injection_trace_never_logs_password(self) -> None:
        device = FakeDevice(CONNECTED_XML)
        device.serial = "emulator-5554"
        device.add_selector("text", "Username, email or mobile number", FakeSelector(1))
        device.add_selector("text", "Password", PlaceholderStickyPasswordSelector())
        device.add_selector("text", "Log in", FakeSelector(1))
        secret_ref = "secret_ref:83de9cc9-5c37-42d1-9edc-c924352b17b1:password:v3"
        vault_uuid = "83de9cc9-5c37-42d1-9edc-c924352b17b1"

        with patch.object(password_executor, "is_fast_ime_available", return_value=True), patch.object(
            password_executor,
            "run_adb_keyboard_b64_input",
            return_value=(False, "adb_keyboard_b64", False, False),
        ):
            result = execute_login_form_credentials(
                device,
                expected_username=USERNAME,
                password=SecretValue(PASSWORD),
                prevalidated_signals=LOGIN_FORM_SIGNALS,
                sleeper=Mock(),
            )

        rendered = json.dumps({"warnings": result.warnings, "metadata": result.safe_metadata}, sort_keys=True)
        for forbidden in (PASSWORD, secret_ref, vault_uuid, "len(", "hash("):
            self.assertNotIn(forbidden, rendered)

    def test_submit_re_resolves_fresh_login_button_after_password_fill(self) -> None:
        stale = FakeSelector(1)
        fresh = FakeSelector(1)
        device = RefreshingLoginButtonDevice(stale=stale, fresh=fresh)
        device.add_selector("text", "Username, email or mobile number", FakeSelector(1))
        device.add_selector("text", "Password", FakeSelector(1))
        device.hierarchies = [FILLED_LOGIN_FORM_XML, EMAIL_CODE_CHALLENGE_XML]

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=SecretValue(PASSWORD),
            prevalidated_signals=LOGIN_FORM_SIGNALS,
            post_submit_wait_ms=0,
            sleeper=Mock(),
        )

        self.assertEqual(result.post_submit_outcome, "verification_pending")
        self.assertEqual(stale.click_calls, 0)
        self.assertEqual(fresh.click_calls, 1)
        self.assertIn("login_submit_fresh_surface_proved", result.warnings)

    def test_ime_hidden_login_button_uses_one_back_then_fresh_layout_target(self) -> None:
        initial = FakeSelector(1)
        hidden = FakeSelector(0)
        fresh_after_back = FakeSelector(1)
        device = SequencedLoginButtonDevice(
            [initial, hidden, fresh_after_back],
            [FILLED_LOGIN_FORM_XML, FILLED_LOGIN_FORM_XML, EMAIL_CODE_CHALLENGE_XML],
        )
        device.add_selector("text", "Username, email or mobile number", FakeSelector(1))
        device.add_selector("text", "Password", FakeSelector(1))

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=SecretValue(PASSWORD),
            prevalidated_signals=LOGIN_FORM_SIGNALS,
            post_submit_wait_ms=0,
            sleeper=Mock(),
        )

        self.assertEqual(result.post_submit_outcome, "verification_pending")
        self.assertEqual(device.press_calls, ["back"])
        self.assertEqual(initial.click_calls, 0)
        self.assertEqual(hidden.click_calls, 0)
        self.assertEqual(fresh_after_back.click_calls, 1)
        self.assertIn("login_submit_ime_hide_recovery_sent", result.warnings)

    def test_disabled_login_button_is_re_resolved_once_then_clicked_when_enabled(self) -> None:
        initial = FakeSelector(1)
        disabled = FakeSelector(1)
        disabled.info = {"enabled": False}
        enabled = FakeSelector(1)
        enabled.info = {"enabled": True}
        device = SequencedLoginButtonDevice(
            [initial, disabled, enabled],
            [FILLED_LOGIN_FORM_XML, EMAIL_CODE_CHALLENGE_XML],
        )
        device.add_selector("text", "Username, email or mobile number", FakeSelector(1))
        device.add_selector("text", "Password", FakeSelector(1))

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=SecretValue(PASSWORD),
            prevalidated_signals=LOGIN_FORM_SIGNALS,
            post_submit_wait_ms=0,
            sleeper=Mock(),
        )

        self.assertEqual(result.post_submit_outcome, "verification_pending")
        self.assertEqual(initial.click_calls, 0)
        self.assertEqual(disabled.click_calls, 0)
        self.assertEqual(enabled.click_calls, 1)

    def test_loading_then_returned_form_gets_one_fresh_bounded_submit_recovery(self) -> None:
        device, _username, _password_selector, login = configured_device()
        device.hierarchies = [
            LOADING_XML,
            LOGGED_OUT_XML,
            LOGGED_OUT_XML,
            LOGGED_OUT_XML,
            FILLED_LOGIN_FORM_XML,
            EMAIL_CODE_CHALLENGE_XML,
        ]

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=SecretValue(PASSWORD),
            prevalidated_signals=LOGIN_FORM_SIGNALS,
            post_submit_wait_ms=0,
            post_submit_observation_interval_ms=1,
            max_post_submit_observations=4,
            sleeper=Mock(),
        )

        self.assertEqual(result.post_submit_outcome, "verification_pending")
        self.assertEqual(login.click_calls, 2)
        self.assertTrue(result.safe_metadata["second_submit_executed"])
        self.assertIn("login_submit_returned_form_recovery_tap_sent", result.warnings)

    def test_stable_logged_out_without_loading_never_double_submits(self) -> None:
        device, _username, _password_selector, login = configured_device()
        device.hierarchies = [LOGGED_OUT_XML, LOGGED_OUT_XML, LOGGED_OUT_XML, LOGGED_OUT_XML]

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=SecretValue(PASSWORD),
            prevalidated_signals=LOGIN_FORM_SIGNALS,
            post_submit_wait_ms=0,
            post_submit_observation_interval_ms=1,
            max_post_submit_observations=4,
            sleeper=Mock(),
        )

        self.assertEqual(result.post_submit_probe_reason, "unknown_logged_out_return")
        self.assertEqual(login.click_calls, 1)
        self.assertFalse(result.safe_metadata["second_submit_executed"])

    def test_challenge_surface_before_submit_fails_closed_without_tap(self) -> None:
        device, _username, _password_selector, login = configured_device()
        device.login_submit_hierarchy = EMAIL_CODE_CHALLENGE_XML

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=SecretValue(PASSWORD),
            prevalidated_signals=LOGIN_FORM_SIGNALS,
            sleeper=Mock(),
        )

        self.assertEqual(result.failure_reason, "login_submit_wrong_surface")
        self.assertEqual(login.click_calls, 0)


if __name__ == "__main__":
    unittest.main()
