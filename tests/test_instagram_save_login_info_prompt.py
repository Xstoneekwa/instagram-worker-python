from __future__ import annotations

import unittest

from instagram_save_login_info_prompt import (
    canonicalize_semantic_action_targets,
    dismiss_save_login_info_prompt_once,
)


def button_xml(*children: str, enabled: str = "true", top: int = 20) -> str:
    return (
        '<hierarchy><node class="android.widget.Button" content-desc="Not now" '
        f'clickable="true" enabled="{enabled}" bounds="[10,{top}][100,{top + 40}]">'
        + "".join(children)
        + "</node></hierarchy>"
    )


SEMANTIC_CHILD = (
    '<node class="android.view.View" text="Not now" content-desc="Not now" '
    'clickable="false" enabled="true" bounds="[30,30][80,50]" />'
)


class Target:
    def __init__(
        self,
        count: int,
        *,
        bounds: tuple[int, int, int, int],
        class_name: str = "android.widget.Button",
    ) -> None:
        self._count = count
        self.click_calls = 0
        self.info = {
            "bounds": {
                "left": bounds[0],
                "top": bounds[1],
                "right": bounds[2],
                "bottom": bounds[3],
            },
            "className": class_name,
        }

    def count(self) -> int:
        return self._count

    def click(self) -> None:
        self.click_calls += 1


class Device:
    def __init__(self, mapping: dict[tuple[str, str], Target]) -> None:
        self.mapping = mapping
        self.missing = Target(0, bounds=(0, 0, 1, 1))

    def __call__(self, **selector: str) -> Target:
        key, value = next(iter(selector.items()))
        return self.mapping.get((key, value), self.missing)


class SaveLoginInfoPromptTests(unittest.TestCase):
    def test_clickable_parent_and_semantic_child_collapse_to_parent(self) -> None:
        hierarchy = button_xml(SEMANTIC_CHILD)
        canonical = canonicalize_semantic_action_targets(hierarchy)
        self.assertEqual(canonical["raw_semantic_match_count"], 2)
        self.assertEqual(len(canonical["targets"]), 1)
        self.assertEqual(canonical["targets"][0].class_name, "android.widget.Button")

    def test_multiple_semantic_descendants_under_one_parent_are_one_target(self) -> None:
        hierarchy = button_xml(
            SEMANTIC_CHILD,
            '<node class="android.view.View" text="Not now" clickable="false" '
            'enabled="true" bounds="[30,50][80,59]" />',
        )
        canonical = canonicalize_semantic_action_targets(hierarchy)
        self.assertEqual(canonical["raw_semantic_match_count"], 3)
        self.assertEqual(len(canonical["targets"]), 1)

    def test_two_independent_buttons_remain_ambiguous_and_never_click(self) -> None:
        hierarchy = (
            "<hierarchy>"
            + button_xml(SEMANTIC_CHILD, top=20).removeprefix("<hierarchy>").removesuffix("</hierarchy>")
            + button_xml(SEMANTIC_CHILD, top=100).removeprefix("<hierarchy>").removesuffix("</hierarchy>")
            + "</hierarchy>"
        )
        first = Target(1, bounds=(10, 20, 100, 60))
        result = dismiss_save_login_info_prompt_once(
            Device({("description", "Not now"): first}),
            hierarchy_xml=hierarchy,
        )
        self.assertFalse(result.dismissed)
        self.assertEqual(result.failure_reason, "ambiguous_not_now_control")
        self.assertEqual(result.raw_semantic_match_count, 4)
        self.assertEqual(result.canonical_target_count, 2)
        self.assertEqual(first.click_calls, 0)

    def test_non_clickable_orphan_is_not_actionable(self) -> None:
        hierarchy = f"<hierarchy>{SEMANTIC_CHILD}</hierarchy>"
        canonical = canonicalize_semantic_action_targets(hierarchy)
        self.assertEqual(canonical["raw_semantic_match_count"], 1)
        self.assertEqual(len(canonical["targets"]), 0)

    def test_disabled_matching_parent_is_rejected(self) -> None:
        hierarchy = button_xml(SEMANTIC_CHILD, enabled="false")
        canonical = canonicalize_semantic_action_targets(hierarchy)
        self.assertEqual(canonical["raw_semantic_match_count"], 2)
        self.assertEqual(len(canonical["targets"]), 0)

    def test_current_field_shape_resolves_one_parent_and_clicks_once(self) -> None:
        hierarchy = (
            '<hierarchy><node class="android.widget.Button" content-desc="Not now" '
            'clickable="true" enabled="true" bounds="[73,2008][1007,2132]">'
            '<node class="android.view.ViewGroup" clickable="false" enabled="true" '
            'bounds="[73,2008][1007,2132]">'
            '<node class="android.view.View" text="Not now" content-desc="Not now" '
            'clickable="false" enabled="true" bounds="[458,2041][622,2099]" />'
            "</node></node></hierarchy>"
        )
        parent = Target(1, bounds=(73, 2008, 1007, 2132))
        result = dismiss_save_login_info_prompt_once(
            Device({("description", "Not now"): parent}),
            hierarchy_xml=hierarchy,
        )
        self.assertTrue(result.dismissed)
        self.assertEqual(result.raw_semantic_match_count, 2)
        self.assertEqual(result.canonical_target_count, 1)
        self.assertEqual(parent.click_calls, 1)

    def test_unique_structural_target_without_semantic_address_fails_closed(self) -> None:
        hierarchy = (
            '<hierarchy><node class="android.widget.Button" clickable="true" enabled="true" '
            'bounds="[10,20][100,60]">'
            f"{SEMANTIC_CHILD}</node></hierarchy>"
        )
        result = dismiss_save_login_info_prompt_once(Device({}), hierarchy_xml=hierarchy)
        self.assertFalse(result.dismissed)
        self.assertEqual(result.failure_reason, "not_now_canonical_target_unaddressable")


if __name__ == "__main__":
    unittest.main()
