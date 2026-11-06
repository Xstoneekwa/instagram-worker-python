from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import own_profile_navigation as nav


class VerifyOwnProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.device = MagicMock()
        self.device.window_size.return_value = (1080, 2400)

    def _verify(self, *, expected: str = "j_automatise_pour_toi", **kwargs) -> tuple[bool, dict]:
        patches = {
            "detect_followers_list_screen": {"is_followers_list": False},
            "_followers_profile_tabs_visible": True,
            "_collect_profile_stats_band_texts": ["12 posts", "100 followers"],
            "_header_has_follow_others_cta": False,
            "_strict_own_profile_username_verified": (False, {}),
        }
        patches.update(kwargs)
        with (
            patch.object(
                nav,
                "detect_followers_list_screen",
                return_value=patches["detect_followers_list_screen"],
            ),
            patch.object(
                nav,
                "_followers_profile_tabs_visible",
                return_value=patches["_followers_profile_tabs_visible"],
            ),
            patch.object(
                nav,
                "_collect_profile_stats_band_texts",
                return_value=patches["_collect_profile_stats_band_texts"],
            ),
            patch.object(
                nav,
                "_header_has_follow_others_cta",
                return_value=patches["_header_has_follow_others_cta"],
            ),
            patch.object(
                nav,
                "_strict_own_profile_username_verified",
                return_value=patches["_strict_own_profile_username_verified"],
            ),
        ):
            return nav.verify_own_profile(self.device, expected)

    def test_follow_cta_ignored_when_strict_username_proof_ok(self) -> None:
        ok, meta = self._verify(
            _header_has_follow_others_cta=True,
            _strict_own_profile_username_verified=(
                True,
                {
                    "verify_profile_ok": True,
                    "verification_method": "own_profile_username_exact:title_resource_id",
                },
            ),
        )
        self.assertTrue(ok)
        self.assertTrue(meta["follow_cta_on_header"])
        self.assertEqual(meta["verification_method"], "own_profile_username_exact:title_resource_id")

    def test_follow_cta_rejected_without_username_proof(self) -> None:
        ok, meta = self._verify(
            _header_has_follow_others_cta=True,
            _strict_own_profile_username_verified=(False, {"verify_profile_ok": False}),
        )
        self.assertFalse(ok)
        self.assertTrue(meta["follow_cta_on_header"])
        self.assertFalse(meta.get("verify_profile_ok"))

    def test_external_profile_mismatch_with_follow_cta_rejected(self) -> None:
        ok, meta = self._verify(
            expected="other_user",
            _header_has_follow_others_cta=True,
            _strict_own_profile_username_verified=(False, {"verify_profile_ok": False}),
        )
        self.assertFalse(ok)
        self.assertTrue(meta["follow_cta_on_header"])

    def test_missing_strict_proof_and_no_follow_cta_uses_profile_chrome_fallback(self) -> None:
        ok, meta = self._verify(
            _strict_own_profile_username_verified=(False, {"verify_profile_ok": False}),
            _header_has_follow_others_cta=False,
        )
        self.assertTrue(ok)
        self.assertEqual(meta.get("verify_profile_fallback"), "profile_chrome_without_username_match")

    def test_missing_expected_username_and_no_signals_rejected(self) -> None:
        ok, meta = self._verify(
            expected="",
            _followers_profile_tabs_visible=False,
            _collect_profile_stats_band_texts=[],
            _strict_own_profile_username_verified=(False, {}),
        )
        self.assertFalse(ok)
        self.assertEqual(meta["expected_username"], "")


class StrictOwnProfileUsernameVerifiedTests(unittest.TestCase):
    def test_hierarchy_title_resource_id_matches(self) -> None:
        device = MagicMock()
        hierarchy = (
            '<hierarchy><node resource-id="com.instagram.androif:id/title" '
            'text="j_automatise_pour_toi"/></hierarchy>'
        )
        with (
            patch.object(nav, "verify_profile", return_value=False),
            patch("account_identity_guard._dump_hierarchy", return_value=hierarchy),
        ):
            ok, meta = nav._strict_own_profile_username_verified(device, "j_automatise_pour_toi")
        self.assertTrue(ok)
        self.assertEqual(meta["verification_method"], "own_profile_username_exact:title_resource_id")



# Synthetic UI only. The optional retained native XML stays outside public source.
class OwnProfileBoundedObservationTests(unittest.TestCase):
    PACKAGE = "com.instagram.android"
    OPAQUE = '<hierarchy><node package="com.instagram.android" resource-id="android:id/content"><node class="android.view.View"/></node></hierarchy>'
    READY = '<hierarchy><node package="com.instagram.android" resource-id="com.instagram.android:id/action_bar_title" text="expected"/><node package="com.instagram.android" resource-id="com.instagram.android:id/profile_header"/><node package="com.instagram.android" text="Edit profile"/></hierarchy>'
    LOADING = '<hierarchy><node package="com.instagram.android" class="android.widget.ProgressBar"/></hierarchy>'

    class Device:
        def __init__(self, xmls):
            self.xmls = list(xmls)
            self.dumps = 0
            self.tab_taps = 0
            self.click = MagicMock()
            self.press = MagicMock()
            self.swipe = MagicMock()
            self.screenshot = MagicMock()
            self.after_read = lambda count: None

        def __call__(self, **kwargs):
            selector = MagicMock()
            selector.wait.return_value = kwargs.get("resourceIdMatches") == ".*:id/profile_tab"
            selector.click.side_effect = lambda: setattr(self, "tab_taps", self.tab_taps + 1)
            return selector

        def dump_hierarchy(self, **kwargs):
            self.dumps += 1
            value = self.xmls.pop(0)
            self.after_read(self.dumps)
            return value

        def app_current(self):
            return {"package": "com.instagram.android"}

    def run_reader(self, xmls, *, callback=None, foreground=True, retry=1.0, after_read=None):
        clock = [0.0]
        device = self.Device(xmls)
        if after_read:
            device.after_read = lambda count: after_read(count, clock)
        sleep = lambda seconds: clock.__setitem__(0, clock[0] + seconds)
        with (
            patch.object(nav, "verify_app_foreground", side_effect=foreground if isinstance(foreground, list) else None, return_value=foreground) as fg,
            patch.object(nav.config, "WELCOME_BASELINE_OWN_PROFILE_SETTLE_S", 0.001, create=True),
            patch.object(nav.config, "OWN_PROFILE_DESTINATION_LOADING_RETRY_S", retry, create=True),
            patch.object(nav.time, "sleep", side_effect=sleep) as waits,
            patch.object(nav.time, "monotonic", side_effect=lambda: clock[0]),
        ):
            result = nav.navigate_to_own_profile_from_bottom_nav(
                device, expected_package_name=self.PACKAGE, expected_username="expected",
                on_first_refusal=callback,
            )
        self.assertEqual(device.tab_taps, 1)
        device.click.assert_not_called()
        device.press.assert_not_called()
        device.swipe.assert_not_called()
        return result, device, fg, waits

    def test_ready_profile_has_no_reobservation_or_artifact_callback(self):
        callback = MagicMock()
        result, device, fg, _ = self.run_reader([self.READY], callback=callback)
        self.assertTrue(result.ok)
        self.assertEqual(device.dumps, 1)
        self.assertFalse(result.reobservation_used)
        callback.assert_not_called()
        self.assertEqual(fg.call_count, 1)

    def test_unknown_to_ready_keeps_exact_first_result_before_second_read(self):
        saved = []
        result, device, fg, _ = self.run_reader(
            [self.OPAQUE, self.READY], callback=lambda first: saved.append(first),
        )
        self.assertTrue(result.ok)
        self.assertEqual(device.dumps, 2)
        self.assertEqual(fg.call_count, 2)
        self.assertTrue(result.reobservation_used)
        self.assertIs(result.first_observation, saved[0])
        self.assertEqual(saved[0].hierarchy_xml, self.OPAQUE)
        self.assertEqual(result.hierarchy_xml, self.READY)
        self.assertEqual(saved[0].outcome, nav.OwnProfileNavigationOutcome.UNKNOWN_SCREEN)
        self.assertFalse(saved[0].evidence["loading_present"])
        self.assertNotEqual(result.evidence["first_hierarchy_sha256"], result.evidence["hierarchy_sha256"])

    def test_persistent_opaque_sheet_stops_after_one_read_only_retry(self):
        result, device, _, _ = self.run_reader([self.OPAQUE, self.OPAQUE])
        self.assertFalse(result.ok)
        self.assertEqual(device.dumps, 2)
        self.assertEqual(result.evidence["failure_reason"], "destination_unproved_after_reobservation")
        self.assertEqual(result.evidence["first_refused_predicate"], "destination_not_proved")

    def test_loading_then_unknown_does_not_stack_an_unknown_retry(self):
        result, device, _, _ = self.run_reader([self.LOADING, self.OPAQUE])
        self.assertFalse(result.ok)
        self.assertEqual(device.dumps, 2)
        self.assertTrue(result.loading_retry_used)
        self.assertTrue(result.reobservation_used)

    def test_late_ready_response_does_not_admit(self):
        result, device, _, _ = self.run_reader(
            [self.OPAQUE, self.READY],
            after_read=lambda count, clock: clock.__setitem__(0, clock[0] + 6) if count == 2 else None,
        )
        self.assertFalse(result.ok)
        self.assertEqual(device.dumps, 2)
        self.assertEqual(result.evidence["failure_reason"], "reobservation_budget_exceeded")

    def test_retention_failure_stops_before_a_second_observation(self):
        result, device, _, _ = self.run_reader([self.OPAQUE], callback=lambda first: False)
        self.assertFalse(result.ok)
        self.assertEqual(device.dumps, 1)
        self.assertEqual(result.evidence["failure_reason"], "first_observation_retention_failed")

    def test_retention_time_cannot_extend_the_extra_observation_deadline(self):
        clock = [0.0]
        device = self.Device([self.OPAQUE])
        with (
            patch.object(nav, "verify_app_foreground", return_value=True),
            patch.object(nav.time, "sleep"),
            patch.object(nav.time, "monotonic", side_effect=lambda: clock[0]),
        ):
            result = nav.navigate_to_own_profile_from_bottom_nav(
                device, expected_package_name=self.PACKAGE, expected_username="expected",
                on_first_refusal=lambda first: clock.__setitem__(0, 6.0),
            )
        self.assertFalse(result.ok)
        self.assertEqual(device.dumps, 1)
        self.assertEqual(result.evidence["failure_reason"], "reobservation_budget_exceeded")

    def test_foreign_first_package_does_not_request_an_extra_dump(self):
        xml = self.OPAQUE.replace(self.PACKAGE, "example.other")
        result, device, _, _ = self.run_reader([xml])
        self.assertFalse(result.ok)
        self.assertEqual(device.dumps, 1)
        self.assertEqual(result.evidence["failure_reason"], "initial_observation_package_unproved")

    def test_foreign_second_package_invalidates_apparent_profile(self):
        result, _, _, _ = self.run_reader([self.OPAQUE, self.READY.replace(self.PACKAGE, "example.other")])
        self.assertFalse(result.ok)
        self.assertEqual(result.evidence["failure_reason"], "reobservation_package_unproved")

    def test_foreground_change_after_second_dump_invalidates_ready_proof(self):
        result, _, _, _ = self.run_reader([self.OPAQUE, self.READY], foreground=[True, False])
        self.assertFalse(result.ok)
        self.assertEqual(result.evidence["failure_reason"], "reobservation_foreground_unproved")

    def test_continuity_change_keeps_first_refusal_and_never_reads_again(self):
        saved = []
        with patch.object(nav.ui_continuity_v1, "require_epoch", side_effect=nav.ui_continuity_v1.UiContinuityLost({"reason": "test_epoch_changed"})):
            with self.assertRaises(nav.ui_continuity_v1.UiContinuityLost):
                self.run_reader([self.OPAQUE], callback=saved.append)
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0].hierarchy_xml, self.OPAQUE)

    def test_human_challenge_stops_without_waiting_for_its_disappearance(self):
        xml = ('<hierarchy><node package="com.instagram.android" text="Confirm you&apos;re human"/>'
               '<node package="com.instagram.android" text="to use your account"/>'
               '<node package="com.instagram.android" text="Continue" clickable="true"/></hierarchy>')
        result, device, _, _ = self.run_reader([xml])
        self.assertFalse(result.ok)
        self.assertEqual(device.dumps, 1)
        self.assertEqual(result.evidence["failure_reason"], "instagram_human_confirmation_required")

    def test_consent_with_background_username_is_not_profile_readiness(self):
        from tests.test_instagram_ads_data_consent_popup import popup_xml
        xml = popup_xml(username="expected").replace("com.instagram.androig", self.PACKAGE)
        result, device, _, _ = self.run_reader([xml])
        self.assertFalse(result.ok)
        self.assertEqual(device.dumps, 1)
        self.assertEqual(result.evidence["failure_reason"], nav.ADS_CONSENT_STOP_REASON)

    def test_restriction_is_not_reobserved_as_transient(self):
        xml = '<hierarchy><node package="com.instagram.android" text="We added a restriction to your account"/></hierarchy>'
        result, device, _, _ = self.run_reader([xml])
        self.assertFalse(result.ok)
        self.assertEqual(result.outcome, nav.OwnProfileNavigationOutcome.RESTRICTION_SCREEN)
        self.assertEqual(device.dumps, 1)

    def test_empty_or_malformed_xml_does_not_become_benign_loading(self):
        for xml in ["", "<broken"]:
            with self.subTest(xml=xml):
                result, device, _, _ = self.run_reader([xml])
                self.assertFalse(result.ok)
                self.assertEqual(device.dumps, 1)

    def test_retry_setting_cannot_increase_the_one_second_wait(self):
        for delay in [float("nan"), float("inf"), 20.0, -1.0]:
            with self.subTest(delay=delay):
                result, _, _, waits = self.run_reader([self.OPAQUE, self.READY], retry=delay)
                self.assertTrue(result.ok)
                self.assertLessEqual(sum(c.args[0] for c in waits.call_args_list), 1.001)

    def test_legacy_wrapper_remains_boolean_and_does_not_expose_xml(self):
        result = nav.OwnProfileNavigationResult(nav.OwnProfileNavigationOutcome.PROFILE_OPENED, True, hierarchy_xml="PRIVATE_XML_SENTINEL")
        with patch.object(nav, "navigate_to_own_profile_from_bottom_nav", return_value=result):
            self.assertIs(nav.open_own_profile_from_bottom_nav(object()), True)
        self.assertNotIn("PRIVATE_XML_SENTINEL", repr(result))

    def test_retained_native_sheet_never_proves_profile_or_loading(self):
        import os, hashlib
        from pathlib import Path
        path = os.environ.get("OWN_PROFILE_RETAINED_XML")
        if not path:
            self.skipTest("private native input is external to public source")
        raw = Path(path).read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), os.environ["OWN_PROFILE_RETAINED_XML_SHA256"])
        outcome, evidence = nav.classify_own_profile_destination(raw.decode(), expected_username="expected")
        self.assertEqual(outcome, nav.OwnProfileNavigationOutcome.UNKNOWN_SCREEN)
        self.assertFalse(evidence["loading_present"])
        result, device, _, _ = self.run_reader([raw.decode(), raw.decode()])
        self.assertFalse(result.ok)
        self.assertEqual(device.dumps, 2)


if __name__ == "__main__":
    unittest.main()
