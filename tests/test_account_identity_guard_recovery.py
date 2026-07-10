from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import account_identity_guard as guard
import scheduled_session_preflight_runner as preflight_runner


LOGIN_XML = (
    '<node text="Log in to Instagram" />'
    '<node text="Username" />'
    '<node text="Password" />'
)
CHECKPOINT_XML = (
    '<node text="Help us confirm it’s you" />'
    '<node text="Verify your account" />'
)
CHALLENGE_2FA_XML = '<node text="Enter code" /><node text="authentication code" />'
HOME_XML = (
    '<node text="Instagram" />'
    '<node text="Your story" />'
    '<node text="Suggested for you" />'
    '<node content-desc="Profile" />'
)
LOADING_XML = '<node text="Loading..." />'
PROFILE_USERNAME_XML = (
    '<node resource-id="com.instagram.android:id/action_bar_title" text="cinema_catchup" />'
)


class AccountIdentityGuardRecoveryTest(unittest.TestCase):
    def _verify(self, device: object, *, expected: str = "cinema_catchup") -> guard.AccountIdentityCheckResult:
        return guard.verify_active_instagram_account_matches_expected(
            device,
            expected_account_username=expected,
            account_id="42c625c2-e761-4100-8a9d-7ae1373de97d",
            run_type="scheduled_session_preflight",
            run_id="preflight-run-1",
            stage="scheduled_session_preflight_identity",
        )

    def test_login_screen_before_profile_tap_returns_clear_reason(self) -> None:
        device = Mock()
        with (
            patch.object(guard, "_dump_hierarchy", return_value=LOGIN_XML),
            patch.object(guard, "open_own_profile_from_bottom_nav") as open_profile,
            patch.object(guard, "_capture_identity_failure_artifacts", return_value={"screenshot_captured": True, "xml_dump_captured": True}),
        ):
            result = self._verify(device)

        self.assertFalse(result.ok)
        self.assertEqual(result.failure_reason, "login_screen_detected")
        self.assertEqual(result.meta.get("detection_reason"), "login_screen_signal")
        self.assertEqual(result.meta.get("identity_guard_stage"), "pre_profile_screen")
        open_profile.assert_not_called()

    def test_checkpoint_before_profile_tap_is_not_bypassed(self) -> None:
        device = Mock()
        with (
            patch.object(guard, "_dump_hierarchy", return_value=CHECKPOINT_XML),
            patch.object(guard, "open_own_profile_from_bottom_nav") as open_profile,
            patch.object(guard, "_capture_identity_failure_artifacts", return_value={"screenshot_captured": True, "xml_dump_captured": True}),
        ):
            result = self._verify(device)

        self.assertFalse(result.ok)
        self.assertEqual(result.failure_reason, "checkpoint")
        self.assertEqual(result.meta.get("detection_reason"), "checkpoint_signal")
        open_profile.assert_not_called()

    def test_login_challenge_before_profile_tap_returns_clear_reason(self) -> None:
        device = Mock()
        with (
            patch.object(guard, "_dump_hierarchy", return_value=CHALLENGE_2FA_XML),
            patch.object(guard, "open_own_profile_from_bottom_nav") as open_profile,
            patch.object(guard, "_capture_identity_failure_artifacts", return_value={"screenshot_captured": True, "xml_dump_captured": True}),
        ):
            result = self._verify(device)

        self.assertFalse(result.ok)
        self.assertEqual(result.failure_reason, "login_challenge")
        self.assertEqual(result.meta.get("detection_reason"), "needs_2fa")
        open_profile.assert_not_called()

    def test_transition_loading_retries_once_then_continues_on_home(self) -> None:
        device = Mock()
        hierarchies = [LOADING_XML, HOME_XML, PROFILE_USERNAME_XML]
        with (
            patch.object(guard, "_dump_hierarchy", side_effect=hierarchies),
            patch("account_identity_guard.time.sleep") as sleep,
            patch.object(guard, "open_own_profile_from_bottom_nav", return_value=True) as open_profile,
            patch.object(
                guard,
                "_extract_own_profile_username_from_hierarchy",
                return_value=("cinema_catchup", "action_bar_title", {"hierarchy_xml_len": 100}),
            ),
        ):
            result = self._verify(device)

        self.assertTrue(result.ok)
        sleep.assert_called_once_with(guard._LOADING_RETRY_SECONDS)
        open_profile.assert_called_once()

    def test_transition_loading_persists_after_single_retry(self) -> None:
        device = Mock()
        with (
            patch.object(guard, "_dump_hierarchy", return_value=LOADING_XML),
            patch("account_identity_guard.time.sleep") as sleep,
            patch.object(guard, "open_own_profile_from_bottom_nav") as open_profile,
            patch.object(guard, "_capture_identity_failure_artifacts", return_value={"screenshot_captured": True, "xml_dump_captured": True}),
        ):
            result = self._verify(device)

        self.assertFalse(result.ok)
        self.assertEqual(result.failure_reason, "ui_transition_loading")
        self.assertTrue(result.meta.get("loading_retry_used"))
        self.assertEqual(result.meta.get("identity_guard_stage"), "pre_profile_screen_loading_retry")
        sleep.assert_called_once_with(guard._LOADING_RETRY_SECONDS)
        open_profile.assert_not_called()

    def test_active_home_screen_continues_to_open_own_profile(self) -> None:
        device = Mock()
        with (
            patch.object(guard, "_dump_hierarchy", side_effect=[HOME_XML, PROFILE_USERNAME_XML]),
            patch.object(guard, "open_own_profile_from_bottom_nav", return_value=True) as open_profile,
            patch.object(
                guard,
                "_extract_own_profile_username_from_hierarchy",
                return_value=("cinema_catchup", "action_bar_title", {"hierarchy_xml_len": 100}),
            ),
        ):
            result = self._verify(device)

        self.assertTrue(result.ok)
        open_profile.assert_called_once()

    def test_identity_failure_captures_screenshot_and_xml(self) -> None:
        device = Mock()
        device.screenshot = Mock()
        with (
            patch.object(guard, "_dump_hierarchy", return_value=LOGIN_XML),
            patch.object(guard, "open_own_profile_from_bottom_nav") as open_profile,
            patch.object(guard, "_IDENTITY_GUARD_ARTIFACTS_DIR", guard._LOGS_ROOT / "identity_guard_test"),
        ):
            result = self._verify(device)

        self.assertFalse(result.ok)
        self.assertTrue(result.meta.get("screenshot_captured"))
        self.assertTrue(result.meta.get("xml_dump_captured"))
        device.screenshot.assert_called_once()
        open_profile.assert_not_called()

    def test_own_profile_open_failed_still_non_retryable_with_artifacts(self) -> None:
        device = Mock()
        with (
            patch.object(guard, "_dump_hierarchy", side_effect=[HOME_XML, HOME_XML]),
            patch.object(guard, "open_own_profile_from_bottom_nav", return_value=False),
            patch.object(guard, "_capture_identity_failure_artifacts", return_value={"screenshot_captured": True, "xml_dump_captured": True}),
        ):
            result = self._verify(device)

        self.assertFalse(result.ok)
        self.assertEqual(result.failure_reason, "own_profile_open_failed")
        self.assertEqual(result.meta.get("screen_type"), "own_profile_open_failed")

    def test_preflight_runner_propagates_identity_screen_metadata(self) -> None:
        identity = Mock(
            ok=False,
            failure_reason="login_screen_detected",
            actual_logged_in_username="",
            verification_method="pre_profile_screen_classification",
            meta={
                "screen_type": "login_form_empty",
                "detection_reason": "login_form_empty",
                "identity_guard_stage": "pre_profile_screen",
                "hierarchy_xml_len": 321,
                "screenshot_captured": True,
                "xml_dump_captured": True,
                "screenshot_basename": "identity_guard_local.png",
                "xml_basename": "identity_guard_local.xml",
            },
        )
        with (
            patch.object(preflight_runner, "_connect_device", return_value=Mock()),
            patch.object(preflight_runner, "_bring_package_foreground", return_value=True),
            patch.object(preflight_runner, "verify_active_instagram_account_matches_expected", return_value=identity),
            patch.object(preflight_runner, "_complete_preflight") as complete,
        ):
            exit_code = preflight_runner.run_scheduled_session_preflight(
                account_id="account-1",
                request_id="request-1",
                device_serial="RFGL145LZHE",
                package_name="com.instagram.androif",
                expected_username="mythyl_fitness",
                preflight_id="preflight-1",
                metadata_safe={},
            )

        self.assertEqual(exit_code, 75)
        metadata = complete.call_args.kwargs["metadata"]
        self.assertEqual(complete.call_args.kwargs["reason_code"], "login_screen_detected")
        self.assertEqual(metadata["screen_type"], "login_form_empty")
        self.assertEqual(metadata["detection_reason"], "login_form_empty")
        self.assertEqual(metadata["identity_guard_stage"], "pre_profile_screen")
        self.assertEqual(metadata["expected_account_username"], "mythyl_fitness")
        self.assertNotIn("screenshot_basename", metadata)
        self.assertNotIn("xml_basename", metadata)


if __name__ == "__main__":
    unittest.main()
