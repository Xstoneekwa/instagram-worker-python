from __future__ import annotations

import unittest

from instagram_save_login_info_prompt import dismiss_save_login_info_prompt_once


class Target:
    def __init__(self, count: int, *, bounds: tuple[int, int, int, int]) -> None:
        self._count = count
        self.click_calls = 0
        self.info = {
            "text": "Not now",
            "bounds": {
                "left": bounds[0],
                "top": bounds[1],
                "right": bounds[2],
                "bottom": bounds[3],
            },
            "className": "android.widget.Button",
        }

    def count(self) -> int:
        return self._count

    def click(self) -> None:
        self.click_calls += 1


class Device:
    def __init__(self, mapping: dict[tuple[str, str], Target]) -> None:
        self.mapping = mapping
        self.missing = Target(0, bounds=(0, 0, 0, 0))

    def __call__(self, **selector: str) -> Target:
        key, value = next(iter(selector.items()))
        return self.mapping.get((key, value), self.missing)


class SaveLoginInfoPromptTests(unittest.TestCase):
    def test_text_and_description_aliases_for_same_node_are_one_control(self) -> None:
        text_alias = Target(1, bounds=(10, 20, 100, 60))
        description_alias = Target(1, bounds=(10, 20, 100, 60))
        result = dismiss_save_login_info_prompt_once(
            Device(
                {
                    ("text", "Not now"): text_alias,
                    ("description", "Not now"): description_alias,
                }
            )
        )
        self.assertTrue(result.dismissed)
        self.assertEqual(text_alias.click_calls + description_alias.click_calls, 1)

    def test_aliases_resolving_to_different_nodes_are_ambiguous(self) -> None:
        first = Target(1, bounds=(10, 20, 100, 60))
        second = Target(1, bounds=(10, 80, 100, 120))
        result = dismiss_save_login_info_prompt_once(
            Device(
                {
                    ("text", "Not now"): first,
                    ("description", "Not now"): second,
                }
            )
        )
        self.assertFalse(result.dismissed)
        self.assertEqual(result.failure_reason, "ambiguous_not_now_control")
        self.assertEqual(first.click_calls + second.click_calls, 0)

    def test_localized_french_control_is_supported_without_coordinates(self) -> None:
        target = Target(1, bounds=(10, 20, 100, 60))
        result = dismiss_save_login_info_prompt_once(
            Device({("text", "Pas maintenant"): target})
        )
        self.assertTrue(result.dismissed)
        self.assertEqual(target.click_calls, 1)


if __name__ == "__main__":
    unittest.main()
