import unittest
from unittest.mock import patch

import unfollow_profile_probe as probe


def _profile_xml(*, text="Following", content_desc="", clickable="true") -> str:
    return (
        '<hierarchy><node resource-id="com.instagram.android:id/profile_header_follow_button" '
        f'class="android.widget.Button" clickable="{clickable}" text="{text}" '
        f'content-desc="{content_desc}" bounds="[620,600][920,690]" /></hierarchy>'
    )


def _missing_xml(size: int = 500) -> str:
    padding = "x" * max(0, size - 58)
    return f'<hierarchy><node text="{padding}" /></hierarchy>'


class _MissingElement:
    info = {}

    def exists(self, timeout=0):
        del timeout
        return False


class _LiveElement:
    def __init__(self, info, clicks=None):
        self.info = info
        self._clicks = clicks

    def exists(self, timeout=0):
        del timeout
        return True

    def click(self):
        if self._clicks is not None:
            self._clicks.append("selector")


class _XmlDevice:
    def __init__(self, xml: str):
        self.xml = xml
        self.clicks = []

    def window_size(self):
        return 1080, 2400

    def dump_hierarchy(self, compressed=False):
        del compressed
        return self.xml

    def __call__(self, **selector):
        del selector
        return _MissingElement()

    def click(self, x, y):
        self.clicks.append((x, y))

    def app_current(self):
        return {"package": "com.instagram.android"}


class _AccessibilityDevice(_XmlDevice):
    def __call__(self, **selector):
        if selector.get("text") == "Following":
            return _LiveElement(
                {
                    "text": "Following",
                    "contentDescription": "",
                    "resourceName": "profile_header_follow_button",
                    "className": "android.widget.Button",
                    "clickable": True,
                    "bounds": {"left": 620, "top": 600, "right": 920, "bottom": 690},
                }
            )
        return _MissingElement()


class _SuggestedAccessibilityDevice(_XmlDevice):
    def __call__(self, **selector):
        if selector.get("text") == "Following":
            return _LiveElement(
                {
                    "text": "Following",
                    "contentDescription": "Following Hélène Wallemacq",
                    "resourceName": "follow_list_row_large_follow_button",
                    "className": "android.widget.Button",
                    "clickable": True,
                    "bounds": {"left": 720, "top": 583, "right": 1035, "bottom": 673},
                }
            )
        return _MissingElement()


class FollowingCtaContractTests(unittest.TestCase):
    def test_native_profile_cta_is_clicked_by_live_selector_after_xml_revalidation(self):
        class _LiveCtaDevice(_XmlDevice):
            def __call__(self, **selector):
                if selector.get("resourceId") == "com.instagram.android:id/profile_header_follow_button":
                    return _LiveElement(
                        {
                            "text": "Following",
                            "contentDescription": "Following target",
                            "resourceName": "com.instagram.android:id/profile_header_follow_button",
                            "className": "android.widget.Button",
                            "clickable": True,
                        },
                        self.clicks,
                    )
                return _MissingElement()

        device = _LiveCtaDevice(_profile_xml())
        with patch.object(
            probe,
            "_detect_actions_sheet_signals",
            return_value={"unfollow_visible": True, "unfollow_text": "Unfollow"},
        ):
            out = probe.open_unfollow_actions_sheet_from_profile_probe(
                device,
                expected_target_username="target",
                profile_exact_confirmed=True,
            )
        self.assertTrue(out["ok"])
        self.assertEqual(device.clicks, ["selector"])

    def test_external_foreground_blocks_cta_tap_without_coordinate_fallback(self):
        class _ExternalDevice(_XmlDevice):
            def app_current(self):
                return {"package": "com.google.android.apps.maps"}

        device = _ExternalDevice(_profile_xml())
        out = probe.open_unfollow_actions_sheet_from_profile_probe(
            device,
            expected_target_username="target",
            profile_exact_confirmed=True,
        )
        self.assertFalse(out["ok"])
        self.assertEqual(out["failure_reason"], "live_cta_foreground_package_mismatch")
        self.assertEqual(device.clicks, [])

    def test_missing_live_native_selector_fails_closed_without_stale_coordinate_tap(self):
        device = _XmlDevice(_profile_xml())
        out = probe.open_unfollow_actions_sheet_from_profile_probe(
            device,
            expected_target_username="target",
            profile_exact_confirmed=True,
        )
        self.assertFalse(out["ok"])
        self.assertEqual(out["failure_reason"], "live_cta_selector_missing")
        self.assertEqual(device.clicks, [])

    def test_cta_present_immediately_is_detected_from_xml(self):
        out = probe.detect_profile_following_button_for_unfollow(
            _XmlDevice(_profile_xml()),
            expected_target_username="target",
        )
        self.assertTrue(out["ok"])
        self.assertEqual(out["stable_reason"], "following_cta_detected_xml")

    def test_slow_render_uses_bounded_local_recovery(self):
        button = {
            "ok": True,
            "probe_at": "2026-07-29T00:00:01Z",
            "hierarchy_xml_len": 900,
            "bounds": {"left": 620, "top": 600, "right": 920, "bottom": 690},
            "tap_x": 770,
            "tap_y": 645,
            "resource_id": "profile_header_follow_button",
            "clickable": True,
            "detection_method": "text_exact_following",
            "stable_reason": "following_cta_detected_xml",
        }
        missing = {
            "ok": False,
            "failure_reason": "following_button_not_found",
            "probe_at": "2026-07-29T00:00:00Z",
            "hierarchy_xml_len": 600,
        }
        device = _XmlDevice(_missing_xml())
        with patch.object(
            probe,
            "detect_profile_following_button_for_unfollow",
            side_effect=[missing, missing, button, button],
        ), patch.object(probe.time, "sleep"), patch.object(
            probe,
            "_detect_actions_sheet_signals",
            return_value={"unfollow_visible": True, "unfollow_text": "Unfollow"},
        ):
            out = probe.open_unfollow_actions_sheet_from_profile_probe(
                device,
                expected_target_username="target",
                profile_exact_confirmed=True,
            )
        self.assertTrue(out["ok"])
        self.assertTrue(out["recovery_used"])
        self.assertEqual(out["probes_count"], 4)

    def test_stale_xml_can_recover_through_live_accessibility(self):
        out = probe.detect_profile_following_button_for_unfollow(
            _AccessibilityDevice(_missing_xml()),
            expected_target_username="target",
        )
        self.assertTrue(out["ok"])
        self.assertEqual(out["stable_reason"], "following_cta_detected_accessibility")

    def test_suggested_following_row_is_not_a_profile_header_cta(self):
        xml = (
            '<hierarchy><node resource-id="com.instagram.android:id/follow_list_row_large_follow_button" '
            'class="android.widget.Button" clickable="true" text="Following" '
            'content-desc="Following Hélène Wallemacq" bounds="[720,583][1035,673]" /></hierarchy>'
        )
        out = probe.detect_profile_following_button_for_unfollow(
            _XmlDevice(xml),
            expected_target_username="getch_officiel",
        )
        self.assertFalse(out["ok"])
        self.assertEqual(
            out["reject_reasons_count"],
            {"following_cta_not_profile_header_owned": 1},
        )

    def test_profile_header_cta_wins_when_suggested_following_row_is_also_visible(self):
        xml = (
            '<hierarchy>'
            '<node resource-id="com.instagram.android:id/profile_header_follow_button" '
            'class="android.widget.Button" clickable="true" text="Following" '
            'bounds="[33,752][532,842]" />'
            '<node resource-id="com.instagram.android:id/follow_list_row_large_follow_button" '
            'class="android.widget.Button" clickable="true" text="Following" '
            'content-desc="Following Hélène Wallemacq" bounds="[720,583][1035,673]" />'
            '</hierarchy>'
        )
        out = probe.detect_profile_following_button_for_unfollow(
            _XmlDevice(xml),
            expected_target_username="getch_officiel",
        )
        self.assertTrue(out["ok"])
        self.assertEqual(
            out["resource_id"],
            "com.instagram.android:id/profile_header_follow_button",
        )
        self.assertEqual(out["tap_x"], 282)
        self.assertEqual(out["tap_y"], 797)

    def test_suggested_accessibility_cta_is_rejected_without_tap(self):
        device = _SuggestedAccessibilityDevice(_missing_xml())
        out = probe.detect_profile_following_button_for_unfollow(
            device,
            expected_target_username="getch_officiel",
        )
        self.assertFalse(out["ok"])
        self.assertEqual(device.clicks, [])

    def test_content_desc_exact_profile_variant_is_accepted(self):
        out = probe.detect_profile_following_button_for_unfollow(
            _XmlDevice(_profile_xml(text="", content_desc="Following target")),
            expected_target_username="target",
        )
        self.assertTrue(out["ok"])
        self.assertEqual(out["stable_reason"], "following_cta_detected_accessibility")

    def test_non_clickable_label_uses_current_clickable_ancestor_bounds(self):
        xml = (
            '<hierarchy><node clickable="true" resource-id="profile_header_follow_button" '
            'class="android.widget.Button" bounds="[620,600][920,690]">'
            '<node clickable="false" text="Following" bounds="[700,620][850,670]" />'
            '</node></hierarchy>'
        )
        out = probe.detect_profile_following_button_for_unfollow(
            _XmlDevice(xml),
            expected_target_username="target",
        )
        self.assertTrue(out["ok"])
        self.assertTrue(out["clickable_ancestor"])
        self.assertEqual(out["bounds"], {"left": 620, "top": 600, "right": 920, "bottom": 690})

    def test_xml_incomplete_but_accessibility_visible_never_uses_fixed_coordinates(self):
        device = _AccessibilityDevice(_missing_xml())
        with patch.object(
            probe,
            "verify_unfollow_target_profile_strict",
            return_value={"ok": True},
        ), patch.object(
            probe,
            "_detect_actions_sheet_signals",
            return_value={"unfollow_visible": True, "unfollow_text": "Unfollow"},
        ), patch.object(probe.time, "sleep"):
            out = probe.open_unfollow_actions_sheet_from_profile_probe(
                device,
                expected_target_username="target",
            )
        self.assertTrue(out["ok"])
        self.assertEqual(device.clicks, [(770, 645)])

    def test_temporary_overlay_or_animation_is_resumable_not_terminal_absence(self):
        detections = [
            {
                "ok": False,
                "failure_reason": "following_button_not_found",
                "probe_at": f"2026-07-29T00:00:0{index}Z",
                "hierarchy_xml_len": length,
            }
            for index, length in enumerate((66000, 88000, 67000, 89000, 70000))
        ]
        with patch.object(
            probe,
            "detect_profile_following_button_for_unfollow",
            side_effect=detections,
        ), patch.object(probe.time, "sleep"):
            out = probe.open_unfollow_actions_sheet_from_profile_probe(
                _XmlDevice(_missing_xml()),
                expected_target_username="elyosevents",
                profile_exact_confirmed=True,
            )
        self.assertFalse(out["ok"])
        self.assertEqual(out["terminal_reason"], "following_cta_surface_not_stable")
        self.assertEqual(out["probes_count"], 5)

    def test_wrong_profile_blocks_before_any_cta_click(self):
        device = _XmlDevice(_profile_xml())
        with patch.object(
            probe,
            "verify_unfollow_target_profile_strict",
            return_value={"ok": False, "failure_reason": "target_profile_username_mismatch"},
        ):
            out = probe.open_unfollow_actions_sheet_from_profile_probe(
                device,
                expected_target_username="other",
            )
        self.assertFalse(out["ok"])
        self.assertFalse(out["profile_exact_confirmed"])
        self.assertEqual(device.clicks, [])

    def test_true_absence_requires_five_probes_on_a_stable_surface(self):
        detections = [
            {
                "ok": False,
                "failure_reason": "following_button_not_found",
                "probe_at": f"2026-07-29T00:00:0{index}Z",
                "hierarchy_xml_len": 80000 + index,
            }
            for index in range(5)
        ]
        with patch.object(
            probe,
            "detect_profile_following_button_for_unfollow",
            side_effect=detections,
        ), patch.object(probe.time, "sleep"):
            out = probe.open_unfollow_actions_sheet_from_profile_probe(
                _XmlDevice(_missing_xml()),
                expected_target_username="target",
                profile_exact_confirmed=True,
            )
        self.assertFalse(out["ok"])
        self.assertEqual(out["terminal_reason"], "following_cta_terminally_absent")
        self.assertEqual(out["probes_count"], 5)

    def test_positive_follow_cta_terminalizes_already_not_following(self):
        device = _XmlDevice(_profile_xml(text="Follow"))
        with patch.object(probe.time, "sleep"):
            out = probe.open_unfollow_actions_sheet_from_profile_probe(
                device,
                expected_target_username="target",
                profile_exact_confirmed=True,
            )
        self.assertFalse(out["ok"])
        self.assertEqual(out["terminal_reason"], "already_not_following_confirmed")
        self.assertEqual(out["positive_not_following_state"], "follow")
        self.assertEqual(device.clicks, [])

    def test_positive_full_width_follow_cta_terminalizes_without_second_unfollow(self):
        xml = (
            '<hierarchy><node '
            'resource-id="com.instagram.android:id/profile_header_follow_button" '
            'class="android.widget.Button" clickable="true" text="Follow" '
            'bounds="[32,600][1046,690]" /></hierarchy>'
        )
        device = _XmlDevice(xml)
        with patch.object(probe.time, "sleep"):
            out = probe.open_unfollow_actions_sheet_from_profile_probe(
                device,
                expected_target_username="target",
                profile_exact_confirmed=True,
            )
        self.assertFalse(out["ok"])
        self.assertEqual(out["terminal_reason"], "already_not_following_confirmed")
        self.assertEqual(out["positive_not_following_state"], "follow")
        self.assertEqual(device.clicks, [])

    def test_ambiguous_full_width_follow_looking_surface_is_rejected(self):
        xml = (
            '<hierarchy><node resource-id="generic_button" '
            'class="android.widget.Button" clickable="true" text="Follow" '
            'bounds="[32,600][1046,690]" /></hierarchy>'
        )
        device = _XmlDevice(xml)
        with patch.object(probe.time, "sleep"):
            out = probe.open_unfollow_actions_sheet_from_profile_probe(
                device,
                expected_target_username="target",
                profile_exact_confirmed=True,
            )
        self.assertEqual(out["terminal_reason"], "following_cta_terminally_absent")
        self.assertEqual(out.get("positive_not_following_state"), "")

    def test_follow_text_outside_profile_cta_band_is_not_terminal_proof(self):
        xml = (
            '<hierarchy><node clickable="true" text="Follow" '
            'bounds="[20,1800][420,1890]" /></hierarchy>'
        )
        device = _XmlDevice(xml)
        with patch.object(probe.time, "sleep"):
            out = probe.open_unfollow_actions_sheet_from_profile_probe(
                device,
                expected_target_username="target",
                profile_exact_confirmed=True,
            )
        self.assertEqual(out["terminal_reason"], "following_cta_terminally_absent")
        self.assertEqual(out.get("positive_not_following_state"), "")

    def test_following_stat_is_not_a_cta_and_never_false_succeeds(self):
        xml = (
            '<hierarchy><node text="" content-desc="5,425 following" '
            'bounds="[100,300][400,360]" /></hierarchy>'
        )
        out = probe.detect_profile_following_button_for_unfollow(
            _XmlDevice(xml),
            expected_target_username="target",
        )
        self.assertFalse(out["ok"])

    def test_elyosevents_redacted_accessibility_fixture_is_detected(self):
        device = _XmlDevice(
            _profile_xml(text="", content_desc="Following elyosevents")
        )
        out = probe.detect_profile_following_button_for_unfollow(
            device,
            expected_target_username="elyosevents",
        )
        self.assertTrue(out["ok"])
        self.assertEqual(out["stable_reason"], "following_cta_detected_accessibility")

    def test_vision_reason_is_reserved_for_verified_vision_only(self):
        self.assertEqual(
            probe._following_cta_reason("vision_verified_current_frame"),
            "following_cta_detected_vision_verified",
        )


if __name__ == "__main__":
    unittest.main()
