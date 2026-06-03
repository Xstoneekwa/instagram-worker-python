from __future__ import annotations

import unittest
from unittest.mock import Mock

from instagram_credentials_runtime_access import SecretValue
from instagram_login_email_code_executor import execute_email_code_challenge_resume

EMAIL_CODE_CHALLENGE_XML = (
    '<node text="Check your email" />'
    '<node text="Enter the code we sent to m*******e@hotmail.com" />'
    '<node class="android.widget.EditText" text="Enter code" editable="true" />'
    '<node text="Continue" clickable="true" />'
    '<node text="Try another way" />'
)
CONNECTED_XML = (
    '<node content-desc="Home" />'
    '<node content-desc="Search" />'
    '<node content-desc="Reels" />'
    '<node content-desc="Profile" />'
)


class FakeTarget:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.clicked = False
        self.value = ""

    def exists(self, timeout: float = 0) -> bool:
        return True

    def click(self) -> None:
        self.clicked = True

    def clear_text(self) -> None:
        self.value = ""

    def set_text(self, value: str) -> None:
        self.value = value
        self.text = value

    @property
    def info(self) -> dict[str, str]:
        return {"text": self.text}


class FakeDevice:
    def __init__(self, hierarchies: list[str]) -> None:
        self.hierarchies = list(hierarchies)
        self.code_target = FakeTarget("Enter code")
        self.continue_target = FakeTarget("Continue")

    def dump_hierarchy(self, compressed: bool = False) -> str:
        return self.hierarchies.pop(0) if self.hierarchies else CONNECTED_XML

    def __call__(self, **selector: str) -> FakeTarget:
        if selector.get("text") == "Enter code" or selector.get("className") == "android.widget.EditText":
            return self.code_target
        if selector.get("text") == "Continue":
            return self.continue_target
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
