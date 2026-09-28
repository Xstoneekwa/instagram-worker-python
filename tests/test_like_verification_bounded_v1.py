from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from PIL import Image

import instagram_navigation as nav


class _Device:
    def __init__(self, *, hierarchy: str = "<hierarchy/>") -> None:
        self.hierarchy = hierarchy
        self.screenshot_calls = 0
        self.dump_calls = 0

    def screenshot(self, *, format: str = "") -> Image.Image:
        self.screenshot_calls += 1
        assert format == "pillow"
        return Image.new("RGB", (1080, 2340), "white")

    def dump_hierarchy(self, compressed: bool = False) -> str:
        self.dump_calls += 1
        return self.hierarchy


def _binding() -> dict[str, object]:
    return {
        "account_id": "account",
        "run_id": "run",
        "request_id": "request",
        "action_id": "action",
        "attempt_id": 1,
        "business_session_id": "business",
        "control_id": "control",
        "worker_sha": "a" * 40,
    }


def _verify_context(**overrides: object) -> dict[str, object]:
    out: dict[str, object] = {
        "version": "PostTapLikeVerifyV1",
        **_binding(),
        "candidate_username": "candidate",
        "package": "com.instagram.android",
        "activity": "com.instagram.mainactivity.MainActivity",
        "like_bounds": {"left": 25, "top": 1800, "right": 115, "bottom": 1890},
        "canonical_generation": 7,
        "v5_positive": True,
        "story_or_highlight_detected": False,
        "tap_ack": True,
    }
    out.update(overrides)
    return out


class BoundedLikeVerificationTest(unittest.TestCase):
    def _run(
        self,
        device: _Device,
        *,
        semantic: list[tuple[bool, str, float, dict[str, object]]],
        roi_ratio: float = 0.0,
        hierarchy_positive: bool = False,
        identity: dict[str, object] | None = None,
        context: dict[str, object] | None = None,
        live_package: str = "com.instagram.android",
    ) -> tuple[dict[str, object], mock.Mock]:
        semantic_mock = mock.Mock(side_effect=semantic)
        with tempfile.TemporaryDirectory() as td, ExitStack() as stack:
            stack.enter_context(
                mock.patch.object(
                    nav,
                    "_followers_current_pkg_activity",
                    return_value={
                        "current_package": live_package,
                        "current_activity": "com.instagram.mainactivity.MainActivity",
                    },
                )
            )
            stack.enter_context(
                mock.patch.object(
                    nav,
                    "_ui_post_viewer_liked_state_for_verify",
                    semantic_mock,
                )
            )
            stack.enter_context(
                mock.patch.object(
                    nav,
                    "_visual_filled_heart_red_ratio",
                    return_value=(
                        roi_ratio,
                        {"left": 25, "top": 1800, "right": 115, "bottom": 1890},
                        "exact",
                    ),
                )
            )
            stack.enter_context(
                mock.patch.object(
                    nav,
                    "_post_open_hierarchy_identity_signals",
                    return_value=identity
                    or {
                        "posts_action_bar_in_snapshot": True,
                        "candidate_username_exact_in_snapshot": True,
                        "story_or_highlight_detected": False,
                    },
                )
            )
            stack.enter_context(
                mock.patch.object(
                    nav,
                    "_hierarchy_liked_state_for_post_verify",
                    return_value=(
                        hierarchy_positive,
                        "hierarchy_unlike_button" if hierarchy_positive else "",
                    ),
                )
            )
            stack.enter_context(
                mock.patch.object(nav, "_SCREENSHOTS_DIR", Path(td))
            )
            stack.enter_context(mock.patch.object(nav, "_ensure_debug_dirs"))
            stack.enter_context(
                mock.patch(
                    "follow_60s_canary.runtime_context",
                    return_value={"ui_generation": 7},
                )
            )
            out = nav.visual_verify_post_liked(
                device,
                source_profile_username="ct",
                expected_follower_username="candidate",
                expected_stage_binding=_binding(),
                post_tap_verification_context=context or _verify_context(),
                tap_x=70,
                tap_y=1845,
                pre_tap_like_button_bounds={
                    "left": 25, "top": 1800, "right": 115, "bottom": 1890
                },
            )
            saved = list(Path(td).glob("*.png"))
            out["_saved_count"] = len(saved)
        return out, semantic_mock

    def test_semantic_success_uses_no_screenshot_or_xml(self) -> None:
        device = _Device()
        out, semantic = self._run(
            device,
            semantic=[(True, "ui_description_exact_unlike", 0.99, {})],
        )
        self.assertTrue(out["liked_verified"])
        self.assertEqual(out["verification_method"], "ui_description_exact_unlike")
        self.assertEqual(device.screenshot_calls, 0)
        self.assertEqual(device.dump_calls, 0)
        self.assertEqual(out["_saved_count"], 0)
        self.assertEqual(semantic.call_count, 1)

    def test_roi_success_uses_one_in_memory_screenshot_and_no_xml(self) -> None:
        device = _Device()
        out, _ = self._run(
            device,
            semantic=[(False, "", 0.0, {})] * 4,
            roi_ratio=0.2,
        )
        self.assertTrue(out["liked_verified"])
        self.assertEqual(out["verification_method"], "visual_exact_like_roi_positive")
        self.assertEqual(device.screenshot_calls, 1)
        self.assertEqual(device.dump_calls, 0)
        self.assertEqual(out["_saved_count"], 0)

    def test_roi_ambiguous_then_one_xml_positive(self) -> None:
        device = _Device(hierarchy="<hierarchy unlike='true'/>")
        out, _ = self._run(
            device,
            semantic=[(False, "", 0.0, {})] * 4,
            roi_ratio=0.01,
            hierarchy_positive=True,
        )
        self.assertTrue(out["liked_verified"])
        self.assertTrue(out["xml_fallback_used"])
        self.assertEqual(device.screenshot_calls, 1)
        self.assertEqual(device.dump_calls, 1)
        self.assertEqual(out["_saved_count"], 0)

    def test_three_negative_stages_save_the_single_capture_only(self) -> None:
        device = _Device()
        out, _ = self._run(
            device,
            semantic=[(False, "", 0.0, {})] * 4,
            roi_ratio=0.0,
            hierarchy_positive=False,
        )
        self.assertFalse(out["liked_verified"])
        self.assertEqual(device.screenshot_calls, 1)
        self.assertEqual(device.dump_calls, 1)
        self.assertEqual(out["_saved_count"], 1)
        self.assertEqual(
            out["screenshot_saved_reason"],
            "like_verification_failed_or_ambiguous",
        )

    def test_package_change_fails_before_any_proof_acquisition(self) -> None:
        device = _Device()
        out, semantic = self._run(
            device,
            semantic=[(True, "ui_description_exact_unlike", 0.99, {})],
            live_package="other.package",
        )
        self.assertFalse(out["liked_verified"])
        self.assertEqual(out["semantic_result"], "like_verify_package_changed")
        self.assertEqual(semantic.call_count, 0)
        self.assertEqual(device.screenshot_calls, 0)
        self.assertEqual(device.dump_calls, 0)

    def test_candidate_mismatch_fails_closed(self) -> None:
        device = _Device()
        out, semantic = self._run(
            device,
            semantic=[(True, "ui_description_exact_unlike", 0.99, {})],
            context=_verify_context(candidate_username="other"),
        )
        self.assertFalse(out["liked_verified"])
        self.assertEqual(out["semantic_result"], "like_verify_candidate_mismatch")
        self.assertEqual(semantic.call_count, 0)

    def test_story_or_highlight_wins_over_positive_xml(self) -> None:
        device = _Device(hierarchy="<hierarchy story='true' unlike='true'/>")
        out, _ = self._run(
            device,
            semantic=[(False, "", 0.0, {})] * 4,
            roi_ratio=0.0,
            hierarchy_positive=True,
            identity={
                "posts_action_bar_in_snapshot": False,
                "candidate_username_exact_in_snapshot": True,
                "story_or_highlight_detected": True,
            },
        )
        self.assertFalse(out["liked_verified"])
        self.assertEqual(out["semantic_result"], "story_or_highlight_detected")

    def test_four_false_negative_fixtures_are_recovered_by_exact_roi(self) -> None:
        for ratio in (0.16, 0.18, 0.21, 0.24):
            with self.subTest(ratio=ratio):
                device = _Device()
                out, _ = self._run(
                    device,
                    semantic=[(False, "", 0.0, {})] * 4,
                    roi_ratio=ratio,
                )
                self.assertTrue(out["liked_verified"])
                self.assertEqual(device.screenshot_calls, 1)
                self.assertEqual(device.dump_calls, 0)

class _BudgetClock:
    def __init__(self, value=0.0):
        self.value = value
    def perf_counter(self):
        return self.value
    def sleep(self, delay):
        self.value += delay


class _BudgetDevice(_Device):
    def __init__(self, clock, *, positive=None, durations=None, info_delay=0, window_delay=0):
        super().__init__()
        self.clock = clock
        self.positive = positive
        self.durations = durations or [0.01] * 7
        self.info_delay = info_delay
        self.window_delay = window_delay
        self.calls = []
    def __call__(self, **selector):
        device = self
        index = len(self.calls)
        self.calls.append(selector)
        class Element:
            def exists(self, timeout):
                device.clock.value += device.durations[min(index, len(device.durations)-1)]
                return index == device.positive
            @property
            def info(self):
                device.clock.value += device.info_delay
                return {'contentDescription': 'Unlike', 'resourceId': 'com.instagram.android:id/row_feed_button_like',
                        'clickable': True, 'className': 'android.widget.ImageView'}
        return Element()
    def window_size(self):
        self.clock.value += self.window_delay
        return 1080, 2340


class SemanticProbeDeadlineTest(unittest.TestCase):
    def _probe(self, device, deadline=.72):
        with mock.patch.object(nav, 'time', device.clock):
            return nav._ui_post_viewer_liked_state_for_verify(device, semantic_deadline=deadline)

    def test_fast_positive_preserves_proof_and_io(self):
        a = _BudgetDevice(_BudgetClock(), positive=0)
        b = _BudgetDevice(_BudgetClock(), positive=0)
        self.assertEqual(self._probe(a), self._probe(b, None))
        self.assertEqual(a.calls, b.calls)
        self.assertEqual(len(a.calls), 1)
        self.assertEqual(a.screenshot_calls + a.dump_calls, 0)

    def test_positive_n_before_deadline(self):
        d = _BudgetDevice(_BudgetClock(), positive=3)
        self.assertTrue(self._probe(d)[0])
        self.assertEqual(len(d.calls), 4)

    def test_slow_negative_stops_following_probes(self):
        d = _BudgetDevice(_BudgetClock(), durations=[.8])
        self.assertFalse(self._probe(d)[0])
        self.assertEqual(len(d.calls), 1)

    def test_slow_positive_is_preserved(self):
        d = _BudgetDevice(_BudgetClock(), positive=0, durations=[.8])
        self.assertTrue(self._probe(d)[0])
        self.assertEqual(len(d.calls), 1)

    def test_positive_exists_slow_info_and_window_completes(self):
        d = _BudgetDevice(_BudgetClock(), positive=0, info_delay=.8, window_delay=.8)
        self.assertTrue(self._probe(d)[0])
        self.assertGreater(d.clock.value, .72)
        self.assertEqual(len(d.calls), 1)

    def test_equal_deadline_does_not_start_probe(self):
        d = _BudgetDevice(_BudgetClock(.72))
        self.assertFalse(self._probe(d)[0])
        self.assertEqual(d.calls, [])

    def test_expiration_between_strict_and_additional_probes(self):
        d = _BudgetDevice(_BudgetClock(), durations=[.18])
        self.assertFalse(self._probe(d)[0])
        self.assertEqual(len(d.calls), 4)

    def test_rid_probe_is_not_started_after_expiry(self):
        d = _BudgetDevice(_BudgetClock(), durations=[.12])
        self.assertFalse(self._probe(d)[0])
        self.assertEqual(len(d.calls), 6)

    def test_none_keeps_all_historical_probes(self):
        d = _BudgetDevice(_BudgetClock(), durations=[.8])
        self.assertFalse(self._probe(d, None)[0])
        self.assertEqual(len(d.calls), 7)

    def test_historical_gagiu_julie_372_timing_prefixes(self):
        for t, durations in [(.81855,[.41790,.77957,.36207,.21405,.31098,.20003,.07122]),
                             (.831335,[.55512,.18853,.20552,.20129,.20199,.19678,.05015]),
                             (.858487,[.36048,.17685,.18806,.21006,.21015,.22148,.18775])]:
            with self.subTest(t=t):
                old = _BudgetDevice(_BudgetClock(t), durations=durations)
                new = _BudgetDevice(_BudgetClock(t), durations=durations)
                self.assertFalse(self._probe(old, None)[0])
                self.assertFalse(self._probe(new)[0])
                self.assertEqual(len(old.calls), 7)
                self.assertEqual(len(new.calls), 0)
                self.assertLess(new.clock.value, 3.2)

    def test_max_probe_count_never_increases(self):
        for delay in (0,.01,.12,.18,.8,3.3):
            for positive in (None,0,3,6):
                old = _BudgetDevice(_BudgetClock(), durations=[delay], positive=positive)
                new = _BudgetDevice(_BudgetClock(), durations=[delay], positive=positive)
                self._probe(old, None); self._probe(new)
                self.assertLessEqual(len(new.calls), len(old.calls))

    def _verify(self, *, duration=.8, positive=None, roi=.0, xml=False, context=None, live_package=None):
        clock = _BudgetClock()
        d = _BudgetDevice(clock, positive=positive, durations=[duration])
        with tempfile.TemporaryDirectory() as td, ExitStack() as stack:
            stack.enter_context(mock.patch.object(nav, 'time', clock))
            stack.enter_context(mock.patch.object(nav, 'log'))
            stack.enter_context(mock.patch.object(nav, '_followers_current_pkg_activity', return_value={
                'current_package':live_package or 'com.instagram.android',
                'current_activity':'com.instagram.mainactivity.MainActivity'}))
            stack.enter_context(mock.patch.object(nav, '_visual_filled_heart_red_ratio', return_value=(roi, {}, 'exact')))
            stack.enter_context(mock.patch.object(nav, '_post_open_hierarchy_identity_signals', return_value={
                'posts_action_bar_in_snapshot':True,'candidate_username_exact_in_snapshot':True,'story_or_highlight_detected':False}))
            stack.enter_context(mock.patch.object(nav, '_hierarchy_liked_state_for_post_verify', return_value=(xml,'hierarchy_unlike_button' if xml else '')))
            stack.enter_context(mock.patch.object(nav, '_SCREENSHOTS_DIR', Path(td)))
            stack.enter_context(mock.patch.object(nav, '_ensure_debug_dirs'))
            stack.enter_context(mock.patch('follow_60s_canary.runtime_context',return_value={'ui_generation':7}))
            out=nav.visual_verify_post_liked(d,expected_follower_username='candidate',expected_stage_binding=_binding(),
                post_tap_verification_context=context or _verify_context(),pre_tap_like_button_bounds=_verify_context()['like_bounds'])
        return out,d

    def test_real_helper_slow_negative_roi_positive(self):
        out,d=self._verify(roi=.2)
        self.assertTrue(out['liked_verified']); self.assertEqual(len(d.calls),1)
        self.assertEqual((d.screenshot_calls,d.dump_calls),(1,0))

    def test_real_helper_slow_negative_roi_negative_xml_positive(self):
        out,d=self._verify(xml=True)
        self.assertTrue(out['liked_verified']); self.assertEqual((d.screenshot_calls,d.dump_calls),(1,1))

    def test_real_helper_all_negative(self):
        out,d=self._verify()
        self.assertFalse(out['liked_verified']); self.assertEqual((d.screenshot_calls,d.dump_calls),(1,1))

    def test_single_rpc_over_global_no_fallback(self):
        out,d=self._verify(duration=3.3)
        self.assertFalse(out['liked_verified']); self.assertEqual((len(d.calls),d.screenshot_calls,d.dump_calls),(1,0,0))

    def test_single_rpc_positive_over_global_preserved(self):
        out,d=self._verify(duration=3.3,positive=0)
        self.assertTrue(out['liked_verified']); self.assertEqual((len(d.calls),d.screenshot_calls,d.dump_calls),(1,0,0))

    def test_context_rejection_matrix_no_probes(self):
        for field,value in [('candidate_username','wrong'),('package','wrong'),('activity','wrong'),('canonical_generation',8),
                            ('account_id','wrong'),('run_id','wrong'),('request_id','wrong'),('tap_ack',False)]:
            with self.subTest(field=field):
                out,d=self._verify(context=_verify_context(**{field:value}))
                self.assertFalse(out['liked_verified']); self.assertEqual(d.calls,[])
                self.assertEqual((d.screenshot_calls,d.dump_calls),(0,0))

    def test_invalid_like_semantics_never_become_positive(self):
        for desc in ('Like','Liked by alice','12 likes',''):
            self.assertFalse(nav._verify_post_tap_text_is_unlike_semantic(desc))
        for desc in ('Unlike',"Je n'aime plus",'Gefällt mir nicht mehr'):
            self.assertTrue(nav._verify_post_tap_text_is_unlike_semantic(desc))


if __name__ == "__main__":
    unittest.main()
