from __future__ import annotations

import json
import inspect
import unittest
from dataclasses import asdict
from unittest.mock import Mock, patch

from instagram_credentials_runtime_access import SecretValue
import instagram_login_provisioner_orchestrator as provisioner_orchestrator
from instagram_login_provisioner_orchestrator import run_login_provisioning_flow, run_old_account_logout_fallback_flow


ACCOUNT_ID = "42c625c2-e761-4100-8a9d-7ae1373de97d"
USERNAME = "cinema_catchup"
PASSWORD = "fake-password-for-unit-tests"
VAULT_ID = "11111111-2222-4333-8444-555555555555"
SECRET_REF = f"supabase_vault://{VAULT_ID}"
LOGIN_FORM_SIGNALS = {
    "screen_type": "login_form_empty",
    "has_username_field": True,
    "has_password_field": True,
    "has_login_button": True,
    "username_editable_present": True,
    "password_field_editable_present": True,
    "forgot_password_present": True,
    "has_create_new_account_button": True,
    "meta_present": True,
    "password_required": True,
    "ready_for_credentials_flow": True,
}
CONTINUE_SIGNALS = {
    "screen_type": "continue_as_candidate",
    "suggested_username": USERNAME,
    "has_continue_button": True,
    "has_use_another_profile": True,
}
WRONG_CONTINUE_SIGNALS = {
    "screen_type": "continue_as_candidate",
    "suggested_username": "random_old_profile",
    "has_continue_button": True,
    "has_use_another_profile": True,
}
ACCOUNT_PICKER_SIGNALS = {
    "screen_type": "account_picker",
    "available_usernames": ["random_expected", "random_old_profile"],
    "expected_username_present": True,
    "expected_username_match_count": 1,
    "has_use_another_profile_button": True,
    "has_create_new_account_button": True,
    "meta_present": True,
}
LOGIN_FORM_XML = (
    '<node text="Username, email or mobile number" />'
    '<node text="Password" />'
    '<node text="Log in" />'
)
CONTINUE_AS_XML = (
    '<node text="Continue" clickable="true" bounds="[100,1000][980,1120]" />'
    '<node text="Use another profile" clickable="false" bounds="[371,1215][710,1280]" />'
    '<node text="Create new account" clickable="true" bounds="[100,2000][980,2190]" />'
)
ACCOUNT_PICKER_XML = (
    '<node clickable="true" bounds="[100,300][980,500]" class="android.view.ViewGroup" />'
    '<node text="random_expected" clickable="false" bounds="[260,350][560,400]" />'
    '<node clickable="true" bounds="[100,540][980,740]" class="android.view.ViewGroup" />'
    '<node text="random_old_profile" clickable="false" bounds="[260,590][620,640]" />'
    '<node text="Use another profile" clickable="true" bounds="[100,780][980,900]" />'
    '<node text="Create new account" clickable="true" bounds="[100,1900][980,2020]" />'
    '<node content-desc="Meta logo" />'
)
PASSWORD_ONLY_XML = (
    '<node text="random_expected" />'
    '<node text="Password" />'
    '<node text="Log in" />'
    '<node text="Forgot password?" />'
)
PASSWORD_ONLY_OVERLAY_XML = (
    '<node text="cinema_catchup" />'
    '<node text="Password" />'
    '<node text="Log in" />'
    '<node text="Suggest strong password" />'
    '<node text="And save to your Google account" />'
)
LOADING_XML = '<node text="Loading..." />'
UNKNOWN_XML = '<node text="Instagram" />'
CONNECTED_XML = (
    '<node content-desc="Home" />'
    '<node content-desc="Search" />'
    '<node content-desc="Reels" />'
    '<node content-desc="Profile" />'
)
NEEDS_2FA_XML = '<node text="Enter code" /><node text="authentication code" />'
CHECKPOINT_XML = '<node text="Help us confirm it’s you" /><node text="Verify your account" />'
LOGIN_FAILED_XML = '<node text="Sorry, your password was incorrect. Please try again." />'
SENSITIVE_XML = '<node text="password secret_ref Vault token emulator-5554 screenshot" />'
ACTIVE_HOME_XML = (
    '<node text="Instagram" />'
    '<node text="Your story" />'
    '<node text="Suggested for you" />'
    '<node content-desc="Home" clickable="true" bounds="[40,2100][160,2240]" />'
    '<node content-desc="Profile" clickable="true" bounds="[880,2100][1020,2240]" />'
)
ACTIVE_PROFILE_OLD_XML = (
    '<node text="random_old_profile" clickable="true" bounds="[70,120][360,190]" />'
    '<node text="Edit profile" />'
    '<node text="Share profile" />'
    '<node text="0 posts" />'
    '<node text="0 followers" />'
    '<node text="2 following" />'
    '<node content-desc="Home" clickable="true" bounds="[40,2100][160,2240]" />'
    '<node content-desc="Profile" clickable="true" bounds="[880,2100][1020,2240]" />'
)
ACTIVE_PROFILE_OLD_MENU_XML = (
    '<node text="random_old_profile" clickable="true" bounds="[70,120][360,190]" />'
    '<node text="Edit profile" />'
    '<node text="Share profile" />'
    '<node text="0 posts" />'
    '<node text="0 followers" />'
    '<node text="2 following" />'
    '<node content-desc="Home" clickable="true" bounds="[40,2100][160,2240]" />'
    '<node content-desc="Profile" clickable="true" bounds="[880,2100][1020,2240]" />'
    '<node resource-id="com.instagram.android:id/action_bar_button_action" clickable="true" bounds="[930,150][1020,240]" />'
)
ACTIVE_PROFILE_EXPECTED_XML = ACTIVE_PROFILE_OLD_XML.replace("random_old_profile", "random_expected")
ACTIVE_PROFILE_EXPECTED_MENU_XML = ACTIVE_PROFILE_OLD_MENU_XML.replace("random_old_profile", "random_expected")
ACCOUNT_SWITCHER_XML = (
    '<node text="random_old_profile" />'
    '<node text="Add Instagram account" clickable="true" bounds="[150,1850][930,1960]" />'
    '<node text="Go to Accounts Center" />'
)
ADD_ACCOUNT_SHEET_XML = (
    '<node text="Add account" />'
    '<node text="Log into existing account" clickable="true" bounds="[100,1700][980,1820]" />'
    '<node text="Create new account" clickable="true" bounds="[100,1880][980,2000]" />'
)
PROFILE_MENU_SHEET_XML = (
    '<node text="Settings and activity" clickable="true" bounds="[80,300][900,420]" />'
)
SETTINGS_AND_ACTIVITY_XML = (
    '<node text="Settings and activity" />'
    '<node text="More info and support" />'
    '<node text="Login" />'
    '<node text="Add account" />'
    '<node text="Log out" clickable="true" bounds="[100,1900][980,2020]" />'
)
SAVE_LOGIN_INFO_PROMPT_XML = (
    '<node text="Save your login info?" />'
    '<node text="Save" clickable="true" bounds="[100,1600][980,1720]" />'
    '<node text="Not now" clickable="true" bounds="[100,1760][980,1880]" />'
)
LOGOUT_CONFIRMATION_PROMPT_XML = (
    '<node text="Log out of your account?" />'
    '<node text="Cancel" clickable="true" bounds="[100,1600][980,1720]" />'
    '<node text="Log out" clickable="true" bounds="[100,1760][980,1880]" />'
)
LOGOUT_CONTINUE_AS_XML = (
    '<node text="random_expected" />'
    '<node text="Continue" clickable="true" bounds="[100,1000][980,1120]" />'
    '<node text="Use another profile" clickable="false" bounds="[371,1215][710,1280]" />'
    '<node text="Create new account" clickable="true" bounds="[100,2000][980,2190]" />'
)


class FakeSelector:
    def __init__(self, count: int = 0, *, click_exc: Exception | None = None, set_exc: Exception | None = None) -> None:
        self._count = count
        self.click_exc = click_exc
        self.set_exc = set_exc
        self.click_calls = 0
        self.set_text_calls: list[str] = []

    def count(self) -> int:
        return self._count

    def click(self, **_kwargs) -> None:
        self.click_calls += 1
        if self.click_exc:
            raise self.click_exc

    def clear_text(self) -> None:
        pass

    def set_text(self, value: str) -> None:
        self.set_text_calls.append(value)
        if self.set_exc:
            raise self.set_exc


class FakeDevice:
    def __init__(self, hierarchies: list[str] | None = None) -> None:
        self.hierarchies = list(hierarchies or [CONNECTED_XML])
        self.dump_calls = 0
        self.selector_calls: list[dict] = []
        self.selectors: dict[tuple[str, str], FakeSelector] = {}
        self.bounds_clicks: list[tuple[int, int]] = []

    def add_selector(self, key: str, value: str, selector: FakeSelector) -> FakeSelector:
        self.selectors[(key, value)] = selector
        return selector

    def __call__(self, **kwargs):
        self.selector_calls.append(dict(kwargs))
        key, value = next(iter(kwargs.items()))
        return self.selectors.get((key, value), FakeSelector(0))

    def dump_hierarchy(self, compressed: bool = False) -> str:
        self.dump_calls += 1
        if not self.hierarchies:
            return ""
        if len(self.hierarchies) == 1:
            return self.hierarchies[0]
        return self.hierarchies.pop(0)

    def click(self, x: int, y: int) -> None:
        self.bounds_clicks.append((int(x), int(y)))


def configured_device(post_xml: str = CONNECTED_XML) -> tuple[FakeDevice, dict[str, FakeSelector]]:
    device = FakeDevice([post_xml])
    selectors = {
        "username": device.add_selector("text", "Username, email or mobile number", FakeSelector(1)),
        "password": device.add_selector("text", "Password", FakeSelector(1)),
        "login": device.add_selector("text", "Log in", FakeSelector(1)),
        "continue": device.add_selector("text", "Continue", FakeSelector(1)),
        "use_another": device.add_selector("text", "Use another profile", FakeSelector(1)),
    }
    return device, selectors


def credentials():
    return {"username": USERNAME, "password": SecretValue(PASSWORD), "secret_ref": SECRET_REF}


class LoginProvisionerOrchestratorTest(unittest.TestCase):
    def _canceled_lifecycle(self) -> Mock:
        return Mock(
            return_value={
                "lifecycle_status": "canceled",
                "clone_reuse_allowed": True,
                "source": "operator_smoke_override",
                "reason": "old canceled account logout fallback",
            }
        )

    def _logout_initial_signals(self, xml: str = ACTIVE_PROFILE_OLD_MENU_XML) -> dict:
        return provisioner_orchestrator._observe_login_signals(
            FakeDevice([xml]),
            expected_username="random_expected",
        )

    def test_login_form_credentials_ok_connected_success(self) -> None:
        device, selectors = configured_device(CONNECTED_XML)

        result = run_login_provisioning_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username=USERNAME,
            credentials_getter=Mock(return_value=credentials()),
            initial_signals=LOGIN_FORM_SIGNALS,
        )

        self.assertTrue(result.ok)
        self.assertTrue(result.completed)
        self.assertEqual(result.final_outcome, "connected")
        self.assertEqual(result.final_login_status, "connected")
        self.assertEqual(result.final_provisioning_status, "ready")
        self.assertEqual(selectors["login"].click_calls, 1)

    def test_login_form_needs_2fa_status_no_retry(self) -> None:
        result = self._run_login_form(NEEDS_2FA_XML)

        self.assertEqual(result.final_outcome, "needs_2fa")
        self.assertEqual(result.final_login_status, "needs_2fa")
        self.assertEqual(result.dashboard_action_type, "complete_two_factor")
        self.assertFalse(result.retry_attempted)

    def test_login_form_checkpoint_status_no_retry(self) -> None:
        result = self._run_login_form(CHECKPOINT_XML)

        self.assertEqual(result.final_outcome, "checkpoint")
        self.assertEqual(result.final_login_status, "checkpoint")
        self.assertEqual(result.dashboard_action_type, "resolve_checkpoint")
        self.assertFalse(result.retry_attempted)

    def test_login_form_login_failed_status_no_retry(self) -> None:
        result = self._run_login_form(LOGIN_FAILED_XML)

        self.assertEqual(result.final_outcome, "login_failed")
        self.assertEqual(result.final_login_status, "failed")
        self.assertEqual(result.final_provisioning_status, "failed")
        self.assertEqual(result.dashboard_action_type, "update_instagram_password")
        self.assertFalse(result.retry_attempted)

    def test_continue_expected_executes_continue_then_login_flow(self) -> None:
        device, selectors = configured_device()
        device.hierarchies = [CONTINUE_AS_XML, LOGIN_FORM_XML, LOGIN_FORM_XML, CONNECTED_XML]

        result = run_login_provisioning_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username=USERNAME,
            credentials_getter=Mock(return_value=credentials()),
            initial_signals=CONTINUE_SIGNALS,
        )

        self.assertTrue(selectors["continue"].click_calls == 1 or device.bounds_clicks)
        self.assertEqual(result.final_outcome, "connected")
        self.assertEqual(result.actions_taken[:3], ["route:continue_expected_account", "tap_continue", "route:start_login_form_flow"])

    def test_continue_expected_connected_home_finalizes_without_password(self) -> None:
        device, selectors = configured_device()
        getter = Mock(return_value=credentials())
        expected_username = "random_expected"
        device.hierarchies = [CONTINUE_AS_XML, CONNECTED_XML, CONNECTED_XML]

        result = run_login_provisioning_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username=expected_username,
            credentials_getter=getter,
            initial_signals={**CONTINUE_SIGNALS, "suggested_username": expected_username},
        )

        self.assertTrue(selectors["continue"].click_calls == 1 or device.bounds_clicks)
        self.assertEqual(result.final_outcome, "connected")
        self.assertEqual(result.final_login_status, "connected")
        self.assertIsNone(result.dashboard_action_type)
        self.assertFalse(result.retry_attempted)
        self.assertFalse(result.should_publish_status)
        self.assertEqual(result.safe_metadata["post_action_status_candidate"], "connected")
        self.assertFalse(result.safe_metadata["password_required"])
        self.assertFalse(result.safe_metadata["ready_for_password_smoke"])
        self.assertFalse(result.safe_metadata["would_submit_password"])
        getter.assert_not_called()

    def test_continue_expected_needs_2fa_finalizes_without_password(self) -> None:
        device, _selectors = configured_device()
        getter = Mock(return_value=credentials())
        expected_username = "random_expected"
        device.hierarchies = [CONTINUE_AS_XML, NEEDS_2FA_XML, NEEDS_2FA_XML]

        result = run_login_provisioning_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username=expected_username,
            credentials_getter=getter,
            initial_signals={**CONTINUE_SIGNALS, "suggested_username": expected_username},
        )

        self.assertEqual(result.final_outcome, "needs_2fa")
        self.assertEqual(result.dashboard_action_type, "complete_two_factor")
        self.assertFalse(result.retry_attempted)
        self.assertFalse(result.should_publish_status)
        getter.assert_not_called()

    def test_continue_expected_checkpoint_finalizes_without_password(self) -> None:
        device, _selectors = configured_device()
        getter = Mock(return_value=credentials())
        expected_username = "random_expected"
        device.hierarchies = [CONTINUE_AS_XML, CHECKPOINT_XML, CHECKPOINT_XML]

        result = run_login_provisioning_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username=expected_username,
            credentials_getter=getter,
            initial_signals={**CONTINUE_SIGNALS, "suggested_username": expected_username},
        )

        self.assertEqual(result.final_outcome, "checkpoint")
        self.assertEqual(result.dashboard_action_type, "resolve_checkpoint")
        self.assertFalse(result.retry_attempted)
        self.assertFalse(result.should_publish_status)
        getter.assert_not_called()

    def test_continue_expected_password_only_connected(self) -> None:
        result = self._run_continue_password_only(CONNECTED_XML)

        self.assertEqual(result.final_outcome, "connected")
        self.assertEqual(result.final_login_status, "connected")
        self.assertIn("login_form_submit", result.actions_taken)
        self.assertFalse(result.retry_attempted)
        self.assertIsNone(result.dashboard_action_type)

    def test_continue_expected_password_only_needs_2fa(self) -> None:
        result = self._run_continue_password_only(NEEDS_2FA_XML)

        self.assertEqual(result.final_outcome, "needs_2fa")
        self.assertEqual(result.final_login_status, "needs_2fa")
        self.assertEqual(result.dashboard_action_type, "complete_two_factor")
        self.assertFalse(result.retry_attempted)

    def test_continue_expected_password_only_checkpoint(self) -> None:
        result = self._run_continue_password_only(CHECKPOINT_XML)

        self.assertEqual(result.final_outcome, "checkpoint")
        self.assertEqual(result.final_login_status, "checkpoint")
        self.assertEqual(result.dashboard_action_type, "resolve_checkpoint")
        self.assertFalse(result.retry_attempted)

    def test_continue_expected_password_only_login_failed(self) -> None:
        result = self._run_continue_password_only(LOGIN_FAILED_XML)

        self.assertEqual(result.final_outcome, "login_failed")
        self.assertEqual(result.final_login_status, "failed")
        self.assertEqual(result.dashboard_action_type, "update_instagram_password")
        self.assertFalse(result.retry_attempted)

    def test_continue_loading_reobserves_once_to_password_only_without_submit_when_credentials_missing(self) -> None:
        device, selectors = configured_device()
        getter = Mock(return_value=None)
        expected_username = "random_expected"
        device.hierarchies = [CONTINUE_AS_XML, LOADING_XML, PASSWORD_ONLY_XML]

        with patch.object(provisioner_orchestrator.time, "sleep") as sleep:
            result = run_login_provisioning_flow(
                device,
                account_id=ACCOUNT_ID,
                expected_username=expected_username,
                credentials_getter=getter,
                initial_signals={**CONTINUE_SIGNALS, "suggested_username": expected_username},
            )

        self.assertTrue(selectors["continue"].click_calls == 1 or device.bounds_clicks)
        sleep.assert_called_once_with(1.5)
        self.assertEqual(result.safe_metadata["post_continue_initial_screen"], "transition_loading")
        self.assertTrue(result.safe_metadata["post_continue_reobserve"])
        self.assertEqual(result.safe_metadata["post_continue_reobserve_count"], 1)
        self.assertEqual(result.safe_metadata["post_continue_final_screen_type"], "continue_password_only")
        self.assertEqual(result.final_outcome, "credentials_missing")
        self.assertEqual(selectors["password"].set_text_calls, [])
        self.assertNotIn("login_form_submit", result.actions_taken)

    def test_continue_loading_still_unknown_stops_after_one_reobserve(self) -> None:
        device, selectors = configured_device()
        getter = Mock(return_value=credentials())
        expected_username = "random_expected"
        device.hierarchies = [CONTINUE_AS_XML, LOADING_XML, LOADING_XML]

        with patch.object(provisioner_orchestrator.time, "sleep") as sleep:
            result = run_login_provisioning_flow(
                device,
                account_id=ACCOUNT_ID,
                expected_username=expected_username,
                credentials_getter=getter,
                initial_signals={**CONTINUE_SIGNALS, "suggested_username": expected_username},
            )

        self.assertTrue(selectors["continue"].click_calls == 1 or device.bounds_clicks)
        sleep.assert_called_once_with(1.5)
        self.assertEqual(result.failure_reason, "unknown_login_screen")
        self.assertEqual(result.safe_metadata["post_continue_reobserve_count"], 1)
        self.assertEqual(result.safe_metadata["post_continue_final_screen_type"], "unknown")
        getter.assert_not_called()
        self.assertNotIn("login_form_submit", result.actions_taken)

    def test_dry_run_current_password_only_overlay_is_ready_without_submit(self) -> None:
        device, selectors = configured_device()
        getter = Mock(return_value=credentials())

        result = run_login_provisioning_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username=USERNAME,
            credentials_getter=getter,
            initial_signals={
                "screen_type": "continue_password_only",
                "suggested_username": USERNAME,
                "has_password_field": True,
                "has_login_button": True,
                "has_username_field": False,
                "overlay_present": True,
                "overlay_type": "password_manager_or_autofill",
                "overlay_blocking_business": False,
            },
            dry_run=True,
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.safe_metadata["router_decision"], "start_login_form_flow")
        self.assertTrue(result.safe_metadata["password_required"])
        self.assertTrue(result.safe_metadata["ready_for_password_smoke"])
        self.assertTrue(result.safe_metadata["overlay_present"])
        self.assertEqual(result.safe_metadata["overlay_type"], "password_manager_or_autofill")
        self.assertFalse(result.safe_metadata["would_submit_password"])
        getter.assert_not_called()
        self.assertEqual(selectors["login"].click_calls, 0)

    def test_previous_canceled_clone_reusable_uses_another_profile_then_login(self) -> None:
        device, selectors = configured_device()
        device.hierarchies = [CONTINUE_AS_XML, LOGIN_FORM_XML, LOGIN_FORM_XML, CONNECTED_XML]
        lookup = Mock(
            return_value={
                "lifecycle_status": "canceled",
                "clone_reuse_allowed": True,
                "source": "operator_smoke_override",
                "reason": "previous account stopped; clone reusable",
            }
        )

        result = run_login_provisioning_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username=USERNAME,
            credentials_getter=Mock(return_value=credentials()),
            previous_account_lifecycle_lookup=lookup,
            initial_signals=WRONG_CONTINUE_SIGNALS,
        )

        lookup.assert_called_once()
        self.assertTrue(selectors["use_another"].click_calls == 1 or device.bounds_clicks == [(540, 1247)])
        self.assertIn("tap_use_another_profile", result.actions_taken)
        self.assertEqual(result.final_outcome, "connected")
        self.assertEqual(
            result.safe_metadata["previous_account_lifecycle"]["source"],
            "operator_smoke_override",
        )

    def test_previous_canceled_clone_reusable_dry_run_routes_use_another_profile(self) -> None:
        lookup = Mock(
            return_value={
                "lifecycle_status": "canceled",
                "clone_reuse_allowed": True,
                "source": "operator_smoke_override",
                "reason": "previous account stopped; clone reusable",
            }
        )

        result = run_login_provisioning_flow(
            FakeDevice(),
            account_id=ACCOUNT_ID,
            expected_username=USERNAME,
            credentials_getter=Mock(return_value=credentials()),
            previous_account_lifecycle_lookup=lookup,
            initial_signals=WRONG_CONTINUE_SIGNALS,
            dry_run=True,
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.safe_metadata["router_decision"], "use_another_profile_previous_account_stopped")
        self.assertTrue(result.safe_metadata["would_tap_use_another_profile"])
        self.assertEqual(result.safe_metadata["previous_account_lifecycle"]["username"], "random_old_profile")
        self.assertEqual(result.safe_metadata["previous_account_lifecycle"]["lifecycle_status"], "canceled")
        self.assertTrue(result.safe_metadata["previous_account_lifecycle"]["clone_reuse_allowed"])
        self.assertEqual(result.safe_metadata["previous_account_lifecycle"]["source"], "operator_smoke_override")

    def test_previous_canceled_clone_not_reusable_blocks_mismatch(self) -> None:
        getter = Mock(return_value=credentials())

        result = run_login_provisioning_flow(
            FakeDevice(),
            account_id=ACCOUNT_ID,
            expected_username=USERNAME,
            credentials_getter=getter,
            previous_account_lifecycle_lookup=Mock(
                return_value={
                    "lifecycle_status": "canceled",
                    "clone_reuse_allowed": False,
                    "source": "operator_smoke_override",
                }
            ),
            initial_signals=WRONG_CONTINUE_SIGNALS,
            dry_run=True,
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.safe_metadata["router_decision"], "block_wrong_suggested_account")
        self.assertEqual(result.dashboard_action_type, "review_account_mismatch")
        self.assertTrue(result.safe_metadata["would_block_mismatch"])
        getter.assert_not_called()

    def test_previous_active_clone_reusable_blocks_mismatch(self) -> None:
        getter = Mock(return_value=credentials())

        result = run_login_provisioning_flow(
            FakeDevice(),
            account_id=ACCOUNT_ID,
            expected_username=USERNAME,
            credentials_getter=getter,
            previous_account_lifecycle_lookup=Mock(
                return_value={
                    "lifecycle_status": "active",
                    "clone_reuse_allowed": True,
                    "source": "operator_smoke_override",
                }
            ),
            initial_signals=WRONG_CONTINUE_SIGNALS,
            dry_run=True,
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.safe_metadata["router_decision"], "block_wrong_suggested_account")
        self.assertEqual(result.safe_metadata["previous_account_lifecycle"]["lifecycle_status"], "active")
        getter.assert_not_called()

    def test_previous_lifecycle_lookup_absent_blocks_mismatch(self) -> None:
        getter = Mock(return_value=credentials())

        result = run_login_provisioning_flow(
            FakeDevice(),
            account_id=ACCOUNT_ID,
            expected_username=USERNAME,
            credentials_getter=getter,
            initial_signals=WRONG_CONTINUE_SIGNALS,
            dry_run=True,
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.safe_metadata["router_decision"], "block_wrong_suggested_account")
        self.assertEqual(result.dashboard_action_type, "review_account_mismatch")
        getter.assert_not_called()

    def test_wrong_suggested_active_account_blocks_mismatch_no_password(self) -> None:
        device, selectors = configured_device()
        getter = Mock(return_value=credentials())

        result = run_login_provisioning_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username=USERNAME,
            credentials_getter=getter,
            lifecycle_lookup=Mock(return_value={"lifecycle_status": "active"}),
            clone_reuse_allowed=True,
            initial_signals=WRONG_CONTINUE_SIGNALS,
        )

        self.assertEqual(result.final_outcome, "mismatch")
        self.assertEqual(result.final_login_status, "mismatch")
        self.assertEqual(result.dashboard_action_type, "review_account_mismatch")
        getter.assert_not_called()
        self.assertEqual(selectors["password"].set_text_calls, [])

    def test_unknown_screen_no_action_no_password(self) -> None:
        device, selectors = configured_device()
        getter = Mock(return_value=credentials())

        result = run_login_provisioning_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username=USERNAME,
            credentials_getter=getter,
            initial_signals={"screen_type": "unknown"},
        )

        self.assertEqual(result.failure_reason, "unknown_login_screen")
        getter.assert_not_called()
        self.assertEqual(selectors["password"].set_text_calls, [])

    def test_missing_credentials_creates_credentials_action_no_executor(self) -> None:
        device, selectors = configured_device()

        result = run_login_provisioning_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username=USERNAME,
            credentials_getter=Mock(return_value=None),
            initial_signals=LOGIN_FORM_SIGNALS,
        )

        self.assertEqual(result.final_outcome, "credentials_missing")
        self.assertEqual(result.dashboard_action_type, "submit_instagram_credentials")
        self.assertEqual(selectors["password"].set_text_calls, [])

    def test_invalid_secret_value_creates_update_password_action_no_executor(self) -> None:
        device, selectors = configured_device()

        result = run_login_provisioning_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username=USERNAME,
            credentials_getter=Mock(return_value={"username": USERNAME, "password": "not-secret"}),
            initial_signals=LOGIN_FORM_SIGNALS,
        )

        self.assertEqual(result.final_outcome, "credentials_invalid")
        self.assertEqual(result.dashboard_action_type, "update_instagram_password")
        self.assertEqual(selectors["password"].set_text_calls, [])

    def test_transient_username_field_not_found_retries_once_after_revalidation(self) -> None:
        device, selectors = configured_device(CONNECTED_XML)
        selectors["username"]._count = 0
        observed = {"done": False}

        def dump_with_recovery(compressed: bool = False) -> str:
            device.dump_calls += 1
            if not observed["done"]:
                observed["done"] = True
                selectors["username"]._count = 1
                return LOGIN_FORM_XML
            return CONNECTED_XML

        device.dump_hierarchy = dump_with_recovery

        result = run_login_provisioning_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username=USERNAME,
            credentials_getter=Mock(return_value=credentials()),
            initial_signals=LOGIN_FORM_SIGNALS,
        )

        self.assertTrue(result.retry_attempted)
        self.assertEqual(result.retry_count, 1)
        self.assertEqual(result.final_outcome, "connected")

    def test_transient_still_failing_after_retry_stops_safe(self) -> None:
        device, selectors = configured_device(LOGIN_FORM_XML)
        selectors["username"]._count = 0

        result = run_login_provisioning_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username=USERNAME,
            credentials_getter=Mock(return_value=credentials()),
            initial_signals=LOGIN_FORM_SIGNALS,
        )

        self.assertTrue(result.retry_attempted)
        self.assertEqual(result.retry_count, 1)
        self.assertEqual(result.failure_reason, "username_field_not_found")

    def test_input_failed_retry_max_one(self) -> None:
        device, selectors = configured_device(LOGIN_FORM_XML)
        selectors["username"].set_exc = RuntimeError("input boom")

        result = run_login_provisioning_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username=USERNAME,
            credentials_getter=Mock(return_value=credentials()),
            initial_signals=LOGIN_FORM_SIGNALS,
        )

        self.assertEqual(result.retry_count, 1)
        self.assertEqual(result.failure_reason, "input_failed")

    def test_submit_failed_retry_max_one(self) -> None:
        device, selectors = configured_device(LOGIN_FORM_XML)
        selectors["login"].click_exc = RuntimeError("submit boom")

        result = run_login_provisioning_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username=USERNAME,
            credentials_getter=Mock(return_value=credentials()),
            initial_signals=LOGIN_FORM_SIGNALS,
        )

        self.assertEqual(result.retry_count, 1)
        self.assertEqual(result.failure_reason, "submit_failed")

    def test_no_retry_for_login_failed(self) -> None:
        result = self._run_login_form(LOGIN_FAILED_XML)
        self.assertFalse(result.retry_attempted)

    def test_no_retry_for_needs_2fa(self) -> None:
        result = self._run_login_form(NEEDS_2FA_XML)
        self.assertFalse(result.retry_attempted)

    def test_no_retry_for_checkpoint(self) -> None:
        result = self._run_login_form(CHECKPOINT_XML)
        self.assertFalse(result.retry_attempted)

    def test_publisher_disabled_by_default(self) -> None:
        publisher = Mock(return_value={"published": True})

        result = self._run_login_form(CONNECTED_XML, publisher=publisher)

        self.assertFalse(result.published)
        self.assertEqual(result.publish_reason, "disabled")
        publisher.assert_not_called()

    def test_publisher_called_only_when_enabled(self) -> None:
        publisher = Mock(return_value={"published": True, "reason": "published"})

        result = self._run_login_form(CONNECTED_XML, publisher=publisher, publish_enabled=True)

        self.assertTrue(result.published)
        self.assertEqual(result.publish_reason, "published")
        publisher.assert_called_once()

    def test_publish_payload_safe(self) -> None:
        publisher = Mock(return_value={"published": True})

        result = self._run_login_form(CONNECTED_XML, publisher=publisher, publish_enabled=True)
        rendered = json.dumps(result.publish_payload, sort_keys=True)

        self.assertNotIn(PASSWORD, rendered)
        self.assertNotIn(SECRET_REF, rendered)
        self.assertNotIn(VAULT_ID, rendered)

    def test_result_safe_dict_no_password(self) -> None:
        result = self._run_login_form(SENSITIVE_XML)
        rendered = json.dumps(asdict(result), sort_keys=True)

        self.assertNotIn(PASSWORD, rendered)
        self.assertNotIn("fake-password", rendered)
        self.assertNotIn("password secret_ref", rendered)

    def test_result_safe_dict_no_secret_ref_vault_token_device_xml_screenshot(self) -> None:
        result = self._run_login_form(SENSITIVE_XML)
        rendered = json.dumps(asdict(result), sort_keys=True)

        for forbidden in ("secret_ref", "vault", "Vault", "token", "emulator-5554", "device_udid", "adb_serial", "xml", "screenshot"):
            self.assertNotIn(forbidden, rendered)

    def test_lifecycle_lookup_exception_blocks_safe_mismatch_unknown(self) -> None:
        result = run_login_provisioning_flow(
            FakeDevice(),
            account_id=ACCOUNT_ID,
            expected_username=USERNAME,
            credentials_getter=Mock(return_value=credentials()),
            previous_account_lifecycle_lookup=Mock(side_effect=RuntimeError("lookup down")),
            initial_signals=WRONG_CONTINUE_SIGNALS,
        )

        self.assertEqual(result.final_outcome, "mismatch")
        self.assertEqual(result.final_provisioning_status, "blocked")
        self.assertIn("lifecycle_lookup_failed", result.reason)

    def test_timings_and_warnings_present(self) -> None:
        result = self._run_login_form(CONNECTED_XML)

        self.assertIn("total_ms", result.timings)
        self.assertIsInstance(result.warnings, list)

    def test_actions_taken_order_for_direct_login_form(self) -> None:
        result = self._run_login_form(CONNECTED_XML)

        self.assertEqual(result.actions_taken, ["route:start_login_form_flow", "login_form_submit"])

    def test_dry_run_login_form_does_not_request_credentials_or_submit(self) -> None:
        device, selectors = configured_device()
        getter = Mock(return_value=credentials())

        result = run_login_provisioning_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username=USERNAME,
            credentials_getter=getter,
            initial_signals=LOGIN_FORM_SIGNALS,
            dry_run=True,
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.final_outcome, "dry_run")
        self.assertTrue(result.safe_metadata["would_request_credentials"])
        self.assertFalse(result.safe_metadata["would_submit_password"])
        self.assertFalse(result.safe_metadata["would_publish"])
        self.assertTrue(result.safe_metadata["ready_for_credentials_flow"])
        self.assertTrue(result.safe_metadata["ready_for_password_smoke"])
        getter.assert_not_called()
        self.assertEqual(selectors["login"].click_calls, 0)

    def test_dry_run_continue_as_expected_previews_continue_only(self) -> None:
        device, selectors = configured_device()
        getter = Mock(return_value=credentials())

        result = run_login_provisioning_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username=USERNAME,
            credentials_getter=getter,
            initial_signals=CONTINUE_SIGNALS,
            dry_run=True,
        )

        self.assertTrue(result.safe_metadata["would_tap_continue"])
        self.assertFalse(result.safe_metadata["would_submit_password"])
        self.assertTrue(result.safe_metadata["smoke_ready_for_real_login"])
        getter.assert_not_called()
        self.assertEqual(selectors["continue"].click_calls, 0)

    def test_dry_run_account_picker_previews_expected_account_only(self) -> None:
        device, _selectors = configured_device()
        getter = Mock(return_value=credentials())

        result = run_login_provisioning_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username="random_expected",
            credentials_getter=getter,
            initial_signals=ACCOUNT_PICKER_SIGNALS,
            dry_run=True,
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.safe_metadata["router_decision"], "select_expected_account_from_picker")
        self.assertTrue(result.safe_metadata["would_tap_expected_account"])
        self.assertFalse(result.safe_metadata["would_submit_password"])
        self.assertFalse(result.safe_metadata["would_publish"])
        getter.assert_not_called()

    def test_account_picker_tap_expected_then_connected_finalizes_without_password(self) -> None:
        device, _selectors = configured_device()
        getter = Mock(return_value=credentials())
        device.hierarchies = [ACCOUNT_PICKER_XML, CONNECTED_XML, CONNECTED_XML]

        result = run_login_provisioning_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username="random_expected",
            credentials_getter=getter,
            initial_signals=ACCOUNT_PICKER_SIGNALS,
        )

        self.assertEqual(result.final_outcome, "connected")
        self.assertIn("tap_expected_account", result.actions_taken)
        self.assertFalse(result.safe_metadata["password_required"])
        self.assertFalse(result.safe_metadata["would_submit_password"])
        getter.assert_not_called()

    def test_account_picker_tap_expected_then_password_only_stops_before_submit_without_credentials(self) -> None:
        device, _selectors = configured_device()
        getter = Mock(return_value=None)
        device.hierarchies = [ACCOUNT_PICKER_XML, PASSWORD_ONLY_XML, PASSWORD_ONLY_XML]

        result = run_login_provisioning_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username="random_expected",
            credentials_getter=getter,
            initial_signals=ACCOUNT_PICKER_SIGNALS,
        )

        self.assertEqual(result.final_outcome, "credentials_missing")
        self.assertIn("tap_expected_account", result.actions_taken)
        self.assertTrue(result.safe_metadata["password_required"])
        self.assertTrue(result.safe_metadata["ready_for_password_submit"])
        self.assertFalse(result.safe_metadata["would_submit_password"])
        self.assertNotIn("login_form_submit", result.actions_taken)

    def test_account_picker_loading_reobserves_once_to_password_only(self) -> None:
        device, _selectors = configured_device()
        getter = Mock(return_value=None)
        device.hierarchies = [ACCOUNT_PICKER_XML, LOADING_XML, PASSWORD_ONLY_XML]

        with patch.object(provisioner_orchestrator.time, "sleep") as sleep:
            result = run_login_provisioning_flow(
                device,
                account_id=ACCOUNT_ID,
                expected_username="random_expected",
                credentials_getter=getter,
                initial_signals=ACCOUNT_PICKER_SIGNALS,
            )

        sleep.assert_called_once_with(1.5)
        self.assertEqual(result.safe_metadata["post_account_picker_initial_screen"], "transition_loading")
        self.assertTrue(result.safe_metadata["post_account_picker_reobserve"])
        self.assertEqual(result.safe_metadata["post_account_picker_reobserve_count"], 1)
        self.assertEqual(result.safe_metadata["post_account_picker_final_screen_type"], "continue_password_only")
        self.assertEqual(result.final_outcome, "credentials_missing")
        self.assertFalse(result.safe_metadata["would_submit_password"])

    def test_account_picker_unknown_transition_reobserves_once_to_password_only(self) -> None:
        device, _selectors = configured_device()
        getter = Mock(return_value=None)
        device.hierarchies = [ACCOUNT_PICKER_XML, UNKNOWN_XML, PASSWORD_ONLY_XML]

        with patch.object(provisioner_orchestrator.time, "sleep") as sleep:
            result = run_login_provisioning_flow(
                device,
                account_id=ACCOUNT_ID,
                expected_username="random_expected",
                credentials_getter=getter,
                initial_signals=ACCOUNT_PICKER_SIGNALS,
            )

        sleep.assert_called_once_with(1.5)
        self.assertEqual(result.safe_metadata["post_account_picker_initial_screen"], "transition_unknown")
        self.assertEqual(result.safe_metadata["post_account_picker_reobserve_count"], 1)
        self.assertEqual(result.safe_metadata["post_account_picker_final_screen_type"], "continue_password_only")
        self.assertEqual(result.final_outcome, "credentials_missing")

    def test_old_logged_in_expected_account_profile_finalizes_connected(self) -> None:
        device, _selectors = configured_device()
        getter = Mock(return_value=credentials())
        device.hierarchies = [ACTIVE_PROFILE_EXPECTED_XML]

        result = run_login_provisioning_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username="random_expected",
            credentials_getter=getter,
        )

        self.assertEqual(result.final_outcome, "connected")
        self.assertFalse(result.safe_metadata["old_logged_in_recovery_attempted"])
        getter.assert_not_called()

    def test_old_logged_in_active_account_blocks_without_tap(self) -> None:
        device, _selectors = configured_device()
        getter = Mock(return_value=credentials())
        device.hierarchies = [ACTIVE_PROFILE_OLD_XML]

        result = run_login_provisioning_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username="random_expected",
            credentials_getter=getter,
            previous_account_lifecycle_lookup=Mock(
                return_value={"lifecycle_status": "active", "clone_reuse_allowed": True}
            ),
        )

        self.assertEqual(result.final_outcome, "mismatch")
        self.assertEqual(result.safe_metadata["lifecycle_gate_result"], "block_wrong_active_account")
        self.assertFalse(result.safe_metadata["old_logged_in_recovery_allowed"])
        self.assertEqual(device.bounds_clicks, [])
        getter.assert_not_called()

    def test_old_logged_in_lifecycle_missing_blocks_without_tap(self) -> None:
        device, _selectors = configured_device()
        device.hierarchies = [ACTIVE_PROFILE_OLD_XML]

        result = run_login_provisioning_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username="random_expected",
            credentials_getter=Mock(return_value=credentials()),
        )

        self.assertEqual(result.final_outcome, "mismatch")
        self.assertFalse(result.safe_metadata["old_logged_in_recovery_allowed"])
        self.assertEqual(device.bounds_clicks, [])

    def test_old_logged_in_lifecycle_exception_blocks_without_tap(self) -> None:
        device, _selectors = configured_device()
        device.hierarchies = [ACTIVE_PROFILE_OLD_XML]

        result = run_login_provisioning_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username="random_expected",
            credentials_getter=Mock(return_value=credentials()),
            previous_account_lifecycle_lookup=Mock(side_effect=RuntimeError("lookup down")),
        )

        self.assertEqual(result.final_outcome, "mismatch")
        self.assertFalse(result.safe_metadata["old_logged_in_recovery_allowed"])
        self.assertEqual(device.bounds_clicks, [])

    def test_old_logged_in_canceled_recovery_to_login_form_no_password(self) -> None:
        device, _selectors = configured_device()
        getter = Mock(return_value=None)
        device.hierarchies = [
            ACTIVE_HOME_XML,
            ACTIVE_HOME_XML,
            ACTIVE_PROFILE_OLD_XML,
            ACTIVE_PROFILE_OLD_XML,
            ACTIVE_PROFILE_OLD_XML,
            ACCOUNT_SWITCHER_XML,
            ACCOUNT_SWITCHER_XML,
            ACCOUNT_SWITCHER_XML,
            ADD_ACCOUNT_SHEET_XML,
            ADD_ACCOUNT_SHEET_XML,
            ADD_ACCOUNT_SHEET_XML,
            LOGIN_FORM_XML,
            LOGIN_FORM_XML,
        ]

        result = run_login_provisioning_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username="random_expected",
            credentials_getter=getter,
            previous_account_lifecycle_lookup=Mock(
                return_value={
                    "lifecycle_status": "canceled",
                    "clone_reuse_allowed": True,
                    "source": "operator_smoke_override",
                    "reason": "old canceled account still logged in",
                }
            ),
        )

        self.assertEqual(result.final_outcome, "credentials_missing")
        self.assertIn("tap_profile_bottom_nav", result.actions_taken)
        self.assertIn("tap_account_switcher", result.actions_taken)
        self.assertIn("tap_add_instagram_account", result.actions_taken)
        self.assertIn("tap_log_into_existing_account", result.actions_taken)
        self.assertTrue(result.safe_metadata["old_logged_in_recovery_attempted"])
        self.assertFalse(result.safe_metadata["logout_attempted"])
        self.assertFalse(result.safe_metadata["would_submit_password"])
        self.assertNotIn("login_form_submit", result.actions_taken)

    def test_old_logged_in_recovery_can_return_continue_as_candidate(self) -> None:
        device, _selectors = configured_device()
        getter = Mock(return_value=credentials())
        device.hierarchies = [
            ACTIVE_PROFILE_OLD_XML,
            ACTIVE_PROFILE_OLD_XML,
            ACCOUNT_SWITCHER_XML,
            ACCOUNT_SWITCHER_XML,
            ACCOUNT_SWITCHER_XML,
            ADD_ACCOUNT_SHEET_XML,
            ADD_ACCOUNT_SHEET_XML,
            ADD_ACCOUNT_SHEET_XML,
            CONTINUE_AS_XML,
            CONTINUE_AS_XML,
        ]

        result = run_login_provisioning_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username="random_expected",
            credentials_getter=getter,
            previous_account_lifecycle_lookup=Mock(
                return_value={"lifecycle_status": "canceled", "clone_reuse_allowed": True}
            ),
        )

        self.assertEqual(result.final_outcome, "unknown")
        self.assertIn("route:unknown_no_action", result.actions_taken)
        getter.assert_not_called()

    def test_old_logged_in_recovery_can_return_account_picker(self) -> None:
        device, _selectors = configured_device()
        getter = Mock(return_value=credentials())
        device.hierarchies = [
            ACTIVE_PROFILE_OLD_XML,
            ACTIVE_PROFILE_OLD_XML,
            ACCOUNT_SWITCHER_XML,
            ACCOUNT_SWITCHER_XML,
            ACCOUNT_SWITCHER_XML,
            ADD_ACCOUNT_SHEET_XML,
            ADD_ACCOUNT_SHEET_XML,
            ADD_ACCOUNT_SHEET_XML,
            ACCOUNT_PICKER_XML,
            ACCOUNT_PICKER_XML,
        ]

        result = run_login_provisioning_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username="random_expected",
            credentials_getter=getter,
            previous_account_lifecycle_lookup=Mock(
                return_value={"lifecycle_status": "canceled", "clone_reuse_allowed": True}
            ),
        )

        self.assertIn("route:select_expected_account_from_picker", result.actions_taken)
        getter.assert_not_called()

    def test_logout_fallback_profile_menu_visible_full_no_password(self) -> None:
        device = FakeDevice(
            [
                ACTIVE_PROFILE_OLD_MENU_XML,
                PROFILE_MENU_SHEET_XML,
                PROFILE_MENU_SHEET_XML,
                PROFILE_MENU_SHEET_XML,
                SETTINGS_AND_ACTIVITY_XML,
                SETTINGS_AND_ACTIVITY_XML,
                SETTINGS_AND_ACTIVITY_XML,
                SAVE_LOGIN_INFO_PROMPT_XML,
                SAVE_LOGIN_INFO_PROMPT_XML,
                SAVE_LOGIN_INFO_PROMPT_XML,
                LOGOUT_CONFIRMATION_PROMPT_XML,
                LOGOUT_CONFIRMATION_PROMPT_XML,
                LOGOUT_CONFIRMATION_PROMPT_XML,
                LOGIN_FORM_XML,
                LOGIN_FORM_XML,
            ]
        )

        result = run_old_account_logout_fallback_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username="random_expected",
            previous_account_lifecycle_lookup=self._canceled_lifecycle(),
            initial_signals=self._logout_initial_signals(),
            sleeper=Mock(),
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.final_outcome, "login_form_empty")
        self.assertEqual(result.safe_metadata["actual_logged_in_username"], "random_old_profile")
        self.assertEqual(result.safe_metadata["lifecycle_gate_result"], "allow_logout_fallback")
        self.assertFalse(result.safe_metadata["profile_menu_initially_missing"])
        self.assertTrue(result.safe_metadata["profile_menu_final_found"])
        self.assertTrue(result.safe_metadata["save_login_prompt_handled"])
        self.assertTrue(result.safe_metadata["logout_confirmation_handled"])
        self.assertFalse(result.safe_metadata["would_submit_password"])
        self.assertFalse(result.safe_metadata["would_publish"])
        self.assertIn("tap_not_now", result.actions_taken)
        self.assertIn("tap_confirm_logout", result.actions_taken)
        self.assertNotIn("login_form_submit", result.actions_taken)

    def test_logout_fallback_profile_menu_appears_after_wait(self) -> None:
        device = FakeDevice(
            [
                ACTIVE_PROFILE_OLD_MENU_XML,
                ACTIVE_PROFILE_OLD_MENU_XML,
                PROFILE_MENU_SHEET_XML,
                PROFILE_MENU_SHEET_XML,
                PROFILE_MENU_SHEET_XML,
                SETTINGS_AND_ACTIVITY_XML,
                SETTINGS_AND_ACTIVITY_XML,
                SETTINGS_AND_ACTIVITY_XML,
                LOGOUT_CONFIRMATION_PROMPT_XML,
                LOGOUT_CONFIRMATION_PROMPT_XML,
                LOGOUT_CONFIRMATION_PROMPT_XML,
                LOGOUT_CONTINUE_AS_XML,
                LOGOUT_CONTINUE_AS_XML,
            ]
        )

        result = run_old_account_logout_fallback_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username="random_expected",
            previous_account_lifecycle_lookup=self._canceled_lifecycle(),
            initial_signals=self._logout_initial_signals(ACTIVE_PROFILE_OLD_XML),
            sleeper=Mock(),
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.final_outcome, "continue_as_candidate")
        self.assertTrue(result.safe_metadata["profile_menu_initially_missing"])
        self.assertTrue(result.safe_metadata["profile_menu_wait_reobserve"])
        self.assertFalse(result.safe_metadata["profile_menu_home_profile_refresh_attempted"])

    def test_logout_fallback_profile_menu_appears_after_home_profile_refresh(self) -> None:
        device = FakeDevice(
            [
                ACTIVE_PROFILE_OLD_XML,
                ACTIVE_HOME_XML,
                ACTIVE_HOME_XML,
                ACTIVE_PROFILE_OLD_MENU_XML,
                ACTIVE_PROFILE_OLD_MENU_XML,
                ACTIVE_PROFILE_OLD_MENU_XML,
                ACTIVE_PROFILE_OLD_MENU_XML,
                PROFILE_MENU_SHEET_XML,
                PROFILE_MENU_SHEET_XML,
                PROFILE_MENU_SHEET_XML,
                SETTINGS_AND_ACTIVITY_XML,
                SETTINGS_AND_ACTIVITY_XML,
                SETTINGS_AND_ACTIVITY_XML,
                LOGOUT_CONFIRMATION_PROMPT_XML,
                LOGOUT_CONFIRMATION_PROMPT_XML,
                LOGOUT_CONFIRMATION_PROMPT_XML,
                ACCOUNT_PICKER_XML,
                ACCOUNT_PICKER_XML,
            ]
        )

        result = run_old_account_logout_fallback_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username="random_expected",
            previous_account_lifecycle_lookup=self._canceled_lifecycle(),
            initial_signals=self._logout_initial_signals(ACTIVE_PROFILE_OLD_XML),
            sleeper=Mock(),
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.final_outcome, "account_picker")
        self.assertTrue(result.safe_metadata["profile_menu_home_profile_refresh_attempted"])
        self.assertIn("tap_home_bottom_nav", result.actions_taken)
        self.assertIn("tap_profile_bottom_nav", result.actions_taken)

    def test_logout_fallback_menu_still_missing_stops_safe(self) -> None:
        device = FakeDevice([ACTIVE_PROFILE_OLD_XML, ACTIVE_HOME_XML, ACTIVE_HOME_XML, ACTIVE_PROFILE_OLD_XML])

        result = run_old_account_logout_fallback_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username="random_expected",
            previous_account_lifecycle_lookup=self._canceled_lifecycle(),
            initial_signals=self._logout_initial_signals(ACTIVE_PROFILE_OLD_XML),
            sleeper=Mock(),
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.failure_reason, "profile_menu_not_found")
        self.assertFalse(result.safe_metadata["profile_menu_final_found"])
        self.assertNotIn("tap_logout", result.actions_taken)

    def test_logout_fallback_username_change_during_refresh_stops_safe(self) -> None:
        device = FakeDevice([ACTIVE_PROFILE_EXPECTED_MENU_XML])

        result = run_old_account_logout_fallback_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username="random_expected",
            previous_account_lifecycle_lookup=self._canceled_lifecycle(),
            initial_signals=self._logout_initial_signals(ACTIVE_PROFILE_OLD_XML),
            sleeper=Mock(),
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.failure_reason, "username_changed")
        self.assertNotIn("tap_logout", result.actions_taken)

    def test_logout_fallback_active_lifecycle_blocks_without_logout(self) -> None:
        device = FakeDevice([ACTIVE_PROFILE_OLD_MENU_XML])

        result = run_old_account_logout_fallback_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username="random_expected",
            previous_account_lifecycle_lookup=Mock(
                return_value={"lifecycle_status": "active", "clone_reuse_allowed": True}
            ),
            initial_signals=self._logout_initial_signals(),
            sleeper=Mock(),
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.safe_metadata["lifecycle_gate_result"], "block_wrong_active_account")
        self.assertEqual(result.dashboard_action_type, "review_logged_in_account_mismatch")
        self.assertNotIn("tap_logout", result.actions_taken)

    def test_logout_fallback_missing_or_exception_lifecycle_blocks(self) -> None:
        for lookup in (None, Mock(side_effect=RuntimeError("lookup down"))):
            with self.subTest(lookup=lookup):
                device = FakeDevice([ACTIVE_PROFILE_OLD_MENU_XML])
                result = run_old_account_logout_fallback_flow(
                    device,
                    account_id=ACCOUNT_ID,
                    expected_username="random_expected",
                    previous_account_lifecycle_lookup=lookup,
                    initial_signals=self._logout_initial_signals(),
                    sleeper=Mock(),
                )

                self.assertFalse(result.ok)
                self.assertEqual(result.safe_metadata["lifecycle_gate_result"], "block_wrong_active_account")
                self.assertNotIn("tap_logout", result.actions_taken)

    def test_logout_fallback_never_logs_out_expected_username(self) -> None:
        device = FakeDevice([ACTIVE_PROFILE_EXPECTED_MENU_XML])

        result = run_old_account_logout_fallback_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username="random_expected",
            previous_account_lifecycle_lookup=self._canceled_lifecycle(),
            initial_signals=self._logout_initial_signals(ACTIVE_PROFILE_EXPECTED_MENU_XML),
            sleeper=Mock(),
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.failure_reason, "no_logout_expected_username")
        self.assertNotIn("tap_logout", result.actions_taken)

    def test_logout_fallback_final_unknown_after_reobserve_stops_safe(self) -> None:
        device = FakeDevice(
            [
                ACTIVE_PROFILE_OLD_MENU_XML,
                PROFILE_MENU_SHEET_XML,
                PROFILE_MENU_SHEET_XML,
                PROFILE_MENU_SHEET_XML,
                SETTINGS_AND_ACTIVITY_XML,
                SETTINGS_AND_ACTIVITY_XML,
                SETTINGS_AND_ACTIVITY_XML,
                LOGOUT_CONFIRMATION_PROMPT_XML,
                LOGOUT_CONFIRMATION_PROMPT_XML,
                LOGOUT_CONFIRMATION_PROMPT_XML,
                UNKNOWN_XML,
                UNKNOWN_XML,
                UNKNOWN_XML,
            ]
        )

        result = run_old_account_logout_fallback_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username="random_expected",
            previous_account_lifecycle_lookup=self._canceled_lifecycle(),
            initial_signals=self._logout_initial_signals(),
            sleeper=Mock(),
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.failure_reason, "post_logout_unknown_screen")
        self.assertTrue(result.safe_metadata["post_logout_reobserve"])

    def test_logout_fallback_no_leak_metadata(self) -> None:
        device = FakeDevice([ACTIVE_PROFILE_OLD_MENU_XML])

        result = run_old_account_logout_fallback_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username="random_expected",
            previous_account_lifecycle_lookup=Mock(
                return_value={
                    "lifecycle_status": "active",
                    "clone_reuse_allowed": True,
                    "reason": "password secret_ref Vault token emulator-5554 screenshot",
                }
            ),
            initial_signals=self._logout_initial_signals(),
            sleeper=Mock(),
        )

        payload = json.dumps(result.safe_metadata)
        for forbidden in ("secret_ref", "Vault", VAULT_ID, "token", "emulator-5554", "screenshot"):
            self.assertNotIn(forbidden, payload)

    def test_dry_run_wrong_candidate_blocks_mismatch_without_db_assumption(self) -> None:
        getter = Mock(return_value=credentials())

        result = run_login_provisioning_flow(
            FakeDevice(),
            account_id=ACCOUNT_ID,
            expected_username=USERNAME,
            credentials_getter=getter,
            initial_signals=WRONG_CONTINUE_SIGNALS,
            dry_run=True,
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.dashboard_action_type, "review_account_mismatch")
        self.assertTrue(result.safe_metadata["would_block_mismatch"])
        self.assertEqual(result.safe_metadata["suggested_username"], "random_old_profile")
        self.assertFalse(result.safe_metadata["would_submit_password"])
        getter.assert_not_called()

    def test_previous_lifecycle_metadata_does_not_leak_secret_device_or_raw_ui(self) -> None:
        result = run_login_provisioning_flow(
            FakeDevice(),
            account_id=ACCOUNT_ID,
            expected_username=USERNAME,
            credentials_getter=Mock(return_value=credentials()),
            previous_account_lifecycle_lookup=Mock(
                return_value={
                    "lifecycle_status": "canceled",
                    "clone_reuse_allowed": True,
                    "source": "operator_smoke_override",
                    "reason": "password secret_ref Vault token emulator-5554 xml screenshot",
                }
            ),
            initial_signals=WRONG_CONTINUE_SIGNALS,
            dry_run=True,
        )
        rendered = json.dumps(asdict(result), sort_keys=True)

        self.assertEqual(result.safe_metadata["previous_account_lifecycle"]["reason"], "")
        for forbidden in (PASSWORD, SECRET_REF, VAULT_ID, "secret_ref", "vault", "Vault", "token", "emulator-5554", "device_udid", "adb_serial", "xml", "screenshot"):
            self.assertNotIn(forbidden, rendered)

    def test_no_observed_username_hardcoded_in_application_logic(self) -> None:
        source = inspect.getsource(provisioner_orchestrator)

        self.assertNotIn("i_m_your_traker", source)

    def test_dry_run_output_has_no_secret_material_or_raw_ui(self) -> None:
        result = run_login_provisioning_flow(
            FakeDevice([SENSITIVE_XML]),
            account_id=ACCOUNT_ID,
            expected_username=USERNAME,
            credentials_getter=Mock(return_value=credentials()),
            dry_run=True,
        )
        rendered = json.dumps(asdict(result), sort_keys=True)

        for forbidden in (PASSWORD, SECRET_REF, VAULT_ID, "secret_ref", "vault", "Vault", "token", "emulator-5554", "xml", "screenshot"):
            self.assertNotIn(forbidden, rendered)

    def _run_login_form(self, xml: str, *, publisher=None, publish_enabled: bool = False):
        device, _selectors = configured_device(xml)
        return run_login_provisioning_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username=USERNAME,
            credentials_getter=Mock(return_value=credentials()),
            initial_signals=LOGIN_FORM_SIGNALS,
            publisher=publisher,
            publish_enabled=publish_enabled,
        )

    def _run_continue_password_only(self, post_submit_xml: str):
        device, _selectors = configured_device()
        expected_username = "random_expected"
        device.hierarchies = [CONTINUE_AS_XML, PASSWORD_ONLY_XML, PASSWORD_ONLY_XML, post_submit_xml]
        return run_login_provisioning_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username=expected_username,
            credentials_getter=Mock(return_value={"username": expected_username, "password": SecretValue(PASSWORD)}),
            initial_signals={**CONTINUE_SIGNALS, "suggested_username": expected_username},
        )


if __name__ == "__main__":
    unittest.main()
