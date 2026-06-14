from __future__ import annotations

import contextlib
import time
import unittest
from contextlib import ExitStack
from unittest import mock

import instagram_navigation as nav

_SURFACE_PRECHECK_OK: dict[str, object] = {
    "skip_like": False,
    "precheck_ms": 1.0,
    "profile_candidate_visible": True,
    "grid_tab_visible": True,
    "followers_list_visible": False,
}


def _like_phase_contract_ctx() -> mock.MagicMock:
    contract_ctx = mock.MagicMock()
    contract_ctx.current_state.value = "sheet_dismissed"
    return contract_ctx


def _patch_post_viewer_like_surface_gates_normal(stack: ExitStack) -> None:
    """Post-open viewer gates call u2 selectors; MagicMock makes exists() truthy by default."""
    stack.enter_context(
        mock.patch.object(
            nav,
            "_ui_post_viewer_facebook_shared_content_detected",
            return_value=(False, ""),
        )
    )
    stack.enter_context(
        mock.patch.object(
            nav,
            "_ui_post_viewer_like_action_bar_exploitable",
            return_value=(True, "like_unlike_ui"),
        )
    )


@contextlib.contextmanager
def _normal_post_viewer_surface_gates():
    with mock.patch.object(
        nav,
        "_ui_post_viewer_facebook_shared_content_detected",
        return_value=(False, ""),
    ), mock.patch.object(
        nav,
        "_ui_post_viewer_like_action_bar_exploitable",
        return_value=(True, "like_unlike_ui"),
    ):
        yield


def _patch_like_phase_common(
    stack: ExitStack,
    *,
    contract_ctx: mock.MagicMock,
    fast_path_enabled: bool = False,
    positive_direct_enabled: bool = False,
) -> None:
    stack.enter_context(
        mock.patch.object(nav.config, "POST_FOLLOW_POST_LIKES_ENABLED", True, create=True)
    )
    stack.enter_context(
        mock.patch.object(
            nav.config,
            "POST_FOLLOW_LIKE_POSTS_COUNT_FAST_PATH_ENABLED",
            fast_path_enabled,
            create=True,
        )
    )
    stack.enter_context(
        mock.patch.object(
            nav.config,
            "POST_FOLLOW_LIKE_POSTS_COUNT_POSITIVE_DIRECT_OPEN_ENABLED",
            positive_direct_enabled,
            create=True,
        )
    )
    stack.enter_context(
        mock.patch.object(nav.config, "ENABLE_REAL_VISUAL_POST_LIKE", True, create=True)
    )
    stack.enter_context(
        mock.patch.object(nav.config, "POST_FOLLOW_POST_LIKES_PERCENTAGE", 100, create=True)
    )
    stack.enter_context(
        mock.patch.object(nav.config, "POST_FOLLOW_POST_LIKES_COUNT_RANGE", "1-1", create=True)
    )
    stack.enter_context(
        mock.patch.object(nav.config, "POST_FOLLOW_TOTAL_LIKES_LIMIT", 150, create=True)
    )
    stack.enter_context(
        mock.patch.object(
            nav, "read_current_profile_username_for_follow_gate", return_value="cand"
        )
    )
    stack.enter_context(
        mock.patch(
            "navigation_engine.observe_instagram_state",
            return_value={"state": "CANDIDATE_PROFILE", "confidence": 0.9},
        )
    )
    stack.enter_context(
        mock.patch.object(
            nav,
            "_post_follow_like_precheck_mute_sheet",
            return_value={"skip_like": False, "precheck_ms": 1.0},
        )
    )
    stack.enter_context(
        mock.patch.object(
            nav, "_post_follow_like_precheck_surface", return_value=dict(_SURFACE_PRECHECK_OK)
        )
    )
    stack.enter_context(
        mock.patch(
            "follow_state_contract.evaluate_like_precheck_contract",
            return_value=(contract_ctx, True, ""),
        )
    )
    _patch_post_viewer_like_surface_gates_normal(stack)
    tmock = stack.enter_context(mock.patch.object(nav, "time"))
    tmock.perf_counter = time.perf_counter
    tmock.time = time.time
    tmock.sleep = lambda *_a, **_k: None


def _patch_pre_reveal_like_phase_common(
    stack: ExitStack,
    *,
    contract_ctx: mock.MagicMock,
) -> None:
    _patch_like_phase_common(stack, contract_ctx=contract_ctx)
    stack.enter_context(
        mock.patch.object(
            nav,
            "_visual_profile_no_posts_tier1_direct_check",
            return_value={"no_posts_detected": False, "detection_method": "none", "confidence": 0.0},
        )
    )


class _TabsNode:
    def __init__(self, bounds: dict[str, int]) -> None:
        self.info = {"bounds": dict(bounds)}


class _TabsSelector:
    def __init__(self, nodes: list[_TabsNode]) -> None:
        self._nodes = list(nodes)

    def exists(self, timeout: float = 0.0) -> bool:
        return bool(self._nodes)

    def all(self) -> list[_TabsNode]:
        return list(self._nodes)


def _tabs_bounds(top: int, bottom: int) -> dict[str, int]:
    return {"left": 0, "top": int(top), "right": 1080, "bottom": int(bottom)}


class FakeWaitSelector:
    def __init__(self, present: bool) -> None:
        self.present = present

    def wait(self, timeout: float = 0.0) -> bool:
        return self.present


class FakeCloneHeaderDevice:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def __call__(self, **kwargs: object) -> FakeWaitSelector:
        self.calls.append(dict(kwargs))
        rid_match = str(kwargs.get("resourceIdMatches") or "")
        return FakeWaitSelector(bool(rid_match and "profile_header" in rid_match))


class PreFollowTapInstrumentationTest(unittest.TestCase):
    def test_followable_candidate_emits_tap_ready_before_tap_sent(self) -> None:
        device = mock.MagicMock()
        button = mock.MagicMock()
        logs: list[tuple[str, dict[str, object]]] = []

        with mock.patch.object(
            nav, "_follow_ui_state_snapshot", side_effect=["follow", "following"]
        ), mock.patch(
            "follow_action_engine.follow_action_surface_wait_and_select_element",
            return_value=(
                button,
                {
                    "events": [
                        (
                            "follow_action_timing_surface_selection_completed",
                            {
                                "result": "ready",
                                "reason": "unit_follow_button",
                                "fallback_used": True,
                            },
                        )
                    ],
                    "exact_follow_fast_path": False,
                    "last_ui_state": "follow",
                },
            ),
        ), mock.patch.object(
            nav,
            "visual_detect_private_profile",
            return_value={
                "private_profile_detected": False,
                "detection_method": "none",
                "confidence": 0.0,
                "probe_ms": 1.0,
                "hierarchy_fallback_used": False,
            },
        ), mock.patch.object(
            nav, "_try_review_before_follow_popup_confirm", return_value=False
        ), mock.patch.object(
            nav, "_review_before_follow_popup_visible", return_value=False
        ), mock.patch(
            "instagram_navigation.time.sleep"
        ), mock.patch.object(
            nav, "log", side_effect=lambda _level, event, **kw: logs.append((str(event), kw))
        ):
            out = nav.perform_follow_safe(
                device,
                "public_user",
                "com.instagram.android",
                profile_already_open=True,
                source_profile_username="source_ct",
                visual_candidate_id="vc-1",
                dont_follow_private_accounts=True,
            )

        events = [event for event, _kw in logs]
        self.assertTrue(out["tapped"])
        self.assertLess(events.index("pre_follow_tap_ready"), events.index("pre_follow_tap_sent"))
        ready = [kw for event, kw in logs if event == "pre_follow_tap_ready"][-1]
        sent = [kw for event, kw in logs if event == "pre_follow_tap_sent"][-1]
        self.assertEqual(ready["target_username"], "source_ct")
        self.assertEqual(ready["candidate_username"], "public_user")
        self.assertTrue(ready["safe_to_tap"])
        self.assertEqual(sent["reason"], "follow_tap_sent")


class PostMuteGapTrackingTest(unittest.TestCase):
    def tearDown(self) -> None:
        nav._clear_post_mute_sheet_closed_proof_stash()

    def _stash_sheet_closed_proof(
        self,
        *,
        source: str = "ct",
        candidate: str = "cand",
        vcid: str = "vc-1",
        age_s: float = 0.0,
        action_bar: str | None = None,
    ) -> None:
        nav._post_mute_sheet_closed_proof_stash = {
            "stashed_at_monotonic": time.perf_counter() - float(age_s),
            "source_profile_username": source,
            "candidate_username": candidate,
            "visual_candidate_id": vcid,
            "action_bar_title": action_bar if action_bar is not None else candidate,
            "sheet_closed": True,
            "duration_ms": 12.0,
        }

    def test_post_mute_checkpoint_fast_profile_proof_skips_heavy_revalidation(self) -> None:
        device = mock.MagicMock()
        logs: list[tuple[str, str, dict[str, object]]] = []
        with mock.patch.object(
            nav, "read_current_profile_username_for_follow_gate", return_value="cand"
        ) as mock_read, mock.patch.object(
            nav,
            "_post_follow_like_precheck_mute_sheet",
            return_value={"skip_like": False, "sheet_visible": False, "precheck_ms": 1.0},
        ), mock.patch.object(
            nav,
            "detect_followers_list_screen",
            side_effect=AssertionError("heavy followers detect should be skipped"),
        ), mock.patch(
            "navigation_engine.observe_instagram_state",
            side_effect=AssertionError("heavy navigation observe should be skipped"),
        ), mock.patch.object(
            nav, "log", side_effect=lambda level, event, **kw: logs.append((level, event, kw))
        ):
            out = nav._post_mute_state_checkpoint(
                device,
                pkg="com.instagram.android",
                source_profile_username="source",
                visual_candidate_id="vc-1",
                candidate_username="cand",
                sheet_dismiss_ok=True,
                allow_fast_profile_proof=True,
            )

        events = [event for _level, event, _kw in logs]
        self.assertTrue(out["ok"])
        self.assertTrue(out["fast_profile_proof"])
        self.assertEqual(mock_read.call_count, 1)
        self.assertIn("post_mute_gap_started", events)
        self.assertIn("post_mute_profile_surface_confirm_started", events)
        self.assertIn("post_mute_profile_surface_confirm_completed", events)
        self.assertIn("post_mute_gap_completed", events)
        completed = [kw for _level, event, kw in logs if event == "post_mute_gap_completed"][-1]
        self.assertTrue(completed["safe_to_continue_ui"])
        self.assertTrue(completed["used_cached_context"])

    def test_post_mute_checkpoint_ambiguous_profile_falls_back_to_heavy_revalidation(self) -> None:
        device = mock.MagicMock()
        logs: list[tuple[str, str, dict[str, object]]] = []
        with mock.patch.object(
            nav, "read_current_profile_username_for_follow_gate", return_value="other"
        ), mock.patch.object(
            nav,
            "_post_follow_like_precheck_mute_sheet",
            return_value={"skip_like": False, "sheet_visible": False, "precheck_ms": 1.0},
        ), mock.patch.object(
            nav,
            "_post_follow_overlay_ui_hints",
            return_value={"likely_mute_toggle_sheet": False},
        ), mock.patch.object(
            nav,
            "detect_followers_list_screen",
            return_value={"is_followers_list": False},
        ) as mock_detect, mock.patch(
            "navigation_engine.observe_instagram_state",
            return_value={"state": "CANDIDATE_PROFILE", "confidence": 0.9, "reason": "unit"},
        ) as mock_observe, mock.patch.object(
            nav, "log", side_effect=lambda level, event, **kw: logs.append((level, event, kw))
        ):
            out = nav._post_mute_state_checkpoint(
                device,
                pkg="com.instagram.android",
                source_profile_username="source",
                visual_candidate_id="vc-1",
                candidate_username="cand",
                sheet_dismiss_ok=True,
                allow_fast_profile_proof=True,
            )

        events = [event for _level, event, _kw in logs]
        self.assertTrue(out["ok"])
        self.assertFalse(out.get("fast_profile_proof", False))
        self.assertEqual(mock_detect.call_count, 1)
        self.assertEqual(mock_observe.call_count, 1)
        self.assertIn("post_mute_gap_checkpoint", events)
        self.assertIn("post_mute_profile_surface_confirm_completed", events)

    def test_post_mute_checkpoint_profile_mismatch_without_candidate_profile_is_not_fast_ok(
        self,
    ) -> None:
        device = mock.MagicMock()
        logs: list[tuple[str, str, dict[str, object]]] = []
        with mock.patch.object(
            nav, "read_current_profile_username_for_follow_gate", return_value="other"
        ), mock.patch.object(
            nav,
            "_post_follow_like_precheck_mute_sheet",
            return_value={"skip_like": False, "sheet_visible": False, "precheck_ms": 1.0},
        ), mock.patch.object(
            nav,
            "_post_follow_overlay_ui_hints",
            return_value={"likely_mute_toggle_sheet": False},
        ), mock.patch.object(
            nav,
            "detect_followers_list_screen",
            return_value={"is_followers_list": True},
        ), mock.patch(
            "navigation_engine.observe_instagram_state",
            return_value={"state": "FOLLOWERS_LIST", "confidence": 0.9, "reason": "unit"},
        ), mock.patch.object(
            nav, "log", side_effect=lambda level, event, **kw: logs.append((level, event, kw))
        ):
            out = nav._post_mute_state_checkpoint(
                device,
                pkg="com.instagram.android",
                source_profile_username="source",
                visual_candidate_id="vc-1",
                candidate_username="cand",
                sheet_dismiss_ok=True,
                allow_fast_profile_proof=True,
            )

        self.assertFalse(out.get("fast_profile_proof", False))
        fast_done = [
            kw
            for _level, event, kw in logs
            if event == "post_mute_profile_surface_confirm_completed"
            and kw.get("phase") == "post_mute_fast_profile_proof"
        ][-1]
        self.assertFalse(fast_done["safe_to_continue_ui"])
        self.assertEqual(fast_done["reason"], "candidate_profile_not_confirmed")

    def test_like_sheet_precheck_reuses_fresh_sheet_closed_proof(self) -> None:
        self._stash_sheet_closed_proof()
        device = mock.MagicMock()
        logs: list[str] = []
        with mock.patch.object(
            nav, "_quick_mute_sheet_visible_guard", return_value=False
        ), mock.patch.object(
            nav,
            "_post_follow_overlay_ui_hints",
            side_effect=AssertionError("full overlay probe should be skipped"),
        ), mock.patch.object(
            nav, "log", side_effect=lambda _level, event, **_kw: logs.append(str(event))
        ):
            out = nav._post_follow_like_precheck_mute_sheet(
                device,
                visual_candidate_id="vc-1",
                source_profile_username="ct",
                follower_username="cand",
            )

        self.assertFalse(out["skip_like"])
        self.assertFalse(out["sheet_visible"])
        self.assertTrue(out["proof_reused"])
        self.assertIn("post_follow_like_sheet_closed_proof_reused", logs)

    def test_like_sheet_precheck_stale_proof_falls_back_to_full_probe(self) -> None:
        self._stash_sheet_closed_proof(age_s=30.0)
        device = mock.MagicMock()
        with mock.patch.object(
            nav, "_post_follow_overlay_ui_hints", return_value={"likely_mute_toggle_sheet": False}
        ) as overlay:
            out = nav._post_follow_like_precheck_mute_sheet(
                device,
                visual_candidate_id="vc-1",
                source_profile_username="ct",
                follower_username="cand",
            )

        self.assertFalse(out["skip_like"])
        overlay.assert_called_once()

    def test_like_sheet_precheck_candidate_mismatch_falls_back_to_full_probe(self) -> None:
        self._stash_sheet_closed_proof(candidate="other")
        device = mock.MagicMock()
        with mock.patch.object(
            nav, "_post_follow_overlay_ui_hints", return_value={"likely_mute_toggle_sheet": False}
        ) as overlay:
            out = nav._post_follow_like_precheck_mute_sheet(
                device,
                visual_candidate_id="vc-1",
                source_profile_username="ct",
                follower_username="cand",
            )

        self.assertFalse(out["skip_like"])
        overlay.assert_called_once()

    def test_like_sheet_precheck_quick_guard_visible_rejects_proof_and_blocks_like(
        self,
    ) -> None:
        self._stash_sheet_closed_proof()
        device = mock.MagicMock()
        logs: list[tuple[str, dict[str, object]]] = []
        with mock.patch.object(
            nav, "_quick_mute_sheet_visible_guard", return_value=True
        ), mock.patch.object(
            nav, "_post_follow_overlay_ui_hints", return_value={"likely_mute_toggle_sheet": True}
        ), mock.patch.object(
            nav, "_mute_engine_v2_dismiss_mute_sheet", return_value=(False, 1.0)
        ), mock.patch.object(
            nav, "_mute_engine_v2_mute_sheet_still_visible", return_value=True
        ), mock.patch.object(
            nav, "log", side_effect=lambda _level, event, **kw: logs.append((str(event), kw))
        ):
            out = nav._post_follow_like_precheck_mute_sheet(
                device,
                visual_candidate_id="vc-1",
                source_profile_username="ct",
                follower_username="cand",
            )

        self.assertTrue(out["skip_like"])
        self.assertEqual(out["skip_reason"], "mute_sheet_still_open")
        rejected = [
            kw for event, kw in logs if event == "post_follow_like_sheet_closed_proof_rejected"
        ]
        self.assertTrue(rejected)
        self.assertEqual(rejected[-1]["reject_reason"], "quick_guard_sheet_visible")

    def test_profile_guard_fast_proof_reused_skips_heavy_observe(self) -> None:
        self._stash_sheet_closed_proof()
        device = mock.MagicMock()
        with mock.patch.object(
            nav.config, "POST_FOLLOW_POST_LIKES_ENABLED", True, create=True
        ), mock.patch.object(
            nav.config, "ENABLE_REAL_VISUAL_POST_LIKE", True, create=True
        ), mock.patch.object(
            nav.config, "POST_FOLLOW_POST_LIKES_PERCENTAGE", 100, create=True
        ), mock.patch.object(
            nav.config, "POST_FOLLOW_POST_LIKES_COUNT_RANGE", "1-1", create=True
        ), mock.patch.object(
            nav, "_followers_current_pkg_activity",
            return_value={"current_package": "com.instagram.android"},
        ), mock.patch.object(
            nav, "read_current_profile_username_for_follow_gate", return_value="cand"
        ), mock.patch.object(
            nav, "is_followers_list_surface_quick", return_value=False
        ), mock.patch(
            "navigation_engine.observe_instagram_state",
            side_effect=AssertionError("heavy observe should be skipped"),
        ), mock.patch.object(
            nav, "_post_follow_like_precheck_mute_sheet",
            return_value={"skip_like": True, "skip_reason": "unit_stop"},
        ), mock.patch.object(nav, "log"):
            out = nav.run_post_follow_post_likes_phase(
                device,
                pkg="com.instagram.android",
                source_profile_username="ct",
                follower_username="cand",
                visual_candidate_id="vc-1",
                follow_success_verified=True,
                follow_state_after="following",
                skipped_tap=False,
            )

        self.assertEqual(out["skipped_reason"], "unit_stop")

    def test_profile_guard_mismatch_falls_back_to_heavy_and_fails_safe(self) -> None:
        self._stash_sheet_closed_proof()
        device = mock.MagicMock()
        with mock.patch.object(
            nav.config, "POST_FOLLOW_POST_LIKES_ENABLED", True, create=True
        ), mock.patch.object(
            nav.config, "ENABLE_REAL_VISUAL_POST_LIKE", True, create=True
        ), mock.patch.object(
            nav.config, "POST_FOLLOW_POST_LIKES_PERCENTAGE", 100, create=True
        ), mock.patch.object(
            nav.config, "POST_FOLLOW_POST_LIKES_COUNT_RANGE", "1-1", create=True
        ), mock.patch.object(
            nav, "_followers_current_pkg_activity",
            return_value={"current_package": "com.instagram.android"},
        ), mock.patch.object(
            nav, "read_current_profile_username_for_follow_gate", return_value="other"
        ), mock.patch(
            "navigation_engine.observe_instagram_state",
            return_value={"state": "CANDIDATE_PROFILE", "confidence": 0.9},
        ) as observe, mock.patch.object(nav, "log"):
            out = nav.run_post_follow_post_likes_phase(
                device,
                pkg="com.instagram.android",
                source_profile_username="ct",
                follower_username="cand",
                visual_candidate_id="vc-1",
                follow_success_verified=True,
                follow_state_after="following",
                skipped_tap=False,
            )

        observe.assert_called_once()
        self.assertEqual(out["skipped_reason"], "profile_mismatch_before_likes")

    def test_surface_precheck_skips_mute_sheet_probe_with_fresh_proof(self) -> None:
        self._stash_sheet_closed_proof()
        device = mock.MagicMock()
        logs: list[str] = []
        with mock.patch.object(
            nav, "is_followers_list_surface_quick", return_value=False
        ), mock.patch.object(
            nav, "read_current_profile_username_for_follow_gate", return_value="cand"
        ), mock.patch.object(
            nav, "_followers_profile_tabs_visible", return_value=True
        ), mock.patch.object(
            nav,
            "_post_follow_overlay_ui_hints",
            side_effect=AssertionError("surface precheck should not probe mute sheet"),
        ), mock.patch.object(
            nav, "log", side_effect=lambda _level, event, **_kw: logs.append(str(event))
        ):
            out = nav._post_follow_like_precheck_surface(
                device,
                source_profile_username="ct",
                follower_username="cand",
                visual_candidate_id="vc-1",
            )

        self.assertFalse(out["skip_like"])
        self.assertTrue(out["grid_tab_visible"])
        self.assertIn("post_follow_like_surface_precheck_sheet_probe_skipped", logs)

    def test_like_phase_still_runs_no_posts_and_like_protections(self) -> None:
        self._stash_sheet_closed_proof()
        device = mock.MagicMock()
        device.window_size.return_value = (1080, 2340)
        logs: list[str] = []
        with mock.patch.object(
            nav.config, "POST_FOLLOW_POST_LIKES_ENABLED", True, create=True
        ), mock.patch.object(
            nav.config, "ENABLE_REAL_VISUAL_POST_LIKE", True, create=True
        ), mock.patch.object(
            nav.config, "POST_FOLLOW_POST_LIKES_PERCENTAGE", 100, create=True
        ), mock.patch.object(
            nav.config, "POST_FOLLOW_POST_LIKES_COUNT_RANGE", "1-1", create=True
        ), mock.patch.object(
            nav.config, "POST_FOLLOW_TOTAL_LIKES_LIMIT", 150, create=True
        ), _normal_post_viewer_surface_gates(), mock.patch.object(
            nav, "_followers_current_pkg_activity",
            return_value={"current_package": "com.instagram.android"},
        ), mock.patch.object(
            nav, "read_current_profile_username_for_follow_gate", return_value="cand"
        ), mock.patch.object(
            nav, "is_followers_list_surface_quick", return_value=False
        ), mock.patch.object(
            nav, "_quick_mute_sheet_visible_guard", return_value=False
        ), mock.patch.object(
            nav, "_followers_profile_tabs_visible", return_value=True
        ), mock.patch.object(
            nav,
            "_visual_profile_no_posts_tier1_direct_check",
            return_value={
                "no_posts_detected": False,
                "detection_method": "none",
                "confidence": 0.0,
            },
        ) as no_posts, mock.patch.object(
            nav, "_followers_profile_tabs_bottom_y_px", return_value=(900, "unit")
        ), mock.patch.object(
            nav,
            "_post_follow_likes_open_top_left_legacy_visual_safe",
            return_value={
                "ok": True,
                "post_detected": True,
                "tap_x": 180,
                "tap_y": 1280,
                "open_strategy": "vision_open_top_left_legacy_safe",
                "viewer_detect_path": "phase_a2_exact_like_desc_fast",
                "detect_reason": "like_unlike_ui",
                "likes_perf_post_open": {},
            },
        ) as legacy_safe, mock.patch.object(
            nav,
            "visual_post_already_liked",
            return_value={"already_liked": False, "detection_method": "hierarchy_like_hint"},
        ) as already_liked, mock.patch.object(
            nav,
            "visual_like_open_post",
            return_value={
                "ok": True,
                "real_tap_sent": True,
                "tap_x": 50,
                "tap_y": 1000,
                "confidence": 0.8,
                "likes_perf_like": {},
            },
        ) as like_open, mock.patch.object(
            nav,
            "visual_verify_post_liked",
            return_value={
                "liked_verified": True,
                "verification_method": "unit",
                "verify_attempts_count": 1,
            },
        ) as verify, mock.patch.object(
            nav, "visual_return_to_profile_from_post", return_value={"ok": True}
        ), mock.patch.object(
            nav, "log", side_effect=lambda _level, event, **_kw: logs.append(str(event))
        ):
            out = nav.run_post_follow_post_likes_phase(
                device,
                pkg="com.instagram.android",
                source_profile_username="ct",
                follower_username="cand",
                visual_candidate_id="vc-1",
                follow_success_verified=True,
                follow_state_after="following",
                skipped_tap=False,
            )

        self.assertEqual(out["phase_outcome"], "success")
        no_posts.assert_called_once()
        legacy_safe.assert_called_once()
        already_liked.assert_called_once()
        like_open.assert_called_once()
        verify.assert_called_once()
        self.assertIn("post_follow_like_pre_reveal_guard_started", logs)
        self.assertIn("post_follow_like_pre_reveal_guard_completed", logs)
        self.assertIn("post_like_surface_to_scroll_gap_started", logs)
        self.assertIn("post_like_no_posts_tier1_timing", logs)
        self.assertIn("post_like_pre_reveal_timing", logs)
        self.assertIn("post_like_legacy_safe_candidate_timing", logs)
        self.assertIn("post_like_surface_to_scroll_gap_completed", logs)


def _probe_sequence_from_visible_fn(
    visible_fn: object,
) -> object:
    """Bridge legacy visible_cell mocks to post-scroll XML probe sequence."""

    def _run(
        _d: object,
        *,
        ui_hints: dict[str, object],
        budget_deadline: float | None,
        ww: int,
        wh: int,
        after_reveal_scroll: bool = False,
        **_kwargs: object,
    ) -> tuple[dict[str, object], dict[str, object]]:
        if not callable(visible_fn):
            raise TypeError("visible_fn must be callable")
        meta = visible_fn(
            _d, ui_hints=ui_hints, budget_deadline=budget_deadline
        )
        state = nav._post_follow_likes_assess_reveal_cell_state(
            meta, ww=int(ww), wh=int(wh)
        )
        if after_reveal_scroll and not bool(state.get("top_left_post_tap_safe")):
            src = str(meta.get("reason") or "")
            if src in {
                "",
                "top_left_xml_thumbnail_not_found",
                "top_left_post_not_detected_after_xml_and_vision_probe",
                "profile_tabs_grid_cell_estimate",
                "no_xml_thumbnail_below_tabs",
            }:
                state = dict(state)
                state["failure_reason"] = (
                    "top_left_post_not_detected_after_xml_and_vision_probe"
                )
                state["tap_safe_reason"] = (
                    "top_left_post_not_detected_after_xml_and_vision_probe"
                )
        return meta, state

    return _run


class PostFollowLikeSamsungFastTest(unittest.TestCase):
    def test_early_profile_transition_uses_clone_compatible_header_before_username_band(self) -> None:
        d = FakeCloneHeaderDevice()
        with mock.patch.object(
            nav,
            "_profile_signal_b_username_top_band",
            side_effect=AssertionError("username_top_band should not be checked before header ids"),
        ):
            signal = nav._early_profile_transition_signal(d, "source.profile")

        self.assertEqual(signal, "header_resource_id")
        self.assertTrue(
            any("profile_header" in str(call.get("resourceIdMatches") or "") for call in d.calls)
        )

    def test_post_follow_surface_truth_confirms_profile_over_followers_list(self) -> None:
        device = mock.MagicMock()
        logs: list[tuple[str, str, dict[str, object]]] = []
        with mock.patch.object(
            nav, "read_current_profile_username_for_follow_gate", return_value="cand"
        ), mock.patch.object(
            nav,
            "visual_profile_already_following_before_follow",
            return_value={"already_following": True, "detection_method": "ui_text_following_exact"},
        ), mock.patch.object(
            nav, "log", side_effect=lambda level, event, **kw: logs.append((level, event, kw))
        ):
            out = nav._post_follow_resolve_surface_truth(
                device,
                candidate_username="cand",
                source_profile_username="ct",
                follow_state_after="following",
                det={"action_bar_title": "cand", "is_followers_list": True},
                nav_obs={"state": "FOLLOWERS_LIST"},
                fp={"screen_class": "followers_list"},
            )

        self.assertEqual(out["decision"], "candidate_profile_confirmed")
        self.assertTrue(out["evidence_profile"])
        self.assertTrue(out["evidence_followers_list"])
        self.assertIn("post_follow_surface_conflict", [event for _level, event, _kw in logs])
        self.assertIn(
            "post_follow_candidate_profile_confirmed",
            [event for _level, event, _kw in logs],
        )

    def test_post_follow_recover_reopens_visible_row(self) -> None:
        device = mock.MagicMock()
        row = {"username": "cand", "row_center": [100, 200]}
        with mock.patch.object(
            nav.config, "POST_FOLLOW_RECOVER_CANDIDATE_PROFILE_FROM_LIST", True, create=True
        ), mock.patch.object(
            nav, "find_visible_followers_row_by_username", return_value=row
        ), mock.patch.object(
            nav, "tap_followers_list_username_row", return_value=(True, 100, 200)
        ), mock.patch.object(
            nav, "read_current_profile_username_for_follow_gate", return_value="cand"
        ), mock.patch.object(
            nav, "_follow_ui_state_snapshot", return_value="following"
        ), mock.patch.object(nav, "time") as tmock:
            tmock.sleep = lambda *_a, **_k: None
            tmock.perf_counter = time.perf_counter
            tmock.monotonic = time.monotonic
            out = nav._post_follow_recover_candidate_profile_from_list(
                device,
                candidate_username="cand",
                source_profile_username="ct",
            )
        self.assertTrue(out["recovered"])
        self.assertEqual(out["reason"], "profile_reopened_with_following_cta")

    def test_suggested_overlay_pre_probe_fast_skip_without_screenshot(self) -> None:
        device = mock.MagicMock()
        device.window_size.return_value = (1080, 2340)
        with mock.patch.object(
            nav.config, "POST_FOLLOW_POST_LIKES_ENABLED", True, create=True
        ), mock.patch.object(
            nav, "_post_follow_likes_grid_ui_surface_hints",
            return_value={
                "suggested_for_you": True,
                "discover_people": False,
                "profile_tabs_visible": True,
            },
        ), mock.patch.object(
            nav,
            "_post_follow_likes_stabilize_grid_under_suggested_overlay",
            return_value={
                "ui_hints": {
                    "suggested_for_you": True,
                    "discover_people": False,
                    "profile_tabs_visible": True,
                },
                "cell_meta": {"reliable": False, "reason": "no_xml_thumbnail_below_tabs"},
                "scroll_attempts": 2,
                "tap_safe": False,
                "tap_safe_reason": "post_grid_not_exposed_after_scroll",
                "grid_exposure": "unstable",
            },
        ), mock.patch.object(nav, "screenshot") as shot, mock.patch.object(
            nav, "time"
        ) as tmock:
            tmock.perf_counter = time.perf_counter
            tmock.monotonic = time.monotonic
            tmock.time = time.time
            tmock.sleep = lambda *_a, **_k: None
            out = nav.ensure_post_grid_visible_for_post_follow_likes(
                device,
                source_profile_username="ct",
                follower_username="cand",
                visual_candidate_id="vc-1",
                budget_s=8.0,
            )
        shot.assert_not_called()
        self.assertEqual(
            out.get("failure_reason"), "post_grid_not_exposed_after_scroll"
        )
        self.assertTrue((out.get("likes_perf_grid") or {}).get("fast_overlay_skip"))

    def test_suggested_with_visible_cell_skips_fast_abort(self) -> None:
        device = mock.MagicMock()
        device.window_size.return_value = (1080, 2340)
        safe_cell = {
            "reliable": True,
            "cell": {
                "left": 0,
                "top": 1000,
                "bottom": 1360,
                "center_x": 180,
                "center_y": 1180,
            },
            "reason": "xml_thumbnail_below_tabs",
        }
        with mock.patch.object(
            nav.config, "POST_FOLLOW_POST_LIKES_ENABLED", True, create=True
        ), mock.patch.object(
            nav, "_post_follow_likes_grid_ui_surface_hints",
            return_value={
                "suggested_for_you": True,
                "discover_people": False,
                "profile_tabs_visible": True,
            },
        ), mock.patch.object(
            nav,
            "_post_follow_likes_stabilize_grid_under_suggested_overlay",
            return_value={
                "ui_hints": {
                    "suggested_for_you": True,
                    "discover_people": False,
                    "profile_tabs_visible": True,
                },
                "cell_meta": safe_cell,
                "scroll_attempts": 1,
                "tap_safe": True,
                "tap_safe_reason": "",
                "grid_exposure": "stable",
            },
        ), mock.patch.object(nav, "screenshot") as shot, mock.patch(
            "PIL.Image.open"
        ), mock.patch.object(nav, "time") as tmock:
            tmock.perf_counter = time.perf_counter
            tmock.monotonic = time.monotonic
            tmock.time = time.time
            tmock.sleep = lambda *_a, **_k: None
            out = nav.ensure_post_grid_visible_for_post_follow_likes(
                device,
                source_profile_username="ct",
                follower_username="cand",
                visual_candidate_id="vc-1",
                budget_s=8.0,
            )
        shot.assert_not_called()
        self.assertTrue(out.get("ok"))
        self.assertIsNotNone(out.get("direct_post_cell_under_suggested"))
        self.assertTrue(out.get("direct_post_cell_tap_safe"))
        self.assertFalse((out.get("likes_perf_grid") or {}).get("fast_overlay_skip"))

    def test_like_grid_cap_starts_after_surface_observed_not_phase_start(self) -> None:
        device = mock.MagicMock()
        logs: list[tuple[str, str, dict[str, object]]] = []
        with mock.patch.object(
            nav,
            "_post_follow_likes_grid_ui_surface_hints",
            return_value={
                "suggested_for_you": True,
                "discover_people": False,
                "profile_tabs_visible": True,
            },
        ), mock.patch.object(
            nav,
            "_post_follow_likes_stabilize_grid_under_suggested_overlay",
            return_value={
                "ui_hints": {
                    "suggested_for_you": True,
                    "discover_people": False,
                    "profile_tabs_visible": True,
                },
                "cell_meta": {
                    "reliable": True,
                    "cell": {
                        "top": 800,
                        "bottom": 1160,
                        "center_x": 180,
                        "center_y": 980,
                    },
                    "reason": "xml_thumbnail_below_tabs",
                },
                "scroll_attempts": 1,
                "tap_safe": True,
                "tap_safe_reason": "",
                "grid_exposure": "stable",
            },
        ), mock.patch.object(nav, "log", side_effect=lambda level, event, **kw: logs.append((level, event, kw))), mock.patch.object(nav, "screenshot") as shot:
            out = nav.ensure_post_grid_visible_for_post_follow_likes(
                device,
                source_profile_username="ct",
                follower_username="cand",
                visual_candidate_id="vc-1",
                budget_s=8.0,
                likes_perf_phase_t0=time.perf_counter()
                - nav._POST_FOLLOW_LIKE_OVERLAY_PHASE_CAP_S
                - 0.1,
            )
        shot.assert_not_called()
        self.assertTrue(out.get("ok"))
        self.assertIsNotNone(out.get("direct_post_cell_under_suggested"))
        self.assertNotEqual(out.get("failure_reason"), "like_grid_global_cap_skipped")
        timer_logs = [
            kw
            for _level, event, kw in logs
            if event == "like_grid_global_timer_started"
        ]
        self.assertEqual(timer_logs[-1].get("timer_scope"), "grid_probe_after_surface_observed")
        self.assertIn(
            "post_follow_like_state_entered",
            [event for _level, event, _kw in logs],
        )

    def test_post_follow_recover_skips_when_row_missing(self) -> None:
        device = mock.MagicMock()
        with mock.patch.object(
            nav.config, "POST_FOLLOW_RECOVER_CANDIDATE_PROFILE_FROM_LIST", True, create=True
        ), mock.patch.object(
            nav, "find_visible_followers_row_by_username", return_value=None
        ):
            out = nav._post_follow_recover_candidate_profile_from_list(
                device,
                candidate_username="cand",
                source_profile_username="ct",
            )
        self.assertFalse(out["recovered"])
        self.assertEqual(out["reason"], "candidate_row_not_visible")

    def test_post_follow_surface_truth_lost_without_candidate_evidence(self) -> None:
        device = mock.MagicMock()
        with mock.patch.object(
            nav, "read_current_profile_username_for_follow_gate", return_value="ct"
        ), mock.patch.object(
            nav,
            "visual_profile_already_following_before_follow",
            return_value={"already_following": False, "detection_method": "none"},
        ):
            out = nav._post_follow_resolve_surface_truth(
                device,
                candidate_username="cand",
                source_profile_username="ct",
                follow_state_after="following",
                det={"action_bar_title": "ct", "is_followers_list": True},
                nav_obs={"state": "FOLLOWERS_LIST"},
                fp={"screen_class": "followers_list"},
            )

        self.assertEqual(out["decision"], "candidate_profile_lost")
        self.assertFalse(out["evidence_profile"])
        self.assertTrue(out["evidence_followers_list"])

    def test_like_precheck_skips_when_mute_sheet_still_open(self) -> None:
        device = mock.MagicMock()
        with mock.patch.object(
            nav,
            "_post_follow_overlay_ui_hints",
            return_value={"likely_mute_toggle_sheet": True},
        ), mock.patch.object(
            nav,
            "_mute_engine_v2_dismiss_mute_sheet",
            return_value=(False, 120.0),
        ), mock.patch.object(
            nav,
            "_mute_engine_v2_mute_sheet_still_visible",
            return_value=True,
        ):
            out = nav._post_follow_like_precheck_mute_sheet(
                device,
                visual_candidate_id="vc-1",
                source_profile_username="ct",
                follower_username="cand",
            )
        self.assertTrue(out["skip_like"])
        self.assertEqual(out["skip_reason"], "mute_sheet_still_open")

    def test_like_surface_precheck_candidate_title_overrides_generic_followers_list(self) -> None:
        device = mock.MagicMock()
        with mock.patch.object(
            nav, "is_followers_list_surface_quick", return_value=True
        ), mock.patch.object(
            nav, "read_current_profile_username_for_follow_gate", return_value="cand"
        ), mock.patch.object(
            nav, "_post_follow_likes_grid_ui_surface_hints"
        ) as hints:
            out = nav._post_follow_like_precheck_surface(
                device,
                source_profile_username="ct",
                follower_username="cand",
                visual_candidate_id="vc-1",
            )

        self.assertFalse(out["skip_like"])
        self.assertEqual(out["skip_reason"], "")
        self.assertTrue(out["profile_candidate_visible"])
        hints.assert_called()

    def test_like_surface_precheck_skips_followers_list_without_candidate_evidence(self) -> None:
        device = mock.MagicMock()
        with mock.patch.object(
            nav, "is_followers_list_surface_quick", return_value=True
        ), mock.patch.object(
            nav, "read_current_profile_username_for_follow_gate", return_value="ct"
        ), mock.patch.object(
            nav, "_post_follow_likes_grid_ui_surface_hints"
        ) as hints:
            out = nav._post_follow_like_precheck_surface(
                device,
                source_profile_username="ct",
                follower_username="cand",
                visual_candidate_id="vc-1",
            )

        self.assertTrue(out["skip_like"])
        self.assertEqual(out["skip_reason"], "followers_list_visible_before_like")
        hints.assert_called()

    def test_like_surface_precheck_skips_wrong_profile_before_grid_probe(self) -> None:
        device = mock.MagicMock()
        with mock.patch.object(
            nav, "is_followers_list_surface_quick", return_value=False
        ), mock.patch.object(
            nav, "read_current_profile_username_for_follow_gate", return_value="other_user"
        ):
            out = nav._post_follow_like_precheck_surface(
                device,
                source_profile_username="ct",
                follower_username="cand",
                visual_candidate_id="vc-1",
            )

        self.assertTrue(out["skip_like"])
        self.assertEqual(out["skip_reason"], "candidate_profile_not_visible_before_like")

    def test_like_phase_no_grid_probe_when_surface_precheck_blocks(self) -> None:
        device = mock.MagicMock()
        with mock.patch.object(
            nav.config, "POST_FOLLOW_POST_LIKES_ENABLED", True, create=True
        ), mock.patch.object(
            nav.config, "ENABLE_REAL_VISUAL_POST_LIKE", True, create=True
        ), mock.patch.object(
            nav, "_post_follow_like_precheck_mute_sheet", return_value={"skip_like": False}
        ), mock.patch.object(
            nav,
            "_post_follow_like_precheck_surface",
            return_value={
                "skip_like": True,
                "skip_reason": "followers_list_visible_before_like",
                "precheck_ms": 2.0,
            },
        ), mock.patch.object(
            nav, "read_current_profile_username_for_follow_gate", return_value="cand"
        ), mock.patch(
            "navigation_engine.observe_instagram_state",
            return_value={"state": "CANDIDATE_PROFILE", "confidence": 0.9},
        ), mock.patch.object(
            nav, "ensure_post_grid_visible_for_post_follow_likes"
        ) as grid_fn:
            out = nav.run_post_follow_post_likes_phase(
                device,
                pkg="com.instagram.android",
                source_profile_username="ct",
                follower_username="cand",
                visual_candidate_id="vc-1",
                follow_success_verified=True,
                follow_state_after="following",
                skipped_tap=False,
            )

        grid_fn.assert_not_called()
        self.assertEqual(out.get("phase_outcome"), "skipped")
        self.assertEqual(out.get("skipped_reason"), "followers_list_visible_before_like")

    def test_partial_suggested_overlay_helper(self) -> None:
        self.assertTrue(
            nav._post_follow_like_partial_suggested_overlay(
                {"grid_state": "partial", "suggested_for_you": True}
            )
        )
        self.assertFalse(
            nav._post_follow_like_partial_suggested_overlay(
                {"grid_state": "visible", "suggested_for_you": True}
            )
        )

    def test_grid_cell_tap_center_on_1080x2340(self) -> None:
        iw, ih, ww, wh = 1080, 2340, 1080, 2340
        cell_w = iw // 3
        cell_h = cell_w
        x0, y0 = 0, 800
        tx, ty = nav._visual_grid_cell_tap_xy_device(
            x0,
            y0,
            cell_w,
            cell_h,
            iw=iw,
            ih=ih,
            ww=ww,
            wh=wh,
            tap_frac_x=nav._POST_FOLLOW_GRID_CELL_TAP_FRAC_X,
            tap_frac_y=nav._POST_FOLLOW_GRID_CELL_TAP_FRAC_Y,
        )
        self.assertEqual(tx, 180)
        self.assertEqual(ty, 980)

    def test_post_follow_viewer_poll_caps_below_legacy(self) -> None:
        self.assertLess(
            nav._POST_FOLLOW_VIEWER_OPEN_POLL_MAX_S,
            nav._VISUAL_POST_VIEWER_OPEN_POLL_MAX_S,
        )
        self.assertLess(
            nav._POST_FOLLOW_VIEWER_OPEN_WALL_CAP_S,
            nav._VISUAL_POST_VIEWER_OPEN_POLL_MAX_S
            + nav._VISUAL_POST_VIEWER_OPEN_POLL_RETRY_MAX_S,
        )

    @mock.patch("instagram_navigation._visual_detect_post_viewer_opened_after_tap")
    @mock.patch("instagram_navigation.time.sleep")
    def test_fast_viewer_wait_early_exit_on_detect(
        self, _sleep: mock.MagicMock, detect: mock.MagicMock
    ) -> None:
        detect.return_value = {
            "post_detected": True,
            "viewer_detect_path": "phase_a_like_unlike_fast",
        }
        d = mock.MagicMock()
        t0 = time.perf_counter()
        out = nav._visual_wait_post_viewer_opened_after_tap(
            d,
            pkg="com.instagram.android",
            expected_follower_username="cand",
            act_before=None,
            poll_label="first_tap",
            post_follow_fast=True,
        )
        elapsed = time.perf_counter() - t0
        self.assertTrue(out.get("post_detected"))
        self.assertLess(elapsed, nav._POST_FOLLOW_VIEWER_OPEN_WALL_CAP_S + 0.15)
        detect.assert_called()
        self.assertTrue(
            all(
                c.kwargs.get("post_follow_fast") is True
                for c in detect.call_args_list
            )
        )

    @mock.patch("instagram_navigation._visual_detect_post_viewer_opened_after_tap")
    @mock.patch("instagram_navigation.time.sleep")
    def test_fast_viewer_wait_respects_wall_cap_when_detect_slow(
        self, _sleep: mock.MagicMock, detect: mock.MagicMock
    ) -> None:
        def _slow_detect(*_a: object, **_k: object) -> dict[str, object]:
            time.sleep(0.35)
            return {"post_detected": False, "prof_still_on_candidate_profile": True}

        detect.side_effect = _slow_detect
        d = mock.MagicMock()
        t0 = time.perf_counter()
        out = nav._visual_wait_post_viewer_opened_after_tap(
            d,
            pkg="com.instagram.android",
            expected_follower_username="cand",
            act_before=None,
            post_follow_fast=True,
        )
        elapsed = time.perf_counter() - t0
        self.assertFalse(out.get("post_detected"))
        self.assertLessEqual(elapsed, nav._POST_FOLLOW_VIEWER_OPEN_WALL_CAP_S + 0.25)

    def test_likes_disabled_early_exit_has_no_grid_cost(self) -> None:
        d = mock.MagicMock()
        with mock.patch.object(
            nav.config, "POST_FOLLOW_POST_LIKES_ENABLED", False, create=True
        ), mock.patch.object(
            nav, "ensure_post_grid_visible_for_post_follow_likes"
        ) as grid_fn:
            out = nav.run_post_follow_post_likes_phase(
                d,
                pkg="com.instagram.android",
                source_profile_username="src",
                follower_username="cand",
                visual_candidate_id="vc1",
                follow_success_verified=True,
                follow_state_after="following",
                skipped_tap=False,
            )
        grid_fn.assert_not_called()
        self.assertEqual(out.get("phase_outcome"), "skipped")
        self.assertEqual(out.get("skipped_reason"), "likes_disabled_by_config")

    def test_open_post_budget_constants(self) -> None:
        self.assertLessEqual(nav._POST_FOLLOW_LIKE_OPEN_POST_MAX_S, 4.0)
        self.assertLessEqual(nav._POST_FOLLOW_LIKE_GRID_PREP_MAX_S, 6.0)

    def test_no_posts_tier1_direct_detect_skips_before_legacy_safe_open(self) -> None:
        device = mock.MagicMock()
        contract_ctx = mock.MagicMock()
        contract_ctx.current_state.value = "sheet_dismissed"
        logs: list[tuple[str, dict[str, object]]] = []

        def _fake_log(_level: str, event: str, **kw: object) -> None:
            logs.append((str(event), dict(kw)))

        with mock.patch.object(
            nav.config, "POST_FOLLOW_POST_LIKES_ENABLED", True, create=True
        ), mock.patch.object(
            nav.config, "ENABLE_REAL_VISUAL_POST_LIKE", True, create=True
        ), mock.patch.object(
            nav.config, "POST_FOLLOW_POST_LIKES_PERCENTAGE", 100, create=True
        ), mock.patch.object(
            nav.config, "POST_FOLLOW_POST_LIKES_COUNT_RANGE", "1-1", create=True
        ), mock.patch.object(
            nav.config, "POST_FOLLOW_TOTAL_LIKES_LIMIT", 150, create=True
        ), mock.patch.object(
            nav, "read_current_profile_username_for_follow_gate", return_value="cand"
        ), mock.patch(
            "navigation_engine.observe_instagram_state",
            return_value={"state": "CANDIDATE_PROFILE", "confidence": 0.9},
        ), mock.patch.object(
            nav,
            "_post_follow_like_precheck_mute_sheet",
            return_value={"skip_like": False, "precheck_ms": 1.0},
        ), mock.patch.object(
            nav,
            "_post_follow_like_precheck_surface",
            return_value={
                "skip_like": False,
                "skip_reason": "",
                "precheck_ms": 1.0,
                "profile_candidate_visible": True,
                "grid_tab_visible": True,
                "followers_list_visible": False,
            },
        ), mock.patch(
            "follow_state_contract.evaluate_like_precheck_contract",
            return_value=(contract_ctx, True, ""),
        ), mock.patch.object(
            nav,
            "_visual_profile_no_posts_tier1_direct_check",
            return_value={
                "no_posts_detected": True,
                "detection_method": "tier1_ui_textContains:No Posts Yet",
                "confidence": 0.91,
            },
        ) as tier1, mock.patch.object(
            nav,
            "visual_profile_has_no_posts",
        ) as full_cheap, mock.patch.object(
            nav,
            "_post_follow_likes_open_top_left_legacy_visual_safe",
        ) as legacy_open, mock.patch.object(
            nav, "ensure_post_grid_visible_for_post_follow_likes"
        ) as grid_probe, mock.patch.object(
            nav, "log", side_effect=_fake_log
        ), mock.patch.object(nav, "time") as tmock:
            tmock.perf_counter = time.perf_counter
            tmock.time = time.time
            tmock.sleep = lambda *_a, **_k: None
            out = nav.run_post_follow_post_likes_phase(
                device,
                pkg="com.instagram.android",
                source_profile_username="ct",
                follower_username="cand",
                visual_candidate_id="vc-1",
                follow_success_verified=True,
                follow_state_after="following",
                skipped_tap=False,
            )

        tier1.assert_called_once_with(device, source_profile_username="ct")
        full_cheap.assert_not_called()
        legacy_open.assert_not_called()
        grid_probe.assert_not_called()
        self.assertTrue(out.get("ok"))
        self.assertTrue(out.get("skipped"))
        self.assertEqual(out.get("phase_outcome"), "skipped")
        self.assertEqual(out.get("skipped_reason"), "post_follow_like_skipped_no_posts_yet")
        self.assertEqual(out.get("liked_count"), 0)
        self.assertEqual(out.get("attempted_count"), 0)
        self.assertIn("visual_profile_no_posts_detected", [event for event, _kw in logs])
        self.assertIn("visual_profile_no_posts_tier1_check_completed", [event for event, _kw in logs])
        self.assertIn("post_follow_post_likes_phase_skipped", [event for event, _kw in logs])

    def test_no_posts_full_cheap_used_when_surface_ambiguous(self) -> None:
        device = mock.MagicMock()
        contract_ctx = mock.MagicMock()
        contract_ctx.current_state.value = "sheet_dismissed"
        logs: list[str] = []

        with mock.patch.object(
            nav.config, "POST_FOLLOW_POST_LIKES_ENABLED", True, create=True
        ), mock.patch.object(
            nav.config, "ENABLE_REAL_VISUAL_POST_LIKE", True, create=True
        ), mock.patch.object(
            nav.config, "POST_FOLLOW_POST_LIKES_PERCENTAGE", 100, create=True
        ), mock.patch.object(
            nav.config, "POST_FOLLOW_POST_LIKES_COUNT_RANGE", "1-1", create=True
        ), mock.patch.object(
            nav.config, "POST_FOLLOW_TOTAL_LIKES_LIMIT", 150, create=True
        ), mock.patch.object(
            nav, "read_current_profile_username_for_follow_gate", return_value="cand"
        ), mock.patch(
            "navigation_engine.observe_instagram_state",
            return_value={"state": "CANDIDATE_PROFILE", "confidence": 0.9},
        ), mock.patch.object(
            nav, "_post_follow_like_precheck_mute_sheet",
            return_value={"skip_like": False, "precheck_ms": 1.0},
        ), mock.patch.object(
            nav,
            "_post_follow_like_precheck_surface",
            return_value={
                "skip_like": False,
                "precheck_ms": 1.0,
                "profile_candidate_visible": True,
                "grid_tab_visible": False,
                "followers_list_visible": False,
            },
        ), mock.patch(
            "follow_state_contract.evaluate_like_precheck_contract",
            return_value=(contract_ctx, True, ""),
        ), mock.patch.object(
            nav,
            "_visual_profile_no_posts_tier1_direct_check",
            return_value={"no_posts_detected": False, "detection_method": "none", "confidence": 0.0},
        ), mock.patch.object(
            nav,
            "visual_profile_has_no_posts",
            return_value={
                "no_posts_detected": True,
                "detection_method": "hierarchy_regex:0_posts_en",
                "confidence": 0.83,
            },
        ) as full_cheap, mock.patch.object(
            nav, "_post_follow_likes_open_top_left_legacy_visual_safe",
        ) as legacy_open, mock.patch.object(
            nav, "log", side_effect=lambda _level, event, **_kw: logs.append(str(event))
        ), mock.patch.object(nav, "time") as tmock:
            tmock.perf_counter = time.perf_counter
            tmock.time = time.time
            tmock.sleep = lambda *_a, **_k: None
            out = nav.run_post_follow_post_likes_phase(
                device,
                pkg="com.instagram.android",
                source_profile_username="ct",
                follower_username="cand",
                visual_candidate_id="vc-1",
                follow_success_verified=True,
                follow_state_after="following",
                skipped_tap=False,
            )

        full_cheap.assert_called_once_with(
            device,
            source_profile_username="ct",
            include_visual_fallback=False,
        )
        legacy_open.assert_not_called()
        self.assertTrue(out.get("skipped"))
        self.assertIn("visual_profile_no_posts_full_cheap_check_completed", logs)
        self.assertNotIn("visual_profile_no_posts_full_cheap_check_skipped", logs)

    def test_no_posts_normal_grid_surface_skips_early_visual_check(self) -> None:
        device = mock.MagicMock()
        device.window_size.return_value = (1080, 2340)
        contract_ctx = mock.MagicMock()
        contract_ctx.current_state.value = "sheet_dismissed"
        logs: list[str] = []

        with mock.patch.object(
            nav.config, "POST_FOLLOW_POST_LIKES_ENABLED", True, create=True
        ), mock.patch.object(
            nav.config, "ENABLE_REAL_VISUAL_POST_LIKE", True, create=True
        ), mock.patch.object(
            nav.config, "POST_FOLLOW_POST_LIKES_PERCENTAGE", 100, create=True
        ), mock.patch.object(
            nav.config, "POST_FOLLOW_POST_LIKES_COUNT_RANGE", "1-1", create=True
        ), mock.patch.object(
            nav.config, "POST_FOLLOW_TOTAL_LIKES_LIMIT", 150, create=True
        ), _normal_post_viewer_surface_gates(), mock.patch.object(
            nav, "read_current_profile_username_for_follow_gate", return_value="cand"
        ), mock.patch(
            "navigation_engine.observe_instagram_state",
            return_value={"state": "CANDIDATE_PROFILE", "confidence": 0.9},
        ), mock.patch.object(
            nav, "_post_follow_like_precheck_mute_sheet",
            return_value={"skip_like": False, "precheck_ms": 1.0},
        ), mock.patch.object(
            nav,
            "_post_follow_like_precheck_surface",
            return_value={
                "skip_like": False,
                "precheck_ms": 1.0,
                "profile_candidate_visible": True,
                "grid_tab_visible": True,
                "followers_list_visible": False,
            },
        ), mock.patch(
            "follow_state_contract.evaluate_like_precheck_contract",
            return_value=(contract_ctx, True, ""),
        ), mock.patch.object(
            nav,
            "_visual_profile_no_posts_tier1_direct_check",
            return_value={"no_posts_detected": False, "detection_method": "none", "confidence": 0.0},
        ), mock.patch.object(
            nav,
            "visual_profile_has_no_posts",
            return_value={"no_posts_detected": False, "detection_method": "none", "confidence": 0.0},
        ) as no_posts, mock.patch.object(
            nav,
            "_post_follow_likes_open_top_left_legacy_visual_safe",
            return_value={
                "ok": True,
                "post_detected": True,
                "failure_reason": "",
                "open_strategy": "vision_open_top_left_legacy_safe",
                "tap_x": 180,
                "tap_y": 1282,
                "likes_perf_post_open": {},
            },
        ), mock.patch.object(
            nav, "visual_post_already_liked",
            return_value={"already_liked": False, "detection_method": "hierarchy_like_hint"},
        ), mock.patch.object(
            nav, "visual_like_open_post",
            return_value={"ok": True, "already_liked": False, "real_tap_sent": True, "likes_perf_like": {}},
        ), mock.patch.object(
            nav, "visual_verify_post_liked",
            return_value={"liked_verified": True, "verification_method": "visual", "verify_attempts_count": 1},
        ), mock.patch.object(
            nav, "visual_return_to_profile_from_post", return_value={"ok": True}
        ), mock.patch.object(nav, "log", side_effect=lambda _level, event, **_kw: logs.append(str(event))), mock.patch.object(nav, "time") as tmock:
            tmock.perf_counter = time.perf_counter
            tmock.time = time.time
            tmock.sleep = lambda *_a, **_k: None
            out = nav.run_post_follow_post_likes_phase(
                device,
                pkg="com.instagram.android",
                source_profile_username="ct",
                follower_username="cand",
                visual_candidate_id="vc-1",
                follow_success_verified=True,
                follow_state_after="following",
                skipped_tap=False,
            )

        self.assertEqual(out.get("phase_outcome"), "success")
        no_posts.assert_not_called()
        self.assertIn("visual_profile_no_posts_tier1_check_completed", logs)
        self.assertIn("visual_profile_no_posts_full_cheap_check_skipped", logs)
        self.assertIn("visual_profile_no_posts_early_visual_check_skipped", logs)
        self.assertIn("visual_profile_no_posts_visual_fallback_deferred", logs)
        self.assertNotIn("visual_profile_no_posts_early_visual_check_started", logs)

    def test_no_posts_early_visual_check_runs_with_weak_hint_and_skips_quickly(self) -> None:
        device = mock.MagicMock()
        device.window_size.return_value = (1080, 2340)
        contract_ctx = mock.MagicMock()
        contract_ctx.current_state.value = "sheet_dismissed"
        logs: list[str] = []
        legacy_outputs = [
            {
                "ok": False,
                "post_detected": False,
                "failure_reason": "legacy_visual_top_left_candidate_ambiguous",
            },
            {
                "ok": False,
                "post_detected": False,
                "failure_reason": "legacy_visual_top_left_variance_insufficient",
            },
        ]

        with mock.patch.object(
            nav.config, "POST_FOLLOW_POST_LIKES_ENABLED", True, create=True
        ), mock.patch.object(
            nav.config, "ENABLE_REAL_VISUAL_POST_LIKE", True, create=True
        ), mock.patch.object(
            nav.config, "POST_FOLLOW_POST_LIKES_PERCENTAGE", 100, create=True
        ), mock.patch.object(
            nav.config, "POST_FOLLOW_POST_LIKES_COUNT_RANGE", "1-1", create=True
        ), mock.patch.object(
            nav.config, "POST_FOLLOW_TOTAL_LIKES_LIMIT", 150, create=True
        ), mock.patch.object(
            nav, "read_current_profile_username_for_follow_gate", return_value="cand"
        ), mock.patch(
            "navigation_engine.observe_instagram_state",
            return_value={"state": "CANDIDATE_PROFILE", "confidence": 0.9},
        ), mock.patch.object(
            nav, "_post_follow_like_precheck_mute_sheet",
            return_value={"skip_like": False, "precheck_ms": 1.0},
        ), mock.patch.object(
            nav,
            "_post_follow_like_precheck_surface",
            return_value={
                "skip_like": False,
                "precheck_ms": 1.0,
                "profile_candidate_visible": True,
                "grid_tab_visible": True,
                "followers_list_visible": False,
            },
        ), mock.patch(
            "follow_state_contract.evaluate_like_precheck_contract",
            return_value=(contract_ctx, True, ""),
        ), mock.patch.object(
            nav,
            "_visual_profile_no_posts_tier1_direct_check",
            return_value={
                "no_posts_detected": False,
                "detection_method": "weak_no_posts_hint",
                "confidence": 0.42,
            },
        ), mock.patch.object(
            nav,
            "visual_profile_has_no_posts",
            return_value={
                "no_posts_detected": True,
                "detection_method": "visual_blank_grid",
                "confidence": 0.86,
            },
        ) as no_posts, mock.patch.object(
            nav, "_post_follow_likes_open_top_left_legacy_visual_safe", side_effect=legacy_outputs
        ) as legacy_open, mock.patch.object(
            nav,
            "_post_follow_likes_profile_scroll_swipe",
            return_value={"swipe_ok": True, "y_start": 1684, "y_end": 936, "scroll_distance_px": 748},
        ) as reveal_swipe, mock.patch.object(
            nav, "log", side_effect=lambda _level, event, **_kw: logs.append(str(event))
        ), mock.patch.object(nav, "time") as tmock:
            tmock.perf_counter = time.perf_counter
            tmock.time = time.time
            tmock.sleep = lambda *_a, **_k: None
            out = nav.run_post_follow_post_likes_phase(
                device,
                pkg="com.instagram.android",
                source_profile_username="ct",
                follower_username="cand",
                visual_candidate_id="vc-1",
                follow_success_verified=True,
                follow_state_after="following",
                skipped_tap=False,
            )

        self.assertTrue(out.get("ok"))
        self.assertTrue(out.get("skipped"))
        self.assertEqual(out.get("skipped_reason"), "post_follow_like_skipped_no_posts_yet")
        no_posts.assert_called_once_with(
            device,
            source_profile_username="ct",
            include_visual_fallback=True,
        )
        self.assertIn("visual_profile_no_posts_full_cheap_check_skipped", logs)
        self.assertIn("visual_profile_no_posts_early_visual_check_started", logs)
        self.assertIn("visual_profile_no_posts_early_visual_check_completed", logs)
        legacy_open.assert_not_called()
        reveal_swipe.assert_not_called()

    def test_no_posts_visual_fallback_can_confirm_after_grid_open_failure_when_early_ambiguous(self) -> None:
        device = mock.MagicMock()
        device.window_size.return_value = (1080, 2340)
        contract_ctx = mock.MagicMock()
        contract_ctx.current_state.value = "sheet_dismissed"
        logs: list[str] = []
        legacy_outputs = [
            {
                "ok": False,
                "post_detected": False,
                "failure_reason": "legacy_visual_top_left_candidate_ambiguous",
            },
            {
                "ok": False,
                "post_detected": False,
                "failure_reason": "legacy_visual_top_left_variance_insufficient",
            },
        ]

        with mock.patch.object(
            nav.config, "POST_FOLLOW_POST_LIKES_ENABLED", True, create=True
        ), mock.patch.object(
            nav.config, "ENABLE_REAL_VISUAL_POST_LIKE", True, create=True
        ), mock.patch.object(
            nav.config, "POST_FOLLOW_POST_LIKES_PERCENTAGE", 100, create=True
        ), mock.patch.object(
            nav.config, "POST_FOLLOW_POST_LIKES_COUNT_RANGE", "1-1", create=True
        ), mock.patch.object(
            nav.config, "POST_FOLLOW_TOTAL_LIKES_LIMIT", 150, create=True
        ), mock.patch.object(
            nav, "read_current_profile_username_for_follow_gate", return_value="cand"
        ), mock.patch(
            "navigation_engine.observe_instagram_state",
            return_value={"state": "CANDIDATE_PROFILE", "confidence": 0.9},
        ), mock.patch.object(
            nav,
            "_post_follow_like_precheck_mute_sheet",
            return_value={"skip_like": False, "precheck_ms": 1.0},
        ), mock.patch.object(
            nav,
            "_post_follow_like_precheck_surface",
            return_value={
                "skip_like": False,
                "precheck_ms": 1.0,
                "profile_candidate_visible": True,
                "grid_tab_visible": True,
                "followers_list_visible": False,
            },
        ), mock.patch(
            "follow_state_contract.evaluate_like_precheck_contract",
            return_value=(contract_ctx, True, ""),
        ), mock.patch.object(
            nav,
            "_visual_profile_no_posts_tier1_direct_check",
            return_value={
                "no_posts_detected": False,
                "detection_method": "weak_no_posts_hint",
                "confidence": 0.42,
            },
        ), mock.patch.object(
            nav,
            "visual_profile_has_no_posts",
            side_effect=[
                {"no_posts_detected": False, "detection_method": "none", "confidence": 0.0},
                {"no_posts_detected": True, "detection_method": "visual_blank_grid", "confidence": 0.86},
            ],
        ) as no_posts, mock.patch.object(
            nav, "_post_follow_likes_open_top_left_legacy_visual_safe", side_effect=legacy_outputs
        ), mock.patch.object(
            nav,
            "_post_follow_likes_profile_scroll_swipe",
            return_value={"swipe_ok": True, "y_start": 1684, "y_end": 936, "scroll_distance_px": 748},
        ), mock.patch.object(
            nav, "log", side_effect=lambda _level, event, **_kw: logs.append(str(event))
        ), mock.patch.object(nav, "time") as tmock:
            tmock.perf_counter = time.perf_counter
            tmock.time = time.time
            tmock.sleep = lambda *_a, **_k: None
            out = nav.run_post_follow_post_likes_phase(
                device,
                pkg="com.instagram.android",
                source_profile_username="ct",
                follower_username="cand",
                visual_candidate_id="vc-1",
                follow_success_verified=True,
                follow_state_after="following",
                skipped_tap=False,
            )

        self.assertTrue(out.get("ok"))
        self.assertTrue(out.get("skipped"))
        self.assertEqual(out.get("skipped_reason"), "post_follow_like_skipped_no_posts_yet")
        self.assertEqual(no_posts.call_count, 2)
        self.assertIn("visual_profile_no_posts_early_visual_check_started", logs)
        self.assertIn("visual_profile_no_posts_early_visual_check_completed", logs)
        self.assertIn("visual_profile_no_posts_early_visual_check_rejected", logs)
        self.assertIn("visual_profile_no_posts_visual_fallback_started", logs)
        self.assertIn("visual_profile_no_posts_visual_fallback_completed", logs)

    def test_no_posts_visual_fallback_false_preserves_open_failure(self) -> None:
        device = mock.MagicMock()
        device.window_size.return_value = (1080, 2340)
        contract_ctx = mock.MagicMock()
        contract_ctx.current_state.value = "sheet_dismissed"
        legacy_outputs = [
            {"ok": False, "post_detected": False, "failure_reason": "legacy_visual_top_left_candidate_ambiguous"},
            {"ok": False, "post_detected": False, "failure_reason": "legacy_visual_top_left_variance_insufficient"},
        ]

        with mock.patch.object(nav.config, "POST_FOLLOW_POST_LIKES_ENABLED", True, create=True), mock.patch.object(
            nav.config, "ENABLE_REAL_VISUAL_POST_LIKE", True, create=True
        ), mock.patch.object(nav.config, "POST_FOLLOW_POST_LIKES_PERCENTAGE", 100, create=True), mock.patch.object(
            nav.config, "POST_FOLLOW_POST_LIKES_COUNT_RANGE", "1-1", create=True
        ), mock.patch.object(nav.config, "POST_FOLLOW_TOTAL_LIKES_LIMIT", 150, create=True), _normal_post_viewer_surface_gates(), mock.patch.object(
            nav, "read_current_profile_username_for_follow_gate", return_value="cand"
        ), mock.patch(
            "navigation_engine.observe_instagram_state",
            return_value={"state": "CANDIDATE_PROFILE", "confidence": 0.9},
        ), mock.patch.object(
            nav, "_post_follow_like_precheck_mute_sheet", return_value={"skip_like": False, "precheck_ms": 1.0}
        ), mock.patch.object(
            nav, "_post_follow_like_precheck_surface",
            return_value={
                "skip_like": False,
                "precheck_ms": 1.0,
                "profile_candidate_visible": True,
                "grid_tab_visible": True,
                "followers_list_visible": False,
            },
        ), mock.patch(
            "follow_state_contract.evaluate_like_precheck_contract",
            return_value=(contract_ctx, True, ""),
        ), mock.patch.object(
            nav,
            "_visual_profile_no_posts_tier1_direct_check",
            return_value={"no_posts_detected": False, "detection_method": "none", "confidence": 0.0},
        ), mock.patch.object(
            nav,
            "visual_profile_has_no_posts",
            return_value={"no_posts_detected": False, "detection_method": "none", "confidence": 0.0},
        ) as no_posts, mock.patch.object(
            nav, "_post_follow_likes_open_top_left_legacy_visual_safe", side_effect=legacy_outputs
        ), mock.patch.object(
            nav, "_post_follow_likes_profile_scroll_swipe",
            return_value={"swipe_ok": True, "y_start": 1684, "y_end": 936, "scroll_distance_px": 748},
        ), mock.patch.object(nav, "log"), mock.patch.object(nav, "time") as tmock:
            tmock.perf_counter = time.perf_counter
            tmock.time = time.time
            tmock.sleep = lambda *_a, **_k: None
            out = nav.run_post_follow_post_likes_phase(
                device,
                pkg="com.instagram.android",
                source_profile_username="ct",
                follower_username="cand",
                visual_candidate_id="vc-1",
                follow_success_verified=True,
                follow_state_after="following",
                skipped_tap=False,
            )

        self.assertEqual(out.get("phase_outcome"), "failed_safe_continue")
        self.assertEqual(out.get("failed_navigation_count"), 1)
        no_posts.assert_called_once_with(
            device,
            source_profile_username="ct",
            include_visual_fallback=True,
        )

    def test_pre_reveal_unknown_tabs_scrolls_before_legacy_safe(self) -> None:
        device = mock.MagicMock()
        device.window_size.return_value = (1080, 2340)
        contract_ctx = mock.MagicMock()
        contract_ctx.current_state.value = "sheet_dismissed"
        order: list[str] = []
        logs: list[tuple[str, dict[str, object]]] = []

        def _fake_log(_level: str, event: str, **kw: object) -> None:
            order.append(str(event))
            logs.append((str(event), dict(kw)))

        def _legacy_open(*_a: object, **_kw: object) -> dict[str, object]:
            order.append("legacy_open")
            return {
                "ok": True,
                "post_detected": True,
                "failure_reason": "",
                "open_strategy": "vision_open_top_left_legacy_safe",
                "tap_x": 180,
                "tap_y": 1056,
                "detect_reason": "like_unlike_ui",
                "viewer_detect_path": "phase_a2_exact_like_desc_fast",
                "likes_perf_post_open": {},
            }

        def _pre_reveal_swipe(*_a: object, **_kw: object) -> dict[str, object]:
            order.append("pre_reveal_swipe")
            return {"swipe_ok": True, "y_start": 1684, "y_end": 936, "scroll_distance_px": 748}

        with ExitStack() as stack:
            _patch_pre_reveal_like_phase_common(stack, contract_ctx=contract_ctx)
            stack.enter_context(
                mock.patch.object(nav, "_followers_profile_tabs_bottom_y_px", return_value=(None, ""))
            )
            reveal_swipe = stack.enter_context(
                mock.patch.object(
                    nav, "_post_follow_likes_profile_scroll_swipe", side_effect=_pre_reveal_swipe
                )
            )
            legacy_open = stack.enter_context(
                mock.patch.object(
                    nav,
                    "_post_follow_likes_open_top_left_legacy_visual_safe",
                    side_effect=_legacy_open,
                )
            )
            stack.enter_context(
                mock.patch.object(
                    nav,
                    "visual_post_already_liked",
                    return_value={"already_liked": False, "detection_method": "hierarchy_like_hint"},
                )
            )
            stack.enter_context(
                mock.patch.object(
                    nav,
                    "visual_like_open_post",
                    return_value={
                        "ok": True,
                        "already_liked": False,
                        "real_tap_sent": True,
                        "likes_perf_like": {},
                    },
                )
            )
            stack.enter_context(
                mock.patch.object(
                    nav,
                    "visual_verify_post_liked",
                    return_value={
                        "liked_verified": True,
                        "verification_method": "visual",
                        "verify_attempts_count": 1,
                    },
                )
            )
            stack.enter_context(
                mock.patch.object(nav, "visual_return_to_profile_from_post", return_value={"ok": True})
            )
            stack.enter_context(mock.patch.object(nav, "log", side_effect=_fake_log))
            out = nav.run_post_follow_post_likes_phase(
                device,
                pkg="com.instagram.android",
                source_profile_username="ct",
                follower_username="cand",
                visual_candidate_id="vc-1",
                follow_success_verified=True,
                follow_state_after="following",
                skipped_tap=False,
            )

        self.assertEqual(out.get("phase_outcome"), "success")
        legacy_open.assert_called_once()
        reveal_swipe.assert_called_once()
        self.assertIn("post_follow_like_legacy_safe_pre_scroll_skipped", order)
        self.assertIn("post_follow_like_scroll_first_for_unknown_tabs", order)
        self.assertLess(order.index("post_follow_like_legacy_safe_pre_scroll_skipped"), order.index("pre_reveal_swipe"))
        self.assertLess(order.index("pre_reveal_swipe"), order.index("legacy_open"))
        legacy_started_checkpoints = [
            kw
            for event, kw in logs
            if event == "post_like_surface_to_scroll_gap_checkpoint"
            and kw.get("checkpoint") == "legacy_safe_started"
        ]
        self.assertFalse(legacy_started_checkpoints)
        self.assertNotIn("legacy_safe_first_failed_to_retry_started", order)
        completed = [kw for event, kw in logs if event == "post_follow_like_pre_reveal_guard_completed"]
        self.assertTrue(completed)
        self.assertFalse(completed[-1].get("pre_reveal_used"))
        self.assertEqual(completed[-1].get("reason"), "profile_tabs_bottom_unknown")
        scroll_attempts = [kw for event, kw in logs if event == "like_grid_reveal_scroll_attempted"]
        self.assertTrue(scroll_attempts)
        self.assertEqual(scroll_attempts[-1].get("scroll_reason"), "profile_tabs_bottom_unknown_scroll_first")
        self.assertIn("legacy_safe_retry_after_reveal_completed", order)

    def test_pre_reveal_tabs_too_low_runs_before_first_legacy_safe(self) -> None:
        device = mock.MagicMock()
        device.window_size.return_value = (1080, 2340)
        contract_ctx = mock.MagicMock()
        contract_ctx.current_state.value = "sheet_dismissed"
        order: list[str] = []
        logs: list[tuple[str, dict[str, object]]] = []

        def _fake_log(_level: str, event: str, **kw: object) -> None:
            order.append(str(event))
            logs.append((str(event), dict(kw)))

        def _legacy_open(*_a: object, **_kw: object) -> dict[str, object]:
            order.append("legacy_open")
            return {
                "ok": True,
                "post_detected": True,
                "failure_reason": "",
                "open_strategy": "vision_open_top_left_legacy_safe",
                "tap_x": 180,
                "tap_y": 1056,
                "detect_reason": "like_unlike_ui",
                "viewer_detect_path": "phase_a2_exact_like_desc_fast",
                "likes_perf_post_open": {},
            }

        def _pre_reveal_swipe(*_a: object, **_kw: object) -> dict[str, object]:
            order.append("pre_reveal_swipe")
            return {"swipe_ok": True, "y_start": 1684, "y_end": 936, "scroll_distance_px": 748}

        with ExitStack() as stack:
            _patch_pre_reveal_like_phase_common(stack, contract_ctx=contract_ctx)
            stack.enter_context(
                mock.patch.object(
                    nav,
                    "_followers_profile_tabs_bottom_y_px",
                    return_value=(1777, "resourceId:profile_tabs_container"),
                )
            )
            reveal_swipe = stack.enter_context(
                mock.patch.object(
                    nav, "_post_follow_likes_profile_scroll_swipe", side_effect=_pre_reveal_swipe
                )
            )
            legacy_open = stack.enter_context(
                mock.patch.object(
                    nav,
                    "_post_follow_likes_open_top_left_legacy_visual_safe",
                    side_effect=_legacy_open,
                )
            )
            stack.enter_context(
                mock.patch.object(
                    nav,
                    "visual_post_already_liked",
                    return_value={"already_liked": False, "detection_method": "hierarchy_like_hint"},
                )
            )
            stack.enter_context(
                mock.patch.object(
                    nav,
                    "visual_like_open_post",
                    return_value={
                        "ok": True,
                        "already_liked": False,
                        "real_tap_sent": True,
                        "likes_perf_like": {},
                    },
                )
            )
            stack.enter_context(
                mock.patch.object(
                    nav,
                    "visual_verify_post_liked",
                    return_value={
                        "liked_verified": True,
                        "verification_method": "visual",
                        "verify_attempts_count": 1,
                    },
                )
            )
            stack.enter_context(
                mock.patch.object(nav, "visual_return_to_profile_from_post", return_value={"ok": True})
            )
            stack.enter_context(mock.patch.object(nav, "log", side_effect=_fake_log))
            out = nav.run_post_follow_post_likes_phase(
                device,
                pkg="com.instagram.android",
                source_profile_username="ct",
                follower_username="cand",
                visual_candidate_id="vc-1",
                follow_success_verified=True,
                follow_state_after="following",
                skipped_tap=False,
            )

        self.assertEqual(out.get("phase_outcome"), "success")
        legacy_open.assert_called_once()
        reveal_swipe.assert_called_once()
        self.assertIn("visual_profile_no_posts_full_cheap_check_skipped", order)
        self.assertLess(order.index("post_follow_like_pre_reveal_needed"), order.index("pre_reveal_swipe"))
        self.assertLess(order.index("pre_reveal_swipe"), order.index("legacy_open"))
        completed = [kw for event, kw in logs if event == "post_follow_like_pre_reveal_guard_completed"]
        self.assertTrue(completed)
        self.assertTrue(completed[-1].get("pre_reveal_used"))
        self.assertEqual(completed[-1].get("tabs_bottom_y"), 1777)
        invalidated = [
            kw for event, kw in logs if event == "post_like_surface_context_invalidated"
        ]
        self.assertTrue(invalidated)
        self.assertEqual(invalidated[-1].get("known_tabs_bottom_y_px"), 1777)
        self.assertFalse(invalidated[-1].get("reused"))

    def test_pre_reveal_tabs_normal_keeps_first_legacy_safe_direct(self) -> None:
        device = mock.MagicMock()
        device.window_size.return_value = (1080, 2340)
        contract_ctx = mock.MagicMock()
        contract_ctx.current_state.value = "sheet_dismissed"
        logs: list[tuple[str, dict[str, object]]] = []

        with ExitStack() as stack:
            _patch_pre_reveal_like_phase_common(stack, contract_ctx=contract_ctx)
            stack.enter_context(
                mock.patch.object(
                    nav,
                    "_followers_profile_tabs_bottom_y_px",
                    return_value=(1200, "resourceId:profile_tabs_container"),
                )
            )
            reveal_swipe = stack.enter_context(
                mock.patch.object(nav, "_post_follow_likes_profile_scroll_swipe")
            )
            legacy_open = stack.enter_context(
                mock.patch.object(
                    nav,
                    "_post_follow_likes_open_top_left_legacy_visual_safe",
                    return_value={
                        "ok": True,
                        "post_detected": True,
                        "failure_reason": "",
                        "tap_x": 180,
                        "tap_y": 1056,
                        "likes_perf_post_open": {},
                    },
                )
            )
            stack.enter_context(
                mock.patch.object(
                    nav,
                    "visual_post_already_liked",
                    return_value={"already_liked": False, "detection_method": "hierarchy_like_hint"},
                )
            )
            stack.enter_context(
                mock.patch.object(
                    nav,
                    "visual_like_open_post",
                    return_value={
                        "ok": True,
                        "already_liked": False,
                        "real_tap_sent": True,
                        "likes_perf_like": {},
                    },
                )
            )
            stack.enter_context(
                mock.patch.object(
                    nav,
                    "visual_verify_post_liked",
                    return_value={
                        "liked_verified": True,
                        "verification_method": "visual",
                        "verify_attempts_count": 1,
                    },
                )
            )
            stack.enter_context(
                mock.patch.object(nav, "visual_return_to_profile_from_post", return_value={"ok": True})
            )
            stack.enter_context(
                mock.patch.object(
                    nav,
                    "log",
                    side_effect=lambda _level, event, **kw: logs.append((str(event), dict(kw))),
                )
            )
            out = nav.run_post_follow_post_likes_phase(
                device,
                pkg="com.instagram.android",
                source_profile_username="ct",
                follower_username="cand",
                visual_candidate_id="vc-1",
                follow_success_verified=True,
                follow_state_after="following",
                skipped_tap=False,
            )

        self.assertEqual(out.get("phase_outcome"), "success")
        legacy_open.assert_called_once()
        reveal_swipe.assert_not_called()
        self.assertNotIn("post_follow_like_pre_reveal_needed", [event for event, _kw in logs])
        completed = [kw for event, kw in logs if event == "post_follow_like_pre_reveal_guard_completed"]
        self.assertTrue(completed)
        self.assertFalse(completed[-1].get("pre_reveal_used"))
        self.assertEqual(completed[-1].get("reason"), "grid_geometry_exploitable_before_legacy_safe")

    def test_pre_reveal_scroll_failure_preserves_legacy_ambiguous_fallback(self) -> None:
        device = mock.MagicMock()
        device.window_size.return_value = (1080, 2340)
        contract_ctx = mock.MagicMock()
        contract_ctx.current_state.value = "sheet_dismissed"
        legacy_outputs = [
            {"ok": False, "post_detected": False, "failure_reason": "legacy_visual_top_left_candidate_ambiguous"},
            {"ok": True, "post_detected": True, "failure_reason": "", "tap_x": 180, "tap_y": 1056, "likes_perf_post_open": {}},
        ]
        swipe_outputs = [
            {"swipe_ok": False, "y_start": 1684, "y_end": 936, "scroll_distance_px": 748},
            {"swipe_ok": True, "y_start": 1684, "y_end": 936, "scroll_distance_px": 748},
        ]
        log_events: list[str] = []

        with ExitStack() as stack:
            _patch_pre_reveal_like_phase_common(stack, contract_ctx=contract_ctx)
            stack.enter_context(
                mock.patch.object(
                    nav,
                    "_followers_profile_tabs_bottom_y_px",
                    return_value=(1777, "resourceId:profile_tabs_container"),
                )
            )
            reveal_swipe = stack.enter_context(
                mock.patch.object(
                    nav, "_post_follow_likes_profile_scroll_swipe", side_effect=swipe_outputs
                )
            )
            legacy_open = stack.enter_context(
                mock.patch.object(
                    nav,
                    "_post_follow_likes_open_top_left_legacy_visual_safe",
                    side_effect=legacy_outputs,
                )
            )
            stack.enter_context(
                mock.patch.object(
                    nav,
                    "visual_post_already_liked",
                    return_value={"already_liked": False, "detection_method": "hierarchy_like_hint"},
                )
            )
            stack.enter_context(
                mock.patch.object(
                    nav,
                    "visual_like_open_post",
                    return_value={
                        "ok": True,
                        "already_liked": False,
                        "real_tap_sent": True,
                        "likes_perf_like": {},
                    },
                )
            )
            stack.enter_context(
                mock.patch.object(
                    nav,
                    "visual_verify_post_liked",
                    return_value={
                        "liked_verified": True,
                        "verification_method": "visual",
                        "verify_attempts_count": 1,
                    },
                )
            )
            stack.enter_context(
                mock.patch.object(nav, "visual_return_to_profile_from_post", return_value={"ok": True})
            )
            stack.enter_context(
                mock.patch.object(
                    nav,
                    "log",
                    side_effect=lambda _level, event, **_kw: log_events.append(str(event)),
                )
            )
            out = nav.run_post_follow_post_likes_phase(
                device,
                pkg="com.instagram.android",
                source_profile_username="ct",
                follower_username="cand",
                visual_candidate_id="vc-1",
                follow_success_verified=True,
                follow_state_after="following",
                skipped_tap=False,
            )

        self.assertIn("visual_profile_no_posts_full_cheap_check_skipped", log_events)
        self.assertEqual(out.get("phase_outcome"), "success")
        self.assertEqual(legacy_open.call_count, 2)
        self.assertEqual(reveal_swipe.call_count, 2)
        self.assertIn("post_follow_like_pre_reveal_needed", log_events)
        self.assertIn("legacy_safe_first_failed_to_retry_started", log_events)
        self.assertIn("legacy_safe_retry_after_reveal_completed", log_events)

    def test_legacy_ambiguous_reveals_and_retries_without_xml_probe(self) -> None:
        device = mock.MagicMock()
        device.window_size.return_value = (1080, 2340)
        contract_ctx = mock.MagicMock()
        contract_ctx.current_state.value = "sheet_dismissed"
        log_events: list[str] = []

        def _fake_log(_level: str, event: str, **_kw: object) -> None:
            log_events.append(str(event))

        legacy_outputs = [
            {
                "ok": False,
                "post_detected": False,
                "failure_reason": "legacy_visual_top_left_candidate_ambiguous",
                "open_strategy": "vision_open_top_left_legacy_safe",
            },
            {
                "ok": True,
                "post_detected": True,
                "failure_reason": "",
                "open_strategy": "vision_open_top_left_legacy_safe",
                "tap_x": 180,
                "tap_y": 1282,
                "detect_reason": "like_unlike_ui",
                "viewer_detect_path": "phase_a2_exact_like_desc_fast",
                "likes_perf_post_open": {
                    "legacy_visual_top_left_total_ms": 400.0,
                    "open_strategy": "vision_open_top_left_legacy_safe",
                },
            },
        ]

        with ExitStack() as stack:
            _patch_like_phase_common(stack, contract_ctx=contract_ctx)
            stack.enter_context(
                mock.patch.object(nav.config, "POST_FOLLOW_POST_LIKES_BUDGET_S", 20.0, create=True)
            )
            stack.enter_context(
                mock.patch.object(
                    nav,
                    "_post_follow_like_precheck_surface",
                    return_value={
                        "skip_like": False,
                        "skip_reason": "",
                        "precheck_ms": 1.0,
                        "profile_candidate_visible": True,
                    },
                )
            )
            legacy_open = stack.enter_context(
                mock.patch.object(
                    nav,
                    "_post_follow_likes_open_top_left_legacy_visual_safe",
                    side_effect=legacy_outputs,
                )
            )
            reveal_swipe = stack.enter_context(
                mock.patch.object(
                    nav,
                    "_post_follow_likes_profile_scroll_swipe",
                    return_value={
                        "swipe_ok": True,
                        "scroll_profile": "reveal_moderate",
                        "y_start": 1684,
                        "y_end": 936,
                        "scroll_distance_px": 748,
                    },
                )
            )
            xml_probe = stack.enter_context(
                mock.patch.object(nav, "ensure_post_grid_visible_for_post_follow_likes")
            )
            stack.enter_context(
                mock.patch.object(
                    nav,
                    "visual_post_already_liked",
                    return_value={
                        "already_liked": False,
                        "detection_method": "hierarchy_like_hint",
                        "confidence": 0.7,
                        "semantic_like_state": "like",
                        "already_liked_decision_reason": "hierarchy_like_confirmed_not_liked",
                    },
                )
            )
            stack.enter_context(
                mock.patch.object(
                    nav,
                    "visual_like_open_post",
                    return_value={
                        "ok": True,
                        "already_liked": False,
                        "real_tap_sent": True,
                        "tap_x": 79,
                        "tap_y": 2017,
                        "confidence": 0.8,
                        "like_button_bounds": {
                            "left": 43,
                            "top": 1951,
                            "right": 115,
                            "bottom": 2084,
                        },
                        "likes_perf_like": {"like_tap_dispatch_ms": 1.0},
                    },
                )
            )
            stack.enter_context(
                mock.patch.object(
                    nav,
                    "visual_verify_post_liked",
                    return_value={
                        "liked_verified": True,
                        "verification_method": "visual_filled_heart_red_ratio_verify_post_tap_reuse",
                        "verify_attempts_count": 1,
                    },
                )
            )
            stack.enter_context(
                mock.patch.object(nav, "visual_return_to_profile_from_post", return_value={"ok": True})
            )
            stack.enter_context(mock.patch.object(nav, "log", side_effect=_fake_log))
            out = nav.run_post_follow_post_likes_phase(
                device,
                pkg="com.instagram.android",
                source_profile_username="ct",
                follower_username="cand",
                visual_candidate_id="vc-1",
                follow_success_verified=True,
                follow_state_after="following",
                skipped_tap=False,
            )

        self.assertEqual(legacy_open.call_count, 2)
        reveal_swipe.assert_called_once()
        self.assertEqual(reveal_swipe.call_args.kwargs.get("scroll_profile"), "reveal_moderate")
        xml_probe.assert_not_called()
        self.assertEqual(out.get("phase_outcome"), "success")
        self.assertEqual(out.get("liked_count"), 1)
        self.assertIn("legacy_safe_first_failed_to_retry_started", log_events)
        self.assertIn("xml_probe_skipped_after_legacy_ambiguous", log_events)
        self.assertIn("legacy_safe_retry_after_reveal_started", log_events)
        self.assertIn("legacy_safe_retry_after_reveal_completed", log_events)
        self.assertIn("legacy_safe_retry_viewer_confirmed", log_events)

    def test_overlay_strategy_perf_is_reported_in_summary_payload(self) -> None:
        payload = nav._post_follow_like_perf_summary_payload(
            timings={"likes_total_ms": 3000.0, "overlay_strategy_ms": 320.0},
            post_open={},
            like_perf={},
            result="failed_safe_continue",
            failure_reason="post_grid_partial_suggested_overlay",
        )

        self.assertEqual(payload["overlay_strategy_ms"], 320.0)
        self.assertEqual(payload["failure_reason"], "post_grid_partial_suggested_overlay")

    def test_suggested_overlay_scroll_strategy_budget_is_bounded_but_not_precheck_starved(self) -> None:
        self.assertGreaterEqual(nav._POST_FOLLOW_LIKE_OVERLAY_PHASE_CAP_S, 10.5)
        self.assertLessEqual(nav._POST_FOLLOW_LIKE_OVERLAY_PHASE_CAP_S, 12.0)
        self.assertLessEqual(nav._POST_FOLLOW_LIKE_GRID_PREP_MAX_S, 6.0)
        self.assertLessEqual(nav._POST_FOLLOW_LIKE_PARTIAL_GRID_DETECT_CAP_S, 4.5)
        self.assertLessEqual(nav._POST_FOLLOW_RETURN_CT_POST_LIKE_RECOVERY_CAP_S, 8.0)

    def test_evaluate_tap_safe_rejects_estimate_cell(self) -> None:
        cell = {
            "left": 0,
            "top": 1000,
            "bottom": 1360,
            "center_x": 180,
            "center_y": 1180,
        }
        ok, reason = nav._post_follow_likes_evaluate_post_cell_tap_safe(
            cell,
            reason="profile_tabs_grid_cell_estimate",
            ww=1080,
            wh=2340,
            y_min_px=900,
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "post_cell_estimate_not_safe_to_tap")

    def test_evaluate_tap_safe_accepts_xml_cell_in_stable_band(self) -> None:
        cell = {
            "left": 0,
            "top": 1000,
            "bottom": 1360,
            "center_x": 180,
            "center_y": 1180,
        }
        ok, reason = nav._post_follow_likes_evaluate_post_cell_tap_safe(
            cell,
            reason="xml_thumbnail_top_left",
            ww=1080,
            wh=2340,
            y_min_px=900,
        )
        self.assertTrue(ok)
        self.assertEqual(reason, "")

    def test_dynamic_first_row_layout_reuses_known_tabs_bottom(self) -> None:
        device = mock.MagicMock()
        with mock.patch.object(
            nav, "_followers_profile_tabs_bottom_y_px"
        ) as tabs_probe:
            y_min, source, tabs_bottom, margin = (
                nav._post_follow_dynamic_first_row_search_y_min_layout(
                    device,
                    2340,
                    {"profile_tabs_visible": True},
                    known_tabs_bottom_y_px=900,
                    known_tabs_bottom_source="same_attempt_tabs",
                )
            )

        tabs_probe.assert_not_called()
        self.assertEqual(y_min, 900 + nav._POST_FOLLOW_PROFILE_TABS_GRID_MARGIN_PX)
        self.assertEqual(source, "profile_tabs_bottom")
        self.assertEqual(tabs_bottom, 900)
        self.assertEqual(margin, nav._POST_FOLLOW_PROFILE_TABS_GRID_MARGIN_PX)

    def test_dynamic_first_row_layout_falls_back_when_known_tabs_invalid(self) -> None:
        device = mock.MagicMock()
        with mock.patch.object(
            nav,
            "_followers_profile_tabs_bottom_y_px",
            return_value=(800, "resourceId:profile_tabs_container"),
        ) as tabs_probe:
            y_min, source, tabs_bottom, margin = (
                nav._post_follow_dynamic_first_row_search_y_min_layout(
                    device,
                    2340,
                    {"profile_tabs_visible": True},
                    known_tabs_bottom_y_px=-1,
                    known_tabs_bottom_source="invalid_pre_scroll",
                )
            )

        tabs_probe.assert_called_once_with(device, window_h=2340)
        self.assertEqual(y_min, 800 + nav._POST_FOLLOW_PROFILE_TABS_GRID_MARGIN_PX)
        self.assertEqual(source, "profile_tabs_bottom")
        self.assertEqual(tabs_bottom, 800)
        self.assertEqual(margin, nav._POST_FOLLOW_PROFILE_TABS_GRID_MARGIN_PX)

    def test_evaluate_tap_safe_rejects_xml_cell_too_low(self) -> None:
        cell = {
            "left": 0,
            "top": 1800,
            "bottom": 2160,
            "center_x": 180,
            "center_y": 1980,
        }
        ok, reason = nav._post_follow_likes_evaluate_post_cell_tap_safe(
            cell,
            reason="xml_thumbnail_top_left",
            ww=1080,
            wh=2340,
            y_min_px=1200,
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "post_cell_too_low_for_safe_tap")

    def test_reveal_moderate_scroll_between_micro_and_reveal_grid(self) -> None:
        micro = nav._post_follow_likes_scroll_geometry(
            scroll_profile="micro", ww=1080, wh=2340
        )
        moderate = nav._post_follow_likes_scroll_geometry(
            scroll_profile="reveal_moderate", ww=1080, wh=2340
        )
        reveal = nav._post_follow_likes_scroll_geometry(
            scroll_profile="reveal_grid", ww=1080, wh=2340
        )
        self.assertEqual(moderate["scroll_profile"], "reveal_moderate")
        self.assertGreater(moderate["scroll_distance_px"], micro["scroll_distance_px"] * 2)
        self.assertLess(moderate["scroll_distance_px"], reveal["scroll_distance_px"])
        self.assertEqual(moderate["y_start"], int(2340 * 0.72))
        self.assertEqual(moderate["y_end"], int(2340 * 0.40))

    def test_reveal_nudge_is_smaller_than_reveal_moderate(self) -> None:
        moderate = nav._post_follow_likes_scroll_geometry(
            scroll_profile="reveal_moderate", ww=1080, wh=2340
        )
        nudge = nav._post_follow_likes_scroll_geometry(
            scroll_profile="reveal_nudge", ww=1080, wh=2340
        )
        self.assertEqual(nudge["scroll_profile"], "reveal_nudge")
        self.assertLess(nudge["scroll_distance_px"], moderate["scroll_distance_px"])

    def test_stabilize_suggested_overlay_scrolls_before_safe_xml_cell(self) -> None:
        device = mock.MagicMock()
        device.window_size.return_value = (1080, 2340)
        scroll_calls: list[int] = []
        probe_calls = [0]
        probe_seq = [
            {
                "reliable": True,
                "cell": {
                    "left": 0,
                    "top": 1972,
                    "bottom": 2332,
                    "center_x": 180,
                    "center_y": 2152,
                },
                "reason": "profile_tabs_grid_cell_estimate",
                "y_min_px": 1500,
            },
            {
                "reliable": True,
                "cell": {"left": 0, "top": 1972, "bottom": 2332, "center_x": 180, "center_y": 2152},
                "reason": "profile_tabs_grid_cell_estimate",
                "y_min_px": 1500,
            },
            {
                "reliable": True,
                "cell": {"left": 0, "top": 1972, "bottom": 2332, "center_x": 180, "center_y": 2152},
                "reason": "profile_tabs_grid_cell_estimate",
                "y_min_px": 1500,
            },
            {
                "reliable": True,
                "cell": {"left": 0, "top": 1972, "bottom": 2332, "center_x": 180, "center_y": 2152},
                "reason": "profile_tabs_grid_cell_estimate",
                "y_min_px": 1500,
            },
            {
                "reliable": True,
                "cell": {
                    "left": 0,
                    "top": 1000,
                    "bottom": 1360,
                    "center_x": 180,
                    "center_y": 1180,
                },
                "reason": "xml_thumbnail_top_left",
                "y_min_px": 900,
                "target_cell": "top_left",
            },
        ]

        def _visible_cell(*_a: object, **_k: object) -> dict[str, object]:
            idx = min(probe_calls[0], len(probe_seq) - 1)
            probe_calls[0] += 1
            return probe_seq[idx]

        def _swipe(*_a: object, **_k: object) -> dict[str, object]:
            scroll_calls.append(1)
            return {"swipe_ok": True}

        with mock.patch.object(
            nav, "_post_follow_likes_profile_scroll_swipe", side_effect=_swipe
        ), mock.patch.object(
            nav, "_post_follow_likes_visible_grid_cell_under_suggested",
            side_effect=_visible_cell,
        ), mock.patch.object(
            nav,
            "_post_follow_likes_run_top_left_xml_probe_sequence",
            side_effect=_probe_sequence_from_visible_fn(_visible_cell),
        ), mock.patch.object(
            nav,
            "_post_follow_likes_grid_ui_surface_hints",
            return_value={
                "suggested_for_you": True,
                "discover_people": False,
                "profile_tabs_visible": True,
            },
        ), mock.patch.object(nav, "log"), mock.patch.object(
            nav, "time"
        ) as tmock:
            tmock.perf_counter = time.perf_counter
            tmock.sleep = lambda *_a, **_k: None
            stab = nav._post_follow_likes_stabilize_grid_under_suggested_overlay(
                device,
                ui_hints={
                    "suggested_for_you": True,
                    "discover_people": False,
                    "profile_tabs_visible": True,
                },
                budget_deadline=time.perf_counter() + 5.0,
                ww=1080,
                wh=2340,
            )
        self.assertEqual(len(scroll_calls), 2)
        self.assertEqual(stab.get("scroll_attempts"), 2)
        self.assertTrue(stab.get("tap_safe"))
        self.assertEqual(stab.get("grid_exposure"), "stable")

    def test_stabilize_uses_reveal_moderate_when_top_left_already_safe(self) -> None:
        device = mock.MagicMock()
        device.window_size.return_value = (1080, 2340)
        scroll_profiles: list[str] = []

        def _swipe(*_a: object, **kw: object) -> dict[str, object]:
            scroll_profiles.append(str(kw.get("scroll_profile") or ""))
            return {"swipe_ok": True}

        with mock.patch.object(
            nav, "_post_follow_likes_profile_scroll_swipe", side_effect=_swipe
        ), mock.patch.object(
            nav, "_post_follow_likes_visible_grid_cell_under_suggested",
            return_value={
                "reliable": True,
                "cell": {
                    "left": 0,
                    "top": 1000,
                    "bottom": 1360,
                    "center_x": 180,
                    "center_y": 1180,
                },
                "reason": "xml_thumbnail_top_left",
                "y_min_px": 900,
                "target_cell": "top_left",
            },
        ), mock.patch.object(
            nav,
            "_post_follow_likes_grid_ui_surface_hints",
            return_value={"suggested_for_you": False, "profile_tabs_visible": True},
        ), mock.patch.object(nav, "log"), mock.patch.object(nav, "time") as tmock:
            tmock.perf_counter = time.perf_counter
            tmock.sleep = lambda *_a, **_k: None
            stab = nav._post_follow_likes_stabilize_grid_under_suggested_overlay(
                device,
                ui_hints={"suggested_for_you": True, "profile_tabs_visible": True},
                budget_deadline=time.perf_counter() + 5.0,
                ww=1080,
                wh=2340,
            )
        self.assertTrue(stab.get("tap_safe"))
        self.assertEqual(scroll_profiles, [])

    def test_stabilize_uses_moderate_then_nudge_profiles(self) -> None:
        device = mock.MagicMock()
        device.window_size.return_value = (1080, 2340)
        scroll_profiles: list[str] = []
        probe_calls = [0]
        probe_seq = [
            {
                "reliable": True,
                "cell": {"left": 0, "top": 1972, "bottom": 2332, "center_x": 180, "center_y": 2152},
                "reason": "profile_tabs_grid_cell_estimate",
                "y_min_px": 1500,
            },
            {
                "reliable": True,
                "cell": {"left": 0, "top": 1972, "bottom": 2332, "center_x": 180, "center_y": 2152},
                "reason": "profile_tabs_grid_cell_estimate",
                "y_min_px": 1500,
            },
            {
                "reliable": True,
                "cell": {"left": 0, "top": 1972, "bottom": 2332, "center_x": 180, "center_y": 2152},
                "reason": "profile_tabs_grid_cell_estimate",
                "y_min_px": 1500,
            },
            {
                "reliable": True,
                "cell": {"left": 0, "top": 1972, "bottom": 2332, "center_x": 180, "center_y": 2152},
                "reason": "profile_tabs_grid_cell_estimate",
                "y_min_px": 1500,
            },
            {
                "reliable": True,
                "cell": {
                    "left": 0,
                    "top": 1000,
                    "bottom": 1360,
                    "center_x": 180,
                    "center_y": 1180,
                },
                "reason": "xml_thumbnail_top_left",
                "y_min_px": 900,
            },
        ]

        def _visible_cell(*_a: object, **_k: object) -> dict[str, object]:
            idx = min(probe_calls[0], len(probe_seq) - 1)
            probe_calls[0] += 1
            return probe_seq[idx]

        def _swipe(*_a: object, **kw: object) -> dict[str, object]:
            scroll_profiles.append(str(kw.get("scroll_profile") or ""))
            return {"swipe_ok": True}

        with mock.patch.object(
            nav, "_post_follow_likes_profile_scroll_swipe", side_effect=_swipe
        ), mock.patch.object(
            nav, "_post_follow_likes_visible_grid_cell_under_suggested",
            side_effect=_visible_cell,
        ), mock.patch.object(
            nav,
            "_post_follow_likes_run_top_left_xml_probe_sequence",
            side_effect=_probe_sequence_from_visible_fn(_visible_cell),
        ), mock.patch.object(
            nav,
            "_post_follow_likes_grid_ui_surface_hints",
            return_value={"suggested_for_you": True, "profile_tabs_visible": True},
        ), mock.patch.object(nav, "log"), mock.patch.object(nav, "time") as tmock:
            tmock.perf_counter = time.perf_counter
            tmock.sleep = lambda *_a, **_k: None
            stab = nav._post_follow_likes_stabilize_grid_under_suggested_overlay(
                device,
                ui_hints={"suggested_for_you": True, "profile_tabs_visible": True},
                budget_deadline=time.perf_counter() + 5.0,
                ww=1080,
                wh=2340,
            )
        self.assertTrue(stab.get("tap_safe"))
        self.assertEqual(scroll_profiles, ["reveal_moderate", "reveal_nudge"])

    def test_stabilize_fail_closed_on_overscroll_without_second_long_scroll(self) -> None:
        device = mock.MagicMock()
        device.window_size.return_value = (1080, 2340)
        scroll_profiles: list[str] = []
        overscroll_cell = {
            "reliable": True,
            "cell": {"left": 0, "top": 200, "bottom": 560, "center_x": 180, "center_y": 380},
            "reason": "xml_thumbnail_top_left",
            "y_min_px": 900,
        }

        def _swipe(*_a: object, **kw: object) -> dict[str, object]:
            scroll_profiles.append(str(kw.get("scroll_profile") or ""))
            return {"swipe_ok": True}

        with mock.patch.object(
            nav, "_post_follow_likes_profile_scroll_swipe", side_effect=_swipe
        ), mock.patch.object(
            nav, "_post_follow_likes_visible_grid_cell_under_suggested",
            return_value=overscroll_cell,
        ), mock.patch.object(
            nav,
            "_post_follow_likes_grid_ui_surface_hints",
            return_value={"suggested_for_you": True, "profile_tabs_visible": True},
        ), mock.patch.object(nav, "log"), mock.patch.object(nav, "time") as tmock:
            tmock.perf_counter = time.perf_counter
            tmock.sleep = lambda *_a, **_k: None
            stab = nav._post_follow_likes_stabilize_grid_under_suggested_overlay(
                device,
                ui_hints={"suggested_for_you": True, "profile_tabs_visible": True},
                budget_deadline=time.perf_counter() + 5.0,
                ww=1080,
                wh=2340,
            )
        self.assertFalse(stab.get("tap_safe"))
        self.assertEqual(
            stab.get("tap_safe_reason"), "grid_reveal_overscrolled_top_left_post"
        )
        self.assertEqual(scroll_profiles, [])

    def test_stabilize_suggested_highlights_low_grid_scrolls_extra_before_tap(self) -> None:
        device = mock.MagicMock()
        device.window_size.return_value = (1080, 2340)
        scroll_calls: list[int] = []
        probe_calls = [0]
        low_cell = {
            "reliable": True,
            "cell": {
                "left": 0,
                "top": 1800,
                "bottom": 2160,
                "center_x": 180,
                "center_y": 1980,
            },
            "reason": "xml_thumbnail_top_left",
            "y_min_px": 1700,
        }
        stable_cell = {
            "reliable": True,
            "cell": {
                "left": 0,
                "top": 1180,
                "bottom": 1540,
                "center_x": 180,
                "center_y": 1360,
            },
            "reason": "xml_thumbnail_top_left",
            "y_min_px": 1000,
        }
        probe_seq = [low_cell, low_cell, low_cell, low_cell, low_cell, stable_cell]

        def _visible_cell(*_a: object, **_k: object) -> dict[str, object]:
            idx = min(probe_calls[0], len(probe_seq) - 1)
            probe_calls[0] += 1
            return probe_seq[idx]

        def _swipe(*_a: object, **_k: object) -> dict[str, object]:
            scroll_calls.append(1)
            return {"swipe_ok": True}

        with mock.patch.object(
            nav, "_post_follow_likes_profile_scroll_swipe", side_effect=_swipe
        ), mock.patch.object(
            nav,
            "_post_follow_likes_visible_grid_cell_under_suggested",
            side_effect=_visible_cell,
        ), mock.patch.object(
            nav,
            "_post_follow_likes_run_top_left_xml_probe_sequence",
            side_effect=_probe_sequence_from_visible_fn(_visible_cell),
        ), mock.patch.object(nav, "log") as log_fn, mock.patch.object(
            nav, "time"
        ) as tmock:
            tmock.perf_counter = time.perf_counter
            tmock.sleep = lambda *_a, **_k: None
            stab = nav._post_follow_likes_stabilize_grid_under_suggested_overlay(
                device,
                ui_hints={"suggested_for_you": True, "profile_tabs_visible": True},
                budget_deadline=time.perf_counter() + 5.0,
                ww=1080,
                wh=2340,
            )
        self.assertEqual(len(scroll_calls), 3)
        self.assertEqual(stab.get("scroll_attempts"), 3)
        self.assertTrue(stab.get("tap_safe"))
        self.assertEqual(stab.get("grid_exposure"), "stable")
        self.assertIn(
            "like_highlights_scroll_attempted",
            [call.args[1] for call in log_fn.call_args_list if len(call.args) > 1],
        )

    def test_stabilize_fail_closed_when_grid_stays_unreliable(self) -> None:
        device = mock.MagicMock()
        device.window_size.return_value = (1080, 2340)
        def _unreliable(*_a: object, **_k: object) -> dict[str, object]:
            return {"reliable": False, "reason": "no_xml_thumbnail_below_tabs"}

        with mock.patch.object(
            nav, "_post_follow_likes_profile_scroll_swipe",
            return_value={"swipe_ok": True},
        ), mock.patch.object(
            nav, "_post_follow_likes_visible_grid_cell_under_suggested",
            side_effect=_unreliable,
        ), mock.patch.object(
            nav,
            "_post_follow_likes_run_top_left_xml_probe_sequence",
            side_effect=_probe_sequence_from_visible_fn(_unreliable),
        ), mock.patch.object(
            nav,
            "_post_follow_likes_grid_ui_surface_hints",
            return_value={"suggested_for_you": True, "profile_tabs_visible": True},
        ), mock.patch.object(nav, "log"), mock.patch.object(
            nav, "time"
        ) as tmock:
            tmock.perf_counter = time.perf_counter
            tmock.sleep = lambda *_a, **_k: None
            stab = nav._post_follow_likes_stabilize_grid_under_suggested_overlay(
                device,
                ui_hints={"suggested_for_you": True, "profile_tabs_visible": True},
                budget_deadline=time.perf_counter() + 5.0,
                ww=1080,
                wh=2340,
            )
        self.assertFalse(stab.get("tap_safe"))
        self.assertEqual(
            stab.get("tap_safe_reason"),
            "top_left_post_not_detected_after_xml_and_vision_probe",
        )

    def test_stabilize_fail_closed_when_grid_never_exposed(self) -> None:
        device = mock.MagicMock()
        device.window_size.return_value = (1080, 2340)
        low_cell = {
            "reliable": True,
            "cell": {
                "left": 0,
                "top": 1800,
                "bottom": 2160,
                "center_x": 180,
                "center_y": 1980,
            },
            "reason": "xml_thumbnail_top_left",
            "y_min_px": 1200,
        }
        def _always_low(*_a: object, **_k: object) -> dict[str, object]:
            return low_cell

        with mock.patch.object(
            nav, "_post_follow_likes_profile_scroll_swipe",
            return_value={"swipe_ok": True},
        ), mock.patch.object(
            nav,
            "_post_follow_likes_visible_grid_cell_under_suggested",
            side_effect=_always_low,
        ), mock.patch.object(
            nav,
            "_post_follow_likes_run_top_left_xml_probe_sequence",
            side_effect=_probe_sequence_from_visible_fn(_always_low),
        ), mock.patch.object(
            nav,
            "_post_follow_likes_grid_ui_surface_hints",
            return_value={"suggested_for_you": True, "profile_tabs_visible": True},
        ), mock.patch.object(nav, "log"), mock.patch.object(
            nav, "time"
        ) as tmock:
            tmock.perf_counter = time.perf_counter
            tmock.sleep = lambda *_a, **_k: None
            stab = nav._post_follow_likes_stabilize_grid_under_suggested_overlay(
                device,
                ui_hints={"suggested_for_you": True, "profile_tabs_visible": True},
                budget_deadline=time.perf_counter() + 5.0,
                ww=1080,
                wh=2340,
            )
        self.assertFalse(stab.get("tap_safe"))
        self.assertEqual(stab.get("scroll_attempts"), 3)
        self.assertEqual(
            stab.get("tap_safe_reason"), "post_cell_too_low_for_safe_tap"
        )

    def test_top_left_xml_probe_after_moderate_found_tap_safe(self) -> None:
        device = mock.MagicMock()
        device.window_size.return_value = (1080, 2340)
        xml_meta = {
            "reliable": True,
            "cell": {
                "left": 0,
                "top": 1000,
                "bottom": 1360,
                "center_x": 180,
                "center_y": 1180,
            },
            "reason": "xml_thumbnail_top_left",
            "y_min_px": 900,
            "target_cell": "top_left",
        }
        with mock.patch.object(
            nav, "_post_follow_likes_probe_top_left_xml_cell_meta", return_value=xml_meta
        ), mock.patch.object(nav, "log"), mock.patch.object(nav, "time") as tmock:
            tmock.perf_counter = time.perf_counter
            tmock.sleep = lambda *_a, **_k: None
            meta, state = nav._post_follow_likes_run_top_left_xml_probe_sequence(
                device,
                ui_hints={"profile_tabs_visible": True},
                budget_deadline=time.perf_counter() + 2.0,
                ww=1080,
                wh=2340,
                after_reveal_scroll=True,
            )
        self.assertTrue(state.get("top_left_post_tap_safe"))
        self.assertEqual(meta.get("reason"), "xml_thumbnail_top_left")

    def test_top_left_xml_relaxed_probe_finds_top_left_when_standard_absent(self) -> None:
        device = mock.MagicMock()
        top_left_cell = {
            "left": 0,
            "top": 1000,
            "right": 360,
            "bottom": 1360,
            "center_x": 180,
            "center_y": 1180,
        }

        def _xml_probe(*_a: object, **kw: object) -> dict[str, object]:
            if bool(kw.get("relaxed_probe")):
                return {"reliable": True, "cell_count": 1, "cells": [top_left_cell]}
            return {"reliable": False, "cell_count": 0, "cells": []}

        with mock.patch.object(
            nav, "_followers_profile_tabs_bottom_y_px", return_value=(900, "tabs")
        ), mock.patch.object(
            nav, "_post_follow_likes_xml_grid_thumbnails_below_tabs", side_effect=_xml_probe
        ), mock.patch.object(
            nav, "_post_follow_likes_probe_top_left_vision_cell_meta"
        ) as vision_probe:
            meta = nav._post_follow_likes_probe_top_left_xml_cell_meta(
                device,
                ww=1080,
                wh=2340,
                budget_deadline=time.perf_counter() + 2.0,
                profile_tabs_visible=True,
            )
            state = nav._post_follow_likes_assess_reveal_cell_state(
                meta, ww=1080, wh=2340
            )

        self.assertTrue(meta.get("reliable"))
        self.assertEqual(meta.get("reason"), "xml_thumbnail_top_left_relaxed")
        self.assertEqual(meta.get("xml_candidate_count"), 0)
        self.assertEqual(meta.get("relaxed_candidate_count"), 1)
        self.assertTrue(state.get("top_left_post_tap_safe"))
        vision_probe.assert_not_called()

    def test_top_left_vision_fallback_finds_top_left_when_xml_absent(self) -> None:
        device = mock.MagicMock()
        vision_meta = {
            "reliable": True,
            "reason": "vision_thumbnail_top_left",
            "cell": {
                "left": 0,
                "top": 1000,
                "right": 360,
                "bottom": 1360,
                "center_x": 180,
                "center_y": 1180,
            },
            "y_min_px": 984,
            "target_cell": "top_left",
            "vision_fallback_attempted": True,
            "vision_cell_found": True,
        }

        with mock.patch.object(
            nav, "_followers_profile_tabs_bottom_y_px", return_value=(900, "tabs")
        ), mock.patch.object(
            nav,
            "_post_follow_likes_xml_grid_thumbnails_below_tabs",
            return_value={"reliable": False, "cell_count": 0, "cells": []},
        ), mock.patch.object(
            nav, "_post_follow_likes_probe_top_left_vision_cell_meta", return_value=vision_meta
        ):
            meta = nav._post_follow_likes_probe_top_left_xml_cell_meta(
                device,
                ww=1080,
                wh=2340,
                budget_deadline=time.perf_counter() + 2.0,
                profile_tabs_visible=True,
            )
            state = nav._post_follow_likes_assess_reveal_cell_state(
                meta, ww=1080, wh=2340
            )

        self.assertTrue(meta.get("reliable"))
        self.assertEqual(meta.get("reason"), "vision_thumbnail_top_left")
        self.assertTrue(meta.get("vision_fallback_attempted"))
        self.assertTrue(meta.get("vision_cell_found"))
        self.assertTrue(state.get("top_left_post_tap_safe"))

    def test_top_left_xml_and_vision_absent_fail_closed_reason(self) -> None:
        device = mock.MagicMock()
        with mock.patch.object(
            nav, "_followers_profile_tabs_bottom_y_px", return_value=(900, "tabs")
        ), mock.patch.object(
            nav,
            "_post_follow_likes_xml_grid_thumbnails_below_tabs",
            return_value={"reliable": False, "cell_count": 0, "cells": []},
        ), mock.patch.object(
            nav,
            "_post_follow_likes_probe_top_left_vision_cell_meta",
            return_value={
                "reliable": False,
                "reason": "vision_thumbnail_top_left_not_found",
                "cell": None,
                "vision_fallback_attempted": True,
                "vision_cell_found": False,
            },
        ):
            meta = nav._post_follow_likes_probe_top_left_xml_cell_meta(
                device,
                ww=1080,
                wh=2340,
                budget_deadline=time.perf_counter() + 2.0,
                profile_tabs_visible=True,
            )
            state = nav._post_follow_likes_assess_reveal_cell_state(
                meta, ww=1080, wh=2340
            )

        self.assertFalse(meta.get("reliable"))
        self.assertEqual(
            meta.get("reason"), "top_left_post_not_detected_after_xml_and_vision_probe"
        )
        self.assertFalse(state.get("top_left_post_tap_safe"))
        self.assertEqual(
            state.get("tap_safe_reason"),
            "top_left_post_not_detected_after_xml_and_vision_probe",
        )

    def test_legacy_visual_top_left_safe_opens_only_top_left_with_viewer_confirmed(self) -> None:
        device = mock.MagicMock()
        device.window_size.return_value = (1080, 2340)
        log_events: list[str] = []

        def _fake_log(_level: str, event: str, **_kw: object) -> None:
            log_events.append(str(event))

        def _fake_screenshot(_d: object, path: str) -> None:
            from PIL import Image

            Image.new("RGB", (1080, 2340), "black").save(path)

        with mock.patch.object(
            nav, "visual_target_profile_lock_verify", return_value={"ok": True}
        ), mock.patch.object(
            nav,
            "_followers_current_pkg_activity",
            return_value={"current_activity": "profile", "current_package": "pkg"},
        ), mock.patch.object(
            nav,
            "_post_follow_likes_grid_ui_surface_hints",
            return_value={
                "profile_tabs_visible": True,
                "suggested_for_you": False,
                "discover_people": False,
            },
        ), mock.patch.object(
            nav, "_followers_profile_tabs_bottom_y_px", return_value=(900, "tabs")
        ), mock.patch.object(
            nav, "screenshot", side_effect=_fake_screenshot
        ) as shot, mock.patch.object(
            nav,
            "_post_follow_dynamic_first_row_search_y_min_layout",
            return_value=(984, "profile_tabs_bottom", 900, 84),
        ) as layout, mock.patch.object(
            nav,
            "_dynamic_first_post_grid_row_from_image",
            return_value={
                "ok": True,
                "first_row_top": 1000,
                "first_row_bottom": 1360,
                "solid_count": 1,
                "cell_h": 360,
            },
        ), mock.patch.object(
            nav, "_visual_image_cell_luma_variance", return_value=180.0
        ), mock.patch.object(
            nav,
            "_visual_wait_post_viewer_opened_after_tap",
            return_value={
                "post_detected": True,
                "detect_reason": "like_unlike_ui",
                "viewer_detect_path": "phase_a_like_unlike_fast",
                "viewer_detect_total_ms": 120.0,
            },
        ) as viewer_wait, mock.patch.object(nav, "log", side_effect=_fake_log), mock.patch.object(
            nav, "time"
        ) as tmock:
            tmock.perf_counter = time.perf_counter
            tmock.time = time.time
            tmock.sleep = lambda *_a, **_k: None
            out = nav._post_follow_likes_open_top_left_legacy_visual_safe(
                device,
                pkg="pkg",
                source_profile_username="ct",
                expected_follower_username="cand",
                visual_candidate_id="vc-1",
                post_index=0,
                likes_perf_phase_t0=time.perf_counter(),
            )

        self.assertTrue(out.get("ok"))
        self.assertTrue(out.get("post_detected"))
        self.assertEqual(out.get("open_strategy"), "vision_open_top_left_legacy_safe")
        device.click.assert_called_once_with(180, 1180)
        shot.assert_called_once()
        viewer_wait.assert_called_once()
        self.assertEqual(layout.call_args.kwargs.get("known_tabs_bottom_y_px"), 900)
        self.assertEqual(layout.call_args.kwargs.get("known_tabs_bottom_source"), "tabs")
        for event in {
            "legacy_safe_timing_profile_lock_completed",
            "legacy_safe_timing_ui_hints_completed",
            "legacy_safe_timing_tabs_bottom_completed",
            "legacy_safe_timing_screenshot_capture_completed",
            "legacy_safe_timing_pil_open_completed",
            "legacy_safe_timing_dynamic_row_scan_completed",
            "legacy_safe_timing_candidate_selection_completed",
            "post_like_surface_context_reused",
        }:
            self.assertIn(event, log_events)


    def test_legacy_safe_viewer_trusted_exact_like_proof_is_stashed(self) -> None:
        device = mock.MagicMock()
        device.window_size.return_value = (1080, 2340)
        logs: list[tuple[str, dict[str, object]]] = []

        def _fake_log(_level: str, event: str, **kw: object) -> None:
            logs.append((str(event), dict(kw)))

        def _fake_screenshot(_d: object, path: str) -> None:
            from PIL import Image

            Image.new("RGB", (1080, 2340), "black").save(path)

        nav._clear_post_follow_open_like_proof_stash()
        try:
            with mock.patch.object(
                nav, "visual_target_profile_lock_verify", return_value={"ok": True}
            ), mock.patch.object(
                nav,
                "_followers_current_pkg_activity",
                return_value={"current_activity": "profile", "current_package": "pkg"},
            ), mock.patch.object(
                nav,
                "_post_follow_likes_grid_ui_surface_hints",
                return_value={
                    "profile_tabs_visible": True,
                    "suggested_for_you": False,
                    "discover_people": False,
                },
            ), mock.patch.object(
                nav, "_followers_profile_tabs_bottom_y_px", return_value=(900, "tabs")
            ), mock.patch.object(
                nav, "screenshot", side_effect=_fake_screenshot
            ), mock.patch.object(
                nav,
                "_post_follow_dynamic_first_row_search_y_min_layout",
                return_value=(984, "profile_tabs_bottom", 900, 84),
            ), mock.patch.object(
                nav,
                "_dynamic_first_post_grid_row_from_image",
                return_value={
                    "ok": True,
                    "first_row_top": 1000,
                    "first_row_bottom": 1360,
                    "solid_count": 1,
                    "cell_h": 360,
                },
            ), mock.patch.object(
                nav, "_visual_image_cell_luma_variance", return_value=180.0
            ), mock.patch.object(
                nav,
                "_visual_wait_post_viewer_opened_after_tap",
                return_value={
                    "post_detected": True,
                    "detect_reason": "like_unlike_ui",
                    "viewer_detect_path": "phase_a2_exact_like_desc_fast",
                    "viewer_detect_a2_exact_desc_signal": "ui_description_exact_like",
                    "viewer_detect_exact_desc_guard_result": "posts_bar_and_not_profile_grid",
                    "posts_action_bar": True,
                    "viewer_detect_total_ms": 120.0,
                },
            ), mock.patch.object(nav, "log", side_effect=_fake_log), mock.patch.object(
                nav, "time"
            ) as tmock:
                tmock.perf_counter = time.perf_counter
                tmock.time = time.time
                tmock.sleep = lambda *_a, **_k: None
                out = nav._post_follow_likes_open_top_left_legacy_visual_safe(
                    device,
                    pkg="pkg",
                    source_profile_username="ct",
                    expected_follower_username="cand",
                    visual_candidate_id="vc-1",
                    post_index=0,
                    likes_perf_phase_t0=time.perf_counter(),
                )

            self.assertTrue(out.get("ok"))
            ok, stash, _age_ms, reject = nav._validate_post_follow_open_like_proof_stash(
                source_profile_username="ct",
                expected_follower_username="cand",
            )
            self.assertTrue(ok, reject)
            self.assertEqual(stash.get("proof_method"), "ui_description_exact_like")
            self.assertEqual(stash.get("proof_source"), "legacy_safe")
            stash_logs = [
                kw
                for event, kw in logs
                if event == "post_follow_like_open_proof_stashed_from_legacy_safe"
            ]
            self.assertTrue(stash_logs)
            self.assertTrue(stash_logs[-1].get("trusted"))
            self.assertEqual(stash_logs[-1].get("signal"), "ui_description_exact_like")
        finally:
            nav._clear_post_follow_open_like_proof_stash()

    def test_already_liked_precheck_reuses_trusted_open_proof(self) -> None:
        device = mock.MagicMock()
        logs: list[tuple[str, dict[str, object]]] = []
        nav._clear_post_follow_open_like_proof_stash()
        try:
            nav._stash_post_follow_open_like_proof(
                {
                    "post_detected": True,
                    "viewer_detect_path": "phase_a2_exact_like_desc_fast",
                    "viewer_detect_a2_exact_desc_signal": "ui_description_exact_like",
                    "viewer_detect_exact_desc_guard_result": "posts_bar_and_not_profile_grid",
                    "posts_action_bar": True,
                },
                source_profile_username="ct",
                follower_username="cand",
                proof_source="legacy_safe",
            )
            with mock.patch.object(
                nav,
                "_followers_current_pkg_activity",
                return_value={"current_activity": "post", "current_package": "pkg"},
            ), mock.patch.object(
                nav,
                "_log_rejected_broad_liked_semantic_candidates",
                side_effect=AssertionError("hierarchy fallback should be skipped"),
            ), mock.patch.object(
                nav, "log", side_effect=lambda level, event, **kw: logs.append((str(event), dict(kw)))
            ):
                out = nav.visual_post_already_liked(
                    device,
                    source_profile_username="ct",
                    expected_follower_username="cand",
                    post_follow_primary_precheck=True,
                )

            self.assertFalse(out.get("already_liked"))
            self.assertTrue(out.get("open_proof_reused"))
            self.assertEqual(out.get("detection_method"), "post_follow_open_like_proof_reuse")
            reuse_logs = [
                kw for event, kw in logs if event == "post_follow_like_open_proof_reused"
            ]
            self.assertTrue(reuse_logs)
            self.assertTrue(reuse_logs[-1].get("reused"))
            self.assertTrue(reuse_logs[-1].get("trusted"))
        finally:
            nav._clear_post_follow_open_like_proof_stash()

    def test_open_proof_absent_non_trusted_expired_or_mismatch_falls_back_to_hierarchy(self) -> None:
        cases: list[tuple[str, dict[str, object] | None, str, str]] = [
            ("absent", None, "ct", "cand"),
            (
                "non_trusted",
                {
                    "viewer_detect_path": "phase_a2_exact_like_desc_fast",
                    "proof_method": "ui_description_exact_comment",
                    "posts_action_bar": True,
                    "stashed_at_monotonic": time.perf_counter(),
                    "source_profile_username": "ct",
                    "follower_username": "cand",
                    "proof_source": "legacy_safe",
                },
                "ct",
                "cand",
            ),
            (
                "expired",
                {
                    "viewer_detect_path": "phase_a2_exact_like_desc_fast",
                    "proof_method": "ui_description_exact_like",
                    "posts_action_bar": True,
                    "stashed_at_monotonic": time.perf_counter() - 30.0,
                    "source_profile_username": "ct",
                    "follower_username": "cand",
                    "proof_source": "legacy_safe",
                },
                "ct",
                "cand",
            ),
            (
                "mismatch",
                {
                    "viewer_detect_path": "phase_a2_exact_like_desc_fast",
                    "proof_method": "ui_description_exact_like",
                    "posts_action_bar": True,
                    "stashed_at_monotonic": time.perf_counter(),
                    "source_profile_username": "other_ct",
                    "follower_username": "cand",
                    "proof_source": "legacy_safe",
                },
                "ct",
                "cand",
            ),
        ]
        for label, stash, source_username, follower_username in cases:
            with self.subTest(label=label):
                logs: list[tuple[str, dict[str, object]]] = []
                nav._clear_post_follow_open_like_proof_stash()
                if stash is not None:
                    nav._post_follow_open_like_proof_stash = dict(stash)
                try:
                    with mock.patch.object(
                        nav,
                        "_followers_current_pkg_activity",
                        return_value={"current_activity": "post", "current_package": "pkg"},
                    ), mock.patch.object(
                        nav,
                        "_log_rejected_broad_liked_semantic_candidates",
                        return_value='<node content-desc="Like" />',
                    ) as hierarchy_probe, mock.patch.object(
                        nav,
                        "_ui_post_viewer_action_button_liked_strict",
                        return_value=(False, "", 0.0, {}),
                    ), mock.patch.object(
                        nav,
                        "_ui_post_viewer_action_button_not_liked_strict",
                        return_value=(False, "", 0.0, {}),
                    ), mock.patch.object(
                        nav, "log", side_effect=lambda level, event, **kw: logs.append((str(event), dict(kw)))
                    ):
                        out = nav.visual_post_already_liked(
                            mock.MagicMock(),
                            source_profile_username=source_username,
                            expected_follower_username=follower_username,
                            post_follow_primary_precheck=True,
                        )

                    self.assertFalse(out.get("already_liked"))
                    self.assertEqual(out.get("detection_method"), "hierarchy_like_hint")
                    hierarchy_probe.assert_called_once()
                    reject_logs = [
                        kw
                        for event, kw in logs
                        if event == "post_follow_like_open_proof_reuse_rejected"
                    ]
                    self.assertTrue(reject_logs)
                    self.assertFalse(reject_logs[-1].get("reused"))
                    self.assertTrue(reject_logs[-1].get("reject_reason"))
                finally:
                    nav._clear_post_follow_open_like_proof_stash()

    def test_already_liked_true_still_blocks_tap_and_verify_still_required(self) -> None:
        device = mock.MagicMock()
        contract_ctx = mock.MagicMock()
        contract_ctx.current_state.value = "sheet_dismissed"
        with ExitStack() as stack:
            _patch_like_phase_common(stack, contract_ctx=contract_ctx)
            stack.enter_context(
                mock.patch.object(
                    nav,
                    "_post_follow_likes_open_top_left_legacy_visual_safe",
                    return_value={
                        "ok": True,
                        "post_detected": True,
                        "failure_reason": "",
                        "open_strategy": "vision_open_top_left_legacy_safe",
                        "tap_x": 180,
                        "tap_y": 1282,
                        "detect_reason": "like_unlike_ui",
                        "viewer_detect_path": "phase_a2_exact_like_desc_fast",
                        "likes_perf_post_open": {},
                    },
                )
            )
            already_liked = stack.enter_context(
                mock.patch.object(
                    nav,
                    "visual_post_already_liked",
                    return_value={
                        "already_liked": True,
                        "detection_method": "ui_description_unlike_exact",
                        "confidence": 0.94,
                        "semantic_like_state": "unlike",
                        "already_liked_decision_reason": "action_button_unlike_confirmed",
                    },
                )
            )
            like_open = stack.enter_context(mock.patch.object(nav, "visual_like_open_post"))
            verify = stack.enter_context(mock.patch.object(nav, "visual_verify_post_liked"))
            stack.enter_context(
                mock.patch.object(
                    nav, "visual_return_to_profile_from_post", return_value={"ok": True}
                )
            )
            out = nav.run_post_follow_post_likes_phase(
                device,
                pkg="com.instagram.android",
                source_profile_username="ct",
                follower_username="cand",
                visual_candidate_id="vc-1",
                follow_success_verified=True,
                follow_state_after="following",
                skipped_tap=False,
            )

        already_liked.assert_called_once()
        like_open.assert_not_called()
        verify.assert_not_called()
        self.assertEqual(out.get("phase_outcome"), "skipped")
        self.assertEqual(out.get("skipped_reason"), "likes_skipped_already_liked")

        with ExitStack() as stack:
            _patch_like_phase_common(stack, contract_ctx=contract_ctx)
            stack.enter_context(
                mock.patch.object(
                    nav,
                    "_post_follow_likes_open_top_left_legacy_visual_safe",
                    return_value={
                        "ok": True,
                        "post_detected": True,
                        "failure_reason": "",
                        "open_strategy": "vision_open_top_left_legacy_safe",
                        "tap_x": 180,
                        "tap_y": 1282,
                        "detect_reason": "like_unlike_ui",
                        "viewer_detect_path": "phase_a2_exact_like_desc_fast",
                        "likes_perf_post_open": {},
                    },
                )
            )
            stack.enter_context(
                mock.patch.object(
                    nav,
                    "visual_post_already_liked",
                    return_value={
                        "already_liked": False,
                        "detection_method": "post_follow_open_like_proof_reuse",
                        "confidence": 0.91,
                    },
                )
            )
            stack.enter_context(
                mock.patch.object(
                    nav,
                    "visual_like_open_post",
                    return_value={
                        "ok": True,
                        "already_liked": False,
                        "real_tap_sent": True,
                        "tap_x": 79,
                        "tap_y": 1891,
                        "confidence": 0.8,
                        "likes_perf_like": {"like_tap_dispatch_ms": 1.0},
                    },
                )
            )
            verify = stack.enter_context(
                mock.patch.object(
                    nav,
                    "visual_verify_post_liked",
                    return_value={
                        "liked_verified": True,
                        "verification_method": "visual_filled_heart_red_ratio_verify_post_tap_reuse",
                        "verify_attempts_count": 1,
                    },
                )
            )
            stack.enter_context(
                mock.patch.object(
                    nav, "visual_return_to_profile_from_post", return_value={"ok": True}
                )
            )
            out = nav.run_post_follow_post_likes_phase(
                device,
                pkg="com.instagram.android",
                source_profile_username="ct",
                follower_username="cand",
                visual_candidate_id="vc-1",
                follow_success_verified=True,
                follow_state_after="following",
                skipped_tap=False,
            )

        verify.assert_called_once()
        self.assertEqual(out.get("phase_outcome"), "success")
        self.assertEqual(out.get("liked_count"), 1)

    def test_post_follow_like_phase_resets_profile_like_counter_for_new_candidate(
        self,
    ) -> None:
        nav._VISUAL_POST_LIKE_TAPS_RECORDED = 1
        device = mock.MagicMock()
        contract_ctx = _like_phase_contract_ctx()
        logs: list[tuple[str, dict[str, object]]] = []
        with ExitStack() as stack:
            _patch_like_phase_common(stack, contract_ctx=contract_ctx)
            stack.enter_context(
                mock.patch.object(
                    nav.config, "VISUAL_POST_MAX_LIKES_PER_PROFILE", 1, create=True
                )
            )
            stack.enter_context(
                mock.patch.object(
                    nav,
                    "_post_follow_likes_open_top_left_legacy_visual_safe",
                    return_value={
                        "ok": True,
                        "post_detected": True,
                        "failure_reason": "",
                        "open_strategy": "vision_open_top_left_legacy_safe",
                        "tap_x": 180,
                        "tap_y": 1282,
                        "detect_reason": "like_unlike_ui",
                        "viewer_detect_path": "phase_a2_exact_like_desc_fast",
                        "likes_perf_post_open": {},
                    },
                )
            )
            stack.enter_context(
                mock.patch.object(
                    nav,
                    "visual_post_already_liked",
                    return_value={
                        "already_liked": False,
                        "detection_method": "post_follow_open_like_proof_reuse",
                        "confidence": 0.91,
                    },
                )
            )

            def _fake_like_open(*_args: object, **_kwargs: object) -> dict[str, object]:
                self.assertEqual(nav._VISUAL_POST_LIKE_TAPS_RECORDED, 0)
                nav._VISUAL_POST_LIKE_TAPS_RECORDED += 1
                return {
                    "ok": True,
                    "already_liked": False,
                    "real_tap_sent": True,
                    "tap_x": 79,
                    "tap_y": 1891,
                    "confidence": 0.8,
                    "likes_perf_like": {"like_tap_dispatch_ms": 1.0},
                }

            like_open = stack.enter_context(
                mock.patch.object(nav, "visual_like_open_post", side_effect=_fake_like_open)
            )
            verify = stack.enter_context(
                mock.patch.object(
                    nav,
                    "visual_verify_post_liked",
                    return_value={
                        "liked_verified": True,
                        "verification_method": "visual",
                        "verify_attempts_count": 1,
                    },
                )
            )
            stack.enter_context(
                mock.patch.object(
                    nav, "visual_return_to_profile_from_post", return_value={"ok": True}
                )
            )
            stack.enter_context(
                mock.patch.object(
                    nav,
                    "log",
                    side_effect=lambda _level, event, **kw: logs.append((str(event), dict(kw))),
                )
            )
            out = nav.run_post_follow_post_likes_phase(
                device,
                pkg="com.instagram.android",
                source_profile_username="ct",
                follower_username="new_cand",
                visual_candidate_id="vc-new",
                follow_success_verified=True,
                follow_state_after="following",
                skipped_tap=False,
                session_likes_used=0,
            )

        self.assertEqual(out.get("phase_outcome"), "success")
        like_open.assert_called_once()
        verify.assert_called_once()
        reset_logs = [
            kw for event, kw in logs if event == "post_follow_like_profile_counter_reset"
        ]
        self.assertTrue(reset_logs)
        self.assertEqual(reset_logs[-1]["candidate_username"], "new_cand")
        self.assertEqual(reset_logs[-1]["previous_profile_like_taps"], 1)
        self.assertEqual(reset_logs[-1]["new_profile_like_taps"], 0)
        self.assertEqual(reset_logs[-1]["max_likes_per_profile"], 1)

    def test_visual_like_max_still_blocks_second_tap_inside_same_profile(self) -> None:
        nav._VISUAL_POST_LIKE_TAPS_RECORDED = 1
        device = mock.MagicMock()
        with mock.patch.object(
            nav.config, "ENABLE_REAL_VISUAL_POST_LIKE", True, create=True
        ), mock.patch.object(
            nav.config, "VISUAL_POST_MAX_LIKES_PER_PROFILE", 1, create=True
        ), mock.patch.object(
            nav,
            "_followers_current_pkg_activity",
            return_value={"current_activity": "post", "current_package": "pkg"},
        ), mock.patch.object(nav, "screenshot") as screenshot, mock.patch.object(nav, "log"):
            out = nav.visual_like_open_post(
                device,
                source_profile_username="ct",
                expected_profile_context={"ok": True},
                post_opened_via_profile_grid=True,
                post_open_context={"ok": True, "post_detected": True},
                expected_follower_username="cand",
            )

        self.assertFalse(out.get("ok"))
        self.assertEqual(out.get("failure_reason"), "max_likes_per_profile")
        self.assertFalse(out.get("real_tap_sent"))
        screenshot.assert_not_called()

    def test_post_follow_total_likes_limit_still_blocks_after_profile_counter_reset(
        self,
    ) -> None:
        nav._VISUAL_POST_LIKE_TAPS_RECORDED = 1
        device = mock.MagicMock()
        logs: list[tuple[str, dict[str, object]]] = []
        with mock.patch.object(
            nav.config, "POST_FOLLOW_POST_LIKES_ENABLED", True, create=True
        ), mock.patch.object(
            nav.config, "ENABLE_REAL_VISUAL_POST_LIKE", True, create=True
        ), mock.patch.object(
            nav.config, "POST_FOLLOW_TOTAL_LIKES_LIMIT", 1, create=True
        ), mock.patch.object(
            nav, "visual_like_open_post"
        ) as like_open, mock.patch.object(
            nav,
            "log",
            side_effect=lambda _level, event, **kw: logs.append((str(event), dict(kw))),
        ):
            out = nav.run_post_follow_post_likes_phase(
                device,
                pkg="com.instagram.android",
                source_profile_username="ct",
                follower_username="cand",
                visual_candidate_id="vc-1",
                follow_success_verified=True,
                follow_state_after="following",
                skipped_tap=False,
                session_likes_used=1,
            )

        self.assertEqual(out.get("skipped_reason"), "likes_skipped_session_quota")
        like_open.assert_not_called()
        reset_logs = [
            kw for event, kw in logs if event == "post_follow_like_profile_counter_reset"
        ]
        self.assertTrue(reset_logs)
        self.assertEqual(reset_logs[-1]["previous_profile_like_taps"], 1)
        self.assertEqual(reset_logs[-1]["session_likes_used"], 1)
        self.assertEqual(reset_logs[-1]["session_likes_limit"], 1)

    def test_return_ct_reuses_post_back_ct_det_without_final_redump(self) -> None:
        device = mock.MagicMock()
        logs: list[tuple[str, dict[str, object]]] = []
        candidate_det = {
            "is_followers_list": True,
            "action_bar_title": "mobi_voyage",
            "open_detection_method": "own_unified_follow_list",
        }
        ct_det = {
            "is_followers_list": True,
            "action_bar_title": "reveaustral",
            "open_detection_method": "own_unified_follow_list",
        }
        fake_now = [100.0]
        detect_calls = {"count": 0}

        def _detect_followers_list(
            _d: object, *, source_profile_username: str
        ) -> dict[str, object]:
            detect_calls["count"] += 1
            if detect_calls["count"] < 4:
                return candidate_det
            # Simulate the expensive UIAutomator dump itself taking several seconds.
            # Reuse age is measured after the detection result exists, not before it.
            fake_now[0] += 3.8
            return ct_det

        def _verify_ct(
            _d: object,
            *,
            source_profile_username: str,
            follower_candidate_username: str | None = None,
            det: dict[str, object] | None = None,
        ) -> bool:
            return bool(det and det.get("action_bar_title") == source_profile_username)

        with mock.patch.object(
            nav,
            "detect_followers_list_screen",
            side_effect=_detect_followers_list,
        ) as detect, mock.patch.object(
            nav,
            "verify_followers_list_surface_is_ct_account",
            side_effect=_verify_ct,
        ), mock.patch(
            "navigation_engine.observe_instagram_state",
            return_value={
                "state": "FOLLOWERS_LIST",
                "confidence": 0.72,
                "reason": "det_is_followers_list",
                "xml_guess": "likely_profile",
            },
        ), mock.patch.object(
            nav, "verify_app_foreground", return_value=True
        ), mock.patch.object(
            nav, "log", side_effect=lambda level, event, **kw: logs.append((str(event), dict(kw)))
        ), mock.patch.object(
            nav.time, "perf_counter", side_effect=lambda: fake_now[0]
        ), mock.patch.object(nav.time, "sleep", side_effect=lambda *_a, **_k: None):
            ok, how, fail = nav.post_follow_controlled_return_to_followers_list(
                device,
                pkg="com.instagram.android",
                source_profile_username="reveaustral",
                follower_username="mobi_voyage",
                visual_candidate_id="vc-1",
                det={},
                max_rounds=1,
                compact_after_follow_verified_mute=True,
                compact_reason="follow_verified_mute_success",
            )

        self.assertTrue(ok)
        self.assertEqual(how, "compact_safe_back_then_list")
        self.assertIsNone(fail)
        self.assertEqual(detect.call_count, 4)
        device.press.assert_called_once_with("back")
        reused = [
            kw
            for event, kw in logs
            if event == "post_follow_return_ct_post_back_det_reused"
        ]
        self.assertTrue(reused)
        self.assertTrue(reused[-1].get("reused"))
        self.assertEqual(reused[-1].get("action_bar_title"), "reveaustral")
        self.assertNotIn(
            "post_follow_return_ct_post_back_det_reuse_rejected",
            [event for event, _kw in logs],
        )

    def test_return_ct_accepts_own_unified_list_with_stale_candidate_action_bar(self) -> None:
        device = mock.MagicMock()
        logs: list[tuple[str, dict[str, object]]] = []
        stale_list_det = {
            "is_followers_list": True,
            "own_unified_followers_list_detected": True,
            "open_detection_method": "own_unified_follow_list",
            "action_bar_title": "mobi_voyage",
            "follow_list_username_count": 8,
            "has_tab_layout": True,
            "signals": [
                "own_unified_followers_list_detected",
                "selected_followers_tab",
                "follow_list_username",
            ],
        }

        with mock.patch.object(
            nav, "detect_followers_list_screen", return_value=stale_list_det
        ), mock.patch.object(
            nav, "log", side_effect=lambda level, event, **kw: logs.append((str(event), dict(kw)))
        ):
            ok_return, how, fail = nav.post_follow_controlled_return_to_followers_list(
                device,
                pkg="com.instagram.android",
                source_profile_username="reveaustral",
                follower_username="mobi_voyage",
                visual_candidate_id="vc-1",
                det={},
                max_rounds=1,
                compact_after_follow_verified_mute=True,
                compact_reason="follow_verified_mute_success",
            )

        self.assertTrue(ok_return)
        self.assertEqual(how, "compact_initial_list_confirmed")
        self.assertIsNone(fail)
        self.assertIn(
            "post_follow_return_ct_accept_stale_candidate_action_bar_own_unified",
            [event for event, _kw in logs],
        )

    def test_return_ct_post_back_det_rejects_and_falls_back_to_final_confirm(self) -> None:
        cases: list[tuple[str, dict[str, object] | None, list[float] | None]] = [
            ("absent", {}, None),
            (
                "candidate_non_ct",
                {"is_followers_list": True, "action_bar_title": "mobi_voyage"},
                None,
            ),
            (
                "stale",
                {"is_followers_list": True, "action_bar_title": "reveaustral"},
                [100.0, 102.0],
            ),
            (
                "ambiguous",
                {"is_followers_list": False, "action_bar_title": "reveaustral"},
                None,
            ),
        ]
        for label, post_back_det, perf_sequence in cases:
            with self.subTest(label=label):
                device = mock.MagicMock()
                logs: list[tuple[str, dict[str, object]]] = []
                candidate_det = {
                    "is_followers_list": True,
                    "action_bar_title": "mobi_voyage",
                    "open_detection_method": "own_unified_follow_list",
                }
                ct_det = {
                    "is_followers_list": True,
                    "action_bar_title": "reveaustral",
                    "open_detection_method": "own_unified_follow_list",
                }

                def _verify_ct(
                    _d: object,
                    *,
                    source_profile_username: str,
                    follower_candidate_username: str | None = None,
                    det: dict[str, object] | None = None,
                ) -> bool:
                    return bool(det and det.get("action_bar_title") == source_profile_username)

                contexts: list[object] = [
                    mock.patch.object(nav.time, "sleep", side_effect=lambda *_a, **_k: None)
                ]
                if perf_sequence is not None:
                    contexts.append(
                        mock.patch.object(
                            nav.time,
                            "perf_counter",
                            side_effect=perf_sequence,
                        )
                    )
                with ExitStack() as stack:
                    for ctx in contexts:
                        stack.enter_context(ctx)
                    detect = stack.enter_context(
                        mock.patch.object(
                            nav,
                            "detect_followers_list_screen",
                            side_effect=[
                                candidate_det,
                                candidate_det,
                                candidate_det,
                                post_back_det or {},
                                ct_det,
                            ],
                        )
                    )
                    stack.enter_context(
                        mock.patch.object(
                            nav,
                            "verify_followers_list_surface_is_ct_account",
                            side_effect=_verify_ct,
                        )
                    )
                    stack.enter_context(
                        mock.patch(
                            "navigation_engine.observe_instagram_state",
                            return_value={
                                "state": "FOLLOWERS_LIST",
                                "confidence": 0.72,
                                "reason": "det_is_followers_list",
                                "xml_guess": "likely_profile",
                            },
                        )
                    )
                    stack.enter_context(mock.patch.object(nav, "verify_app_foreground", return_value=True))
                    stack.enter_context(
                        mock.patch.object(
                            nav,
                            "log",
                            side_effect=lambda level, event, **kw: logs.append((str(event), dict(kw))),
                        )
                    )
                    ok, how, fail = nav.post_follow_controlled_return_to_followers_list(
                        device,
                        pkg="com.instagram.android",
                        source_profile_username="reveaustral",
                        follower_username="mobi_voyage",
                        visual_candidate_id="vc-1",
                        det={},
                        max_rounds=1,
                        compact_after_follow_verified_mute=True,
                        compact_reason="follow_verified_mute_success",
                    )

                self.assertTrue(ok)
                self.assertEqual(how, "compact_safe_back_then_list")
                self.assertIsNone(fail)
                self.assertEqual(detect.call_count, 5)
                rejected = [
                    kw
                    for event, kw in logs
                    if event == "post_follow_return_ct_post_back_det_reuse_rejected"
                ]
                self.assertTrue(rejected)
                self.assertFalse(rejected[-1].get("reused"))
                self.assertTrue(rejected[-1].get("reject_reason"))

    def test_return_ct_noncompact_path_does_not_use_post_back_det_reuse(self) -> None:
        device = mock.MagicMock()
        logs: list[tuple[str, dict[str, object]]] = []
        ct_det = {
            "is_followers_list": True,
            "action_bar_title": "reveaustral",
            "open_detection_method": "own_unified_follow_list",
        }

        with mock.patch.object(
            nav, "detect_followers_list_screen", return_value=ct_det
        ), mock.patch.object(
            nav, "verify_followers_list_surface_is_ct_account", return_value=True
        ), mock.patch.object(
            nav, "log", side_effect=lambda level, event, **kw: logs.append((str(event), dict(kw)))
        ):
            ok, how, fail = nav.post_follow_controlled_return_to_followers_list(
                device,
                pkg="com.instagram.android",
                source_profile_username="reveaustral",
                follower_username="mobi_voyage",
                visual_candidate_id="vc-1",
                det={},
                max_rounds=2,
                compact_after_follow_verified_mute=False,
            )

        self.assertTrue(ok)
        self.assertEqual(how, "already_on_followers_list")
        self.assertIsNone(fail)
        self.assertNotIn(
            "post_follow_return_ct_post_back_det_reused",
            [event for event, _kw in logs],
        )

    def test_legacy_safe_ui_hints_lightweight_skips_mute_sheet_probe(self) -> None:
        class _Selector:
            def __init__(self, exists: bool) -> None:
                self._exists = exists

            def exists(self, timeout: float = 0.0) -> bool:
                return self._exists

        device = mock.MagicMock()

        def _selector(**kw: object) -> _Selector:
            if kw.get("textContains") == "Suggested for you":
                return _Selector(True)
            if kw.get("textContains") == "Discover people":
                return _Selector(True)
            if kw.get("resourceIdMatches") == r".*:id/profile_tab_layout":
                return _Selector(True)
            return _Selector(False)

        device.side_effect = _selector
        previous_ctx = getattr(nav, "_LEGACY_SAFE_TIMING_CONTEXT", None)
        nav._LEGACY_SAFE_TIMING_CONTEXT = {
            "visual_candidate_id": "vc-1",
            "source_profile_username": "ct",
            "follower_username": "cand",
            "post_index": 0,
            "attempt_label": "legacy_safe_attempt_test",
            "known_previous_signals": {},
        }
        log_calls: list[dict[str, object]] = []

        def _fake_log(_level: str, event: str, **kw: object) -> None:
            if event in {
                "legacy_safe_ui_hints_probe_started",
                "legacy_safe_ui_hints_probe_completed",
            }:
                log_calls.append({"event": event, **kw})

        try:
            with mock.patch.object(
                nav, "_mute_engine_v2_detect_sheet_level"
            ) as sheet_probe, mock.patch.object(nav, "log", side_effect=_fake_log):
                hints = nav._post_follow_likes_grid_ui_surface_hints(device)
        finally:
            nav._LEGACY_SAFE_TIMING_CONTEXT = previous_ctx

        sheet_probe.assert_not_called()
        self.assertTrue(hints.get("profile_tabs_visible"))
        self.assertTrue(hints.get("suggested_for_you"))
        self.assertTrue(hints.get("discover_people"))
        self.assertTrue(log_calls)
        for call in log_calls:
            self.assertEqual(call.get("hint_mode"), "legacy_safe_lightweight")
            self.assertTrue(call.get("mute_sheet_level_skipped"))
            self.assertEqual(
                call.get("skipped_reason"),
                "not_needed_for_legacy_safe_like_open",
            )

    def test_legacy_safe_ui_hints_critical_rid_skips_overlay_probes(self) -> None:
        class _Selector:
            def __init__(self, exists: bool) -> None:
                self._exists = exists

            def exists(self, timeout: float = 0.0) -> bool:
                return self._exists

        device = mock.MagicMock()
        calls: list[dict[str, object]] = []

        def _selector(**kw: object) -> _Selector:
            calls.append(dict(kw))
            if kw.get("resourceIdMatches") == r".*:id/profile_tabs_container":
                return _Selector(True)
            if "textContains" in kw:
                self.fail("overlay probe should not run after critical RID")
            return _Selector(False)

        device.side_effect = _selector
        previous_ctx = getattr(nav, "_LEGACY_SAFE_TIMING_CONTEXT", None)
        nav._LEGACY_SAFE_TIMING_CONTEXT = {
            "visual_candidate_id": "vc-1",
            "source_profile_username": "ct",
            "follower_username": "cand",
            "post_index": 0,
            "attempt_label": "legacy_safe_attempt_test",
            "known_previous_signals": {},
        }
        critical_logs: list[dict[str, object]] = []
        completed_logs: list[dict[str, object]] = []

        def _fake_log(_level: str, event: str, **kw: object) -> None:
            if event == "legacy_safe_ui_hints_critical_fast_path":
                critical_logs.append(dict(kw))
            if event == "legacy_safe_ui_hints_probe_completed":
                completed_logs.append(dict(kw))

        try:
            with mock.patch.object(
                nav, "_mute_engine_v2_detect_sheet_level"
            ) as sheet_probe, mock.patch.object(nav, "log", side_effect=_fake_log):
                hints = nav._post_follow_likes_grid_ui_surface_hints(device)
        finally:
            nav._LEGACY_SAFE_TIMING_CONTEXT = previous_ctx

        sheet_probe.assert_not_called()
        self.assertEqual(
            calls,
            [{"resourceIdMatches": r".*:id/profile_tabs_container"}],
        )
        self.assertTrue(hints.get("profile_tabs_visible"))
        self.assertFalse(hints.get("suggested_for_you"))
        self.assertFalse(hints.get("discover_people"))
        self.assertTrue(critical_logs)
        self.assertTrue(critical_logs[-1].get("overlay_probes_skipped"))
        self.assertFalse(critical_logs[-1].get("fallback_used"))
        self.assertTrue(completed_logs[-1].get("overlay_probes_skipped"))

    def test_tabs_bottom_authoritative_rid_short_circuits_when_safe(self) -> None:
        device = mock.MagicMock()
        device.window_size.return_value = (1080, 2340)
        calls: list[dict[str, object]] = []
        log_calls: list[dict[str, object]] = []

        def _selector(**kw: object) -> _TabsSelector:
            calls.append(dict(kw))
            if kw.get("resourceIdMatches") == r".*:id/profile_tabs_container":
                return _TabsSelector([_TabsNode(_tabs_bounds(900, 961))])
            if kw.get("resourceIdMatches") == r".*:id/profile_tab_layout":
                self.fail("fallback probe should not run after safe authoritative RID")
            return _TabsSelector([])

        def _fake_log(_level: str, event: str, **kw: object) -> None:
            if event == "legacy_safe_tabs_bottom_authoritative_short_circuit":
                log_calls.append(dict(kw))

        previous_ctx = getattr(nav, "_LEGACY_SAFE_TIMING_CONTEXT", None)
        nav._LEGACY_SAFE_TIMING_CONTEXT = {
            "visual_candidate_id": "vc-1",
            "source_profile_username": "ct",
            "follower_username": "cand",
            "post_index": 0,
            "attempt_label": "legacy_safe_attempt_test",
            "stable_key": "stable-tabs",
            "profile_lock_ok": True,
            "profile_tabs_visible": True,
        }
        try:
            with mock.patch.object(nav, "log", side_effect=_fake_log):
                device.side_effect = _selector
                tabs_bottom, source = nav._followers_profile_tabs_bottom_y_px(
                    device, window_h=2340
                )
        finally:
            nav._LEGACY_SAFE_TIMING_CONTEXT = previous_ctx

        self.assertEqual((tabs_bottom, source), (961, "resourceId:profile_tabs_container"))
        self.assertEqual(len(calls), 1)
        self.assertTrue(log_calls)
        self.assertTrue(log_calls[-1].get("short_circuit_used"))
        self.assertEqual(log_calls[-1].get("reason"), "authoritative_rid_bounds_safe")

    def test_tabs_bottom_authoritative_rid_absent_uses_full_fallback(self) -> None:
        device = mock.MagicMock()
        device.window_size.return_value = (1080, 2340)
        calls: list[dict[str, object]] = []

        def _selector(**kw: object) -> _TabsSelector:
            calls.append(dict(kw))
            if kw.get("resourceIdMatches") == r".*:id/profile_tab_layout":
                return _TabsSelector([_TabsNode(_tabs_bounds(900, 960))])
            return _TabsSelector([])

        previous_ctx = getattr(nav, "_LEGACY_SAFE_TIMING_CONTEXT", None)
        nav._LEGACY_SAFE_TIMING_CONTEXT = {
            "stable_key": "stable-tabs",
            "profile_lock_ok": True,
            "profile_tabs_visible": True,
        }
        try:
            with mock.patch.object(nav, "log"):
                device.side_effect = _selector
                tabs_bottom, source = nav._followers_profile_tabs_bottom_y_px(
                    device, window_h=2340
                )
        finally:
            nav._LEGACY_SAFE_TIMING_CONTEXT = previous_ctx

        self.assertEqual((tabs_bottom, source), (960, "resourceId:profile_tab_layout"))
        self.assertIn(
            {"resourceIdMatches": r".*:id/profile_tab_layout"},
            calls,
        )

    def test_tabs_bottom_authoritative_rid_ambiguous_uses_full_fallback(self) -> None:
        device = mock.MagicMock()
        device.window_size.return_value = (1080, 2340)
        calls: list[dict[str, object]] = []
        log_calls: list[dict[str, object]] = []

        def _selector(**kw: object) -> _TabsSelector:
            calls.append(dict(kw))
            if kw.get("resourceIdMatches") == r".*:id/profile_tabs_container":
                return _TabsSelector(
                    [
                        _TabsNode(_tabs_bounds(850, 930)),
                        _TabsNode(_tabs_bounds(900, 950)),
                    ]
                )
            if kw.get("resourceIdMatches") == r".*:id/profile_tab_layout":
                return _TabsSelector([_TabsNode(_tabs_bounds(900, 960))])
            return _TabsSelector([])

        def _fake_log(_level: str, event: str, **kw: object) -> None:
            if event == "legacy_safe_tabs_bottom_authoritative_short_circuit":
                log_calls.append(dict(kw))

        previous_ctx = getattr(nav, "_LEGACY_SAFE_TIMING_CONTEXT", None)
        nav._LEGACY_SAFE_TIMING_CONTEXT = {
            "stable_key": "stable-tabs",
            "profile_lock_ok": True,
            "profile_tabs_visible": True,
        }
        try:
            with mock.patch.object(nav, "log", side_effect=_fake_log):
                device.side_effect = _selector
                tabs_bottom, source = nav._followers_profile_tabs_bottom_y_px(
                    device, window_h=2340
                )
        finally:
            nav._LEGACY_SAFE_TIMING_CONTEXT = previous_ctx

        self.assertEqual((tabs_bottom, source), (960, "resourceId:profile_tab_layout"))
        self.assertIn(
            {"resourceIdMatches": r".*:id/profile_tab_layout"},
            calls,
        )
        self.assertTrue(log_calls)
        self.assertFalse(log_calls[-1].get("short_circuit_used"))
        self.assertEqual(log_calls[-1].get("reason"), "authoritative_rid_ambiguous")

    def test_tabs_bottom_authoritative_rid_unsafe_uses_full_fallback(self) -> None:
        device = mock.MagicMock()
        device.window_size.return_value = (1080, 2340)
        calls: list[dict[str, object]] = []

        def _selector(**kw: object) -> _TabsSelector:
            calls.append(dict(kw))
            if kw.get("resourceIdMatches") == r".*:id/profile_tabs_container":
                return _TabsSelector([_TabsNode(_tabs_bounds(5, 80))])
            if kw.get("resourceIdMatches") == r".*:id/profile_tab_layout":
                return _TabsSelector([_TabsNode(_tabs_bounds(900, 960))])
            return _TabsSelector([])

        previous_ctx = getattr(nav, "_LEGACY_SAFE_TIMING_CONTEXT", None)
        nav._LEGACY_SAFE_TIMING_CONTEXT = {
            "stable_key": "stable-tabs",
            "profile_lock_ok": True,
            "profile_tabs_visible": True,
        }
        try:
            with mock.patch.object(nav, "log"):
                device.side_effect = _selector
                tabs_bottom, source = nav._followers_profile_tabs_bottom_y_px(
                    device, window_h=2340
                )
        finally:
            nav._LEGACY_SAFE_TIMING_CONTEXT = previous_ctx

        self.assertEqual((tabs_bottom, source), (960, "resourceId:profile_tab_layout"))
        self.assertIn(
            {"resourceIdMatches": r".*:id/profile_tab_layout"},
            calls,
        )

    def test_legacy_visual_top_left_safe_refuses_low_variance(self) -> None:
        device = mock.MagicMock()
        device.window_size.return_value = (1080, 2340)

        def _fake_screenshot(_d: object, path: str) -> None:
            from PIL import Image

            Image.new("RGB", (1080, 2340), "black").save(path)

        with mock.patch.object(
            nav, "visual_target_profile_lock_verify", return_value={"ok": True}
        ), mock.patch.object(
            nav,
            "_followers_current_pkg_activity",
            return_value={"current_activity": "profile", "current_package": "pkg"},
        ), mock.patch.object(
            nav,
            "_post_follow_likes_grid_ui_surface_hints",
            return_value={
                "profile_tabs_visible": True,
                "suggested_for_you": False,
                "discover_people": False,
            },
        ), mock.patch.object(
            nav, "_followers_profile_tabs_bottom_y_px", return_value=(900, "tabs")
        ), mock.patch.object(
            nav, "screenshot", side_effect=_fake_screenshot
        ), mock.patch.object(
            nav,
            "_post_follow_dynamic_first_row_search_y_min_layout",
            return_value=(984, "profile_tabs_bottom", 900, 84),
        ), mock.patch.object(
            nav,
            "_dynamic_first_post_grid_row_from_image",
            return_value={
                "ok": True,
                "first_row_top": 1000,
                "first_row_bottom": 1360,
                "solid_count": 1,
                "cell_h": 360,
            },
        ), mock.patch.object(
            nav, "_visual_image_cell_luma_variance", return_value=12.0
        ), mock.patch.object(nav, "log"), mock.patch.object(nav, "time") as tmock:
            tmock.perf_counter = time.perf_counter
            tmock.time = time.time
            tmock.sleep = lambda *_a, **_k: None
            out = nav._post_follow_likes_open_top_left_legacy_visual_safe(
                device,
                pkg="pkg",
                source_profile_username="ct",
                expected_follower_username="cand",
                visual_candidate_id="vc-1",
                post_index=0,
                likes_perf_phase_t0=time.perf_counter(),
            )

        self.assertFalse(out.get("ok"))
        self.assertEqual(
            out.get("failure_reason"), "legacy_visual_top_left_variance_insufficient"
        )
        device.click.assert_not_called()

    def test_top_left_xml_probe_retries_before_fail_on_estimate(self) -> None:
        device = mock.MagicMock()
        device.window_size.return_value = (1080, 2340)
        probe_calls = [0]

        def _probe_meta(*_a: object, **_k: object) -> dict[str, object]:
            probe_calls[0] += 1
            if probe_calls[0] >= 2:
                return {
                    "reliable": True,
                    "cell": {
                        "left": 0,
                        "top": 1000,
                        "bottom": 1360,
                        "center_x": 180,
                        "center_y": 1180,
                    },
                    "reason": "xml_thumbnail_top_left",
                    "y_min_px": 900,
                }
            return {"reliable": False, "reason": "top_left_xml_thumbnail_not_found", "cell": None}

        with mock.patch.object(
            nav, "_post_follow_likes_probe_top_left_xml_cell_meta", side_effect=_probe_meta
        ), mock.patch.object(
            nav,
            "_post_follow_likes_visible_grid_cell_under_suggested",
            return_value={
                "reliable": True,
                "cell": {"top": 1500, "bottom": 1860, "center_x": 180, "center_y": 1680, "left": 0},
                "reason": "profile_tabs_grid_cell_estimate",
                "y_min_px": 1400,
            },
        ), mock.patch.object(nav, "log"), mock.patch.object(nav, "time") as tmock:
            tmock.perf_counter = time.perf_counter
            tmock.sleep = lambda *_a, **_k: None
            meta, state = nav._post_follow_likes_run_top_left_xml_probe_sequence(
                device,
                ui_hints={"profile_tabs_visible": True},
                budget_deadline=time.perf_counter() + 5.0,
                ww=1080,
                wh=2340,
                after_reveal_scroll=True,
            )
        self.assertEqual(probe_calls[0], 2)
        self.assertTrue(state.get("top_left_post_tap_safe"))
        self.assertEqual(meta.get("reason"), "xml_thumbnail_top_left")

    def test_top_left_xml_probe_fail_closed_reason_after_retries(self) -> None:
        device = mock.MagicMock()
        device.window_size.return_value = (1080, 2340)
        estimate = {
            "reliable": True,
            "cell": {"left": 0, "top": 1500, "bottom": 1860, "center_x": 180, "center_y": 1680},
            "reason": "profile_tabs_grid_cell_estimate",
            "y_min_px": 1400,
        }
        with mock.patch.object(
            nav,
            "_post_follow_likes_probe_top_left_xml_cell_meta",
            return_value={"reliable": False, "reason": "top_left_xml_thumbnail_not_found", "cell": None},
        ), mock.patch.object(
            nav, "_post_follow_likes_visible_grid_cell_under_suggested", return_value=estimate
        ), mock.patch.object(nav, "log"), mock.patch.object(nav, "time") as tmock:
            tmock.perf_counter = time.perf_counter
            tmock.sleep = lambda *_a, **_k: None
            _meta, state = nav._post_follow_likes_run_top_left_xml_probe_sequence(
                device,
                ui_hints={"profile_tabs_visible": True},
                budget_deadline=time.perf_counter() + 5.0,
                ww=1080,
                wh=2340,
                after_reveal_scroll=True,
            )
        self.assertEqual(
            state.get("tap_safe_reason"),
            "top_left_post_not_detected_after_xml_and_vision_probe",
        )

    def test_priority_xml_probe_runs_before_grid_ui_hints_after_scroll(self) -> None:
        device = mock.MagicMock()
        device.window_size.return_value = (1080, 2340)
        call_order: list[str] = []
        xml_meta = {
            "reliable": True,
            "cell": {
                "left": 0,
                "top": 1000,
                "bottom": 1360,
                "center_x": 180,
                "center_y": 1180,
            },
            "reason": "xml_thumbnail_top_left",
            "y_min_px": 900,
        }
        xml_state = nav._post_follow_likes_assess_reveal_cell_state(
            xml_meta, ww=1080, wh=2340
        )

        def _hints(*_a: object, **_k: object) -> dict[str, object]:
            call_order.append("grid_ui_hints")
            return {"suggested_for_you": False, "profile_tabs_visible": True}

        def _probe_seq(*_a: object, **kw: object) -> tuple[dict[str, object], dict[str, object]]:
            call_order.append("xml_probe")
            self.assertTrue(kw.get("skip_estimate_fallback"))
            self.assertIsNotNone(kw.get("xml_probe_budget_ms"))
            return xml_meta, xml_state

        def _swipe(*_a: object, **_k: object) -> dict[str, object]:
            return {"swipe_ok": True}

        with mock.patch.object(
            nav, "_post_follow_likes_profile_scroll_swipe", side_effect=_swipe
        ), mock.patch.object(
            nav, "_post_follow_likes_visible_grid_cell_under_suggested",
            return_value={
                "reliable": True,
                "cell": {"left": 0, "top": 1972, "bottom": 2332, "center_x": 180, "center_y": 2152},
                "reason": "profile_tabs_grid_cell_estimate",
                "y_min_px": 1500,
            },
        ), mock.patch.object(
            nav, "_post_follow_likes_grid_ui_surface_hints", side_effect=_hints
        ), mock.patch.object(
            nav, "_post_follow_likes_run_top_left_xml_probe_sequence", side_effect=_probe_seq
        ), mock.patch.object(nav, "log"), mock.patch.object(nav, "time") as tmock:
            tmock.perf_counter = time.perf_counter
            tmock.sleep = lambda *_a, **_k: None
            stab = nav._post_follow_likes_stabilize_grid_under_suggested_overlay(
                device,
                ui_hints={"suggested_for_you": True, "profile_tabs_visible": True},
                budget_deadline=time.perf_counter() + 5.0,
                ww=1080,
                wh=2340,
            )
        self.assertTrue(stab.get("tap_safe"))
        self.assertIn("xml_probe", call_order)
        self.assertIn("grid_ui_hints", call_order)
        self.assertLess(call_order.index("xml_probe"), call_order.index("grid_ui_hints"))

    def test_xml_probe_logs_started_completed_when_grid_cap_is_low(self) -> None:
        device = mock.MagicMock()
        device.window_size.return_value = (1080, 2340)
        t0 = time.perf_counter()
        log_events: list[str] = []

        def _fake_log(_level: str, event: str, **_kw: object) -> None:
            log_events.append(str(event))

        with mock.patch.object(
            nav, "_post_follow_likes_probe_top_left_xml_cell_meta",
            return_value={"reliable": False, "reason": "top_left_xml_thumbnail_not_found", "cell": None},
        ), mock.patch.object(nav, "log", side_effect=_fake_log), mock.patch.object(
            nav, "time"
        ) as tmock:
            tmock.perf_counter = lambda: t0 + 12.5
            tmock.sleep = lambda *_a, **_k: None
            _meta, _state = nav._post_follow_likes_run_top_left_xml_probe_sequence(
                device,
                ui_hints={"profile_tabs_visible": True},
                budget_deadline=t0 + 12.6,
                grid_cap_deadline=t0 + 12.0,
                xml_probe_budget_ms=1800.0,
                skip_estimate_fallback=True,
                ww=1080,
                wh=2340,
                after_reveal_scroll=True,
            )
        self.assertIn("like_top_left_xml_probe_started", log_events)
        self.assertIn("like_top_left_xml_probe_completed", log_events)

    def test_evaluate_tap_safe_never_accepts_estimate(self) -> None:
        cell = {"left": 0, "top": 1000, "bottom": 1360, "center_x": 180, "center_y": 1180}
        ok, reason = nav._post_follow_likes_evaluate_post_cell_tap_safe(
            cell,
            reason="profile_tabs_grid_cell_estimate",
            ww=1080,
            wh=2340,
            y_min_px=900,
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "post_cell_estimate_not_safe_to_tap")

    def test_visual_like_skips_profile_context_verify_for_legacy_safe_open(self) -> None:
        device = mock.MagicMock()
        device.window_size.return_value = (1080, 2340)
        log_events: list[str] = []

        def _fake_log(_level: str, event: str, **_kw: object) -> None:
            log_events.append(str(event))

        def _fake_screenshot(_d: object, path: str) -> None:
            from PIL import Image

            Image.new("RGB", (1080, 2340), "black").save(path)

        heart_bounds = {"left": 43, "top": 1538, "right": 115, "bottom": 1671}
        legacy_open_ctx = {
            "ok": True,
            "open_strategy": "vision_open_top_left_legacy_safe",
            "post_detected": True,
            "viewer_detect_path": "phase_a2_exact_like_desc_fast",
            "detect_reason": "like_unlike_ui",
        }
        nav._VISUAL_POST_LIKE_TAPS_RECORDED = 0
        with mock.patch.object(
            nav.config, "ENABLE_REAL_VISUAL_POST_LIKE", True, create=True
        ), mock.patch.object(
            nav.config, "ENABLE_VISUAL_PROFILE_CONTEXT_LOCK", True, create=True
        ), mock.patch.object(
            nav.config, "VISUAL_POST_MAX_LIKES_PER_PROFILE", 1, create=True
        ), mock.patch.object(
            nav, "_followers_current_pkg_activity",
            return_value={"current_activity": "profile", "current_package": "pkg"},
        ), mock.patch.object(nav, "screenshot", side_effect=_fake_screenshot), mock.patch.object(
            nav,
            "_visual_post_like_heart_crop_bounds",
            return_value=(heart_bounds, "ui_bounds_like_exact"),
        ), mock.patch.object(
            nav, "_visual_image_cell_luma_variance", return_value=1800.0
        ), mock.patch.object(
            nav, "visual_target_profile_lock_verify", return_value={"ok": True}
        ), mock.patch.object(
            nav,
            "visual_post_already_liked",
            return_value={
                "already_liked": False,
                "detection_method": "hierarchy_like_hint",
                "confidence": 0.7,
            },
        ), mock.patch.object(
            nav, "visual_verify_same_profile_context"
        ) as verify_ctx, mock.patch.object(
            nav, "_clear_post_follow_open_like_proof_stash"
        ), mock.patch.object(nav, "log", side_effect=_fake_log), mock.patch.object(
            nav, "time"
        ) as tmock:
            tmock.perf_counter = time.perf_counter
            tmock.time = time.time
            tmock.sleep = lambda *_a, **_k: None
            out = nav.visual_like_open_post(
                device,
                source_profile_username="vipbeach",
                expected_profile_context={"ok": False},
                post_opened_via_profile_grid=True,
                post_open_context=legacy_open_ctx,
                expected_follower_username="allaround.agency",
            )

        verify_ctx.assert_not_called()
        self.assertTrue(out.get("real_tap_sent"))
        self.assertIn(
            "visual_post_like_context_accepted_from_legacy_safe_open", log_events
        )
        self.assertIn(
            "visual_profile_context_verify_skipped_for_legacy_safe_open", log_events
        )
        self.assertIn("visual_post_like_tap_sent", log_events)
        device.click.assert_called_once()

    def test_visual_like_still_aborts_profile_context_when_legacy_viewer_unconfirmed(
        self,
    ) -> None:
        device = mock.MagicMock()
        device.window_size.return_value = (1080, 2340)
        log_events: list[str] = []

        def _fake_log(_level: str, event: str, **_kw: object) -> None:
            log_events.append(str(event))

        def _fake_screenshot(_d: object, path: str) -> None:
            from PIL import Image

            Image.new("RGB", (1080, 2340), "black").save(path)

        heart_bounds = {"left": 43, "top": 1538, "right": 115, "bottom": 1671}
        legacy_open_ctx = {
            "ok": True,
            "open_strategy": "vision_open_top_left_legacy_safe",
            "post_detected": False,
            "viewer_detect_path": "phase_a2_exact_like_desc_fast",
            "detect_reason": "like_unlike_ui",
        }
        nav._VISUAL_POST_LIKE_TAPS_RECORDED = 0
        with mock.patch.object(
            nav.config, "ENABLE_REAL_VISUAL_POST_LIKE", True, create=True
        ), mock.patch.object(
            nav.config, "ENABLE_VISUAL_PROFILE_CONTEXT_LOCK", True, create=True
        ), mock.patch.object(
            nav.config, "VISUAL_POST_MAX_LIKES_PER_PROFILE", 1, create=True
        ), mock.patch.object(
            nav, "_followers_current_pkg_activity",
            return_value={"current_activity": "profile", "current_package": "pkg"},
        ), mock.patch.object(nav, "screenshot", side_effect=_fake_screenshot), mock.patch.object(
            nav,
            "_visual_post_like_heart_crop_bounds",
            return_value=(heart_bounds, "ui_bounds_like_exact"),
        ), mock.patch.object(
            nav, "_visual_image_cell_luma_variance", return_value=1800.0
        ), mock.patch.object(
            nav, "visual_target_profile_lock_verify", return_value={"ok": True}
        ), mock.patch.object(
            nav,
            "visual_post_already_liked",
            return_value={
                "already_liked": False,
                "detection_method": "hierarchy_like_hint",
                "confidence": 0.7,
            },
        ), mock.patch.object(
            nav,
            "visual_verify_same_profile_context",
            return_value={
                "same_profile": False,
                "confidence": 0.0,
                "expected_profile_fingerprint": "",
                "current_profile_fingerprint": "abc",
                "verification_method": "visual_hamming",
            },
        ), mock.patch.object(nav, "log", side_effect=_fake_log), mock.patch.object(
            nav, "time"
        ) as tmock:
            tmock.perf_counter = time.perf_counter
            tmock.time = time.time
            tmock.sleep = lambda *_a, **_k: None
            out = nav.visual_like_open_post(
                device,
                source_profile_username="vipbeach",
                expected_profile_context={"ok": False},
                post_opened_via_profile_grid=True,
                post_open_context=legacy_open_ctx,
                expected_follower_username="allaround.agency",
            )

        self.assertFalse(out.get("real_tap_sent"))
        self.assertIn("visual_profile_context_mismatch_abort", log_events)
        self.assertNotIn(
            "visual_profile_context_verify_skipped_for_legacy_safe_open", log_events
        )
        device.click.assert_not_called()

    def test_like_open_skips_direct_tap_when_cell_not_tap_safe(self) -> None:
        device = mock.MagicMock()
        contract_ctx = mock.MagicMock()
        contract_ctx.current_state.value = "sheet_dismissed"
        with mock.patch.object(
            nav.config, "POST_FOLLOW_POST_LIKES_ENABLED", True, create=True
        ), mock.patch.object(
            nav.config, "ENABLE_REAL_VISUAL_POST_LIKE", True, create=True
        ), mock.patch.object(
            nav, "read_current_profile_username_for_follow_gate", return_value="cand"
        ), mock.patch.object(
            nav, "_post_follow_like_precheck_mute_sheet", return_value={"skip_like": False}
        ), mock.patch.object(
            nav,
            "_post_follow_like_precheck_surface",
            return_value={
                "skip_like": False,
                "skip_reason": "",
                "precheck_ms": 1.0,
                "profile_candidate_visible": True,
            },
        ), mock.patch(
            "follow_state_contract.evaluate_like_precheck_contract",
            return_value=(contract_ctx, True, ""),
        ), mock.patch.object(
            nav,
            "ensure_post_grid_visible_for_post_follow_likes",
            return_value={
                "ok": True,
                "grid_state_after": "visible",
                "direct_post_cell_under_suggested": {"center_x": 180, "center_y": 2152},
                "direct_post_cell_tap_safe": False,
                "direct_post_cell_source": "profile_tabs_grid_cell_estimate",
                "failure_reason": "post_cell_estimate_not_safe_to_tap",
                "likes_perf_grid": {},
            },
        ), mock.patch(
            "navigation_engine.observe_instagram_state",
            return_value={"state": "CANDIDATE_PROFILE", "confidence": 0.9},
        ):
            out = nav.run_post_follow_post_likes_phase(
                device,
                pkg="com.instagram.android",
                source_profile_username="ct",
                follower_username="cand",
                visual_candidate_id="vc-1",
                follow_success_verified=True,
                follow_state_after="following",
                skipped_tap=False,
            )
        device.click.assert_not_called()
        self.assertEqual(out.get("phase_outcome"), "failed_safe_continue")


class PostViewerUnusableSurfaceTests(unittest.TestCase):
    def test_facebook_shared_content_banner_detected_from_ui_text(self) -> None:
        device = mock.MagicMock()
        pred = mock.MagicMock()
        pred.exists.return_value = True
        device.textContains.return_value = pred
        detected, method = nav._ui_post_viewer_facebook_shared_content_detected(device)
        self.assertTrue(detected)
        self.assertEqual(method, "ui_text_contains_facebook_shared_banner")

    def test_like_action_bar_missing_when_fast_probes_miss(self) -> None:
        device = mock.MagicMock()
        with mock.patch.object(
            nav, "_ui_post_viewer_open_like_unlike_fast", return_value=(False, "", [], {})
        ), mock.patch.object(
            nav,
            "_ui_post_viewer_open_exact_like_desc_fast",
            return_value=(False, "", [], "", "", {}),
        ):
            exploitable, method = nav._ui_post_viewer_like_action_bar_exploitable(
                device,
                pkg="com.instagram.android",
            )
        self.assertFalse(exploitable)
        self.assertEqual(method, "post_like_action_bar_missing")

    def test_post_like_phase_skips_facebook_shared_content_surface(self) -> None:
        device = mock.MagicMock()
        contract_ctx = _like_phase_contract_ctx()
        logs: list[str] = []
        with ExitStack() as stack:
            _patch_like_phase_common(stack, contract_ctx=contract_ctx)
            stack.enter_context(
                mock.patch.object(
                    nav,
                    "_ui_post_viewer_facebook_shared_content_detected",
                    return_value=(True, "ui_text_contains_facebook_shared_banner"),
                )
            )
            stack.enter_context(
                mock.patch.object(
                    nav,
                    "_post_follow_likes_open_top_left_legacy_visual_safe",
                    return_value={
                        "ok": True,
                        "post_detected": True,
                        "failure_reason": "",
                        "open_strategy": "vision_open_top_left_legacy_safe",
                        "tap_x": 180,
                        "tap_y": 1282,
                        "likes_perf_post_open": {},
                    },
                )
            )
            like_open = stack.enter_context(mock.patch.object(nav, "visual_like_open_post"))
            verify = stack.enter_context(mock.patch.object(nav, "visual_verify_post_liked"))
            stack.enter_context(
                mock.patch.object(
                    nav, "visual_return_to_profile_from_post", return_value={"ok": True}
                )
            )
            stack.enter_context(
                mock.patch.object(
                    nav, "log", side_effect=lambda _level, event, **_kw: logs.append(str(event))
                )
            )
            out = nav.run_post_follow_post_likes_phase(
                device,
                pkg="com.instagram.android",
                source_profile_username="ct",
                follower_username="cand",
                visual_candidate_id="vc-1",
                follow_success_verified=True,
                follow_state_after="following",
                skipped_tap=False,
            )

        self.assertEqual(out.get("phase_outcome"), "skipped")
        self.assertEqual(out.get("skipped_reason"), "post_like_skipped_facebook_shared_content")
        like_open.assert_not_called()
        verify.assert_not_called()
        self.assertIn("post_follow_post_like_skipped_unusable_surface", logs)

    def test_post_like_phase_skips_when_like_action_bar_missing(self) -> None:
        device = mock.MagicMock()
        contract_ctx = _like_phase_contract_ctx()
        logs: list[str] = []
        with ExitStack() as stack:
            _patch_like_phase_common(stack, contract_ctx=contract_ctx)
            stack.enter_context(
                mock.patch.object(
                    nav,
                    "_ui_post_viewer_like_action_bar_exploitable",
                    return_value=(False, "post_like_action_bar_missing"),
                )
            )
            stack.enter_context(
                mock.patch.object(
                    nav,
                    "_post_follow_likes_open_top_left_legacy_visual_safe",
                    return_value={
                        "ok": True,
                        "post_detected": True,
                        "failure_reason": "",
                        "open_strategy": "vision_open_top_left_legacy_safe",
                        "tap_x": 180,
                        "tap_y": 1282,
                        "likes_perf_post_open": {},
                    },
                )
            )
            already_liked = stack.enter_context(mock.patch.object(nav, "visual_post_already_liked"))
            like_open = stack.enter_context(mock.patch.object(nav, "visual_like_open_post"))
            verify = stack.enter_context(mock.patch.object(nav, "visual_verify_post_liked"))
            stack.enter_context(
                mock.patch.object(
                    nav, "visual_return_to_profile_from_post", return_value={"ok": True}
                )
            )
            stack.enter_context(
                mock.patch.object(
                    nav, "log", side_effect=lambda _level, event, **_kw: logs.append(str(event))
                )
            )
            out = nav.run_post_follow_post_likes_phase(
                device,
                pkg="com.instagram.android",
                source_profile_username="ct",
                follower_username="cand",
                visual_candidate_id="vc-1",
                follow_success_verified=True,
                follow_state_after="following",
                skipped_tap=False,
            )

        self.assertEqual(out.get("phase_outcome"), "skipped")
        self.assertEqual(out.get("skipped_reason"), "post_like_skipped_no_like_button")
        already_liked.assert_not_called()
        like_open.assert_not_called()
        verify.assert_not_called()
        self.assertIn("post_follow_post_like_skipped_unusable_surface", logs)


if __name__ == "__main__":
    unittest.main()
