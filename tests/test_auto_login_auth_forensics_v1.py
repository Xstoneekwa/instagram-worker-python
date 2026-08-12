from __future__ import annotations

import json
import os
import unittest
from datetime import datetime, timezone

from auto_login_auth_forensics import (
    ACCOUNT_IDS_FLAG_NAME,
    FLAG_NAME,
    AuthForensicsTrace,
    enabled_for_account,
    safe_hierarchy_fingerprint,
    safe_ui_states,
)


class Clock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        self.value += 0.00001
        return self.value


class AuthForensicsV1Tests(unittest.TestCase):
    def context(self) -> dict[str, str]:
        return {
            "request_id": "request-1",
            "run_id": "run-1",
            "account_id": "account-1",
            "app_instance_id": "app-1",
            "device_id": "device-1",
            "worker_sha": "abc123",
            "expected_package": "com.instagram.clone",
        }

    def trace(self) -> AuthForensicsTrace:
        return AuthForensicsTrace(
            enabled=True,
            context=self.context(),
            timer=Clock(),
            utc_now=lambda: datetime(2026, 8, 12, 20, 0, tzinfo=timezone.utc),
        )

    def test_flag_off_by_default_and_account_allowlist(self) -> None:
        self.assertFalse(enabled_for_account("account-1", {}))
        env = {FLAG_NAME: "true", ACCOUNT_IDS_FLAG_NAME: "account-1,account-2"}
        self.assertTrue(enabled_for_account("account-1", env))
        self.assertFalse(enabled_for_account("account-3", env))

    def test_loading_to_logged_out_sequence_keeps_one_trace_id_and_attempts(self) -> None:
        trace = self.trace()
        trace.record("AUTH_FLOW_START")
        trace.mark_submit_tap(attempt=1)
        trace.observe(attempt=1, hierarchy_xml='<node text="Loading..."/>', observed={"outcome": "unknown", "screen_type": "loading"})
        trace.record("FORM_REOBSERVED", attempt=2)
        trace.mark_submit_tap(attempt=2)
        trace.observe(attempt=2, hierarchy_xml='<node text="Log in"/>', observed={"outcome": "logged_out", "screen_type": "login_form"})
        snapshot = trace.snapshot(terminal_outcome="unknown_logged_out_return")
        self.assertTrue(snapshot["auth_trace_id"])
        self.assertEqual([event["attempt_number"] for event in snapshot["events"] if event["event"] == "LOGIN_SUBMIT_TAP"], [1, 2])

    def test_logged_out_without_loading_is_classified_without_decision(self) -> None:
        trace = self.trace()
        trace.observe(attempt=1, hierarchy_xml='<node text="Log in"/>', observed={"outcome": "logged_out"})
        self.assertEqual(trace.snapshot()["events"][0]["classified_state"], "logged_out")

    def test_loading_then_challenge(self) -> None:
        trace = self.trace()
        trace.mark_submit_tap()
        trace.observe(
            attempt=1,
            hierarchy_xml='<node text="Enter code"/>',
            observed={"screen_type": "sms_code_challenge", "verification_code_challenge_detected": True},
        )
        self.assertEqual(trace.snapshot()["events"][-1]["challenge_state"], "present")

    def test_loading_then_home(self) -> None:
        trace = self.trace()
        trace.observe(attempt=1, hierarchy_xml='<node content-desc="Home"/>', observed={"outcome": "connected"})
        self.assertEqual(trace.snapshot()["events"][-1]["classified_state"], "connected")

    def test_direct_success(self) -> None:
        trace = self.trace()
        trace.mark_submit_tap()
        trace.observe(
            attempt=1,
            hierarchy_xml='<node content-desc="Home"/>',
            observed={"outcome": "connected", "screen_type": "connected"},
        )
        self.assertEqual(trace.snapshot()["terminal_outcome"], "")
        self.assertEqual(trace.snapshot()["events"][-1]["classified_state"], "connected")

    def test_loading_then_save_login_info(self) -> None:
        trace = self.trace()
        trace.mark_submit_tap()
        trace.observe(
            attempt=1,
            hierarchy_xml='<node text="Save your login info?"/>',
            observed={"screen_type": "save_login_info_prompt", "save_login_info_prompt_present": True},
        )
        event = trace.snapshot()["events"][-1]
        self.assertEqual(event["classified_state"], "save_login_info_prompt")
        self.assertEqual(event["system_overlay_state"], "present")

    def test_activity_transient_is_preserved_without_changing_decision(self) -> None:
        trace = self.trace()
        trace.observe(
            attempt=1,
            hierarchy_xml="",
            observed={"screen_type": "loading"},
            app_current={"package": "com.instagram.clone", "activity": "LoginActivity", "pid": 10},
        )
        trace.observe(
            attempt=1,
            hierarchy_xml="",
            observed={"screen_type": "challenge"},
            app_current={"package": "com.instagram.clone", "activity": "ModalActivity", "pid": 10},
        )
        activities = [event.get("foreground_activity") for event in trace.snapshot()["events"]]
        self.assertIn("LoginActivity", activities)
        self.assertIn("ModalActivity", activities)

    def test_loading_then_crash_or_process_restart(self) -> None:
        trace = self.trace()
        trace.observe(attempt=1, hierarchy_xml="", observed={"screen_type": "loading"}, app_current={"pid": 10})
        trace.observe(attempt=1, hierarchy_xml="", observed={"screen_type": "loading"}, app_current={"pid": 11})
        self.assertTrue(trace.snapshot()["events"][-1]["instagram_pid_changed"])

    def test_double_submit_traced_separately(self) -> None:
        trace = self.trace()
        for attempt in (1, 2):
            trace.record("LOGIN_SUBMIT_PLANNED", attempt=attempt)
            trace.mark_submit_tap(attempt=attempt)
            trace.record("LOGIN_SUBMIT_ACK", attempt=attempt, tap_call_succeeded=True)
        self.assertEqual(len(trace.snapshot()["events"]), 6)

    def test_button_loading_state_is_categorical(self) -> None:
        state = safe_ui_states('<node text="Log in" enabled="false"/>')
        self.assertEqual(state["button_state"], "visible_disabled_or_unknown")

    def test_ime_keyboard_is_never_collected(self) -> None:
        trace = self.trace()
        trace.record("AUTH_OBSERVATION", ime="ADB Keyboard", keyboard="visible")
        rendered = json.dumps(trace.snapshot()).lower()
        self.assertNotIn("keyboard", rendered)
        self.assertNotIn("adb keyboard", rendered)

    def test_overlay_presence_is_categorical(self) -> None:
        state = safe_ui_states("", {"save_login_info_prompt_present": True})
        self.assertEqual(state["system_overlay_state"], "present")

    def test_network_signal_is_not_collected_without_passive_source(self) -> None:
        snapshot = self.trace().snapshot()
        self.assertFalse(snapshot["passive_sources"]["network_trace"])

    def test_accessibility_transient_is_declared_unavailable_without_passive_hook(self) -> None:
        snapshot = self.trace().snapshot()
        self.assertFalse(snapshot["passive_sources"]["accessibility_stream"])

    def test_redacted_logcat_contract_is_disabled_without_hook(self) -> None:
        snapshot = self.trace().snapshot()
        self.assertFalse(snapshot["passive_sources"]["logcat_window"])

    def test_forensics_off_has_no_events(self) -> None:
        trace = AuthForensicsTrace(enabled=False, context=self.context())
        trace.record("AUTH_FLOW_START")
        self.assertEqual(trace.snapshot(), {"enabled": False, "schema_version": "auto_login_auth_forensics_v1"})

    def test_on_and_off_do_not_change_executor_decision(self) -> None:
        from instagram_credentials_runtime_access import SecretValue
        from instagram_login_password_form_executor import execute_login_form_credentials
        from tests.test_instagram_login_password_form_executor import LOGIN_FORM_SIGNALS, configured_device

        outcomes = []
        previous = os.environ.get(FLAG_NAME)
        try:
            for enabled in (False, True):
                if enabled:
                    os.environ[FLAG_NAME] = "true"
                else:
                    os.environ.pop(FLAG_NAME, None)
                device, _username, _password, _login = configured_device()
                result = execute_login_form_credentials(
                    device,
                    expected_username="cinema_catchup",
                    password=SecretValue("fake-password-for-unit-tests"),
                    prevalidated_signals=LOGIN_FORM_SIGNALS,
                    post_submit_wait_ms=0,
                    post_submit_observation_interval_ms=0,
                    sleeper=lambda _seconds: None,
                )
                outcomes.append((result.ok, result.reason, result.failure_reason, result.post_submit_outcome))
            self.assertEqual(outcomes[0], outcomes[1])
        finally:
            if previous is None:
                os.environ.pop(FLAG_NAME, None)
            else:
                os.environ[FLAG_NAME] = previous

    def test_no_secret_or_password_length_leak(self) -> None:
        xml_short = '<node text="secret" password="true" class="EditText"/>'
        xml_long = '<node text="much-longer-secret" password="true" class="EditText"/>'
        self.assertEqual(safe_hierarchy_fingerprint(xml_short), safe_hierarchy_fingerprint(xml_long))
        trace = self.trace()
        trace.record("PASSWORD_INPUT_END", password="top-secret", password_length=10, vault_ref="vault://1", confirmed=True)
        rendered = json.dumps(trace.snapshot()).lower()
        self.assertNotIn("top-secret", rendered)
        self.assertNotIn("password_length", rendered)
        self.assertNotIn("vault://", rendered)

    def test_observation_overhead_target_under_100ms(self) -> None:
        trace = self.trace()
        trace.observe(attempt=1, hierarchy_xml='<node class="TextView" text="Loading"/>', observed={"screen_type": "loading"})
        self.assertLess(trace.snapshot()["median_observation_overhead_ms"], 100.0)


if __name__ == "__main__":
    unittest.main()
