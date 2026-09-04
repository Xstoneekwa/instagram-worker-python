from __future__ import annotations

import unittest

from profile_cta_stabilization import (
    classify_music_audio_sheet,
    derive_safe_sheet_dismiss_point,
    validate_profile_cta_transition,
)


def observation(*, top: int = 700, text: str = "Follow", clickable: bool = True):
    return {
        "resource_id": "com.instagram.android:id/profile_header_follow_button",
        "text": text,
        "semantic": text,
        "clickable": clickable,
        "bounds": {"left": 30, "top": top, "right": 530, "bottom": top + 90},
        "package": "com.instagram.android",
        "activity": "com.instagram.mainactivity.InstagramMainActivity",
        "profile_identity_ok": True,
        "overlay_present": False,
    }


def music_xml(*, use_audio: str = "Use audio", add_profile: str = "Add to profile") -> str:
    return f'''<hierarchy>
      <node resource-id="com.instagram.android:id/action_bar_title" text="theofficialcelineblanc" bounds="[0,80][1080,180]" clickable="false" />
      <node resource-id="com.instagram.android:id/music_bottom_sheet" bounds="[0,600][1080,2400]" clickable="false">
        <node text="Today's Menu" content-desc="Audio track" bounds="[40,650][1040,820]" clickable="false" />
        <node text="{add_profile}" bounds="[40,900][520,1010]" clickable="true" />
        <node text="{use_audio}" bounds="[560,900][1040,1010]" clickable="true" />
      </node>
    </hierarchy>'''


class ProfileCtaStabilizationTests(unittest.TestCase):
    def test_stable_and_tiny_shift_return_fresh_bounds_only(self):
        initial = observation(top=700)
        fresh = observation(top=706)
        result = validate_profile_cta_transition(
            initial,
            fresh,
            accepted_semantics=("follow", "suivre"),
            max_center_shift_px=140,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["fresh_bounds"], fresh["bounds"])
        self.assertFalse(result["old_bounds_reused"])

    def test_major_shift_semantic_change_disappearance_and_overlay_fail_closed(self):
        initial = observation(top=700)
        cases = [
            observation(top=1000),
            observation(top=700, text="Message"),
            {**observation(top=700), "clickable": False},
            {**observation(top=700), "overlay_present": True},
            {**observation(top=700), "bounds": {}},
        ]
        for fresh in cases:
            with self.subTest(fresh=fresh):
                result = validate_profile_cta_transition(
                    initial,
                    fresh,
                    accepted_semantics=("follow", "suivre"),
                    max_center_shift_px=140,
                )
                self.assertFalse(result["ok"])
                self.assertFalse(result["old_bounds_reused"])

    def test_music_sheet_requires_both_exact_actions_and_coherent_modal_bounds(self):
        proved = classify_music_audio_sheet(
            music_xml(), screen_width=1080, screen_height=2400
        )
        self.assertTrue(proved["proved"])
        self.assertEqual(proved["surface_type"], "MUSIC_AUDIO_SHEET")
        unknown = classify_music_audio_sheet(
            music_xml(use_audio="Continue"), screen_width=1080, screen_height=2400
        )
        self.assertFalse(unknown["proved"])
        self.assertEqual(unknown["surface_type"], "UNKNOWN_MODAL")

    def test_safe_dismiss_point_avoids_clickable_controls(self):
        xml = music_xml()
        classified = classify_music_audio_sheet(
            xml, screen_width=1080, screen_height=2400
        )
        point = derive_safe_sheet_dismiss_point(
            xml,
            sheet_bounds=classified["sheet_bounds"],
            screen_width=1080,
            screen_height=2400,
        )
        self.assertIsNotNone(point)
        assert point is not None
        self.assertLess(point[1], classified["sheet_bounds"]["top"])


if __name__ == "__main__":
    unittest.main()
