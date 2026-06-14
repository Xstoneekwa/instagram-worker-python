from __future__ import annotations

import json
import inspect
import time
import unittest
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import Mock, patch

from instagram_credentials_runtime_access import InstagramLoginCredentialsResult, SecretValue
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
JOIN_INSTAGRAM_SIGNALS = {
    "screen_type": "join_instagram_landing",
    "join_instagram_landing_detected": True,
    "has_join_instagram_title": True,
    "has_join_instagram_subtitle": True,
    "has_get_started_button": True,
    "has_already_have_profile_button": True,
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
PREFILLED_LOGIN_FORM_XML = (
    '<node class="android.widget.EditText" text="random_old_profile" editable="true" />'
    '<node class="android.widget.EditText" text="Password" editable="true" />'
    '<node text="Log in" />'
    '<node text="Create new account" />'
    '<node text="Meta" />'
)
CONTINUE_AS_XML = (
    '<node text="Continue" clickable="true" bounds="[100,1000][980,1120]" />'
    '<node text="Use another profile" clickable="false" bounds="[371,1215][710,1280]" />'
    '<node text="Create new account" clickable="true" bounds="[100,2000][980,2190]" />'
)
JOIN_INSTAGRAM_XML = (
    '<node text="Join Instagram" />'
    '<node text="Share what you&apos;re into with the people who get you." />'
    '<node text="Get started" clickable="true" bounds="[100,1480][980,1600]" />'
    '<node text="I already have a profile" clickable="true" bounds="[100,1640][980,1760]" />'
    '<node text="Meta" />'
)
JOIN_INSTAGRAM_WITHOUT_EXISTING_XML = (
    '<node text="Join Instagram" />'
    '<node text="Share what you&apos;re into with the people who get you." />'
    '<node text="Get started" clickable="true" bounds="[100,1480][980,1600]" />'
    '<node text="Meta" />'
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
GOOGLE_SAVE_PASSWORD_PROMPT_XML = (
    '<node text="Google Password Manager" />'
    '<node text="Save password for Instagram?" />'
    '<node text="cinema_catchup" />'
    '<node text="••••••••••" />'
    '<node text="Continue" clickable="true" />'
)
CONNECTED_XML = (
    '<node content-desc="Home" />'
    '<node content-desc="Search" />'
    '<node content-desc="Reels" />'
    '<node content-desc="Profile" />'
)
POST_LOGIN_LOCATION_SERVICES_PROMPT_XML = (
    '<node text="Set up on new device" />'
    '<node text="To use Location services, allow Instagram to access your location" />'
    '<node text="How you can use location services" />'
    '<node text="How we&apos;ll use this information" />'
    '<node text="How you can control this" />'
    '<node text="Continue" clickable="true" />'
)
NEEDS_2FA_XML = '<node text="Enter code" /><node text="authentication code" />'
CHECKPOINT_XML = '<node text="Help us confirm it’s you" /><node text="Verify your account" />'
LOGIN_FAILED_XML = '<node text="Sorry, your password was incorrect. Please try again." />'
EMAIL_CODE_CHALLENGE_XML = (
    '<node text="Check your email" />'
    '<node text="Enter the code we sent to m*******e@hotmail.com" />'
    '<node class="android.widget.EditText" text="Enter code" editable="true" />'
    '<node text="Get a new code" />'
    '<node text="Continue" />'
    '<node text="Try another way" />'
)
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
SETTINGS_AND_ACTIVITY_TOP_XML = (
    '<node text="Settings and activity" />'
    '<node text="Account type and tools" clickable="true" bounds="[80,520][980,640]" />'
    '<node text="Fundraisers" clickable="true" bounds="[80,780][980,900]" />'
    '<node text="Orders and payments" clickable="true" bounds="[80,900][980,1020]" />'
    '<node text="More info and support" />'
    '<node text="Help" clickable="true" bounds="[80,1120][980,1240]" />'
    '<node text="Privacy Center" clickable="true" bounds="[80,1240][980,1360]" />'
    '<node text="Account Status" clickable="true" bounds="[80,1360][980,1480]" />'
    '<node text="About" clickable="true" bounds="[80,1480][980,1600]" />'
    '<node text="Also from Meta" />'
    '<node text="WhatsApp" clickable="true" bounds="[80,1720][980,1840]" />'
    '<node text="Threads" clickable="true" bounds="[80,1960][980,2080]" />'
    '<node text="Facebook" clickable="true" bounds="[80,2080][980,2200]" />'
)
SETTINGS_AND_ACTIVITY_FRENCH_LOGOUT_XML = (
    '<node text="Settings and activity" />'
    '<node text="More info and support" />'
    '<node text="Login" />'
    '<node text="Déconnexion" clickable="true" bounds="[100,1900][980,2020]" />'
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
LOGOUT_CONTINUE_AS_OLD_XML = (
    '<node text="random_old_profile" />'
    '<node text="Continue" clickable="true" bounds="[100,1000][980,1120]" />'
    '<node text="Use another profile" clickable="true" bounds="[371,1215][710,1280]" />'
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


class ConfirmingTextSelector(FakeSelector):
    def __init__(self) -> None:
        super().__init__(1)
        self.text = ""

    def clear_text(self) -> None:
        self.text = ""

    def set_text(self, value: str) -> None:
        super().set_text(value)
        self.text = value

    def info(self) -> dict[str, str]:
        return {"text": self.text}


class FakeScroll:
    def __init__(self, result: bool = True) -> None:
        self.result = result
        self.forward_calls = 0
        self.to_calls: list[dict] = []

    def forward(self, **_kwargs) -> bool:
        self.forward_calls += 1
        return self.result

    def to(self, **kwargs) -> bool:
        self.to_calls.append(dict(kwargs))
        return False


class FakeFling:
    def __init__(self) -> None:
        self.to_end_calls = 0

    def toEnd(self, **_kwargs) -> bool:
        self.to_end_calls += 1
        return True


class FakeScrollableSelector(FakeSelector):
    def __init__(self, result: bool = True) -> None:
        super().__init__(1)
        self.scroll = FakeScroll(result=result)
        self.fling = FakeFling()


class FakeDevice:
    def __init__(self, hierarchies: list[str] | None = None, *, foreground_package: str | None = None) -> None:
        self.hierarchies = list(hierarchies or [CONNECTED_XML])
        self.foreground_package = foreground_package
        self.dump_calls = 0
        self.selector_calls: list[dict] = []
        self.selectors: dict[tuple[str, str], FakeSelector] = {}
        self.bounds_clicks: list[tuple[int, int]] = []
        self.press_calls: list[str] = []
        self.app_start = Mock()

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

    def press(self, key: str) -> None:
        self.press_calls.append(str(key))

    def app_current(self) -> dict:
        return {"package": self.foreground_package} if self.foreground_package is not None else {}


class TransientCredentialManagerDevice(FakeDevice):
    def press(self, key: str) -> None:
        super().press(key)
        if key == "back" and self.foreground_package == "com.android.credentialmanager":
            self.foreground_package = "com.instagram.android"


class TrackingSecretValue(SecretValue):
    def __init__(self, value: str) -> None:
        super().__init__(value)
        self.revealed = False

    def reveal_for_login_executor(self) -> str:
        self.revealed = True
        return super().reveal_for_login_executor()


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
    def run_flow(self, *args, **kwargs):
        kwargs.setdefault("observe_current_screen_only", True)
        return run_login_provisioning_flow(*args, **kwargs)

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

    def test_default_flow_app_starts_before_probe(self) -> None:
        device = FakeDevice([CONNECTED_XML])
        credentials_getter = Mock(return_value=credentials())
        sleeper = Mock()

        result = run_login_provisioning_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username=USERNAME,
            credentials_getter=credentials_getter,
            sleeper=sleeper,
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.reason, "connected_no_password_needed")
        device.app_start.assert_called_once_with("com.instagram.android")
        self.assertEqual(device.dump_calls, 1)
        sleeper.assert_called_once_with(1.5)
        credentials_getter.assert_not_called()
        self.assertTrue(result.safe_metadata["app_start_attempted"])
        self.assertTrue(result.safe_metadata["app_start_ok"])
        self.assertEqual(result.safe_metadata["screen_after_app_start"], "connected")

    def test_startup_email_code_challenge_creates_dashboard_action_without_credentials(self) -> None:
        device = FakeDevice([EMAIL_CODE_CHALLENGE_XML])
        credentials_getter = Mock(return_value=credentials())

        with (
            patch.object(
                provisioner_orchestrator,
                "sync_login_challenge_dashboard_action",
                return_value={"published": True, "reason": "upserted", "dashboard_action_id": "action-1"},
            ) as sync_action,
            patch.object(
                provisioner_orchestrator,
                "publish_login_challenge_pending_incident",
                return_value={"published": True, "reason": "published"},
            ),
        ):
            result = run_login_provisioning_flow(
                device,
                account_id=ACCOUNT_ID,
                expected_username=USERNAME,
                credentials_getter=credentials_getter,
                sleeper=Mock(),
            )

        self.assertFalse(result.ok)
        self.assertEqual(result.final_outcome, "verification_pending")
        self.assertEqual(result.final_login_status, "verification_pending")
        self.assertEqual(result.dashboard_action_type, "enter_email_verification_code")
        self.assertEqual(result.safe_metadata["screen_after_app_start"], "email_code_challenge")
        self.assertEqual(result.safe_metadata["dashboard_action_sync"]["dashboard_action_id"], "action-1")
        credentials_getter.assert_not_called()
        sync_action.assert_called_once()

    def test_observe_current_screen_only_skips_app_start(self) -> None:
        device = FakeDevice([CONNECTED_XML])

        result = run_login_provisioning_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username=USERNAME,
            credentials_getter=Mock(return_value=credentials()),
            observe_current_screen_only=True,
        )

        self.assertEqual(result.reason, "connected_no_password_needed")
        device.app_start.assert_not_called()
        self.assertFalse(result.safe_metadata["app_start_attempted"])
        self.assertTrue(result.safe_metadata["observe_current_screen_only"])

    def test_app_start_failed_after_retry_stops_before_credentials_and_password_reveal(self) -> None:
        device = FakeDevice([LOGIN_FORM_XML])
        device.app_start.side_effect = RuntimeError("boom")
        secret = TrackingSecretValue(PASSWORD)
        credentials_getter = Mock(return_value={"username": USERNAME, "password": secret})

        result = run_login_provisioning_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username=USERNAME,
            credentials_getter=credentials_getter,
            sleeper=Mock(),
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "app_start_failed_after_retry")
        self.assertFalse(secret.revealed)
        credentials_getter.assert_not_called()
        self.assertEqual(device.dump_calls, 0)
        self.assertFalse(result.safe_metadata["app_start_ok"])
        self.assertTrue(result.safe_metadata["app_start_retry_attempted"])
        self.assertEqual(result.safe_metadata["app_start_retry_count"], 1)
        self.assertEqual(result.safe_metadata["app_start_retry_reason"], "app_start_failed")
        self.assertEqual(result.safe_metadata["app_start_retry_result"], "app_start_failed_after_retry")
        self.assertEqual(device.app_start.call_count, 2)

    def test_app_start_first_failure_retries_once_then_routes(self) -> None:
        device, selectors = configured_device(CONNECTED_XML)
        device.hierarchies = [LOGIN_FORM_XML, CONNECTED_XML]
        device.app_start.side_effect = [RuntimeError("temporary"), None]

        result = run_login_provisioning_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username=USERNAME,
            credentials_getter=Mock(return_value=credentials()),
            post_start_wait_ms=0,
            sleeper=Mock(),
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.safe_metadata["selected_route"], "login_form_empty")
        self.assertTrue(result.safe_metadata["app_start_retry_attempted"])
        self.assertEqual(result.safe_metadata["app_start_retry_result"], "started_after_retry")
        self.assertEqual(device.app_start.call_count, 2)
        self.assertEqual(selectors["login"].click_calls, 1)

    def test_app_start_unknown_stable_stops_after_startup_settling(self) -> None:
        device = FakeDevice([UNKNOWN_XML])
        secret = TrackingSecretValue(PASSWORD)
        credentials_getter = Mock(return_value={"username": USERNAME, "password": secret})

        result = run_login_provisioning_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username=USERNAME,
            credentials_getter=credentials_getter,
            post_start_wait_ms=0,
            sleeper=Mock(),
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "startup_unknown_after_retry")
        self.assertFalse(secret.revealed)
        credentials_getter.assert_not_called()
        self.assertEqual(result.safe_metadata["screen_after_app_start"], "unknown")
        self.assertEqual(result.safe_metadata["startup_observation_count"], 4)
        self.assertEqual(result.safe_metadata["startup_screens"], ["unknown", "unknown", "unknown", "unknown"])
        self.assertTrue(result.safe_metadata["startup_settling_used"])
        self.assertTrue(result.safe_metadata["app_start_retry_attempted"])
        self.assertEqual(result.safe_metadata["app_start_retry_reason"], "startup_unknown_or_loading")
        self.assertEqual(result.safe_metadata["app_start_retry_result"], "startup_unknown_after_retry")

    def test_app_start_unknown_stable_retries_once_then_routes(self) -> None:
        device, selectors = configured_device(CONNECTED_XML)
        device.hierarchies = [UNKNOWN_XML, UNKNOWN_XML, UNKNOWN_XML, UNKNOWN_XML, LOGIN_FORM_XML, CONNECTED_XML]

        result = run_login_provisioning_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username=USERNAME,
            credentials_getter=Mock(return_value=credentials()),
            post_start_wait_ms=0,
            sleeper=Mock(),
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.safe_metadata["selected_route"], "login_form_empty")
        self.assertTrue(result.safe_metadata["app_start_retry_attempted"])
        self.assertEqual(result.safe_metadata["app_start_retry_result"], "routable_after_retry")
        self.assertEqual(result.safe_metadata["startup_after_retry_screens"], ["login_form_empty"])
        self.assertEqual(device.app_start.call_count, 2)
        self.assertEqual(selectors["login"].click_calls, 1)

    def test_app_start_unknown_then_continue_as_candidate_routes_continue(self) -> None:
        continue_xml = f'<node text="{USERNAME}" />' + CONTINUE_AS_XML
        device, selectors = configured_device(CONNECTED_XML)
        device.hierarchies = [UNKNOWN_XML, continue_xml, PASSWORD_ONLY_OVERLAY_XML, PASSWORD_ONLY_OVERLAY_XML, CONNECTED_XML]

        result = run_login_provisioning_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username=USERNAME,
            credentials_getter=Mock(return_value=credentials()),
            post_start_wait_ms=0,
            sleeper=Mock(),
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.safe_metadata["screen_after_app_start_initial"], "unknown")
        self.assertEqual(result.safe_metadata["screen_after_app_start_final"], "continue_as_candidate")
        self.assertEqual(result.safe_metadata["startup_screens"], ["unknown", "continue_as_candidate"])
        self.assertEqual(result.safe_metadata["startup_observation_count"], 2)
        self.assertTrue(result.safe_metadata["startup_settling_used"])
        self.assertTrue(result.safe_metadata["central_orchestrator_used"])
        self.assertEqual(result.safe_metadata["selected_route"], "continue_as_expected")
        self.assertEqual(selectors["continue"].click_calls, 1)

    def test_app_start_unknown_then_login_form_empty_is_accepted(self) -> None:
        device, selectors = configured_device(CONNECTED_XML)
        device.hierarchies = [UNKNOWN_XML, LOGIN_FORM_XML, CONNECTED_XML]

        result = run_login_provisioning_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username=USERNAME,
            credentials_getter=Mock(return_value=credentials()),
            post_start_wait_ms=0,
            sleeper=Mock(),
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.safe_metadata["screen_after_app_start_final"], "login_form_empty")
        self.assertEqual(result.safe_metadata["selected_route"], "login_form_empty")
        self.assertEqual(selectors["login"].click_calls, 1)

    def test_app_start_unknown_then_continue_password_only_is_accepted(self) -> None:
        device, selectors = configured_device(CONNECTED_XML)
        device.hierarchies = [UNKNOWN_XML, PASSWORD_ONLY_OVERLAY_XML, CONNECTED_XML]

        result = run_login_provisioning_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username=USERNAME,
            credentials_getter=Mock(return_value=credentials()),
            post_start_wait_ms=0,
            sleeper=Mock(),
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.safe_metadata["screen_after_app_start_final"], "continue_password_only")
        self.assertEqual(result.safe_metadata["selected_route"], "continue_password_only")
        self.assertEqual(selectors["login"].click_calls, 1)

    def test_app_start_continue_as_candidate_fast_path_no_startup_reobserve(self) -> None:
        continue_xml = f'<node text="{USERNAME}" />' + CONTINUE_AS_XML
        device, selectors = configured_device(CONNECTED_XML)
        device.hierarchies = [continue_xml, PASSWORD_ONLY_OVERLAY_XML, PASSWORD_ONLY_OVERLAY_XML, CONNECTED_XML]

        result = run_login_provisioning_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username=USERNAME,
            credentials_getter=Mock(return_value=credentials()),
            post_start_wait_ms=0,
            sleeper=Mock(),
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.safe_metadata["startup_observation_count"], 1)
        self.assertEqual(result.safe_metadata["startup_wait_total_ms"], 0)
        self.assertFalse(result.safe_metadata["startup_settling_used"])
        self.assertEqual(selectors["continue"].click_calls, 1)

    def test_package_name_is_configurable_for_future_clones(self) -> None:
        device = FakeDevice([CONNECTED_XML])

        result = run_login_provisioning_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username=USERNAME,
            credentials_getter=Mock(return_value=credentials()),
            package_name="com.instagram.android.clone1",
            post_start_wait_ms=0,
            sleeper=Mock(),
        )

        self.assertTrue(result.ok)
        device.app_start.assert_called_once_with("com.instagram.android.clone1")
        self.assertEqual(result.safe_metadata["package_name"], "com.instagram.android.clone1")

    def test_provisioning_clone_package_ok_can_continue_to_routing(self) -> None:
        device = FakeDevice([LOGIN_FORM_XML], foreground_package="com.instagram.androie")
        result = run_login_provisioning_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username=USERNAME,
            credentials_getter=Mock(side_effect=AssertionError("dry run must not load credentials")),
            package_name="com.instagram.androie",
            dry_run=True,
            post_start_wait_ms=0,
            sleeper=Mock(),
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.final_outcome, "dry_run")
        self.assertEqual(result.safe_metadata["screen_type"], "login_form_empty")
        device.app_start.assert_called_once_with("com.instagram.androie")

    def test_wrong_package_foreground_stops_before_credentials_or_input_and_reports(self) -> None:
        device = FakeDevice([LOGIN_FORM_XML], foreground_package="com.instagram.android")
        credentials_getter = Mock(side_effect=AssertionError("credentials must not load on package mismatch"))

        with (
            patch.object(
                provisioner_orchestrator,
                "publish_login_package_mismatch_incident",
                return_value={"published": True, "incident_id": "incident-1"},
            ) as incident,
            patch.object(
                provisioner_orchestrator,
                "sync_login_package_mismatch_dashboard_action",
                return_value={"published": True, "dashboard_action_id": "action-1"},
            ) as dashboard,
            patch.object(
                provisioner_orchestrator,
                "dispatch_login_package_mismatch_notifications",
                return_value={
                    "dispatched": True,
                    "enabled_channels": ["slack"],
                    "skipped_channel_disabled_count": 1,
                },
            ) as notifications,
            patch.object(
                provisioner_orchestrator,
                "execute_login_form_credentials",
                side_effect=AssertionError("password executor must not run"),
            ) as executor,
        ):
            result = run_login_provisioning_flow(
                device,
                account_id=ACCOUNT_ID,
                expected_username=USERNAME,
                credentials_getter=credentials_getter,
                package_name="com.instagram.androie",
                run_id="run-1",
                run_type="login_provisioning",
                device_serial="RFGL145VCKE",
                expected_app_instance_id="7637db9a-3581-4099-8068-d5eb1ed86f96",
                post_start_wait_ms=0,
                sleeper=Mock(),
            )

        self.assertFalse(result.ok)
        self.assertEqual(result.final_outcome, "wrong_app_package")
        self.assertEqual(result.reason, "expected_package_mismatch")
        self.assertEqual(result.dashboard_action_type, "review_login_package_mismatch")
        self.assertNotIn("login_form_submit", result.actions_taken)
        credentials_getter.assert_not_called()
        executor.assert_not_called()
        incident.assert_called_once()
        dashboard.assert_called_once()
        notifications.assert_called_once()
        metadata = result.safe_metadata
        self.assertEqual(metadata["expected_package_name"], "com.instagram.androie")
        self.assertEqual(metadata["actual_foreground_package"], "com.instagram.android")
        self.assertTrue(metadata["package_guard_mismatch"])
        self.assertFalse(metadata["would_submit_password"])
        self.assertFalse(metadata["password_input"])
        self.assertFalse(metadata["submit_executed"])
        rendered = json.dumps(metadata, sort_keys=True)
        self.assertNotIn(PASSWORD, rendered)
        self.assertNotIn(SECRET_REF, rendered)
        self.assertNotIn(VAULT_ID, rendered)
        self.assertIn("RFGL***VCKE", rendered)

    def test_transient_credential_manager_overlay_recovers_before_credentials_and_submits(self) -> None:
        device = TransientCredentialManagerDevice(
            [LOGIN_FORM_XML],
            foreground_package="com.android.credentialmanager",
        )
        recovered = provisioner_orchestrator._recover_transient_foreground_package(
            device,
            expected_package_name="com.instagram.android",
            timer=time.perf_counter,
            sleeper=Mock(),
        )
        self.assertIn("back", device.press_calls)
        self.assertTrue(recovered.get("transient_foreground_recovery_attempted"))
        self.assertTrue(recovered.get("transient_foreground_recovery_succeeded"))
        self.assertFalse(recovered.get("package_guard_mismatch"))

    def test_resume_email_wrong_package_stops_before_consuming_code(self) -> None:
        device = FakeDevice([EMAIL_CODE_CHALLENGE_XML], foreground_package="com.instagram.android")
        with (
            patch.object(
                provisioner_orchestrator,
                "consume_verification_code_for_worker",
                side_effect=AssertionError("code must not be consumed"),
            ) as consume,
            patch.object(
                provisioner_orchestrator,
                "execute_email_code_challenge_resume",
                side_effect=AssertionError("email code executor must not run"),
            ) as executor,
            patch.object(
                provisioner_orchestrator,
                "publish_login_package_mismatch_incident",
                return_value={"published": True, "incident_id": "incident-1"},
            ),
            patch.object(
                provisioner_orchestrator,
                "sync_login_package_mismatch_dashboard_action",
                return_value={"published": True, "dashboard_action_id": "action-1"},
            ),
            patch.object(
                provisioner_orchestrator,
                "dispatch_login_package_mismatch_notifications",
                return_value={"dispatched": True, "enabled_channels": ["discord"]},
            ),
        ):
            result = provisioner_orchestrator.run_email_code_resume_flow(
                device,
                account_id=ACCOUNT_ID,
                expected_username=USERNAME,
                verification_code=SecretValue("123456"),
                action_id="action-1",
                consume_from_action=True,
                run_id="run-1",
                package_name="com.instagram.androie",
                device_serial="RFGL145VCKE",
            )

        self.assertFalse(result.ok)
        self.assertEqual(result.final_outcome, "wrong_app_package")
        self.assertEqual(result.reason, "resume_email_code_wrong_package")
        consume.assert_not_called()
        executor.assert_not_called()
        self.assertNotIn("email_code_submit", result.actions_taken)

    def test_email_code_resume_chains_password_after_post_code_password_required(self) -> None:
        device = FakeDevice([PASSWORD_ONLY_OVERLAY_XML, CONNECTED_XML])
        secret = TrackingSecretValue(PASSWORD)
        from instagram_login_email_code_executor import EmailCodeResumeResult

        resume_result = EmailCodeResumeResult(
            ok=False,
            executed=True,
            action="email_code_submit",
            reason="post_code_password_required",
            failure_reason=None,
            post_submit_outcome="post_code_password_required",
            post_submit_probe_reason="post_code_password_required",
            post_submit_screen_type="continue_password_only",
            code_entered=True,
            continue_tapped=False,
            timings={},
            warnings=[],
            safe_metadata={"post_code_password_required": True, "screen_type": "continue_password_only"},
        )
        password_result = type(
            "PasswordResult",
            (),
            {
                "failure_reason": None,
                "post_submit_outcome": "connected",
                "post_submit_probe_reason": "connected",
                "post_submit_screen_type": "connected",
                "executed": True,
                "submit_tapped": True,
                "timings": {},
                "warnings": [],
                "safe_metadata": {"post_submit_screen_type": "connected"},
            },
        )()
        with (
            patch.object(
                provisioner_orchestrator,
                "consume_verification_code_for_worker",
                return_value={"ok": True, "verification_code": "123456"},
            ),
            patch.object(
                provisioner_orchestrator,
                "execute_email_code_challenge_resume",
                return_value=resume_result,
            ) as email_executor,
            patch.object(
                provisioner_orchestrator,
                "execute_login_form_credentials",
                return_value=password_result,
            ) as password_executor,
            patch.object(
                provisioner_orchestrator,
                "_observe_login_signals",
                side_effect=[
                    {
                        "screen_type": "email_code_challenge",
                        "email_code_challenge_present": True,
                    },
                    {
                        "screen_type": "continue_password_only",
                        "suggested_username": USERNAME,
                        "has_password_field": True,
                        "has_login_button": True,
                    },
                ],
            ),
        ):
            result = provisioner_orchestrator.run_email_code_resume_flow(
                device,
                account_id=ACCOUNT_ID,
                expected_username=USERNAME,
                verification_code=SecretValue(""),
                credentials_getter=Mock(return_value={"username": USERNAME, "password": secret}),
                action_id="action-1",
                consume_from_action=True,
                run_id="run-1",
            )

        self.assertTrue(result.ok)
        email_executor.assert_called_once()
        password_executor.assert_called_once()
        self.assertIn("route:post_email_code_password", result.actions_taken)
        self.assertIn("login_form_submit", result.actions_taken)
        rendered = json.dumps(result.safe_metadata, sort_keys=True)
        self.assertNotIn(PASSWORD, rendered)
        self.assertNotIn("123456", rendered)

    def test_email_code_resume_chains_password_after_code_input_empty_on_password_screen(self) -> None:
        device = FakeDevice([PASSWORD_ONLY_OVERLAY_XML, CONNECTED_XML])
        from instagram_login_email_code_executor import EmailCodeResumeResult

        resume_result = EmailCodeResumeResult(
            ok=False,
            executed=True,
            action="email_code_submit",
            reason="verification_code_input_empty",
            failure_reason="verification_code_input_empty",
            post_submit_outcome=None,
            post_submit_probe_reason="verification_code_input_empty",
            post_submit_screen_type="",
            code_entered=False,
            continue_tapped=False,
            timings={},
            warnings=["verification_code_input_fallback_adb_keyboard_b64_attempted"],
            safe_metadata={"stage": "email_code_resume"},
        )
        password_result = type(
            "PasswordResult",
            (),
            {
                "failure_reason": None,
                "post_submit_outcome": "connected",
                "post_submit_probe_reason": "connected",
                "post_submit_screen_type": "connected",
                "executed": True,
                "submit_tapped": True,
                "timings": {},
                "warnings": [],
                "safe_metadata": {
                    "post_submit_screen_type": "connected",
                    "password_input_method": "adb_keyboard_b64",
                    "password_field_non_empty_confirmed": "true",
                },
            },
        )()
        password_signals = {
            "screen_type": "continue_password_only",
            "suggested_username": USERNAME,
            "has_password_field": True,
            "has_login_button": True,
            "email_code_challenge_present": False,
        }
        with (
            patch.object(
                provisioner_orchestrator,
                "consume_verification_code_for_worker",
                return_value={"ok": True, "verification_code": "123456"},
            ),
            patch.object(
                provisioner_orchestrator,
                "execute_email_code_challenge_resume",
                return_value=resume_result,
            ),
            patch.object(
                provisioner_orchestrator,
                "execute_login_form_credentials",
                return_value=password_result,
            ) as password_executor,
            patch.object(
                provisioner_orchestrator,
                "_observe_login_signals",
                side_effect=[
                    {"screen_type": "email_code_challenge", "email_code_challenge_present": True},
                    password_signals,
                ],
            ),
        ):
            result = provisioner_orchestrator.run_email_code_resume_flow(
                device,
                account_id=ACCOUNT_ID,
                expected_username=USERNAME,
                verification_code=SecretValue(""),
                credentials_getter=Mock(return_value={"username": USERNAME, "password": SecretValue("x")}),
                action_id="action-1",
                consume_from_action=True,
                run_id="run-1",
            )

        self.assertTrue(result.ok)
        password_executor.assert_called_once()
        self.assertIn("route:post_email_code_password", result.actions_taken)

    def test_email_code_resume_skips_code_entry_when_password_screen_ready(self) -> None:
        device = FakeDevice([PASSWORD_ONLY_OVERLAY_XML, CONNECTED_XML])
        secret = TrackingSecretValue(PASSWORD)
        password_result = type(
            "PasswordResult",
            (),
            {
                "failure_reason": None,
                "post_submit_outcome": "connected",
                "post_submit_probe_reason": "connected",
                "post_submit_screen_type": "connected",
                "executed": True,
                "submit_tapped": True,
                "timings": {},
                "warnings": [],
                "safe_metadata": {"post_submit_screen_type": "connected"},
            },
        )()
        password_signals = {
            "screen_type": "continue_password_only",
            "suggested_username": USERNAME,
            "has_password_field": True,
            "has_login_button": True,
        }
        with (
            patch.object(
                provisioner_orchestrator,
                "consume_verification_code_for_worker",
                return_value={"ok": True, "verification_code": "123456"},
            ),
            patch.object(
                provisioner_orchestrator,
                "execute_email_code_challenge_resume",
                side_effect=AssertionError("email code executor must not run"),
            ) as email_executor,
            patch.object(
                provisioner_orchestrator,
                "execute_login_form_credentials",
                return_value=password_result,
            ) as password_executor,
            patch.object(
                provisioner_orchestrator,
                "_observe_login_signals",
                return_value=password_signals,
            ),
        ):
            result = provisioner_orchestrator.run_email_code_resume_flow(
                device,
                account_id=ACCOUNT_ID,
                expected_username=USERNAME,
                verification_code=SecretValue(""),
                credentials_getter=Mock(return_value={"username": USERNAME, "password": secret}),
                action_id="action-1",
                consume_from_action=True,
                run_id="run-1",
            )

        self.assertTrue(result.ok)
        email_executor.assert_not_called()
        password_executor.assert_called_once()
        self.assertIn("route:post_email_code_password", result.actions_taken)
        self.assertTrue(result.safe_metadata.get("email_code_entry_skipped"))

    def test_app_start_account_picker_can_prepare_password_only_then_submit(self) -> None:
        account_picker = (
            '<node clickable="true" enabled="true" visible-to-user="true" bounds="[100,300][980,500]" class="android.view.ViewGroup" />'
            f'<node text="{USERNAME}" enabled="true" visible-to-user="true" clickable="false" bounds="[260,350][560,400]" />'
            '<node clickable="true" enabled="true" visible-to-user="true" bounds="[100,540][980,740]" class="android.view.ViewGroup" />'
            '<node text="random_old_profile" enabled="true" visible-to-user="true" clickable="false" bounds="[260,590][620,640]" />'
            '<node text="Use another profile" clickable="true" enabled="true" visible-to-user="true" bounds="[100,780][980,900]" />'
            '<node text="Create new account" clickable="true" enabled="true" visible-to-user="true" bounds="[100,1900][980,2020]" />'
            '<node content-desc="Meta logo" />'
        )
        device, selectors = configured_device(CONNECTED_XML)
        device.hierarchies = [account_picker, account_picker, PASSWORD_ONLY_OVERLAY_XML, PASSWORD_ONLY_OVERLAY_XML, CONNECTED_XML]
        secret = TrackingSecretValue(PASSWORD)

        result = run_login_provisioning_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username=USERNAME,
            credentials_getter=Mock(return_value={"username": USERNAME, "password": secret}),
            post_start_wait_ms=0,
            sleeper=Mock(),
        )

        self.assertTrue(result.ok)
        self.assertIn("tap_expected_account", result.actions_taken)
        self.assertIn("login_form_submit", result.actions_taken)
        self.assertEqual(result.safe_metadata["selected_route"], "account_picker")
        self.assertTrue(secret.revealed)
        self.assertTrue(selectors["password"].set_text_calls)
        self.assertEqual(result.safe_metadata["screen_after_app_start"], "account_picker")

    def test_app_start_continue_expected_can_prepare_password_only_then_submit(self) -> None:
        continue_xml = CONTINUE_AS_XML.replace("Continue", "Continue").replace("Create new account", "Create new account")
        continue_xml = f'<node text="{USERNAME}" />' + continue_xml
        device, selectors = configured_device(CONNECTED_XML)
        device.hierarchies = [continue_xml, PASSWORD_ONLY_OVERLAY_XML, PASSWORD_ONLY_OVERLAY_XML, PASSWORD_ONLY_OVERLAY_XML, CONNECTED_XML]
        secret = TrackingSecretValue(PASSWORD)

        result = run_login_provisioning_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username=USERNAME,
            credentials_getter=Mock(return_value={"username": USERNAME, "password": secret}),
            post_start_wait_ms=0,
            sleeper=Mock(),
        )

        self.assertTrue(result.ok)
        self.assertIn("tap_continue", result.actions_taken)
        self.assertIn("login_form_submit", result.actions_taken)
        self.assertEqual(result.safe_metadata["selected_route"], "continue_as_expected")
        self.assertTrue(secret.revealed)
        self.assertTrue(selectors["password"].set_text_calls)
        self.assertEqual(result.safe_metadata["screen_after_app_start"], "continue_as_candidate")

    def test_join_instagram_landing_uses_existing_profile_then_submits_login_form(self) -> None:
        device, selectors = configured_device(CONNECTED_XML)
        device.hierarchies = [
            JOIN_INSTAGRAM_XML,
            LOGIN_FORM_XML,
            LOGIN_FORM_XML,
            CONNECTED_XML,
            CONNECTED_XML,
        ]
        secret = TrackingSecretValue(PASSWORD)

        result = self.run_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username=USERNAME,
            credentials_getter=Mock(return_value={"username": USERNAME, "password": secret}),
            initial_signals=JOIN_INSTAGRAM_SIGNALS,
            sleeper=Mock(),
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.final_outcome, "connected")
        self.assertIn("route:open_existing_profile_from_join_landing", result.actions_taken)
        self.assertIn("tap_already_have_profile", result.actions_taken)
        self.assertIn("route:start_login_form_flow", result.actions_taken)
        self.assertIn("login_form_submit", result.actions_taken)
        self.assertEqual(device.bounds_clicks, [(540, 1700)])
        self.assertTrue(result.safe_metadata["join_instagram_landing_detected"])
        self.assertTrue(result.safe_metadata["already_have_profile_tap_sent"])
        self.assertTrue(result.safe_metadata["login_form_after_join_landing_detected"])
        self.assertEqual(result.safe_metadata["selected_route"], "join_instagram_existing_profile")
        self.assertTrue(secret.revealed)
        self.assertTrue(selectors["username"].set_text_calls)
        self.assertTrue(selectors["password"].set_text_calls)

    def test_join_instagram_landing_missing_existing_profile_stops_safe(self) -> None:
        device, selectors = configured_device(CONNECTED_XML)
        device.hierarchies = [JOIN_INSTAGRAM_WITHOUT_EXISTING_XML]

        result = self.run_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username=USERNAME,
            credentials_getter=Mock(return_value=credentials()),
            initial_signals=JOIN_INSTAGRAM_SIGNALS,
            sleeper=Mock(),
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.final_outcome, "action_failed")
        self.assertEqual(result.failure_reason, "unsupported_login_landing")
        self.assertEqual(device.bounds_clicks, [])
        self.assertEqual(selectors["login"].click_calls, 0)
        self.assertNotIn("login_form_submit", result.actions_taken)

    def test_login_form_credentials_ok_connected_success(self) -> None:
        device, selectors = configured_device(CONNECTED_XML)
        selectors["username"] = device.add_selector(
            "text",
            "Username, email or mobile number",
            ConfirmingTextSelector(),
        )

        result = self.run_flow(
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
        self.assertEqual(result.safe_metadata["screen_type"], "login_form_empty")
        self.assertEqual(result.safe_metadata["router_decision"], "start_login_form_flow")
        self.assertEqual(result.safe_metadata["selected_route"], "login_form_empty")
        self.assertTrue(selectors["username"].set_text_calls)
        self.assertTrue(selectors["password"].set_text_calls)
        self.assertTrue(result.safe_metadata["password_result"]["executed"])
        self.assertTrue(result.safe_metadata["password_result"]["submit_tapped"])
        self.assertFalse(result.safe_metadata["password_result"]["username_replaced"])
        self.assertEqual(
            result.safe_metadata["password_result"]["username_input_result"],
            "username_input_confirmed",
        )
        self.assertGreaterEqual(result.safe_metadata["password_result"]["username_input_ms"], 0)

    def test_blocked_secret_payload_shape_maps_to_secret_payload_not_password(self) -> None:
        device, _selectors = configured_device(CONNECTED_XML)
        password_result = type(
            "PasswordResult",
            (),
            {
                "ok": False,
                "executed": False,
                "failure_reason": "blocked_secret_payload_shape",
                "post_submit_outcome": None,
                "post_submit_probe_reason": None,
                "timings": {},
                "warnings": ["blocked_secret_payload_shape"],
                "safe_metadata": {"password_submit_result": "blocked_secret_payload_shape"},
            },
        )()

        with patch.object(provisioner_orchestrator, "execute_login_form_credentials", return_value=password_result):
            result = self.run_flow(
                device,
                account_id=ACCOUNT_ID,
                expected_username=USERNAME,
                credentials_getter=Mock(return_value=credentials()),
                initial_signals=LOGIN_FORM_SIGNALS,
            )

        self.assertEqual(result.final_outcome, "secret_payload_not_password")
        self.assertEqual(result.failure_reason, "blocked_secret_payload_shape")
        self.assertFalse(result.should_publish_status)

    def test_password_required_outcome_does_not_collapse_to_unknown(self) -> None:
        device, _selectors = configured_device(CONNECTED_XML)
        password_result = type(
            "PasswordResult",
            (),
            {
                "failure_reason": "password_input_missing_or_not_accepted",
                "post_submit_outcome": "password_input_missing_or_not_accepted",
                "post_submit_probe_reason": "password_required_dialog",
                "timings": {},
                "warnings": [],
            },
        )()

        with patch.object(provisioner_orchestrator, "execute_login_form_credentials", return_value=password_result):
            result = self.run_flow(
                device,
                account_id=ACCOUNT_ID,
                expected_username=USERNAME,
                credentials_getter=Mock(return_value=credentials()),
                initial_signals=LOGIN_FORM_SIGNALS,
            )

        self.assertEqual(result.final_outcome, "password_input_missing_or_not_accepted")
        self.assertEqual(result.failure_reason, "password_input_missing_or_not_accepted")
        self.assertEqual(result.reason, "password_required_dialog")
        self.assertFalse(result.should_publish_status)

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

    def test_login_form_email_code_challenge_status_no_retry(self) -> None:
        result = self._run_login_form(EMAIL_CODE_CHALLENGE_XML)

        self.assertEqual(result.final_outcome, "verification_pending")
        self.assertEqual(result.reason, "email_verification_code_required")
        self.assertEqual(result.final_login_status, "verification_pending")
        self.assertEqual(result.final_provisioning_status, "login_verification_pending")
        self.assertEqual(result.final_onboarding_status, "verification_pending")
        self.assertFalse(result.retry_attempted)
        self.assertEqual(result.actions_taken.count("login_form_submit"), 1)
        self.assertNotIn("login_form_submit_retry", result.actions_taken)
        password_meta = result.safe_metadata["password_result"]
        self.assertEqual(password_meta["post_submit_screen_type"], "email_code_challenge")
        self.assertTrue(password_meta["email_code_challenge_detected"])
        self.assertEqual(password_meta["challenge_type"], "email")
        self.assertTrue(password_meta["masked_email_present"])
        self.assertEqual(result.dashboard_action_type, "enter_email_verification_code")
        rendered = json.dumps(result.safe_metadata, sort_keys=True)
        self.assertNotIn("m*******e@hotmail.com", rendered)
        self.assertNotIn(PASSWORD, rendered)
        self.assertNotIn(SECRET_REF, rendered)

    def test_login_form_login_failed_status_no_retry(self) -> None:
        result = self._run_login_form(LOGIN_FAILED_XML)

        self.assertEqual(result.final_outcome, "login_failed")
        self.assertEqual(result.final_login_status, "failed")
        self.assertEqual(result.final_provisioning_status, "failed")
        self.assertEqual(result.dashboard_action_type, "update_instagram_password")
        self.assertFalse(result.retry_attempted)

    def test_logged_out_after_settling_reason_is_preserved(self) -> None:
        device, _selectors = configured_device(CONNECTED_XML)
        password_result = type(
            "PasswordResult",
            (),
            {
                "failure_reason": None,
                "post_submit_outcome": "logged_out",
                "post_submit_probe_reason": "session_expired_after_settling",
                "executed": True,
                "submit_tapped": True,
                "timings": {},
                "warnings": [],
                "safe_metadata": {
                    "post_submit_observation_count": 4,
                    "post_submit_wait_total_ms": 2250,
                    "post_submit_screens": ["logged_out", "logged_out", "logged_out", "logged_out"],
                    "final_terminal_screen": "logged_out",
                },
            },
        )()

        with patch.object(provisioner_orchestrator, "execute_login_form_credentials", return_value=password_result):
            result = self.run_flow(
                device,
                account_id=ACCOUNT_ID,
                expected_username=USERNAME,
                credentials_getter=Mock(return_value=credentials()),
                initial_signals=LOGIN_FORM_SIGNALS,
            )

        self.assertEqual(result.final_outcome, "logged_out")
        self.assertEqual(result.reason, "session_expired_after_settling")
        self.assertEqual(result.safe_metadata["password_result"]["post_submit_observation_count"], 4)

    def test_unknown_after_settling_does_not_retry_password(self) -> None:
        device, _selectors = configured_device(CONNECTED_XML)
        password_result = type(
            "PasswordResult",
            (),
            {
                "failure_reason": None,
                "post_submit_outcome": "unknown",
                "post_submit_probe_reason": "post_submit_unknown_after_settling",
                "executed": True,
                "submit_tapped": True,
                "timings": {},
                "warnings": ["post_submit_unknown_after_settling"],
                "safe_metadata": {
                    "post_submit_observation_count": 4,
                    "post_submit_wait_total_ms": 4000,
                    "post_submit_screens": ["unknown", "unknown", "unknown", "unknown"],
                    "final_terminal_screen": "unknown",
                },
            },
        )()

        with patch.object(provisioner_orchestrator, "execute_login_form_credentials", return_value=password_result) as patched:
            result = self.run_flow(
                device,
                account_id=ACCOUNT_ID,
                expected_username=USERNAME,
                credentials_getter=Mock(return_value=credentials()),
                initial_signals=LOGIN_FORM_SIGNALS,
            )

        self.assertEqual(result.final_outcome, "unknown")
        self.assertEqual(result.reason, "post_submit_unknown_after_settling")
        self.assertFalse(result.retry_attempted)
        self.assertEqual(result.retry_count, 0)
        patched.assert_called_once()

    def test_app_start_retry_never_happens_after_submit_unknown(self) -> None:
        device, _selectors = configured_device(LOGIN_FORM_XML)
        password_result = type(
            "PasswordResult",
            (),
            {
                "failure_reason": None,
                "post_submit_outcome": "unknown",
                "post_submit_probe_reason": "post_submit_unknown_after_settling",
                "executed": True,
                "submit_tapped": True,
                "timings": {},
                "warnings": ["post_submit_unknown_after_settling"],
                "safe_metadata": {
                    "post_submit_observation_count": 4,
                    "post_submit_wait_total_ms": 4000,
                    "post_submit_screens": ["unknown", "unknown", "unknown", "unknown"],
                    "final_terminal_screen": "unknown",
                },
            },
        )()

        with patch.object(provisioner_orchestrator, "execute_login_form_credentials", return_value=password_result):
            result = run_login_provisioning_flow(
                device,
                account_id=ACCOUNT_ID,
                expected_username=USERNAME,
                credentials_getter=Mock(return_value=credentials()),
                post_start_wait_ms=0,
                sleeper=Mock(),
            )

        self.assertEqual(result.final_outcome, "unknown")
        self.assertEqual(result.reason, "post_submit_unknown_after_settling")
        self.assertFalse(result.safe_metadata["app_start_retry_attempted"])
        self.assertEqual(device.app_start.call_count, 1)

    def test_save_password_dismiss_loading_then_connected_final_settling(self) -> None:
        device, _selectors = configured_device(CONNECTED_XML)
        device.hierarchies = [
            GOOGLE_SAVE_PASSWORD_PROMPT_XML,
            LOADING_XML,
            CONNECTED_XML,
        ]

        result = self.run_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username=USERNAME,
            credentials_getter=Mock(return_value=credentials()),
            initial_signals=LOGIN_FORM_SIGNALS,
            post_submit_timeout_ms=2000,
        )

        password_meta = result.safe_metadata["password_result"]
        self.assertEqual(result.final_outcome, "connected")
        self.assertTrue(result.ok)
        self.assertTrue(password_meta["save_password_prompt_detected"])
        self.assertTrue(password_meta["save_password_prompt_dismissed"])
        self.assertEqual(password_meta["post_dismiss_screen_type"], "connected")
        self.assertEqual(password_meta["post_dismiss_final_screens"], ["connected"])
        self.assertTrue(password_meta["connected_detected_after_save_prompt_dismiss"])

    def test_save_password_dismiss_unknown_then_connected_final_settling(self) -> None:
        device, _selectors = configured_device(CONNECTED_XML)
        device.hierarchies = [
            GOOGLE_SAVE_PASSWORD_PROMPT_XML,
            UNKNOWN_XML,
            CONNECTED_XML,
        ]

        result = self.run_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username=USERNAME,
            credentials_getter=Mock(return_value=credentials()),
            initial_signals=LOGIN_FORM_SIGNALS,
            post_submit_timeout_ms=2000,
        )

        password_meta = result.safe_metadata["password_result"]
        self.assertEqual(result.final_outcome, "connected")
        self.assertEqual(password_meta["post_dismiss_final_screens"], ["connected"])
        self.assertTrue(password_meta["connected_detected_after_save_prompt_dismiss"])

    def test_save_password_dismiss_loading_stable_timeout_no_publish(self) -> None:
        publisher = Mock(return_value={"published": True})
        device, _selectors = configured_device(CONNECTED_XML)
        device.hierarchies = [
            GOOGLE_SAVE_PASSWORD_PROMPT_XML,
            LOADING_XML,
            LOADING_XML,
            LOADING_XML,
            LOADING_XML,
            LOADING_XML,
        ]

        result = self.run_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username=USERNAME,
            credentials_getter=Mock(return_value=credentials()),
            initial_signals=LOGIN_FORM_SIGNALS,
            post_submit_timeout_ms=3000,
            publisher=publisher,
            publish_enabled=True,
        )

        password_meta = result.safe_metadata["password_result"]
        self.assertEqual(result.final_outcome, "login_submit_still_loading")
        self.assertEqual(result.reason, "post_submit_loading_timeout")
        self.assertFalse(result.should_publish_status)
        self.assertFalse(result.published)
        publisher.assert_not_called()
        self.assertEqual(password_meta["post_dismiss_final_screens"], ["loading", "loading", "loading", "loading"])
        self.assertFalse(password_meta["connected_detected_after_save_prompt_dismiss"])

    def test_loading_timeout_final_outcome_no_retry(self) -> None:
        device, _selectors = configured_device(CONNECTED_XML)
        password_result = type(
            "PasswordResult",
            (),
            {
                "failure_reason": None,
                "post_submit_outcome": "login_submit_still_loading",
                "post_submit_probe_reason": "post_submit_loading_timeout",
                "executed": True,
                "submit_tapped": True,
                "timings": {},
                "warnings": ["post_submit_loading_timeout"],
                "safe_metadata": {
                    "post_submit_observation_count": 10,
                    "post_submit_wait_total_ms": 10000,
                    "post_submit_timeout_ms": 10000,
                    "post_submit_interval_ms": 1000,
                    "post_submit_loading_timeout": True,
                    "post_submit_screens": ["loading"] * 10,
                    "final_terminal_screen": "loading",
                },
            },
        )()

        with patch.object(provisioner_orchestrator, "execute_login_form_credentials", return_value=password_result) as patched:
            result = self.run_flow(
                device,
                account_id=ACCOUNT_ID,
                expected_username=USERNAME,
                credentials_getter=Mock(return_value=credentials()),
                initial_signals=LOGIN_FORM_SIGNALS,
                post_submit_timeout_ms=10000,
            )

        self.assertEqual(result.final_outcome, "login_submit_still_loading")
        self.assertEqual(result.reason, "post_submit_loading_timeout")
        self.assertFalse(result.retry_attempted)
        self.assertFalse(result.should_publish_status)
        self.assertTrue(result.safe_metadata["password_result"]["post_submit_loading_timeout"])
        patched.assert_called_once()

    def test_save_password_prompt_blocking_final_outcome_no_retry(self) -> None:
        device, _selectors = configured_device(CONNECTED_XML)
        password_result = type(
            "PasswordResult",
            (),
            {
                "failure_reason": None,
                "post_submit_outcome": "save_password_prompt_blocking",
                "post_submit_probe_reason": "save_password_prompt_not_dismissed_after_2_attempts",
                "executed": True,
                "submit_tapped": True,
                "timings": {},
                "warnings": ["save_password_prompt_blocking"],
                "safe_metadata": {
                    "save_password_prompt_detected": True,
                    "save_password_prompt_dismissed": False,
                    "save_password_prompt_dismiss_attempt_count": 2,
                    "dismiss_method": "back",
                    "post_submit_observation_count": 2,
                    "post_submit_wait_total_ms": 2000,
                    "post_submit_screens": [
                        "google_password_manager_save_prompt",
                        "google_password_manager_save_prompt",
                    ],
                    "final_terminal_screen": "google_password_manager_save_prompt",
                },
            },
        )()

        with patch.object(provisioner_orchestrator, "execute_login_form_credentials", return_value=password_result) as patched:
            result = self.run_flow(
                device,
                account_id=ACCOUNT_ID,
                expected_username=USERNAME,
                credentials_getter=Mock(return_value=credentials()),
                initial_signals=LOGIN_FORM_SIGNALS,
            )

        self.assertEqual(result.final_outcome, "save_password_prompt_blocking")
        self.assertEqual(result.reason, "save_password_prompt_blocking")
        self.assertFalse(result.retry_attempted)
        patched.assert_called_once()

    def test_save_login_info_prompt_blocking_final_outcome_no_retry(self) -> None:
        device, _selectors = configured_device(CONNECTED_XML)
        password_result = type(
            "PasswordResult",
            (),
            {
                "failure_reason": None,
                "post_submit_outcome": "save_login_info_prompt_blocking",
                "post_submit_probe_reason": "save_login_info_prompt_not_dismissed_after_2_attempts",
                "executed": True,
                "submit_tapped": True,
                "timings": {},
                "warnings": ["save_login_info_prompt_blocking"],
                "safe_metadata": {
                    "instagram_save_login_info_prompt_detected": True,
                    "instagram_save_login_info_prompt_not_now": False,
                    "save_password_prompt_dismiss_attempt_count": 2,
                    "dismiss_method": "not_now",
                    "post_submit_observation_count": 2,
                    "post_submit_wait_total_ms": 2000,
                    "post_submit_screens": [
                        "save_login_info_prompt",
                        "save_login_info_prompt",
                    ],
                    "final_terminal_screen": "save_login_info_prompt",
                },
            },
        )()

        with patch.object(provisioner_orchestrator, "execute_login_form_credentials", return_value=password_result) as patched:
            result = self.run_flow(
                device,
                account_id=ACCOUNT_ID,
                expected_username=USERNAME,
                credentials_getter=Mock(return_value=credentials()),
                initial_signals=LOGIN_FORM_SIGNALS,
            )

        self.assertEqual(result.final_outcome, "save_login_info_prompt_blocking")
        self.assertEqual(result.reason, "save_login_info_prompt_blocking")
        self.assertFalse(result.retry_attempted)
        patched.assert_called_once()

    def test_continue_expected_executes_continue_then_login_flow(self) -> None:
        device, selectors = configured_device()
        device.hierarchies = [CONTINUE_AS_XML, LOGIN_FORM_XML, LOGIN_FORM_XML, CONNECTED_XML]

        result = self.run_flow(
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

        result = self.run_flow(
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

        result = self.run_flow(
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

        result = self.run_flow(
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
        self.assertIn("tap_continue", result.actions_taken)
        self.assertIn("route:continue_expected_account", result.actions_taken)
        self.assertEqual(result.safe_metadata["post_continue_final_screen_type"], "continue_password_only")
        self.assertEqual(result.safe_metadata["displayed_username"], "random_expected")
        self.assertEqual(result.safe_metadata["password_only_username"], "random_expected")
        self.assertEqual(result.safe_metadata["password_result"]["username_input_result"], "not_required")
        self.assertFalse(result.retry_attempted)
        self.assertIsNone(result.dashboard_action_type)

    def test_direct_continue_password_only_connected_without_username_input(self) -> None:
        device, selectors = configured_device(CONNECTED_XML)
        secret = TrackingSecretValue(PASSWORD)

        result = self.run_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username=USERNAME,
            credentials_getter=Mock(return_value={"username": USERNAME, "password": secret}),
            initial_signals={
                "screen_type": "continue_password_only",
                "suggested_username": USERNAME,
                "has_password_field": True,
                "has_login_button": True,
                "has_username_field": False,
                "ready_for_password_submit": True,
            },
        )

        self.assertEqual(result.final_outcome, "connected")
        self.assertEqual(result.safe_metadata["screen_type"], "continue_password_only")
        self.assertEqual(result.safe_metadata["displayed_username"], USERNAME)
        self.assertEqual(result.safe_metadata["password_only_username"], USERNAME)
        self.assertTrue(result.safe_metadata["username_match"])
        self.assertEqual(result.safe_metadata["router_decision"], "start_login_form_flow")
        self.assertEqual(result.safe_metadata["selected_route"], "continue_password_only")
        self.assertEqual(result.safe_metadata["password_result"]["username_input_result"], "not_required")
        self.assertFalse(result.safe_metadata["password_result"]["username_replaced"])
        self.assertEqual(selectors["username"].set_text_calls, [])
        self.assertTrue(secret.revealed)
        self.assertIn("login_form_submit", result.actions_taken)

    def test_direct_continue_password_only_wrong_username_blocks_without_secret_reveal(self) -> None:
        device, selectors = configured_device(CONNECTED_XML)
        secret = TrackingSecretValue(PASSWORD)
        getter = Mock(return_value={"username": USERNAME, "password": secret})

        result = self.run_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username=USERNAME,
            credentials_getter=getter,
            initial_signals={
                "screen_type": "continue_password_only",
                "suggested_username": "i_m_your_traker",
                "has_password_field": True,
                "has_login_button": True,
                "has_username_field": False,
            },
        )

        self.assertEqual(result.final_outcome, "mismatch")
        self.assertEqual(result.reason, "continue_password_only_username_mismatch")
        self.assertEqual(result.safe_metadata["selected_route"], "continue_password_only_username_mismatch")
        self.assertFalse(result.safe_metadata["username_match"])
        self.assertEqual(result.safe_metadata["displayed_username"], "i_m_your_traker")
        getter.assert_not_called()
        self.assertFalse(secret.revealed)
        self.assertEqual(selectors["password"].set_text_calls, [])
        self.assertNotIn("login_form_submit", result.actions_taken)

    def test_direct_continue_password_only_missing_username_blocks_without_secret_reveal(self) -> None:
        device, selectors = configured_device(CONNECTED_XML)
        getter = Mock(return_value=credentials())

        result = self.run_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username=USERNAME,
            credentials_getter=getter,
            initial_signals={
                "screen_type": "continue_password_only",
                "suggested_username": "",
                "has_password_field": True,
                "has_login_button": True,
                "has_username_field": False,
            },
        )

        self.assertEqual(result.reason, "continue_password_only_username_mismatch")
        self.assertFalse(result.safe_metadata["username_match"])
        getter.assert_not_called()
        self.assertEqual(selectors["password"].set_text_calls, [])
        self.assertNotIn("login_form_submit", result.actions_taken)

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
            result = self.run_flow(
                device,
                account_id=ACCOUNT_ID,
                expected_username=expected_username,
                credentials_getter=getter,
                initial_signals={**CONTINUE_SIGNALS, "suggested_username": expected_username},
            )

        self.assertTrue(selectors["continue"].click_calls == 1 or device.bounds_clicks)
        sleep.assert_called_once_with(1.0)
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
            result = self.run_flow(
                device,
                account_id=ACCOUNT_ID,
                expected_username=expected_username,
                credentials_getter=getter,
                initial_signals={**CONTINUE_SIGNALS, "suggested_username": expected_username},
            )

        self.assertTrue(selectors["continue"].click_calls == 1 or device.bounds_clicks)
        self.assertEqual(sleep.call_count, 4)
        sleep.assert_called_with(1.0)
        self.assertEqual(result.failure_reason, "unknown_login_screen")
        self.assertEqual(result.safe_metadata["post_continue_reobserve_count"], 4)
        self.assertEqual(result.safe_metadata["post_continue_final_screen_type"], "unknown")
        getter.assert_not_called()
        self.assertNotIn("login_form_submit", result.actions_taken)

    def test_dry_run_current_password_only_overlay_is_ready_without_submit(self) -> None:
        device, selectors = configured_device()
        getter = Mock(return_value=credentials())

        result = self.run_flow(
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

        result = self.run_flow(
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
        self.assertEqual(result.safe_metadata["selected_route"], "use_another_profile")
        self.assertEqual(result.final_outcome, "connected")
        self.assertEqual(
            result.safe_metadata["previous_account_lifecycle"]["source"],
            "operator_smoke_override",
        )

    def test_previous_canceled_reusable_prefilled_wrong_username_is_replaced_then_submitted(self) -> None:
        device, selectors = configured_device()
        selectors["username"]._count = 0
        prefilled_username = device.add_selector("text", "random_old_profile", FakeSelector(1))
        device.hierarchies = [
            CONTINUE_AS_XML,
            PREFILLED_LOGIN_FORM_XML,
            PREFILLED_LOGIN_FORM_XML,
            CONNECTED_XML,
        ]
        lookup = Mock(
            return_value={
                "lifecycle_status": "canceled",
                "clone_reuse_allowed": True,
                "source": "operator_smoke_override",
                "reason": "previous account stopped; clone reusable",
            }
        )

        result = self.run_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username=USERNAME,
            credentials_getter=Mock(return_value=credentials()),
            previous_account_lifecycle_lookup=lookup,
            initial_signals=WRONG_CONTINUE_SIGNALS,
        )

        self.assertEqual(result.final_outcome, "connected")
        self.assertIn("tap_use_another_profile", result.actions_taken)
        self.assertIn("route:start_login_form_flow_replace_username", result.actions_taken)
        self.assertEqual(result.safe_metadata["selected_route"], "use_another_profile")
        self.assertEqual(prefilled_username.set_text_calls, [USERNAME])
        self.assertEqual(selectors["password"].set_text_calls, [PASSWORD])
        self.assertEqual(result.safe_metadata["screen_type"], "login_form_prefilled_username")
        self.assertEqual(result.safe_metadata["prefilled_username"], "random_old_profile")
        self.assertEqual(result.safe_metadata["previous_account_lifecycle"]["source"], "operator_smoke_override")
        self.assertEqual(result.safe_metadata["previous_account_lifecycle"]["lifecycle_status"], "canceled")
        self.assertTrue(result.safe_metadata["previous_account_lifecycle"]["clone_reuse_allowed"])
        self.assertTrue(result.safe_metadata["password_result"]["username_replaced"])
        self.assertEqual(result.safe_metadata["password_result"]["username_input_result"], "username_input_assumed")

    def test_direct_prefilled_expected_username_submits_without_username_replace(self) -> None:
        device, selectors = configured_device(CONNECTED_XML)
        selectors["username"]._count = 0
        device.add_selector("text", USERNAME, FakeSelector(1))
        expected_prefilled_xml = PREFILLED_LOGIN_FORM_XML.replace("random_old_profile", USERNAME)
        device.hierarchies = [expected_prefilled_xml, CONNECTED_XML]
        secret = TrackingSecretValue(PASSWORD)

        result = self.run_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username=USERNAME,
            credentials_getter=Mock(return_value={"username": USERNAME, "password": secret}),
            initial_signals={
                "screen_type": "login_form_prefilled_username",
                "suggested_username": USERNAME,
                "prefilled_username": USERNAME,
                "username_prefilled_present": True,
                "username_field_present": True,
                "username_field_editable_present": True,
                "has_password_field": True,
                "has_login_button": True,
            },
        )

        self.assertEqual(result.final_outcome, "connected")
        self.assertEqual(result.safe_metadata["selected_route"], "login_form_prefilled_expected")
        self.assertFalse(result.safe_metadata["password_result"]["username_replaced"])
        self.assertTrue(selectors["password"].set_text_calls)
        self.assertTrue(secret.revealed)

    def test_direct_prefilled_old_reusable_username_is_replaced_then_submitted(self) -> None:
        device, selectors = configured_device(CONNECTED_XML)
        selectors["username"]._count = 0
        prefilled_username = device.add_selector("text", "random_old_profile", FakeSelector(1))
        device.hierarchies = [PREFILLED_LOGIN_FORM_XML, CONNECTED_XML]
        lookup = self._canceled_lifecycle()

        result = self.run_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username=USERNAME,
            credentials_getter=Mock(return_value=credentials()),
            previous_account_lifecycle_lookup=lookup,
            initial_signals={
                "screen_type": "login_form_prefilled_username",
                "suggested_username": "random_old_profile",
                "prefilled_username": "random_old_profile",
                "username_prefilled_present": True,
                "username_field_present": True,
                "username_field_editable_present": True,
                "has_password_field": True,
                "has_login_button": True,
            },
        )

        self.assertEqual(result.final_outcome, "connected")
        self.assertEqual(result.safe_metadata["selected_route"], "replace_prefilled_username")
        self.assertEqual(prefilled_username.set_text_calls, [USERNAME])
        self.assertTrue(result.safe_metadata["password_result"]["username_replaced"])

    def test_direct_prefilled_unknown_lifecycle_blocks_without_secret_reveal(self) -> None:
        device, selectors = configured_device(CONNECTED_XML)
        secret = TrackingSecretValue(PASSWORD)
        getter = Mock(return_value={"username": USERNAME, "password": secret})

        result = self.run_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username=USERNAME,
            credentials_getter=getter,
            previous_account_lifecycle_lookup=Mock(
                return_value={"lifecycle_status": "unknown", "clone_reuse_allowed": False}
            ),
            initial_signals={
                "screen_type": "login_form_prefilled_username",
                "suggested_username": "random_old_profile",
                "prefilled_username": "random_old_profile",
                "username_prefilled_present": True,
                "username_field_present": True,
                "username_field_editable_present": True,
                "has_password_field": True,
                "has_login_button": True,
            },
        )

        self.assertEqual(result.final_outcome, "mismatch")
        self.assertEqual(result.reason, "username_prefilled_mismatch_requires_review")
        self.assertEqual(result.safe_metadata["selected_route"], "username_prefilled_mismatch_requires_review")
        getter.assert_not_called()
        self.assertFalse(secret.revealed)
        self.assertEqual(selectors["password"].set_text_calls, [])

    def test_prefilled_username_not_editable_stops_without_submit(self) -> None:
        getter = Mock(return_value=credentials())

        result = self.run_flow(
            FakeDevice(),
            account_id=ACCOUNT_ID,
            expected_username=USERNAME,
            credentials_getter=getter,
            initial_signals={
                "screen_type": "login_form_prefilled_username",
                "prefilled_username": "random_old_profile",
                "username_prefilled_present": True,
                "username_field_present": True,
                "username_field_editable_present": False,
                "has_password_field": True,
                "has_login_button": True,
            },
        )

        self.assertEqual(result.final_outcome, "username_prefilled_not_editable")
        self.assertEqual(result.safe_metadata["selected_route"], "username_prefilled_mismatch_requires_review")
        self.assertFalse(result.safe_metadata["would_submit_password"])
        getter.assert_not_called()

    def test_routing_screen_type_uses_startup_continue_when_probe_misclassified_home(self) -> None:
        signals = {
            "screen_type": "active_account_home",
            "suggested_username": "random_old_profile",
            "has_continue_button": True,
            "has_use_another_profile": True,
        }
        preparation = {
            "expected_username": USERNAME,
            "startup_final_screen_type": "continue_as_candidate",
            "screen_after_app_start_final": "continue_as_candidate",
        }

        self.assertEqual(
            provisioner_orchestrator._routing_screen_type(signals, preparation),
            "continue_as_candidate",
        )

    def test_route_provisioning_screen_cas_a_fallback_when_probe_unknown(self) -> None:
        routing_signals = {
            "screen_type": "unknown",
            "suggested_username": "random_old_profile",
            "available_usernames": [],
        }
        previous_account_lifecycle = {
            "username": "random_old_profile",
            "lifecycle_status": "canceled",
            "clone_reuse_allowed": True,
            "source": "operator_smoke_override",
        }

        route = provisioner_orchestrator._route_provisioning_screen(
            expected_username=USERNAME,
            routing_signals=provisioner_orchestrator._routing_signals(
                routing_signals,
                {
                    "expected_username": USERNAME,
                    "startup_final_screen_type": "continue_as_candidate",
                },
                previous_account_lifecycle=previous_account_lifecycle,
            ),
            previous_account_lifecycle=previous_account_lifecycle,
            account_id=ACCOUNT_ID,
        )

        self.assertEqual(route.decision, "use_another_profile_previous_account_stopped")

    def test_use_another_profile_unknown_then_prefilled_settles_without_early_stop(self) -> None:
        device, selectors = configured_device()
        selectors["username"]._count = 0
        device.add_selector("text", "random_old_profile", FakeSelector(1))
        device.hierarchies = [
            CONTINUE_AS_XML,
            UNKNOWN_XML,
            PREFILLED_LOGIN_FORM_XML,
            PREFILLED_LOGIN_FORM_XML,
            CONNECTED_XML,
        ]

        result = self.run_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username=USERNAME,
            credentials_getter=Mock(return_value=credentials()),
            previous_account_lifecycle_lookup=self._canceled_lifecycle(),
            initial_signals=WRONG_CONTINUE_SIGNALS,
        )

        self.assertEqual(result.final_outcome, "connected")
        self.assertGreaterEqual(result.safe_metadata.get("post_use_another_profile_observation_count", 0), 1)
        post_screens = result.safe_metadata.get("post_use_another_profile_screens", [])
        self.assertTrue(
            any(screen in post_screens for screen in ("unknown", "transition_unknown")),
            post_screens,
        )
        self.assertEqual(
            result.safe_metadata.get("screen_after_use_another_profile_final"),
            "login_form_prefilled_username",
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

        result = self.run_flow(
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

        result = self.run_flow(
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

        result = self.run_flow(
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

        result = self.run_flow(
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

        result = self.run_flow(
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

        result = self.run_flow(
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

        result = self.run_flow(
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

        result = self.run_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username=USERNAME,
            credentials_getter=Mock(return_value={"username": USERNAME, "password": "not-secret"}),
            initial_signals=LOGIN_FORM_SIGNALS,
        )

        self.assertEqual(result.final_outcome, "credentials_invalid")
        self.assertEqual(result.dashboard_action_type, "update_instagram_password")
        self.assertEqual(result.safe_metadata["credentials_error_code"], "password_secret_invalid")
        self.assertEqual(result.safe_metadata["credentials_invalid_reason"], "password_secret_invalid")
        self.assertEqual(selectors["password"].set_text_calls, [])

    def test_credentials_getter_exception_exposes_safe_error_code(self) -> None:
        device, selectors = configured_device()

        def failing_getter(_account_id: str):
            raise RuntimeError("SUPABASE_URL is not set")

        result = self.run_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username=USERNAME,
            credentials_getter=failing_getter,
            initial_signals=LOGIN_FORM_SIGNALS,
        )

        self.assertEqual(result.final_outcome, "credentials_invalid")
        self.assertEqual(result.safe_metadata["credentials_error_code"], "supabase_env_missing")
        self.assertEqual(result.safe_metadata["credentials_invalid_reason"], "supabase_env_missing")
        self.assertEqual(result.safe_metadata["credentials_stage"], "credentials_getter")
        self.assertEqual(selectors["password"].set_text_calls, [])

    def test_runtime_secret_reader_failure_preserves_error_code(self) -> None:
        device, selectors = configured_device()
        credentials_result = InstagramLoginCredentialsResult(
            ok=False,
            account_id=ACCOUNT_ID,
            provider="instagram",
            reason="secret_reader_failed",
            failure_reason="secret_reader_failed",
            credentials_status="active",
            credentials_version=1000,
        )

        result = self.run_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username=USERNAME,
            credentials_getter=Mock(return_value=credentials_result),
            initial_signals=LOGIN_FORM_SIGNALS,
        )

        self.assertEqual(result.final_outcome, "credentials_invalid")
        self.assertEqual(result.reason, "secret_reader_failed")
        self.assertEqual(result.safe_metadata["credentials_error_code"], "secret_reader_failed")
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

        result = self.run_flow(
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

        result = self.run_flow(
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

        result = self.run_flow(
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

        result = self.run_flow(
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
        self.assertEqual(result.publish_reason, "published_connected")
        self.assertTrue(result.safe_metadata["publish_attempted"])
        self.assertEqual(result.safe_metadata["publish_result"], "published")
        publisher.assert_called_once()

    def test_publish_payload_matches_real_publisher_signature(self) -> None:
        calls = []

        def strict_publisher(
            account_id,
            login_status=None,
            provisioning_status=None,
            onboarding_status=None,
            reauth_required=None,
            reauth_reason=None,
            reason=None,
            external_request_id=None,
            metadata=None,
        ):
            calls.append(
                {
                    "account_id": account_id,
                    "login_status": login_status,
                    "provisioning_status": provisioning_status,
                    "onboarding_status": onboarding_status,
                    "reauth_required": reauth_required,
                    "reauth_reason": reauth_reason,
                    "reason": reason,
                    "external_request_id": external_request_id,
                    "metadata": metadata,
                }
            )
            return {"published": True, "reason": "published"}

        result = self._run_login_form(CONNECTED_XML, publisher=strict_publisher, publish_enabled=True)

        self.assertTrue(result.published)
        self.assertEqual(result.publish_reason, "published_connected")
        self.assertEqual(calls[0]["login_status"], "connected")
        rendered_payload = json.dumps(result.publish_payload, sort_keys=True)
        self.assertNotIn('"stage"', rendered_payload.split('"metadata"')[0])
        self.assertNotIn('"probe_version"', rendered_payload.split('"metadata"')[0])
        self.assertNotIn('"source"', rendered_payload.split('"metadata"')[0])

    def test_post_login_location_services_prompt_publishes_connected_ready_and_safe_back(self) -> None:
        calls = []

        def strict_publisher(
            account_id,
            login_status=None,
            provisioning_status=None,
            onboarding_status=None,
            reauth_required=None,
            reauth_reason=None,
            reason=None,
            external_request_id=None,
            metadata=None,
        ):
            calls.append(
                {
                    "account_id": account_id,
                    "login_status": login_status,
                    "provisioning_status": provisioning_status,
                    "onboarding_status": onboarding_status,
                    "reauth_required": reauth_required,
                    "reauth_reason": reauth_reason,
                    "reason": reason,
                    "external_request_id": external_request_id,
                    "metadata": metadata,
                }
            )
            return {"published": True, "reason": "published"}

        device, _selectors = configured_device(POST_LOGIN_LOCATION_SERVICES_PROMPT_XML)
        result = self.run_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username=USERNAME,
            credentials_getter=Mock(return_value=credentials()),
            initial_signals=LOGIN_FORM_SIGNALS,
            publisher=strict_publisher,
            publish_enabled=True,
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.final_outcome, "connected")
        self.assertEqual(result.final_login_status, "connected")
        self.assertEqual(result.final_provisioning_status, "ready")
        self.assertEqual(result.publish_reason, "published_connected")
        self.assertEqual(calls[0]["login_status"], "connected")
        self.assertEqual(calls[0]["provisioning_status"], "ready")
        self.assertFalse(calls[0]["reauth_required"])
        self.assertIsNone(calls[0]["reauth_reason"])
        self.assertTrue(result.safe_metadata["password_result"]["post_login_location_services_prompt_detected"])
        self.assertTrue(result.safe_metadata["password_result"]["post_login_location_services_prompt_dismissed"])
        self.assertEqual(
            result.safe_metadata["password_result"]["post_login_location_services_prompt_dismiss_method"],
            "back",
        )
        self.assertEqual(device.press_calls, ["back"])

    def test_publish_payload_safe(self) -> None:
        publisher = Mock(return_value={"published": True})

        result = self._run_login_form(CONNECTED_XML, publisher=publisher, publish_enabled=True)
        rendered = json.dumps(result.publish_payload, sort_keys=True)

        self.assertNotIn(PASSWORD, rendered)
        self.assertNotIn(SECRET_REF, rendered)
        self.assertNotIn(VAULT_ID, rendered)

    def test_connected_publish_failure_is_fail_open(self) -> None:
        publisher = Mock(return_value={"published": False, "reason": "rpc_failed"})

        result = self._run_login_form(CONNECTED_XML, publisher=publisher, publish_enabled=True)

        self.assertTrue(result.ok)
        self.assertEqual(result.final_outcome, "connected")
        self.assertFalse(result.published)
        self.assertEqual(result.publish_reason, "publisher_rpc_error")
        self.assertEqual(result.safe_metadata["publish_result"], "failed")
        self.assertEqual(result.safe_metadata["publish_error_code"], "publisher_rpc_error")
        self.assertIn("publish_failed_safe", result.warnings)

    def test_publisher_type_error_is_safe_invalid_payload(self) -> None:
        def publisher(**_payload):
            raise TypeError("unexpected keyword")

        result = self._run_login_form(CONNECTED_XML, publisher=publisher, publish_enabled=True)

        self.assertTrue(result.ok)
        self.assertEqual(result.final_outcome, "connected")
        self.assertFalse(result.published)
        self.assertEqual(result.publish_reason, "publisher_invalid_payload")
        self.assertEqual(result.safe_metadata["publish_error_code"], "publisher_invalid_payload")
        self.assertIn("publish_failed_safe", result.warnings)

    def test_non_connected_outcomes_deferred_from_publish_v1(self) -> None:
        publisher = Mock(return_value={"published": True})

        for xml, expected_reason in (
            (NEEDS_2FA_XML, "deferred_until_dashboard"),
            (CHECKPOINT_XML, "deferred_until_dashboard"),
            (LOGIN_FAILED_XML, "deferred_until_dashboard"),
        ):
            with self.subTest(xml=xml):
                result = self._run_login_form(xml, publisher=publisher, publish_enabled=True)
                self.assertFalse(result.published)
                self.assertEqual(result.publish_reason, expected_reason)

        publisher.assert_not_called()

    def test_publish_missing_account_id_skips_publisher(self) -> None:
        publisher = Mock(return_value={"published": True})
        result = provisioner_orchestrator._finalize(
            ok=True,
            completed=True,
            final_outcome="connected",
            reason="login_connected",
            account_id="",
            expected_username=USERNAME,
            actions_taken=["login_form_submit"],
            timings={},
            warnings=[],
            extra_metadata={
                "central_orchestrator_used": True,
                "selected_route": "login_form_empty",
            },
            total_start=provisioner_orchestrator.time.perf_counter(),
            timer=provisioner_orchestrator.time.perf_counter,
            final_login_status="connected",
            final_provisioning_status="ready",
            final_onboarding_status="ready",
            should_publish_status=True,
            publisher=publisher,
            publish_enabled=True,
        )

        self.assertEqual(result.final_outcome, "connected")
        self.assertFalse(result.published)
        self.assertEqual(result.publish_reason, "missing_account_id")
        publisher.assert_not_called()

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
        result = self.run_flow(
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

        self.assertEqual(
            result.actions_taken,
            [
                "route:start_login_form_flow",
                "login_form_empty_detected",
                "credential_runtime_read_started",
                "credential_runtime_read_ok",
                "login_form_submit",
            ],
        )

    def test_dry_run_login_form_does_not_request_credentials_or_submit(self) -> None:
        device, selectors = configured_device()
        getter = Mock(return_value=credentials())

        result = self.run_flow(
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

        result = self.run_flow(
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

        result = self.run_flow(
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

    def test_account_picker_expected_absent_stops_without_submit(self) -> None:
        getter = Mock(return_value=credentials())

        result = self.run_flow(
            FakeDevice(),
            account_id=ACCOUNT_ID,
            expected_username="missing_expected",
            credentials_getter=getter,
            initial_signals=ACCOUNT_PICKER_SIGNALS,
        )

        self.assertEqual(result.final_outcome, "mismatch")
        self.assertIn("route:expected_account_not_listed", result.actions_taken)
        self.assertEqual(result.safe_metadata["selected_route"], "expected_account_not_listed")
        self.assertNotIn("tap_expected_account", result.actions_taken)
        self.assertNotIn("login_form_submit", result.actions_taken)
        self.assertFalse(result.safe_metadata["account_picker_selection_executed"])
        getter.assert_not_called()

    def test_account_picker_tap_expected_then_connected_finalizes_without_password(self) -> None:
        device, _selectors = configured_device()
        getter = Mock(return_value=credentials())
        device.hierarchies = [ACCOUNT_PICKER_XML, CONNECTED_XML, CONNECTED_XML]

        result = self.run_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username="random_expected",
            credentials_getter=getter,
            initial_signals=ACCOUNT_PICKER_SIGNALS,
        )

        self.assertEqual(result.final_outcome, "connected")
        self.assertIn("tap_expected_account", result.actions_taken)
        self.assertEqual(result.safe_metadata["selected_route"], "account_picker")
        self.assertEqual(result.safe_metadata["available_usernames"], ["random_expected", "random_old_profile"])
        self.assertTrue(result.safe_metadata["expected_username_present"])
        self.assertEqual(result.safe_metadata["selected_account_username"], "random_expected")
        self.assertTrue(result.safe_metadata["account_picker_selection_executed"])
        self.assertGreaterEqual(result.safe_metadata["post_account_picker_observation_count"], 1)
        self.assertIn("connected", result.safe_metadata["post_account_picker_screens"])
        self.assertEqual(result.safe_metadata["screen_after_account_picker_final"], "connected")
        self.assertFalse(result.safe_metadata["password_required"])
        self.assertFalse(result.safe_metadata["would_submit_password"])
        getter.assert_not_called()

    def test_account_picker_tap_expected_then_password_only_stops_before_submit_without_credentials(self) -> None:
        device, _selectors = configured_device()
        getter = Mock(return_value=None)
        device.hierarchies = [ACCOUNT_PICKER_XML, PASSWORD_ONLY_XML, PASSWORD_ONLY_XML]

        result = self.run_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username="random_expected",
            credentials_getter=getter,
            initial_signals=ACCOUNT_PICKER_SIGNALS,
        )

        self.assertEqual(result.final_outcome, "credentials_missing")
        self.assertIn("tap_expected_account", result.actions_taken)
        self.assertEqual(result.safe_metadata["selected_route"], "account_picker")
        self.assertEqual(result.safe_metadata["selected_account_username"], "random_expected")
        self.assertTrue(result.safe_metadata["account_picker_selection_executed"])
        self.assertTrue(result.safe_metadata["password_required"])
        self.assertTrue(result.safe_metadata["ready_for_password_submit"])
        self.assertFalse(result.safe_metadata["would_submit_password"])
        self.assertNotIn("login_form_submit", result.actions_taken)

    def test_account_picker_tap_expected_then_password_only_submits_credentials(self) -> None:
        device, selectors = configured_device()
        getter = Mock(return_value=credentials())
        device.hierarchies = [ACCOUNT_PICKER_XML, PASSWORD_ONLY_XML, PASSWORD_ONLY_XML, CONNECTED_XML]

        result = self.run_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username="random_expected",
            credentials_getter=getter,
            initial_signals=ACCOUNT_PICKER_SIGNALS,
        )

        self.assertEqual(result.final_outcome, "connected")
        self.assertIn("tap_expected_account", result.actions_taken)
        self.assertIn("login_form_submit", result.actions_taken)
        self.assertEqual(result.safe_metadata["selected_route"], "account_picker")
        self.assertEqual(result.safe_metadata["screen_after_account_picker_final"], "continue_password_only")
        self.assertEqual(result.safe_metadata["selected_account_username"], "random_expected")
        self.assertTrue(selectors["password"].set_text_calls)

    def test_account_picker_tap_expected_then_login_form_empty_submits_credentials(self) -> None:
        device, selectors = configured_device()
        getter = Mock(return_value=credentials())
        device.hierarchies = [ACCOUNT_PICKER_XML, LOGIN_FORM_XML, LOGIN_FORM_XML, CONNECTED_XML]

        result = self.run_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username="random_expected",
            credentials_getter=getter,
            initial_signals=ACCOUNT_PICKER_SIGNALS,
        )

        self.assertEqual(result.final_outcome, "connected")
        self.assertIn("tap_expected_account", result.actions_taken)
        self.assertIn("login_form_submit", result.actions_taken)
        self.assertEqual(result.safe_metadata["screen_after_account_picker_final"], "login_form_empty")
        self.assertEqual(selectors["username"].set_text_calls, ["random_expected"])
        self.assertEqual(selectors["password"].set_text_calls, [PASSWORD])

    def test_account_picker_tap_expected_then_prefilled_form_replaces_username(self) -> None:
        device, selectors = configured_device()
        selectors["username"]._count = 0
        prefilled_username = device.add_selector("text", "random_old_profile", FakeSelector(1))
        getter = Mock(return_value=credentials())
        device.hierarchies = [ACCOUNT_PICKER_XML, PREFILLED_LOGIN_FORM_XML, PREFILLED_LOGIN_FORM_XML, CONNECTED_XML]

        result = self.run_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username="random_expected",
            credentials_getter=getter,
            initial_signals=ACCOUNT_PICKER_SIGNALS,
        )

        self.assertEqual(result.final_outcome, "connected")
        self.assertIn("tap_expected_account", result.actions_taken)
        self.assertEqual(result.safe_metadata["screen_after_account_picker_final"], "login_form_prefilled_username")
        self.assertEqual(prefilled_username.set_text_calls, ["random_expected"])
        self.assertTrue(result.safe_metadata["password_result"]["username_replaced"])

    def test_account_picker_loading_reobserves_once_to_password_only(self) -> None:
        device, _selectors = configured_device()
        getter = Mock(return_value=None)
        device.hierarchies = [ACCOUNT_PICKER_XML, LOADING_XML, PASSWORD_ONLY_XML]

        with patch.object(provisioner_orchestrator.time, "sleep") as sleep:
            result = self.run_flow(
                device,
                account_id=ACCOUNT_ID,
                expected_username="random_expected",
                credentials_getter=getter,
                initial_signals=ACCOUNT_PICKER_SIGNALS,
            )

        sleep.assert_called_once_with(1.0)
        self.assertEqual(result.safe_metadata["post_account_picker_initial_screen"], "transition_loading")
        self.assertTrue(result.safe_metadata["post_account_picker_reobserve"])
        self.assertEqual(result.safe_metadata["post_account_picker_reobserve_count"], 1)
        self.assertEqual(result.safe_metadata["post_account_picker_final_screen_type"], "continue_password_only")
        self.assertEqual(result.safe_metadata["post_account_picker_observation_count"], 1)
        self.assertEqual(
            result.safe_metadata["post_account_picker_screens"],
            ["transition_loading", "continue_password_only"],
        )
        self.assertEqual(result.safe_metadata["screen_after_account_picker_final"], "continue_password_only")
        self.assertEqual(result.final_outcome, "credentials_missing")
        self.assertFalse(result.safe_metadata["would_submit_password"])

    def test_account_picker_unknown_transition_reobserves_once_to_password_only(self) -> None:
        device, _selectors = configured_device()
        getter = Mock(return_value=None)
        device.hierarchies = [ACCOUNT_PICKER_XML, UNKNOWN_XML, PASSWORD_ONLY_XML]

        with patch.object(provisioner_orchestrator.time, "sleep") as sleep:
            result = self.run_flow(
                device,
                account_id=ACCOUNT_ID,
                expected_username="random_expected",
                credentials_getter=getter,
                initial_signals=ACCOUNT_PICKER_SIGNALS,
            )

        sleep.assert_called_once_with(1.0)
        self.assertEqual(result.safe_metadata["post_account_picker_initial_screen"], "transition_unknown")
        self.assertEqual(result.safe_metadata["post_account_picker_reobserve_count"], 1)
        self.assertEqual(result.safe_metadata["post_account_picker_final_screen_type"], "continue_password_only")
        self.assertEqual(result.safe_metadata["screen_after_account_picker_final"], "continue_password_only")
        self.assertEqual(result.final_outcome, "credentials_missing")

    def _active_home_connected_signals(self) -> dict:
        return provisioner_orchestrator._observe_login_signals(
            FakeDevice([ACTIVE_HOME_XML]),
            expected_username=USERNAME,
        )

    def test_active_account_home_connected_probe_without_identity_stops_safe(self) -> None:
        getter = Mock(return_value=credentials())
        result = self.run_flow(
            FakeDevice([ACTIVE_HOME_XML, ACTIVE_HOME_XML]),
            account_id=ACCOUNT_ID,
            expected_username=USERNAME,
            credentials_getter=getter,
            initial_signals=self._active_home_connected_signals(),
        )

        self.assertEqual(result.reason, "identity_unknown_on_connected_home")
        self.assertEqual(result.safe_metadata["selected_route"], "identity_unknown_on_connected_home")
        self.assertNotEqual(result.reason, "connected_no_password_needed")
        self.assertFalse(result.safe_metadata.get("would_submit_password"))
        self.assertFalse(result.published)
        getter.assert_not_called()

    def test_active_account_home_operator_smoke_mismatch_starts_add_existing_recovery(self) -> None:
        device, _selectors = configured_device()
        getter = Mock(return_value=None)
        device.hierarchies = [
            ACTIVE_HOME_XML,
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

        result = self.run_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username=USERNAME,
            credentials_getter=getter,
            initial_signals=self._active_home_connected_signals(),
            operator_smoke_active_account_username="random_old_profile",
            previous_account_lifecycle_lookup=self._canceled_lifecycle(),
        )

        self.assertNotEqual(result.reason, "connected_no_password_needed")
        self.assertTrue(result.safe_metadata["account_mismatch_detected"])
        self.assertEqual(result.safe_metadata["active_account_username"], "random_old_profile")
        self.assertEqual(result.safe_metadata["actual_logged_in_username"], "random_old_profile")
        self.assertEqual(result.safe_metadata["active_account_lifecycle_source"], "operator_smoke_override")
        self.assertEqual(result.safe_metadata["active_account_lifecycle_status"], "canceled")
        self.assertTrue(result.safe_metadata["clone_reuse_allowed"])
        self.assertEqual(result.safe_metadata["recovery_path"], "add_existing_account")
        self.assertEqual(result.safe_metadata["selected_route"], "add_existing_account")
        self.assertIn("tap_profile_bottom_nav", result.actions_taken)
        getter.assert_not_called()

    def test_active_account_home_expected_profile_connected_without_password(self) -> None:
        device, _selectors = configured_device()
        getter = Mock(return_value=credentials())
        device.hierarchies = [ACTIVE_HOME_XML, ACTIVE_PROFILE_EXPECTED_XML, ACTIVE_PROFILE_EXPECTED_XML]

        result = self.run_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username="random_expected",
            credentials_getter=getter,
            initial_signals=self._active_home_connected_signals(),
        )

        self.assertEqual(result.final_outcome, "connected")
        self.assertEqual(result.reason, "active_profile_matches_expected")
        self.assertEqual(result.safe_metadata["selected_route"], "already_connected_expected")
        self.assertNotEqual(result.reason, "connected_no_password_needed")
        self.assertFalse(result.safe_metadata.get("recovery_path"))
        getter.assert_not_called()

    def test_active_account_home_mismatch_unknown_lifecycle_blocks_without_add_account(self) -> None:
        device, _selectors = configured_device()
        getter = Mock(return_value=credentials())
        device.hierarchies = [ACTIVE_HOME_XML, ACTIVE_PROFILE_OLD_XML, ACTIVE_PROFILE_OLD_XML]

        result = self.run_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username=USERNAME,
            credentials_getter=getter,
            initial_signals=self._active_home_connected_signals(),
        )

        self.assertEqual(result.final_outcome, "mismatch")
        self.assertEqual(result.safe_metadata["selected_route"], "requires_review")
        self.assertEqual(result.safe_metadata["lifecycle_gate_result"], "block_wrong_active_account")
        self.assertFalse(result.safe_metadata.get("recovery_path"))
        self.assertNotIn("tap_add_instagram_account", result.actions_taken)
        getter.assert_not_called()

    def test_active_account_home_identity_unknown_jsonl_safe(self) -> None:
        result = self.run_flow(
            FakeDevice([ACTIVE_HOME_XML, ACTIVE_HOME_XML]),
            account_id=ACCOUNT_ID,
            expected_username=USERNAME,
            credentials_getter=Mock(return_value=credentials()),
            initial_signals=self._active_home_connected_signals(),
        )
        payload = json.dumps(result.safe_metadata)

        for forbidden in (PASSWORD, SECRET_REF, VAULT_ID, "secret_ref", "vault", "Vault", "token", "emulator-5554", "screenshot"):
            self.assertNotIn(forbidden, payload)

    def test_defer_connected_no_password_when_startup_home_without_identity(self) -> None:
        signals = self._active_home_connected_signals()
        preparation = {
            "screen_after_app_start_final": "active_account_home",
            "startup_final_screen_type": "active_account_home",
        }

        self.assertTrue(
            provisioner_orchestrator._defer_connected_no_password_early_exit(
                signals,
                preparation,
                expected_username=USERNAME,
            )
        )

    def test_old_logged_in_expected_account_profile_finalizes_connected(self) -> None:
        device, _selectors = configured_device()
        getter = Mock(return_value=credentials())
        device.hierarchies = [ACTIVE_PROFILE_EXPECTED_XML]

        result = self.run_flow(
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

        result = self.run_flow(
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

        result = self.run_flow(
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

        result = self.run_flow(
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
            ACTIVE_HOME_XML,
            ACTIVE_PROFILE_OLD_XML,
            ACTIVE_PROFILE_OLD_XML,
            ACCOUNT_SWITCHER_XML,
            ACCOUNT_SWITCHER_XML,
            ACCOUNT_SWITCHER_XML,
            ACCOUNT_SWITCHER_XML,
            ADD_ACCOUNT_SHEET_XML,
            ADD_ACCOUNT_SHEET_XML,
            ADD_ACCOUNT_SHEET_XML,
            ADD_ACCOUNT_SHEET_XML,
            LOGIN_FORM_XML,
            LOGIN_FORM_XML,
            LOGIN_FORM_XML,
            LOGIN_FORM_XML,
            LOGIN_FORM_XML,
        ]

        result = self.run_flow(
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
        self.assertEqual(result.safe_metadata["recovery_path"], "add_existing_account")
        self.assertEqual(result.safe_metadata["actual_logged_in_username"], "random_old_profile")
        self.assertEqual(result.safe_metadata["active_account_username"], "random_old_profile")
        self.assertEqual(result.safe_metadata["profile_username"], "random_old_profile")
        self.assertTrue(result.safe_metadata["account_mismatch_detected"])
        self.assertTrue(result.safe_metadata["profile_opened"])
        self.assertTrue(result.safe_metadata["profile_menu_initially_missing"])
        self.assertFalse(result.safe_metadata["profile_refresh_attempted"])
        self.assertEqual(result.safe_metadata["active_account_lifecycle_source"], "operator_smoke_override")
        self.assertEqual(result.safe_metadata["active_account_lifecycle_status"], "canceled")
        self.assertTrue(result.safe_metadata["clone_reuse_allowed"])
        self.assertTrue(result.safe_metadata["account_switcher_opened"])
        self.assertTrue(result.safe_metadata["add_instagram_account_tapped"])
        self.assertTrue(result.safe_metadata["add_account_sheet_opened"])
        self.assertTrue(result.safe_metadata["log_into_existing_account_tapped"])
        self.assertEqual(result.safe_metadata["screen_after_add_existing_final"], "login_form_empty")
        self.assertNotIn("login_form_submit", result.actions_taken)

    def test_add_existing_direct_login_form_empty_skips_log_into_existing_sheet(self) -> None:
        device, _selectors = configured_device()
        getter = Mock(return_value=credentials())
        device.hierarchies = [
            ACTIVE_HOME_XML,
            ACTIVE_HOME_XML,
            ACTIVE_HOME_XML,
            ACTIVE_PROFILE_OLD_XML,
            ACTIVE_PROFILE_OLD_XML,
            ACTIVE_PROFILE_OLD_XML,
            ACCOUNT_SWITCHER_XML,
            ACCOUNT_SWITCHER_XML,
            ACCOUNT_SWITCHER_XML,
            ACCOUNT_SWITCHER_XML,
            ACCOUNT_SWITCHER_XML,
            LOGIN_FORM_XML,
            LOGIN_FORM_XML,
            LOGIN_FORM_XML,
            LOGIN_FORM_XML,
            LOGIN_FORM_XML,
        ]

        result = self.run_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username="random_expected",
            credentials_getter=getter,
            previous_account_lifecycle_lookup=Mock(
                return_value={"lifecycle_status": "canceled", "clone_reuse_allowed": True}
            ),
        )

        self.assertNotEqual(result.reason, "add_account_sheet_not_validated")
        self.assertNotIn("tap_log_into_existing_account", result.actions_taken)
        self.assertTrue(result.safe_metadata.get("add_instagram_account_tapped"))
        self.assertFalse(result.safe_metadata["add_account_sheet_opened"])
        self.assertFalse(result.safe_metadata["log_into_existing_account_tapped"])
        self.assertGreater(result.safe_metadata["post_add_existing_observation_count"], 0)
        self.assertEqual(result.safe_metadata["screen_after_add_existing_final"], "login_form_empty")
        self.assertIn("route:start_login_form_flow", result.actions_taken)

    def test_add_existing_direct_login_form_prefilled_username(self) -> None:
        device, _selectors = configured_device()
        getter = Mock(return_value=credentials())
        device.hierarchies = [
            ACTIVE_PROFILE_OLD_XML,
            ACTIVE_PROFILE_OLD_XML,
            ACCOUNT_SWITCHER_XML,
            ACCOUNT_SWITCHER_XML,
            ACCOUNT_SWITCHER_XML,
            PREFILLED_LOGIN_FORM_XML,
            PREFILLED_LOGIN_FORM_XML,
            PREFILLED_LOGIN_FORM_XML,
            PREFILLED_LOGIN_FORM_XML,
            PREFILLED_LOGIN_FORM_XML,
        ]

        result = self.run_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username="random_expected",
            credentials_getter=getter,
            previous_account_lifecycle_lookup=Mock(
                return_value={"lifecycle_status": "canceled", "clone_reuse_allowed": True}
            ),
        )

        self.assertNotEqual(result.reason, "add_account_sheet_not_validated")
        self.assertNotIn("tap_log_into_existing_account", result.actions_taken)
        self.assertEqual(result.safe_metadata["screen_after_add_existing_final"], "login_form_prefilled_username")
        self.assertIn("route:start_login_form_flow_replace_username", result.actions_taken)

    def test_add_existing_direct_account_picker(self) -> None:
        device, _selectors = configured_device()
        getter = Mock(return_value=credentials())
        device.hierarchies = [
            ACTIVE_PROFILE_OLD_XML,
            ACTIVE_PROFILE_OLD_XML,
            ACCOUNT_SWITCHER_XML,
            ACCOUNT_SWITCHER_XML,
            ACCOUNT_SWITCHER_XML,
            ACCOUNT_PICKER_XML,
            ACCOUNT_PICKER_XML,
            ACCOUNT_PICKER_XML,
            ACCOUNT_PICKER_XML,
            ACCOUNT_PICKER_XML,
            ACCOUNT_PICKER_XML,
        ]

        result = self.run_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username="random_expected",
            credentials_getter=getter,
            previous_account_lifecycle_lookup=Mock(
                return_value={"lifecycle_status": "canceled", "clone_reuse_allowed": True}
            ),
        )

        self.assertNotEqual(result.reason, "add_account_sheet_not_validated")
        self.assertNotIn("tap_log_into_existing_account", result.actions_taken)
        self.assertEqual(result.safe_metadata.get("screen_after_add_existing_final"), "account_picker")
        self.assertIn("route:select_expected_account_from_picker", result.actions_taken)

    def test_add_existing_direct_continue_as_candidate(self) -> None:
        device, _selectors = configured_device()
        getter = Mock(return_value=credentials())
        continue_xml = f'<node text="{USERNAME}" />' + CONTINUE_AS_XML
        device.hierarchies = [
            ACTIVE_PROFILE_OLD_XML,
            ACTIVE_PROFILE_OLD_XML,
            ACCOUNT_SWITCHER_XML,
            ACCOUNT_SWITCHER_XML,
            ACCOUNT_SWITCHER_XML,
            continue_xml,
            continue_xml,
            continue_xml,
            continue_xml,
            continue_xml,
            continue_xml,
            continue_xml,
            continue_xml,
        ]

        result = self.run_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username="random_expected",
            credentials_getter=getter,
            previous_account_lifecycle_lookup=Mock(
                return_value={"lifecycle_status": "canceled", "clone_reuse_allowed": True}
            ),
        )

        self.assertNotEqual(result.reason, "add_account_sheet_not_validated")
        self.assertNotIn("tap_log_into_existing_account", result.actions_taken)
        self.assertEqual(result.safe_metadata.get("screen_after_add_existing_final"), "continue_as_candidate")

    def test_add_existing_direct_continue_password_only(self) -> None:
        device, _selectors = configured_device()
        getter = Mock(return_value=credentials())
        device.hierarchies = [
            ACTIVE_PROFILE_OLD_XML,
            ACTIVE_PROFILE_OLD_XML,
            ACCOUNT_SWITCHER_XML,
            ACCOUNT_SWITCHER_XML,
            ACCOUNT_SWITCHER_XML,
            PASSWORD_ONLY_XML,
            PASSWORD_ONLY_XML,
            PASSWORD_ONLY_XML,
            PASSWORD_ONLY_XML,
            PASSWORD_ONLY_XML,
        ]

        result = self.run_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username="random_expected",
            credentials_getter=getter,
            previous_account_lifecycle_lookup=Mock(
                return_value={"lifecycle_status": "canceled", "clone_reuse_allowed": True}
            ),
        )

        self.assertNotEqual(result.reason, "add_account_sheet_not_validated")
        self.assertNotIn("tap_log_into_existing_account", result.actions_taken)
        self.assertEqual(result.safe_metadata["screen_after_add_existing_final"], "continue_password_only")

    def test_add_existing_unknown_stable_stops_post_add_existing_unknown(self) -> None:
        device, _selectors = configured_device()
        getter = Mock(return_value=credentials())
        device.hierarchies = [
            ACTIVE_PROFILE_OLD_XML,
            ACTIVE_PROFILE_OLD_XML,
            ACCOUNT_SWITCHER_XML,
            ACCOUNT_SWITCHER_XML,
            ACCOUNT_SWITCHER_XML,
            UNKNOWN_XML,
            UNKNOWN_XML,
            UNKNOWN_XML,
            UNKNOWN_XML,
            UNKNOWN_XML,
            UNKNOWN_XML,
            UNKNOWN_XML,
            UNKNOWN_XML,
        ]

        result = self.run_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username="random_expected",
            credentials_getter=getter,
            previous_account_lifecycle_lookup=Mock(
                return_value={"lifecycle_status": "canceled", "clone_reuse_allowed": True}
            ),
        )

        self.assertEqual(result.reason, "post_add_existing_unknown")
        self.assertTrue(result.safe_metadata["add_instagram_account_tapped"])
        self.assertGreater(result.safe_metadata["post_add_existing_observation_count"], 0)
        getter.assert_not_called()

    def test_add_existing_metadata_jsonl_safe(self) -> None:
        device, _selectors = configured_device()
        device.hierarchies = [
            ACTIVE_PROFILE_OLD_XML,
            ACTIVE_PROFILE_OLD_XML,
            ACCOUNT_SWITCHER_XML,
            ACCOUNT_SWITCHER_XML,
            ACCOUNT_SWITCHER_XML,
            LOGIN_FORM_XML,
            LOGIN_FORM_XML,
            LOGIN_FORM_XML,
            LOGIN_FORM_XML,
            LOGIN_FORM_XML,
        ]
        result = self.run_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username="random_expected",
            credentials_getter=Mock(return_value=credentials()),
            previous_account_lifecycle_lookup=Mock(
                return_value={"lifecycle_status": "canceled", "clone_reuse_allowed": True}
            ),
        )
        payload = json.dumps(result.safe_metadata)
        for forbidden in (PASSWORD, SECRET_REF, VAULT_ID, "secret_ref", "vault", "Vault", "token", "emulator-5554", "screenshot"):
            self.assertNotIn(forbidden, payload)

    def test_old_logged_in_recovery_can_return_continue_as_candidate(self) -> None:
        device, _selectors = configured_device()
        getter = Mock(return_value=credentials())
        continue_xml = f'<node text="random_old_profile" />' + CONTINUE_AS_XML
        device.hierarchies = [
            ACTIVE_PROFILE_OLD_XML,
            ACTIVE_PROFILE_OLD_XML,
            ACCOUNT_SWITCHER_XML,
            ACCOUNT_SWITCHER_XML,
            ACCOUNT_SWITCHER_XML,
            ADD_ACCOUNT_SHEET_XML,
            ADD_ACCOUNT_SHEET_XML,
            ADD_ACCOUNT_SHEET_XML,
            ADD_ACCOUNT_SHEET_XML,
            continue_xml,
            continue_xml,
            continue_xml,
            continue_xml,
            continue_xml,
        ]

        result = self.run_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username="random_expected",
            credentials_getter=getter,
            previous_account_lifecycle_lookup=Mock(
                return_value={"lifecycle_status": "canceled", "clone_reuse_allowed": True}
            ),
        )

        self.assertEqual(result.final_outcome, "unknown")
        self.assertIn("tap_log_into_existing_account", result.actions_taken)
        self.assertIn("route:use_another_profile_previous_account_stopped", result.actions_taken)
        self.assertEqual(result.safe_metadata.get("screen_after_add_existing_final"), "continue_as_candidate")
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
            ADD_ACCOUNT_SHEET_XML,
            ACCOUNT_PICKER_XML,
            ACCOUNT_PICKER_XML,
            ACCOUNT_PICKER_XML,
            ACCOUNT_PICKER_XML,
            ACCOUNT_PICKER_XML,
        ]

        result = self.run_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username="random_expected",
            credentials_getter=getter,
            previous_account_lifecycle_lookup=Mock(
                return_value={"lifecycle_status": "canceled", "clone_reuse_allowed": True}
            ),
        )

        self.assertIn("route:select_expected_account_from_picker", result.actions_taken)
        self.assertEqual(result.safe_metadata["recovery_path"], "add_existing_account")
        self.assertEqual(result.safe_metadata["screen_after_add_existing_final"], "account_picker")
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
        self.assertEqual(result.safe_metadata["recovery_path"], "logout_fallback")
        self.assertTrue(result.safe_metadata["logout_fallback_allowed"])
        self.assertTrue(result.safe_metadata["profile_menu_opened"])
        self.assertTrue(result.safe_metadata["settings_opened"])
        self.assertFalse(result.safe_metadata["logout_settings_scroll_attempted"])
        self.assertEqual(result.safe_metadata["logout_settings_scroll_count"], 0)
        self.assertTrue(result.safe_metadata["logout_button_visible_before_scroll"])
        self.assertTrue(result.safe_metadata["logout_button_tapped"])
        self.assertEqual(result.safe_metadata["logout_button_target_text"], "Log out")
        self.assertTrue(result.safe_metadata["save_login_info_prompt_detected"])
        self.assertTrue(result.safe_metadata["save_login_info_not_now_tapped"])
        self.assertTrue(result.safe_metadata["logout_confirmation_detected"])
        self.assertTrue(result.safe_metadata["logout_confirmation_tapped"])
        self.assertEqual(result.safe_metadata["screen_after_logout_final"], "login_form_empty")

    def test_logout_fallback_scrolls_settings_until_logout_visible(self) -> None:
        device = FakeDevice(
            [
                ACTIVE_PROFILE_OLD_MENU_XML,
                PROFILE_MENU_SHEET_XML,
                PROFILE_MENU_SHEET_XML,
                PROFILE_MENU_SHEET_XML,
                SETTINGS_AND_ACTIVITY_TOP_XML,
                SETTINGS_AND_ACTIVITY_TOP_XML,
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
        scrollable = FakeScrollableSelector()
        device.add_selector("scrollable", True, scrollable)

        result = run_old_account_logout_fallback_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username="random_expected",
            previous_account_lifecycle_lookup=self._canceled_lifecycle(),
            initial_signals=self._logout_initial_signals(),
            sleeper=Mock(),
        )

        self.assertTrue(result.ok)
        self.assertTrue(result.safe_metadata["settings_opened"])
        self.assertTrue(result.safe_metadata["logout_settings_scroll_attempted"])
        self.assertGreater(result.safe_metadata["logout_settings_scroll_count"], 0)
        self.assertFalse(result.safe_metadata["logout_button_visible_before_scroll"])
        self.assertTrue(result.safe_metadata["logout_button_visible_after_scroll"])
        self.assertTrue(result.safe_metadata["logout_button_tapped"])
        self.assertEqual(result.safe_metadata["logout_button_target_text"], "Log out")
        self.assertEqual(result.safe_metadata["screen_after_logout_final"], "login_form_empty")
        self.assertGreater(scrollable.scroll.forward_calls, 0)

    def test_logout_fallback_settings_without_logout_stops_after_bounded_scrolls(self) -> None:
        device = FakeDevice(
            [
                ACTIVE_PROFILE_OLD_MENU_XML,
                PROFILE_MENU_SHEET_XML,
                PROFILE_MENU_SHEET_XML,
                PROFILE_MENU_SHEET_XML,
                *([SETTINGS_AND_ACTIVITY_TOP_XML] * 10),
            ]
        )
        device.add_selector("scrollable", True, FakeScrollableSelector())

        result = run_old_account_logout_fallback_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username="random_expected",
            previous_account_lifecycle_lookup=self._canceled_lifecycle(),
            initial_signals=self._logout_initial_signals(),
            sleeper=Mock(),
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.failure_reason, "logout_not_visible_after_scrolls")
        self.assertEqual(result.safe_metadata["logout_settings_scroll_count"], 5)
        self.assertFalse(result.safe_metadata["logout_button_tapped"])
        self.assertNotIn("tap_logout", result.actions_taken)
        self.assertNotIn((540, 1180), device.bounds_clicks)
        self.assertNotIn((540, 1300), device.bounds_clicks)
        self.assertNotIn((540, 1420), device.bounds_clicks)
        self.assertNotIn((540, 2020), device.bounds_clicks)

    def test_logout_fallback_detects_french_logout_label(self) -> None:
        device = FakeDevice(
            [
                ACTIVE_PROFILE_OLD_MENU_XML,
                PROFILE_MENU_SHEET_XML,
                PROFILE_MENU_SHEET_XML,
                PROFILE_MENU_SHEET_XML,
                SETTINGS_AND_ACTIVITY_FRENCH_LOGOUT_XML,
                SETTINGS_AND_ACTIVITY_FRENCH_LOGOUT_XML,
                SETTINGS_AND_ACTIVITY_FRENCH_LOGOUT_XML,
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
        self.assertTrue(result.safe_metadata["logout_button_tapped"])
        self.assertEqual(result.safe_metadata["logout_button_target_text"], "Déconnexion")

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

    def test_logout_fallback_final_unknown_after_settling_stops_safe(self) -> None:
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
                UNKNOWN_XML,
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
        self.assertGreaterEqual(int(result.safe_metadata["post_logout_observation_count"] or 0), 4)

    def test_logout_fallback_post_logout_unknown_then_continue_as_old_account(self) -> None:
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
                LOGOUT_CONTINUE_AS_OLD_XML,
                LOGOUT_CONTINUE_AS_OLD_XML,
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
        self.assertEqual(result.final_outcome, "continue_as_candidate")
        self.assertEqual(result.safe_metadata["screen_after_logout_final"], "continue_as_candidate")
        self.assertEqual(result.safe_metadata["post_logout_final_suggested_username"], "random_old_profile")

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

    def test_login_flow_expected_active_account_never_uses_logout_fallback(self) -> None:
        device, _selectors = configured_device()
        getter = Mock(return_value=credentials())
        device.hierarchies = [ACTIVE_HOME_XML, ACTIVE_PROFILE_EXPECTED_XML]

        result = self.run_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username="random_expected",
            credentials_getter=getter,
            initial_signals=self._active_home_connected_signals(),
            operator_smoke_allow_logout_fallback=True,
        )

        self.assertEqual(result.final_outcome, "connected")
        self.assertEqual(result.reason, "active_profile_matches_expected")
        self.assertNotIn("tap_logout", result.actions_taken)
        getter.assert_not_called()

    def test_login_flow_mismatch_unknown_lifecycle_does_not_logout_with_flag(self) -> None:
        device, _selectors = configured_device()
        getter = Mock(return_value=credentials())
        device.hierarchies = [ACTIVE_HOME_XML, ACTIVE_PROFILE_OLD_XML]

        result = self.run_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username="random_expected",
            credentials_getter=getter,
            initial_signals=self._active_home_connected_signals(),
            operator_smoke_allow_logout_fallback=True,
        )

        self.assertEqual(result.final_outcome, "mismatch")
        self.assertNotIn("tap_logout", result.actions_taken)
        getter.assert_not_called()

    def test_login_flow_canceled_without_logout_flag_prefers_add_existing(self) -> None:
        device, _selectors = configured_device()
        getter = Mock(return_value=None)
        device.hierarchies = [
            ACTIVE_HOME_XML,
            ACTIVE_PROFILE_OLD_XML,
            ACTIVE_PROFILE_OLD_XML,
            ACCOUNT_SWITCHER_XML,
            ACCOUNT_SWITCHER_XML,
            ACCOUNT_SWITCHER_XML,
            ACCOUNT_SWITCHER_XML,
            ACCOUNT_SWITCHER_XML,
            LOGIN_FORM_XML,
            LOGIN_FORM_XML,
        ]

        result = self.run_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username="random_expected",
            credentials_getter=getter,
            initial_signals=self._active_home_connected_signals(),
            previous_account_lifecycle_lookup=self._canceled_lifecycle(),
        )

        self.assertEqual(result.safe_metadata["recovery_path"], "add_existing_account")
        self.assertFalse(result.safe_metadata["logout_fallback_allowed"])
        self.assertTrue(result.safe_metadata["add_existing_attempted"])
        self.assertNotIn("tap_logout", result.actions_taken)

    def test_login_flow_logout_fallback_flag_resumes_login_form_empty(self) -> None:
        device, _selectors = configured_device()
        getter = Mock(return_value=None)
        device.hierarchies = [
            ACTIVE_PROFILE_OLD_MENU_XML,
            PROFILE_MENU_SHEET_XML,
            PROFILE_MENU_SHEET_XML,
            PROFILE_MENU_SHEET_XML,
            SETTINGS_AND_ACTIVITY_XML,
            SETTINGS_AND_ACTIVITY_XML,
            SETTINGS_AND_ACTIVITY_XML,
            SETTINGS_AND_ACTIVITY_XML,
            SAVE_LOGIN_INFO_PROMPT_XML,
            SAVE_LOGIN_INFO_PROMPT_XML,
            LOGOUT_CONFIRMATION_PROMPT_XML,
            LOGOUT_CONFIRMATION_PROMPT_XML,
            LOGOUT_CONFIRMATION_PROMPT_XML,
            LOGIN_FORM_XML,
            LOGIN_FORM_XML,
            LOGIN_FORM_XML,
        ]

        result = self.run_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username="random_expected",
            credentials_getter=getter,
            initial_signals=self._logout_initial_signals(ACTIVE_PROFILE_OLD_MENU_XML),
            previous_account_lifecycle_lookup=self._canceled_lifecycle(),
            operator_smoke_allow_logout_fallback=True,
        )

        self.assertEqual(result.final_outcome, "credentials_missing")
        self.assertEqual(result.safe_metadata["recovery_path"], "logout_fallback")
        self.assertTrue(result.safe_metadata["logout_fallback_allowed"])
        self.assertFalse(result.safe_metadata["add_existing_attempted"])
        self.assertEqual(result.safe_metadata["screen_after_logout_final"], "login_form_empty")
        self.assertIn("tap_logout", result.actions_taken)
        self.assertIn("tap_not_now", result.actions_taken)
        self.assertIn("tap_confirm_logout", result.actions_taken)
        self.assertNotIn("tap_add_instagram_account", result.actions_taken)

    def test_login_flow_logout_fallback_resumes_account_picker(self) -> None:
        device, _selectors = configured_device()
        getter = Mock(return_value=credentials())
        device.hierarchies = [
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
            ACCOUNT_PICKER_XML,
        ]

        result = self.run_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username="random_expected",
            credentials_getter=getter,
            initial_signals=self._logout_initial_signals(ACTIVE_PROFILE_OLD_MENU_XML),
            previous_account_lifecycle_lookup=self._canceled_lifecycle(),
            operator_smoke_allow_logout_fallback=True,
        )

        self.assertEqual(result.safe_metadata["recovery_path"], "logout_fallback")
        self.assertEqual(result.safe_metadata["screen_after_logout_final"], "account_picker")
        self.assertIn("route:select_expected_account_from_picker", result.actions_taken)
        getter.assert_not_called()

    def test_login_flow_logout_fallback_resumes_continue_as_expected_username(self) -> None:
        device, _selectors = configured_device()
        getter = Mock(return_value=credentials())
        device.hierarchies = [
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
            LOGOUT_CONTINUE_AS_XML,
        ]

        result = self.run_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username="random_expected",
            credentials_getter=getter,
            initial_signals=self._logout_initial_signals(ACTIVE_PROFILE_OLD_MENU_XML),
            previous_account_lifecycle_lookup=self._canceled_lifecycle(),
            operator_smoke_allow_logout_fallback=True,
        )

        self.assertEqual(result.safe_metadata["recovery_path"], "logout_fallback")
        self.assertEqual(result.safe_metadata["selected_route"], "logout_fallback")
        self.assertEqual(result.safe_metadata["screen_after_logout_final"], "continue_as_candidate")
        self.assertIn("route:continue_expected_account", result.actions_taken)
        getter.assert_not_called()

    def test_login_flow_logout_fallback_old_continue_as_use_another_profile(self) -> None:
        device, _selectors = configured_device()
        getter = Mock(return_value=None)
        device.hierarchies = [
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
            LOGOUT_CONTINUE_AS_OLD_XML,
            LOGOUT_CONTINUE_AS_OLD_XML,
            LOGIN_FORM_XML,
            LOGIN_FORM_XML,
            LOGIN_FORM_XML,
        ]

        result = self.run_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username="random_expected",
            credentials_getter=getter,
            initial_signals=self._logout_initial_signals(ACTIVE_PROFILE_OLD_MENU_XML),
            previous_account_lifecycle_lookup=self._canceled_lifecycle(),
            operator_smoke_allow_logout_fallback=True,
        )

        self.assertEqual(result.safe_metadata["recovery_path"], "logout_fallback")
        self.assertEqual(result.safe_metadata["selected_route"], "logout_fallback")
        self.assertEqual(result.safe_metadata["screen_after_logout_final"], "continue_as_candidate")
        self.assertEqual(result.safe_metadata["post_logout_final_suggested_username"], "random_old_profile")
        self.assertIn("tap_use_another_profile", result.actions_taken)
        self.assertEqual(result.safe_metadata["screen_after_use_another_profile_final"], "login_form_empty")
        self.assertEqual(result.final_outcome, "credentials_missing")
        getter.assert_called_once()

    def test_login_flow_logout_fallback_preserves_parent_app_start_metadata(self) -> None:
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
                LOGOUT_CONTINUE_AS_OLD_XML,
                LOGOUT_CONTINUE_AS_OLD_XML,
                LOGIN_FORM_XML,
                LOGIN_FORM_XML,
                LOGIN_FORM_XML,
            ]
        )
        getter = Mock(return_value=None)

        result = run_login_provisioning_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username="random_expected",
            credentials_getter=getter,
            initial_signals=self._logout_initial_signals(ACTIVE_PROFILE_OLD_MENU_XML),
            previous_account_lifecycle_lookup=self._canceled_lifecycle(),
            operator_smoke_allow_logout_fallback=True,
            start_app_before_probe=True,
            observe_current_screen_only=False,
            sleeper=Mock(),
        )

        device.app_start.assert_called_once_with("com.instagram.android")
        self.assertTrue(result.safe_metadata["app_start_attempted"])
        self.assertTrue(result.safe_metadata["app_start_ok"])
        self.assertTrue(result.safe_metadata["post_logout_resume_observe_only"])
        self.assertEqual(result.safe_metadata["recovery_path"], "logout_fallback")
        self.assertEqual(result.safe_metadata["selected_route"], "logout_fallback")

    def test_login_flow_logout_fallback_resumes_prefilled_login_form(self) -> None:
        device, _selectors = configured_device()
        getter = Mock(return_value=credentials())
        device.hierarchies = [
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
            PREFILLED_LOGIN_FORM_XML,
            PREFILLED_LOGIN_FORM_XML,
            PREFILLED_LOGIN_FORM_XML,
        ]

        result = self.run_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username="random_expected",
            credentials_getter=getter,
            initial_signals=self._logout_initial_signals(ACTIVE_PROFILE_OLD_MENU_XML),
            previous_account_lifecycle_lookup=self._canceled_lifecycle(),
            operator_smoke_allow_logout_fallback=True,
        )

        self.assertEqual(result.safe_metadata["recovery_path"], "logout_fallback")
        self.assertEqual(result.safe_metadata["screen_after_logout_final"], "login_form_prefilled_username")
        self.assertIn("route:start_login_form_flow_replace_username", result.actions_taken)

    def test_dry_run_wrong_candidate_blocks_mismatch_without_db_assumption(self) -> None:
        getter = Mock(return_value=credentials())

        result = self.run_flow(
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
        result = self.run_flow(
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
        result = self.run_flow(
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
        return self.run_flow(
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
        return self.run_flow(
            device,
            account_id=ACCOUNT_ID,
            expected_username=expected_username,
            credentials_getter=Mock(return_value={"username": expected_username, "password": SecretValue(PASSWORD)}),
            initial_signals={**CONTINUE_SIGNALS, "suggested_username": expected_username},
        )


if __name__ == "__main__":
    unittest.main()
