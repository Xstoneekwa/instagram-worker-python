from __future__ import annotations

import time
import unittest
import inspect
from unittest.mock import MagicMock, patch

import follow_action_engine as engine
import instagram_navigation as navigation


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

    @patch(
        "follow_60s_canary.runtime_context",
        return_value={"ui_generation": 7, "navigation_counter": 11},
    )
    @patch("follow_60s_canary.invalidate")
    @patch("follow_action_engine.try_select_exact_profile_header_follow_fast")
    def test_capture_and_consume_use_only_fresh_level1_evidence(
        self, select_fast, invalidate, _runtime
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
        invalidate.assert_called_once_with(
            "ordering_v2_profile_reentry_navigation"
        )
        self.assertEqual("nav:11:ui:7", live["navigation_generation"])
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

    @patch(
        "follow_60s_canary.runtime_context",
        return_value={"ui_generation": 0, "navigation_counter": 0},
    )
    @patch("follow_60s_canary.invalidate")
    @patch("follow_action_engine.try_select_exact_profile_header_follow_fast")
    def test_reentry_without_a_sealed_generation_still_fails_closed(
        self, select_fast, invalidate, _runtime
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
        invalidate.assert_called_once()
        self.assertFalse(live["ok"])
        self.assertEqual("v2_reentry_generation_missing", live["reason"])

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


class FollowPreTapStabilizationTests(unittest.TestCase):
    def test_live_observation_ignores_permanent_bottom_sheet_host(self) -> None:
        hierarchy = '''<hierarchy>
          <node resource-id="com.instagram.android:id/action_bar_title"
                text="stable_user" bounds="[0,100][1080,220]" visible-to-user="true" />
          <node resource-id="com.instagram.android:id/bottom_sheet_camera_container"
                class="android.widget.FrameLayout" bounds="[0,100][1080,2205]"
                visible-to-user="true" />
          <node resource-id="com.instagram.android:id/profile_header_follow_button"
                text="Follow" clickable="true" bounds="[33,800][479,890]"
                visible-to-user="true" />
        </hierarchy>'''
        device = MagicMock()
        device.app_current.return_value = {
            "package": "com.instagram.android",
            "activity": "com.instagram.mainactivity.InstagramMainActivity",
        }
        device.dump_hierarchy.return_value = hierarchy
        device.window_size.return_value = (1080, 2340)

        result = navigation._follow_pre_tap_live_observation(
            device,
            expected_username="stable_user",
            expected_package="com.instagram.android",
        )

        self.assertTrue(result["ok"])
        self.assertTrue(result["target_binding_ok"])
        self.assertFalse(result["overlay_present"])
        self.assertEqual(result["bounds"], {"left": 33, "top": 800, "right": 479, "bottom": 890})

    resource_id = "com.instagram.android:id/profile_header_follow_button"
    package = "com.instagram.android"
    activity = "com.instagram.mainactivity.InstagramMainActivity"

    def _observation(
        self,
        *,
        top: int = 700,
        ok: bool = True,
        target_binding_ok: bool = True,
        overlay_present: bool = False,
        music: bool = False,
    ) -> dict:
        return {
            "ok": ok,
            "reason": "follow_pre_tap_live_observation_ready" if ok else "follow_pre_tap_cta_missing",
            "resource_id": self.resource_id,
            "text": "Follow",
            "semantic": "Follow",
            "clickable": True,
            "bounds": {"left": 30, "top": top, "right": 530, "bottom": top + 90},
            "package": self.package,
            "activity": self.activity,
            "profile_identity_ok": target_binding_ok,
            "target_binding_ok": target_binding_ok,
            "overlay_present": overlay_present,
            "music_sheet": {"proved": music},
            "bounds_source": "fresh_live_profile_header_follow_button",
            "observed_at_monotonic": time.monotonic(),
        }

    def _stabilize(self, device: object) -> dict:
        return navigation._stabilize_follow_cta_immediately_before_tap(
            device,
            expected_username="alice",
            expected_package=self.package,
            expected_activity=self.activity,
            initial_resource_id=self.resource_id,
            initial_text="Follow",
            initial_bounds={"left": 30, "top": 700, "right": 530, "bottom": 790},
        )

    @patch.object(navigation, "_follow_pre_tap_live_observation")
    def test_stable_path_uses_one_live_probe_and_only_fresh_bounds(self, observe) -> None:
        observe.return_value = self._observation(top=706)
        result = self._stabilize(object())
        self.assertTrue(result["ok"])
        self.assertEqual(result["probe_count"], 1)
        self.assertFalse(result["old_bounds_reused"])
        self.assertEqual(result["fresh_bounds"]["top"], 706)
        observe.assert_called_once()

    @patch.object(navigation, "_follow_pre_tap_live_observation")
    def test_major_shift_requires_one_bounded_stable_reselect(self, observe) -> None:
        observe.side_effect = [self._observation(top=1000), self._observation(top=1008)]
        result = self._stabilize(object())
        self.assertTrue(result["ok"])
        self.assertTrue(result["reselect_used"])
        self.assertEqual(result["probe_count"], 2)
        self.assertEqual(result["fresh_bounds"]["top"], 1008)
        self.assertFalse(result["old_bounds_reused"])

    @patch.object(navigation, "_follow_pre_tap_live_observation")
    def test_disappearing_cta_never_recovers_from_a_single_unbound_observation(self, observe) -> None:
        observe.side_effect = [
            self._observation(ok=False, target_binding_ok=False),
            self._observation(top=700),
        ]
        result = self._stabilize(object())
        self.assertFalse(result["ok"])
        self.assertEqual(result["probe_count"], 2)
        self.assertFalse(result["old_bounds_reused"])

    @patch.object(navigation, "_follow_pre_tap_live_observation")
    def test_unknown_modal_fails_closed_without_music_recovery(self, observe) -> None:
        observe.return_value = self._observation(overlay_present=True, music=False)
        result = self._stabilize(object())
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "unknown_modal_fail_closed")
        self.assertFalse(result["music_recovery_used"])
        observe.assert_called_once()

    @patch.object(navigation, "_follow_pre_tap_live_observation")
    def test_story_highlight_and_reel_audio_surfaces_never_gain_follow_authority(self, observe) -> None:
        story = {
            **self._observation(),
            "activity": "com.instagram.story.viewer.StoryViewerActivity",
        }
        reel_audio = {
            **self._observation(),
            "text": "Use audio",
            "semantic": "Use audio",
        }
        for unsafe in (story, reel_audio):
            with self.subTest(unsafe=unsafe):
                observe.side_effect = [unsafe, unsafe]
                result = self._stabilize(object())
                self.assertFalse(result["ok"])
                self.assertFalse(result["old_bounds_reused"])

    @patch.object(navigation, "_dismiss_proved_music_sheet_once")
    @patch.object(navigation, "_follow_pre_tap_live_observation")
    def test_music_sheet_gets_one_recovery_then_full_fresh_proof(self, observe, dismiss) -> None:
        sheet = self._observation(overlay_present=True, music=True)
        fresh = self._observation(top=720)
        final_fresh = self._observation(top=724)
        observe.side_effect = [sheet, final_fresh]
        dismiss.return_value = (True, fresh)
        result = self._stabilize(object())
        self.assertTrue(result["ok"])
        self.assertTrue(result["music_recovery_used"])
        self.assertFalse(result["old_bounds_reused"])
        self.assertEqual(result["fresh_bounds"]["top"], 724)
        self.assertEqual(result["probe_count"], 3)
        dismiss.assert_called_once()

    @patch.object(navigation, "_follow_pre_tap_live_observation")
    def test_fresh_proof_outside_immediate_action_window_is_rejected(self, observe) -> None:
        stale = self._observation()
        stale["observed_at_monotonic"] = time.monotonic() - 1.0
        observe.return_value = stale
        result = self._stabilize(object())
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "follow_pre_tap_fresh_proof_expired")
        self.assertFalse(result["old_bounds_reused"])

    def test_stabilization_and_music_recovery_add_no_fixed_sleep(self) -> None:
        source = inspect.getsource(navigation._stabilize_follow_cta_immediately_before_tap)
        source += inspect.getsource(navigation._dismiss_proved_music_sheet_once)
        self.assertNotIn("sleep(", source)

    @patch.object(navigation, "_follow_pre_tap_live_observation")
    def test_proved_music_recovery_uses_at_most_one_dismiss_action(self, observe) -> None:
        class RecoveryDevice:
            def __init__(self) -> None:
                self.clicks: list[tuple[int, int]] = []
                self.backs = 0

            def click(self, x: int, y: int) -> None:
                self.clicks.append((x, y))

            def press(self, key: str) -> None:
                self.backs += int(key == "back")

        device = RecoveryDevice()
        sheet_xml = '''<hierarchy>
          <node resource-id="com.instagram.android:id/music_bottom_sheet" bounds="[0,600][1080,2400]" clickable="false">
            <node text="Add to profile" bounds="[40,900][520,1010]" clickable="true" />
            <node text="Use audio" bounds="[560,900][1040,1010]" clickable="true" />
          </node>
        </hierarchy>'''
        current = {
            **self._observation(overlay_present=True, music=True),
            "hierarchy_xml": sheet_xml,
            "screen_width": 1080,
            "screen_height": 2400,
            "music_sheet": {
                "proved": True,
                "sheet_bounds": {"left": 0, "top": 600, "right": 1080, "bottom": 2400},
            },
        }
        observe.return_value = self._observation(top=720)
        dismissed, _fresh = navigation._dismiss_proved_music_sheet_once(
            device,
            current,
            expected_username="alice",
            expected_package=self.package,
        )
        self.assertTrue(dismissed)
        self.assertEqual(len(device.clicks) + device.backs, 1)
        observe.assert_called_once()

    @patch.object(navigation, "_follow_pre_tap_live_observation")
    def test_nine_historical_stale_authority_cases_are_caught_by_gate(self, observe) -> None:
        # Exact selected bounds and ready-to-tap gaps reconstructed from the
        # nine field failures across six natural runs.  A representative
        # layout move is injected at the previously unguarded boundary; every
        # case discards its recorded authority and consumes the stable reselect.
        cases = [
            (752, 842, 1696.67),
            (1122, 1212, 2057.07),
            (850, 940, 2040.35),
            (910, 1000, 2354.09),
            (592, 682, 1996.93),
            (699, 789, 2029.20),
            (706, 796, 2628.91),
            (645, 735, 2047.42),
            (592, 682, 2609.58),
        ]
        for top, bottom, vulnerable_gap_ms in cases:
            with self.subTest(top=top, vulnerable_gap_ms=vulnerable_gap_ms):
                first = self._observation(top=top + 300)
                second = self._observation(top=top + 304)
                observe.side_effect = [first, second]
                result = navigation._stabilize_follow_cta_immediately_before_tap(
                    object(),
                    expected_username="alice",
                    expected_package=self.package,
                    expected_activity=self.activity,
                    initial_resource_id=self.resource_id,
                    initial_text="Follow",
                    initial_bounds={
                        "left": 33,
                        "top": top,
                        "right": 532 if top == 706 else 479,
                        "bottom": bottom,
                    },
                )
                self.assertTrue(result["ok"])
                self.assertTrue(result["reselect_used"])
                self.assertFalse(result["old_bounds_reused"])
                self.assertEqual(result["fresh_bounds"], second["bounds"])


if __name__ == "__main__":
    unittest.main()
