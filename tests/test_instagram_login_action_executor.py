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


class InstagramLoginActionExecutorTest(unittest.TestCase):
    def test_continue_expected_account_taps_continue_once(self) -> None:
        device = FakeDevice()
        continue_selector = device.add_selector("text", "Continue", FakeSelector(1))

        result = execute_login_screen_decision(device, _continue_decision(), sleeper=Mock())

        self.assertTrue(result.ok)
        self.assertTrue(result.executed)
        self.assertEqual(result.action, "tap_continue")
        self.assertEqual(continue_selector.click_calls, 1)
        self.assertEqual(device.dump_calls, 1)

    def test_use_another_profile_taps_use_another_profile_once(self) -> None:
        device = FakeDevice()
        selector = device.add_selector("text", "Use another profile", FakeSelector(1))

        result = execute_login_screen_decision(device, _use_another_decision(), sleeper=Mock())

        self.assertTrue(result.ok)
        self.assertTrue(result.executed)
        self.assertEqual(result.action, "tap_use_another_profile")
        self.assertEqual(selector.click_calls, 1)

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
        self.assertEqual(device.dump_calls, 0)

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
        self.assertEqual(device.dump_calls, 0)

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
        self.assertEqual(device.dump_calls, 1)

    def test_post_action_login_form_empty_detected_after_use_another_profile(self) -> None:
        device = FakeDevice(hierarchy=LOGIN_FORM_XML)
        device.add_selector("text", "Use another profile", FakeSelector(1))

        result = execute_login_screen_decision(device, _use_another_decision(), sleeper=Mock())

        self.assertTrue(result.ok)
        self.assertEqual(result.post_action_screen_type, "login_form_empty")
        self.assertEqual(result.post_action_probe_reason, "post_action_observed")
        self.assertTrue(result.post_action_signals["has_login_button"])

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
        self.assertEqual(device.dump_calls, 1)


if __name__ == "__main__":
    unittest.main()
