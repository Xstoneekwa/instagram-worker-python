from __future__ import annotations

import time
import unittest
from unittest.mock import patch

import follow_action_engine as engine


class _Selector:
    def __init__(self, *, exists: bool = False, text: str = "") -> None:
        self._exists = exists
        self._text = text

    def exists(self, timeout: float = 0.0) -> bool:
        return self._exists

    def get_text(self) -> str:
        return self._text


class _Device:
    def __init__(self) -> None:
        self.package = "com.instagram.android"
        self.activity = "com.instagram.mainactivity.InstagramMainActivity"
        self.selector_calls: list[dict] = []

    def app_current(self):
        return {"package": self.package, "activity": self.activity}

    def __call__(self, **kwargs):
        self.selector_calls.append(dict(kwargs))
        if "resourceIdMatches" in kwargs:
            return _Selector(exists=True, text="alice")
        return _Selector(exists=False)


class _Ign:
    class config:
        INSTAGRAM_PACKAGE = "com.instagram.android"


class Follow60OrderingV2ReentrySurfaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.device = _Device()
        self.bounds = {"left": 700, "top": 300, "right": 1010, "bottom": 420}
        self.meta = {
            "bounds": dict(self.bounds),
            "resource_id": "com.instagram.android:id/profile_header_follow_button",
            "text": "Follow",
        }

    @patch("follow_60s_canary.runtime_context", return_value={"ui_generation": 7})
    @patch("follow_action_engine.try_select_exact_profile_header_follow_fast")
    def test_capture_and_consume_use_only_fresh_level1_evidence(
        self, select_fast, _runtime
    ) -> None:
        select_fast.return_value = (object(), dict(self.meta))
        live = engine.capture_ordering_v2_profile_reentry_follow_surface(
            self.device,
            _Ign,
            "com.instagram.android",
            candidate_username="alice",
            visual_candidate_id="action-a",
            screen_guard=None,
        )
        self.assertTrue(live["ok"])
        self.assertEqual(0, live["xml_count"])
        self.assertEqual(0, live["screenshot_count"])
        proof = {
            "schema": "FOLLOW60_ORDERING_V2_BEHAVIORAL_CANARY_V1",
            "candidate_username": "alice",
            "activity": self.device.activity,
            "cta_bounds": dict(self.bounds),
            "cta_resource_id": self.meta["resource_id"],
            "cta_text": "Follow",
            "overlay_or_challenge": False,
            "ui_generation": 7,
            "captured_at_monotonic": time.monotonic(),
        }
        proxy, selected, reason = engine._select_ordering_v2_reentry_follow_from_context(
            self.device,
            _Ign,
            "com.instagram.android",
            username="alice",
            source_profile_username="ct",
            visual_candidate_id="action-a",
            pre_follow_context={
                "action_bar_title": "alice",
                "source_profile_username": "ct",
                "ordering_v2_reentry": proof,
            },
        )
        self.assertIsNotNone(proxy)
        self.assertEqual("v2_reentry_fresh_bounds_selected", reason)
        self.assertEqual(self.bounds, selected["bounds"])

    @patch("follow_60s_canary.runtime_context", return_value={"ui_generation": 8})
    def test_stale_generation_wrong_profile_and_wrong_activity_fail_closed(self, _runtime) -> None:
        base = {
            "schema": "FOLLOW60_ORDERING_V2_BEHAVIORAL_CANARY_V1",
            "candidate_username": "alice",
            "activity": self.device.activity,
            "cta_bounds": dict(self.bounds),
            "cta_resource_id": "com.instagram.android:id/profile_header_follow_button",
            "cta_text": "Follow",
            "overlay_or_challenge": False,
            "ui_generation": 7,
            "captured_at_monotonic": time.monotonic(),
        }
        common = {
            "action_bar_title": "alice",
            "source_profile_username": "ct",
            "ordering_v2_reentry": base,
        }
        proxy, _meta, reason = engine._select_ordering_v2_reentry_follow_from_context(
            self.device, _Ign, "com.instagram.android",
            username="alice", source_profile_username="ct",
            visual_candidate_id="action-a", pre_follow_context=common,
        )
        self.assertIsNone(proxy)
        self.assertEqual("v2_reentry_ui_generation_changed", reason)

        wrong = {**common, "action_bar_title": "mallory"}
        proxy, _meta, reason = engine._select_ordering_v2_reentry_follow_from_context(
            self.device, _Ign, "com.instagram.android",
            username="alice", source_profile_username="ct",
            visual_candidate_id="action-a", pre_follow_context=wrong,
        )
        self.assertIsNone(proxy)
        self.assertEqual("v2_reentry_action_bar_mismatch", reason)

        self.device.activity = "com.instagram.story.viewer.StoryViewerActivity"
        proxy, _meta, reason = engine._select_ordering_v2_reentry_follow_from_context(
            self.device, _Ign, "com.instagram.android",
            username="alice", source_profile_username="ct",
            visual_candidate_id="action-a", pre_follow_context=common,
        )
        self.assertIsNone(proxy)
        self.assertEqual("v2_reentry_live_package_or_activity_changed", reason)


if __name__ == "__main__":
    unittest.main()
