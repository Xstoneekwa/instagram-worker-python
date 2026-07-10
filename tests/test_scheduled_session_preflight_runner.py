"""CP4 preflight runner — package hydration contract (assigned clone package is runtime truth)."""

from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import config
import scheduled_session_preflight_runner as runner


class ScheduledSessionPreflightRunnerPackageHydrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original_package = config.INSTAGRAM_PACKAGE
        self.addCleanup(setattr, config, "INSTAGRAM_PACKAGE", self._original_package)

    def test_runner_hydrates_config_package_before_identity_guard(self) -> None:
        config.INSTAGRAM_PACKAGE = "com.instagram.android"
        seen_package_at_guard: list[str] = []

        def fake_guard(*args, **kwargs):
            seen_package_at_guard.append(str(config.INSTAGRAM_PACKAGE))
            return Mock(
                ok=True,
                actual_logged_in_username="mythyl_fitness",
                verification_method="verify_profile",
                failure_reason="",
            )

        with (
            patch.object(runner, "_connect_device", return_value=Mock()),
            patch.object(runner, "_bring_package_foreground", return_value=True),
            patch.object(runner, "ensure_preflight_device_unlocked", return_value=None),
            patch.object(
                runner,
                "verify_active_instagram_account_matches_expected",
                side_effect=fake_guard,
            ),
            patch.object(runner, "_complete_preflight") as complete,
        ):
            exit_code = runner.run_scheduled_session_preflight(
                account_id="account-1",
                request_id="request-1",
                device_serial="RFGL145LZHE",
                package_name="com.instagram.androif",
                expected_username="mythyl_fitness",
                preflight_id="preflight-1",
                metadata_safe={},
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(config.INSTAGRAM_PACKAGE, "com.instagram.androif")
        self.assertEqual(seen_package_at_guard, ["com.instagram.androif"])
        complete.assert_called_once()
        self.assertEqual(complete.call_args.kwargs["status"], runner.PREFLIGHT_READY)

    def test_runner_hygiene_force_stops_only_expected_package_before_launch(self) -> None:
        config.INSTAGRAM_PACKAGE = "com.instagram.android"
        calls: list[tuple[str, str]] = []

        device = Mock()
        device.app_stop.side_effect = lambda pkg: calls.append(("app_stop", pkg))
        device.press.side_effect = lambda key: calls.append(("press", key))
        device.app_start.side_effect = lambda pkg, stop=False: calls.append(("app_start", pkg))
        device.app_current.return_value = {"package": "com.instagram.androif"}

        def fake_guard(*args, **kwargs):
            calls.append(("identity_guard", str(config.INSTAGRAM_PACKAGE)))
            return Mock(
                ok=True,
                actual_logged_in_username="mythyl_fitness",
                verification_method="verify_profile",
                failure_reason="",
            )

        with (
            patch.object(runner, "_connect_device", return_value=device),
            patch.object(runner, "ensure_preflight_device_unlocked", return_value=None),
            patch.object(
                runner,
                "verify_active_instagram_account_matches_expected",
                side_effect=fake_guard,
            ),
            patch.object(runner, "_complete_preflight") as complete,
        ):
            exit_code = runner.run_scheduled_session_preflight(
                account_id="account-1",
                request_id="request-1",
                device_serial="RFGL145LZHE",
                package_name="com.instagram.androif",
                expected_username="mythyl_fitness",
                preflight_id="preflight-1",
                metadata_safe={},
            )

        self.assertEqual(exit_code, 0)
        # Force-stop scoped to the expected clone only — never other packages.
        device.app_stop.assert_called_once_with("com.instagram.androif")
        device.press.assert_called_once_with("home")
        self.assertEqual(
            calls,
            [
                ("app_stop", "com.instagram.androif"),
                ("press", "home"),
                ("app_start", "com.instagram.androif"),
                ("identity_guard", "com.instagram.androif"),
            ],
        )
        self.assertEqual(config.INSTAGRAM_PACKAGE, "com.instagram.androif")
        self.assertEqual(complete.call_args.kwargs["status"], runner.PREFLIGHT_READY)

    def test_runner_hygiene_force_stop_failure_does_not_abort_launch(self) -> None:
        config.INSTAGRAM_PACKAGE = "com.instagram.android"
        device = Mock()
        device.app_stop.side_effect = RuntimeError("adb force-stop failed")
        device.app_current.return_value = {"package": "com.instagram.androif"}

        def fake_guard(*args, **kwargs):
            return Mock(
                ok=True,
                actual_logged_in_username="mythyl_fitness",
                verification_method="verify_profile",
                failure_reason="",
            )

        with (
            patch.object(runner, "_connect_device", return_value=device),
            patch.object(runner, "ensure_preflight_device_unlocked", return_value=None),
            patch.object(
                runner,
                "verify_active_instagram_account_matches_expected",
                side_effect=fake_guard,
            ),
            patch.object(runner, "_complete_preflight") as complete,
        ):
            exit_code = runner.run_scheduled_session_preflight(
                account_id="account-1",
                request_id="request-1",
                device_serial="RFGL145LZHE",
                package_name="com.instagram.androif",
                expected_username="mythyl_fitness",
                preflight_id="preflight-1",
                metadata_safe={},
            )

        self.assertEqual(exit_code, 0)
        device.app_start.assert_called_once()
        self.assertEqual(complete.call_args.kwargs["status"], runner.PREFLIGHT_READY)

    def test_runner_hygiene_blocks_when_foreground_is_another_package(self) -> None:
        config.INSTAGRAM_PACKAGE = "com.instagram.android"
        device = Mock()
        device.app_current.return_value = {"package": "com.instagram.other"}

        with (
            patch.object(runner, "_connect_device", return_value=device),
            patch.object(runner.time, "sleep"),
            patch.object(
                runner.time,
                "monotonic",
                side_effect=[0.0, 25.0, 50.0],
            ),
            patch.object(runner, "_complete_preflight") as complete,
        ):
            exit_code = runner.run_scheduled_session_preflight(
                account_id="account-1",
                request_id="request-1",
                device_serial="RFGL145LZHE",
                package_name="com.instagram.androif",
                expected_username="mythyl_fitness",
                preflight_id="preflight-1",
                metadata_safe={},
            )

        self.assertEqual(exit_code, 13)
        self.assertEqual(complete.call_args.kwargs["reason_code"], "expected_package_not_foreground")

    def test_runner_does_not_hydrate_package_when_serial_missing(self) -> None:
        config.INSTAGRAM_PACKAGE = "com.instagram.android"
        with patch.object(runner, "_complete_preflight") as complete:
            exit_code = runner.run_scheduled_session_preflight(
                account_id="account-1",
                request_id="request-1",
                device_serial="",
                package_name="com.instagram.androif",
                expected_username="mythyl_fitness",
                preflight_id="preflight-1",
                metadata_safe={},
            )
        self.assertEqual(exit_code, 12)
        self.assertEqual(config.INSTAGRAM_PACKAGE, "com.instagram.android")
        self.assertEqual(complete.call_args.kwargs["reason_code"], "device_serial_missing")

    def test_runner_blocks_when_package_missing_without_hydration(self) -> None:
        config.INSTAGRAM_PACKAGE = "com.instagram.android"
        with (
            patch.object(runner, "_connect_device") as connect,
            patch.object(runner, "_complete_preflight") as complete,
        ):
            exit_code = runner.run_scheduled_session_preflight(
                account_id="account-1",
                request_id="request-1",
                device_serial="RFGL145LZHE",
                package_name="",
                expected_username="mythyl_fitness",
                preflight_id="preflight-1",
                metadata_safe={},
            )
        self.assertEqual(exit_code, 12)
        self.assertEqual(config.INSTAGRAM_PACKAGE, "com.instagram.android")
        self.assertEqual(complete.call_args.kwargs["reason_code"], "expected_package_missing")
        # No Android action at all when the expected package is missing.
        connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
