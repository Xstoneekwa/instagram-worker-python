from __future__ import annotations

import unittest
from contextlib import ExitStack
from unittest.mock import patch

import instagram_navigation as nav


class FakeSelector:
    def __init__(self, *, text: str = "", exists: bool = True, focused: bool = False):
        self._text = text
        self._exists = exists
        self._focused = focused

    def wait(self, timeout: float = 0.0) -> bool:
        return self._exists

    def get_text(self) -> str:
        return self._text

    @property
    def info(self) -> dict:
        return {"focused": self._focused, "bounds": {"left": 0, "top": 0, "right": 1, "bottom": 1}}


class FakeDevice:
    def __init__(self, *, query: str, keyboard_visible: bool = True):
        self.edit = FakeSelector(text=query, exists=True, focused=keyboard_visible)
        self.keyboard_visible = keyboard_visible
        self.presses: list[str] = []
        self.dumped = False

    def __call__(self, **kwargs):
        if kwargs.get("className") == "android.widget.EditText":
            return self.edit
        if kwargs.get("textContains") == "ADB Keyboard":
            return FakeSelector(exists=self.keyboard_visible)
        return FakeSelector(exists=False)

    def press(self, key: str) -> None:
        self.presses.append(key)
        if key in {"enter", "back"}:
            self.edit._focused = False
            self.keyboard_visible = False

    def dump_hierarchy(self, compressed: bool = False) -> str:
        self.dumped = True
        return "<hierarchy />"


class FollowCtSearchRecoveryTests(unittest.TestCase):
    def _log_payloads(self, log_mock, event_name: str) -> list[dict]:
        return [
            kwargs
            for args, kwargs in log_mock.call_args_list
            if len(args) >= 2 and args[1] == event_name
        ]

    def test_recovers_blank_results_after_keyboard_hide(self) -> None:
        device = FakeDevice(query="relive.group", keyboard_visible=True)
        recovered_row = object()

        with ExitStack() as stack:
            time_mod = stack.enter_context(patch.object(nav, "time"))
            log_mock = stack.enter_context(patch.object(nav, "log"))
            stack.enter_context(
                patch.object(
                    nav,
                    "_collect_raw_row_search_elements",
                    side_effect=[
                        [],
                        [
                            (
                                recovered_row,
                                "com.instagram.androie:id/row_search_user_username",
                            )
                        ],
                    ],
                )
            )
            time_mod.sleep.return_value = None
            result = nav._recover_follow_ct_blank_search_results_once(
                device,
                "relive.group",
                scan_once=lambda: recovered_row,
            )

        self.assertIs(result, recovered_row)
        self.assertIn("enter", device.presses)
        self.assertTrue(device.dumped)
        payloads = self._log_payloads(log_mock, "follow_ct_search_results_recovery_completed")
        self.assertEqual(payloads[-1]["final_reason"], "search_results_recovered_after_keyboard_hide")
        self.assertEqual(payloads[-1]["query_text_detected"], "relive.group")
        self.assertTrue(payloads[-1]["keyboard_visible"])
        self.assertTrue(payloads[-1]["recovery_attempted"])
        self.assertEqual(payloads[-1]["rows_before_recovery"], 0)
        self.assertEqual(payloads[-1]["rows_after_recovery"], 1)

    def test_fails_closed_when_results_stay_blank(self) -> None:
        device = FakeDevice(query="relive.group", keyboard_visible=True)

        with ExitStack() as stack:
            time_mod = stack.enter_context(patch.object(nav, "time"))
            log_mock = stack.enter_context(patch.object(nav, "log"))
            stack.enter_context(
                patch.object(nav, "_collect_raw_row_search_elements", side_effect=[[], []])
            )
            time_mod.sleep.return_value = None
            result = nav._recover_follow_ct_blank_search_results_once(
                device,
                "relive.group",
                scan_once=lambda: None,
            )

        self.assertIsNone(result)
        payloads = self._log_payloads(log_mock, "follow_ct_search_results_recovery_completed")
        self.assertEqual(payloads[-1]["final_reason"], "search_results_still_blank_after_recovery")
        self.assertEqual(payloads[-1]["rows_after_recovery"], 0)

    def test_skips_recovery_when_query_text_mismatches_target(self) -> None:
        device = FakeDevice(query="other.account", keyboard_visible=True)

        with ExitStack() as stack:
            time_mod = stack.enter_context(patch.object(nav, "time"))
            log_mock = stack.enter_context(patch.object(nav, "log"))
            stack.enter_context(
                patch.object(nav, "_collect_raw_row_search_elements", return_value=[])
            )
            time_mod.sleep.return_value = None
            result = nav._recover_follow_ct_blank_search_results_once(
                device,
                "relive.group",
                scan_once=lambda: object(),
            )

        self.assertIsNone(result)
        self.assertEqual(device.presses, [])
        payloads = self._log_payloads(log_mock, "follow_ct_search_results_recovery_skipped")
        self.assertEqual(payloads[-1]["final_reason"], "search_query_mismatch_no_recovery")
        self.assertFalse(payloads[-1]["recovery_attempted"])

    def test_skips_recovery_when_rows_exist_without_exact_match(self) -> None:
        device = FakeDevice(query="relive.group", keyboard_visible=True)

        with ExitStack() as stack:
            time_mod = stack.enter_context(patch.object(nav, "time"))
            log_mock = stack.enter_context(patch.object(nav, "log"))
            stack.enter_context(
                patch.object(
                    nav,
                    "_collect_raw_row_search_elements",
                    return_value=[(object(), "com.instagram.androie:id/row_search_user_username")],
                )
            )
            time_mod.sleep.return_value = None
            result = nav._recover_follow_ct_blank_search_results_once(
                device,
                "relive.group",
                scan_once=lambda: object(),
            )

        self.assertIsNone(result)
        self.assertEqual(device.presses, [])
        payloads = self._log_payloads(log_mock, "follow_ct_search_results_recovery_skipped")
        self.assertEqual(payloads[-1]["final_reason"], "search_results_ambiguous_no_exact_match")
        self.assertEqual(payloads[-1]["rows_before_recovery"], 1)
        self.assertFalse(payloads[-1]["recovery_attempted"])


if __name__ == "__main__":
    unittest.main()
