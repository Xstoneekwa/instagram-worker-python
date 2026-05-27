from __future__ import annotations

import json
import unittest
from dataclasses import asdict
from unittest.mock import Mock
from unittest.mock import patch

from instagram_credentials_runtime_access import SecretValue
import instagram_login_password_form_executor as password_executor
from instagram_login_password_form_executor import execute_login_form_credentials


USERNAME = "cinema_catchup"
PASSWORD = "fake-password-for-unit-tests"
LOGIN_FORM_SIGNALS = {
    "screen_type": "login_form_empty",
    "has_username_field": True,
    "has_password_field": True,
    "has_login_button": True,
}
PASSWORD_ONLY_SIGNALS = {
    "screen_type": "continue_password_only",
    "suggested_username": "cinema_catchup",
    "has_username_field": False,
    "has_password_field": True,
    "has_login_button": True,
}
CONNECTED_XML = (
    '<node content-desc="Home" />'
    '<node content-desc="Search" />'
    '<node content-desc="Reels" />'
    '<node content-desc="Profile" />'
)
NEEDS_2FA_XML = '<node text="Enter code" /><node text="authentication code" />'
CHECKPOINT_XML = '<node text="Help us confirm it’s you" /><node text="Verify your account" />'
LOGIN_FAILED_XML = '<node text="Sorry, your password was incorrect. Please try again." />'
LOGGED_OUT_XML = '<node text="Log in to Instagram" /><node text="Username" /><node text="Password" />'
LOADING_XML = '<node text="Loading..." />'
SENSITIVE_XML = '<node text="password secret_ref Vault token emulator-5554 screenshot" />'
PASSWORD_REQUIRED_XML = (
    '<node text="Password required" />'
    '<node text="Enter your password to continue." />'
    '<node text="OK" />'
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

    def add_selector(self, key: str, value: str, selector: FakeSelector) -> FakeSelector:
        self.selectors[(key, value)] = selector
        return selector

    def __call__(self, **kwargs):
        self.selector_calls.append(dict(kwargs))
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

    def press(self, key: str) -> None:
        self.press_calls.append(str(key))


def configured_device(hierarchy: str = CONNECTED_XML) -> tuple[FakeDevice, FakeSelector, FakeSelector, FakeSelector]:
    device = FakeDevice(hierarchy)
    username = device.add_selector("text", "Username, email or mobile number", FakeSelector(1))
    password = device.add_selector("text", "Password", FakeSelector(1))
    login = device.add_selector("text", "Log in", FakeSelector(1))
    return device, username, password, login


class InstagramLoginPasswordFormExecutorTest(unittest.TestCase):
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
        sleeper.assert_called_once_with(1.0)

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
        self.assertEqual(sleeper.call_count, 2)

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
        self.assertEqual(result.safe_metadata["post_submit_observation_count"], 1)
        self.assertEqual(login.click_calls, 1)

    def test_post_submit_session_expired_stable_after_settling(self) -> None:
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
        self.assertEqual(result.post_submit_probe_reason, "session_expired_after_settling")
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

    def test_empty_password_secret_refuses_without_ui_action(self) -> None:
        device, username, password_selector, login = configured_device()

        result = execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=SecretValue(""),
            prevalidated_signals=LOGIN_FORM_SIGNALS,
        )

        self.assertEqual(result.failure_reason, "password_secret_missing")
        self.assertFalse(result.executed)
        self.assertEqual(username.click_calls + password_selector.click_calls + login.click_calls, 0)

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

        self.assertEqual(result.failure_reason, "password_input_not_confirmed")
        self.assertFalse(result.executed)
        self.assertEqual(login.click_calls, 0)
        self.assertTrue(result.safe_metadata["input_action_reported_success"])

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
        password_selector.info = {"text": "••••••••", "focused": True}

        with patch.object(password_executor, "is_fast_ime_available", return_value=True), patch.object(
            password_executor,
            "_run_adb_keyboard_b64_input",
            return_value=(True, "adb_keyboard_b64", True, True),
        ) as fast_input:
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
        fast_input.assert_called_once()

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
            "_run_adb_keyboard_b64_input",
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
                sleeper.assert_called_once_with(expected_sleep)

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


if __name__ == "__main__":
    unittest.main()
