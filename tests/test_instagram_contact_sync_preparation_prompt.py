from __future__ import annotations

import unittest

from instagram_contact_sync_preparation_prompt import (
    activate_contact_sync_next_once,
    canonicalize_contact_sync_next_targets,
    find_unique_contact_sync_next_target,
    is_android_contacts_permission_dialog,
    is_contact_sync_preparation_prompt,
)


CURRENT_CONTACT_PREP_XML = (
    '<hierarchy><node class="android.widget.TextView" clickable="false" enabled="true" '
    'text="Next, you can allow access to your contacts to make it easier to find your friends on Instagram" '
    'bounds="[90,355][990,668]" />'
    '<node class="android.widget.TextView" clickable="false" enabled="true" '
    'text="Your contacts will be periodically synced and stored securely on our servers" '
    'bounds="[181,713][1012,969]" />'
    '<node class="android.widget.FrameLayout" clickable="true" enabled="true" '
    'bounds="[45,1788][1035,1923]">'
    '<node class="android.widget.Button" text="Next" clickable="true" enabled="true" '
    'bounds="[68,1788][1012,1923]" /></node>'
    '<node class="android.widget.TextView" clickable="false" enabled="true" '
    'text="By tapping Next, you can choose to sync your contacts or skip this step." '
    'bounds="[45,1946][1035,2047]" /></hierarchy>'
)

ANDROID_CONTACT_PERMISSION_XML = (
    '<hierarchy><node package="com.google.android.permissioncontroller" '
    'text="Allow Instagram to access your contacts?" />'
    '<node class="android.widget.Button" text="While using the app" clickable="true" '
    'enabled="true" bounds="[10,20][100,60]" />'
    '<node class="android.widget.Button" text="Don\'t allow" clickable="true" '
    'enabled="true" bounds="[10,80][100,120]" /></hierarchy>'
)


class _Selector:
    def __init__(self, count: int = 1, bounds=(68, 1788, 1012, 1923)) -> None:
        self._count = count
        self.click_calls = 0
        self.info = {
            "text": "Next",
            "className": "android.widget.Button",
            "clickable": True,
            "enabled": True,
            "bounds": {
                "left": bounds[0], "top": bounds[1],
                "right": bounds[2], "bottom": bounds[3],
            },
        }

    def count(self):
        return self._count

    def click(self):
        self.click_calls += 1


class _Device:
    def __init__(self, count: int = 1, bounds=(68, 1788, 1012, 1923)) -> None:
        self.calls = []
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


class ContactSyncPreparationPromptTests(unittest.TestCase):
    def test_current_contact_preparation_fixture_is_recognized(self) -> None:
        self.assertTrue(is_contact_sync_preparation_prompt(CURRENT_CONTACT_PREP_XML))

    def test_current_fixture_has_one_structurally_addressable_next(self) -> None:
        device = _Device()
        canonical = canonicalize_contact_sync_next_targets(CURRENT_CONTACT_PREP_XML)
        result = find_unique_contact_sync_next_target(
            device,
            hierarchy_xml=CURRENT_CONTACT_PREP_XML,
        )
        self.assertEqual(canonical["raw_semantic_match_count"], 1)
        self.assertEqual(len(canonical["targets"]), 1)
        self.assertIs(result["target"], device.selector)
        self.assertNotIn("bounds", device.calls[0])

    def test_parent_child_duplicate_resolves_to_one_target(self) -> None:
        xml = (
            '<hierarchy><node class="android.widget.Button" content-desc="Next" '
            'clickable="true" enabled="true" bounds="[10,20][100,60]">'
            '<node class="android.view.View" text="Next" clickable="false" '
            'enabled="true" bounds="[30,30][80,50]" /></node></hierarchy>'
        )
        canonical = canonicalize_contact_sync_next_targets(xml)
        self.assertEqual(canonical["raw_semantic_match_count"], 2)
        self.assertEqual(len(canonical["targets"]), 1)

    def test_two_distinct_next_buttons_fail_closed(self) -> None:
        xml = (
            '<hierarchy><node class="android.widget.Button" text="Next" clickable="true" '
            'enabled="true" bounds="[10,20][100,60]" />'
            '<node class="android.widget.Button" text="Next" clickable="true" '
            'enabled="true" bounds="[10,80][100,120]" /></hierarchy>'
        )
        result = find_unique_contact_sync_next_target(_Device(), hierarchy_xml=xml)
        self.assertIsNone(result["target"])
        self.assertEqual(result["failure_reason"], "ambiguous_contact_sync_next")

    def test_missing_actionable_next_fails_closed(self) -> None:
        xml = CURRENT_CONTACT_PREP_XML.replace('clickable="true"', 'clickable="false"')
        result = find_unique_contact_sync_next_target(_Device(), hierarchy_xml=xml)
        self.assertIsNone(result["target"])
        self.assertEqual(result["failure_reason"], "contact_sync_next_not_found")

    def test_unique_target_is_clicked_exactly_once(self) -> None:
        device = _Device()
        result = activate_contact_sync_next_once(
            device,
            hierarchy_xml=CURRENT_CONTACT_PREP_XML,
        )
        self.assertTrue(result.acted)
        self.assertEqual(device.selector.click_calls, 1)

    def test_android_permission_dialog_is_not_contact_preparation(self) -> None:
        self.assertFalse(is_contact_sync_preparation_prompt(ANDROID_CONTACT_PERMISSION_XML))
        self.assertTrue(is_android_contacts_permission_dialog(ANDROID_CONTACT_PERMISSION_XML))


if __name__ == "__main__":
    unittest.main()
