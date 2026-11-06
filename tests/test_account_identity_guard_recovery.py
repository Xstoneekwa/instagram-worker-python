from __future__ import annotations

import tempfile
import unittest
from unittest.mock import ANY, Mock, patch

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
            patch.object(guard, "navigate_to_own_profile_from_bottom_nav") as open_profile,
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
            patch.object(guard, "navigate_to_own_profile_from_bottom_nav") as open_profile,
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
            patch.object(guard, "navigate_to_own_profile_from_bottom_nav") as open_profile,
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
            patch.object(guard, "navigate_to_own_profile_from_bottom_nav", return_value=guard.OwnProfileNavigationResult(guard.OwnProfileNavigationOutcome.PROFILE_OPENED, True)) as open_profile,
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
            patch.object(guard, "navigate_to_own_profile_from_bottom_nav") as open_profile,
            patch.object(guard, "_capture_identity_failure_artifacts", return_value={"screenshot_captured": True, "xml_dump_captured": True}),
        ):
            result = self._verify(device)

        self.assertFalse(result.ok)
        self.assertEqual(result.failure_reason, "ui_transition_loading")
        self.assertTrue(result.meta.get("loading_retry_used"))
        self.assertEqual(result.meta.get("identity_guard_stage"), "pre_profile_screen_loading_retry")
        sleep.assert_called_once_with(guard._LOADING_RETRY_SECONDS)
        open_profile.assert_not_called()

    def test_profile_loading_network_context_is_unverifiable_not_mismatch(self) -> None:
        loading_profile = (
            '<hierarchy><node resource-id="com.instagram.androig:id/action_bar_large_title_auto_size" '
            'text="cinema_catchup" /><node resource-id="com.instagram.androig:id/profile_tab" '
            'selected="true" /><node class="android.view.View" '
            'resource-id="com.instagram.androig:id/swipe_refresh_animated_progressbar_container" />'
            '</hierarchy>'
        )
        device = Mock()
        with (
            patch.object(guard, "_dump_hierarchy", side_effect=[HOME_XML, loading_profile]),
            patch.object(guard, "navigate_to_own_profile_from_bottom_nav", return_value=guard.OwnProfileNavigationResult(guard.OwnProfileNavigationOutcome.LOADING_INTERMEDIATE_ON_PROFILE, True, hierarchy_xml=loading_profile, evidence={"network_context": "network_suspected_unavailable"})),
            patch.object(
                guard,
                "_capture_identity_failure_artifacts",
                return_value={"screenshot_captured": True, "xml_dump_captured": True},
            ),
        ):
            result = self._verify(device)

        self.assertFalse(result.ok)
        self.assertEqual(result.actual_logged_in_username, "")
        self.assertEqual(result.failure_reason, "instagram_connectivity_unverified")
        self.assertNotEqual(result.failure_reason, guard.ACCOUNT_IDENTITY_MISMATCH_REASON)
        self.assertEqual(result.meta["network_context"], "network_suspected_unavailable")

    def test_active_home_screen_continues_to_open_own_profile(self) -> None:
        device = Mock()
        with (
            patch.object(guard, "_dump_hierarchy", side_effect=[HOME_XML, PROFILE_USERNAME_XML]),
            patch.object(guard, "navigate_to_own_profile_from_bottom_nav", return_value=guard.OwnProfileNavigationResult(guard.OwnProfileNavigationOutcome.PROFILE_OPENED, True)) as open_profile,
            patch.object(
                guard,
                "_extract_own_profile_username_from_hierarchy",
                return_value=("cinema_catchup", "action_bar_title", {"hierarchy_xml_len": 100}),
            ),
        ):
            result = self._verify(device)

        self.assertTrue(result.ok)
        open_profile.assert_called_once()

    def test_login_identity_completion_uses_bound_clone_package(self) -> None:
        device = Mock()
        completion = Mock(
            safe_for_identity_guard=True,
            screen_type="active_account_home",
            recovery_count=1,
            recovered_screen_types=("connected_post_login_location_services_prompt",),
            fingerprint_changed=True,
            metadata={},
            failure_reason=None,
        )
        with (
            patch.object(guard, "prepare_post_verification_identity_surface", return_value=completion) as prepare,
            patch.object(guard, "_dump_hierarchy", side_effect=[HOME_XML, PROFILE_USERNAME_XML]),
            patch.object(guard, "navigate_to_own_profile_from_bottom_nav", return_value=guard.OwnProfileNavigationResult(guard.OwnProfileNavigationOutcome.PROFILE_OPENED, True)) as open_profile,
            patch.object(
                guard,
                "_extract_own_profile_username_from_hierarchy",
                return_value=("cinema_catchup", "action_bar_title", {"hierarchy_xml_len": 100}),
            ),
        ):
            result = guard.verify_active_instagram_account_matches_expected(
                device,
                expected_account_username="cinema_catchup",
                expected_package_name="com.instagram.androig",
                account_id="42c625c2-e761-4100-8a9d-7ae1373de97d",
                run_type="login_provisioning",
                run_id="login-run-1",
                stage="login_provisioning_post_login_identity",
            )

        self.assertTrue(result.ok)
        prepare.assert_called_once_with(
            device,
            expected_package_name="com.instagram.androig",
        )
        open_profile.assert_called_once_with(
            device,
            expected_package_name="com.instagram.androig",
            expected_username="cinema_catchup",
            on_first_refusal=ANY,
        )

    def test_identity_failure_captures_screenshot_and_xml(self) -> None:
        device = Mock()
        device.screenshot = Mock()
        with tempfile.TemporaryDirectory(prefix="identity-guard-test-") as directory:
            with (
                patch.object(guard, "_dump_hierarchy", return_value=LOGIN_XML),
                patch.object(guard, "navigate_to_own_profile_from_bottom_nav") as open_profile,
                patch.object(guard, "_IDENTITY_GUARD_ARTIFACTS_DIR", guard.Path(directory)),
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
            patch.object(guard, "navigate_to_own_profile_from_bottom_nav", return_value=guard.OwnProfileNavigationResult(guard.OwnProfileNavigationOutcome.HOME_FEED, True, hierarchy_xml=HOME_XML)),
            patch.object(guard, "_capture_identity_failure_artifacts", return_value={"screenshot_captured": True, "xml_dump_captured": True}),
        ):
            result = self._verify(device)

        self.assertFalse(result.ok)
        self.assertEqual(result.failure_reason, "own_profile_navigation_stayed_on_home")
        self.assertEqual(result.meta.get("screen_type"), "home_feed")

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
            patch.object(guard.config, "INSTAGRAM_PACKAGE", guard.config.INSTAGRAM_PACKAGE),
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



class StructuredOwnProfileRecoveryIntegrationTests(unittest.TestCase):
    def _exercise(self, final_xml=None, *, first_xml=None, identity_xml=None, second_read_callback=None):
        from pathlib import Path
        import os
        import own_profile_navigation as nav
        from tests.test_own_profile_navigation import OwnProfileBoundedObservationTests as Samples
        first = first_xml if first_xml is not None else Samples.OPAQUE
        final = final_xml if final_xml is not None else Samples.READY
        identity = identity_xml if identity_xml is not None else Samples.READY
        device = Samples.Device([HOME_XML, first, final, identity])
        if second_read_callback:
            device.after_read = second_read_callback
        with tempfile.TemporaryDirectory(prefix="own-profile-private-") as directory:
            with (
                patch.object(guard, "_IDENTITY_GUARD_ARTIFACTS_DIR", Path(directory)),
                patch.object(nav, "verify_app_foreground", return_value=True),
                patch.object(nav.config, "WELCOME_BASELINE_OWN_PROFILE_SETTLE_S", 0.001, create=True),
                patch.object(nav.config, "OWN_PROFILE_DESTINATION_LOADING_RETRY_S", 0.0, create=True),
                patch.object(nav.time, "sleep"),
                patch.object(guard, "publish_ads_data_consent_operator_alert", return_value={}),
                patch("runtime_incidents.publish_account_incident", return_value={}),
                patch.object(guard, "_publish_identity_mismatch_incident"),
                patch.object(guard, "_publish_identity_mismatch_status"),
            ):
                result = guard.verify_active_instagram_account_matches_expected(
                    device, expected_account_username="expected", expected_package_name=Samples.PACKAGE,
                    account_id="synthetic-account", run_id="synthetic-run", run_type="outreach_session",
                )
            contents = {p.name: p.read_text() for p in Path(directory).glob("*.xml")}
            modes = {p.name: os.stat(p).st_mode & 0o777 for p in Path(directory).glob("*.xml")}
        self.assertEqual(device.tab_taps, 1)
        device.click.assert_not_called()
        device.press.assert_not_called()
        device.swipe.assert_not_called()
        return result, device, contents, modes

    def test_real_guard_requalifies_fresh_identity_after_transient_unknown(self):
        from tests.test_own_profile_navigation import OwnProfileBoundedObservationTests as Samples
        result, device, contents, modes = self._exercise()
        self.assertTrue(result.ok)
        self.assertEqual(result.actual_logged_in_username, "expected")
        self.assertTrue(result.meta["own_profile_reobservation_used"])
        self.assertEqual(device.dumps, 4)
        name = result.meta["own_profile_first_xml_basename"]
        self.assertEqual(contents[name], Samples.OPAQUE)
        self.assertEqual(modes[name], 0o600)
        self.assertEqual(result.meta["own_profile_first_refused_predicate"], "destination_not_proved")
        self.assertFalse(result.meta.get("screenshot_captured", False))
        device.screenshot.assert_not_called()
        self.assertNotIn(Samples.OPAQUE, str(result.to_dict()))

    def test_persistent_unknown_keeps_both_observations_and_stops_before_identity_read(self):
        from tests.test_own_profile_navigation import OwnProfileBoundedObservationTests as Samples
        result, device, contents, _ = self._exercise(final_xml=Samples.OPAQUE.replace("android.view.View", "android.view.ViewGroup"))
        self.assertFalse(result.ok)
        self.assertEqual(result.failure_reason, "own_profile_open_failed")
        self.assertEqual(result.meta["own_profile_navigation_subreason"], "destination_unproved_after_reobservation")
        self.assertEqual(device.dumps, 3)
        self.assertEqual(contents[result.meta["own_profile_first_xml_basename"]], Samples.OPAQUE)
        self.assertNotEqual(contents[result.meta["xml_basename"]], Samples.OPAQUE)
        self.assertFalse(result.meta["screenshot_contemporaneous_with_xml"])

    def test_failed_initial_private_retention_cannot_admit_a_recovered_profile(self):
        with patch.object(guard, "_capture_identity_failure_artifacts", return_value={"xml_dump_captured": False}):
            result, device, _, _ = self._exercise()
        self.assertFalse(result.ok)
        self.assertEqual(device.dumps, 2)
        self.assertFalse(result.meta["own_profile_first_xml_captured"])
        self.assertEqual(result.meta["own_profile_navigation_subreason"], "first_observation_retention_failed")

    def test_different_username_in_full_fresh_identity_is_never_admitted(self):
        from tests.test_own_profile_navigation import OwnProfileBoundedObservationTests as Samples
        result, _, _, _ = self._exercise(identity_xml=Samples.READY.replace('text="expected"', 'text="other_account"'))
        self.assertFalse(result.ok)
        self.assertEqual(result.failure_reason, guard.ACCOUNT_IDENTITY_MISMATCH_REASON)

    def test_opaque_fresh_identity_invalidates_the_ready_second_observation(self):
        from tests.test_own_profile_navigation import OwnProfileBoundedObservationTests as Samples
        result, _, _, _ = self._exercise(identity_xml=Samples.OPAQUE)
        self.assertFalse(result.ok)
        self.assertEqual(result.meta["own_profile_navigation_subreason"], "identity_requalification_surface_unproved")

    def test_application_change_before_identity_admission_invalidates_previous_profile(self):
        from tests.test_own_profile_navigation import OwnProfileBoundedObservationTests as Samples
        original = Samples.Device.app_current
        with patch.object(Samples.Device, "app_current", lambda d: {"package": "example.other"} if d.dumps >= 4 else original(d)):
            result, _, _, _ = self._exercise()
        self.assertFalse(result.ok)
        self.assertEqual(result.meta["own_profile_navigation_subreason"], "identity_requalification_package_unproved")

    def test_foreign_identity_xml_cannot_borrow_the_expected_foreground_package(self):
        from tests.test_own_profile_navigation import OwnProfileBoundedObservationTests as Samples
        result, _, _, _ = self._exercise(identity_xml=Samples.READY.replace(Samples.PACKAGE, "example.other"))
        self.assertFalse(result.ok)
        self.assertEqual(result.meta["own_profile_navigation_subreason"], "identity_requalification_package_unproved")

    def test_post_tap_consent_with_background_username_still_requires_operator(self):
        from tests.test_instagram_ads_data_consent_popup import popup_xml
        xml = popup_xml(username="expected").replace("com.instagram.androig", "com.instagram.android")
        result, device, _, _ = self._exercise(first_xml=xml)
        self.assertFalse(result.ok)
        self.assertEqual(result.failure_reason, guard.ADS_DATA_CONSENT_BUSINESS_PAUSED_REASON)
        self.assertTrue(result.meta["operator_action_required"])
        self.assertFalse(result.meta["business_actions_allowed"])
        self.assertEqual(device.dumps, 2)

    def test_post_tap_restriction_retains_specific_operator_reason(self):
        xml = ('<hierarchy><node package="com.instagram.android" text="We added a restriction to your account"/>'
               '<node package="com.instagram.android" text="You can&apos;t send messages"/></hierarchy>')
        result, device, _, _ = self._exercise(first_xml=xml)
        self.assertFalse(result.ok)
        self.assertEqual(result.failure_reason, guard.MESSAGES_DISABLED_REASON)
        self.assertEqual(device.dumps, 2)

    def test_post_tap_human_confirmation_retains_specific_operator_reason(self):
        xml = ('<hierarchy><node package="com.instagram.android" text="Confirm you&apos;re human"/>'
               '<node package="com.instagram.android" text="to use your account"/>'
               '<node package="com.instagram.android" text="Continue" clickable="true"/></hierarchy>')
        result, device, _, _ = self._exercise(first_xml=xml)
        self.assertFalse(result.ok)
        self.assertEqual(result.failure_reason, guard.HUMAN_CONFIRMATION_REASON)
        self.assertEqual(device.dumps, 2)

    def test_first_xml_is_private_and_survives_a_later_continuity_failure(self):
        from pathlib import Path
        import own_profile_navigation as nav
        from tests.test_own_profile_navigation import OwnProfileBoundedObservationTests as Samples
        device = Samples.Device([HOME_XML, Samples.OPAQUE])
        with tempfile.TemporaryDirectory(prefix="own-profile-fence-") as directory:
            with (
                patch.object(guard, "_IDENTITY_GUARD_ARTIFACTS_DIR", Path(directory)),
                patch.object(nav, "verify_app_foreground", return_value=True),
                patch.object(nav.time, "sleep"),
                patch.object(nav.ui_continuity_v1, "require_epoch", side_effect=nav.ui_continuity_v1.UiContinuityLost({"reason": "epoch_changed"})),
            ):
                with self.assertRaises(nav.ui_continuity_v1.UiContinuityLost):
                    guard.verify_active_instagram_account_matches_expected(
                        device, expected_account_username="expected", expected_package_name=Samples.PACKAGE,
                        account_id="synthetic-account", run_id="synthetic-run", run_type="outreach_session",
                    )
            files = list(Path(directory).glob("*.xml"))
            self.assertEqual(len(files), 1)
            self.assertEqual(files[0].read_text(), Samples.OPAQUE)
            self.assertEqual(files[0].stat().st_mode & 0o777, 0o600)
        device.screenshot.assert_not_called()
        self.assertEqual(device.dumps, 2)

    def test_empty_causal_observation_never_triggers_a_replacement_dump_in_writer(self):
        from pathlib import Path
        device = Mock()
        with tempfile.TemporaryDirectory(prefix="own-profile-missing-") as directory:
            with patch.object(guard, "_IDENTITY_GUARD_ARTIFACTS_DIR", Path(directory)):
                result = guard._capture_identity_failure_artifacts(
                    device, "", account_id="synthetic", run_id="synthetic-run", stage="test",
                    capture_screenshot=False, observation_role="navigation_first", observed_at_monotonic=1.0,
                )
            self.assertFalse(result["xml_dump_captured"])
            self.assertEqual(Path(directory, result["xml_basename"]).read_text(), "")
        device.dump_hierarchy.assert_not_called()
        device.screenshot.assert_not_called()


if __name__ == "__main__":
    unittest.main()
