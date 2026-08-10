from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

from instagram_credentials_runtime_access import SecretValue
from instagram_login_email_code_executor import execute_verification_code_challenge_resume
from instagram_login_status_classifier import LoginProbeOutcome
from instagram_login_ui_probe import (
    extract_login_screen_signals_from_hierarchy,
    probe_login_ui_from_hierarchy,
)
from login_challenge_provenance import (
    PROVENANCE_KIND_ACTIVE_RUN,
    evaluate_historical_verification_challenge_provenance,
)
from tests.test_instagram_login_email_code_executor import CONNECTED_XML, FakeDevice


NOW = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
CHANNEL_XML = {
    "email": (
        '<node text="Check your email" />'
        '<node text="Enter the code we sent to m*******e@example.com" />'
        '<node class="android.widget.EditText" text="Enter code" editable="true" />'
        '<node text="Continue" clickable="true" />'
        '<node text="Try another way" />'
    ),
    "sms": (
        '<node text="Check your SMS" />'
        '<node text="Enter the code we sent to +41 ** *** ** 81." />'
        '<node class="android.widget.EditText" text="Enter code" editable="true" />'
        '<node text="Continue" clickable="true" />'
        '<node text="Try another way" />'
    ),
    "whatsapp": (
        '<node text="Check your WhatsApp messages" />'
        '<node text="Enter the code we sent to your WhatsApp account." />'
        '<node class="android.widget.EditText" text="Code" editable="true" />'
        '<node text="Trust this device and skip this step from now on" checkable="true" />'
        '<node text="Continue" clickable="true" />'
        '<node text="Try another way" />'
    ),
    "authenticator_app": (
        '<node text="Go to your authentication app" />'
        '<node text="Enter the 6-digit code for this account from the two-factor authentication app you set up (such as Duo Mobile or Google Authenticator)." />'
        '<node class="android.widget.EditText" text="Code" editable="true" />'
        '<node text="Trust this device and skip this step from now on" checkable="true" />'
        '<node text="Continue" clickable="true" />'
        '<node text="Try another way" />'
    ),
}


class TrackingDevice(FakeDevice):
    def __init__(self, hierarchies: list[str]) -> None:
        super().__init__(hierarchies)
        self.selectors: list[dict[str, str]] = []

    def __call__(self, **selector: str):
        self.selectors.append(dict(selector))
        return super().__call__(**selector)


class LoginVerificationChannelsTest(unittest.TestCase):
    def test_all_channels_share_one_verification_pending_contract(self) -> None:
        screen_types = {
            "email": "email_code_challenge",
            "sms": "sms_code_challenge",
            "whatsapp": "whatsapp_code_challenge",
            "authenticator_app": "authenticator_app_code_challenge",
        }
        for channel, xml in CHANNEL_XML.items():
            with self.subTest(channel=channel):
                result = probe_login_ui_from_hierarchy(xml, stage="post_submit")
                signals = extract_login_screen_signals_from_hierarchy(xml)
                self.assertEqual(result.outcome, LoginProbeOutcome.VERIFICATION_PENDING)
                self.assertEqual(result.reason, "verification_code_required")
                self.assertEqual(result.metadata["verification_channel"], channel)
                self.assertEqual(signals["screen_type"], screen_types[channel])
                self.assertTrue(signals["verification_code_challenge_present"])

    def test_operator_resume_uses_the_same_executor_for_every_channel(self) -> None:
        for channel, xml in CHANNEL_XML.items():
            with self.subTest(channel=channel):
                device = TrackingDevice([xml, CONNECTED_XML])
                result = execute_verification_code_challenge_resume(
                    device,
                    verification_code=SecretValue("123456"),
                    expected_channel=channel,
                    post_submit_wait_ms=1,
                    post_submit_observation_interval_ms=1,
                    max_post_submit_observations=1,
                    sleeper=Mock(),
                )
                self.assertTrue(result.executed)
                self.assertTrue(result.code_entered)
                self.assertEqual(result.safe_metadata["verification_channel"], channel)
                selector_text = " ".join(str(item) for item in device.selectors).lower()
                self.assertNotIn("trust this device", selector_text)
                self.assertNotIn("try another way", selector_text)

    def test_wrong_channel_is_rejected_before_any_code_or_continue_tap(self) -> None:
        device = TrackingDevice([CHANNEL_XML["sms"]])
        result = execute_verification_code_challenge_resume(
            device,
            verification_code=SecretValue("123456"),
            expected_channel="whatsapp",
            sleeper=Mock(),
        )
        self.assertFalse(result.ok)
        self.assertFalse(result.executed)
        self.assertEqual(result.failure_reason, "verification_channel_mismatch")
        self.assertFalse(device.code_target.clicked)
        self.assertFalse(device.continue_target.clicked)

    def test_unrelated_code_input_is_not_accepted(self) -> None:
        xml = (
            '<node text="Discount code" />'
            '<node class="android.widget.EditText" text="Enter code" editable="true" />'
            '<node text="Continue" clickable="true" />'
        )
        result = probe_login_ui_from_hierarchy(xml)
        signals = extract_login_screen_signals_from_hierarchy(xml)
        self.assertNotEqual(result.outcome, LoginProbeOutcome.VERIFICATION_PENDING)
        self.assertFalse(signals["verification_code_challenge_present"])

    def test_expired_challenge_remains_fail_closed_and_channel_explicit(self) -> None:
        xml = CHANNEL_XML["sms"] + '<node text="Code expired. Get a new code." />'
        result = probe_login_ui_from_hierarchy(xml)
        self.assertEqual(result.outcome, LoginProbeOutcome.VERIFICATION_PENDING)
        self.assertEqual(result.metadata["verification_channel"], "sms")
        self.assertTrue(result.metadata["verification_code_expired"])

    def test_historical_resume_requires_the_same_persisted_channel_and_lineage(self) -> None:
        action = {
            "account_id": "account-1",
            "action_type": "enter_email_verification_code",
            "status": "pending_verification",
            "created_at": (NOW - timedelta(minutes=1)).isoformat(),
            "metadata": {
                "provenance_kind": PROVENANCE_KIND_ACTIVE_RUN,
                "stage": "post_submit",
                "run_id": "run-1",
                "expected_app_instance_id": "clone-1",
                "assignment_id": "assignment-1",
                "credentials_version": 1,
                "verification_channel": "whatsapp",
            },
        }
        accepted = evaluate_historical_verification_challenge_provenance(
            action_row=action,
            account_id="account-1",
            run_id="run-1",
            expected_app_instance_id="clone-1",
            assignment_id="assignment-1",
            credentials_version=1,
            expected_channel="whatsapp",
            now=NOW,
        )
        mismatch = evaluate_historical_verification_challenge_provenance(
            action_row=action,
            account_id="account-1",
            run_id="run-1",
            expected_app_instance_id="clone-1",
            assignment_id="assignment-1",
            credentials_version=1,
            expected_channel="sms",
            now=NOW,
        )
        self.assertTrue(accepted.accepted)
        self.assertFalse(mismatch.accepted)
        self.assertEqual(mismatch.reason, "historical_verification_channel_mismatch")


if __name__ == "__main__":
    unittest.main()
