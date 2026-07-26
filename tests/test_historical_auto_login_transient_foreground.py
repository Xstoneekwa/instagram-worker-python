from __future__ import annotations

import unittest

from historical_auto_login_07ee import instagram_login_provisioner_orchestrator as orchestrator


class _Device:
    def __init__(self, packages: list[str]) -> None:
        self.packages = list(packages)
        self.index = 0
        self.presses: list[str] = []

    def app_current(self) -> dict[str, str]:
        package = self.packages[min(self.index, len(self.packages) - 1)]
        self.index += 1
        return {"package": package}

    def press(self, key: str) -> None:
        self.presses.append(key)


class HistoricalAutoLoginTransientForegroundTest(unittest.TestCase):
    def test_known_credential_overlay_is_dismissed_then_expected_clone_is_accepted(self) -> None:
        device = _Device(["com.android.credentialmanager", "com.instagram.androig"])
        result = orchestrator._guard_foreground_package_for_login_input(
            device,
            expected_package_name="com.instagram.androig",
            timer=lambda: 0.0,
            sleeper=lambda _seconds: None,
        )

        self.assertFalse(result["package_guard_mismatch"])
        self.assertTrue(result["transient_foreground_recovery_attempted"])
        self.assertTrue(result["transient_foreground_recovery_succeeded"])
        self.assertEqual(device.presses, ["back"])

    def test_unknown_wrong_package_remains_a_strict_mismatch(self) -> None:
        device = _Device(["com.example.wrong"])
        result = orchestrator._guard_foreground_package_for_login_input(
            device,
            expected_package_name="com.instagram.androig",
            timer=lambda: 0.0,
            sleeper=lambda _seconds: None,
        )

        self.assertTrue(result["package_guard_mismatch"])
        self.assertFalse(result["transient_foreground_recovery_attempted"])
        self.assertEqual(device.presses, [])

    def test_persistent_credential_overlay_is_bounded_and_still_blocks(self) -> None:
        device = _Device(["com.android.credentialmanager"] * 3)
        result = orchestrator._guard_foreground_package_for_login_input(
            device,
            expected_package_name="com.instagram.androig",
            timer=lambda: 0.0,
            sleeper=lambda _seconds: None,
        )

        self.assertTrue(result["package_guard_mismatch"])
        self.assertEqual(result["transient_foreground_recovery_count"], 2)
        self.assertEqual(device.presses, ["back", "back"])


if __name__ == "__main__":
    unittest.main()
