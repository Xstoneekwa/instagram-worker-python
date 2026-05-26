from __future__ import annotations

import json
import unittest
from dataclasses import asdict
from unittest.mock import Mock

from instagram_login_action_executor import execute_login_screen_decision
from instagram_login_screen_router import route_login_screen


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
)
ACCOUNT_PICKER_DUPLICATE_EXPECTED_XML = (
    '<node text="random_expected" clickable="false" bounds="[260,350][560,400]" />'
    '<node text="random_expected" clickable="false" bounds="[260,590][560,640]" />'
    '<node text="Use another profile" clickable="true" bounds="[100,780][980,900]" />'
    '<node text="Create new account" clickable="true" bounds="[100,1900][980,2020]" />'
)
PROFILE_XML = (
    '<node content-desc="Profile" clickable="true" bounds="[880,2100][1020,2240]" />'
)
ACTIVE_PROFILE_XML = (
    '<node text="random_old_profile" clickable="true" bounds="[70,120][360,190]" />'
    '<node text="Edit profile" />'
    '<node text="Share profile" />'
)
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
ACCOUNT_SWITCHER_NESTED_ADD_XML = (
    '<node text="random_old_profile" />'
    '<node content-desc="Add Instagram account" clickable="true" bounds="[53,1978][1027,2147]" />'
    '<node text="Add Instagram account" clickable="false" bounds="[232,2035][678,2090]" />'
    '<node text="Go to Accounts Center" />'
)
USE_ANOTHER_DUPLICATE_XML = (
    '<node text="Continue" clickable="true" bounds="[100,1000][980,1120]" />'
    '<node text="Use another profile" clickable="false" bounds="[371,1215][710,1280]" />'
    '<node clickable="true" bounds="[360,1200][720,1295]" class="android.view.ViewGroup" />'
    '<node text="Create new account" clickable="true" bounds="[100,2000][980,2190]" />'
)
USE_ANOTHER_TWO_ZONES_XML = (
    '<node text="Continue" clickable="true" bounds="[100,1000][980,1120]" />'
    '<node text="Use another profile" clickable="true" bounds="[100,1200][400,1280]" />'
    '<node text="Use another profile" clickable="true" bounds="[680,1200][980,1280]" />'
    '<node text="Create new account" clickable="true" bounds="[100,2000][980,2190]" />'
)
USE_ANOTHER_OUTSIDE_ZONE_XML = (
    '<node text="Continue" clickable="true" bounds="[100,1000][980,1120]" />'
    '<node text="Use another profile" clickable="true" bounds="[100,900][400,980]" />'
    '<node text="Create new account" clickable="true" bounds="[100,2000][980,2190]" />'
)
USE_ANOTHER_DISABLED_XML = (
    '<node text="Continue" clickable="true" bounds="[100,1000][980,1120]" />'
    '<node text="Use another profile" clickable="true" enabled="false" bounds="[371,1215][710,1280]" />'
    '<node text="Create new account" clickable="true" bounds="[100,2000][980,2190]" />'
)
UNKNOWN_XML = '<node text="Instagram" />'
SENSITIVE_XML = '<node text="password secret_ref Vault token emulator-5554" />'


class FakeSelector:
    def __init__(self, count: int = 0, exc: Exception | None = None) -> None:
        self._count = count
        self.exc = exc
        self.click_calls = 0
        self.click_kwargs: list[dict] = []

    def count(self) -> int:
        return self._count

    def click(self, **kwargs) -> None:
        self.click_calls += 1
        self.click_kwargs.append(dict(kwargs))
        if self.exc:
            raise self.exc


class FakeDevice:
    def __init__(self, *, hierarchy: str = LOGIN_FORM_XML) -> None:
        self.hierarchy = hierarchy
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
        return self.hierarchy

    def click(self, x: int, y: int) -> None:
        self.bounds_clicks.append((int(x), int(y)))


class DumpFailingDevice(FakeDevice):
    def dump_hierarchy(self, compressed: bool = False) -> str:
        self.dump_calls += 1
        raise RuntimeError("dump boom")


def _continue_decision():
    return route_login_screen(
        expected_username="expected_account",
        suggested_username="expected_account",
        screen_type="continue_as_candidate",
    )


def _use_another_decision():
    return route_login_screen(
        expected_username="new_account",
        suggested_username="i_m_your_traker",
        screen_type="continue_as_candidate",
        account_lifecycle_lookup=lambda _username: {"lifecycle_status": "canceled"},
        clone_reuse_allowed=True,
    )


def _account_picker_decision(expected_username: str = "random_expected"):
    return route_login_screen(
        expected_username=expected_username,
        screen_type="account_picker",
        available_usernames=["random_expected", "random_old_profile"],
    )


class InstagramLoginActionExecutorTest(unittest.TestCase):
    def test_continue_expected_account_taps_continue_once(self) -> None:
        device = FakeDevice()
        continue_selector = device.add_selector("text", "Continue", FakeSelector(1))

        result = execute_login_screen_decision(device, _continue_decision(), sleeper=Mock())

        self.assertTrue(result.ok)
        self.assertTrue(result.executed)
        self.assertEqual(result.action, "tap_continue")
        self.assertEqual(continue_selector.click_calls, 1)
        self.assertEqual(device.dump_calls, 2)

    def test_use_another_profile_taps_use_another_profile_once(self) -> None:
        device = FakeDevice(hierarchy=CONTINUE_AS_XML)

        result = execute_login_screen_decision(device, _use_another_decision(), sleeper=Mock())

        self.assertTrue(result.ok)
        self.assertTrue(result.executed)
        self.assertEqual(result.action, "tap_use_another_profile")
        self.assertEqual(device.bounds_clicks, [(540, 1247)])
        self.assertEqual(device.dump_calls, 2)

    def test_block_wrong_suggested_account_does_not_tap(self) -> None:
        decision = route_login_screen(
            expected_username="new_account",
            suggested_username="active_account",
            screen_type="continue_as_candidate",
            account_lifecycle_lookup=lambda _username: {"lifecycle_status": "active"},
            clone_reuse_allowed=True,
        )
        device = FakeDevice()
        selector = device.add_selector("text", "Continue", FakeSelector(1))

        result = execute_login_screen_decision(device, decision)

        self.assertFalse(result.executed)
        self.assertEqual(result.action, "no_action")
        self.assertEqual(selector.click_calls, 0)
        self.assertEqual(device.dump_calls, 0)

    def test_start_login_form_flow_does_not_tap(self) -> None:
        decision = route_login_screen(expected_username="new_account", screen_type="login_form_empty")
        device = FakeDevice()

        result = execute_login_screen_decision(device, decision)

        self.assertTrue(result.ok)
        self.assertFalse(result.executed)
        self.assertEqual(result.reason, "decision_requires_no_action")
        self.assertEqual(device.selector_calls, [])

    def test_unknown_no_action_does_not_tap(self) -> None:
        decision = route_login_screen(expected_username="new_account", screen_type="unknown")
        device = FakeDevice()

        result = execute_login_screen_decision(device, decision)

        self.assertTrue(result.ok)
        self.assertFalse(result.executed)
        self.assertEqual(device.selector_calls, [])

    def test_unsupported_decision_does_not_tap_and_returns_failure_reason(self) -> None:
        device = FakeDevice()

        result = execute_login_screen_decision(device, "future_decision")

        self.assertFalse(result.ok)
        self.assertFalse(result.executed)
        self.assertEqual(result.failure_reason, "unsupported_decision")
        self.assertEqual(device.selector_calls, [])

    def test_continue_button_absent_returns_target_button_not_found(self) -> None:
        device = FakeDevice()

        result = execute_login_screen_decision(device, _continue_decision())

        self.assertFalse(result.ok)
        self.assertFalse(result.executed)
        self.assertEqual(result.failure_reason, "target_button_not_found")
        self.assertEqual(device.dump_calls, 1)

    def test_use_another_profile_button_absent_returns_target_button_not_found(self) -> None:
        device = FakeDevice()

        result = execute_login_screen_decision(device, _use_another_decision())

        self.assertFalse(result.ok)
        self.assertFalse(result.executed)
        self.assertEqual(result.failure_reason, "target_button_not_found")

    def test_ambiguous_button_returns_ambiguous_target_button(self) -> None:
        device = FakeDevice()
        selector = device.add_selector("text", "Continue", FakeSelector(2))

        result = execute_login_screen_decision(device, _continue_decision())

        self.assertFalse(result.ok)
        self.assertFalse(result.executed)
        self.assertEqual(result.failure_reason, "ambiguous_target_button")
        self.assertEqual(selector.click_calls, 0)

    def test_tap_exception_returns_tap_failed(self) -> None:
        device = FakeDevice()
        selector = device.add_selector("text", "Continue", FakeSelector(1, exc=RuntimeError("tap boom")))

        result = execute_login_screen_decision(device, _continue_decision())

        self.assertFalse(result.ok)
        self.assertFalse(result.executed)
        self.assertEqual(result.failure_reason, "tap_failed")
        self.assertEqual(selector.click_calls, 1)
        self.assertEqual(device.dump_calls, 1)

    def test_post_action_wait_ms_clamped_to_zero_and_max(self) -> None:
        for raw_wait, expected_wait, expected_sleep in ((-5, 0, None), (9999, 1500, 1.5)):
            with self.subTest(raw_wait=raw_wait):
                device = FakeDevice()
                device.add_selector("text", "Continue", FakeSelector(1))
                sleeper = Mock()

                result = execute_login_screen_decision(
                    device,
                    _continue_decision(),
                    post_action_wait_ms=raw_wait,
                    sleeper=sleeper,
                )

                self.assertEqual(result.timings["post_action_wait_ms"], expected_wait)
                if expected_sleep is None:
                    sleeper.assert_not_called()
                else:
                    sleeper.assert_called_once_with(expected_sleep)

    def test_dump_after_action_called_once(self) -> None:
        device = FakeDevice()
        device.add_selector("text", "Continue", FakeSelector(1))

        result = execute_login_screen_decision(device, _continue_decision(), sleeper=Mock())

        self.assertTrue(result.executed)
        self.assertEqual(device.dump_calls, 2)

    def test_post_action_login_form_empty_detected_after_use_another_profile(self) -> None:
        device = FakeDevice(hierarchy=CONTINUE_AS_XML)
        device.hierarchy = CONTINUE_AS_XML

        def advance_hierarchy(compressed: bool = False) -> str:
            device.dump_calls += 1
            if device.dump_calls == 1:
                return CONTINUE_AS_XML
            return LOGIN_FORM_XML

        device.dump_hierarchy = advance_hierarchy  # type: ignore[method-assign]

        result = execute_login_screen_decision(device, _use_another_decision(), sleeper=Mock())

        self.assertTrue(result.ok)
        self.assertEqual(result.post_action_screen_type, "login_form_empty")
        self.assertEqual(result.post_action_probe_reason, "post_action_observed")
        self.assertTrue(result.post_action_signals["has_login_button"])

    def test_duplicate_child_parent_nodes_deduped_to_single_bounds_tap(self) -> None:
        device = FakeDevice(hierarchy=USE_ANOTHER_DUPLICATE_XML)

        result = execute_login_screen_decision(device, _use_another_decision(), sleeper=Mock())

        self.assertTrue(result.executed)
        self.assertEqual(len(device.bounds_clicks), 1)
        self.assertEqual(device.bounds_clicks[0], (540, 1247))

    def test_exact_text_with_clickable_parent_uses_unique_bounds_center(self) -> None:
        device = FakeDevice(hierarchy=USE_ANOTHER_DUPLICATE_XML)

        result = execute_login_screen_decision(device, _use_another_decision(), sleeper=Mock())

        self.assertTrue(result.ok)
        self.assertEqual(device.bounds_clicks, [(540, 1247)])

    def test_two_distinct_use_another_profile_zones_stay_ambiguous(self) -> None:
        device = FakeDevice(hierarchy=USE_ANOTHER_TWO_ZONES_XML)

        result = execute_login_screen_decision(device, _use_another_decision())

        self.assertFalse(result.executed)
        self.assertEqual(result.failure_reason, "ambiguous_target_button")
        self.assertEqual(device.bounds_clicks, [])

    def test_use_another_profile_outside_vertical_zone_not_found(self) -> None:
        device = FakeDevice(hierarchy=USE_ANOTHER_OUTSIDE_ZONE_XML)

        result = execute_login_screen_decision(device, _use_another_decision())

        self.assertFalse(result.executed)
        self.assertEqual(result.failure_reason, "target_button_not_found")

    def test_disabled_use_another_profile_candidate_not_tapped(self) -> None:
        device = FakeDevice(hierarchy=USE_ANOTHER_DISABLED_XML)

        result = execute_login_screen_decision(device, _use_another_decision())

        self.assertFalse(result.executed)
        self.assertEqual(result.failure_reason, "target_button_not_found")

    def test_target_below_continue_and_above_create_is_accepted(self) -> None:
        device = FakeDevice(hierarchy=CONTINUE_AS_XML)

        result = execute_login_screen_decision(device, _use_another_decision(), sleeper=Mock())

        self.assertTrue(result.executed)
        self.assertEqual(device.bounds_clicks[0][1], 1247)

    def test_account_picker_taps_expected_account_row_once(self) -> None:
        device = FakeDevice(hierarchy=ACCOUNT_PICKER_XML)

        result = execute_login_screen_decision(device, _account_picker_decision(), sleeper=Mock())

        self.assertTrue(result.ok)
        self.assertTrue(result.executed)
        self.assertEqual(result.action, "tap_expected_account")
        self.assertEqual(device.bounds_clicks, [(540, 400)])

    def test_account_picker_never_taps_old_profile_for_different_expected(self) -> None:
        device = FakeDevice(hierarchy=ACCOUNT_PICKER_XML)

        result = execute_login_screen_decision(device, _account_picker_decision("random_expected"), sleeper=Mock())

        self.assertTrue(result.executed)
        self.assertNotIn((540, 640), device.bounds_clicks)

    def test_account_picker_missing_target_does_not_tap(self) -> None:
        device = FakeDevice(hierarchy=ACCOUNT_PICKER_XML)
        decision = route_login_screen(
            expected_username="missing_expected",
            screen_type="account_picker",
            available_usernames=["missing_expected"],
        )

        result = execute_login_screen_decision(device, decision)

        self.assertFalse(result.executed)
        self.assertEqual(result.failure_reason, "target_account_row_not_found")
        self.assertEqual(device.bounds_clicks, [])

    def test_account_picker_duplicate_target_rows_are_ambiguous(self) -> None:
        device = FakeDevice(hierarchy=ACCOUNT_PICKER_DUPLICATE_EXPECTED_XML)

        result = execute_login_screen_decision(device, _account_picker_decision())

        self.assertFalse(result.executed)
        self.assertEqual(result.failure_reason, "ambiguous_target_account_row")
        self.assertEqual(device.bounds_clicks, [])

    def test_old_logged_in_recovery_targets_are_single_tap(self) -> None:
        cases = (
            ("open_profile_from_home", PROFILE_XML, (950, 2170)),
            ("open_account_switcher", ACTIVE_PROFILE_XML, (215, 155)),
            ("tap_add_instagram_account", ACCOUNT_SWITCHER_XML, (540, 1905)),
            ("tap_log_into_existing_account", ADD_ACCOUNT_SHEET_XML, (540, 1760)),
        )
        for decision, xml, expected_tap in cases:
            with self.subTest(decision=decision):
                device = FakeDevice(hierarchy=xml)
                decision_obj = type(
                    "Decision",
                    (),
                    {"decision": decision, "target_username": "random_old_profile"},
                )()

                result = execute_login_screen_decision(device, decision_obj, sleeper=Mock())

                self.assertTrue(result.executed)
                self.assertEqual(device.bounds_clicks, [expected_tap])

    def test_add_instagram_account_prefers_clickable_container_for_nested_label(self) -> None:
        device = FakeDevice(hierarchy=ACCOUNT_SWITCHER_NESTED_ADD_XML)
        decision_obj = type("Decision", (), {"decision": "tap_add_instagram_account"})()

        result = execute_login_screen_decision(device, decision_obj, sleeper=Mock())

        self.assertTrue(result.executed)
        self.assertEqual(device.bounds_clicks, [(540, 2062)])

    def test_output_is_safe_without_raw_xml_or_sensitive_values(self) -> None:
        device = FakeDevice(hierarchy=SENSITIVE_XML)
        device.add_selector("text", "Continue", FakeSelector(1))

        result = execute_login_screen_decision(device, _continue_decision(), sleeper=Mock())
        rendered = json.dumps(asdict(result), sort_keys=True)

        for forbidden in (
            SENSITIVE_XML,
            "secret_ref",
            "Vault",
            "vault",
            "token",
            "password",
            "emulator-5554",
            "adb_serial",
            "device_udid",
        ):
            self.assertNotIn(forbidden, rendered)

    def test_timings_are_present(self) -> None:
        device = FakeDevice()
        device.add_selector("text", "Continue", FakeSelector(1))

        result = execute_login_screen_decision(device, _continue_decision(), sleeper=Mock())

        self.assertEqual(
            set(result.timings.keys()),
            {
                "target_lookup_ms",
                "tap_ms",
                "post_action_wait_ms",
                "post_action_dump_ms",
                "total_ms",
            },
        )

    def test_no_retry_by_default(self) -> None:
        device = DumpFailingDevice()
        device.add_selector("text", "Continue", FakeSelector(1))

        result = execute_login_screen_decision(device, _continue_decision(), sleeper=Mock())

        self.assertFalse(result.ok)
        self.assertTrue(result.executed)
        self.assertEqual(result.failure_reason, "post_action_dump_failed")
        self.assertEqual(device.dump_calls, 2)


if __name__ == "__main__":
    unittest.main()
