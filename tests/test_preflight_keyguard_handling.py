from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import account_identity_guard as guard
import runner
import scheduled_session_preflight_runner as preflight_runner

KEYGUARD_SWIPE_XML = (
    "<hierarchy>"
    '<node package="com.android.systemui" resource-id="com.android.systemui:id/keyguard_indication_text" text="Swipe to open" />'
    '<node package="com.android.systemui" resource-id="com.samsung.android.app.clockpack:id/keyguard_status_view" />'
    "</hierarchy>"
)
KEYGUARD_SECURE_XML = (
    "<hierarchy>"
    '<node package="com.android.systemui" resource-id="com.android.systemui:id/keyguard_indication_text" text="Enter PIN" />'
    '<node package="com.android.systemui" resource-id="com.android.systemui:id/pin_view" text="Enter PIN" />'
    "</hierarchy>"
)
HOME_XML = (
    '<node text="Instagram" />'
    '<node text="Your story" />'
    '<node content-desc="Profile" />'
)
PROFILE_USERNAME_XML = (
    '<node resource-id="com.instagram.android:id/action_bar_title" text="mythyl_fitness" />'
)
LOGIN_XML = (
    '<node text="Log in to Instagram" />'
    '<node text="Username" />'
    '<node text="Password" />'
)
CHECKPOINT_XML = (
    '<node text="Help us confirm it’s you" />'
    '<node text="Verify your account" />'
)


class PreflightKeyguardHandlingTests(unittest.TestCase):
    def test_systemui_swipe_xml_detects_device_keyguard(self) -> None:
        state = guard.classify_keyguard_from_hierarchy(
            KEYGUARD_SWIPE_XML,
            foreground_package="com.android.systemui",
        )
        self.assertTrue(state["keyguard_active"])
        self.assertTrue(state["swipe_only_unlock_available"])
        self.assertFalse(state["secure_lock_required"])

    def test_swipe_only_keyguard_attempts_single_swipe_then_continues(self) -> None:
        device = Mock()
        device.app_current.side_effect = [
            {"package": "com.android.systemui"},
            {"package": "com.instagram.androif"},
        ]

        with (
            patch.object(guard, "_dump_hierarchy", side_effect=[KEYGUARD_SWIPE_XML, HOME_XML]),
            patch.object(guard, "_wake_device_for_preflight"),
            patch.object(guard, "_swipe_up_keyguard_once") as swipe,
            patch("account_identity_guard.time.sleep") as sleep,
        ):
            result = guard.ensure_preflight_device_unlocked(
                device,
                account_id="0d299d1e-46ee-49d2-8a84-4f928f2bb182",
                run_id="request-1",
            )

        self.assertIsNone(result)
        swipe.assert_called_once()
        sleep.assert_called_once_with(guard._KEYGUARD_SWIPE_SETTLE_SECONDS)

    def test_secure_pin_keyguard_never_bypasses(self) -> None:
        device = Mock()
        device.app_current.return_value = {"package": "com.android.systemui"}

        with (
            patch.object(guard, "_dump_hierarchy", return_value=KEYGUARD_SECURE_XML),
            patch.object(guard, "_wake_device_for_preflight"),
            patch.object(guard, "_swipe_up_keyguard_once") as swipe,
            patch.object(
                guard,
                "_capture_identity_failure_artifacts",
                return_value={"screenshot_captured": True, "xml_dump_captured": True},
            ),
        ):
            result = guard.ensure_preflight_device_unlocked(
                device,
                account_id="0d299d1e-46ee-49d2-8a84-4f928f2bb182",
                run_id="request-1",
            )

        self.assertIsNotNone(result)
        self.assertEqual(result.failure_reason, guard.DEVICE_LOCKED_REQUIRES_OPERATOR_REASON)
        self.assertEqual(result.meta["screen_type"], guard.KEYGUARD_SCREEN_TYPE)
        self.assertEqual(result.meta["detection_reason"], guard.KEYGUARD_DETECTION_REASON)
        self.assertEqual(result.meta["identity_guard_stage"], guard.PREFLIGHT_KEYGUARD_STAGE)
        self.assertFalse(result.meta["unlock_attempted"])
        self.assertEqual(result.meta["unlock_result"], "secure_lock_required")
        swipe.assert_not_called()

    def test_persistent_keyguard_after_swipe_returns_device_unlock_failed(self) -> None:
        device = Mock()
        device.app_current.return_value = {"package": "com.android.systemui"}

        with (
            patch.object(guard, "_dump_hierarchy", return_value=KEYGUARD_SWIPE_XML),
            patch.object(guard, "_wake_device_for_preflight"),
            patch.object(guard, "_swipe_up_keyguard_once"),
            patch("account_identity_guard.time.sleep"),
            patch.object(
                guard,
                "_capture_identity_failure_artifacts",
                return_value={"screenshot_captured": True, "xml_dump_captured": True},
            ),
        ):
            result = guard.ensure_preflight_device_unlocked(
                device,
                account_id="0d299d1e-46ee-49d2-8a84-4f928f2bb182",
                run_id="request-1",
            )

        self.assertIsNotNone(result)
        self.assertEqual(result.failure_reason, guard.DEVICE_UNLOCK_FAILED_REASON)
        self.assertTrue(result.meta["unlock_attempted"])
        self.assertEqual(result.meta["unlock_result"], "failed")

    def test_main_runner_unlocks_and_restores_exact_clone_before_identity(self) -> None:
        device = Mock()
        device.app_current.side_effect = [
            {"package": "com.android.systemui"},
            {"package": "com.android.systemui"},
            {"package": "com.instagram.clone.exact"},
            {"package": "com.instagram.clone.exact"},
        ]
        with (
            patch.object(runner, "app_start") as start,
            patch.object(runner, "verify_app_foreground", return_value=True) as foreground,
            patch.object(guard, "ensure_preflight_device_unlocked", return_value=None),
            patch("runner.time.sleep"),
        ):
            result = runner._prepare_device_for_account_identity_preflight(
                device,
                expected_package="com.instagram.clone.exact",
                expected_account_username="generic_account",
                account_id="account-1",
                run_id="run-1",
            )
        self.assertIsNone(result)
        start.assert_called_once_with(device, "com.instagram.clone.exact")
        foreground.assert_called_once_with(device, "com.instagram.clone.exact")

    def test_main_runner_blocks_before_package_restore_when_unlock_fails(self) -> None:
        device = Mock()
        keyguard_block = guard.AccountIdentityCheckResult(
            ok=False,
            expected_account_username="",
            failure_reason=guard.DEVICE_UNLOCK_FAILED_REASON,
            verification_method="preflight_keyguard_check",
        )
        with (
            patch.object(runner, "app_start") as start,
            patch.object(runner, "verify_app_foreground") as foreground,
            patch.object(guard, "ensure_preflight_device_unlocked", return_value=keyguard_block),
        ):
            result = runner._prepare_device_for_account_identity_preflight(
                device,
                expected_package="com.instagram.clone.exact",
                expected_account_username="generic_account",
                account_id="account-1",
                run_id="run-1",
            )
        self.assertIs(result, keyguard_block)
        start.assert_not_called()
        foreground.assert_not_called()

    def test_main_runner_never_accepts_wrong_clone_after_unlock(self) -> None:
        device = Mock()
        device.app_current.side_effect = [
            {"package": "com.instagram.other"},
            {"package": "com.instagram.clone.exact"},
            {"package": "com.instagram.other"},
            {"package": "com.instagram.other"},
        ]
        with (
            patch.object(runner, "app_start"),
            patch.object(runner, "verify_app_foreground") as foreground,
            patch.object(guard, "ensure_preflight_device_unlocked", return_value=None),
            patch("runner.time.monotonic", return_value=10.0),
        ):
            result = runner._prepare_device_for_account_identity_preflight(
                device,
                expected_package="com.instagram.clone.exact",
                expected_account_username="generic_account",
                account_id="account-1",
                run_id="run-1",
            )
        self.assertEqual(
            result.failure_reason,
            "expected_instagram_package_not_foreground_after_unlock",
        )
        foreground.assert_not_called()

    def test_preflight_runner_blocks_on_keyguard_before_identity_guard(self) -> None:
        keyguard_block = guard.AccountIdentityCheckResult(
            ok=False,
            expected_account_username="mythyl_fitness",
            failure_reason=guard.DEVICE_LOCKED_REASON,
            verification_method="preflight_keyguard_check",
            meta={
                "screen_type": guard.KEYGUARD_SCREEN_TYPE,
                "detection_reason": guard.KEYGUARD_DETECTION_REASON,
                "identity_guard_stage": guard.PREFLIGHT_KEYGUARD_STAGE,
                "unlock_attempted": True,
                "unlock_result": "failed",
                "screenshot_captured": True,
                "xml_dump_captured": True,
            },
        )
        with (
            patch.object(preflight_runner, "_connect_device", return_value=Mock()),
            patch.object(preflight_runner, "_bring_package_foreground", return_value=True),
            patch.object(preflight_runner, "ensure_preflight_device_unlocked", return_value=keyguard_block),
            patch.object(preflight_runner, "verify_active_instagram_account_matches_expected") as identity_guard,
            patch.object(preflight_runner, "_complete_preflight") as complete,
        ):
            exit_code = preflight_runner.run_scheduled_session_preflight(
                account_id="0d299d1e-46ee-49d2-8a84-4f928f2bb182",
                request_id="request-1",
                device_serial="RFGL145LZHE",
                package_name="com.instagram.androif",
                expected_username="mythyl_fitness",
                preflight_id="preflight-1",
                metadata_safe={},
            )

        self.assertEqual(exit_code, 75)
        identity_guard.assert_not_called()
        self.assertEqual(complete.call_args.kwargs["reason_code"], guard.DEVICE_LOCKED_REASON)
        self.assertEqual(complete.call_args.kwargs["metadata"]["screen_type"], guard.KEYGUARD_SCREEN_TYPE)

    def test_keyguard_xml_no_longer_maps_to_username_not_detected_in_preflight_guard(self) -> None:
        device = Mock()
        device.app_current.return_value = {"package": "com.android.systemui"}
        with (
            patch.object(guard, "_dump_hierarchy", side_effect=[HOME_XML, KEYGUARD_SWIPE_XML]),
            patch.object(guard, "open_own_profile_from_bottom_nav", return_value=True),
            patch.object(guard, "_device_foreground_package", return_value="com.android.systemui"),
            patch.object(
                guard,
                "_extract_own_profile_username_from_hierarchy",
                return_value=("", "own_profile_username_not_found", {"hierarchy_xml_len": 100}),
            ),
            patch.object(
                guard,
                "_capture_identity_failure_artifacts",
                return_value={"screenshot_captured": True, "xml_dump_captured": True},
            ),
        ):
            result = guard.verify_active_instagram_account_matches_expected(
                device,
                expected_account_username="mythyl_fitness",
                account_id="0d299d1e-46ee-49d2-8a84-4f928f2bb182",
                run_type="scheduled_session_preflight",
                run_id="request-1",
                stage="scheduled_session_preflight_identity",
            )

        self.assertFalse(result.ok)
        self.assertEqual(result.failure_reason, guard.DEVICE_LOCKED_REASON)
        self.assertEqual(result.meta["screen_type"], guard.KEYGUARD_SCREEN_TYPE)
        self.assertNotEqual(result.failure_reason, "actual_logged_in_username_not_detected")

    def test_login_screen_still_login_screen_detected(self) -> None:
        device = Mock()
        with (
            patch.object(guard, "_dump_hierarchy", return_value=LOGIN_XML),
            patch.object(guard, "open_own_profile_from_bottom_nav") as open_profile,
            patch.object(
                guard,
                "_capture_identity_failure_artifacts",
                return_value={"screenshot_captured": True, "xml_dump_captured": True},
            ),
        ):
            result = guard.verify_active_instagram_account_matches_expected(
                device,
                expected_account_username="mythyl_fitness",
                run_type="scheduled_session_preflight",
            )

        self.assertEqual(result.failure_reason, "login_screen_detected")
        open_profile.assert_not_called()

    def test_checkpoint_still_not_bypassed(self) -> None:
        device = Mock()
        with (
            patch.object(guard, "_dump_hierarchy", return_value=CHECKPOINT_XML),
            patch.object(guard, "open_own_profile_from_bottom_nav") as open_profile,
            patch.object(
                guard,
                "_capture_identity_failure_artifacts",
                return_value={"screenshot_captured": True, "xml_dump_captured": True},
            ),
        ):
            result = guard.verify_active_instagram_account_matches_expected(
                device,
                expected_account_username="mythyl_fitness",
                run_type="scheduled_session_preflight",
            )

        self.assertEqual(result.failure_reason, "checkpoint")
        open_profile.assert_not_called()


if __name__ == "__main__":
    unittest.main()
