from __future__ import annotations

import unittest
from unittest.mock import patch

import unfollow_profile_probe as probe


def _private_modal_xml(*, username: str = "marina.pavln", include_context: bool = True) -> str:
    context = (
        f"If you change your mind, you'll have to request to follow {username} again."
        if include_context
        else "Confirm action"
    )
    return f"""
    <hierarchy>
      <node resource-id="com.instagram.android:id/action_bar_title" text="{username}" bounds="[120,110][650,190]" />
      <node text="{context}" bounds="[120,850][960,1120]" />
      <node class="android.widget.Button" clickable="true" text="Unfollow" bounds="[80,1240][1000,1390]" />
      <node class="android.widget.Button" clickable="true" text="Cancel" bounds="[80,1390][1000,1540]" />
    </hierarchy>
    """


def _unsafe_private_modal_xml(*, username: str = "marina.pavln") -> str:
    return _private_modal_xml(username=username).replace(
        "</hierarchy>",
        '<node text="Verify your identity" bounds="[120,700][960,820]" /></hierarchy>',
    )


def _private_modal_xml_without_profile_title(
    *, username: str = "marina.pavln"
) -> str:
    """Mirror builds where the dialog replaces the profile action-bar title."""
    return _private_modal_xml(username=username).replace(
        f'<node resource-id="com.instagram.android:id/action_bar_title" text="{username}" bounds="[120,110][650,190]" />',
        '<node resource-id="com.instagram.android:id/dialog_title" text="Unfollow" bounds="[120,110][650,190]" />',
    )


class _Element:
    def __init__(self, exists: bool, *, bounds=None) -> None:
        self._exists = exists
        self.info = {"bounds": dict(bounds or {})}

    def exists(self, timeout=0):
        del timeout
        return self._exists


class _PrivateModalDevice:
    def __init__(self, *, username: str = "marina.pavln") -> None:
        self.username = username
        self.modal_open = True
        self.clicks: list[tuple[int, int]] = []

    def window_size(self):
        return 1080, 2400

    def dump_hierarchy(self, compressed=False):
        del compressed
        if self.modal_open:
            return _private_modal_xml(username=self.username)
        return (
            '<hierarchy>'
            f'<node resource-id="com.instagram.android:id/action_bar_title" text="{self.username}" bounds="[120,110][650,190]" />'
            '<node class="android.widget.Button" clickable="true" text="Follow" bounds="[80,600][520,700]" />'
            '</hierarchy>'
        )

    def __call__(self, **selector):
        label = str(selector.get("text") or "")
        if self.modal_open:
            return _Element(
                label == "Unfollow",
                bounds={"left": 80, "top": 1240, "right": 1000, "bottom": 1390},
            )
        return _Element(label == "Follow")

    def click(self, x, y):
        self.clicks.append((int(x), int(y)))
        self.modal_open = False


class _StuckPrivateModalDevice(_PrivateModalDevice):
    def click(self, x, y):
        self.clicks.append((int(x), int(y)))


class _PrivateModalWithoutActionBarDevice(_PrivateModalDevice):
    def dump_hierarchy(self, compressed=False):
        del compressed
        if self.modal_open:
            return _private_modal_xml_without_profile_title(username=self.username)
        return super().dump_hierarchy(compressed=False)


class _DisappearingPrivateModalDevice(_PrivateModalDevice):
    def __init__(self) -> None:
        super().__init__()
        self.unfollow_queries = 0

    def __call__(self, **selector):
        label = str(selector.get("text") or "")
        if label == "Unfollow":
            self.unfollow_queries += 1
            if self.unfollow_queries >= 2:
                self.modal_open = False
                return _Element(False)
        return super().__call__(**selector)


class _PublicUnfollowCompletedDevice(_PrivateModalDevice):
    def __init__(self) -> None:
        super().__init__()
        self.modal_open = False


class PrivateUnfollowConfirmationContractTests(unittest.TestCase):
    def test_public_profile_standard_unfollow_path_remains_tap_free_in_verifier(self):
        device = _PublicUnfollowCompletedDevice()
        with patch.object(probe, "log"), patch.object(probe.time, "sleep"):
            out = probe.verify_unfollow_action_success_after_tap(
                device,
                target_username="public.profile",
                timeout_s=0.5,
                profile_identity_certified=True,
                private_flow_engaged=True,
            )
        self.assertTrue(out["ok"])
        self.assertFalse(out["private_confirmation_detected"])
        self.assertEqual(device.clicks, [])

    def test_exact_private_modal_is_classified_from_one_immutable_snapshot(self):
        out = probe.classify_private_unfollow_confirmation_snapshot(
            _private_modal_xml(),
            target_username="marina.pavln",
            profile_identity_certified=True,
            private_flow_engaged=True,
            screen_w=1080,
            screen_h=2400,
        )
        self.assertTrue(out["present"])
        self.assertTrue(out["ok"])
        self.assertEqual(out["action_candidate_count"], 1)
        self.assertTrue(out["snapshot_generation"])

    def test_private_modal_without_action_bar_reuses_prior_exact_profile_identity(self):
        out = probe.classify_private_unfollow_confirmation_snapshot(
            _private_modal_xml_without_profile_title(),
            target_username="marina.pavln",
            profile_identity_certified=True,
            private_flow_engaged=True,
            screen_w=1080,
            screen_h=2400,
        )
        self.assertTrue(out["present"])
        self.assertTrue(out["ok"])
        self.assertEqual(
            out["identity_verification_method"],
            "prior_profile_exact_plus_modal_target_context",
        )
        self.assertEqual(out["actual_profile_username"], "marina.pavln")

    def test_wrong_profile_fails_closed_without_action_contract(self):
        out = probe.classify_private_unfollow_confirmation_snapshot(
            _private_modal_xml(username="other.profile"),
            target_username="marina.pavln",
            profile_identity_certified=True,
            private_flow_engaged=True,
            screen_w=1080,
            screen_h=2400,
        )
        self.assertTrue(out["present"])
        self.assertFalse(out["ok"])
        self.assertEqual(
            out["failure_reason"],
            "private_confirmation_profile_identity_mismatch",
        )

    def test_generic_unfollow_dialog_without_private_context_is_not_accepted(self):
        out = probe.classify_private_unfollow_confirmation_snapshot(
            _private_modal_xml(include_context=False),
            target_username="marina.pavln",
            profile_identity_certified=True,
            private_flow_engaged=True,
            screen_w=1080,
            screen_h=2400,
        )
        self.assertFalse(out["present"])
        self.assertFalse(out["ok"])

    def test_security_surface_wins_over_otherwise_exact_private_modal(self):
        out = probe.classify_private_unfollow_confirmation_snapshot(
            _unsafe_private_modal_xml(),
            target_username="marina.pavln",
            profile_identity_certified=True,
            private_flow_engaged=True,
            screen_w=1080,
            screen_h=2400,
        )
        self.assertTrue(out["present"])
        self.assertFalse(out["ok"])
        self.assertEqual(out["failure_reason"], "private_confirmation_unsafe_surface")

    def test_private_modal_is_tapped_once_then_strict_success_is_verified(self):
        device = _PrivateModalDevice()
        with patch.object(probe, "guard_instagram_action_rate_limit"), patch.object(
            probe, "log"
        ), patch.object(probe.time, "sleep"):
            out = probe.verify_unfollow_action_success_after_tap(
                device,
                target_username="marina.pavln",
                timeout_s=1.0,
                profile_identity_certified=True,
                private_flow_engaged=True,
            )
        self.assertTrue(out["ok"])
        self.assertTrue(out["private_confirmation_detected"])
        self.assertTrue(out["private_confirmation_tapped"])
        self.assertEqual(len(device.clicks), 1)
        self.assertLess(device.clicks[0][1], 1390)
        self.assertNotEqual(device.clicks[0], (540, 1465))

    def test_reference_private_modal_without_action_bar_taps_unfollow_once(self):
        device = _PrivateModalWithoutActionBarDevice()
        with patch.object(probe, "guard_instagram_action_rate_limit"), patch.object(
            probe, "log"
        ), patch.object(probe.time, "sleep"):
            out = probe.verify_unfollow_action_success_after_tap(
                device,
                target_username="marina.pavln",
                timeout_s=1.0,
                profile_identity_certified=True,
                private_flow_engaged=True,
            )
        self.assertTrue(out["ok"])
        self.assertTrue(out["private_confirmation_detected"])
        self.assertTrue(out["private_confirmation_tapped"])
        self.assertEqual(len(device.clicks), 1)
        self.assertLess(device.clicks[0][1], 1390)

    def test_private_modal_without_certified_identity_is_never_tapped(self):
        device = _PrivateModalDevice()
        with patch.object(probe, "log"), patch.object(probe.time, "sleep"):
            out = probe.verify_unfollow_action_success_after_tap(
                device,
                target_username="marina.pavln",
                timeout_s=1.0,
                profile_identity_certified=False,
                private_flow_engaged=True,
            )
        self.assertFalse(out["ok"])
        self.assertEqual(
            out["failure_reason"],
            "private_confirmation_identity_not_certified",
        )
        self.assertEqual(device.clicks, [])

    def test_stuck_private_modal_is_never_blindly_tapped_twice(self):
        device = _StuckPrivateModalDevice()
        with patch.object(probe, "guard_instagram_action_rate_limit"), patch.object(
            probe, "log"
        ), patch.object(probe.time, "sleep"):
            out = probe.verify_unfollow_action_success_after_tap(
                device,
                target_username="marina.pavln",
                timeout_s=0.01,
                profile_identity_certified=True,
                private_flow_engaged=True,
            )
        self.assertFalse(out["ok"])
        self.assertTrue(out["private_confirmation_tapped"])
        self.assertEqual(len(device.clicks), 1)

    def test_private_modal_disappearing_before_tap_is_handled_without_click(self):
        device = _DisappearingPrivateModalDevice()
        with patch.object(probe, "guard_instagram_action_rate_limit"), patch.object(
            probe, "log"
        ), patch.object(probe.time, "sleep"):
            out = probe.verify_unfollow_action_success_after_tap(
                device,
                target_username="marina.pavln",
                timeout_s=0.5,
                profile_identity_certified=True,
                private_flow_engaged=True,
            )
        self.assertFalse(out["ok"])
        self.assertEqual(
            out["failure_reason"],
            "private_confirmation_disappeared_before_tap",
        )
        self.assertEqual(device.clicks, [])


if __name__ == "__main__":
    unittest.main()
