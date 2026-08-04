from __future__ import annotations

import inspect
import unittest
from unittest import mock

import config
import instagram_navigation as nav


def _shadow_inputs() -> dict[str, object]:
    package = str(config.INSTAGRAM_PACKAGE)
    return {
        "binding": {
            "account_id": "account",
            "run_id": "run",
            "request_id": "request",
            "action_id": "action",
            "worker_sha": "worker",
            "source_target_id": "target",
        },
        "evidence": {
            "candidate_username": "cand",
            "post_reveal_package": package,
            "post_reveal_activity": "com.instagram.mainactivity.InstagramMainActivity",
            "coordinate_frame": {
                "raw_width": 1080,
                "raw_height": 2340,
                "captured_at": 99.0,
            },
            "navigation_counter": 7,
            "scroll_generation": 8,
            "canonical_generation": 9,
            "identity_exact": True,
            "private_profile_visible": False,
            "post_count_positive": True,
            "profile_tabs_present": True,
            "grid_selected": True,
            "story_or_highlight_opened": False,
            "reels_tab_state": "unselected",
            "tagged_tab_state": "unselected",
            "reels_or_tagged_selected": False,
            "suggested_region_separate": True,
            "absolute_top_left_origin_proven": True,
            "absolute_row_index": 1,
            "absolute_column_index": 1,
            "classification_reveal_ttl_ms": 3000.0,
        },
        "candidate_username": "cand",
        "target_username": "ct",
        "expected_package": package,
        "live_package": package,
        "live_activity": "com.instagram.mainactivity.InstagramMainActivity",
        "viewport": {"width": 1080, "height": 2340},
        "runtime": {
            "navigation_counter": 7,
            "scroll_counter": 8,
            "ui_generation": 9,
        },
        "ui_hints": {"suggested_for_you": False, "discover_people": False},
        "selected_cell": {
            "row": 0,
            "col": 0,
            "bounds": {"left": 0, "top": 900, "right": 360, "bottom": 1260},
        },
        "ordered_candidates": [{"row": 0, "col": 0, "eligible": True}],
        "screenshot_captured_at_monotonic": 99.5,
        "now_monotonic": 100.0,
        "stage_timings": {
            "golden_profile_lock_ms": 2500.0,
            "golden_no_posts_check_ms": 1800.0,
        },
    }


class Follow60VisualRoiShadowV1Tests(unittest.TestCase):
    def _evaluate(self, **mutations: object) -> dict[str, object]:
        kwargs = _shadow_inputs()
        for key, value in mutations.items():
            if key.startswith("evidence__"):
                kwargs["evidence"][key.split("__", 1)[1]] = value
            elif key.startswith("binding__"):
                kwargs["binding"][key.split("__", 1)[1]] = value
            elif key.startswith("runtime__"):
                kwargs["runtime"][key.split("__", 1)[1]] = value
            elif key.startswith("ui_hints__"):
                kwargs["ui_hints"][key.split("__", 1)[1]] = value
            elif key.startswith("selected_cell__"):
                kwargs["selected_cell"][key.split("__", 1)[1]] = value
            else:
                kwargs[key] = value
        return nav.evaluate_visual_roi_fast_path_shadow(**kwargs)

    def test_unique_absolute_top_left_is_shadow_candidate_only(self) -> None:
        out = self._evaluate()
        self.assertTrue(out["shadow_fast_path_candidate"])
        self.assertEqual(out["shadow_fast_path_absolute_row"], 0)
        self.assertEqual(out["shadow_fast_path_absolute_column"], 0)
        self.assertEqual(out["shadow_fast_path_unique_cell_count"], 1)
        self.assertEqual(out["shadow_fast_path_estimated_saving_ms"], 4300.0)
        self.assertEqual(out["shadow_fast_path_estimated_conservative_saving_ms"], 3010.0)
        forbidden = {"tap_x", "tap_y", "tap_authorized", "intent", "permission"}
        self.assertTrue(forbidden.isdisjoint(out))

    def test_first_rejection_reason_is_exact_for_all_shadow_guards(self) -> None:
        package = str(config.INSTAGRAM_PACKAGE)
        cases = (
            ({"binding__account_id": ""}, "shadow_account_binding_not_exact"),
            ({"candidate_username": "other"}, "shadow_candidate_mismatch"),
            ({"target_username": ""}, "shadow_target_not_exact"),
            ({"live_package": "wrong"}, "shadow_package_mismatch"),
            ({"live_activity": "wrong"}, "shadow_activity_mismatch"),
            ({"viewport": {"width": 999, "height": 2340}}, "shadow_frame_mismatch"),
            ({"runtime__navigation_counter": 99}, "shadow_navigation_generation_mismatch"),
            ({"runtime__scroll_counter": 99}, "shadow_scroll_generation_mismatch"),
            ({"runtime__ui_generation": 99}, "shadow_ui_generation_mismatch"),
            ({"evidence__private_profile_visible": True}, "shadow_profile_not_public_exact"),
            ({"evidence__post_count_positive": False}, "shadow_post_count_not_positive"),
            ({"evidence__grid_selected": False}, "shadow_posts_surface_not_structurally_proven"),
            ({"evidence__story_or_highlight_opened": True}, "shadow_story_or_highlight_present"),
            ({"evidence__reels_tab_state": "selected"}, "shadow_reels_selected"),
            ({"evidence__tagged_tab_state": "selected"}, "shadow_tagged_selected"),
            (
                {
                    "ui_hints__suggested_for_you": True,
                    "evidence__suggested_region_separate": False,
                },
                "shadow_suggested_overlap",
            ),
            (
                {
                    "ui_hints__highlights_visible": True,
                    "evidence__highlights_region_detected": True,
                    "evidence__highlights_region_separate": False,
                },
                "shadow_highlights_overlap",
            ),
            ({"screenshot_captured_at_monotonic": 90.0}, "shadow_screenshot_stale"),
            (
                {
                    "ordered_candidates": [
                        {"row": 0, "col": 0, "eligible": True},
                        {"row": 0, "col": 1, "eligible": True},
                    ]
                },
                "shadow_top_left_cell_not_unique",
            ),
            ({"selected_cell__row": 1}, "shadow_row_2_only"),
            ({"selected_cell__col": 1}, "shadow_absolute_top_left_not_proven"),
            (
                {
                    "selected_cell__bounds": {
                        "left": 0,
                        "top": 2200,
                        "right": 360,
                        "bottom": 2500,
                    }
                },
                "shadow_bounds_outside_viewport",
            ),
            (
                {
                    "evidence__coordinate_frame": {
                        "raw_width": 1080,
                        "raw_height": 2340,
                        "captured_at": 96.0,
                    },
                    "screenshot_captured_at_monotonic": 99.5,
                },
                "shadow_positive_proof_ttl_expired",
            ),
        )
        self.assertEqual(package, _shadow_inputs()["expected_package"])
        for mutations, reason in cases:
            with self.subTest(reason=reason):
                out = self._evaluate(**mutations)
                self.assertFalse(out["shadow_fast_path_candidate"])
                self.assertEqual(out["shadow_fast_path_rejection_reason"], reason)

    def test_shadow_result_cannot_change_golden_or_v5_control_flow(self) -> None:
        source = inspect.getsource(nav.run_post_follow_post_likes_phase)
        self.assertIn("_post_open_surface_audits(", source)
        evaluator_source = inspect.getsource(nav.evaluate_visual_roi_fast_path_shadow)
        self.assertNotIn("d.click", evaluator_source)
        self.assertNotIn("PostOpenIntent", evaluator_source)
        self.assertNotIn("screenshot(", evaluator_source)


class Follow60GoldenShadowEvidenceTransportV1Tests(unittest.TestCase):
    def _transport(self, **overrides: object) -> tuple[dict, dict]:
        base = _shadow_inputs()
        source = dict(base["evidence"])
        binding = dict(base["binding"])
        hints = dict(base["ui_hints"])
        selected = dict(base["selected_cell"])
        ordered = list(base["ordered_candidates"])
        candidate = "cand"
        if "source_evidence" in overrides:
            source = dict(overrides.pop("source_evidence") or {})
        if "binding" in overrides:
            binding = dict(overrides.pop("binding") or {})
        if "ui_hints" in overrides:
            hints = dict(overrides.pop("ui_hints") or {})
        if "selected_cell" in overrides:
            selected = dict(overrides.pop("selected_cell") or {})
        if "ordered_candidates" in overrides:
            ordered = list(overrides.pop("ordered_candidates") or [])
        if "candidate_username" in overrides:
            candidate = str(overrides.pop("candidate_username") or "")
        transport = nav._transport_golden_evidence_to_visual_roi_shadow_v1(
            binding=binding,
            source_evidence=source,
            candidate_username=candidate,
            target_username="ct",
            expected_package=str(config.INSTAGRAM_PACKAGE),
            live_package=str(config.INSTAGRAM_PACKAGE),
            live_activity="com.instagram.mainactivity.InstagramMainActivity",
            golden_frame={
                "raw_window_size": (1080, 2340),
                "canonical_content_size": (1080, 2340),
            },
            golden_screenshot_hash="golden-hash",
            golden_screenshot_captured_at_monotonic=99.5,
            runtime={
                "navigation_counter": 7,
                "scroll_counter": 8,
                "ui_generation": 9,
            },
            ui_hints=hints,
            selected_cell=selected,
            ordered_candidates=ordered,
            profile_identity_exact=True,
        )
        evaluator = dict(base)
        evaluator.update(
            {
                "binding": binding,
                "evidence": transport,
                "candidate_username": candidate,
                "ui_hints": hints,
                "selected_cell": selected,
                "ordered_candidates": ordered,
            }
        )
        evaluator.update(overrides)
        return transport, nav.evaluate_visual_roi_fast_path_shadow(**evaluator)

    def test_complete_golden_transport_evaluates_top_left(self) -> None:
        transport, out = self._transport()
        self.assertEqual(
            transport["shadow_transport_version"],
            "VisualRoiShadowEvidenceTransportV1",
        )
        self.assertTrue(out["shadow_fast_path_candidate"])
        self.assertEqual(out["shadow_fast_path_absolute_row"], 0)
        self.assertEqual(out["shadow_fast_path_absolute_column"], 0)

    def test_explicit_candidate_mismatch_is_preserved_and_rejected(self) -> None:
        source = dict(_shadow_inputs()["evidence"])
        source["candidate_username"] = "other"
        transport, out = self._transport(source_evidence=source)
        self.assertEqual(transport["candidate_username"], "other")
        self.assertEqual(
            out["shadow_fast_path_rejection_reason"], "shadow_candidate_mismatch"
        )

    def test_suggested_overlap_is_rejected(self) -> None:
        source = dict(_shadow_inputs()["evidence"])
        source["suggested_region_separate"] = False
        _, out = self._transport(
            source_evidence=source,
            ui_hints={
                "suggested_for_you": True,
                "profile_tabs_visible": True,
            },
        )
        self.assertEqual(
            out["shadow_fast_path_rejection_reason"], "shadow_suggested_overlap"
        )

    def test_highlight_overlap_is_rejected(self) -> None:
        source = dict(_shadow_inputs()["evidence"])
        source["highlights_region_detected"] = True
        source["highlights_region_separate"] = False
        _, out = self._transport(
            source_evidence=source,
            ui_hints={
                "highlights_visible": True,
                "profile_tabs_visible": True,
            },
        )
        self.assertEqual(
            out["shadow_fast_path_rejection_reason"], "shadow_highlights_overlap"
        )

    def test_reels_and_tagged_surfaces_are_rejected(self) -> None:
        for key, reason in (
            ("reels_tab_state", "shadow_reels_selected"),
            ("tagged_tab_state", "shadow_tagged_selected"),
        ):
            with self.subTest(key=key):
                source = dict(_shadow_inputs()["evidence"])
                source[key] = "selected"
                _, out = self._transport(source_evidence=source)
                self.assertEqual(out["shadow_fast_path_rejection_reason"], reason)

    def test_row_two_only_is_rejected(self) -> None:
        _, out = self._transport(
            selected_cell={
                "row": 1,
                "col": 0,
                "bounds": {"left": 0, "top": 1260, "right": 360, "bottom": 1620},
            },
            ordered_candidates=[{"row": 1, "col": 0, "eligible": True}],
        )
        self.assertEqual(
            out["shadow_fast_path_rejection_reason"], "shadow_row_2_only"
        )

    def test_unique_top_left_can_be_true_without_intent_or_tap(self) -> None:
        _, out = self._transport()
        self.assertTrue(out["shadow_fast_path_candidate"])
        forbidden = {"tap_x", "tap_y", "tap_authorized", "intent", "permission"}
        self.assertTrue(forbidden.isdisjoint(out))

    def test_transport_and_evaluator_are_pure_and_non_authoritative(self) -> None:
        transport_source = inspect.getsource(
            nav._transport_golden_evidence_to_visual_roi_shadow_v1
        )
        evaluator_source = inspect.getsource(nav.evaluate_visual_roi_fast_path_shadow)
        for forbidden in (
            "d.click", "screenshot(", "dump_hierarchy", "PostOpenIntent", "time.sleep"
        ):
            self.assertNotIn(forbidden, transport_source)
            self.assertNotIn(forbidden, evaluator_source)

    def test_shadow_false_does_not_mutate_source_or_golden_inputs(self) -> None:
        source = dict(_shadow_inputs()["evidence"])
        original = dict(source)
        _, out = self._transport(
            source_evidence=source,
            selected_cell={
                "row": 0,
                "col": 1,
                "bounds": {"left": 360, "top": 900, "right": 720, "bottom": 1260},
            },
            ordered_candidates=[{"row": 0, "col": 1, "eligible": True}],
        )
        self.assertFalse(out["shadow_fast_path_candidate"])
        self.assertEqual(source, original)

    def test_transport_declares_zero_extra_acquisitions(self) -> None:
        transport, _ = self._transport()
        self.assertEqual(transport["shadow_transport_extra_acquisitions"], 0)


class Follow60GoldenInstrumentationV1Tests(unittest.TestCase):
    def _run_golden(self, *, shadow_raises: bool = False):
        device = mock.MagicMock()
        device.window_size.return_value = (1080, 2340)
        image = mock.MagicMock()
        image.convert.return_value = image
        image.size = (1080, 2340)
        image.tobytes.return_value = b"golden-frame"
        device.screenshot.return_value = image
        package = str(config.INSTAGRAM_PACKAGE)
        evidence = dict(_shadow_inputs()["evidence"])
        evidence.update(
            {
                "outcome": "POST_GRID_AMBIGUOUS_FINAL",
                "physical_cells": [
                    {"left": 0, "top": 900, "right": 360, "bottom": 1260}
                ],
                "no_posts_positive": False,
                "loading_visible": False,
                "reveal_count_total_for_like_phase": 1,
                "reacquire_dump_count": 1,
                "old_bounds_invalidated": True,
                "rejection_reason": "post_reveal_fully_visible_first_row_missing",
            }
        )
        binding = dict(_shadow_inputs()["binding"])
        shadow_side_effect = RuntimeError("instrumentation failed") if shadow_raises else None
        patches = (
            mock.patch.object(
                nav,
                "_followers_current_pkg_activity",
                return_value={
                    "current_package": package,
                    "current_activity": "com.instagram.mainactivity.InstagramMainActivity",
                },
            ),
            mock.patch.object(nav, "_try_profile_signals_once", return_value=True),
            mock.patch.object(nav, "visual_target_profile_lock_verify", return_value={"ok": True}),
            mock.patch.object(nav, "visual_profile_has_no_posts", return_value={"no_posts_detected": False}),
            mock.patch.object(
                nav,
                "_post_follow_likes_grid_ui_surface_hints",
                return_value={
                    "suggested_for_you": False,
                    "discover_people": False,
                    "profile_tabs_visible": True,
                },
            ),
            mock.patch.object(
                nav,
                "_post_follow_dynamic_first_row_search_y_min_layout",
                return_value=(780, "profile_tabs_bottom", 760, 16),
            ),
            mock.patch.object(
                nav,
                "_dynamic_first_post_grid_row_from_image",
                return_value={"ok": True, "first_row_top": 900, "first_row_bottom": 1260},
            ),
            mock.patch.object(
                nav,
                "_visual_select_profile_grid_cell",
                return_value=(
                    (0, 0, 0, 900, 3000.0),
                    [{"row": 0, "col": 0, "eligible": True, "variance": 3000.0}],
                ),
            ),
            mock.patch.object(nav, "_create_post_open_intent_from_final_proof", return_value=object()),
            mock.patch.object(
                nav,
                "_dispatch_post_open_intent_v2_tap",
                return_value={
                    "ok": True,
                    "intent_age_ms": 100.0,
                    "intent_final_validation_ms": 4.0,
                    "command_tap_ack_ms": 8.0,
                    "post_open_stage_provenance": {},
                },
            ),
            mock.patch.object(
                nav,
                "_visual_wait_post_viewer_opened_after_tap",
                return_value={
                    "post_detected": True,
                    "prof_still_on_candidate_profile": False,
                    "viewer_detection_signals_seen": ["like_unlike_ui"],
                    "detect_reason": "like_unlike_ui",
                    "viewer_detect_path": "phase_a2_exact_like_desc_fast",
                    "viewer_detect_total_ms": 100.0,
                    "poll_count": 1,
                },
            ),
            mock.patch.object(nav, "_stash_post_follow_open_like_proof"),
            mock.patch.object(nav.time, "sleep"),
            mock.patch.object(nav, "log"),
        )
        with patches[0] as meta, patches[1] as profile, patches[2] as lock, patches[3] as no_posts, patches[4] as hints, patches[5], patches[6], patches[7] as select, patches[8] as create_intent, patches[9] as dispatch, patches[10] as viewer, patches[11], patches[12], patches[13]:
            shadow_patch = mock.patch.object(
                nav,
                "evaluate_visual_roi_fast_path_shadow",
                side_effect=shadow_side_effect,
                wraps=None if shadow_raises else nav.evaluate_visual_roi_fast_path_shadow,
            )
            with shadow_patch as shadow:
                out = nav.visual_open_recent_post_from_profile(
                    device,
                    source_profile_username="ct",
                    expected_follower_username="cand",
                    selection_policy=nav._VISUAL_POST_OPEN_SELECTION_FIRST_ROW_LTR,
                    likes_perf_phase_t0=0.0,
                    post_follow_stash_open_like_proof=True,
                    post_open_intent_binding=binding,
                    post_open_intent_target_username="ct",
                    post_grid_existence_evidence=evidence,
                )
        return out, device, meta, profile, lock, no_posts, hints, select, create_intent, dispatch, viewer, shadow

    def test_every_requested_golden_timing_is_published_and_nonnegative(self) -> None:
        out, device, _meta, _profile, _lock, no_posts, hints, select, create_intent, dispatch, viewer, shadow = self._run_golden()
        self.assertTrue(out["ok"])
        perf = dict(out["likes_perf_post_open"])
        for field in nav._GOLDEN_OBSERVABILITY_TIMING_FIELDS:
            self.assertIn(field, perf)
            self.assertGreaterEqual(float(perf[field]), 0.0)
        for field in (
            "golden_entered_at",
            "positive_post_grid_proof_received_at",
            "golden_reuse_decision_started_at",
            "golden_reuse_decision_finished_at",
            "golden_reuse_eligible",
            "golden_reuse_rejection_reason",
            "positive_proof_age_ms",
            "positive_proof_candidate_match",
            "positive_proof_package_match",
            "positive_proof_activity_match",
            "positive_proof_frame_match",
            "positive_proof_navigation_generation_match",
            "positive_proof_scroll_generation_match",
            "positive_proof_ui_generation_match",
            "positive_proof_post_count_status",
            "positive_proof_absolute_top_left_status",
        ):
            self.assertIn(field, perf)
        device.screenshot.assert_called_once_with(format="pillow")
        no_posts.assert_not_called()
        hints.assert_called_once()
        select.assert_called_once()
        create_intent.assert_called_once()
        dispatch.assert_called_once()
        viewer.assert_called_once()
        shadow.assert_called_once()

    def test_instrumentation_failure_does_not_change_golden_result_or_counts(self) -> None:
        out, device, _meta, _profile, _lock, no_posts, hints, select, create_intent, dispatch, viewer, shadow = self._run_golden(shadow_raises=True)
        self.assertTrue(out["ok"])
        self.assertTrue(out["post_detected"])
        self.assertEqual(
            out["likes_perf_post_open"]["shadow_fast_path_rejection_reason"],
            "shadow_evaluator_error:RuntimeError",
        )
        device.screenshot.assert_called_once_with(format="pillow")
        no_posts.assert_not_called()
        hints.assert_called_once()
        select.assert_called_once()
        create_intent.assert_called_once()
        dispatch.assert_called_once()
        viewer.assert_called_once()
        shadow.assert_called_once()


if __name__ == "__main__":
    unittest.main()
