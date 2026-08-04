from __future__ import annotations

import unittest
from dataclasses import asdict
from unittest import mock

import config
import follow_60s_canary
import instagram_navigation as nav


def _positive_post_reveal_evidence(**overrides: object) -> dict[str, object]:
    runtime = follow_60s_canary.runtime_context()
    frame = follow_60s_canary.build_coordinate_frame_v1(
        source="raw_window_exact",
        raw_width=1080,
        raw_height=2340,
        canonical_width=1080,
        canonical_height=2340,
        orientation="portrait",
        captured_at=nav.time.monotonic(),
        navigation_generation=str(runtime.get("ui_generation") or ""),
        scroll_generation=int(runtime.get("scroll_counter") or 0),
    )
    assert frame is not None
    out: dict[str, object] = {
        "outcome": "POST_GRID_AMBIGUOUS_FINAL",
        "candidate_username": "cand",
        "identity_exact": True,
        "profile_tabs_present": True,
        "grid_selected": True,
        "post_count_positive": True,
        "physical_cells": [
            {
                "left": 0,
                "top": 1480,
                "right": 360,
                "bottom": 1840,
            }
        ],
        "no_posts_positive": False,
        "loading_visible": False,
        "private_profile_visible": False,
        "reels_or_tagged_selected": False,
        "reveal_count_total_for_like_phase": 1,
        "reacquire_dump_count": 1,
        "old_bounds_invalidated": True,
        "rejection_reason": "post_reveal_fully_visible_first_row_missing",
        "post_reveal_package": str(config.INSTAGRAM_PACKAGE),
        "post_reveal_activity": "com.instagram.mainactivity.InstagramMainActivity",
        "coordinate_frame": asdict(frame),
        "classification_reveal_ttl_ms": 3000.0,
    }
    out.update(overrides)
    return out


class Follow60PostOpenPerfV1Tests(unittest.TestCase):
    def test_fresh_post_reveal_physical_cell_reuses_existence_only(self) -> None:
        ok, reason = nav._post_follow_golden_can_reuse_positive_post_grid_existence(
            _positive_post_reveal_evidence(),
            expected_follower_username="cand",
        )
        self.assertTrue(ok)
        self.assertEqual(reason, "fresh_post_reveal_physical_cell_bounds_rejected")

    def test_ambiguous_or_unsafe_evidence_keeps_golden_no_posts_check(self) -> None:
        cases = (
            _positive_post_reveal_evidence(physical_cells=[]),
            _positive_post_reveal_evidence(reacquire_dump_count=0),
            _positive_post_reveal_evidence(old_bounds_invalidated=False),
            _positive_post_reveal_evidence(
                rejection_reason="post_reveal_positive_surface_contract_missing"
            ),
            _positive_post_reveal_evidence(candidate_username="other"),
        )
        for evidence in cases:
            with self.subTest(evidence=evidence):
                ok, _reason = (
                    nav._post_follow_golden_can_reuse_positive_post_grid_existence(
                        evidence,
                        expected_follower_username="cand",
                    )
                )
                self.assertFalse(ok)

    def test_profile_lock_reuse_fails_closed_on_stale_or_generation_mismatch(self) -> None:
        stale = _positive_post_reveal_evidence()
        stale_frame = dict(stale["coordinate_frame"])
        stale_frame["captured_at"] = nav.time.monotonic() - 4.0
        stale["coordinate_frame"] = stale_frame
        generation_mismatch = _positive_post_reveal_evidence()
        mismatch_frame = dict(generation_mismatch["coordinate_frame"])
        mismatch_frame["scroll_generation"] = int(
            mismatch_frame.get("scroll_generation") or 0
        ) + 1
        generation_mismatch["coordinate_frame"] = mismatch_frame
        for evidence in (stale, generation_mismatch):
            with self.subTest(evidence=evidence):
                ok, _reason = (
                    nav._post_follow_golden_can_reuse_exact_profile_lock(
                        evidence,
                        expected_follower_username="cand",
                        expected_package=str(config.INSTAGRAM_PACKAGE),
                        live_package=str(config.INSTAGRAM_PACKAGE),
                        live_activity=(
                            "com.instagram.mainactivity.InstagramMainActivity"
                        ),
                        live_profile_detected=True,
                        consumer_size=(1080, 2340),
                    )
                )
                self.assertFalse(ok)

    def test_golden_reuses_existence_but_keeps_fresh_selection_and_intent(self) -> None:
        device = mock.MagicMock()
        device.window_size.return_value = (1080, 2340)
        image = mock.MagicMock()
        image.convert.return_value = image
        image.size = (1080, 2340)
        image.tobytes.return_value = b"fresh-golden-frame"
        device.screenshot.return_value = image
        package = str(config.INSTAGRAM_PACKAGE)
        logs: list[tuple[str, dict[str, object]]] = []

        with mock.patch.object(
            nav,
            "_followers_current_pkg_activity",
            return_value={
                "current_package": package,
                "current_activity": "com.instagram.mainactivity.InstagramMainActivity",
            },
        ), mock.patch.object(
            nav, "_try_profile_signals_once", return_value=True
        ), mock.patch.object(
            nav, "visual_target_profile_lock_verify", return_value={"ok": True}
        ) as target_lock, mock.patch.object(
            nav, "visual_profile_has_no_posts"
        ) as no_posts, mock.patch.object(
            nav,
            "_post_follow_likes_grid_ui_surface_hints",
            return_value={
                "suggested_for_you": False,
                "discover_people": False,
                "profile_tabs_visible": True,
            },
        ) as surface_hints, mock.patch.object(
            nav,
            "_post_follow_dynamic_first_row_search_y_min_layout",
            return_value=(780, "profile_tabs_bottom", 760, 16),
        ), mock.patch.object(
            nav,
            "_dynamic_first_post_grid_row_from_image",
            return_value={
                "ok": True,
                "first_row_top": 900,
                "first_row_bottom": 1260,
            },
        ), mock.patch.object(
            nav,
            "_visual_select_profile_grid_cell",
            return_value=((0, 0, 0, 900, 3000.0), [(0, 0, 0, 900, 3000.0)]),
        ) as select_cell, mock.patch.object(
            nav,
            "_create_post_open_intent_from_final_proof",
            return_value=object(),
        ), mock.patch.object(
            nav,
            "_dispatch_post_open_intent_v2_tap",
            return_value={
                "ok": True,
                "intent_age_ms": 900.0,
                "post_open_stage_provenance": {
                    "intent_version": "PostOpenIntentV2",
                    "candidate_username": "cand",
                },
            },
        ) as dispatch, mock.patch.object(
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
        ) as viewer, mock.patch.object(
            nav, "_stash_post_follow_open_like_proof"
        ), mock.patch.object(
            nav,
            "log",
            side_effect=lambda _level, event, **kw: logs.append(
                (str(event), dict(kw))
            ),
        ):
            out = nav.visual_open_recent_post_from_profile(
                device,
                source_profile_username="ct",
                expected_follower_username="cand",
                selection_policy=nav._VISUAL_POST_OPEN_SELECTION_FIRST_ROW_LTR,
                likes_perf_phase_t0=0.0,
                post_follow_stash_open_like_proof=True,
                post_open_intent_binding={"run_id": "run", "request_id": "request"},
                post_open_intent_target_username="ct",
                post_grid_existence_evidence=_positive_post_reveal_evidence(),
            )

        no_posts.assert_not_called()
        target_lock.assert_not_called()
        device.screenshot.assert_called_once_with(format="pillow")
        surface_hints.assert_called_once()
        select_cell.assert_called_once()
        dispatch.assert_called_once()
        viewer.assert_called_once()
        self.assertTrue(out.get("ok"))
        self.assertTrue(out.get("post_detected"))
        skip_logs = [
            fields
            for event, fields in logs
            if event
            == "visual_profile_no_posts_recheck_skipped_positive_post_grid_proof"
        ]
        self.assertEqual(len(skip_logs), 1)
        self.assertFalse(skip_logs[0]["tap_authorized"])


if __name__ == "__main__":
    unittest.main()
