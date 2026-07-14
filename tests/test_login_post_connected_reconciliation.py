from __future__ import annotations

import unittest
from unittest.mock import Mock

from instagram_credentials_runtime_access import SecretValue
from login_post_connected_reconciliation import run_post_login_connected_reconciliation
from tests.test_instagram_login_email_code_executor import (
    SAVE_LOGIN_INFO_PROMPT_XML,
    SAVE_LOGIN_INFO_WRONG_ACCOUNT_XML,
)
from tests.test_instagram_login_provisioner_orchestrator import CONNECTED_XML, FakeDevice, FakeSelector


class PostLoginConnectedReconciliationTests(unittest.TestCase):
    def test_save_login_identity_confirmed_publishes_connected(self) -> None:
        device = FakeDevice([SAVE_LOGIN_INFO_PROMPT_XML, CONNECTED_XML, CONNECTED_XML])
        not_now = device.add_selector("text", "Not now", FakeSelector(1))
        publisher = Mock(return_value={"published": True})

        result = run_post_login_connected_reconciliation(
            device,
            account_id="871c5836-0fb4-4afb-a5c7-b8bb3fc6b74c",
            expected_username="xstonekwa_backup_acc",
            publisher=publisher,
            publish_enabled=True,
            sleeper=Mock(),
        )

        self.assertTrue(result.ok)
        self.assertTrue(result.save_login_info_prompt_detected)
        self.assertTrue(result.save_login_info_not_now_tapped)
        self.assertTrue(result.published)
        self.assertEqual(result.final_outcome, "connected")
        self.assertEqual(not_now.click_calls, 1)
        publisher.assert_called_once()
        payload = publisher.call_args.kwargs
        self.assertEqual(payload["login_status"], "connected")
        self.assertEqual(payload["provisioning_status"], "ready")

    def test_ambiguous_identity_blocks_without_tap_or_publish(self) -> None:
        device = FakeDevice([SAVE_LOGIN_INFO_WRONG_ACCOUNT_XML])
        publisher = Mock(return_value={"published": True})

        result = run_post_login_connected_reconciliation(
            device,
            account_id="871c5836-0fb4-4afb-a5c7-b8bb3fc6b74c",
            expected_username="xstonekwa_backup_acc",
            publisher=publisher,
            publish_enabled=True,
            sleeper=Mock(),
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "save_login_info_identity_not_confirmed")
        self.assertFalse(result.save_login_info_not_now_tapped)
        self.assertFalse(result.published)
        publisher.assert_not_called()

    def test_already_connected_surface_publishes_without_tap(self) -> None:
        device = FakeDevice([CONNECTED_XML])
        publisher = Mock(return_value={"published": True})

        result = run_post_login_connected_reconciliation(
            device,
            account_id="871c5836-0fb4-4afb-a5c7-b8bb3fc6b74c",
            expected_username="xstonekwa_backup_acc",
            publisher=publisher,
            publish_enabled=True,
            sleeper=Mock(),
        )

        self.assertTrue(result.ok)
        self.assertFalse(result.save_login_info_prompt_detected)
        self.assertTrue(result.published)
        publisher.assert_called_once()


if __name__ == "__main__":
    unittest.main()
