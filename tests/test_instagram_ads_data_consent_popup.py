from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import account_identity_guard as identity_guard
import instagram_action_restriction as action_guard
import instagram_login_provisioner_orchestrator as orchestrator
import instagram_post_verification_completion as post_completion
from instagram_ads_data_consent_popup import (
    BUSINESS_PAUSED_REASON,
    IDENTITY_PENDING_REASON,
    InstagramAdsDataConsentPopupDetected,
    classify_instagram_ads_data_consent_popup,
    guard_instagram_ads_data_consent_popup,
    publish_ads_data_consent_operator_alert,
)


def popup_xml(*, username: str = "") -> str:
    username_node = (
        f'<node package="com.instagram.androig" resource-id="com.instagram.androig:id/action_bar_title" '
        f'text="{username}" />'
        if username
        else ""
    )
    return (
        '<hierarchy><node package="com.instagram.androig" class="android.app.Dialog">'
        + username_node
        + '<node package="com.instagram.androig" text="Choose if we process your data for ads" />'
        + '<node package="com.instagram.androig" text="As part of laws in your region, you can choose whether you consent to us processing your personal data for personalised ads on Meta Company Products." />'
        + '<node package="com.instagram.androig" class="android.widget.Button" text="Get started" clickable="true" />'
        + "</node></hierarchy>"
    )


class AdsDataConsentClassifierTests(unittest.TestCase):
    def test_popup_detected_by_text(self) -> None:
        result = classify_instagram_ads_data_consent_popup(
            popup_xml(), package_name="com.instagram.androig"
        )
        self.assertTrue(result.detected)
        self.assertTrue(result.title_detected)
        self.assertTrue(result.body_detected)
        self.assertTrue(result.cta_detected)

    def test_other_login_and_system_popups_are_distinct(self) -> None:
        for text in (
            "Save your login info? Not now",
            "To use Location services, allow Instagram to access your location",
            "Enter the code we sent to WhatsApp",
            "Try again later",
        ):
            xml = f'<hierarchy><node package="com.instagram.androig" text="{text}" /></hierarchy>'
            self.assertFalse(classify_instagram_ads_data_consent_popup(xml).detected)


class AdsDataConsentIdentityTests(unittest.TestCase):
    def _verify(self, hierarchy: str) -> identity_guard.AccountIdentityCheckResult:
        device = Mock()
        device.dump_hierarchy.return_value = hierarchy
        with (
            patch.object(identity_guard, "_dump_hierarchy", return_value=hierarchy),
            patch.object(post_completion, "_dump_hierarchy", return_value=hierarchy),
            patch.object(identity_guard, "publish_ads_data_consent_operator_alert", return_value={
                "incident_id": "incident-1",
                "dashboard_action_id": "action-1",
                "detected_at": "2026-08-13T16:31:00+00:00",
            }) as publish,
            patch.object(identity_guard, "open_own_profile_from_bottom_nav") as open_profile,
        ):
            result = identity_guard.verify_active_instagram_account_matches_expected(
                device,
                expected_account_username="bmybusinesses",
                expected_package_name="com.instagram.androig",
                account_id="account-1",
                run_type="login_provisioning",
                run_id="run-1",
                stage="login_provisioning_post_login_identity",
            )
        publish.assert_called_once()
        open_profile.assert_not_called()
        return result

    def test_all_interactive_phases_pause_without_a_gesture_or_false_receipt(self) -> None:
        device = Mock()
        context = SimpleNamespace(
            account_id="account-1", account_username="safe-user", run_id="run-1",
            request_id="request-1", device_id="device-1", app_instance_id="app-1", clone="3",
        )
        phases = (
            "follow", "unfollow", "dm", "welcome_dm", "like", "comment",
            "target_navigation", "return_ct",
        )
        with patch(
            "instagram_ads_data_consent_popup.publish_ads_data_consent_operator_alert",
            return_value={"incident_id": "incident-1", "dashboard_action_id": "action-1"},
        ):
            for phase in phases:
                with self.subTest(phase=phase):
                    with self.assertRaises(InstagramAdsDataConsentPopupDetected) as raised:
                        guard_instagram_ads_data_consent_popup(
                            device, hierarchy_xml=popup_xml(), package_name="com.instagram.androig",
                            context=context, phase=phase, preceding_action=f"before_{phase}_gesture",
                        )
                    self.assertEqual(raised.exception.summary["phase_status"], "partial_resumable")
                    self.assertEqual(raised.exception.summary["safe_next_step"], "schedule_resume")
                    self.assertNotIn("verified_actions", raised.exception.summary)
        device.click.assert_not_called()

    def test_operator_alert_is_account_idempotent_and_explicit(self) -> None:
        incident_rows = [
            {"published": True, "incident_id": "incident-1", "occurrence_count": 1},
            {"published": True, "incident_id": "incident-1", "occurrence_count": 2},
        ]
        with (
            patch("instagram_ads_data_consent_popup.publish_account_incident", side_effect=incident_rows) as incident,
            patch("login_challenge_runtime.sync_login_challenge_dashboard_action", return_value={"dashboard_action_id": "action-1"}),
            patch("incident_notifications.dispatch_account_incident_notifications", return_value={"dispatched": True}),
        ):
            results = [
                publish_ads_data_consent_operator_alert(
                    account_id="account-1", account_username="safe-user", run_id="run-1",
                    request_id="request-1", device_id="device-1", app_instance_id="app-1",
                    clone="3", phase="unfollow", preceding_action="before_unfollow_tap",
                    identity_pending=False,
                )
                for _ in range(2)
            ]
        self.assertEqual([row["incident_id"] for row in results], ["incident-1", "incident-1"])
        for call in incident.call_args_list:
            self.assertEqual(call.kwargs["dedupe_key"], "account:account-1:instagram_ads_data_consent_popup")
            self.assertIn("Choose if we process your data for ads", call.kwargs["admin_message"])
            self.assertIn("app-1", str(call.kwargs["metadata"]))

    def test_popup_clearance_reobserves_identity_without_credentials_or_new_request(self) -> None:
        first = identity_guard.AccountIdentityCheckResult(
            ok=False, expected_account_username="safe-user", actual_logged_in_username=None,
            failure_reason=IDENTITY_PENDING_REASON, verification_method="ads_data_consent_popup",
            identity_evidence="username_unreadable_behind_popup",
            meta={"popup_type": "instagram_ads_data_consent_popup", "session_authenticated": True,
                  "identity_pending_popup": True, "profile_opened": False},
        )
        second = identity_guard.AccountIdentityCheckResult(
            ok=True, expected_account_username="safe-user", actual_logged_in_username="safe-user",
            failure_reason="", verification_method="own_profile_username_exact:action_bar_title",
            identity_evidence="username_exact_match", meta={"profile_opened": True},
        )
        credential_loader = Mock()
        request_creator = Mock()
        pending_meta, pending_reason = orchestrator._canonical_post_auth_identity_handoff(
            object(), verifier=Mock(return_value=first), account_id="account-1",
            expected_username="safe-user", expected_package_name="com.instagram.androig",
            run_id="run-1", run_type="login_provisioning",
        )
        resumed_meta, resumed_reason = orchestrator._canonical_post_auth_identity_handoff(
            object(), verifier=Mock(return_value=second), account_id="account-1",
            expected_username="safe-user", expected_package_name="com.instagram.androig",
            run_id="run-1", run_type="login_provisioning",
        )
        self.assertEqual(pending_reason, IDENTITY_PENDING_REASON)
        self.assertTrue(pending_meta["session_authenticated"])
        self.assertFalse(resumed_reason)
        self.assertTrue(resumed_meta["expected_identity_verified"])
        credential_loader.assert_not_called()
        request_creator.assert_not_called()

    def test_popup_with_exact_own_username_allows_identity_pass_only(self) -> None:
        result = self._verify(popup_xml(username="bmybusinesses"))
        self.assertTrue(result.ok)
        self.assertTrue(result.meta["profile_opened"])
        self.assertTrue(result.meta["operator_action_required"])
        self.assertFalse(result.meta["automatic_cta_click_allowed"])

    def test_popup_without_readable_username_keeps_connected_false(self) -> None:
        result = self._verify(popup_xml())
        self.assertFalse(result.ok)
        self.assertEqual(result.failure_reason, IDENTITY_PENDING_REASON)
        self.assertTrue(result.meta["session_authenticated"])
        self.assertTrue(result.meta["identity_pending_popup"])

        metadata, failure = orchestrator._canonical_post_auth_identity_handoff(
            object(),
            verifier=lambda *_args, **_kwargs: result,
            account_id="account-1",
            expected_username="bmybusinesses",
            expected_package_name="com.instagram.androig",
            run_id="run-1",
            run_type="login_provisioning",
        )
        self.assertEqual(failure, IDENTITY_PENDING_REASON)
        self.assertFalse(metadata["expected_identity_verified"])
        self.assertTrue(metadata["identity_pending_popup"])


class AdsDataConsentBusinessGuardTests(unittest.TestCase):
    def test_business_action_is_paused_before_any_tap_or_receipt(self) -> None:
        class Device:
            clicks = 0

            def app_current(self):
                return {"package": "com.instagram.androig"}

            def click(self, *_args, **_kwargs):
                self.clicks += 1

        device = Device()
        action_guard.configure_restriction_runtime_context(
            account_id="account-1",
            account_username="safe-user",
            run_id="run-1",
            request_id="request-1",
            app_instance_id="app-1",
        )
        with patch(
            "instagram_ads_data_consent_popup.publish_ads_data_consent_operator_alert",
            return_value={"incident_id": "incident-1", "dashboard_action_id": "action-1"},
        ) as publish:
            with self.assertRaises(InstagramAdsDataConsentPopupDetected) as raised:
                action_guard.guard_instagram_action_rate_limit(
                    device,
                    phase="unfollow",
                    preceding_action="before_unfollow_tap",
                    hierarchy_xml=popup_xml(),
                )
        self.assertEqual(device.clicks, 0)
        self.assertEqual(raised.exception.summary["reason"], BUSINESS_PAUSED_REASON)
        self.assertEqual(raised.exception.summary["phase_status"], "partial_resumable")
        self.assertTrue(raised.exception.summary["resume_recommended"])
        publish.assert_called_once()

    def test_all_known_auto_login_variants_keep_one_canonical_handoff(self) -> None:
        self.assertEqual(len(orchestrator.CANONICAL_POST_AUTH_SUCCESS_VARIANTS), 15)
        for variant in orchestrator.CANONICAL_POST_AUTH_SUCCESS_VARIANTS:
            metadata, failure = orchestrator._canonical_post_auth_identity_handoff(
                object(),
                verifier=lambda *_args, **_kwargs: {
                    "ok": False,
                    "expected_account_username": "expected",
                    "failure_reason": IDENTITY_PENDING_REASON,
                    "meta": {
                        "popup_type": "instagram_ads_data_consent_popup",
                        "identity_pending_popup": True,
                        "session_authenticated": True,
                        "profile_opened": False,
                    },
                },
                account_id="account-1",
                expected_username="expected",
                expected_package_name="com.instagram.androig",
                run_id="run-1",
                run_type="login_provisioning",
                extra_metadata={"post_auth_success_variant": variant},
            )
            self.assertEqual(failure, IDENTITY_PENDING_REASON, variant)
            self.assertTrue(metadata["canonical_post_auth_handoff_reached"], variant)
            self.assertFalse(metadata["expected_identity_verified"], variant)


if __name__ == "__main__":
    unittest.main()
