from __future__ import annotations

import inspect
import unittest
from unittest.mock import Mock, call, patch

import instagram_login_password_form_executor as executor
from instagram_credentials_runtime_access import SecretValue
from tests.test_instagram_login_password_form_executor import (
    CONNECTED_XML,
    FILLED_LOGIN_FORM_XML,
    LOGIN_FORM_SIGNALS,
    PASSWORD,
    USERNAME,
    configured_device,
)


class InstagramLoginHumanInputEventsTest(unittest.TestCase):
    def _execute(self, device, *, sleeper=None):
        return executor.execute_login_form_credentials(
            device,
            expected_username=USERNAME,
            password=SecretValue(PASSWORD),
            prevalidated_signals=LOGIN_FORM_SIGNALS,
            sleeper=sleeper or Mock(),
        )

    def test_human_event_input_is_preferred_for_username_and_password(self) -> None:
        device, username, password, login = configured_device(CONNECTED_XML)
        device.serial = "unit-test-serial"
        with (
            patch.object(executor, "is_fast_ime_available", return_value=True),
            patch.object(executor, "adb_available", return_value=True),
            patch.object(
                executor,
                "ensure_adb_keyboard_ready",
                return_value={"ok": True, "reason": "adb_keyboard_ready"},
            ),
            patch.object(
                executor,
                "run_adb_keyboard_b64_input",
                return_value=(True, "adb_keyboard_b64", True, True),
            ) as human_input,
        ):
            result = self._execute(device)

        self.assertTrue(result.executed)
        self.assertEqual(human_input.call_count, 2)
        self.assertEqual(username.set_text_calls, [])
        self.assertEqual(password.set_text_calls, [])
        self.assertEqual(login.click_calls, 1)
        self.assertEqual(result.safe_metadata["username_input_method"], "adb_keyboard_b64")
        self.assertEqual(result.safe_metadata["password_input_method"], "adb_keyboard_b64")

    def test_set_text_remains_safe_fallback_when_human_input_is_unavailable(self) -> None:
        device, username, password, login = configured_device(CONNECTED_XML)
        device.serial = "unit-test-serial"
        with patch.object(executor, "is_fast_ime_available", return_value=False):
            result = self._execute(device)

        self.assertTrue(result.executed)
        self.assertEqual(username.set_text_calls, [USERNAME])
        self.assertEqual(password.set_text_calls, [PASSWORD])
        self.assertEqual(login.click_calls, 1)
        self.assertIn("username_set_text_fallback_used", result.warnings)
        self.assertIn("password_set_text_fallback_used", result.warnings)

    def test_stable_fields_are_reobserved_after_events_settle_before_single_submit(self) -> None:
        device, _username, _password, login = configured_device(CONNECTED_XML)
        sleeper = Mock()
        result = self._execute(device, sleeper=sleeper)

        self.assertTrue(result.executed)
        self.assertEqual(login.click_calls, 1)
        self.assertTrue(result.safe_metadata["field_content_stable_before_submit"])
        self.assertFalse(result.safe_metadata["autofill_interference_detected"])
        self.assertTrue(result.safe_metadata["fresh_form_observation_before_tap"])
        self.assertTrue(result.safe_metadata["form_events_settled"])
        self.assertIn(call(executor.LOGIN_FORM_EVENT_SETTLE_MS / 1000.0), sleeper.mock_calls)

    def test_autofill_username_change_blocks_submit_without_blind_retry(self) -> None:
        device, _username, _password, login = configured_device(CONNECTED_XML)
        changed = FILLED_LOGIN_FORM_XML.replace(USERNAME, "another_account")
        device.dump_login_submit_hierarchy = Mock(side_effect=[FILLED_LOGIN_FORM_XML, changed])

        result = self._execute(device)

        self.assertFalse(result.executed)
        self.assertEqual(result.failure_reason, "login_submit_username_changed_before_tap")
        self.assertTrue(result.safe_metadata["autofill_interference_detected"])
        self.assertEqual(login.click_calls, 0)

    def test_autofill_password_clear_blocks_submit_without_false_success(self) -> None:
        device, _username, _password, login = configured_device(CONNECTED_XML)
        cleared = FILLED_LOGIN_FORM_XML.replace("••••••••", "")
        device.dump_login_submit_hierarchy = Mock(side_effect=[FILLED_LOGIN_FORM_XML, cleared])

        result = self._execute(device)

        self.assertFalse(result.executed)
        self.assertEqual(result.failure_reason, "login_submit_password_changed_before_tap")
        self.assertTrue(result.safe_metadata["autofill_interference_detected"])
        self.assertEqual(login.click_calls, 0)

    def test_empty_or_unproved_username_at_fresh_boundary_never_submits(self) -> None:
        device, _username, _password, login = configured_device(CONNECTED_XML)
        without_username = FILLED_LOGIN_FORM_XML.replace(USERNAME, "")
        device.dump_login_submit_hierarchy = Mock(return_value=without_username)

        result = self._execute(device)

        self.assertFalse(result.executed)
        self.assertEqual(result.failure_reason, "login_submit_username_not_exact")
        self.assertEqual(login.click_calls, 0)

    def test_fix_is_generic_and_contains_no_account_or_clone_condition(self) -> None:
        source = inspect.getsource(executor._resolve_fresh_login_submit_target).lower()
        self.assertNotIn("bmybusinesses", source)
        self.assertNotIn("clone", source)
        self.assertNotIn("account_id", source)


if __name__ == "__main__":
    unittest.main()
