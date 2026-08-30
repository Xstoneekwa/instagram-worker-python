from __future__ import annotations

import unittest

from instagram_turn_on_notifications_prompt import (
    activate_notification_prompt_action_once,
    canonicalize_notification_action_targets,
    find_unique_notification_action_target,
)


CURRENT_NOTIFICATION_XML = (
    '<hierarchy><node text="Turn on notifications" class="android.widget.TextView" '
    'clickable="false" enabled="true" bounds="[191,391][889,464]" />'
    '<node class="android.widget.FrameLayout" clickable="true" enabled="true" '
    'bounds="[45,1857][1035,1992]">'
    '<node text="Next" class="android.widget.Button" clickable="true" enabled="true" '
    'bounds="[68,1857][1012,1992]" />'
    '</node></hierarchy>'
)


class _Selector:
    def __init__(self, *, count: int, bounds: tuple[int, int, int, int]) -> None:
        self._count = count
        self.click_calls = 0
        self.info = {
            "text": "Next",
            "className": "android.widget.Button",
            "clickable": True,
            "enabled": True,
            "bounds": {
                "left": bounds[0],
                "top": bounds[1],
                "right": bounds[2],
                "bottom": bounds[3],
            },
        }

    def count(self) -> int:
        return self._count

    def click(self) -> None:
        self.click_calls += 1


class _Device:
    def __init__(
        self,
        *,
        count: int = 1,
        bounds: tuple[int, int, int, int] = (68, 1857, 1012, 1992),
    ) -> None:
        self.calls: list[dict[str, object]] = []
        self.selector = _Selector(count=count, bounds=bounds)
        self.missing = _Selector(count=0, bounds=bounds)

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs == {
            "text": "Next",
            "clickable": True,
            "enabled": True,
            "className": "android.widget.Button",
        }:
            return self.selector
        return self.missing


class NotificationPromptActionTests(unittest.TestCase):
    def test_current_hierarchy_has_one_structurally_addressable_next_button(self) -> None:
        device = _Device()
        canonical = canonicalize_notification_action_targets(CURRENT_NOTIFICATION_XML)
        result = find_unique_notification_action_target(
            device,
            hierarchy_xml=CURRENT_NOTIFICATION_XML,
        )
        self.assertEqual(canonical["raw_semantic_match_count"], 1)
        self.assertEqual(len(canonical["targets"]), 1)
        self.assertIs(result["target"], device.selector)
        self.assertEqual(result["failure_reason"], "")

    def test_parent_child_semantic_duplicate_resolves_to_one_parent(self) -> None:
        xml = (
            '<hierarchy><node class="android.widget.Button" content-desc="Next" '
            'clickable="true" enabled="true" bounds="[10,20][100,60]">'
            '<node class="android.view.View" text="Next" clickable="false" '
            'enabled="true" bounds="[30,30][80,50]" /></node></hierarchy>'
        )
        canonical = canonicalize_notification_action_targets(xml)
        self.assertEqual(canonical["raw_semantic_match_count"], 2)
        self.assertEqual(len(canonical["targets"]), 1)

    def test_two_distinct_clickable_targets_fail_closed(self) -> None:
        xml = (
            '<hierarchy><node class="android.widget.Button" text="Next" clickable="true" '
            'enabled="true" bounds="[10,20][100,60]" />'
            '<node class="android.widget.Button" text="Skip" clickable="true" '
            'enabled="true" bounds="[10,80][100,120]" /></hierarchy>'
        )
        result = find_unique_notification_action_target(_Device(), hierarchy_xml=xml)
        self.assertIsNone(result["target"])
        self.assertEqual(result["failure_reason"], "ambiguous_notification_action")
        self.assertEqual(result["canonical_target_count"], 2)

    def test_nonclickable_or_disabled_semantic_control_fails_closed(self) -> None:
        for clickable, enabled in (("false", "true"), ("true", "false")):
            with self.subTest(clickable=clickable, enabled=enabled):
                xml = (
                    f'<hierarchy><node class="android.widget.Button" text="Next" '
                    f'clickable="{clickable}" enabled="{enabled}" '
                    'bounds="[10,20][100,60]" /></hierarchy>'
                )
                result = find_unique_notification_action_target(_Device(), hierarchy_xml=xml)
                self.assertIsNone(result["target"])
                self.assertEqual(result["failure_reason"], "notification_action_not_found")

    def test_different_layout_is_addressed_structurally_without_fixed_bounds(self) -> None:
        xml = CURRENT_NOTIFICATION_XML.replace(
            "[68,1857][1012,1992]",
            "[40,600][1040,760]",
        )
        device = _Device(bounds=(40, 600, 1040, 760))
        result = find_unique_notification_action_target(device, hierarchy_xml=xml)
        self.assertIs(result["target"], device.selector)
        self.assertNotIn("bounds", device.calls[0])

    def test_unique_target_is_clicked_exactly_once(self) -> None:
        device = _Device()
        result = activate_notification_prompt_action_once(
            device,
            hierarchy_xml=CURRENT_NOTIFICATION_XML,
        )
        self.assertTrue(result.acted)
        self.assertEqual(device.selector.click_calls, 1)


if __name__ == "__main__":
    unittest.main()
