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
    '<node text="Not now" clickable="true" />'
)
CONNECTED_XML = (
    '<node content-desc="Home" />'
    '<node content-desc="Search" />'
    '<node content-desc="Reels" />'
    '<node content-desc="Profile" />'
)


class FakeTarget:
    def __init__(self, text: str = "", *, set_text_updates: bool = True) -> None:
        self.text = text
        self.clicked = False
        self.value = ""
        self.set_text_updates = set_text_updates

    def exists(self, timeout: float = 0) -> bool:
        return True

    def click(self) -> None:
        self.clicked = True

    def clear_text(self) -> None:
        self.value = ""

    def set_text(self, value: str) -> None:
        self.value = value
        if self.set_text_updates:
            self.text = value

    @property
    def info(self) -> dict[str, str]:
        return {"text": self.text}


class FakeDevice:
    def __init__(self, hierarchies: list[str], *, set_text_updates: bool = True) -> None:
        self.hierarchies = list(hierarchies)
        self.serial = "RFGL145VCKE"
        self.code_target = FakeTarget("Enter code", set_text_updates=set_text_updates)
        self.continue_target = FakeTarget("Continue")
        self.not_now_target = FakeTarget("Not now")

    def dump_hierarchy(self, compressed: bool = False) -> str:
        return self.hierarchies.pop(0) if self.hierarchies else CONNECTED_XML

    def __call__(self, **selector: str) -> FakeTarget:
        if selector.get("text") == "Enter code" or selector.get("className") == "android.widget.EditText":
            return self.code_target
        if selector.get("text") == "Continue":
            return self.continue_target
        if selector.get("text") == "Not now":
            return self.not_now_target
        return FakeTarget()


class EmailCodeExecutorTests(unittest.TestCase):
    def test_resume_requires_email_code_screen(self) -> None:
        device = FakeDevice(['<node text="Log in to Instagram" /><node text="Password" />'])
        result = execute_email_code_challenge_resume(
            device,
            verification_code=SecretValue("123456"),
            sleeper=Mock(),
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.failure_reason, "email_code_challenge_screen_required")

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

    def test_resume_uses_adb_keyboard_fallback_when_set_text_not_confirmed(self) -> None:
        device = FakeDevice(
            [EMAIL_CODE_CHALLENGE_XML, EMAIL_CODE_CHALLENGE_XML, EMAIL_CODE_FILLED_XML, CONNECTED_XML],
            set_text_updates=False,
        )

        with (
            patch.object(email_code_executor, "adb_available", return_value=True),
            patch.object(email_code_executor, "is_fast_ime_available", return_value=True),
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
