import unittest
from unittest.mock import patch

import unfollow_profile_probe as probe


class _Device:
    def __init__(self) -> None:
        self.clicks = []

    def click(self, x, y) -> None:
        self.clicks.append((x, y))


class UnfollowProfileInitialCtaRetryTests(unittest.TestCase):
    def test_transient_missing_following_cta_revalidates_exact_profile_then_retries(self) -> None:
        button = {
            "ok": True,
            "bounds": {"left": 10, "top": 20, "right": 110, "bottom": 70},
            "tap_x": 60,
            "tap_y": 45,
            "resource_id": "profile_header_following_button",
            "clickable": True,
            "detection_method": "resource_id",
        }
        device = _Device()
        with patch.object(
            probe,
            "detect_profile_following_button_for_unfollow",
            side_effect=[{"ok": False, "failure_reason": "following_button_not_found"}, button, button],
        ) as detector, patch.object(
            probe,
            "verify_unfollow_target_profile_strict",
            return_value={"ok": True},
        ) as identity, patch.object(
            probe,
            "_detect_actions_sheet_signals",
            return_value={"sheet_context": True, "unfollow_visible": True, "unfollow_text": "Unfollow"},
        ), patch.object(probe.time, "sleep"):
            result = probe.open_unfollow_actions_sheet_from_profile_probe(
                device,
                expected_target_username="angiedakouo",
            )
        self.assertTrue(result["ok"])
        self.assertEqual(detector.call_count, 3)
        identity.assert_called_once()
        self.assertEqual(device.clicks, [(60, 45)])

    def test_retry_never_taps_when_exact_profile_cannot_be_revalidated(self) -> None:
        device = _Device()
        with patch.object(
            probe,
            "detect_profile_following_button_for_unfollow",
            return_value={"ok": False, "failure_reason": "following_button_not_found"},
        ) as detector, patch.object(
            probe,
            "verify_unfollow_target_profile_strict",
            return_value={"ok": False, "failure_reason": "target_profile_identity_mismatch"},
        ), patch.object(probe.time, "sleep"):
            result = probe.open_unfollow_actions_sheet_from_profile_probe(
                device,
                expected_target_username="angiedakouo",
            )
        self.assertFalse(result["ok"])
        self.assertEqual(result["failure_reason"], "following_button_not_found")
        self.assertEqual(detector.call_count, 1)
        self.assertEqual(device.clicks, [])


if __name__ == "__main__":
    unittest.main()
