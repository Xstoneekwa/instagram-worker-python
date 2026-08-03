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


if __name__ == "__main__":
    unittest.main()
